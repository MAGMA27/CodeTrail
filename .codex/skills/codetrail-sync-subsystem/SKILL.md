---
name: codetrail-sync-subsystem
description: 在 CodeTrail 实现变化后同步受影响的子系统文档、生成契约和决策状态，或按要求修正文档漂移与格式。
---

# 同步子系统

读取 docs/development.md 和 docs/subsystems/README.md，以实际 diff、源码与已运行验证确定当前事实。识别本次变更，避免把其他未完成工作一起标记完成。

更新受影响的唯一权威文档。子系统页面遵循 docs/templates/subsystem.md；公共 API 细节以声明及 OpenAPI 为准，JSON Schema 从 Pydantic 生成；文档解释语义、限制、错误和生命周期，引用源码而不复制完整字段表。新建、移动或删除页面同步索引和入站链接。新增源码必须在子系统索引的 owners 中有归属。

契约变化时从根目录执行 uv run --directory apps/api python -m app.export_schema，再运行 pnpm contracts:check。生成物不得手改；现有导出器未覆盖的接口应明确报告，不声称全量覆盖。

重要决策按实际实施状态更新；只有验收证据支持时标记 implemented。被替代的决策标记 superseded 并链接新记录。memory-bank 存在时同步受影响的进度与下一步，但保留其本地、不纳入 Git 的约定。

执行 pnpm docs:check；涉及本地计划文档时另执行 pnpm docs:check:local。文档没有事实变化可以不编辑，说明判断依据。输出修改的事实归属、检查结果和遗留漂移，不在本 skill 中把格式通过当成语义审查通过。
