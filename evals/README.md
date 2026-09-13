# CodeTrail 独立评测

## 职责与边界

`evals/` 保存与 `fixtures/` 分开的冻结 Python Issue 检索评测集、源码 manifest、评分标注、确定性关键词基线和离线报告。它只评估固定修复前快照上的候选排序与证据合法性，不执行被分析源码或测试，不调用真实模型、网络或训练平台，也不证明根因推理、修复正确性或真实仓库泛化能力。

评测源码位于 `cases/<case>/source/`，只允许通过数据集显式引用的目录建索引；Issue 位于 `issues/`，评分标注位于源码目录外的 `annotations/`，不能进入索引或基线检索函数。`timeout-demo` 仍是开发演示样本，不属于本评测集。

## 入口与目录导航

- `dataset.json`：数据集版本、案例、校准/留出划分和相对文件引用。
- `cases/`：六个自有小型 Python 案例；源码不含答案注释或 gold 文件。
- `issues/`：六个冻结 Issue 输入。
- `manifests/`：每例源码文件大小、SHA-256 与 snapshot hash。
- `annotations/`：独立评分依据、预期文件/符号/位置和证据不足标签；基线运行阶段不读取。
- `models.py`：评测报告、候选、用量和指标的 Pydantic 模型。
- `pipeline.py`：manifest 校验、隔离索引、FTS5 关键词基线和评分编排。
- `cli.py`：离线命令入口。
- `tests/`：数据、评分、预算、失败、重复候选排序和连续运行一致性测试。
- `results/p4-e1-v1/`：提交的实际基线 JSON/Markdown 报告；`.runtime/` 为本机受控 SQLite 临时目录，不纳入 Git。

## 接口与依赖

评测复用 [快照与关系](../docs/subsystems/indexing.md) 的只读 `build_source_snapshot`、定义/关系提取和 [存储与查询](../docs/subsystems/storage.md) 的 `IndexStore`/FTS5 搜索与快照 evidence。评测自己的数据与报告模型在 `models.py`；产品运行时契约仍由 [API contracts](../apps/api/app/contracts.py) 拥有。

关键词基线只接收当前案例的 `Issue.title`/`Issue.body` 和该案例 `index_id`，按固定 tokenizer、首次出现去重、每词上限、候选聚合排序和 evidence 读取规则运行；`mentioned_paths` 不参与查询。评分编排在检索完成后才应用独立标注。后续真实 Run 导入应转换到本目录的结果格式，但本步不付费运行模型。

## 数据与生命周期

`dataset.json` 的版本和每例 manifest/hash 冻结源码身份；每例使用独立 `repository_id`、确定性 `index_id` 和独立 SQLite 索引，数据库只写入 `evals/.runtime/`。执行命令不修改原始案例；输出报告写入指定结果目录，包含数据集版本、代码 revision、工作树状态、配置、Issue hash、snapshot hash、用量、耗时、逐例候选和失败原因。

校准集和留出集在 `p4-e1-v1` 中固定为各三例，四例有可定位缺陷，两例标记为本地证据不足/外部因素。修复参考与评分依据不进入源目录、索引或工具可读根目录；修改冻结源码必须同步提升数据版本，不能只更新答案。

## 使用与配置

从仓库根目录执行以下命令；这些命令不需要 API Key 或网络：

```powershell
pnpm evals:validate
pnpm evals:test
pnpm evals:baseline
```

等价的 Python 入口为：

```powershell
uv run --project apps/api python -m evals.cli validate
uv run --project apps/api python -m evals.cli build-index --split all
uv run --project apps/api python -m evals.cli run --split all --runtime-dir evals/.runtime --output-dir evals/results/p4-e1-v1
```

`run` 会生成 `baseline-all.json` 和 `baseline-all.md`；也可传 `--split calibration` 或 `--split holdout`。配置由 `BaselineConfig` 固定记录，当前 `model_calls=0`、`network_calls=0`，不从环境变量读取评测参数。

## 错误与限制

manifest、路径、Issue、标注、行范围或数据划分不一致时命令失败；索引失败、搜索失败和超时都保留为逐例 `failed`，计入分母，不丢弃样本。候选的证据必须来自同一 `index_id` 和冻结文件 hash；证据合法不等于根因正确。

本基线只能说明关键词检索返回了什么。证据不足案例的 `baseline_no_candidate` 仅表示关键词没有候选，不能当作模型弃答；无候选检索结果不等于模型完成了证据不足判断。小型合成案例、固定词法和当前 FTS5 排序不能代表大型真实仓库或真实供应商质量。

## 验证

`pnpm evals:test` 运行隔离评分与重复性测试；`pnpm evals:validate` 校验数据、源码集合、manifest/hash 和标注边界；`pnpm evals:baseline` 生成本地离线报告。文档变更使用 `pnpm docs:check`；本轮不修改共享产品契约，因此不新增 Schema 导出。

## 关联文档

评测隔离和评分取舍见 [独立评测集与离线关键词基线决策](../docs/decisions/2026-09-13-independent-evaluation-baseline.md)，目录关系见 [子系统索引](../docs/subsystems/README.md)，开发样本边界见 [fixtures README](../fixtures/README.md)。产品阶段计划仍由本地 `memory-bank/next.md` 控制，`memory-bank/` 按项目约定保持 Git 忽略。
