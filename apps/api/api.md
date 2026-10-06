# API 与工具适配

## 状态

已实现 Project/Index/Session、来源导入、O1 `python-entry-v5+http-shape-v2` 概览（入口、HTTP/源码结构组、聚合模块关系及同快照 HTTP 客户端/路由方法路径形状关联）、O2/O3 功能生成与 P2-R1/P2-R2 fixture/real Run 的 HTTP 和本地 MCP 入口；索引支持本地 `.py`/`.ts`/`.tsx` 源码 freshness 与显式更新，Python 文件缓存和 TypeScript 有界冷 AST 解析隔离。Run 已覆盖冻结输入、受限工具执行、单 Agent 有界模型循环、公开事件、持久化 SSE 回放/续接、Issue/代码理解报告、取消和重启中断；P3-I1 已增加从保存报告候选/发现读取固定快照的有界静态影响查询；Codex external Agent 已能通过本地 MCP 创建并驱动同一持久化 Run。HTTP method/path association 仅表达静态形状一致，不声称运行时网络目标；真实供应商语义质量仍未验证。

## 职责与范围

将服务端允许的仓库或来源映射到 Project、不可变 Index、Session 和受限 Run；统一承担 project/session/index/generation 归属、预算、错误和幂等语义。HTTP 面向工作台，MCP 面向本地 Agent；两者不各自实现一套 Run 业务逻辑。该子系统不执行被分析仓库代码、shell、测试或隐藏思维链，real Run 只使用服务端配置的 OpenAI-compatible 模型传输，external Run 只执行服务端白名单代码查询工具并接收结构化结论。

## 接口与依赖

[service.py](app/service.py) 提供应用服务；[main.py](app/main.py) 提供 HTTP 路由；[mcp_server.py](app/mcp_server.py) 提供 stdio 工具注册和结构化错误；[run_execution.py](app/run_execution.py)、[run_sse.py](app/run_sse.py) 和 [issue_analysis.py](app/issue_analysis.py) 使用 [IndexService](app/service.py) 的实际 search/read/neighbors 工具结果或持久化事件。精确请求、状态、事件、SSE 帧和报告形状由 [contracts.py](app/contracts.py) 声明，Schema 由 [export_schema.py](app/export_schema.py) 生成。

`search_code`、HTTP 搜索和 external Run 的 `run_search_code` 都委托同一 `IndexStore`。搜索先按有界名称层返回完整限定名（支持限定名后缀）、叶子名精确和名称部分命中，再按有界 FTS 批次复核符号自身源码范围内的正文命中；名称层先完成，因此精确定义不会被正文候选挤出。每次搜索共享服务端候选、源码文件/字节和 0.5 秒预算，SQL 使用 progress handler；同一文件只读取一次。结果保留 `index_id`、稳定 Symbol 身份、rank、matched terms、snippet 和截断说明，预算停止使用 `search budget exceeded: ...`，已确认还有更多结果才使用 `max_results reached`；模块正文范围仍是整文件，函数/类只使用自身行范围。HTTP、普通 MCP 与绑定 Run 不各自实现排序或预算规则。

`POST /api/projects/{project_id}/indexes/{index_id}/freshness` 调用共享的 freshness 检查，返回 `consistent`、`changed` 或 `unknown` 的已执行观察，包含基准 index、检查时间、变化计数、受限路径、扫描统计和机器可读原因；本地来源按内容 hash 比较，不执行 AST 或关系提取，快照与 freshness 使用相同的 `.py`/`.ts`/`.tsx` 范围和预算，并共同排除 `.next`、`.turbo`、`.vercel`、`.vite`、`.parcel-cache`、`coverage`、`out`、`storybook-static`、`target` 及既有依赖/运行数据目录。源码规则为 `source-v3`，规则身份包含锁定 TypeScript `5.9.3` 与 `typescript-facts-v2`；含 TypeScript 的 API 部署须具备 Node.js 和 `apps/web` 锁定编译器。GitHub 来源调用共享 `GitHubClient.resolve` 做 metadata-only 的 ref→commit 检查，附带 `remote_status`、canonical URL、请求/解析 ref 与远端 commit，不下载归档；远端状态和本地规则/配置状态分别表达。当前关系语义为 `relations-v4`，参与 freshness 的 `rules_version`；当前关系事实缓存规则/格式为 `python-relation-facts-v2` / `relation-facts-v2`，只控制事实 payload 命中；当前索引构建身份为 `relations-v7`，用于新 immutable index ID。源码捕获规则或配置变化时，freshness 会明确报告 `rules_changed`/`configuration_changed`，不能复用不兼容的旧 index。`POST /api/projects/{project_id}/indexes/{index_id}/refresh` 沿用 loopback 写约束并只处理本地来源；GitHub 使用同样 loopback 限制下独立的 `POST /api/projects/{project_id}/indexes/{index_id}/github-refresh`，服务端再次复核并通过受限导入更新。GitHub 更新在同一 commit 且构建身份兼容时复用当前或历史 snapshot，规则/配置变化时重建；检查与下载之间 ref 移动时以实际 attempt 的 commit/ref 为准。失败或远端未知保留旧 latest，历史快照的 revision/dirty 等来源 metadata 不原地改写。HTTP 与 MCP 复用同一 WorkspaceService；MCP 始终注册 `check_index_freshness`，本地 `refresh_index` 需 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1`，GitHub `refresh_github_source` 还需两个 GitHub 导入开关。

Run HTTP 包括 create/list/detail/events/`events/stream`/tool-results/report/impact/cancel；create 接受显式 `mode=fixture|real`，real 还要求 `CODETRAIL_REAL_RUN_ENABLED=1`，执行器有空闲预留名额且 worker 启动成功后显式返回 queued `run_id`，不在请求线程执行分析。`get_overview` 从固定 index 生成有界概览；`python-entry-v5+http-shape-v2` 的 `fastapi_route` 入口保存方法、声明路径、处理函数 symbol 与同快照 evidence，结构组按声明路径静态前缀/handler 模块和源码目录生成，模块关系只聚合同快照可映射的 `imports`/`calls`，附最多 8 条代表边。静态 `requestJson` 调用若能 AST 证明透明转发给 `fetch`，字面量路径或完整段 `encodeURIComponent` 模板仅在与唯一 FastAPI 路由完整同形时生成 `method_path_shape` association，client/server source evidence 共用一个 index_id；覆盖不足标 unknown、重叠候选标 ambiguous、query 只接受安全的顶层 `URLSearchParams` 尾段，不写成 CodeEdge，也不证明请求目的地为本地服务器。旧 `typescript-facts-v1` snapshot 保留 literal-only 语义，coverage 单独标 `path_analysis=literal_only`。结构输出通过 `max_groups`（80/200）和 `max_module_relations`（200/400）独立限制，覆盖字段区分总量/可见量；静态路径仍从处理函数沿既有图展开，入口绑定和聚合关系均不生成 CodeEdge。`mode=external` 只由 MCP external 工具创建，服务端冻结输入但不占内部 worker；后续固定的 `run_search_code`、`run_read_source`、`run_get_neighbors` 和 `submit_external_run` 由同一 WorkspaceService 执行、持久化并校验证据。内部 Run 与 external Run 共用 `IndexService.execute_tool`；Run 工具 `get_neighbors` 的 ToolResult 最多保留 40 条 unresolved 详情，超出时以 `truncated`/`truncation_reason` 报告结果层截断，固定索引的 unresolved 覆盖字段保持独立；普通 HTTP/MCP neighbors 查询保持原查询结果语义。容量满时在内部 Run 落库前返回可重试的 `budget_exceeded`/429。`events/stream` 复用事件表，以 `{run_id}:{sequence}` 和 `after_sequence`/`Last-Event-ID` 实现严格续接；总连接、单 Run 连接、批次、事件大小、心跳和生命周期均有界，满载在握手前返回 429。`impact` 要求恰好一个报告内 candidate/finding ID，由 WorkspaceService 在 Run 固定 index 上遍历同快照静态 `calls`，深度最多 2、节点默认/最多 40，并返回解释路径与未知边界；它不启动 Run、不调用模型。默认 MCP 增加 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`、`get_run_impact`；`CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 才增加独立的 `start_session_run` 和 `cancel_session_run`，`CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 才增加 Codex external 工具，MCP real 另要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会隐式开放 Session 写入。

GitHub 和本地索引更新先登记项目级持久化 update intent；expected latest 贯穿检查、历史复用/下载、构建和 `finish_project_index` 的 CAS。latest 成功后，来源 identity 与 attempt ready 在管理库单事务中收尾；跨库故障留下可恢复 intent，服务重启只对 owner 明确 `exited` 的 intent 按候选 ready latest 补提交或终止。owner 探测为 `alive`、`exited`、`unknown` 三态，Windows 使用只读进程句柄查询，权限不足或未知错误保持 `unknown` 并保护该项目的 active attempt；待恢复 intent 存在时同项目更新返回 conflict。HTTP 与 MCP 都复用这套 WorkspaceService 行为，错误不把旧 latest/source 误报为新 commit 成功。

## 数据与生命周期

Run 的 `analysis_kind` 默认是 `issue`；只有 external Run 可声明 `understanding`。该类型提交 `CodeUnderstandingFinal`，包含最多 12 个有序 `ExplanationStep`、`complete/partial` 覆盖和同一 Run 返回的源码 evidence；confirmed 必须有 evidence，complete 不能含 unknown，服务端不生成 findings/candidate links。旧 Run JSON 缺少该字段时按 Issue 读取，不做存储迁移。

创建 Run 时校验 Session 的固定 ready index、可选同快照 feature generation 和消息 cutoff；省略 cutoff 时以服务端当前保存消息计数为线性化点，并读取完整连续消息前缀，保存 input hash、字符数和范围。Run、RunInput、事件、工具结果和报告由 [IndexStore](app/storage.py) 同一数据库事务保存；Session 最多一个 queued/running Run，幂等键绑定请求指纹。

本地 executor 在 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS`（默认 2）上做非阻塞准入，只保留有限的 daemon worker，不引入持久或内部等待队列、多 Agent。索引新鲜度扫描默认最多读取 2,000 个 `.py`/`.ts`/`.tsx` 文件、20 MiB 和 2 秒；变化路径最多返回 100 条，扫描预算与路径展示上限分开。定义描述和关系事实缓存默认每个项目/仓库合计最多 5,000 条和 16 MiB，损坏或不兼容按未命中处理；关系事实只保存严格 JSON 的导入 alias、作用域绑定/遮蔽、构造赋值/变量失效、类继承/装饰器安全标记、有序调用目标和位置，不保存 AST、旧 Symbol ID、最终边或 evidence，`read_source` 仍读取固定 snapshot。关系目标解析仅在直接 `obj = KnownClass(...)` 且类及直接方法唯一、无继承/影响绑定装饰器/已知属性覆盖时形成 `obj.method(...)` 的静态边，其他对象方法保留 unresolved。fixture 和 real 的工具计划只调用 `search_code`、`read_source`、`get_neighbors`；real 每轮由模型返回一个 Pydantic 校验的工具请求或最终结论，最多一次有限修复。external Run 在首个外部工具或结论请求时写入 `run.started`，不经过内部 executor；每个外部 `request_id` 绑定原始参数和实际结果，重复调用只返回已持久化结果。`submit_external_run` 的成功回执与持久化的 succeeded Run/Report 共用同一 `report_id` 和终态序号；连接在持久化后中断时，使用完全相同的结论重试会复用原报告和 evidence references，不追加终态事件；不同结论返回 `conflict`，不创建补偿 Run。未知 `internal_error` 不能当作成功，客户端应先读取 Run/report，再决定是否用相同内容重试。服务端按创建时间兑现 external 的 `max_seconds`，超时只中断当前 Run；成功、证据不足和工具失败报告均由这次执行的真实结果组装，证据/图绑定冻结 index_id。公开事件按连续 sequence 写入，queued/running 只能进入一次 succeeded/failed/cancelled/interrupted 终态；模型调用预算在请求前预留，输出只记录输出字符/token 用量，不保存原始模型文本；容量预留在存储失败、worker 启动失败、取消、超时、异常和完成路径恰好释放，晚到结果因终态事务拒绝。影响读取不持久化派生结果，且从报告映射失败时返回 unmapped 空结果，不按同名符号猜测。

## 错误与限制

本地/GitHub 导入继续执行绝对路径、allow-list、符号链接/junction、官方 URL、归档安全、时间和大小边界；HTTP 写入口只接受 loopback 客户端。未知项目、非所属索引/Session/Run 返回 `not_found`，参数、预算和执行容量边界返回 `invalid_input` 或可重试的 `budget_exceeded`，失败不泄露宿主路径。

代码理解提交还会拒绝重复步骤 ID、未实际返回的步骤 evidence/symbol、分析类型与 Run 不匹配、`complete` 中的 unknown，以及任何 Issue findings/candidate links；步骤顺序是 Agent 解释顺序，不改变静态图或影响查询语义。

Run 拒绝空消息、跨 Project/Session/Index/Generation、不合法 cutoff 和越界预算；索引 freshness 对本地来源读取源码 manifest，对 GitHub 来源只解析跟踪 ref 的远端 commit。两者的文件读取、来源边界、TOCTOU 或扫描预算异常都返回 `unknown`，不能当作一致；GitHub 的显式更新使用独立 `github-refresh`，只接受项目当前 latest，服务端复核/下载失败不替换旧 latest，检查与下载间 ref 移动时以实际导入 commit 为准。real 只有在对应显式能力开关和服务端 `config.json` 有效时才会执行，external 还要求 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 和 Codex 来源。输入/工具调用、单次/总源码行、输入/输出字符、模型调用/修复、模型输入/输出、事件、总时间和并发都有服务端上限。概览识别另受 2 秒默认总时间、200 个入口候选、模块/组/模块关系预算约束；组总量来源于固定快照，即使组内模块因 `max_modules` 不可见也会在下钻时说明。仅支持模块/函数体直接语句中直接确认的 FastAPI 实例、字面量路径和静态方法，不展开控制流块或类体，也不解析 `APIRouter`、`include_router`、`mount`、动态路径或运行时注册。活动 Run、幂等语义不一致和快照冲突返回 conflict，未知归属返回 not_found，模型不可用、模型失败、非法结构化输出、预算或容量失败返回明确错误；real 失败不回退 fixture，external 不能提交未由本 Run 实际返回的证据或符号。容量错误可重试，且不写入 queued Run。报告只引用已持久化的同快照工具 evidence；取消/中断报告不生成候选结论。读接口带 project/session/run 作用域和有界游标，默认 MCP 读工具不会启动执行，external 终态拒绝后续写入。

模型配置与启动见 [服务入口](README.md)，MCP 开关见 [MCP 配置](mcp.md)；GitHub freshness/update 的取舍见 [GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)；Run 的设计取舍见 [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)、[Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)、[Run 影响查询决策](../../docs/decisions/2026-09-13-run-impact.md) 和 [代码理解流程与证据报告决策](../../docs/decisions/2026-09-14-code-understanding-explanation.md)。

## 使用与配置

当前本地联合环境使用 [共享启动脚本](../../scripts/start-local.ps1)，让 API 使用仓库根目录下 `.codetrail-runtime` 中已有的 managed source；API 与 Codex MCP 必须共用该目录。完整步骤见 [MVP 操作说明](../../docs/mvp-operation.md)：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service api
```

MCP 代码更新后由 Codex 宿主对 `codetrail` 做一次 reload（或按宿主能力重启 Codex）；仓库不清理运行目录，也不通过手工项目/索引 ID 绕过列表发现。修改 MCP 代码后需要 reload/重启才能发现新增工具，服务端运行目录无需清理。

## 验证

`apps/api/tests/test_runs.py` 验证 fixture 生命周期和固定报告影响查询；`apps/api/tests/test_storage.py` 验证有界静态路径、环、去重、manifest 和 latest 冲突；`apps/api/tests/test_freshness.py` 验证内容 hash、预算未知、显式复用/重建、旧 metadata 和旧 latest 保留；`apps/api/tests/test_process_liveness.py` 验证 Windows 只读句柄探测的存活/退出/未知分支、句柄关闭和 unknown 恢复保护；`apps/api/tests/test_github_process_lifecycle_script.py` 验证验收脚本的 release 超时、阶段报告、默认单场景和 worker 回收/日志保留；`apps/api/tests/test_run_sse.py` 验证持久化逐帧回放、双游标续接、终态控制帧、未来/跨 Run 拒绝、断开释放容量和 health 并发；`apps/api/tests/test_issue_runs.py` 验证 real 的模型工具协议、不同问题的工具计划、证据不足、非法引用/工具、有限修复、预算、持久化配置和 OpenAI-compatible 请求、供应商失败、取消与终态容量；`apps/api/tests/test_mcp.py` 验证 HTTP/MCP 新鲜度对照、独立 index 写能力、同一 Run、影响结果和 Codex external 的实际工具结果、代码理解有序步骤、证据拒绝、幂等、时间预算、取消与终态拒写；`apps/api/tests/test_overview.py` 另验证结构组、模块关系、预算和固定快照。双进程脚本默认只运行 `http-a-mcp-b`，`--scenario all` 才显式运行全部离线场景；每个场景有父进程总超时、worker release 超时、阶段日志和受控失败日志，取消时释放 gate、退出 MCP context 并只回收脚本创建的 HTTP worker。实际进程场景验收见相关决策；浏览器、公开 GitHub 和真实 DeepSeek 语义质量仍为 UNVERIFIED。

现有 `apps/api/tests/test_definition_cache.py` 覆盖定义描述/关系事实缓存的冷/热复用、同构 AST 共享、重启、目标重绑定、失效、损坏、容量、并发和失败降级；测试和编译结果记录在具体交付/验收材料中。契约或前端改动时分别执行对应检查。

## 相关决策

- [Run 执行决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [TypeScript 静态索引与 HTTP 形状关联](../../docs/decisions/2026-10-06-typescript-http-overview.md)
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)
- [本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)
- [文件级定义提取缓存](../../docs/decisions/2026-09-15-definition-extraction-cache.md)
- [文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)
- [GitHub 更新的跨进程提交一致性](../../docs/decisions/2026-09-16-github-update-commit-consistency.md)
