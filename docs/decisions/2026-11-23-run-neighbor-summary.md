# 决策：外部 Run 邻居摘要与 unresolved 按需分页

Status: implemented

## 问题

Run 邻居结果保留的图拓扑和源码位置对调查有用，但完整 unresolved 列表可能达到旧 40 条上限，并占用 Run 的输出字符预算。普通 `list_unresolved` 不绑定 Run、原始查询节点集或 Run 账本，不能安全地作为历史 Run 详情入口。

## 决定

仅对 external Run `run_get_neighbors` 增加显式 `detail=summary|full`。缺参继续选择 legacy `full`。摘要使用新的结果 discriminator，保留 root、所有返回 nodes/edges、条件调用、计数、截断和三组索引覆盖字段；把 unresolved 详情设为 null，并单独记录 `empty|omitted|unknown`、原返回节点范围内的索引计数、旧 full 收据实际返回数和全部节点 ID。摘要中的数量不代表全局索引覆盖。

新增 `run_list_unresolved`，从指定 Run 内成功邻居收据派生固定节点 ID 集（含 root），使用绑定 project/session/run/index/anchor/node scope 的 cursor，以 `path,line,id` 顺序分页。每页作为普通 Run ToolResult 计入既有调用、事件与输出预算；页与事件原子保存。预算拒绝不保存页或工具调用事件；相同 request ID 和参数重放既有页，不同参数冲突。报告只收实际返回的详情，不从省略状态推断 explanation coverage。

## 替代方案

复用通用 `list_unresolved` 无法覆盖跨多个返回节点的集合，也没有 Run request、快照锚定、持久化或预算记账。仅复用通用 neighbors 摘要会改变图消费形状并移除 Run 报告所需的节点与边。两者都不满足固定 Run 历史和证据边界。

## 验收与证据

实现证据：`apps/api/tests/test_mcp.py` 的四个定向回归通过，包含冷 stdio MCP 与 HTTP 历史回读。pt123u 验收 helper 在原 pt119 数据副本上确认固定旧 index `ffbb8bce`（最新仍为不同的 `d8283106`）、54 条记录完整分页，以及实际 Run JSON 从 23,719 降至 8,746 字符。`pnpm contracts:check` 为 37 passed，`pnpm docs:check`、API import-linter、前端 typecheck 和两个新增前端定向测试通过。命令和实际结果见外部验收记录 `/workspace/ct-g5-acceptance/pt123u/runs/20261123T040335Z/summary.json`。这些定向证据不代表 G5 整体验收通过。

## 后果

既有 Run receipts 和索引无需迁移；通用 HTTP/MCP 邻居查询及工作台实时图保持原有 full 行为。调用者必须显式选择摘要才能节省输出预算。索引关系/unresolved 覆盖仍独立于当前结果节点范围的计数。
