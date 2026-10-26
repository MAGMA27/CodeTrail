# 决策：Issue 影响分层

Status: proposed

## 问题

[工作区决策](2026-09-11-project-workspace.md) 把「Issue 影响分层和完整范围」留在验收之外，但仓库文档没有定义层的名称、成员规则或和其他事实的边界。[Run 影响查询](2026-09-13-run-impact.md) 已经能从一份已保存报告读取有界静态 `calls`，工作台图例也区分候选根、静态可达和 unresolved。这些结果仍是一份平坦的节点、路径和未知引用，调用方必须自己猜测最短路径深度，请求失败还会和「当前方向没有静态 calls」画在一起。

本记录只补上这次读取的分层语义。它不把静态可达升级为已确认影响，也不把深度 2 的有界结果称作完整影响范围。层的词汇是保守提案，需要所有者确认后才能把本记录标为 implemented。

## 决定

影响分层是同一次只读 `RunImpactResponse` 的派生分类，不写回 Run，不调用模型，不执行来源仓库代码、shell 或测试。HTTP 与 MCP 继续走同一个 `WorkspaceService.get_run_impact`。存储层的 `get_static_impact` 仍只遍历同快照静态 `calls`；`imports`、contextual calls 和模型叙述不进入任何层。

每次响应固定按下列顺序返回四层。同一返回符号只属于一层，按最短简单路径归类：

| 层 | 如何判定 | 不表示什么 |
| --- | --- | --- |
| `report_anchor` 报告锚点 | `mapping_status=mapped` 时，取已保存报告里唯一的 candidate `source_symbol_id` 或仅含一个 symbol 的 finding，且该符号必须出现在报告图节点中。判定标记为 `saved_report_symbol`。 | 不是已确认缺陷位置，只是调查起点。 |
| `static_direct` 直接静态可达 | 返回节点中，最短简单 `calls` 路径深度为 1 的符号。判定标记为 `static_calls`。 | 调用可达不等于已确认影响。 |
| `static_indirect` 间接静态可达 | 返回节点中，最短简单 `calls` 路径深度为 2 的符号。深度请求为 1 时此层为空。判定标记为 `static_calls`。 | 不是完整仓库范围，也不是已确认影响。 |
| `unknown` 未知边界 | 本次返回节点上的 dynamic/unresolved 引用，ID 顺序与 `unresolved` 列表一致。判定标记为 `unresolved_reference`。 | 保持未知，不绘制为确定影响边，也不因为没有返回就当成不存在。 |

`mapping_status=unmapped` 时四层的符号和 unresolved ID 都为空，并保留现有说明：没有唯一符号时不按同名猜测。查询截断只分类已经返回的节点和引用；截断理由仍放在既有 `truncated` / `truncation_reason`，空层或空的静态可达不能解释成没有影响。索引扫描未完成和 unresolved 详情截断继续使用既有覆盖字段，不并入这四层的成员规则。

工作台用这四层的文案和图例展示锚点、直接可达、间接可达和未知边界。无唯一映射显示 unmapped 空态；影响请求失败显示错误并提供重试，且不把失败画成「没有静态 calls」；截断显示独立说明。方向、深度、预算、固定 `index_id` 和旧 Run 不回退到后来的 latest index 保持 [Run 影响查询](2026-09-13-run-impact.md) 的限制。

## 替代方案

把层留给前端按路径深度现算：契约无法拒绝把深度 2 的符号同时标成直接和间接，MCP 调用方也看不到同一套分类。把 unresolved 画成第三跳静态边会把未知关系显示成已确认影响，因此不采用。把深度上限放宽到整仓可达会改变已实施的有界影响查询，也不能补上运行时未执行的路径，因此完整影响范围仍留在本记录之外。

## 验收与证据

实现与本提案一致，但本记录保持 proposed：层词汇尚未由所有者确认，下面的自动测试也不能代替真实网页流程。

- 实现：`ImpactLayer` 进入 `RunImpactResponse`；`impact_layers.py` 从已保存报告映射和 `get_static_impact` 的返回路径派生四层；影响面板分开显示 unmapped、请求失败重试和截断。
- 2026-10-26 在本分支上跑过的自动检查：`pnpm --dir apps/web test` 94 通过；`pnpm web:typecheck`、`pnpm web:build`、`pnpm docs:check` 通过；`pnpm contracts:check` 30 通过；`uv run --directory apps/api python -m compileall -q app` 通过。仓库没有单独的 lint 脚本。`uv run --directory apps/api pytest -q` 为 280 通过、4 失败。这 4 个失败在 `origin/main` `3dd21bb` 上用同一锁定 TypeScript 5.9.3 解析器复现：大小写敏感文件系统上把导入路径改成大写后返回 400；Linux 的 `ctypes` 没有 `get_last_error`；仓库自索引关系边达到 `max_edges`；本地导入旅程的结果步骤带有源码 evidence。它们不记为本分层实现的回归，也没有为了变绿而放宽路径、Windows 进程探测或关系预算。
- 真实网页上的 unmapped、请求失败重试和截断尚未按用户流程点过，记为尚未验证。完整仓库影响范围、GitHub 公网导入和真实供应商语义质量不在本决策的验收范围内。

## 后果

[工作区决策](2026-09-11-project-workspace.md) 在层词汇确认、真实网页边界验收和其余既有缺口关闭前继续保持 proposed。影响读取的调用方必须消费 `layers`，不能再从平坦节点推断层。截断后的层是已返回子集。Issue 分析提示仍不负责分层，避免把模型假设写进静态事实。
