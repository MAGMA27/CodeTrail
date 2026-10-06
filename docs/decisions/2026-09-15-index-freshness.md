# 决策：本地项目索引新鲜度与显式更新

Status: implemented

## 问题

本地项目当前可以持久化复用不可变索引，但工作台和 Agent 无法判断索引对应的源码是否已经变化，也没有一个显式、受预算约束的更新入口。仅比较 mtime/size 或依赖 git 状态不能可靠发现同大小且恢复时间戳的修改；自动监听也不应替代一次明确的核对。

本次影响索引、存储、应用 API、本地 MCP 和工作台。本记录定义本地来源的 manifest freshness；GitHub 固定 commit 的远端 ref 检查和显式更新由后续的 [GitHub 远端 ref 新鲜度与显式更新](2026-09-16-github-remote-freshness.md) 补充，未来的自动监听仍不在范围内。

## 决定

1. 在 `IndexSnapshot` 中记录可影响索引结果的规则版本和配置指纹；旧数据库通过 nullable 字段兼容，缺失时新鲜度只能报告 `unknown`，不能假定一致。
2. 应用服务提供共享的新鲜度检查。HTTP 路由和 MCP 只读工具均调用同一业务方法，检查绑定 `project_id` 与基准 `index_id`，读取本地来源但不创建索引、不修改源码。检查对纳入索引范围的 Python 文件读取内容 hash，并同步比较新增、修改、删除、快照问题、规则版本和配置指纹。
3. 检查结果区分 `consistent`、`changed`、`unknown`；契约保留 `unchecked` 供未执行检查的工作台状态使用。结果包含检查时间、基准身份、变化计数、受限路径、路径列表截断与扫描不完整的独立字段、扫描统计和机器可读原因。预算耗尽、读取失败、符号链接/边界异常、来源消失或文件在读取期间变化都不能返回一致。
4. 工作台提供“检查更新”和“更新索引”。更新接口以路径中的基准索引为准，在服务端重新核对：内容和当前规则均一致时返回 `reused`；完整且安全扫描确认源码恢复到同项目中规则、配置、schema 和完整 manifest 均兼容的历史快照时返回 `historical_reused`，推进 `latest` 到该 immutable index，并保留其原 revision/dirty 等来源 metadata；其他源码/规则/配置变化，或旧元数据缺失（可与这些可重建原因同时出现）时，从一次新的源码捕获生成新不可变快照；检查不完整或来源无法安全读取时失败并保留旧 `latest`。旧 metadata 场景仍报告 `unknown`，不冒充 `consistent`。
5. 更新只允许推进调用时仍为项目 `latest_index_id` 的基准，完成写入时再次校验，避免并发请求以较晚完成的旧结果覆盖较新的 latest。旧 Session/Run/source evidence 继续绑定原 index/hash/行号。HTTP 更新沿用 loopback 写约束；MCP 更新工具单独受显式写开关控制，默认不注册。
6. 本地来源的索引构建使用新的身份版本，使旧索引即使与源码内容相同也不会被错误地当作已经写入了当前规则元数据。不会迁移、复制或删除旧存储；GitHub 来源不使用本地 manifest 规则，远端 ref freshness/update 由 [GitHub 远端 ref 新鲜度与显式更新](2026-09-16-github-remote-freshness.md) 定义。
7. 版本职责明确分离：当前关系语义为 `relations-v3`，参与 `rules_version` 和 freshness；当前关系事实缓存规则/格式为 `python-relation-facts-v2` / `relation-facts-v2`，只决定事实缓存是否命中；当前索引构建身份为 `relations-v5`，决定相同源码 hash 下生成的 immutable index ID 命名空间。源码捕获规则单独版本化；扫描范围或边界变化必须同时使 freshness 不兼容并生成新 index，缓存格式变化只使对应派生缓存安全 miss，不单独改变图身份。

## 替代方案

- 只比较 mtime/size：开销较低，但无法发现时间戳恢复或内容原地替换，违反可靠性要求。
- 检查时直接重新执行 AST/关系提取：可以得到新索引，但无法证明无变化路径跳过解析，且把只读检查和写入混在一起。
- 自动监听目录：会增加生命周期、进程重启和来源边界复杂度；本步先交付显式、可验证的检查，监听留待后续优化。
- 原地修改当前索引元数据：会破坏不可变快照以及历史证据的可复现性，因此改为新身份版本和新快照。

## 验收与证据

- 覆盖不变、新增、修改、删除、同大小且恢复 mtime 的修改、排除文件、来源边界/符号链接、读取失败和文件/响应预算；扫描不完整均为 `unknown`，路径列表截断不冒充扫描不完整。
- 不变更新不调用 AST/关系提取且不增加快照；变化更新生成新 index 并推进 latest；失败保留旧 latest；历史绑定保持不变。
- 覆盖旧索引缺元数据、规则/配置变化、检查后源码再次变化、并发/重试和 GitHub 临时来源边界；HTTP 与 MCP 结果一致，MCP 写能力默认关闭并可显式开启。
- 通过受影响 pytest、Python 编译、前端类型检查/构建、契约生成与 `contracts:check`、`docs:check`、`docs:check:local`，并记录浏览器检查→变化摘要→显式更新的实际证据。

## 后果

检查必须读取当前纳入范围的文件，源码规模越大耗时越高，但有文件数、字节、时间和响应上限，结果不会错误声称一致。首次升级后的旧索引需要一次显式重建来获得完整规则元数据。文件级定义描述与关系事实缓存均在不改变快照契约的前提下实施；关系目标重绑定和自动监听仍不在本决策范围，见[文件级定义提取缓存](2026-09-15-definition-extraction-cache.md)与[文件关系事实缓存与全快照目标解析](2026-09-15-relation-facts-cache.md)。

## 实际证据（2026-09-15）

- `apps/api/tests/test_freshness.py` 覆盖同大小且恢复 mtime 的内容修改、排除文件、新增/删除、无变化复用、变化重建、预算未知、旧元数据修复后再次一致/复用，以及旧 metadata 与源码变化组合的安全重建/不安全拒绝；后端全量 `uv run pytest -q` 为 149 passed。
- `apps/api/tests/test_mcp.py` 覆盖 HTTP/MCP 新鲜度结果对照和 MCP index 写能力默认关闭/显式开启；`uv run python -m compileall -q app`、前端 25 项测试、严格类型检查、生产构建、契约 23 项测试、`pnpm doc-sync` 和 `pnpm docs:check:local` 均通过。
- 实际工作台浏览器使用共享运行目录中的旧 latest：源码已发生变化且该旧索引缺少规则/配置元数据。索引管理显示 `无法确定`、1 个新增/16 个修改/0 个删除、扫描 121 个文件/1,404,324 bytes 和机器可读原因，并提示完整安全扫描可全量重建；点击“更新索引”创建 `index-import-c929f2f2b7a3e5b435ddaee3859158fe-relations-v3-rebuild-3378e41e1001cbce`，旧 v2 latest 仍保留。对新快照再次“检查更新”显示 `源码一致`，再次“更新索引”显示“复用原快照，未重新解析”；原 `session-aad58cde386e4b48868fca082ae6b94e` 及其历史 Run/evidence 仍可读取。
- 实际宿主 `check_index_freshness` 与 HTTP freshness 对照一致：旧 v2 均为 `unknown`（`source_changed` + `missing_index_metadata`），新 v3 均为 `consistent`；当前宿主工具目录未注册 `refresh_index`，因此只做宿主读检查，写入路径由工作台闭环验证。
- 本地来源的 freshness 语义在本决策中保持；GitHub 的 metadata-only ref 检查、显式更新和 HTTP/MCP 复用见 [GitHub 远端 ref 新鲜度与显式更新](2026-09-16-github-remote-freshness.md)。真实供应商语义质量未在本步验证。未修改用户控制的 `memory-bank/next.md`，`memory-bank/` 也未纳入 Git。

## 实际证据（2026-09-16 关系语义升级）

- 在隔离的同一源码和服务数据目录中，旧提交 `dee662f` 生成 `relations-v2` index；`sample.caller.resolved` 的 `obj.run()` 只有 `unresolved`，没有方法 calls 边。
- 当前代码读取该旧 index 时 freshness 为 `changed`，原因为 `rules_changed`、`configuration_changed`，当前规则为 `python-source-v1+python-3.13+relations-v3`。显式更新生成不同的 `relations-v4-rebuild` index；旧 index 仍保留，新 index 出现 `sample.models.Widget.run` calls 边；再次 freshness 为 `consistent`，再次更新为 `reused`。
- 同一过程通过 HTTP 和 stdio MCP 对照验证；MCP 工具数为 21。工作台对旧固定快照显示“解析或关系规则版本变化 · 索引配置指纹变化”，对新快照的关系图和源码 evidence 显示 `Widget.run`。
- 本轮新增升级回归测试与现有 freshness/indexing 定向测试通过；真实供应商语义质量、共享运行目录的实际迁移未在本步验证。旧 index、历史 evidence 和用户未提交修改均未删除或改写。

## 实际证据（2026-10-06 源码扫描边界升级）

- 完整快照扫描与 freshness manifest 都使用同一排除目录规则，现跳过 `.runtime` 和 `.codetrail-runtime`，避免采集评测和服务运行生成的 Python 源文件。
- 捕获规则从 `python-source-v1` 升至 `python-source-v2`，索引身份从 `relations-v4` 升至 `relations-v5`；因此 freshness 同时报告 `rules_changed` / `configuration_changed`，而相同内容 hash 也不会复用旧身份快照。
- `test_indexing.py` 验证两种扫描入口一致排除这两个目录；`test_freshness.py` 模拟旧污染快照，验证显式刷新创建干净的新 index，旧快照仍可读并保留在项目可用索引中。

## 后续实现更新（2026-10-06）

当前快照规则已扩展到 `.py`、`.ts`、`.tsx`，并把 TypeScript 5.9.3 与 `typescript-facts-v1` 纳入 freshness 身份；当前关系语义为 `relations-v4`、索引构建身份为 `relations-v6`。本决策中的旧 Python-only 版本号和验收快照保留其历史语境；当前行为与 TS parser/runtime 前提见 [TypeScript 静态索引与 HTTP 形状关联](2026-10-06-typescript-http-overview.md)。
