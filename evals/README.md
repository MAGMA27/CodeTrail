# CodeTrail 独立评测

## 职责与边界

`evals/` 保存与 `fixtures/` 分开的冻结 Python Issue 检索评测集、源码 manifest、评分标注、确定性关键词基线、真实 Run 适配和评测报告。P4-E2a 的 `offline_fake` 只验证产品 Run 协议和评分接线；P4-E2b 提供受门控的 DeepSeek 外部入口，但本仓库验证和默认命令不调用外部模型或网络。

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
- `model_pipeline.py`：通过 `WorkspaceService`/`RunExecutor` 执行或恢复 offline/external real Run，并在执行完成后评分。
- `cli.py`：离线命令和显式 external 计划/执行入口。
- `tests/`：数据、评分、预算、失败、候选映射、隔离、恢复和本地 HTTP external 协议测试。
- `results/p4-e1-v1/`：提交的实际基线 JSON/Markdown 报告；`.runtime/` 为本机受控 SQLite 临时目录，不纳入 Git。
- `results/p4-e2a-v1/`：历史 P4-E2a 离线报告，保留为不可覆盖的事实记录。
- `results/p4-e2a-fix-v1/`：P4-E2a 验收修复后的独立执行清单、raw Run 报告和基线对照报告；raw state 不含 gold，可用于恢复且包含 v2 执行身份与累计预算。
- `results/p4-e2b-external-v1/`：当前 DeepSeek calibration external dry-run 清单；配置缺失时只记录 `configuration_status=missing`，不代表真实评测已执行。
- [results/p4-e2b-codex-host-20261001/](results/p4-e2b-codex-host-20261001/codex-host-holdout.md)：实际 Codex 宿主已知案例复测，保留首轮失败和独立补充 Run；[影响链验收](results/p4-e2b-codex-host-20261001/impact-chain-acceptance.md) 记录接口、截图与未验收的 UI 边界，不代表盲测或供应商质量。
- [results/p4-e2b-codex-host-20261007/](results/p4-e2b-codex-host-20261007/README.md)：三例自有 synthetic Issue 的新一轮宿主验收；[首轮评分](results/p4-e2b-codex-host-20261007/first-round-scoring.json)、[提交契约失败记录](results/p4-e2b-codex-host-20261007/submission-failures.md) 与[独立指导 UI 补验](results/p4-e2b-codex-host-20261007/guided-acceptance.md)分开保存。首轮 0/3 为 submit 形状误用后 Run 超时；三例和过程隔离均不代表外部仓库泛化或训练集陌生性。

## 接口与依赖

评测复用 [快照与关系](../apps/api/indexing.md) 的只读 `build_source_snapshot`、定义/关系提取和 [存储与查询](../apps/api/storage.md) 的 `IndexStore`/FTS5 搜索与快照 evidence。评测自己的数据与报告模型在 `models.py`；产品运行时契约仍由 [API contracts](../apps/api/app/contracts.py) 拥有。

关键词基线只接收当前案例的 `Issue.title`/`Issue.body` 和该案例 `index_id`，按固定 tokenizer、首次出现去重、每词上限、候选聚合排序和 evidence 读取规则运行；`mentioned_paths` 不参与查询。P4-E2a 只给每个 real Run 传入该案例 Issue、完整保存 Session 消息前缀和 Run 工具返回的同快照上下文；工具根目录是单案例源码副本，annotation、修复参考、其他案例和基线候选不进入执行路径。评分编排在所有执行结果持久化后才读取独立标注。

模型候选只从最终报告的 `candidate_links` 和 `findings` 转换，工具中间观察符号不算答案；候选按现有评分配置最多取三名，同一位置稳定去重；非法/跨快照/缺失 evidence 记录映射失败并清空可评分候选。Run 失败、取消、中断和超时保留原状态与原因，不继承已有中间命中；整次预算不足时未启动的案例保留为 `budget_skipped`，仍进入总样本与定位指标分母。模型 token 用量未知时输出 `null`/`unknown`，不以零代替；整次预算以可执行的调用/字符/墙钟最坏预留约束。external 另外要求显式费用上限，并按已核实的 cache-miss 最坏价格预留；供应商未返回 token 或请求中断时整例保留最坏费用，不能把未知费用记为零。

## 数据与生命周期

`dataset.json` 的版本和每例 manifest/hash 冻结源码身份；每例使用独立 `repository_id`、确定性 `index_id` 和独立 SQLite 索引，数据库只写入 `evals/.runtime/`。每个案例源码复制到对应评测 runtime，原始 `cases/` 不被写入；offline runtime 只生成无密钥配置，external 将已校验的 `ModelProviderConfig` 进程内注入隔离服务，不把 key 写入评测 runtime、raw state、报告、命令参数或日志。冻结的 Issue hash、源码/index snapshot、输入构造版本、方法（含 provider/model/prompt/execution、配置指纹和费用策略）、单 Run/整次预算及 code revision 组成 v2 执行身份；配置变化必须重新建计划，恢复不会悄悄沿用旧确认。执行清单先写出案例、发送字段、源码文件、模型、预算、调用上限、费用上限、结果路径和恢复方式；每个 Run 启动前先持久化整次评测的最坏预算预留，终态结果按实际/保守用量结算，缺少 checkpoint 的处理中断按最坏预留记账，不能靠重启扩大额度。

校准集和留出集在 `p4-e1-v1` 中固定为各三例，四例有可定位缺陷，两例标记为本地证据不足/外部因素。修复参考与评分依据不进入源目录、索引或工具可读根目录；修改冻结源码必须同步提升数据版本，不能只更新答案。

## 使用与配置

从仓库根目录执行以下命令；这些默认命令不需要 API Key 或网络：

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
uv run --project apps/api python -m evals.cli model-plan --output-dir evals/results/p4-e2a-fix-v1
uv run --project apps/api python -m evals.cli model-run --provider offline_fake --runtime-dir evals/.runtime/p4-e2a-fix-v1 --output-dir evals/results/p4-e2a-fix-v1
uv run --project apps/api python -m evals.cli render-baseline
```

`run` 会生成 `baseline-all.json` 和 `baseline-all.md`；也可传 `--split calibration` 或 `--split holdout`。配置由 `BaselineConfig` 固定记录，当前 `model_calls=0`、`network_calls=0`，不从环境变量读取评测参数。

`model-plan` 只生成不含标注的 dry-run 清单；`model-run` 默认使用本地假供应商和独立的 `p4-e2a-fix-v1` 结果目录，自动生成 `model-<split>.json/.md`、`comparison-<split>.json/.md` 和每例原始 Run 报告。已有 `raw-state.json` 时默认恢复，但必须通过包含 Issue、源码/index、输入构造、方法、配置指纹、费用策略和累计预算的 v2 身份核对；不一致时拒绝并保持既有计划、状态和报告不变。

external 入口必须显式指定 DeepSeek 配置文件和费用上限；计划不需要 key，缺 key 会标记为 `missing`，实际执行还必须同时传 `--allow-external`，否则在任何 Run/网络写入前拒绝。配置示例位于 `apps/api/config.example.json`，复制到服务数据目录的持久化 `config.json` 后填写 key；评测 CLI 也可用 `--config-file` 指定该文件。当前成本策略以官方 [DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) 在 2026-09-13 核实的 USD peak/cache-miss 档为依据：输入 `$0.44/1M`、输出 `$1.32/1M`，未知 token 采用整例最坏预留，实际结算仍是保守估算。

例如，下面只生成三例 calibration 清单，不发请求；本仓库当前生成的清单在 `results/p4-e2b-external-v1/`：

```powershell
uv run --project apps/api python -m evals.cli model-plan --provider external --split calibration --max-eval-cost-usd 0.25 --output-dir evals/results/p4-e2b-external-v1
```

真实执行必须在清单、源码发送范围、模型、恢复方式和费用上限审核后另行授权；执行命令还需显式 `--allow-external`，并建议使用独立 `p4-e2b-external-v1` 输出目录。没有该授权或可用 key 时，真实评测状态是 `UNVERIFIED`，不能把 offline_fake 报告当作供应商成绩。

## 错误与限制

manifest、路径、Issue、标注、行范围或数据划分不一致时命令失败；索引失败、搜索失败和超时都保留为逐例 `failed`，计入分母，不丢弃样本。累计预算不足的未执行项保留为 `budget_skipped`，同样不从总样本和定位指标分母中删除。候选的证据必须来自同一 `index_id` 和冻结文件 hash；证据合法不等于根因正确。

本基线只能说明关键词检索返回了什么。证据不足案例的 `baseline_no_candidate` 仅表示关键词没有候选，不能当作模型弃答；无候选检索结果不等于模型完成了证据不足判断。小型合成案例、固定词法和当前 FTS5 排序不能代表大型真实仓库或真实供应商质量。

模型执行的 Run 失败、取消、中断、预算耗尽、非法结构化输出和候选映射失败均保留执行状态与有限错误原因；真实 Run 终态报告可在恢复时读取。raw state 使用 v2 身份和持久化累计预留/消耗，旧格式或身份错配明确拒绝且不覆盖既有文件。external 的鉴权失败、限流、服务中断和未知 usage 不会回退 fixture 或无限重试；配置指纹变化、过期计划和不足费用上限均要求重新计划/授权。

## 验证

`pnpm evals:test` 运行隔离评分、真实 Run 离线适配、external 本地 HTTP 协议、候选映射、累计费用/预算、失败/取消/中断、状态身份拒绝和恢复测试；`pnpm evals:validate` 校验数据、源码集合、manifest/hash 和标注边界；`pnpm evals:baseline` 生成本地离线基线；`pnpm evals:model-plan` 生成默认 offline 清单。文档变更使用 `pnpm docs:check`；本轮新增服务内部配置注入但未改变 HTTP/MCP 响应契约，仍执行契约回归。

## 关联文档

评测隔离和评分取舍见 [独立评测集与离线关键词基线决策](../docs/decisions/2026-09-13-independent-evaluation-baseline.md)，真实 Run 离线适配见 [真实 Run 离线评测决策](../docs/decisions/2026-09-13-real-run-evaluation.md)，外部入口与费用边界见 [外部供应商评测准备决策](../docs/decisions/2026-09-13-external-evaluation-preparation.md)，目录关系见 [子系统索引](../docs/subsystems/README.md)，开发样本边界见 [fixtures README](../fixtures/README.md)。产品阶段计划仍由本地 `memory-bank/next.md` 控制，`memory-bank/` 按项目约定保持 Git 忽略。
