# 快照与关系

## 状态

已实现 Python 源码快照、定义、保守关系提取，以及本地源码新鲜度检查与显式索引更新；新索引对项目隔离的未变化文件复用文件级定义描述和关系事实缓存，源码恢复到兼容历史内容时还可直接复用既有 immutable index，不重新解析；新快照的目标解析仍基于当前 DefinitionMaps 全量执行，不是完整增量索引。当前源码捕获规则为 `python-source-v2`（递归扫描跳过 `.runtime`、`.codetrail-runtime` 等服务生成数据目录），关系语义为 `relations-v3`，事实缓存规则/格式为 `python-relation-facts-v2` / `relation-facts-v2`，索引构建身份为 `relations-v5`；各版本职责分离。关系解析额外支持同一作用域中 `obj = KnownClass(...)` 后的直接 `obj.method(...)`，但只接受可唯一确认的无继承/无影响绑定装饰器/无属性覆盖类目标。

## 职责与范围

读取受限源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。

## 接口与依赖

[indexing.py](app/indexing.py) 生成快照和只读源码 manifest，并把定义提取拆成可校验的文件描述；[relations.py](app/relations.py) 将文件内语法事实与当前快照目标解析分开，并记录有序构造赋值/变量失效及类安全标记。应用服务通过 [storage.py](app/storage.py) 查询/写入项目隔离的定义缓存和关系事实缓存；两类缓存键均包含规范化相对路径、内容 hash、各自规则版本、Python AST 版本和配置 hash。关系语义版本参与 freshness 的 `rules_version`，关系语义和配置指纹共同参与服务创建的 index identity；因此源码 hash 相同但解析规则或边界配置变化时仍得到独立的不可变 index。事实缓存格式版本只控制对应 JSON payload 的命中，不直接充当图身份。

## 数据与生命周期

快照保存文本及 hash、规则版本和配置指纹；关系与符号属于同一 index_id，后续读取不依赖变动工作树。新建快照先读取完整源码，再按精确键复用未变化文件的定义描述和关系事实；定义缓存命中后按当前 index_id 重新生成 Symbol，关系事实命中后仍用当前 DefinitionMaps 解析目标，任何旧跨文件边都不会复用。关系事实中的直接构造赋值按事件顺序更新当前作用域的对象绑定；仅类直接定义的方法可形成对象 calls 边，类继承、装饰器、属性写入、变量失效或不确定控制流会拒绝绑定并保留 unresolved。定义缓存未命中时产生的有效 AST 只在当前构建内供关系事实提取共享，缓存写入在完整 index 成功后以独立事务提交；两类缓存共用每个 scope/repository 的容量。定义/关系事实命中、未命中、实际新 `ast.parse` 和进程内 AST 复用分别记录。新鲜度检查使用与快照相同的源码范围和内容 hash，只读取 manifest，不执行 AST/关系提取，返回 `consistent`、`changed` 或 `unknown` 及有界变化路径。显式更新在服务端重新核对：无变化时返回 `reused` 并复用当前基准；完整且安全扫描确认源码恢复到同项目中规则、配置、schema 和完整 manifest 均兼容的历史快照时返回 `historical_reused`、推进 latest 到该快照且保留其原 revision/dirty metadata；其他源码、规则、配置变化或旧元数据缺失时生成新的不可变 index，旧 metadata 与这些可重建原因同时出现时仍允许全量修复但保持 `unknown`；读取/边界/预算/TOCTOU 等安全原因继续拒绝。开始和完成写入时都校验基准仍是项目 latest。源码捕获规则或索引配置变化会使 freshness 失效、改变 configuration hash，并选择新的 `relations-v5` index 身份；事实缓存规则/格式变化则只使相应缓存安全 miss。关系索引将静态扫描覆盖（`truncated`/`truncation_reason`）与 unresolved 详情容量（`unresolved_truncated`/`unresolved_truncation_reason`）分开记录；unresolved 达到上限后仍继续有界扫描确定静态边。旧索引的新字段为未知，不被回填或改写。

GitHub 来源是 freshness 的远端例外：由于成功导入后的归档目录会清理，检查只调用共享客户端解析已跟踪 ref 的当前 commit，不读取归档、不执行 AST/关系提取，也不改变 index。结果同时保留远端 URL、请求 ref、解析 ref、commit 与 `remote_status`；远端 commit 一致时仍需独立比较 index 的 rules/config 身份。显式 GitHub 更新再次解析并通过受限固定 commit 导入，兼容的同 commit snapshot 可复用，ref 移动以实际 attempt 的 commit 为准，失败保留旧 latest。

## 错误与限制

路径、文件和执行预算受限；定义描述和关系事实缓存只保存严格 JSON，默认每个项目/仓库合计最多 5,000 条、16 MiB，损坏、版本不兼容、行号不匹配或读写失败均安全降级为未命中，清理不触碰历史 index/evidence。关系事实包含导入 alias、作用域绑定/遮蔽、调用目标形状、构造赋值/失效事件、类继承/装饰器安全标记、位置和有序嵌套作用域，不保存 AST、Symbol ID 或最终边；未完成文件事实不写入缓存。新鲜度扫描的文件数、总字节和时间上限独立于变化路径列表上限，本地扫描不完整、读取失败、符号链接、边界逃逸、TOCTOU 或 GitHub metadata 远端检查异常均不能判定一致；GitHub 正常 freshness 只判定跟踪 ref 的远端 commit 身份，不把它当作本地源码 manifest。无法唯一确认构造类或直接方法的调用保留 unresolved；不支持工厂、返回值、`self.field`、上下文管理器、动态属性和复杂别名传播。静态目标解析可因时间/边预算不完整，unresolved 详情也可独立截断；空邻接不能证明没有关系。

## 验证

apps/api/tests/test_indexing.py、test_relations.py、test_storage.py、test_freshness.py 和 test_definition_cache.py 覆盖稳定 ID、规则版本隔离、路径约束、资源预算、定义/关系事实缓存冷热等价、共享 AST、失效/损坏/容量/并发/失败降级、内容 hash 新鲜度、显式复用/重建、旧元数据和 latest 保留；其中 test_relations.py 还覆盖局部构造实例的正例及控制流/重赋值/继承/装饰器/属性覆盖反例，test_definition_cache.py 覆盖被调用类修改/恢复后的当前快照重绑定，test_freshness.py 覆盖关系语义升级时的 rules/config 变化、新 index 身份和重复复用；test_mcp.py 覆盖 HTTP/MCP 检查结果一致及独立 index 写开关。本项目的冷/热/单文件/目标重绑定/禁用缓存和跨进程实测见 [本项目索引更新与缓存效果联合验收](../../docs/decisions/2026-09-16-project-index-cache-acceptance.md)，关系目标解析与两阶段实际计数均有独立证据。

## 相关决策

当前依据为现有实现和根目录开发约束；取舍记录在 [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)、[本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)、[GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)、[文件级定义提取缓存](../../docs/decisions/2026-09-15-definition-extraction-cache.md)、[文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md) 和 [本项目索引更新与缓存效果联合验收](../../docs/decisions/2026-09-16-project-index-cache-acceptance.md)，其他重要取舍记录到 [决策目录](../../docs/decisions/README.md)。
