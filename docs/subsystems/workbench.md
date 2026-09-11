# 工作台

## 状态

已实现基于真实索引 API 的多项目工作台；项目创建/列表/切换、本地路径导入、导入状态/重试、索引归属与刷新恢复、规则驱动项目概览、显式 O2 功能生成和概览/功能到关系图/源码证据联动已实现，并能打开 MCP 生成的固定版本证据引用。Issue 分析、Run 生命周期和报告界面尚未实现。

## 职责与范围

通过公开 API 创建/列出/切换项目，提交服务端可见的本地路径并显示 ImportAttempt，创建或打开项目所属索引，默认展示绑定当前快照的 O1 入口/模块概览；用户可显式选择 fixture/real 与源码范围，请求 O2 功能生成，再展开入口、模块、符号和证据，也可查看受限搜索结果、静态邻接子图、分组折叠和快照源码证据；不直接访问 SQLite 或浏览器宿主路径。

## 接口与依赖

[apps/web README](../../apps/web/README.md) 记录目录职责；[page.tsx](../../apps/web/app/page.tsx) 挂载工作台，[workbench.tsx](../../apps/web/app/workbench.tsx) 管理交互，[api.ts](../../apps/web/lib/api.ts) 验证 HTTP 边界，[graph.ts](../../apps/web/lib/graph.ts) 管理图合并和分组；Next.js 局部规则见 [AGENTS.md](../../apps/web/AGENTS.md)。

## 数据与生命周期

项目状态覆盖列表载入、空列表、就绪和错误；导入状态显示 queued/acquiring/indexing/ready/failed/interrupted 并提供失败/中断重试；索引状态覆盖未打开、创建中、载入中、就绪、部分完成和错误。切换 project_id 会取消或忽略旧请求，清空旧 index_id、图、源码和错误，再恢复该项目最新可用索引；浏览器只保存最近项目 ID，项目与索引数据从服务端持久化恢复。

证据链接携带 project_id、index_id、路径、行范围、content_hash 和 evidence_id。工作台先检查 project_id 存在且 index_id 属于该项目，再读取项目作用域的固定快照；任何返回的 index、路径、行范围、hash 或 evidence_id 不一致都会显示错误，不会回退到当前 latest index。概览入口只接受 `python-entry-v1` 的 `__main__.py`/顶层 main guard 结果；O2 功能只展示同一生成结果实际返回的 symbol/evidence，明确显示 fixture/real、用量、未查询模块、局限和 partial/insufficient/failed 状态；入口/功能证据进入既有源码检查器，模块选择进入同一快照的关系图，所有概览/生成功能、路径/模块请求都有显式上限。

## 错误与限制

前端可以提交服务端 API 可见的绝对路径，但不读取或解析浏览器本机目录；服务端负责允许根目录、符号链接/junction 和 loopback 写边界。项目作用域端点拒绝不属于当前 project_id 的 index_id。概览未识别入口时明确标记未知，不把目录或名为 `main` 的函数冒充入口；动态调用保留 unresolved。功能生成显式触发，real 未配置模型时显示失败且不回退 fixture；切换项目/索引、模式/范围变化和重试都会取消或忽略过期请求。响应经过运行时形状校验，API 停止或返回错误时保留明确失败状态。

## 验证

pnpm --dir apps/web test、pnpm web:typecheck 和 pnpm web:build；浏览器核对本地路径导入、索引/项目切换、刷新恢复、概览入口/模块跳转、fixture 功能生成、功能证据 URL 打开、real 未配置失败/重试、导入错误状态、MCP 证据 URL 打开与 hash/归属校验，以及既有搜索、关系方向、分组折叠、源码证据、unresolved、API 停止和恢复重试。

## 相关决策

React Flow 固定为 `@xyflow/react@12.11.6`，只渲染当前 API 返回的受限静态子图；后续重要取舍记录到 [决策目录](../decisions/README.md)。
