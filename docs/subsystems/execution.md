# Run 执行

## 状态

P2-R1 已实现 fixture-only 的显式 Run 纵向闭环：服务端冻结 Session 输入，后台执行白名单工具，持久化公开事件/工具结果/报告，支持取消、历史读取和重启中断。执行器已补上单进程全局容量准入，不创建无界等待线程。真实供应商 Run、SSE、LangGraph、多 Agent 和任意仓库代码执行仍未实现。

## 职责与范围

本子系统拥有 Run 的创建、输入快照、生命周期、预算、事件、工具结果、终态报告和工作台展示协作；应用服务是 HTTP/MCP 的共同业务入口，存储层是事实来源。执行器不执行来源仓库 Python、shell、测试或网络模型调用，也不把 UI 草稿当作输入。

## 接口与依赖

- 契约与 Schema：[contracts.py](../../apps/api/app/contracts.py)、[export_schema.py](../../apps/api/app/export_schema.py)。
- 应用服务与 HTTP：[service.py](../../apps/api/app/service.py)、[main.py](../../apps/api/app/main.py)。
- SQLite 持久化：[storage.py](../../apps/api/app/storage.py)。
- fixture executor：[run_execution.py](../../apps/api/app/run_execution.py)。
- MCP：[mcp_server.py](../../apps/api/app/mcp_server.py)；只读 Run 工具默认注册，写工具由 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 独立开放。
- 工作台：[workbench.tsx](../../apps/web/app/workbench.tsx)、[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx)、[api.ts](../../apps/web/lib/api.ts)。

HTTP create/list/detail/events/tool-results/report/cancel 与 MCP Run 工具都调用 WorkspaceService；创建只在执行器预留名额并成功启动 worker 后返回 queued `run_id`，后台 worker 才会执行。容量满时创建在 Run 落库前返回可重试的 `budget_exceeded`/HTTP 429，MCP 复用同一错误码和 retryable 语义。fixture 计划只使用应用服务的 `search_code`、`read_source`、`get_neighbors` 结果。

## 数据与生命周期

Run 固定 `project_id/session_id/index_id`、Issue、模式、场景、版本和预算。创建时服务端读取完整连续的已保存消息前缀，记录 `message_end_sequence`、input hash、字符数、可选同快照 feature generation 和冻结时间；后续消息、草稿和 latest index 不改变 RunInput。每个 Session 通过 SQLite 部分唯一索引最多有一个 queued/running Run；相同幂等键只能复用相同请求指纹。执行器在创建事务前以非阻塞方式预留 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS` 个进程内名额之一，默认容量为 2；没有持久或内部等待队列，溢出请求不写 Run。

状态迁移为 queued → running → succeeded/failed/cancelled/interrupted；终态不可逆且只写一个 terminal event。名额从预留开始由提交生命周期持有，worker 启动失败、执行异常、超时、取消和正常终态均恰好释放；若已保存 Run 但 worker 无法启动，服务端先保存可查询的 failed 报告再返回 503。工具开始、完成/失败、阶段、finding、graph 和终态事件使用服务端连续 sequence，工具完成事件与结果原子保存。取消和服务重启都保存对应终态报告；worker 发现终态后忽略迟到结果。取消信号会唤醒测试门控的工具等待，但运行中的实际工具未返回前不会释放名额。

三个 fixture 场景分别组装候选结论、证据不足和工具失败。报告中的 graph/evidence/candidate link/finding 仅来自本次 Run 的同快照结果；失败场景保留此前已保存的 evidence，取消/中断只提供无候选的终态摘要。工作台通过 450ms 有界轮询读取状态、事件、工具结果和报告，切换/刷新会恢复而不重复创建。

## 错误与限制

拒绝空 Session、跨 Project/Session/Index/Generation、real 模式、不完整消息 cutoff 和超出输入/工具/源码/输出/事件/时间上限的请求。活动 Run、幂等指纹变化和快照冲突返回 conflict；归属错误返回 not_found；单次 Run 预算失败以 budget_exceeded 终态或创建错误呈现；进程容量满也使用 `budget_exceeded`/429 并提示稍后重试，查询、取消和 health 不被容量拒绝阻塞。默认 MCP 只能读取已保存 Run，写入开关不授予 Session 写权限。Run 不展示隐藏思维链，不开放 SSE 或真实模型。

## 验证

`apps/api/tests/test_runs.py` 覆盖完整输入冻结、追加消息/latest index 隔离、同快照 evidence、三个 fixture outcome、预算终态、幂等/活动冲突、跨 Session 容量拒绝、幂等重试、queued 启动取消、运行取消、超时、执行/存储/worker 启动失败、报告和重启 interrupted；`apps/api/tests/test_mcp.py` 覆盖 HTTP/MCP 共享 Run、只读工具和独立写开关。当前后端 96 passed、Python 编译检查通过；`pnpm contracts:check` 为 19 passed，前端单测 11 passed，typecheck/build、docs:check 和 docs:check:local 均通过；真实 HTTP/工作台已复核运行中取消、刷新恢复和 cancelled 报告，真实供应商语义与 SSE 为 UNVERIFIED。

## 相关决策

- [P2-R1 Run 决策](../decisions/2026-09-12-run-execution.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
