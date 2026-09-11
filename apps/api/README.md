# API 服务

## 职责与边界

`apps/api` 提供本地 FastAPI 服务、静态仓库 allow-list、受限本地路径导入、公开 GitHub 固定 commit 导入、项目身份、索引构建、项目/索引查询、规则驱动的项目概览骨架和显式触发的主要功能语义生成。它不执行被分析仓库代码，不实现 Run、SSE 或 Issue Agent。

## 入口与目录导航

- [main.py](app/main.py)：FastAPI 应用和 HTTP 路由。
- [service.py](app/service.py)：仓库注册、项目生命周期、索引服务和项目归属校验。
- [storage.py](app/storage.py)：静态仓库快照、服务管理目录中的动态来源注册/导入状态和 SQLite/FTS5 查询。
- [github.py](app/github.py)：GitHub 官方 API 固定 commit、受限 archive 获取、重定向校验和安全解包。
- [indexing.py](app/indexing.py) 与 [relations.py](app/relations.py)：Python 快照、符号和保守静态关系提取。
- [overview.py](app/overview.py)：按 `python-entry-v1` 从固定快照生成入口、模块和有界静态路径概览。
- [config.py](app/config.py)：从服务数据目录读取并校验持久化模型供应商配置。
- [feature_generation.py](app/feature_generation.py)：收集有界快照上下文，调用确定性 fixture 或服务端配置的 DeepSeek/OpenAI-compatible 模型，并校验带证据的功能结果。
- [tests](tests/)：API、契约、索引、关系和存储测试。

## 接口与依赖

契约位于 [contracts.py](app/contracts.py)，Schema 由 [export_schema.py](app/export_schema.py) 生成。旧 `/api/indexes/*` 保留兼容；项目工作区使用 `/api/projects/*`，本地导入使用 `/api/imports/local`，GitHub 导入使用 `/api/imports/github`，两者都通过 `/api/imports/{attempt_id}` 查询状态；本地和 GitHub 各自有重试入口，GitHub 清理失败可通过 cleanup 入口单独重试。项目索引查询必须通过项目归属校验。

## 数据与生命周期

静态仓库由 `CODETRAIL_REPOSITORIES` JSON 对象提供；本地导入允许根目录由 `CODETRAIL_ALLOWED_IMPORT_ROOTS` JSON 数组提供，动态注册和 `ImportAttempt` 写入 `CODETRAIL_DATA_DIR`（默认 API 工作目录 `.codetrail`）。模型供应商配置从同一服务数据目录的持久化 `config.json` 加载，缺少该文件时使用无密钥的 DeepSeek 默认值。GitHub 导入由 `CODETRAIL_GITHUB_IMPORT_ENABLED=1` 开启，服务端可选读取 `CODETRAIL_GITHUB_TOKEN`；远端身份、requested ref、resolved commit、content hash 和 cleanup 状态写入管理库，archive 临时目录位于管理目录并在完成后清理。新项目的索引库位于管理目录，不在来源目录写入 `.codetrail`。Project 持有稳定 ID、名称、来源 repository_id、创建时间和索引关联；索引源码及证据仍绑定不可变 `index_id`。导入状态为 `queued/acquiring/indexing/ready/failed/interrupted`，失败和重启中断不替换原有可用索引。

## 使用与配置

从仓库根目录准备服务端仓库映射后启动：

```powershell
$env:CODETRAIL_REPOSITORIES = '{"timeout-demo":"D:\\Dev\\CodeTrail\\fixtures\\repos\\timeout-demo"}'
$env:CODETRAIL_ALLOWED_IMPORT_ROOTS = '["D:\\Dev\\CodeTrail\\fixtures\\repos"]'
$env:CODETRAIL_DATA_DIR = 'D:\\Dev\\CodeTrail\\.codetrail-service'
$env:CODETRAIL_WEB_ORIGINS = 'http://localhost:3000,http://127.0.0.1:3000'
$env:CODETRAIL_GITHUB_IMPORT_ENABLED = '1'
Set-Location apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 健康检查为 `GET /api/health`。`CODETRAIL_WEB_ORIGINS` 只控制浏览器跨源读取来源，不替代 loopback 写入边界。本地写入口只接受 loopback 客户端；`POST /api/imports/local` 和 `POST /api/imports/github` 同步返回项目、attempt 和索引结果，`GET /api/imports/{attempt_id}` 查询状态，local/GitHub retry 入口重试失败/中断尝试，`POST /api/imports/{attempt_id}/cleanup` 单独重试 GitHub 临时目录清理。GitHub 输入只允许官方 HTTPS 仓库 URL，可选 ref，服务端固定 commit 后再下载官方 archive。前端和测试通过项目 API 创建项目、建立索引，再用 project_id/index_id 查询；O2 使用 `POST /api/projects/{project_id}/indexes/{index_id}/features` 显式生成，使用 `GET /api/projects/{project_id}/indexes/{index_id}/features/{generation_id}` 读取已保存结果，读取不会再次调用模型。

## 错误与限制

本地导入路径必须是服务端绝对路径、位于允许根目录内且不能跨符号链接/junction；服务管理目录与来源目录不能重叠。GitHub 拒绝任意下载 URL、凭据、非官方重定向、绝对/遍历路径、链接和特殊文件，并限制 archive、文件数、解包大小、重定向、单请求/总时间；同仓库同 commit 幂等，分支移动创建新 attempt。幂等键全局绑定一次提交，重复来源复用项目，并发重复提交不会创建重复活动 attempt。未知项目、非所属索引和未配置 repository_id 返回稳定 `not_found`；输入边界返回 `invalid_input`，非 loopback 写入返回 `forbidden`。功能生成受工具调用、源码行、输入/输出和特征数预算限制，失败、证据不足、部分结果和模型不可用分开返回；fixture 不需要密钥，real 模式只读服务端模型配置且不回退 fixture。当前导入、索引和单次功能生成在受限流程中完成，Run、SSE 和 Agent 未实现。

## 验证

从 `apps/api` 执行 `uv run pytest -q` 和 `uv run python -m compileall -q app`。项目隔离、旧索引兼容关联、失败不覆盖最新索引、动态本地导入、幂等/并发、路径边界、重试和服务重启恢复由 `tests/test_api.py` 覆盖；GitHub 固定 commit、archive 安全、分支移动、清理和 HTTP 入口由 `tests/test_github.py` 覆盖；O1 入口规则、静态路径、循环/动态 unresolved、预算和旧快照稳定性由 `tests/test_overview.py` 覆盖；O2 真实工具上下文、结构化模型协议、缓存/幂等、失败保留、证据校验和模型不可用由 `tests/test_features.py` 覆盖；真实 HTTP/MCP 双进程导入、概览、证据引用和可选功能工具由 `tests/test_mcp.py` 覆盖。

## 关联文档

- [API 子系统说明](../../docs/subsystems/api.md)
- [存储与查询子系统说明](../../docs/subsystems/storage.md)
- [数据契约](../../docs/subsystems/contracts.md)
- [工作区与项目概览决策](../../docs/decisions/2026-09-11-project-workspace.md)

## 本地只读 MCP 入口

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务。stdout 只承载 MCP 协议帧，日志写 stderr；服务复用 `WorkspaceService`，不直接访问 SQLite，不创建索引，不执行仓库代码。

只读工具为 `list_projects`、`get_project`、`list_indexes`、`get_index`、`search_code`、`read_source`、`get_neighbors` 和 `get_overview`。除项目发现外均要求显式 `project_id`，索引查询同时要求 `index_id`；错误以结构化 `code`、`message`、`retryable` 返回。`read_source` 和 `get_overview` 额外返回包含 project/index、行范围、content hash、evidence_id 和工作台 URL 的引用。

设置 `CODETRAIL_MCP_LOCAL_IMPORT_ENABLED=1` 后，额外注册 `import_local_path`、`get_import_status` 和 `retry_local_import`；设置 `CODETRAIL_MCP_GITHUB_IMPORT_ENABLED=1` 后，额外注册 `import_github_repository`、`get_import_status` 和 `retry_github_import`；设置 `CODETRAIL_MCP_FEATURE_GENERATION_ENABLED=1` 后，额外注册 `generate_project_features` 和只读的 `get_feature_generation`。三个开关独立，GitHub MCP 开关还要求 `CODETRAIL_GITHUB_IMPORT_ENABLED=1`；未设置时 MCP 保持八项只读工具，默认只读工具不会触发模型。写工具只能使用服务端边界，不能通过参数扩大本地路径或网络权限。

功能生成的 `fixture` 模式使用 `fixture-feature-v1`，不需要密钥；`real` 模式默认适配 DeepSeek，从服务数据目录的 `config.json` 读取服务端配置。未配置密钥时返回 `model_unavailable`，不会静默使用 fixture。请求预算由契约限制工具调用、模型调用、时间、源码行、输入/输出大小、特征数和修复次数。

DeepSeek 配置文件默认路径为 `apps/api/.codetrail/config.json`；若配置了 `CODETRAIL_DATA_DIR`，则放在该目录下。先复制无密钥模板：

```powershell
New-Item -ItemType Directory -Force apps/api/.codetrail
Copy-Item apps/api/config.example.json apps/api/.codetrail/config.json
```

然后编辑 `config.json`：

```json
{
  "provider": "deepseek",
  "api_key": "sk-你的 DeepSeek API Key",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-flash"
}
```

`config.json` 已位于被 Git 忽略的服务数据目录，密钥只保存在运行 API/MCP 的服务端；不要把真实密钥写入 `config.example.json` 或提交到 Git。修改配置后重启 API/MCP 进程生效。`base_url` 使用 DeepSeek 根地址，不要写成 `/v1`，适配器会请求 `/chat/completions`。如需切换其他 OpenAI-compatible 供应商，可同时修改 `provider`、地址和模型名；请求仍固定使用 JSON 输出格式、受限的 `max_tokens` 和一次修复上限。

配置工作台引用地址（默认 `http://localhost:3000`）：

```powershell
$env:CODETRAIL_WORKBENCH_URL = 'http://localhost:3000'
Set-Location apps/api
uv run python -m app.mcp_server
```

用 MCP 客户端启动时，命令为 `uv run --directory apps/api python -m app.mcp_server`，并传入与 HTTP 服务相同的 `CODETRAIL_REPOSITORIES`、`CODETRAIL_DATA_DIR`、`CODETRAIL_ALLOWED_IMPORT_ROOTS`、GitHub 全局开关和可选服务端 token。两个 MCP 导入开关只影响对应导入工具注册；功能生成开关独立控制模型工具，远程 MCP、认证和私有仓库不开放，real 模式只使用服务端模型配置。

设计与验收见 [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)。
