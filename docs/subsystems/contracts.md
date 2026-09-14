# 数据契约

## 状态

已实现 Pydantic 契约和可重复 Schema 导出；前端对 Project、Index、Session/消息、概览、功能、fixture/real/external Run、事件、工具结果、报告、Run 影响查询和错误响应执行运行时边界校验；MCP 使用同一领域契约包装结构化结果。external Run 的 `execution_source=codex`、工具响应、结论响应和报告限制已纳入契约。

## 职责与范围

[contracts.py](../../apps/api/app/contracts.py) 定义 Project/Index/Session、fixture/real/external Run、RunInput、预算、`IssueModelOutput`、公开事件、allowlisted ToolResult、Report、证据/图、MCP envelope 和请求/响应的字段与校验；概览入口的 `fastapi_route` 变体要求方法、声明路径、处理函数 symbol 与同快照范围。旧 Python 入口仍可省略这些路由字段。[export_schema.py](../../apps/api/app/export_schema.py) 生成 [codetrail.json](../../contracts/schemas/codetrail.json)；HTTP 路由和前端分别拥有传输与消费编排。

## 接口与依赖

Run 的公共边界包括 `Run`、`RunInput`、`RunBudget`、`EventSequence` 中的连续事件、`ToolResult`、`Report`、`CreateRunRequest/Response`、Run history/events/tool-results/report/impact 响应及取消响应。external MCP 另使用 `McpExternalToolResponse` 和 `McpExternalCompleteResponse`，分别只允许实际工具结果或成功 Run/report 进入 `ok` envelope。邻接 HTTP/MCP 结果的 `truncated`/`truncation_reason` 表示本次查询预算，`index_truncated`/`index_truncation_reason` 表示固定关系扫描覆盖，`index_unresolved_truncated`/`index_unresolved_truncation_reason` 表示 unresolved 详情覆盖；后者允许 NULL 表示旧索引未知，三组字段分别校验，不能交叉推断。Index 响应也保留同样的扫描与 unresolved 覆盖拆分。`RunImpactResponse` 要求恰好一个来自已保存报告的 candidate/finding ID，固定 `index_id`，明确 callers/callees 方向、最大深度、节点/边/路径/unresolved 上限、mapped/unmapped 状态和固定索引覆盖事实；路径只能解释同快照 `calls` 边。`SseEventFrame` 的 `id` 固定为 `{run_id}:{sequence}`，`SseStreamEndFrame` 只作为终态游标续接的非持久化完成标记；两者的 `event`/`data` 必须匹配，均保留 `schema_version`。MCP 的 Run 读/写 envelope 只允许对应 `ok` 状态携带 result/report；所有请求/响应保留 `schema_version`。精确字段以声明和生成 Schema 为准，不在文档中复制完整字段表。

## 数据与生命周期

Run contract 要求 project/session/index 同时出现，输入消息是从 1 到 cutoff 的完整连续已保存前缀，input hash/字符数与消息一致；Run budget 记录工具、模型调用/修复、输入/输出字符、可选 token、源码、事件和总时间限制及使用量。real 模型每轮只能返回一个 `IssueModelToolCall` 或 `IssueModelFinal`；external 由外部 Agent 触发同一白名单工具，但最终结论的 symbols/evidence/candidate links 只能引用本 Run 实际返回的同一 index 结果，原始模型文本不属于持久化契约。事件 sequence 从 1 连续递增且最后只能是一个 terminal event；terminal status 与 report outcome 必须相互一致。Report graph、evidence、candidate links 和 findings 只能引用同一 index 及实际工具返回的 evidence，取消/中断使用对应终态 outcome。Run 影响结果是按报告 ID 临时派生的只读结果，不进入 Run 持久化；root 被排除在 reachable_node_count 外，重复节点/多路径去重，循环只保留可解释的静态边，dynamic/unresolved 保持未知。

## 错误与限制

契约限制 Run 模式、execution source、fixture scenario、工具调用/修复/源码/输入/输出/模型输入/模型输出/事件预算、分页页大小、消息 cutoff 和报告引用；real/external Run 不携带 fixture scenario，external 不携带内部 model。概览的 `fastapi_route` 要求方法属于受支持集合、声明路径以 `/` 开头、handler 为同一 index/path 的 function symbol 且落在入口证据范围内；不支持的动态路径或绑定不会被契约升级为确定路由。邻接响应必须分别解释查询截断、关系扫描覆盖和 unresolved 详情覆盖；`index_unresolved_truncated=null` 只表示旧索引未记录状态，不能当作 false；空 `neighbors` 在 `index_truncated=true` 时不能表示“没有关系”。影响查询限制 candidate/finding 唯一性、方向、深度不超过 2、节点不超过 40、边不超过 80、路径和未知引用不超过 40，并携带固定索引的相同覆盖语义；unmapped 结果必须为空且说明无法唯一映射。SSE 游标和连接资源边界由 API 流程校验，Schema 约束帧元数据、终态完成标记和公开事件形状。静态关系与候选假设分开；tool failure、insufficient evidence、cancelled 和 interrupted 不冒充 candidate_found。Schema 是机械形状校验，不能单独证明 HTTP 归属、浏览器流程、真实模型质量或执行器时序。

## 验证

修改契约后运行 `uv run --directory apps/api python -m app.export_schema` 和 `pnpm contracts:check`；`apps/api/tests/test_contracts.py` 覆盖 Run/fixture/external/impact 响应、事件序列、SSE 帧、工具结果、报告和非法引用，`apps/api/tests/test_run_sse.py` 覆盖实际 SSE 传输，`apps/api/tests/test_issue_runs.py` 覆盖 real 输出协议，`apps/api/tests/test_runs.py` 覆盖实际生命周期与固定快照影响读取，`apps/api/tests/test_storage.py` 覆盖静态影响路径，`apps/web` 单测和 typecheck 覆盖消费边界。当前 Schema 已由生成器重新生成；本轮 `pnpm contracts:check` 为 21 passed，后端全量为 128 passed，前端测试为 22 passed，类型检查、生产构建、文档检查和本地 stdio MCP/浏览器 FastAPI 路由联动均通过；真实 DeepSeek 语义质量仍为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [Run 影响查询决策](../decisions/2026-09-13-run-impact.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
- [Codex 外部 Agent Run 决策](../decisions/2026-09-13-external-agent-run.md)
- [关系扫描与 unresolved 覆盖语义](../decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../decisions/2026-09-14-fastapi-route-overview.md)
