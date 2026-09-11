# Web 工作台

## 职责与边界

`apps/web` 提供项目列表、项目切换、索引控制和代码关系/源码证据工作台。它只通过公开 HTTP API 读取项目与索引，不直接访问 SQLite 或宿主路径；Issue 分析、Run、Agent、SSE 和功能概览尚未实现。

## 入口与目录导航

- [app/page.tsx](app/page.tsx)：挂载工作台页面。
- [app/workbench.tsx](app/workbench.tsx)：项目选择、索引生命周期、搜索、关系图和源码侧栏交互。
- [lib/api.ts](lib/api.ts)：HTTP 请求、Project/Index/Graph 响应运行时校验。
- [lib/graph.ts](lib/graph.ts)：局部图合并、分组和 unresolved 选择逻辑。
- [app/globals.css](app/globals.css)：工作台全局视觉样式。

## 接口与依赖

前端调用 `/api/projects` 创建/列出项目，调用项目作用域的索引、搜索、邻接和源码端点；旧索引 API 仍由后端兼容但不是工作台主入口。React Flow 版本固定为 `@xyflow/react@12.11.6`，Next.js 相关规则见 [AGENTS.md](AGENTS.md)。

## 数据与生命周期

当前项目由 `project_id` 选择，项目内索引由 `index_id` 选择。切换项目会取消在途请求、清除旧图/源码/错误并载入该项目最新可用索引；浏览器 localStorage 仅保存最近项目 ID，实际数据始终从服务端恢复。旧响应通过 AbortController 和请求序号忽略。

MCP `read_source` 返回的工作台引用使用 `project_id`、`index_id`、路径、行范围、`evidence_id` 和 `content_hash` 查询参数。工作台打开引用时先验证项目和索引归属，再从固定快照读取源码，并拒绝返回证据与链接不一致的响应；不依赖全局当前项目。

## 使用与配置

后端启动并配置 `CODETRAIL_REPOSITORIES` 后，从仓库根目录执行：

```powershell
pnpm web:dev
```

打开 `http://localhost:3000`，创建项目时填写服务端 `repository_id`，然后在项目内创建或打开索引。示例仓库 ID 为 `timeout-demo`。

## 错误与限制

项目为空、索引未建立、API 离线、项目不存在或索引不属于当前项目时显示明确状态。图只渲染当前索引返回的最多 40 个静态节点；动态调用显示为 unresolved，不绘制确定边。浏览器不能注册任意本地目录。

## 验证

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`，并用浏览器验证两个项目创建、分别建立索引、切换、刷新恢复及同名源码隔离。

## 关联文档

- [工作台子系统说明](../../docs/subsystems/workbench.md)
- [API 子系统说明](../../docs/subsystems/api.md)
- [工作区与项目概览决策](../../docs/decisions/2026-09-11-project-workspace.md)
