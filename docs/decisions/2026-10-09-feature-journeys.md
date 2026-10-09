# 决策：从固定索引事实组装功能旅程

Status: implemented

## 问题

工作台能分别展示静态关系、HTTP method/path 形状关联和保存的功能语义结果，但用户入口、HTTP 调用、服务处理与结果展示没有按职责组织成一条可读旅程。静态关系也不能证明浏览器执行、请求实际发送或响应数据流。

## 决定

从同一固定源码快照提取有限的 JSX `onSubmit`/`onClick` 动作锚点；只有 AST 唯一证明的局部 handler 才标为静态绑定。内联/跨组件 prop callback 保留同快照候选证据并标为未确认。用同快照 symbols/calls、唯一 HTTP method/path association 与 FastAPI handler 组装不超过 8 个职责步骤。语义动作和结果展示使用独立关系类型并标明未确认；无法证明的连接显示断点与来源，不添加静态 CodeEdge。FeatureGeneration 可附加语义说明，但不是旅程起点或前置条件。HTTP 读取适配器调用共享 WorkspaceService。Coverage 分别报告 JSX 扫描、固定关系索引与 HTTP association 状态；不会把 catalog 所用的小型概览路径预算误报为全局关系覆盖。

## 替代方案

继续分别打开 HTTP 链路和功能图会保留实现细节优先的阅读路径。把所有 JSX、属性和值流完整建模会超出首条用户旅程所需范围，并增加难以验证的静态解释。

## 验收与证据

独立源码副本中，真实本地导入 action 的 HTTP 与 stdio MCP catalog/detail 完全一致，步骤和 9 个 source evidence references 均绑定同一 project/index。根代理通过 7 步用户旅程、覆盖说明折叠及从 `apps/web/app/workbench-ui.tsx` L923–940 返回同一固定源码快照的 UI 验收。定向 API/parser/MCP 测试 24 passed，contracts 29 passed，Web 79 passed，typecheck、共享/本地 docs 检查及 diff check 通过。默认 Turbopack build 受 safe copy 的外部 `node_modules` symlink 边界阻断；确认 CLI 支持后，`next build --webpack` 在同一副本完成 production build。

## 后果

旅程 detail 使用所选 immutable index 的源 evidence。`typescript-facts-v5` 扩展通过固定规则识别具有 timeout-rest 的透明 request wrapper；v4 及更旧 index 不会原地回写新 HTTP facts，coverage 会说明可能缺少新识别的关联，显式刷新后才纳入。TSX action 锚点在 catalog 读取时从同快照有界解析，不持久化为新事实。首版不覆盖完整 UI callback/prop 解析或 async result/dataflow；跨组件绑定和响应界面更新仍可保持 unconfirmed，语义覆盖不是运行时 trace。
