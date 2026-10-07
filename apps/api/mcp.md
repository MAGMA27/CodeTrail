# 本地 MCP 配置

`get_neighbors` 与 `run_get_neighbors` 独立返回构造上下文 `contextual_calls`：绑定实际返回的激活 calls 边，条件目标不增加普通 nodes/static_edges。记录可能带逃逸后改写未知标记，不能视为确定调用或已确认影响；查询与静态边共用时间、最多 200 条关系及输出预算。旧快照返回空列表，精确语义见 [索引说明](indexing.md) 与 [条件调用决策](../../docs/decisions/2026-10-07-python-contextual-instance-calls.md)。

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务；stdout 只承载协议帧，日志写 stderr。服务复用 WorkspaceService，不直接访问 SQLite，不创建索引，不执行仓库代码。

默认只读工具包括项目、索引、`check_index_freshness`、Session/消息、`get_overview`、`list_module_symbols`、`get_neighbors`、`read_source`、功能历史/版本/图，以及 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`、`get_run_impact`。新鲜度检查按当前本地源码内容 hash 做有界只读核对，不创建索引；模块列表只分页读取固定快照中指定 module path 的 class/function/test definitions；Run 读取和影响查询只返回持久化报告及其固定快照上的静态结果，不重新执行分析。

`list_module_symbols(project_id,index_id,module_id,limit=40,cursor)` 与 HTTP 的 `/api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols` 使用相同参数、cursor 和 response page。页最多 40 项，HTTP/MCP 共用 40,000 字节序列化预算；跨页传回原 cursor，跨 Project/Index/Module 的 cursor 返回 `invalid_input`，module 不存在或 symbol 不是 module root 返回 `not_found`。该工具只描述 indexer 在固定 snapshot 捕获到的定义，不保证解析完整；详情见[模块定义分页决策](../../docs/decisions/2026-10-06-module-symbol-pagination.md)。

HTTP 入口链路没有额外 MCP 工具：外部 Agent 从 `get_overview` 读取同一 `project_id/index_id` 的 HTTP association 与路由入口，再将入口的 handler symbol 传给 `get_neighbors(direction="callees", depth=2, limit=40, edge_kinds=["calls"])`，并可用 `read_source` 读取关联两端或返回静态边的同快照源码。`overview.paths` 默认 40 条是整个概览共享的上限，不是 handler 独立覆盖；因此要判断返回观察，必须直接查询 handler。MCP 和 HTTP 调用相同 WorkspaceService，保持相同节点、边、evidence 与截断字段。查询使用现有邻接默认预算：最多 200 条边、1 秒、64,000 输出字节；空邻接与 `unresolved`/索引覆盖字段均不代表静态执行链完整。

设置 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1` 后，独立增加 `refresh_index`；它只允许对项目当前 latest 做有界复核，在完整安全扫描下对旧 metadata/源码/规则/配置变化执行显式当前复用、兼容历史快照复用或重建，失败保留旧 latest。GitHub 导入开关同时开启时，MCP 另注册 `refresh_github_source`；它只允许对当前 latest 的 GitHub 项目显式检查并通过受限官方归档更新，复用/重建和失败保留语义与 HTTP 相同。设置 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 后，独立增加 `start_session_run` 和 `cancel_session_run`；它允许显式选择 fixture 或 real，但 real 还要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会因为 Run 写开关开放 `create_session` 或 `append_session_message`。Session 写入仍需 `CODETRAIL_MCP_SESSION_WRITE_ENABLED=1`。未设置时 MCP 保持只读工具，默认只读工具不会调用模型或 Run。

设置 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 后，独立增加 `start_external_run`、`run_search_code`、`run_read_source`、`run_get_neighbors`、`submit_external_run` 和 `cancel_external_run`，供 Codex 作为外部 Agent 驱动一次调查。start 只冻结已有 Session 的消息和固定 index，不占内部 Run worker；默认 `analysis_kind=issue`，可设为 `understanding` 将 Run 标记为代码流程理解；每次工具调用必须带 Run 作用域和 request_id，服务端保存实际返回的结果，重复 request_id 只复用相同参数的已保存结果。

`submit_external_run` 的结论形状由 start 时选择的 `analysis_kind` 决定：`issue` Run 只能提交 `candidate_found` 或 `insufficient_evidence`，并且不能带 `explanation`；`understanding` Run 只能提交 `explanation`，不能带 Issue 的 `findings` 或 `candidate_links`。`candidate_found` 的 `candidate_links` 至少需要一个条目，`insufficient_evidence` 不允许 `candidate_links`。Issue 结论顶层 `evidence_ids` 必须包含所有 findings 与 candidate links 引用的 evidence ID；explanation 的顶层 `evidence_ids` 则必须与其有序步骤引用的 ID 集合完全相同。所有 evidence、symbol、candidate 和 index 引用必须来自本 Run 实际返回的工具结果，服务端会校验这些边界。报告带有公开限制，超时、取消和终态写入均不可伪造或重复。工作台通过 Session Run history 自动发现该 Run，并用同一 Run 的 SSE 展示公开事件。

Codex 的本地 stdio 配置示例（Windows）如下；仓库映射必须使用服务数据目录中已存在的 repository ID，`CODETRAIL_DATA_DIR` 需与 API 共用：

```toml
[mcp_servers.codetrail]
command = "uv"
args = ["run", "--directory", "D:/Dev/CodeTrail/apps/api", "python", "-m", "app.mcp_server"]
startup_timeout_sec = 120

[mcp_servers.codetrail.env]
CODETRAIL_DATA_DIR = "D:/Dev/CodeTrail/.codetrail-runtime"
CODETRAIL_REPOSITORIES = "{}" # 使用 CODETRAIL_DATA_DIR 中已注册的 managed source
CODETRAIL_WORKBENCH_URL = "http://localhost:3000"
CODETRAIL_MCP_EXTERNAL_RUN_ENABLED = "1"
```

需要通过 MCP 显式更新索引时，再单独加入 `CODETRAIL_MCP_INDEX_WRITE_ENABLED = "1"`；默认不注册 `refresh_index`。修改 MCP 代码后对 `codetrail` 做一次 reload 或重启 Codex，服务端运行目录无需清理。

配置文件由 Codex 宿主加载；修改 MCP 代码后对 `codetrail` 做一次 reload（或按宿主能力重启 Codex）即可。API/MCP 不应各自指向仓库根目录下的默认 `.codetrail`，也不应通过手工项目/索引 ID 绕过列表发现。

功能生成和 Run 的 `real` 模式默认适配 DeepSeek，从服务数据目录的 `config.json` 读取配置；例如：

```json
{
  "provider": "deepseek",
  "api_key": "sk-你的 DeepSeek API Key",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-v4-flash"
}
```

不要提交真实密钥；配置文件位于被 Git 忽略的服务数据目录。设计与验收见 [统一接口决策](../../docs/decisions/2026-09-11-agent-native-interfaces.md)、[Run 决策](../../docs/decisions/2026-09-12-run-execution.md)、[Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)、[代码理解流程与证据报告决策](../../docs/decisions/2026-09-14-code-understanding-explanation.md)、[本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)、[GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)、[GitHub 更新的跨进程提交一致性](../../docs/decisions/2026-09-16-github-update-commit-consistency.md) 和 [文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)。
