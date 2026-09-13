# 决策：Codex 外部 Agent 绑定 Run 调查闭环

Status: implemented

## 问题

CodeTrail 已有本地 stdio MCP、工作区 Session/Run 持久化、只读代码工具和工作台 SSE，但普通 MCP 查询不会进入 Run，也不会在工作台展示为一次可恢复的调查。下一步需要让 Codex 通过本地 MCP 自主完成一次调查，同时保持项目归属、快照、预算、证据和终态规则只有一份实现。

## 决定

- 新增 `RunMode = external`，并以 `execution_source = codex` 明确它由外部 Agent 驱动；fixture/real 继续使用内部执行来源。
- 外部 Run 仍由 `WorkspaceService` 创建并写入现有 runs、run_inputs、run_events、run_tool_results、run_reports 表，但不占用或提交内部 `RunExecutor`。
- 通过独立开关 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 开放以下固定 MCP 工具：
  - `start_external_run`
  - `run_search_code`
  - `run_read_source`
  - `run_get_neighbors`
  - `submit_external_run`
  - `cancel_external_run`
- 外部工具只接受明确的项目、Session、Run、请求幂等键和结构化参数。服务层验证归属、活动状态、固定索引快照、预算与终态；MCP 适配器不复制业务规则。
- 工具调用复用现有 `IndexService.execute_tool`。服务端记录开始事件、实际工具结果和返回证据；客户端不能伪造工具结果或证据。
- 结构化结论只允许 `candidate_found` 或 `insufficient_evidence`。服务端仅接受同一 Run、同一索引快照且实际返回过的证据和符号引用，并复用现有 Report/Impact 契约。
- start/tool/complete/cancel 均按请求幂等处理：同一幂等键和相同输入返回既有结果，终态 Run 拒绝新的活动；外部连接恢复不重复追加事件。外部 Run 跨进程恢复时只将超过 Run 预算时间的活动 Run 标为 interrupted，不影响另一个进程中新建或仍在预算内的 Run。
- 工作台通过既有 Session Run 列表发现外部 Run，自动选择并订阅同一 Run 的 SSE；刷新、切换 Session 或重复发现不得重复订阅或混入迟到事件。展示公开工具活动、证据、结论和限制，不展示隐藏思维链。

## 输入、输出与归属

- 输入：项目、Session、Issue、固定 `index_id`、消息截止序号、Run 预算，以及每次工具请求或结构化最终结论。
- 输出：既有 Run 状态、顺序事件、工具结果、证据引用和 Report；HTTP 工作台与 MCP 读取同一持久化记录。
- 数据所有者：`WorkspaceService` 负责生命周期和规则，`IndexService` 负责代码工具执行，`IndexStore` 负责事务与幂等，MCP 仅负责协议映射，工作台仅负责发现和展示。
- 故障：归属/快照/证据错误、预算超限、解析失败、终态写入和取消都返回明确 MCP 错误；工具自身失败作为已记录的工具失败结果返回，不伪造成功证据。连接断开由服务启动恢复逻辑按预算时间界定，不会无条件中断其他来源的 Run。

## 替代方案

- 让 Codex 直接调用现有无 Run 工具：无法形成可恢复的调查记录，也无法安全提交与工作台一致的结论。
- 让 MCP 适配器自行写 SQLite 或维护另一套证据规则：会造成 HTTP/MCP 行为漂移，违反统一业务服务边界。
- 继续把外部调用塞进内部 worker：无法表达外部连接生命周期，且会错误占用内部并发槽位。

## 验收与证据

实现后需分别记录：

1. 实际 Codex 主机发现并调用本地 stdio MCP，且不依赖内置真实供应商密钥。
2. 一次外部 start → 真实 search/read/neighbors → 结构化结论形成单一 Run，工作台无需刷新显示事件、证据和 Report。
3. 后端契约/存储回归覆盖归属、快照、证据、幂等、预算、取消、连接失效和终态拒写；HTTP/MCP/SSE 与前端类型检查、构建和文档检查通过。
4. 明确区分协议/自动化验证、实际 Codex 连接、外部调查闭环和结论质量；真实供应商语义质量不因本次闭环而宣称已验证。

本次验收证据（2026-09-13）：后端全量测试 `123 passed`，前端测试 `19 passed`，`pnpm web:typecheck`、`pnpm web:build`、`pnpm contracts:check`（21 passed）、`pnpm docs:check`、`pnpm docs:check:local` 和 `pnpm doc-sync` 通过；按 [Codex 本地配置](../../apps/api/README.md) 的同一 stdio 命令启动 MCP，当前 Codex 主机实际发现 26 项 CodeTrail 工具，并在共享 `D:/Dev/CodeTrail-runtime` 中读取目标项目、固定 index 和 Session。实际 external 闭环创建 `run-1648e725f3424dc590a5298e2baee1a0`，从未带候选位置的现象出发执行 `search_code`、`read_source`、`get_neighbors` 并提交结构化候选，持久化 11 条公开事件、4 项工具结果、证据 hash 和报告；工作台无需刷新自动发现并载入，源码证据和静态影响面板打开正确，历史 Run、Session 切换和刷新恢复均通过且没有重复 Run。当前结论通过的是 external 连接、调查闭环和证据一致性；真实 DeepSeek 语义质量和实际账单差异仍为 UNVERIFIED。

## 后果

该设计不引入远程 MCP、队列、多 Agent、批处理或模型配置 UI。外部 Agent 的费用和隐藏内部步骤不可见，系统只记录服务端实际收到的工具调用与公开结论；如需下载或付费能力，另行设计授权和预算。
