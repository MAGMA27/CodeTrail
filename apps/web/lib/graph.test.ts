import assert from "node:assert/strict";
import test from "node:test";

import type { CodeEdge, ContextualCall, NeighborsResponse, SymbolRecord } from "./api.ts";
import {
  CALL_CHAIN_NODE_HEIGHT,
  CALL_CHAIN_NODE_WIDTH,
  CALL_CHAIN_ROW_STEP,
  callChainEdgePath,
  layoutCallChain,
  emptyGraphState,
  directImportTargets,
  graphFitNodeIds,
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

test("call-chain layout separates two branches and keeps four target edges clear of other cards", () => {
  const symbols = [
    symbol("chain-root", "app.py", "app.start", 1),
    symbol("chain-a", "app.py", "app.branch_a", 10),
    symbol("chain-b", "app.py", "app.branch_b", 20),
    symbol("chain-a1", "app.py", "app.a_one", 30),
    symbol("chain-a2", "app.py", "app.a_two", 40),
    symbol("chain-b1", "app.py", "app.b_one", 50),
    symbol("chain-b2", "app.py", "app.b_two", 60),
  ];
  const calls = [
    edge("chain-root-a", "chain-root", "chain-a"),
    edge("chain-root-b", "chain-root", "chain-b"),
    edge("chain-a-a1", "chain-a", "chain-a1"),
    edge("chain-a-a2", "chain-a", "chain-a2"),
    edge("chain-b-b1", "chain-b", "chain-b1"),
    edge("chain-b-b2", "chain-b", "chain-b2"),
  ];
  const layout = layoutCallChain("chain-root", symbols, calls);
  assert.equal(layout.nodes.size, symbols.length);
  const boxes = [...layout.nodes].map(([id, node]) => ({ id, x: node.position.x, y: node.position.y }));
  for (let left = 0; left < boxes.length; left += 1) {
    for (let right = left + 1; right < boxes.length; right += 1) {
      const a = boxes[left];
      const b = boxes[right];
      const overlap = a.x < b.x + CALL_CHAIN_NODE_WIDTH && b.x < a.x + CALL_CHAIN_NODE_WIDTH &&
        a.y < b.y + CALL_CHAIN_NODE_HEIGHT && b.y < a.y + CALL_CHAIN_NODE_HEIGHT;
      assert.equal(overlap, false, `${a.id} overlaps ${b.id}`);
    }
  }
  assert.ok(Math.abs(layout.nodes.get("chain-a")!.position.y - (layout.nodes.get("chain-a1")!.position.y + layout.nodes.get("chain-a2")!.position.y) / 2) < 1);
  assert.ok(Math.abs(layout.nodes.get("chain-b")!.position.y - (layout.nodes.get("chain-b1")!.position.y + layout.nodes.get("chain-b2")!.position.y) / 2) < 1);
  assert.ok(layout.nodes.get("chain-b")!.position.y - layout.nodes.get("chain-a")!.position.y >= CALL_CHAIN_ROW_STEP);

  for (const call of calls.slice(2)) {
    const route = layout.routes.get(call.edge_id)!;
    assert.equal(route.type, "forward");
    const source = layout.nodes.get(call.source_id)!.position;
    const target = layout.nodes.get(call.target_id)!.position;
    const corridor = { left: source.x + CALL_CHAIN_NODE_WIDTH, right: target.x };
    assert.ok(corridor.right > corridor.left);
    for (const box of boxes.filter(({ id }) => id !== call.source_id && id !== call.target_id)) {
      assert.equal(box.x < corridor.right && box.x + CALL_CHAIN_NODE_WIDTH > corridor.left, false, `${call.edge_id} crosses ${box.id}`);
    }
    const path = callChainEdgePath(
      route,
      { x: corridor.left, y: source.y + CALL_CHAIN_NODE_HEIGHT / 2 },
      { x: target.x, y: target.y + CALL_CHAIN_NODE_HEIGHT / 2 },
    );
    const coordinates = path.match(/^M (-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?) C (-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/);
    assert.ok(coordinates, `${call.edge_id} uses a forward cubic path`);
    const sx = Number(coordinates[1]);
    const sy = Number(coordinates[2]);
    const c1x = Number(coordinates[3]);
    const c1y = Number(coordinates[4]);
    const c2x = Number(coordinates[5]);
    const c2y = Number(coordinates[6]);
    const tx = Number(coordinates[7]);
    const ty = Number(coordinates[8]);
    for (let step = 1; step < 100; step += 1) {
      const t = step / 100;
      const inverse = 1 - t;
      const point = {
        x: inverse ** 3 * sx + 3 * inverse ** 2 * t * c1x + 3 * inverse * t ** 2 * c2x + t ** 3 * tx,
        y: inverse ** 3 * sy + 3 * inverse ** 2 * t * c1y + 3 * inverse * t ** 2 * c2y + t ** 3 * ty,
      };
      for (const box of boxes.filter(({ id }) => id !== call.source_id && id !== call.target_id)) {
        const crossesCard = point.x >= box.x && point.x <= box.x + CALL_CHAIN_NODE_WIDTH &&
          point.y >= box.y && point.y <= box.y + CALL_CHAIN_NODE_HEIGHT;
        assert.equal(crossesCard, false, `${call.edge_id} path enters ${box.id}`);
      }
    }
  }
});

test("call-chain shared-target, cross-layer, back, and same-layer edges retain independent outer routes", () => {
  const symbols = [
    symbol("lane-root", "app.py", "app.start", 1),
    symbol("lane-a", "app.py", "app.alpha", 10),
    symbol("lane-b", "app.py", "app.beta", 20),
    symbol("lane-shared", "app.py", "app.shared", 30),
  ];
  const calls = [
    edge("lane-root-a", "lane-root", "lane-a"),
    edge("lane-root-b", "lane-root", "lane-b"),
    edge("lane-a-shared", "lane-a", "lane-shared"),
    edge("lane-b-shared", "lane-b", "lane-shared"),
    edge("lane-a-root", "lane-a", "lane-root"),
    edge("lane-a-b", "lane-a", "lane-b"),
  ];
  const layout = layoutCallChain("lane-root", symbols, calls);
  assert.equal(layout.nodes.get("lane-shared")?.depth, 2);
  assert.equal(layout.routes.size, calls.length);
  assert.equal(layout.routes.get("lane-a-shared")?.type, "forward");
  const outer = [
    layout.routes.get("lane-b-shared")!,
    layout.routes.get("lane-a-root")!,
    layout.routes.get("lane-a-b")!,
  ];
  assert.deepEqual(outer.map((route) => route.type), ["outer", "outer", "outer"]);
  assert.equal(new Set(outer.map((route) => route.type === "outer" ? route.routeY : null)).size, outer.length);
  for (const route of outer) {
    assert.equal(route.type, "outer");
    const positions = [...layout.nodes.values()].map(({ position }) => position);
    if (route.type === "outer" && route.side === "top") {
      assert.ok(route.routeY < Math.min(...positions.map(({ y }) => y)));
    } else if (route.type === "outer") {
      assert.ok(route.routeY > Math.max(...positions.map(({ y }) => y + CALL_CHAIN_NODE_HEIGHT)));
    }
  }
  for (const call of calls.filter((item) => layout.routes.get(item.edge_id)?.type === "outer")) {
    const route = layout.routes.get(call.edge_id)!;
    if (route.type !== "outer") assert.fail(`${call.edge_id} should use an outer route`);
    assert.equal(route.sourceEscapeX, layout.nodes.get(call.source_id)!.position.x + CALL_CHAIN_NODE_WIDTH + 28);
    assert.equal(route.targetEscapeX, layout.nodes.get(call.target_id)!.position.x - 28);
  }
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
    const lastChildBottom = box.position.y + 68 + (groups[index].symbols.length - 1) * 78 + 60;
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

test("graph focus bounds include only the focused nodes and their parent groups", () => {
  const nodes = [
    { id: "group:api" },
    { id: "group:routes", parentId: "group:api" },
    { id: "symbol:health", parentId: "group:routes" },
    { id: "symbol:response", parentId: "group:routes" },
    { id: "group:other" },
    { id: "symbol:unused", parentId: "group:other" },
  ];

  assert.deepEqual(graphFitNodeIds(nodes, ["symbol:health", "symbol:response"]), [
    "symbol:health",
    "symbol:response",
    "group:routes",
    "group:api",
  ]);
  assert.deepEqual(graphFitNodeIds(nodes, []), nodes.map(({ id }) => id));
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
