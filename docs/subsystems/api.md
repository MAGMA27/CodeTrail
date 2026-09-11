# API 与工具适配

## 状态

源码已有项目、索引及查询端点、本地路径导入和本地 MCP 适配器；已通过真实本地 HTTP、MCP stdio 客户端、双项目工作台与双进程导入流程验收。Run/SSE、GitHub 导入和功能概览尚未实现。

## 职责与范围

将受认可的仓库或服务端允许的本地路径映射到 Project、索引与查询能力，为 HTTP 和工具提供复用逻辑；项目作用域查询负责校验 index_id 归属，导入服务负责来源边界、持久 attempt、幂等和重试。

## 接口与依赖

[apps/api README](../../apps/api/README.md) 记录服务入口；[main.py](../../apps/api/app/main.py) 定义 HTTP 路由，[service.py](../../apps/api/app/service.py) 拥有项目归属和查询语义，[mcp_server.py](../../apps/api/app/mcp_server.py) 只负责 MCP stdio 注册、结构化结果和错误/引用映射。

## 数据与生命周期

Project 请求使用 project_id，静态来源复用服务端 allow-list 的 repository_id，动态来源使用持久注册的 import-* repository_id；索引与查询同时使用 index_id。阻塞索引进入 worker thread，操作独立管理数据库连接。项目保存可用索引列表与当前构建标记，导入 attempt 单独记录 queued/acquiring/indexing/ready/failed/interrupted，不冒充 Run。邻接响应返回 root、受限 nodes、静态 neighbors 和对应 unresolved，供工作台绑定同一快照渲染。

本地 MCP 使用 SDK `2.2.0` 的 stdio 传输，stdout 仅承载协议，日志写 stderr。默认七项工具为 list_projects、get_project、list_indexes、get_index、search_code、read_source、get_neighbors；启用 `CODETRAIL_MCP_LOCAL_IMPORT_ENABLED=1` 后增加 import_local_path、get_import_status、retry_local_import。除项目发现外显式要求 project_id，索引读取同时校验 index_id 所属项目。MCP 结构化结果复用 Pydantic 领域响应，业务失败带 code、message、retryable；read_source 另外生成同一 project_id/index_id、路径、行范围、hash 和 evidence_id 的工作台引用。

## 错误与限制

静态仓库使用服务端 allow-list；本地导入使用 `CODETRAIL_ALLOWED_IMPORT_ROOTS` 和服务管理目录，拒绝相对路径、越界、符号链接/junction 逃逸和管理目录重叠。HTTP 写入口只接受 loopback 客户端；项目作用域拒绝未知项目或不属于项目的 index_id，参数、预算和内部失败有错误映射，不泄露宿主路径。旧 `/api/indexes/*` 仍保留为无项目上下文的兼容入口，MCP 不调用该兼容入口。MCP 写工具默认未注册，启用后仍只能使用允许根目录，不支持远程传输、认证、GitHub 下载或模型调用。

## 验证

apps/api/tests/test_api.py、apps/api/tests/test_mcp.py；已实际 HTTP 本地导入、同源复用、幂等/并发、路径边界、空源码、失败重试、重启中断和管理目录隔离，以及 MCP 真实 stdio 客户端的只读/写开关、HTTP 导入发现、证据读取与七项工具对照验证，并由工作台浏览器流程复核导入闭环。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
