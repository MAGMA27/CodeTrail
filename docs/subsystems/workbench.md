# 工作台

## 状态

已实现基于真实索引 API 的多项目工作台；项目创建/列表/切换、本地路径导入、导入状态/重试、索引归属与刷新恢复、规则驱动项目概览、P2-S1 项目 Session/消息、O3 项目知识页、保存功能版本历史、同快照功能图、显式 O2 功能生成和概览/功能到关系图/源码证据联动已实现，并能打开 MCP 生成的固定版本证据或 Session 引用。UI-S2 已将生产 `/` 迁移为左侧项目/Session、中间知识或会话、右侧按需证据/关系面板的真实工作区；独立 `/prototype` 仍是演示数据原型。Session 当前只保存消息，不做 Issue 分析；Run 生命周期和报告界面尚未实现。

## 职责与范围

通过公开 API 创建/列出/切换项目，提交服务端可见的本地路径并显示 ImportAttempt，创建或打开项目所属索引，默认展示绑定当前快照的 O1 入口/模块概览；左侧按 project_id 列出 Session，创建时固定当前可用快照，中心面板读取/追加分页消息并显示 human/agent 来源、服务端序号和固定 revision/snapshot，追加只保存内容不启动 Run；项目知识页按稳定 cursor 读取保存的功能版本摘要，明确选择的 generation_id 按 project_id/index_id 恢复，按需打开同一保存版本的功能图；用户可显式选择 fixture/real 与源码范围，请求 O2 功能生成，再展开入口、模块、符号和证据，也可查看受限搜索结果、静态邻接子图、分组折叠和快照源码证据；不直接访问 SQLite 或浏览器宿主路径。

## 接口与依赖

[apps/web README](../../apps/web/README.md) 记录目录职责；[page.tsx](../../apps/web/app/page.tsx) 挂载工作台，[workbench.tsx](../../apps/web/app/workbench.tsx) 管理状态与交互编排，[workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx) 提供生产外壳和视图组件，[api.ts](../../apps/web/lib/api.ts) 验证 HTTP 边界，[graph.ts](../../apps/web/lib/graph.ts) 管理图合并和分组；Next.js 局部规则见 [AGENTS.md](../../apps/web/AGENTS.md)。

## 设计原型与生产边界

UI-S1 的目标布局、尺寸、状态和截图验收见 [工作区 UI 规范](../design/workspace-ui-spec.md)；独立 `/prototype` 由 [prototype/page.tsx](../../apps/web/app/prototype/page.tsx) 提供，仅使用演示数据，不调用 API。UI-S2 已将真实 `/` 迁移到该信息架构；原型截图仍只证明视觉状态，不能替代真实接口和持久化验收，生产截图与原型截图分目录保存。

## 数据与生命周期

项目状态覆盖列表载入、空列表、就绪和错误；导入状态显示 queued/acquiring/indexing/ready/failed/interrupted 并提供失败/中断重试；索引状态覆盖未打开、创建中、载入中、就绪、部分完成和错误。切换 project_id 会取消或忽略旧请求，清空旧 index_id、图、源码、Session 列表/消息/当前选择和历史，再恢复该项目最新可用索引；Session 草稿不随旧项目状态混入，而是以 `project_id/session_id` 为键保存在浏览器 localStorage。浏览器另保存最近项目 ID、按 project_id 分隔的 selectedSessionId，以及按 project_id/index_id 分隔的明确 generation_id，项目、Session、消息与索引数据从服务端持久化恢复。Session 创建/消息追加使用稳定幂等键，写入失败保留草稿并可重试；切换 Session 不带入旧消息。消息接口按服务端 sequence 递增返回，后续页在消息尾部追加，空会话不显示无效分页入口。历史失败版本仍可打开，但默认选择只取最新 succeeded/partial；功能图不单独持久化，按需读取保存生成结果并从同一静态索引组装。

证据链接携带 project_id、index_id、路径、行范围、content_hash 和 evidence_id；Session 链接携带 project_id、session_id。工作台先检查 project_id 存在且 index_id/Session 属于该项目，再读取项目作用域的固定快照或 Session；任何返回的 index、路径、行范围、hash、evidence_id 或 Session 身份不一致都会显示错误，不会回退到当前 latest index。概览入口只接受 `python-entry-v1` 的 `__main__.py`/顶层 main guard 结果；O2 功能只展示同一生成结果实际返回的 symbol/evidence，明确显示 fixture/real、用量、未查询模块、局限和 partial/insufficient/failed 状态；功能图区分 semantic 与 static 关系，节点点击可回到同一快照的功能/源码证据，入口映射规则版本不一致时显示 unavailable；入口/功能证据进入既有源码检查器，模块选择进入同一快照的关系图，所有概览/生成功能、Session 消息、历史分页、路径/模块请求都有显式上限。

Session 使用同一幂等键重试时，工作台继续使用服务端返回的首次绑定快照，即使项目已经产生新的 latest index；历史页读取旧单次模型用量时由服务端兼容，无法恢复的单条版本不会阻断其他有效历史。

## 错误与限制

前端可以提交服务端 API 可见的绝对路径，但不读取或解析浏览器本机目录；服务端负责允许根目录、符号链接/junction 和 loopback 写边界。项目作用域端点拒绝不属于当前 project_id 的 index_id 或 Session。Session 主题/消息受长度限制，切换项目或 Session 不会带入其他范围的草稿或消息；保存失败保留当前草稿和原幂等键并允许重试，当前版本不显示模型回复、不启动 Run。概览未识别入口时明确标记未知，不把目录或名为 `main` 的函数冒充入口；动态调用保留 unresolved。功能生成显式触发，real 未配置模型时显示失败且不回退 fixture；切换项目/索引、模式/范围变化和重试都会取消或忽略过期请求。历史和功能图响应经过运行时形状校验，API 停止或返回错误时保留明确失败状态；Session 最多按页读取 40 个，消息最多按页读取 100 条，历史最多按页读取 40 个版本，功能图最多渲染 40 个节点和 120 条边，跨项目/索引/Session/生成版本及错误 evidence 引用均拒绝显示。

## 验证

pnpm --dir apps/web test、pnpm web:typecheck 和 pnpm web:build；UI-S2 本轮另以实际浏览器核对生产 `/` 的桌面/平板/移动知识页、真实 Session 创建/切换/消息保存、Enter/Shift+Enter、空消息状态、草稿隔离、证据侧栏、合成证据边界、功能图侧栏和各视口无横向溢出，并生成 [生产截图](../design/workspace-ui-production/screenshots/)。既有流程继续核对本地路径导入、索引/项目切换、刷新恢复、历史版本选择/分页、功能图 semantic/static 图例与节点证据钻取、概览入口/模块跳转、fixture 功能生成、功能证据和 Session URL 打开、real 未配置失败/重试、导入错误状态、MCP 证据 URL 打开与 hash/归属校验，以及搜索、关系方向、分组折叠、源码证据、unresolved、API 停止和恢复重试。

## 相关决策

React Flow 固定为 `@xyflow/react@12.11.6`，只渲染当前 API 返回的受限静态子图；后续重要取舍记录到 [决策目录](../decisions/README.md)。
