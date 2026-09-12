# 存储与查询

## 状态

已实现 SQLite/FTS5 快照、Project/Index 归属、来源导入、Session/消息、搜索、源码证据、受限邻接、O2 功能结果，以及 P2-R1/P2-R2 Run/冻结输入/事件/工具结果/报告和模型用量持久化；P3-I1 增加从 Run 固定报告/快照派生的有界静态影响查询。SSE 仅读取同一持久化事件事实，不增加存储日志，影响结果也不写回 Run。

## 职责与范围

[IndexStore](../../apps/api/app/storage.py) 拥有不可变 Index、Project 关联、Session/消息和 Run 生命周期数据；[ManagementStore](../../apps/api/app/storage.py) 拥有动态来源和 ImportAttempt。存储层负责事务、唯一约束、作用域读取、游标、契约反序列化和同快照静态 `calls` 影响遍历，不负责 HTTP 错误呈现、模型推理或工具计划；影响查询只读取已有 symbols/static_edges/unresolved，不修改 Run 或推断模型影响；real Run 的模型调用只通过这里原子预留/记录有界用量，不保存原始 prompt/response。

## 接口与依赖

应用服务通过 [service.py](../../apps/api/app/service.py) 的 registry 获取对应 IndexStore；Run executor 只通过同一存储接口写事件、工具结果和报告，[run_sse.py](../../apps/api/app/run_sse.py) 只调用有界事件读取。Run 表、run_inputs、run_events、run_tool_results、run_reports 与 sessions 使用 project/session/index 外键和索引，所有读取要求显式作用域。JSON 行在读写边界再次通过 Pydantic Event/ToolResult/Report/Run/RunInput 校验。

## 数据与生命周期

创建 Run 的事务校验 Session 与固定 index，保存完整连续的已保存消息前缀、cutoff、hash 和字符用量；后续追加消息不修改 run_inputs。应用服务在该事务前从进程内执行器预留容量，容量失败不进入存储；存储异常或唯一冲突都会释放预留。`runs_active_session` 部分唯一索引限制每个 Session 同时只有一个 queued/running Run，`runs_idempotency` 绑定 project/session/idempotency key 与请求指纹。事件以 `(run_id, sequence)` 连续递增；工具完成事件与结果同事务写入并更新预算；终态事件与报告、Run 终态同事务写入。

模型请求发送前，`reserve_run_model_call` 在事务中检查 running 状态、调用/修复次数和累计输入字符并更新 Run budget；响应返回后，`record_run_model_output` 只累加输出字符和供应商提供的 token，用量缺失时保留未知。取消、终态或超限会拒绝/忽略迟到更新，执行容量仍由 worker 持有到供应商调用返回。取消和服务启动恢复分别原子写入 cancelled/interrupted 事件和空候选终态报告；worker 启动失败会写入 failed 事件与报告；终态再次读取不重复写事件。SSE 通过 `load_run_event_cursor` 读取当前最大 sequence 做未来游标校验，再以 `load_run_events` 分页回放；单次 SQLite 连接只覆盖一次查询，等待客户端期间不持有连接。SQLite 连接按存储操作创建/释放，索引查询、Run worker 和 SSE 读取使用独立连接，不共享线程连接。索引仍是不可变快照，报告/工具 evidence 和影响节点只允许指向同一 `index_id`；影响遍历使用深度、节点、边、路径、unresolved、时间和输出字节上限，简单路径去重并保留循环闭合静态边。

## 错误与限制

Run 持久化限制工具调用、模型调用/修复、单次/总源码行、输入/输出字符、模型输入/输出字符、事件和时间预算；事件页最多 100 条、工具结果页最多 40 条、Run 历史最多 40 条，游标绑定 project/session/run。影响查询默认/最大深度为 2、节点最多 40、边最多 80、路径和 unresolved 最多 40，且有时间与输出字节边界；root 不计入 reachable_node_count，重复节点和多路径去重，循环不展开为重复符号路径。唯一约束冲突映射为 conflict，坏 scope 映射为 not_found，输入或预算违反映射为 invalid_input/budget_exceeded；执行器容量满在落库前映射为可重试的 budget_exceeded/429；终态后写工具结果、事件和模型用量会被拒绝或忽略。SSE 连接/单 Run 订阅、批次、事件字节、心跳和生命周期也有独立上限，游标未来或跨 Run 不进入流；来源目录只读，索引和服务数据目录由 registry 边界控制。

## 验证

`apps/api/tests/test_storage.py` 验证 SQLite 重开、事务、旧快照、邻接和影响路径边界；`apps/api/tests/test_runs.py` 验证 fixture Run 冻结、唯一活动约束、分页读取、工具结果/事件/报告、固定报告影响读取、取消和重启恢复；`apps/api/tests/test_run_sse.py` 验证 SSE 逐帧回放、游标续接、终态完成标记、边界拒绝和断开释放容量；`apps/api/tests/test_issue_runs.py` 验证 real 模型用量预留、token/字符限制、终态取消竞争和报告持久化；`apps/api/tests/test_api.py`、`test_features.py` 和 `test_mcp.py` 验证上层作用域与双入口。本轮 API 完整测试为 `121 passed`，`compileall` 通过，影响路径定向测试和 HTTP/MCP 对照均通过；真实供应商语义质量仍未验证。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [Run 影响查询决策](../decisions/2026-09-13-run-impact.md)
