# 工作台

## 状态

已实现基于真实索引 API 的多项目代码关系工作台；项目创建/列表/切换、索引归属与刷新恢复已实现，并能打开 MCP 生成的固定版本证据引用。Issue 分析、Run 生命周期、报告界面和功能概览尚未实现。

## 职责与范围

通过公开 API 创建/列出/切换项目，创建或打开项目所属索引，展示受限搜索结果、静态邻接子图、分组折叠和快照源码证据；不直接访问 SQLite。

## 接口与依赖

[apps/web README](../../apps/web/README.md) 记录目录职责；[page.tsx](../../apps/web/app/page.tsx) 挂载工作台，[workbench.tsx](../../apps/web/app/workbench.tsx) 管理交互，[api.ts](../../apps/web/lib/api.ts) 验证 HTTP 边界，[graph.ts](../../apps/web/lib/graph.ts) 管理图合并和分组；Next.js 局部规则见 [AGENTS.md](../../apps/web/AGENTS.md)。

## 数据与生命周期

项目状态覆盖列表载入、空列表、就绪和错误；索引状态覆盖未打开、创建中、载入中、就绪、部分完成和错误。切换 project_id 会取消或忽略旧请求，清空旧 index_id、图、源码和错误，再恢复该项目最新可用索引；浏览器只保存最近项目 ID，项目与索引数据从服务端持久化恢复。

证据链接携带 project_id、index_id、路径、行范围、content_hash 和 evidence_id。工作台先检查 project_id 存在且 index_id 属于该项目，再读取项目作用域的固定快照；任何返回的 index、路径、行范围、hash 或 evidence_id 不一致都会显示错误，不会回退到当前 latest index。

## 错误与限制

前端只提交服务端配置的 repository_id，不接受宿主路径；项目作用域端点拒绝不属于当前 project_id 的 index_id。响应经过运行时形状校验，过期请求通过 AbortController 和递增请求 ID 忽略，API 停止或返回错误时保留明确失败状态。

## 验证

pnpm --dir apps/web test、pnpm web:typecheck 和 pnpm web:build；浏览器核对两个项目创建、分别建索引、切换、刷新恢复、同名源码隔离、MCP 证据 URL 打开与 hash/归属校验，以及既有搜索、关系方向、分组折叠、源码证据、unresolved、API 停止和恢复重试。

## 相关决策

React Flow 固定为 `@xyflow/react@12.11.6`，只渲染当前 API 返回的受限静态子图；后续重要取舍记录到 [决策目录](../decisions/README.md)。
