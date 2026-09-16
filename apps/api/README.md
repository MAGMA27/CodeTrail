# API 服务

## 职责与边界

`apps/api` 提供本地 FastAPI 服务、静态仓库 allow-list、受限本地/GitHub 来源导入、Project/Index/Session 管理、索引新鲜度检查与显式更新、规则概览、功能生成，以及显式 fixture/real/external Run 的冻结、执行、事件、SSE、Issue/代码理解报告读取和报告候选的有界静态影响查询。它不执行被分析仓库代码、shell 或测试；real Run 只通过有界模型适配器请求服务端配置的兼容供应商，external Run 只接收 Codex 通过本地 MCP 发起的白名单工具调用和结构化结论。

## 入口与目录导航

- [main.py](app/main.py)：FastAPI 应用和 HTTP 路由。
- [service.py](app/service.py)：仓库注册、Project/Index/Session/Run 归属和应用服务。
- [storage.py](app/storage.py)：快照、来源、Session/消息、Run、事件、工具结果、报告、manifest、项目隔离且共享容量的定义/关系事实缓存和 SQLite/FTS5 查询。
- [run_execution.py](app/run_execution.py)：有界 daemon 线程中的 fixture/real Run 执行器，只调用白名单查询工具；external Run 不经过该内部 worker。
- [run_sse.py](app/run_sse.py)：复用持久化事件表的有界 SSE 回放、断线游标和订阅容量管理。
- [issue_analysis.py](app/issue_analysis.py)：real Run 的结构化模型输出、有限工具循环、预算和引用校验。
- [indexing.py](app/indexing.py) 与 [relations.py](app/relations.py)：Python 快照、只读源码 manifest、可复用的文件级定义/关系事实提取、符号和保守静态关系目标解析。
- [overview.py](app/overview.py)、[feature_generation.py](app/feature_generation.py) 与 [feature_graph.py](app/feature_graph.py)：概览、功能生成和同快照功能图。
- [mcp_server.py](app/mcp_server.py)：本地 MCP stdio 适配器，包含可选 Codex external Run 工具。
- [config.py](app/config.py)：服务数据目录中的持久化模型供应商配置。

## 接口与依赖

契约位于 [contracts.py](app/contracts.py)，Schema 由 [export_schema.py](app/export_schema.py) 生成。旧 `/api/indexes/*` 保留兼容；项目工作区使用 `/api/projects/*`。Run 的 HTTP 入口为 `/api/projects/{project_id}/sessions/{session_id}/runs` 及其 detail、events、`events/stream`、tool-results、report、impact、cancel 子路径；fixture/real 创建只做边界校验、冻结、容量预留和提交，worker 启动成功后返回 queued 的 run_id。external Run 只从 MCP `start_external_run` 创建，冻结同样的 Session 输入但不占内部 worker；`analysis_kind=understanding` 只走 external，`run_search_code`、`run_read_source`、`run_get_neighbors` 记录服务端实际结果，`get_neighbors` 的 `truncated` 表示查询预算，`index_truncated` 表示固定关系扫描覆盖，`index_unresolved_truncated` 表示 unresolved 详情覆盖。`submit_external_run` 可提交 Issue 结论或最多 12 步的 explanation，且只接受本 Run 返回的同快照证据/符号；explanation 不产生候选链接。容量满时内部 Run 返回可重试的 `budget_exceeded`/429，不写入 Run。`events/stream` 在握手前校验 project/session/run 与 `after_sequence`/`Last-Event-ID`，按 `{run_id}:{sequence}` 逐帧回放持久化公开事件，终态后关闭，SSE 不可用时由工作台自行回退轮询。`impact` 只按已保存报告中的一个 candidate/finding ID 查询 Run 固定 index 上的静态 `calls`，并返回固定索引覆盖事实；限制方向、深度、节点、路径和 unresolved 输出，不修改 Run。执行由 [Run 执行子系统](../../docs/subsystems/execution.md)负责。所有项目索引、Session 和 Run 操作都复用 WorkspaceService 的归属校验。

`search_code` 与 `run_search_code` 使用同一 `IndexStore` 搜索语义：先按完整限定名（也接受限定名后缀）精确匹配，再按单段叶子名精确匹配、名称部分匹配，最后才按符号自身源码范围的正文匹配；名称层完成后才进入正文层，避免正文候选挤掉精确定义。每层只读取有界批次，正文候选默认每批 32 行、每次搜索最多检查 256 个候选、读取 32 个文件和 256 KiB UTF-8 源码，并为每条 SQL 安装 SQLite progress handler；同一文件在一次搜索中只读取和切分一次。排序在同层按限定名、路径和稳定 `symbol_id` 确定，最多应用 40 条的 `limit`；预算耗尽返回已有结果并以 `search budget exceeded: ...` 说明原因，只有确认还有更多结果时才报告 `max_results reached`。现有 `symbol_fts` 仍是文件级候选索引，正文命中会按不可变 `start_line/end_line` 复核；模块符号有意覆盖整个文件以保留文件导航，函数/类不会因同文件其他符号命中而被带入。HTTP、普通 MCP 和绑定 Run 复用同一规则。该改动不改变 FTS 表格式、快照 hash、Symbol ID 或历史 Run，因此已有索引无需重建。

`GET /api/projects/{project_id}/indexes/{index_id}/overview` 从同一不可变快照生成确定性的 `python-entry-v4` 概览。除 `__main__.py`/main guard 外，`fastapi_route` 入口还会保存已确认的 HTTP 方法、声明路径、处理函数 symbol、装饰器到函数结束的 evidence；概览同时生成 `http_path` 和 `source_directory` 结构组，并把固定快照中可映射的跨模块 `imports`/`calls` 聚合为带代表源码位置的模块关系。处理函数仍沿既有静态图展开，入口绑定本身不新增 CodeEdge；结构组和聚合关系也不会写回静态边。HTTP 组按首个 `{parameter}` 前的声明路径静态前缀及 handler 模块分组，源码组按相对目录分组；它们都不是业务功能。只识别已知 `FastAPI()` 实例、字面量路径和静态方法，声明路径不包含未解析的 router/部署前缀；未展开语句中的可能绑定写入会保守使已知绑定失效，不把后续不确定装饰器升级为路由。结构输出由 `max_groups`（默认 80、最大 200）和 `max_module_relations`（默认 200、最大 400）独立限制，代表边最多保留 8 条并以 `edge_count` 说明总量。

`POST /api/projects/{project_id}/indexes/{index_id}/freshness` 以同一源码规则读取有界内容 hash manifest，返回 `consistent`、`changed` 或 `unknown`，不解析 AST、不创建索引；`POST /api/projects/{project_id}/indexes/{index_id}/refresh` 只允许 loopback，在完整安全扫描下可修复旧 metadata 与源码/规则/配置变化的组合，服务端复核后返回 `reused`、`historical_reused` 或 `created`：一致时复用当前 latest，源码恢复到同项目中规则/配置/schema 和完整 manifest 均兼容的历史快照时复用该快照并推进 latest，否则生成新 immutable index，其他不确定情况失败并保留旧 latest。历史快照的 revision/dirty 等来源描述不原地改写，响应保留其原 metadata。新快照构建先捕获源码，再用项目隔离的路径、内容 hash、对应事实规则、Python AST 版本和配置 hash 查询严格 JSON 定义/关系事实缓存；定义缓存命中后按新 index 重建 Symbol，关系事实命中后仍基于新快照 DefinitionMaps 全量解析目标，不复用旧跨文件边。两类缓存合计默认 5,000 条/16 MiB，坏缓存安全降级，缓存不是源码 evidence 来源。HTTP 与 MCP 复用同一 WorkspaceService 和刷新语义。

## 数据与生命周期

静态仓库由 `CODETRAIL_REPOSITORIES` JSON 对象提供；动态来源注册和导入状态写入 `CODETRAIL_DATA_DIR`，索引库归属于服务管理目录或来源的既有存储。Session 在创建时固定 ready `index_id`，消息由事务分配连续 sequence。Run 在同一索引库中保存不可变的 `project_id/session_id/index_id`、execution source、可选同快照 feature generation、完整已保存消息前缀及 hash、预算、状态、事件、工具结果和报告；每个 Session 最多一个 `queued/running` Run。服务启动把未完成的内部 Run 标记为 `interrupted`；external Run 在仍处于其创建时间预算内时保留，超时或后续活动请求只中断自身。取消与重启都保存终态事件和终态报告，迟到工具结果不会落库。进程内内部 Run 执行容量由 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS` 配置，默认 2；external Run 不占用该槽位，没有内部等待队列。SSE 订阅使用 `CODETRAIL_RUN_SSE_MAX_CONNECTIONS`、`CODETRAIL_RUN_SSE_MAX_PER_RUN`、`CODETRAIL_RUN_SSE_BATCH_SIZE`、`CODETRAIL_RUN_SSE_POLL_INTERVAL_SECONDS`、`CODETRAIL_RUN_SSE_IDLE_HEARTBEAT_SECONDS`、`CODETRAIL_RUN_SSE_MAX_LIFETIME_SECONDS` 和 `CODETRAIL_RUN_SSE_MAX_EVENT_BYTES` 配置有限资源，默认总连接 8、单 Run 2、批次 20、最长 300 秒。

fixture Run 继续接受三个场景：候选、证据不足和工具失败。real Run 每轮由模型选择一个 `search_code`、`read_source` 或 `get_neighbors` 请求，或返回结构化候选/证据不足结论；每个请求都包含完整冻结 Issue/消息和已返回证据，最多做一次有限格式修复。external Run 由 Codex 通过 MCP 逐次选择同一组白名单工具，服务端只持久化实际返回；结论必须使用本 Run 返回的同快照证据和符号，且按创建时间兑现时间预算。执行器使用实际索引工具结果组装报告，不执行仓库源码、shell 或测试。所有报告 evidence 和图节点绑定 Run 的固定 `index_id`；影响查询重新读取该固定快照的静态 `calls`，不把 imports、dynamic/unresolved 或模型候选升级为确定影响；草稿、后续消息和后续 latest index 不会改变已保存 Run 输入。

`Run.analysis_kind=understanding` 仅用于 external Run。Codex 可在 `submit_external_run` 中提交 `outcome=explanation` 和 `explanation`：最多 12 个有序步骤，confirmed 步骤必须有本 Run 返回的 evidence，complete 不得含 unknown；服务端拒绝跨 Run/跨 index 的 evidence 或 symbol，也不为 explanation 生成 findings、candidate links 或影响结论。旧 Run/report 缺少该字段时按 Issue 语义读取，不迁移历史 JSON。

新索引记录源码捕获规则版本和配置指纹；新鲜度扫描默认最多读取 2,000 个 Python 文件、20 MiB 和 2 秒，使用内容 hash 而不是 mtime/size。旧索引缺少这些元数据时检查返回 `unknown`；若扫描完整且安全，显式更新可与源码/规则/配置变化一起建立新快照，读取/边界/预算/TOCTOU 不确定则保留旧 latest。GitHub 来源返回 `remote_not_checked`，不会访问已清理的临时目录。MCP 的 `check_index_freshness` 默认只读可用；设置 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1` 才开放 `refresh_index`，且该开关不开放 Session 或 Run 写入。

## 使用与配置

从仓库根目录启动当前 API 与 Codex external 联合验收环境；脚本固定使用 `D:/Dev/CodeTrail-runtime`，从该服务数据目录读取已注册 managed source，不需要手工设置临时环境变量。完整步骤见 [MVP 操作说明](../../docs/mvp-operation.md)：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service api
```

API 健康检查为 `GET /api/health`。前端或测试先创建 Project、建立 Index、创建 Session 并追加至少一条已保存消息，再 POST Run 请求；`message_end_sequence` 省略时由服务端读取当前保存消息计数。Run 读取通过同一 Project/Session 的 list/detail/events/tool-results/report 端点，取消使用 cancel 端点。Run 请求可声明预算和幂等键；响应的 Run 状态、输入 hash、消息范围、公开事件、工具结果和报告都是服务端持久化数据。external Run 的工具 request_id 和 start/complete/cancel 由 MCP 固定入口承载；工作台会从 Session Run history 发现 Codex external Run 并订阅其 SSE。

模型供应商配置从服务数据目录的持久化 `config.json` 读取，缺少密钥时 fixture 功能可用而 real Run 明确失败；配置格式和 DeepSeek 示例见下方 MCP 入口说明。Run 记录 provider/model、提示词版本和执行版本，但不保存密钥、原始 prompt 或原始模型 response。HTTP real 需设置 `CODETRAIL_REAL_RUN_ENABLED=1`；MCP real 还需设置 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，两个开关相互独立。

## 错误与限制

本地/GitHub 导入继续执行绝对路径、allow-list、符号链接/junction、官方 URL、归档安全、时间和大小边界；HTTP 写入口只接受 loopback 客户端。未知项目、非所属索引/Session/Run 返回 `not_found`，参数、预算和执行容量边界返回 `invalid_input` 或可重试的 `budget_exceeded`，失败不泄露宿主路径。

Run 拒绝未开启能力的 `mode=real` 或 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED`、拒绝空 Session、跨项目/Session/index/generation，冻结完整消息前缀而不是前端已加载页；草稿不会进入输入。单次/总源码行、工具调用、模型调用/修复、模型输入/输出字符、输入/输出字符、事件、总时间和进程并发均有服务端上限；状态只能从 queued/running 进入一个终态，终态事件不可重复，取消幂等，重启中的 Run 标为 interrupted。关系扫描和 unresolved 详情分别受有界预算控制；前者未完成与后者未知/截断都不能被空邻接解释为没有关系，旧 index 的 NULL 只表示尚未记录。概览另受总计算时间、200 个入口候选、模块/组/模块关系上限约束；模块关系只显示两个端点都能映射到同一固定快照模块的静态边，unresolved、无法映射和自模块边不进入聚合；源码目录组即使包含因 `max_modules` 未返回的模块，也会在下钻时明确显示预算遗漏。只支持模块/函数体直接语句中的已声明 FastAPI 实例路由，不展开控制流块或类体，也不解析 `APIRouter`、`include_router`、`mount`、动态路径或运行时注册，未识别入口不表示项目没有路由。external 不执行内部模型，且结论不能引用本 Run 未实际返回的证据或符号；终态拒绝迟到工具和结论。模型供应商缺失、请求失败、非法结构化输出和预算耗尽明确失败，不回退 fixture；Run 历史、事件和工具结果分页均有上限并带作用域游标。SSE 的跨 Run、未来游标、游标冲突和订阅容量拒绝在流建立前返回结构化错误；断线只释放订阅，不取消或重启 Run，终态游标收到 `run.stream.end` 控制帧后有限结束；容量满不落库且不影响查询/取消/health；取消/中断报告无候选结论，失败报告保留已持久化的同快照 evidence。影响查询要求报告中的 candidate/finding 精确归属；找不到唯一源符号时返回有解释的空 unmapped 结果，找到后只遍历同快照 `calls`，最大深度 2、默认节点 40，并保留 cycles 的静态边和 unresolved 未知边界。

代码理解 external Run 使用 `analysis_kind=understanding`，只能提交 `outcome=explanation`；服务端拒绝未返回的步骤 evidence/symbol、重复步骤 ID、确认步骤无证据、complete 含 unknown，以及任何 Issue findings/candidate links。理解结果的步骤顺序是 Agent 的解释顺序，不等同于静态 calls 图，也不承诺未索引的 TypeScript、动态注册或完整运行时行为。

## 验证

从 `apps/api` 执行 `uv run pytest -q` 和 `uv run python -m compileall -q app`。`tests/test_runs.py` 覆盖 fixture 生命周期和固定 Run 报告影响查询；`tests/test_storage.py` 与 `tests/test_definition_cache.py` 覆盖静态路径、节点去重、环、容量边界、文件定义/关系事实缓存的冷/热复用、共享 AST、重启、失效、损坏、并发和失败降级；`tests/test_run_sse.py` 覆盖持久化逐帧回放、双游标续接、终态控制帧、未来/跨 Run 拒绝、断开释放容量和 health 并发；`tests/test_issue_runs.py` 覆盖 real 工具决策、证据不足、非法引用/工具、有限修复、预算、取消、持久化 config.json 和 OpenAI-compatible 请求；`tests/test_mcp.py` 覆盖 HTTP/MCP Run 与影响查询对照、默认只读、独立 Run 写开关和 real 能力开关，以及 external 的实际 stdio 工具、证据边界、幂等、时间预算、取消和终态拒写。其它项目/索引/导入/Session/O1/O2/O3 测试继续覆盖各自边界。本轮全量结果以本轮交付记录为准，compileall 通过；前端和契约未因本步改变，真实 DeepSeek 语义质量仍未验证。

## 关联文档

- [Run 执行子系统说明](../../docs/subsystems/execution.md)
- [API 子系统说明](../../docs/subsystems/api.md)
- [存储与查询子系统说明](../../docs/subsystems/storage.md)
- [数据契约](../../docs/subsystems/contracts.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [MVP 操作说明](../../docs/mvp-operation.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)

## 本地 MCP 入口

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务；stdout 只承载协议帧，日志写 stderr。服务复用 WorkspaceService，不直接访问 SQLite，不创建索引，不执行仓库代码。

默认只读工具包括项目、索引、`check_index_freshness`、Session/消息、概览、功能历史/版本/图，以及 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`、`get_run_impact`。新鲜度检查按当前本地源码内容 hash 做有界只读核对，不创建索引；Run 读取和影响查询只返回持久化报告及其固定快照上的静态结果，不重新执行分析。

设置 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1` 后，独立增加 `refresh_index`；它只允许对项目当前 latest 做有界复核，在完整安全扫描下对旧 metadata/源码/规则/配置变化执行显式当前复用、兼容历史快照复用或重建，失败保留旧 latest。设置 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 后，独立增加 `start_session_run` 和 `cancel_session_run`；它允许显式选择 fixture 或 real，但 real 还要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会因为 Run 写开关开放 `create_session` 或 `append_session_message`。Session 写入仍需 `CODETRAIL_MCP_SESSION_WRITE_ENABLED=1`。未设置时 MCP 保持只读工具，默认只读工具不会调用模型或 Run。

设置 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 后，独立增加 `start_external_run`、`run_search_code`、`run_read_source`、`run_get_neighbors`、`submit_external_run` 和 `cancel_external_run`，供 Codex 作为外部 Agent 驱动一次调查。start 只冻结已有 Session 的消息和固定 index，不占内部 Run worker；可选 `analysis_kind=understanding` 将 Run 标记为代码流程理解；每次工具调用必须带 Run 作用域和 request_id，服务端保存实际返回的结果，重复 request_id 只复用相同参数的已保存结果。submit 接受 `candidate_found`、`insufficient_evidence` 或 `explanation`；explanation 必须携带有序步骤，所有 evidence、symbol 和 Issue 字段都必须遵守该 Run 的实际工具结果与分析类型边界，且 explanation 不产生 candidate link。报告带有公开限制，超时、取消和终态写入均不可伪造或重复。工作台通过 Session Run history 自动发现该 Run，并用同一 Run 的 SSE 展示公开事件。

Codex 的本地 stdio 配置示例（Windows）如下；仓库映射必须使用服务数据目录中已存在的 repository ID，`CODETRAIL_DATA_DIR` 需与 API 共用：

```toml
[mcp_servers.codetrail]
command = "uv"
args = ["run", "--directory", "D:/Dev/CodeTrail/apps/api", "python", "-m", "app.mcp_server"]
startup_timeout_sec = 120

[mcp_servers.codetrail.env]
CODETRAIL_DATA_DIR = "D:/Dev/CodeTrail-runtime"
CODETRAIL_REPOSITORIES = "{}" # 使用 CODETRAIL_DATA_DIR 中已注册的 managed source
CODETRAIL_WORKBENCH_URL = "http://localhost:3000"
CODETRAIL_MCP_EXTERNAL_RUN_ENABLED = "1"
```

需要通过 MCP 显式更新索引时，再单独加入 `CODETRAIL_MCP_INDEX_WRITE_ENABLED = "1"`；默认不注册 `refresh_index`。修改 MCP 代码后对 `codetrail` 做一次 reload 或重启 Codex，服务端运行目录无需清理。

配置文件由 Codex 宿主加载；修改 MCP 代码后对 `codetrail` 做一次 reload（或按宿主能力重启 Codex）即可。API/MCP 不应各自指向仓库根目录下的默认 `.codetrail`，也不应通过手工项目/索引 ID 绕过列表发现。

功能生成和 Run 的 `real` 模式默认适配 DeepSeek，从服务数据目录的 `config.json` 读取配置；例如：

```json
{
  "provider": "deepseek",
  "api_key": "sk-你的 DeepSeek API Key",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash"
}
```

不要提交真实密钥；配置文件位于被 Git 忽略的服务数据目录。设计与验收见 [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)、[Run 决策](../../docs/decisions/2026-09-12-run-execution.md)、[Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)、[代码理解流程与证据报告决策](../../docs/decisions/2026-09-14-code-understanding-explanation.md)、[本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md) 和 [文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)。
