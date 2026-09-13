# 子系统索引

本目录维护当前行为；计划与历史分别留在本地计划和决策记录。owners 为检查器读取的源码归属，新增源码需更新归属或补充子系统。Run 已有独立执行页面；独立评测已建立离线关键词基线，真实模型对比仍属后续阶段。

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
