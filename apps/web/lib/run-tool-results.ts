import type { RunToolResult } from "./api";

/** Explain omitted Run neighbor details without treating omission as completeness. */
export function runNeighborSummaryNotice(result: RunToolResult): string | null {
  if (result.kind !== "get_neighbors.summary_succeeded") return null;
  const summary = result.unresolved_summary;
  const count = summary.total_indexed_in_scope === null
    ? "索引范围内数量未知"
    : `返回节点范围内索引记录 ${summary.total_indexed_in_scope} 条`;
  const detail = summary.detail_state === "empty"
    ? "当前范围没有匹配记录"
    : summary.detail_state === "omitted"
      ? "详情已省略，可通过 run_list_unresolved 按页读取"
      : "详情数量未知；不能由省略详情推断不存在";
  const coverage: string[] = [];
  if (result.completeness?.reasons.length) {
    coverage.push(`邻接 completeness 原因：${result.completeness.reasons.join(", ")}`);
  }
  if (result.truncated && result.truncation_reason) coverage.push(`邻居结果截断：${result.truncation_reason}`);
  if (result.index_truncated) coverage.push(`关系索引截断：${result.index_truncation_reason ?? "原因未知"}`);
  if (result.index_unresolved_truncated === true) {
    coverage.push(`索引 unresolved 覆盖截断：${result.index_unresolved_truncation_reason ?? "原因未知"}`);
  } else if (result.index_unresolved_truncated === null) {
    coverage.push("旧索引未记录 unresolved 覆盖状态");
  }
  return [detail, count, ...coverage].join(" · ");
}
