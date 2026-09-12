# 演示样本

## 状态

已有 timeout-demo、场景和固定响应；O2 的 `fixture-feature-v1` 会从真实索引查询返回的入口、搜索、邻接和源码 evidence 确定性生成主要功能结果；fixture 通过不代表真实模型效果。

## 职责与范围

提供确定性开发样本；独立评测数据后续单独维护。

## 接口与依赖

[fixtures README](../../fixtures/README.md) 拥有场景说明；[生成器](../../fixtures/generate_responses.py) 维护固定响应。

## 数据与生命周期

样本 manifest 与响应绑定；O2 功能生成直接读取当前不可变索引，不另存一份模型报告；修改样本时同步 hash、位置和相关固定数据。

## 错误与限制

工具失败场景的 `read_source` 注入已由 Run executor 实际驱动，并保留注入前已返回的 evidence；三个 Run fixture 只用于离线契约、证据和生命周期验证，不能代替真实模型语义质量或独立评测。

## 验证

pnpm contracts:check 校验响应；`apps/api/tests/test_features.py` 校验 fixture 经过实际 `search_code`、`get_neighbors`、`read_source` 工具上下文后生成、保存、读取和证据钻取；样本变更另核对 manifest 和复现命令。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
