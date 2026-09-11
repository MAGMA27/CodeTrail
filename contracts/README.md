# 数据契约 Schema

## 职责与边界

`contracts` 保存由后端 Pydantic 契约生成、供前端和固定响应校验使用的 JSON Schema。它不拥有运行时业务逻辑，也不手工维护与 Python 声明重复的字段定义。

## 入口与目录导航

- [schemas/codetrail.json](schemas/codetrail.json)：当前聚合 Schema。
- [后端契约声明](../apps/api/app/contracts.py)：字段、校验和版本的唯一机械来源。
- [Schema 生成器](../apps/api/app/export_schema.py)：可重复生成 Schema。

## 接口与依赖

契约版本当前为 `0.1.0`。Project、ProjectListResponse、ProjectIndexListResponse、本地导入请求/attempt/状态响应和 A1 MCP 结构化结果与现有 Index/Graph 契约一起导出；HTTP 路由以 FastAPI response_model 为边界，前端在 [apps/web/lib/api.ts](../apps/web/lib/api.ts) 执行运行时形状校验。

## 数据与生命周期

Schema 不保存项目或索引数据，只描述请求/响应形状。Project 的 latest/building 与 available index 关联、ImportAttempt 的 phase/status 和 ready/failed 生命周期必须满足声明旁的 Pydantic 校验；源码、图和证据仍由同一 `index_id` 绑定。

## 使用与配置

从仓库根目录生成：

```powershell
uv run --directory apps/api python -m app.export_schema
pnpm contracts:check
```

禁止直接编辑 `schemas/codetrail.json`；修改 [contracts.py](../apps/api/app/contracts.py) 后重新生成。

## 错误与限制

Schema 检查不能证明 HTTP 业务流程、项目隔离、路径边界或浏览器切换正确；固定响应也不代表真实模型或 GitHub 导入已实现。

## 验证

`pnpm contracts:check` 验证 Schema 可重生成和固定响应包；项目接口的归属、持久化与重启行为由 `apps/api/tests/test_api.py` 验证。

## 关联文档

- [数据契约子系统说明](../docs/subsystems/contracts.md)
- [API 子系统说明](../docs/subsystems/api.md)
- [工作区与项目概览决策](../docs/decisions/2026-09-11-project-workspace.md)
