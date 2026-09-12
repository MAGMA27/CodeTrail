# Web 工作台

## 职责与边界

`apps/web` 提供项目/索引切换、概览、Session/消息、保存功能历史、同快照功能图、源码证据，以及 Session 内显式 fixture Run 的启动、状态、事件、工具结果、报告和取消。它只通过公开 HTTP API 访问服务端数据，不直接访问 SQLite、GitHub、宿主路径或模型；真实 Issue Agent 和 SSE 尚未接入。

## 入口与目录导航

- [app/page.tsx](app/page.tsx)：挂载生产工作台。
- [app/workbench.tsx](app/workbench.tsx)：项目、索引、Session、Run、概览和证据请求编排。
- [app/workbench-ui.tsx](app/workbench-ui.tsx)：生产三栏工作区、Session/Run 面板和证据/关系面板。
- [lib/api.ts](lib/api.ts)：HTTP 请求和 Project/Index/Session/Run/Report 响应运行时校验。
- [lib/graph.ts](lib/graph.ts)：局部静态图合并、分组和 unresolved 选择逻辑。
- [app/globals.css](app/globals.css)：工作台全局样式。
- [app/prototype/page.tsx](app/prototype/page.tsx)：只使用演示数据的 `/prototype` 评审原型，不调用 API。

## 接口与依赖

项目、索引、概览、Session/消息和 Run 均通过 `/api/projects/*` 调用；Run 使用同一 Session 的 list/detail/events/tool-results/report/cancel 端点。启动按钮只发送 Issue/场景/固定 index_id，服务端独立冻结已保存消息；前端不把已加载的消息页当作 Run 输入。HTTP 响应在 [lib/api.ts](lib/api.ts) 经过形状和 project/session/index/hash 边界校验。

## 数据与生命周期

Session 内的“离线分析”必须由用户显式点击“启动离线分析”。启动前若有草稿会显示提示，但草稿不进入 Run；服务端返回 queued 的 run_id 后，工作台以有界轮询读取 Run 状态、公开事件、工具结果和终态报告。面板展示 fixture 场景、固定消息范围、input hash、index、execution version、事件时间线、工具结果和报告 evidence，并可从报告 evidence 打开既有源码证据侧栏。

切换 Session 或项目会取消/忽略旧的 Run 请求和轮询，清空旧 Run 详情，再读取新 Session 的 Run 历史；刷新后先恢复项目/Session，再从服务端历史选择活动或最新 Run，重新载入状态，不重复创建。Run 取消使用独立写请求，成功后继续读取终态；消息保存仍只保存消息，不会隐式启动 Run。其它项目/索引/功能历史状态继续按既有 project_id/index_id 键隔离。

## 使用与配置

先启动 API，再从仓库根目录执行：

```powershell
pnpm web:dev
```

打开 `http://localhost:3000`，创建或打开项目、建立 ready 索引、创建 Session 并保存至少一条消息，在 Session 的“离线分析”面板选择 fixture 场景后点击“启动离线分析”。`success-timeout-missing` 展示候选结论和同快照 evidence；`insufficient-external-service` 展示证据不足；`tool-failure-read-source` 展示工具失败及失败前保留的 evidence。

## 错误与限制

API 离线、项目/Session/index 不属于当前范围、消息未保存、空 Session、Run 活动冲突、预算或执行容量拒绝、取消和重启中断都会显示明确状态；容量满时服务端在落库前返回可重试错误，工作台不会伪造 queued Run；Run 失败不会伪造成功结论。工作台不显示隐藏思维链，只显示服务端公开事件、工具结果、证据和报告；real Run 当前由服务端拒绝，fixture 也不会执行仓库源码或 shell。Run 历史、事件和工具结果使用服务端有界分页，前端当前读取每页最多 40/100/40 项；报告 evidence 必须和 Run 的固定 index_id 一致，否则拒绝载入。

草稿继续按 project/session 保存到浏览器 localStorage，切换范围不串入；消息保存中的旧响应不能清除新草稿。证据链接携带 project_id、index_id、路径、行范围、content hash 和 evidence_id，Session 链接携带 project_id/session_id，不回退到 latest index。

## 验证

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`。Run 的 HTTP 生命周期、取消后终态报告、刷新/切换恢复和浏览器视觉流程已与实际运行 API 复核；真实供应商语义质量和 SSE 尚未验证。

## 关联文档

- [工作台子系统说明](../../docs/subsystems/workbench.md)
- [Run 执行子系统说明](../../docs/subsystems/execution.md)
- [API README](../api/README.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
