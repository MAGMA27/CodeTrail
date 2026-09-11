# API 与工具适配

## 状态

源码已有项目、索引及查询端点，已通过真实本地 HTTP 与双项目工作台流程验收。Run/SSE、仓库导入和功能概览尚未实现。

## 职责与范围

将受认可的仓库映射到 Project、索引与查询能力，为 HTTP 和工具提供复用逻辑；项目作用域查询负责校验 index_id 归属。

## 接口与依赖

[apps/api README](../../apps/api/README.md) 记录服务入口；[main.py](../../apps/api/app/main.py) 定义路由，[service.py](../../apps/api/app/service.py) 适配存储与工具结果。

## 数据与生命周期

Project 请求使用 project_id，来源复用服务端 allow-list 的 repository_id；索引与查询同时使用 index_id。阻塞索引进入 worker thread，操作独立管理数据库连接。项目保存可用索引列表与当前构建标记，邻接响应返回 root、受限 nodes、静态 neighbors 和对应 unresolved，供工作台绑定同一快照渲染。

## 错误与限制

仓库使用服务端 allow-list；项目作用域拒绝未知项目或不属于项目的 index_id，参数、预算和内部失败有错误映射，不泄露宿主路径。旧 `/api/indexes/*` 仍保留为无项目上下文的兼容入口。

## 验证

apps/api/tests/test_api.py；已实际 HTTP 创建/列表/切换项目、项目索引归属、失败不覆盖最新版本、重启恢复，以及索引搜索、邻接与证据读取，并由工作台浏览器流程复核双项目隔离。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
