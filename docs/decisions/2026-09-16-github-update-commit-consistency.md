# 决策：GitHub 更新的跨进程提交一致性

Status: implemented

## 问题

GitHub 更新同时改变不可变 index 库中的项目 latest，以及管理库中的来源 identity 和 import attempt。两个库不能共享一个 SQLite 事务；仅依赖进程内锁或先写其中一边再在另一边检查，会让独立 HTTP/MCP 服务互相覆盖，或在 CAS 冲突后污染旧来源 metadata。下载、构建、提交和进程中断还必须留下可解释且可恢复的状态。

## 决定

1. ManagementStore 增加 `project_updates` 持久化提交意图，并以 `project_id` 唯一约束作为跨进程准入保护。记录 expected latest、owner PID、attempt、候选 immutable index、目标 GitHub source metadata 和 `building`/`latest_committed` 阶段；服务启动只恢复 owner 明确已退出的记录。owner 探测使用 `alive`/`exited`/`unknown` 三态：Windows 通过只读进程句柄等待状态查询，POSIX 才使用平台限定的无信号探测；权限不足或未知错误保持 `unknown`，不会误恢复活动进程。
2. 所有项目索引创建、本地显式更新、GitHub 新 commit/历史复用/重试都沿用原始 expected latest：先在当前 project 上复核，再将 expected 传入下载后的构建、历史复用的 `finish_project_index` 和最终 CAS。候选 index 可以先成为不可变快照，但只有带 expected 检查的 `finish_project_index` 才能推进 latest；来源 identity 不在 CAS 之前改写。
3. latest CAS 成功后，ManagementStore 在一个 `BEGIN IMMEDIATE` 事务中同时写入 GitHub source identity、attempt ready/index/hash 并删除提交意图。两库之间短暂的 latest/source 可见窗口由持久化意图解释；若 registry 提交失败，owner 被释放并保留意图，重启时若候选仍是当前 ready latest 则补提交，否则只将本次 attempt 失败并终止意图，绝不回滚或覆盖其他请求已提交的 latest/source。清理状态继续独立记录，清理失败可重试且不改变成功提交事实。
4. 下载失败、构建失败、CAS 冲突、作用域错误和存储故障均通过现有 ServiceError/attempt 状态呈现；恢复流程只依据已持久化阶段决定完成或终止，不通过随机压力循环或盲目回滚推断状态。项目级持久化占用冲突返回可解释的 conflict，待持有者结束或服务重启恢复后再重试。

## 替代方案

- 只扩大 `_index_mutation_lock`：只能保护同一进程，不能约束独立 HTTP 与 stdio MCP 服务，也不能在进程崩溃后判断两库已经完成哪一阶段。
- 只调换 latest/source 两行写入顺序：另一边仍可能在中间故障或 CAS 冲突，且无法保留恢复所需的候选和目标 metadata。
- 引入通用队列或把两套数据库合并：超出当前单项目、单 Agent、同步受限更新的范围，增加不必要的运行时基础设施和迁移风险。

## 验收与证据

- `apps/api/tests/test_github.py` 受控交错测试验证独立服务实例中旧请求不能覆盖新 latest/source，历史复用 CAS 冲突不改写来源 identity；registry 提交故障测试验证候选 latest、持久化意图、owner 释放和重启补提交。
- `apps/api/tests/test_process_liveness.py` 使用伪造 Win32 API 验证存活/退出/权限失败、句柄关闭、无信号分派和 unknown 恢复保护；`apps/api/tests/test_github_process_lifecycle_script.py` 另验证 release 超时、阶段报告、默认单场景和 worker 回收/日志保留。本轮受影响的 process_liveness/GitHub/storage/脚本定向回归为 41 passed。`scripts/verify-github-process-lifecycle.py` 的 worker 参数在边界统一转换为 `Path`，提供单调 release deadline、每场景总超时、阶段日志、无控制台 HTTP worker、受控失败日志和显式公网开关。
- 既有 GitHub、freshness、storage、MCP 定向回归为 `57 passed`，既有后端全量为 `180 passed`；本轮新增脚本回归与受影响存储/生命周期定向回归为 `41 passed`，并重新通过 Python 编译、文档检查和 diff 检查。
- 本轮显式运行 `--scenario all`，四个真实 HTTP/MCP 进程场景均通过：双向活动更新冲突、latest CAS 前退出恢复、latest CAS 后管理库提交前退出恢复及第二次重启幂等；每个场景最终无 pending `project_updates`、无 `building_index_id`，并保留与预期一致的 latest/source/attempt。默认不运行 `all`，公网仍需显式 `--public-github`。浏览器联合流程、GitHub 公网和真实供应商语义质量仍未验证。
- 脚本中的 HTTP 请求在 `asyncio.to_thread` 中执行；取消等待不会停止已运行线程，故 `_http_json` 保持独立 5 秒 socket 超时，worker 进程则由受控 cleanup 单独回收。浏览器联合流程、GitHub 公网和真实供应商语义质量仍未验证。
- 既有同 commit 复用、分支变化、规则升级、旧 Session/evidence 保留、cleanup 重试、HTTP/MCP 归属和错误契约继续由原有回归覆盖；本轮不引用历史全量 180 项作为本轮证据。

## 后果

每个项目同一时刻最多一个索引更新意图，跨进程竞争从“可能覆盖”变为 conflict；进程异常后的首次新服务启动会执行有界恢复。由于 latest 和 source 位于不同数据库，恢复前可能短暂看到“latest 已变、source identity 尚未更新”，但 pending intent 可解释该状态，重启会完成或终止它；不存在把新 commit 配到旧 latest 的成功提交路径。Windows 或 POSIX owner 探测无法确认时保留意图并保护对应 active attempt，避免把未知状态误标为中断；未完成恢复的意图会暂时阻止同项目的新更新，避免在状态不明时继续叠加写入。
