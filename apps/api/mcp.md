# 本地 MCP 配置

`python -m app.mcp_server` 启动 MCP SDK `2.2.0` 的本地 stdio 服务；stdout 只承载协议帧，日志写 stderr。服务复用 WorkspaceService，不直接访问 SQLite，不创建索引，不执行仓库代码。

默认只读工具包括项目、索引、`check_index_freshness`、Session/消息、概览、功能历史/版本/图，以及 `list_session_runs`、`get_session_run`、`list_run_events`、`list_run_tool_results`、`get_run_report`、`get_run_impact`。新鲜度检查按当前本地源码内容 hash 做有界只读核对，不创建索引；Run 读取和影响查询只返回持久化报告及其固定快照上的静态结果，不重新执行分析。

设置 `CODETRAIL_MCP_INDEX_WRITE_ENABLED=1` 后，独立增加 `refresh_index`；它只允许对项目当前 latest 做有界复核，在完整安全扫描下对旧 metadata/源码/规则/配置变化执行显式当前复用、兼容历史快照复用或重建，失败保留旧 latest。GitHub 导入开关同时开启时，MCP 另注册 `refresh_github_source`；它只允许对当前 latest 的 GitHub 项目显式检查并通过受限官方归档更新，复用/重建和失败保留语义与 HTTP 相同。设置 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 后，独立增加 `start_session_run` 和 `cancel_session_run`；它允许显式选择 fixture 或 real，但 real 还要求 `CODETRAIL_MCP_REAL_RUN_ENABLED=1`，不会因为 Run 写开关开放 `create_session` 或 `append_session_message`。Session 写入仍需 `CODETRAIL_MCP_SESSION_WRITE_ENABLED=1`。未设置时 MCP 保持只读工具，默认只读工具不会调用模型或 Run。

设置 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1` 后，独立增加 `start_external_run`、`run_search_code`、`run_read_source`、`run_get_neighbors`、`submit_external_run` 和 `cancel_external_run`，供 Codex 作为外部 Agent 驱动一次调查。start 只冻结已有 Session 的消息和固定 index，不占内部 Run worker；可选 `analysis_kind=understanding` 将 Run 标记为代码流程理解；每次工具调用必须带 Run 作用域和 request_id，服务端保存实际返回的结果，重复 request_id 只复用相同参数的已保存结果。submit 接受 `candidate_found`、`insufficient_evidence` 或 `explanation`；explanation 必须携带有序步骤，所有 evidence、symbol 和 Issue 字段都必须遵守该 Run 的实际工具结果与分析类型边界，且 explanation 不产生 candidate link。报告带有公开限制，超时、取消和终态写入均不可伪造或重复。工作台通过 Session Run history 自动发现该 Run，并用同一 Run 的 SSE 展示公开事件。

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
