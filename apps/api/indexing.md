# 快照与关系

## 状态

已实现 Python 与 TypeScript/TSX 的有界不可变源码快照、定义、相对导入、静态调用及保守 HTTP path facts，以及本地 freshness 检查和显式索引更新；Python 继续使用文件级定义/关系事实缓存，TypeScript 使用每次有界冷 AST 解析，不计入 Python AST/cache 统计。`codetrail init` 从 CodeTrail 源码工作区发现锁定的 TypeScript 5.9.3 编译器；编译器不随安装包分发，缺失时 Python 索引仍可用，含 TypeScript/TSX 的新索引会显式失败。TypeScript 语法错误、锁定解析器不可用和快照扫描不完整都会显式令新索引 failed。源码捕获规则为 `source-v4`，TypeScript 编译器锁定 `5.9.3`，当前静态 facts 规则为 `typescript-facts-v5`，关系语义为 `relations-v9`，Python 关系事实缓存规则/格式为 `python-relation-facts-v4` / `relation-facts-v4`，索引身份为 `relations-v13`；定义/关系目标仍基于当前快照全量解析，不是完整增量索引。v5 只识别 `requestJson` wrapper 直接函数体中的唯一 `fetch`，并允许已验证的 `init` timeout-rest 转发形状；嵌套回调中的 `fetch` 不纳入客户端请求事实。v4 及更旧 immutable snapshots 不会原地重写，新 association 需通过 freshness 和显式 refresh 创建新 index。

Python calls 可将安全实例构造点绑定到构造器直接赋值的 `self.field`，再解析 `self.field.method()`。仅当当前快照中该类的所有可解析构造调用都把同一个安全类实例传给唯一参数、构造器与参数绑定没有动态展开/重赋值、类无继承/装饰器/`__new__` 或自定义属性协议，并且同名字段没有不兼容写入或可识别类引用逃逸时才生成静态边。裸类名作为普通调用实参、直接返回类名、字段/局部别名和绕过 `__init__` 的 `Class.__new__(Class)` 都使该类的实例来源不再可证明；仅有未知动态反射仍不构成运行时保证。类型注解不会单独证明实例类型；工厂、未知构造点、字段变更和未证明为同类实例的外部多段字段写继续 unresolved。该边只表示快照中的静态语法一致，不保证运行时一定采用该类型或执行调用。

对已证明的正常构造接收者，关系索引还可记录独立 `ContextualCall`，绑定同快照实际返回的 `calls` 激活边、字段方法调用、构造位置和字段赋值位置；它仅在激活边出现在 neighbors 或 Run 工具结果时返回，不进入 `static_edges`、静态影响或概览。对象写入普通属性容器后可能发生的重绑以 `binding_mutation_after_escape_unverified` 标记；未知别名、改写、闭包和同一行多构造仍保守拒绝，旧 index 读取为空。HTTP 与 MCP 使用同一关系索引和解析结果。

## 职责与范围

Python 绝对导入可使用捕获快照中唯一嵌套 package 的短名：依据 `__init__.py` 包边界建立别名，canonical 名称优先，同名包根多义时拒绝。Symbol 的限定名与 ID 保持原值；不读取运行环境的 sys.path，也不加载或执行仓库。

读取受限 `.py`、`.ts`、`.tsx` 源码并提取静态事实；不执行目标仓库，不把动态猜测当确定边。目录扫描和 freshness 使用一致的文件数、字节、单文件大小和时间预算；同时排除 `.next`、`.turbo`、`.vercel`、`.vite`、`.parcel-cache`、`coverage`、`out`、`storybook-static`、`target` 及既有依赖/运行数据目录。

可选根文件 [`codetrail-subsystems.json`](../../codetrail-subsystems.json) 为运行时职责分组声明稳定 ID、显示名称和精确文件/目录 owner；它与文档导航 owner 分开维护。捕获器先观察这个确切路径，再应用源码文件/字节预算；metadata 自身单独限 64 KiB，不作为 Python/TS 源码解析。可读但不合法的原文与 `manifest_invalid` issue 随快照保存且不阻断代码索引；缺失配置保留原目录视图。未覆盖模块也保留在目录 fallback 中。概览仅从固定 snapshot 解析配置，并只返回一个 same-index manifest evidence；完整文件 hash 绑定 captured file，供 freshness 对改动、删除和 invalid capture 作出 changed/unknown 判断。格式与错误回退见[职责分组决策](../../docs/decisions/2026-10-06-subsystem-responsibility-manifest.md)。

## 接口与依赖

[indexing.py](app/indexing.py) 生成快照和只读 manifest；[typescript_parser.cjs](app/typescript_parser.cjs) 通过仓库锁定的 TypeScript 5.9.3 编译器 AST 解析 captured TS/TSX 源码文本，只读语法结构，不加载目标仓库模块、配置或依赖，也不执行仓库代码；缺少 Node.js 或锁定 `apps/web/node_modules/typescript` 时，含 TypeScript 的新索引会失败并报告 `parser_unavailable`。`relations.py` 将 Python 文件事实、TypeScript 相对导入和调用与当前快照目标解析分开。TypeScript 对词法可见且唯一的同文件具名函数/函数值，以及显式 named import（含别名）指向唯一 captured 相对模块导出，写入现有 `calls/ast`；词法查找优先使用调用点可见的块、函数及祖先绑定，兄弟块声明不遮蔽调用；无法证明的遮蔽或重绑定保留 unresolved。函数调用初始化中的单个回调仍可建立独立函数定义和源码定位，但未知返回值不视为变量的可调用目标，也不作为静态导出目标。属性/元素方法、动态调用、缺失或多义目标保留 unresolved。调用位置绑定调用者源码范围。Python 只将精确导入的 FastAPI/Starlette `run_in_threadpool` 首个简单 callable 参数按静态 calls 解析，目标须唯一落到快照函数或安全直接实例方法；该边使用 `ast` 来源和外层调用位置，不证明运行时一定执行。两种语言的静态导入关系均使用既有 `CodeEdge.extraction_method="ast"`。HTTP 形状关联与 Python `ContextualCall` 仍各自独立，不转换成静态调用边。`storage.py` 将 HTTP 客户端请求事实和静态关系都写入同一个 index_id。Python 缓存键包含路径、内容 hash、规则、Python AST 版本和配置 hash；TypeScript 暂不持久缓存，计数单独报告。

## 数据与生命周期

合并关系索引默认最多 6,000 条静态边，并受既有时间、unresolved 与输出预算约束，超限会明确标记覆盖不完整。

快照保存文本及 hash、规则版本和配置指纹；符号、导入、静态调用边、HTTP request facts 和后续 evidence 属于同一不可变 index_id。TypeScript 单次解析最多记录 5,000 个调用事实，超限会让关系覆盖标记为 truncated；通用关系时间/边/unresolved/output 预算仍分别生效。Python 命中缓存后按当前 index_id 重新生成 Symbol，跨文件关系仍由当前 DefinitionMaps 解析。TypeScript 解析计数不冒充 Python AST cache hit。HTTP 客户端只接受 AST 确认的透明 `requestJson`→`fetch(apiBaseUrl + path)` 封装与静态 method；默认 method 为 GET。路径 facts 分为字面量和安全参数化模板：参数段只接受完整段的未遮蔽 `encodeURIComponent(函数参数)`，query 只允许经过当前函数体顶层 `const URLSearchParams(...).toString()` 的末尾 query；动态 query、fragment、半段/未编码插值、遮蔽、赋值和不支持的形状保留 unknown。概览对客户端与 FastAPI 声明路径做段级 overlap 检查，只有完整同形且唯一的路由才关联；字面路由与模板部分重叠会使结果 ambiguous，只有部分覆盖则为 unknown，转换器或截断候选不会被当作唯一。HTTP association 独立于 `CodeEdge`，不证明 URL 实际指向本地服务。coverage 的 `path_analysis` 将当前 v2/v3 分析、旧 v1 `literal_only` 和未分析分开；旧数据库新列默认 literal，旧 snapshot 的静态字面量关联继续可读。显式刷新和 freshness 对 TS/TSX 与 Python 使用同一文件规则和预算。

工作台的 HTTP 入口链路将同一快照 association 映射回它绑定的 FastAPI handler，并单独按 handler 查询 `calls`。概览 `paths` 默认最多返回 40 条，是所有入口共享的全局预算，不能用某个 handler 未出现在列表中推断没有调用；链路也不能把同名函数、源码文本或 HTTP 形状当作静态执行边。空邻接、未解析调用和索引覆盖字段分别保留为有限观察与未知边界。

GitHub 来源是 freshness 的远端例外：由于成功导入后的归档目录会清理，检查只调用共享客户端解析已跟踪 ref 的当前 commit，不读取归档、不执行 AST/关系提取，也不改变 index。结果同时保留远端 URL、请求 ref、解析 ref、commit 与 `remote_status`；远端 commit 一致时仍需独立比较 index 的 rules/config 身份。显式 GitHub 更新再次解析并通过受限固定 commit 导入，兼容的同 commit snapshot 可复用，ref 移动以实际 attempt 的 commit 为准，失败保留旧 latest。

## 错误与限制

路径、文件、解析器输出/时间和关系预算受限。Python 缓存损坏、版本不兼容、行号不匹配或读写失败安全降级为未命中；清理不触碰历史 index/evidence。TypeScript 不持久缓存。相对路径别名（例如 `@/lib/api`）、包别名和含编码/查询/片段的导入目标不解析；即使仓库存在 tsconfig，当前固定快照不读取该配置。静态调用仅识别直接标识符且能证明唯一绑定的函数目标；属性/元素方法、动态调用、无法判定的块级或闭包遮蔽、重绑定、变量别名、未知包装器返回值、缺失或多义导入/导出及未建模的回调执行均保留 unresolved。函数调用初始化中的回调只作为独立定义定位，不证明包装器返回或执行该回调。Threadpool 转发只接受白名单模块的绝对 `from` import，且导入事件须先于调用（捕获作用域中的导入须先于嵌套函数定义）；同名本地模块、遮蔽、赋值别名、动态目标、类目标、多义方法、继承/装饰/覆盖以及捕获作用域内其他赋值均保留 unresolved。HTTP 仅为通过 AST 验证的透明 `requestJson` wrapper 提取直接顶层函数调用；未验证 wrapper 不产生请求 fact，已识别 wrapper 中的不支持模板、动态 query/method/options、路由挂载前缀、路径转换器或不唯一候选不伪造匹配，动态值保留 unknown。文件扫描不完整、读后增长越过总字节预算、读取失败、符号链接、边界逃逸、TOCTOU 或 GitHub metadata 检查异常均不能判定 freshness 一致；旧索引不会回写新的 TypeScript facts。静态目标解析可因时间/边预算不完整，unresolved 详情也可独立截断；空邻接不能证明没有关系。

## 验证

apps/api/tests/test_indexing.py、test_relations.py、test_storage.py、test_freshness.py 和 test_definition_cache.py 覆盖稳定 ID、规则版本隔离、路径约束、资源预算、定义/关系事实缓存冷热等价、共享 AST、失效/损坏/容量/并发/失败降级、内容 hash 新鲜度、显式复用/重建、旧元数据和 latest 保留；其中 test_relations.py 覆盖局部构造实例的静态/条件关系与保守拒绝边界，test_definition_cache.py 覆盖条件调用的冷热等价和跨文件重绑定，test_mcp.py 覆盖 HTTP 与冷 stdio neighbors 条件记录的一致性。本项目的缓存跨进程实测见 [索引更新与缓存效果联合验收](../../docs/decisions/2026-09-16-project-index-cache-acceptance.md)、[TypeScript 静态索引与 HTTP 形状关联](../../docs/decisions/2026-10-06-typescript-http-overview.md) 和 [Python 实例字段条件调用](../../docs/decisions/2026-10-07-python-contextual-instance-calls.md)。

## 相关决策

当前依据为现有实现和根目录开发约束；取舍记录在 [关系扫描与 unresolved 覆盖语义](../../docs/decisions/2026-09-13-relation-coverage.md)、[本地项目索引新鲜度与显式更新](../../docs/decisions/2026-09-15-index-freshness.md)、[GitHub 远端 ref 新鲜度与显式更新](../../docs/decisions/2026-09-16-github-remote-freshness.md)、[文件级定义提取缓存](../../docs/decisions/2026-09-15-definition-extraction-cache.md)、[文件关系事实缓存与全快照目标解析](../../docs/decisions/2026-09-15-relation-facts-cache.md)、[保守解析 Python threadpool 包装调用](../../docs/decisions/2026-10-06-python-threadpool-calls.md)、[Python 实例字段条件调用](../../docs/decisions/2026-10-07-python-contextual-instance-calls.md) 和 [本项目索引更新与缓存效果联合验收](../../docs/decisions/2026-09-16-project-index-cache-acceptance.md)，其他重要取舍记录到 [决策目录](../../docs/decisions/README.md)。
