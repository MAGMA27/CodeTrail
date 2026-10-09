# 本地 MCP 配置

`get_neighbors` 与 `run_get_neighbors` 独立返回构造上下文 `contextual_calls`：绑定实际返回的激活 calls 边，条件目标不增加普通 nodes/static_edges。记录可能带逃逸后改写未知标记，不能视为确定调用或已确认影响；查询与静态边共用时间、最多 200 条关系及输出预算。旧快照返回空列表，精确语义见 [索引说明](indexing.md) 与 [条件调用决策](../../docs/decisions/2026-10-07-python-contextual-instance-calls.md)。

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务；stdout 只承载协议帧，日志写 stderr。服务复用 WorkspaceService，不直接访问 SQLite，不创建索引，不执行仓库代码。

默认只读工具包括项目、索引、`check_index_freshness`、Session/消息、`get_overview`、`list_module_symbols`、`get_neighbors`、`read_source`、功能历史/版本/图，以及 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`、`get_run_impact`。新鲜度检查按当前本地源码内容 hash 做有界只读核对，不创建索引；模块列表只分页读取固定快照中指定 module path 的 class/function/test definitions；Run 读取和影响查询只返回持久化报告及其固定快照上的静态结果，不重新执行分析。`generate_project_features` 不注册为 MCP 工具；内置 HTTP 功能生成仍可按既有服务端模型配置运行。

`list_module_symbols(project_id,index_id,module_id,limit=40,cursor)` 与 HTTP 的 `/api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols` 使用相同参数、cursor 和 response page。页最多 40 项，HTTP/MCP 共用 40,000 字节序列化预算；跨页传回原 cursor，跨 Project/Index/Module 的 cursor 返回 `invalid_input`，module 不存在或 symbol 不是 module root 返回 `not_found`。该工具只描述 indexer 在固定 snapshot 捕获到的定义，不保证解析完整；详情见[模块定义分页决策](../../docs/decisions/2026-10-06-module-symbol-pagination.md)。

HTTP 入口链路没有额外 MCP 工具：外部 Agent 从 `get_overview` 读取同一 `project_id/index_id` 的 HTTP association 与路由入口，再将入口的 handler symbol 传给 `get_neighbors(direction="callees", depth=2, limit=40, edge_kinds=["calls"])`，并可用 `read_source` 读取关联两端或返回静态边的同快照源码。`overview.paths` 默认 40 条是整个概览共享的上限，不是 handler 独立覆盖；因此要判断返回观察，必须直接查询 handler。MCP 和 HTTP 调用相同 WorkspaceService，保持相同节点、边、evidence 与截断字段。查询使用现有邻接默认预算：最多 200 条边、1 秒、64,000 输出字节；空邻接与 `unresolved`/索引覆盖字段均不代表静态执行链完整。

设置 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1` 后，独立增加 `refresh_index`；它只允许对项目当前 latest 做有界复核，在完整安全扫描下对旧 metadata/源码/规则/配置变化执行显式当前复用、兼容历史快照复用或重建，失败保留旧 latest。GitHub 导入开关同时开启时，MCP 另注册 `refresh_github_source`；它只允许对当前 latest 的 GitHub 项目显式检查并通过受限官方归档更新，复用/重建和失败保留语义与 HTTP 相同。设置 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 后，独立增加 `start_session_run` 和 `cancel_session_run`；它允许显式选择 fixture 或 real，但 real 还要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会因为 Run 写开关开放 `create_session` 或 `append_session_message`。Session 写入仍需 `CODETRAIL_MCP_SESSION_WRITE_ENABLED=1`。未设置时 MCP 保持只读工具，默认只读工具不会调用模型或 Run。

设置 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 后，独立增加 `start_external_run`、`run_search_code`、`run_read_source`、`run_get_neighbors`、`submit_external_run`、`submit_project_features` 和 `cancel_external_run`，供 Codex 作为外部 Agent 驱动一次调查或功能识别。start 只冻结已有 Session 的消息和固定 index，不占内部 Run worker；默认 `analysis_kind=issue`，可设为 `understanding` 将 Run 标记为代码流程理解；每次工具调用必须带 Run 作用域和 request_id，服务端保存实际返回的结果，重复 request_id 只复用相同参数的已保存结果。

`submit_external_run` 的结论形状由 start 时选择的 `analysis_kind` 决定：`issue` Run 只能提交 `candidate_found` 或 `insufficient_evidence`，并且不能带 `explanation`；`understanding` Run 只能提交 `explanation`，不能带 Issue 的 `findings` 或 `candidate_links`。`candidate_found` 的 `candidate_links` 至少需要一个条目，`insufficient_evidence` 不允许 `candidate_links`。Issue 结论顶层 `evidence_ids` 必须包含所有 findings 与 candidate links 引用的 evidence ID；explanation 的顶层 `evidence_ids` 则必须与其有序步骤引用的 ID 集合完全相同。所有 evidence、symbol、candidate 和 index 引用必须来自本 Run 实际返回的工具结果，服务端会校验这些边界。报告带有公开限制，超时、取消和终态写入均不可伪造或重复。工作台通过 Session Run history 自动发现该 Run，并用同一 Run 的 SSE 展示公开事件。

`submit_project_features` 只接受同项目、Session、固定 index 的活动 `understanding` external Run。宿主 Agent 自行识别功能并提交 `FeatureModelOutput`；服务端从该 Run 已保存的 `run_read_source` 结果重建 evidence，再复读固定快照校验路径、行范围和 hash。入口、模块与符号也必须由同一快照概览、已读路径或 Run 的 search/neighbors 结果支持；客户端不能只提交自称读过的 evidence，也不能创建静态调用边。该提交须在 `submit_external_run` 结束 Run 前完成；同一幂等键和内容复用既有功能版本，不同内容冲突。它复用现有 FeatureGeneration 持久化、HTTP/MCP 历史、版本和功能图读取，记录 `source=external`、`source_scope=external_run`、宿主自报模型名及服务端模型调用数 `0`；宿主 token 与费用不进入服务端统计。此能力只随 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 开放，不再有独立的模型调用开关，也不要求服务端模型密钥。

CLI 安装并初始化后，执行 `codetrail install` 注册 Codex；`codetrail install --print-config` 可先预览。已有注册保留，需要更新时使用 `codetrail install --replace`。注册后重连或重启宿主加载。源码开发环境可用 scripts/setup-mcp.ps1 包装安装与注册步骤。

```toml
[mcp_servers.codetrail]
command = "codetrail"
args = ["mcp"]
```

API (`codetrail api`) 与 MCP (`codetrail mcp`) 都读取 `~/.config/codetrail/settings.json`，不把业务配置复制进 Codex。配置包含 `data_dir`、`allowed_import_roots`、`workbench_url`、`web_origins`、`github_import_enabled`、`external_run_enabled` 和可选 `typescript_compiler`。路径必须是绝对路径，导入根目录必须存在；未识别字段拒绝启动。环境变量优先。自定义文件使用 `--config` 或 `CODETRAIL_CONFIG`；`codetrail --config C:/path/settings.json install` 注册时保存同一文件位置。修改配置后重启 API/MCP，已运行进程不会热加载。

`codetrail install --print-config` 只预览注册内容；`status` 显示配置位置和宿主注册，`uninstall` 只移除 codetrail 注册，不删除项目数据或用户配置。安装后的包包含 stdio 服务和 TypeScript parser，但不包含 TypeScript 编译器。创建 settings 时，`codetrail init` 从 CodeTrail 源码工作区的 `apps/web/node_modules/typescript` 或根 `node_modules/typescript` 只读检查 package.json，只有版本为 5.9.3 时才记录路径；已有配置不改写。settings 未配置路径时，API/MCP 仅在从同一源码工作区启动时尝试发现；从其他目录启动需配置 `typescript_compiler` 或 `CODETRAIL_TYPESCRIPT_COMPILER`。没有编译器时 Python 可用，TypeScript/TSX 新索引会返回明确解析失败。可用 `--typescript-compiler` 或 `CODETRAIL_TYPESCRIPT_COMPILER` 指向锁定的 typescript.js。开发时 `uv run --directory apps/api codetrail ...` 使用相同入口。

需要通过 MCP 显式更新索引时，再单独加入 `CODETRAIL_MCP_INDEX_WRITE_ENABLED = "1"`；默认不注册 `refresh_index`。修改 MCP 代码后对 `codetrail` 做一次 reload 或重启 Codex，服务端运行目录无需清理。

配置文件由 Codex 宿主加载；修改 MCP 代码后对 `codetrail` 做一次 reload（或按宿主能力重启 Codex）即可。API/MCP 不应各自指向仓库根目录下的默认 `.codetrail`，也不应通过手工项目/索引 ID 绕过列表发现。

内置 HTTP 功能生成和 Run 的 `real` 模式默认适配 DeepSeek，从服务数据目录的 `config.json` 读取配置；外部功能提交由宿主模型负责，不读取该配置，也不调用服务端模型；例如：

工作台通过 `GET/POST /api/model-config` 查看和保存脱敏设置；留空密钥保留当前值，明确移除才清除。API 与 MCP 的新 real 任务每次读取同一文件，因此改模型配置不需重启服务；活动 Run 使用创建时捕获的配置。保存只写配置，不会试连供应商或发送源码。

```json
{
  "provider": "deepseek",
  "api_key": "sk-你的 DeepSeek API Key",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash"
}
```

不要提交真实密钥；配置文件位于被 Git 忽略的服务数据目录。设计与验收见 [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)、[Run 决策](../../docs/decisions/2026-09-12-run-execution.md)、[Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)、[代码理解流程与证据报告决策](../../docs/decisions/2026-09-14-code-understanding-explanation.md)、[本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)、[GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)、[GitHub 更新的跨进程提交一致性](../../docs/decisions/2026-09-16-github-update-commit-consistency.md) 和 [文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)。

调查 Run 的 `max_seconds` 默认与上限均为 600 秒（10 分钟），HTTP 与 MCP 使用相同预算契约，可显式设置 1–600 秒。新默认只作用于新建 Run，已持久化 Run 保留冻结预算；external Run 在后续请求到来时检查总时长，不承诺到期立即强制终止。

## 本地路径配置与故障排查

首次 `codetrail init` 默认允许当前目录。要导入其他项目，编辑 `~/.config/codetrail/settings.json` 的 `allowed_import_roots`，例如 `["D:/Dev"]`，保存后重启 API。既有 settings 不会被 init 参数覆盖；`--allow-root` 和 `--typescript-compiler` 仅用于首次创建设置。

GitHub HTTP 导入默认开启，可通过 settings 的 `github_import_enabled=false` 或环境变量 `CODETRAIL_GITHUB_IMPORT_ENABLED=0` 关闭。可选的服务端 `CODETRAIL_GITHUB_TOKEN` 用于降低未认证请求限流影响；MCP GitHub 写能力仍独立开启。

- 找不到 codetrail：执行 `uv tool update-shell` 并重新打开终端。
- 本地导入被拒绝：核对允许根目录、绝对路径和符号链接边界；修改 settings 后重启 API。
- Codex 看不到项目或网页看不到 Run：核对 API/MCP 使用同一配置文件及 data_dir，并选择同一个项目和 Session；CODETRAIL_DATA_DIR 环境变量优先于文件。
- 没有 external Run 工具：核对 settings 的 external_run_enabled（默认 true），然后重连 MCP；CODETRAIL_MCP_EXTERNAL_RUN_ENABLED 环境变量优先于文件。
- 初始化后仍缺编译器：先安装源码工作区依赖。已有 settings 不会自动补写发现的编译器地址；需要跨目录启动 MCP 时，在 settings 中设置 typescript_compiler。
- 修改源码后行为未更新：执行 `uv tool install --force ./apps/api`，再重启 API/MCP；安装副本不会自动跟随源码变化。

Web 仍从源码运行。Node.js 最低声明为 20.9，但需兼容根 package.json 指定的 pnpm 版本；具体依赖由锁文件维护。根 .env.example 是环境变量参考，CLI 和启动脚本不会自动加载它。
