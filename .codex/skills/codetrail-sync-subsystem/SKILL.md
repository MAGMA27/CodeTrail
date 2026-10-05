---
name: codetrail-sync-subsystem
description: 在 CodeTrail 实现变化后同步受影响的子系统文档、生成契约和决策状态，或按要求修正文档漂移与格式。
---

# 同步子系统

读取 docs/development.md 和 docs/subsystems/README.md，以实际 diff、源码与已运行验证确定当前事实。识别本次变更，避免把其他未完成工作一起标记完成。

更新受影响的唯一权威文档。子系统总索引登记遵循 docs/templates/subsystem.md；公共 API 细节以声明及 OpenAPI 为准，JSON Schema 从 Pydantic 生成；文档解释语义、限制、错误和生命周期，引用源码而不复制完整字段表。新建、移动或删除页面同步索引和入站链接。新增源码必须在子系统索引的 owners 中有归属。

每次同步先检查受影响源码最近的 README.md、模块 docstring/JSDoc 和适用 AGENTS.md。遵循 docs/development.md 的“就近文档”规则：在已授权开发或文档维护范围内，独立职责目录缺少 README 时直接按 docs/templates/local-readme.md 创建，依据当前代码填充，不等待用户逐一要求。优先复用已有 README；有独立局部开发约束才创建 AGENTS.md。本次仅改 skill 或全局规范时不触发全仓局部文档补建。

局部 README 拥有该目录的职责、用法、限制和验证入口，具体方法契约留在声明旁。集中子系统总索引维护跨目录协作与导航；旧逐子系统导航页归档且停止维护，新事实直接维护就近文档，同步子系统索引及父 README 的入口，避免维护两个版本。多个模块尚未拆目录时在共享 README 建立模块导航，精确语义放模块 docstring。缺失文档需要描述已经存在的事实，因此即使本次行为未变也应在受影响范围补齐；无事实变化的免编辑规则仅适用于文档已经充分的情况。

契约变化时从根目录执行 uv run --directory apps/api python -m app.export_schema，再运行 pnpm contracts:check。生成物不得手改；现有导出器未覆盖的接口应明确报告，不声称全量覆盖。

重要决策按实际实施状态更新；只有验收证据支持时标记 implemented。被替代的决策标记 superseded 并链接新记录。memory-bank 存在时同步受影响的进度与下一步，但保留其本地、不纳入 Git 的约定。

执行 pnpm docs:check；涉及本地计划文档时另执行 pnpm docs:check:local。文档没有事实变化可以不编辑，说明判断依据。输出修改的事实归属、检查结果和遗留漂移，不在本 skill 中把格式通过当成语义审查通过。
