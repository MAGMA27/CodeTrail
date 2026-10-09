# 工作台

## 状态

已实现多项目真实工作台、Project/Index/Session/消息、可恢复回收站、索引新鲜度检查/显式更新、O1/O2/O3 浏览和 P2-R1/P2-R2 Session 内显式 fixture/real Run 面板。项目知识页默认展示当前固定快照的项目架构画布，再展示入口、功能和结构详情；Python/TypeScript 静态 imports/calls 可筛选，HTTP 客户端到 FastAPI 路由的 method/path shape association 以单独虚线边聚合显示，也可独立过滤；参数化模板只有完整同形且唯一时关联，列表标示字面量/模板并保留客户端与路由的同一 index 双端证据。点击模块进入“模块内部结构”，初开复用同一 project/index 的定义分页，只显示当前文件已加载的定义；用户可显式读取按目标文件聚合的静态导入依赖，或为所选定义读取一跳静态 calls。类成员只在限定名与源码范围同时吻合时缩进，不生成包含边；每个聚合导入保留原始静态边数和同一 index 的源码位置。HTTP 关联列表可打开客户端锚点→独立 HTTP 形状→对应 handler 的有界 calls 视图；HTTP shape association 自身不生成 TypeScript calls，可证明的客户端调用作为独立静态边显示。函数值变量（包括单函数参数包装中的 callback）可在既有关系图中定位；包装器执行 callback 的关系若不能证明仍保留 unresolved。所有静态箭头只表示 source→target，不代表数据流或运行时必然执行。概览版本为 `python-entry-v6+http-shape-v2+architecture-v1`；无 TypeScript fact 标记的旧索引显示 `not_analyzed`；旧 v1 索引明确显示 `literal_only`，不把未做参数化分析解释为无调用。Run 面板支持 AI 分析提示、启动、状态同步、持久化 SSE 事件流、有限重连/轮询回退、工具结果、Issue/代码理解报告 evidence、候选位置与有界静态影响、取消、历史选择和刷新/切换恢复；Codex external Run 会从 Session history 自动发现、选择并订阅，代码理解结果按顺序步骤和覆盖级别展示。

左侧固定底部“设置”入口打开全局设置，当前只有“模型服务”一节。配置属于当前连接的 CodeTrail 服务，适用于所有项目和会话中的内置 AI 分析；Codex 外部 Agent 使用宿主模型，无需在这里配置。入口不依赖当前项目，项目功能识别和 Session real Run 缺少密钥时的“打开设置”按钮也会打开同一面板。读取接口只返回供应商、服务地址、模型和密钥是否已配置；留空保留已保存密钥，只有明确移除操作才会清除。密钥只保留在当前表单内存，保存成功后清空。保存不试连供应商、不发送源码，后续任务读取更新，活动 Run 保留启动时配置。顶栏保留 API 连接状态和重试。

## 职责与范围

同一固定概览中的入口点按实际返回的 `fastapi_route`、`python_main_module` 与 `python_main_guard` 分类：架构容器汇总实际入口所在模块，展开后只有能按同快照 handler/path 或 module path/symbol 映射到入口的模块才显示入口类型；其他可达模块单独显示“入口可达”，不会被计作入口所在模块。组直接引用了入口但无法映射所属模块的条目单独显示“入口模块未映射”；明细未返回的引用另行计数。只对有入口引用的容器显示可点击 badge。入口清单首屏显示最多 8 条，其余可展开查看。清单的主操作会展开并聚焦所属职责/目录、强调映射到的模块，并说明入口 calls 起点是实际 handler、模块或未解析；源码按钮独立读取当前快照 evidence。调用链按钮复用既有 neighbors 图，以同 index 的路由 handler 或 Python module symbol 为起点，只请求向下 `calls`、深度最多 2、最多 40 个符号；当前结果是有界静态子图，不支持逐层展开；Python main guard 不是独立 handler。静态可达只表示快照中返回的 calls，不表示运行时必然执行或数据流。HTTP 客户端到服务路由的 method/path shape 关联仍由既有独立链路展示；从入口源码返回或退出 calls 链路后保留架构聚焦，切换项目或 index 时清除入口选择和展开焦点。

架构画布以职责和未归属的业务源码目录为主路径；常见测试、fixture、docs、examples、benchmarks 等辅助源码目录默认收起，显式打开后按目录容器呈现。聚焦职责居中，入向邻居在左、出向邻居在右，同一双向邻居只显示一个节点；仅保留聚焦职责与一跳邻居之间的真实关系，不画邻居彼此关系。职责之间的关系连接容器，展开组内模块关系默认汇总在容器计数中；悬停或键盘聚焦模块时，画布才显示该模块的已返回相邻边，并在模块提示中提供当前邻边计数。展开一次只分配一个职责/目录组；模块点击进入内部定义模式，返回后恢复架构视图。静态导入依目标文件聚合并按需读取；选中具体定义后才查询一跳静态 calls，箭头明确表示 source→target。依赖层级遵循已返回方向；循环组保持同层并计数，不伪造无环拓扑。通用符号关系图目录容器按每行最高内容高度定位子目录，子节点保持父容器内。

模块内部定义、静态导入和静态 calls 共用可伸展的内容区；定义与导入列表在各自面板内滚动，calls 画布填充状态说明和图例之外的剩余高度。工具栏将项目路径与视图/源码操作分行显示。calls 聚焦会把焦点符号所在的父 group 一并纳入拟合边界以保留完整标题；边悬停只切换边标签，不触发画布重拟合，用户可继续平移和缩放。

架构概览另在现有职责/目录模块卡片上显示应用入口、公共 API、插件扩展点与未知区域计数；图上分类计数使用可读辅助字号并提供完整提示，选择现有模块可查看最多 200 条分类和同快照源码 evidence。分类说明当前规则识别到的锚点，不增加 calls 路径，也不把“未从已识别应用入口到达”解释为无用。Python 规则只从静态 `__all__` 与真实 pluggy hookspec marker 绑定确认；动态导出、复杂重绑定、其他插件框架和当前未持久化的 TypeScript exports 保留未知，详情中的 evidence 最多 4 条且整体有界。

通过公开 HTTP API 创建/列出/切换项目、建立索引、读取概览/功能/图/源码证据，管理 Session 消息并显式启动 real Run；概览结构组按固定快照的声明路径/handler 模块和源码目录生成，imports/calls 样本与 HTTP 形状关联分别展示，不与语义功能混合。项目架构画布直接投影既有 `ProjectOverview`，没有第二套架构 API 或持久化编辑状态：源码目录组是规则生成的容器，静态 module relations 聚合 imports/calls；HTTP associations 保持独立数据结构和筛选，不转换为 CodeEdge。客户端模块到服务端模块按可见模块或折叠目录投影，点击关联边先打开所选聚合关联的右侧详情，由按钮分别读取客户端与路由 evidence；工具栏可明确打开全部已返回关联。关联 endpoint 未映射、40 模块画布上限摘要、服务端候选截断和旧索引未分析状态均显式提示；列表保留同一概览返回的关联与双端证据。职责聚焦是固定快照的本地视图：用户从已选职责显式进入后，按当前 imports/calls 类型过滤从完整已返回模块归属映射求双向一跳；HTTP 形状开关独立决定是否额外纳入 HTTP 相邻端点和边。只绘制保留职责/目录端点之间的真实关系，不生成传递边；邻接组初次折叠，可展开模块并沿用原 40 模块上限，当前职责优先。视图显示聚焦范围外的职责、静态关系和 HTTP 关联计数，并与概览/关系预算截断分开；返回项目总览会恢复进入聚焦前的展开组。展开、聚焦、缩放、平移和过滤均为本地视图状态，切换项目或 index 会重置。可从结构组中的 FastAPI 路由查看声明路径及处理函数的同快照证据，并从处理函数/模块打开既有关系图；HTTP 关联行的“查看链路”使用同一概览中的 `route_entry_point_id` 定位 handler，再调用项目级 neighbors 查询，显示客户端 evidence、非 CodeEdge 的方法/路径关联、handler evidence 与真实返回的静态 calls。链路模式固定为向下 `calls`，深度最多 2、节点最多 40；`overview.paths` 是所有入口共享的全局预算，链路不以缺少概览路径推断空邻接。面板常驻说明此深度边界，另显示 query/index/unresolved 截断和 unresolved；空结果明确不能证明没有服务/存储调用。源码面板提供返回当前链路的按钮；可退出并选择其他关联。切换项目/索引、选择其他概览组和新关系查询会中止旧链路请求，使过期结果无效，失败保留链上下文并可按原 handler 重试。关联映射失败会清除旧链状态而显示错误。也可从画布模块进入符号关系图、从代表静态边读取同一 index 的源码 evidence。已保存 Run 报告的 candidate/finding 可进入固定快照影响画布，Codex external Run 由工作台自动发现。桌面上代码关系、功能图和候选影响共用主图分栏，按 `.72fr / 1.28fr` 与项目/Session 内容并列；源码面板保持可调宽度，从关系图、功能图或影响图打开证据后可返回原图。主图分栏时中央架构画布按可用宽度收缩，HTTP 详情以单列右侧栏读取，项目名称保留完整文本提示。代码关系面板把查询截断与固定关系索引未完成分开显示；大目录分组默认收起，展开后可按目录边界聚焦，仍可通过平移/缩放查看已加载内容。项目操作页标题和关闭入口常驻，正文独立滚动；Run 当前状态、失败/取消、报告限制和证据入口常显，执行事件、工具结果与历史列表默认折叠。前端负责当前视图、请求取消/过期响应、草稿和 Run 展示，不负责冻结消息、执行工具、鉴权、预算或组装报告；这些由 API 应用服务拥有。

总览容器依赖采用方向居中的直线；穿过其他容器及安全边距的关系仅在当前画布折叠，显示数量与聚焦入口，不改变索引或聚合计数。职责和实际可显示的源码目录均可聚焦一跳依赖，目录不会伪装成职责；已被 manifest 完全归属的重复目录不提供假聚焦入口。模块悬停只改变边的显示，不插入会推动画布的提示段落；相邻关系数量保留在模块提示中。

后端概览仍整体计算并返回有界概要，尚未按展开动作分页加载路径与未知调用详情。密集全图适配会缩小卡片，需要聚焦或手动缩放阅读。

## 接口与依赖

Neighbors 查询返回的 `contextual_calls` 在代码关系面板单独列出，不进入静态 `CodeEdge` 图及节点/边计数。每条记录提供触发调用、字段调用、接收方构造器和字段赋值位置的同快照源码入口；它只表示指定构造上下文中的静态语法关系，不保证运行时执行。若构造实例逃逸到未知对象属性，会提示初始绑定之后字段可能被改写，目标仍未确认。普通邻接仍保留其空图与 unresolved 含义，查询截断时条件记录也可能不完整。

职责架构容器来自项目固定 index 的可选根 `codetrail-subsystems.json`，owner 是精确文件或目录前缀；职责组优先显示，未归属模块仍放在源码目录 fallback。工作台显示 `not_configured`/`ready`/`partial`/`invalid`、组与模块预算提示，并以同 index 的 manifest evidence 打开归属来源。点开职责组可见其模块；模块行进入内部结构，复用固定索引定义分页，按源码行和 symbol ID 显示 class/function/test。只有限定名和源码范围都匹配时才将类成员缩进归组；契约没有父 ID 时不补造归属。选择定义复用现有 source panel 读取同一 project/index 的 snapshot evidence；列表说明定义只代表索引实际捕获内容，不暗示解析完整。切换组、模块、项目或索引时取消过期页请求，续页失败保留当前页和原游标供重试。模块内静态导入只在用户请求后读取，以目标文件聚合；每个聚合显示实际返回的原始静态边数和可打开的固定快照源码位置。具体定义的调用关系也在显式选择后读取，保持一跳静态 calls；两种查询失败均可重试。容器之间的箭头只投影概览返回的静态 imports/calls，方向沿 source→target；HTTP 方法/路径形状仍单独画虚线关联，不与职责依赖混合。项目配置和回退规则见[职责分组决策](../../docs/decisions/2026-10-06-subsystem-responsibility-manifest.md)及[模块定义分页决策](../../docs/decisions/2026-10-06-module-symbol-pagination.md)。

项目、索引、新鲜度、概览、Session/消息和 Run 均通过 `/api/projects/*` 调用；索引管理提供检查更新和显式更新，前端展示 `consistent`、`changed`、`unknown`、变化计数、扫描预算及有界路径，更新返回复用或新快照结果。GitHub freshness 额外展示 `remote_status`、来源 URL、请求/解析 ref、远端 commit 和基线索引 commit；检查只解析远端 metadata，不下载归档，显式 GitHub 更新调用独立的 `github-refresh` 入口。概览先展示固定索引中的结构组：HTTP 路径按静态声明前缀与 handler 模块分组，源码按相对目录分组；选择分组后查看真实入口或可见模块。FastAPI 路由入口显示 HTTP 方法、声明路径和处理函数，点击入口先打开装饰器到函数结束的同快照 evidence，关系图按钮则以处理函数作为既有静态图根节点；模块关系区域展示聚合的静态 imports/calls、总边数和最多 8 条源码位置样本。结构组与模块关系是静态组织事实，不是语义功能，前端对组、模块和关系的可见/总量分别显示预算边界。Run 使用同一 Session 的 list/detail/events、`events/stream`、tool-results/report/impact/cancel 端点。启动按钮只发送 Issue、real 模式与固定 index_id，服务端独立冻结已保存消息；前端不把已加载的消息页当作 Run 输入。Codex external Run 不由网页创建，工作台在 Session history 成功后有界轮询 `execution_source=codex` 的活动 Run，再复用 detail/events/SSE 读取链。代码关系面板区分邻接查询 `truncated`、固定关系扫描 `index_truncated` 和 unresolved 详情 `index_unresolved_truncated`，并对旧索引的未知状态单独提示；大目录分组默认收起，展开后按选中节点及有限相邻节点聚焦。影响面板从报告候选或唯一 finding symbol 发起，重新查询 Run 固定 index 的静态 `calls`，方向/深度由用户控制且不修改 Run。HTTP 响应在 [lib/api.ts](lib/api.ts) 经过形状和 project/session/index/hash 边界校验，SSE 事件按 schema、Run 和连续 sequence 校验；`run.stream.end` 只接受 `succeeded`、`failed`、`cancelled`、`interrupted` 四类 Run 终态。

架构画布只消费 `ProjectOverview` 中 `project_id/index_id` 一致的源码目录、模块和关系；折叠目录汇总内部静态关系，展开后最多显示 40 个模块。分组聚焦选择实际可显示的职责或源码目录及与之有一跳真实关联的可用职责/目录；邻接范围先从固定 overview 的完整已返回模块关系与归属映射计算，再独立执行画布折叠/模块预算。HTTP method/path shape associations 以独立虚线有向边从客户端指向服务路由，不进入静态 imports/calls 聚合；关联边点击显示所选聚合关联的右侧详情，证据按钮分别打开客户端和路由 evidence。TypeScript calls 只来自可证明的直接具名绑定；路径别名、方法属性、动态调用和未确认的 callback 执行仍是 unresolved，且与 HTTP shape association 分开。概览截断、模块超限、聚焦隐藏计数、关联端点映射失败、画布限额摘要和 unresolved 均分别显示为覆盖提示，不把未返回数据解释成“无依赖”。模块钻取初开只读取当前固定 index 的模块定义分页，不发起 neighbors 查询；静态导入按需一跳读取并按目标文件聚合，源码位置可逐边打开。定义的调用关系单独按需读取，箭头只表示真实静态 source→target 方向，不代表数据流。分页、关系请求均校验固定模块或符号及 index，失败可重试；项目或 index 切换会使旧请求失效。TypeScript 快照使用 API 主机可用的 Node.js 和工作区锁定 TypeScript 5.9.3 解析器；无运行依赖或有语法错误时索引显式 failed。

侧栏和空项目页的创建入口统一打开名称与来源表单，支持本地目录和 GitHub HTTPS 导入；ready 后选中项目及其 ready 索引、清除旧 Session 链接并返回架构页，刷新仍恢复到该项目。项目 URL 中显式的 `project_id` 优先于浏览器 localStorage；只有完整的 `project_id` 与 `session_id` 才恢复 Session，架构 URL 不会因本地历史选择自动跳回旧 Session。返回架构会用项目级 URL 替换 Session 参数，保留 hash。来源路径必须位于 API 配置的本地可导入目录内；拒绝时提示更新 API 设置并重启。导入非终态时每 750 ms 查询一次，最多 40 次且总等待不超过 35 秒；单次状态查询最多等待 10 秒，初次导入及重试请求最多等待 60 秒。超时或网络中断显示“服务端可能仍在处理”，有 attempt_id 时可刷新；初次请求未收到 attempt_id 时，相同表单内容会复用幂等键以重新连接，收到确定响应或确定错误后清除，后续明确导入可建立新 attempt。状态请求与客户端等待均有 AbortSignal；关闭表单、切换项目/视图或 Session 后，晚到的 attempt 状态仍可显示在原表单状态中，但不会改变当前项目、URL、视图或关闭新面板。客户端取消不代表服务端导入已取消。GitHub 导入保留 API 返回的失败原因；如公网限流发生在 attempt 创建前，表单维持可再次提交。源代码与索引管理页继续分为“源代码来源”和“固定索引”两个页签：前者复用同一导入表单和 ImportAttempt 状态，后者承载固定版本、检查更新和显式更新。固定索引列表可直接打开版本，显示当前/历史、状态和快照 hash；普通流程不要求填写 `index_id`、`revision` 或 `dirty`，缺少可信时间字段时不显示由 ID/hash 伪造的日期。两者共享项目上下文，但仍分别调用导入和项目索引接口；前端不直接访问来源路径或 SQLite。

固定索引页签的本地检查结果由服务端读取源码 manifest，按内容 hash 区分 `consistent`、`changed`、`unknown`；GitHub 检查只做跟踪 ref→commit 的 bounded metadata 请求，远端状态与本地规则/配置状态分别显示，远端 commit 一致不代表索引规则一致。前端同时显示变化统计、扫描预算、路径列表和扫描不完整告警，完整安全扫描且仅为旧 metadata/源码/规则/配置变化时提示可全量重建。更新成功显示复用或新建快照，GitHub ref 在检查与下载之间移动时以实际导入 commit 为准，失败不替换旧 latest。

索引更新的响应模式为 `reused`、`historical_reused` 或 `created`。当源码恢复到同项目中规则、配置、schema 和完整 manifest 均兼容的历史内容时，工作台提示 latest 已切换到历史 immutable index，并保留历史 revision/dirty 等来源 metadata；前端不把该场景伪装成当前快照复用。

## 设计原型与生产边界

生产 `/` 使用真实服务端数据；`/prototype` 仅是演示数据原型，不调用 API、不写入项目状态。UI 布局和证据侧栏沿用 [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)，Run 面板是现有 Session 视图中的显式操作，不自动随消息保存触发。

## 项目知识桌面工作区

侧栏为 Project 和 Session 提供“移到回收站”入口及明确确认文案，用户可从同一侧栏打开回收站并分别恢复项目或独立 Session。恢复只更新服务端移除标记，不自动选择对象、切换当前视图或启动分析；恢复项目也不恢复单独移除的 Session。正在更新索引的项目不能移除；Run、导入或功能生成与移除冲突时由服务端拒绝，界面保留具体错误以便重试。操作成功后只清理仍指向被移除对象的当前上下文、请求、草稿、图/证据选择和 URL；如果用户已切换到其他对象，晚到的清理不会覆盖新上下文。来源、固定索引、消息、Run、报告与证据均由服务端保留。

侧栏默认用当前项目选择器、项目知识和当前项目的 Session 列表组织空间；完整项目列表按需展开，展开时仍保留每个项目的更多操作。Session 列表占用侧栏剩余高度并独立滚动，行内只显示主题和消息数。桌面侧栏默认宽 264px，可用持续可见的窄分隔线拖动，或聚焦分隔线后用方向键按 16px 调整、Home/End 到当前范围边界；宽度在 220-480px 内，并随中央内容和已打开面板的可用宽度动态收紧。宽度保存在浏览器 localStorage 的 `codetrail.sidebar-width.v1`，跨项目切换和刷新保留；移动抽屉不提供调宽控件。Project 与 Session 的“更多”按钮打开键盘可操作的展开菜单；菜单可用 Escape、焦点移出或点击外部关闭，操作明确标为“移到回收站”，继续使用原确认、忙碌禁用和请求失效处理。项目选择、项目知识、新建会话、回收站及源代码与索引入口保持直达。

架构容器展开后，模块 hover 只重投影关系边；节点数组与 hover 状态解耦，避免强调相邻关系时重建全部模块节点和重置布局。模块仍保留 `nodrag/nopan`、鼠标 hover、点击钻取和源码返回交互；模块关系与过滤仍来自同一个固定 overview 快照。

项目架构工具栏将“关系筛选”与“画布操作”分组；用户可分别控制静态导入、静态调用和 HTTP 关联，并按 HTTP 入口聚焦关联。技术术语保留在提示中。聚焦职责或目录作为状态显示，聚焦、显示辅助目录、切换/返回总览和重置保留原行为；控件共享紧凑的高度、字体、圆角和交互状态，HTTP 入口选择器在可用空间内自然换行。

中央知识页默认为架构视图，工具栏常驻，画布使用剩余高度。HTTP 关联、符号搜索、版本与覆盖由明确入口按需打开右侧详情；目录/职责选中立即显示结构详情，静态边选中显示聚合数量与源码依据，HTTP 边只显示所选聚合关系对应的双端证据。全部 HTTP 关联仅通过工具栏入口显示，不再默认纵向铺开。结构详情标题直接显示组名与“职责详情”或“目录详情”，提供“模块 / 入口 / 依赖”页签，每个页签的内容独立滚动。普通组选择默认打开模块页；点击架构卡片中的入口清单 badge 或入口行会打开入口页。模块页列出当前组已返回文件的短名和完整路径，并可进入原有模块定义视图；不会在此重复展开定义列表。依赖页区分关系组数与静态边数，显示短模块名、完整两端路径和当前 index 的代表源码证据，每次最多列出 12 组。

入口页保留入口聚焦、源码和有界 calls 操作；Python 启动块使用文件名等用户可读标题。规则 ID、分类源码依据与覆盖限制默认收在“解析详情”中。当前组分类数只统计匹配当前组的 `selectedClassifications`；项目级分类记录返回数与角色分析受限状态分开说明，不把全项目覆盖数解释为当前组完整度。所有证据操作仍绑定概览的固定 `index_id`。

架构与功能是明确的视图切换；功能视图集中保存版本、显式生成、失败/重试及功能结果，独立滚动，不自动启动生成。架构组件切换时保持挂载，保留展开、聚焦与过滤。结构详情的标题和页签固定，各页签内容独立滚动；用户可拖动其左侧分隔线，或聚焦分隔线后用方向键按 16px 调整、Home/End 到当前范围边界。宽度首选值保存在浏览器 localStorage 的 `codetrail.structure-detail-width.v1`；最大宽度随可用空间收紧，并在空间允许时为中央画布保留至少 480px。全局源码证据或外部图面板打开时，架构详情暂时隐藏，返回后恢复所选上下文，避免并存两套右侧栏。关闭详情清理详情选择，不改索引或图展开；项目/index 的 key 变化重建知识视图并清空旧详情和旧展开，但保留本地宽度首选值。

本轮验收以当前 PC 的 MVP 阅读流程为准，不要求固定分辨率矩阵；此前尺寸证据保留为历史。搜索、定义分页、固定快照源码校验、HTTP 链路和功能请求仍复用原编排与服务；后端概览没有改为按展开动作加载，也没有新增运行时数据流分析。

## 数据与生命周期

桌面代码关系、功能图和影响范围按 `.72fr / 1.28fr` 与当前中央内容分配主图空间；源码面板使用可调侧栏。窄屏断点和具体布局见 [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)。断点和布局约束见 [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)。

当 Run 报告的 `outcome=explanation` 时，工作台在结果区域显示覆盖级别和最多 12 个有序步骤；每步显示 confirmed/inferred/unknown 状态、说明、同一报告的源码 evidence 按钮。partial/unknown 会明确显示未确认范围；只有一个有效 symbol 时可进入现有局部关系图，多符号或无符号步骤保留源码证据，不显示候选影响按钮。旧 Issue 报告继续使用候选/影响展示。

项目架构视图随当前明确选定的项目和固定 index 重建；切换项目会清理旧 Session/证据深链及 URL 参数，避免刷新跳回原项目。刷新后从恢复的项目概览重建图；展开目录、imports/calls 和 HTTP 形状过滤及焦点不持久化。画布表示该索引实际返回的 Python/TypeScript 静态源码关系和规则确认的 HTTP 方法/路径形状关联；后者不证明实际网络目标、本地部署拓扑或运行时请求行为。

Session 内的 AI 分析必须由用户显式点击“开始分析”；Codex external 分析由 MCP Agent 启动，网页只发现和展示。真实模式启动前显示会发送冻结 Issue、已保存消息和有界源码证据、可能产生费用且失败显示错误的提示；启动前若有草稿也会显示提示，但草稿不进入 Run。服务端返回 queued 的 run_id 后，工作台先读取已持久化事件建立历史，再以 `after_sequence` 订阅 SSE 逐项接收新增公开事件；连接失败经过有限退避后回退到有界轮询。`onopen` 只表示握手成功，不清零无进展重连计数；只有连续 sequence 的新事件重置重连预算，达到上限后只保留一个轮询定时器。面板常显当前状态、失败/取消入口、报告 evidence 和限制；事件时间线、工具结果及 Run 历史默认折叠，展开后仍可查看。面板展示模式、execution source、固定消息范围、input hash、index 和 execution version，并可从报告 evidence 打开既有源码证据侧栏。

切换 Session 或项目会取消/忽略旧的 Run 请求和轮询，关闭旧 EventSource、取消影响查询并使旧回调失效，清空旧 Run 详情，再读取新 Session 的 Run 历史；刷新后先恢复项目/Session，再从服务端历史选择活动或最新 Run，重新载入状态，不重复创建。Run 事件以 Run/sequence 去重，断线使用最后成功处理的 sequence 续接；连接状态独立于 Run 状态，终态后停止订阅和刷新。Run 取消使用独立写请求，成功后继续读取终态；消息保存仍只保存消息，不会隐式启动 Run。影响节点和源码证据优先使用返回的 symbol.index_id，选择旧 Run 时不会回退到当前 latest index；请求失败、切换和刷新都会清理影响面板。其它项目/索引/功能历史状态继续按既有 project_id/index_id 键隔离。

## 错误与限制

API 离线、项目/Session/index 不属于当前范围、索引不在 latest、源码扫描不完整或来源不可用、消息未保存、空 Session、Run 活动冲突、预算或执行容量拒绝、模型不可用/失败、结构化输出无效、取消和重启中断都会显示明确状态；unknown 不渲染为一致，完整安全扫描下的旧 metadata 组合会提示可重建，其他更新失败保留旧 latest。项目概览错误有单独的重试入口；点击后按当前选定的 project/index 再次读取概览。2026-10-06 的真实浏览器验收用一次性本机代理注入首个 overview HTTP 503，点击重试后恢复 200 和架构画布，细节见本地阶段记录。容量满时服务端在落库前返回可重试错误，工作台不会伪造 queued Run；Run 失败不会伪造成功结论。工作台不显示隐藏思维链，只显示服务端公开事件、工具结果、证据、报告和限制；real Run 的能力开关和配置由服务端控制，fixture/real/external 都不会执行仓库源码或 shell。架构画布最多显示 40 个展开模块；overview 的模块/关系也可能先受服务端预算截断。固定关系扫描未完成、unresolved 详情截断、缺少映射端点和旧索引未知状态分别显示覆盖告警，查询自身截断时另显示查询告警；空邻接不能证明没有关系。Run 历史、事件和工具结果使用服务端有界分页，影响查询最多返回 40 个节点、40 条解释路径和 40 个 unresolved；前端显式显示 mapped/unmapped、截断、静态 calls、未知边界和范围说明。报告 evidence 与影响节点必须和 Run 的固定 index_id 一致，否则拒绝载入；外部 Run 终态或超时后不会继续显示新工具活动。

草稿继续按 project/session 保存到浏览器 localStorage，切换范围不串入；消息保存中的旧响应不能清除新草稿。证据链接携带 project_id、index_id、路径、行范围、content hash 和 evidence_id，Session 链接携带 project_id/session_id，不回退到 latest index。

## 验证

本次实际浏览器展开 API 职责后 hover `service` 模块，仅相邻关系强调、节点位置稳定，点击钻取/返回后展开状态保留。后续侧栏验收覆盖拖动、16px 键盘步进、Home/End 边界、刷新与项目切换持久化，并在受控 900px 视口确认可用宽度会收紧最大值。隔离 Web/API 实例使用临时 `CODETRAIL_DATA_DIR` 和 fixture 项目完成 Session 移入回收站、项目移入并恢复、独立 Session tombstone 保留后再恢复的 UI 闭环；回收站最终为空。原有 localhost:8000 API 仍对 `/api/trash` 返回 404，未重启或停止；该旧服务未用于本轮在线闭环，详细证据见本地阶段记录。自动检查命令与结果也记录在该阶段记录中。

展开模块共用 40 个画布名额；无聚焦时总览中当前选中且已展开的组优先，聚焦时当前职责优先，其余按展开顺序分配。未返回的 overview 模块、画布名额不足和聚焦范围外的可用事实分别提示，选择组不会加载遗漏模块。静态过滤保留至少一种 imports/calls，HTTP 形状开关独立。可复现的较大样本生成与边界见 [样本说明](../../fixtures/README.md)。

从仓库根目录执行 `pnpm --dir apps/web test`、`pnpm web:typecheck` 和 `pnpm web:build`。项目架构画布的浏览器交互验收及未完成项见本地 memory-bank 阶段记录；布局约束见 [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)。2026-10-06 已在真实浏览器验证概览 503 失败后通过重试恢复；产品按用户确认面向 PC 端，窄屏矩阵不属于验收范围；已有 390×844 点检仅作为历史证据。当前架构验收画面可用官方浏览器截图接口查看，未保存截图文件；不能由构建或单测推定 UI 全部通过。其他浏览器流程与已知案例限制见 [影响链验收](../../evals/results/p4-e2b-codex-host-20261001/impact-chain-acceptance.md)，其既有边界状态仍有效。

## 相关决策

- [Project 与 Session 可恢复回收站](../../docs/decisions/2026-10-08-workspace-trash.md)
- [固定快照架构画布的可读层级](../../docs/decisions/2026-10-08-architecture-view-readability.md)
- [Run 执行决策](../../docs/decisions/2026-09-12-run-execution.md)
- [P2-R2 模型驱动 Issue 分析决策](../../docs/decisions/2026-09-12-run-model-analysis.md)
- [Run SSE 决策](../../docs/decisions/2026-09-12-run-sse.md)
- [Run 影响查询决策](../../docs/decisions/2026-09-13-run-impact.md)
- [项目与会话决策](../../docs/decisions/2026-09-11-project-session-workspace.md)
- [工作区 UI 规范](../../docs/design/workspace-ui-spec.md)
- [Codex 外部 Agent Run 决策](../../docs/decisions/2026-09-13-external-agent-run.md)
- [external Run 的代码理解流程与证据报告](../../docs/decisions/2026-09-14-code-understanding-explanation.md)
- [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)
- [FastAPI 路由入口概览](../../docs/decisions/2026-09-14-fastapi-route-overview.md)
- [项目结构总览](../../docs/decisions/2026-09-14-project-structure-overview.md)
- [TypeScript 静态索引与 HTTP 形状关联](../../docs/decisions/2026-10-06-typescript-http-overview.md)
- [库与插件架构概览](../../docs/decisions/2026-10-07-library-plugin-overview.md)
- [本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)
- [GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)
- [本项目索引更新与缓存效果联合验收](../../docs/decisions/2026-09-16-project-index-cache-acceptance.md)

普通工作台内置的功能识别和 Issue 分析不提供 fixture/real 或固定场景选项；两个显式动作只发送 real 模式，未配置密钥时先导向模型设置，不启动任务。历史 fixture 功能版本及 Run 仍可读取，标记“演示结果”。MCP 外部 Agent 可用自己的宿主模型提交功能结果；历史列表和结果详情会显示外部来源及可追溯的来源 Run，点击后打开该 Session 并选中 Run；字段加入前保存的旧外部版本明确显示来源 Run 未记录。自报模型名单独标为宿主信息，服务端模型调用统计为 0；结果刷新后仍从同一 FeatureGeneration 读取。该路径不要求配置 CodeTrail 服务端模型密钥。开发者通过[fixtures 离线入口](../../fixtures/README.md)使用模拟项目，HTTP/MCP 的 fixture 契约与自动测试保持兼容。
