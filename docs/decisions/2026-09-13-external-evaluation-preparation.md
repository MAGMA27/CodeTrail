# 决策：外部供应商评测入口与费用边界

Status: implemented

## 问题

P4-E2a 已沿产品真实 Run 路径完成离线协议验证，但 `external` 入口仍被拒绝。下一步需要先把真实供应商接线、请求隔离、恢复身份和费用边界做成可审查、可测试的入口；没有明确的发送范围、持久化配置和费用上限时，不能发送仓库源码或产生真实费用。

## 决定

P4-E2b external 入口复用产品已有的 OpenAI-compatible HTTP 适配器和 `WorkspaceService`/`RunExecutor`，当前首个评测供应商固定为 DeepSeek，模型固定为 `deepseek-v4-flash`。CLI 通过 `--provider external` 明确选择，真实执行还必须显式传 `--allow-external`；默认 `model-plan` 和 `model-run` 仍是无网络的 `offline_fake`。

供应商配置只从服务端持久化 `config.json` 读取。评测计划可以在 key 缺失时生成并显示 `configuration_status=missing`，但 `model-run` 在创建 Run、写入 external runtime 或发出请求前拒绝空 key。隔离案例把已校验的配置对象进程内传给服务；不会在案例 runtime 创建配置副本，也不会把 API key 写入命令参数、日志、raw state、报告、清单或 Git。执行身份只保存包含 provider、base URL、model 和 key SHA-256 的配置指纹；配置或模型变化要求建立新计划。

每个 external Run 仍只接收冻结 Issue、完整保存 Session 消息前缀和该案例同 snapshot 的受限工具结果；annotation、修复参考、基线答案和其他案例源码在评分完成前不可读取。结果使用独立的 `p4-e2b-external-v1` 目录，不能覆盖 P4-E1/P4-E2a 结果。已有终态不重跑；请求中断、取消、服务错误和未知 usage 不回退 fixture，不无限重试，并按最坏保留费用预留以便恢复。

费用预算使用整数 micro-USD 保存并纳入累计评测身份。当前 DeepSeek 官方 [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing) 页面于 2026-09-13 核实，策略记录 USD peak/cache-miss 档：输入 cache miss `$0.44/1M tokens`，输出 `$1.32/1M tokens`；cache hit 价格虽记录在官方依据中，但不用于最坏预留。请求前按单 Run 字符预算和保守 token/字符假设预留费用；供应商返回完整 token 后仍按 cache-miss 价格保守估算，缺 token、失败或中断则消耗整例最坏预留。未知价格或不支持的 provider/model 不允许进入 external 评测。

执行顺序固定为：先生成 calibration 三例的 gold-free 清单，再审核源码范围、配置状态、模型、恢复方式和累计费用上限；只有明确授权后才执行 calibration，提示词/预算校准不得查看 holdout 标注后反复调参。holdout 继续使用冻结输入并保留失败/跳过分母。

## 替代方案

- 把 key 放在环境变量或命令行：会脱离项目的服务端持久化边界，可能进入进程诊断、shell 历史或日志，拒绝采用。
- 在评测目录复制 `config.json`：会把秘密带进隔离产物，拒绝采用。
- external 直接复用 `OfflineFakeIssueAdapter`：无法证明真实 HTTP 传输，且会把离线成绩伪装成供应商成绩，拒绝采用。
- 未知 token/价格按零费用：会虚假宣称费用可控，拒绝采用；改为保守预留并保留未知状态。
- 自动联网、自动重试或自动扩大额度：超出当前授权，拒绝采用。

## 验收与证据

实现入口为 [评测模型契约](../../evals/model_models.py)、[评测编排](../../evals/model_pipeline.py)、[评测 CLI](../../evals/cli.py) 和 [评测 README](../../evals/README.md)。

- `evals/tests/test_model_pipeline.py` 使用本地 HTTP server 验证 external Bearer 请求、模型和 JSON 协议、密钥脱敏、预算前跳过、429 错误、未知 usage、取消、恢复和配置指纹变化；测试不访问互联网。
- `evals/results/p4-e2b-external-v1/dry-run-plan.json/.md` 是三例 calibration 的真实清单产物；当前服务配置没有可用 key，清单状态为 `missing`，实际供应商 Run 为 `UNVERIFIED`。
- 默认 DeepSeek 配置已切换为 `deepseek-v4-flash`；产品层仍通过持久化 `config.json` 使用 OpenAI-compatible `/chat/completions`。
- `pnpm evals:validate`、`pnpm evals:test`、后端回归、Python 编译、`pnpm contracts:check` 和文档检查通过后，才可宣告本步准备完成；不把离线报告当作真实供应商语义结果。

## 后果

仓库现在有可审查的 external 入口和明确的“先计划、后授权、再执行”边界；真实执行仍能被 key、授权、费用上限、配置指纹和独立结果目录约束。当前没有真实 DeepSeek 请求、真实 token 账单或供应商语义质量证据，后续必须在用户批准具体清单和额度后单独运行并记录原始结果。

`memory-bank/` 继续按项目约定保持 Git 忽略，`next.md` 由用户控制且本步不修改。
