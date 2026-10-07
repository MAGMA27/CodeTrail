# 决策：库与插件架构概览

Status: proposed

实现状态：本轮约定的 Python 静态规则、契约、HTTP/MCP 引用与 Web 聚合已实现；TypeScript 导出事实未持久化，按未知处理。验收状态：定向自动检查通过；pluggy 当前宿主 MCP 已实际返回新 schema，真实 Issue Run 的 HTTP/MCP 报告与 evidence 一致，PC 刷新恢复与 bounded impact 有证据。CodeTrail API 请求 `max_modules=40` 返回 40/96，实际分类 excerpt 4,356 字符/96 行；Web 画布 40 模块投影上限及 40,000 字符预算触发边界仍未验证。因此保留 proposed。执行证据见本地阶段记录 `memory-bank/progress/2026-10-07-mvp-final-closure.md` 与 `memory-bank/mvp-final-closure-acceptance.md`；这些本地记录不属于干净克隆运行依赖。

## 问题

现有固定快照概览主要识别应用入口，并显示从入口出发的静态可达路径。库或插件没有应用入口并不代表没有公共 API 或扩展点；静态规则也不能证明运行时调用或未使用。

## 决定

扩展 `ProjectOverview`，在现有职责/目录模块聚合旁提供独立的应用入口、公共 API、插件扩展点和未知区域分类。分类记录绑定 `index_id`、模块与规则证据；入口引用已有 entry evidence，其他来源使用同一快照的源码 evidence。分类不得生成或更改 `CodeEdge`、`OverviewPath` 或既有 calls 可达统计。

本轮只确认明确的源码声明：沿用现有 Python/FastAPI 入口规则；Python 公共 API 只从顶层静态字符串 `__all__` 解析，并支持唯一、可定位的本地定义及同快照直接 import alias；插件扩展点只接受可追溯到 `pluggy.HookspecMarker` / `pluggy._hooks.HookspecMarker` 导入绑定及其字面实例/别名的顶层函数装饰器。单凭名字、注释、字符串、装饰器拼写或命名约定不确认为分类。TypeScript 导出事实目前没有随持久化索引重载；本轮将其列为未知/未分析，不额外解析、持久化或推断。

分类扫描、响应条目、evidence 行数和 excerpt 字符数使用固定预算（分别为 2,000、200、400 行和 40,000 字符）并共享概览 deadline；coverage 分开表达总量、可见量、各分类计数和不完整状态。未知表示当前快照未命中本轮规则，不代表无用、未引用或运行时不可达。HTTP 与 MCP 继续调用同一服务和契约。

默认画布仍以职责/目录及模块为节点，显示模块分类摘要；选中或展开现有组后查看有界分类列表，并通过同快照 evidence 打开源码。保留现有模块画布上限和符号分页，不增加符号节点或关系。

## 替代方案

- 用名字、装饰器拼写或可达性推断 API/扩展点：同名自定义符号和动态绑定会造成误识别。
- 执行仓库代码或依赖读取运行时注册：会越过来源只读边界且结果依赖运行环境。
- 为本轮补建 TypeScript 导出持久层或再次启动 parser：扩大索引生命周期和契约；现有持久化事实不足以证实此能力。
- 把分类当作静态 calls 或把所有未可达模块标成无用：会改变现有关系语义并夸大结论。

## 验收与证据

固定 pluggy 索引实际显示 21 个 public API、2 个 hookspec 扩展点；PC 选中 `PluginManager` 公共 API 后打开了 `src/pluggy/__init__.py:L12` 导出声明证据，并打开 `docs/examples/eggsample/eggsample/hookspecs.py:L7` 的 `@hookspec` 证据。两项面板均显示同一固定 `index_id`、路径、行、片段 hash 与源码；目录默认摘要显示 API/扩展点/未知计数，eggsample 组展开和折叠均可操作。CodeTrail 自用索引的最终 API overview 以 `max_modules=40` 返回 40/96 模块；Web 画布自身 40 模块投影边界仍未触发。分类 excerpt 总计 4,356 字符/96 行，低于 40,000 字符预算，字符边界触发仍为 UNVERIFIED。当前宿主 MCP 已直接返回新分类 schema，不需要强制重载；真实 Run/HTTP-MCP evidence 对照与 PC 刷新恢复见本地闭环记录。CodeTrail 完整分类 coverage 仍显示 `truncated=true`，不能解释成全量无截断。来源仓库代码和测试未执行。

## 后果

分类是规则证据，不是运行时注册或完整语言语义。动态 `__all__`、复杂重导出、其他插件框架、未持久化的 TypeScript export、入口规则之外的启动方式和超预算项保留为未知或截断；UI 必须说明这些边界。
