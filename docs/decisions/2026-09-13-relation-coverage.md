# 关系扫描与 unresolved 覆盖语义

Status: implemented
- Date: 2026-09-13
- Scope: Python 关系提取、索引存储、查询契约、Run 报告与工作台提示

## 问题

关系提取同时有静态边、unresolved 详情、时间和数量预算。旧实现把
`max_unresolved` 当成整个关系扫描的停止条件：前部文件耗尽 unresolved 输出容量后，后部文件不会继续扫描，因此后部可确认的静态调用边可能永久缺失。仅把索引标成 `truncated` 还无法区分“扫描没有完成”和“扫描完成但 unresolved 详情没有全部保存”。

本次问题必须在不提高默认资源预算、不改写旧索引的前提下修复，并让 HTTP、MCP、报告、影响查询和工作台使用同一解释。

## 决定

将两个事实拆开记录：

1. `truncated` / `truncation_reason` 只表示关系扫描因时间或静态边预算停止，因而静态关系覆盖不完整。
2. 新增 `unresolved_truncated` / `unresolved_truncation_reason` 表示 unresolved 详情达到输出上限。达到该上限后停止追加 unresolved，但继续执行有界的 AST 扫描和确定静态边提取；时间、静态边和查询输出预算仍然会停止工作。
3. 新建索引使用关系规则版本参与 index identity。源码 hash 相同但关系结果因规则变化时，创建新 index，不覆盖或复用旧 index；旧 Session、Run 和证据保持不变。
4. 新列在旧 SQLite 行上保持 `NULL`，读取解释为 unresolved 覆盖状态未知，而不是推断为完整或截断。新提取结果写入明确的 `true` 或 `false`。
5. Index、neighbors、impact、外部 Run 报告和工作台分别展示扫描覆盖与 unresolved 详情覆盖；空邻接不升级为“确定没有关系”。

## 替代方案

- 提高 `max_unresolved`：只能延后同一问题，不能保证资源有界，也掩盖了停止条件耦合。
- 保持全局停止：会继续丢失后部确定边，与代码理解目标冲突。
- 达到上限后丢弃所有 unresolved：会隐藏已发现的不确定关系，破坏证据边界。

## 验收与证据

实现与回归测试已完成。`uv run --directory apps/api pytest -q` 为 127 passed，`pnpm doc-sync`（含 `docs:check`、`contracts:check`）通过；此前前端测试 21 passed、`pnpm web:typecheck` 和 `pnpm web:build` 通过。

- 回归覆盖前部耗尽 unresolved 详情容量、后部仍提取确定调用；时间/静态边预算、动态调用不伪造边、稳定顺序和旧数据读取也有测试。
- 旧索引 `index-import-c929f2f2b7a3e5b435ddaee3859158fe-d8eec005e29b59c8` 保持 `truncated=true/max_unresolved reached`，新增字段为 `NULL`；5000 条 unresolved、1603 个 symbol、1997 条 static edge 未改写。新索引 `index-import-c929f2f2b7a3e5b435ddaee3859158fe-relations-v2-ea4f713fe5959262` 使用 `relations-v2` 身份，`truncated=false`、`unresolved_truncated=true/max_unresolved reached`，包含 5000 条 unresolved、1643 个 symbol、2946 条 static edge。
- 新索引 HTTP 邻接确认 `run_model_evaluation`（`evals/model_pipeline.py:1908-1913`）到 `_settle_case_usage` 的 `calls/ast` 边；同一位置的 `read_source` 返回匹配源码、hash 和 evidence。旧索引同一目标仍为零邻接，旧 Session/Run 保持不变，新 Session `session-80e2c492db07483b86f381ace15a0480` 绑定新索引。
- 新启动的本地 stdio MCP `app.mcp_server` 返回与 HTTP 一致的 unresolved 覆盖字段、symbol、edge、行号和 evidence。工作台实际显示新 snapshot `ea4f713fe595`、新边和 `max_unresolved reached` 警告；现场截图已在本次 CUA 验收输出展示。简化 CUA 接口仅返回截图字节，不提供可写入本地文件的路径。
- 旧索引、旧 Session/Run/evidence 未迁移或覆盖；同源码 hash 的解析规则版本隔离由 `relations-v2` identity 回归测试覆盖。当前 Codex 宿主连接在定向重载后关闭，未把旧宿主结果冒充新契约验收；直接 stdio 协议验证通过。

## 后果

新索引在 unresolved 详情截断时仍可能包含完整的静态扫描结果，但 unresolved 列表不是全集。时间或静态边预算触发时，静态扫描覆盖仍是不完整。旧索引不会被重写，其 unresolved 覆盖状态在新契约中可能是未知；调用方必须保守解释。
