# API 与工具适配

## 状态

源码已有索引及查询端点，已通过真实本地 HTTP 与工作台流程验收。Run/SSE 尚未实现。

## 职责与范围

将受认可的仓库映射到索引与查询能力，为 HTTP 和工具提供复用逻辑。

## 接口与依赖

[main.py](../../apps/api/app/main.py) 定义路由，[service.py](../../apps/api/app/service.py) 适配存储与工具结果。

## 数据与生命周期

请求使用 repository_id 和 index_id；阻塞索引进入 worker thread，操作独立管理数据库连接。邻接响应返回 root、受限 nodes、静态 neighbors 和对应 unresolved，供工作台绑定同一快照渲染。

## 错误与限制

仓库使用服务端 allow-list；参数、未知目标、预算和内部失败有错误映射，不泄露宿主路径。

## 验证

apps/api/tests/test_api.py；已实际 HTTP 创建索引、搜索、邻接与证据读取，并由工作台浏览器流程复核创建/打开、方向切换和错误恢复。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
