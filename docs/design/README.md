# 设计文档

本目录维护 CodeTrail 的界面规范、可浏览原型和截图证据。`/prototype` 仍是不调用 API 的 UI-S1 评审页。生产 `/` 的 UI-S2 规范与清单在 [workspace-ui-spec.md](workspace-ui-spec.md)，该文记录迁移已经完成；当前真实 API 与工作台行为仍以 [工作台子系统说明](../../apps/web/workbench.md) 和 [Web 工作台 README](../../apps/web/README.md) 为准。规范中的截图不替代流程证据，汇总状态见 [MVP 验收清单](../mvp-acceptance.md)。

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [workspace-ui-spec.md](workspace-ui-spec.md) | 项目/会话优先的工作区布局、交互、token、截图验收和原型边界 | UI-S2 已实现的工作区规范（保留 UI-S1 基线） |
| [workspace-ui-prototype/](../../apps/web/app/prototype/page.tsx) | 独立 Next.js `/prototype` 可浏览原型；演示数据，不调用 API | UI-S1 已交付 |

## 使用原型

从仓库根目录执行 `pnpm web:dev`，打开 `http://localhost:3000/prototype`。右上角的“预览状态”可以切换空工作区、项目知识页、空会话、有消息会话、证据面板、功能图面板和保存失败；这些状态只用于评审 UI，不会写入项目、索引或 Session。

截图放在 [workspace-ui-prototype/screenshots/](workspace-ui-prototype/screenshots/)；文件名包含目标视口，规范中的截图矩阵与需求映射以该目录为准。

## 阶段边界

UI-S1 交付规范基线、独立原型和视觉证据。UI-S2 已按该规范迁移生产 `/` 工作台，并复用既有 HTTP/MCP 契约；清单里仍与其他验收记录冲突的影响边界见 [MVP 验收清单](../mvp-acceptance.md)。
