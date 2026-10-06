# Web 工作台

## 职责与边界

`apps/web` 提供项目/索引切换、索引新鲜度检查与显式更新、概览、Session/消息、保存功能历史、同快照功能图、源码证据，以及 Session 内显式 fixture/real Run 的启动、状态、事件、工具结果、Issue/代码理解报告、候选位置与有界静态影响、SSE 订阅和取消；Codex external Run 会被自动发现并接入同一展示链。它只通过公开 HTTP API 访问服务端数据，不直接访问 SQLite、GitHub、宿主路径或模型。

## 入口与目录导航

- [workbench.md](workbench.md)：当前实现、生命周期、限制与验收入口。

- [app/page.tsx](app/page.tsx)：挂载生产工作台。
- [app/workbench.tsx](app/workbench.tsx)：项目、索引、Session、Run、概览和证据请求编排。
- [app/workbench-ui.tsx](app/workbench-ui.tsx)：生产三栏工作区、Session/Run 面板和证据/关系面板。
- [lib/api.ts](lib/api.ts)：HTTP 请求和 Project/Index/Session/Run/Report/Impact 响应运行时校验。
- [lib/run-event-stream.ts](lib/run-event-stream.ts)：注入式 EventSource/计时器订阅状态机，负责 SSE 校验、续接、终态和有界回退。
- [lib/graph.ts](lib/graph.ts)：局部静态图合并、分组和 unresolved 选择逻辑。
- [lib/architecture-graph.ts](lib/architecture-graph.ts)：固定 `ProjectOverview` 到项目架构画布的有界确定性投影；测试见同目录 `architecture-graph.test.ts`。
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

打开 `http://localhost:3000`，创建或打开项目、建立 ready 索引、创建 Session 并保存至少一条消息，在 Session 的“Issue 分析”面板选择 fixture 场景后点击“启动离线分析”，或选择 real 后点击“启动真实分析”。`success-timeout-missing` 展示候选结论和同快照 evidence；`insufficient-external-service` 展示证据不足；`tool-failure-read-source` 展示工具失败及失败前保留的 evidence。real 还要求 API 设置 `CODETRAIL_REAL_RUN_ENABLED=1` 并在服务数据目录配置 `config.json`。

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
- [共享本地运行环境与 Codex 联合演示](../../docs/decisions/2026-09-14-shared-runtime-codex-demo.md)
