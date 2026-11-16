# 决策：Python API 与 CLI 原生入口旅程

Status: proposed

## 问题

现有功能旅程从 TSX 用户动作开始，不能直接选择已被项目概览识别的 Python API 或 CLI 入口。API handler 和 console script 已有同快照入口事实及静态调用边，但应保留旧 UI summary 的含义与大小。

## 决定

在现有 journey catalog 增加独立有界 native summaries/coverage，并复用现有 detail HTTP 路由、MCP tool 与 WorkspaceService 选择详情。native 入口仅包括直接 FastAPI route handler，以及 `project.scripts`/Poetry scripts 指向的唯一顶层函数。GUI scripts、插件组、动态注册、歧义/缺失目标、条件/间接 re-export 与其他入口类型保留未确认或未覆盖。

Native detail 最多 8 个步骤，处理 hop 只由同索引 `calls` 边构成，并最多沿深度 3 展示 7 条边；BFS 并列样本不是执行顺序。输入参数、return、raise、`print`/`sys.stdout.write` 形状候选和有值属性/下标赋值作为最多 8 条 source observations，跨函数按类别选择并标明省略。嵌套函数与类/方法体显式标为未覆盖；注解但无值不是赋值。所有结论绑定当前 project/index，引用固定快照来源。语法只表示源码形状，不表示运行、标准输出绑定、分支执行、数据流或持久化。HTTP full 与 MCP summary 沿用既有详情默认语义；UI action summary 字段和独立 40 项上限不变。

## 替代方案

- 将 `JourneySummary` 的 UI 字段改为可选，会改变旧 summary 的含义和工作台现有数据形状，因此不采用。
- 为 native journey 新建 HTTP/MCP 工具族会重复现有归属、证据和预算适配，因此在现有 catalog/detail 上增加可区分的 additive variants。
- 推断 arbitrary callables、GUI/plugin 入口、动态注册或条件 re-export 会超出已证明的入口事实，因此保留边界。

## 验收与证据

实现已提交协调者复核；端到端验收仍待执行。要求 HTTP 与独立 stdio MCP 对同一固定 index 返回一致 detail；覆盖直接 API/CLI、歧义与条件目标、来源范围/hash、截断和旧 UI summary；真实 CLI 正例使用公共仓库固定 commit 的静态快照，不导入、构建或运行外部代码。

准备的正例为 `adamchainz/django-upgrade` commit `5ccd385680292c5e5b10bedf0a3e509b5e6e9da8`。该版本 `pyproject.toml` 将 `django-upgrade` 声明为 `django_upgrade.main:main`；入口 `main` 为输入文件解析选项、逐文件调用 `fix_file` 并返回状态码；`fix_file` 经 `apply_fixers` 处理源码并具有 stdout/stderr 写入或文件改写分支。Codex 只读取了该提交的 pyproject 与 Python 源文件文本，没有导入、构建或运行它。隔离 HTTP 已完成该源码导入和索引，首次 detail 请求发现 `with` 遍历回归；最终 HTTP/MCP/浏览器结果另行记录在交付与 PR 报告中。

## 后果

用户可从工作台选择直接 Python API/CLI 入口并继续源码与 calls 导航。未支持的声明不会被补造成功能；当前语法观察不解释跨函数值传播、运行时分支、持久状态、响应 UI 或业务职责。
