# CodeTrail 独立评测

## 职责与边界

`evals/` 保存与 `fixtures/` 分开的冻结 Python Issue 检索评测集、源码 manifest、评分标注、确定性关键词基线、真实 Run 离线适配和离线报告。P4-E2a 的 `offline_fake` 只验证产品 Run 协议和评分接线，不代表真实模型质量；本步不调用外部模型或网络。

评测源码位于 `cases/<case>/source/`，只允许通过数据集显式引用的目录建索引；Issue 位于 `issues/`，评分标注位于源码目录外的 `annotations/`，不能进入索引或基线检索函数。`timeout-demo` 仍是开发演示样本，不属于本评测集。

## 入口与目录导航

- `dataset.json`：数据集版本、案例、校准/留出划分和相对文件引用。
- `cases/`：六个自有小型 Python 案例；源码不含答案注释或 gold 文件。
- `issues/`：六个冻结 Issue 输入。
- `manifests/`：每例源码文件大小、SHA-256 与 snapshot hash。
- `annotations/`：独立评分依据、预期文件/符号/位置和证据不足标签；基线运行阶段不读取。
- `models.py`：P4-E1 报告、候选、用量和指标的 Pydantic 模型。
- `model_models.py`：P4-E2a dry-run、Run 原始结果、候选映射和对照报告模型。
- `pipeline.py`：manifest 校验、隔离索引、FTS5 关键词基线和评分编排；支持先加载无 gold 的冻结输入。
- `model_pipeline.py`：通过 `WorkspaceService`/`RunExecutor` 执行或恢复 real Run，并在执行完成后评分。
- `cli.py`：离线命令入口。
- `tests/`：数据、评分、预算、失败、候选映射、隔离和恢复一致性测试。
- `results/p4-e1-v1/`：提交的实际基线 JSON/Markdown 报告；`.runtime/` 为本机受控 SQLite 临时目录，不纳入 Git。
- `results/p4-e2a-v1/`：提交的离线假供应商执行清单、raw Run 报告和基线对照报告；raw state 不含 gold，可用于恢复。

## 接口与依赖

评测复用 [快照与关系](../docs/subsystems/indexing.md) 的只读 `build_source_snapshot`、定义/关系提取和 [存储与查询](../docs/subsystems/storage.md) 的 `IndexStore`/FTS5 搜索与快照 evidence。评测自己的数据与报告模型在 `models.py`；产品运行时契约仍由 [API contracts](../apps/api/app/contracts.py) 拥有。

关键词基线只接收当前案例的 `Issue.title`/`Issue.body` 和该案例 `index_id`，按固定 tokenizer、首次出现去重、每词上限、候选聚合排序和 evidence 读取规则运行；`mentioned_paths` 不参与查询。P4-E2a 只给每个 real Run 传入该案例 Issue、完整保存 Session 消息前缀和 Run 工具返回的同快照上下文；工具根目录是单案例源码副本，annotation、修复参考、其他案例和基线候选不进入执行路径。评分编排在所有执行结果持久化后才读取独立标注。

模型候选只从最终报告的 `candidate_links` 和 `findings` 转换，工具中间观察符号不算答案；同一位置稳定去重，非法/跨快照/缺失 evidence 记录映射失败并清空可评分候选。Run 失败、取消、中断和超时保留原状态与原因，不继承已有中间命中。模型 token 用量未知时输出 `null`/`unknown`，不以零代替。

## 数据与生命周期

`dataset.json` 的版本和每例 manifest/hash 冻结源码身份；每例使用独立 `repository_id`、确定性 `index_id` 和独立 SQLite 索引，数据库只写入 `evals/.runtime/`。P4-E2a 还把单案例源码复制到 `.runtime/p4-e2a-v1/<case>/source/`，原始 `cases/` 不被写入；服务数据目录的 `config.json` 只声明 `offline-fake`，不含密钥。执行清单先写出案例、发送字段、源码文件、模型、预算、调用上限和结果路径；每例创建 Run 后立即写 checkpoint，终态结果写入 raw state，恢复不会重新执行已有终态 Run。输出报告包含数据集版本、代码 revision、Issue/input hash、snapshot hash、Run/report ID、配置、用量、耗时、逐例候选和失败原因。

校准集和留出集在 `p4-e1-v1` 中固定为各三例，四例有可定位缺陷，两例标记为本地证据不足/外部因素。修复参考与评分依据不进入源目录、索引或工具可读根目录；修改冻结源码必须同步提升数据版本，不能只更新答案。

## 使用与配置

从仓库根目录执行以下命令；这些命令不需要 API Key 或网络：

```powershell
pnpm evals:validate
pnpm evals:test
pnpm evals:baseline
pnpm evals:model-plan
pnpm evals:model-run
pnpm evals:render-baseline
```

等价的 Python 入口为：

```powershell
uv run --project apps/api python -m evals.cli validate
uv run --project apps/api python -m evals.cli build-index --split all
uv run --project apps/api python -m evals.cli run --split all --runtime-dir evals/.runtime --output-dir evals/results/p4-e1-v1
uv run --project apps/api python -m evals.cli model-plan --output-dir evals/results/p4-e2a-v1
uv run --project apps/api python -m evals.cli model-run --provider offline_fake --runtime-dir evals/.runtime/p4-e2a-v1 --output-dir evals/results/p4-e2a-v1
uv run --project apps/api python -m evals.cli render-baseline
```

`run` 会生成 `baseline-all.json` 和 `baseline-all.md`；也可传 `--split calibration` 或 `--split holdout`。配置由 `BaselineConfig` 固定记录，当前 `model_calls=0`、`network_calls=0`，不从环境变量读取评测参数。

`model-plan` 只生成不含标注的 dry-run 清单；`model-run` 默认使用本地假供应商，自动生成 `model-<split>.json/.md`、`comparison-<split>.json/.md` 和每例原始 Run 报告。已有 `raw-state.json` 时默认恢复，`--no-resume` 只用于明确拒绝旧状态；`--provider external` 在 P4-E2a 中会失败，不能绕过显式授权调用外网。

## 错误与限制

manifest、路径、Issue、标注、行范围或数据划分不一致时命令失败；索引失败、搜索失败和超时都保留为逐例 `failed`，计入分母，不丢弃样本。候选的证据必须来自同一 `index_id` 和冻结文件 hash；证据合法不等于根因正确。

本基线只能说明关键词检索返回了什么。证据不足案例的 `baseline_no_candidate` 仅表示关键词没有候选，不能当作模型弃答；无候选检索结果不等于模型完成了证据不足判断。小型合成案例、固定词法和当前 FTS5 排序不能代表大型真实仓库或真实供应商质量。

模型执行的 Run 失败、取消、中断、预算耗尽、非法结构化输出和候选映射失败均保留执行状态与有限错误原因；真实 Run 终态报告可在恢复时读取。外部 provider 在本步显式拒绝，避免把离线适配误认为付费语义运行。

## 验证

`pnpm evals:test` 运行隔离评分、真实 Run 离线适配、候选映射、预算失败和恢复测试；`pnpm evals:validate` 校验数据、源码集合、manifest/hash 和标注边界；`pnpm evals:baseline` 生成本地离线基线；`pnpm evals:model-plan` 验证 gold-free 执行清单；`pnpm evals:model-run` 生成离线集成和对照报告。文档变更使用 `pnpm docs:check`；本轮不修改共享产品契约，因此不新增 API Schema 导出。

## 关联文档

评测隔离和评分取舍见 [独立评测集与离线关键词基线决策](../docs/decisions/2026-09-13-independent-evaluation-baseline.md)，真实 Run 离线适配见 [真实 Run 离线评测决策](../docs/decisions/2026-09-13-real-run-evaluation.md)，目录关系见 [子系统索引](../docs/subsystems/README.md)，开发样本边界见 [fixtures README](../fixtures/README.md)。产品阶段计划仍由本地 `memory-bank/next.md` 控制，`memory-bank/` 按项目约定保持 Git 忽略。
