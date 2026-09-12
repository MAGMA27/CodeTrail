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
  getProjectFeatureGraph,
  getProjectFeatureGeneration,
  importGitHubRepository,
  importLocalPath,
  appendProjectSessionMessage,
  createProjectSession,
  getProjectSession,
  listProjectIndexes,
  listProjectFeatureGenerations,
  listProjectSessionMessages,
  listProjectSessions,
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
  type FeatureGenerationSummary,
  type FeatureGenerationMode,
  type FeatureGraph,
  type FeatureGraphNode,
  type FeatureGraphOrigin,
  type FeatureGraphRelation,
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
  type Session,
  type SessionMessage,
  type SessionMessageSource,
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
import {
  nextSessionDraftRevision,
  shouldClearSubmittedSessionDraft,
  type SessionDraftSubmission,
} from "../lib/session-draft";
import WorkbenchUi from "./workbench-ui";

type IndexUiStatus = "unindexed" | "creating" | "loading" | "ready" | "partial" | "error";
type AsyncStatus = "idle" | "loading" | "success" | "empty" | "error";
type WorkbenchView = "overview" | "session";
type WorkbenchPanel = "none" | "source" | "graph" | "feature_graph";

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

type FeatureFlowNodeData = {
  node: FeatureGraphNode;
  onSelect: (node: FeatureGraphNode) => void;
};

type FeatureFlowNode = Node<FeatureFlowNodeData, "feature">;
type FeatureFlowEdge = Edge<{ origin: FeatureGraphOrigin; relation: FeatureGraphRelation }>;

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

function FeatureFlowNodeView({ data }: NodeProps<FeatureFlowNode>) {
  const { node } = data;
  return (
    <div
      className="flow-feature-card"
      data-kind={node.kind}
      onClick={() => data.onSelect(node)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          data.onSelect(node);
        }
      }}
    >
      <Handle type="target" position={Position.Left} id="target" />
      <span className="flow-feature-kind">{node.kind.replace("_", " ")}</span>
      <strong title={node.label}>{node.label}</strong>
      {node.detail ? <small title={node.detail}>{node.detail}</small> : null}
      {node.path && node.start_line ? <small>{node.path}:L{node.start_line}–L{node.end_line}</small> : null}
      <Handle type="source" position={Position.Right} id="source" />
    </div>
  );
}

const featureNodeTypes = {
  feature: FeatureFlowNodeView,
};

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

function newSessionIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `session-${crypto.randomUUID()}`;
  }
  return `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
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

function savedFeatureStatusLabel(status: FeatureGenerationSummary["status"]): string {
  return {
    succeeded: "已生成",
    partial: "部分完成",
    insufficient_evidence: "证据不足",
    failed: "失败",
  }[status];
}

function formatFeatureCreatedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function featureSelectionStorageKey(projectId: string, indexId: string): string {
  return `codetrail.selectedFeatureGenerationId:${projectId}:${indexId}`;
}

function sessionSelectionStorageKey(projectId: string): string {
  return `codetrail.selectedSessionId:${projectId}`;
}

function sessionDraftStorageKey(projectId: string, sessionId: string): string {
  return `${projectId}:${sessionId}`;
}

function readSessionDrafts(): Record<string, string> {
  if (typeof window === "undefined") {
    return {};
  }
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem("codetrail.sessionDrafts") ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value).filter((entry): entry is [string, string] => (
        typeof entry[0] === "string" && typeof entry[1] === "string" && entry[1].length <= 20_000
      )),
    );
  } catch {
    return {};
  }
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
  const hasEvidenceValue = keys.slice(1).some((key) => params.get(key) !== null);
  if (!hasEvidenceValue) {
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

type SessionLink = {
  projectId: string;
  sessionId: string;
};

function readSessionLink(): { link: SessionLink | null; error: string | null } {
  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("project_id");
  const sessionId = params.get("session_id");
  if (projectId === null && sessionId === null) {
    return { link: null, error: null };
  }
  if (sessionId === null) {
    return { link: null, error: null };
  }
  if (!projectId || !sessionId) {
    return { link: null, error: "工作台会话引用缺少必要的项目或会话参数。" };
  }
  return { link: { projectId, sessionId }, error: null };
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
  const [sourceGenerationId, setSourceGenerationId] = useState<string | null>(null);
  const [sourceWindowStart, setSourceWindowStart] = useState(1);
  const [graphStatus, setGraphStatus] = useState<AsyncStatus>("idle");
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graph, setGraph] = useState<GraphState>(emptyGraphState);
  const [viewMode, setViewMode] = useState<WorkbenchView>("overview");
  const [panelMode, setPanelMode] = useState<WorkbenchPanel>("none");
  const [overviewStatus, setOverviewStatus] = useState<AsyncStatus>("idle");
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [selectedOverviewEntryId, setSelectedOverviewEntryId] = useState<string | null>(null);
  const [featureStatus, setFeatureStatus] = useState<AsyncStatus>("idle");
  const [featureGeneration, setFeatureGeneration] = useState<FeatureGeneration | null>(null);
  const [featureError, setFeatureError] = useState<string | null>(null);
  const [featureHistory, setFeatureHistory] = useState<FeatureGenerationSummary[]>([]);
  const [featureHistoryCursor, setFeatureHistoryCursor] = useState<string | null>(null);
  const [featureHistoryHasMore, setFeatureHistoryHasMore] = useState(false);
  const [featureHistoryStatus, setFeatureHistoryStatus] = useState<AsyncStatus>("idle");
  const [featureHistoryError, setFeatureHistoryError] = useState<string | null>(null);
  const [selectedFeatureGenerationId, setSelectedFeatureGenerationId] = useState<string | null>(null);
  const [featureGraphStatus, setFeatureGraphStatus] = useState<AsyncStatus>("idle");
  const [featureGraph, setFeatureGraph] = useState<FeatureGraph | null>(null);
  const [featureGraphError, setFeatureGraphError] = useState<string | null>(null);
  const [featureMode, setFeatureMode] = useState<FeatureGenerationMode>("fixture");
  const [featureSourceScope, setFeatureSourceScope] = useState<FeatureSourceScope>("entry_reachable");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionCursor, setSessionCursor] = useState<string | null>(null);
  const [sessionHasMore, setSessionHasMore] = useState(false);
  const [sessionStatus, setSessionStatus] = useState<AsyncStatus>("idle");
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [sessionMessages, setSessionMessages] = useState<SessionMessage[]>([]);
  const [sessionMessagesCursor, setSessionMessagesCursor] = useState<string | null>(null);
  const [sessionMessagesHasMore, setSessionMessagesHasMore] = useState(false);
  const [sessionMessagesStatus, setSessionMessagesStatus] = useState<AsyncStatus>("idle");
  const [sessionMessagesError, setSessionMessagesError] = useState<string | null>(null);
  const [sessionTopic, setSessionTopic] = useState("");
  const [sessionDrafts, setSessionDrafts] = useState<Record<string, string>>(readSessionDrafts);
  const [sessionWriteStatus, setSessionWriteStatus] = useState<AsyncStatus>("idle");
  const [sessionWriteError, setSessionWriteError] = useState<string | null>(null);
  const [direction, setDirection] = useState<NeighborDirection>("both");
  const [edgeKinds, setEdgeKinds] = useState<EdgeKind[]>(["calls", "imports"]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const [featureFlowInstance, setFeatureFlowInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);

  const activeSessionDraftKey = selectedProjectId && selectedSessionId
    ? sessionDraftStorageKey(selectedProjectId, selectedSessionId)
    : null;
  const sessionDraftRevisions = useRef<Record<string, number>>({});
  const sessionDraft = activeSessionDraftKey ? sessionDrafts[activeSessionDraftKey] ?? "" : "";
  const updateSessionDraft = useCallback((value: string) => {
    if (!activeSessionDraftKey) {
      return;
    }
    sessionDraftRevisions.current[activeSessionDraftKey] = nextSessionDraftRevision(
      sessionDraftRevisions.current[activeSessionDraftKey],
    );
    setSessionDrafts((previous) => {
      const next = { ...previous };
      if (value) {
        next[activeSessionDraftKey] = value;
      } else {
        delete next[activeSessionDraftKey];
      }
      return next;
    });
  }, [activeSessionDraftKey]);

  useEffect(() => {
    window.localStorage.setItem("codetrail.sessionDrafts", JSON.stringify(sessionDrafts));
  }, [sessionDrafts]);

  const healthController = useRef<AbortController | null>(null);
  const projectController = useRef<AbortController | null>(null);
  const importController = useRef<AbortController | null>(null);
  const indexController = useRef<AbortController | null>(null);
  const searchController = useRef<AbortController | null>(null);
  const graphController = useRef<AbortController | null>(null);
  const sourceController = useRef<AbortController | null>(null);
  const overviewController = useRef<AbortController | null>(null);
  const featureController = useRef<AbortController | null>(null);
  const featureHistoryController = useRef<AbortController | null>(null);
  const featureGraphController = useRef<AbortController | null>(null);
  const sessionController = useRef<AbortController | null>(null);
  const sessionOpenController = useRef<AbortController | null>(null);
  const sessionMessagesController = useRef<AbortController | null>(null);
  const sessionWriteController = useRef<AbortController | null>(null);
  const indexRequestId = useRef(0);
  const projectRequestId = useRef(0);
  const importRequestId = useRef(0);
  const searchRequestId = useRef(0);
  const graphRequestId = useRef(0);
  const sourceRequestId = useRef(0);
  const overviewRequestId = useRef(0);
  const featureRequestId = useRef(0);
  const featureHistoryRequestId = useRef(0);
  const featureGraphRequestId = useRef(0);
  const sessionRequestId = useRef(0);
  const sessionOpenRequestId = useRef(0);
  const sessionMessagesRequestId = useRef(0);
  const sessionWriteRequestId = useRef(0);
  const featureIdempotencyKey = useRef<string | null>(null);
  const sessionCreateRequest = useRef<{ projectId: string; topic: string; key: string } | null>(null);
  const sessionMessageRequest = useRef<{
    projectId: string;
    sessionId: string;
    content: string;
    key: string;
    draftRevision: number;
  } | null>(null);
  const knownGroupKeys = useRef<Set<string>>(new Set());
  const evidenceLink = useRef<EvidenceLink | null>(null);
  const evidenceLinkApplied = useRef(false);
  const sessionLink = useRef<SessionLink | null>(null);
  const sessionLinkApplied = useRef(false);

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
      const parsedEvidenceLink = readEvidenceLink();
      const parsedSessionLink = readSessionLink();
      evidenceLink.current = parsedEvidenceLink.link;
      evidenceLinkApplied.current = false;
      sessionLink.current = parsedSessionLink.link;
      sessionLinkApplied.current = false;
      const linkError = parsedEvidenceLink.error ?? parsedSessionLink.error;
      if (parsedEvidenceLink.link && parsedSessionLink.link) {
        setProjectStatus("error");
        setProjectError("工作台链接同时包含源码证据和会话引用，已拒绝打开。请只保留一种引用。");
      } else if (linkError) {
        setProjectStatus("error");
        setProjectError(linkError);
      }
      const savedProjectId = window.localStorage.getItem("codetrail.selectedProjectId");
      const requestedProjectId = parsedEvidenceLink.link?.projectId ?? parsedSessionLink.link?.projectId;
      const nextProject = linkError || (parsedEvidenceLink.link && parsedSessionLink.link)
        ? null
        : requestedProjectId
          ? response.projects.find((project) => project.project_id === requestedProjectId) ?? null
          : response.projects.find((project) => project.project_id === savedProjectId) ?? response.projects[0] ?? null;
      if (requestedProjectId && !nextProject) {
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
      featureHistoryController.current?.abort();
      featureGraphController.current?.abort();
      sessionController.current?.abort();
      sessionOpenController.current?.abort();
      sessionMessagesController.current?.abort();
      sessionWriteController.current?.abort();
    };
  }, [checkHealth]);

  const clearExploration = useCallback(() => {
    searchController.current?.abort();
    graphController.current?.abort();
    sourceController.current?.abort();
    overviewController.current?.abort();
    featureController.current?.abort();
    featureHistoryController.current?.abort();
    featureGraphController.current?.abort();
    searchRequestId.current += 1;
    graphRequestId.current += 1;
    sourceRequestId.current += 1;
    overviewRequestId.current += 1;
    featureHistoryRequestId.current += 1;
    featureGraphRequestId.current += 1;
    setSearchStatus("idle");
    setSearchResults([]);
    setSearchError(null);
    setSelectedId(null);
    setLinkedSymbol(null);
    setSourceStatus("idle");
    setSourceResponse(null);
    setSourceError(null);
    setSourceGenerationId(null);
    setGraphStatus("idle");
    setGraphError(null);
    setGraph(emptyGraphState);
    setViewMode("overview");
    setPanelMode("none");
    setOverviewStatus("idle");
    setOverview(null);
    setOverviewError(null);
    setSelectedOverviewEntryId(null);
    featureRequestId.current += 1;
    featureIdempotencyKey.current = null;
    setFeatureStatus("idle");
    setFeatureGeneration(null);
    setFeatureError(null);
    setFeatureHistory([]);
    setFeatureHistoryCursor(null);
    setFeatureHistoryHasMore(false);
    setFeatureHistoryStatus("idle");
    setFeatureHistoryError(null);
    setSelectedFeatureGenerationId(null);
    setFeatureGraphStatus("idle");
    setFeatureGraph(null);
    setFeatureGraphError(null);
    setExpandedGroups(new Set());
    knownGroupKeys.current.clear();
  }, []);

  const loadLinkedEvidence = useCallback(
    async (link: EvidenceLink, response: IndexResponse, generationId: string | null = null) => {
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
      setPanelMode("source");
      setSourceGenerationId(generationId);
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

  const loadFeatureGenerationById = useCallback(
    async (projectId: string, indexId: string, generationId: string) => {
      featureController.current?.abort();
      const controller = new AbortController();
      featureController.current = controller;
      const requestId = ++featureRequestId.current;
      setFeatureStatus("loading");
      setFeatureError(null);
      try {
        const response = await getProjectFeatureGeneration(projectId, indexId, generationId, controller.signal);
        if (
          !isLatestRequest(requestId, featureRequestId.current) ||
          response.generation.project_id !== projectId ||
          response.generation.index.index_id !== indexId ||
          response.generation.generation_id !== generationId
        ) {
          return;
        }
        setSelectedFeatureGenerationId(generationId);
        window.localStorage.setItem(featureSelectionStorageKey(projectId, indexId), generationId);
        applyFeatureGenerationResponse(response.generation);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, featureRequestId.current)) {
          return;
        }
        setFeatureStatus("error");
        setFeatureError(errorMessage(error));
      }
    },
    [applyFeatureGenerationResponse],
  );

  const selectFeatureGeneration = useCallback(
    (generationId: string) => {
      if (!selectedProjectId || !indexResponse) {
        return;
      }
      const indexId = indexResponse.index.index_id;
      setSelectedFeatureGenerationId(generationId);
      window.localStorage.setItem(featureSelectionStorageKey(selectedProjectId, indexId), generationId);
      void loadFeatureGenerationById(selectedProjectId, indexId, generationId);
    },
    [indexResponse, loadFeatureGenerationById, selectedProjectId],
  );

  const loadFeatureHistory = useCallback(
    async (projectId: string, indexId: string, cursor: string | null = null, append = false) => {
      if (append && !cursor) {
        return;
      }
      featureHistoryController.current?.abort();
      const controller = new AbortController();
      featureHistoryController.current = controller;
      const requestId = ++featureHistoryRequestId.current;
      setFeatureHistoryStatus("loading");
      setFeatureHistoryError(null);
      try {
        const response = await listProjectFeatureGenerations(projectId, indexId, 20, cursor ?? undefined, controller.signal);
        if (!isLatestRequest(requestId, featureHistoryRequestId.current)) {
          return;
        }
        setFeatureHistory((previous) => {
          if (!append) {
            return response.generations;
          }
          const existing = new Set(previous.map((generation) => generation.generation_id));
          return [...previous, ...response.generations.filter((generation) => !existing.has(generation.generation_id))];
        });
        setFeatureHistoryCursor(response.next_cursor);
        setFeatureHistoryHasMore(response.has_more);
        setFeatureHistoryStatus(response.generations.length > 0 || append ? "success" : "empty");
        if (!append) {
          const savedId = window.localStorage.getItem(featureSelectionStorageKey(projectId, indexId));
          const selected =
            (savedId && response.generations.some((generation) => generation.generation_id === savedId) ? savedId : null) ??
            response.generations.find((generation) => generation.status === "succeeded" || generation.status === "partial")?.generation_id ??
            response.generations[0]?.generation_id ??
            null;
          setSelectedFeatureGenerationId(selected);
          if (selected) {
            window.localStorage.setItem(featureSelectionStorageKey(projectId, indexId), selected);
            void loadFeatureGenerationById(projectId, indexId, selected);
          } else {
            setFeatureGeneration(null);
            setFeatureStatus("idle");
            setFeatureGraph(null);
            setFeatureGraphStatus("idle");
          }
        }
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, featureHistoryRequestId.current)) {
          return;
        }
        setFeatureHistoryStatus("error");
        setFeatureHistoryError(errorMessage(error));
      }
    },
    [loadFeatureGenerationById],
  );

  const loadFeatureGraph = useCallback(
    async (projectId: string, indexId: string, generationId: string) => {
      featureGraphController.current?.abort();
      const controller = new AbortController();
      featureGraphController.current = controller;
      const requestId = ++featureGraphRequestId.current;
      setFeatureGraphStatus("loading");
      setFeatureGraphError(null);
      try {
        const response = await getProjectFeatureGraph(projectId, indexId, generationId, 40, 80, controller.signal);
        if (
          !isLatestRequest(requestId, featureGraphRequestId.current) ||
          response.graph.project_id !== projectId ||
          response.graph.index_id !== indexId ||
          response.graph.generation_id !== generationId
        ) {
          return;
        }
        setFeatureGraph(response.graph);
        setFeatureGraphStatus(response.graph.nodes.length > 0 ? "success" : "empty");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, featureGraphRequestId.current)) {
          return;
        }
        setFeatureGraph(null);
        setFeatureGraphStatus("error");
        setFeatureGraphError(errorMessage(error));
      }
    },
    [],
  );

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
        setSelectedFeatureGenerationId(response.generation.generation_id);
        window.localStorage.setItem(
          featureSelectionStorageKey(selectedProjectId, currentIndexId),
          response.generation.generation_id,
        );
        applyFeatureGenerationResponse(response.generation);
        void loadFeatureHistory(selectedProjectId, currentIndexId);
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
      loadFeatureHistory,
      selectedProjectId,
    ],
  );

  const loadSavedFeatureGeneration = useCallback(() => {
    if (!indexResponse || !selectedProjectId || !selectedFeatureGenerationId) {
      return;
    }
    void loadFeatureGenerationById(
      selectedProjectId,
      indexResponse.index.index_id,
      selectedFeatureGenerationId,
    );
  }, [indexResponse, loadFeatureGenerationById, selectedFeatureGenerationId, selectedProjectId]);

  const resetFeatureRequest = useCallback((change: () => void) => {
    featureController.current?.abort();
    featureRequestId.current += 1;
    featureIdempotencyKey.current = null;
    setFeatureGeneration(null);
    setFeatureStatus("idle");
    setFeatureError(null);
    setFeatureGraph(null);
    setFeatureGraphStatus("idle");
    setFeatureGraphError(null);
    change();
  }, []);

  const resetSessions = useCallback(() => {
    sessionController.current?.abort();
    sessionOpenController.current?.abort();
    sessionMessagesController.current?.abort();
    sessionWriteController.current?.abort();
    sessionRequestId.current += 1;
    sessionOpenRequestId.current += 1;
    sessionMessagesRequestId.current += 1;
    sessionWriteRequestId.current += 1;
    sessionCreateRequest.current = null;
    sessionMessageRequest.current = null;
    setSessions([]);
    setSessionCursor(null);
    setSessionHasMore(false);
    setSessionStatus("idle");
    setSessionError(null);
    setSelectedSessionId(null);
    setSessionMessages([]);
    setSessionMessagesCursor(null);
    setSessionMessagesHasMore(false);
    setSessionMessagesStatus("idle");
    setSessionMessagesError(null);
    setSessionTopic("");
    setSessionWriteStatus("idle");
    setSessionWriteError(null);
  }, []);

  const loadSessionMessages = useCallback(
    async (projectId: string, sessionId: string, cursor: string | null = null, append = false) => {
      sessionMessagesController.current?.abort();
      const controller = new AbortController();
      sessionMessagesController.current = controller;
      const requestId = ++sessionMessagesRequestId.current;
      setSessionMessagesStatus("loading");
      setSessionMessagesError(null);
      try {
        const response = await listProjectSessionMessages(
          projectId,
          sessionId,
          100,
          cursor ?? undefined,
          controller.signal,
        );
        if (
          !isLatestRequest(requestId, sessionMessagesRequestId.current) ||
          response.project_id !== projectId ||
          response.session_id !== sessionId
        ) {
          return;
        }
        setSessionMessages((previous) => {
          if (!append) {
            return response.messages;
          }
          const known = new Set(previous.map((message) => message.message_id));
          return [...previous, ...response.messages.filter((message) => !known.has(message.message_id))];
        });
        setSessionMessagesCursor(response.next_cursor);
        setSessionMessagesHasMore(response.has_more);
        setSessionMessagesStatus(response.messages.length === 0 && !append ? "empty" : "success");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionMessagesRequestId.current)) {
          return;
        }
        setSessionMessagesStatus("error");
        setSessionMessagesError(errorMessage(error));
      }
    },
    [],
  );

  const openSession = useCallback(
    async (projectId: string, sessionId: string, knownSession?: Session) => {
      sessionWriteController.current?.abort();
      sessionWriteRequestId.current += 1;
      sessionMessageRequest.current = null;
      let session = knownSession;
      const requestId = ++sessionOpenRequestId.current;
      try {
        if (!session) {
          sessionOpenController.current?.abort();
          const controller = new AbortController();
          sessionOpenController.current = controller;
          const response = await getProjectSession(projectId, sessionId, controller.signal);
          if (!isLatestRequest(requestId, sessionOpenRequestId.current)) {
            return;
          }
          session = response.session;
        }
        if (!session || session.project_id !== projectId || session.session_id !== sessionId) {
          throw new Error("返回的会话不属于请求的项目，已拒绝打开。");
        }
        setSessions((previous) => [
          session!,
          ...previous.filter((candidate) => candidate.session_id !== sessionId),
        ]);
        setSelectedSessionId(sessionId);
        setViewMode("session");
        setPanelMode("none");
        setSessionWriteStatus("idle");
        setSessionWriteError(null);
        sessionMessageRequest.current = null;
        window.localStorage.setItem(sessionSelectionStorageKey(projectId), sessionId);
        const url = new URL(window.location.href);
        url.search = "";
        url.searchParams.set("project_id", projectId);
        url.searchParams.set("session_id", sessionId);
        window.history.replaceState(null, "", `${url.pathname}?${url.searchParams.toString()}${url.hash}`);
        if (sessionLink.current?.projectId === projectId && sessionLink.current.sessionId === sessionId) {
          sessionLinkApplied.current = true;
        }
        setSessionMessages([]);
        setSessionMessagesCursor(null);
        setSessionMessagesHasMore(false);
        void loadSessionMessages(projectId, sessionId);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionOpenRequestId.current)) {
          return;
        }
        setSessionStatus("error");
        setSessionError(errorMessage(error));
      }
    },
    [loadSessionMessages],
  );

  const selectSession = useCallback(
    (sessionId: string) => {
      if (!selectedProjectId) {
        return;
      }
      const session = sessions.find((candidate) => candidate.session_id === sessionId);
      void openSession(selectedProjectId, sessionId, session);
    },
    [openSession, selectedProjectId, sessions],
  );

  const loadSessions = useCallback(
    async (projectId: string, cursor: string | null = null, append = false) => {
      sessionController.current?.abort();
      const controller = new AbortController();
      sessionController.current = controller;
      const requestId = ++sessionRequestId.current;
      setSessionStatus("loading");
      setSessionError(null);
      try {
        const response = await listProjectSessions(
          projectId,
          40,
          cursor ?? undefined,
          controller.signal,
        );
        if (!isLatestRequest(requestId, sessionRequestId.current) || response.project_id !== projectId) {
          return;
        }
        setSessions((previous) => {
          if (!append) {
            return response.sessions;
          }
          const known = new Set(previous.map((session) => session.session_id));
          return [...previous, ...response.sessions.filter((session) => !known.has(session.session_id))];
        });
        setSessionCursor(response.next_cursor);
        setSessionHasMore(response.has_more);
        setSessionStatus(response.sessions.length === 0 && !append ? "empty" : "success");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionRequestId.current)) {
          return;
        }
        setSessionStatus("error");
        setSessionError(errorMessage(error));
      }
    },
    [],
  );

  const createSession = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (!selectedProjectId) {
        setSessionError("请先选择一个项目。");
        return;
      }
      const topic = sessionTopic.trim();
      if (!topic) {
        setSessionError("请输入本次 Issue 或排查主题。");
        return;
      }
      const previous = sessionCreateRequest.current;
      const key = previous?.projectId === selectedProjectId && previous.topic === topic
        ? previous.key
        : newSessionIdempotencyKey();
      sessionCreateRequest.current = { projectId: selectedProjectId, topic, key };
      sessionWriteController.current?.abort();
      const controller = new AbortController();
      sessionWriteController.current = controller;
      const requestId = ++sessionWriteRequestId.current;
      setSessionWriteStatus("loading");
      setSessionWriteError(null);
      try {
        const response = await createProjectSession(
          selectedProjectId,
          { topic, idempotencyKey: key },
          controller.signal,
        );
        if (
          !isLatestRequest(requestId, sessionWriteRequestId.current) ||
          response.session.project_id !== selectedProjectId
        ) {
          return;
        }
        setSessions((previousSessions) => [
          response.session,
          ...previousSessions.filter((session) => session.session_id !== response.session.session_id),
        ]);
        setSessionStatus("success");
        setSessionTopic("");
        setSessionWriteStatus("success");
        setSessionWriteError(null);
        sessionCreateRequest.current = null;
        await openSession(selectedProjectId, response.session.session_id, response.session);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionWriteRequestId.current)) {
          return;
        }
        setSessionWriteStatus("error");
        setSessionWriteError(errorMessage(error));
      }
    },
    [openSession, selectedProjectId, sessionTopic],
  );

  const sendSessionMessage = useCallback(
    async (event?: React.FormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      if (sessionWriteStatus === "loading") {
        return;
      }
      if (!selectedProjectId || !selectedSessionId) {
        setSessionWriteError("请先选择一个会话。");
        return;
      }
      const content = sessionDraft.trim();
      if (!content) {
        setSessionWriteError("请输入要保存的消息。");
        return;
      }
      const previous = sessionMessageRequest.current;
      const key = previous?.projectId === selectedProjectId &&
        previous.sessionId === selectedSessionId &&
        previous.content === content
        ? previous.key
        : newSessionIdempotencyKey();
      const draftRevision = activeSessionDraftKey
        ? sessionDraftRevisions.current[activeSessionDraftKey] ?? 0
        : 0;
      const submission: SessionDraftSubmission = {
        projectId: selectedProjectId,
        sessionId: selectedSessionId,
        draftRevision,
      };
      sessionMessageRequest.current = {
        projectId: selectedProjectId,
        sessionId: selectedSessionId,
        content,
        key,
        draftRevision,
      };
      sessionWriteController.current?.abort();
      const controller = new AbortController();
      sessionWriteController.current = controller;
      const requestId = ++sessionWriteRequestId.current;
      setSessionWriteStatus("loading");
      setSessionWriteError(null);
      try {
        const message = await appendProjectSessionMessage(
          selectedProjectId,
          selectedSessionId,
          "human" satisfies SessionMessageSource,
          content,
          key,
          controller.signal,
        );
        if (
          !isLatestRequest(requestId, sessionWriteRequestId.current) ||
          message.session_id !== selectedSessionId
        ) {
          return;
        }
        setSessionMessages((previousMessages) => previousMessages.some(
          (candidate) => candidate.message_id === message.message_id,
        ) ? previousMessages : [...previousMessages, message]);
        setSessions((previousSessions) => previousSessions.map((session) => (
          session.session_id === selectedSessionId
            ? { ...session, message_count: Math.max(session.message_count, message.sequence), updated_at: message.created_at }
            : session
        )));
        const shouldClearDraft = shouldClearSubmittedSessionDraft(
          {
            projectId: selectedProjectId,
            sessionId: selectedSessionId,
            draftRevision: activeSessionDraftKey
              ? sessionDraftRevisions.current[activeSessionDraftKey]
              : undefined,
          },
          submission,
        );
        if (shouldClearDraft) {
          updateSessionDraft("");
        }
        setSessionWriteStatus(shouldClearDraft ? "success" : "idle");
        setSessionWriteError(null);
        sessionMessageRequest.current = null;
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionWriteRequestId.current)) {
          return;
        }
        setSessionWriteStatus("error");
        setSessionWriteError(errorMessage(error));
      }
    },
    [activeSessionDraftKey, selectedProjectId, selectedSessionId, sessionDraft, sessionWriteStatus, updateSessionDraft],
  );

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
        selectedFeatureGenerationId,
      );
    },
    [indexResponse, loadLinkedEvidence, selectedFeatureGenerationId, selectedProjectId],
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
    if (!selectedProjectId || sessionStatus !== "success") {
      return;
    }
    const requested = sessionLink.current?.projectId === selectedProjectId
      ? sessionLink.current.sessionId
      : null;
    const saved = requested ? null : window.localStorage.getItem(sessionSelectionStorageKey(selectedProjectId));
    const target = requested ?? saved;
    if (!target) {
      sessionLinkApplied.current = true;
      return;
    }
    if (selectedSessionId === target) {
      sessionLinkApplied.current = true;
      setViewMode("session");
      return;
    }
    if (sessionLinkApplied.current) {
      return;
    }
    sessionLinkApplied.current = true;
    const knownSession = sessions.find((session) => session.session_id === target);
    void openSession(selectedProjectId, target, knownSession);
  }, [indexResponse?.index.index_id, openSession, selectedProjectId, selectedSessionId, sessionStatus, sessions]);

  useEffect(() => {
    if (!selectedProjectId) {
      indexController.current?.abort();
      indexRequestId.current += 1;
      resetSessions();
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
    sessionLinkApplied.current = false;
    resetSessions();
    void loadSessions(project.project_id);
    void loadSelectedProject(project.project_id);
  }, [clearExploration, loadSelectedProject, loadSessions, resetSessions, selectedProjectId]);

  useEffect(() => {
    const indexId = indexResponse?.index.index_id;
    if (!selectedProjectId || !indexId) {
      return;
    }
    void loadOverview(selectedProjectId, indexId);
  }, [indexResponse?.index.index_id, loadOverview, selectedProjectId]);

  useEffect(() => {
    const indexId = indexResponse?.index.index_id;
    if (!selectedProjectId || !indexId) {
      return;
    }
    void loadFeatureHistory(selectedProjectId, indexId);
  }, [indexResponse?.index.index_id, loadFeatureHistory, selectedProjectId]);

  useEffect(() => {
    const indexId = indexResponse?.index.index_id;
    const generationId = featureGeneration?.generation_id;
    if (
      !selectedProjectId ||
      !indexId ||
      !generationId ||
      featureGeneration.project_id !== selectedProjectId ||
      featureGeneration.index.index_id !== indexId
    ) {
      featureGraphController.current?.abort();
      featureGraphRequestId.current += 1;
      setFeatureGraph(null);
      setFeatureGraphStatus("idle");
      setFeatureGraphError(null);
      return;
    }
    void loadFeatureGraph(selectedProjectId, indexId, generationId);
  }, [featureGeneration, indexResponse?.index.index_id, loadFeatureGraph, selectedProjectId]);

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
      setSourceGenerationId(null);
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
      setPanelMode("source");
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
        setSourceGenerationId(null);
        setSourceStatus(entry.evidence_error ? "error" : "idle");
        setSourceError(entry.evidence_error);
        setPanelMode("source");
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
      selectSymbol(module.symbol);
      setViewMode("overview");
      setPanelMode("graph");
      void loadNeighborhood(indexResponse.index.index_id, module.symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, overview, selectSymbol],
  );

  const openOverviewModule = useCallback(
    (module: OverviewModule) => {
      if (!indexResponse) {
        return;
      }
      selectSymbol(module.symbol);
      setViewMode("overview");
      setPanelMode("graph");
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

  const selectFeatureGraphNode = useCallback(
    (node: FeatureGraphNode) => {
      if (node.symbol) {
        selectSymbol(node.symbol);
        return;
      }
      const evidence = node.evidence_ids
        .map((evidenceId) => featureGraph?.evidence.find((item) => item.evidence_id === evidenceId))
        .find((item): item is Evidence => item !== undefined);
      if (evidence) {
        selectFeatureEvidence(evidence);
      }
    },
    [featureGraph, selectFeatureEvidence, selectSymbol],
  );

  const featureFlowModel = useMemo(() => {
    if (!featureGraph) {
      return { nodes: [] as FeatureFlowNode[], edges: [] as FeatureFlowEdge[] };
    }
    const rowByKind: Record<FeatureGraphNode["kind"], number> = {
      feature: 0,
      entry_point: 0,
      module: 0,
      symbol: 0,
    };
    const positions: Record<FeatureGraphNode["kind"], number> = {
      feature: 30,
      entry_point: 390,
      module: 760,
      symbol: 760,
    };
    const nodes: FeatureFlowNode[] = featureGraph.nodes.map((node) => {
      const row = rowByKind[node.kind]++;
      return {
        id: node.node_id,
        type: "feature",
        position: { x: positions[node.kind], y: 25 + row * 112 },
        data: { node, onSelect: selectFeatureGraphNode },
        style: { width: node.kind === "feature" ? 300 : 270 },
        draggable: false,
      };
    });
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges: FeatureFlowEdge[] = featureGraph.edges
      .filter((edge) => nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id))
      .map((edge) => ({
        id: edge.edge_id,
        source: edge.source_id,
        target: edge.target_id,
        sourceHandle: "source",
        targetHandle: "target",
        type: "smoothstep",
        label: edge.relation.replaceAll("_", " "),
        data: { origin: edge.origin, relation: edge.relation },
        markerEnd: { type: MarkerType.ArrowClosed },
        style: {
          stroke: edge.origin === "semantic" ? "#f3a65b" : "#79c8c2",
          strokeWidth: edge.origin === "semantic" ? 2 : 1.5,
          strokeDasharray: edge.origin === "static" ? "5 4" : undefined,
        },
        labelStyle: { fill: "#e7ece8", fontSize: 10, fontWeight: 700 },
        labelBgStyle: { fill: "#152421", fillOpacity: 0.92 },
        labelBgPadding: [5, 3] as [number, number],
      }));
    return { nodes, edges };
  }, [featureGraph, selectFeatureGraphNode]);

  useEffect(() => {
    if (flowInstance && flowModel.nodes.length > 0) {
      const timer = window.setTimeout(() => {
        flowInstance.fitView({ padding: 0.18, duration: 320 });
      }, 50);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [flowInstance, flowModel.nodes.length]);

  useEffect(() => {
    if (featureFlowInstance && featureFlowModel.nodes.length > 0) {
      const timer = window.setTimeout(() => {
        featureFlowInstance.fitView({ padding: 0.18, duration: 320 });
      }, 50);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [featureFlowInstance, featureFlowModel.nodes.length, featureGraph?.generation_id]);

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
    <WorkbenchUi
      connection={{
        state: connectionState,
        health,
        error: connectionError,
        onCheck: checkHealth,
      }}
      project={{
        projects,
        selectedProjectId,
        selectedProject,
        status: projectStatus,
        error: projectError,
        name: projectName,
        repositoryId,
        onNameChange: setProjectName,
        onRepositoryIdChange: setRepositoryId,
        onSelectProject: (projectId) => setSelectedProjectId(projectId),
        onCreate: createNewProject,
      }}
      localImport={{
        name: importName,
        path: localPath,
        status: importStatus,
        attempt: importAttempt,
        error: importError,
        onNameChange: setImportName,
        onPathChange: setLocalPath,
        onSubmit: importLocalProject,
        onRetry: retryLocalProjectImport,
      }}
      githubImport={{
        name: githubImportName,
        url: githubUrl,
        ref: githubRef,
        status: githubImportStatus,
        attempt: githubImportAttempt,
        error: githubImportError,
        onNameChange: setGithubImportName,
        onUrlChange: setGithubUrl,
        onRefChange: setGithubRef,
        onSubmit: importGitHubProject,
        onRetry: retryGitHubProjectImport,
        onRetryCleanup: retryGitHubCleanup,
      }}
      index={{
        response: indexResponse,
        indexes: projectIndexes,
        status: indexStatus,
        error: indexError,
        revision,
        dirty,
        indexIdInput,
        onRevisionChange: setRevision,
        onDirtyChange: setDirty,
        onIndexIdChange: setIndexIdInput,
        onCreate: createNewIndex,
        onOpen: openExistingIndex,
      }}
      session={{
        sessions,
        cursor: sessionCursor,
        hasMore: sessionHasMore,
        status: sessionStatus,
        error: sessionError,
        selectedSessionId,
        onSelectSession: selectSession,
        onLoadMore: () => {
          if (selectedProjectId) {
            void loadSessions(selectedProjectId, sessionCursor, true);
          }
        },
        topic: sessionTopic,
        onTopicChange: (value) => {
          setSessionTopic(value);
          setSessionError(null);
        },
        writeStatus: sessionWriteStatus,
        onCreate: createSession,
        messages: sessionMessages,
        messageCursor: sessionMessagesCursor,
        messagesHasMore: sessionMessagesHasMore,
        messagesStatus: sessionMessagesStatus,
        messagesError: sessionMessagesError,
        onLoadMoreMessages: () => {
          if (selectedProjectId && selectedSessionId) {
            void loadSessionMessages(selectedProjectId, selectedSessionId, sessionMessagesCursor, true);
          }
        },
        onRetryMessages: () => {
          if (selectedProjectId && selectedSessionId) {
            void loadSessionMessages(selectedProjectId, selectedSessionId);
          }
        },
        draft: sessionDraft,
        onDraftChange: (value) => {
          updateSessionDraft(value);
          setSessionWriteError(null);
          const pending = sessionMessageRequest.current;
          if (pending && pending.content !== value.trim()) {
            sessionMessageRequest.current = null;
          }
        },
        onSendMessage: () => sendSessionMessage(),
      }}
      knowledge={{
        searchQuery,
        searchStatus,
        searchResults,
        searchError,
        onSearchQueryChange: setSearchQuery,
        onSearch: runSearch,
        onSelectSearchResult: handleSearchResult,
        overviewStatus,
        overview,
        overviewError,
        selectedOverviewEntryId,
        onSelectOverviewEntry: selectOverviewEntry,
        onOpenOverviewGraph: openOverviewEntryGraph,
        onOpenOverviewModule: openOverviewModule,
        onRetryOverview: () => {
          if (selectedProjectId && indexResponse) {
            void loadOverview(selectedProjectId, indexResponse.index.index_id);
          }
        },
        featureStatus,
        featureGeneration,
        featureError,
        featureHistory,
        featureHistoryStatus,
        featureHistoryError,
        featureHistoryHasMore,
        onLoadMoreFeatureHistory: () => {
          if (selectedProjectId && indexResponse && featureHistoryCursor) {
            void loadFeatureHistory(selectedProjectId, indexResponse.index.index_id, featureHistoryCursor, true);
          }
        },
        onRetryFeatureHistory: () => {
          if (selectedProjectId && indexResponse) {
            void loadFeatureHistory(selectedProjectId, indexResponse.index.index_id);
          }
        },
        selectedFeatureGenerationId,
        onSelectFeatureGeneration: selectFeatureGeneration,
        featureMode,
        featureSourceScope,
        onFeatureModeChange: (mode) => resetFeatureRequest(() => setFeatureMode(mode)),
        onFeatureSourceScopeChange: (scope) => resetFeatureRequest(() => setFeatureSourceScope(scope)),
        onRunFeatureGeneration: runFeatureGeneration,
        onReloadFeatureGeneration: loadSavedFeatureGeneration,
        onOpenFeatureGraph: () => {
          if (featureGeneration) {
            setViewMode("overview");
            setPanelMode("feature_graph");
          }
        },
        onSelectFeatureEvidence: selectFeatureEvidence,
      }}
      evidence={{
        projectId: selectedProjectId,
        index: indexIdentity ?? null,
        generationId: sourceGenerationId,
        selectedSymbol,
        sourceStatus,
        sourceResponse,
        sourceError,
        sourceWindowStart,
        sourceHasPrevious: Boolean(sourceHasPrevious),
        sourceHasNext: Boolean(sourceHasNext),
        connectedEdges,
        selectedUnresolved,
        onLoadSource: loadSource,
        onRetrySource: () => {
          if (selectedSymbol) {
            void loadSource(selectedSymbol, sourceWindowStart);
          }
        },
        onExpandRelation: () => {
          if (selectedSymbol) {
            setViewMode("overview");
            setPanelMode("graph");
            void loadNeighborhood(selectedSymbol.index_id, selectedSymbol.symbol_id, false);
          }
        },
        onClose: () => setPanelMode("none"),
      }}
      graph={{
        panelMode: panelMode === "feature_graph" ? "feature_graph" : "graph",
        codeModel: flowModel,
        codeStatus: graphStatus,
        codeError: graphError,
        codeTruncated: graph.truncated,
        codeTruncationReason: graph.truncationReason,
        codeSymbolCount: graph.symbols.length,
        codeEdgeCount: graph.edges.length,
        featureModel: featureFlowModel,
        featureStatus: featureGraphStatus,
        featureError: featureGraphError,
        featureGraph,
        featureGeneration,
        direction,
        edgeKinds,
        onToggleEdgeKind: toggleEdgeKind,
        onChangeDirection: changeDirection,
        onCodeNodeClick: (nodeId) => {
          const symbol = graph.symbols.find((candidate) => candidate.symbol_id === nodeId);
          if (symbol) {
            selectSymbol(symbol);
          }
        },
        onFeatureNodeClick: (nodeId) => {
          const node = featureGraph?.nodes.find((candidate) => candidate.node_id === nodeId);
          if (node) {
            selectFeatureGraphNode(node);
          }
        },
        onCodeInit: setFlowInstance,
        onFeatureInit: setFeatureFlowInstance,
        codeNodeTypes: nodeTypes,
        featureNodeTypes,
        flowInstance,
        featureFlowInstance,
        selectedId,
        onPanelModeChange: setPanelMode,
        onClose: () => setPanelMode("none"),
      }}
      viewMode={viewMode}
      onViewModeChange={setViewMode}
      panelMode={panelMode}
      onPanelModeChange={setPanelMode}
    />
  );
}
