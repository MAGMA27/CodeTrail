# P4-E2a 基线与 Run 评测对照

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`；方法：`real-run-offline-fake-v1`
- 基线：`evals/results/p4-e1-v1/baseline-all.json`；模型报告：`evals/results/p4-e2a-fix-v1/model-all.json`
- 模拟说明：本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。

## 分组汇总

| 分组 | 方法 | file H@1 | file H@3 | symbol H@1 | symbol H@3 | 失败 | 跳过 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| all | baseline | 2/4 (0.5) | 4/4 (1.0) | 0/4 (0.0) | 3/4 (0.75) | 0/6 | 0/6 |
| all | offline fake Run | 3/4 (0.75) | 3/4 (0.75) | 3/4 (0.75) | 3/4 (0.75) | 0/6 | 0/6 |
| calibration | baseline | 1/2 (0.5) | 2/2 (1.0) | 0/2 (0.0) | 1/2 (0.5) | 0/3 | 0/3 |
| calibration | offline fake Run | 1/2 (0.5) | 1/2 (0.5) | 1/2 (0.5) | 1/2 (0.5) | 0/3 | 0/3 |
| holdout | baseline | 1/2 (0.5) | 2/2 (1.0) | 0/2 (0.0) | 2/2 (1.0) | 0/3 | 0/3 |
| holdout | offline fake Run | 2/2 (1.0) | 2/2 (1.0) | 2/2 (1.0) | 2/2 (1.0) | 0/3 | 0/3 |

## 逐例对照

| 案例 | 划分 | 基线 symbol H@1/H@3 | fake Run symbol H@1/H@3 | Run 状态 |
| --- | --- | --- | --- | --- |
| `csv-import-validation` | calibration | 0/0 | 1/1 | succeeded |
| `cache-key-tenant` | calibration | 0/1 | 0/0 | succeeded |
| `partner-billing-outage` | calibration | n/a/n/a | n/a/n/a | succeeded |
| `redirect-query-encoding` | holdout | 0/1 | 1/1 | succeeded |
| `retry-control-flow` | holdout | 0/1 | 1/1 | succeeded |
| `remote-rollout-config` | holdout | n/a/n/a | n/a/n/a | succeeded |

## 解释边界

这是相同冻结输入上的指标对照和执行状态记录。provider 是本地模拟适配器，不能据此作出真实模型优劣、根因能力或供应商质量结论；留出集分母、失败、跳过、弃答和误报均按报告逐例保留。
