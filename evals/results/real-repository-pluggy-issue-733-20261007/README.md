# 真实仓库旅程：pytest-dev/pluggy Issue #733

本轮用户旅程已执行并记录；整体为 **partial**。真实 Issue 的静态代码定位正确，但产品 GitHub 导入受 HTTP 429 阻断，且架构视图没有识别 pluggy 插件入口。单仓库结果不代表泛化。

| 验收项 | 状态 | 实际结果 |
| --- | --- | --- |
| GitHub 产品导入 | NEEDS_FIX（外部阻断，未归因为代码缺陷） | API 已启用 GitHub 导入后，`POST /api/imports/github` 返回 429。没有重试下载；另用产品本地目录流程导入同一公开仓库，并单独标为 local fallback。UI 保留了 GitHub 限流错误横幅。 |
| 固定来源身份 | PASS | Issue #733 修复前第一父提交 `f91048bede722a546a0fc7e776231459b90af75b`，tree `bccf202865ecde768ae4be0400927af158b208ed`；独立 clone 当时 clean。产品 revision `fixture-v1` 是标签，不能证明 Git commit。身份见 [freeze.json](freeze.json)，36 个索引模块逐文件 SHA-256 见 [source-manifest.json](source-manifest.json)。manifest 在 Run 后、来源 checkout 变化前生成，哈希基于 Windows checkout bytes（autocrlf 下 CRLF），不是原始 Git blob bytes；父级将两个关键文件的官方 Git blob 转为 CRLF 后核实与 manifest/read_source hash 一致。 |
| 架构理解与源码钻取 | NEEDS_FIX | PC 画布按 9 个源码目录容器显示 36 个模块、33 组模块关系；`get_overview` 标记 4 个 `__main__` 入口、4 个模块可达、32 个不可达、80 个 unresolved 且被 `max_unresolved` 截断。`src/pluggy` 展开后 13 个模块都未从这些入口可达；职责 manifest 未配置，规则说明尚不识别插件入口和 `pyproject` 脚本。已从 `_manager.py` 结构详情钻到固定源码片段，但这不弥补主要入口缺口。 |
| Issue 调查 | PASS（仅静态定位） | fresh solver 在 600 秒 Run 中提交 `candidate_found`，定位 `PluginManager.register`，正确解释先写插件映射、再逐个加入 hook implementation、后续 `_verify_hook` 可抛 `PluginValidationError` 的部分状态风险，并给出先校验/暂存及回归验证建议。研究者在首轮报告与事件落盘后，参考合并修复 PR #741 写出事后解释性评分，结果 **10/10**；评分准则未预注册，不应视为盲测得分。原 Issue 标题和正文已经点名 `PluginManager.register` 并包含 TODO，因此这是有位置线索的历史 Issue，不是盲定位样本。 |
| Session 复用与隔离 | PASS | Issue Session `session-84f84838ab0c479fb9b3008c20c2fec3` 与新建空 Session `session-c8aced44a7fa4391abaef80e621dfe7d` 共享同一个 project/index；新 Session 为 0 消息、0 Run，Issue Session 的输入和 Run 历史保留在原 Session。 |
| 刷新与恢复 | PASS | 浏览器 reload 后恢复原 Session 消息、完成的外部 Run、8 个工具结果、19 个公开事件和报告；重新打开 `_manager.py` 证据时仍绑定旧 index 与原 content hash。 |
| 来源变化与显式更新 | PASS（仅索引范围） | 在独立 clone 切换到已核实的修复 merge commit `f51e3a70c7f8414bff96e05c4ddb066972353376` 后，检查检测到两个索引范围内 Python 文件变化。产品“更新索引”建立了新的 ready index；新 index 再检查为 consistent。旧 Session 和 Run 仍绑定原 snapshot。PR 还改动了一个 `.rst` 文件；当前 freshness 只扫描 Python/TypeScript/TSX，因此这次检查并不覆盖 Git diff 中的全部文件。 |

## 冻结来源与 Run

- 仓库：[pytest-dev/pluggy](https://github.com/pytest-dev/pluggy)，MIT；Issue：[Issue #733](https://github.com/pytest-dev/pluggy/issues/733)。修复参考：[PR #741](https://github.com/pytest-dev/pluggy/pull/741)，merge commit [`f51e3a7`](https://github.com/pytest-dev/pluggy/commit/f51e3a70c7f8414bff96e05c4ddb066972353376)。修复资料没有进入 solver 上下文或基线 snapshot。
- 初始项目 `project-6bb41715c4ff4eedbff6d6bc2ae6508d`，本地导入 `import-ad73b47ba9a2107435ad47460a97f969`，旧 index `index-import-ad73b47ba9a2107435ad47460a97f969-relations-v13-5c7945218a427004`，snapshot `5c7945218a427004d20df0008667baa113f121ec84b76ba65f6d3f66344c60a0`。
- 首轮 Run `run-27c09fc8dd9841c8a8a3630f44480169`，报告 `report-run-27c09fc8dd9841c8a8a3630f44480169`，终态 `succeeded / candidate_found`，预算 `max_seconds=600`、8 次工具调用。原始 payload 保存在 [raw-report.json](raw-report.json) 和 [raw-events.json](raw-events.json)；首轮报告落盘后编写的事后评分见 [first-round-scoring.json](first-round-scoring.json)，没有预注册 rubric。
- 来源改变后，新 index 为 `index-import-ad73b47ba9a2107435ad47460a97f969-relations-v13-865676bc1a866153`，snapshot `865676bc1a866153c8b1837f951158fff067c95756ebe87268848345e90d911e`，revision 仍显示 `fixture-v1`。变化过程和旧 Run 核验见 [freshness.json](freshness.json) 与 [freshness-final.json](freshness-final.json)。

本轮没有执行仓库代码或测试，也没有调用外部供应商。报告是对固定索引的静态审查，没有运行时复现；不能据此声称问题在某个调用方实际触发，或断言下游影响。原始源码 clone 保留在本机运行数据目录，未复制进本仓库结果目录。
