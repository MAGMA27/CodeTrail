# 决策：Run 报告候选与有界静态影响

Status: implemented

## 问题

Run 报告已经能够给出候选链接、finding、源码 evidence 和同快照图，但工作台还不能从一个报告候选定位到可解释的上下游范围。该能力必须继续区分模型候选、静态关系、未知边界和确认影响，并且不能因项目后来建立新索引而改变旧 Run 的证据语义。

## 决定

- 影响读取要求恰好一个已保存报告中的 `candidate_id` 或 `finding_id`，由 `WorkspaceService` 统一执行 project/session/run/report 归属与映射；HTTP 和 MCP 只适配同一应用服务。
- 结果绑定 Run 的不可变 `index_id`，不写回 Run，不启动新的 Run，不调用模型，不执行来源仓库代码；没有唯一 source symbol 时返回显式 `unmapped` 空结果，不按同名符号猜测。
- 存储层只遍历同快照静态 `calls` 边，支持 callers/callees，默认和最大深度为 2，节点最多 40、边最多 80、路径最多 40、unresolved 最多 40，并受时间和输出字节限制。
- 遍历以简单路径保持有限；重复节点和多路径去重，根不计入 `reachable_node_count`，循环闭合边仍作为静态边返回，dynamic/unresolved 只作为未知边界展示，不绘制为确定影响。
- 工作台从报告候选/finding 进入影响面板，允许切换方向和深度；影响节点点击使用其自身的 snapshot symbol 进入源码证据，并在 Run/Session/Project 切换、刷新和过期请求时取消或忽略旧响应。

## 替代方案

- 直接复用通用 `neighbors` 响应：未提供从根出发的去重路径、影响专用计数和报告 ID 作用域，无法完整表达本步验收的解释路径。
- 把模型的候选直接当作影响范围：会混淆模型假设与静态事实，也会让未知调用看起来像已确认影响，因此不采用。
- 持久化每次影响查询：会制造与固定索引重复的派生状态和失效清理问题；影响查询成本与输出已经有界，故按需派生。

## 验收与证据

本轮实际验收：

- `apps/api/tests/test_storage.py` 覆盖 A→B→C、环、多路径、边/节点去重、动态 unresolved、同名符号和预算截断。
- `apps/api/tests/test_runs.py` 覆盖报告 candidate/finding 映射、错误/取消空结果、参数与归属拒绝、重建新 index 后旧 Run 仍读取旧 index。
- `apps/api/tests/test_mcp.py` 覆盖默认只读 `get_run_impact` 与 HTTP 结果一致；`apps/web/lib/api.test.ts` 覆盖同快照路径和 unmapped 运行时校验。
- `uv run --directory apps/api pytest -q` 为 `121 passed`，`uv run --directory apps/api python -m compileall -q app` 通过；前端测试为 `19 passed`，`pnpm web:typecheck`、`pnpm web:build`、`pnpm contracts:check`（`21 passed`）、`pnpm docs:check` 和 `pnpm docs:check:local` 通过。
- 本地 HTTP/MCP 影响结果对照通过；浏览器 fixture 已复核 candidate/finding 入口、callers/callees、1–2 层深度、影响节点到同一固定 Run/index 源码证据、图联动、失败 Run 无唯一映射提示、Run/Session 切换和刷新恢复。另捕获了候选影响面板截图用于视觉复核。
- 真实供应商语义质量、真实浏览器人为断网重连和 OS 级 SSE 离线行为仍为 UNVERIFIED。

## 后果

影响范围是“有界静态可达性”而不是 Bug 影响证明；imports、动态调用和无法解析的引用不会进入确定路径。大图、模型语义质量、真实供应商结果和 OS 级 SSE 离线行为仍需独立评测；下一步计划由 `memory-bank/next.md` 维护并不在本决策中改写。
