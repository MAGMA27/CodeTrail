import assert from "node:assert/strict";
import test from "node:test";

import type { CodeEdge, NeighborsResponse, SymbolRecord } from "./api.ts";
import {
  emptyGraphState,
  groupSymbolsByDirectory,
  isLatestRequest,
  mergeNeighborGraph,
  unresolvedForSymbol,
} from "./graph.ts";

const indexId = "index-timeout-demo-test";

function symbol(symbolId: string, path: string, qualifiedName: string, startLine: number): SymbolRecord {
  return {
    symbol_id: symbolId,
    index_id: indexId,
    path,
    qualified_name: qualifiedName,
    kind: "function",
    start_line: startLine,
    end_line: startLine + 2,
  };
}

function edge(edgeId: string, sourceId: string, targetId: string): CodeEdge {
  return {
    edge_id: edgeId,
    index_id: indexId,
    source_id: sourceId,
    target_id: targetId,
    kind: "calls",
    extraction_method: "ast",
    source_location: {
      path: "timeout_demo/app.py",
      start_line: 12,
      end_line: 12,
    },
  };
}

const root = symbol("symbol-root", "timeout_demo/app.py", "timeout_demo.app.start", 10);
const child = symbol("symbol-child", "timeout_demo/config.py", "timeout_demo.config.load_settings", 12);

const response: NeighborsResponse = {
  schema_version: "0.1.0",
  index_id: indexId,
  root,
  nodes: [root, child],
  neighbors: [{ symbol: child, edge: edge("edge-one", root.symbol_id, child.symbol_id) }],
  unresolved: [],
  truncated: true,
  truncation_reason: "max_nodes reached",
};

test("mergeNeighborGraph deduplicates symbols and edges while preserving truncation", () => {
  const once = mergeNeighborGraph(emptyGraphState, response);
  const twice = mergeNeighborGraph(once, response);

  assert.equal(twice.symbols.length, 2);
  assert.equal(twice.edges.length, 1);
  assert.equal(twice.truncated, true);
  assert.equal(twice.truncationReason, "max_nodes reached");
});

test("groupSymbolsByDirectory keeps a deterministic directory/file hierarchy", () => {
  const groups = groupSymbolsByDirectory([
    child,
    root,
    symbol("symbol-root-file", "README.py", "README", 1),
  ]);

  assert.deepEqual(groups.map((group) => group.key), [".", "timeout_demo"]);
  assert.equal(groups[1].symbols[0].path, "timeout_demo/app.py");
  assert.equal(groups[1].label, "timeout_demo");
});

test("stale request IDs and unresolved references stay explicit", () => {
  assert.equal(isLatestRequest(8, 8), true);
  assert.equal(isLatestRequest(7, 8), false);
  const unresolved = {
    unresolved_id: "unresolved-one",
    index_id: indexId,
    source_symbol_id: root.symbol_id,
    path: root.path,
    expression: "handler(client)",
    reason: "dynamic_call_target",
    possible_target_ids: [],
    start_line: 20,
    end_line: 20,
  };
  assert.deepEqual(unresolvedForSymbol([unresolved], root.symbol_id), [unresolved]);
  assert.deepEqual(unresolvedForSymbol([unresolved], child.symbol_id), []);
});
