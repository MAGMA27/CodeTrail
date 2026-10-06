# 存储与查询

## 状态

已实现 SQLite/FTS5 快照、Project/Index 归属、来源导入、Session/消息、搜索、源码证据、受限邻接、O2 功能结果，以及 P2-R1/P2-R2 Run/冻结输入/事件/工具结果/报告和模型用量持久化；P3-I1 增加从 Run 固定报告/快照派生的有界静态影响查询；external Codex Run 复用同一表记录真实工具调用、证据和结论；已支持索引规则/配置元数据、只读源码 manifest、latest 保护下的显式更新，以及项目隔离且共享容量的文件级定义描述/关系事实派生缓存。SSE 仅读取同一持久化事件事实，不增加存储日志，影响结果也不写回 Run。

## 职责与范围

[IndexStore](app/storage.py) 拥有不可变 Index、Project 关联、Session/消息、Run 生命周期数据、同快照 `http_client_requests` 事实表和服务管理的 `definition_cache`/`relation_cache` 派生表；[ManagementStore](app/storage.py) 拥有动态来源和 ImportAttempt。存储层负责事务、唯一约束、作用域读取、游标、契约反序列化、manifest 读取、两类缓存的严格 JSON 校验和同快照静态 `calls` 影响遍历，不负责 HTTP 错误呈现、模型推理或工具计划；索引更新由应用服务发起，存储层在事务中再次校验 expected latest，避免并发旧结果覆盖新 latest。影响查询只读取已有 symbols/static_edges/unresolved，不修改 Run 或推断模型影响；real Run 的模型调用只通过这里原子预留/记录有界用量，不保存原始 prompt/response；external Run 的请求事件、实际 ToolResult、超时中断和 Report 也由同一事务边界拥有。

搜索的 `symbol_fts` 保持文件级 `qualified_name/source_text` 候选索引，以兼容已有不可变快照；`search_symbols_bounded` 先执行完整限定名/后缀、叶子名精确和名称部分三层有界查询，再以小批次读取 FTS 正文候选。正文候选只读取 `symbol_id/path/bm25`，再按 Symbol 的 `start_line/end_line` 从 `snapshot_files` 复核；同一文件在一次查询中只读取、切分和缓存一次。每次搜索最多检查 256 个候选、正文批次为 32、源码最多 32 个文件和 256 KiB UTF-8，并为每条 SQL 安装 SQLite progress handler，Python 循环也共享 0.5 秒预算；预算停止时保留已确认结果并返回 `search budget exceeded: ...`，与确认仍有更多结果的 `max_results reached` 区分。模块 Symbol 保留整文件范围用于导航，函数/类不继承同文件其他 Symbol 的正文命中；同层使用限定名、路径、`symbol_id` 稳定排序，同名定义不合并。该查询修复不修改 FTS 格式、快照 hash、Symbol ID 或历史 Run，不需要重建旧索引。

## 接口与依赖

应用服务通过 [service.py](app/service.py) 的 registry 获取对应 IndexStore；Run executor 只通过同一存储接口写事件、工具结果和报告，[run_sse.py](app/run_sse.py) 只调用有界事件读取。Run 表、run_inputs、run_events、run_tool_results、run_reports 与 sessions 使用 project/session/index 外键和索引，所有读取要求显式作用域。JSON 行在读写边界再次通过 Pydantic Event/ToolResult/Report/Run/RunInput 校验。

## 派生缓存

`definition_cache` 保存文件级定义描述，`relation_cache` 保存导入 alias、作用域绑定/遮蔽、构造赋值与变量失效事件、类继承/装饰器安全标记、有序调用目标和位置等文件关系事实；两者都按 scope、repository、路径、内容 hash、事实规则、Python 版本和配置 hash 隔离。值是有界且严格校验的 JSON，不保存 AST、旧 index/Symbol ID、evidence 或最终跨文件边；关系事实格式升级或解析规则变化会通过版本键安全 miss。当前源码捕获规则为 `source-v3`，支持 `.py`/`.ts`/`.tsx`，规则身份包含锁定 TypeScript `5.9.3` 与 `typescript-facts-v1`；快照与 freshness manifest 使用相同文件范围、预算，并共同排除 `.next`、`.turbo`、`.vercel`、`.vite`、`.parcel-cache`、`coverage`、`out`、`storybook-static`、`target` 及既有依赖/运行数据目录。TypeScript/TSX facts 每次有界冷解析，不写 Python definition/relation cache；关系事实规则/格式为 `python-relation-facts-v2` / `relation-facts-v2`，只控制 Python 事实 payload；关系语义为 `relations-v4`，参与 index snapshot 的 `rules_version` 与 freshness；索引构建身份为 `relations-v6`，用于 immutable index ID 命名空间。两类缓存共用每个 scope/repository 默认 5,000 条、16 MiB 预算；索引成功后分别以独立 IMMEDIATE 事务写入，任一读写失败只导致后续解析，不回滚已提交索引或历史 Run。清理只触碰这两张派生表。

## 数据与生命周期

索引更新还在 ManagementStore 的 `project_updates` 中保留跨数据库提交意图。该表以 `project_id` 唯一约束串行化项目更新，记录 expected latest、owner PID、attempt、候选 immutable index、GitHub source identity 和阶段；服务启动只处理 owner 明确已退出的记录。owner 探测为 `alive`、`exited`、`unknown` 三态：Windows 使用 `OpenProcess(SYNCHRONIZE)` 与 `WaitForSingleObject(..., 0)` 的只读句柄查询并始终关闭句柄，POSIX 才使用平台限定的无信号探测；权限不足、未知错误或查询失败保持 `unknown`，不会触发恢复。应用服务将 expected 贯穿源码检查、下载、构建、历史复用和 `finish_project_index` 的 CAS，候选快照可以先写入但不会绕过 latest 校验。latest CAS 成功后，管理库用一个 `BEGIN IMMEDIATE` 事务写入 source identity、attempt ready/index/hash 并删除意图；跨库中断时，当前 latest 若仍是候选 ready 快照则补提交，否则终止意图且不回滚其他 writer。恢复前的短暂 source/latest 差异由该意图解释，待恢复意图会阻止同项目继续更新。

`index_snapshots` 以 nullable 的 `rules_version`、`configuration_hash` 兼容旧数据库；`http_client_requests` 按 `index_id` 保存经 AST 提取的请求形状、client module、调用行范围和 unknown 原因，不写入 `static_edges`，读取时仍通过同快照 evidence 校验；`definition_cache` 和 `relation_cache` 分别以 scope、repository、路径、源码 hash、对应事实规则、Python AST 版本和配置 hash 做复合键，值为带格式版本的严格 JSON，并记录 UTF-8 字节数。关系事实包含导入 alias、作用域绑定/遮蔽、构造赋值/失效事件、类继承/装饰器安全标记、有序调用目标和位置，不保存 AST、旧 index 或最终跨文件边。`rules_version` 记录当前源码捕获规则 `source-v3`、Python 版本、TypeScript `5.9.3`/facts 规则 `typescript-facts-v1` 与关系语义 `relations-v4` 的组合，`configuration_hash` 同时绑定构建配置与捕获规则；`relations-v6` 是新 immutable index 的构建身份，旧边界 index 不会因源码 hash 相同而被当前 freshness/历史兼容查询选中。`load_snapshot_manifest` 只读取基准快照的路径/大小/hash 和 snapshot issues，不把当前工作树写入数据库。显式更新从应用服务获得 expected latest 后写入新快照；完整安全扫描下旧 metadata 可与源码/规则/配置变化一起触发修复，安全扫描原因不满足时拒绝；`finish_project_index` 在 IMMEDIATE 事务中再次检查 latest，冲突时保留旧关联。两类缓存只在 index 成功提交后以独立 IMMEDIATE 事务写入，并按每个 scope/repository 合计的条目数和字节数清理；缓存事务回滚或读取坏行只影响后续解析，不影响历史 index、Session/Run/evidence。创建 Run 的事务校验 Session 与固定 index，保存完整连续的已保存消息前缀、cutoff、hash 和字符用量；后续追加消息不修改 run_inputs。应用服务在内部 Run 事务前从进程内执行器预留容量，容量失败不进入存储；external Run 不预留内部容量。存储异常或唯一冲突都会释放预留。`runs_active_session` 部分唯一索引限制每个 Session 同时只有一个 queued/running Run，`runs_idempotency` 绑定 project/session/idempotency key 与请求指纹。事件以 `(run_id, sequence)` 连续递增；工具完成事件与结果同事务写入并更新预算，外部 request_id 通过已持久化的开始事件和结果复用；终态事件与报告、Run 终态同事务写入。

模型请求发送前，`reserve_run_model_call` 在事务中检查 running 状态、调用/修复次数和累计输入字符并更新 Run budget；响应返回后，`record_run_model_output` 只累加输出字符和供应商提供的 token，用量缺失时保留未知。取消、终态或超限会拒绝/忽略迟到更新，执行容量仍由 worker 持有到供应商调用返回。取消和服务启动恢复分别原子写入 cancelled/interrupted 事件和空候选终态报告；external 超过创建时的 `max_seconds` 由 scoped `interrupt_run` 原子写入 interrupted 事件和报告，不会扫描或改变其他活动 Run；worker 启动失败会写入 failed 事件与报告；终态再次读取不重复写事件。SSE 通过 `load_run_event_cursor` 读取当前最大 sequence 做未来游标校验，再以 `load_run_events` 分页回放；单次 SQLite 连接只覆盖一次查询，等待客户端期间不持有连接。SQLite 连接按存储操作创建/释放，索引查询、Run worker 和 SSE 读取使用独立连接，不共享线程连接。索引仍是不可变快照，报告/工具 evidence 和影响节点只允许指向同一 `index_id`；`get_neighbors` 同时返回查询预算的 `truncated` 与关系索引扫描覆盖的 `index_truncated`，以及独立的 `index_unresolved_truncated` 详情覆盖字段；影响遍历读取并透传同一组索引覆盖事实，避免把 unresolved 详情截断或空邻接误读为没有关系；影响遍历使用深度、节点、边、路径、unresolved、时间和输出字节上限，简单路径去重并保留循环闭合静态边。

## 错误与限制

项目更新的持久化准入冲突返回 conflict；下载/构建失败或 latest CAS 冲突不会改写旧 source identity/latest。管理库提交失败不会被当作成功，意图会释放 owner 并等待下一次服务启动按候选 ready latest 恢复或终止；清理状态单独保留，清理失败不撤销已经提交的 latest。两套 SQLite 没有跨库原子事务，因此恢复前可能存在可解释的短暂可见差异，但不会通过回滚覆盖其他请求。owner 无法确认时不会把 active attempt 标为 interrupted，意图继续作为项目级 conflict 保护状态。

Run 持久化限制工具调用、模型调用/修复、单次/总源码行、输入/输出字符、模型输入/输出字符、事件和时间预算；external Run 不走内部并发槽位，但仍受相同工具、源码、输出、事件和创建时间预算。定义描述和关系事实缓存默认每个 scope/repository 合计最多 5,000 条和 16 MiB，单行 UTF-8 字节数、格式版本、路径、行号和事实形状均复核；坏行按未命中，写入/清理失败不回滚已提交 index。新鲜度检查还受文件数、总字节和时间预算约束；扫描完整且仅有旧 metadata 与源码/规则/配置变化时允许显式重建，读取错误、来源不可用、路径/符号链接异常、TOCTOU 或预算原因仍返回未知且不覆盖 latest。变化路径列表另有 100 条展示上限，不能把路径列表截断误读为扫描不完整。关系扫描达到时间或静态边预算时才设置 `index_truncated`；unresolved 达到详情上限则只设置 `index_unresolved_truncated` 并继续扫描静态边。旧索引的 unresolved 列为 NULL，查询透传为未知；邻接查询自身的 `truncated`/`truncation_reason` 只表示本次节点、边、时间或输出边界。事件页最多 100 条、工具结果页最多 40 条、Run 历史最多 40 条，游标绑定 project/session/run。影响查询默认/最大深度为 2、节点最多 40、边最多 80、路径和 unresolved 最多 40，且有时间与输出字节边界；root 不计入 reachable_node_count，重复节点和多路径去重，循环不展开为重复符号路径。唯一约束冲突映射为 conflict，坏 scope 映射为 not_found，输入或预算违反映射为 invalid_input/budget_exceeded；执行器容量满在落库前映射为可重试的 budget_exceeded/429；终态后写工具结果、事件和模型用量会被拒绝或忽略。SSE 连接/单 Run 订阅、批次、事件字节、心跳和生命周期也有独立上限，游标未来或跨 Run 不进入流；来源目录只读，索引和服务数据目录由 registry 边界控制。

## 验证

`apps/api/tests/test_storage.py`、`apps/api/tests/test_definition_cache.py` 与 `apps/api/tests/test_typescript_indexing.py` 验证 SQLite 重开、事务、项目隔离、定义/关系事实缓存严格校验、冷/热复用、共享容量/并发和失败降级、旧快照、manifest、latest 冲突、邻接和影响路径边界；`apps/api/tests/test_process_liveness.py` 验证 Windows 只读进程句柄的 alive/exited/unknown 语义、句柄释放和 unknown 恢复保护；`apps/api/tests/test_github_process_lifecycle_script.py` 验证受控验收脚本的 release 超时、阶段报告、默认单场景和 worker 回收/日志保留；`apps/api/tests/test_freshness.py` 验证内容 hash、预算未知、显式复用/重建和旧 metadata，并验证关系语义升级时明确返回 rules/config 变化、生成 `relations-v6-rebuild` 且重复更新复用新快照，并覆盖 runtime 目录变化后的旧快照保留；其它测试继续覆盖 Run、SSE、API 双入口和 MCP。双进程验收脚本为 `scripts/verify-github-process-lifecycle.py`，默认只运行 `http-a-mcp-b`，`--scenario all` 才显式运行全部离线场景；每个场景具有父进程总超时、worker release 超时、受控失败日志和只针对脚本创建 worker 的安全清理。实际进程场景验收见相关决策；真实供应商语义质量、浏览器和公开 GitHub 仍未验证。

## 相关决策

- [GitHub 更新的跨进程提交一致性](../../docs/decisions/2026-09-16-github-update-commit-consistency.md)
- [Run 执行决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [Run 影响查询决策](../../docs/decisions/2026-09-13-run-impact.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [文件级定义提取缓存](../../docs/decisions/2026-09-15-definition-extraction-cache.md)
- [文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)
