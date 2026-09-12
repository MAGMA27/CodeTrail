# CodeTrail 开发与文档规范

## 架构原则

产品目标为多项目工作区，每次分析仍保持单仓库、单 Agent、只读；新增能力边界见 [产品方向决策](decisions/2026-09-11-project-workspace.md)。按职责与独立变化拆分模块；API 与工具适配复用服务逻辑，服务使用索引与存储，前端通过公开接口访问。模块数量不作为架构质量指标。

图、证据和报告绑定同一 index_id，旧证据读取不可变快照。静态关系、unresolved 与模型假设分开；调用可达不代表确认影响。接口说明输入、返回、失败、时序、所有权及预算。外部输入通过边界验证，资源有明确释放时机，错误不泄露宿主细节。

## 事实归属

| 事实 | 权威位置 |
| --- | --- |
| 常驻开发约束与 skill 路由 | [AGENTS.md](../AGENTS.md) |
| 文档格式与验证规则 | 本文 |
| 目录当前职责、使用与限制 | 最近的源码目录 README；尚未迁移的事实暂由现有子系统页面拥有 |
| 跨目录协作和子系统导航 | [子系统索引](subsystems/README.md) 指向的页面，链接局部 README |
| 方法、类型与事件的精确语义 | 声明旁的 docstring/JSDoc |
| Python 数据契约 | [contracts.py](../apps/api/app/contracts.py) |
| HTTP 路由与响应声明 | [main.py](../apps/api/app/main.py) 及运行时 OpenAPI |
| 导出 Schema | [生成器](../apps/api/app/export_schema.py) 产生的 JSON，禁止手改 |
| 设计取舍与替代关系 | [决策记录](decisions/README.md) |
| 工作区目标 UI 结构与截图验收 | [UI 规范](design/workspace-ui-spec.md)；当前实现仍以工作台子系统为准 |
| 本地产品计划与阶段进度 | memory-bank，保持现有 Git 忽略策略 |

同一事实只在一处详细维护，其他位置链接。当前文档写当前行为；历史理由写决策，计划明确标为未实现。记录验证必须带实际范围，不把过去运行结果当作当前证据。

## 统一格式

### 就近文档

处理子系统时沿源码目录向上读取 README.md 与适用 AGENTS.md。以独立职责选择位置：apps/api、apps/web、contracts、fixtures 可以拥有 README；将来形成独立目录的索引、Agent 或 Run 子系统在其目录维护 README。不因文档要求重组源码，不给每个文件创建 README。平铺模块由共享目录 README 导航，模块 docstring 描述自身语义。

在已授权开发或文档维护范围内，受影响的独立职责目录缺少 README 时自动创建，采用 [就近 README 框架](templates/local-readme.md)，写清实际实现与限制。已有文档保留有价值的内容，将缺少的框架内容补入；非平凡更新时逐步统一栏目，无需为了格式重写无关内容。只读设计与审查报告缺口，不自动修改文件。仅修改工作流规则不要求全仓迁移。

局部 README 拥有目录级事实，集中子系统文档负责跨目录说明与链接。迁移详细说明后将原文改成导航链接，同步父级 README 与子系统索引，保留现有有效链接。不要同时复制接口字段、行为和限制；精确接口契约在源码声明，生成 Schema 仍由生成器拥有。

AGENTS.md 仅记录该目录额外的开发规则、所需阅读和检查命令。没有额外规则就不创建；不复制根级规则或 README。新 README 的栏目均需有具体内容，不适用时解释原因，不留下模板占位说明。

子系统页面使用 [模板](templates/subsystem.md)，栏目保留且内容非空，不适用时解释原因。一个子系统可以对应多个文件，不强制按目录拆包。索引 owners 是根目录相对路径前缀或文件，覆盖 apps/api/app、apps/web/app、contracts、fixtures 下受检源码；新增独立子系统才增加页面。

段落一行，标题后空一行，链接使用相对路径，文件以一个换行结束。代码是机械定义的权威，文档说明用途、限制和非显然语义。避免复制完整 Schema、临时推理、过时测试数量与重复进度。

决策使用 [模板](templates/decision.md)，状态为 proposed、implemented、rejected 或 superseded。implemented 需要验收证据；superseded 必须指向替代记录。已否决的方案不得写成当前约束。

## 验证选择

| 改动 | 必要证据 |
| --- | --- |
| 文档、skill、归属索引 | pnpm docs:check；修改本地计划时加 pnpm docs:check:local |
| Pydantic 或 Schema | pnpm contracts:check；消费者受影响时加前端类型检查 |
| 索引、关系、存储、API | 对应 pytest 文件，必要时真实本地 HTTP 流程 |
| 前端行为 | pnpm web:typecheck、相关构建及实际浏览器流程 |
| 模型与事件行为 | 确定性 fixture 和实际组装路径；真实模型评测另记环境与结果 |

根目录运行后端测试：uv run --directory apps/api pytest tests/test_api.py -q。按修改面替换测试文件。pnpm doc-sync 聚合共享文档检查与契约测试，测试不调用网络或真实模型；首次依赖安装可能需要网络。首次准备环境使用 uv sync --project apps/api --locked。

docs:check 验证本仓库约定的内联文件链接、栏目、状态、源码归属及 skill 元数据，不验证外部 URL、Markdown 锚点、自然语言语义或运行时正确性。docs:check:local 另检查存在的 memory-bank 内联文件链接，不将其作为干净克隆的依赖。

## 完成条件

设计与实际变更一致，相关文档同步，所需验证已有结果。审查按 [模板](templates/review.md) 给出 PASS、NEEDS_FIX 或 UNVERIFIED。skill 由 AGENTS.md 路由调用，不是后台守护进程；仓库 CI 执行 doc-sync 才能持续提供机械约束。

## 双入口设计约定

HTTP 与 MCP 复用应用服务和领域契约，界面状态不充当 Agent 的隐式上下文；同一 project_id/index_id 的领域结果与错误语义已完成 A1/O1/O2 对照验证，W2 本地导入也复用同一项目/索引服务。O1 规则概览、O2 有证据功能生成、P2-R2 有界 Issue 分析和 P3-S1 持久化 Run SSE 已实施，完整候选/潜在影响高亮仍属后续范围，见 [统一接口决策](decisions/2026-09-11-agent-native-interfaces.md)、[P2-R2 决策](decisions/2026-09-12-run-model-analysis.md) 和 [Run SSE 决策](decisions/2026-09-12-run-sse.md)。
