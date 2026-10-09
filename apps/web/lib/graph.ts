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
