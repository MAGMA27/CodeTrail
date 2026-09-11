# 工作台

## 状态

已实现基于真实索引 API 的代码关系工作台；Issue 分析、Run 生命周期和报告界面尚未实现。

## 职责与范围

通过公开 API 创建/打开索引，展示受限搜索结果、静态邻接子图、分组折叠和快照源码证据；不直接访问 SQLite。

## 接口与依赖

[page.tsx](../../apps/web/app/page.tsx) 挂载工作台，[workbench.tsx](../../apps/web/app/workbench.tsx) 管理交互，[api.ts](../../apps/web/lib/api.ts) 验证 HTTP 边界，[graph.ts](../../apps/web/lib/graph.ts) 管理图合并和分组；Next.js 局部规则见 [AGENTS.md](../../apps/web/AGENTS.md)。

## 数据与生命周期

索引状态覆盖未打开、创建中、载入中、就绪、部分完成和错误；搜索、邻接、源码请求各自维护 loading、success、empty、error 状态，切换 index_id 会清空旧图和旧选择。

## 错误与限制

前端只提交服务端配置的 repository_id，不接受宿主路径；响应经过运行时形状校验，过期请求通过 AbortController 和递增请求 ID 忽略，API 停止或返回错误时保留明确失败状态。

## 验证

pnpm --dir apps/web test、pnpm web:typecheck 和 pnpm web:build；浏览器核对真实索引创建/打开、搜索、关系方向、分组折叠、源码证据、unresolved、API 停止和恢复重试。

## 相关决策

React Flow 固定为 `@xyflow/react@12.11.6`，只渲染当前 API 返回的受限静态子图；后续重要取舍记录到 [决策目录](../decisions/README.md)。
