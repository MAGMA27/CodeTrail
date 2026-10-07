# CodeTrail MVP 操作说明

本说明用于在本地启动 CodeTrail 工作台，并让当前 Codex 主机通过 stdio MCP 驱动一次可恢复的 external Run。

## 启动 API 与工作台

本地联合演示使用仓库根目录下被 Git 忽略的 `.codetrail-runtime` 作为服务管理目录（当前路径为 `D:/Dev/CodeTrail/.codetrail-runtime`）。该目录中的已注册 managed source、Project、Index、Session 和 Run 会被 API 与 Codex MCP 共同读取；启动脚本不会创建或清空数据，也不需要在当前 PowerShell 会话中手工设置临时环境变量。

从仓库根目录启动 API：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service api
```

另开 PowerShell 启动工作台：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service web
```

若 API 已经占用 `8000`，先在原 API 终端按 `Ctrl+C` 停止，再只启动一个 API 实例。启动后先检查 `http://127.0.0.1:8000/api/health`；工作台打开后应能发现名为 `CodeTrail 自用验收` 的项目及其 ready 索引。

## 从 HTTP 入口理解项目

选择一个 ready index 后，工作台会在项目概览的“入口路径”中列出可静态确认的 FastAPI 路由。`HTTP` 行显示方法、声明路径和处理函数；点击入口打开装饰器到函数结束的同快照源码证据，点击“关系图”则以处理函数为根进入既有静态关系图。MCP 的 `get_overview` 返回同一 `project_id/index_id` 下的入口和 evidence 引用，可用 `/api/health` 或项目概览路由核对行号与源码 hash。

这里的路径是装饰器声明值，不是解析过 router 或部署前缀后的最终 URL。当前规则只扫描模块/函数体的直接语句，不展开控制流块或类体；也不识别 `APIRouter`、`include_router`、`mount`、动态路径和运行时注册。未识别路由不代表项目没有 HTTP 入口。

## 从结构总览进入源码

项目知识页在当前固定索引中显示职责、HTTP 路径和源码目录容器。职责来自 `codetrail-subsystems.json` manifest，不是模型推断；职责覆盖不完整时，未归属模块仍按源码目录显示。容器用于浏览索引结构，不代表运行时业务边界。

展开容器可继续进入模块；模块与入口都可钻取到同一固定索引上的源码或局部静态关系图。画布中的 `imports`/`calls` 保留 `source → target` 方向；HTTP 客户端与 FastAPI 路由按 method/path 形状单独关联，不混入静态调用边。形状匹配不证明请求实际发往本地服务。`可见/总量` 与告警会说明覆盖和预算边界；`unresolved` 始终表示未知，不能解读为无依赖。概览可能受到 `max_paths`、`max_unresolved` 和模块关系边样本预算限制。

MCP `get_overview` 使用显式 `project_id`/`index_id` 和有界结果，可核对职责状态、容器、入口、HTTP 形状关联、模块关系与固定索引。HTTP、浏览器与本地 stdio MCP 读取同一快照事实，不会把结构结果写回静态 `CodeEdge`。

## 加载 Codex MCP

当前 Codex 主机的 `codetrail` stdio 配置应使用与 API 相同的代码目录、`.codetrail-runtime` 和 `CODETRAIL_MCP_EXTERNAL_RUN_ENABLED=1`；配置示例和完整字段见 [API README](../apps/api/README.md)。索引刷新和 Session/Run 持久化由共享 runtime 提供，数据变化通常不需要重载 MCP。只有 MCP 服务代码或工具契约更新后，才按宿主支持的方式 reload 并重新核对 `list_projects`、`list_indexes`、`get_overview`。不要清空 runtime 或手工编造项目/索引 ID。external Run 不需要真实模型供应商密钥。

## 进行调查

先用 API 的 `/api/projects`、`/api/projects/{project_id}/indexes` 或 MCP 的 `list_projects`、`list_indexes` 发现目标项目和 ready index。若 freshness 检查报告源码、规则或配置已变化，对现有 index 调用 `POST /api/projects/{project_id}/indexes/{index_id}/refresh` 创建新的固定快照；确认新 index ready，并保留旧 index 及其 Session。项目知识页可浏览最新索引，但旧 Session 始终固定在创建时的 index。

打开 `http://localhost:3000`，选择目标项目，在该项目下新建一个独立 Session，并确认 Session 标题区显示新快照的 revision/hash。不要复用旧 Session 来证明新快照行为；切换项目后回到目标项目时，也要确认选中的 `project_id` 和 Session。

在 Codex 中只描述现象，不预先提供候选文件、函数或行号，例如：“批量评测时供应商返回明确 token 用量，但费用高于请求前预留时最终累计费用可能仍停留在预留值，请检查是否存在导致低估的逻辑，给出源码证据、触发条件与可能影响；证据不足时明确说明。”

先在工作台打开这个新 Session，让其 Run history 已加载；保存问题消息后，由 Codex 主机 MCP 用该 Session 返回的固定 `index_id` 与 `session_id` 显式调用 `start_external_run`。随后使用同一 Run 的 `run_search_code`、`run_read_source`、`run_get_neighbors` 和 `submit_external_run`。若外部调查预计超过 Run 默认总时限，可显式设置合法、有界的 `max_seconds`；这不会增加单次搜索、输出或工具预算，也不会改变默认值。工作台不创建 external Run，而是自动发现并实时展示当前 Session 的事件与工具结果。

验收新快照时应启动一条新的 external Run，避免用旧报告代替当前快照的执行证据。Run 会冻结这条 Session 的已保存消息与固定 index；现有历史报告仍绑定原 Session/index，不会被新 Run 覆盖。

## 查看结果

在工作台 Run history 打开完成的 `CODEX / 外部 Agent` Run，核对公开事件、实际工具结果、报告、证据和限制都绑定同一个 project/session/index。通过证据按钮查看源码与 hash；候选报告可以打开固定快照上的有界静态影响范围，并保留 unresolved 边界。切到另一个项目或旧 Session 再切回来、刷新页面后，应恢复原 Session 与 Run，不创建重复 Run。

## 边界

external Run 只执行白名单代码搜索、源码读取和静态邻接查询，不执行来源仓库代码、shell 或测试；报告不展示隐藏思维链。真实供应商调用、DeepSeek 语义质量和实际账单仍需单独授权与评测，不因本地 external 闭环通过而宣称已验证。

## 停止后复现

1. 只在自己启动的 API 与 Web 终端按 `Ctrl+C`，等待两个服务退出；不要终止其他服务或宿主 MCP 进程。
2. 从仓库根目录重新执行 `pwsh -File .\scripts\start-local.ps1 -Service api` 和 `pwsh -File .\scripts\start-local.ps1 -Service web`。
3. 用 `GET http://127.0.0.1:8000/api/health` 检查 API；刷新工作台并选择目标项目。
4. 确认新 Session 仍绑定刷新后创建的 ready index，旧 Session 仍绑定原 index，Run history、报告、事件、工具结果与 evidence 均可恢复。数据持久化验收不需要清空 runtime 或重载 MCP。

## 已知 Issue 闭环复测记录

2026-10-07 使用当前宿主 MCP 与本地 API/Web 共享的 `.codetrail-runtime`，对 pluggy 已知 Issue #733 的修复前固定快照完成一次 external Run。当前宿主工具实际返回新版架构分类；Run 报告、HTTP 读取、实时 UI、源码 evidence、bounded impact 与刷新恢复绑定同一个 Session/index。该结果只证明已知案例和修复前证据链，不是盲测，也不证明修复后行为或一般调查质量。没有运行来源代码或测试。CodeTrail 自用大索引的 API `max_modules=40` 请求返回 40/96 个模块；该服务端请求不验证 Web 画布自己的 40 模块投影上限。实际分类 excerpt 低于 40,000 字符，因此 UI 投影上限与字符预算触发边界仍待验证；本轮整体容量验收保持 partial。
