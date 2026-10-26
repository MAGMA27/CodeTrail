import assert from "node:assert/strict";
import test from "node:test";

import type { RunImpactResponse } from "./api.ts";
import { describeImpactPanel, impactPanelStateAttribute } from "./impact-layers.ts";

function impact(overrides: Partial<RunImpactResponse> = {}): RunImpactResponse {
  const root = {
    symbol_id: "symbol-root",
    index_id: "index-impact",
    path: "pkg/app.py",
    qualified_name: "pkg.app.root",
    kind: "function" as const,
    start_line: 1,
    end_line: 2,
  };
  return {
    schema_version: "0.1.0",
    project_id: "project-impact",
    session_id: "session-impact",
    run_id: "run-impact",
    index_id: "index-impact",
    candidate_id: "candidate-impact",
    finding_id: null,
    mapping_status: "mapped",
    direction: "callers",
    max_depth: 2,
    root_symbol_id: root.symbol_id,
    root,
    nodes: [root],
    edges: [],
    paths: [],
    unresolved: [],
    layers: [
      {
        kind: "report_anchor",
        determination: "saved_report_symbol",
        symbol_ids: [root.symbol_id],
        unresolved_ids: [],
        summary: "报告锚点不是已确认影响。",
      },
      {
        kind: "static_direct",
        determination: "static_calls",
        symbol_ids: [],
        unresolved_ids: [],
        summary: "调用可达不等于已确认影响。",
      },
      {
        kind: "static_indirect",
        determination: "static_calls",
        symbol_ids: [],
        unresolved_ids: [],
        summary: "调用可达不等于已确认影响。",
      },
      {
        kind: "unknown",
        determination: "unresolved_reference",
        symbol_ids: [],
        unresolved_ids: [],
        summary: "它们保持未知。",
      },
    ],
    reachable_node_count: 0,
    returned_node_count: 1,
    scope_note: "静态可达不等同于已确认影响。",
    truncated: false,
    truncation_reason: null,
    index_truncated: false,
    index_truncation_reason: null,
    index_unresolved_truncated: false,
    index_unresolved_truncation_reason: null,
    ...overrides,
  };
}

test("unmapped impact stays an empty mapping state", () => {
  const panel = describeImpactPanel({
    status: "empty",
    canRetry: false,
    impact: impact({
      mapping_status: "unmapped",
      candidate_id: null,
      finding_id: "finding-impact",
      root_symbol_id: null,
      root: null,
      nodes: [],
      reachable_node_count: 0,
      returned_node_count: 0,
      layers: impact().layers.map((layer) => ({ ...layer, symbol_ids: [], unresolved_ids: [] })),
    }),
  });

  assert.equal(panel.mode, "unmapped");
  assert.equal(panel.showUnmapped, true);
  assert.equal(panel.showGraph, false);
  assert.equal(panel.showRetry, false);
  assert.equal(panel.emptyStatic, false);
  assert.equal(impactPanelStateAttribute(panel), "unmapped");
  assert.equal(panel.headline, "没有可映射的唯一符号");
});

test("impact request failure offers retry and does not claim empty static calls", () => {
  const retryable = describeImpactPanel({ status: "error", impact: null, canRetry: true });
  assert.equal(retryable.mode, "error");
  assert.equal(retryable.showRetry, true);
  assert.equal(retryable.showGraph, false);
  assert.equal(retryable.emptyStatic, false);
  assert.equal(retryable.headline, "影响范围请求失败");
  assert.equal(impactPanelStateAttribute(retryable), "error");
  assert.equal(retryable.truncationMessage, null);

  const blocked = describeImpactPanel({ status: "error", impact: null, canRetry: false });
  assert.equal(blocked.showRetry, false);
});

test("truncated impact keeps returned layers and says the result is incomplete", () => {
  const panel = describeImpactPanel({
    status: "empty",
    canRetry: false,
    impact: impact({ truncated: true, truncation_reason: "max_nodes reached" }),
  });

  assert.equal(panel.mode, "result");
  assert.equal(panel.showGraph, true);
  assert.equal(panel.showTruncation, true);
  assert.equal(panel.emptyStatic, false);
  assert.equal(panel.headline, "影响分层已截断");
  assert.equal(impactPanelStateAttribute(panel), "truncated");
  assert.match(panel.truncationMessage ?? "", /max_nodes reached/);
  assert.match(panel.truncationMessage ?? "", /不是已确认影响/);
  assert.doesNotMatch(panel.headline, /没有静态 calls/);
});

test("a complete empty caller direction can say there is no static calls reachability", () => {
  const panel = describeImpactPanel({ status: "empty", canRetry: false, impact: impact() });
  assert.equal(panel.emptyStatic, true);
  assert.equal(panel.showTruncation, false);
  assert.equal(panel.headline, "当前方向没有静态 calls 可达节点");
  assert.equal(impactPanelStateAttribute(panel), "result");
});

test("loading and idle impact panels do not render a graph or retry", () => {
  const loading = describeImpactPanel({ status: "loading", impact: null, canRetry: true });
  assert.equal(loading.mode, "loading");
  assert.equal(loading.showGraph, false);
  assert.equal(loading.showRetry, false);
  const idle = describeImpactPanel({ status: "idle", impact: null, canRetry: false });
  assert.equal(idle.mode, "idle");
  assert.equal(impactPanelStateAttribute(idle), "idle");
});
