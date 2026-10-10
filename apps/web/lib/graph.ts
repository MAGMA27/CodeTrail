import type {
  CodeEdge,
  ContextualCall,
  NeighborsResponse,
  SymbolRecord,
  UnresolvedReference,
} from "./api.ts";

export type GraphState = {
  symbols: SymbolRecord[];
  edges: CodeEdge[];
  unresolved: UnresolvedReference[];
  truncated: boolean;
  truncationReason: string | null;
  indexTruncated: boolean;
  indexTruncationReason: string | null;
  indexUnresolvedTruncated: boolean | null;
  indexUnresolvedTruncationReason: string | null;
};

export type SymbolGroup = {
  key: string;
  label: string;
  symbols: SymbolRecord[];
};

export type ModuleDefinitionRow = {
  symbol: SymbolRecord;
  depth: number;
};

export type DirectImportTarget = {
  path: string;
  edges: CodeEdge[];
};

export type CallChainPosition = { x: number; y: number };
export type CallChainEdgeRoute =
  | { type: "forward"; bend: number }
  | { type: "outer"; side: "top" | "bottom"; lane: number; sourceEscapeX: number; targetEscapeX: number; routeY: number };

export const CALL_CHAIN_NODE_WIDTH = 196;
export const CALL_CHAIN_NODE_HEIGHT = 80;
export const CALL_CHAIN_COLUMN_STEP = CALL_CHAIN_NODE_WIDTH + 112;
export const CALL_CHAIN_ROW_STEP = CALL_CHAIN_NODE_HEIGHT + 64;

export function graphViewportLayoutKey(
  model: {
    nodes: Array<{
      id: string;
      position: { x: number; y: number };
      style?: { width?: number | string; height?: number | string };
      parentId?: string | null;
    }>;
    edges: Array<{ id: string; source: string; target: string; data?: Record<string, unknown> }>;
  },
  focusNodeIds: string[],
): string {
  const nodes = model.nodes
    .map((node) => [node.id, node.position.x, node.position.y, node.style?.width ?? null, node.style?.height ?? null, node.parentId ?? null])
    .sort((left, right) => String(left[0]).localeCompare(String(right[0])));
  const edges = model.edges
    .map((edge) => [edge.id, edge.source, edge.target, edge.data?.route ?? null])
    .sort((left, right) => String(left[0]).localeCompare(String(right[0])));
  return JSON.stringify([nodes, edges, [...focusNodeIds].sort()]);
}

function callPairKey(sourceId: string, targetId: string) {
  return `${sourceId}\u0000${targetId}`;
}

export function layoutCallChain(
  rootId: string,
  symbols: SymbolRecord[],
  edges: CodeEdge[],
): { nodes: Map<string, { depth: number; position: CallChainPosition }>; routes: Map<string, CallChainEdgeRoute> } {
  const symbolById = new Map(symbols.map((symbol) => [symbol.symbol_id, symbol]));
  if (!symbolById.has(rootId)) return { nodes: new Map(), routes: new Map() };

  const outgoing = new Map<string, CodeEdge[]>();
  for (const edge of edges) {
    if (edge.kind !== "calls" || !symbolById.has(edge.source_id) || !symbolById.has(edge.target_id)) continue;
    const from = outgoing.get(edge.source_id) ?? [];
    from.push(edge);
    outgoing.set(edge.source_id, from);
  }
  for (const calls of outgoing.values()) {
    calls.sort((left, right) =>
      symbolById.get(left.target_id)!.qualified_name.localeCompare(symbolById.get(right.target_id)!.qualified_name) ||
      left.edge_id.localeCompare(right.edge_id),
    );
  }

  const depthById = new Map([[rootId, 0]]);
  const treeChildren = new Map<string, string[]>();
  for (let depth = 0; depth < 2; depth += 1) {
    const parents = [...depthById.entries()]
      .filter(([, nodeDepth]) => nodeDepth === depth)
      .map(([id]) => id)
      .sort((left, right) => symbolById.get(left)!.qualified_name.localeCompare(symbolById.get(right)!.qualified_name));
    for (const sourceId of parents) {
      for (const edge of outgoing.get(sourceId) ?? []) {
        if (depthById.has(edge.target_id)) continue;
        depthById.set(edge.target_id, depth + 1);
        const children = treeChildren.get(sourceId) ?? [];
        children.push(edge.target_id);
        treeChildren.set(sourceId, children);
      }
    }
  }

  const rowById = new Map<string, number>();
  let nextRow = 0;
  const place = (id: string): number => {
    const children = treeChildren.get(id) ?? [];
    const childRows = children.map(place);
    const row = childRows.length > 0 ? childRows.reduce((sum, value) => sum + value, 0) / childRows.length : nextRow++;
    rowById.set(id, row);
    return row;
  };
  place(rootId);

  const nodes = new Map([...depthById].map(([id, depth]) => [id, {
    depth,
    position: {
      x: 28 + depth * CALL_CHAIN_COLUMN_STEP,
      y: 64 + (rowById.get(id) ?? 0) * CALL_CHAIN_ROW_STEP,
    },
  }]));
  const minY = Math.min(...[...nodes.values()].map(({ position }) => position.y));
  const maxY = Math.max(...[...nodes.values()].map(({ position }) => position.y + CALL_CHAIN_NODE_HEIGHT));
  const validEdges = edges
    .filter((edge) => edge.kind === "calls" && nodes.has(edge.source_id) && nodes.has(edge.target_id))
    .sort((left, right) =>
      symbolById.get(left.source_id)!.qualified_name.localeCompare(symbolById.get(right.source_id)!.qualified_name) ||
      symbolById.get(left.target_id)!.qualified_name.localeCompare(symbolById.get(right.target_id)!.qualified_name) ||
      left.edge_id.localeCompare(right.edge_id),
    );
  const routes = new Map<string, CallChainEdgeRoute>();
  const routeByPair = new Map<string, CallChainEdgeRoute>();
  const forwardBendByPair = new Map<string, number>();
  const fanoutBySource = new Map<string, number>();
  const outerLanes = { top: 0, bottom: 0 };
  let outerIndex = 0;
  for (const edge of validEdges) {
    const pairKey = callPairKey(edge.source_id, edge.target_id);
    const sourceDepth = nodes.get(edge.source_id)!.depth;
    const targetDepth = nodes.get(edge.target_id)!.depth;
    if (targetDepth === sourceDepth + 1) {
      let bend = forwardBendByPair.get(pairKey);
      if (bend === undefined) {
        const fanout = fanoutBySource.get(edge.source_id) ?? 0;
        fanoutBySource.set(edge.source_id, fanout + 1);
        bend = fanout % 2 === 0 ? 8 : -8;
        forwardBendByPair.set(pairKey, bend);
      }
      const route = { type: "forward", bend } as const;
      routes.set(edge.edge_id, route);
      routeByPair.set(pairKey, route);
      continue;
    }
    const existingRoute = routeByPair.get(pairKey);
    if (existingRoute) {
      routes.set(edge.edge_id, existingRoute);
      continue;
    }
    const side = outerIndex++ % 2 === 0 ? "top" : "bottom";
    const lane = outerLanes[side]++;
    const route: CallChainEdgeRoute = {
      type: "outer",
      side,
      lane,
      sourceEscapeX: nodes.get(edge.source_id)!.position.x + CALL_CHAIN_NODE_WIDTH + 28,
      targetEscapeX: nodes.get(edge.target_id)!.position.x - 28,
      routeY: side === "top" ? minY - 28 - lane * 18 : maxY + 28 + lane * 18,
    };
    routes.set(edge.edge_id, route);
    routeByPair.set(pairKey, route);
  }
  return { nodes, routes };
}

export function callChainEdgePath(
  route: CallChainEdgeRoute,
  source: CallChainPosition,
  target: CallChainPosition,
): string {
  if (route.type === "outer") {
    return `M ${source.x},${source.y} L ${route.sourceEscapeX},${source.y} L ${route.sourceEscapeX},${route.routeY} L ${route.targetEscapeX},${route.routeY} L ${route.targetEscapeX},${target.y} L ${target.x},${target.y}`;
  }
  const control = Math.min(48, (target.x - source.x) / 3);
  return `M ${source.x},${source.y} C ${source.x + control},${source.y + route.bend} ${target.x - control},${target.y + route.bend} ${target.x},${target.y}`;
}

export function graphFitNodeIds(nodes: Array<{ id: string; parentId?: string }>, focusNodeIds: string[]): string[] {
  if (focusNodeIds.length === 0) return nodes.map(({ id }) => id);

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const fitIds = new Set(focusNodeIds);
  for (const id of focusNodeIds) {
    let parentId = nodeById.get(id)?.parentId;
    while (parentId && !fitIds.has(parentId)) {
      fitIds.add(parentId);
      parentId = nodeById.get(parentId)?.parentId;
    }
  }
  return [...fitIds];
}

export const emptyGraphState: GraphState = {
  symbols: [],
  edges: [],
  unresolved: [],
  truncated: false,
  truncationReason: null,
  indexTruncated: false,
  indexTruncationReason: null,
  indexUnresolvedTruncated: null,
  indexUnresolvedTruncationReason: null,
};

export function mergeNeighborGraph(previous: GraphState, response: NeighborsResponse): GraphState {
  const symbols = new Map(previous.symbols.map((symbol) => [symbol.symbol_id, symbol]));
  symbols.set(response.root.symbol_id, response.root);
  for (const symbol of response.nodes) {
    symbols.set(symbol.symbol_id, symbol);
  }
  const edges = new Map(previous.edges.map((edge) => [edge.edge_id, edge]));
  for (const neighbor of response.neighbors) {
    symbols.set(neighbor.symbol.symbol_id, neighbor.symbol);
    edges.set(neighbor.edge.edge_id, neighbor.edge);
  }
  const unresolved = new Map(
    previous.unresolved.map((reference) => [reference.unresolved_id, reference]),
  );
  for (const reference of response.unresolved) {
    unresolved.set(reference.unresolved_id, reference);
  }
  const responseUnresolvedTruncated = response.index_unresolved_truncated ?? null;
  const indexUnresolvedTruncated =
    previous.indexUnresolvedTruncated === true || responseUnresolvedTruncated === true
      ? true
      : responseUnresolvedTruncated === false
        ? false
        : previous.indexUnresolvedTruncated;
  return {
    symbols: [...symbols.values()],
    edges: [...edges.values()],
    unresolved: [...unresolved.values()],
    truncated: previous.truncated || response.truncated,
    truncationReason: response.truncated
      ? response.truncation_reason
      : previous.truncationReason,
    indexTruncated: previous.indexTruncated || response.index_truncated,
    indexTruncationReason: response.index_truncated
      ? response.index_truncation_reason
      : previous.indexTruncationReason,
    indexUnresolvedTruncated,
    indexUnresolvedTruncationReason: indexUnresolvedTruncated
      ? response.index_unresolved_truncation_reason ?? previous.indexUnresolvedTruncationReason
      : null,
  };
}

export function mergeContextualCalls(
  previous: ContextualCall[],
  response: NeighborsResponse,
  replace: boolean,
): ContextualCall[] {
  const calls = new Map(
    (replace ? [] : previous)
      .filter((call) => call.index_id === response.index_id)
      .map((call) => [call.context_id, call]),
  );
  for (const call of response.contextual_calls ?? []) {
    calls.set(call.context_id, call);
  }
  return [...calls.values()];
}

export function isCallsOnlyChainResponse(
  response: NeighborsResponse,
  indexId: string,
  rootSymbolId: string,
): boolean {
  const symbols = [response.root, ...response.nodes, ...response.neighbors.map((item) => item.symbol)];
  const symbolIds = new Set(symbols.map((symbol) => symbol.symbol_id));
  return (
    response.index_id === indexId &&
    response.root.index_id === indexId &&
    response.root.symbol_id === rootSymbolId &&
    symbols.every((symbol) => symbol.index_id === indexId) &&
    response.neighbors.every(({ edge, symbol }) =>
      edge.index_id === indexId &&
      edge.kind === "calls" &&
      edge.target_id === symbol.symbol_id &&
      symbolIds.has(edge.source_id)
    ) &&
    response.unresolved.every((item) =>
      item.index_id === indexId && symbolIds.has(item.source_symbol_id),
    )
  );
}

export function hasDirectedCallCycle(rootSymbolId: string, edges: CodeEdge[]): boolean {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    if (edge.kind !== "calls") {
      continue;
    }
    const targets = outgoing.get(edge.source_id) ?? [];
    targets.push(edge.target_id);
    outgoing.set(edge.source_id, targets);
  }

  const visiting = new Set<string>();
  const visited = new Set<string>();
  const visit = (symbolId: string): boolean => {
    if (visiting.has(symbolId)) {
      return true;
    }
    if (visited.has(symbolId)) {
      return false;
    }
    visiting.add(symbolId);
    for (const targetId of outgoing.get(symbolId) ?? []) {
      if (visit(targetId)) {
        return true;
      }
    }
    visiting.delete(symbolId);
    visited.add(symbolId);
    return false;
  };
  return visit(rootSymbolId);
}

export function groupSymbolsByDirectory(symbols: SymbolRecord[]): SymbolGroup[] {
  const groups = new Map<string, SymbolRecord[]>();
  for (const symbol of symbols) {
    const separator = symbol.path.lastIndexOf("/");
    const key = separator > 0 ? symbol.path.slice(0, separator) : ".";
    const group = groups.get(key) ?? [];
    group.push(symbol);
    groups.set(key, group);
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, groupSymbols]) => ({
      key,
      label: key === "." ? "根目录" : key,
      symbols: groupSymbols.sort(
        (left, right) =>
          left.start_line - right.start_line || left.qualified_name.localeCompare(right.qualified_name),
      ),
    }));
}

// ponytail: until symbols expose stable parent IDs, indent only same-file class scopes proven by names and ranges.
export function moduleDefinitionRows(symbols: SymbolRecord[]): ModuleDefinitionRow[] {
  const classes = symbols.filter((symbol) => symbol.kind === "class");
  const parentById = new Map<string, SymbolRecord>();
  for (const symbol of symbols) {
    const parent = classes
      .filter((candidate) =>
        candidate.symbol_id !== symbol.symbol_id &&
        candidate.path === symbol.path &&
        symbol.qualified_name.startsWith(`${candidate.qualified_name}.`) &&
        candidate.start_line <= symbol.start_line &&
        candidate.end_line >= symbol.end_line,
      )
      .sort((left, right) =>
        left.end_line - left.start_line - (right.end_line - right.start_line) ||
        right.qualified_name.length - left.qualified_name.length,
      )[0];
    if (parent) parentById.set(symbol.symbol_id, parent);
  }

  const children = new Map<string, SymbolRecord[]>();
  for (const symbol of symbols) {
    const parent = parentById.get(symbol.symbol_id);
    if (!parent) continue;
    const siblings = children.get(parent.symbol_id) ?? [];
    siblings.push(symbol);
    children.set(parent.symbol_id, siblings);
  }
  const ordered = (items: SymbolRecord[]) => items.sort(
    (left, right) => left.start_line - right.start_line || left.end_line - right.end_line || left.qualified_name.localeCompare(right.qualified_name),
  );
  const rows: ModuleDefinitionRow[] = [];
  const append = (items: SymbolRecord[], depth: number) => {
    for (const symbol of ordered(items)) {
      rows.push({ symbol, depth });
      append(children.get(symbol.symbol_id) ?? [], depth + 1);
    }
  };
  append(symbols.filter((symbol) => !parentById.has(symbol.symbol_id)), 0);
  return rows;
}

export function directImportTargets(state: GraphState, module: SymbolRecord): DirectImportTarget[] {
  const symbols = new Map(state.symbols.map((symbol) => [symbol.symbol_id, symbol]));
  const targets = new Map<string, CodeEdge[]>();
  for (const edge of state.edges) {
    if (edge.kind !== "imports" || edge.index_id !== module.index_id || edge.source_id !== module.symbol_id) continue;
    const target = symbols.get(edge.target_id);
    if (!target || target.index_id !== module.index_id) continue;
    const edges = targets.get(target.path) ?? [];
    if (!edges.some((item) => item.edge_id === edge.edge_id)) edges.push(edge);
    targets.set(target.path, edges);
  }
  return [...targets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, edges]) => ({ path, edges }));
}

export function layoutSymbolGroups(groups: SymbolGroup[], expandedKeys: Set<string>): Array<{ position: { x: number; y: number }; height: number }> {
  const heights = groups.map((group) => expandedKeys.has(group.key) ? Math.max(126, 72 + group.symbols.length * 78) : 74);
  const rowHeights: number[] = [];
  heights.forEach((height, index) => {
    const row = Math.floor(index / 2);
    rowHeights[row] = Math.max(rowHeights[row] ?? 0, height);
  });
  const rowOffsets: number[] = [];
  rowHeights.forEach((height, row) => {
    rowOffsets[row] = row === 0 ? 26 : rowOffsets[row - 1] + rowHeights[row - 1] + 30;
  });
  return groups.map((_, index) => ({
    position: { x: 30 + index % 2 * 370, y: rowOffsets[Math.floor(index / 2)] ?? 26 },
    height: heights[index],
  }));
}

export function isLatestRequest(requestId: number, latestRequestId: number): boolean {
  return requestId === latestRequestId;
}

export function unresolvedForSymbol(
  unresolved: UnresolvedReference[],
  symbolId: string,
): UnresolvedReference[] {
  return unresolved.filter((reference) => reference.source_symbol_id === symbolId);
}

export function edgeKindLabel(kind: CodeEdge["kind"]): string {
  return kind === "calls" ? "调用" : kind === "imports" ? "导入" : "包含";
}
