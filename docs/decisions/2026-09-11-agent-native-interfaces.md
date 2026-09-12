# 决策：人和 Agent 共用应用服务与数据契约

Status: implemented

## 问题

W1 已提供项目管理、索引归属和工作台切换。外部 Agent 需要直接使用同一项目、图与证据，而不依赖网页操作或必须委托内置 Agent。现有 HTTP/工具适配可复用；A1 已补齐本地 MCP 入口及固定版本证据接续。

## 决定

建立应用服务层作为业务语义和数据边界的唯一入口：人通过工作台与 HTTP 使用，外部 Agent 通过 MCP 使用，内置 Agent 直接调用受限服务。HTTP 与 MCP 只做传输、参数和结果映射，不复制检索、归属校验、预算和存储逻辑。Pydantic 是领域请求/结果的权威，传输包装可以不同；生成 Schema 不手改。

首步 A1 为本地只读 MCP，采用 stdio 作为计划传输，复用同一配置和存储的应用服务；兼容 SDK 及协议版本在实施时核实并锁定。该进程与 HTTP 进程共享持久数据，但不共享可变内存会话或线程连接。远程 MCP、身份认证和写操作留后续设计，不把本地配置当远程权限方案。

A1 暴露 list_projects、get_project、list_indexes、get_index、search_code、read_source、get_neighbors。除项目发现外均显式绑定 project_id，索引查询同时绑定 index_id；不得通过无项目旧接口绕过归属检查，也不依赖全局当前项目。结果复用稳定符号/证据 ID、hash、位置、unresolved 和截断信息。分页或总量限制必须明确，不能默默返回无限项目列表。

结构化结果是主输出，短文字摘要可辅助解释；工具描述说明参数、效果、限制和错误。统一领域错误 code/message/retryable 的语义，再映射到 HTTP/MCP 各自错误机制；传输错误和业务失败区分。只读能力不经过内置模型，不消耗模型费用。

提供可序列化引用 project_id/index_id/path/行范围/content_hash/evidence_id 和基于配置工作台地址的打开链接。前端验证引用和归属，从固定快照读取；不得把 Agent 文本或链接参数视作可信源码。引用相同不表示某个 Run 已取得该证据，Run 引用仍要求本次工具返回记录。

后续创建/导入、索引、概览生成、Issue 分析是单独开放的写入或费用能力，服务端按调用方能力集执行，不能由工具参数扩大权限。O2 已按同一原则开放 HTTP 功能生成，并以独立 MCP 开关控制可能产生费用的生成工具；P2-R2 又以 `CODETRAIL_REAL_RUN_ENABLED` 和 `CODETRAIL_MCP_REAL_RUN_ENABLED` 分别控制 HTTP/MCP real Run，fixture Run 写权限不会自动开放模型调用。结果读取保持只读且不调用模型。长任务提供操作标识、查询与取消的统一语义；ImportAttempt 与 Run 保留各自领域状态，Operation 只做投影，不替代现有生命周期或创建第二套状态源。重试写操作需要幂等约定，取消不抹去已有证据；P3-S1 已将 SSE 作为工作台公开事件增量入口，MCP 继续使用同一持久化事实的分页读取。

## 替代方案

Agent 通过网页操作无法稳定组合能力；独立重写 MCP 存储和检索会导致两套权限与证据。把所有调用交给内置 Agent 会增加费用与不确定性。本地先直接复用服务；独立远程部署时可改为调用同一 HTTP 服务，但必须保留一致性测试。

## 验收与证据

A1 已实现并用官方 Python MCP SDK `2.2.0` 的真实 stdio client 验证：`apps/api/tests/test_mcp.py` 通过，客户端发现并调用七项基础查询工具，项目/索引/搜索/邻接/源码结果与 HTTP 领域 payload 对照一致，跨项目索引返回 `not_found`，源码结果带固定 project_id/index_id、行范围、content hash、evidence_id 和工作台 URL。O1 在同一适配器增加默认只读 `get_overview`，O2 又增加独立开关控制的 `generate_project_features`/`get_feature_generation`；P2-R2 增加独立开关控制的 `start_session_run` real 能力，并与 HTTP 共用 Run 归属、预算、取消和报告语义。其 HTTP/MCP 领域结果和证据引用已对照验证，默认 MCP 仍不注册生成工具。工作台浏览器流程已验证引用只打开归属项目的固定索引证据；SDK 协议帧使用 stdout，日志使用 stderr。

## 后果

A1 已插入并完成于 W1 后、W2 前；W2/W3/O/P2 每项新能力均按同一应用服务映射，P2-R2 real Run 已完成对应 HTTP/MCP 能力边界与本地协议验证。保留现有栈与工作区主线，不引入多 Agent、通用插件框架或远程部署；SSE 和真实供应商语义评测仍是后续工作。API 就近 README、子系统索引、Schema 和客户端配置说明已同步。本记录补充工作区决策，不替代其产品范围。
