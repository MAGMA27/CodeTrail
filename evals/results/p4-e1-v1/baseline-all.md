# P4-E1 离线关键词基线

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`
- 基线：`keyword-fts5-v1`，划分：`all`
- 运行：`eval-run-db0acea332e2ee00489c5219c6d1be2f`，代码 revision：`c2e7db1875bccca0c2dbfc5321d88f022d988f18`
- 输入仓库状态：`dirty`；模型调用：`0`；网络调用：`0`

## 汇总

样本 **6**，成功 **6**，失败 **0**；可定位 **4**，证据不足 **2**。

| 指标 | 分子/分母 | 值 |
| --- | ---: | ---: |
| file Hit@1 | 2/4 (0.5) |
| file Hit@3 | 4/4 (1.0) |
| symbol Hit@1 | 0/4 (0.0) |
| symbol Hit@3 | 3/4 (0.75) |
| 证据位置/hash/归属合法率 | 17/17 (1.0) |
| 证据不足基线无候选率 | 0/2 (0.0) |
| 证据不足基线误报率 | 2/2 (1.0) |

## 逐例结果

| 案例 | 划分 | 状态 | 类型 | file H@1/H@3 | symbol H@1/H@3 | location H@1/H@3 | 候选 |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| `csv-import-validation` | calibration | succeeded | input_validation | 0/1 | 0/0 | 0/1 | 3 |
  - #1 `app.py:1-7` `app`；terms=`import,rows`；证据=合法
  - #2 `app.py:5-7` `app.import_records`；terms=`import,rows`；证据=合法
  - #3 `reader.py:1-6` `reader`；terms=`rows,name`；证据=合法
| `cache-key-tenant` | calibration | succeeded | state_isolation | 1/1 | 0/1 | 1/1 | 3 |
  - #1 `service.py:1-7` `service`；terms=`cache`；证据=合法
  - #2 `service.py:5-7` `service.get_profile`；terms=`cache`；证据=合法
  - #3 `cache.py:1-8` `cache`；terms=`cache`；证据=合法
| `partner-billing-outage` | calibration | succeeded | external_evidence_missing | n/a/n/a | n/a/n/a | n/a/n/a | 3 |
  - #1 `billing/config.py:1-2` `billing.config.billing_endpoint`；terms=`billing,endpoint`；证据=合法
  - #2 `billing/config.py:1-2` `billing.config`；terms=`billing,endpoint`；证据=合法
  - #3 `billing/client.py:1-5` `billing.client`；terms=`billing,endpoint`；证据=合法
| `redirect-query-encoding` | holdout | succeeded | data_flow_encoding | 0/1 | 0/1 | 0/1 | 3 |
  - #1 `router.py:4-5` `router.redirect_to_login`；terms=`login,redirect`；证据=合法
  - #2 `router.py:1-5` `router`；terms=`login,redirect`；证据=合法
  - #3 `auth.py:1-2` `auth.build_login_redirect`；terms=`login,redirect`；证据=合法
| `retry-control-flow` | holdout | succeeded | retry_control_flow | 1/1 | 0/1 | 1/1 | 3 |
  - #1 `worker.py:1-10` `worker`；terms=`worker,retry,attempts`；证据=合法
  - #2 `worker.py:5-10` `worker.retry_request`；terms=`worker,retry,attempts`；证据=合法
  - #3 `worker.py:1-2` `worker.TemporaryError`；terms=`worker,retry,attempts`；证据=合法
| `remote-rollout-config` | holdout | succeeded | external_evidence_missing | n/a/n/a | n/a/n/a | n/a/n/a | 2 |
  - #1 `rollout/region.py:1-2` `rollout.region.region_for`；terms=`region`；证据=合法
  - #2 `rollout/region.py:1-2` `rollout.region`；terms=`region`；证据=合法

## 边界与局限

本报告只衡量固定 Issue 在修复前源码索引上的确定性关键词排序和证据合法性，不衡量根因推理、修复正确性或真实模型质量。关键词基线不读取 `evals/annotations/`，不运行案例代码/测试，不访问网络或真实模型；`abstention` 在本报告中仅表示基线没有候选，不能冒充模型弃答。

每例源码由 manifest/hash 冻结，评分标注位于源码目录外；耗时、运行标识和工作树状态属于运行元数据，可能随执行环境变化。
