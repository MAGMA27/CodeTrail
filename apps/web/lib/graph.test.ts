import assert from "node:assert/strict";
import test from "node:test";

import type { CodeEdge, ContextualCall, NeighborsResponse, SymbolRecord } from "./api.ts";
import {
  emptyGraphState,
  directImportTargets,
  groupSymbolsByDirectory,
  hasDirectedCallCycle,
  isCallsOnlyChainResponse,
  isLatestRequest,
  layoutSymbolGroups,
  mergeContextualCalls,
  mergeNeighborGraph,
  moduleDefinitionRows,
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
  index_truncated: false,
  index_truncation_reason: null,
  index_unresolved_truncated: true,
  index_unresolved_truncation_reason: "max_unresolved reached",
};

test("mergeNeighborGraph deduplicates symbols and edges while preserving truncation", () => {
  const once = mergeNeighborGraph(emptyGraphState, response);
  const twice = mergeNeighborGraph(once, response);

  assert.equal(twice.symbols.length, 2);
  assert.equal(twice.edges.length, 1);
  assert.equal(twice.truncated, true);
  assert.equal(twice.truncationReason, "max_nodes reached");
  assert.equal(twice.indexTruncated, false);
  assert.equal(twice.indexTruncationReason, null);
  assert.equal(twice.indexUnresolvedTruncated, true);
  assert.equal(twice.indexUnresolvedTruncationReason, "max_unresolved reached");
});

test("conditional calls remain visible separately when a static graph is empty", () => {
  const conditional: ContextualCall = {
    context_id: "context-one",
    index_id: indexId,
    activation_edge_id: "edge-one",
    activation_source: root,
    activation_location: edge("edge-one", root.symbol_id, child.symbol_id).source_location,
    source: child,
    target: symbol("symbol-target", "timeout_demo/service.py", "timeout_demo.service.Service.read", 30),
    source_location: { path: child.path, start_line: child.start_line, end_line: child.start_line },
    receiver_constructor_location: { path: "timeout_demo/app.py", start_line: 20, end_line: 22 },
    field_assignment_location: { path: child.path, start_line: 8, end_line: 8 },
    field_name: "service",
    binding_mutation_after_escape_unverified: false,
  };
  const conditionalOnly: NeighborsResponse = {
    ...response,
    nodes: [root],
    neighbors: [],
    contextual_calls: [conditional],
    truncated: true,
    truncation_reason: "contextual calls max_edges reached",
  };
  const staticGraph = mergeNeighborGraph(emptyGraphState, conditionalOnly);
  const displayedConditions = mergeContextualCalls([], conditionalOnly, true);

  assert.equal(staticGraph.edges.length, 0);
  assert.equal(staticGraph.symbols.length, 1);
  assert.deepEqual(displayedConditions, [conditional]);
  assert.deepEqual(
    mergeContextualCalls([{ ...conditional, context_id: "stale-context", index_id: "old-index" }], conditionalOnly, false),
    [conditional],
  );
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

test("module definitions nest only when names and source ranges confirm a class parent", () => {
  const definitions = [
    { ...symbol("class-a", "pkg/mod.py", "pkg.mod.Service", 2), kind: "class" as const, end_line: 40 },
    { ...symbol("method-a", "pkg/mod.py", "pkg.mod.Service.run", 8), end_line: 12 },
    { ...symbol("nested-class", "pkg/mod.py", "pkg.mod.Service.Client", 15), kind: "class" as const, end_line: 30 },
    { ...symbol("nested-method", "pkg/mod.py", "pkg.mod.Service.Client.send", 18), end_line: 20 },
    { ...symbol("lookalike", "pkg/mod.py", "pkg.mod.Services.start", 22), end_line: 24 },
    { ...symbol("outside-range", "pkg/mod.py", "pkg.mod.Service.late", 45), end_line: 47 },
  ];

  assert.deepEqual(
    moduleDefinitionRows(definitions).map(({ symbol: item, depth }) => [item.symbol_id, depth]),
    [
      ["class-a", 0],
      ["method-a", 1],
      ["nested-class", 1],
      ["nested-method", 2],
      ["lookalike", 0],
      ["outside-range", 0],
    ],
  );
});

test("direct imports aggregate by target file and retain every source edge", () => {
  const module = { ...root, kind: "module" as const };
  const firstTarget = { ...child, kind: "module" as const };
  const secondTarget = { ...symbol("symbol-other", "timeout_demo/config.py", "timeout_demo.config", 1), kind: "module" as const };
  const otherFile = { ...symbol("symbol-third", "timeout_demo/store.py", "timeout_demo.store", 1), kind: "module" as const };
  const importEdge = (edgeId: string, targetId: string, line: number, sourceId = module.symbol_id): CodeEdge => ({
    ...edge(edgeId, sourceId, targetId),
    kind: "imports",
    source_location: { path: module.path, start_line: line, end_line: line },
  });
  const state = mergeNeighborGraph(emptyGraphState, {
    ...response,
    root: module,
    nodes: [module, firstTarget, secondTarget, otherFile],
    neighbors: [
      { symbol: firstTarget, edge: importEdge("import-one", firstTarget.symbol_id, 4) },
      { symbol: secondTarget, edge: importEdge("import-two", secondTarget.symbol_id, 9) },
      { symbol: otherFile, edge: importEdge("import-three", otherFile.symbol_id, 14) },
      { symbol: otherFile, edge: importEdge("import-other-source", otherFile.symbol_id, 20, firstTarget.symbol_id) },
    ],
  });

  const targets = directImportTargets(state, module);
  assert.deepEqual(targets.map(({ path, edges }) => [path, edges.map((item) => item.edge_id)]), [
    ["timeout_demo/config.py", ["import-one", "import-two"]],
    ["timeout_demo/store.py", ["import-three"]],
  ]);
  assert.deepEqual(targets[0]?.edges.map(({ source_location }) => source_location.start_line), [4, 9]);
});

test("symbol directory containers grow with their children and stay disjoint", () => {
  const groups = [4, 3, 2, 5, 1].map((count, groupIndex) => ({
    key: `dir-${groupIndex}`,
    label: `dir-${groupIndex}`,
    symbols: Array.from({ length: count }, (_, symbolIndex) => symbol(`s-${groupIndex}-${symbolIndex}`, `dir-${groupIndex}/f${symbolIndex}.py`, `f${symbolIndex}`, 1)),
  }));
  const expanded = new Set(groups.map(({ key }) => key));
  const boxes = layoutSymbolGroups(groups, expanded);

  for (let index = 0; index < groups.length; index += 1) {
    const box = boxes[index];
    const lastChildBottom = box.position.y + 58 + (groups[index].symbols.length - 1) * 78 + 60;
    assert.ok(lastChildBottom <= box.position.y + box.height);
    for (let other = index + 1; other < groups.length; other += 1) {
      const next = boxes[other];
      const overlaps = box.position.x < next.position.x + 340
        && box.position.x + 340 > next.position.x
        && box.position.y < next.position.y + next.height
        && box.position.y + box.height > next.position.y;
      assert.equal(overlaps, false, `${groups[index].key} overlaps ${groups[other].key}`);
    }
  }
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

test("calls-only entry chains stay on the requested graph and expose bounded cycles", () => {
  assert.equal(isCallsOnlyChainResponse(response, indexId, root.symbol_id), true);
  assert.equal(isCallsOnlyChainResponse(response, "another-index", root.symbol_id), false);
  assert.equal(isCallsOnlyChainResponse(response, indexId, child.symbol_id), false);
  assert.equal(
    isCallsOnlyChainResponse({
      ...response,
      neighbors: [{ symbol: child, edge: { ...edge("edge-import", root.symbol_id, child.symbol_id), kind: "imports" } }],
    }, indexId, root.symbol_id),
    false,
  );
  const unknownCall = {
    unresolved_id: "unresolved-one",
    index_id: indexId,
    source_symbol_id: root.symbol_id,
    path: root.path,
    expression: "dynamic_call()",
    reason: "dynamic_call_target",
    possible_target_ids: [],
    start_line: 20,
    end_line: 20,
  };
  assert.equal(isCallsOnlyChainResponse({ ...response, unresolved: [unknownCall] }, indexId, root.symbol_id), true);
  assert.equal(
    isCallsOnlyChainResponse({ ...response, unresolved: [{ ...unknownCall, source_symbol_id: "missing" }] }, indexId, root.symbol_id),
    false,
  );
  assert.equal(hasDirectedCallCycle(root.symbol_id, [edge("edge-out", root.symbol_id, child.symbol_id)]), false);
  assert.equal(hasDirectedCallCycle(root.symbol_id, [
    edge("edge-out", root.symbol_id, child.symbol_id),
    edge("edge-back", child.symbol_id, root.symbol_id),
  ]), true);
});
