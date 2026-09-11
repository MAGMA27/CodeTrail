# 决策：三个项目 skill 与共享文档检查

Status: implemented

## 问题

CodeTrail 需要统一设计、子系统文档与审查流程；原本地计划中 API 状态存在漂移，且干净克隆不包含 memory-bank。

## 决定

在 .codex/skills 下维护设计、同步、审查三个 skill，由根目录 AGENTS.md 路由读取。共享规范与当前子系统文档位于 docs，本地计划保持忽略。机械检查验证文件链接、必需栏目、源码归属、决策状态与 skill 元数据；契约测试检查现有导出 Schema 和固定响应。

## 替代方案

把所有流程放在单个 skill 会模糊设计、同步与审查责任。直接复制 dsh 全套插件与文档流程不符合当前单 Agent MVP 范围。只依赖本地计划无法给干净克隆提供完整开发规范。

## 验收与证据

pnpm docs:check 通过；pnpm contracts:check 的 16 项测试通过；pnpm docs:test 验证有效仓库通过，并拒绝断链、缺少栏目、无归属源码及非法决策状态。skill 校验另在交付中报告。未执行 GitHub 远端 CI，不声称语义审查可由机械脚本代替。

## 后果

后续源码增加必须有子系统归属，重要取舍有决策记录。客户端未自动发现 .codex/skills 时通过 AGENTS.md 显式加载。既有 API 阶段验收与历史进度差异仍需后续开发核对。
