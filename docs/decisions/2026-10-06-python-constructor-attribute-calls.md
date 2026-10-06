# 决策：保守解析 Python 构造器实例属性调用

Status: implemented
- Date: 2026-10-06
- Scope: Python relation facts、静态 calls 目标解析、事实缓存与不可变索引身份

## 问题

直接实例方法调用已经可按安全构造赋值解析，但服务常通过 `self.field = parameter` 保存依赖，再调用 `self.field.method()`。仅靠注解无法证明注入实例；忽略其他构造点、外部写入或类逃逸也可能把静态候选误写成确定边。

## 决定

只对唯一、直接、无条件的 `self.field = parameter` 考虑属性绑定。实例类必须由每一个当前快照内可解析的所属类构造调用点，以简单位置或关键字实参证明为同一个安全类；不接受参数重赋值、参数展开、重复参数、动态或别名构造、factory 构造、类引用别名/逃逸、继承、装饰器、`__new__` 或自定义属性协议。把裸类名作为普通调用实参、直接返回类名，以及 `Class.__new__(Class)` 都使该类实例来源不再可证明；不会为自用源码或测试目录增加来源例外。任何未能纳入构造点扫描的预算截断都会禁用新绑定并标记关系扫描截断。类型注解不单独参与实例证明。

同名直接字段写入通过唯一性检查。简单外部 `obj.field` 写入保守拒绝；多段外部字段写入只有在 RHS 实例类被现有构造器对象绑定证明与候选依赖同类时才不改变绑定。字段删除、增量、注解或控制流写入、可识别 `setattr`/`delattr`、动态属性名及所属类或依赖类自定义 `__getattribute__`、`__getattr__`、`__setattr__`、`__delattr__` 都拒绝普通字段解析。实现仅扩展当前有界 Python relation facts 和构造解析，不建立通用数据流框架。

这些静态边表示捕获快照中所有已解析构造点和字段事实的一致性，不承诺运行时实例唯一、动态执行一定经过该构造点或调用必然发生。调用可达仍不等于确认影响。

新增有序参数、目标字段、直接返回类引用和属性写事实后，关系 facts 缓存规则/格式升级至 `python-relation-facts-v4` / `relation-facts-v4`；关系语义升级至 `relations-v6`，索引构建身份升级至 `relations-v10`。不迁移或重写旧 immutable index、Session、Run、evidence；旧 facts 缓存因版本不符安全 miss。

## 替代方案

- 从类型注解推断依赖类型：注解可被动态赋值或覆盖，不能证明对象来源。
- 忽略不能解析的构造调用，只按剩余构造点绑定：会把不完整的调用集合误当作全体。
- 按 CodeTrail 的类名或调用链手工补边：绑定项目源码且绕过共享 HTTP/MCP 快照解析。
- 建立跨程序点通用数据流或运行目标仓库：超出本次最小静态关系能力与只读索引边界。

## 验收与证据

最终定向关系/cache/freshness 与 HTTP/冷 stdio 回归通过（48 passed；其中后者独立启动 stdio 子进程并与 HTTP neighbors 逐对象比较）。父任务最终全量后端 235 passed，`compileall` 通过；`pnpm docs:check` 与 `pnpm docs:check:local` 在本轮文档更新后通过。当前宿主 Codex MCP external Run 成功并完成实时 PC UI 观察；HTTP 和宿主读取的报告一致。整仓 neighbors 返回 0 条静态边、2 条 unresolved，source 位置仍是 `WorkspaceService.read_source` 中对 `self.index_service.read_source` 的调用；搜索与 unresolved 详情受预算截断，因此当前结果为部分解释，没有声称完整执行链或确认影响。宿主验收包含新独立 `relations-v10` 索引，旧 `relations-v5` index 仍可读。详细 Run、session、source 与 UI 记录保存在本地忽略文档，不作为共享决策的理解前提。

## 后果

动态构造、反射、未识别的类逃逸、继承、重绑定和无法证明的外部字段写入仍保留 unresolved。全站同一类构造调用点不表示真实运行时唯一；静态边不能作为实际类型、执行顺序或已确认影响的保证。新构建身份使 freshness 要求为当前规则生成新快照，旧快照保持可读且不被原地改写。
