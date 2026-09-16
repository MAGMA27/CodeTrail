# 工作台

## 状态

已实现多项目真实工作台、Project/Index/Session/消息、索引新鲜度检查/显式更新、O1/O2/O3 浏览和 P2-R1/P2-R2 Session 内显式 fixture/real Run 面板。概览默认先展示 `python-entry-v4` 的 HTTP 路径组、源码目录组、计数和静态模块关系；选择组后可查看真实路由/模块，再从入口 evidence 进入源码或从处理函数/模块进入既有关系图。Run 面板支持模式选择、真实模式警告、启动、状态同步、持久化 SSE 事件流、有限重连/轮询回退、工具结果、Issue/代码理解报告 evidence、候选位置与有界静态影响、取消、历史选择和刷新/切换恢复；Codex external Run 会从 Session history 自动发现、选择并订阅，代码理解结果按顺序步骤和覆盖级别展示。

## 职责与范围

通过公开 HTTP API 创建/列出/切换项目、建立索引、读取概览/功能/图/源码证据，管理 Session 消息并显式启动 fixture 或 real Run；概览结构组按固定快照的声明路径/handler 模块和源码目录生成，模块关系只显示可追溯的静态 imports/calls 样本，不与语义功能混合。可从结构组中的 FastAPI 路由查看声明路径及处理函数的同快照证据，并从处理函数/模块打开既有关系图；也可从已保存 Run 报告的 candidate/finding 进入固定快照影响面板，并自动发现 MCP 创建的 Codex external Run。代码关系面板把查询截断与固定关系索引未完成分开显示；大目录分组默认收起，展开后聚焦选中节点及有限相邻节点，仍可通过平移/展开查看已加载的其余内容。前端负责当前视图、请求取消/过期响应、草稿和 Run 展示，不负责冻结消息、执行工具、鉴权、预算或组装报告；这些由 API 应用服务拥有。

## 接口与依赖

[workbench.tsx](../../apps/web/app/workbench.tsx) 编排 Project/Index/Session/Run 请求，[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx) 渲染 Session、Run、索引新鲜度和影响面板，[api.ts](../../apps/web/lib/api.ts) 校验边界，[run-event-stream.ts](../../apps/web/lib/run-event-stream.ts) 承载可测试的 SSE 订阅状态机，[globals.css](../../apps/web/app/globals.css) 提供样式。索引管理调用 freshness/refresh 项目端点；Run 调用 create/list/detail/events/`events/stream`/tool-results/report/impact/cancel 项目 Session 端点；外部 Run 通过受限 history 轮询发现后复用同一 detail/SSE 订阅链，不创建 HTTP Run；影响查询只传递报告中的精确 candidate/finding ID，并把返回 symbol/index 用于图和源码联动；接口语义和 fixture/external 限制见 [Run 执行子系统](execution.md)。

## 设计原型与生产边界

生产 `/` 使用真实服务端数据；`/prototype` 仅是演示数据原型，不调用 API、不写入项目状态。UI 布局和证据侧栏沿用 [工作区 UI 规范](../design/workspace-ui-spec.md)，Run 面板是现有 Session 视图中的显式操作，不自动随消息保存触发。

## 数据与生命周期

打开项目后从唯一的“源代码与索引”入口进入项目操作页；其中“固定索引”页签可显式执行“检查更新”或“更新索引”。检查结果显示 `consistent`、`changed` 或 `unknown`、检查时间、变化统计、扫描预算和有界路径；更新只针对当前 latest，consistent 返回 `reused` 直接复用，源码恢复到同项目中规则/配置/schema/manifest 兼容的历史内容时返回 `historical_reused` 并切换 latest 到历史 index，其他可安全重建的变化生成新的 immutable index，历史 revision/dirty metadata 不被改写，失败不替换旧 latest。“源代码来源”页签仍负责本地/GitHub 导入和 ImportAttempt 状态，GitHub 来源明确显示远端未检查。

代码理解报告使用 `outcome=explanation`：结果区域显示 `complete/partial` 覆盖和最多 12 个有序步骤，按 confirmed/inferred/unknown 区分证据强度；步骤 evidence 只能打开报告内同快照源码，唯一 symbol 可进入现有局部关系图，多符号或无符号步骤说明原因，不派生候选影响。

打开 Session 后读取消息和 Run history；history 选择活动或最新 Run，并载入状态、事件、工具结果和报告。Session 视图在首次 history 成功后以 1 秒有界轮询重新读取 history，发现 `execution_source=codex` 的 queued/running Run 时自动选择并载入详情；轮询只更新历史，不重复创建或并行建立 SSE。代码关系查询回传的 `index_truncated` 会在图面板显示扫描覆盖告警，`index_unresolved_truncated` 另显示 unresolved 详情告警，旧索引的 NULL 显示未知；当关系为空时仍保留告警，不把“未分析到”渲染成“没有关系”。图按相对目录分组，超过 12 个符号的分组首次收起；用户展开后默认把选中节点及最多 8 个节点放大，聚焦按钮重复该行为。点击报告中的候选链接或唯一 finding symbol 后，工作台按 Run 固定 index 请求 callers/callees 影响，显示根节点、静态 calls 路径、unresolved 未知边界、深度/截断、固定索引覆盖和范围说明；没有唯一映射时只显示 unmapped 空态。点击“启动离线分析”或“启动真实分析”时只发送当前 Session 的固定 index、模式、主题和 Issue body，服务端独立冻结完整已保存消息；未保存 draft 仅显示警告，不被提交。真实模式启动前明确提示会发送冻结 Issue/消息和有界源码证据、可能产生费用且失败不回退 fixture。Run 返回 queued 后前端先读取事件历史，再以最后成功 sequence 订阅 SSE；事件按 Run/sequence 去重，断线有限指数退避，`onopen` 不被视为事件进展，只有有效前进事件重置重连预算；超过上限才回退 450ms 轮询，且不与 SSE 并行，连接状态不等于 Run 失败，四类终态控制帧或终态事件均停止订阅并刷新报告。

切换 Session/Project 会 abort 并递增请求代数，关闭旧 EventSource、清理旧 Run 轮询和影响请求、状态和详情，旧流/旧影响回调不能写入新范围；刷新后从服务端历史恢复，不重复创建。Run report 的 evidence 点击进入既有源码证据检查器，影响节点点击也使用节点自身的固定 `index_id`，并校验 project/index/path/line/hash/evidence_id；工作台不展示隐藏思维链，不把后续消息或 latest index 合并进已冻结 Run。

## 错误与限制

索引检查的 `changed_paths_truncated` 与 `scan_truncated` 分开显示；unknown 不能被渲染为一致，完整安全扫描且仅含 metadata/源码/规则/配置变化时会提示可全量重建，历史兼容恢复会明确显示 latest 已切换且沿用历史来源 metadata，其他 refresh 失败会明确保留旧 latest。索引更新按钮只在当前项目有 ready latest 时可用，避免隐式创建或跨项目操作。

API 离线、归属错误、消息为空、活动 Run 冲突、预算或执行容量拒绝、模型不可用/失败、结构化输出失败、取消和 interrupted 状态显示为明确错误或状态；容量拒绝不创建本地 Run，提示可稍后重试。结构组/模块关系和入口路径受各自预算限制，工作台显示可见/总量与代表边样本边界；源码目录组可能包含因 `max_modules` 未载入的模块，下钻时明确说明。固定关系索引扫描未完成会显示“关系提取未完成”，unresolved 详情截断会显示独立告警，旧索引未记录该状态会显示未知，查询自身达到节点/边/时间/输出边界则另显示“查询已截断”；这些状态同时存在时不把空邻接当成否定证据。real Run 仍需服务端显式能力开关和持久化配置，失败不回退 fixture；fixture、real 和 external 都不执行仓库源码或 shell，external 的公开工具/报告状态只从服务端持久化事实读取。影响请求失败、无唯一映射、超出深度/节点/路径边界和 unresolved 均显示明确状态，不猜测同名符号。前端读取 Run history/events/tool-results 受服务端页大小限制，报告和证据必须绑定同一 index；请求失败不会用旧项目、旧 Session 或 latest index 的数据填补。

Session 草稿按 project/session 保存到 localStorage，消息保存旧响应不能清除新草稿；Run 请求也按项目/Session取消和代数隔离。工作台只显示公开事件、工具结果、同快照证据、报告结论和用量，不显示模型隐藏思维链。

## 验证

本轮前端新鲜度契约/交互、类型检查和生产构建，以及后端新鲜度、HTTP/MCP 对照、全量回归和文档检查通过。实际浏览器验证了旧索引缺元数据且源码变化时的 `unknown` 与安全重建提示、更新后新快照、再次检查 `consistent`、再次更新 `reused`，并确认旧 latest、Session 和 evidence 仍可读取；本项目隔离副本上的缓存/更新联合验收（含真实工作台的 changed、显式 update、consistent、reused 和历史 Session 恢复）见 [本项目索引更新与缓存效果联合验收](../decisions/2026-09-16-project-index-cache-acceptance.md)。真实供应商语义质量仍为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [Run 影响查询决策](../decisions/2026-09-13-run-impact.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [工作区 UI 规范](../design/workspace-ui-spec.md)
- [Codex 外部 Agent Run 决策](../decisions/2026-09-13-external-agent-run.md)
- [external Run 的代码理解流程与证据报告](../decisions/2026-09-14-code-understanding-explanation.md)
- [关系扫描与 unresolved 覆盖语义](../decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../decisions/2026-09-14-project-structure-overview.md)
- [本地项目索引新鲜度与显式更新](../decisions/2026-09-15-index-freshness.md)
- [本项目索引更新与缓存效果联合验收](../decisions/2026-09-16-project-index-cache-acceptance.md)
