# 决策：本地路径导入使用服务管理目录与持久导入状态

Status: implemented

## 问题

W1 只接受服务端预先配置的 `repository_id`，无法让用户从工作台提交一个服务端可见的本地目录。导入必须保持源目录只读、跨 HTTP/MCP 进程可恢复，并且失败或重启不能覆盖项目已有的可用索引。

## 决定

保留 `CODETRAIL_REPOSITORIES` 的静态仓库和原有 `.codetrail/index.sqlite3` 作为兼容路径。新增 `CODETRAIL_ALLOWED_IMPORT_ROOTS`、`CODETRAIL_DATA_DIR` 和 `CODETRAIL_MCP_LOCAL_IMPORT_ENABLED`：本地路径只能位于服务端允许根目录内，注册信息与 `ImportAttempt` 写入管理 SQLite，导入项目的索引库写入管理目录下的项目存储，不在来源目录创建 `.codetrail` 或缓存。

来源路径在服务端规范化并按平台大小写规则去重；源目录、祖先路径中的符号链接或 junction 逃逸被拒绝。相同规范来源复用既有项目；幂等键绑定项目、路径、名称、revision 和 dirty，参数冲突返回稳定冲突错误。同步导入仍记录 `queued → acquiring → indexing → ready/failed`，重启时将活动尝试标记 `interrupted`，失败可通过同一 attempt 重试，项目的 `latest_index_id` 只在新索引完整写入后更新。

HTTP 导入、状态和重试入口只接受 loopback 客户端；MCP 保留 A1 七个只读工具，只有开关启用时才注册本地导入和状态/重试工具。两个进程分别打开自己的 SQLite 连接，通过管理库和动态注册刷新共享持久状态，不共享进程内会话。

## 替代方案

继续把索引库写入来源目录实现简单，但违反源仓库不变和可移植服务管理要求。只在内存中追加 allow-list 无法让重启后的 HTTP/MCP 进程发现导入项目。把导入伪装成 Issue Run 会混淆生命周期，也无法表达幂等和重试状态。

## 验收与证据

已验证：`apps/api/tests/test_api.py` 覆盖两个临时 Python 来源、源目录 manifest 不变、管理目录索引、项目/attempt 持久化、重开恢复、路径/链接/空源码边界、幂等/并发、失败重试和旧 latest 保留；`apps/api/tests/test_mcp.py` 覆盖 MCP 开关关闭时仅七项只读工具、开启后与 HTTP 共用项目和证据。`uv run pytest -q` 为 55 passed，契约检查为 18 passed，前端交互测试为 3 passed，类型检查、生产构建、编译检查和 docs:check/docs:check:local 通过；浏览器以 `CODETRAIL_WEB_ORIGINS` 配置的本地 API 完成路径导入并刷新恢复同一项目和索引。

## 后果

新增一个受限本地来源注册边界和管理 SQLite，需维护旧静态仓库兼容、动态 registry 刷新以及导入状态生命周期。W2 仍只支持 Python 静态快照，不实现 GitHub、远程 MCP、异步队列、多租户、概览或模型调用。
