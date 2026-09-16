# 决策：本项目索引更新与缓存效果联合验收

Status: implemented
- Date: 2026-09-16
- Scope: CodeTrail 自身 Python 源码的文件级定义/关系事实缓存、索引新鲜度、显式更新、项目/Session/Run 快照边界，以及 HTTP/MCP/工作台闭环

## 问题

文件级缓存已经实现，但仅凭单元测试还不能确认它在 CodeTrail 自身规模上真的复用、变化失效和跨快照重绑定正确。本轮需要把冷构建、独立进程热构建、单文件变化、目标重命名/删除/恢复、无变化刷新、禁用缓存语义、旧 Session/Run/evidence 和工作台实际操作放在同一组可复核证据中，同时不污染用户源码或共享运行目录。

## 决定

1. 使用 CodeTrail 工作树在 `D:\Dev\CodeTrail-validation-20260916-v2` 等目录的只读实验副本，使用独立服务数据目录；每个冷样本使用独立源码副本，避免静态本地仓库的 `.codetrail/index.sqlite3` 互相复用。性能数据来自 CodeTrail 自身源码，不把 fixture 的结果当成本项目结果。
2. 以当前提交 `85149eb295ab89c78d162b1e8d886bfae811c26e` 为实现身份；实验未使用 CodeGraph（仓库没有 `.codegraph/`）。基线输入为 122 个纳入范围的 Python 文件、1,493,069 bytes，`snapshot_hash=013cdc3cfccdaab7c1de5926133380e1e676cb36709754ad8fcba698d3bb9f94`，`rules_version=python-source-v1+python-3.13+relations-v2`，`configuration_hash=635c00f44957b75595366b9941ab1223d65506e978892b9500d740cf20486ef4`，缓存所用 Python 为 3.13.12。
3. 复用判断以内部构建统计、不可变 index/hash、完整定义/边/unresolved/位置/evidence 和新鲜度响应为准；耗时只做本机少量重复的诊断，不从单次测量推导稳定性能结论。现有统计没有独立的目标解析耗时计数，因此该成本只通过输出与总耗时间接观察并记录为后续观测缺口。
4. HTTP、local stdio MCP 和工作台都连接同一个隔离项目数据；验收完成后停止临时 8016/3016 服务。用户源码、共享 `.codetrail`、共享配置、历史 Run 和 Git 暂存边界均不进入实验写入范围。

## 替代方案

- 只跑 fixture 或单元测试：不能证明 CodeTrail 自身规模下的跨进程复用、完整快照目标重绑定和工作台行为，因此只作为补充，不作为本轮结论。
- 直接对用户工作树或共享 runtime 做压力测试：会污染用户源码、latest、缓存或历史证据，违反项目的隔离边界，因此改用独立源码副本和数据目录。
- 本步直接实现完整增量图、文件监听或 GitHub 增量同步：当前缺少目标解析和持久化阶段的独立成本数据，先记录观测缺口，避免在没有瓶颈证据时扩大实现范围。

## 验收与证据

### 冷、热、变化和缓存容量

使用 `IndexService.create_index(..., on_build_stats=...)`，每次创建新的 `RepositoryRegistry`/`IndexService`；冷样本使用不同源码副本和数据目录，热样本使用两个独立 `uv run` Python 进程、同一缓存 scope `project-624b70dc65f64abb8f23fd75baa506e6`。

| 场景 | 次数/耗时 | 定义缓存与 AST | 关系事实缓存与 AST | 写入 |
| --- | --- | --- | --- | --- |
| 冷构建 | 3 次，4,363.80–4,472.06 ms | 0 hit / 122 miss / 122 `ast.parse` | 0 hit / 122 miss / 0 新 AST / 122 次复用定义 AST | 定义 122、关系 122 |
| 独立进程热构建 | 2 次，1,785.50–1,858.36 ms | 122 hit / 0 miss / 0 `ast.parse` | 122 hit / 0 miss / 0 新 AST / 0 复用 | 0、0 |
| 单文件变化 | 1 次，3,130.46 ms | 121 hit / 1 miss / 1 `ast.parse` | 121 hit / 1 miss / 0 新 AST / 1 次复用 | 定义 1、关系 1 |
| 无变化显式刷新 | 1 次 | 不进入提取阶段 | 不进入提取阶段 | `mode=reused` |

冷样本分别为 `cold8`、`cold9`、`cold10`；每个样本仍得到同一基线 index/hash。热样本的独立进程结果确认这是跨进程缓存复用，不把同一进程内 service 重开误写成进程重启。单文件变化只修改隔离副本的 `apps/api/app/__init__.py`，完整 snapshot 读取和当前快照目标解析仍保留，所以总耗时没有按 AST 解析减少比例下降。

实验结束时同一 scope 的 SQLite 派生缓存为：定义 125 条/254,710 bytes，关系事实 125 条/3,206,434 bytes，合计 250 条/3,461,144 bytes；容量仍由两类缓存共享，历史 index/evidence 不在清理对象中。

### 禁用缓存语义对照

在基线源码副本上用空缓存直接执行 `build_source_snapshot`、`extract_definitions(cached={})` 和 `extract_relations(cached={})`，与已持久化基线 index 对照：

- `same_index_id=true`、`same_snapshot_hash=true`、`same_source_files=true`；
- `same_definitions=true`、`same_edges=true`、`same_unresolved=true`、`same_coverage=true`；
- 两边均为 1,850 个定义、3,342 条边、5,000 条 unresolved 详情，覆盖为 `truncated=false`、`unresolved_truncated=true`、原因 `max_unresolved reached`；
- 无缓存直接执行定义阶段 122 次 AST 解析、122 次关系事实 miss，说明热路径的收益来自派生事实复用，而不是改变输出边界。

### 目标重绑定与旧快照

在同一隔离数据库中保留原始目标、重命名目标、删除目标和恢复后的 index。基线目标 `apps.api.app.indexing.current_source_rules_version` 的旧 Symbol ID 为 `sym-98d924f9d6ed3f5751f2e531f5f34393`。

| 变体 | 结果 | 目标调用关系 / unresolved |
| --- | --- | --- |
| 基线 `013cdc3cfccdaab7` | 目标存在 | `imports` 与 `calls` 各 1 条，共 2 条；unresolved 0 |
| 目标重命名 `4faf5c82d97a9ad0` | 新 Symbol 为 `current_source_rules_version_renamed`，旧 Symbol ID 不存在 | 目标边 0；调用方保留 2 条 unresolved，分别为 `missing_relative_import`、`call_target_not_in_snapshot` |
| 目标删除 `b03f60c188692203` | 目标 Symbol 不存在，定义从 1,850 减为 1,849，边从 3,342 减为 3,340 | 目标边 0；同样保留 2 条 unresolved |
| 目标恢复 | index 回到 `013cdc3cfccdaab7`，恢复构建统计为 122 hit / 0 miss / 0 AST parse | 旧跨快照 Symbol ID 未被复用，重新用当前 DefinitionMaps 得到关系 |

另用独立的项目副本走公开 `WorkspaceService.refresh_project_index`：原始 index 为 `index-codetrail-relations-v3-013cdc3cfccdaab7`，单文件变化得到 `index-codetrail-relations-v3-2c5dc8beeca5e630`，`modified_count=1`、`added_count=0`、`deleted_count=0`、`scan_complete=true`；恢复后原 index 仍可用。此前已保存的 `session-e6fe68ca9b884ab88a9a0008160bc21c`、fixture Run `run-0030d9e1af9e4b97853115affa82fd84`、Report 和 evidence 均持续绑定原 index，未随 project latest 改写。原 index 无变化刷新返回 `consistent` + `mode=reused`。

用于创建旧 evidence 的 fixture Run 只验证 Session/Run/evidence 生命周期，不参与上述本项目性能结论。

### HTTP、stdio MCP 与工作台

HTTP 使用隔离环境变量启动 `uv run uvicorn app.main:app --host 127.0.0.1 --port 8016`，设置 `CODETRAIL_REPOSITORIES` 指向 `D:\Dev\CodeTrail-validation-20260916-project`、`CODETRAIL_DATA_DIR` 指向 `D:\Dev\CodeTrail-validation-data-20260916-project`。实际 loopback 请求结果：

- `GET /api/health` 为 HTTP 200，返回 `{"status":"ok","service":"codetrail-api","version":"0.1.0"}`；
- `GET /api/projects`、`GET /api/projects/{project_id}/indexes` 和 `GET /api/projects/{project_id}/indexes/{index_id}` 均为 200，列出同一项目的 2 个保存快照；
- `POST /api/projects/{project_id}/indexes/{index_id}/freshness` 对变化快照返回 200，报告 1 个 modified path `apps/api/app/__init__.py`。

local stdio MCP 使用 `uv run python -` 启动 `mcp.client.Client`，子进程执行 `python -m app.mcp_server`，同样传入隔离源码和数据目录。`list_tools` 返回 21 个默认只读工具；`list_projects`、`get_project`、`list_indexes` 和 `check_index_freshness` 均为 `ok=true`，项目、快照 ID 和 freshness 结果与 HTTP 相同。

工作台使用独立 `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8016 pnpm dev --hostname 127.0.0.1 --port 3016`，在真实浏览器中确认：

- 页面显示 `API ONLINE`、项目 `Project cache acceptance`、Session 固定版本和 3 个可用快照；打开历史 Session 后仍显示 `013cdc3cfc` 和既有 1 条消息/1 个 fixture Run；
- 索引管理先显示“源码一致”，扫描 122 文件/1,493,069 bytes；隔离副本变更后显示“发现变化”、1 修改、0 新增、0 删除及 hash 变化原因；
- 点击“更新索引”显示“本次显式更新：已生成新的不可变快照；旧快照仍保留”；把当前 latest 与源码保持一致后再次检查为“源码一致”，再次更新显示“复用原快照，未重新解析”；
- 打开原历史快照时显示“仅 latest 可更新”，Session 仍回到其固定原 index，未被 latest 替换。

此前一次恢复尝试使用工作台默认 `fixture-v1` revision，而历史原快照使用另一 revision；服务端按 immutable payload 规则返回 `index content conflicts with an existing index`。这不是缓存错误，但暴露了显式更新无法复用历史内容的生命周期缺口；该现象触发本次 next 的 `NEEDS_FIX`。本次修复增加 `historical_reused`：完整安全扫描确认同项目的 schema、规则、配置和完整 manifest 兼容时，直接复用历史 immutable index、推进 latest，保留原 revision/dirty metadata，不执行 AST/关系提取，也不原地改写旧索引。工作台“打开固定版本”不再作为该场景的验收替代。

### 版本恢复修复复核（2026-09-16）

- 隔离项目按 A → B → A 变更源码；A 使用 `freshness-v1`/`dirty=false`，B 使用不同 revision/dirty。恢复 A 后以 B 为基准执行显式更新，返回 `mode=historical_reused`，latest 切回原 A index，A 的原 revision/dirty 保持不变，索引总数不增加；重复刷新 A 返回 `mode=reused`。
- 历史复用只在完整且安全 manifest 与 repository、rules version、configuration hash、schema version 均兼容时发生；配置变化、跨项目归属或不完整扫描不复用历史，仍按既有创建/拒绝语义处理。候选历史 index 通过项目关联查询和完整 manifest 签名再次核对，不能只凭 snapshot hash。
- `test_freshness.py` 的历史恢复测试通过 monkeypatch 证明复用路径不进入 definition/relation 提取；`test_mcp.py` 通过真实 stdio MCP 子进程对照 HTTP 的历史恢复结果；前端契约测试覆盖 `historical_reused` 及伪造模式拒绝。补充实际 loopback HTTP（8017）结果：隔离项目 `project-0653c5836fc94b78a74a69781beda246` 从 A（`index-history-fix-relations-v3-53c3c2488d7a20a1`）到 B（`index-history-fix-relations-v3-251bd0f8c9bfea74`）再恢复 A，返回 `created` → `historical_reused` → `reused`，latest 为 A、可用快照数保持 2，A 的 `history-a-20260916`/`dirty=false` 保持不变。实际 stdio MCP 使用同一隔离数据目录，`check_index_freshness` 与 HTTP 的 status/changed_paths/reasons 一致，写工具注册且历史复用/当前复用均成功。实际 3017 工作台在隔离项目中完成源码 B=97 的“检查变化 → 更新生成新快照”、恢复 A 的“检查变化 → 更新并显示 `复用兼容历史快照，未重新解析；项目 latest 已切换到该快照，历史元数据保持不变。”，再次更新显示“复用当前快照，未重新解析。”；页面可见结果与服务端模式一致。

### 检查命令与 Git 边界

本轮实际使用了 `uv run python -` 的缓存统计/语义对照/目标变更驱动、独立 `uv run` 进程热构建、`uv run uvicorn` + `Invoke-WebRequest` loopback HTTP（8017）、`python -m app.mcp_server` stdio 客户端和 3016/3017 工作台浏览器操作。临时服务已停止，用户工作树未被实验副本修改。验收记录本身写入 Git；`memory-bank/` 仍由仓库规则忽略，`next.md` 未修改。

## 后果

本项目实测通过了冷构建、跨进程热复用、单文件失效、无变化复用、禁用缓存等价、目标删除/重命名/恢复重绑定、旧 Session/Run/evidence 固定和 HTTP/MCP/工作台闭环。缓存明确减少了定义/关系事实提取的 AST 工作；单文件更新仍需读取完整快照并重解析当前快照目标，因此不能宣称完整增量索引或线性加速。

本轮基于成本证据只保留一个后续建议（不修改用户控制的 `next.md`）：为“当前快照目标重解析”和 index 持久化增加有界的阶段计数/耗时观测，再决定是否值得设计受影响子图增量更新。当前不引入自动监听、完整增量图、GitHub 增量或新语言；真实供应商语义质量仍未验证。

## 相关决策

- [文件级定义提取缓存](2026-09-15-definition-extraction-cache.md)
- [文件关系事实缓存与全快照目标解析](2026-09-15-relation-facts-cache.md)
- [本地项目索引新鲜度与显式更新](2026-09-15-index-freshness.md)
