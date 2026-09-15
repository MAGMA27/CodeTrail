# 快照与关系

## 状态

已实现 Python 源码快照、定义、保守关系提取，以及本地源码新鲜度检查与显式索引更新；新索引对项目隔离的未变化文件复用文件级定义描述缓存，但关系阶段仍全量解析，不是完整增量索引。

## 职责与范围

读取受限源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。

## 接口与依赖

[indexing.py](../../apps/api/app/indexing.py) 生成快照和只读源码 manifest，并把定义提取拆成可校验的文件描述；[relations.py](../../apps/api/app/relations.py) 基于整份快照提取关系。应用服务通过 [storage.py](../../apps/api/app/storage.py) 查询/写入项目隔离的定义缓存；缓存键包含规范化相对路径、内容 hash、定义规则版本、Python AST 版本和配置 hash。关系规则版本和索引配置指纹参与服务创建的 index identity，使源码 hash 相同但解析规则或边界配置变化时仍得到独立的不可变 index。

## 数据与生命周期

快照保存文本及 hash、规则版本和配置指纹；关系与符号属于同一 index_id，后续读取不依赖变动工作树。新建快照先读取完整源码，再按精确缓存键复用未变化文件的定义描述；命中后按当前 index_id 重新生成 Symbol，未命中只对该文件执行定义提取，缓存写入在完整 index 成功后独立提交。定义和关系阶段分别记录实际 `ast.parse` 次数，关系仍以整份新快照重算。新鲜度检查使用与快照相同的源码范围和内容 hash，只读取 manifest，不执行 AST/关系提取，返回 `consistent`、`changed` 或 `unknown` 及有界变化路径。显式更新在服务端重新核对：无变化时复用基准快照；完整且安全扫描确认源码、规则或配置变化，或发现旧元数据缺失时生成新的不可变快照，旧 metadata 与这些可重建原因同时出现时仍允许全量修复但保持 `unknown`；读取/边界/预算/TOCTOU 等安全原因继续拒绝。开始和完成写入时都校验基准仍是项目 latest。关系索引将静态扫描覆盖（`truncated`/`truncation_reason`）与 unresolved 详情容量（`unresolved_truncated`/`unresolved_truncation_reason`）分开记录；unresolved 达到上限后仍继续有界扫描确定静态边。旧索引的新字段为未知，不被回填或改写。

## 错误与限制

路径、文件和执行预算受限；定义缓存只保存严格 JSON 描述，默认每个项目/仓库最多 5,000 条和 16 MiB，损坏、版本不兼容、行号不匹配或读写失败均安全降级为未命中，清理不触碰历史 index/evidence。新鲜度扫描的文件数、总字节和时间上限独立于变化路径列表上限，扫描不完整、读取错误、符号链接、边界逃逸、TOCTOU 或 GitHub 远端均不能判定一致。无法确定的调用保留 unresolved。静态扫描可因时间/边预算不完整，unresolved 详情也可独立截断；空邻接不能证明没有关系。

## 验证

apps/api/tests/test_indexing.py、test_relations.py、test_storage.py、test_freshness.py 和 test_definition_cache.py 覆盖稳定 ID、规则版本隔离、路径约束、资源预算、文件缓存冷/热等价、失效/损坏/容量/并发/失败降级、内容 hash 新鲜度、显式复用/重建、旧元数据和 latest 保留；test_mcp.py 覆盖 HTTP/MCP 检查结果一致及独立写开关。本步后端全量为 158 passed，关系阶段全量解析与定义阶段实际计数均有独立断言。

## 相关决策

当前依据为现有实现和根目录开发约束；取舍记录在 [关系扫描与 unresolved 覆盖语义](../decisions/2026-09-13-relation-coverage.md)、[本地项目索引新鲜度与显式更新](../decisions/2026-09-15-index-freshness.md) 和 [文件级定义提取缓存](../decisions/2026-09-15-definition-extraction-cache.md)，其他重要取舍记录到 [决策目录](../decisions/README.md)。
