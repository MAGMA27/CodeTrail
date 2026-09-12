# Web 工作台

## 职责与边界

`apps/web` 提供项目/索引切换、概览、Session/消息、保存功能历史、同快照功能图、源码证据，以及 Session 内显式 fixture/real Run 的启动、状态、事件、工具结果、报告、SSE 订阅和取消。它只通过公开 HTTP API 访问服务端数据，不直接访问 SQLite、GitHub、宿主路径或模型。

## 入口与目录导航

- [app/page.tsx](app/page.tsx)：挂载生产工作台。
- [app/workbench.tsx](app/workbench.tsx)：项目、索引、Session、Run、概览和证据请求编排。
- [app/workbench-ui.tsx](app/workbench-ui.tsx)：生产三栏工作区、Session/Run 面板和证据/关系面板。
- [lib/api.ts](lib/api.ts)：HTTP 请求和 Project/Index/Session/Run/Report 响应运行时校验。
- [lib/run-event-stream.ts](lib/run-event-stream.ts)：注入式 EventSource/计时器订阅状态机，负责 SSE 校验、续接、终态和有界回退。
- [lib/graph.ts](lib/graph.ts)：局部静态图合并、分组和 unresolved 选择逻辑。
- [app/globals.css](app/globals.css)：工作台全局样式。
- [app/prototype/page.tsx](app/prototype/page.tsx)：只使用演示数据的 `/prototype` 评审原型，不调用 API。

## 接口与依赖

项目、索引、概览、Session/消息和 Run 均通过 `/api/projects/*` 调用；Run 使用同一 Session 的 list/detail/events、`events/stream`、tool-results/report/cancel 端点。启动按钮只发送 Issue/模式/fixture 场景/固定 index_id，服务端独立冻结已保存消息；前端不把已加载的消息页当作 Run 输入。HTTP 响应在 [lib/api.ts](lib/api.ts) 经过形状和 project/session/index/hash 边界校验，SSE 事件按 schema、Run 和连续 sequence 校验；`run.stream.end` 只接受 `succeeded`、`failed`、`cancelled`、`interrupted` 四类 Run 终态。

## 数据与生命周期

Session 内的 fixture/real 分析必须由用户显式点击对应启动按钮。真实模式启动前显示会发送冻结 Issue、已保存消息和有界源码证据、可能产生费用且失败不回退的提示；启动前若有草稿也会显示提示，但草稿不进入 Run。服务端返回 queued 的 run_id 后，工作台先读取已持久化事件建立历史，再以 `after_sequence` 订阅 SSE 逐项接收新增公开事件；连接失败经过有限退避后回退到有界轮询。`onopen` 只表示握手成功，不清零无进展重连计数；只有连续 sequence 的新事件重置重连预算，达到上限后只保留一个轮询定时器。面板展示模式、固定消息范围、input hash、index、execution version、事件时间线、工具结果和报告 evidence，并可从报告 evidence 打开既有源码证据侧栏。

切换 Session 或项目会取消/忽略旧的 Run 请求和轮询，关闭旧 EventSource 并使旧回调失效，清空旧 Run 详情，再读取新 Session 的 Run 历史；刷新后先恢复项目/Session，再从服务端历史选择活动或最新 Run，重新载入状态，不重复创建。Run 事件以 Run/sequence 去重，断线使用最后成功处理的 sequence 续接；连接状态独立于 Run 状态，终态后停止订阅和刷新。Run 取消使用独立写请求，成功后继续读取终态；消息保存仍只保存消息，不会隐式启动 Run。其它项目/索引/功能历史状态继续按既有 project_id/index_id 键隔离。

## 使用与配置

先启动 API，再从仓库根目录执行：

```powershell
pnpm web:dev
```

打开 `http://localhost:3000`，创建或打开项目、建立 ready 索引、创建 Session 并保存至少一条消息，在 Session 的“Issue 分析”面板选择 fixture 场景后点击“启动离线分析”，或选择 real 后点击“启动真实分析”。`success-timeout-missing` 展示候选结论和同快照 evidence；`insufficient-external-service` 展示证据不足；`tool-failure-read-source` 展示工具失败及失败前保留的 evidence。real 还要求 API 设置 `CODETRAIL_REAL_RUN_ENABLED=1` 并在服务数据目录配置 `config.json`。

## 错误与限制

API 离线、项目/Session/index 不属于当前范围、消息未保存、空 Session、Run 活动冲突、预算或执行容量拒绝、模型不可用/失败、结构化输出无效、取消和重启中断都会显示明确状态；容量满时服务端在落库前返回可重试错误，工作台不会伪造 queued Run；Run 失败不会伪造成功结论。工作台不显示隐藏思维链，只显示服务端公开事件、工具结果、证据和报告；real Run 的能力开关和配置由服务端控制，fixture/real 都不会执行仓库源码或 shell。Run 历史、事件和工具结果使用服务端有界分页，前端当前读取每页最多 40/100/40 项；报告 evidence 必须和 Run 的固定 index_id 一致，否则拒绝载入。

草稿继续按 project/session 保存到浏览器 localStorage，切换范围不串入；消息保存中的旧响应不能清除新草稿。证据链接携带 project_id、index_id、路径、行范围、content hash 和 evidence_id，Session 链接携带 project_id/session_id，不回退到 latest index。

## 验证

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`。本轮前端测试 17 项通过，覆盖订阅状态机的四类终态控制帧、错误终态、去重/缺号、连续 open-error 上限、按最后 sequence 续接以及旧回调清理；Run 的 HTTP 生命周期、取消、终态报告/evidence 联动、刷新/切换恢复和本地受限 SSE 生命周期断流后的浏览器续接已用假供应商复核。真实浏览器离线网络切换和真实供应商语义质量未验证。

## 关联文档

- [工作台子系统说明](../../docs/subsystems/workbench.md)
- [Run 执行子系统说明](../../docs/subsystems/execution.md)
- [API README](../api/README.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
