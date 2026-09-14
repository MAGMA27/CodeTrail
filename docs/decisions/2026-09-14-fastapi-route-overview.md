# FastAPI 路由入口概览

Status: implemented
- Date: 2026-09-14
- Scope: Python O1 概览、FastAPI 路由静态识别、HTTP/MCP/工作台展示

## 问题

现有 Python 概览只识别 `__main__.py` 和顶层 `__name__ == "__main__"`。CodeTrail 自身的 FastAPI 路由实际位于 `FastAPI()` 实例的路由装饰器中，用户无法从项目概览看到 HTTP 方法、声明路径和处理函数。路由识别必须保持源码快照只读，不能执行服务或把入口绑定伪造成静态 `calls` 边。

## 决定

1. 将入口规则版本升级为 `python-entry-v2`，在现有 `ProjectOverview.entries` 中增加 `fastapi_route` 候选。路由入口保存 HTTP 方法、字面量 `declared_path`、处理函数 symbol 和从装饰器开始到函数结束的同快照 evidence；`symbol_id` 指向处理函数，入口绑定不新增 `CodeEdge`。
2. 只确认静态可证明的 FastAPI 实例：支持 `from fastapi import FastAPI as Alias`、`import fastapi as Alias` 及其直接构造调用；绑定按模块/函数词法作用域顺序处理，参数、局部赋值、删除或重新绑定会遮蔽/失效外层实例。只扫描模块和函数体的直接语句，以及可定位到现有 function symbol 的函数装饰器；不展开控制流块或类体。
3. 支持 `app.get/post/put/patch/delete/options/head/trace` 和 `app.api_route` 的常量路径；`api_route` 只有在 `methods` 为静态字符串序列时按方法拆成稳定的独立候选。路径变量、f-string、动态方法、未知接收者和运行时注册不生成确定路由。
4. `declared_path` 只表示装饰器声明值，不解析 `APIRouter`、`include_router`、`mount` 或部署前缀，不将其展示为最终 URL；未识别路由继续用边界说明表达，不解释为项目没有路由。
5. 稳定入口 ID 使用规则版本、文件、方法、声明路径和处理函数 symbol 组合，重复装饰器按同一注册身份去重。识别继续受概览时间、路径、模块和关系索引既有预算约束；HTTP/MCP 复用同一 `WorkspaceService.get_overview`。
6. 规则版本变化只影响概览和新生成结果的入口映射，不改写旧 index、Session、Run 或 evidence。旧保存功能结果继续可读取，规则不一致时沿现有 feature graph 逻辑明确标记不可映射。

## 替代方案

- 直接读取 FastAPI 应用的运行时 `routes`：需要导入/执行被分析项目，违反源码只读和不执行目标仓库边界。
- 把路由绑定写成 `calls` 边：会混淆框架注册事实和函数运行调用关系，破坏静态图语义。
- 解析所有 `APIRouter` 和挂载前缀：当前项目验收不需要，且在未解析动态前缀时容易把声明路径冒充最终 URL。

## 验收与证据

已补充：离线测试验证 CodeTrail 实际使用的 FastAPI 构造与路由形态，并覆盖导入别名、async、作用域遮蔽/重新绑定、同名普通对象、动态路径和 `api_route` 静态方法；当前工作区源码快照通过 HTTP/MCP 返回 `/api/health` 与项目概览路由的同快照证据、处理函数 symbol、行号和 hash 对照；浏览器从路由入口进入源码和处理函数关系图。实现使用 `python-entry-v2`，路由候选数量上限为 200，超限会标记概览截断；直接语句之外的控制流/类体范围保持未识别，不把未识别解释为没有路由。

## 后果

概览会同时包含 Python 文件入口、main guard 和 FastAPI 路由入口；路由证据可跳到处理函数的源码/关系图，但不会表示运行时注册顺序、挂载后的完整 URL、请求覆盖或框架行为。已保存的旧规则结果不会被静默重解释，真实运行服务和真实模型仍不在范围内。
