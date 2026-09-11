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
  createProject,
  createProjectIndex,
  generateProjectFeatures,
  getHealth,
  getProjectNeighbors,
  getProjectOverview,
  getProjectFeatureGeneration,
  importGitHubRepository,
  importLocalPath,
  listProjectIndexes,
  listProjects,
  isHealthResponse,
  openProjectIndex,
  readProjectSource,
  retryLocalImport,
  retryGitHubImport,
  retryImportCleanup,
  searchProjectIndex,
  type CodeEdge,
  type EdgeKind,
  type Evidence,
  type FeatureGeneration,
  type FeatureGenerationMode,
  type FeatureSourceScope,
  type HealthResponse,
  type ImportStatus,
  type IndexResponse,
  type ImportAttempt,
  type GitHubImportResponse,
  type LocalImportResponse,
  type NeighborDirection,
  type OverviewEntryPoint,
  type OverviewModule,
  type ProjectOverview,
  type ProjectRecord,
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
type WorkbenchView = "overview" | "graph";

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

function overviewStatusLabel(status: AsyncStatus, overview: ProjectOverview | null): string {
  if (status === "loading") {
    return "概览载入中";
  }
  if (status === "error") {
    return "概览失败";
  }
  if (overview?.entry_status === "not_identified") {
    return "未识别入口";
  }
  if (overview?.entry_status === "partial") {
    return "结果已截断";
  }
  return overview ? "规则概览" : "等待索引";
}

function importStatusLabel(status: ImportStatus): string {
  return {
    queued: "排队中",
    acquiring: "校验来源",
    indexing: "建立索引",
    ready: "导入完成",
    failed: "导入失败",
    cancelled: "已取消",
    interrupted: "重启中断",
  }[status];
}

function evidenceLines(evidence: Evidence): Array<{ number: number; text: string }> {
  return evidence.excerpt.split("\n").map((text, offset) => ({
    number: evidence.start_line + offset,
    text,
  }));
}

function newFeatureIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `workbench-${crypto.randomUUID()}`;
  }
  return `workbench-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function featureAsyncStatus(generation: FeatureGeneration): AsyncStatus {
  if (generation.status === "failed") {
    return "error";
  }
  if (generation.status === "insufficient_evidence") {
    return "empty";
  }
  return "success";
}

function featureStatusLabel(status: AsyncStatus, generation: FeatureGeneration | null): string {
  if (status === "loading") {
    return "生成功能中";
  }
  if (status === "error") {
    return "生成功能失败";
  }
  if (generation?.status === "insufficient_evidence") {
    return "证据不足";
  }
  if (generation?.status === "partial") {
    return "部分完成";
  }
  if (generation?.status === "succeeded") {
    return "已生成";
  }
  return "等待生成";
}

type EvidenceLink = {
  projectId: string;
  indexId: string;
  path: string;
  startLine: number;
  endLine: number;
  contentHash: string;
  evidenceId: string;
};

function readEvidenceLink(): { link: EvidenceLink | null; error: string | null } {
  const params = new URLSearchParams(window.location.search);
  const keys = ["project_id", "index_id", "path", "start_line", "end_line", "content_hash", "evidence_id"];
  const values = Object.fromEntries(keys.map((key) => [key, params.get(key)]));
  const hasAnyValue = Object.values(values).some((value) => value !== null);
  if (!hasAnyValue) {
    return { link: null, error: null };
  }
  if (Object.values(values).some((value) => !value)) {
    return { link: null, error: "工作台引用缺少必要的项目、索引或证据参数。" };
  }
  const startLine = Number(values.start_line);
  const endLine = Number(values.end_line);
  if (!Number.isInteger(startLine) || !Number.isInteger(endLine) || startLine < 1 || endLine < startLine) {
    return { link: null, error: "工作台引用的源码行号无效。" };
  }
  return {
    link: {
      projectId: values.project_id as string,
      indexId: values.index_id as string,
      path: values.path as string,
      startLine,
      endLine,
      contentHash: values.content_hash as string,
      evidenceId: values.evidence_id as string,
    },
    error: null,
  };
}

export default function Workbench() {
  const [connectionState, setConnectionState] = useState<"checking" | "connected" | "error">("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("Timeout Demo");
  const [projectStatus, setProjectStatus] = useState<AsyncStatus>("idle");
  const [projectError, setProjectError] = useState<string | null>(null);
  const [importName, setImportName] = useState("Local project");
  const [localPath, setLocalPath] = useState("");
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [importAttempt, setImportAttempt] = useState<ImportAttempt | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [githubImportName, setGithubImportName] = useState("GitHub Python project");
  const [githubUrl, setGithubUrl] = useState("");
  const [githubRef, setGithubRef] = useState("");
  const [githubImportStatus, setGithubImportStatus] = useState<ImportStatus | null>(null);
  const [githubImportAttempt, setGithubImportAttempt] = useState<ImportAttempt | null>(null);
  const [githubImportError, setGithubImportError] = useState<string | null>(null);
  const [projectIndexes, setProjectIndexes] = useState<IndexResponse["index"][]>([]);
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
  const [linkedSymbol, setLinkedSymbol] = useState<SymbolRecord | null>(null);
  const [sourceStatus, setSourceStatus] = useState<AsyncStatus>("idle");
  const [sourceResponse, setSourceResponse] = useState<SourceResponse | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceWindowStart, setSourceWindowStart] = useState(1);
  const [graphStatus, setGraphStatus] = useState<AsyncStatus>("idle");
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphState>(emptyGraphState);
  const [viewMode, setViewMode] = useState<WorkbenchView>("overview");
  const [overviewStatus, setOverviewStatus] = useState<AsyncStatus>("idle");
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [selectedOverviewEntryId, setSelectedOverviewEntryId] = useState<string | null>(null);
  const [featureStatus, setFeatureStatus] = useState<AsyncStatus>("idle");
  const [featureGeneration, setFeatureGeneration] = useState<FeatureGeneration | null>(null);
  const [featureError, setFeatureError] = useState<string | null>(null);
  const [featureMode, setFeatureMode] = useState<FeatureGenerationMode>("fixture");
  const [featureSourceScope, setFeatureSourceScope] = useState<FeatureSourceScope>("entry_reachable");
  const [direction, setDirection] = useState<NeighborDirection>("both");
  const [edgeKinds, setEdgeKinds] = useState<EdgeKind[]>(["calls", "imports"]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<FlowNode, FlowEdge> | null>(null);

  const healthController = useRef<AbortController | null>(null);
  const projectController = useRef<AbortController | null>(null);
  const importController = useRef<AbortController | null>(null);
  const indexController = useRef<AbortController | null>(null);
  const searchController = useRef<AbortController | null>(null);
  const graphController = useRef<AbortController | null>(null);
  const sourceController = useRef<AbortController | null>(null);
  const overviewController = useRef<AbortController | null>(null);
  const featureController = useRef<AbortController | null>(null);
  const indexRequestId = useRef(0);
  const projectRequestId = useRef(0);
  const importRequestId = useRef(0);
  const searchRequestId = useRef(0);
  const graphRequestId = useRef(0);
  const sourceRequestId = useRef(0);
  const overviewRequestId = useRef(0);
  const featureRequestId = useRef(0);
  const featureIdempotencyKey = useRef<string | null>(null);
  const knownGroupKeys = useRef<Set<string>>(new Set());
  const evidenceLink = useRef<EvidenceLink | null>(null);
  const evidenceLinkApplied = useRef(false);

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

  const loadProjects = useCallback(async () => {
    projectController.current?.abort();
    const controller = new AbortController();
    projectController.current = controller;
    const requestId = ++projectRequestId.current;
    setProjectStatus("loading");
    setProjectError(null);
    try {
      const response = await listProjects(controller.signal);
      if (!isLatestRequest(requestId, projectRequestId.current)) {
        return;
      }
      setProjects(response.projects);
      setProjectStatus(response.projects.length > 0 ? "success" : "empty");
      const parsedLink = readEvidenceLink();
      evidenceLink.current = parsedLink.link;
      evidenceLinkApplied.current = false;
      if (parsedLink.error) {
        setProjectStatus("error");
        setProjectError(parsedLink.error);
      }
      const savedProjectId = window.localStorage.getItem("codetrail.selectedProjectId");
      const nextProject = parsedLink.error
        ? null
        : parsedLink.link
          ? response.projects.find((project) => project.project_id === parsedLink.link?.projectId) ?? null
          : response.projects.find((project) => project.project_id === savedProjectId) ?? response.projects[0] ?? null;
      if (parsedLink.link && !nextProject) {
        setProjectStatus("error");
        setProjectError("引用的 project_id 不存在，已拒绝打开。");
      }
      setSelectedProjectId(nextProject?.project_id ?? null);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, projectRequestId.current)) {
        return;
      }
      setProjectStatus("error");
      setProjectError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    return () => {
      healthController.current?.abort();
      projectController.current?.abort();
      importController.current?.abort();
      indexController.current?.abort();
      searchController.current?.abort();
      graphController.current?.abort();
      sourceController.current?.abort();
      overviewController.current?.abort();
      featureController.current?.abort();
    };
  }, [checkHealth]);

  const clearExploration = useCallback(() => {
    searchController.current?.abort();
    graphController.current?.abort();
    sourceController.current?.abort();
    overviewController.current?.abort();
    featureController.current?.abort();
    searchRequestId.current += 1;
    graphRequestId.current += 1;
    sourceRequestId.current += 1;
    overviewRequestId.current += 1;
    setSearchStatus("idle");
    setSearchResults([]);
    setSearchError(null);
    setSelectedId(null);
    setLinkedSymbol(null);
    setSourceStatus("idle");
    setSourceResponse(null);
    setSourceError(null);
    setGraphStatus("idle");
    setGraphError(null);
    setGraph(emptyGraphState);
    setViewMode("overview");
    setOverviewStatus("idle");
    setOverview(null);
    setOverviewError(null);
    setSelectedOverviewEntryId(null);
    featureRequestId.current += 1;
    featureIdempotencyKey.current = null;
    setFeatureStatus("idle");
    setFeatureGeneration(null);
    setFeatureError(null);
    setExpandedGroups(new Set());
    knownGroupKeys.current.clear();
  }, []);

  const loadLinkedEvidence = useCallback(
    async (link: EvidenceLink, response: IndexResponse) => {
      sourceController.current?.abort();
      const controller = new AbortController();
      sourceController.current = controller;
      const requestId = ++sourceRequestId.current;
      const linked = {
        symbol_id: `evidence-${link.evidenceId}`,
        index_id: response.index.index_id,
        path: link.path,
        qualified_name: `${link.path}:L${link.startLine}–L${link.endLine}`,
        kind: "file" as const,
        start_line: link.startLine,
        end_line: link.endLine,
      } satisfies SymbolRecord;
      setLinkedSymbol(linked);
      setSelectedId(linked.symbol_id);
      setSourceWindowStart(link.startLine);
      setSourceStatus("loading");
      setSourceError(null);
      try {
        const source = await readProjectSource(
          link.projectId,
          response.index.index_id,
          link.path,
          link.startLine,
          link.endLine,
          controller.signal,
        );
        if (!isLatestRequest(requestId, sourceRequestId.current)) {
          return;
        }
        const evidence = source.evidence;
        if (
          source.index_id !== link.indexId ||
          evidence.index_id !== link.indexId ||
          evidence.evidence_id !== link.evidenceId ||
          evidence.path !== link.path ||
          evidence.start_line !== link.startLine ||
          evidence.end_line !== link.endLine ||
          evidence.content_hash !== link.contentHash
        ) {
          throw new Error("返回的源码证据与链接引用不匹配，已拒绝加载。");
        }
        setSourceResponse(source);
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
    [],
  );

  const loadOverview = useCallback(async (projectId: string, indexId: string) => {
    overviewController.current?.abort();
    const controller = new AbortController();
    overviewController.current = controller;
    const requestId = ++overviewRequestId.current;
    setOverviewStatus("loading");
    setOverviewError(null);
    try {
      const response = await getProjectOverview(projectId, indexId, 4, 40, 100, controller.signal);
      if (!isLatestRequest(requestId, overviewRequestId.current)) {
        return;
      }
      setOverview(response);
      setSelectedOverviewEntryId(response.entries[0]?.entry_point_id ?? null);
      setOverviewStatus("success");
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, overviewRequestId.current)) {
        return;
      }
      setOverview(null);
      setOverviewStatus("error");
      setOverviewError(errorMessage(error));
    }
  }, []);

  const applyFeatureGenerationResponse = useCallback((generation: FeatureGeneration) => {
    setFeatureGeneration(generation);
    setFeatureStatus(featureAsyncStatus(generation));
    setFeatureError(generation.status === "failed" ? generation.error?.message ?? "功能生成失败。" : null);
  }, []);

  const runFeatureGeneration = useCallback(
    async (retryFailed = false) => {
      if (!indexResponse || !selectedProjectId) {
        setFeatureError("请先创建项目并打开一个索引。");
        setFeatureStatus("error");
        return;
      }
      if (featureStatus === "loading") {
        return;
      }
      const key = retryFailed && featureGeneration?.status === "failed"
        ? newFeatureIdempotencyKey()
        : featureIdempotencyKey.current ?? newFeatureIdempotencyKey();
      featureIdempotencyKey.current = key;
      featureController.current?.abort();
      const controller = new AbortController();
      featureController.current = controller;
      const requestId = ++featureRequestId.current;
      const currentIndexId = indexResponse.index.index_id;
      setFeatureStatus("loading");
      setFeatureError(null);
      try {
        const response = await generateProjectFeatures(
          selectedProjectId,
          currentIndexId,
          {
            mode: featureMode,
            source_scope: featureSourceScope,
            idempotency_key: key,
          },
          controller.signal,
        );
        if (
          !isLatestRequest(requestId, featureRequestId.current) ||
          selectedProjectId !== response.generation.project_id ||
          currentIndexId !== response.generation.index.index_id
        ) {
          return;
        }
        applyFeatureGenerationResponse(response.generation);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, featureRequestId.current)) {
          return;
        }
        setFeatureStatus("error");
        setFeatureError(errorMessage(error));
      }
    },
    [
      applyFeatureGenerationResponse,
      featureGeneration?.status,
      featureMode,
      featureSourceScope,
      featureStatus,
      indexResponse,
      selectedProjectId,
    ],
  );

  const loadSavedFeatureGeneration = useCallback(async () => {
    if (!indexResponse || !selectedProjectId || !featureGeneration) {
      return;
    }
    featureController.current?.abort();
    const controller = new AbortController();
    featureController.current = controller;
    const requestId = ++featureRequestId.current;
    const currentIndexId = indexResponse.index.index_id;
    setFeatureStatus("loading");
    setFeatureError(null);
    try {
      const response = await getProjectFeatureGeneration(
        selectedProjectId,
        currentIndexId,
        featureGeneration.generation_id,
        controller.signal,
      );
      if (
        !isLatestRequest(requestId, featureRequestId.current) ||
        response.generation.project_id !== selectedProjectId ||
        response.generation.index.index_id !== currentIndexId
      ) {
        return;
      }
      applyFeatureGenerationResponse(response.generation);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, featureRequestId.current)) {
        return;
      }
      setFeatureStatus("error");
      setFeatureError(errorMessage(error));
    }
  }, [applyFeatureGenerationResponse, featureGeneration, indexResponse, selectedProjectId]);

  const resetFeatureRequest = useCallback((change: () => void) => {
    featureController.current?.abort();
    featureRequestId.current += 1;
    featureIdempotencyKey.current = null;
    setFeatureGeneration(null);
    setFeatureStatus("idle");
    setFeatureError(null);
    change();
  }, []);

  const selectFeatureEvidence = useCallback(
    (evidence: Evidence) => {
      if (!indexResponse || !selectedProjectId) {
        return;
      }
      void loadLinkedEvidence(
        {
          projectId: selectedProjectId,
          indexId: evidence.index_id,
          path: evidence.path,
          startLine: evidence.start_line,
          endLine: evidence.end_line,
          contentHash: evidence.content_hash,
          evidenceId: evidence.evidence_id,
        },
        indexResponse,
      );
    },
    [indexResponse, loadLinkedEvidence, selectedProjectId],
  );

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

  const loadSelectedProject = useCallback(
    async (projectId: string) => {
      indexController.current?.abort();
      const controller = new AbortController();
      indexController.current = controller;
      const requestId = ++indexRequestId.current;
      setIndexResponse(null);
      setIndexIdInput("");
      setIndexError(null);
      setProjectIndexes([]);
      setIndexStatus("loading");
      clearExploration();
      try {
        const projectIndexResponse = await listProjectIndexes(projectId, controller.signal);
        if (!isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        setProjectIndexes(projectIndexResponse.indexes);
        setProjects((previous) => previous.map((project) => (
          project.project_id === projectId
            ? {
                ...project,
                available_index_ids: projectIndexResponse.indexes.map((index) => index.index_id),
                latest_index_id: projectIndexResponse.latest_index_id,
                building_index_id: projectIndexResponse.building_index_id,
              }
            : project
        )));
        const requestedLink =
          !evidenceLinkApplied.current && evidenceLink.current?.projectId === projectId
            ? evidenceLink.current
            : null;
        const requestedIndexId = requestedLink?.indexId ?? projectIndexResponse.latest_index_id;
        if (requestedLink && !projectIndexResponse.indexes.some((index) => index.index_id === requestedLink.indexId)) {
          setIndexStatus("error");
          setIndexError("引用的 index_id 不属于当前 project_id，已拒绝打开。");
          return;
        }
        if (!requestedIndexId) {
          setIndexStatus("unindexed");
          return;
        }
        const response = await openProjectIndex(
          projectId,
          requestedIndexId,
          controller.signal,
        );
        if (!isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        applyIndexResponse(response);
        if (requestedLink) {
          await loadLinkedEvidence(requestedLink, response);
          evidenceLinkApplied.current = true;
        }
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        setIndexStatus("error");
        setIndexError(errorMessage(error));
      }
    },
    [applyIndexResponse, clearExploration, loadLinkedEvidence],
  );

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  useEffect(() => {
    if (!selectedProjectId) {
      indexController.current?.abort();
      indexRequestId.current += 1;
      setProjectIndexes([]);
      setIndexResponse(null);
      setIndexIdInput("");
      setIndexStatus("unindexed");
      setIndexError(null);
      clearExploration();
      return;
    }
    const project = projects.find((candidate) => candidate.project_id === selectedProjectId);
    if (!project) {
      return;
    }
    setProjectName(project.name);
    setRepositoryId(project.repository_id);
    window.localStorage.setItem("codetrail.selectedProjectId", project.project_id);
    void loadSelectedProject(project.project_id);
  }, [clearExploration, loadSelectedProject, selectedProjectId]);

  useEffect(() => {
    const indexId = indexResponse?.index.index_id;
    if (!selectedProjectId || !indexId) {
      return;
    }
    void loadOverview(selectedProjectId, indexId);
  }, [indexResponse?.index.index_id, loadOverview, selectedProjectId]);

  const createNewProject = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = projectName.trim();
      const repository = repositoryId.trim();
      if (!name || !repository) {
        setProjectError("请输入项目名称和服务端 allow-list 中的 repository_id。");
        return;
      }
      projectController.current?.abort();
      const controller = new AbortController();
      projectController.current = controller;
      const requestId = ++projectRequestId.current;
      setProjectStatus("loading");
      setProjectError(null);
      try {
        const project = await createProject(name, repository, controller.signal);
        if (!isLatestRequest(requestId, projectRequestId.current)) {
          return;
        }
        setProjects((previous) => [...previous, project]);
        setSelectedProjectId(project.project_id);
        setProjectStatus("success");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, projectRequestId.current)) {
          return;
        }
        setProjectStatus("error");
        setProjectError(errorMessage(error));
      }
    },
    [projectName, repositoryId],
  );

  const applyImportResponse = useCallback((response: LocalImportResponse) => {
    setImportAttempt(response.attempt);
    setImportStatus(response.attempt.status);
    setImportError(
      response.attempt.status === "ready"
        ? null
        : response.attempt.error_message ?? "本地导入未完成。",
    );
    setProjects((previous) => [
      ...previous.filter((project) => project.project_id !== response.project.project_id),
      response.project,
    ]);
    setSelectedProjectId(response.project.project_id);
    setProjectStatus("success");
  }, []);

  const applyGitHubImportResponse = useCallback((response: GitHubImportResponse) => {
    setGithubImportAttempt(response.attempt);
    setGithubImportStatus(response.attempt.status);
    setGithubImportError(
      response.attempt.status === "ready"
        ? response.cleanup_status === "failed"
          ? response.cleanup_error ?? "索引已完成，但临时目录清理失败。"
          : null
        : response.attempt.error_message ?? "GitHub 导入未完成。",
    );
    setProjects((previous) => [
      ...previous.filter((project) => project.project_id !== response.project.project_id),
      response.project,
    ]);
    setSelectedProjectId(response.project.project_id);
    setProjectStatus("success");
    if (response.index) {
      applyIndexResponse(response.index);
    }
  }, [applyIndexResponse]);

  const importLocalProject = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = importName.trim();
      const path = localPath.trim();
      if (!name || !path) {
        setImportError("请输入项目名称和 API 服务端可见的绝对路径。");
        setImportStatus("failed");
        return;
      }
      importController.current?.abort();
      const controller = new AbortController();
      importController.current = controller;
      const requestId = ++importRequestId.current;
      setImportStatus("queued");
      setImportAttempt(null);
      setImportError(null);
      try {
        const response = await importLocalPath(
          name,
          path,
          revision.trim() || "working-tree",
          dirty,
          undefined,
          controller.signal,
        );
        if (!isLatestRequest(requestId, importRequestId.current)) {
          return;
        }
        applyImportResponse(response);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
          return;
        }
        setImportStatus("failed");
        setImportError(errorMessage(error));
      }
    },
    [applyImportResponse, dirty, importName, localPath, revision],
  );

  const retryLocalProjectImport = useCallback(async () => {
    if (!importAttempt) {
      return;
    }
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    setImportStatus("queued");
    setImportError(null);
    try {
      const response = await retryLocalImport(importAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      applyImportResponse(response);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      setImportStatus("failed");
      setImportError(errorMessage(error));
    }
  }, [applyImportResponse, importAttempt]);

  const importGitHubProject = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = githubImportName.trim();
      const url = githubUrl.trim();
      const ref = githubRef.trim();
      if (!name || !url) {
        setGithubImportError("请输入项目名称和标准 GitHub HTTPS 仓库地址。" );
        setGithubImportStatus("failed");
        return;
      }
      importController.current?.abort();
      const controller = new AbortController();
      importController.current = controller;
      const requestId = ++importRequestId.current;
      setGithubImportStatus("queued");
      setGithubImportAttempt(null);
      setGithubImportError(null);
      try {
        const response = await importGitHubRepository(
          name,
          url,
          ref || undefined,
          undefined,
          controller.signal,
        );
        if (!isLatestRequest(requestId, importRequestId.current)) {
          return;
        }
        applyGitHubImportResponse(response);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
          return;
        }
        setGithubImportStatus("failed");
        setGithubImportError(errorMessage(error));
      }
    }, [applyGitHubImportResponse, githubImportName, githubRef, githubUrl],
  );

  const retryGitHubProjectImport = useCallback(async () => {
    if (!githubImportAttempt) {
      return;
    }
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    setGithubImportStatus("queued");
    setGithubImportError(null);
    try {
      const response = await retryGitHubImport(githubImportAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      applyGitHubImportResponse(response);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      setGithubImportStatus("failed");
      setGithubImportError(errorMessage(error));
    }
  }, [applyGitHubImportResponse, githubImportAttempt]);

  const retryGitHubCleanup = useCallback(async () => {
    if (!githubImportAttempt) {
      return;
    }
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    setGithubImportError(null);
    try {
      const response = await retryImportCleanup(githubImportAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      applyGitHubImportResponse(response);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      setGithubImportError(errorMessage(error));
    }
  }, [applyGitHubImportResponse, githubImportAttempt]);

  const createNewIndex = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!selectedProjectId) {
        setIndexError("请先创建或切换到一个项目。");
        return;
      }
      indexController.current?.abort();
      const controller = new AbortController();
      indexController.current = controller;
      const requestId = ++indexRequestId.current;
      setIndexStatus("creating");
      setIndexError(null);
      try {
        const response = await createProjectIndex(
          selectedProjectId,
          revision.trim() || "working-tree",
          dirty,
          controller.signal,
        );
        if (!isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        applyIndexResponse(response);
        setProjectIndexes((previous) => [
          response.index,
          ...previous.filter((index) => index.index_id !== response.index.index_id),
        ]);
        setProjects((previous) => previous.map((project) => (
          project.project_id === selectedProjectId
            ? {
                ...project,
                available_index_ids: [
                  response.index.index_id,
                  ...project.available_index_ids.filter((indexId) => indexId !== response.index.index_id),
                ],
                latest_index_id: response.index.index_id,
                building_index_id: null,
              }
            : project
        )));
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
          return;
        }
        setIndexStatus("error");
        setIndexError(errorMessage(error));
      }
    },
    [applyIndexResponse, dirty, revision, selectedProjectId],
  );

  const openExistingIndex = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!selectedProjectId) {
        setIndexError("请先创建或切换到一个项目。");
        return;
      }
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
        const response = await openProjectIndex(selectedProjectId, indexId, controller.signal);
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
    [applyIndexResponse, indexIdInput, selectedProjectId],
  );

  const loadSource = useCallback(
    async (symbol: SymbolRecord, startLine = symbol.start_line) => {
      if (!indexResponse || !selectedProjectId) {
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
        const response = await readProjectSource(
          selectedProjectId,
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
    [indexResponse, selectedProjectId],
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
      if (!selectedProjectId) {
        return;
      }
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
        const response = await getProjectNeighbors(
          selectedProjectId,
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
    [direction, edgeKinds, selectedProjectId],
  );

  const selectOverviewEntry = useCallback(
    (entry: OverviewEntryPoint) => {
      setSelectedOverviewEntryId(entry.entry_point_id);
      if (!entry.evidence || !indexResponse || !selectedProjectId) {
        setSelectedId(null);
        setLinkedSymbol(null);
        setSourceResponse(null);
        setSourceStatus(entry.evidence_error ? "error" : "idle");
        setSourceError(entry.evidence_error);
        return;
      }
      const evidence = entry.evidence;
      void loadLinkedEvidence(
        {
          projectId: selectedProjectId,
          indexId: evidence.index_id,
          path: evidence.path,
          startLine: evidence.start_line,
          endLine: evidence.end_line,
          contentHash: evidence.content_hash,
          evidenceId: evidence.evidence_id,
        },
        indexResponse,
      );
    },
    [indexResponse, loadLinkedEvidence, selectedProjectId],
  );

  const openOverviewEntryGraph = useCallback(
    (entry: OverviewEntryPoint) => {
      if (!indexResponse || !overview || !entry.symbol_id) {
        return;
      }
      const module = overview.modules.find((candidate) => candidate.module_id === entry.symbol_id);
      if (!module) {
        return;
      }
      setViewMode("graph");
      selectSymbol(module.symbol);
      void loadNeighborhood(indexResponse.index.index_id, module.symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, overview, selectSymbol],
  );

  const openOverviewModule = useCallback(
    (module: OverviewModule) => {
      if (!indexResponse) {
        return;
      }
      setViewMode("graph");
      selectSymbol(module.symbol);
      void loadNeighborhood(indexResponse.index.index_id, module.symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, selectSymbol],
  );

  const runSearch = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!indexResponse || !selectedProjectId) {
        setSearchError("请先创建项目并打开一个索引。");
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
        const response = await searchProjectIndex(
          selectedProjectId,
          indexResponse.index.index_id,
          query,
          40,
          controller.signal,
        );
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
    [indexResponse, loadNeighborhood, searchQuery, selectSymbol, selectedProjectId],
  );

  const handleSearchResult = useCallback(
    (match: SearchMatch) => {
      if (!selectedProjectId || !indexResponse) {
        return;
      }
      selectSymbol(match.symbol);
      void loadNeighborhood(indexResponse.index.index_id, match.symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, selectSymbol, selectedProjectId],
  );

  const selectedSymbol = useMemo(() => {
    if (!selectedId) {
      return null;
    }
    return (
      graph.symbols.find((symbol) => symbol.symbol_id === selectedId) ??
      searchResults.find((match) => match.symbol.symbol_id === selectedId)?.symbol ??
      (linkedSymbol?.symbol_id === selectedId ? linkedSymbol : null) ??
      null
    );
  }, [graph.symbols, linkedSymbol, searchResults, selectedId]);

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
  const selectedProject = useMemo(
    () => projects.find((project) => project.project_id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
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

      <section className="project-bar panel-surface" aria-label="工作区项目">
        <div className="project-bar-heading">
          <div>
            <p className="section-index">00 / WORKSPACE</p>
            <h2>项目工作区</h2>
          </div>
          <span className="status-pill" data-state={projectStatus}>
            {projectStatus === "loading" ? "同步中" : projectStatus === "error" ? "加载失败" : `${projects.length} 个项目`}
          </span>
        </div>
        <div className="project-bar-body">
          <div className="project-list" aria-label="项目列表">
            {projects.length > 0 ? projects.map((project) => (
              <button
                type="button"
                className={`project-card${project.project_id === selectedProjectId ? " is-selected" : ""}`}
                key={project.project_id}
                onClick={() => setSelectedProjectId(project.project_id)}
                aria-label={`切换项目 ${project.name}`}
              >
                <span className="project-card-mark" aria-hidden="true">{project.name.slice(0, 1).toUpperCase()}</span>
                <span className="project-card-copy">
                  <strong>{project.name}</strong>
                  <small>{project.repository_id} · {project.available_index_ids.length > 0 ? `${project.available_index_ids.length} 个快照` : "未索引"}</small>
                </span>
                <span className="project-card-state">{project.project_id === selectedProjectId ? "当前" : "切换"}</span>
              </button>
            )) : (
              <div className="project-empty"><span>∅</span><p>还没有项目，先创建一个服务端仓库项目。</p></div>
            )}
          </div>
          <div className="project-forms">
            <form className="project-create-form" onSubmit={(event) => void createNewProject(event)}>
              <label htmlFor="project-name">新项目名称</label>
              <input
                id="project-name"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Timeout Demo"
                spellCheck={false}
              />
              <label htmlFor="project-repository">来源 repository_id</label>
              <input
                id="project-repository"
                value={repositoryId}
                onChange={(event) => setRepositoryId(event.target.value)}
                placeholder="timeout-demo"
                spellCheck={false}
              />
              <button type="submit" className="primary-button" disabled={projectStatus === "loading"}>
                {projectStatus === "loading" ? "保存中…" : "创建项目"}
              </button>
            </form>
            <form className="project-create-form project-import-form" onSubmit={(event) => void importLocalProject(event)}>
              <label htmlFor="import-name">导入本地目录 / 项目名称</label>
              <input
                id="import-name"
                value={importName}
                onChange={(event) => setImportName(event.target.value)}
                placeholder="Local project"
                spellCheck={false}
              />
              <label htmlFor="local-path">API 服务端绝对路径</label>
              <input
                id="local-path"
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                placeholder="D:\\projects\\my-repo"
                spellCheck={false}
              />
              <small className="form-help">路径由 API 服务端读取，不是浏览器本机路径；来源目录保持只读。</small>
              <button type="submit" className="primary-button" disabled={importStatus === "queued" || importStatus === "acquiring" || importStatus === "indexing"}>
                {importStatus === "indexing" ? "建立中…" : "导入并建立索引"}
              </button>
              {importAttempt ? (
                <div className="import-status" data-state={importAttempt.status}>
                  <span>{importStatusLabel(importAttempt.status)}</span>
                  <code>{importAttempt.attempt_id}</code>
                  {importAttempt.status === "failed" || importAttempt.status === "interrupted" ? (
                    <button type="button" className="text-button" onClick={() => void retryLocalProjectImport()}>
                      重试
                    </button>
                  ) : null}
                </div>
              ) : null}
            </form>
            <form className="project-create-form project-import-form" onSubmit={(event) => void importGitHubProject(event)}>
              <label htmlFor="github-import-name">导入 GitHub 仓库 / 项目名称</label>
              <input
                id="github-import-name"
                value={githubImportName}
                onChange={(event) => setGithubImportName(event.target.value)}
                placeholder="GitHub Python project"
                spellCheck={false}
              />
              <label htmlFor="github-url">公开 GitHub HTTPS 地址</label>
              <input
                id="github-url"
                value={githubUrl}
                onChange={(event) => setGithubUrl(event.target.value)}
                placeholder="https://github.com/owner/repository"
                spellCheck={false}
                inputMode="url"
              />
              <label htmlFor="github-ref">分支 / tag / commit（可选）</label>
              <input
                id="github-ref"
                value={githubRef}
                onChange={(event) => setGithubRef(event.target.value)}
                placeholder="留空使用默认分支"
                spellCheck={false}
              />
              <small className="form-help">只获取 GitHub 官方 archive，并固定到解析出的 commit；仅索引 Python 文件。</small>
              <button type="submit" className="primary-button" disabled={githubImportStatus === "queued" || githubImportStatus === "acquiring" || githubImportStatus === "indexing"}>
                {githubImportStatus === "acquiring" ? "获取中…" : githubImportStatus === "indexing" ? "建立中…" : "导入 GitHub 仓库"}
              </button>
              {githubImportAttempt ? (
                <div className="import-status" data-state={githubImportAttempt.status}>
                  <span>{importStatusLabel(githubImportAttempt.status)}</span>
                  <code>{githubImportAttempt.resolved_commit?.slice(0, 12) ?? githubImportAttempt.attempt_id}</code>
                  {githubImportAttempt.status === "failed" || githubImportAttempt.status === "interrupted" ? (
                    <button type="button" className="text-button" onClick={() => void retryGitHubProjectImport()}>
                      重试
                    </button>
                  ) : null}
                  {githubImportAttempt.cleanup_status === "failed" ? (
                    <button type="button" className="text-button" onClick={() => void retryGitHubCleanup()}>
                      清理临时目录
                    </button>
                  ) : null}
                  {githubImportAttempt.status === "ready" ? (
                    <small>commit {githubImportAttempt.resolved_commit?.slice(0, 12)} · snapshot {githubImportAttempt.content_hash?.slice(0, 12)}{githubImportAttempt.cleanup_status === "failed" ? " · 清理待重试" : ""}</small>
                  ) : null}
                </div>
              ) : null}
            </form>
          </div>
        </div>
      </section>

      <section className="command-bar" aria-label="索引控制">
        <form className="command-group" onSubmit={(event) => void createNewIndex(event)}>
          <label>当前项目 / 服务端仓库</label>
          <div className="command-input-row">
            <div className="project-context-readout">
              <strong>{selectedProject?.name ?? "未选择项目"}</strong>
              <code>{selectedProject?.repository_id ?? "—"}</code>
            </div>
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
            <button type="submit" className="primary-button" disabled={!selectedProjectId || indexStatus === "creating"}>
              {indexStatus === "creating" ? "建立中…" : "创建索引"}
            </button>
          </div>
        </form>
        <form className="command-group command-open" onSubmit={(event) => void openExistingIndex(event)}>
          <label htmlFor="index-id">打开当前项目已有 index_id · {projectIndexes.length} 个快照</label>
          <div className="command-input-row">
            <input
              id="index-id"
              value={indexIdInput}
              onChange={(event) => setIndexIdInput(event.target.value)}
              placeholder="index-timeout-demo-…"
              spellCheck={false}
            />
            <button type="submit" className="secondary-button" disabled={!selectedProjectId || indexStatus === "loading"}>
              {indexStatus === "loading" ? "载入中…" : "打开"}
            </button>
          </div>
        </form>
        <button type="button" className="health-button" onClick={() => void checkHealth()} disabled={connectionState === "checking"}>
          {connectionState === "checking" ? "连接检查中" : "重试 API"}
        </button>
      </section>

      {indexError || connectionError || projectError || importError || githubImportError ? (
        <div className="global-alert" role="alert">
          <span>{indexError ?? projectError ?? importError ?? githubImportError ?? connectionError}</span>
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

        <section className="graph-panel panel-surface" aria-label={viewMode === "overview" ? "项目概览" : "代码关系图"}>
          <div className="panel-heading graph-heading">
            <div>
              <p className="section-index">02 / TOPOLOGY</p>
              <h2>{viewMode === "overview" ? "项目概览" : "关系图谱"} <span>{viewMode === "overview" ? `${overview?.coverage.visible_modules ?? 0} modules` : `${graph.symbols.length}/40`}</span></h2>
            </div>
            <div className="graph-actions">
              <div className="view-toggle" role="tablist" aria-label="工作台视图">
                <button type="button" role="tab" aria-selected={viewMode === "overview"} className={viewMode === "overview" ? "is-active" : ""} onClick={() => setViewMode("overview")}>项目概览</button>
                <button type="button" role="tab" aria-selected={viewMode === "graph"} className={viewMode === "graph" ? "is-active" : ""} onClick={() => setViewMode("graph")}>代码关系</button>
              </div>
              {viewMode === "graph" ? (
                <>
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
                </>
              ) : null}
            </div>
          </div>
          {viewMode === "overview" ? (
            <div className="graph-status-row">
              <span className="graph-status" data-state={overviewStatus}>{overviewStatusLabel(overviewStatus, overview)}</span>
              {overview?.truncated ? <span className="truncated-badge">已截断 · {overview.truncation_reason}</span> : null}
              {overviewError ? <span className="inline-error">{overviewError}</span> : null}
            </div>
          ) : (
            <div className="graph-status-row">
              <span className="graph-status" data-state={graphStatus}>{graphStatus === "loading" ? "载入关系中" : graphStatus === "error" ? "关系请求失败" : graphStatus === "empty" ? "当前方向没有静态边" : graphStatus === "success" ? "当前为已加载局部图" : "选择搜索命中后载入关系"}</span>
              {graph.truncated ? <span className="truncated-badge">已截断 · {graph.truncationReason}</span> : null}
              {graphError ? <span className="inline-error">{graphError}</span> : null}
            </div>
          )}
          {viewMode === "overview" ? (
            <div className="overview-stage">
              {overviewStatus === "loading" ? (
                <div className="loading-note"><span className="loader" /> 正在从当前不可变快照生成概览…</div>
              ) : overviewStatus === "error" ? (
                <div className="graph-empty"><div className="graph-crosshair" aria-hidden="true"><span /><span /></div><p>项目概览请求失败。</p><small>{overviewError ?? "请重试或切换到其他索引。"}</small><button type="button" className="secondary-button" onClick={() => indexResponse && selectedProjectId && void loadOverview(selectedProjectId, indexResponse.index.index_id)}>重试</button></div>
              ) : overview ? (
                <div className="overview-content">
                  <div className="overview-summary">
                    <div><span className="overview-kicker">RULES / {overview.rules_version}</span><strong>{overview.entry_status === "not_identified" ? "没有命中当前入口规则" : `${overview.entries.length} 个入口候选`}</strong><small>只展示当前 index_id 的静态事实；未识别不等于项目没有入口。</small></div>
                    <div className="overview-metrics"><span><b>{overview.coverage.reachable_modules}</b> reachable</span><span><b>{overview.coverage.unreachable_modules}</b> other modules</span><span><b>{overview.paths.length}</b> paths</span><span><b>{overview.unresolved.length}</b> unresolved</span></div>
                  </div>
                  <section className="feature-generation-section" aria-label="主要功能语义识别">
                    <div className="subsection-heading"><span>O2 / FEATURE SEMANTICS</span><em>{featureGeneration?.features.length ?? "—"}</em></div>
                    <div className="feature-generation-intro">
                      <p>根据当前不可变快照中实际返回的入口、模块、符号和源码证据，生成有边界的主要功能描述。</p>
                      <small>生成是显式操作；fixture 不需要密钥，real 使用 API 服务端配置并可能产生模型费用。</small>
                    </div>
                    <div className="feature-generation-controls">
                      <label className="feature-control">模式
                        <select
                          value={featureMode}
                          onChange={(event) => resetFeatureRequest(() => setFeatureMode(event.target.value as FeatureGenerationMode))}
                          disabled={featureStatus === "loading"}
                        >
                          <option value="fixture">fixture / 离线</option>
                          <option value="real">real / 服务端模型</option>
                        </select>
                      </label>
                      <label className="feature-control">源码范围
                        <select
                          value={featureSourceScope}
                          onChange={(event) => resetFeatureRequest(() => setFeatureSourceScope(event.target.value as FeatureSourceScope))}
                          disabled={featureStatus === "loading"}
                        >
                          <option value="entry_reachable">入口可达模块</option>
                          <option value="visible_modules">当前可见模块</option>
                        </select>
                      </label>
                      <div className="feature-generation-actions">
                        <button
                          type="button"
                          className="primary-button"
                          onClick={() => void runFeatureGeneration()}
                          disabled={!indexResponse || featureStatus === "loading"}
                        >
                          {featureStatus === "loading" ? "生成中…" : "识别主要功能"}
                        </button>
                        {featureGeneration ? (
                          <button type="button" className="secondary-button" onClick={() => void loadSavedFeatureGeneration()} disabled={featureStatus === "loading"}>
                            重读已保存结果
                          </button>
                        ) : null}
                      </div>
                    </div>
                    <div className="feature-scope-note">
                      <span className="graph-status" data-state={featureStatus}>{featureStatusLabel(featureStatus, featureGeneration)}</span>
                      <span>{featureSourceScope === "entry_reachable" ? "只查询入口可达模块；未查询模块不会被当作不存在。" : "在当前可见模块内查询，但仍受工具、源码、输入和输出预算限制。"}</span>
                    </div>
                    {featureStatus === "loading" ? <div className="loading-note"><span className="loader" /> 正在收集有界证据并生成语义摘要…</div> : null}
                    {featureError && !featureGeneration ? (
                      <div className="feature-error" role="alert">
                        <span>{featureError}</span>
                        <button type="button" className="text-button" onClick={() => void runFeatureGeneration()}>重试</button>
                      </div>
                    ) : null}
                    {featureGeneration ? (
                      <div className="feature-generation-result">
                        <div className="feature-generation-meta">
                          <span className="feature-status-badge" data-state={featureGeneration.status}>{featureGeneration.status}</span>
                          {featureGeneration.mode === "fixture" ? <span className="fixture-badge">FIXTURE / OFFLINE</span> : <span className="real-badge">REAL / SERVER CONFIG</span>}
                          <code title={featureGeneration.model ?? "未配置模型"}>{featureGeneration.model ?? "未配置模型"}</code>
                          <small>{featureGeneration.source_paths.length} 个源码文件 · {featureGeneration.usage.tool_calls} 次工具 · {featureGeneration.usage.model_calls} 次模型 · {featureGeneration.usage.elapsed_ms} ms</small>
                        </div>
                        <p className="feature-generation-summary">{featureGeneration.summary}</p>
                        {featureGeneration.error ? (
                          <div className="feature-error" role="alert">
                            <span>{featureGeneration.error.message}</span>
                            <button type="button" className="text-button" onClick={() => void runFeatureGeneration(true)}>重试</button>
                          </div>
                        ) : null}
                        {featureGeneration.truncated ? <p className="feature-warning">上下文已截断：{featureGeneration.truncation_reason}</p> : null}
                        {featureGeneration.omitted_module_ids.length > 0 ? <p className="feature-warning">未查询模块 {featureGeneration.omitted_module_ids.length} 个；它们未被当作没有功能。</p> : null}
                        {featureGeneration.features.length > 0 ? (
                          <div className="feature-list">
                            {featureGeneration.features.map((feature) => (
                              <article className="feature-card" key={feature.feature_id}>
                                <div className="feature-card-heading">
                                  <strong>{feature.name}</strong>
                                  <span>{feature.symbol_ids.length} symbols · {feature.evidence_ids.length} evidence</span>
                                </div>
                                <p>{feature.description}</p>
                                <div className="feature-ref-row">
                                  <span>入口 {feature.entry_point_ids.length}</span>
                                  <span>模块 {feature.module_ids.length}</span>
                                  <span>静态符号 {feature.symbol_ids.length}</span>
                                </div>
                                <div className="feature-evidence-list">
                                  {feature.evidence_ids.map((evidenceId) => {
                                    const evidence = featureGeneration.evidence.find((item) => item.evidence_id === evidenceId);
                                    return evidence ? (
                                      <button type="button" className="feature-evidence-button" key={evidenceId} onClick={() => selectFeatureEvidence(evidence)}>
                                        {evidence.path}:L{evidence.start_line}–L{evidence.end_line}
                                      </button>
                                    ) : <span className="feature-missing-evidence" key={evidenceId}>缺少证据 {evidenceId}</span>;
                                  })}
                                </div>
                                {feature.limitations.length > 0 ? <small className="feature-card-limitations">{feature.limitations.join(" · ")}</small> : null}
                              </article>
                            ))}
                          </div>
                        ) : (
                          <p className="feature-empty">{featureGeneration.status === "insufficient_evidence" ? "当前快照没有收集到足够的入口或源码证据，未生成功能结论。" : "本次没有返回可展示的功能。"}</p>
                        )}
                        {featureGeneration.associations.length > 0 ? (
                          <details className="feature-associations">
                            <summary>查看 {featureGeneration.associations.length} 条功能关联（与静态 CodeEdge 分开）</summary>
                            {featureGeneration.associations.map((association) => (
                              <div className="feature-association" key={association.association_id}>
                                <code>{association.relation}</code>
                                <span>{association.target_kind}:{association.target_id}</span>
                                <small>{association.evidence_ids.length} 条证据</small>
                              </div>
                            ))}
                          </details>
                        ) : null}
                        {featureGeneration.limitations.length > 0 ? (
                          <div className="feature-limitations">
                            <span>边界与限制</span>
                            {featureGeneration.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </section>
                  <div className="overview-section">
                    <div className="subsection-heading"><span>ENTRY CANDIDATES</span><em>{overview.entries.length}</em></div>
                    {overview.entries.length > 0 ? overview.entries.map((entry) => (
                      <div className={`overview-entry-row${selectedOverviewEntryId === entry.entry_point_id ? " is-selected" : ""}`} key={entry.entry_point_id}>
                        <button type="button" className="overview-entry-button" onClick={() => selectOverviewEntry(entry)}>
                          <span className="overview-entry-kind">{entry.kind === "python_main_module" ? "MODULE" : "GUARD"}</span>
                          <span className="overview-entry-copy"><strong>{entry.label}</strong><small>{entry.path}:L{entry.start_line}–L{entry.end_line} · {entry.rule_id}</small></span>
                          <span className="result-arrow">↗</span>
                        </button>
                        <button type="button" className="overview-link-button" onClick={() => openOverviewEntryGraph(entry)} disabled={!entry.symbol_id}>关系图</button>
                      </div>
                    )) : <p className="overview-note">{overview.notes[0] ?? "当前规则没有识别到入口。"}</p>}
                  </div>
                  <div className="overview-section">
                    <div className="subsection-heading"><span>MODULE COVERAGE</span><em>{overview.modules.length}/{overview.coverage.total_modules}</em></div>
                    <div className="overview-module-list">
                      {overview.modules.map((module) => (
                        <button type="button" className="overview-module-button" key={module.module_id} onClick={() => openOverviewModule(module)}>
                          <span className={`overview-reachability${module.entry_reachable ? " is-reachable" : ""}`}>{module.entry_reachable ? "REACHABLE" : "OTHER"}</span>
                          <span className="overview-entry-copy"><strong>{module.symbol.qualified_name}</strong><small>{module.symbol.path} · {module.entry_point_ids.length > 0 ? `${module.entry_point_ids.length} 个入口` : "未从入口路径到达"}</small></span>
                          <span className="result-arrow">↗</span>
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="overview-notes">
                    <div className="subsection-heading"><span>BOUNDARIES</span><em>{overview.notes.length}</em></div>
                    {overview.notes.map((note) => <p key={note}>{note}</p>)}
                  </div>
                </div>
              ) : (
                <div className="graph-empty"><div className="graph-crosshair" aria-hidden="true"><span /><span /></div><p>{indexResponse ? "等待项目概览。" : "建立或打开索引后，这里会显示项目概览。"}</p><small>概览绑定同一 project_id 与 index_id，不执行仓库代码。</small></div>
              )}
            </div>
          ) : (
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
          )}
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
