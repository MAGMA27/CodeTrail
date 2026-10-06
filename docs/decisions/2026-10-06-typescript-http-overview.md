# TypeScript 静态索引与 HTTP 形状关联

Status: implemented
- Date: 2026-10-06
- Scope: apps/api indexing/storage/overview, ProjectOverview contract, PC architecture canvas

## 问题

CodeTrail 的不可变索引原先只捕获 Python 源码，无法展示前端 TypeScript 静态定义、相对导入或客户端请求与 FastAPI 声明之间的证据路径。新增分析必须继续读取固定快照、不运行仓库代码，并与 Python 的 AST/cache 统计、静态 imports/calls 语义及旧快照读取边界分开。

## 决定

1. 同一有界快照捕获 `.py`、`.ts`、`.tsx`，freshness 复用同样的扩展名、生成目录排除和文件/字节/时间预算。快照规则版本包含 TypeScript 编译器版本和 `typescript-facts-v1`；捕获预算进入 configuration hash。读取后若实际累计字节越过预算，扫描标记不完整，不能返回一致 freshness。
2. TypeScript/TSX 使用 API 固定 helper 调用 `apps/web` 锁定的 TypeScript 5.9.3 编译器，以 compiler AST 解析内存中的 captured source text；不加载或执行目标仓库模块、依赖或配置。API 主机对含 TypeScript 的索引要求 Node.js 和锁定 package 可用；缺少运行时/包、超时、输出超限或语法诊断会显式令新索引失败。TypeScript 第一版每次有界冷解析，不持久化事实缓存，也不计入 Python AST/cache hit。
3. 为 TS/TSX 捕获模块、函数/类定义及静态/动态模块 specifier。仅精确且唯一落在同一快照内的安全相对导入生成现有 `CodeEdge(kind="imports", extraction_method="ast")`；路径别名、包导入、动态导入、越界、歧义和缺失目标保留 unresolved。符号身份含相对路径，避免 `foo.py` 与 `foo.ts` 撞 ID。
4. HTTP 客户端只对 AST 能确认的透明顶层 `requestJson`→`fetch(apiBaseUrl + path)` wrapper 提取其直接顶层函数调用。未通过透明封装验证的 wrapper 不产出请求 fact；已识别封装中的动态 path/method、computed/spread/重复 method 或不支持的调用形状保留 unknown，嵌套函数中的调用不提取。字面量路径限 `/api/...`，method 取明确静态值或 wrapper 默认 GET。
5. request facts 与来源模块、源码范围绑定并持久化在同一 immutable index。概览只将唯一相同 method/path 的客户端 fact 与 FastAPI 静态声明路由关联为 `method_path_shape`，两端 evidence 均从该 index 读取；不写入 `CodeEdge` 或 calls/imports 聚合。base URL 可能是远端，因此该关联只表示形状一致，不证明实际运行请求目标。
6. `ProjectOverview` 用独立 HTTP associations、coverage 及 `complete`/`incomplete`/`not_analyzed` 描述分析状态。路由候选不完整、HTTP/关系 deadline 或 association 可见上限都会降低状态；旧 index 若规则身份没有 TypeScript facts 标记，返回 `not_analyzed`，不会把无 fact 伪装为无请求。`complete` 只表示本规则范围内的读取完整，不代表所有运行时 HTTP 调用都已覆盖。
7. PC 架构画布将 HTTP association 聚合为独立虚线有向边并提供独立过滤，不混入静态 imports/calls；折叠模块端点由目录节点代表，40 模块上限隐藏端点和未映射目录计入提示。下方 association 列表保留客户端及服务路由双端证据入口。

## 替代方案

- 用正则或字符串查找 TypeScript：不能证明语法结构或作用域/遮蔽，容易制造错误定义和关系。
- 导入或运行被分析前端、FastAPI 服务以读取运行时请求与路由：会执行不可信目标代码并引入环境副作用，越过只读索引边界。
- 把 HTTP 匹配当作 `calls` 边：混淆静态模块关系和跨网络运行行为，也会令现有 graph 查询错误解释证据。
- 为 TypeScript 另建通用缓存框架：第一版的冷解析受文件/字节/时间/输出限制，额外 cache 维护不改善事实可靠性。

## 验收与证据

离线测试覆盖 AST facts、parser 缺失/错误、路径与预算边界、unknown 保留、同快照双端 evidence、旧 index、刷新和 PC canvas。CodeTrail 在独立数据目录实际索引后，PC 浏览器显示 20 条 TypeScript 静态导入关联及 `getHealth` → `GET /api/health`；客户端和 FastAPI 证据的完整 content hash 经 HTTP 与真实 stdio MCP `get_overview` 一致。具体索引、调用方式、命令及未完成覆盖见 `memory-bank/progress/2026-10-06-typescript-http-overview.md`。

## 后果

新 index 可将 Python 与 TypeScript/TSX 模块纳入相同快照身份；Python cache 行为保持不变。HTTP shape associations 是独立只读证据，动态 URL、其他 fetch 封装、路径别名、挂载前缀和实际请求目的地仍不确认。Node.js 与锁定 TypeScript package 是含 TS/TSX 项目的 API 部署前提；旧 immutable index 保持可读，但其 TypeScript coverage 明确为 `not_analyzed`。
