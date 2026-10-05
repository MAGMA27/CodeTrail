# Web 工作台

## 职责与边界

`apps/web` 提供项目/索引切换、索引新鲜度检查与显式更新、概览、Session/消息、保存功能历史、同快照功能图、源码证据，以及 Session 内显式 fixture/real Run 的启动、状态、事件、工具结果、Issue/代码理解报告、候选位置与有界静态影响、SSE 订阅和取消；Codex external Run 会被自动发现并接入同一展示链。它只通过公开 HTTP API 访问服务端数据，不直接访问 SQLite、GitHub、宿主路径或模型。

## 入口与目录导航

- [app/page.tsx](app/page.tsx)：挂载生产工作台。
- [app/workbench.tsx](app/workbench.tsx)：项目、索引、Session、Run、概览和证据请求编排。
- [app/workbench-ui.tsx](app/workbench-ui.tsx)：生产三栏工作区、Session/Run 面板和证据/关系面板。
- [lib/api.ts](lib/api.ts)：HTTP 请求和 Project/Index/Session/Run/Report/Impact 响应运行时校验。
- [lib/run-event-stream.ts](lib/run-event-stream.ts)：注入式 EventSource/计时器订阅状态机，负责 SSE 校验、续接、终态和有界回退。
- [lib/graph.ts](lib/graph.ts)：局部静态图合并、分组和 unresolved 选择逻辑。
- [app/globals.css](app/globals.css)：工作台全局样式。
- [app/prototype/page.tsx](app/prototype/page.tsx)：只使用演示数据的 `/prototype` 评审原型，不调用 API。

## 接口与依赖

项目、索引、新鲜度、概览、Session/消息和 Run 均通过 `/api/projects/*` 调用；索引管理提供检查更新和显式更新，前端展示 `consistent`、`changed`、`unknown`、变化计数、扫描预算及有界路径，更新返回复用或新快照结果。GitHub freshness 额外展示 `remote_status`、来源 URL、请求/解析 ref、远端 commit 和基线索引 commit；检查只解析远端 metadata，不下载归档，显式 GitHub 更新调用独立的 `github-refresh` 入口。概览先展示固定索引中的结构组：HTTP 路径按静态声明前缀与 handler 模块分组，源码按相对目录分组；选择分组后查看真实入口或可见模块。FastAPI 路由入口显示 HTTP 方法、声明路径和处理函数，点击入口先打开装饰器到函数结束的同快照 evidence，关系图按钮则以处理函数作为既有静态图根节点；模块关系区域展示聚合的静态 imports/calls、总边数和最多 8 条源码位置样本。结构组与模块关系是静态组织事实，不是语义功能，前端对组、模块和关系的可见/总量分别显示预算边界。Run 使用同一 Session 的 list/detail/events、`events/stream`、tool-results/report/impact/cancel 端点。启动按钮只发送 Issue/模式/fixture 场景/固定 index_id，服务端独立冻结已保存消息；前端不把已加载的消息页当作 Run 输入。Codex external Run 不由网页创建，工作台在 Session history 成功后有界轮询 `execution_source=codex` 的活动 Run，再复用 detail/events/SSE 读取链。代码关系面板区分邻接查询 `truncated`、固定关系扫描 `index_truncated` 和 unresolved 详情 `index_unresolved_truncated`，并对旧索引的未知状态单独提示；大目录分组默认收起，展开后按选中节点及有限相邻节点聚焦。影响面板从报告候选或唯一 finding symbol 发起，重新查询 Run 固定 index 的静态 `calls`，方向/深度由用户控制且不修改 Run。HTTP 响应在 [lib/api.ts](lib/api.ts) 经过形状和 project/session/index/hash 边界校验，SSE 事件按 schema、Run 和连续 sequence 校验；`run.stream.end` 只接受 `succeeded`、`failed`、`cancelled`、`interrupted` 四类 Run 终态。

工作台只保留一个稳定的“源代码与索引”项目操作入口。入口页内分为“源代码来源”和“固定索引”两个页签：前者承载本地/GitHub 导入及 ImportAttempt 状态，后者承载固定版本、检查更新和显式更新。两者共享项目上下文，但仍分别调用导入和项目索引接口；前端不直接访问来源路径或 SQLite。

固定索引页签的本地检查结果由服务端读取源码 manifest，按内容 hash 区分 `consistent`、`changed`、`unknown`；GitHub 检查只做跟踪 ref→commit 的 bounded metadata 请求，远端状态与本地规则/配置状态分别显示，远端 commit 一致不代表索引规则一致。前端同时显示变化统计、扫描预算、路径列表和扫描不完整告警，完整安全扫描且仅为旧 metadata/源码/规则/配置变化时提示可全量重建。更新成功显示复用或新建快照，GitHub ref 在检查与下载之间移动时以实际导入 commit 为准，失败不替换旧 latest。

索引更新的响应模式为 `reused`、`historical_reused` 或 `created`。当源码恢复到同项目中规则、配置、schema 和完整 manifest 均兼容的历史内容时，工作台提示 latest 已切换到历史 immutable index，并保留历史 revision/dirty 等来源 metadata；前端不把该场景伪装成当前快照复用。

## 数据与生命周期

影响范围打开时，桌面画布与报告按剩余宽度分配，窄屏覆盖中央内容；普通源码/关系面板仍使用可调侧栏。断点和布局约束见 [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)。

当 Run 报告的 `outcome=explanation` 时，工作台在结果区域显示覆盖级别和最多 12 个有序步骤；每步显示 confirmed/inferred/unknown 状态、说明、同一报告的源码 evidence 按钮。partial/unknown 会明确显示未确认范围；只有一个有效 symbol 时可进入现有局部关系图，多符号或无符号步骤保留源码证据，不显示候选影响按钮。旧 Issue 报告继续使用候选/影响展示。

Session 内的 fixture/real 分析必须由用户显式点击对应启动按钮；Codex external 分析由 MCP Agent 启动，网页只发现和展示。真实模式启动前显示会发送冻结 Issue、已保存消息和有界源码证据、可能产生费用且失败不回退的提示；启动前若有草稿也会显示提示，但草稿不进入 Run。服务端返回 queued 的 run_id 后，工作台先读取已持久化事件建立历史，再以 `after_sequence` 订阅 SSE 逐项接收新增公开事件；连接失败经过有限退避后回退到有界轮询。`onopen` 只表示握手成功，不清零无进展重连计数；只有连续 sequence 的新事件重置重连预算，达到上限后只保留一个轮询定时器。面板展示模式、execution source、固定消息范围、input hash、index、execution version、事件时间线、工具结果、报告 evidence 和限制，并可从报告 evidence 打开既有源码证据侧栏。

切换 Session 或项目会取消/忽略旧的 Run 请求和轮询，关闭旧 EventSource、取消影响查询并使旧回调失效，清空旧 Run 详情，再读取新 Session 的 Run 历史；刷新后先恢复项目/Session，再从服务端历史选择活动或最新 Run，重新载入状态，不重复创建。Run 事件以 Run/sequence 去重，断线使用最后成功处理的 sequence 续接；连接状态独立于 Run 状态，终态后停止订阅和刷新。Run 取消使用独立写请求，成功后继续读取终态；消息保存仍只保存消息，不会隐式启动 Run。影响节点和源码证据优先使用返回的 symbol.index_id，选择旧 Run 时不会回退到当前 latest index；请求失败、切换和刷新都会清理影响面板。其它项目/索引/功能历史状态继续按既有 project_id/index_id 键隔离。

## 使用与配置

先按 [MVP 操作说明](../../docs/mvp-operation.md) 启动 API，再从仓库根目录执行共享启动脚本：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service web
```

打开 `http://localhost:3000`，创建或打开项目、建立 ready 索引、创建 Session 并保存至少一条消息，在 Session 的“Issue 分析”面板选择 fixture 场景后点击“启动离线分析”，或选择 real 后点击“启动真实分析”。`success-timeout-missing` 展示候选结论和同快照 evidence；`insufficient-external-service` 展示证据不足；`tool-failure-read-source` 展示工具失败及失败前保留的 evidence。real 还要求 API 设置 `CODETRAIL_REAL_RUN_ENABLED=1` 并在服务数据目录配置 `config.json`。

CodeTrail 作为自身仓库的 Codex external 验收流程见 [MVP 操作说明](../../docs/mvp-operation.md)；external Run 使用本地 MCP 和固定项目/Session，不需要真实供应商配置。

代码流程理解由 MCP external Run 的 `start_external_run(analysis_kind="understanding")` 发起，网页不创建该 Run，只在发现后展示其 explanation 报告；真实供应商语义质量和完整运行时覆盖仍需单独验证。

历史兼容恢复属于安全扫描确认后的显式更新；若扫描不完整、来源不可用或基准已过期，工作台继续显示失败并保留旧 latest。

## 错误与限制

API 离线、项目/Session/index 不属于当前范围、索引不在 latest、源码扫描不完整或来源不可用、消息未保存、空 Session、Run 活动冲突、预算或执行容量拒绝、模型不可用/失败、结构化输出无效、取消和重启中断都会显示明确状态；unknown 不渲染为一致，完整安全扫描下的旧 metadata 组合会提示可重建，其他更新失败保留旧 latest。容量满时服务端在落库前返回可重试错误，工作台不会伪造 queued Run；Run 失败不会伪造成功结论。工作台不显示隐藏思维链，只显示服务端公开事件、工具结果、证据、报告和限制；real Run 的能力开关和配置由服务端控制，fixture/real/external 都不会执行仓库源码或 shell。固定关系扫描未完成、unresolved 详情截断和旧索引未知状态分别显示覆盖告警，查询自身截断时另显示查询告警；空邻接不能证明没有关系。Run 历史、事件和工具结果使用服务端有界分页，影响查询最多返回 40 个节点、40 条解释路径和 40 个 unresolved；前端显式显示 mapped/unmapped、截断、静态 calls、未知边界和范围说明。报告 evidence 与影响节点必须和 Run 的固定 index_id 一致，否则拒绝载入；外部 Run 终态或超时后不会继续显示新工具活动。

草稿继续按 project/session 保存到浏览器 localStorage，切换范围不串入；消息保存中的旧响应不能清除新草稿。证据链接携带 project_id、index_id、路径、行范围、content hash 和 evidence_id，Session 链接携带 project_id/session_id，不回退到 latest index。

## 验证

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`。本轮前端测试覆盖新鲜度响应校验、结构组、模块关系、概览路由契约及既有订阅状态机；类型检查和生产构建通过。实际浏览器验证检查更新→变化摘要，并在无法确定时拒绝显式更新、保留旧 latest；变化后的新快照切换由后端业务回归验证。真实供应商语义质量仍未验证。

## 关联文档

- [工作台子系统说明](../../docs/subsystems/workbench.md)
- [Run 执行子系统说明](../../docs/subsystems/execution.md)
- [API README](../api/README.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [external Run 的代码理解流程与证据报告](../../docs/decisions/2026-09-14-code-understanding-explanation.md)
- [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)
