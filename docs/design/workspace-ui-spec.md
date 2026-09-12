# CodeTrail 工作区 UI 规范

状态：UI-S2 已实现的工作区规范与验收记录。本文保留 UI-S1 的设计基线，同时记录生产 `/` 的迁移结果；可浏览原型位于 [apps/web/app/prototype/page.tsx](../../apps/web/app/prototype/page.tsx)，生产组件位于 [apps/web/app/workbench-ui.tsx](../../apps/web/app/workbench-ui.tsx)。

## 设计读法

这是给工程师使用的代码理解工作区，核心任务是先建立项目事实，再围绕一个调查主题保留上下文。视觉语言采用冷静、编辑器式的中性界面：正文使用无衬线字体，代码和身份使用等宽字体，单一蓝色强调交互，信息密度中等，避免实验室网格、装饰渐变和营销式卡片堆叠。

本轮设计取值为 `DESIGN_VARIANCE 5`、`MOTION_INTENSITY 3`、`VISUAL_DENSITY 5`。动效只用于按钮按下、面板开关和状态反馈，必须在 `prefers-reduced-motion: reduce` 下退化为即时变化。设计借鉴 Codex 式的项目与会话层级，不复制品牌资产、版本标识或未核实的像素细节。

## 迁移前基线与入口映射

UI-S1 迁移前的生产页是单页纵向工作台，顶部为 `CODETRAIL / INDEX LAB` 和“关系勘验台”，项目、导入、索引、搜索、概览、图和源码证据按纵向区块排列，背景使用深色网格和渐变。它已经承载真实项目、固定索引、导入重试、概览、历史、功能图、代码关系、源码证据和 Session 消息能力，但主导航没有把项目知识与会话分成稳定的空间；UI-S2 已保留这些 API 行为并迁移外壳与信息架构。

| 现有入口或控件 | 目标入口 | 保留的行为 |
| --- | --- | --- |
| 项目列表与项目切换 | 左侧“项目”列表 | 仍按 `project_id` 切换，并清理或忽略旧请求和旧草稿 |
| Session 列表、新建 Session | 当前项目下的“会话”列表和 `+` | 列表按项目隔离；创建后固定服务端返回的快照 |
| “知识页”视图 | 左侧“项目知识”入口、中央知识页 | 不输入 Issue 也能查看入口、功能、模块和版本详情 |
| 功能图、代码关系 | 知识页操作或右侧“功能图”面板 | 图筛选、分组和关系方向仍使用现有受限图结果 |
| 源码检查器、证据链接 | 右侧“源码证据”面板 | 证据仍验证 `project_id`、`index_id`、路径、行范围、hash 和 `evidence_id` |
| 本地/GitHub 导入 | 项目操作入口“导入源代码” | 仍显示 ImportAttempt、固定 commit、失败和重试状态 |
| 索引控制和版本历史 | 项目操作入口“索引管理”与版本详情 | 仍能创建、打开和选择固定版本；失败历史不替换成功版本 |
| 符号搜索 | 知识页工具栏的“搜索”入口 | 搜索仍限定当前 `project_id/index_id`，结果可打开证据 |
| Session 消息追加 | 中央会话页底部输入区 | 当前只保存 human 消息，不展示假助手回复或可用的分析按钮 |

UI-S1 不删除任何现有 API 能力；映射是目标信息架构，UI-S2 已完成生产页面迁移，未改变 HTTP/MCP 契约。

## 应用外壳

### 三栏结构

根容器使用 `min-height: 100dvh`，不使用固定 `h-screen`。顶部全局栏高 `56px`，下方工作区拥有剩余高度。工作区默认三列，右列关闭时宽度为 `0`，打开时通过 CSS grid 使用明确的面板宽度变量。

| 视口 | 左侧项目/会话 | 中央内容 | 右侧面板 | 规则 |
| --- | ---: | --- | ---: | --- |
| `>= 1101px` | `264px` | `minmax(0, 1fr)`，正文最大 `900px` | 默认 `0px`，打开默认 `360px`，可调 `300-440px` | 左侧常驻；中央内容居中，不因面板打开而横向溢出 |
| `768-1100px` | `236px` | `minmax(0, 1fr)`，左右内边距 `26px` | 默认 `0px`，打开 `320px` | 面板打开后中央仍至少保留 `360px`；空间不足时先关闭面板 |
| `< 768px` | 抽屉，宽度 `min(86vw, 304px)` | 单列，水平内边距 `16px` | 全宽底部 sheet，最大 `100vw` | 项目按钮打开抽屉；遮罩关闭；面板打开时不改变会话路由 |

桌面工作区最小高度是 `calc(100dvh - 56px)`。顶部栏只保留品牌、当前项目/区域、原型或真实状态；不堆放导入、索引、Run 等低频动作。生产页的具体状态文案仍由 API 返回状态决定。

### 滚动、折叠和层级

- 左侧导航独立纵向滚动；项目与会话列表不随中央消息滚动。
- 中央知识页和会话消息共享中央滚动容器。会话输入区在中央底部 `sticky`，不遮住消息，滚动回顶部或切换 Session 时不把旧位置带入新 Session。
- 右侧证据/功能图拥有独立滚动容器；打开和关闭只改变面板，不重置中央滚动位置、会话草稿或当前项目。
- 右侧面板默认收起。知识页通过“打开功能图”、入口/功能行的“查看证据”打开；面板标题提供关闭按钮和“源码证据/功能图”切换。
- 面板宽度滑块只改变右列宽度，范围 `300-440px`，步进 `10px`。移动端以全宽 sheet 展示，滑块仍可隐藏或不影响宽度。
- `z-index` 只使用外壳层级：移动导航 `5`、遮罩 `4`、证据面板 `6`。不在内容卡片上堆叠任意高层级。

### 项目导航

左侧顶端是“项目”和“新建”，下方每个项目显示名称、来源类型和真实索引状态。展开项目后显示“会话”和 `+`；Session 行显示主题和更新时间，当前行有背景和焦点边框，而不是装饰性彩色点。左侧底部提供“项目知识”和“索引与导入”两个稳定入口。

当前项目没有可用索引时，项目仍可选择，但“项目知识”显示空状态，不能伪造功能图、Run 或分析结果。没有项目时左侧显示“还没有项目”，中央显示空工作区，并提供真实创建/导入入口。

## 页面状态

### 项目知识页

中央顶部显示项目名称、简短说明、低频的“导入源代码”和“索引管理”。版本条紧邻顶部内容，展示当前可用 revision 或 snapshot，不把 ID/hash 放进标题；“查看详情”展开或打开项目操作详情。主 tab 只保留“知识页”“功能图”“代码关系”，功能图和代码关系在右侧面板打开，不把中央变成第二个画布。

知识页按以下顺序组织：

1. “从入口到功能”展示入口候选、主要功能和关系来源；静态关系与语义线索有明确文字和样式区别。
2. “项目功能”展示少量主要功能，可逐项打开同一快照的证据。
3. “版本详情”展示 `index_id`、源码 hash、生成模式和限制；详情不抢占首屏主层级。
4. 说明条提醒演示数据、当前 Session 只保存消息以及 Run 未接入等事实，目标设计不能把计划显示成已实现。

入口、功能、模块和符号都使用稳定 ID 或可追溯的证据链接。图上的可达关系不等于确认影响，测试引用不等于覆盖率，模型推断不升级为静态事实。

### 会话页

会话页标题栏显示“返回项目知识”、主题和简短固定版本标签；Session ID、Project ID、完整 revision/snapshot 身份收入可展开的详情层。固定版本是当前会话的上下文说明，不允许自动跟随项目最新索引。中央消息区只展示已保存消息、来源、序号和时间；在 Run 尚未实现前，不展示假执行事件、假助手回复、费用或可用的“分析”按钮。

输入区固定在中央底部，textarea 最小高 `86px`，支持垂直调整大小。具体行为如下：

| 行为 | 约定 |
| --- | --- |
| `Enter` | 在 `Shift` 未按下且 `event.nativeEvent.isComposing` 为 false 时提交；提交前去除首尾空白 |
| `Shift+Enter` | 插入换行，不提交 |
| 输入法 | composition 期间 Enter 不提交；composition 完成后用户再次按 Enter 才提交 |
| 草稿 | 以 `project_id/session_id` 为键隔离；切换项目或 Session 不带入旧草稿，刷新恢复策略由生产实现决定 |
| 保存中 | 保留 textarea 内容，允许编辑后续草稿；提交按钮和 Enter 禁用重复提交，状态显示“保存中”；不插入本地假消息 |
| 保存成功 | 使用服务端 sequence 和时间追加 human 消息；只有提交时的 project/session/request 和草稿修订仍匹配时才清空当前草稿，否则保留后续文字并回到待保存状态 |
| 保存失败 | 错误紧邻输入区或消息区，未变更草稿保留原幂等键并提供“重试保存”；后续编辑使用新的提交身份，不显示成功或助手状态 |
| 分页 | 当前 API 按 `sequence` 递增返回，消息超过一页时在消息尾部提供“加载后续消息”；空会话不显示无效分页入口，追加新消息不跳到旧消息中间 |

### 证据面板

证据面板顶部固定显示来源上下文：`project_id`、`index_id`、必要时 `generation_id` 或 `session_id`。源码片段显示相对路径、行范围、content hash 和证据类型；打开面板前后中央会话内容、滚动位置和草稿完全不变。返回结果中的任何 project/index/path/line/hash/evidence 身份不一致时显示错误，不回退到当前 latest。

功能图面板的工具栏负责入口、功能、模块和全部筛选；图结果来自当前固定快照的受限接口。面板中可切换到源码证据，节点点击不隐式改变项目或 Session。证据链接不调用模型，已保存结果和历史读取不触发生成。

### 项目操作

“导入源代码”打开本地路径和 GitHub HTTPS 导入表单，说明路径由 API 服务端读取，来源目录只读；“索引管理”显示固定版本、失败/部分状态、revision、snapshot hash 和重试。生成模式、预算和模型供应商只放在详情或明确的生成操作中。当前真实功能生成仍需用户显式选择 `fixture/real` 与源码范围，原型不调用它。

## 设计 token

目标页使用单一主题的中性浅色 token，同时定义等价的深色 token 以响应系统偏好；不在同一页面分割浅深主题，不使用旧实验室网格或背景渐变。

| 类别 | 规范 |
| --- | --- |
| 背景 | 浅色 `#f7f8fa`，提升层 `#ffffff`，弱层 `#f0f2f5`；深色使用 `#171b22`、`#1d222b`、`#222832` |
| 正文 | 浅色 `#202633`，次级文字 `#687285`，弱文字 `#9aa3b2`；正文对比度按 WCAG AA 验证 |
| 强调 | 全页只用一组低饱和蓝 `#3158d9` 及其状态变体；不使用 AI 紫渐变或多色 CTA |
| 错误/成功 | 错误 `#b43d49` 配浅红底，成功 `#2f7658` 配浅绿底；仅在传达真实状态时使用 |
| 字体 | 正文 `Avenir Next`, `Segoe UI`, `Noto Sans SC` 回退；代码、ID、hash 使用 `Cascadia Code`, `Consolas` |
| 字号 | 页面标题 `26-36px`，区块标题 `17px`，正文 `13-14px`，辅助说明 `10-12px`；代码 `11px` |
| 行高 | 标题 `1.1`，正文 `1.5-1.65`，代码 `1.8`；不使用会裁剪字形的极窄标题行高 |
| 间距 | 基础步长 `4px`；控件内边距 `8-12px`；区块间距 `28-34px`；中央桌面内边距 `34px` |
| 分隔线 | `1px` `#e1e5eb`，强调边界 `#cbd2dd`；长列表只使用单侧分隔线，不给每行加双线 |
| 圆角 | 面板 `10px`，普通控件 `6px`，状态标签和筛选器使用 `999px`；保持同一规则，不混用装饰性形状 |
| 阴影 | 只给移动抽屉和右侧面板使用带背景色的轻阴影，桌面主体优先留白和边界 |
| 焦点 | `2px` 强调色外轮廓，偏移 `2px`；键盘可到达所有按钮、tab、输入和面板操作 |
| 图标 | 优先使用短文字或语义字符，避免手绘 SVG 和无意义装饰点；状态圆点只表达真实索引/连接状态 |

按钮文字必须在桌面单行显示，主按钮与背景保持 AA 对比度。原型不引入缺失的图标包、不新增依赖，不使用 emoji、照片、假终端或伪造截图。

## 原型与生产边界

独立原型是 `apps/web/app/prototype/` 下的 `/prototype` 路由，使用本地常量和 React 本地状态，故意不导入 `lib/api.ts`，不访问网络、不读取 SQLite、不读取宿主路径、不调用模型、不创建项目或 Session。右上角“预览状态”明确标记仅为设计评审；状态切换和保存延迟是视觉行为演示，不能当作真实 API 验收。

生产 `/` 页面由 `apps/web/app/workbench.tsx` 编排，并由 `apps/web/app/workbench-ui.tsx` 提供视图。现有 API、HTTP/MCP 契约、项目归属、固定索引、证据和 Session 消息规则继续有效；UI-S2 已完成真实页面迁移。迁移后的生产页与原型继续分开验收，不能因任一截图把 Run、Agent、SSE 或 Issue 分析标为已实现。

## 截图验收矩阵

截图由实际浏览器在指定视口生成。生产 `/` 的 UI-S2 证据放在 [workspace-ui-production/screenshots/](workspace-ui-production/screenshots/)，独立原型的 UI-S1 证据放在 [workspace-ui-prototype/screenshots/](workspace-ui-prototype/screenshots/)。截图文件是视觉证据，不是功能测试替代品。

### 生产 `/`（UI-S2）

| 文件 | 视口 | 状态 | 覆盖内容 |
| --- | --- | --- | --- |
| [desktop-empty-1440.png](workspace-ui-production/screenshots/desktop-empty-1440.png) | `1440x900` | 真实空工作区 | 空项目列表、创建项目入口、无索引知识空态 |
| [desktop-knowledge-1440.png](workspace-ui-production/screenshots/desktop-knowledge-1440.png) | `1440x900` | 真实项目知识页 | 项目/Session 导航、固定索引、概览、搜索和功能历史 |
| [desktop-session-1440.png](workspace-ui-production/screenshots/desktop-session-1440.png) | `1440x900` | 真实有消息 Session | 主题、简短固定版本、可展开 Session 身份、服务端消息序号、底部输入区 |
| [desktop-evidence-1440.png](workspace-ui-production/screenshots/desktop-evidence-1440.png) | `1440x900` | 真实源码证据 | 右侧证据上下文、源码行号/hash、合成证据关系按钮禁用 |
| [tablet-feature-graph-1024.png](workspace-ui-production/screenshots/tablet-feature-graph-1024.png) | `1024x768` | 真实功能图面板 | 平板三栏尺寸、语义/静态图例、面板宽度控制 |
| [mobile-knowledge-390.png](workspace-ui-production/screenshots/mobile-knowledge-390.png) | `390x844` | 真实移动知识页 | 单列内容、移动项目入口、操作按钮和搜索不越界 |

### `/prototype`（UI-S1）

| 文件 | 视口 | 状态 | 覆盖内容 |
| --- | --- | --- | --- |
| [baseline-current.png](workspace-ui-prototype/screenshots/baseline-current.png) | `1440x900` | 当前生产页 | 旧纵向 Index Lab 基线，记录 UI-S1 前的网格/实验室布局 |
| [empty-workspace-1440.png](workspace-ui-prototype/screenshots/empty-workspace-1440.png) | `1440x900` | 原型空工作区 | 无项目导航、空态说明和演示入口 |
| [desktop-knowledge-1440.png](workspace-ui-prototype/screenshots/desktop-knowledge-1440.png) | `1440x900` | 项目知识页 | 项目/会话主导航、中央知识内容、版本详情和低频项目操作 |
| [desktop-messages-1440.png](workspace-ui-prototype/screenshots/desktop-messages-1440.png) | `1440x900` | 有消息会话 | 固定版本标题、human 消息、中央底部输入区和草稿提示 |
| [desktop-evidence-1440.png](workspace-ui-prototype/screenshots/desktop-evidence-1440.png) | `1440x900` | 证据面板 | 右侧打开、固定上下文、源码行号、中央内容保留 |
| [desktop-failure-1440.png](workspace-ui-prototype/screenshots/desktop-failure-1440.png) | `1440x900` | 保存失败 | 错误提示、草稿保留和重试入口，无假成功 |
| [tablet-1024.png](workspace-ui-prototype/screenshots/tablet-1024.png) | `1024x768` | 功能图面板 | 236px 左栏、320px 面板和中央最小宽度，验证无横向溢出 |
| [mobile-390.png](workspace-ui-prototype/screenshots/mobile-390.png) | `390x844` | 空会话 | 单列布局、移动项目抽屉入口、输入区不遮挡内容 |
| [mobile-evidence-390.png](workspace-ui-prototype/screenshots/mobile-evidence-390.png) | `390x844` | 证据面板 | 全宽 sheet、关闭入口和证据上下文不越界 |

截图验收只证明布局和视觉状态可浏览；生产截图使用真实 API 数据，仍不能替代导入、索引、Session 持久化、归属拒绝、证据 hash 和 Run 生命周期的独立流程证据。

## 验收清单（UI-S2 本轮）

- [x] 生产 `/` 已在 `1440x900`、`1024x768` 和 `390x844` 实际打开并生成截图，页面没有横向溢出。
- [x] 项目列表、会话列表、新建会话、项目知识入口和索引/导入入口均可找到；真实项目/Session 可切换并刷新恢复。
- [x] 可从知识页打开和关闭功能图、源码证据，并调整桌面面板宽度；中央滚动、Session 身份和草稿不丢失。
- [x] 已验证空 Session 不显示无效分页、已有消息显示后续分页入口；空工作区有真实 `/` 截图，原型另有空态截图。
- [x] 已验证 Enter、Shift+Enter、输入法组合期间不提交、按 Session 隔离草稿，以及 API 离线后的写失败保留草稿与恢复后重试。
- [x] 已验证保存响应延迟期间编辑草稿不会被旧成功回调清空；切换 Session 会使旧写请求失效，未变更内容仍按原幂等键重试。
- [x] Session 标题优先主题与简短固定版本，Session ID、Project ID 和完整 snapshot hash 可在展开详情中访问。
- [x] 证据面板显示 `project_id/index_id/generation_id` 上下文，图筛选位于图工具栏；合成证据不发起无效关系请求。
- [x] 视觉使用中性背景、单一强调色、明确焦点和错误状态；无旧网格、装饰渐变、假终端和假精确产品宣传。
- [x] 原型截图与生产页面分开标记，未实现的 Agent 与候选/潜在影响高亮仍保持未实现；已实现的 Run SSE 状态不会伪装成隐藏推理或模型质量。
