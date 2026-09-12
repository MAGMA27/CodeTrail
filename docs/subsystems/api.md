# API 与工具适配

## 状态

已实现 Project/Index/Session、来源导入、O1 概览、O2/O3 功能生成与 P2-R1/P2-R2 fixture/real Run 的 HTTP 和本地 MCP 入口。Run 已覆盖冻结输入、受限工具执行、单 Agent 有界模型循环、公开事件、持久化 SSE 回放/续接、报告、取消和重启中断；完整影响高亮和真实供应商语义质量仍未实现或验证。

## 职责与范围

将服务端允许的仓库或来源映射到 Project、不可变 Index、Session 和受限 Run；统一承担 project/session/index/generation 归属、预算、错误和幂等语义。HTTP 面向工作台，MCP 面向本地 Agent；两者不各自实现一套 Run 业务逻辑。该子系统不执行被分析仓库代码、shell、测试或隐藏思维链，real Run 只使用服务端配置的 OpenAI-compatible 模型传输。

## 接口与依赖

[service.py](../../apps/api/app/service.py) 提供应用服务；[main.py](../../apps/api/app/main.py) 提供 HTTP 路由；[mcp_server.py](../../apps/api/app/mcp_server.py) 提供 stdio 工具注册和结构化错误；[run_execution.py](../../apps/api/app/run_execution.py)、[run_sse.py](../../apps/api/app/run_sse.py) 和 [issue_analysis.py](../../apps/api/app/issue_analysis.py) 使用 [IndexService](../../apps/api/app/service.py) 的实际 search/read/neighbors 工具结果或持久化事件。精确请求、状态、事件、SSE 帧和报告形状由 [contracts.py](../../apps/api/app/contracts.py) 声明，Schema 由 [export_schema.py](../../apps/api/app/export_schema.py) 生成。

Run HTTP 包括 create/list/detail/events/`events/stream`/tool-results/report/cancel；create 接受显式 `mode=fixture|real`，real 还要求 `CODETRAIL_REAL_RUN_ENABLED=1`，执行器有空闲预留名额且 worker 启动成功后显式返回 queued `run_id`，不在请求线程执行分析。容量满时在 Run 落库前返回可重试的 `budget_exceeded`/429。`events/stream` 复用事件表，以 `{run_id}:{sequence}` 和 `after_sequence`/`Last-Event-ID` 实现严格续接；总连接、单 Run 连接、批次、事件大小、心跳和生命周期均有界，满载在握手前返回 429。默认 MCP 增加 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`；`CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 才增加独立的 `start_session_run` 和 `cancel_session_run`，MCP real 另要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会隐式开放 Session 写入。

## 数据与生命周期

创建 Run 时校验 Session 的固定 ready index、可选同快照 feature generation 和消息 cutoff；省略 cutoff 时以服务端当前保存消息计数为线性化点，并读取完整连续消息前缀，保存 input hash、字符数和范围。Run、RunInput、事件、工具结果和报告由 [IndexStore](../../apps/api/app/storage.py) 同一数据库事务保存；Session 最多一个 queued/running Run，幂等键绑定请求指纹。

本地 executor 在 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS`（默认 2）上做非阻塞准入，只保留有限的 daemon worker，不引入持久或内部等待队列、多 Agent。fixture 和 real 的工具计划只调用 `search_code`、`read_source`、`get_neighbors`；real 每轮由模型返回一个 Pydantic 校验的工具请求或最终结论，最多一次有限修复。成功、证据不足和工具失败报告均由这次执行的真实结果组装，证据/图绑定冻结 index_id。公开事件按连续 sequence 写入，queued/running 只能进入一次 succeeded/failed/cancelled/interrupted 终态；模型调用预算在请求前预留，输出只记录字符/token 用量，不保存原始模型文本；容量预留在存储失败、worker 启动失败、取消、超时、异常和完成路径恰好释放，晚到结果因终态事务拒绝。

## 错误与限制

Run 拒绝空消息、跨 Project/Session/Index/Generation、不合法 cutoff 和越界预算；real 只有在对应显式能力开关和服务端 `config.json` 有效时才会执行。输入/工具调用、单次/总源码行、输入/输出字符、模型调用/修复、模型输入/输出、事件、总时间和并发都有服务端上限。活动 Run、幂等语义不一致和快照冲突返回 conflict，未知归属返回 not_found，模型不可用、模型失败、非法结构化输出、预算或容量失败返回明确错误；real 失败不回退 fixture。容量错误可重试，且不写入 queued Run。报告只引用已持久化的同快照工具 evidence；取消/中断报告不生成候选结论。读接口带 project/session/run 作用域和有界游标，默认 MCP 读工具不会启动执行。

其它来源导入、GitHub、模型配置、Session 和功能生成限制见 [apps/api README](../../apps/api/README.md)；Run 的设计取舍见 [Run 决策](../decisions/2026-09-12-run-execution.md) 和 [Run SSE 决策](../decisions/2026-09-12-run-sse.md)。

## 验证

`apps/api/tests/test_runs.py` 验证 fixture 生命周期；`apps/api/tests/test_run_sse.py` 验证持久化逐帧回放、双游标续接、终态控制帧、未来/跨 Run 拒绝、断开释放容量和 health 并发；`apps/api/tests/test_issue_runs.py` 验证 real 的模型工具协议、不同问题的工具计划、证据不足、非法引用/工具、有限修复、预算、持久化配置和 OpenAI-compatible 请求、供应商失败、取消与终态容量；`apps/api/tests/test_mcp.py` 验证 HTTP/MCP 同一 Run 和独立 real 写能力。另运行 `uv run --directory apps/api pytest -q`、`uv run --directory apps/api python -m compileall -q app` 和本地 HTTP/MCP/SSE 流程；浏览器 real 模式使用本地假供应商的结果与真实供应商质量分别记录，真实 DeepSeek 语义质量为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
