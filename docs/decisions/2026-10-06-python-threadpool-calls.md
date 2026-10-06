# 决策：保守解析 Python threadpool 包装调用

Status: implemented
- Date: 2026-10-06
- Scope: Python 关系 facts、静态 calls 目标解析、事实缓存与不可变索引身份

## 问题

已识别的 `run_in_threadpool(function, ...)` 调用中，第一个 callable 以名称或属性引用传递，不是独立的 `ast.Call`，所以现有关系提取只留下包装器 unresolved，无法连接到可证明的本地函数或实例方法。解析任意包装器、运行被分析仓库或按相似名称推测都会超出静态证据。

## 决定

只对从 `fastapi.concurrency` 或 `starlette.concurrency` 精确导入的 `run_in_threadpool` 识别第一个简单 positional callable。导入必须在该调用所属作用域已经处理到，捕获作用域的导入还须先于嵌套函数定义；仓库 snapshot 若含同路径模块、绑定被遮蔽/赋值、或捕获作用域里存在后续及 `global`/`nonlocal` 重绑定，则不把该调用当作白名单包装器。

转发目标仅在当前 index 唯一解析到函数/test function，或其接收者绑定到唯一、未失效的本地类实例且目标是安全的直接唯一方法时生成 `calls/ast`。边位置取完整包装调用；此关系表示 AST 可证明的参数转发，不保证运行时一定执行。类目标、动态/别名目标、多义方法及不安全方法继续保留原包装调用 unresolved。共享的关系 facts 缓存和索引规则身份随语义版本升级；历史 immutable snapshot 不回写或迁移。

## 替代方案

- 仅在 `main.py` 等已知位置补静态边：绑定 CodeTrail 文件结构，且绕开共用的关系解析与缓存。
- 对所有首参调用做转发：会把普通函数、动态包装器或同名本地函数误写成静态 calls。
- 执行目标仓库验证 wrapper 行为：违反只读索引和不执行目标代码的边界。

## 验收与证据

离线回归 `uv run --directory apps/api pytest -q` 通过（212 passed），覆盖正反例、facts 序列化、旧 cache miss 与 freshness/index identity 升级；`compileall`、`pnpm docs:check`、`pnpm docs:check:local`、`git diff --check` 均通过。CodeTrail 自用项目经 HTTP refresh 创建独立 `relations-v8` snapshot；指定两个 HTTP association 的 HTTP neighbors、冷启动 stdio MCP neighbors 与 read_source 逐对象一致，PC 桌面工作台可见各自 handler 子图。新 index/hash、旧 v7 snapshot 保留、图内 unresolved 与服务层后续缺口见本地阶段记录 `memory-bank/progress/2026-10-06-python-threadpool-forwarding.md`。当前隔离数据中无 Session/Run，因此没有旧 Run 实例可对照。

## 后果

只支持两个明确来源和简单直接 callable；包别名、动态分发、赋值别名、函数工厂和无法静态证明的导入执行顺序仍显示为 unresolved。新增 facts 字段提升缓存版本，语义版本与 index namespace 的提升会要求显式生成新索引；旧 Session、Run、源码 evidence 保持原快照可读。
