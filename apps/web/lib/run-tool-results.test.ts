import assert from "node:assert/strict";
import test from "node:test";
import { SCHEMA_VERSION, isRunToolResultsResponse, type GetNeighborsSummaryRunToolResult } from "./api.ts";
import { runNeighborSummaryNotice } from "./run-tool-results.ts";

function summary(detailState: "empty" | "omitted" | "unknown", count: number | null): GetNeighborsSummaryRunToolResult {
  return {
    request_id: "neighbor-summary", run_id: "run-1", index_id: "index-1",
    completed_sequence: 2, kind: "get_neighbors.summary_succeeded", tool: "get_neighbors",
    root: { symbol_id: "symbol-1", index_id: "index-1", path: "src/a.py", qualified_name: "a", kind: "function", start_line: 1, end_line: 2 },
    nodes: [{ symbol_id: "symbol-1", index_id: "index-1", path: "src/a.py", qualified_name: "a", kind: "function", start_line: 1, end_line: 2 }],
    neighbors: [], contextual_calls: [], possible_unresolved_callers: [],
    completeness: {
      status: "possibly_incomplete", reasons: ["index_truncated"],
      possible_unresolved_caller_count: null, unresolved_outgoing_count: null, hint: "Coverage is limited.",
    },
    unresolved: null,
    unresolved_summary: {
      detail_state: detailState, total_indexed_in_scope: count,
      items_in_legacy_full_result: count === null ? null : Math.min(count, 40),
      neighbor_request_id: "neighbor-summary", node_ids: ["symbol-1"], detail_hint: "Use run_list_unresolved.",
    },
    truncated: false, truncation_reason: null, index_truncated: false,
    index_truncation_reason: null, index_unresolved_truncated: null,
    index_unresolved_truncation_reason: null,
  };
}

test("summary UI distinguishes empty, omitted, and unknown counts and preserves coverage uncertainty", () => {
  assert.match(runNeighborSummaryNotice(summary("empty", 0)) ?? "", /当前范围没有匹配记录/);
  assert.match(runNeighborSummaryNotice(summary("omitted", 54)) ?? "", /索引记录 54 条/);
  assert.match(runNeighborSummaryNotice(summary("omitted", 54)) ?? "", /index_truncated/);
  assert.match(runNeighborSummaryNotice(summary("unknown", null)) ?? "", /数量未知/);
  assert.match(runNeighborSummaryNotice(summary("unknown", null)) ?? "", /旧索引未记录/);
});

test("saved summary receipt is accepted only with its explicit summary discriminator and fields", () => {
  const result = summary("omitted", 3);
  const response = {
    schema_version: SCHEMA_VERSION, project_id: "project-1", session_id: "session-1", run_id: "run-1",
    results: [result], next_after_sequence: null, has_more: false,
  };
  assert.equal(isRunToolResultsResponse(response), true);
  assert.equal(isRunToolResultsResponse({ ...response, results: [{ ...result, unresolved: [] }] }), false);
});
