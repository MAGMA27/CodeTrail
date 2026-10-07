# 首轮工具失败与恢复

本文件只记录三例首轮 Run 中的真实工具错误和恢复。三个 Run 的最终 submit 均成功；没有报告形状错误，也没有修后 Run。

- `duplicate-notification-delivery`（`run-08eb2743f3f74969851a3fe607a1a74b`）：首次 `run_read_source` 请求 `delivery.py` 第 1–40 行，被工具以 `invalid_input` 拒绝，因为读取行范围超出工具上限。Solver 将范围缩至 9–16 行后成功取证，后续还读取 `ledger.py` 1–9 行并完成报告。失败请求包含在首轮记录中，未从分母删除。
- `unobservable-gateway-timeout`（`run-125dbc9c5f8845d7be564d8b5a477ea6`）：读取两处同快照证据并完成结论后，下一次 `run_search_code(query="retry")` 因 Run 的 8 次工具调用预算已用尽而被拒。Solver 保留该限制，并用已有证据提交 `insufficient_evidence`；这不改变准确的弃答结论，但无法再确认是否存在重试实现。
- 租约案例没有首轮工具错误。

验证员的本地导入最初因 API 未配置允许导入根而返回 HTTP 400（`local import path is outside allowed roots`）。随后仅将本轮结果目录设为 `CODETRAIL_ALLOWED_IMPORT_ROOTS` 并重启本轮自有 API；三例重新导入并绑定独立索引。这个 setup 失败没有改变冻结字节或 Run 评分。
