# 决策：固定快照模块定义分页

Status: implemented

## 问题

职责组和源码目录已经展开到固定索引模块，用户仍需搜索或从模块进入邻接图才能找到定义。搜索是相关性检索，neighbors 只遍历现有静态关系，两者都不能作为模块内完整定义列表。继续从 live source 重新解析会破坏 Project/Index 的快照边界。

## 决定

增加项目级 `GET /api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols` 和 MCP `list_module_symbols`，两者使用同一 WorkspaceService、Pydantic page contract 与 storage query。请求要求指定归属项目、固定索引、module Symbol ID；找不到 module root 或给入非 module Symbol 返回 `not_found`，Project/Index 归属沿用既有 not-found 语义。每页默认 40、最大 40，定义仅来自该 module root 的 snapshot path，kind 限定为 `class`、`function`、`test`；根对象始终回显以消除 overview 的模块可见预算对页上下文的影响。

按 `(start_line, symbol_id)` 做稳定 keyset 排序，cursor 采用现有 base64 JSON 风格，带固定版本、完整 SHA-256 的 project/index/module scope fingerprint 和最后排序键，输入/输出均限制 500 字符。游标仅用于作用域绑定，不是签名机制。每页包含 module root 的完整 JSON 输出预算为 40,000 字节，在 HTTP 与 MCP 共享；预算截短时返回已有定义及可继续 cursor，单个首项也超预算时返回 `budget_exceeded`，避免空页循环。使用兼容旧 SQLite 文件的 `CREATE INDEX IF NOT EXISTS` 查询索引，不增 `contains` edge，不改 immutable snapshot、Symbol identity 或 parser rules。

工作台在职责/目录模块行提供单独的定义列表入口，并保留原关系图入口。定义按页显示，点击后复用既有同 project/index `read_source` evidence 面板。请求随模块组、项目或索引切换取消并用 request ID 屏蔽迟到响应；续页失败保留已显示页面和原 cursor。列表说明内容只是 indexer 当前捕获的定义，不能表达完整解析覆盖。没有可持久化的通用 class/method parent ID，因此页面扁平列出模块路径下的 definitions，不生成父子 scope。

## 替代方案

- 用 FTS/search 补足模块浏览：FTS 是相关性候选并按结果上限截断，无法作为完整模块目录。
- 用 `get_neighbors` 或 `contains` edge：neighbors 只返回有限关系邻居；增加 edge 会改变快照图语义并需要独立版本和兼容规则。
- 按需重新读/解析当前目录：会绕过不可变 index，可能把改动后的 live source 混入旧证据。
- 为定义创建通用 containment parent schema：现有 snapshot 没有可靠持久化的 class/method parent 关系，此次只交付可验证的模块文件级清单。

## 验收与证据

- Storage/API/MCP 定向测试覆盖稳定多页、同起始行 Symbol ID tie-break、cursor 作用域/版本/字段校验、非模块拒绝、共享响应预算和默认 MCP 工具注册；此前 69 项通过，修正的 2 个 MCP 注册 fixtures 后单独通过。Contracts schema 检查通过。
- 对同一 project/index/module 比较真实 HTTP 与冷 stdio MCP 的 page 1、page 2，两个响应均逐对象一致，各有 40 项并返回续页 cursor；使用现存 immutable index `index-import-c929f2f2b7a3e5b435ddaee3859158fe-relations-v9-rebuild-08759c7862754f11`，snapshot hash `08759c7862754f11427ca4913cd53cd55cb8e5e085029ae589c58d1c15876c48`，未刷新或覆盖旧快照。
- PC Chrome 验证职责→`apps.api.app.service` 模块→definitions 40→80→`IndexService` source。源码来自 `apps/api/app/service.py` L702–L901，同 index source hash 前缀 `d8a49da970f9`；返回定义列表保留已加载 80 项。打开来源时切换到 Storage 职责会立即取消并清除旧来源和模块列表，Storage definitions 重新以 Storage 模块为 root 显示；原关系图入口仍在。
- `pnpm --dir apps/web test` 35 项通过；`pnpm web:typecheck`、`pnpm web:build`、`pnpm contracts:check`、`pnpm docs:check` 与 `pnpm docs:check:local` 通过。无窄屏验收；没有保存截图文件。

## 后果

module page 是同一 immutable index 中当前已保存的 definitions 子集，未捕获或无法解析的内容不会通过分页补齐。页数由稳定 keyset 续传；接口不提供全量计数，调用方应展示已加载项和 `has_more`，不能据空页推断解析覆盖。
