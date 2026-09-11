# 存储与查询

## 状态

已实现 SQLite/FTS5 快照存储、Project 身份与索引归属、动态本地/GitHub 来源注册、ImportAttempt、搜索、证据读取、受限邻接和 O2 功能生成结果。

## 职责与范围

拥有 Project、index_id 归属、不可变快照、来源元数据、查询语义和 `feature_generations` 结果缓存；不负责模型推理与 HTTP 错误呈现。

## 接口与依赖

[IndexStore](../../apps/api/app/storage.py) 和 `ManagementStore` 供服务层调用；底层依赖 SQLite/FTS5。静态仓库继续使用来源目录的 `.codetrail/index.sqlite3`，动态本地和 GitHub 来源使用服务管理目录下的 `registry.sqlite3`、项目存储和索引库；GitHub 的 canonical URL、requested ref、resolved commit、content hash 与 cleanup 状态和 attempt 一起持久化。

## 数据与生命周期

静态 Project、project_indexes、索引快照、符号和证据继续由原仓库数据库持久化；动态来源的同类索引数据写入服务管理目录，注册来源和 ImportAttempt 由管理 SQLite 持久化。O2 的 `feature_generations` 保存生成契约 JSON、project/index 作用域、模式、模型、提示词/规则版本、缓存键、幂等键、状态和创建时间；结果内部仍绑定不可变 index_id，读取不调用模型。成功、部分和证据不足结果可按缓存键复用，失败记录单独保留且不覆盖已有成功结果，同一幂等键不能跨请求语义复用。数据按 index_id 隔离，项目关联校验 repository_id。旧数据库中的未归属索引只在确有未归属记录时迁移到明确的 legacy project；证据读取保存文本，写入事务维持完整性。来源目录只读，管理库连接按操作独立创建并释放。

## 错误与限制

导入来源拒绝路径越界和符号链接/junction 逃逸，管理目录与来源目录不重叠；GitHub 临时目录使用 attempt owner marker，只允许官方归档安全解包，限制 archive/文件数/解包大小/重定向/请求时间，清理失败可单独重试；邻接只遍历静态边，查询受数量和时间预算约束。项目索引构建完成后才提升 latest_index_id，失败只清理 building 标记，不覆盖既有可用索引；ImportAttempt 失败或重启中断可重试，冲突及失败不能留下半套可用索引数据。功能生成写入前由服务层校验 project/index、功能/关联/evidence 同快照，数据库唯一索引约束幂等键；缓存键包含预算，避免窄预算请求复用超预算结果。

## 验证

apps/api/tests/test_storage.py 验证重开数据库、事务、旧快照和查询预算；功能生成结果的保存/读取、索引绑定、缓存和幂等由 `apps/api/tests/test_features.py` 覆盖；动态来源、导入状态、源目录不变、幂等/并发、边界、失败重试和重启恢复由 `apps/api/tests/test_api.py` 覆盖；GitHub archive、固定 commit、幂等、分支移动和清理生命周期由 `apps/api/tests/test_github.py` 覆盖。

## 相关决策

当前依据为现有实现和根目录开发约束；后续重要取舍记录到 [决策目录](../decisions/README.md)。
