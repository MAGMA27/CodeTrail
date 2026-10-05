# 数据契约

## 状态

这是跨目录导航页；当前实现与未验证范围由 [就近文档](../../contracts/reference.md) 维护。

## 职责与范围

后端 Pydantic 声明是字段与校验的机械来源。

## 接口与依赖

API、MCP、前端和固定响应共享生成契约，不各自维护字段定义。 入口见 [目录 README](../../contracts/README.md)。

## 数据与生命周期

数据所有权与生命周期见 [当前实现](../../contracts/reference.md)，本页不重复维护。

## 错误与限制

边界、失败和覆盖限制见 [就近文档](../../contracts/reference.md)。

## 验证

测试命令与验证边界见 [就近文档](../../contracts/reference.md)；导航存在不代表验收已通过。

## 相关决策

具体取舍从就近文档进入 [决策目录](../decisions/README.md)。
