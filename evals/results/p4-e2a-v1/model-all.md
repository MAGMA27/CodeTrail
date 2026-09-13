# P4-E2a 离线假供应商 Run 评测

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`；划分：`all`
- 方法：`real-run-offline-fake-v1`；provider：`offline-fake`；模型：`codetrail-eval-fake-v1`
- 运行：`eval-model-6e38749ac3e9385763ae9be230dc6ef4`；代码 revision：`8740b37d957d8cfc26cffb766b74accb4a42a2b1`
- 原始状态：`evals/results/p4-e2a-v1/raw-state.json`；P4-E1 基线：`evals/results/p4-e1-v1/baseline-all.json`
- 模拟说明：本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。

## 汇总

样本 **6**，成功 **6**，失败 **0**；可定位 **4**，证据不足 **2**。

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
- 用量：model=3，repair=0，tool=2，model chars=23430/1125，tokens=unknown。

### `cache-key-tenant`
- #1 `repository.py:1-2` `repository.load_user`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，model chars=23103/1134，tokens=unknown。

### `partner-billing-outage`
- 无最终候选。
- 用量：model=1，repair=0，tool=0，model chars=6962/314，tokens=unknown。

### `redirect-query-encoding`
- #1 `auth.py:1-2` `auth.build_login_redirect`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，model chars=22124/1128，tokens=unknown。

### `retry-control-flow`
- #1 `worker.py:5-10` `worker.retry_request`；来源=`candidate_link`；证据=合法
- 用量：model=3，repair=0，tool=2，model chars=22545/1130，tokens=unknown。

### `remote-rollout-config`
- 无最终候选。
- 用量：model=1，repair=0，tool=0，model chars=6956/314，tokens=unknown。

## 边界与局限

本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。
模型结果只取最终 candidate_link/finding 的同快照证据；中间检索符号不计为答案，失败/取消不继承中间命中。P4-E1 基线和模型结果使用相同冻结案例及评分字段，但算术对照不能证明模型优于基线。
