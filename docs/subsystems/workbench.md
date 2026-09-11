# 工作台

## 状态

已实现首页及健康连接检查；图交互、Issue 分析和报告界面尚未实现。

## 职责与范围

展示连接状态；未来通过 API 获取图与证据，不直接访问 SQLite。

## 接口与依赖

[page.tsx](../../apps/web/app/page.tsx) 调用健康接口并验证响应；Next.js 局部规则见 [AGENTS.md](../../apps/web/AGENTS.md)。

## 数据与生命周期

当前仅有 checking、connected、error 连接状态；尚无 Run 状态消费。

## 错误与限制

网络与非法响应显示失败；当前首页产品文案不能用作后端实现清单。

## 验证

pnpm web:typecheck 和 pnpm web:build；浏览器核对连接、失败和恢复。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
