# 设计文档

本目录维护 CodeTrail 的目标界面规范、可浏览原型和截图证据。这里的目标设计不等于生产页面已经实现；当前真实 API 与工作台行为仍以 [工作台子系统说明](../subsystems/workbench.md) 和 [Web 工作台 README](../../apps/web/README.md) 为准。

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [workspace-ui-spec.md](workspace-ui-spec.md) | 项目/会话优先的工作区布局、交互、token、截图验收和原型边界 | UI-S1 目标规范 |
| [workspace-ui-prototype/](../../apps/web/app/prototype/page.tsx) | 独立 Next.js `/prototype` 可浏览原型；演示数据，不调用 API | UI-S1 已交付 |

## 使用原型

从仓库根目录执行 `pnpm web:dev`，打开 `http://localhost:3000/prototype`。右上角的“预览状态”可以切换空工作区、项目知识页、空会话、有消息会话、证据面板、功能图面板和保存失败；这些状态只用于评审 UI，不会写入项目、索引或 Session。

截图放在 [workspace-ui-prototype/screenshots/](workspace-ui-prototype/screenshots/)；文件名包含目标视口，规范中的截图矩阵与需求映射以该目录为准。

## 阶段边界

UI-S1 交付规范、独立原型和视觉证据。UI-S2 才会按规范重构真实 `/` 工作台，并复用现有 HTTP/MCP 业务契约；Run、Agent、SSE 和 Issue 分析仍由后续阶段实施。
