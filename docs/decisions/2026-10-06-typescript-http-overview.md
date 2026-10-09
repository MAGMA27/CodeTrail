# TypeScript 静态索引与 HTTP 形状关联

Status: implemented
- Date: 2026-10-06
- Scope: apps/api indexing/storage/overview, ProjectOverview contract, PC architecture canvas

## 问题

CodeTrail 的不可变索引原先只捕获 Python 源码，无法展示前端 TypeScript 静态定义、相对导入或客户端请求与 FastAPI 声明之间的证据路径。新增分析必须继续读取固定快照、不运行仓库代码，并与 Python 的 AST/cache 统计、静态 imports/calls 语义及旧快照读取边界分开。

## 决定

1. 同一有界快照捕获 `.py`、`.ts`、`.tsx`，freshness 复用同样的扩展名、生成目录排除和文件/字节/时间预算。首版快照规则标识为 TypeScript 编译器版本和 `typescript-facts-v1`；当前 v2 参数化 facts 规则见下方扩展；捕获预算进入 configuration hash。读取后若实际累计字节越过预算，扫描标记不完整，不能返回一致 freshness。
2. TypeScript/TSX 使用 API 固定 helper 调用 `apps/web` 锁定的 TypeScript 5.9.3 编译器，以 compiler AST 解析内存中的 captured source text；不加载或执行目标仓库模块、依赖或配置。API 主机对含 TypeScript 的索引要求 Node.js 和锁定 package 可用；缺少运行时/包、超时、输出超限或语法诊断会显式令新索引失败。TypeScript 第一版每次有界冷解析，不持久化事实缓存，也不计入 Python AST/cache hit。
3. 为 TS/TSX 捕获模块、函数/类定义及静态/动态模块 specifier。仅精确且唯一落在同一快照内的安全相对导入生成现有 `CodeEdge(kind="imports", extraction_method="ast")`；路径别名、包导入、动态导入、越界、歧义和缺失目标保留 unresolved。符号身份含相对路径，避免 `foo.py` 与 `foo.ts` 撞 ID。
4. HTTP 客户端只对 AST 能确认的透明顶层 `requestJson`→`fetch(apiBaseUrl + path)` wrapper 提取其直接顶层函数调用。未通过透明封装验证的 wrapper 不产出请求 fact；已识别封装中的动态 path/method、computed/spread/重复 method 或不支持的调用形状保留 unknown，嵌套函数中的调用不提取。v1 只识别 `/api/...` 字面量路径，method 取明确静态值或 wrapper 默认 GET；参数化范围见下方 v2 扩展。
5. request facts 与来源模块、源码范围绑定并持久化在同一 immutable index。v1 概览只将唯一相同字面 method/path 的客户端 fact 与 FastAPI 静态声明路由关联为 `method_path_shape`；当前 shape matcher 见下方 v2 扩展。两端 evidence 均从该 index 读取；不写入 `CodeEdge` 或 calls/imports 聚合。base URL 可能是远端，因此该关联只表示形状一致，不证明实际运行请求目标。
6. `ProjectOverview` 用独立 HTTP associations、coverage 及 `complete`/`incomplete`/`not_analyzed` 描述分析状态。路由候选不完整、HTTP/关系 deadline 或 association 可见上限都会降低状态；无 TypeScript facts 标记的旧 index 返回 `not_analyzed`；v1 index 继续读取既有 literal facts，并在 `path_analysis` 中标为 `literal_only`。`complete` 只表示本规则范围内的读取完整，不代表所有运行时 HTTP 调用都已覆盖。
7. PC 架构画布将 HTTP association 聚合为独立虚线有向边并提供独立过滤，不混入静态 imports/calls；折叠模块端点由目录节点代表，40 模块上限隐藏端点和未映射目录计入提示。下方 association 列表保留客户端及服务路由双端证据入口。

## 替代方案

- 用正则或字符串查找 TypeScript：不能证明语法结构或作用域/遮蔽，容易制造错误定义和关系。
- 导入或运行被分析前端、FastAPI 服务以读取运行时请求与路由：会执行不可信目标代码并引入环境副作用，越过只读索引边界。
- 把 HTTP 匹配当作 `calls` 边：混淆静态模块关系和跨网络运行行为，也会令现有 graph 查询错误解释证据。
- 为 TypeScript 另建通用缓存框架：第一版的冷解析受文件/字节/时间/输出限制，额外 cache 维护不改善事实可靠性。

## 验收与证据

离线测试覆盖 AST facts、parser 缺失/错误、路径与预算边界、unknown 保留、同快照双端 evidence、旧 index、刷新和 PC canvas。CodeTrail 在独立数据目录实际索引后，PC 浏览器显示 20 条 TypeScript 静态导入关联及 `getHealth` → `GET /api/health`；客户端和 FastAPI 证据的完整 content hash 经 HTTP 与真实 stdio MCP `get_overview` 一致。具体索引、调用方式、命令及未完成覆盖见 `memory-bank/progress/2026-10-06-typescript-http-overview.md`。

## 后果

新 index 可将 Python 与 TypeScript/TSX 模块纳入相同快照身份；Python cache 行为保持不变。HTTP shape associations 是独立只读证据，动态 URL、其他 fetch 封装、路径别名、挂载前缀和实际请求目的地仍不确认。Node.js 与锁定 TypeScript package 是含 TS/TSX 项目的 API 部署前提；旧 immutable index 保持可读：无 TypeScript facts 标记为 `not_analyzed`，v1 facts 标为 `literal_only`。

## v2 参数化路径扩展（2026-10-06）

当前实现使用 `typescript-facts-v2` 和 index identity namespace `relations-v7`。只接受完整 URL path segment 中未遮蔽的 `encodeURIComponent(functionParameter)`；字面路径仍按 literal 保存，模板用匿名 `{*}` segment 表示，不把字面字符串 `{*}` 解释为参数。末尾 query 只接受调用函数体顶层 `const params = new URLSearchParams(...)` 后的 `?${params.toString()}`；`params` 参数/嵌套块遮蔽、其它 query 插值、片段、未编码/半段 interpolation、全局构造器遮蔽或赋值均保留 unknown。

v2 matcher 按 segment 检查客户端路径和 FastAPI 声明路径的 overlap 与完整 shape。参数化客户端只在唯一完整同形路由关联；同一模板还有固定字面路径可匹配时记为 ambiguous，只有部分字面覆盖时记为 unknown；不相交的 path converter 不影响其它候选，相交的 converter 保守标 unknown。路由候选截断时不能用可见候选证明唯一性。Association 保留服务端声明 path，并额外给出客户端模板；它仍是 method/path shape 关联，不确认运行时请求目标。

`path_analysis` 与原 `analysis_status` 分开：新 v2 index 为 `parameterized`，兼容 v1 immutable snapshot 为 `literal_only`，没有 TypeScript fact marker 的旧 snapshot 为 `not_analyzed`。SQLite 迁移只为旧 HTTP facts 加 literal 默认值，不重写旧 snapshot 或 request identity；同源规则升级通过 `relations-v7` 生成新的 index id。PC 画布与列表继续独立显示 HTTP 关系，参数模板有单独类型标签，双端源码证据保持 index/hash 校验。该扩展的自动化与自用项目验收证据追加在对应阶段记录中。

## v3 静态调用扩展（2026-10-07；历史规则）

TypeScript facts 当时升级为 `typescript-facts-v3`，调用解析语义为 `relations-v8`，index identity namespace 为 `relations-v12`。编译器 AST 为每个调用记录 caller 与精确调用行；首版解析词法可见、未遮蔽/重赋值且唯一的直接具名函数目标，包括同文件函数/函数值，以及显式 named import（含别名）指向唯一 captured 相对模块导出。调用初始化中的函数实参当时仅为定位 callback 符号，不表示包装器执行 callback，也不应作为包装器变量的确定返回值；实现最初未把这两类事实分开，见下方 v4 修正。

对象/元素方法、动态表达式、参数/局部遮蔽、重绑定、别名路径（例如 `@/lib/api`）、缺失或多义目标都保留既有 unresolved。HTTP method/path association 和 Python `ContextualCall` 不转换成 `calls`。调用 facts 不新增 API/数据库 schema 或持久化缓存；成功解析的边和 unresolved 仍绑定同一 immutable index。单次 TS 解析最多提取 5,000 个调用；合并关系边的默认预算为 6,000 条，以容纳两种语言的确定关系，超限仍按关系覆盖报告。

离线回归覆盖同文件函数、函数值、单函数包装 callback、relative import alias、遮蔽、重赋值、对象方法、path alias 和调用预算断言。CodeTrail 自用整仓的新 index、HTTP/冷 stdio/当前宿主 MCP 一致性以及 PC 源码跳转验收记在当前阶段记录；未实际执行这些验收前不得据此宣称端到端完成。

## v4 静态调用 soundness 修正（2026-10-07）

调用目标先按调用点检查当前块、函数参数/局部绑定和祖先闭包绑定，再考虑模块 import 或模块函数；兄弟块中的 `let`/`const` 不会遮蔽调用，同一可见作用域的多个同名声明保留 ambiguous。命名函数表达式体内的参数和 `var` 局部绑定优先于其内部名称。不能证明唯一 callable binding 时保留 unresolved。

函数调用初始化中的一个函数实参仍可独立生成函数符号和源码定位，但未知包装器返回值不再作为该变量的调用目标或导出目标。HTTP path 分析识别 `typescript-facts-v4`；关系语义为 `relations-v9`，新索引 identity 为 `relations-v13`。旧 `relations-v12` index、Session、Run 和 evidence 保持不变，语义修复通过新索引生效。

本修正的 TypeScript 索引/关系定向测试 18 passed，freshness 测试 15 passed，全后端测试 246 passed；compileall、共享/本地 docs 检查及 diff 检查通过。父任务只读内存快照 probe 有 2514 symbols、issues=[]，并验证 `openArchitectureSource → readProjectSource` 源码 `calls/ast` 边位于 `apps/web/app/workbench.tsx:L2922`；索引 `index-self-readonly-review-a58a7582c8a576a5` 未持久化。新 rules-v4 持久化索引及 HTTP/stdio/宿主 MCP/PC 流程仍 UNVERIFIED；整仓阶段保持 partial。

## v5 HTTP wrapper 识别（2026-10-09）

当前 TypeScript facts 规则升为 `typescript-facts-v5`。`requestJson` wrapper 只有在直接函数体中存在唯一 `fetch`、path 与 apiBaseUrl 仍按透明形式拼接、请求 options 的唯一 rest 来源可证为 `init` 时才产出客户端请求事实；识别已验证的 `{ timeoutMs, ...requestInit } = init` 形状。扫描跳过嵌套回调中的 fetch，嵌套调用不参与唯一性判断。该扩展使本地导入客户端的 `/api/imports/local` request fact 可进入既有 HTTP association；facts 版本进入 freshness，因此 v4 及更旧 immutable index 继续可读但不会自动得到该新关联，需要显式 refresh 创建新 index。UI action facts 仍在读取 journey catalog 时从同一个快照的 TSX 源码有界冷解析，不写入索引关系或构造新 `CodeEdge`。
