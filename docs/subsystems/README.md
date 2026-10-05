# 子系统索引

本目录只维护职责摘要、跨目录依赖和源码归属导航；当前实现细节由源码目录的 README/专题文档拥有，精确契约仍以声明为准。owners 为检查器读取的源码归属；本地计划与历史记录保存在被 Git 忽略的 memory-bank，共享取舍保存在决策目录。

| 子系统 | 文档 | owners |
| --- | --- | --- |
| 数据契约 | [数据契约](contracts.md) | apps/api/app/contracts.py, apps/api/app/export_schema.py, contracts/ |
| 快照与关系 | [快照与关系](indexing.md) | apps/api/app/indexing.py, apps/api/app/relations.py |
| 存储与查询 | [存储与查询](storage.md) | apps/api/app/storage.py |
| API 与工具适配 | [API 与工具适配](api.md) | apps/api/README.md, apps/api/app/main.py, apps/api/app/mcp_server.py, apps/api/app/service.py, apps/api/app/github.py, apps/api/app/overview.py, apps/api/app/feature_generation.py, apps/api/app/feature_graph.py, apps/api/app/config.py, apps/api/app/__init__.py |
| Run 执行 | [Run 执行](execution.md) | apps/api/app/run_execution.py, apps/api/app/issue_analysis.py, apps/api/app/run_sse.py |
| 工作台 | [工作台](workbench.md) | apps/web/README.md, apps/web/app/, apps/web/lib/ |
| 演示样本 | [演示样本](fixtures.md) | fixtures/ |
| 独立评测 | [独立评测](evals.md) | evals/ |
