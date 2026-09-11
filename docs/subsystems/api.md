# API 与工具适配

## 状态

源码已有项目、索引及查询端点、本地路径导入、公开 GitHub 固定 commit 导入、O1 规则驱动项目概览、O2 有证据的主要功能语义生成、O3 保存版本历史/同快照功能图和本地 MCP 适配器；已通过离线远端边界测试、功能生成测试与真实本地 HTTP/MCP stdio 客户端验收。Run/SSE 和 Issue Agent 尚未实现。

## 职责与范围

将受认可的仓库、服务端允许的本地路径或公开 GitHub 固定 commit 映射到 Project、索引与查询能力，为 HTTP 和工具提供复用逻辑；项目作用域查询负责校验 index_id 归属，导入服务负责来源边界、远端预算、持久 attempt、幂等、重试和临时清理。

## 接口与依赖

[apps/api README](../../apps/api/README.md) 记录服务入口；[main.py](../../apps/api/app/main.py) 定义 HTTP 路由，[service.py](../../apps/api/app/service.py) 拥有项目归属、查询和生成功能，[overview.py](../../apps/api/app/overview.py) 生成固定快照概览，[feature_generation.py](../../apps/api/app/feature_generation.py) 收集有界上下文并适配 fixture/真实模型，[feature_graph.py](../../apps/api/app/feature_graph.py) 组装保存功能映射与静态关系，[config.py](../../apps/api/app/config.py) 从服务数据目录读取持久化模型配置，[mcp_server.py](../../apps/api/app/mcp_server.py) 负责 MCP stdio 注册、结构化结果和错误/引用映射。

## 数据与生命周期

Project 请求使用 project_id，静态来源复用服务端 allow-list 的 repository_id，动态来源使用持久注册的 import-* repository_id；索引与查询同时使用 index_id。阻塞索引进入 worker thread，操作独立管理数据库连接。项目保存可用索引列表与当前构建标记，导入 attempt 单独记录 queued/acquiring/indexing/ready/failed/interrupted，不冒充 Run。GitHub attempt 另外记录 canonical URL、requested ref、resolved commit、content hash 与临时清理状态；同仓库同 commit 复用 attempt，分支移动产生新快照。邻接响应返回 root、受限 nodes、静态 neighbors 和对应 unresolved，供工作台绑定同一快照渲染。

本地 MCP 使用 SDK `2.2.0` 的 stdio 传输，stdout 仅承载协议，日志写 stderr。默认十一项只读工具为 list_projects、get_project、list_indexes、get_index、search_code、read_source、get_neighbors、get_overview、list_feature_generations、get_feature_generation、get_feature_graph；启用本地或 GitHub 专用开关后才增加相应导入、状态和重试工具，其中 `CODETRAIL_MCP_GITHUB_IMPORT_ENABLED=1` 独立控制网络获取。除项目发现外显式要求 project_id，索引读取同时校验 index_id 所属项目。MCP 结构化结果复用 Pydantic 领域响应，业务失败带 code、message、retryable；read_source、get_overview、get_feature_generation 和 get_feature_graph 另外生成同一 project_id/index_id、路径、行范围、hash 和 evidence_id 的工作台引用。

`get_overview` 与 HTTP `GET /api/projects/{project_id}/indexes/{index_id}/overview` 复用 `WorkspaceService`。它不新增概览持久化表，只从指定不可变快照按 `python-entry-v1` 识别 `__main__.py` 和顶层 main guard，沿已有 `calls`/`imports` 静态边生成受深度、路径、模块和时间预算限制的入口路径；动态调用保持 unresolved。无入口返回 `not_identified`，不把目录或名为 `main` 的函数当成入口。

O2 的 `POST /api/projects/{project_id}/indexes/{index_id}/features` 复用同一 `WorkspaceService`，先读取 O1，再在预算内调用 `search_code`、`get_neighbors` 和 `read_source` 收集同一快照上下文；模型只能引用实际返回的 symbol/evidence。`fixture` 使用确定性 `fixture-feature-v1`，`real` 默认使用服务数据目录 `config.json` 中的 DeepSeek 配置，通过 OpenAI-compatible JSON HTTP 适配器请求 `https://api.deepseek.com` + `/chat/completions`；也可修改配置文件切换到其他兼容供应商。真实请求的 system message 从 `FeatureModelOutput.model_json_schema()` 生成当前输出契约，并与任务目标一起发送；修复请求继续发送同一 system 契约和任务目标。完整消息正文字符数计入输入预算，证据按完整块有界收缩，无法容纳修复附加内容时明确拒绝。结构化输出最多修复一次；每次模型调用的输入/输出字符数、累计 token/字符用量、提示词版本、规则版本、耗时、截断和未查询模块均持久化。总 deadline 覆盖上下文收集、每次外部调用、网络分块读取和成功结果保存前检查；超时保留证据与用量并保存失败结果，不保存成功结果。O3 的 `GET /api/projects/{project_id}/features` 使用绑定 project_id/index_id 的稳定游标，按 `created_at DESC, generation_id DESC` 返回保存版本摘要；明确 generation_id 的读取和 `/graph` 读取只访问保存结果与同一不可变索引，不重复调用模型。功能图在请求时组装，最多返回 40 个节点和 120 条边，semantic 功能关系与 static CodeEdge 分开，规则版本不一致的入口映射保留为不可用并披露限制。`GET .../features/{generation_id}` 只读取结果，HTTP 和 MCP 默认可用；MCP 的 `generate_project_features` 仅在 `CODETRAIL_MCP_FEATURE_GENERATION_ENABLED=1` 时注册，默认十一项只读工具不会触发模型。

## 错误与限制

静态仓库使用服务端 allow-list；本地导入使用 `CODETRAIL_ALLOWED_IMPORT_ROOTS` 和服务管理目录，拒绝相对路径、越界、符号链接/junction 逃逸和管理目录重叠。GitHub 只接受官方 HTTPS 仓库 URL，先固定 commit，再受限获取官方 archive 并拒绝解包链接/遍历/特殊文件；HTTP 写入口只接受 loopback 客户端。项目作用域拒绝未知项目或不属于项目的 index_id，参数、预算、上游限流和内部失败有错误映射，不泄露宿主路径。旧 `/api/indexes/*` 仍保留为无项目上下文的兼容入口，MCP 不调用该兼容入口。MCP 写工具默认未注册，GitHub 网络下载不由本地 MCP 开关隐式授权。持久化模型配置只读服务数据目录，缺失密钥、无效 JSON、未知字段和非法地址均不会泄露配置内容；功能生成区分 `model_unavailable`、模型失败、非法模型输出、证据不足、预算超限和部分截断；real 未配置密钥不回退 fixture，失败记录不覆盖既有成功缓存，非法引用不能保存。历史游标绑定 project_id/index_id，限制为每页最多 40 条；功能图限制为最多 40 个节点/120 条边，并校验节点、边、证据和同快照身份。通用上下文排序按入口关系、静态路径距离和稳定路径/行号排序，不依赖演示仓库符号名。

## 验证

apps/api/tests/test_api.py、apps/api/tests/test_github.py、apps/api/tests/test_overview.py、apps/api/tests/test_features.py、apps/api/tests/test_mcp.py；已验证本地导入、GitHub URL/固定 commit/官方重定向/限流/超时/恶意 archive/解包预算、同 commit 幂等、分支移动、清理失败重试、路径边界、重启中断和管理目录隔离，以及 O1 的入口规则、无入口/动态调用/预算/旧快照稳定性、O2 的真实工具上下文/fixture、模型协议、持久化配置读取/校验、证据校验、预算、缓存/幂等、失败保留和 MCP 可选开关，O3 的历史分页/精确读取/功能图边界/跨项目隔离/HTTP-MCP 对照，并由工作台浏览器流程复核概览、fixture 功能证据钻取、real 未配置失败和 O3 知识页流程。真实供应商语义质量仍未验证。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
