# 决策：GitHub 更新的跨进程提交一致性

Status: implemented

## 问题

GitHub 更新同时改变不可变 index 库中的项目 latest，以及管理库中的来源 identity 和 import attempt。两个库不能共享一个 SQLite 事务；仅依赖进程内锁或先写其中一边再在另一边检查，会让独立 HTTP/MCP 服务互相覆盖，或在 CAS 冲突后污染旧来源 metadata。下载、构建、提交和进程中断还必须留下可解释且可恢复的状态。

## 决定

1. ManagementStore 增加 `project_updates` 持久化提交意图，并以 `project_id` 唯一约束作为跨进程准入保护。记录 expected latest、owner PID、attempt、候选 immutable index、目标 GitHub source metadata 和 `building`/`latest_committed` 阶段；服务启动只恢复 owner 已死亡的记录，同一活动进程的记录不会被另一个服务实例误标记为中断。
2. 所有项目索引创建、本地显式更新、GitHub 新 commit/历史复用/重试都沿用原始 expected latest：先在当前 project 上复核，再将 expected 传入下载后的构建、历史复用的 `finish_project_index` 和最终 CAS。候选 index 可以先成为不可变快照，但只有带 expected 检查的 `finish_project_index` 才能推进 latest；来源 identity 不在 CAS 之前改写。
3. latest CAS 成功后，ManagementStore 在一个 `BEGIN IMMEDIATE` 事务中同时写入 GitHub source identity、attempt ready/index/hash 并删除提交意图。两库之间短暂的 latest/source 可见窗口由持久化意图解释；若 registry 提交失败，owner 被释放并保留意图，重启时若候选仍是当前 ready latest 则补提交，否则只将本次 attempt 失败并终止意图，绝不回滚或覆盖其他请求已提交的 latest/source。清理状态继续独立记录，清理失败可重试且不改变成功提交事实。
4. 下载失败、构建失败、CAS 冲突、作用域错误和存储故障均通过现有 ServiceError/attempt 状态呈现；恢复流程只依据已持久化阶段决定完成或终止，不通过随机压力循环或盲目回滚推断状态。项目级持久化占用冲突返回可解释的 conflict，待持有者结束或服务重启恢复后再重试。

## 替代方案

- 只扩大 `_index_mutation_lock`：只能保护同一进程，不能约束独立 HTTP 与 stdio MCP 服务，也不能在进程崩溃后判断两库已经完成哪一阶段。
- 只调换 latest/source 两行写入顺序：另一边仍可能在中间故障或 CAS 冲突，且无法保留恢复所需的候选和目标 metadata。
- 引入通用队列或把两套数据库合并：超出当前单项目、单 Agent、同步受限更新的范围，增加不必要的运行时基础设施和迁移风险。

## 验收与证据

- `apps/api/tests/test_github.py` 受控交错测试验证独立服务实例中旧请求不能覆盖新 latest/source，历史复用 CAS 冲突不改写来源 identity；registry 提交故障测试验证候选 latest、持久化意图、owner 释放和重启补提交。
- GitHub、freshness、storage、MCP 定向回归为 `57 passed`；后端全量 `uv run pytest -q` 为 `180 passed`，Python `compileall`、`pnpm docs:check`、`pnpm docs:check:local` 和 `git diff --check` 通过。
- 既有同 commit 复用、分支变化、规则升级、旧 Session/evidence 保留、cleanup 重试、HTTP/MCP 归属和错误契约继续由原有回归覆盖。真实 GitHub 公网与真实供应商语义质量仍未验证。

## 后果

每个项目同一时刻最多一个索引更新意图，跨进程竞争从“可能覆盖”变为 conflict；进程异常后的首次新服务启动会执行有界恢复。由于 latest 和 source 位于不同数据库，恢复前可能短暂看到“latest 已变、source identity 尚未更新”，但 pending intent 可解释该状态，重启会完成或终止它；不存在把新 commit 配到旧 latest 的成功提交路径。未完成恢复的意图会暂时阻止同项目的新更新，避免在状态不明时继续叠加写入。
