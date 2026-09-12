# 数据契约

## 状态

已实现 Pydantic 契约和可重复 Schema 导出；前端对 Project、Index、Session/消息、概览、功能、fixture/real Run、事件、工具结果、报告和错误响应执行运行时边界校验；MCP 使用同一领域契约包装结构化结果。

## 职责与范围

[contracts.py](../../apps/api/app/contracts.py) 定义 Project/Index/Session、fixture/real Run、RunInput、预算、`IssueModelOutput`、公开事件、allowlisted ToolResult、Report、证据/图、MCP envelope 和请求/响应的字段与校验。[export_schema.py](../../apps/api/app/export_schema.py) 生成 [codetrail.json](../../contracts/schemas/codetrail.json)；HTTP 路由和前端分别拥有传输与消费编排。

## 接口与依赖

Run 的公共边界包括 `Run`、`RunInput`、`RunBudget`、`EventSequence` 中的连续事件、`ToolResult`、`Report`、`CreateRunRequest/Response`、Run history/events/tool-results 响应及取消响应。MCP 的 Run 读/写 envelope 只允许对应 `ok` 状态携带 result/report；所有请求/响应保留 `schema_version`。精确字段以声明和生成 Schema 为准，不在文档中复制完整字段表。

## 数据与生命周期

Run contract 要求 project/session/index 同时出现，输入消息是从 1 到 cutoff 的完整连续已保存前缀，input hash/字符数与消息一致；Run budget 记录工具、模型调用/修复、输入/输出字符、可选 token、源码、事件和总时间限制及使用量。real 模型每轮只能返回一个 `IssueModelToolCall` 或 `IssueModelFinal`；最终结论的 symbols/evidence/candidate links 由执行器再按本 Run 同一 index 的实际返回结果校验，原始模型文本不属于持久化契约。事件 sequence 从 1 连续递增且最后只能是一个 terminal event；terminal status 与 report outcome 必须相互一致。Report graph、evidence、candidate links 和 findings 只能引用同一 index 及实际工具返回的 evidence，取消/中断使用对应终态 outcome。

## 错误与限制

契约限制 Run 模式、fixture scenario、工具调用/修复/源码/输入/输出/模型输入/模型输出/事件预算、分页页大小、消息 cutoff 和报告引用；real Run 不携带 fixture scenario。静态关系与候选假设分开；tool failure、insufficient evidence、cancelled 和 interrupted 不冒充 candidate_found。Schema 是机械形状校验，不能单独证明 HTTP 归属、浏览器流程、真实模型质量或执行器时序。

## 验证

修改契约后运行 `uv run --directory apps/api python -m app.export_schema` 和 `pnpm contracts:check`；`apps/api/tests/test_contracts.py` 覆盖 Run/fixture 响应、事件序列、工具结果、报告和非法引用，`apps/api/tests/test_issue_runs.py` 覆盖 real 输出协议，`apps/api/tests/test_runs.py` 覆盖实际生命周期，`apps/web` 单测和 typecheck 覆盖消费边界。当前 Schema 已由生成器重新生成；本轮 `pnpm contracts:check` 为 19 passed，固定响应和文档检查均通过。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [统一接口决策](../decisions/2026-09-11-agent-native-interfaces.md)
