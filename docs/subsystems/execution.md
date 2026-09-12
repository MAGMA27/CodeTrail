# Run 执行

## 状态

P2-R1 已实现 fixture-only 的显式 Run 纵向闭环：服务端冻结 Session 输入，后台执行白名单工具，持久化公开事件/工具结果/报告，支持取消、历史读取和重启中断。真实供应商 Run、SSE、LangGraph、多 Agent 和任意仓库代码执行仍未实现。

## 职责与范围

本子系统拥有 Run 的创建、输入快照、生命周期、预算、事件、工具结果、终态报告和工作台展示协作；应用服务是 HTTP/MCP 的共同业务入口，存储层是事实来源。执行器不执行来源仓库 Python、shell、测试或网络模型调用，也不把 UI 草稿当作输入。

## 接口与依赖

- 契约与 Schema：[contracts.py](../../apps/api/app/contracts.py)、[export_schema.py](../../apps/api/app/export_schema.py)。
- 应用服务与 HTTP：[service.py](../../apps/api/app/service.py)、[main.py](../../apps/api/app/main.py)。
- SQLite 持久化：[storage.py](../../apps/api/app/storage.py)。
- fixture executor：[run_execution.py](../../apps/api/app/run_execution.py)。
- MCP：[mcp_server.py](../../apps/api/app/mcp_server.py)；只读 Run 工具默认注册，写工具由 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 独立开放。
- 工作台：[workbench.tsx](../../apps/web/app/workbench.tsx)、[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx)、[api.ts](../../apps/web/lib/api.ts)。

HTTP create/list/detail/events/tool-results/report/cancel 与 MCP Run 工具都调用 WorkspaceService；创建只返回 queued `run_id`，后台 worker 才会执行。fixture 计划只使用应用服务的 `search_code`、`read_source`、`get_neighbors` 结果。

## 数据与生命周期

Run 固定 `project_id/session_id/index_id`、Issue、模式、场景、版本和预算。创建时服务端读取完整连续的已保存消息前缀，记录 `message_end_sequence`、input hash、字符数、可选同快照 feature generation 和冻结时间；后续消息、草稿和 latest index 不改变 RunInput。每个 Session 通过 SQLite 部分唯一索引最多有一个 queued/running Run；相同幂等键只能复用相同请求指纹。

状态迁移为 queued → running → succeeded/failed/cancelled/interrupted；终态不可逆且只写一个 terminal event。工具开始、完成/失败、阶段、finding、graph 和终态事件使用服务端连续 sequence，工具完成事件与结果原子保存。取消和服务重启都保存对应终态报告；worker 发现终态后忽略迟到结果。

三个 fixture 场景分别组装候选结论、证据不足和工具失败。报告中的 graph/evidence/candidate link/finding 仅来自本次 Run 的同快照结果；失败场景保留此前已保存的 evidence，取消/中断只提供无候选的终态摘要。工作台通过 450ms 有界轮询读取状态、事件、工具结果和报告，切换/刷新会恢复而不重复创建。

## 错误与限制

拒绝空 Session、跨 Project/Session/Index/Generation、real 模式、不完整消息 cutoff 和超出输入/工具/源码/输出/事件/时间上限的请求。活动 Run、幂等指纹变化和快照冲突返回 conflict；归属错误返回 not_found；预算失败以 budget_exceeded 终态或创建错误呈现。默认 MCP 只能读取已保存 Run，写入开关不授予 Session 写权限。Run 不展示隐藏思维链，不开放 SSE 或真实模型。

## 验证

`apps/api/tests/test_runs.py` 覆盖完整输入冻结、追加消息/latest index 隔离、同快照 evidence、三个 fixture outcome、预算终态、幂等/活动冲突、取消、报告和重启 interrupted；`apps/api/tests/test_mcp.py` 覆盖 HTTP/MCP 共享 Run、只读工具和独立写开关。完整后端、契约、前端单测/typecheck/build 已运行；浏览器实际 Run 流程在最终交付前复核，真实供应商语义与 SSE 为 UNVERIFIED。

## 相关决策

- [P2-R1 Run 决策](../decisions/2026-09-12-run-execution.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
