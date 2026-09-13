# P4-E2a 离线假供应商 Run 评测

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`；划分：`all`
- 方法：`real-run-offline-fake-v1`；provider：`offline-fake`；模型：`codetrail-eval-fake-v1`
- 运行：`eval-model-7e20ae4bbb54160c415eb6dfa189f191`；代码 revision：`4df725f8d9715044c14da7f78d1851b8d42b5181`
- 原始状态：`evals/results/p4-e2a-fix-v1/raw-state.json`；P4-E1 基线：`evals/results/p4-e1-v1/baseline-all.json`
- 整次预算：cases=100，model calls=24，input/model input chars=600000/800000，output/model output chars=300000/120000，seconds=180
- 累计已结算：cases=6，model calls=14，input/model input chars=1169/105120，output/model output chars=12267/5145，elapsed_ms=6478；未结算预留 cases=0
- 模拟说明：本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。

## 汇总

样本 **6**，成功 **6**，失败 **0**，跳过 **0**；可定位 **4**，证据不足 **2**。

| 指标 | 分子/分母 | 值 |
| --- | ---: | ---: |
| file Hit@1 | 3/4 | 0.75 |
| file Hit@3 | 3/4 | 0.75 |
| symbol Hit@1 | 3/4 | 0.75 |
| symbol Hit@3 | 3/4 | 0.75 |
| 证据合法率 | 4/4 | 1.0 |
| 模型明确证据不足率 | 2/2 | 1.0 |
| 证据不足误报率 | 0/2 | 0.0 |

## 逐例结果

| 案例 | 划分 | Run | outcome | file H@1/H@3 | symbol H@1/H@3 | 候选 |
| --- | --- | --- | --- | --- | --- | ---: |
| `csv-import-validation` | calibration | succeeded | candidate_found | 1/1 | 1/1 | 1 |
| `cache-key-tenant` | calibration | succeeded | candidate_found | 0/0 | 0/0 | 1 |
| `partner-billing-outage` | calibration | succeeded | insufficient_evidence | n/a/n/a | n/a/n/a | 0 |
| `redirect-query-encoding` | holdout | succeeded | candidate_found | 1/1 | 1/1 | 1 |
| `retry-control-flow` | holdout | succeeded | candidate_found | 1/1 | 1/1 | 1 |
| `remote-rollout-config` | holdout | succeeded | insufficient_evidence | n/a/n/a | n/a/n/a | 0 |

## 候选、用量与失败详情

### `csv-import-validation`
- #1 `reader.py:1-2` `reader.load_rows`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，input/model input chars=167/23430，output/model output chars=3231/1125，tokens=unknown。

### `cache-key-tenant`
- #1 `repository.py:1-2` `repository.load_user`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，input/model input chars=181/23103，output/model output chars=3033/1134，tokens=unknown。

### `partner-billing-outage`
- 无最终候选。
- 用量：model=1，repair=0，tool=0，input/model input chars=242/6962，output/model output chars=314/314，tokens=unknown。

### `redirect-query-encoding`
- #1 `auth.py:1-2` `auth.build_login_redirect`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，input/model input chars=177/22124，output/model output chars=2534/1128，tokens=unknown。

### `retry-control-flow`
- #1 `worker.py:5-10` `worker.retry_request`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，input/model input chars=162/22545，output/model output chars=2841/1130，tokens=unknown。

### `remote-rollout-config`
- 无最终候选。
- 用量：model=1，repair=0，tool=0，input/model input chars=240/6956，output/model output chars=314/314，tokens=unknown。

## 边界与局限

本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。
模型结果只取最终 candidate_link/finding 的同快照证据；中间检索符号不计为答案，失败/取消/中断不继承中间命中，budget_skipped 保留在总样本分母。token 用量未知时保持 unknown/null，整次预算使用可执行的调用/字符/墙钟最坏预留，不能据此推断 token 费用。P4-E1 基线和模型结果使用相同冻结案例及评分字段，但算术对照不能证明模型优于基线。
