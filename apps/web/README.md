# Web 工作台

## 职责与边界

`apps/web` 提供项目/索引切换、索引新鲜度检查与显式更新、概览、Session/消息、保存功能历史、同快照功能图、源码证据，以及 Session 内显式 AI 分析 Run 的启动、状态、事件、工具结果、Issue/代码理解报告、候选位置与有界静态影响、SSE 订阅和取消；项目架构画布并列展示 Python/TypeScript imports/calls 与独立的 HTTP 方法/路径形状关联及双端证据。HTTP 关联列表可打开客户端锚点→方法/路径形状→FastAPI handler，再从 handler 查看最多 2 层、40 节点的同快照 calls 查询与 unresolved 边界。入口链路可从当前选中符号按一跳追踪调用方或被调用方，默认 calls 并可筛选 imports；模块静态导入视图只查询模块符号的一跳导入边，按名称导入文件内 class/function 的引用不会出现在模块反向结果中，可对具体定义查询 imports，空结果不代表整个文件无人导入。Codex external Run 会被自动发现并接入同一展示链。它只通过公开 HTTP API 访问服务端数据，不直接访问 SQLite、GitHub、宿主路径或模型。

## 入口与目录导航

- [workbench.md](workbench.md)：当前实现、生命周期、限制与验收入口。

- [app/page.tsx](app/page.tsx)：挂载生产工作台。
- [app/workbench.tsx](app/workbench.tsx)：项目、索引、Session、Run、概览和证据请求编排。
- [app/workbench-ui.tsx](app/workbench-ui.tsx)：生产三栏工作区、Session/Run 面板和证据/关系面板。
- [lib/api.ts](lib/api.ts)：HTTP 请求和 Project/Index/Session/Run/Report/Impact 响应运行时校验。
- [lib/run-event-stream.ts](lib/run-event-stream.ts)：注入式 EventSource/计时器订阅状态机，负责 SSE 校验、续接、终态和有界回退。
- [lib/graph.ts](lib/graph.ts)：局部静态图合并、分组和 unresolved 选择逻辑。
- [lib/architecture-graph.ts](lib/architecture-graph.ts)：固定 `ProjectOverview` 到项目架构画布的有界确定性投影；测试见同目录 `architecture-graph.test.ts`。
- [lib/structure-detail-width.ts](lib/structure-detail-width.ts)：结构详情栏宽度偏好、可用空间边界和约束。
- [app/globals.css](app/globals.css)：工作台全局样式。
- [app/prototype/page.tsx](app/prototype/page.tsx)：只使用演示数据的 `/prototype` 评审原型，不调用 API。

## 接口与依赖

本目录的接口与依赖由 [workbench.md](workbench.md) 维护；精确字段与行为约束以源码声明为准。

## 数据与生命周期

本目录的数据与生命周期由 [workbench.md](workbench.md) 维护；精确字段与行为约束以源码声明为准。

## 使用与配置

先按 [MVP 操作说明](../../docs/mvp-operation.md) 启动 API，再从仓库根目录执行共享启动脚本：

```powershell
pwsh -File .\scripts\start-local.ps1 -Service web
```

打开 `http://localhost:3000`，从本地目录或 GitHub HTTPS 导入项目，或打开已有项目；导入 ready 后会选中项目和索引并打开架构页。导入状态轮询最多 40 次且总等待不超过 35 秒，初次导入及重试请求最多等待 60 秒；超时会保留“服务端可能仍在处理”的状态，可刷新已有 attempt，或用相同表单重新连接初次请求。项目级 URL 刷新保持架构视图，只有完整 Session URL 恢复 Session；固定索引列表可直接打开版本。本地目录未开放时按页面提示更新 API 设置并重启。然后创建 Session 并保存至少一条消息，点击“开始分析”。服务端模型分析要求 API 设置 `CODETRAIL_REAL_RUN_ENABLED=1` 并在服务数据目录配置 `config.json`；未配置或调用失败会显示错误，不自动切换模拟响应。功能视图的“识别主要功能”同样使用服务端模型，保留源码范围选择。普通工作台不提供 fixture 模式或固定场景选择；开发演示使用专用[样本仓库与离线入口](../../fixtures/README.md)，已有演示结果保留明确标识。

CodeTrail 作为自身仓库的 Codex external 验收流程见 [MVP 操作说明](../../docs/mvp-operation.md)；external Run 使用本地 MCP 和固定项目/Session，不需要真实供应商配置。

代码流程理解由 MCP external Run 的 `start_external_run(analysis_kind="understanding")` 发起，网页不创建该 Run，只在发现后展示其 explanation 报告；真实供应商语义质量和完整运行时覆盖仍需单独验证。

历史兼容恢复属于安全扫描确认后的显式更新；若扫描不完整、来源不可用或基准已过期，工作台继续显示失败并保留旧 latest。

## 错误与限制

本目录的错误与限制由 [workbench.md](workbench.md) 维护；精确字段与行为约束以源码声明为准。

## 验证

本目录的验证由 [workbench.md](workbench.md) 维护；精确字段与行为约束以源码声明为准。

## 关联文档

- [工作台子系统说明](workbench.md)
- [Run 执行子系统说明](../api/execution.md)
- [API README](../api/README.md)
- [Run 决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [external Run 的代码理解流程与证据报告](../../docs/decisions/2026-09-14-code-understanding-explanation.md)
- [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [TypeScript 静态索引与 HTTP 形状关联](../../docs/decisions/2026-10-06-typescript-http-overview.md)
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)
