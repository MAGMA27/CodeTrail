# 决策：文件关系事实缓存与全快照目标解析

Status: implemented
- Date: 2026-09-15
- Scope: Python 关系事实提取、索引阶段 AST 生命周期、项目派生缓存和关系构建统计
- Latest implementation: 2026-09-16 局部构造实例方法调用解析；关系语义与索引身份升级联动

## 问题

文件级定义描述已经可以跨索引复用，但关系访问器仍会为每个文件重新解析 AST。关系结果同时依赖文件内的导入、作用域和调用语法，以及当前快照中可能变化的跨文件定义；直接缓存最终边会把旧 index 身份和旧目标绑定错误地带入新快照。

## 决定

1. 在现有服务管理 SQLite 中增加项目/仓库隔离的 `relation_cache` 派生表。键包含规范化相对路径、源码内容 hash、关系事实规则版本、Python AST 版本和索引配置 hash；缓存不属于 index、Session、Run 或 evidence。
2. 缓存值只允许严格 JSON 的版本化文件事实：导入语句及 alias/module/relative-level、作用域的 imports/assigned/definition/global/dynamic/parameter/attribute-write binding 摘要、有序调用和赋值/失效事件、name/attribute/dynamic 目标形状、类继承/装饰器安全标记、调用位置和嵌套作用域事件。值不保存 AST、旧 `index_id`、Symbol ID、最终跨文件边或可执行对象；损坏、未知字段、未知版本和越界位置均按未命中处理。
3. 定义阶段冷解析产生的有效 AST 只在本次构建内挂在 `DefinitionIndex` 上，关系事实提取优先复用它；定义缓存命中但关系事实未命中时关系阶段自行解析一次。持久化的是事实，AST 在构建结束后不进入存储。统计分别报告定义命中/未命中/解析、关系事实命中/未命中、新 `ast.parse` 和定义阶段 AST 复用次数。
4. 每次关系构建都用当前快照的完整 DefinitionMaps 解析所有已取得的文件事实，重新产生当前 `index_id` 的 imports/calls/unresolved。除既有名称/模块解析外，只支持同一作用域直接单名称赋值 `obj = KnownClass(...)`：构造名必须由同文件类或明确导入的类绑定，目标类和直接方法必须唯一、无继承/影响绑定的装饰器/已知属性写覆盖；随后 `obj.method(...)` 才产生 calls 边。条件/循环赋值、重赋值、参数或构造名遮蔽、重复定义、继承、装饰器、动态属性、工厂/返回值/实例字段等无法唯一确认时保留 unresolved。调用方文件未变但目标新增、删除、移动、重命名或变更时，不复用旧边；目标无法确认仍保留 unresolved。事实提取未完成的文件不写入完整缓存，跨文件目标解析不进入缓存。
5. 关系事实提取和目标解析共享既有关系时间/数量预算。时间耗尽前已完成的文件事实可以安全写入，尚未完成的文件不写入；改变边/unresolved/时间预算不会把部分关系输出当作缓存事实。关系缓存与定义缓存共用每个 scope/repository 的默认 5,000 条、16 MiB 上限，写入在 index 成功后的独立事务中完成，清理只删除两类派生缓存。
6. HTTP、MCP、Pydantic、快照 hash、Symbol ID、旧 index/Session/Run/evidence 契约不变；freshness 一致时仍跳过整个构建，GitHub 远端检查、自动监听、受影响子图增量更新和完整增量图不在本步范围。
7. 关系语义版本、事实缓存规则/格式和索引构建身份独立维护。当前关系语义为 `relations-v3`，由 `current_source_rules_version` 组成 `IndexSnapshot.rules_version`；当前关系事实提取规则和 JSON 格式为 `python-relation-facts-v2` / `relation-facts-v2`，只控制 index-independent 的事实 payload；当前不可变索引构建身份为 `relations-v4`，写入 index ID 命名空间。改变图或 unresolved 语义必须同时提升语义版本和构建身份；只改变事实序列化格式不自动提升图身份。freshness、历史兼容查询和 refresh 共享当前 rules/config；旧语义 index 只能保留并显式重建，不能被历史复用。

## 替代方案

- 缓存最终跨文件边：目标定义和 Symbol ID 可能随新快照变化，会把旧关系带入新 index，因此只缓存文件内语法事实。
- 持久化 AST 或用 pickle/eval 恢复：会引入可执行对象、版本兼容和安全边界问题，因此使用严格 JSON 事实。
- 关系阶段继续独立解析两次 AST：实现简单但冷构建重复工作；本步在同一进程复用定义阶段 AST，同时保留跨进程事实缓存。
- 为每类缓存分别设置独立上限：会让两类派生数据叠加占用不可见的两倍预算，因此使用 scope/repository 共享容量。

## 验收与证据

- `test_relations.py` 的现有关系语义测试保持通过；缓存热路径另覆盖跨文件导入/别名、相对导入、同名函数、参数和赋值遮蔽、动态调用及 unresolved。
- 三文件样本冷构建定义阶段解析 3 次，关系事实阶段复用 3 棵 AST 且新 `ast.parse` 为 0；重启热构建定义/关系事实均为 3 命中且 0 次解析；修改一个文件时两类缓存各 2 命中/1 未命中，只有该文件重新解析定义并共享其 AST，跨文件目标仍以新快照全量解析。
- `test_definition_cache.py` 覆盖关系事实重启、语义等价、目标重命名/恢复后的重新绑定、语法诊断、规则/配置/路径/项目隔离、损坏/不兼容、共享容量、构建失败和时间截断未完成文件不写入；关系目标解析不读取或写入旧 index/edge。
- 局部构造实例验收覆盖同文件类、跨文件导入类、导入别名、同名类区分，以及重赋值、参数/构造名遮蔽、条件赋值、继承、装饰器、类/实例属性写入、重复方法和动态属性反例；每个正例校验调用位置和目标类方法，反例只保留 unresolved。
- 修改被调用类的方法时，未变化调用方的事实缓存仍可命中但会按当前 DefinitionMaps 重新解析并撤销旧边；恢复方法后重新绑定到新 `index_id` 的 Symbol，不复用旧跨快照边。关系事实规则和 JSON 格式已升至 v2，旧缓存安全 miss。
- 关系语义升级的跨提交验收使用旧提交 `dee662f` 与当前代码在同一隔离源码/数据目录运行：旧 `relations-v2` 索引对 `obj.run()` 保留 `unresolved`，当前 freshness 返回 `changed`，原因明确为 `rules_changed` 与 `configuration_changed`；显式 refresh 生成新的 `relations-v4-rebuild` index 并出现 `Widget.run` calls 边，旧 index 仍保留；再次检查为 `consistent`，再次更新为 `reused`。HTTP、stdio MCP 和工作台分别观察到相同规则变化/新关系结果。
- 本轮实际收尾：`uv run pytest tests/test_definition_cache.py tests/test_relations.py tests/test_storage.py -q` 为 39 passed，`uv run pytest -q` 为 162 passed，`uv run python -m compileall -q app`、`pnpm docs:check`、`pnpm docs:check:local` 和 `git diff --check` 通过；本步没有契约或前端行为变化，不生成 Schema、不运行前端构建。

## 后果

未变化文件的关系事实可在后续索引和服务重启中复用，冷构建也避免定义与关系事实各自执行一次 AST 解析；目标解析仍需对新快照全量运行，因此这不是受影响子图增量更新，也不保证单文件更新的总耗时按比例下降。关系语义升级会让共享的 index 配置指纹变化，首次升级构建可能同时冷读定义缓存；这是可接受的正确性优先代价，定义缓存复用收益留待后续拆分独立指纹时优化。局部对象绑定是静态推断，不代表运行时类型或分派；保守拒绝优先于猜测，且不承诺连通 `self.field`、上下文管理器或工厂返回值。事实缓存损坏或被容量清理只损失性能；源码 evidence 和报告始终来自不可变 snapshot。后续若改变事实结构或规则，必须递增对应规则/格式版本；若改变图或 unresolved 语义，还必须递增关系语义版本和索引构建身份，并重新验证所有作用域、遮蔽和 unresolved 语义。
