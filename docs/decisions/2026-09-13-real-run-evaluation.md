# 决策：真实 Run 离线评测适配

Status: implemented

## 问题

P4-E1 已冻结输入、独立标注和关键词基线，但还没有验证模型结果能否沿用产品的 Session/Run/工具/报告契约并进入同一评分边界。直接让评测器自建模型工具循环会产生第二套预算、权限和证据规则，也难以在中断后安全恢复。

## 决定

P4-E2a 新增独立的模型评测编排：先只加载冻结 Issue、manifest 和源码，逐案例复制到受控运行目录，再通过 `WorkspaceService` 创建项目、索引、Session、消息和显式 `real` Run。模型适配器仍由 RunExecutor 注入，工具、预算、取消、终态持久化和报告均复用产品路径。

默认入口是本地 `offline_fake` provider。它实现 `IssueModelAdapter` 的多轮 search/read/final 协议以及显式 `insufficient_evidence`，只用于验证组装、隔离、用量和评分，不能作为真实模型能力结果。外部 provider 在 P4-E2a 中明确拒绝，真实供应商运行留到单独授权的后续步骤。

执行前生成不含标注和基线候选的 dry-run 清单；每个终态 Run 先写入 gold-free raw state 和原始报告引用。恢复时复用 checkpoint 或跳过已有终态，不能静默重复 Run。所有模型最终候选只从报告的 `candidate_links`/`findings` 映射，重复位置稳定去重；中间观察符号、非法证据、失败、取消和超时不继承为命中。

评分在所有案例执行结果持久化后才加载 annotation，沿用 P4-E1 的 file/symbol/location Hit@1/3、证据合法性、证据不足弃答/误报和失败分母。token 用量缺失记为 null/unknown，不能以零代替；对照报告必须醒目标记模拟 provider，不输出模型优于基线的语义结论。

## 替代方案

- 在 `evals/` 里重新实现搜索、读取和模型循环：会复制产品的权限、预算和证据校验，拒绝采用。
- 执行前加载 annotation 或把 gold 传给假供应商：会污染模型输入和隔离验证，拒绝采用。
- 用 P4-E1 关键词结果冒充模型候选：无法衡量最终报告映射、弃答和 Run 失败，拒绝采用。
- P4-E2a 直接调用真实供应商：会在适配和评分尚未验收时产生不可控费用和语义误判，留给后续显式授权步骤。

## 验收与证据

实现入口为 [模型评测编排](../../evals/model_pipeline.py)、[评测模型契约](../../evals/model_models.py) 和 [评测 README](../../evals/README.md)。

- `pnpm evals:validate` 通过，冻结数据版本和六例划分未变。
- `pnpm evals:test` 通过（16 passed），覆盖 gold-free 清单、真实 Run 离线假供应商、多轮工具结果、显式弃答、预算失败、取消、恢复去重、最终候选去重和非法证据映射。
- 离线集成结果写入 `evals/results/p4-e2a-v1/`；六例均沿真实 Run 路径产生终态，模型和对照报告明确标记 `offline-fake`。结果为 6/6 成功、4/4 可定位案例证据合法、2/2 证据不足案例明确弃答，token 用量为 unknown/null。
- `pnpm contracts:check`（21 passed）、`pnpm docs:check`、后端回归（121 passed）和 Python 编译检查通过；本步不改变共享 API/Pydantic 契约。

## 后果

P4-E2a 可以在无 API Key、无网络和不读取 gold 的条件下验收 Run 适配及评分事实，并保留原始报告、hash、用量、失败原因和恢复状态。假供应商只证明协议和边界，不证明真实模型语义质量；外部调用、费用核算和更大留出集仍需独立授权与评测记录。`memory-bank/` 继续按项目约定忽略，`next.md` 由用户控制且本步不修改。
