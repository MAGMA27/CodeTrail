# 子系统索引

本目录只保留本总索引，直接链接源码附近的当前文档并维护源码归属；当前实现细节由源码目录的 README/专题文档拥有，精确契约仍以声明为准。owners 为检查器读取的源码归属；本地计划与历史记录保存在被 Git 忽略的 memory-bank，共享取舍保存在决策目录。

| 子系统 | 文档 | owners |
| --- | --- | --- |
| 数据契约 | [数据契约](../../contracts/reference.md) | apps/api/app/contracts.py, apps/api/app/export_schema.py, contracts/ |
| 快照与关系 | [快照与关系](../../apps/api/indexing.md) | apps/api/app/indexing.py, apps/api/app/relations.py, apps/api/app/typescript_parser.cjs |
| 存储与查询 | [存储与查询](../../apps/api/storage.md) | apps/api/app/storage.py |
| API 与工具适配 | [API 与工具适配](../../apps/api/api.md) | apps/api/README.md, apps/api/app/main.py, apps/api/app/mcp_server.py, apps/api/app/service.py, apps/api/app/github.py, apps/api/app/overview.py, apps/api/app/journeys.py, apps/api/app/feature_generation.py, apps/api/app/feature_graph.py, apps/api/app/config.py, apps/api/app/cli.py, apps/api/app/__init__.py |
| Run 执行 | [Run 执行](../../apps/api/execution.md) | apps/api/app/run_execution.py, apps/api/app/issue_analysis.py, apps/api/app/run_sse.py |
| 工作台 | [工作台](../../apps/web/workbench.md) | apps/web/README.md, apps/web/app/, apps/web/lib/ |
| 演示样本 | [演示样本](../../fixtures/README.md) | fixtures/ |
| 独立评测 | [独立评测](../../evals/README.md) | evals/ |

旧的八个导航页已迁入 [归档目录](../archive/subsystems/README.md)，停止维护；新增子系统直接在此登记就近文档与 owners，不再创建集中导航页。
