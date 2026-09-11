# 演示样本

## 状态

已有 timeout-demo、场景和固定响应；fixture 通过不代表真实模型效果。

## 职责与范围

提供确定性开发样本；独立评测数据后续单独维护。

## 接口与依赖

[fixtures README](../../fixtures/README.md) 拥有场景说明；[生成器](../../fixtures/generate_responses.py) 维护固定响应。

## 数据与生命周期

样本 manifest 与响应绑定；修改样本时同步 hash、位置和相关固定数据。

## 错误与限制

工具失败场景定义不等于运行时失败注入已实现；开发案例不充当独立评测。

## 验证

pnpm contracts:check 校验响应；样本变更另核对 manifest 和复现命令。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
