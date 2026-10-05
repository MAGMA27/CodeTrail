# 工作台

## 状态

这是跨目录导航页；当前实现与未验证范围由 [就近文档](../../apps/web/workbench.md) 维护。

## 职责与范围

通过公开 HTTP/SSE 展示项目、Session、Run、图与源码。

## 接口与依赖

服务端拥有数据和执行，前端拥有视图、草稿及过期请求隔离。 入口见 [目录 README](../../apps/web/README.md)。

## 数据与生命周期

数据所有权与生命周期见 [当前实现](../../apps/web/workbench.md)，本页不重复维护。

## 错误与限制

边界、失败和覆盖限制见 [就近文档](../../apps/web/workbench.md)。

## 验证

测试命令与验证边界见 [就近文档](../../apps/web/workbench.md)；导航存在不代表验收已通过。

## 相关决策

具体取舍从就近文档进入 [决策目录](../decisions/README.md)。
