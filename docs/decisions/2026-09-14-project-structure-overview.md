# 决策：项目结构总览

Status: implemented
- Date: 2026-09-14
- Scope: O1 ProjectOverview、HTTP/MCP 概览、项目知识页结构浏览

## 问题

当前 O1 已能返回入口、入口可达模块和符号路径，但工作台主要按入口平铺展示，用户难以先理解源码目录、HTTP 路径族和模块之间的静态依赖。结构分组必须来自同一个不可变 `index_id`，不能把目录或 URL 名称解释成模型确认的业务功能，也不能把模块聚合关系写回底层静态边。

## 决定

1. 扩展现有 `ProjectOverview`，增加 `groups`、`module_relations` 及其覆盖计数；不新增概览持久化表、不新增结构图数据库、不启动模型。HTTP 和 MCP 继续调用同一个 `WorkspaceService.get_overview`。
2. HTTP 组按 `(声明路径静态前缀, 处理函数所在模块)` 确定性分组。静态前缀保留从根到首个 `{parameter}` 之前的路径段；没有模板段时使用完整声明路径。组名明确标为 HTTP 路径/模块，不表示业务功能。处理模块从 handler symbol 的快照路径映射到 module symbol，无法映射时保留入口但标记未知模块。
3. 源码组按模块所在的相对源目录确定性分组，根目录使用 `/`；组内保留模块 symbol、入口 ID 和入口可达性。模块不属于业务功能，HTTP 模块也可同时出现在源码组中，以保留入口与源码结构的两种视角。
4. 模块关系只聚合固定快照中 source/target 均可映射到模块的 `imports`/`calls` 边；同一模块对的多条边合并为一个关系，保留关系类型、总边数和最多 8 条带源码位置的代表边。自模块边、无法映射的边和 unresolved 不伪造成模块依赖。
5. 概览使用 `max_groups`（默认 80、最大 200）和 `max_module_relations`（默认 200、最大 400）独立限制结构输出；现有 `max_depth`、`max_paths`、`max_modules` 语义不改变。组或模块关系达到上限时复用概览 `truncated/truncation_reason`，覆盖计数区分总量与可见量。
6. 规则身份升级为 `python-entry-v4`，使结构结果和新功能生成明确属于新的概览规则身份；旧 index、旧 Session、旧 Run、旧 evidence 和旧保存功能结果不改写，规则不一致时沿现有 feature graph 逻辑拒绝静默映射。
7. 工作台默认显示少量结构组、模块计数和静态模块关系；选择组后只展开该组的真实入口或模块，入口可继续进入同快照源码 evidence，handler/模块可继续进入既有 React Flow 局部关系图。组选择提供返回总览操作并保留当前入口/快照上下文。语义功能区域与结构事实分开显示。

## 替代方案

- 新增 `/structure` 接口：会让 HTTP/MCP/工作台拥有两套归属和预算路径，且没有必要脱离现有固定快照概览服务。
- 直接把所有符号放进总图：输出和画布会失去边界，无法先理解主要模块，也会把静态索引覆盖误认为完整架构。
- 用目录名或路径文本生成业务功能：没有源码/模型证据，违反结构事实与语义推断分离。
- 将模块聚合边写回 `CodeEdge`：会改变底层关系语义并使聚合边看起来像源码中的直接调用。

## 验收与证据

实施后需通过后端结构分组/关系聚合/归属/预算回归、前端运行时契约与工作台相关测试；生成并检查 Schema，运行前端测试、typecheck/build、Python compileall、docs 检查和 `doc-sync`。用 CodeTrail 自身同一 project/index 通过 HTTP 与本地 stdio MCP 对照至少三组入口和三条模块依赖的组成员、类型、源码位置及 index_id；浏览器验证结构总览、组选择、返回、入口 evidence 和 handler 局部关系图，并记录桌面/窄侧栏截图范围。旧快照和旧 evidence 继续可读取；完整 CFG、APIRouter、动态注册、多语言、远程 MCP、模型功能识别和 CodeGraph/Archify 等价均不在范围。

### 验收与证据（2026-09-14）

- 后端 `uv run --directory apps/api pytest -q`：130 passed；`uv run --directory apps/api python -m compileall -q app` 通过。
- 前端 `pnpm --dir apps/web test`：23 passed；`pnpm web:typecheck`、`pnpm web:build` 通过。
- `pnpm doc-sync` 通过，其中契约测试 21 passed；`pnpm docs:check:local` 和 `git diff --check` 通过。
- HTTP 与本地 stdio MCP 对同一 `project_id=project-6b917fa46b584d98a9b0298775d70ab5`、`index_id=index-codetrail-relations-v2-4c34a1225b926b41` 对照：均返回 `python-entry-v4`、36 个结构组、68 条模块关系；MCP 注册 20 个工具。抽查 HTTP 路由组 `/api/health`、`/api/projects`、`/api/indexes` 及对应模块关系，关系包含 `imports`/`calls`、`edge_count` 和真实源码行号，字段与 index_id 一致。
- Codex In-app Browser 桌面视图验证结构组选择、返回总览、`GET /api/health` handler evidence（`apps/api/app/main.py:L192–L196`）和 handler 局部关系图（`HealthResponse`、`health`，2 节点/1 条边）；`390×844` 窄视图验证侧栏收缩、结构组单列布局、模块关系和边界提示。截图已在 CUA 证据输出中捕获；该接口不提供本地截图路径。

## 后果

结构总览提供可追溯的静态导航，但不会恢复运行时架构或业务语义；模块关系的代表边是有界样本，`edge_count` 和截断说明必须同时展示。结构结果仍受固定索引扫描/unresolved 覆盖、模块、组、关系和总时间预算影响，空或缺失关系不代表不存在。`memory-bank/` 继续保持 Git 忽略，`next.md` 由用户控制不自动修改或提交。
