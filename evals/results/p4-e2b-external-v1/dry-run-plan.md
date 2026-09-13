# P4-E2b 外部供应商评测执行清单

- 数据集：`codetrail-p4-e1` / `p4-e1-v1`；划分：`calibration`
- 方法：`real-run-external-deepseek-v1`；provider：`deepseek`；模型：`deepseek-v4-flash`
- 配置状态：`missing`；配置指纹：`fda3a9c567af846745f0cc64ffd00e4078752359b86a556faf39fd9d4ba16cac`
- prompt：`issue-analysis-v1`；execution：`p4-e2a-real-run-v1`
- 输入构造：`p4-e2a-input-v2`；整次预算：cases=100，model calls=24，input/model input chars=600000/800000，output/model output chars=300000/120000，seconds=180；费用上限=0.250000 USD；价格依据=https://api-docs.deepseek.com/quick_start/pricing（检查日 2026-09-13）
- 结果目录：`evals\results\p4-e2b-external-v1`；恢复状态：`evals\results\p4-e2b-external-v1\raw-state.json`
- 网络调用：`12`；最大模型调用：`12`
- 执行说明：这是受显式授权、配置指纹、发送范围和累计费用上限约束的真实供应商评测入口；本地验证使用 HTTP 假供应商，当前仓库交付不代表已经访问外网或产生真实费用。

## 案例输入

| 案例 | 划分 | Issue | 源码文件 | snapshot hash | Run 输入 |
| --- | --- | --- | --- | --- | --- |
| `csv-import-validation` | calibration | `eval-issue-csv-import-validation` / `3e7ba9cc05f0` | 4 个 manifest 文件 | `cd81783813b2` | Issue JSON, complete saved Session message prefix |
| `cache-key-tenant` | calibration | `eval-issue-cache-key-tenant` / `8eeae5a2c1f8` | 3 个 manifest 文件 | `4aa1b67e0201` | Issue JSON, complete saved Session message prefix |
| `partner-billing-outage` | calibration | `eval-issue-partner-billing-outage` / `8196263f39d5` | 3 个 manifest 文件 | `b03e176f68ba` | Issue JSON, complete saved Session message prefix |

## 发送范围

每个 Run 只发送对应案例的 Issue JSON、完整保存 Session 消息前缀和工具返回的同快照有限上下文；工具根目录只包含该案例源码副本。标签、修复参考、其他案例和基线候选不进入执行路径。
