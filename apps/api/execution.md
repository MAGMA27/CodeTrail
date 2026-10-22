# Run 执行

## 状态

neighbors 工具结果可独立保存构造上下文 `contextual_calls`，复用 HTTP/MCP 的归属与预算；这些条件记录不会进入报告的 `static_edges` 或静态影响查询。报告引用仍只允许实际返回的普通符号及源码 evidence，条件目标若需引用应另行 search/read 获取；原始工具结果保留条件与逃逸后改写未知标记，不由报告自动补成确定链路。

P2-R1 的 fixture 闭环已扩展为 P2-R2 的显式 fixture/real Run：服务端冻结 Session 输入，后台执行白名单工具；real 模式由一个有界模型工具循环决定检索计划并生成结构化结论。公开事件、工具结果、报告、取消、历史读取和重启中断继续复用同一生命周期；P3-S1 已增加基于持久化事件表的有界 SSE 回放与断线续接；P3-I1 已从终态报告候选提供固定快照上的有界静态影响读取，不改变 Run 生命周期；Codex external Run 已由本地 MCP 驱动同一生命周期并自动进入工作台；external 与 real understanding 共用 explanation 结果和同快照证据约束。LangGraph、多 Agent、任意仓库代码执行和真实供应商语义质量仍未实现或验证；2026-09-30 当前 Codex 宿主在默认 50,000 字符输出预算下成功重放 neighbors 并完成 explanation 报告，与 HTTP/普通 MCP 领域结果及工作台证据联动对照通过。

## 职责与范围

本子系统拥有 Run 的创建、输入快照、生命周期、预算、事件、工具结果、终态报告和工作台展示协作；应用服务是 HTTP/MCP 的共同业务入口，存储层是事实来源。执行器不执行来源仓库 Python、shell 或测试；real 模式只通过服务端配置的兼容模型适配器发起受剩余时间约束的请求，也不把 UI 草稿当作输入。

## 接口与依赖

- 契约与 Schema：[contracts.py](app/contracts.py)、[export_schema.py](app/export_schema.py)。
- 应用服务与 HTTP：[service.py](app/service.py)、[main.py](app/main.py)。
- SQLite 持久化：[storage.py](app/storage.py)。
- executor：[run_execution.py](app/run_execution.py)；real 模型协议与有界循环：[issue_analysis.py](app/issue_analysis.py)。
- SSE 订阅：[run_sse.py](app/run_sse.py)；只从既有 SQLite 事件事实回放和追踪，不维护第二事件日志。
- MCP：[mcp_server.py](app/mcp_server.py)；只读 Run 工具默认注册，内部 fixture/real 写工具由 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 独立开放，Codex external 写工具由 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 独立开放。
- 工作台：[workbench.tsx](../web/app/workbench.tsx)、[workbench-ui.tsx](../web/app/workbench-ui.tsx)、[api.ts](../web/lib/api.ts)、[run-event-stream.ts](../web/lib/run-event-stream.ts)；报告候选位置与影响范围在同一 Run/index 上按需读取。

HTTP create/list/detail/events/`events/stream`/tool-results/report/cancel 与 MCP Run 工具都调用 WorkspaceService；fixture/real 创建只在执行器预留名额并成功启动 worker 后返回 queued `run_id`，后台 worker 才会执行。Codex external 创建冻结输入并返回 queued `run_id`，不预留内部 worker；首个 external 工具或结论请求启动 Run，工具结果按 request_id 幂等持久化，Issue 结论或 explanation 结论只接受同一 Run 实际返回的证据和符号。`analysis_kind=understanding` 可用于 real 与 external，fixture 只接受 `issue`；real 由同一个有界模型工具循环按任务选择专用提示和结构化输出，理解结果保存 explanation，不写 Issue findings/candidate links。模型只引用本 Run 实际 `read_source` 返回的 evidence 和已观察 symbols；理解问题来自冻结 Session 消息。HTTP real 能力由 `CODETRAIL_REAL_RUN_ENABLED=1` 开放，MCP real 另由 `CODETRAIL_MCP_REAL_RUN_ENABLED=1` 开放，二者都仍需相应 Run 写权限；external 由 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 开放。external `understanding` Run 可在提交结论前调用 `submit_project_features`：宿主 Agent 自行识别，服务端从同一 Run 的实际源码读取结果重建 evidence、复读固定快照校验 hash/行范围，并以共享 FeatureGeneration 服务持久化；服务端模型用量为 0，不记录宿主 token/费用。新 generation 和摘要保存服务端确定的 `external_run_id`、`external_session_id`，保存事务要求它们与调用 scope 和 active Run 一致；HTTP/MCP 版本读取可据此回到原 Session/Run。旧外部 generation 若创建于该字段存在前会保留空来源并显示未记录，不伪造可读 Run。结果通过现有 HTTP/MCP 历史和功能图读取，不生成 CodeEdge，且必须在 Run 终结前提交。容量满时内部 Run 创建在 Run 落库前返回可重试的 `budget_exceeded`/HTTP 429，MCP 复用同一错误码和 retryable 语义。`events/stream` 在握手前完成归属和游标校验，以 `{run_id}:{sequence}` 续接并按有界批次读取持久化事件；订阅容量满时返回 429。fixture、real 和 external 都只使用应用服务的 `search_code`、`read_source`、`get_neighbors` 结果。

## 数据与生命周期

Run 固定 `project_id/session_id/index_id`、Issue、模式、场景、版本和预算。创建时服务端读取完整连续的已保存消息前缀，记录 `message_end_sequence`、input hash、字符数、可选同快照 feature generation 和冻结时间；后续消息、草稿和 latest index 不改变 RunInput。每个 Session 通过 SQLite 部分唯一索引最多有一个 queued/running Run；相同幂等键只能复用相同请求指纹。执行器在创建事务前以非阻塞方式预留 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS` 个进程内名额之一，默认容量为 2；没有持久或内部等待队列，溢出请求不写 Run。

real Run 创建时用同一份模型配置解析 provider/model 并传入 worker；密钥只留在进程内，不写进 Run、RunInput、事件或报告。后续 HTTP/MCP real 任务各自从共享 `config.json` 读取新配置，活动 Run 持续使用创建时捕获的配置。内置 HTTP 功能生成也读取该配置；MCP external understanding Run 的宿主功能提交只校验/保存 FeatureModelOutput，不读取模型配置、不调用模型，并且服务端模型用量为 0。

状态迁移为 queued → running → succeeded/failed/cancelled/interrupted；终态不可逆且只写一个 terminal event。名额从预留开始由提交生命周期持有，worker 启动失败、执行异常、超时、取消和正常终态均恰好释放；若已保存 Run 但 worker 无法启动，服务端先保存可查询的 failed 报告再返回 503。工具开始、完成/失败、阶段、finding、graph 和终态事件使用服务端连续 sequence，工具完成事件与结果原子保存。模型调用在发送完整 prompt 前原子预留调用/修复次数和输入字符，响应返回后只保存输出字符及可用 token 用量；不保存原始模型文本。取消和服务重启都保存对应终态报告；worker 发现终态后忽略迟到结果，运行中的供应商请求返回前不释放容量。

三个 fixture 场景继续组装候选结论、证据不足和工具失败。real Issue 模式每轮只接受一个白名单工具请求或一个 `IssueModelFinal`；real understanding 使用独立 `CodeUnderstandingFinal` 协议；两者均允许一次有限修复，完整冻结消息、工具历史和本次返回的 evidence 组成每轮上下文，最终引用必须属于本 Run 的同一 `index_id`。understanding 报告最多 12 个有序步骤：confirmed 必须带 evidence，complete 不得含 unknown，partial/unknown 明确未确认范围，且不生成 findings/candidate links。external 模式由 Codex 负责调查编排，服务端只接受白名单工具的实际返回和与 `analysis_kind` 匹配的结构化 `IssueModelFinal` 或 `CodeUnderstandingFinal`，并以创建时间兑现 `max_seconds`，超时只写当前 Run 的 interrupted 终态。内部与 external Run 都经 `IndexService.execute_tool`；Run `get_neighbors` ToolResult 的 `unresolved` 详情最多保留 40 条，超出时将 `truncated` 设为 true 并报告 `max_unresolved result limit reached`，不改变 `index_unresolved_truncated` 的固定索引覆盖语义；HTTP 和普通 MCP neighbors 查询仍使用原有查询结果，不走该 Run ToolResult 上限。Issue 证据不足是成功终态，非法引用、非法工具、供应商失败和预算耗尽是明确失败；报告中的 graph/evidence/candidate link/finding 仅来自本次 Run 的同快照结果，同时把工具看到的固定索引扫描/unresolved 覆盖限制写入公开 Report limitations。P3-I1 的影响读取只接受报告内精确 candidate/finding ID；若没有唯一 source symbol 返回 unmapped 空结果，否则只在固定 index 的静态 `calls` 上按 callers/callees、深度不超过 2 遍历，返回同一固定索引的扫描与 unresolved 覆盖事实，去重节点/路径并保留 unresolved 未知边界，静态可达不升级为确认影响。公开事件通过 `events/stream` 按 `{run_id}:{sequence}` 先回放后追踪，终态事件后结束，终态游标收到只接受四类终态的 `run.stream.end`；工作台优先 SSE，`onopen` 不重置无进展重连预算，只有有效前进事件重置，达到上限后才回退 450ms 有界轮询且不并行保留 SSE，切换/刷新会恢复而不重复创建。

## 错误与限制

拒绝空 Session、跨 Project/Session/Index/Generation、不完整消息 cutoff 和超出输入/工具/源码/输入/输出/事件/模型调用/修复/时间上限的请求；real 还要求显式能力开关和可用服务端配置，external 还要求独立 MCP 开关。活动 Run、幂等指纹变化和快照冲突返回 conflict；归属错误返回 not_found；模型不可用、模型失败、结构化输出无效和预算失败以明确错误终态呈现，模型失败不回退 fixture；external 不能提交本 Run 未返回的证据/符号，终态拒绝迟到工具和结论。进程容量满也使用 `budget_exceeded`/429 并提示稍后重试，查询、取消和 health 不被容量拒绝阻塞。影响读取受报告归属、候选映射、节点/路径/unresolved、深度、时间和输出大小限制，不执行仓库源码、不调用模型、不写 Run；无唯一映射不猜测同名符号。SSE 的归属、游标冲突、跨 Run、未来 sequence 和订阅容量在握手前拒绝；断线不改变 Run 状态，连接名额在终止/断开后释放。默认 MCP 只能读取已保存 Run，写入开关不授予 Session 写权限。Run 不展示隐藏思维链。

## 验证

`apps/api/tests/test_runs.py` 覆盖 fixture 生命周期、固定 Run 报告影响查询，以及 internal Run 中 neighbors unresolved 溢出后静态边/截断/evidence 的持久化；`apps/api/tests/test_storage.py` 覆盖有界静态路径、环、去重和未知边界；`apps/api/tests/test_run_sse.py` 覆盖 HTTP 逐帧回放、双游标续接、终态控制帧、边界拒绝、断开释放容量和 health 并发；`apps/api/tests/test_issue_runs.py` 覆盖 Issue 与 real understanding 的工具计划、结构化解释/证据、未返回引用拒绝、Issue 回归、有限修复、模型预算、供应商失败、持久化 DeepSeek-compatible 协议、token/字符用量、取消和无凭据能力边界；`apps/api/tests/test_mcp.py` 覆盖 HTTP/MCP 的 real 能力开关、`start_session_run.analysis_kind`、fixture understanding 拒绝、共享 Run 读取和影响结果对照，以及 external 工具实际结果、代码理解有序步骤、证据边界、幂等、时间预算、取消和终态拒写；`apps/web/lib/run-event-stream.test.ts` 和 `apps/web/lib/api.test.ts` 覆盖前端实际订阅与 explanation 解析。本轮全量结果以最终验证为准；真实供应商语义质量仍为 UNVERIFIED。

## 相关决策

- [P2-R1 Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [Run 影响查询决策](../../docs/decisions/2026-09-13-run-impact.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [external Run 的代码理解流程与证据报告](../../docs/decisions/2026-09-14-code-understanding-explanation.md)
- [Real Run 直接代码理解](../../docs/decisions/2026-10-22-real-code-understanding-runs.md)

调查 Run 的 `max_seconds` 默认与上限均为 600 秒（10 分钟），HTTP 与 MCP 使用相同预算契约，可显式设置 1–600 秒。新默认只作用于新建 Run，已持久化 Run 保留冻结预算；external Run 在后续请求到来时检查总时长，不承诺到期立即强制终止。
