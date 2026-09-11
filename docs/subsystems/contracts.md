# 数据契约

## 状态

已有 Pydantic 契约和 Schema 导出；前端在 [api.ts](../../apps/web/lib/api.ts) 对 Project、Index、本地/GitHub 导入 attempt 和公开查询响应做运行时形状校验；MCP 的结构化结果、领域错误、导入状态和固定版本证据引用也由同一契约声明；契约中存在 Run 定义不代表 Run 服务已实现。

## 职责与范围

定义 Project、Index、本地/GitHub 导入请求与 attempt、图、证据、MCP 结果/错误/引用与报告的数据约束；HTTP 端点由 API 子系统拥有。

## 接口与依赖

[声明](../../apps/api/app/contracts.py) 和 [导出器](../../apps/api/app/export_schema.py) 是入口；前端消费边界数据。

## 数据与生命周期

Project 通过 project_id 管理 repository_id 与可用 index_id，图和证据仍通过 index_id 绑定快照；latest/building 索引状态、ImportAttempt 的 phase/status、MCP 证据引用和事件序列具有校验规则，引用的 hash、行范围和 evidence_id 必须与返回证据一致。

## 错误与限制

静态边与候选关联分开；GitHub attempt 的 source URL、requested ref、resolved commit、content hash 和 cleanup 状态有明确字段；Schema 导出范围以生成器 models 为准，已包含本地/GitHub 导入和 A1 MCP 领域结果，不保证覆盖全部 HTTP 模型或 SDK 传输包装。

## 验证

`pnpm contracts:check` 验证 Project 关联约束、导入生命周期、固定响应、MCP 证据引用、非法引用和 Schema 可重复性。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
