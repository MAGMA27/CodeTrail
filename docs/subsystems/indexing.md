# 快照与关系

## 状态

已实现 Python 源码快照、定义及保守关系提取。

## 职责与范围

读取受限源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。

## 接口与依赖

[indexing.py](../../apps/api/app/indexing.py) 生成快照；[relations.py](../../apps/api/app/relations.py) 基于快照提取关系。

## 数据与生命周期

快照保存文本及 hash；关系与符号属于同一 index_id，后续读取不依赖变动工作树。

## 错误与限制

路径、文件和执行预算受限；无法确定的调用保留 unresolved，部分结果记录截断。

## 验证

apps/api/tests/test_indexing.py 和 test_relations.py 覆盖稳定 ID、路径约束和解析限制。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
