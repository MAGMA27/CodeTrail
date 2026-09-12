# API 与工具适配

## 状态

已实现 Project/Index/Session、来源导入、O1 概览、O2/O3 功能生成与本轮 P2-R1 fixture Run 的 HTTP 和本地 MCP 入口。Run 已覆盖冻结输入、受限工具执行、公开事件、报告、取消和重启中断；真实 Issue Agent、真实供应商 Run 和 SSE 仍未实现。

## 职责与范围

将服务端允许的仓库或来源映射到 Project、不可变 Index、Session 和受限 Run；统一承担 project/session/index/generation 归属、预算、错误和幂等语义。HTTP 面向工作台，MCP 面向本地 Agent；两者不各自实现一套 Run 业务逻辑。该子系统不执行被分析仓库代码、shell、测试或隐藏思维链。

## 接口与依赖

[service.py](../../apps/api/app/service.py) 提供应用服务；[main.py](../../apps/api/app/main.py) 提供 HTTP 路由；[mcp_server.py](../../apps/api/app/mcp_server.py) 提供 stdio 工具注册和结构化错误；[run_execution.py](../../apps/api/app/run_execution.py) 使用 [IndexService](../../apps/api/app/service.py) 的实际 search/read/neighbors 工具结果。精确请求、状态、事件和报告形状由 [contracts.py](../../apps/api/app/contracts.py) 声明，Schema 由 [export_schema.py](../../apps/api/app/export_schema.py) 生成。

Run HTTP 包括 create/list/detail/events/tool-results/report/cancel；create 只接受 `mode=fixture`，执行器有空闲预留名额且 worker 启动成功后显式返回 queued `run_id`，不在请求线程执行分析。容量满时在 Run 落库前返回可重试的 `budget_exceeded`/429。默认 MCP 增加 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`；`CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 才增加独立的 `start_session_run` 和 `cancel_session_run`，不会隐式开放 Session 写入。

## 数据与生命周期

创建 Run 时校验 Session 的固定 ready index、可选同快照 feature generation 和消息 cutoff；省略 cutoff 时以服务端当前保存消息计数为线性化点，并读取完整连续消息前缀，保存 input hash、字符数和范围。Run、RunInput、事件、工具结果和报告由 [IndexStore](../../apps/api/app/storage.py) 同一数据库事务保存；Session 最多一个 queued/running Run，幂等键绑定请求指纹。

本地 executor 在 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS`（默认 2）上做非阻塞准入，只保留有限的 daemon worker，不引入持久或内部等待队列、多 Agent。工具计划只调用 `search_code`、`read_source`、`get_neighbors`；成功、证据不足和工具失败报告均由这次执行的真实结果组装，证据/图绑定冻结 index_id。公开事件按连续 sequence 写入，queued/running 只能进入一次 succeeded/failed/cancelled/interrupted 终态；容量预留在存储失败、worker 启动失败、取消、超时、异常和完成路径恰好释放；取消和服务重启分别写终态事件及报告，晚到结果因终态事务拒绝。

## 错误与限制

Run 拒绝空消息、跨 Project/Session/Index/Generation、real 模式和不合法 cutoff；输入/工具调用、单次/总源码行、输入/输出字符、事件、总时间和并发都有服务端上限。活动 Run、幂等语义不一致和快照冲突返回 conflict，未知归属返回 not_found，预算或容量失败返回 budget_exceeded；容量错误可重试，且不写入 queued Run。报告只引用已持久化的同快照工具 evidence；取消/中断报告不生成候选结论。读接口带 project/session/run 作用域和有界游标，默认 MCP 读工具不会启动执行。

其它来源导入、GitHub、模型配置、Session 和功能生成限制见 [apps/api README](../../apps/api/README.md)；Run 的设计取舍见 [Run 决策](../decisions/2026-09-12-run-execution.md)。

## 验证

`apps/api/tests/test_runs.py` 验证消息冻结、后续消息/latest index 隔离、三个 fixture outcome、工具/事件/报告、预算、跨 Session 容量与幂等、queued/运行取消、超时、执行/存储/启动失败、活动冲突和重启中断；`apps/api/tests/test_mcp.py` 验证 HTTP/MCP 同一 Run、默认只读和独立写开关。另运行 `uv run --directory apps/api pytest -q`、`uv run --directory apps/api python -m compileall -q app` 和真实本地 HTTP/MCP 流程；浏览器 Run 取消/刷新恢复已复核，真实供应商语义质量与 SSE 为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
