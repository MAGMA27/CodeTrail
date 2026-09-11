# API 服务

## 职责与边界

`apps/api` 提供本地 FastAPI 服务、静态仓库 allow-list、受限本地路径导入、项目身份、索引构建及项目/索引查询。它不执行被分析仓库代码，不实现 GitHub 获取、项目功能概览或 Issue Agent。

## 入口与目录导航

- [main.py](app/main.py)：FastAPI 应用和 HTTP 路由。
- [service.py](app/service.py)：仓库注册、项目生命周期、索引服务和项目归属校验。
- [storage.py](app/storage.py)：静态仓库快照、服务管理目录中的动态来源注册/导入状态和 SQLite/FTS5 查询。
- [indexing.py](app/indexing.py) 与 [relations.py](app/relations.py)：Python 快照、符号和保守静态关系提取。
- [tests](tests/)：API、契约、索引、关系和存储测试。

## 接口与依赖

契约位于 [contracts.py](app/contracts.py)，Schema 由 [export_schema.py](app/export_schema.py) 生成。旧 `/api/indexes/*` 保留兼容；项目工作区使用 `/api/projects/*`，本地导入使用 `/api/imports/local`、`/api/imports/{attempt_id}` 和重试入口。项目索引查询必须通过项目归属校验。

## 数据与生命周期

静态仓库由 `CODETRAIL_REPOSITORIES` JSON 对象提供；本地导入允许根目录由 `CODETRAIL_ALLOWED_IMPORT_ROOTS` JSON 数组提供，动态注册和 `ImportAttempt` 写入 `CODETRAIL_DATA_DIR`（默认 API 工作目录 `.codetrail`）。新项目的索引库位于管理目录，不在来源目录写入 `.codetrail`。Project 持有稳定 ID、名称、来源 repository_id、创建时间和索引关联；索引源码及证据仍绑定不可变 `index_id`。导入状态为 `queued/acquiring/indexing/ready/failed/interrupted`，失败和重启中断不替换原有可用索引。

## 使用与配置

从仓库根目录准备服务端仓库映射后启动：

```powershell
$env:CODETRAIL_REPOSITORIES = '{"timeout-demo":"D:\\Dev\\CodeTrail\\fixtures\\repos\\timeout-demo"}'
$env:CODETRAIL_ALLOWED_IMPORT_ROOTS = '["D:\\Dev\\CodeTrail\\fixtures\\repos"]'
$env:CODETRAIL_DATA_DIR = 'D:\\Dev\\CodeTrail\\.codetrail-service'
$env:CODETRAIL_WEB_ORIGINS = 'http://localhost:3000,http://127.0.0.1:3000'
Set-Location apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 健康检查为 `GET /api/health`。`CODETRAIL_WEB_ORIGINS` 只控制浏览器跨源读取来源，不替代 loopback 写入边界。本地写入口只接受 loopback 客户端；`POST /api/imports/local` 同步返回项目、attempt 和索引结果，`GET /api/imports/{attempt_id}` 查询状态，`POST /api/imports/{attempt_id}/retry` 重试失败/中断尝试。前端和测试通过项目 API 创建项目、建立索引，再用 project_id/index_id 查询。

## 错误与限制

本地导入路径必须是服务端绝对路径、位于允许根目录内且不能跨符号链接/junction；服务管理目录与来源目录不能重叠。幂等键全局绑定一次提交，重复来源复用项目，并发重复提交不会创建重复活动 attempt。未知项目、非所属索引和未配置 repository_id 返回稳定 `not_found`；输入边界返回 `invalid_input`，非 loopback 写入返回 `forbidden`。当前导入和索引构建在受限 worker 中同步完成，仍只解析 Python，Run、SSE、Agent 和 GitHub 获取未实现。

## 验证

从 `apps/api` 执行 `uv run pytest -q` 和 `uv run python -m compileall -q app`。项目隔离、旧索引兼容关联、失败不覆盖最新索引、动态本地导入、幂等/并发、路径边界、重试和服务重启恢复由 `tests/test_api.py` 覆盖；真实 HTTP/MCP 双进程导入由 `tests/test_mcp.py` 覆盖。

## 关联文档

- [API 子系统说明](../../docs/subsystems/api.md)
- [存储与查询子系统说明](../../docs/subsystems/storage.md)
- [数据契约](../../docs/subsystems/contracts.md)
- [工作区与项目概览决策](../../docs/decisions/2026-09-11-project-workspace.md)

## 本地只读 MCP 入口

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务。stdout 只承载 MCP 协议帧，日志写 stderr；服务复用 `WorkspaceService`，不直接访问 SQLite，不创建索引，不执行仓库代码。

只读工具为 `list_projects`、`get_project`、`list_indexes`、`get_index`、`search_code`、`read_source` 和 `get_neighbors`。除项目发现外均要求显式 `project_id`，索引查询同时要求 `index_id`；错误以结构化 `code`、`message`、`retryable` 返回。`read_source` 额外返回包含 project/index、行范围、content hash、evidence_id 和工作台 URL 的引用。

设置 `CODETRAIL_MCP_LOCAL_IMPORT_ENABLED=1` 后，额外注册 `import_local_path`、`get_import_status` 和 `retry_local_import`；未设置时 MCP 保持七项只读工具。写工具只能使用服务端已配置的允许根目录，不能通过参数扩大权限。

配置工作台引用地址（默认 `http://localhost:3000`）：

```powershell
$env:CODETRAIL_WORKBENCH_URL = 'http://localhost:3000'
Set-Location apps/api
uv run python -m app.mcp_server
```

用 MCP 客户端启动时，命令为 `uv run --directory apps/api python -m app.mcp_server`，并传入与 HTTP 服务相同的 `CODETRAIL_REPOSITORIES`、`CODETRAIL_DATA_DIR` 和 `CODETRAIL_ALLOWED_IMPORT_ROOTS`。本地导入开关只影响 MCP 写工具；远程 MCP、认证、GitHub 下载和模型调用不开放。

设计与验收见 [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)。
