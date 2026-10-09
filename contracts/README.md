# 数据契约 Schema

## 职责与边界

`contracts` 保存由后端 Pydantic 契约生成、供前端和固定响应校验使用的 JSON Schema。它不拥有运行时业务逻辑，也不手工维护与 Python 声明重复的字段定义。

## 入口与目录导航

- [reference.md](reference.md)：当前实现、生命周期、限制与验收入口。

- [schemas/codetrail.json](schemas/codetrail.json)：当前聚合 Schema。
- [后端契约声明](../apps/api/app/contracts.py)：字段、校验和版本的唯一机械来源。
- [Schema 生成器](../apps/api/app/export_schema.py)：可重复生成 Schema。
- [外部 Agent 功能结果提交决策](../docs/decisions/2026-10-09-external-agent-feature-submission.md)：来源、固定 Run 证据和服务端模型用量边界。

## 接口与依赖

本目录的接口与依赖由 [reference.md](reference.md) 维护；精确字段与行为约束以源码声明为准。

## 数据与生命周期

本目录的数据与生命周期由 [reference.md](reference.md) 维护；精确字段与行为约束以源码声明为准。

## 使用与配置

从仓库根目录生成：

```powershell
uv run --directory apps/api python -m app.export_schema
pnpm contracts:check
```

禁止直接编辑 `schemas/codetrail.json`；修改 [contracts.py](../apps/api/app/contracts.py) 后重新生成。

## 错误与限制

本目录的错误与限制由 [reference.md](reference.md) 维护；精确字段与行为约束以源码声明为准。

## 验证

本目录的验证由 [reference.md](reference.md) 维护；精确字段与行为约束以源码声明为准。

## 关联文档

- [数据契约子系统说明](reference.md)
- [API 子系统说明](../apps/api/api.md)
- [P2-R2 模型驱动 Issue 分析决策](../docs/decisions/2026-09-12-run-model-analysis.md)
- [工作区与项目概览决策](../docs/decisions/2026-09-11-project-workspace.md)
