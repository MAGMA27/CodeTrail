# Web 工作台

## 职责与边界

`apps/web` 提供项目列表、项目切换、本地路径/GitHub 导入、索引控制、项目知识页、保存功能版本历史、同快照功能图、显式主要功能语义生成、代码关系和源码证据工作台。它只通过公开 HTTP API 访问项目与索引，不直接访问 SQLite、GitHub 或宿主路径；Issue 分析、Run、Agent 和 SSE 尚未实现。

## 入口与目录导航

- [app/page.tsx](app/page.tsx)：挂载工作台页面。
- [app/workbench.tsx](app/workbench.tsx)：项目选择、索引生命周期、知识页/历史/功能图/代码关系切换、搜索和源码侧栏交互。
- [lib/api.ts](lib/api.ts)：HTTP 请求、Project/Index/Overview/Feature/Graph 响应运行时校验。
- [lib/graph.ts](lib/graph.ts)：局部图合并、分组和 unresolved 选择逻辑。
- [app/globals.css](app/globals.css)：工作台全局视觉样式。

## 接口与依赖

前端调用 `/api/projects` 创建/列出项目，调用 `/api/imports/local` 提交服务端可见的本地目录，或调用 `/api/imports/github` 提交标准 GitHub HTTPS 仓库地址与可选 ref，再通过 attempt 返回导入结果和 resolved commit；随后调用项目作用域的索引、概览、搜索、邻接和源码端点。旧索引 API 仍由后端兼容但不是工作台主入口。React Flow 版本固定为 `@xyflow/react@12.11.6`，Next.js 相关规则见 [AGENTS.md](AGENTS.md)。

## 数据与生命周期

当前项目由 `project_id` 选择，项目内索引由 `index_id` 选择。切换项目会取消在途请求、清除旧图/源码/错误并载入该项目最新可用索引；浏览器 localStorage 保存最近项目 ID，以及按 project_id/index_id 分隔的明确 generation_id，刷新时优先恢复该版本；没有明确选择时取当前索引最新 succeeded/partial，失败版本仍可从历史查看但不会替换旧成功结果。历史通过稳定 cursor 分页，功能图按需从保存结果和同一不可变快照组装，不另存一份图。GitHub 表单显示固定 commit、snapshot hash 与临时清理失败状态，失败/中断可重试，清理失败可单独重试。旧响应通过 AbortController 和请求序号忽略。

MCP `read_source`、`get_overview` 和功能读取工具返回的工作台引用使用 `project_id`、`index_id`、路径、行范围、`evidence_id` 和 `content_hash` 查询参数。工作台打开引用时先验证项目和索引归属，再从固定快照读取源码，并拒绝返回证据与链接不一致的响应；不依赖全局当前项目。打开索引后默认载入 `python-entry-v1` 概览和当前快照的功能历史，可在“知识页/功能图/代码关系”之间切换；历史卡片显示版本身份、模式、状态、时间、用量和限制，入口/功能/代码节点可打开同一 `index_id` 的源码证据。O2 功能生成不会自动触发，用户显式选择 `fixture/real` 模式和 `entry_reachable/visible_modules` 范围后才请求；结果展示功能、入口/模块/符号关联、证据钻取、fixture 标识、预算用量、未查询模块和局限，已保存结果、历史和功能图读取不调用模型。导入状态在表单中显示，失败或重启中断的 attempt 可从同一入口重试。

## 使用与配置

后端启动并配置 `CODETRAIL_REPOSITORIES`、`CODETRAIL_ALLOWED_IMPORT_ROOTS`、`CODETRAIL_DATA_DIR` 和 `CODETRAIL_GITHUB_IMPORT_ENABLED=1` 后，从仓库根目录执行：

```powershell
pnpm web:dev
```

打开 `http://localhost:3000`，可填写服务端 `repository_id` 创建兼容项目，也可在“导入本地目录”表单中填写 API 服务端可见的绝对路径，或在 GitHub 表单中输入 `https://github.com/owner/repository` 和可选分支/tag/commit。路径不是浏览器本机路径；服务端校验边界后在管理目录建立索引。示例本地仓库 ID 为 `timeout-demo`。

## 错误与限制

项目为空、本地/GitHub 导入失败/中断、索引未建立、API 离线、项目不存在或索引不属于当前项目时显示明确状态。概览只使用当前固定快照，入口规则首轮覆盖 `__main__.py` 和顶层 main guard；未识别入口不等于项目没有入口，动态调用仍显示为 unresolved。功能生成只接受同一项目/索引快照的 evidence 和符号，显示 `succeeded/partial/insufficient_evidence/failed`，失败可重试且不会覆盖已有成功结果；切换项目或索引会取消/忽略旧请求。历史最多按页读取 40 个版本，功能图最多展示 40 个节点/120 条边，semantic 与 static 来源分别标识，规则版本缺失映射和截断/限制明确显示；运行时校验拒绝跨项目、跨索引和错误 evidence 引用。概览和生成上下文、模型请求、功能数和关系图都有上限。浏览器只提交路径或 GitHub 仓库身份字符串，不能绕过服务端允许根目录、官方重定向、archive 安全和 loopback 写入边界。

## 验证

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`，并用浏览器验证本地目录/GitHub 导入、固定 commit 显示、项目知识页自动载入、历史版本选择/分页/刷新恢复、显式 fixture 功能生成、功能图 semantic/static 图例与节点证据钻取、real 未配置失败/重试、入口证据与代码关系联动、导入失败/重试、项目切换及同名源码隔离。

## 关联文档

- [工作台子系统说明](../../docs/subsystems/workbench.md)
- [API 子系统说明](../../docs/subsystems/api.md)
- [工作区与项目概览决策](../../docs/decisions/2026-09-11-project-workspace.md)
