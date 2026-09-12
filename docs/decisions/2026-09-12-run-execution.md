# 决策：P2-R1 单进程 Run 与 fixture 执行

Status: implemented

## 问题

P2-S1 已保存项目内 Session 和消息，但没有一次可观察、可取消且能复原读取的 Issue 执行。Run 必须使用 Session 的固定快照，不能被后续消息或项目 latest index 改写；HTTP 工作台和本地 MCP 还需要共享同一领域生命周期。

## 决定

Run 以 `project_id`、`session_id` 和 `index_id` 为不可变归属，创建事务同时校验 Session、快照和可选 `feature_generation_id`，冻结截至服务端 sequence 的完整已保存消息，保存输入 hash、消息范围和字符用量。草稿不属于 Run 输入。创建请求保留显式 `index_id`、Issue、fixture 场景、模式、版本、预算和幂等键；本轮只接受 `mode=fixture`，`real` 明确拒绝。

Run 和冻结输入、事件、工具结果、报告写入项目对应的 SQLite 管理库。Session 每次最多一个 `queued/running` Run；幂等键重试返回首次绑定 Run，改变请求语义返回 conflict。事件使用连续 sequence，终态只有一次且不可逆；取消和服务重启通过事务写入 `cancelled`/`interrupted` 及终态事件，迟到工具结果不能落库。

执行器使用本地 daemon 线程，先以 `CODETRAIL_RUN_MAX_CONCURRENT_RUNS`（默认 2）做非阻塞容量预留，再启动 worker；不设置持久或内部等待队列，不引入外部队列或多 Agent。容量满时创建在落库前返回可重试的 `budget_exceeded`/429；预留从存储成功前延续到 worker 终态，存储失败、启动失败、取消、超时、异常和完成均恰好释放。fixture 计划只调用应用服务的 `search_code`、`get_neighbors`、`read_source`，工具失败场景在适配器层注入确定性错误；报告从本次工具实际返回的 Symbol/Evidence 组装，静态边和候选关系分开。HTTP 提供启动、列表、详情、事件、报告和取消；MCP 的读操作默认注册，启动/取消仅由独立 `CODETRAIL_MCP_RUN_WRITE_ENABLED=1` 开关开放，并复用同一容量和错误语义。

工作台在现有 Session 视图内显式启动离线分析，先提示未保存草稿，轮询有界事件并恢复 Run/报告；不展示隐藏思维链、不自动执行、不把取消当成断线、不接 SSE 或真实供应商。

## 替代方案

把执行直接塞进追加消息会让用户无法区分保存和付费/耗时操作，也无法冻结上下文；复用 ImportAttempt 会混淆来源导入与 Issue 生命周期；读取固定响应包会绕过真实应用工具和证据归属。外部任务队列、LangGraph 和多 Agent 暂不引入，先验证单进程纵向闭环。

## 验收与证据

验证两个入口对同一 Run 的启动/查询结果、并发幂等与权限；验证超过一页的完整消息冻结、后续追加/latest 重建不改变输入和证据、跨项目/Session/index/generation 拒绝；验证三个 fixture 场景、工具/时间/输出预算、跨 Session 容量拒绝、满载幂等重试、queued 启动取消、运行取消、启动/存储失败和重启 interrupted，以及健康接口并发。浏览器验证保存需求、启动、事件、报告证据、切换和刷新恢复；使用测试专用内部工具延迟实际捕获运行中取消、cancelled 状态和终态报告。

实际验收记录：`uv run --directory apps/api pytest -q` 为 96 passed；`uv run --directory apps/api python -m compileall -q app` 通过；`pnpm contracts:check` 为 19 passed；`pnpm --dir apps/web test` 为 11 passed；`pnpm web:typecheck`、`pnpm web:build`、`pnpm docs:check`、`pnpm docs:check:local` 通过。浏览器在 `http://localhost:3000/` 使用 `timeout-demo` 固定快照完成新建 Session、保存消息、启动候选 fixture、观察 13 条公开事件、打开 `timeout_demo/config.py:L12–L25` 同快照证据、切换 Session 和刷新恢复；另以测试专用内部延迟启动证据不足 fixture，捕获“执行中→取消 Run→已取消”、4 条公开事件和取消报告，刷新后恢复同一终态。工具失败 fixture 观察到 9 条事件、失败工具结果及先前证据保留。MCP 读写边界与 HTTP 共用状态由 `apps/api/tests/test_mcp.py` 覆盖。

## 后果

本轮 Run 报告只支持 fixture 的候选、证据不足和失败结果；取消/重启会保存对应终态报告、状态和公开事件，报告不伪造候选证据。单进程容量拒绝不创建 queued Run，worker 启动失败则留下可查询 failed Run；事件读取先用轮询，SSE、真实模型、完整影响高亮和独立评测留给后续阶段。Run 的契约和存储字段成为未来 Agent 接入的共享边界。
