# CodeTrail 开发与文档规范

## 架构原则

保持单仓库、单 Agent、只读分析的当前产品范围。按职责与独立变化拆分模块；API 与工具适配复用服务逻辑，服务使用索引与存储，前端通过公开接口访问。模块数量不作为架构质量指标。

图、证据和报告绑定同一 index_id，旧证据读取不可变快照。静态关系、unresolved 与模型假设分开；调用可达不代表确认影响。接口说明输入、返回、失败、时序、所有权及预算。外部输入通过边界验证，资源有明确释放时机，错误不泄露宿主细节。

## 事实归属

| 事实 | 权威位置 |
| --- | --- |
| 常驻开发约束与 skill 路由 | [AGENTS.md](../AGENTS.md) |
| 文档格式与验证规则 | 本文 |
| 子系统当前行为与限制 | [子系统索引](subsystems/README.md) 指向的页面 |
| Python 数据契约 | [contracts.py](../apps/api/app/contracts.py) |
| HTTP 路由与响应声明 | [main.py](../apps/api/app/main.py) 及运行时 OpenAPI |
| 导出 Schema | [生成器](../apps/api/app/export_schema.py) 产生的 JSON，禁止手改 |
| 设计取舍与替代关系 | [决策记录](decisions/README.md) |
| 本地产品计划与阶段进度 | memory-bank，保持现有 Git 忽略策略 |

同一事实只在一处详细维护，其他位置链接。当前文档写当前行为；历史理由写决策，计划明确标为未实现。记录验证必须带实际范围，不把过去运行结果当作当前证据。

## 统一格式

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
