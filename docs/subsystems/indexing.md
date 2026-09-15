# 快照与关系

## 状态

已实现 Python 源码快照、定义、保守关系提取，以及本地源码新鲜度检查与显式索引更新。

## 职责与范围

读取受限源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。

## 接口与依赖

[indexing.py](../../apps/api/app/indexing.py) 生成快照和只读源码 manifest；[relations.py](../../apps/api/app/relations.py) 基于快照提取关系。关系规则版本和索引配置指纹参与服务创建的 index identity，使源码 hash 相同但解析规则或边界配置变化时仍得到独立的不可变 index。

## 数据与生命周期

快照保存文本及 hash、规则版本和配置指纹；关系与符号属于同一 index_id，后续读取不依赖变动工作树。新鲜度检查使用与快照相同的源码范围和内容 hash，只读取 manifest，不执行 AST/关系提取，返回 `consistent`、`changed` 或 `unknown` 及有界变化路径。显式更新在服务端重新核对：无变化时复用基准快照；完整且安全扫描确认源码、规则或配置变化，或发现旧元数据缺失时生成新的不可变快照，旧 metadata 与这些可重建原因同时出现时仍允许全量修复但保持 `unknown`；读取/边界/预算/TOCTOU 等安全原因继续拒绝。开始和完成写入时都校验基准仍是项目 latest。关系索引将静态扫描覆盖（`truncated`/`truncation_reason`）与 unresolved 详情容量（`unresolved_truncated`/`unresolved_truncation_reason`）分开记录；unresolved 达到上限后仍继续有界扫描确定静态边。旧索引的新字段为未知，不被回填或改写。

## 错误与限制

路径、文件和执行预算受限；新鲜度扫描的文件数、总字节和时间上限独立于变化路径列表上限，扫描不完整、读取错误、符号链接、边界逃逸、TOCTOU 或 GitHub 远端均不能判定一致。无法确定的调用保留 unresolved。静态扫描可因时间/边预算不完整，unresolved 详情也可独立截断；空邻接不能证明没有关系。

## 验证

apps/api/tests/test_indexing.py、test_relations.py 和 test_freshness.py 覆盖稳定 ID、规则版本隔离、路径约束、资源预算、内容 hash 新鲜度、显式复用/重建、旧元数据和 latest 保留；test_mcp.py 覆盖 HTTP/MCP 检查结果一致及独立写开关。

## 相关决策

当前依据为现有实现和根目录开发约束；取舍记录在 [关系扫描与 unresolved 覆盖语义](../decisions/2026-09-13-relation-coverage.md) 和 [本地项目索引新鲜度与显式更新](../decisions/2026-09-15-index-freshness.md)，其他重要取舍记录到 [决策目录](../decisions/README.md)。
