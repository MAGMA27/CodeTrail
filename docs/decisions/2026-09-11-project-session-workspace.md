# 决策：项目共享知识与独立 Issue Session

Status: implemented

## 问题

原工作台围绕索引与概览组织，用户希望采用类似 Codex 的项目/会话导航，在同一项目下独立提出多个 Issue，同时共享代码结构和功能概览。将全部操作放在一个画布中无法清晰区分共享知识、问题上下文和执行记录。

## 决定

采用 Project—Index/Overview—Session—Run 分层。Project 共享来源、不可变索引和版本化概览；Session 拥有一个调查主题、消息与追问；Run 表示一次具体工具执行，保存输入上下文版本、预算、事件、证据与报告。补充消息不必立即执行，启动分析必须显式触发。外部 Agent 可直接查询知识，也可通过相同服务接续 Session；不要求所有查询先建会话。

UI 采用左侧项目/Session 导航、中央项目知识页或会话内容、右侧按需打开的图与源码证据面板。项目概览是持久导航入口，不依赖 Issue。会话中央展示用户需求、可见执行事件和结论，不展示隐藏思维链。借鉴交互结构而非复制品牌资产；保留已有工作台图能力，技术 ID/hash/预算放入详情，顶部突出项目与版本。

新 Session 创建时由服务端一次性解析并固定最新可用 index_id，可指定同项目旧版本；没有可用索引时不能启动分析。每个 Run 冻结消息范围或消息版本、index_id 和可选 overview generation_id；并发新增消息不修改在途 Run 输入。首版每个 Session 最多一个活动 Run，重复启动幂等。修改 Session 的后续版本需显式操作并说明影响，旧 Run/消息/证据仍指向原快照；首轮可通过新建指定版本 Session 实现，暂不做原地升级。

不同 Session 共享代码知识，不自动共享调查结论。跨会话复用需显式引用 session_id/run_id 和证据，作为带来源的上下文而非静态事实；首轮不实现跨会话自动记忆。功能概览本身仍保留模型推断标识，不能因项目共享而升级为事实。

HTTP/MCP 共用 Session 创建、列表、读取、追加消息和后续 Run 启动/查询/取消的领域契约，所有操作验证 project/session/index/run 归属。读操作不调用模型，写消息与付费执行使用分别明确的权限。传输包装可不同，领域结果、幂等及错误一致。

## 替代方案

一个项目只有一个聊天会混淆多个 Issue；一个 Session 复制整个索引会重复存储且难以保持版本一致。Session 与 Run 合并则无法清楚表达追问、重试和取消。采用引用共享固定知识、独立会话与执行记录，不引入多 Agent 或多租户。

## 验收与证据

Session/消息持久化、固定快照 Run、HTTP/MCP 归属与证据校验、公开事件和工作台恢复已实施。验收与回归入口见 [Run 执行](../../apps/api/execution.md)、[工作台](../../apps/web/workbench.md) 和 [MVP 操作说明](../mvp-operation.md)。[Codex 宿主复测](../../evals/results/p4-e2b-codex-host-20261001/codex-host-holdout.md) 保存独立项目/Session 的实际 Run 记录；[影响链验收](../../evals/results/p4-e2b-codex-host-20261001/impact-chain-acceptance.md) 记录固定快照源码、切换和刷新流程。已知案例复测不代表盲测或真实供应商质量；影响面板部分边界 UI 状态仍待验收，不能由本决策 implemented 推定全部产品验收完成。

## 后果

已按 O3 项目知识、P2-S1 Session、P2-R1/P2-R2 Run、P3-S1 SSE 与 P3-I1 静态影响查询分步实施；Codex external Run 复用同一生命周期。继续保留旧数据、证据与共享项目知识，跨会话自动记忆及真实供应商语义质量不属于本决策已验证范围。
