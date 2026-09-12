# 工作台

## 状态

已实现多项目真实工作台、Project/Index/Session/消息、O1/O2/O3 浏览和 P2-R1 Session 内显式 fixture Run 面板。Run 面板支持启动、状态轮询、公开事件、工具结果、报告 evidence、取消、历史选择和刷新/切换恢复；真实 Issue Agent 和 SSE 尚未接入。

## 职责与范围

通过公开 HTTP API 创建/列出/切换项目、建立索引、读取概览/功能/图/源码证据，管理 Session 消息并显式启动 fixture Run。前端负责当前视图、请求取消/过期响应、草稿和 Run 展示，不负责冻结消息、执行工具、鉴权、预算或组装报告；这些由 API 应用服务拥有。

## 接口与依赖

[workbench.tsx](../../apps/web/app/workbench.tsx) 编排 Project/Index/Session/Run 请求，[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx) 渲染 Session 和 Run 面板，[api.ts](../../apps/web/lib/api.ts) 校验边界，[globals.css](../../apps/web/app/globals.css) 提供样式。Run 调用 create/list/detail/events/tool-results/report/cancel 项目 Session 端点；接口语义和 fixture 限制见 [Run 执行子系统](execution.md)。

## 设计原型与生产边界

生产 `/` 使用真实服务端数据；`/prototype` 仅是演示数据原型，不调用 API、不写入项目状态。UI 布局和证据侧栏沿用 [工作区 UI 规范](../design/workspace-ui-spec.md)，Run 面板是现有 Session 视图中的显式操作，不自动随消息保存触发。

## 数据与生命周期

打开 Session 后读取消息和 Run history；history 选择活动或最新 Run，并载入状态、事件、工具结果和报告。点击“启动离线分析”时只发送当前 Session 的固定 index、主题、场景和服务端可见的 Issue body，服务端独立冻结完整已保存消息；未保存 draft 仅显示警告，不被提交。Run 返回 queued 后前端以 450ms 有界轮询，终态再读取报告；SSE 尚未使用。

切换 Session/Project 会 abort 并递增请求代数，清理旧 Run 轮询、状态和详情；刷新后从服务端历史恢复，不重复创建。Run report 的 evidence 点击进入既有源码证据检查器，并校验 project/index/path/line/hash/evidence_id；工作台不展示隐藏思维链，不把后续消息或 latest index 合并进已冻结 Run。

## 错误与限制

API 离线、归属错误、消息为空、活动 Run 冲突、预算拒绝、取消和 interrupted 状态显示为明确错误或状态；real Run 当前显示服务端拒绝，fixture 不执行仓库源码或 shell。前端读取 Run history/events/tool-results 受服务端页大小限制，报告和证据必须绑定同一 index；请求失败不会用旧项目、旧 Session 或 latest index 的数据填补。

Session 草稿按 project/session 保存到 localStorage，消息保存旧响应不能清除新草稿；Run 请求也按项目/Session取消和代数隔离。工作台只显示公开事件、工具结果、同快照证据、报告结论和用量，不显示模型隐藏思维链。

## 验证

已运行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`；后端生命周期与 MCP 对照由 `apps/api/tests/test_runs.py`、`test_mcp.py` 覆盖。浏览器实际 Run 启动、报告 evidence、取消、切换和刷新流程在最终交付前复核，真实供应商语义质量与 SSE 为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [工作区 UI 规范](../design/workspace-ui-spec.md)
