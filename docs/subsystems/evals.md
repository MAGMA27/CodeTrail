# 独立评测

## 状态

P4-E1 已实现：六个冻结自有案例、独立标注、manifest/hash 校验、确定性 FTS5 关键词基线、评分器、离线命令和实际 JSON/Markdown 报告均已交付。P4-E2a 已实现：通过真实 Run 服务路径接入本地确定性 `offline_fake`，生成 gold-free 执行清单、raw Run 报告和基线对照；真实供应商语义质量仍未运行。

## 职责与范围

本子系统评估 Issue 到文件/符号/位置候选的排序、同快照 evidence 合法性、运行失败和证据不足案例的基线误报/无候选表现，并验证模型结果接入真实 Run 后的候选映射。源码、Issue、标注和报告的职责见 [evals README](../../evals/README.md)。它不执行案例代码或测试；P4-E2a 不读取网络、不调用真实模型，也不判断根因或修复正确性。

## 接口与依赖

入口是 `uv run --project apps/api python -m evals.cli`，P4-E1 数据和报告模型位于 `evals/models.py`，P4-E2a 模型评测模型位于 `evals/model_models.py`，编排位于 `evals/pipeline.py` 和 `evals/model_pipeline.py`。实现复用 [快照与关系](indexing.md) 的 Python 快照/定义/关系流程、[存储与查询](storage.md) 的 `IndexStore.search_symbols_bounded`/`read_evidence` 以及 [Run 执行](execution.md) 的 `WorkspaceService`、`RunExecutor`、白名单工具、预算、取消、报告和持久化；评测评分标注不进入检索或 Run 执行接口。

## 数据与生命周期

每例从显式 `cases/<case>/source/` 读取 UTF-8 Python 文件，先校验只读 manifest/hash；模型评测再将该案例复制到独立运行目录，由服务在副本下建立项目和 SQLite。`index_id` 由产品服务生成并绑定 Session/Run，结果额外记录冻结 manifest hash、Issue/input hash、Run/report ID 和 evidence hash。P4-E2a 每创建一个 Run 就写入 gold-free checkpoint，终态结果先落 raw state，恢复会读取已有终态而不重复执行。校准/留出划分在 `dataset.json` 固定，标注只在所有执行结果持久化后加载。

## 错误与限制

路径穿越、源码集合变化、manifest/hash、数据划分、标注范围或索引异常会导致校验/该例失败；失败计入汇总分母。搜索和 Run 工具均使用服务端上限，模型候选只取最终 `candidate_links`/`findings` 的同快照 evidence，最多三名并稳定去重；中间观察符号不算模型答案。Run 失败、取消、中断、超时、预算耗尽或非法候选映射均保留状态和原因，不继承中间命中。`baseline_no_candidate` 是检索没有候选，`model_insufficient_evidence` 才是模型明确弃答；静态 evidence 合法也不意味着候选是根因。token 用量缺失为 null/unknown。

## 验证

使用 `pnpm evals:test` 验证正确/错误位置、同名符号、跨快照、无候选、失败、重复候选排序、分母、真实 Run 离线假供应商、最终候选映射、预算和恢复；使用 `pnpm evals:validate` 验证六例冻结数据；使用 `pnpm evals:baseline` 实际运行全量关键词基线；使用 `pnpm evals:model-plan` 和 `pnpm evals:model-run` 生成并验证 P4-E2a 离线集成。交付报告位于 `evals/results/p4-e1-v1/` 和 `evals/results/p4-e2a-v1/`；后者明确标记模拟 provider。

## 相关决策

实现取舍与验收证据见 [独立评测集与离线关键词基线决策](../decisions/2026-09-13-independent-evaluation-baseline.md) 和 [真实 Run 离线评测决策](../decisions/2026-09-13-real-run-evaluation.md)；开发样本与独立评测的边界见 [fixtures README](../../fixtures/README.md)。
