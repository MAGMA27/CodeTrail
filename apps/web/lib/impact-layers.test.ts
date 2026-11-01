import assert from "node:assert/strict";
import test from "node:test";

import type { RunImpactResponse } from "./api.ts";
import { ApiRequestError, RequestTimeoutError } from "./api.ts";
import {
  ImpactScopeMismatchError,
  describeImpactPanel,
  impactBudgetReasonLabel,
  impactErrorView,
  impactIndexTruncationMessage,
  impactPanelStateAttribute,
  impactScopeMismatch,
  impactUnresolvedTruncationMessage,
} from "./impact-layers.ts";

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
  // The truncation notice sits next to the counts instead of replacing them.
  assert.equal(panel.headline, "0 个可达节点 · 0 条路径 · 已截断");
  assert.equal(impactPanelStateAttribute(panel), "truncated");
  assert.match(panel.truncationMessage ?? "", /达到节点数上限（max_nodes reached）/);
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


test("truncated impact keeps reachable and path counts in the headline", () => {
  const base = impact();
  const panel = describeImpactPanel({
    status: "success",
    canRetry: false,
    impact: impact({
      truncated: true,
      truncation_reason: "max_paths reached",
      reachable_node_count: 11,
      paths: Array.from({ length: 3 }, (_, index) => ({
        path_id: `path-${index}`,
        symbol_ids: [base.root_symbol_id ?? "symbol-root"],
        edges: [],
        depth: 1,
      })) as unknown as RunImpactResponse["paths"],
    }),
  });
  assert.equal(panel.headline, "11 个可达节点 · 3 条路径 · 已截断");
  assert.match(panel.truncationMessage ?? "", /达到路径数上限（max_paths reached）/);
});

test("budget reasons are labelled in Chinese and keep the raw reason", () => {
  assert.equal(impactBudgetReasonLabel("max_unresolved reached"), "达到未知边界（unresolved）条数上限（max_unresolved reached）");
  assert.equal(impactBudgetReasonLabel("max_seconds exceeded"), "达到时间预算（max_seconds exceeded）");
  assert.equal(
    impactBudgetReasonLabel("max_edges reached while collecting contextual calls"),
    "达到关系边上限（上下文调用阶段）（max_edges reached while collecting contextual calls）",
  );
  assert.equal(impactBudgetReasonLabel("something new"), "其他限制（something new）");
});

test("index truncation wording names the relation budget or the time budget", () => {
  const budget = impactIndexTruncationMessage("max_edges reached while collecting contextual calls");
  assert.match(budget, /固定索引关系分析达到关系预算/);
  assert.doesNotMatch(budget, /扫描未完成/);
  assert.match(impactIndexTruncationMessage("max_seconds exceeded"), /固定索引关系分析达到时间预算/);
  assert.match(
    impactIndexTruncationMessage("max_seconds exceeded; max_edges reached"),
    /同时达到关系分析的时间预算和关系预算/,
  );
  assert.match(impactIndexTruncationMessage(null), /固定索引覆盖不完整：未提供原因/);
  assert.match(impactUnresolvedTruncationMessage("max_unresolved reached"), /unresolved 详情已截断：达到未知边界/);
});

test("impact errors become Chinese messages with the raw code as detail", () => {
  const timeout = impactErrorView(new RequestTimeoutError(), 20_000);
  assert.match(timeout.message, /影响查询超时（20 秒内没有返回）/);
  const network = impactErrorView(new TypeError("Failed to fetch"));
  assert.match(network.message, /无法连接 API/);
  assert.equal(network.detail, "Failed to fetch");
  const notFound = impactErrorView(new ApiRequestError("Run, report, candidate, or finding was not found", 404, "not_found"));
  assert.match(notFound.message, /找不到这个 Run、报告、候选或发现/);
  assert.equal(notFound.detail, "not_found · HTTP 404 · Run, report, candidate, or finding was not found");
  assert.match(impactErrorView(new ApiRequestError("request parameters are invalid", 422, "invalid_input")).message, /参数无效/);
  assert.match(impactErrorView(new ApiRequestError("local Run impact storage failed", 500, "internal_error")).message, /本地存储失败/);
  assert.match(impactErrorView(new ApiRequestError("请求失败（HTTP 502）", 502, "internal_error")).message, /服务端暂时不可用（HTTP 502）/);
  const mismatch = impactErrorView(new ImpactScopeMismatchError("不一致字段：run_id"));
  assert.match(mismatch.message, /不属于当前选择的 Run/);
  assert.equal(mismatch.detail, "不一致字段：run_id");
});

test("impact scope mismatch lists every differing identifier", () => {
  const response = impact();
  const scope = { projectId: "project-impact", sessionId: "session-impact", runId: "run-impact", indexId: "index-impact" };
  assert.equal(impactScopeMismatch(response, scope), null);
  assert.equal(impactScopeMismatch(response, { ...scope, runId: "run-other", indexId: "index-other" }), "不一致字段：run_id, index_id");
  assert.equal(impactScopeMismatch(response, { projectId: "project-impact", sessionId: "session-impact", runId: "run-impact" }), null);
});
