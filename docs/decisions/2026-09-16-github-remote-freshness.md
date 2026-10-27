# 决策：GitHub 远端 ref 新鲜度与显式更新

Status: implemented

## 问题

GitHub 来源的临时归档在导入成功后会被清理，不能把已清理目录当作 freshness 的源码来源；仅显示 `remote_not_checked` 又无法让用户知道跟踪的 branch/tag 是否已移动。检查需要可追溯到项目、基准 index、请求 ref、解析 ref、远端 commit 和检查时间，并且不能在只读检查时下载或创建新 index。

## 决定

1. `GitHubClient.resolve` 是检查和导入共用的 ref→commit 入口。GitHub freshness、导入、重试和 refresh 共用 20 秒累计限流等待预算，只有 429/限流型 403 的重试休眠扣减预算；正常 metadata 请求、归档下载和解包耗时不计入；索引独立受默认 240 秒 `CODETRAIL_INDEX_TIMEOUT` 限制。每次 GitHub API 请求有独立 20 秒超时，归档下载另有 30 秒空闲读超时和 300 秒总超时。metadata-only freshness 只调用 `resolve`，不调用归档下载、不访问临时目录。
2. `IndexFreshness` 增加 `remote_status` 与远端 URL、请求 ref、解析 ref、commit 字段。`consistent`/`changed` 表示远端身份已确认，`unknown` 表示元数据缺失、远端不可用或预算失败；远端 commit 与基准 index 一致不代表当前规则/配置一致，二者分别计算。旧默认分支记录缺少有效解析 ref 时不猜测，保持 unknown。
3. GitHub 项目使用独立的 `POST /api/projects/{project_id}/indexes/{index_id}/github-refresh`。它只允许项目当前 ready latest，先做一次 metadata 检查，再通过受限导入流程实际解析、下载一个固定 commit 的官方归档并建立或复用 immutable index；metadata 检查与后续解析/下载共享上述 20 秒限流等待预算，而下载另受空闲/总超时限制。刷新下载或索引超时通过 HTTP 返回 `download_timeout`/`index_timeout` 的 504；普通导入或固定 commit 重试会在正常导入响应中记录对应错误码的失败 attempt。MCP 的 `refresh_github_source` 复用同一 WorkspaceService，只有 GitHub 导入和 MCP GitHub 导入开关同时开启时注册；本地 `refresh_index` 开关不隐式开放远端下载。
4. 显式更新以实际导入 attempt 的 commit/ref 为最终事实，以防 ref 在检查与下载之间移动。相同 commit 且 schema/rules/config/content metadata 兼容时复用当前或同项目历史 index；规则/配置过期或历史 index 不兼容时重新构建。失败、未知远端或基准冲突不替换旧 latest，也不改写旧 Session/Run/evidence；历史来源 metadata 保持原样。
5. 更新的 latest CAS、来源 identity 和 attempt 收尾遵循独立的持久化项目更新意图；expected latest 贯穿复核、构建和 `finish_project_index`。latest 提交后若管理库事务尚未完成，服务重启按候选快照和阶段补提交或终止，不回滚其他请求的 latest。跨数据库一致性取舍与恢复状态见 [GitHub 更新的跨进程提交一致性](2026-09-16-github-update-commit-consistency.md)。

## 替代方案

- 继续返回 `remote_not_checked`：无法发现跟踪 ref 的移动，且把可检查的远端状态与未知状态混在一起。
- freshness 时下载归档：把只读检查变成昂贵写路径，并重新引入临时目录、解包和清理风险。
- 直接把检查时的 commit 写入 source：检查失败或后续导入失败会污染旧来源身份，且无法处理检查与下载之间的 ref 移动。
- 使用无持久化的跨进程锁：服务重启后没有办法判断候选快照是否已经成为 latest，也不能安全恢复来源和 attempt 的提交。

## 验收与证据

- 离线 GitHub client 覆盖默认分支、显式 ref、固定 SHA、限流、每请求超时和独立下载空闲/总超时；freshness、resolve 与下载共享累计限流等待预算，refresh 服务复用同一 budget。Freshness 的 changed/consistent/unknown 与本地规则/配置状态分别可见，独立检查不增加下载次数或 index。GitHub refresh 的下载 timeout HTTP 504 行为由 [GitHub 导入决策](2026-09-11-github-import.md) 中的当前验收证据覆盖；导入/重试则以 failed attempt 返回。
- HTTP 显式更新覆盖同一 commit 复用、远端移动新建、历史 commit 复用、失败保留 latest/source identity，以及项目归属/latest 冲突。
- MCP 发现只在 GitHub 开关开启时增加 `refresh_github_source`，其结果契约与 HTTP 共用 `IndexRefreshResponse`；前端显示 URL、请求/解析 ref、远端 commit、基线 index commit，并在 unknown 时禁止更新。
- 通过后端全量 pytest、compileall、前端测试/typecheck/build、Schema 导出与 contracts/docs 检查；真实供应商语义质量仍不在本步验收范围。

## 后果

正常 freshness 仍会产生一次受预算约束的 GitHub API metadata 请求，不能离线判断远端状态；远端限流、超时和元数据缺失必须由用户重试或重新导入处理。GitHub 的显式更新与本地更新保持不同入口，但都在工作台同一“源代码与索引”面板中呈现，并共享项目归属、latest、不可变快照和历史证据边界。跨数据库提交存在可恢复的短暂 latest/source 可见窗口；项目更新意图会在服务启动时按阶段收敛，未完成恢复时同项目更新返回 conflict。
