# 决策：P2-R2 有界模型驱动 Issue 分析

Status: implemented

## 问题

P2-R1 只验证了固定 fixture 工具计划，不能让模型根据实际 Issue 和保存消息选择检索路径。P2-R2 需要接入真实供应商，同时继续保持单项目、单快照、只读仓库、可取消、有预算和可复核证据的边界。

## 决定

real Run 与 fixture Run 共用 `WorkspaceService`、`RunExecutor`、SQLite 生命周期和 `search_code`、`get_neighbors`、`read_source` 工具。每一轮模型只能返回一个经 Pydantic 校验的工具请求或一个 `IssueModelFinal` 结论；模型不能请求 shell、网络、测试或改变 project/index，工具参数和服务端调用都再次受边界校验。完整冻结 Issue、Session 消息前缀、工具结果和同一 Run 已返回的证据组成每轮请求，后续消息和 latest index 不会进入在途 Run。

模型适配器复用现有 `config.json` 的 OpenAI-compatible 传输，保存 provider、model、提示词版本和执行版本，不保存密钥、原始 prompt、原始 response 或隐藏思维链。服务端在发送前原子预留模型/修复次数和完整输入字符，返回后记录输出字符及可用 token；缺失 token 保持未知。每次 real Run 有模型调用、有限修复、模型输入/输出、工具、源码、事件和总时间上限；非法输出最多修复一次，证据不足是正常结构化结论，非法引用、供应商失败和预算耗尽明确失败。

HTTP real 能力由 `CODETRAIL_REAL_RUN_ENABLED=1` 单独开放，MCP real 另由 `CODETRAIL_MCP_REAL_RUN_ENABLED=1` 开放，且都需要对应 Run 写权限。工作台必须显式选择 real，并在启动前说明冻结问题、消息和有界源码证据会发送到服务端配置模型、可能产生费用；失败不回退 fixture。状态、取消、报告和证据继续使用现有轮询与 Session 视图。

## 替代方案

为 real Run 另建检索层会复制项目归属、预算和证据规则；引入多 Agent、队列或 LangGraph 会扩大尚未需要的生命周期。把模型原始文本或隐藏推理写入事件会增加敏感数据和不可复核的输出面。因此先保留单 Agent、单请求一步一工具的纵向闭环，后续若需要 SSE 或影响高亮再在同一 Run 事实源上扩展。

## 验收与证据

`apps/api/tests/test_issue_runs.py` 验证两个问题产生不同工具计划、完整冻结上下文、候选/证据不足、非法工具/跨快照或未返回证据、有限修复、模型调用预算、供应商失败、取消与容量保持、持久化 `config.json` 和 OpenAI-compatible 请求/鉴权/JSON 输出/usage；`apps/api/tests/test_mcp.py` 验证 HTTP/MCP real 能力开关独立。当前后端完整测试为 105 passed，真实模型路径使用本地假供应商协议验证；真实 DeepSeek 供应商语义质量、费用、网络可用性和独立评测仍为 UNVERIFIED。

工作台以本地假供应商复核 real 模式选择、显式启动、模型驱动工具执行、取消、终态报告和 evidence 钻取；自动测试不依赖网络或真实密钥。契约 Schema 由生成器重新导出，前端和文档检查另以本轮命令结果为准。

## 后果

real Run 的能力默认关闭并可能产生费用；模型 provider 只能从服务端持久配置读取，配置变更需重启服务。Run 报告只呈现结构化结论、公开工具事件、同快照 evidence 和用量，不呈现隐藏思维链。真实供应商输出质量和更大仓库性能仍需独立评测，不能用本地假响应或 fixture 代替。
