# API 服务

## 职责与边界

`apps/api` 提供本地 FastAPI 服务、静态仓库 allow-list、受限本地/GitHub 来源导入、Project/Index/Session 管理、索引新鲜度检查与显式更新、规则概览、功能生成，以及显式 fixture/real/external Run 的冻结、执行、事件、SSE、Issue/代码理解报告读取和报告候选的有界静态影响查询。外部 `understanding` Run 还可接收宿主 Agent 识别的功能结果；服务端只校验和保存，不为该提交调用模型。源码索引覆盖 `.py`、`.ts`、`.tsx`；纯 Python 仓库可只用 `uv` 运行 API，仓库含 TypeScript 时，API 主机还必须可运行 Node.js，并使用 CodeTrail 工作区锁定的 `apps/web/node_modules/typescript` 5.9.3。工作区根目录运行 `codetrail init` 会自动发现它；安装 `codetrail` 包本身不包含 Web 或 TypeScript 编译器。解析器缺失或语法错误会显式令新索引失败。API 不执行被分析仓库代码、shell 或测试；real Run 和内置 HTTP 功能生成只通过有界模型适配器请求服务端配置的兼容供应商，external Run 只接收 Codex 通过本地 MCP 发起的白名单工具调用和结构化结论。

## 入口与目录导航

- [api.md](api.md)：应用服务、HTTP 和来源边界。
- [indexing.md](indexing.md)：源码快照、定义与关系提取。
- [storage.md](storage.md)：SQLite、检索、缓存及事务。
- [execution.md](execution.md)：Run、Agent 工具循环、SSE 与证据生命周期。
- [mcp.md](mcp.md)：stdio 启动、宿主配置和独立能力开关。

- [main.py](app/main.py)：FastAPI 应用和 HTTP 路由。
- [service.py](app/service.py)：仓库注册、Project/Index/Session/Run 归属和应用服务。
- [storage.py](app/storage.py)：快照、来源、Session/消息、Run、事件、工具结果、报告、manifest、项目隔离且共享容量的定义/关系事实缓存和 SQLite/FTS5 查询。
- [run_execution.py](app/run_execution.py)：有界 daemon 线程中的 fixture/real Run 执行器，只调用白名单查询工具；external Run 不经过该内部 worker。
- [run_sse.py](app/run_sse.py)：复用持久化事件表的有界 SSE 回放、断线游标和订阅容量管理。
- [issue_analysis.py](app/issue_analysis.py)：real Run 的结构化模型输出、有限工具循环、预算和引用校验。
- [indexing.py](app/indexing.py)、[typescript_parser.cjs](app/typescript_parser.cjs) 与 [relations.py](app/relations.py)：Python/TypeScript 快照、TypeScript AST 定义/导入/保守调用、Python 文件级缓存和静态关系目标解析；TypeScript 每次有界冷解析，不伪装成 Python cache hit。
- [overview.py](app/overview.py)、[feature_generation.py](app/feature_generation.py) 与 [feature_graph.py](app/feature_graph.py)：同快照入口概览、唯一静态 HTTP method/path shape association、功能生成和功能图；HTTP 形状匹配不证明运行时请求目的地。
- [mcp_server.py](app/mcp_server.py)：本地 MCP stdio 适配器，包含可选 Codex external Run 与宿主功能结果提交工具。
- [config.py](app/config.py)：服务数据目录中的持久化模型供应商配置。

## 接口与依赖

邻接结果独立提供有证据的构造上下文调用，静态边、条件记录与 unresolved 分开；语义及限制见 [索引说明](indexing.md)。HTTP、MCP 和 Run 工具复用同一应用服务与固定索引。

本目录的接口与依赖由 [api.md](api.md) 维护；精确字段与行为约束以源码声明为准。

## 数据与生命周期

项目概览分类在同一固定快照中将应用入口、由字面 `__all__` 支持的 Python 公共 API、由真实 pluggy marker 导入与别名链证明的 hookspec、以及当前规则未识别区域分开返回。条目、扫描和 evidence 行有界，分类不生成 `calls` 关系；TypeScript export facts 尚未持久化，相关区域保留未知。

本目录的数据与生命周期由 [api.md](api.md) 维护；精确字段与行为约束以源码声明为准。

## 使用与配置

公开 GitHub 的 HTTP 导入默认开启；设置 github_import_enabled=false 或 CODETRAIL_GITHUB_IMPORT_ENABLED=0 可关闭。MCP GitHub 写入能力仍由独立开关控制。

安装入口使用 `uv tool install ./apps/api`，得到独立 `codetrail` 命令。`codetrail init` 初始化用户设置，`codetrail api` 与 `codetrail mcp` 共用该设置；`install --print-config`、`install`、`status` 和 `uninstall` 管理 Codex 注册。完整配置和覆盖规则见 [mcp.md](mcp.md)。源码启动脚本调用同一 CLI；Web 仍从源码运行。

从仓库根目录启动当前 API 与 Codex external 联合验收环境；脚本使用仓库根目录下的 `.codetrail-runtime`，从该服务数据目录读取已注册 managed source，不需要手工设置临时环境变量。完整步骤见 [MVP 操作说明](../../docs/mvp-operation.md)：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service api
```

API 健康检查为 `GET /api/health`。前端或测试先创建 Project、建立 Index、创建 Session 并追加至少一条已保存消息，再 POST Run 请求；`message_end_sequence` 省略时由服务端读取当前保存消息计数。Run 读取通过同一 Project/Session 的 list/detail/events/tool-results/report 端点，取消使用 cancel 端点。Run 请求可声明预算和幂等键；响应的 Run 状态、输入 hash、消息范围、公开事件、工具结果和报告都是服务端持久化数据。external Run 的工具 request_id 和 start/complete/cancel 由 MCP 固定入口承载；工作台会从 Session Run history 发现 Codex external Run 并订阅其 SSE。

模型供应商配置从服务数据目录的持久化 `config.json` 读取，工作台通过 `/api/model-config` 管理脱敏状态和设置；留空保留已有密钥，明确移除才会清除。API 与 MCP 的新 real 任务读取共享文件，活动 Run 保留创建时配置。缺少密钥时 fixture 功能可用而 real Run 明确失败；配置格式和 DeepSeek 示例见 [MCP 配置](mcp.md)。Run 记录 provider/model、提示词版本和执行版本，但不保存密钥、原始 prompt 或原始模型 response。HTTP real 需设置 `CODETRAIL_REAL_RUN_ENABLED=1`；MCP real 还需设置 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，两个开关相互独立。

## 错误与限制

本目录的错误与限制由 [api.md](api.md) 维护；精确字段与行为约束以源码声明为准。

## 验证

本目录的验证由 [api.md](api.md) 维护；精确字段与行为约束以源码声明为准。

## 关联文档

- [Run 执行子系统说明](execution.md)
- [API 子系统说明](api.md)
- [存储与查询子系统说明](storage.md)
- [数据契约](../../contracts/reference.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [外部 Agent 功能结果提交决策](../../docs/decisions/2026-10-09-external-agent-feature-submission.md)
- [O2 有证据的主要功能语义识别](../../docs/decisions/2026-09-11-feature-semantic-generation.md)
- [MVP 操作说明](../../docs/mvp-operation.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [TypeScript 静态索引与 HTTP 形状关联](../../docs/decisions/2026-10-06-typescript-http-overview.md)
- [库与插件架构概览](../../docs/decisions/2026-10-07-library-plugin-overview.md)
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)
