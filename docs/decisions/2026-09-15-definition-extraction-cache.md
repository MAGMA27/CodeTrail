# 决策：文件级定义提取缓存

Status: implemented

## 问题

本地项目显式更新会重新捕获源码并从头执行定义提取和关系解析。源码快照本身已经按内容 hash 区分文件，但未变化文件的定义描述没有跨构建、跨进程复用；同时不能把关系阶段仍需覆盖全量新快照的成本隐藏成“完整增量索引”。

## 决定

1. 在现有每个 repository 的 SQLite 中增加服务管理的 `definition_cache` 派生表；缓存按 `scope_id` 与 `repository_id` 隔离，WorkspaceService 使用 `project_id`，低层兼容入口使用 repository fallback。缓存不写入来源目录，不属于 index、Session、Run 或 evidence。
2. 缓存键由规范化相对路径、源码内容 hash、`DEFINITION_EXTRACTION_RULES_VERSION`、完整 Python AST 版本和当前索引配置 hash 组成。同内容不同路径、`__init__.py` 与普通模块不会共享键；源码删除自然不再命中，修改、规则、Python 或配置变化自然失效。
3. 缓存值只允许严格 JSON：格式版本、限定名、`module|class|function` 类型、起止行号和可选 syntax error 诊断。读取拒绝未知字段、错误类型、重复定义、错误路径、超出源码行数、字节数不符或不兼容版本；不保存旧 `index_id`、Symbol、Evidence 或可执行对象。命中后以当前快照的 `index_id` 重新生成 Symbol ID。
4. 构建先捕获不可变源码，再按当前快照查缓存。定义阶段分别记录命中、未命中和实际 `ast.parse` 次数；未命中结果在成功写入完整 index 后才尝试写入缓存。关系目标解析仍以整份新快照的 DefinitionMaps 重新绑定，文件级关系事实缓存与同一 AST 的复用见[文件关系事实缓存与全快照目标解析](2026-09-15-relation-facts-cache.md)。
5. 缓存写入是独立的 best-effort 事务：索引成功后缓存写入失败只记录日志，不使索引失败；构建失败不会写入本次新解析结果。每个 scope/repository 默认最多 5,000 条、16 MiB，写入事务内按时间和 rowid 清理超出的缓存行，清理只删除派生缓存，不触碰历史 index、Session、Run 或 evidence。旧数据库无表时由现有 schema 初始化，损坏缓存按未命中安全降级。
6. 对外 HTTP/MCP/Pydantic 契约不增加缓存字段；统计仅通过内部构建回调用于验收和诊断。无变化 refresh 仍在 freshness 层复用整个快照，跳过整个构建。

## 替代方案

- 将缓存写入来源目录：实现直观，但污染只读来源并破坏服务数据边界，因此复用现有 SQLite。
- 以 mtime/size 或旧 index_id 作为缓存身份：无法可靠处理同大小替换、路径语义变化或新快照 Symbol 身份，因此使用内容 hash 和完整规则键。
- 缓存 AST 对象或跨文件关系：对象不可安全持久化，关系目标受整份快照和变化文件影响；本步只缓存可校验的定义描述，关系事实另以严格 JSON 缓存并在新快照中重新解析目标。
- 把缓存写入和 index 写入放进同一长期事务：会扩大不可变 index 的锁定范围，且缓存只是可重建优化；因此 index 先独立提交，缓存另行原子写入。

## 验收与证据

- 本记录的定义缓存验收已由后续关系事实缓存扩展覆盖：定义与关系事实共用冷路径 AST，热路径分别命中，当前快照的 DefinitionMaps 重新解析跨文件目标；缓存值不含 AST、旧 Symbol ID 或最终边。
- 本记录的历史服务统计保留为定义缓存阶段证据；关系事实缓存的最新测试数量、共享 AST 计数、容量/损坏/不完整事实边界及本轮全量结果以[文件关系事实缓存与全快照目标解析](2026-09-15-relation-facts-cache.md)和本轮交付记录为准。
- 本步未改变契约或前端，因此不生成 Schema、不运行前端构建；`uv run python -m compileall -q app` 与文档检查在本轮收尾时重新执行。

## 后果

定义提取与关系事实都可在同一项目的后续更新和服务重启中复用未变化文件，命中结果仍绑定新 immutable index；冷路径可共享有效 AST，热路径无需解析。磁盘缓存会占用有限 SQLite 空间并可能被清理，损坏或写入失败只损失性能。关系目标解析仍按全量新快照执行，所以单文件更新的总体收益受目标重绑定和快照捕获成本限制；`read_source` 和报告 evidence 始终读取持久化快照，不读取缓存。

与 [本地项目索引新鲜度与显式更新](2026-09-15-index-freshness.md) 的关系是：freshness 决定是否需要新建快照，本决策只优化新快照内部的定义/关系事实阶段，不改变旧 latest、Session/Run 或更新资格；关系事实的具体边界见[文件关系事实缓存与全快照目标解析](2026-09-15-relation-facts-cache.md)。
