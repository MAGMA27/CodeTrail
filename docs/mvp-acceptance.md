# CodeTrail MVP 验收清单

本文件把分散在操作说明、界面规范、开发约束、决策验收段和 `evals/results/` 中的 MVP 验收准则收成一份公开清单。操作步骤仍以 [MVP 操作说明](mvp-operation.md) 为准；阶段计划仍在 Git 忽略的 `memory-bank/next.md`，不是干净克隆的运行依赖。本次工作区没有 `memory-bank/`，因此没有把未公开的 next 条目写进来。影响链记录点名的未执行界面状态已经列入。

状态沿用 [AGENTS.md](../AGENTS.md) 的约定：实现状态和验收状态分开，没有证据不能写成完成。这里的结论是各记录在其日期和样本内的原话，不是对本提交重新跑测试或浏览器。推断会在表格里标明。

| 状态 | 含义 |
| --- | --- |
| 已验证 | 来源在写明的日期、样本和范围内给出通过结论 |
| 部分验证 | 同一准则里通过与未通过或未执行并存，或较早的通过范围没有被较新记录覆盖 |
| 尚未验证 | 来源把它留为未执行、未验收或缺失能力，或者只有操作步骤、没有通过记录 |

## 导入与索引

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | 启动后检查 `GET /api/health`，工作台能发现名为「CodeTrail 自用验收」的项目及其 ready 索引 | [MVP 操作说明](mvp-operation.md) | 部分验证 | [共享运行决策](decisions/2026-09-14-shared-runtime-codex-demo.md) 记录当时以共享目录发现该项目，最新索引为 `relations-v2` / `python-entry-v4`，API 重启后浏览器看到 `API ONLINE`。2026-10-07 操作说明只记录自用大索引 `max_modules=40` 返回 40/96，没有重写 health 和项目发现的通过结论。推断：旧规则版本的通过不能覆盖当前索引身份。 |
| 2 | 本地项目创建、索引隔离、源码证据隔离、失败不覆盖 latest、应用重开后恢复 | [工作区决策](decisions/2026-09-11-project-workspace.md) 验收段 | 已验证 | 该段写明用两个服务端配置的临时仓库验证了 W1。决策整体仍是 proposed，因为 GitHub 导入和影响分层未完成；本项只覆盖这段 W1。 |
| 3 | 冷构建、跨进程热复用、单文件失效、无变化复用、禁用缓存等价、目标重命名/删除/恢复后重绑定，以及旧 Session/Run/evidence 仍绑定原 index；HTTP、stdio MCP 和工作台读取同一隔离数据 | [索引缓存联合验收](decisions/2026-09-16-project-index-cache-acceptance.md) | 已验证 | 决策状态为 implemented。样本是提交 `85149eb` 的 Python 快照。后果写明这不是完整增量索引。 |
| 4 | 完整且安全的 manifest 兼容时，显式更新以 `historical_reused` 复用历史不可变 index，保留原 revision，不原地改写 | 同上 | 已验证 | 同决策的「版本恢复修复复核」：隔离 HTTP（8017）、stdio MCP 和 3017 工作台。 |
| 5 | 源码变化后显式更新得到新的 ready index；旧 Session 与 Run 留在原 index；新建 Session 绑定刷新后的 index | [MVP 操作说明](mvp-operation.md)；[项目与会话决策](decisions/2026-09-11-project-session-workspace.md) | 部分验证 | [pluggy 旅程](../evals/results/real-repository-pluggy-issue-733-20261007/README.md) 的「来源变化与显式更新」为 PASS（仅索引范围）：新 index 再检查为 consistent，旧 Session 和 Run 仍绑定原 snapshot。缓存验收也记录旧 Session 不被 latest 替换。指定记录没有写出刷新之后新建的 Session 显示新 revision/hash。会话决策把“新 Session 固定最新可用 index”写成已实施，并说明不能据此推定全部产品验收完成。 |
| 6 | 公开 GitHub 仓库可以经产品导入并得到可用索引 | [工作区决策](decisions/2026-09-11-project-workspace.md)；[pluggy 旅程](../evals/results/real-repository-pluggy-issue-733-20261007/README.md) | 部分验证 | 工作区决策写明公网成功导入仍受未认证 API 限流影响。pluggy 的 `POST /api/imports/github` 返回 429，状态为 NEEDS_FIX（外部阻断，未归因为代码缺陷），随后改走单独标明的本地目录导入。 |
| 7 | 语言解析先验证 Python；多语言不是任意来源格式的隐含承诺 | [工作区决策](decisions/2026-09-11-project-workspace.md) 决定原文 | 部分验证 | 2026-09-11 原文保留，并已加日期注。此后 [TypeScript 决策](decisions/2026-10-06-typescript-http-overview.md) 为 implemented，同一有界快照捕获 `.py`、`.ts`、`.tsx`，并有一次 PC 浏览器上的 HTTP 形状证据。第 3 项的缓存样本只有 Python。推断：不能把该次 Python 缓存验收当成 TypeScript/TSX 已做同等验收。 |

## 架构浏览

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 8 | 在固定索引上查看职责或目录容器和模块，并钻到同一快照的源码或局部静态关系图。`imports`/`calls` 保持 source → target。HTTP 方法/路径形状不混入静态调用边。`unresolved` 表示未知，不能当成没有依赖 | [MVP 操作说明](mvp-operation.md) | 部分验证 | pluggy 架构项为 NEEDS_FIX：PC 画布显示 9 个源码目录容器、36 个模块和 33 组模块关系，并从 `_manager.py` 钻到固定源码。`src/pluggy` 展开后的 13 个模块都未从已识别的 `__main__` 入口可达；职责 manifest 未配置。 |
| 9 | 概览列出可静态确认的 FastAPI 路由：方法、声明路径和处理函数。点击入口打开装饰器到函数结束的同快照源码，关系图以处理函数为根。路径是装饰器声明值，不解析 `APIRouter`、`include_router`、`mount`、动态路径和运行时注册 | [MVP 操作说明](mvp-operation.md) | 部分验证 | [共享运行决策](decisions/2026-09-14-shared-runtime-codex-demo.md) 记录浏览器核对过 `/api/health` 入口 evidence，以及 2 个节点、1 条边的处理函数关系图，规则版本是 `python-entry-v4`。2026-10 的指定结果没有重做这条入口闭环。操作说明中的不识别范围是行为描述，不是当次点击证据。 |
| 10 | 规则入口覆盖无入口、静态路径、预算、项目隔离、HTTP/MCP 对照、证据引用和工作台联动 | [工作区决策](decisions/2026-09-11-project-workspace.md) 对 O1 的验收句 | 已验证 | 该句写明已用固定快照验证。没有另附结果文件。父决策仍为 proposed。 |
| 11 | 保存功能版本的稳定分页和精确读取、同一快照上的 semantic/static 功能图、项目/index/generation 隔离，以及 HTTP/MCP 和工作台版本与图流程 | 同决策对 O3 的验收句 | 已验证 | 该句写明已验证。没有另附结果文件。 |
| 12 | fixture/real 功能生成的适配、真实工具上下文、引用校验、预算/缓存/失败语义、HTTP 或可选 MCP，以及工作台证据钻取 | 同决策对 O2 的验收句 | 已验证 | 该句写明已验证。该段没有把真实供应商语义质量算作通过。 |
| 13 | 架构视图识别插件入口和 `pyproject` 脚本 | [pluggy 旅程](../evals/results/real-repository-pluggy-issue-733-20261007/README.md) 的架构项 | 尚未验证 | 该项为 NEEDS_FIX，并写明规则说明尚不识别插件入口和 `pyproject` 脚本。源码钻取本身见第 8 项。 |

## Session 与 Run

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 14 | 新建 Session 显示它所绑定快照的 revision/hash；不用旧 Session 证明新快照 | [MVP 操作说明](mvp-operation.md)；[UI 规范](design/workspace-ui-spec.md) | 部分验证 | UI 规范把标题定为简短固定版本，完整 revision/snapshot 放在可展开详情，对应清单项已勾选。[600 秒验收](../evals/results/codex-host-600s-20261007/README.md) 在三个页面确认了各自的快照指纹。没有指定记录写明自用验收项目在刷新后新建的 Session 显示新 revision/hash。 |
| 15 | 不向 Codex 预先提供候选文件、函数或行号。由 `start_external_run`，再经 `run_search_code`、`run_read_source`、`run_get_neighbors` 和 `submit_external_run` 完成一次调查。工作台发现该 Run，不负责创建它 | [MVP 操作说明](mvp-operation.md) | 已验证 | 600 秒验收：三例只给出 Issue 和该例的 project/index/session，显式 `max_seconds=600`，首轮 22/22。范围是这三例 synthetic，不代表大仓库或外部模型质量。操作说明里的自用项目费用示例没有出现在指定结果中，不记入本项。 |
| 16 | 公开事件、工具结果、报告和证据绑定同一个 project、session 和 index | [MVP 操作说明](mvp-operation.md)；[AGENTS.md](../AGENTS.md) | 已验证 | 2026-10-07 操作说明；pluggy 的刷新恢复；600 秒验收中三页互不串用项目和 Run；[影响链验收](../evals/results/p4-e2b-codex-host-20261001/impact-chain-acceptance.md) 的 HTTP/MCP 对照和浏览器恢复。均限于这些 Run。 |
| 17 | 切换项目或 Session、刷新页面后恢复原来的 Session 与 Run，不创建重复 Run | [MVP 操作说明](mvp-operation.md)；[UI 规范](design/workspace-ui-spec.md) 清单 | 已验证 | 影响链写明返回并重新加载后恢复原 Run，且没有创建 Run。600 秒验收和 pluggy 都记录了浏览器刷新后的恢复。UI 清单勾选了取消、Run 切换和刷新不启动重复 Run。 |
| 18 | 显式 600 秒预算下，三例陌生 synthetic Issue 走完 external Run 基本闭环 | [600 秒验收](../evals/results/codex-host-600s-20261007/README.md) | 已验证 | 首轮 22/22：两例定位到预期符号并引用同快照证据，一例按证据不足弃答。记录写明不代表真实仓库泛化、生产缺陷定位或外部模型质量。一次过宽读取和工具预算用尽见 [submission-failures.md](../evals/results/codex-host-600s-20261007/submission-failures.md)。 |
| 19 | 对 pluggy 已知 Issue #733 的修复前快照完成 external Run，并保持报告、HTTP、实时 UI、源码 evidence、bounded impact 和刷新恢复在同一 Session/index | [MVP 操作说明](mvp-operation.md)；[pluggy 旅程](../evals/results/real-repository-pluggy-issue-733-20261007/README.md) | 部分验证 | 操作说明把 2026-10-07 复测写成已知案例和修复前证据链，不是盲测。旅程整体为 partial。Issue 调查为 PASS（仅静态定位），事后解释性评分 10/10；原 Issue 已经点名 `PluginManager.register`。 |
| 20 | 已配置服务端模型时，工作台「理解代码」在隔离的 localhost 上走完实时 SSE、刷新和源码返回 | [real Run 代码理解决策](decisions/2026-10-22-real-code-understanding-runs.md) | 尚未验证 | 决策状态为 proposed。验收段要求完成该网页流程后再更新状态。同一段列出的确定性 fake adapter HTTP 检查不能代替网页验收。真实供应商语义质量不在确定性回归中宣称通过。 |

## 证据与影响

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 21 | 从报告或证据入口打开同一快照的源码和 hash；project/index/path/line/hash/evidence 不一致时显示错误，不回退到当前 latest | [MVP 操作说明](mvp-operation.md)；[UI 规范](design/workspace-ui-spec.md) | 已验证 | 600 秒验收点击报告证据后核对了路径、行范围和内容 SHA 前缀。影响链的 caller 源码 hash 前缀与固定 index 的完整 hash 一致。pluggy 刷新后重新打开的证据仍绑定旧 index 和原 content hash。UI 清单勾选了证据面板上下文。 |
| 22 | 候选报告可以打开该 Run 固定快照上的有界静态影响，并保留 unresolved。静态可达不等于已确认影响，测试引用不等于测试覆盖 | [MVP 操作说明](mvp-operation.md)；[AGENTS.md](../AGENTS.md)；[影响链验收](../evals/results/p4-e2b-codex-host-20261001/impact-chain-acceptance.md) | 部分验证 | 影响链把用户要求的候选、调用链、源码、切换、刷新和截图记为已验证，并写明空的 callers 不是“没有业务影响”。同一记录没有执行 unmapped、请求失败/重试和截断这三类 UI。会话决策仍写影响面板部分边界待验收。UI 清单却勾选了失败、取消和无唯一映射空态。对照结论：这三份文档不一致，不把勾选升成这些边界已全部验证。[工作台说明](../apps/web/workbench.md) 写明影响链的既有边界状态仍有效。 |
| 23 | 终态之后，工作台能够持久化并展示形状正确的结论、证据和影响或证据不足说明 | [指导验收](../evals/results/p4-e2b-codex-host-20261007/guided-acceptance.md) | 已验证 | 三例 synthetic 在打开 gold 之后补验。记录写明这不是盲跑、不计分，也不改变该轮首轮 0/3。 |
| 24 | Issue 影响分层和完整影响范围 | [工作区决策](decisions/2026-09-11-project-workspace.md) | 尚未验证 | 验收段写明仍待后续验收，因此该决策保持 proposed。 |

## 持久化与恢复

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 25 | 切到另一个项目或旧 Session 再切回来，并刷新页面后，恢复原 Session 与 Run | [MVP 操作说明](mvp-operation.md) | 已验证 | 与第 17 项是同一组记录（影响链、600 秒验收、pluggy），不是另一次独立复测。 |
| 26 | 只停止自己启动的 API 与 Web 并重新执行启动脚本后，新 Session 绑定刷新后的 ready index，旧 Session 绑定原 index；Run history、报告、事件、工具结果和 evidence 可恢复。数据持久化验收不需要清空 runtime，也不需要为数据变化重载 MCP | [MVP 操作说明](mvp-operation.md)「停止后复现」 | 部分验证 | 工作区决策的 W1 写了临时仓库上的应用重开恢复。缓存验收的工作台闭环使用独立数据目录，不是这条联合脚本。pluggy 和 600 秒验收记录的是浏览器刷新。推断：指定的 2026-10-07 复测没有执行这里的进程重启步骤。 |

## 边界

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 27 | external Run 和核心 MVP 只调用白名单工具，不执行来源仓库代码、shell 或测试。报告不展示隐藏思维链 | [MVP 操作说明](mvp-operation.md)；[AGENTS.md](../AGENTS.md) | 已验证 | 操作说明的边界段。pluggy 旅程和 600 秒验收都写了没有运行来源代码或测试，也没有调用外部供应商。UI 清单写明已实现的 Run SSE 不会伪装成隐藏推理。这些是上述运行和清单的记录，不是一次新的安全审计。 |
| 28 | 自动测试使用 fixture，不依赖网络、API Key 或真实模型；真实模型评测单独记录 | [AGENTS.md](../AGENTS.md) 测试指南 | 尚未验证 | 本清单没有重跑 pytest 或 `pnpm evals:*`。历史决策里的通过次数不作为当前证据。 |
| 29 | 真实供应商调用、DeepSeek 语义质量和实际账单不因本地 external 闭环而算作已验证 | [MVP 操作说明](mvp-operation.md) | 尚未验证 | 操作说明边界段的原话。会话决策同样把真实供应商语义质量放在已验证范围之外。 |

## 界面范围

[UI 规范](design/workspace-ui-spec.md) 写明产品面向 PC，当前交付条件是 PC 上的 MVP 阅读闭环。窄屏、移动端和平板专用适配不是交付条件；已有窄屏截图只保留为历史证据。截图只证明布局和视觉状态，不能替代导入、索引、Session 持久化、归属拒绝、证据 hash 和 Run 生命周期的流程证据。规范清单中的 13 项都已勾选。下表按该勾选记录状态；和影响链或会话决策冲突的项保持部分验证。本文件没有重做这些浏览器流程。

| # | 准则 | 来源 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 30 | PC 桌面是交付范围；窄屏、移动端和平板不是交付条件。PC MVP 阅读闭环才是交付条件，截图不替代流程证据 | [UI 规范](design/workspace-ui-spec.md) 设计读法与截图矩阵 | 已验证 | 规范记录用户于 2026-10-06 确认 PC 范围。[工作台说明](../apps/web/workbench.md) 也写明窄屏矩阵不属于验收范围。阅读闭环的各段是否通过，以上表各项为准，不因本条范围声明而全部通过。 |
| 31 | 生产 `/` 在 `1440x900`、`1024x768` 和 `390x844` 实际打开并生成截图，页面没有横向溢出 | UI 规范清单第 1 项 | 已验证 | 规范清单已勾选，截图目录见规范中的矩阵。窄屏截图不作为交付条件。 |
| 32 | 找得到项目列表、会话列表、新建会话、项目知识入口和唯一的「源代码与索引」；真实项目和 Session 可切换并在刷新后恢复 | 清单第 2 项 | 已验证 | 规范清单已勾选。切换和刷新另见第 17 项。 |
| 33 | 「源代码与索引」页内可切换「源代码来源 / 固定索引」，知识页和项目上下文头不再重复渲染导入或索引入口 | 清单第 3 项 | 已验证 | 规范清单已勾选。指定的评测结果没有复述这个控件。 |
| 34 | 可从知识页打开和关闭功能图、源码证据，并调整桌面面板宽度；中央滚动、Session 身份和草稿不丢失 | 清单第 4 项 | 已验证 | 规范清单已勾选。 |
| 35 | 空 Session 不显示无效分页；已有消息显示后续分页入口；空工作区有真实 `/` 截图，原型另有空态截图 | 清单第 5 项 | 已验证 | 规范清单已勾选。 |
| 36 | Enter、Shift+Enter、输入法组合期间不提交、草稿按 Session 隔离；API 离线时写失败保留草稿，恢复后可重试 | 清单第 6 项 | 已验证 | 规范清单已勾选。 |
| 37 | 保存响应延迟期间继续编辑的草稿不会被旧的成功回调清空；切换 Session 使旧写请求失效；未变更内容仍按原幂等键重试 | 清单第 7 项 | 已验证 | 规范清单已勾选。 |
| 38 | Session 标题优先显示主题和简短固定版本；Session ID、Project ID 和完整 snapshot hash 可在展开详情中访问 | 清单第 8 项 | 已验证 | 规范清单已勾选。操作说明仍写“标题区显示 revision/hash”；规范把完整身份放在详情层，并以该清单项为验收记录。 |
| 39 | 证据面板显示 `project_id` / `index_id` / `generation_id`；图筛选位于图工具栏；合成证据不发起无效关系请求 | 清单第 9 项 | 已验证 | 规范清单已勾选。源码 hash 的流程证据见第 21 项。 |
| 40 | 生产 Run 面板显示连接与终态；受限 SSE 断流后续接保持事件 sequence 连续且不重复；取消、Run 切换和刷新不启动重复 Run | 清单第 10 项 | 已验证 | 规范清单已勾选 sequence 续接。不重复 Run 另见第 17 项。 |
| 41 | 视觉使用中性背景、单一强调色、明确焦点和错误状态；无旧网格、装饰渐变、假终端和假精确产品宣传 | 清单第 11 项 | 已验证 | 规范清单已勾选。这是视觉证据。 |
| 42 | 原型截图与生产页面分开标记；影响面板区分报告候选、静态 calls 可达和 unresolved；Run SSE 不伪装成隐藏推理或模型质量 | 清单第 12 项 | 部分验证 | 规范清单已勾选上述区分。模型质量见第 29 项，仍为尚未验证。影响边界是否都走过见第 22 项。 |
| 43 | 报告 candidate 或 finding 可进入固定 Run/index 的影响结果；方向和深度可控；影响节点能打开同一 snapshot 的源码并与图联动；失败、取消和无唯一映射有空态；旧 Run 不回退到后来的 latest index | 清单第 13 项 | 部分验证 | 规范清单已勾选。影响链写明 unmapped、请求失败/重试和截断 UI 在该次记录中没有执行。会话决策仍写部分边界待验收。已走过的候选、调用链、源码、切换和刷新见第 22 项。 |

## 仍开放的缺口

下面只列上述记录已经写明、尚未关闭的项。

- 影响分层与部分影响界面边界仍未关闭。工作区决策因此保持 proposed。影响链未执行 unmapped、请求失败/重试和截断 UI。会话决策写明部分边界仍待验收。UI 清单的勾选与这两份记录冲突，见第 22 项和第 43 项。
- real Run 的网页「理解代码」验收尚未完成，见第 20 项。
- pluggy Issue #733 旅程整体为 partial，见第 19 项。静态定位不是盲测。
- GitHub 产品导入在该次旅程中被 HTTP 429 挡住，见第 6 项。记录把它标成外部阻断，没有归因为代码缺陷。
- 插件入口和 `pyproject` 脚本未被架构规则识别，见第 13 项。
- 自用大索引的容量验收保持 partial。2026-10-07 操作说明写明 API `max_modules=40` 返回 40/96，该请求不验证 Web 画布自己的 40 模块投影上限；实际分类 excerpt 低于 40,000 字符，因此字符预算触发边界仍待验证。
- 真实供应商语义质量和实际账单尚未验证，见第 29 项。
- 600 秒三例和指导验收只覆盖小型 synthetic。指导验收发生在打开 gold 之后，不估计 solver 质量，也不代表真实仓库泛化。
- 跨会话自动记忆不在会话决策的已验证范围内。
- 索引缓存验收不宣称完整增量索引，样本也不包含后来加入的 TypeScript/TSX。
- pluggy 的 freshness 只扫描 Python、TypeScript 和 TSX。该次修复还改了一个 `.rst` 文件，这次检查不覆盖该 Git diff 的全部文件。
