# 存储与查询

## 状态

已实现 SQLite/FTS5 快照存储、Project 身份与索引归属、搜索、证据读取与受限邻接。

## 职责与范围

拥有 Project、index_id 归属、不可变快照及查询语义；不负责模型推理与 HTTP 错误呈现。

## 接口与依赖

[IndexStore](../../apps/api/app/storage.py) 供服务层调用；底层依赖 SQLite/FTS5，数据库位于每个服务端配置仓库的 `.codetrail/index.sqlite3`。

## 数据与生命周期

Project、project_indexes、索引快照、符号和证据均由同一仓库数据库持久化；数据按 index_id 隔离，项目关联校验 repository_id。旧数据库中的未归属索引只在确有未归属记录时迁移到明确的 legacy project；证据读取保存文本，写入事务维持完整性，连接由调用方管理生命周期。

## 错误与限制

邻接只遍历静态边；查询受数量和时间预算约束，项目索引构建完成后才提升 latest_index_id，失败只清理 building 标记，不覆盖既有可用索引；冲突及失败不能留下半套索引数据。

## 验证

apps/api/tests/test_storage.py 验证重开数据库、事务、旧快照和查询预算；项目关联、旧索引迁移和重启恢复由 `apps/api/tests/test_api.py` 覆盖。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
