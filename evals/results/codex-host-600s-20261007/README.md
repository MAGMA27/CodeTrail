# 600 秒预算下的三例陌生 Issue 首轮验收

日期：2026-10-07。基线 revision：`affd43f`。三个全新 synthetic Issue 样本在 Codex 客户端重启后通过实际宿主 MCP 完成首轮；此前 90 秒轮次的失败仍保留在前一份专项记录，不与本轮合并。

## 结果

首轮三例均进入终态，评分为 **22/22**：两个有候选的问题定位到预期符号并引用同快照证据；网关延迟案例按证据不足弃答，没有把 Protocol 接口或传递调用误报为实现。此结果验证的是三例样本的基本闭环，不代表大仓库泛化、生产缺陷定位能力或外部模型质量。

| 案例 | 首轮结果 | 证据与范围 |
| --- | --- | --- |
| `lease-expiry-boundary` | `candidate_found`，7/7 | `lease_is_active` 的 `now <= expires_at`；证据覆盖唯一影响边界：恰好到期时仍接受。 |
| `duplicate-notification-delivery` | `candidate_found`，8/8 | `dispatch_notification` 每次创建新 ledger，跨到 `ledger.py` 证明发送集合是实例内空集；不推断上游重试调度或实际重复送达次数。 |
| `unobservable-gateway-timeout` | `insufficient_evidence`，7/7 | `bridge.deliver` 仅转发到 `GatewayPort` Protocol；没有具体 adapter、timeout 或 transport 证据，因此不生成候选。 |

完整评分见 [first-round-scoring.json](first-round-scoring.json)，宿主计时及隔离信息见 [first-round.json](first-round.json)，每例 API 持久结果见 [runs/](runs/)。失败和恢复单列于 [submission-failures.md](submission-failures.md)。

## 冻结与运行

所有案例源码、Issue 与评分规则先于 Run 冻结；评分文件在三份报告都到终态之后才打开 gold 制作。答案文件位于每个导入源码根目录之外，三个项目、索引、Session 和运行时仓库 ID 各自独立。冻结字节摘要和每个索引快照摘要列于 [freeze.json](freeze.json) 与 [manifests/](manifests/)；`sources/`、`issues/`、`gold/`、`manifests/` 和 `freeze.json` 的 Git attributes 禁止文本换行归一化。

重启后的实际宿主 MCP 工具说明已核实：`analysis_kind` 默认为 `issue`，issue submit 形状及候选证据约束已更新。每次 `start_external_run` 显式传 `max_seconds=600`、省略 `analysis_kind`；三条持久 Run 记录都确认 `analysis_kind=issue` 与 `max_seconds=600`。每例由独立 fresh GPT-6 Luna / xhigh 子 Agent 调查，仅给出对应 Issue 和自己的 project/index/session ID，solver prompt 限定使用公开 Run-scoped MCP 工具。没有执行样本源码或测试，也没有请求外部模型供应商。

MCP await 累计值与服务端时间分开保存。服务端的 created-to-terminal 时间包含等待/调度；MCP 累计值包含每次调用的宿主往返，但排除了调用间思考和调度；这些数字都不代表纯推理时间。前两例没有保留连续客户端总 wall time。Run 的 `model_calls_used=0` 是 CodeTrail 托管模型调用计数，solver 由 Codex 宿主模型执行，不能把该字段解释为没有模型推理。

## PC 工作台验收

在三张 Chrome 工作台页面上分别打开唯一的 project/session URL 并确认对应快照指纹、Issue 消息、外部 Run 历史与终态报告。每页有不同 project/session/index ID：

| 案例 | 固定 snapshot 前缀 | UI 证据点击 | 影响/未知边界 |
| --- | --- | --- | --- |
| 租约 | `6370f4474649` | 点击报告证据后显示 `lease_policy.py:L1–L11`，内容 SHA 前缀 `74f16f90d1de`。 | 影响面板显示 `authorize_action` 根节点，没有静态 calls 可达节点；`action()` 为一个未知边界。 |
| 重复通知 | `929a7ef4cde1` | 点击报告证据后显示 `ledger.py:L1–L9`，内容 SHA 前缀 `4ced2d041d83`。 | 影响面板显示 `dispatch_notification` 根节点，没有静态 calls 可达节点；`sender.send(notification_id)` 为未知边界。 |
| 网关延迟 | `5230fc31c1ea` | 点击报告证据后显示 `ports.py:L1–L7`，内容 SHA 前缀 `0b9a67b95dfb`，明确呈现 Protocol 方法桩。 | 结论为证据不足，没有候选影响图；报告把具体 transport、timeout 和 logging 路径列为未知。 |

对三页都执行了浏览器刷新。带 project/session 查询参数的页面先异步恢复项目、快照、消息和 Run 历史；重新选择已完成 Run 后，工作台恢复对应的完整终态事件和报告。首个短暂加载状态并未当作失败；复核以已恢复的 UI 内容为准。完整 Run 事件数分别为 11、19、19，工具结果数为 4、8、8。三页互不串用项目和 Run，源码面板也显示各自 project/index ID。

## 真实失败与限制

重复通知案例一次过宽的 `run_read_source` 行范围返回 `invalid_input`，缩小到有效范围后成功。网关案例在 8 次 Run 工具调用预算已用完后，未能执行最后一次 `retry` 搜索；报告保留此限制，已有两处证据仍足以正确弃答。完整记录见 [submission-failures.md](submission-failures.md)。

这三个评测样本由本次验收构造并标注 synthetic；全新 solver 与过程未复用先前 Issue 答案，但它们不是第三方真实仓库样本。MCP-only 是 solver 过程约束，不是操作系统级文件权限隔离。报告没有证明跨项目数据泄漏之外的所有安全性质，也没有证明静态调用覆盖完备。没有发现需要最小代码修复的阻断，因此没有补验 Run 或产品代码变更。
