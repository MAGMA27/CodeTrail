# 独立评测

## 状态

P4-E1 已实现：六个冻结自有案例、独立标注、manifest/hash 校验、确定性 FTS5 关键词基线、评分器、离线命令和实际 JSON/Markdown 报告均已交付。真实模型对比属于后续 P4-E2，尚未运行。

## 职责与范围

本子系统评估 Issue 到文件/符号/位置候选的排序、同快照 evidence 合法性、运行失败和证据不足案例的基线误报/无候选表现。源码、Issue、标注和报告的职责见 [evals README](../../evals/README.md)。它不执行案例代码或测试，不读取网络，不调用真实模型，不判断根因或修复正确性。

## 接口与依赖

入口是 `uv run --project apps/api python -m evals.cli`，数据和报告模型位于 `evals/models.py`，编排位于 `evals/pipeline.py`。实现复用 [快照与关系](indexing.md) 的 Python 快照/定义/关系流程和 [存储与查询](storage.md) 的 `IndexStore.search_symbols_bounded`、`read_evidence`；评测评分标注不进入这些检索接口。

## 数据与生命周期

每例从显式 `cases/<case>/source/` 读取 UTF-8 Python 文件，先校验只读 manifest/hash，再写入 `evals/.runtime/` 下独立 SQLite。`index_id` 由数据集版本、case_id 和 snapshot hash 确定；结果逐例绑定 Issue hash、snapshot hash、index_id 和 evidence hash。校准/留出划分在 `dataset.json` 固定，标注存放在源码目录外并只在检索完成后用于评分。

## 错误与限制

路径穿越、源码集合变化、manifest/hash、数据划分、标注范围或索引异常会导致校验/该例失败；失败计入汇总分母。搜索使用固定关键词上限、每词返回上限和最多三名候选，不能表达模型推理。`baseline_no_candidate` 是检索没有候选，不是模型弃答；静态 evidence 合法也不意味着候选是根因。

## 验证

使用 `pnpm evals:test` 验证正确/错误位置、同名符号、跨快照、无候选、失败、重复候选排序、分母和预算；使用 `pnpm evals:validate` 验证六例冻结数据；使用 `pnpm evals:baseline` 实际运行全量离线基线。交付报告位于 `evals/results/p4-e1-v1/`。

## 相关决策

实现取舍与验收证据见 [独立评测集与离线关键词基线决策](../decisions/2026-09-13-independent-evaluation-baseline.md)；开发样本与独立评测的边界见 [fixtures README](../../fixtures/README.md)。
