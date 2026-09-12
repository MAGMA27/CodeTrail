# 工作台

## 状态

已实现多项目真实工作台、Project/Index/Session/消息、O1/O2/O3 浏览和 P2-R1/P2-R2 Session 内显式 fixture/real Run 面板。Run 面板支持模式选择、真实模式警告、启动、状态同步、持久化 SSE 事件流、有限重连/轮询回退、工具结果、报告 evidence、候选位置与有界静态影响、取消、历史选择和刷新/切换恢复。

## 职责与范围

通过公开 HTTP API 创建/列出/切换项目、建立索引、读取概览/功能/图/源码证据，管理 Session 消息并显式启动 fixture 或 real Run；可从已保存 Run 报告的 candidate/finding 进入固定快照影响面板。前端负责当前视图、请求取消/过期响应、草稿和 Run 展示，不负责冻结消息、执行工具、鉴权、预算或组装报告；这些由 API 应用服务拥有。

## 接口与依赖

[workbench.tsx](../../apps/web/app/workbench.tsx) 编排 Project/Index/Session/Run 请求，[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx) 渲染 Session、Run 和影响面板，[api.ts](../../apps/web/lib/api.ts) 校验边界，[run-event-stream.ts](../../apps/web/lib/run-event-stream.ts) 承载可测试的 SSE 订阅状态机，[globals.css](../../apps/web/app/globals.css) 提供样式。Run 调用 create/list/detail/events/`events/stream`/tool-results/report/impact/cancel 项目 Session 端点；影响查询只传递报告中的精确 candidate/finding ID，并把返回 symbol/index 用于图和源码联动；接口语义和 fixture 限制见 [Run 执行子系统](execution.md)。

## 设计原型与生产边界

生产 `/` 使用真实服务端数据；`/prototype` 仅是演示数据原型，不调用 API、不写入项目状态。UI 布局和证据侧栏沿用 [工作区 UI 规范](../design/workspace-ui-spec.md)，Run 面板是现有 Session 视图中的显式操作，不自动随消息保存触发。

## 数据与生命周期

打开 Session 后读取消息和 Run history；history 选择活动或最新 Run，并载入状态、事件、工具结果和报告。点击报告中的候选链接或唯一 finding symbol 后，工作台按 Run 固定 index 请求 callers/callees 影响，显示根节点、静态 calls 路径、unresolved 未知边界、深度/截断和范围说明；没有唯一映射时只显示 unmapped 空态。点击“启动离线分析”或“启动真实分析”时只发送当前 Session 的固定 index、模式、主题和 Issue body，服务端独立冻结完整已保存消息；未保存 draft 仅显示警告，不被提交。真实模式启动前明确提示会发送冻结 Issue/消息和有界源码证据、可能产生费用且失败不回退 fixture。Run 返回 queued 后前端先读取事件历史，再以最后成功 sequence 订阅 SSE；事件按 Run/sequence 去重，断线有限指数退避，`onopen` 不被视为事件进展，只有有效前进事件重置重连预算；超过上限才回退 450ms 轮询，且不与 SSE 并行，连接状态不等于 Run 失败，四类终态控制帧或终态事件均停止订阅并刷新报告。

切换 Session/Project 会 abort 并递增请求代数，关闭旧 EventSource、清理旧 Run 轮询和影响请求、状态和详情，旧流/旧影响回调不能写入新范围；刷新后从服务端历史恢复，不重复创建。Run report 的 evidence 点击进入既有源码证据检查器，影响节点点击也使用节点自身的固定 `index_id`，并校验 project/index/path/line/hash/evidence_id；工作台不展示隐藏思维链，不把后续消息或 latest index 合并进已冻结 Run。

## 错误与限制

API 离线、归属错误、消息为空、活动 Run 冲突、预算或执行容量拒绝、模型不可用/失败、结构化输出失败、取消和 interrupted 状态显示为明确错误或状态；容量拒绝不创建本地 Run，提示可稍后重试。real Run 仍需服务端显式能力开关和持久化配置，失败不回退 fixture；fixture 和 real 都不执行仓库源码或 shell。影响请求失败、无唯一映射、超出深度/节点/路径边界和 unresolved 均显示明确状态，不猜测同名符号。前端读取 Run history/events/tool-results 受服务端页大小限制，报告和证据必须绑定同一 index；请求失败不会用旧项目、旧 Session 或 latest index 的数据填补。

Session 草稿按 project/session 保存到 localStorage，消息保存旧响应不能清除新草稿；Run 请求也按项目/Session取消和代数隔离。工作台只显示公开事件、工具结果、同快照证据、报告结论和用量，不显示模型隐藏思维链。

## 验证

本轮前端测试 `19 passed`，`pnpm web:typecheck` 和 `pnpm web:build` 通过；`lib/api.test.ts` 覆盖 RunImpact 响应边界，`lib/run-event-stream.test.ts` 覆盖实际订阅处理器的四类终态控制帧、错误终态、去重/缺号、连续 open-error 上限、sequence 续接和旧回调清理。后端完整测试 `121 passed`，固定报告影响读取与 MCP 对照由 `apps/api/tests/test_runs.py`、`test_storage.py`、`test_issue_runs.py`、`test_mcp.py` 覆盖，SSE 回放/续接和容量由 `test_run_sse.py` 覆盖；契约 `21 passed`、`docs:check` 和 `docs:check:local` 通过。浏览器使用本地 fixture 已复核报告候选/finding 入口、callers/callees、方向/深度、源码/图联动、失败 Run 的无唯一映射提示、Run/Session 切换和刷新恢复；真实浏览器离线网络切换与真实供应商语义质量为 UNVERIFIED。

## 相关决策

- [Run 执行决策](../decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../decisions/2026-09-12-run-sse.md)
- [Run 影响查询决策](../decisions/2026-09-13-run-impact.md)
- [项目与会话决策](../decisions/2026-09-11-project-session-workspace.md)
- [工作区 UI 规范](../design/workspace-ui-spec.md)
