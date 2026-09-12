# Run 执行

## 状态

P2-R1 的 fixture 闭环已扩展为 P2-R2 的显式 fixture/real Run：服务端冻结 Session 输入，后台执行白名单工具；real 模式由一个有界模型工具循环决定检索计划并生成结构化结论。公开事件、工具结果、报告、取消、历史读取和重启中断继续复用同一生命周期；P3-S1 已增加基于持久化事件表的有界 SSE 回放与断线续接。LangGraph、多 Agent、任意仓库代码执行和真实供应商语义质量仍未实现或验证。

## 职责与范围

本子系统拥有 Run 的创建、输入快照、生命周期、预算、事件、工具结果、终态报告和工作台展示协作；应用服务是 HTTP/MCP 的共同业务入口，存储层是事实来源。执行器不执行来源仓库 Python、shell 或测试；real 模式只通过服务端配置的兼容模型适配器发起受剩余时间约束的请求，也不把 UI 草稿当作输入。

## 接口与依赖

- 契约与 Schema：[contracts.py](../../apps/api/app/contracts.py)、[export_schema.py](../../apps/api/app/export_schema.py)。
- 应用服务与 HTTP：[service.py](../../apps/api/app/service.py)、[main.py](../../apps/api/app/main.py)。
- SQLite 持久化：[storage.py](../../apps/api/app/storage.py)。
- executor：[run_execution.py](../../apps/api/app/run_execution.py)；real 模型协议与有界循环：[issue_analysis.py](../../apps/api/app/issue_analysis.py)。
- SSE 订阅：[run_sse.py](../../apps/api/app/run_sse.py)；只从既有 SQLite 事件事实回放和追踪，不维护第二事件日志。
- MCP：[mcp_server.py](../../apps/api/app/mcp_server.py)；只读 Run 工具默认注册，写工具由 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 独立开放。
- 工作台：[workbench.tsx](../../apps/web/app/workbench.tsx)、[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx)、[api.ts](../../apps/web/lib/api.ts)、[run-event-stream.ts](../../apps/web/lib/run-event-stream.ts)。

HTTP create/list/detail/events/`events/stream`/tool-results/report/cancel 与 MCP Run 工具都调用 WorkspaceService；创建只在执行器预留名额并成功启动 worker 后返回 queued `run_id`，后台 worker 才会执行。HTTP real 能力由 `CODETRAIL_REAL_RUN_ENABLED=1` 开放，MCP real 另由 `CODETRAIL_MCP_REAL_RUN_ENABLED=1` 开放，二者都仍需相应 Run 写权限。容量满时创建在 Run 落库前返回可重试的 `budget_exceeded`/HTTP 429，MCP 复用同一错误码和 retryable 语义。`events/stream` 在握手前完成归属和游标校验，以 `{run_id}:{sequence}` 续接并按有界批次读取持久化事件；订阅容量满时返回 429。fixture 和 real 都只使用应用服务的 `search_code`、`read_source`、`get_neighbors` 结果。

## 数据与生命周期

Run 固定 `project_id/session_id/index_id`、Issue、模式、场景、版本和预算。创建时服务端读取完整连续的已保存消息前缀，记录 `message_end_sequence`、input hash、字符数、可选同快照 feature generation 和冻结时间；后续消息、草稿和 latest index 不改变 RunInput。每个 Session 通过 SQLite 部分唯一索引最多有一个 queued/running Run；相同幂等键只能复用相同请求指纹。执行器在创建事务前以非阻塞方式预留 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS` 个进程内名额之一，默认容量为 2；没有持久或内部等待队列，溢出请求不写 Run。

状态迁移为 queued → running → succeeded/failed/cancelled/interrupted；终态不可逆且只写一个 terminal event。名额从预留开始由提交生命周期持有，worker 启动失败、执行异常、超时、取消和正常终态均恰好释放；若已保存 Run 但 worker 无法启动，服务端先保存可查询的 failed 报告再返回 503。工具开始、完成/失败、阶段、finding、graph 和终态事件使用服务端连续 sequence，工具完成事件与结果原子保存。模型调用在发送完整 prompt 前原子预留调用/修复次数和输入字符，响应返回后只保存输出字符及可用 token 用量；不保存原始模型文本。取消和服务重启都保存对应终态报告；worker 发现终态后忽略迟到结果，运行中的供应商请求返回前不释放容量。

三个 fixture 场景继续组装候选结论、证据不足和工具失败。real 模式每轮只接受一个白名单工具请求或一个 `IssueModelFinal`，允许一次有限修复；完整冻结 Issue/消息、工具历史和本次返回的 evidence 组成每轮上下文，最终引用必须属于本 Run 的同一 `index_id`。证据不足是成功终态，非法引用、非法工具、供应商失败和预算耗尽是明确失败；报告中的 graph/evidence/candidate link/finding 仅来自本次 Run 的同快照结果。公开事件通过 `events/stream` 按 `{run_id}:{sequence}` 先回放后追踪，终态事件后结束，终态游标收到只接受四类终态的 `run.stream.end`；工作台优先 SSE，`onopen` 不重置无进展重连预算，只有有效前进事件重置，达到上限后才回退 450ms 有界轮询且不并行保留 SSE，切换/刷新会恢复而不重复创建。

## 错误与限制

拒绝空 Session、跨 Project/Session/Index/Generation、不完整消息 cutoff 和超出输入/工具/源码/输入/输出/事件/模型调用/修复/时间上限的请求；real 还要求显式能力开关和可用服务端配置。活动 Run、幂等指纹变化和快照冲突返回 conflict；归属错误返回 not_found；模型不可用、模型失败、结构化输出无效和预算失败以明确错误终态呈现，模型失败不回退 fixture。进程容量满也使用 `budget_exceeded`/429 并提示稍后重试，查询、取消和 health 不被容量拒绝阻塞。SSE 的归属、游标冲突、跨 Run、未来 sequence 和订阅容量在握手前拒绝；断线不改变 Run 状态，连接名额在终止/断开后释放。默认 MCP 只能读取已保存 Run，写入开关不授予 Session 写权限。Run 不展示隐藏思维链。

## 验证

`apps/api/tests/test_runs.py` 覆盖 fixture 生命周期；`apps/api/tests/test_run_sse.py` 覆盖 HTTP 逐帧回放、双游标续接、终态控制帧、边界拒绝、断开释放容量和 health 并发；`apps/api/tests/test_issue_runs.py` 覆盖不同问题的工具计划、证据不足、非法工具/引用、有限修复、模型预算、供应商失败、持久化 DeepSeek-compatible 协议、token/字符用量、取消和无凭据能力边界；`apps/api/tests/test_mcp.py` 覆盖 HTTP/MCP 的 real 能力开关与共享 Run 读取；`apps/web/lib/run-event-stream.test.ts` 覆盖前端实际订阅处理器。本轮实际验证：后端完整测试 `118 passed`，Python `compileall` 通过；前端测试 `17 passed`、typecheck 和 build 通过；契约检查 `20 passed`，`docs:check` 和 `docs:check:local` 通过；`test_run_sse.py` 共 `12 passed`，其中 3 项为实际流场景；本地 HTTP 逐帧回放 sequence 11–13 并正常结束，浏览器使用本地延迟假供应商验证受限生命周期断流后的 sequence 续接无遗漏/重复、取消、终态报告/evidence、Run 切换和刷新恢复。真实浏览器离线网络切换和真实 DeepSeek 语义质量为 UNVERIFIED。

## 相关决策

- [P2-R1 Run 决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
