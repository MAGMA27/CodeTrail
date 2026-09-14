# 决策：external Run 的代码理解流程与证据报告

Status: implemented

## 问题

当前 external Run 的结构化结论只有 `candidate_found` 和 `insufficient_evidence`，适合 Issue 调查，但不能表达“已经解释了用户询问的代码流程、没有缺陷候选”。继续复用缺陷候选字段会把正常流程理解误导成风险报告，也会让工作台无法按步骤阅读关键函数和证据。

本步需要保持 Project/Session/Run、固定 `index_id`、实际工具证据、预算、SSE 和幂等边界不变；内部 fixture/real Run 继续使用 Issue 模型协议，不要求真实供应商支持新的任务类型。

## 决定

- 在 Run 上增加 `analysis_kind`，取值为 `issue` 或 `understanding`，默认 `issue`。只有 `mode=external` 可以使用 `understanding`；旧 Run、fixture 和 real Issue Run 缺少该字段时按 `issue` 读取，不做数据库迁移，字段随现有 `run_json` 持久化。
- 新增独立的 `CodeUnderstandingFinal`，结果 `outcome` 固定为 `explanation`；保留 `IssueModelFinal` 及 `ModelOutput` 的既有两类 Issue 结果，不把解释结果加入内部模型循环。
- `explanation` 包含 `coverage=complete|partial` 和最多 12 个按 Agent 给出的顺序排列的 `ExplanationStep`。步骤有稳定 `step_id`、`kind=confirmed|inferred|unknown`、标题、简短说明、`evidence_ids` 和可选 `symbol_ids`。confirmed 步骤必须有 evidence；complete 不得包含 unknown 步骤；整份结果至少有一份由本 Run `read_source` 实际返回的 evidence。未知步骤可以没有 evidence，但说明字段必须明确缺口。
- WorkspaceService 根据 Run 的 `analysis_kind` 选择并校验 external 结论；所有步骤的 evidence 和 symbol 必须来自同一 Run、同一固定 index。解释不生成 `candidate_links`，不进入报告影响查询；静态 `graph` 仍只保留工具实际返回的事实。
- `Report.outcome` 增加 `explanation`，并增加可选 `explanation` 载荷；旧报告默认为空并保持原有状态/候选/影响语义。`run.completed` 允许该成功结果，HTTP、MCP、SSE 和幂等比较使用同一字段。
- `start_external_run` 增加可选 `analysis_kind`，`submit_external_run` 增加 `explanation` 结构和 `outcome=explanation`。MCP 只做协议到领域模型的映射，服务层拥有 Run 类型、作用域、证据、步骤和终态校验。
- 工作台在报告 outcome 为 explanation 时显示“流程解释”、覆盖级别和顺序步骤；每步只从报告 evidence 打开源码，唯一 symbol 复用现有关系图入口，unknown/partial 显示限制，不显示候选影响按钮。刷新、历史切换和旧缺陷报告继续沿原路径工作。

## 替代方案

- 扩展 `IssueModelFinal`：实现更少，但会让内部 real Issue 模型自动暴露新任务语义，并把正常理解和缺陷调查混在一个协议中，拒绝。
- 继续使用 `insufficient_evidence`：无法区分“调查失败/证据不足”和“流程解释完成但没有缺陷候选”，也不能稳定承载有序步骤，拒绝。
- 为流程解释创建独立 Run/存储表：会复制生命周期、证据和幂等规则，造成 HTTP/MCP 漂移，拒绝。

## 验收与证据

- 契约测试覆盖 explanation、complete/partial、confirmed 无证据、complete 含 unknown、重复/跨作用域/未返回 evidence 或 symbol、candidate link 禁止、旧报告默认读取。
- Service/MCP 测试覆盖 understanding 首次提交、至少三个有源码支持的步骤、unknown 缺口、同内容重试、改变步骤冲突、HTTP/MCP 报告一致和单一终态事件；既有 Issue/external 多 evidence 回归继续通过。
- 前端 API 解析、步骤阅读、源码 evidence、唯一 symbol 下钻、partial/unknown 文案和刷新恢复通过；旧候选报告仍能打开影响范围。
- 运行相关后端/前端测试、类型检查、生产构建、Python 编译、Schema 导出与 `contracts:check`、`docs:check`、`docs:check:local`；实际 Codex 宿主首次 explanation 提交与工作台发现若需 reload，记录实际 reload 结果，不以独立进程替代。

代码侧最终验证通过：后端全量 134 passed，前端测试 24 passed，契约测试 23 passed，类型检查、生产构建、compileall、Schema 导出、doc-sync 和本地文档检查均通过。当前 Codex `app-server` 仍缓存旧的 MCP 工具 schema；本轮对旧宿主进行了实际探测并取消了探测 Run，但未以旧 schema 冒充 explanation 首次提交，因此宿主 explanation 提交与工作台自动发现保持 UNVERIFIED，不能由本地 stdio 结果替代。

## 后果

该结果语义只表达用户所问范围内的代码流程解释，不承诺完整运行时架构，也不把推断升级为静态事实。步骤本身是报告解释顺序，不生成静态 calls 边；真实模型语义质量、TypeScript 索引和动态注册仍保持未验证。旧报告无需重写，Schema 由 Pydantic 生成，前端必须显式消费新增可选字段。
