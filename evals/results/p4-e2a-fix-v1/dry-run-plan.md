# P4-E2a 离线 Run 评测执行清单

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`；划分：`all`
- 方法：`real-run-offline-fake-v1`；provider：`offline-fake`；模型：`codetrail-eval-fake-v1`
- prompt：`issue-analysis-v1`；execution：`p4-e2a-real-run-v1`
- 输入构造：`p4-e2a-input-v2`；整次预算：cases=100，model calls=24，input/model input chars=600000/800000，output/model output chars=300000/120000，seconds=180
- 结果目录：`evals\results\p4-e2a-fix-v1`；恢复状态：`evals\results\p4-e2a-fix-v1\raw-state.json`
- 网络调用：`0`；最大模型调用：`24`
- 模拟说明：本报告使用本地确定性假供应商，目的是验证真实 Run/工具/预算/持久化/评分适配；它不代表任何真实模型的语义质量、根因判断或供应商表现。

## 案例输入

| 案例 | 划分 | Issue | 源码文件 | snapshot hash | Run 输入 |
| --- | --- | --- | --- | --- | --- |
| `csv-import-validation` | calibration | `eval-issue-csv-import-validation` / `3e7ba9cc05f0` | 4 个 manifest 文件 | `cd81783813b2` | Issue JSON, complete saved Session message prefix |
| `cache-key-tenant` | calibration | `eval-issue-cache-key-tenant` / `8eeae5a2c1f8` | 3 个 manifest 文件 | `4aa1b67e0201` | Issue JSON, complete saved Session message prefix |
| `partner-billing-outage` | calibration | `eval-issue-partner-billing-outage` / `8196263f39d5` | 3 个 manifest 文件 | `b03e176f68ba` | Issue JSON, complete saved Session message prefix |
| `redirect-query-encoding` | holdout | `eval-issue-redirect-query-encoding` / `b47173b1718b` | 3 个 manifest 文件 | `83b6daed08a4` | Issue JSON, complete saved Session message prefix |
| `retry-control-flow` | holdout | `eval-issue-retry-control-flow` / `190b4ee73d82` | 2 个 manifest 文件 | `2033251b411e` | Issue JSON, complete saved Session message prefix |
| `remote-rollout-config` | holdout | `eval-issue-remote-rollout-config` / `aded6fe98755` | 3 个 manifest 文件 | `ddddb739e050` | Issue JSON, complete saved Session message prefix |

## 发送范围

每个 Run 只发送对应案例的 Issue JSON、完整保存 Session 消息前缀和工具返回的同快照有限上下文；工具根目录只包含该案例源码副本。标签、修复参考、其他案例和基线候选不进入执行路径。
