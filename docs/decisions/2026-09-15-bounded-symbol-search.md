# 决策：有界符号搜索候选与源码读取

Status: implemented
- Date: 2026-09-15
- Scope: `IndexStore.search_symbols_bounded` 及其 HTTP/MCP/绑定 Run 调用

## 问题

目标定义排序修复后，搜索仍会无界读取一个索引的全部 `symbols` 和 FTS 命中行，并把重复的整文件源码带入内存，再逐符号切分、分词和排序。公开 `limit` 只限制返回条数，不能限制常见正文词或多定义文件造成的候选读取、源码读取和执行时间。

## 决定

1. 保持 `SearchIndexResponse`、`SearchCodeSuccess`、`search_code` 和 `run_search_code` 的契约不变；名称候选按完整限定名/限定名后缀、叶子名精确、名称部分三层有界查询，完成名称层后才进入正文层。每层只取足够判断结果上限的稳定排序行，不先混合正文候选再截断。
2. 正文层继续使用已有文件级 `symbol_fts` 作为候选索引，但按小批次读取 `symbol_id/path/bm25`，再从同一 `snapshot_files` 记录按需读取源码。每个文件在一次查询内只读取、切分和缓存一次；源码预算按文件数和 UTF-8 字节数限制，缓存不跨查询复用。
3. 每条搜索 SQL 安装 SQLite `progress_handler`，并在 Python 候选循环中检查同一固定时间预算；同时限制搜索候选行总数和正文批次。预算耗尽返回已经确认的部分结果，设置 `truncated=true` 和 `search budget exceeded: ...`，与已确认存在更多结果的 `max_results reached` 区分。未完成扫描不得报告完整无结果。
4. 预算是服务端固定内部边界，不由客户端扩大；搜索是只读操作，不修改索引、快照 hash、Symbol ID、历史 Run/report 或 evidence。没有改变 FTS 表格式，因此已有索引无需迁移或重建。

## 替代方案

- 继续全量读取后在 Python 截断：实现简单但公开 `limit` 不能约束内存、CPU 或数据库执行时间，正是本次问题。
- 为每个 Symbol 改存独立正文 FTS：能减少范围复核，但会改变派生索引内容，需要版本、重建和旧索引迁移，不是本步必要条件。
- 只用数据库 `LIMIT` 而不装 progress handler：可以限制返回行数，但不能保证排序、过滤或 FTS 扫描在时间预算内停止。
- 发现预算后直接抛错：会丢失已经找到的精确定义；本步保留部分结果和明确原因，让 Agent 能继续使用可验证的命中。

## 验收与证据

已通过原有精确名称/正文范围/快照隔离回归；新增 180 个函数的受控样本在候选上限为 64 时只读取同一文件一次，所有搜索 `symbols`/FTS 查询均带 `LIMIT`，精确末位函数仍由名称层优先返回。源码字节预算测试在超限前不读取 `source_text`；注入步进时钟和 `progress_handler` 的测试确认 SQLite 查询可中断，清理 handler 后同一连接可继续查询。预算原因与 `max_results reached` 分开验证；存储全量测试为 138 passed，Python compileall、docs:check 和 docs:check:local 通过。实际共享 API 的 `import_local` 前 5 与限定名查询均和当前 Codex MCP 的结果顺序、Symbol ID、截断原因一致；`test_mcp.py` 的 HTTP/stdio/bound external Run 对照继续通过。未改变源码 evidence、snapshot hash、Symbol ID 或历史报告，也没有契约变化，因此未重生成 Schema。

## 后果

常见词或超大索引在预算内可能只返回部分正文结果，客户端必须阅读 `truncated` 和原因；精确名称层仍优先完成。固定边界适合本地 Agent 工具调用，不等同于完整全文检索保证，也不代表真实模型质量。`memory-bank/` 继续 Git 忽略，`next.md` 由用户控制。
