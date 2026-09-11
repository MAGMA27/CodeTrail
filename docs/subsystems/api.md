# API 与工具适配

## 状态

源码已有索引及查询端点；本轮文档建立不等于完成真实 HTTP 场景验收。Run/SSE 尚未实现。

## 职责与范围

将受认可的仓库映射到索引与查询能力，为 HTTP 和工具提供复用逻辑。

## 接口与依赖

[main.py](../../apps/api/app/main.py) 定义路由，[service.py](../../apps/api/app/service.py) 适配存储与工具结果。

## 数据与生命周期

请求使用 repository_id 和 index_id；阻塞索引进入 worker thread，操作独立管理数据库连接。

## 错误与限制

仓库使用服务端 allow-list；参数、未知目标、预算和内部失败有错误映射，不泄露宿主路径。

## 验证

apps/api/tests/test_api.py；端到端交付还需实际 HTTP 创建索引、搜索、邻接与证据读取。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
