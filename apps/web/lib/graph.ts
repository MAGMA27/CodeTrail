import type {
  CodeEdge,
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
