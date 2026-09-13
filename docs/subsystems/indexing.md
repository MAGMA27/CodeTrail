# 快照与关系

## 状态

已实现 Python 源码快照、定义及保守关系提取。

## 职责与范围

读取受限源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。

## 接口与依赖

[indexing.py](../../apps/api/app/indexing.py) 生成快照；[relations.py](../../apps/api/app/relations.py) 基于快照提取关系。关系规则版本参与服务创建的 index identity，使源码 hash 相同但解析规则变化时仍得到独立的不可变 index。

## 数据与生命周期

快照保存文本及 hash；关系与符号属于同一 index_id，后续读取不依赖变动工作树。关系索引将静态扫描覆盖（`truncated`/`truncation_reason`）与 unresolved 详情容量（`unresolved_truncated`/`unresolved_truncation_reason`）分开记录；unresolved 达到上限后仍继续有界扫描确定静态边。旧索引的新字段为未知，不被回填或改写。

## 错误与限制

路径、文件和执行预算受限；无法确定的调用保留 unresolved。静态扫描可因时间/边预算不完整，unresolved 详情也可独立截断；空邻接不能证明没有关系。

## 验证

apps/api/tests/test_indexing.py 和 test_relations.py 覆盖稳定 ID、规则版本隔离、路径约束、资源预算及 unresolved 达上限后继续提取后部确定调用的回归。

## 相关决策

当前依据为现有实现和根目录开发约束；本步取舍记录在 [关系扫描与 unresolved 覆盖语义](../decisions/2026-09-13-relation-coverage.md)，其他重要取舍记录到 [决策目录](../decisions/README.md)。
