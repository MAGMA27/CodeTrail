# 决策：共享本地运行环境与 Codex 联合演示

Status: implemented
- Date: 2026-09-14
- Scope: 本地 API、工作台、stdio MCP、结构总览与 external Run 的可复现启动

## 问题

当前代码已经实现 `python-entry-v4` 结构总览，但 API、工作台和 Codex MCP 若从不同终端启动，可能分别使用默认 `.codetrail`、旧服务目录或旧 MCP 进程。这样会把项目不可见、索引不一致和旧工具契约混在一起，无法判断结构字段是否真实联动。运行目录还包含已有 Project、Index、Session、Run 和 evidence，不能通过清空或迁移数据来修复展示问题。

## 决定

1. 本地联合演示统一使用仓库根目录下被 Git 忽略的 `.codetrail-runtime` 作为服务管理目录。原 `D:/Dev/CodeTrail-runtime` 中的 managed source 和项目数据已于 2026-09-17 完整迁入新位置；API 仍设置 `CODETRAIL_REPOSITORIES={}`，不清空历史数据。
2. 新增 `scripts/start-local.ps1`，用 `-Service api|web` 在两个独立终端启动服务。API 侧固定共享目录、工作台来源和 external Run 开关；不引入进程管理器、后台队列或手工 ID。
3. Codex 的 `codetrail` stdio 配置继续使用当前仓库 `apps/api` 和同一共享目录。MCP 进程缓存代码和工具契约，源码更新后只需要一次宿主 reload；宿主 reload 是外部操作，不由仓库脚本杀掉其他 MCP 实例。
4. 结构总览通过项目/索引列表发现 `CodeTrail 自用验收` 的最新 `relations-v2` ready index；external Run 验收读取已有完成报告，报告仍绑定自己的固定旧 index，不能与最新结构 index 混用。

## 替代方案

- 继续让每个终端手工设置环境变量：容易因当前目录或旧变量残留指向不同数据目录，无法稳定复现。
- 清空默认 `.codetrail` 或迁移旧快照：会破坏用户已有数据，且不能证明新代码与同一快照正确联动。
- 杀掉所有 Python/MCP 进程强制刷新：可能中断其他任务，宿主拥有 MCP 生命周期，仓库不越权管理。

## 验收与证据

- 已验证 API 以共享目录启动后，`/api/projects` 发现 `CodeTrail 自用验收`，其最新关系索引返回 `python-entry-v4`、36 个结构组和 68 条模块关系；抽查组包含 `/api/health`、`/api/projects`、`/api/indexes`，模块关系包含 `imports`/`calls`、`edge_count` 和源码位置。
- 已验证现有 external Run 报告可从同一共享目录读取，状态为 succeeded，报告、工具结果和 evidence 绑定其自身固定 index；本轮没有创建新 Run。
- API 重启后浏览器已使用同一项目/index 核对 `API ONLINE`、`python-entry-v4`、36 个结构组、`/api/health` 入口 evidence、2 节点/1 条边处理函数关系图和返回结构总览；切换到已有 external Run 会话后也恢复了 11 条公开事件、4 项工具结果和固定报告证据。
- 2026-09-14 reload 前曾出现旧 MCP 进程返回 `python-entry-v1` 的历史状态；重载后实际 Codex MCP 已返回 `python-entry-v4`，并与 HTTP 在同一 project/index、同一预算下对照通过，groups/module_relations、计数、成员、关系类型、edge_count、源码位置和 coverage 字段一致。本记录不把旧响应当作当前状态。

## 后果

共享目录和脚本减少了启动漂移，运行目录现在位于仓库根目录下并由 Git 忽略，不纳入 Git。脚本只保证 API/工作台启动参数一致，不能替代 Codex 宿主 reload。`memory-bank/` 继续 Git 忽略，`next.md` 由用户控制，不由本决策或实现自动更新。
