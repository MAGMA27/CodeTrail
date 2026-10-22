# 决策：在 real Run 中支持直接代码理解

Status: proposed

## 问题

代码理解已有 explanation 报告、固定快照工具证据和外部 Agent Run，但工作台不能直接请求服务端模型解释代码。现有模型工具循环只接受 Issue 结论，正常解释若复用 Issue 结果会错误地产生缺陷候选语义。

## 决定

- 将 `analysis_kind` 作为任务协议选择：`issue` 继续为默认；`understanding` 可用于 `real` 与 `external` Run，fixture 明确拒绝。旧 external-only 取舍仍描述其历史范围；本决定只扩展理解协议的适用模式。
- Real understanding 复用现有 `WorkspaceService`、内部 RunExecutor、Session/Run 快照、模型配置、白名单工具、模型调用/修复/时间/并发预算、事件、SSE、取消及报告持久化。它只调用 `search_code`、`read_source` 和 `get_neighbors`，不执行仓库代码、shell 或测试。
- 按 `analysis_kind` 选择独立的模型系统提示和 Pydantic 输出 union。理解结论使用 `CodeUnderstandingFinal`，引用必须属于本 Run 实际读取的源码 evidence 与观察到的 symbols，仍绑定创建时冻结的 `index_id`。
- 成功的理解 Run 保存已有 `Report.explanation`，不写 Issue findings 或 candidate links，也不发 `finding.created` 事件；失败理解报告同样不附带 Issue finding。Issue final、提示和报告语义保持原样。
- HTTP 与 MCP 使用同一个 `CreateRunRequest.analysis_kind` 和服务层实现。没有可用服务端模型配置时，real Run 仍返回明确的 `model_unavailable`；工作台可选择现有 external MCP 流程，不自动启动模型调用。
- 继续保留 `CreateRunRequest.issue` 字段以兼容共享 Run 记录；理解提示以冻结的 Session 消息作为用户问题，不把该字段当作缺陷调查材料。

## 替代方案

- 让所有解释继续走 external Agent：保留了证据闭环但不能在已配置服务端模型的工作台直接理解代码，不能满足本轮入口目标。
- 把 `CodeUnderstandingFinal` 并入 Issue 输出或 `ModelOutput`：会使 Issue real prompt 和结论携带非 Issue 语义，破坏现有协议边界。
- 建立独立 explanation executor/存储：会重复 Run 生命周期、预算、证据、SSE 和取消规则。

## 验收与证据

- 确定性 fake adapter 经实际 HTTP Run 创建、模型循环、工具执行、报告与事件组装，保存同快照 explanation/evidence；验证 understanding 提示读取冻结用户问题、输出无 Issue findings/candidate links，未返回 evidence 被拒绝。
- 既有 Issue HTTP/MCP 协议保持通过；fixture understanding 被拒绝，HTTP/MCP real 与 external 均使用相同 `analysis_kind` 契约。
- 根代理完成隔离 localhost API 与网页“理解代码”流程、实时 SSE 和刷新/源码返回验收后，再更新本决定状态。真实供应商语义质量不在确定性回归中宣称通过。

## 后果

此决定扩展 [external Run 代码理解决策](2026-09-14-code-understanding-explanation.md) 的任务模式，不改变其 explanation 字段、同 Run 证据和不生成缺陷候选的约束。`real` understanding 的配置缺失、供应商失败与预算耗尽沿用既有明确 Run 失败状态；模型协议分别版本化，旧 Issue Run 与持久化结果无需迁移。
