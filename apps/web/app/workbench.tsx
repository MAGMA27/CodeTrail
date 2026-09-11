"use client";

import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  createIndex,
  getHealth,
  getNeighbors,
  isHealthResponse,
  openIndex,
  readSource,
  searchIndex,
  type CodeEdge,
  type EdgeKind,
  type Evidence,
  type HealthResponse,
  type IndexResponse,
  type NeighborDirection,
  type SearchMatch,
  type SourceResponse,
  type SymbolRecord,
} from "../lib/api";
import {
  emptyGraphState,
  edgeKindLabel,
  groupSymbolsByDirectory,
  isLatestRequest,
  mergeNeighborGraph,
  unresolvedForSymbol,
  type GraphState,
} from "../lib/graph";

type IndexUiStatus = "unindexed" | "creating" | "loading" | "ready" | "partial" | "error";
type AsyncStatus = "idle" | "loading" | "success" | "empty" | "error";

type SymbolNodeData = {
  symbol: SymbolRecord;
  selected: boolean;
  onSelect: (symbolId: string) => void;
  onExpand: (symbolId: string) => void;
};

type GroupNodeData = {
  label: string;
  count: number;
  expanded: boolean;
  onToggle: (key: string) => void;
  groupKey: string;
};

type SymbolFlowNode = Node<SymbolNodeData, "symbol">;
type GroupFlowNode = Node<GroupNodeData, "group">;
type FlowNode = SymbolFlowNode | GroupFlowNode;
type FlowEdge = Edge<{ kind: EdgeKind }>;

const nodeTypes = {
  symbol: SymbolFlowNodeView,
  group: GroupFlowNodeView,
};

function SymbolFlowNodeView({ data, selected }: NodeProps<SymbolFlowNode>) {
  const shortName = data.symbol.qualified_name.split(".").at(-1) ?? data.symbol.qualified_name;
  return (
    <div
      className={`flow-symbol-card${selected || data.selected ? " is-selected" : ""}`}
      data-kind={data.symbol.kind}
      onClick={() => data.onSelect(data.symbol.symbol_id)}
      >
      <Handle type="target" position={Position.Left} id="target" />
      <span className="flow-handle-label">{data.symbol.kind}</span>
      <span className="flow-symbol-name" title={data.symbol.qualified_name}>
        {shortName}
      </span>
      <span className="flow-symbol-line">
        {data.symbol.path}:{data.symbol.start_line}
      </span>
      <button
        type="button"
        className="flow-expand-button nodrag"
        aria-label={`展开 ${data.symbol.qualified_name} 的关系`}
        onClick={(event) => {
          event.stopPropagation();
          data.onExpand(data.symbol.symbol_id);
        }}
      >
        +
      </button>
      <Handle type="source" position={Position.Right} id="source" />
    </div>
  );
}

function GroupFlowNodeView({ data }: NodeProps<GroupFlowNode>) {
  return (
    <div className="flow-group-card" data-expanded={data.expanded}>
      <div className="flow-group-heading">
        <span className="flow-group-marker" aria-hidden="true" />
        <span className="flow-group-label" title={data.label}>
          {data.label}
        </span>
        <span className="flow-group-count">{data.count}</span>
        <button
          type="button"
          className="flow-group-toggle nodrag"
          onClick={(event) => {
            event.stopPropagation();
            data.onToggle(data.groupKey);
          }}
          aria-label={`${data.expanded ? "收起" : "展开"} ${data.label}`}
        >
          {data.expanded ? "−" : "+"}
        </button>
      </div>
      <span className="flow-group-caption">按相对文件目录分组 · {data.expanded ? "已展开" : "已收起"}</span>
    </div>
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

function indexStatusLabel(status: IndexUiStatus): string {
  return {
    unindexed: "未打开索引",
    creating: "正在建立快照",
    loading: "正在载入索引",
    ready: "索引就绪",
    partial: "索引部分完成",
    error: "索引错误",
  }[status];
}

function queryStatusLabel(status: AsyncStatus): string {
  return {
    idle: "等待搜索",
    loading: "检索中",
    success: "已返回",
    empty: "没有命中",
    error: "检索失败",
  }[status];
}

function evidenceLines(evidence: Evidence): Array<{ number: number; text: string }> {
  return evidence.excerpt.split("\n").map((text, offset) => ({
    number: evidence.start_line + offset,
    text,
  }));
}

export default function Workbench() {
  const [connectionState, setConnectionState] = useState<"checking" | "connected" | "error">("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [repositoryId, setRepositoryId] = useState("timeout-demo");
  const [revision, setRevision] = useState("fixture-v1");
  const [dirty, setDirty] = useState(false);
  const [indexIdInput, setIndexIdInput] = useState("");
  const [indexResponse, setIndexResponse] = useState<IndexResponse | null>(null);
  const [indexStatus, setIndexStatus] = useState<IndexUiStatus>("unindexed");
  const [indexError, setIndexError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("load_settings");
  const [searchStatus, setSearchStatus] = useState<AsyncStatus>("idle");
  const [searchResults, setSearchResults] = useState<SearchMatch[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sourceStatus, setSourceStatus] = useState<AsyncStatus>("idle");
  const [sourceResponse, setSourceResponse] = useState<SourceResponse | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceWindowStart, setSourceWindowStart] = useState(1);
  const [graphStatus, setGraphStatus] = useState<AsyncStatus>("idle");
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphState>(emptyGraphState);
  const [direction, setDirection] = useState<NeighborDirection>("both");
  const [edgeKinds, setEdgeKinds] = useState<EdgeKind[]>(["calls", "imports"]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<FlowNode, FlowEdge> | null>(null);

  const healthController = useRef<AbortController | null>(null);
  const indexController = useRef<AbortController | null>(null);
  const searchController = useRef<AbortController | null>(null);
  const graphController = useRef<AbortController | null>(null);
  const sourceController = useRef<AbortController | null>(null);
  const indexRequestId = useRef(0);
  const searchRequestId = useRef(0);
  const graphRequestId = useRef(0);
  const sourceRequestId = useRef(0);
  const knownGroupKeys = useRef<Set<string>>(new Set());

  const checkHealth = useCallback(async () => {
    healthController.current?.abort();
    const controller = new AbortController();
    healthController.current = controller;
    setConnectionState("checking");
    setConnectionError(null);
    try {
      const result = await getHealth(controller.signal);
      if (!isHealthResponse(result)) {
        throw new Error("后端返回了无法识别的健康状态");
      }
      setHealth(result);
      setConnectionState("connected");
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      setHealth(null);
      setConnectionState("error");
      setConnectionError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    return () => {
      healthController.current?.abort();
      indexController.current?.abort();
      searchController.current?.abort();
      graphController.current?.abort();
      sourceController.current?.abort();
    };
  }, [checkHealth]);

  const clearExploration = useCallback(() => {
    searchController.current?.abort();
    graphController.current?.abort();
    sourceController.current?.abort();
    searchRequestId.current += 1;
    graphRequestId.current += 1;
    sourceRequestId.current += 1;
    setSearchStatus("idle");
    setSearchResults([]);
    setSearchError(null);
    setSelectedId(null);
    setSourceStatus("idle");
    setSourceResponse(null);
    setSourceError(null);
    setGraphStatus("idle");
    setGraphError(null);
    setGraph(emptyGraphState);
    setExpandedGroups(new Set());
    knownGroupKeys.current.clear();
  }, []);

  const applyIndexResponse = useCallback(
    (response: IndexResponse) => {
      setIndexResponse(response);
      setIndexIdInput(response.index.index_id);
      setIndexError(null);
      setIndexStatus(
        response.index.status === "failed"
          ? "error"
          : response.truncated || response.issues.length > 0
            ? "partial"
            : "ready",
      );
      clearExploration();
    },
    [clearExploration],
  );

  const createNewIndex = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const repository = repositoryId.trim();
      if (!repository) {
        setIndexError("请输入服务端 allow-list 中的 repository_id。");
        return;
      }
      indexController.current?.abort();
      const controller = new AbortController();
      indexController.current = controller;
      const requestId = ++indexRequestId.current;
      setIndexStatus("creating");
      setIndexError(null);
      try {
        const response = await createIndex(repository, revision.trim() || "working-tree", dirty, controller.signal);
        if (!isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        applyIndexResponse(response);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        setIndexStatus("error");
        setIndexError(errorMessage(error));
      }
    },
    [applyIndexResponse, dirty, repositoryId, revision],
  );

  const openExistingIndex = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const indexId = indexIdInput.trim();
      if (!indexId) {
        setIndexError("请输入已有 index_id。");
        return;
      }
      indexController.current?.abort();
      const controller = new AbortController();
      indexController.current = controller;
      const requestId = ++indexRequestId.current;
      setIndexStatus("loading");
      setIndexError(null);
      try {
        const response = await openIndex(indexId, controller.signal);
        if (!isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        applyIndexResponse(response);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        setIndexStatus("error");
        setIndexError(errorMessage(error));
      }
    },
    [applyIndexResponse, indexIdInput],
  );

  const loadSource = useCallback(
    async (symbol: SymbolRecord, startLine = symbol.start_line) => {
      if (!indexResponse) {
        return;
      }
      sourceController.current?.abort();
      const controller = new AbortController();
      sourceController.current = controller;
      const requestId = ++sourceRequestId.current;
      const endLine = Math.min(symbol.end_line, startLine + 199);
      setSourceWindowStart(startLine);
      setSourceStatus("loading");
      setSourceError(null);
      try {
        const response = await readSource(
          indexResponse.index.index_id,
          symbol.path,
          startLine,
          endLine,
          controller.signal,
        );
        if (!isLatestRequest(requestId, sourceRequestId.current)) {
          return;
        }
        setSourceResponse(response);
        setSourceStatus("success");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sourceRequestId.current)) {
          return;
        }
        setSourceResponse(null);
        setSourceStatus("error");
        setSourceError(errorMessage(error));
      }
    },
    [indexResponse],
  );

  const selectSymbol = useCallback(
    (symbol: SymbolRecord) => {
      setSelectedId(symbol.symbol_id);
      void loadSource(symbol);
    },
    [loadSource],
  );

  const loadNeighborhood = useCallback(
    async (
      indexId: string,
      symbolId: string,
      replace: boolean,
      requestedDirection = direction,
      requestedEdgeKinds = edgeKinds,
    ) => {
      graphController.current?.abort();
      const controller = new AbortController();
      graphController.current = controller;
      const requestId = ++graphRequestId.current;
      setSelectedId(symbolId);
      setGraphStatus("loading");
      setGraphError(null);
      if (replace) {
        setGraph(emptyGraphState);
        setExpandedGroups(new Set());
        knownGroupKeys.current.clear();
      }
      try {
        const response = await getNeighbors(
          indexId,
          symbolId,
          requestedDirection,
          requestedEdgeKinds,
          controller.signal,
        );
        if (!isLatestRequest(requestId, graphRequestId.current)) {
          return;
        }
        setGraph((previous) =>
          mergeNeighborGraph(replace ? emptyGraphState : previous, response),
        );
        setGraphStatus(
          response.truncated ? "success" : response.neighbors.length === 0 ? "empty" : "success",
        );
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, graphRequestId.current)) {
          return;
        }
        setGraphStatus("error");
        setGraphError(errorMessage(error));
      }
    },
    [direction, edgeKinds],
  );

  const runSearch = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!indexResponse) {
        setSearchError("请先创建或打开一个索引。");
        return;
      }
      const query = searchQuery.trim();
      if (!query) {
        setSearchError("请输入符号名或源码关键词。");
        return;
      }
      searchController.current?.abort();
      const controller = new AbortController();
      searchController.current = controller;
      const requestId = ++searchRequestId.current;
      graphController.current?.abort();
      graphRequestId.current += 1;
      setSearchStatus("loading");
      setSearchError(null);
      setSearchResults([]);
      setSelectedId(null);
      setSourceResponse(null);
      setSourceStatus("idle");
      setGraph(emptyGraphState);
      setGraphStatus("idle");
      setGraphError(null);
      setExpandedGroups(new Set());
      knownGroupKeys.current.clear();
      try {
        const response = await searchIndex(indexResponse.index.index_id, query, 40, controller.signal);
        if (!isLatestRequest(requestId, searchRequestId.current)) {
          return;
        }
        setSearchResults(response.matches);
        if (response.matches.length === 0) {
          setSearchStatus("empty");
          return;
        }
        setSearchStatus("success");
        const first = response.matches[0].symbol;
        selectSymbol(first);
        void loadNeighborhood(indexResponse.index.index_id, first.symbol_id, true);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, searchRequestId.current)) {
          return;
        }
        setSearchStatus("error");
        setSearchError(errorMessage(error));
      }
    },
    [indexResponse, loadNeighborhood, searchQuery, selectSymbol],
  );

  const handleSearchResult = useCallback(
    (match: SearchMatch) => {
      selectSymbol(match.symbol);
      void loadNeighborhood(indexResponse?.index.index_id ?? "", match.symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, selectSymbol],
  );

  const selectedSymbol = useMemo(() => {
    if (!selectedId) {
      return null;
    }
    return (
      graph.symbols.find((symbol) => symbol.symbol_id === selectedId) ??
      searchResults.find((match) => match.symbol.symbol_id === selectedId)?.symbol ??
      null
    );
  }, [graph.symbols, searchResults, selectedId]);

  const selectedUnresolved = useMemo(
    () => (selectedId ? unresolvedForSymbol(graph.unresolved, selectedId) : []),
    [graph.unresolved, selectedId],
  );

  const toggleGroup = useCallback((key: string) => {
    setExpandedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const groups = useMemo(() => groupSymbolsByDirectory(graph.symbols), [graph.symbols]);

  useEffect(() => {
    if (groups.length === 0) {
      knownGroupKeys.current.clear();
      return;
    }
    setExpandedGroups((previous) => {
      const next = new Set(previous);
      const activeKeys = new Set(groups.map((group) => group.key));
      for (const key of [...knownGroupKeys.current]) {
        if (!activeKeys.has(key)) {
          next.delete(key);
        }
      }
      for (const group of groups) {
        if (!knownGroupKeys.current.has(group.key) || previous.size === 0) {
          next.add(group.key);
        }
      }
      knownGroupKeys.current = activeKeys;
      return next;
    });
  }, [groups]);

  const flowModel = useMemo(() => {
    const nodes: FlowNode[] = [];
    const visibleSymbolIds = new Set<string>();
    groups.forEach((group, groupIndex) => {
      const column = groupIndex % 2;
      const row = Math.floor(groupIndex / 2);
      const position = { x: 30 + column * 370, y: 26 + row * 290 };
      const expanded = expandedGroups.has(group.key);
      const groupHeight = expanded ? Math.max(126, 72 + group.symbols.length * 78) : 74;
      nodes.push({
        id: `group:${group.key}`,
        type: "group",
        position,
        data: {
          label: group.label,
          count: group.symbols.length,
          expanded,
          onToggle: toggleGroup,
          groupKey: group.key,
        },
        style: { width: 340, height: groupHeight },
        selectable: false,
        draggable: false,
        zIndex: 0,
      });
      if (!expanded) {
        return;
      }
      group.symbols.forEach((symbol, symbolIndex) => {
        visibleSymbolIds.add(symbol.symbol_id);
        nodes.push({
          id: symbol.symbol_id,
          type: "symbol",
          position: { x: position.x + 16, y: position.y + 58 + symbolIndex * 78 },
          data: {
            symbol,
            selected: selectedId === symbol.symbol_id,
            onSelect: (symbolId) => {
              const next = graph.symbols.find((candidate) => candidate.symbol_id === symbolId);
              if (next) {
                selectSymbol(next);
              }
            },
            onExpand: (symbolId) => {
              if (indexResponse) {
                void loadNeighborhood(indexResponse.index.index_id, symbolId, false);
              }
            },
          },
          style: { width: 308 },
          draggable: false,
          zIndex: 1,
        });
      });
    });

    const edges: FlowEdge[] = graph.edges
      .filter((edge) => visibleSymbolIds.has(edge.source_id) && visibleSymbolIds.has(edge.target_id))
      .map((edge) => ({
        id: edge.edge_id,
        source: edge.source_id,
        target: edge.target_id,
        sourceHandle: "source",
        targetHandle: "target",
        type: "smoothstep",
        label: edgeKindLabel(edge.kind),
        data: { kind: edge.kind },
        markerEnd: { type: MarkerType.ArrowClosed },
        style: {
          stroke: edge.kind === "calls" ? "#f3a65b" : "#79c8c2",
          strokeWidth: edge.kind === "calls" ? 2 : 1.5,
        },
        labelStyle: { fill: "#e7ece8", fontSize: 10, fontWeight: 700 },
        labelBgStyle: { fill: "#152421", fillOpacity: 0.92 },
        labelBgPadding: [5, 3] as [number, number],
      }));
    return { nodes, edges };
  }, [expandedGroups, graph.edges, graph.symbols, groups, indexResponse, loadNeighborhood, selectedId, selectSymbol, toggleGroup]);

  useEffect(() => {
    if (flowInstance && flowModel.nodes.length > 0) {
      const timer = window.setTimeout(() => {
        flowInstance.fitView({ padding: 0.18, duration: 320 });
      }, 50);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [flowInstance, flowModel.nodes.length]);

  const connectedEdges = useMemo(() => {
    if (!selectedId) {
      return [];
    }
    return graph.edges.filter((edge) => edge.source_id === selectedId || edge.target_id === selectedId);
  }, [graph.edges, selectedId]);

  const indexIdentity = indexResponse?.index;
  const sourceHasPrevious = selectedSymbol && sourceWindowStart > selectedSymbol.start_line;
  const sourceHasNext =
    selectedSymbol && sourceResponse && sourceResponse.evidence.end_line < selectedSymbol.end_line;

  const toggleEdgeKind = (kind: EdgeKind) => {
    const next = edgeKinds.includes(kind)
      ? edgeKinds.filter((candidate) => candidate !== kind)
      : [...edgeKinds, kind];
    if (next.length === 0) {
      return;
    }
    setEdgeKinds(next);
    if (indexIdentity && selectedId) {
      void loadNeighborhood(indexIdentity.index_id, selectedId, true, direction, next);
    }
  };

  const changeDirection = (nextDirection: NeighborDirection) => {
    setDirection(nextDirection);
    if (indexIdentity && selectedId) {
      void loadNeighborhood(indexIdentity.index_id, selectedId, true, nextDirection, edgeKinds);
    }
  };

  return (
    <main className="workbench-shell">
      <header className="workbench-header">
        <div className="brand-lockup">
          <span className="brand-mark">CT</span>
          <div>
            <p className="eyebrow">CODETRAIL / INDEX LAB</p>
            <h1>关系勘验台</h1>
          </div>
        </div>
        <div className="header-signal" data-state={connectionState} aria-live="polite">
          <span className="signal-dot" aria-hidden="true" />
          <span>{connectionState === "connected" ? "API ONLINE" : connectionState === "checking" ? "API CHECKING" : "API OFFLINE"}</span>
          {health ? <small>v{health.version}</small> : null}
        </div>
      </header>

      <section className="command-bar" aria-label="索引控制">
        <form className="command-group" onSubmit={(event) => void createNewIndex(event)}>
          <label htmlFor="repository-id">服务端仓库 ID</label>
          <div className="command-input-row">
            <input
              id="repository-id"
              value={repositoryId}
              onChange={(event) => setRepositoryId(event.target.value)}
              placeholder="timeout-demo"
              spellCheck={false}
            />
            <input
              aria-label="索引 revision"
              value={revision}
              onChange={(event) => setRevision(event.target.value)}
              placeholder="working-tree"
              spellCheck={false}
            />
            <label className="toggle-field">
              <input type="checkbox" checked={dirty} onChange={(event) => setDirty(event.target.checked)} />
              <span>dirty</span>
            </label>
            <button type="submit" className="primary-button" disabled={indexStatus === "creating"}>
              {indexStatus === "creating" ? "建立中…" : "创建索引"}
            </button>
          </div>
        </form>
        <form className="command-group command-open" onSubmit={(event) => void openExistingIndex(event)}>
          <label htmlFor="index-id">打开已有 index_id</label>
          <div className="command-input-row">
            <input
              id="index-id"
              value={indexIdInput}
              onChange={(event) => setIndexIdInput(event.target.value)}
              placeholder="index-timeout-demo-…"
              spellCheck={false}
            />
            <button type="submit" className="secondary-button" disabled={indexStatus === "loading"}>
              {indexStatus === "loading" ? "载入中…" : "打开"}
            </button>
          </div>
        </form>
        <button type="button" className="health-button" onClick={() => void checkHealth()} disabled={connectionState === "checking"}>
          {connectionState === "checking" ? "连接检查中" : "重试 API"}
        </button>
      </section>

      {indexError || connectionError ? (
        <div className="global-alert" role="alert">
          <span>{indexError ?? connectionError}</span>
          {connectionState === "error" ? (
            <button type="button" onClick={() => void checkHealth()}>
              重新检查
            </button>
          ) : null}
        </div>
      ) : null}

      <section className="index-ribbon" aria-label="当前索引状态">
        <div className="ribbon-status" data-state={indexStatus}>
          <span className="ribbon-dot" aria-hidden="true" />
          <strong>{indexStatusLabel(indexStatus)}</strong>
        </div>
        {indexIdentity ? (
          <>
            <span className="ribbon-item"><small>REPOSITORY</small><code>{indexIdentity.repository_id}</code></span>
            <span className="ribbon-item"><small>INDEX</small><code>{indexIdentity.index_id}</code></span>
            <span className="ribbon-item"><small>REVISION</small><code>{indexIdentity.revision}</code></span>
            <span className="ribbon-item"><small>WORKTREE</small><code>{indexIdentity.dirty ? "DIRTY" : "CLEAN"}</code></span>
            <span className="ribbon-item"><small>SNAPSHOT</small><code>{indexIdentity.snapshot_hash.slice(0, 12)}</code></span>
          </>
        ) : (
          <span className="ribbon-empty">创建或打开一个服务端允许的索引，开始浏览固定快照。</span>
        )}
      </section>

      <div className="workbench-grid">
        <aside className="search-panel panel-surface" aria-label="符号搜索">
          <div className="panel-heading">
            <div>
              <p className="section-index">01 / TRACE</p>
              <h2>符号检索</h2>
            </div>
            <span className="status-pill" data-state={searchStatus}>{queryStatusLabel(searchStatus)}</span>
          </div>
          <form className="search-form" onSubmit={(event) => void runSearch(event)}>
            <label htmlFor="symbol-search">符号名或源码关键词</label>
            <div className="search-input-row">
              <input
                id="symbol-search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="例如 load_settings"
                spellCheck={false}
              />
              <button type="submit" aria-label="执行搜索" disabled={!indexResponse || searchStatus === "loading"}>
                ↗
              </button>
            </div>
          </form>
          {searchError ? <p className="inline-error">{searchError}</p> : null}
          {searchStatus === "empty" ? <div className="empty-state compact"><span>∅</span><p>没有找到匹配符号。</p></div> : null}
          {searchResults.length > 0 ? (
            <div className="result-list" aria-live="polite">
              <div className="result-list-meta"><span>{searchResults.length} 个候选</span><span>按相关性</span></div>
              {searchResults.map((match) => (
                <button
                  type="button"
                  className={`result-item${selectedId === match.symbol.symbol_id ? " is-selected" : ""}`}
                  key={match.symbol.symbol_id}
                  onClick={() => handleSearchResult(match)}
                >
                  <span className="result-rank">{String(match.rank).padStart(2, "0")}</span>
                  <span className="result-copy">
                    <strong>{match.symbol.qualified_name}</strong>
                    <small>{match.symbol.path}:{match.symbol.start_line} · {match.symbol.kind}</small>
                  </span>
                  <span className="result-arrow">↗</span>
                </button>
              ))}
            </div>
          ) : null}
          {searchStatus === "loading" ? <div className="loading-note"><span className="loader" /> 正在读取索引命中…</div> : null}
          <div className="search-footnote">搜索只返回当前 index_id 的符号与源码命中，不等于已确认影响。</div>
        </aside>

        <section className="graph-panel panel-surface" aria-label="代码关系图">
          <div className="panel-heading graph-heading">
            <div>
              <p className="section-index">02 / TOPOLOGY</p>
              <h2>关系图谱 <span>{graph.symbols.length}/40</span></h2>
            </div>
            <div className="graph-actions">
              <label className="compact-select-label">方向
                <select value={direction} onChange={(event) => changeDirection(event.target.value as NeighborDirection)} disabled={!selectedId}>
                  <option value="both">双向</option>
                  <option value="callers">调用方</option>
                  <option value="callees">被调用方</option>
                </select>
              </label>
              <label className="filter-toggle"><input type="checkbox" checked={edgeKinds.includes("calls")} onChange={() => toggleEdgeKind("calls")} disabled={!selectedId} /><span>calls</span></label>
              <label className="filter-toggle"><input type="checkbox" checked={edgeKinds.includes("imports")} onChange={() => toggleEdgeKind("imports")} disabled={!selectedId} /><span>imports</span></label>
              <button type="button" className="focus-button" onClick={() => flowInstance?.fitView({ padding: 0.18, duration: 320 })} disabled={!flowModel.nodes.length}>聚焦</button>
            </div>
          </div>
          <div className="graph-status-row">
            <span className="graph-status" data-state={graphStatus}>{graphStatus === "loading" ? "载入关系中" : graphStatus === "error" ? "关系请求失败" : graphStatus === "empty" ? "当前方向没有静态边" : graphStatus === "success" ? "当前为已加载局部图" : "选择搜索命中后载入关系"}</span>
            {graph.truncated ? <span className="truncated-badge">已截断 · {graph.truncationReason}</span> : null}
            {graphError ? <span className="inline-error">{graphError}</span> : null}
          </div>
          <div className="graph-stage">
            {flowModel.nodes.length > 0 ? (
              <ReactFlow<FlowNode, FlowEdge>
                nodes={flowModel.nodes}
                edges={flowModel.edges}
                nodeTypes={nodeTypes}
                onInit={setFlowInstance}
                onNodeClick={(_event, node) => {
                  if (node.type !== "symbol") {
                    return;
                  }
                  const symbol = graph.symbols.find((candidate) => candidate.symbol_id === node.id);
                  if (symbol) {
                    selectSymbol(symbol);
                  }
                }}
                fitView
                fitViewOptions={{ padding: 0.18 }}
                nodesConnectable={false}
                nodesDraggable={false}
                elementsSelectable
                minZoom={0.25}
                maxZoom={1.8}
              >
                <Background color="#2a413d" gap={24} size={1} />
                <Controls showInteractive={false} position="bottom-left" />
                <MiniMap
                  pannable
                  zoomable
                  nodeColor={(node) => node.type === "group" ? "#426963" : node.id === selectedId ? "#f3a65b" : "#79c8c2"}
                  maskColor="rgba(10, 20, 18, 0.72)"
                  position="bottom-right"
                />
              </ReactFlow>
            ) : (
              <div className="graph-empty">
                <div className="graph-crosshair" aria-hidden="true"><span /><span /></div>
                <p>{indexResponse ? "搜索一个符号，关系会从它向外展开。" : "建立或打开索引后，这里会显示关系图。"}</p>
                <small>图中只表示当前已加载的静态子图，不代表全仓架构。</small>
              </div>
            )}
          </div>
        </section>

        <aside className="inspector-panel panel-surface" aria-label="源码检查器">
          <div className="panel-heading">
            <div>
              <p className="section-index">03 / EVIDENCE</p>
              <h2>源码证据</h2>
            </div>
            {selectedSymbol ? <span className="status-pill" data-state={sourceStatus}>{sourceStatus === "loading" ? "读取中" : sourceStatus === "error" ? "读取失败" : "已绑定快照"}</span> : null}
          </div>
          {selectedSymbol ? (
            <div className="inspector-content">
              <div className="selected-symbol-heading">
                <span className="kind-chip">{selectedSymbol.kind}</span>
                <h3 title={selectedSymbol.qualified_name}>{selectedSymbol.qualified_name}</h3>
                <p>{selectedSymbol.path} · L{selectedSymbol.start_line}–L{selectedSymbol.end_line}</p>
              </div>
              <div className="inspector-actions">
                <button type="button" className="secondary-button" onClick={() => void loadNeighborhood(selectedSymbol.index_id, selectedSymbol.symbol_id, false)}>
                  展开关系 ↗
                </button>
                <span>{connectedEdges.length} 条静态边</span>
              </div>
              {sourceError ? <div className="source-error" role="alert">{sourceError}<button type="button" onClick={() => void loadSource(selectedSymbol, sourceWindowStart)}>重试</button></div> : null}
              {sourceStatus === "loading" ? <div className="source-loading"><span className="loader" /> 正在从不可变快照读取…</div> : null}
              {sourceResponse ? (
                <>
                  <div className="evidence-meta">
                    <span>SNAPSHOT EVIDENCE</span>
                    <code title={sourceResponse.evidence.content_hash}>{sourceResponse.evidence.content_hash.slice(0, 12)}</code>
                  </div>
                  <div className="source-window">
                    {evidenceLines(sourceResponse.evidence).map((line) => (
                      <div className={`source-line${line.number >= selectedSymbol.start_line && line.number <= selectedSymbol.end_line ? " is-definition" : ""}`} key={line.number}>
                        <span>{String(line.number).padStart(3, "0")}</span>
                        <code>{line.text || " "}</code>
                      </div>
                    ))}
                  </div>
                  <div className="source-pager">
                    <button type="button" onClick={() => void loadSource(selectedSymbol, Math.max(selectedSymbol.start_line, sourceWindowStart - 200))} disabled={!sourceHasPrevious}>上一段</button>
                    <span>{sourceResponse.evidence.start_line}–{sourceResponse.evidence.end_line} / {selectedSymbol.end_line}</span>
                    <button type="button" onClick={() => void loadSource(selectedSymbol, sourceResponse.evidence.end_line + 1)} disabled={!sourceHasNext}>下一段</button>
                  </div>
                </>
              ) : null}
              {selectedUnresolved.length > 0 ? (
                <div className="unresolved-panel">
                  <div className="subsection-heading"><span>UNRESOLVED</span><em>{selectedUnresolved.length}</em></div>
                  {selectedUnresolved.map((reference) => (
                    <div className="unresolved-item" key={reference.unresolved_id}>
                      <code>{reference.expression}</code>
                      <p>{reference.reason} · L{reference.start_line}</p>
                      <small>保留为未解析关系，不绘制确定调用边。</small>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="inspector-empty">
              <span className="empty-number">03</span>
              <p>选择一个符号节点查看路径、定义行和快照证据。</p>
              <small>源码始终来自同一 index_id 的不可变快照。</small>
            </div>
          )}
        </aside>
      </div>

      <footer className="workbench-footer">
        <span>STATIC RELATIONS ONLY</span>
        <span>·</span>
        <span>Issue 分析 / Run / Agent 尚未接入</span>
        <span className="footer-spacer" />
        <span>{indexResponse ? `${graph.symbols.length} nodes · ${graph.edges.length} edges loaded` : "No snapshot loaded"}</span>
      </footer>
    </main>
  );
}
