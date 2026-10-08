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
  IMPORT_STATUS_REQUEST_TIMEOUT_MS,
  RequestTimeoutError,
  createProjectIndex,
  checkProjectIndexFreshness,
  generateProjectFeatures,
  getHealth,
  getImportStatus,
  getProjectNeighbors,
  getProjectOverview,
  getProjectFeatureGraph,
  getProjectFeatureGeneration,
  getProjectSessionRunEventsStreamUrl,
  importGitHubRepository,
  importLocalPath,
  appendProjectSessionMessage,
  cancelProjectSessionRun,
  createProjectSessionRun,
  createProjectSession,
  getProjectSession,
  getProjectSessionRun,
  getProjectSessionRunImpact,
  getProjectSessionRunReport,
  listProjectIndexes,
  listProjectFeatureGenerations,
  listProjectModuleSymbols,
  listProjectSessionMessages,
  listProjectSessionRunEvents,
  listProjectSessionRunToolResults,
  listProjectSessionRuns,
  listProjectSessions,
  listProjects,
  listTrash,
  moveProjectToTrash,
  moveSessionToTrash,
  restoreProject,
  restoreSession,
  isHealthResponse,
  openProjectIndex,
  readProjectSource,
  retryLocalImport,
  retryGitHubImport,
  retryImportCleanup,
  refreshGitHubSource,
  refreshProjectIndex,
  searchProjectIndex,
  type CodeEdge,
  type ContextualCall,
  type EdgeKind,
  type Evidence,
  type FeatureGeneration,
  type FeatureGenerationSummary,
  type FeatureGraph,
  type FeatureGraphNode,
  type FeatureGraphOrigin,
  type FeatureGraphRelation,
  type FeatureSourceScope,
  type HealthResponse,
  type ImpactDirection,
  type ImportStatus,
  type ImportAttemptResponse,
  type IndexFreshness,
  type IndexRefreshMode,
  type IndexResponse,
  type ImportAttempt,
  type GitHubImportResponse,
  type LocalImportResponse,
  type NeighborDirection,
  type OverviewEntryPoint,
  type OverviewHttpAssociation,
  type OverviewGroup,
  type OverviewModule,
  type ModuleSymbolsPageResponse,
  type ProjectOverview,
  type ProjectRecord,
  type TrashListResponse,
  type RunEvent,
  type RunReport,
  type RunImpactResponse,
  type RunStatusResponse,
  type RunToolResult,
  type RunRecord,
  type SearchMatch,
  type Session,
  type SessionMessage,
  type SessionMessageSource,
  type SourceResponse,
  type SourceLocation,
  type SymbolRecord,
} from "../lib/api";
import {
  createRunEventStream,
  type RunEventStreamController,
  type RunStreamStatus,
} from "../lib/run-event-stream";
import {
  finishImportSubmissionKey,
  isImportStatusTerminal,
  nextImportSubmissionKey,
  waitForImportStatus,
} from "../lib/import-status";
import {
  emptyGraphState,
  edgeKindLabel,
  directImportTargets,
  groupSymbolsByDirectory,
  hasDirectedCallCycle,
  isHttpCallChainResponse,
  isLatestRequest,
  layoutSymbolGroups,
  mergeContextualCalls,
  mergeNeighborGraph,
  unresolvedForSymbol,
  type DirectImportTarget,
  type GraphState,
} from "../lib/graph";
import {
  nextSessionDraftRevision,
  shouldClearSubmittedSessionDraft,
  type SessionDraftSubmission,
} from "../lib/session-draft";
import { readWorkbenchRoute, resolveWorkbenchProject, workbenchLocation } from "../lib/workbench-navigation";
import WorkbenchUi from "./workbench-ui";

type IndexUiStatus = "unindexed" | "creating" | "loading" | "ready" | "partial" | "error";
type AsyncStatus = "idle" | "loading" | "success" | "empty" | "error";
type WorkbenchView = "overview" | "session";
type WorkbenchPanel = "none" | "source" | "graph" | "feature_graph" | "impact";

type ImpactSelection = {
  candidateId?: string;
  findingId?: string;
};

type HttpEntryChain = {
  association: OverviewHttpAssociation;
  handler: SymbolRecord;
};

type SymbolNodeData = {
  symbol: SymbolRecord;
  selected: boolean;
  onSelect: (symbolId: string) => void;
  onExpand: ((symbolId: string) => void) | null;
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

const AUTO_EXPAND_GROUP_SYMBOL_LIMIT = 12;
const FOCUS_NODE_LIMIT = 8;

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
      {data.onExpand ? <button
          type="button"
          className="flow-expand-button nodrag"
          aria-label={`展开 ${data.symbol.qualified_name} 的关系`}
          onClick={(event) => {
            event.stopPropagation();
            data.onExpand?.(data.symbol.symbol_id);
          }}
        >
          +
        </button> : null}
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

function isUncertainImportRequestError(error: unknown): boolean {
  return error instanceof RequestTimeoutError || error instanceof TypeError;
}

function importRequestErrorMessage(error: unknown): string {
  return isUncertainImportRequestError(error)
    ? "连接中断，服务端导入可能仍在处理。已有 attempt 可刷新状态；初次请求可用相同内容重新连接。"
    : errorMessage(error);
}

function localImportErrorMessage(message: string): string {
  return message.includes("local import path is outside allowed roots")
    ? "API 当前未开放这个目录。请在 API 设置中加入本地可导入目录，重启 API 后再试。"
    : message;
}

async function importIndexFromStatus(
  status: ImportAttemptResponse,
  signal: AbortSignal,
): Promise<IndexResponse | null> {
  const indexId = status.attempt.index_id;
  if (status.attempt.status !== "ready" || !indexId) {
    return null;
  }
  if (status.latest_index?.index.index_id === indexId) {
    return status.latest_index;
  }
  return openProjectIndex(status.project.project_id, indexId, signal, IMPORT_STATUS_REQUEST_TIMEOUT_MS);
}

async function localImportResponseFromStatus(
  status: ImportAttemptResponse,
  signal: AbortSignal,
): Promise<LocalImportResponse> {
  return {
    schema_version: status.schema_version,
    source_path: status.source_path,
    project: status.project,
    attempt: status.attempt,
    index: await importIndexFromStatus(status, signal),
  };
}

async function githubImportResponseFromStatus(
  status: ImportAttemptResponse,
  signal: AbortSignal,
): Promise<GitHubImportResponse> {
  const { attempt } = status;
  return {
    schema_version: status.schema_version,
    source_url: attempt.source_url ?? status.source_path,
    requested_ref: attempt.requested_ref,
    resolved_ref: attempt.resolved_ref,
    resolved_commit: attempt.resolved_commit,
    content_hash: attempt.content_hash,
    cleanup_status: attempt.cleanup_status,
    cleanup_error: attempt.cleanup_error,
    project: status.project,
    attempt,
    index: await importIndexFromStatus(status, signal),
  };
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

function featureSelectionStorageKey(projectId: string, indexId: string): string {
  return `codetrail.selectedFeatureGenerationId:${projectId}:${indexId}`;
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

function newImportIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `import-${crypto.randomUUID()}`;
  }
  return `import-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function Workbench() {
  const [connectionState, setConnectionState] = useState<"checking" | "connected" | "error">("checking");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [projectStatus, setProjectStatus] = useState<AsyncStatus>("idle");
  const [projectError, setProjectError] = useState<string | null>(null);
  const [trash, setTrash] = useState<TrashListResponse | null>(null);
  const [trashStatus, setTrashStatus] = useState<AsyncStatus>("idle");
  const [trashError, setTrashError] = useState<string | null>(null);
  const [trashMutationBusy, setTrashMutationBusy] = useState(false);
  const [importName, setImportName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [importAttempt, setImportAttempt] = useState<ImportAttempt | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [githubImportName, setGithubImportName] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [githubRef, setGithubRef] = useState("");
  const [githubImportStatus, setGithubImportStatus] = useState<ImportStatus | null>(null);
  const [githubImportAttempt, setGithubImportAttempt] = useState<ImportAttempt | null>(null);
  const [githubImportError, setGithubImportError] = useState<string | null>(null);
  const [projectIndexes, setProjectIndexes] = useState<IndexResponse["index"][]>([]);
  const [indexResponse, setIndexResponse] = useState<IndexResponse | null>(null);
  const [indexStatus, setIndexStatus] = useState<IndexUiStatus>("unindexed");
  const [indexError, setIndexError] = useState<string | null>(null);
  const [indexFreshness, setIndexFreshness] = useState<IndexFreshness | null>(null);
  const [indexFreshnessStatus, setIndexFreshnessStatus] = useState<"idle" | "checking" | "refreshing">("idle");
  const [indexFreshnessError, setIndexFreshnessError] = useState<string | null>(null);
  const [indexRefreshMode, setIndexRefreshMode] = useState<IndexRefreshMode | null>(null);
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
  const [graphIndexId, setGraphIndexId] = useState<string | null>(null);
  const [contextualCalls, setContextualCalls] = useState<ContextualCall[]>([]);
  const [viewMode, setViewMode] = useState<WorkbenchView>("overview");
  const [panelMode, setPanelMode] = useState<WorkbenchPanel>("none");
  const [architectureGraphRootId, setArchitectureGraphRootId] = useState<string | null>(null);
  const [overviewStatus, setOverviewStatus] = useState<AsyncStatus>("idle");
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [moduleSymbolsRoot, setModuleSymbolsRoot] = useState<SymbolRecord | null>(null);
  const [moduleSymbolsPage, setModuleSymbolsPage] = useState<ModuleSymbolsPageResponse | null>(null);
  const [moduleSymbolsStatus, setModuleSymbolsStatus] = useState<AsyncStatus>("idle");
  const [moduleSymbolsError, setModuleSymbolsError] = useState<string | null>(null);
  const [moduleSymbolsRetry, setModuleSymbolsRetry] = useState<{ cursor: string | null; append: boolean } | null>(null);
  const [selectedOverviewEntryId, setSelectedOverviewEntryId] = useState<string | null>(null);
  const [selectedOverviewGroupId, setSelectedOverviewGroupId] = useState<string | null>(null);
  const [httpEntryChain, setHttpEntryChain] = useState<HttpEntryChain | null>(null);
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
  const [runHistory, setRunHistory] = useState<RunRecord[]>([]);
  const [runHistoryStatus, setRunHistoryStatus] = useState<AsyncStatus>("idle");
  const [runHistoryError, setRunHistoryError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runStatusResponse, setRunStatusResponse] = useState<RunStatusResponse | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [runToolResults, setRunToolResults] = useState<RunToolResult[]>([]);
  const [runReport, setRunReport] = useState<RunReport | null>(null);
  const [runStatus, setRunStatus] = useState<AsyncStatus>("idle");
  const [runError, setRunError] = useState<string | null>(null);
  const [runStreamStatus, setRunStreamStatus] = useState<RunStreamStatus>("idle");
  const [runStreamError, setRunStreamError] = useState<string | null>(null);
  const [runWriteStatus, setRunWriteStatus] = useState<AsyncStatus>("idle");
  const [runWriteError, setRunWriteError] = useState<string | null>(null);
  const [runImpact, setRunImpact] = useState<RunImpactResponse | null>(null);
  const [runImpactStatus, setRunImpactStatus] = useState<AsyncStatus>("idle");
  const [runImpactError, setRunImpactError] = useState<string | null>(null);
  const [runImpactDirection, setRunImpactDirection] = useState<ImpactDirection>("callers");
  const [runImpactDepth, setRunImpactDepth] = useState(2);
  const [runImpactSelection, setRunImpactSelection] = useState<ImpactSelection | null>(null);
  const [direction, setDirection] = useState<NeighborDirection>("both");
  const [edgeKinds, setEdgeKinds] = useState<EdgeKind[]>(["calls", "imports"]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const [featureFlowInstance, setFeatureFlowInstance] = useState<ReactFlowInstance<Node, Edge> | null>(null);

  const activeSessionDraftKey = selectedProjectId && selectedSessionId
    ? sessionDraftStorageKey(selectedProjectId, selectedSessionId)
    : null;
  const selectedProjectIdRef = useRef(selectedProjectId);
  selectedProjectIdRef.current = selectedProjectId;
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;
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
  const trashController = useRef<AbortController | null>(null);
  const trashMutationInFlight = useRef(false);
  const importController = useRef<AbortController | null>(null);
  const importActionInFlight = useRef(false);
  const importNavigationId = useRef(0);
  const localImportIdempotency = useRef<{ signature: string; key: string } | null>(null);
  const githubImportIdempotency = useRef<{ signature: string; key: string } | null>(null);
  const indexController = useRef<AbortController | null>(null);
  const searchController = useRef<AbortController | null>(null);
  const graphController = useRef<AbortController | null>(null);
  const sourceController = useRef<AbortController | null>(null);
  const overviewController = useRef<AbortController | null>(null);
  const moduleSymbolsController = useRef<AbortController | null>(null);
  const featureController = useRef<AbortController | null>(null);
  const featureHistoryController = useRef<AbortController | null>(null);
  const featureGraphController = useRef<AbortController | null>(null);
  const sessionController = useRef<AbortController | null>(null);
  const sessionOpenController = useRef<AbortController | null>(null);
  const sessionMessagesController = useRef<AbortController | null>(null);
  const sessionWriteController = useRef<AbortController | null>(null);
  const runController = useRef<AbortController | null>(null);
  const runHistoryController = useRef<AbortController | null>(null);
  const runDiscoveryController = useRef<AbortController | null>(null);
  const runWriteController = useRef<AbortController | null>(null);
  const runImpactController = useRef<AbortController | null>(null);
  const indexRequestId = useRef(0);
  const projectRequestId = useRef(0);
  const trashRequestId = useRef(0);
  const importRequestId = useRef(0);
  const searchRequestId = useRef(0);
  const graphRequestId = useRef(0);
  const sourceRequestId = useRef(0);
  const overviewRequestId = useRef(0);
  const moduleSymbolsRequestId = useRef(0);
  const featureRequestId = useRef(0);
  const featureHistoryRequestId = useRef(0);
  const featureGraphRequestId = useRef(0);
  const sessionRequestId = useRef(0);
  const sessionOpenRequestId = useRef(0);
  const sessionMessagesRequestId = useRef(0);
  const sessionWriteRequestId = useRef(0);
  const runRequestId = useRef(0);
  const runHistoryRequestId = useRef(0);
  const runDiscoveryRequestId = useRef(0);
  const runWriteRequestId = useRef(0);
  const runImpactRequestId = useRef(0);
  const featureIdempotencyKey = useRef<string | null>(null);
  const sessionCreateRequest = useRef<{ projectId: string; topic: string; key: string } | null>(null);
  const sessionMessageRequest = useRef<{
    projectId: string;
    sessionId: string;
    content: string;
    key: string;
    draftRevision: number;
  } | null>(null);
  const runCreateRequest = useRef<{
    projectId: string;
    sessionId: string;
    key: string;
  } | null>(null);
  const runPollTimer = useRef<number | null>(null);
  const runDiscoveryTimer = useRef<number | null>(null);
  const runEventStreamController = useRef<RunEventStreamController | null>(null);
  const runStreamRequestId = useRef(0);
  const runStreamLastSequence = useRef(0);
  const runDetailsLoader = useRef<((projectId: string, sessionId: string, runId: string, useStream: boolean) => void) | null>(null);
  const knownGroupKeys = useRef<Set<string>>(new Set());
  const evidenceLink = useRef<EvidenceLink | null>(null);
  const evidenceLinkApplied = useRef(false);
  const sessionLink = useRef<SessionLink | null>(null);
  const sessionLinkApplied = useRef(false);

  const invalidateImportNavigation = useCallback(() => {
    importNavigationId.current += 1;
  }, []);
  const replaceWorkbenchLocation = useCallback((projectId: string, sessionId?: string) => {
    window.history.replaceState(null, "", workbenchLocation(window.location.href, projectId, sessionId));
  }, []);
  const changeWorkbenchView = useCallback((mode: WorkbenchView) => {
    setViewMode(mode);
    if (mode === "overview") {
      sessionLink.current = null;
      sessionLinkApplied.current = true;
      if (selectedProjectId) {
        replaceWorkbenchLocation(selectedProjectId);
      }
    }
  }, [replaceWorkbenchLocation, selectedProjectId]);

  const resetModuleSymbolSource = useCallback(() => {
    sourceController.current?.abort();
    sourceRequestId.current += 1;
    setSelectedId(null);
    setLinkedSymbol(null);
    setSourceStatus("idle");
    setSourceResponse(null);
    setSourceError(null);
    setSourceGenerationId(null);
    setPanelMode("none");
  }, []);

  const resetModuleSymbols = useCallback(() => {
    moduleSymbolsController.current?.abort();
    moduleSymbolsRequestId.current += 1;
    setModuleSymbolsRoot(null);
    setModuleSymbolsPage(null);
    setModuleSymbolsStatus("idle");
    setModuleSymbolsError(null);
    setModuleSymbolsRetry(null);
    resetModuleSymbolSource();
  }, [resetModuleSymbolSource]);

  const closeRunEventStream = useCallback(() => {
    runStreamRequestId.current += 1;
    runEventStreamController.current?.close();
    runEventStreamController.current = null;
  }, []);

  const stopExternalRunDiscovery = useCallback(() => {
    runDiscoveryController.current?.abort();
    runDiscoveryController.current = null;
    runDiscoveryRequestId.current += 1;
    if (runDiscoveryTimer.current !== null) {
      window.clearTimeout(runDiscoveryTimer.current);
      runDiscoveryTimer.current = null;
    }
  }, []);

  const resetRunImpact = useCallback(() => {
    runImpactController.current?.abort();
    runImpactRequestId.current += 1;
    setRunImpact(null);
    setRunImpactStatus("idle");
    setRunImpactError(null);
    setRunImpactSelection(null);
  }, []);

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
      const route = readWorkbenchRoute(window.location.search);
      evidenceLink.current = parsedEvidenceLink.link;
      evidenceLinkApplied.current = false;
      sessionLink.current = route.error || parsedEvidenceLink.error || !route.projectId || !route.sessionId
        ? null
        : { projectId: route.projectId, sessionId: route.sessionId };
      sessionLinkApplied.current = sessionLink.current === null;
      const linkError = parsedEvidenceLink.error ?? route.error;
      const conflictingLinks = parsedEvidenceLink.link && route.sessionId !== null;
      const resolution = resolveWorkbenchProject(
        route,
        parsedEvidenceLink.link?.projectId ?? null,
        response.projects.map((project) => project.project_id),
        window.localStorage.getItem("codetrail.selectedProjectId"),
      );
      if (conflictingLinks) {
        setProjectStatus("error");
        setProjectError("工作台链接同时包含源码证据和会话引用，已拒绝打开。请只保留一种引用。");
      } else if (linkError) {
        setProjectStatus("error");
        setProjectError(linkError);
      }
      const nextProject = linkError || conflictingLinks
        ? null
        : response.projects.find((project) => project.project_id === resolution.projectId) ?? null;
      if (resolution.requestedProjectId && !nextProject) {
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

  const loadTrash = useCallback(async () => {
    trashController.current?.abort();
    const controller = new AbortController();
    trashController.current = controller;
    const requestId = ++trashRequestId.current;
    setTrashStatus("loading");
    setTrashError(null);
    try {
      const response = await listTrash(controller.signal);
      if (!isLatestRequest(requestId, trashRequestId.current)) {
        return;
      }
      setTrash(response);
      setTrashStatus(response.projects.length + response.sessions.length > 0 ? "success" : "empty");
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, trashRequestId.current)) {
        return;
      }
      setTrashStatus("error");
      setTrashError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void checkHealth();
    return () => {
      healthController.current?.abort();
      projectController.current?.abort();
      trashController.current?.abort();
      importController.current?.abort();
      indexController.current?.abort();
      searchController.current?.abort();
      graphController.current?.abort();
      sourceController.current?.abort();
      overviewController.current?.abort();
      moduleSymbolsController.current?.abort();
      featureController.current?.abort();
      featureHistoryController.current?.abort();
      featureGraphController.current?.abort();
      sessionController.current?.abort();
      sessionOpenController.current?.abort();
      sessionMessagesController.current?.abort();
      sessionWriteController.current?.abort();
      runController.current?.abort();
      runHistoryController.current?.abort();
      stopExternalRunDiscovery();
      runWriteController.current?.abort();
      runImpactController.current?.abort();
      closeRunEventStream();
      if (runPollTimer.current !== null) {
        window.clearTimeout(runPollTimer.current);
      }
      stopExternalRunDiscovery();
    };
  }, [checkHealth, closeRunEventStream, stopExternalRunDiscovery]);

  const clearExploration = useCallback(() => {
    searchController.current?.abort();
    graphController.current?.abort();
    sourceController.current?.abort();
    overviewController.current?.abort();
    resetModuleSymbols();
    featureController.current?.abort();
    featureHistoryController.current?.abort();
    featureGraphController.current?.abort();
    runImpactController.current?.abort();
    searchRequestId.current += 1;
    graphRequestId.current += 1;
    sourceRequestId.current += 1;
    overviewRequestId.current += 1;
    featureHistoryRequestId.current += 1;
    featureGraphRequestId.current += 1;
    runImpactRequestId.current += 1;
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
    setGraphIndexId(null);
    setContextualCalls([]);
    setViewMode("overview");
    setPanelMode("none");
    setOverviewStatus("idle");
    setOverview(null);
    setOverviewError(null);
    setSelectedOverviewEntryId(null);
    setSelectedOverviewGroupId(null);
    setHttpEntryChain(null);
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
    setRunImpact(null);
    setRunImpactStatus("idle");
    setRunImpactError(null);
    setRunImpactSelection(null);
    setExpandedGroups(new Set());
    knownGroupKeys.current.clear();
  }, [resetModuleSymbols]);

  const loadLinkedEvidence = useCallback(
    async (link: EvidenceLink, generationId: string | null = null) => {
      sourceController.current?.abort();
      const controller = new AbortController();
      sourceController.current = controller;
      const requestId = ++sourceRequestId.current;
      const linked = {
        symbol_id: `evidence-${link.evidenceId}`,
        index_id: link.indexId,
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
          link.indexId,
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
    resetModuleSymbols();
    const controller = new AbortController();
    overviewController.current = controller;
    const requestId = ++overviewRequestId.current;
    setOverviewStatus("loading");
    setOverviewError(null);
    try {
      const response = await getProjectOverview(projectId, indexId, 4, 40, 100, 80, 200, controller.signal);
      if (!isLatestRequest(requestId, overviewRequestId.current)) {
        return;
      }
      setOverview(response);
      setSelectedOverviewEntryId(response.entries[0]?.entry_point_id ?? null);
      setSelectedOverviewGroupId(null);
      setOverviewStatus("success");
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, overviewRequestId.current)) {
        return;
      }
      setOverview(null);
      setOverviewStatus("error");
      setOverviewError(errorMessage(error));
    }
  }, [resetModuleSymbols]);

  const loadModuleSymbolsPage = useCallback(
    async (module: SymbolRecord, cursor: string | null = null, append = false) => {
      if (!selectedProjectId || !indexResponse || module.kind !== "module" || module.index_id !== indexResponse.index.index_id) {
        return;
      }
      if (append && !cursor) {
        return;
      }
      moduleSymbolsController.current?.abort();
      const controller = new AbortController();
      moduleSymbolsController.current = controller;
      const requestId = ++moduleSymbolsRequestId.current;
      setModuleSymbolsRoot(module);
      setModuleSymbolsRetry({ cursor, append });
      if (!append) {
        setModuleSymbolsPage(null);
      }
      setModuleSymbolsStatus("loading");
      setModuleSymbolsError(null);
      try {
        const response = await listProjectModuleSymbols(
          selectedProjectId,
          indexResponse.index.index_id,
          module.symbol_id,
          40,
          cursor ?? undefined,
          controller.signal,
        );
        if (
          !isLatestRequest(requestId, moduleSymbolsRequestId.current)
        ) {
          return;
        }
        if (
          response.project_id !== selectedProjectId ||
          response.index_id !== indexResponse.index.index_id ||
          response.module.symbol_id !== module.symbol_id ||
          response.module.path !== module.path
        ) {
          throw new Error("返回的模块定义与当前固定索引或模块不匹配。");
        }
        setModuleSymbolsPage((previous) => {
          if (!append || !previous || previous.module.symbol_id !== module.symbol_id) {
            return response;
          }
          const existing = new Set(previous.symbols.map((symbol) => symbol.symbol_id));
          return {
            ...response,
            symbols: [...previous.symbols, ...response.symbols.filter((symbol) => !existing.has(symbol.symbol_id))],
          };
        });
        setModuleSymbolsStatus(response.symbols.length > 0 || append ? "success" : "empty");
        setModuleSymbolsRetry(null);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, moduleSymbolsRequestId.current)) {
          return;
        }
        setModuleSymbolsStatus("error");
        setModuleSymbolsError(errorMessage(error));
      }
    },
    [indexResponse, selectedProjectId],
  );

  const openModuleSymbols = useCallback(
    (module: OverviewModule) => {
      if (moduleSymbolsRoot?.symbol_id !== module.symbol.symbol_id) {
        resetModuleSymbolSource();
      }
      void loadModuleSymbolsPage(module.symbol);
    },
    [loadModuleSymbolsPage, moduleSymbolsRoot?.symbol_id, resetModuleSymbolSource],
  );

  const loadMoreModuleSymbols = useCallback(() => {
    if (moduleSymbolsPage?.has_more && moduleSymbolsPage.next_cursor) {
      void loadModuleSymbolsPage(moduleSymbolsPage.module, moduleSymbolsPage.next_cursor, true);
    }
  }, [loadModuleSymbolsPage, moduleSymbolsPage]);

  const retryModuleSymbols = useCallback(() => {
    if (moduleSymbolsRoot) {
      void loadModuleSymbolsPage(
        moduleSymbolsRoot,
        moduleSymbolsRetry?.cursor ?? null,
        moduleSymbolsRetry?.append ?? false,
      );
    }
  }, [loadModuleSymbolsPage, moduleSymbolsRetry, moduleSymbolsRoot]);

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
            mode: "real",
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
    runController.current?.abort();
    runHistoryController.current?.abort();
    stopExternalRunDiscovery();
    runWriteController.current?.abort();
    closeRunEventStream();
    if (runPollTimer.current !== null) {
      window.clearTimeout(runPollTimer.current);
      runPollTimer.current = null;
    }
    sessionRequestId.current += 1;
    sessionOpenRequestId.current += 1;
    sessionMessagesRequestId.current += 1;
    sessionWriteRequestId.current += 1;
    runRequestId.current += 1;
    runHistoryRequestId.current += 1;
    runWriteRequestId.current += 1;
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
    setRunHistory([]);
    setRunHistoryStatus("idle");
    setRunHistoryError(null);
    setSelectedRunId(null);
    setRunStatusResponse(null);
    setRunEvents([]);
    setRunToolResults([]);
    setRunReport(null);
    setRunStatus("idle");
    setRunError(null);
    setRunStreamStatus("idle");
    setRunStreamError(null);
    setRunWriteStatus("idle");
    setRunWriteError(null);
    resetRunImpact();
    runCreateRequest.current = null;
  }, [closeRunEventStream, resetRunImpact, stopExternalRunDiscovery]);

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

  const refreshRunAfterStream = useCallback(async (projectId: string, sessionId: string, runId: string, streamRequestId: number) => {
    const controller = new AbortController();
    try {
      const [response, toolResultsResponse] = await Promise.all([
        getProjectSessionRun(projectId, sessionId, runId, controller.signal),
        listProjectSessionRunToolResults(projectId, sessionId, runId, 0, 40, controller.signal),
      ]);
      if (
        streamRequestId !== runStreamRequestId.current ||
        response.run.project_id !== projectId ||
        response.run.session_id !== sessionId ||
        response.run.run_id !== runId
      ) {
        return;
      }
      setRunStatusResponse(response);
      setRunToolResults(toolResultsResponse.results);
      setRunHistory((previous) => previous.map((run) => run.run_id === runId ? response.run : run));
      if (response.run.report_id) {
        try {
          const reportResponse = await getProjectSessionRunReport(projectId, sessionId, runId, controller.signal);
          if (streamRequestId === runStreamRequestId.current) {
            setRunReport(reportResponse.report);
          }
        } catch (error) {
          if (!isAbortError(error) && !(error instanceof ApiRequestError && error.code === "not_found")) {
            throw error;
          }
          if (streamRequestId === runStreamRequestId.current) {
            setRunReport(null);
          }
        }
      } else if (streamRequestId === runStreamRequestId.current) {
        setRunReport(null);
      }
      if (streamRequestId === runStreamRequestId.current) {
        setRunStatus("success");
      }
    } catch (error) {
      if (isAbortError(error) || streamRequestId !== runStreamRequestId.current) {
        return;
      }
      setRunStatus("error");
      setRunError(errorMessage(error));
    }
  }, []);

  const startRunEventStream = useCallback((projectId: string, sessionId: string, runId: string, afterSequence: number) => {
    closeRunEventStream();
    runStreamLastSequence.current = afterSequence;
    const streamRequestId = runStreamRequestId.current;
    const isCurrent = () => streamRequestId === runStreamRequestId.current;
    const controller = createRunEventStream({
      runId,
      afterSequence,
      getUrl: (sequence) => getProjectSessionRunEventsStreamUrl(projectId, sessionId, runId, sequence),
      onStatus: (status) => {
        if (isCurrent()) {
          setRunStreamStatus(status);
        }
      },
      onError: (message) => {
        if (isCurrent()) {
          setRunStreamError(message);
        }
      },
      onEvent: (event) => {
        if (!isCurrent()) {
          return;
        }
        runStreamLastSequence.current = event.sequence;
        setRunEvents((previous) => {
          if (previous.some((known) => known.sequence === event.sequence && known.run_id === runId)) {
            return previous;
          }
          return [...previous, event];
        });
      },
      onTerminal: () => {
        if (!isCurrent()) {
          return;
        }
        runEventStreamController.current = null;
        void refreshRunAfterStream(projectId, sessionId, runId, streamRequestId);
      },
      onFallback: () => {
        if (!isCurrent() || runPollTimer.current !== null) {
          return;
        }
        runPollTimer.current = window.setTimeout(() => {
          runPollTimer.current = null;
          runDetailsLoader.current?.(projectId, sessionId, runId, false);
        }, 450);
      },
    });
    runEventStreamController.current = controller;
    controller.start();
  }, [closeRunEventStream, refreshRunAfterStream]);

  const loadRunDetails = useCallback(async (projectId: string, sessionId: string, runId: string, useStream = true) => {
    if (runPollTimer.current !== null) {
      window.clearTimeout(runPollTimer.current);
      runPollTimer.current = null;
    }
    closeRunEventStream();
    runController.current?.abort();
    const controller = new AbortController();
    runController.current = controller;
    const requestId = ++runRequestId.current;
    setRunStatus((previous) => previous === "idle" ? "loading" : previous);
    setRunError(null);
    try {
      const response = await getProjectSessionRun(projectId, sessionId, runId, controller.signal);
      const [eventsResponse, toolResultsResponse] = await Promise.all([
        listProjectSessionRunEvents(projectId, sessionId, runId, 0, 100, controller.signal),
        listProjectSessionRunToolResults(projectId, sessionId, runId, 0, 40, controller.signal),
      ]);
      if (
        !isLatestRequest(requestId, runRequestId.current) ||
        response.run.project_id !== projectId ||
        response.run.session_id !== sessionId ||
        response.run.run_id !== runId
      ) {
        return;
      }
      setRunStatusResponse(response);
      setRunEvents(eventsResponse.events);
      setRunToolResults(toolResultsResponse.results);
      setRunHistory((previous) => previous.map((run) => run.run_id === runId ? response.run : run));
      if (response.run.status === "queued" || response.run.status === "running") {
        setRunStatus("loading");
        runStreamLastSequence.current = eventsResponse.events.at(-1)?.sequence ?? 0;
        if (useStream) {
          startRunEventStream(projectId, sessionId, runId, runStreamLastSequence.current);
        } else {
          setRunStreamStatus("fallback");
          runPollTimer.current = window.setTimeout(() => {
            runPollTimer.current = null;
            void loadRunDetails(projectId, sessionId, runId, false);
          }, 450);
        }
        return;
      }
      setRunStreamStatus("closed");
      setRunStreamError(null);
      if (response.run.report_id) {
        try {
          const reportResponse = await getProjectSessionRunReport(projectId, sessionId, runId, controller.signal);
          if (isLatestRequest(requestId, runRequestId.current)) {
            setRunReport(reportResponse.report);
          }
        } catch (error) {
          if (!isAbortError(error) && !(error instanceof ApiRequestError && error.code === "not_found")) {
            throw error;
          }
          setRunReport(null);
        }
      } else {
        setRunReport(null);
      }
      setRunStatus("success");
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, runRequestId.current)) {
        return;
      }
      setRunStatus("error");
      setRunError(errorMessage(error));
    }
  }, [closeRunEventStream, startRunEventStream]);

  runDetailsLoader.current = loadRunDetails;

  const loadRunImpact = useCallback(
    async (
      selection: ImpactSelection,
      requestedDirection = runImpactDirection,
      requestedDepth = runImpactDepth,
    ) => {
      if (!selectedProjectId || !selectedSessionId || !selectedRunId) {
        return;
      }
      if (!selection.candidateId && !selection.findingId) {
        setRunImpactStatus("error");
        setRunImpactError("影响查询缺少候选或发现标识。");
        return;
      }
      runImpactController.current?.abort();
      const controller = new AbortController();
      runImpactController.current = controller;
      const requestId = ++runImpactRequestId.current;
      setRunImpactSelection(selection);
      setRunImpactDirection(requestedDirection);
      setRunImpactDepth(requestedDepth);
      setPanelMode("impact");
      setRunImpactStatus("loading");
      setRunImpactError(null);
      setRunImpact(null);
      try {
        const response = await getProjectSessionRunImpact(
          selectedProjectId,
          selectedSessionId,
          selectedRunId,
          {
            ...(selection.candidateId ? { candidateId: selection.candidateId } : {}),
            ...(selection.findingId ? { findingId: selection.findingId } : {}),
            direction: requestedDirection,
            depth: requestedDepth,
            limit: 40,
          },
          controller.signal,
        );
        const expectedIndexId = runStatusResponse?.run.index_id ?? runReport?.index_id;
        if (
          !isLatestRequest(requestId, runImpactRequestId.current) ||
          response.project_id !== selectedProjectId ||
          response.session_id !== selectedSessionId ||
          response.run_id !== selectedRunId ||
          (expectedIndexId !== undefined && response.index_id !== expectedIndexId)
        ) {
          return;
        }
        setRunImpact(response);
        setRunImpactStatus(
          response.mapping_status === "mapped" && response.reachable_node_count > 0 ? "success" : "empty",
        );
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, runImpactRequestId.current)) {
          return;
        }
        setRunImpact(null);
        setRunImpactStatus("error");
        setRunImpactError(errorMessage(error));
      }
    },
    [runImpactDepth, runImpactDirection, runReport, runStatusResponse, selectedProjectId, selectedRunId, selectedSessionId],
  );

  const changeRunImpactDirection = useCallback(
    (nextDirection: ImpactDirection) => {
      setRunImpactDirection(nextDirection);
      if (runImpactSelection) {
        void loadRunImpact(runImpactSelection, nextDirection, runImpactDepth);
      }
    },
    [loadRunImpact, runImpactDepth, runImpactSelection],
  );

  const changeRunImpactDepth = useCallback(
    (nextDepth: number) => {
      setRunImpactDepth(nextDepth);
      if (runImpactSelection) {
        void loadRunImpact(runImpactSelection, runImpactDirection, nextDepth);
      }
    },
    [loadRunImpact, runImpactDirection, runImpactSelection],
  );

  const loadRunHistory = useCallback(async (projectId: string, sessionId: string) => {
    closeRunEventStream();
    resetRunImpact();
    runHistoryController.current?.abort();
    const controller = new AbortController();
    runHistoryController.current = controller;
    const requestId = ++runHistoryRequestId.current;
    setRunHistoryStatus("loading");
    setRunHistoryError(null);
    try {
      const response = await listProjectSessionRuns(projectId, sessionId, 40, undefined, controller.signal);
      if (
        !isLatestRequest(requestId, runHistoryRequestId.current) ||
        response.project_id !== projectId ||
        response.session_id !== sessionId
      ) {
        return;
      }
      setRunHistory(response.runs);
      setRunHistoryStatus(response.runs.length > 0 ? "success" : "empty");
      const selected = response.runs.find((run) => run.status === "queued" || run.status === "running") ?? response.runs[0] ?? null;
      setSelectedRunId(selected?.run_id ?? null);
      setRunStatusResponse(null);
      setRunEvents([]);
      setRunToolResults([]);
      setRunReport(null);
      setRunStatus(selected ? "loading" : "idle");
      if (selected) {
        void loadRunDetails(projectId, sessionId, selected.run_id);
      }
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, runHistoryRequestId.current)) {
        return;
      }
      setRunHistoryStatus("error");
      setRunHistoryError(errorMessage(error));
    }
  }, [closeRunEventStream, loadRunDetails, resetRunImpact]);

  const discoverExternalRun = useCallback(async (projectId: string, sessionId: string) => {
    runDiscoveryController.current?.abort();
    const controller = new AbortController();
    runDiscoveryController.current = controller;
    const requestId = ++runDiscoveryRequestId.current;
    try {
      const response = await listProjectSessionRuns(projectId, sessionId, 40, undefined, controller.signal);
      if (
        !isLatestRequest(requestId, runDiscoveryRequestId.current) ||
        response.project_id !== projectId ||
        response.session_id !== sessionId
      ) {
        return;
      }
      setRunHistory(response.runs);
      setRunHistoryStatus(response.runs.length > 0 ? "success" : "empty");
      const activeExternalRun = response.runs.find(
        (run) => run.execution_source === "codex" && (run.status === "queued" || run.status === "running"),
      );
      if (activeExternalRun && selectedRunId !== activeExternalRun.run_id) {
        setSelectedRunId(activeExternalRun.run_id);
        setRunStatusResponse(null);
        setRunEvents([]);
        setRunToolResults([]);
        setRunReport(null);
        setRunStatus("loading");
        resetRunImpact();
        void loadRunDetails(projectId, sessionId, activeExternalRun.run_id);
        return;
      }
      runDiscoveryTimer.current = window.setTimeout(() => {
        runDiscoveryTimer.current = null;
        void discoverExternalRun(projectId, sessionId);
      }, 1_000);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, runDiscoveryRequestId.current)) {
        return;
      }
      runDiscoveryTimer.current = window.setTimeout(() => {
        runDiscoveryTimer.current = null;
        void discoverExternalRun(projectId, sessionId);
      }, 2_000);
    }
  }, [loadRunDetails, resetRunImpact, selectedRunId]);

  useEffect(() => {
    if (
      viewMode !== "session" ||
      !selectedProjectId ||
      !selectedSessionId ||
      (runHistoryStatus !== "success" && runHistoryStatus !== "empty")
    ) {
      return;
    }
    void discoverExternalRun(selectedProjectId, selectedSessionId);
    return () => {
      stopExternalRunDiscovery();
    };
  }, [
    discoverExternalRun,
    runHistoryStatus,
    selectedProjectId,
    selectedSessionId,
    stopExternalRunDiscovery,
    viewMode,
  ]);

  const selectRun = useCallback((runId: string) => {
    if (!selectedProjectId || !selectedSessionId) {
      return;
    }
    setSelectedRunId(runId);
    resetRunImpact();
    setRunReport(null);
    void loadRunDetails(selectedProjectId, selectedSessionId, runId);
  }, [loadRunDetails, resetRunImpact, selectedProjectId, selectedSessionId]);

  const startSessionRun = useCallback(async () => {
    if (!selectedProjectId || !selectedSessionId) {
      setRunWriteStatus("error");
      setRunWriteError("请先选择一个会话。");
      return;
    }
    if (runWriteStatus === "loading") {
      return;
    }
    const selectedSession = sessions.find((session) => session.session_id === selectedSessionId);
    if (!selectedSession) {
      setRunWriteStatus("error");
      setRunWriteError("当前会话尚未载入，无法启动 Run。");
      return;
    }
    const activeRun = runHistory.find((run) => run.status === "queued" || run.status === "running");
    if (activeRun) {
      setSelectedRunId(activeRun.run_id);
      setRunWriteStatus("error");
      setRunWriteError("这个会话已有一个进行中的 Run，请等待完成或先取消。");
      void loadRunDetails(selectedProjectId, selectedSessionId, activeRun.run_id);
      return;
    }
    const previous = runCreateRequest.current;
    const key = previous?.projectId === selectedProjectId && previous.sessionId === selectedSessionId
      ? previous.key
      : newSessionIdempotencyKey();
    runCreateRequest.current = { projectId: selectedProjectId, sessionId: selectedSessionId, key };
    runWriteController.current?.abort();
    const controller = new AbortController();
    runWriteController.current = controller;
    const requestId = ++runWriteRequestId.current;
    setRunWriteStatus("loading");
    setRunWriteError(null);
    const savedContext = sessionMessages.map((message) => message.content).join("\n\n").trim();
    try {
      const response = await createProjectSessionRun(
        selectedProjectId,
        selectedSessionId,
        {
          indexId: selectedSession.index.index_id,
          issueId: `session-${selectedSessionId}`,
          title: selectedSession.topic,
          body: (savedContext || selectedSession.topic).slice(0, 20_000),
          mode: "real",
          idempotencyKey: key,
        },
        controller.signal,
      );
      if (!isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      if (response.project_id !== selectedProjectId || response.session_id !== selectedSessionId) {
        throw new Error("返回的 Run 不属于当前项目或会话，已拒绝载入。");
      }
      setSelectedRunId(response.run_id);
      setRunWriteStatus("success");
      setRunWriteError(null);
      await loadRunHistory(selectedProjectId, selectedSessionId);
      if (!isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      setSelectedRunId(response.run_id);
      void loadRunDetails(selectedProjectId, selectedSessionId, response.run_id);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      setRunWriteStatus("error");
      setRunWriteError(errorMessage(error));
    }
  }, [loadRunDetails, loadRunHistory, runHistory, runWriteStatus, selectedProjectId, selectedSessionId, sessionMessages, sessions]);

  const cancelSelectedRun = useCallback(async () => {
    if (!selectedProjectId || !selectedSessionId || !selectedRunId) {
      return;
    }
    const selectedRun = runStatusResponse?.run ?? runHistory.find((run) => run.run_id === selectedRunId) ?? null;
    if (!selectedRun || (selectedRun.status !== "queued" && selectedRun.status !== "running")) {
      return;
    }
    runWriteController.current?.abort();
    const controller = new AbortController();
    runWriteController.current = controller;
    const requestId = ++runWriteRequestId.current;
    setRunWriteStatus("loading");
    setRunWriteError(null);
    try {
      await cancelProjectSessionRun(
        selectedProjectId,
        selectedSessionId,
        selectedRunId,
        "cancelled from the workbench",
        controller.signal,
      );
      if (!isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      setRunWriteStatus("success");
      await loadRunHistory(selectedProjectId, selectedSessionId);
      if (!isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      void loadRunDetails(selectedProjectId, selectedSessionId, selectedRunId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, runWriteRequestId.current)) {
        return;
      }
      setRunWriteStatus("error");
      setRunWriteError(errorMessage(error));
    }
  }, [loadRunDetails, loadRunHistory, runHistory, runStatusResponse, selectedProjectId, selectedRunId, selectedSessionId]);

  const openSession = useCallback(
    async (projectId: string, sessionId: string, knownSession?: Session) => {
      sessionWriteController.current?.abort();
      sessionWriteRequestId.current += 1;
      runController.current?.abort();
      runHistoryController.current?.abort();
      stopExternalRunDiscovery();
      runWriteController.current?.abort();
      closeRunEventStream();
      runRequestId.current += 1;
      runHistoryRequestId.current += 1;
      runWriteRequestId.current += 1;
      if (runPollTimer.current !== null) {
        window.clearTimeout(runPollTimer.current);
        runPollTimer.current = null;
      }
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
        replaceWorkbenchLocation(projectId, sessionId);
        sessionLink.current = { projectId, sessionId };
        sessionLinkApplied.current = true;
        setSessionMessages([]);
        setSessionMessagesCursor(null);
        setSessionMessagesHasMore(false);
        void loadSessionMessages(projectId, sessionId);
        setRunHistory([]);
        setRunHistoryStatus("idle");
        setRunHistoryError(null);
        setSelectedRunId(null);
        setRunStatusResponse(null);
        setRunEvents([]);
        setRunToolResults([]);
        setRunReport(null);
        setRunStatus("idle");
        setRunError(null);
        setRunStreamStatus("idle");
        setRunStreamError(null);
        setRunWriteStatus("idle");
        setRunWriteError(null);
        resetRunImpact();
        runCreateRequest.current = null;
        void loadRunHistory(projectId, sessionId);
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, sessionOpenRequestId.current)) {
          return;
        }
        setSessionStatus("error");
        setSessionError(errorMessage(error));
      }
    },
    [closeRunEventStream, loadRunHistory, loadSessionMessages, replaceWorkbenchLocation, resetRunImpact, stopExternalRunDiscovery],
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
      invalidateImportNavigation();
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
    [invalidateImportNavigation, openSession, selectedProjectId, sessionTopic],
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
      if (!selectedProjectId) {
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
        selectedFeatureGenerationId,
      );
    },
    [loadLinkedEvidence, selectedFeatureGenerationId, selectedProjectId],
  );

  const applyIndexResponse = useCallback(
    (response: IndexResponse) => {
      setArchitectureGraphRootId(null);
      setIndexResponse(response);
      setIndexError(null);
      setIndexFreshness(null);
      setIndexFreshnessError(null);
      setIndexRefreshMode(null);
      setIndexStatus(
        response.index.status === "failed"
          ? "error"
          : response.truncated || response.unresolved_truncated === true || response.issues.length > 0
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
      setArchitectureGraphRootId(null);
      setIndexError(null);
      setIndexFreshness(null);
      setIndexFreshnessError(null);
      setIndexRefreshMode(null);
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
          await loadLinkedEvidence(requestedLink);
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
    const requested = sessionLink.current?.projectId === selectedProjectId ? sessionLink.current.sessionId : null;
    if (!requested) {
      sessionLinkApplied.current = true;
      return;
    }
    if (selectedSessionId === requested) {
      sessionLinkApplied.current = true;
      setViewMode("session");
      return;
    }
    if (sessionLinkApplied.current) {
      return;
    }
    sessionLinkApplied.current = true;
    const knownSession = sessions.find((session) => session.session_id === requested);
    void openSession(selectedProjectId, requested, knownSession);
  }, [indexResponse?.index.index_id, openSession, selectedProjectId, selectedSessionId, sessionStatus, sessions]);

  useEffect(() => {
    if (!selectedProjectId) {
      indexController.current?.abort();
      indexRequestId.current += 1;
      resetSessions();
      setProjectIndexes([]);
      setIndexResponse(null);
      setIndexStatus("unindexed");
      setIndexError(null);
      clearExploration();
      return;
    }
    const project = projects.find((candidate) => candidate.project_id === selectedProjectId);
    if (!project) {
      return;
    }
    window.localStorage.setItem("codetrail.selectedProjectId", project.project_id);
    sessionLinkApplied.current = sessionLink.current?.projectId !== project.project_id;
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

  const applyImportedProject = useCallback((
    project: ProjectRecord,
    index: IndexResponse | null,
    mayNavigate: boolean,
    keepOperationPanel = false,
  ) => {
    setProjects((previous) => [
      ...previous.filter((item) => item.project_id !== project.project_id),
      project,
    ]);
    if (mayNavigate) {
      setSelectedProjectId(project.project_id);
      setProjectStatus("success");
    }
    if (mayNavigate && index) {
      sessionLink.current = null;
      sessionLinkApplied.current = true;
      evidenceLink.current = null;
      evidenceLinkApplied.current = false;
      resetSessions();
      setViewMode("overview");
      replaceWorkbenchLocation(project.project_id);
    }
    if (mayNavigate && index) {
      if (selectedProjectId === project.project_id) {
        setProjectIndexes((previous) => [
          index.index,
          ...previous.filter((item) => item.index_id !== index.index.index_id),
        ]);
      }
      applyIndexResponse(index);
      if (keepOperationPanel) {
        setPanelMode(panelMode);
      }
    }
  }, [applyIndexResponse, panelMode, replaceWorkbenchLocation, resetSessions, selectedProjectId]);

  const applyImportResponse = useCallback((response: LocalImportResponse, navigationId: number) => {
    setImportAttempt(response.attempt);
    setImportStatus(response.attempt.status);
    setImportError(
      response.attempt.status === "ready"
        ? null
        : localImportErrorMessage(response.attempt.error_message ?? "本地导入未完成。"),
    );
    if (response.attempt.status === "ready") {
      applyImportedProject(response.project, response.index, navigationId === importNavigationId.current);
    }
  }, [applyImportedProject]);

  const applyGitHubImportResponse = useCallback((response: GitHubImportResponse, navigationId: number) => {
    setGithubImportAttempt(response.attempt);
    setGithubImportStatus(response.attempt.status);
    setGithubImportError(
      response.attempt.status === "ready"
        ? response.cleanup_status === "failed"
          ? response.cleanup_error ?? "索引已完成，但临时目录清理失败。"
          : null
      : response.attempt.error_message ?? "GitHub 导入未完成。",
    );
    if (response.attempt.status === "ready") {
      applyImportedProject(
        response.project,
        response.index,
        navigationId === importNavigationId.current,
        response.cleanup_status === "failed",
      );
    }
  }, [applyImportedProject]);

  const settleLocalImportResponse = useCallback(async (
    initial: LocalImportResponse,
    signal: AbortSignal,
    requestId: number,
    navigationId: number,
  ) => {
    applyImportResponse(initial, navigationId);
    const response = await waitForImportStatus(
      initial,
      async (statusSignal) => localImportResponseFromStatus(
        await getImportStatus(initial.attempt.attempt_id, statusSignal),
        statusSignal,
      ),
      signal,
    );
    if (!isLatestRequest(requestId, importRequestId.current)) {
      return false;
    }
    if (response !== initial) {
      applyImportResponse(response, navigationId);
    }
    if (navigationId !== importNavigationId.current) {
      return false;
    }
    if (!isImportStatusTerminal(response.attempt.status)) {
      setImportStatus(null);
      setImportError("本地导入仍在处理中。点击“刷新状态”继续查看。");
      return false;
    }
    return response.attempt.status === "ready";
  }, [applyImportResponse]);

  const settleGitHubImportResponse = useCallback(async (
    initial: GitHubImportResponse,
    signal: AbortSignal,
    requestId: number,
    navigationId: number,
  ) => {
    applyGitHubImportResponse(initial, navigationId);
    const response = await waitForImportStatus(
      initial,
      async (statusSignal) => githubImportResponseFromStatus(
        await getImportStatus(initial.attempt.attempt_id, statusSignal),
        statusSignal,
      ),
      signal,
    );
    if (!isLatestRequest(requestId, importRequestId.current)) {
      return false;
    }
    if (response !== initial) {
      applyGitHubImportResponse(response, navigationId);
    }
    if (navigationId !== importNavigationId.current) {
      return false;
    }
    if (!isImportStatusTerminal(response.attempt.status)) {
      setGithubImportStatus(null);
      setGithubImportError("GitHub 导入仍在处理中。点击“刷新状态”继续查看。");
      return false;
    }
    return response.attempt.status === "ready" && response.cleanup_status !== "failed";
  }, [applyGitHubImportResponse]);

  const refreshLocalProjectImportStatus = useCallback(async () => {
    if (!importAttempt || isImportStatusTerminal(importAttempt.status) || importActionInFlight.current) {
      return false;
    }
    importActionInFlight.current = true;
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    const navigationId = importNavigationId.current;
    setImportStatus(importAttempt.status);
    setImportError(null);
    try {
      const status = await getImportStatus(importAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      const response = await localImportResponseFromStatus(status, controller.signal);
      return await settleLocalImportResponse(response, controller.signal, requestId, navigationId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      setImportStatus(null);
      setImportError(localImportErrorMessage(importRequestErrorMessage(error)));
      return false;
    } finally {
      importActionInFlight.current = false;
    }
  }, [importAttempt, settleLocalImportResponse]);

  const refreshGitHubProjectImportStatus = useCallback(async () => {
    if (!githubImportAttempt || isImportStatusTerminal(githubImportAttempt.status) || importActionInFlight.current) {
      return false;
    }
    importActionInFlight.current = true;
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    const navigationId = importNavigationId.current;
    setGithubImportStatus(githubImportAttempt.status);
    setGithubImportError(null);
    try {
      const status = await getImportStatus(githubImportAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      const response = await githubImportResponseFromStatus(status, controller.signal);
      return await settleGitHubImportResponse(response, controller.signal, requestId, navigationId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      setGithubImportStatus(null);
      setGithubImportError(importRequestErrorMessage(error));
      return false;
    } finally {
      importActionInFlight.current = false;
    }
  }, [githubImportAttempt, settleGitHubImportResponse]);

  const importLocalProject = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = importName.trim();
      const path = localPath.trim();
      if (!name || !path) {
        setImportError("请填写项目名称和 API 所在电脑上的绝对目录路径。");
        setImportStatus("failed");
        return false;
      }
      if (importActionInFlight.current) {
        return false;
      }
      importActionInFlight.current = true;
      importController.current?.abort();
      const controller = new AbortController();
      importController.current = controller;
      const requestId = ++importRequestId.current;
      const navigationId = importNavigationId.current;
      const submissionKey = nextImportSubmissionKey(
        localImportIdempotency.current,
        JSON.stringify([name, path]),
        newImportIdempotencyKey,
      );
      localImportIdempotency.current = submissionKey;
      setImportStatus("queued");
      setImportAttempt(null);
      setImportError(null);
      try {
        const response = await importLocalPath(
          name,
          path,
          "working-tree",
          false,
          submissionKey.key,
          controller.signal,
        );
        localImportIdempotency.current = finishImportSubmissionKey(localImportIdempotency.current, false);
        if (!isLatestRequest(requestId, importRequestId.current)) {
          return false;
        }
        return await settleLocalImportResponse(response, controller.signal, requestId, navigationId);
      } catch (error) {
        localImportIdempotency.current = finishImportSubmissionKey(
          localImportIdempotency.current,
          isUncertainImportRequestError(error),
        );
        if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
          return false;
        }
        setImportStatus(isUncertainImportRequestError(error) ? null : "failed");
        setImportError(localImportErrorMessage(importRequestErrorMessage(error)));
        return false;
      } finally {
        importActionInFlight.current = false;
      }
    },
    [importName, localPath, settleLocalImportResponse],
  );

  const retryLocalProjectImport = useCallback(async () => {
    if (
      !importAttempt ||
      (importAttempt.status !== "failed" && importAttempt.status !== "interrupted") ||
      importActionInFlight.current
    ) {
      return false;
    }
    importActionInFlight.current = true;
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    const navigationId = importNavigationId.current;
    setImportStatus("queued");
    setImportError(null);
    try {
      const response = await retryLocalImport(importAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      return await settleLocalImportResponse(response, controller.signal, requestId, navigationId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      setImportStatus(isUncertainImportRequestError(error) ? null : "failed");
      setImportError(localImportErrorMessage(importRequestErrorMessage(error)));
      return false;
    } finally {
      importActionInFlight.current = false;
    }
  }, [importAttempt, settleLocalImportResponse]);

  const importGitHubProject = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = githubImportName.trim();
      const url = githubUrl.trim();
      const ref = githubRef.trim();
      if (!name || !url) {
        setGithubImportError("请输入项目名称和标准 GitHub HTTPS 仓库地址。" );
        setGithubImportStatus("failed");
        return false;
      }
      if (importActionInFlight.current) {
        return false;
      }
      importActionInFlight.current = true;
      importController.current?.abort();
      const controller = new AbortController();
      importController.current = controller;
      const requestId = ++importRequestId.current;
      const navigationId = importNavigationId.current;
      const submissionKey = nextImportSubmissionKey(
        githubImportIdempotency.current,
        JSON.stringify([name, url, ref]),
        newImportIdempotencyKey,
      );
      githubImportIdempotency.current = submissionKey;
      setGithubImportStatus("queued");
      setGithubImportAttempt(null);
      setGithubImportError(null);
      try {
        const response = await importGitHubRepository(
          name,
          url,
          ref || undefined,
          submissionKey.key,
          controller.signal,
        );
        githubImportIdempotency.current = finishImportSubmissionKey(githubImportIdempotency.current, false);
        if (!isLatestRequest(requestId, importRequestId.current)) {
          return false;
        }
        return await settleGitHubImportResponse(response, controller.signal, requestId, navigationId);
      } catch (error) {
        githubImportIdempotency.current = finishImportSubmissionKey(
          githubImportIdempotency.current,
          isUncertainImportRequestError(error),
        );
        if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
          return false;
        }
        setGithubImportStatus(isUncertainImportRequestError(error) ? null : "failed");
        setGithubImportError(importRequestErrorMessage(error));
        return false;
      } finally {
        importActionInFlight.current = false;
      }
    }, [githubImportName, githubRef, githubUrl, settleGitHubImportResponse],
  );

  const retryGitHubProjectImport = useCallback(async () => {
    if (
      !githubImportAttempt ||
      (githubImportAttempt.status !== "failed" && githubImportAttempt.status !== "interrupted") ||
      importActionInFlight.current
    ) {
      return false;
    }
    importActionInFlight.current = true;
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    const navigationId = importNavigationId.current;
    setGithubImportStatus("queued");
    setGithubImportError(null);
    try {
      const response = await retryGitHubImport(githubImportAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      return await settleGitHubImportResponse(response, controller.signal, requestId, navigationId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return false;
      }
      setGithubImportStatus(isUncertainImportRequestError(error) ? null : "failed");
      setGithubImportError(importRequestErrorMessage(error));
      return false;
    } finally {
      importActionInFlight.current = false;
    }
  }, [githubImportAttempt, settleGitHubImportResponse]);

  const retryGitHubCleanup = useCallback(async () => {
    if (!githubImportAttempt || githubImportAttempt.cleanup_status !== "failed" || importActionInFlight.current) {
      return;
    }
    importActionInFlight.current = true;
    importController.current?.abort();
    const controller = new AbortController();
    importController.current = controller;
    const requestId = ++importRequestId.current;
    const navigationId = importNavigationId.current;
    setGithubImportStatus("queued");
    setGithubImportError(null);
    try {
      const response = await retryImportCleanup(githubImportAttempt.attempt_id, controller.signal);
      if (!isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      applyGitHubImportResponse(response, navigationId);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, importRequestId.current)) {
        return;
      }
      setGithubImportStatus("ready");
      setGithubImportError(errorMessage(error));
    } finally {
      importActionInFlight.current = false;
    }
  }, [applyGitHubImportResponse, githubImportAttempt]);

  const createNewIndex = useCallback(
    async () => {
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
          "working-tree",
          false,
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
    [applyIndexResponse, selectedProjectId],
  );

  const openExistingIndex = useCallback(
    async (indexId: string) => {
      if (!selectedProjectId) {
        setIndexError("请先创建或切换到一个项目。");
        return;
      }
      if (!indexId) {
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
    [applyIndexResponse, selectedProjectId],
  );

  const checkCurrentIndexFreshness = useCallback(async () => {
    if (!selectedProjectId || !indexResponse) {
      setIndexFreshnessError("请先打开一个固定索引。");
      return;
    }
    indexController.current?.abort();
    const controller = new AbortController();
    indexController.current = controller;
    const requestId = ++indexRequestId.current;
    setIndexFreshnessStatus("checking");
    setIndexFreshnessError(null);
    try {
      const result = await checkProjectIndexFreshness(
        selectedProjectId,
        indexResponse.index.index_id,
        controller.signal,
      );
      if (!isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      setIndexFreshness(result);
      setIndexRefreshMode(null);
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      setIndexFreshnessError(errorMessage(error));
    } finally {
      if (isLatestRequest(requestId, indexRequestId.current)) {
        setIndexFreshnessStatus("idle");
      }
    }
  }, [indexResponse, selectedProjectId]);

  const refreshCurrentIndex = useCallback(async () => {
    if (!selectedProjectId || !indexResponse) {
      setIndexFreshnessError("请先打开一个固定索引。");
      return;
    }
    indexController.current?.abort();
    const controller = new AbortController();
    indexController.current = controller;
    const requestId = ++indexRequestId.current;
    setIndexFreshnessStatus("refreshing");
    setIndexFreshnessError(null);
    try {
      const result = await refreshProjectIndex(
        selectedProjectId,
        indexResponse.index.index_id,
        "working-tree",
        false,
        controller.signal,
      );
      if (!isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      applyIndexResponse(result.index);
      setIndexFreshness(result.freshness);
      setIndexRefreshMode(result.mode);
      setProjectIndexes((previous) => [
        result.index.index,
        ...previous.filter((index) => index.index_id !== result.index.index.index_id),
      ]);
      setProjects((previous) => previous.map((project) => (
        project.project_id === selectedProjectId
          ? {
              ...project,
              available_index_ids: [
                result.index.index.index_id,
                ...project.available_index_ids.filter((indexId) => indexId !== result.index.index.index_id),
              ],
              latest_index_id: result.index.index.index_id,
              building_index_id: null,
            }
          : project
      )));
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      setIndexFreshnessError(errorMessage(error));
    } finally {
      if (isLatestRequest(requestId, indexRequestId.current)) {
        setIndexFreshnessStatus("idle");
      }
    }
  }, [applyIndexResponse, indexResponse, selectedProjectId]);

  const refreshCurrentGitHubSource = useCallback(async () => {
    if (!selectedProjectId || !indexResponse || !indexFreshness?.remote_source_url) {
      setIndexFreshnessError("请先检查一个可追踪的 GitHub 来源。");
      return;
    }
    if (!["consistent", "changed"].includes(indexFreshness.remote_status)) {
      setIndexFreshnessError("GitHub 远端状态无法确定，请先重试检查。");
      return;
    }
    indexController.current?.abort();
    const controller = new AbortController();
    indexController.current = controller;
    const requestId = ++indexRequestId.current;
    setIndexFreshnessStatus("refreshing");
    setIndexFreshnessError(null);
    try {
      const result = await refreshGitHubSource(
        selectedProjectId,
        indexResponse.index.index_id,
        controller.signal,
      );
      if (!isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      applyIndexResponse(result.index);
      setIndexFreshness(result.freshness);
      setIndexRefreshMode(result.mode);
      setProjectIndexes((previous) => [
        result.index.index,
        ...previous.filter((index) => index.index_id !== result.index.index.index_id),
      ]);
      setProjects((previous) => previous.map((project) => (
        project.project_id === selectedProjectId
          ? {
              ...project,
              available_index_ids: [
                result.index.index.index_id,
                ...project.available_index_ids.filter((indexId) => indexId !== result.index.index.index_id),
              ],
              latest_index_id: result.index.index.index_id,
              building_index_id: null,
            }
          : project
      )));
    } catch (error) {
      if (isAbortError(error) || !isLatestRequest(requestId, indexRequestId.current)) {
        return;
      }
      setIndexFreshnessError(errorMessage(error));
    } finally {
      if (isLatestRequest(requestId, indexRequestId.current)) {
        setIndexFreshnessStatus("idle");
      }
    }
  }, [applyIndexResponse, indexFreshness, indexResponse, selectedProjectId]);

  const loadSource = useCallback(
    async (symbol: SymbolRecord, startLine = symbol.start_line) => {
      if (!selectedProjectId) {
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
          symbol.index_id,
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
    [selectedProjectId],
  );

  const selectSymbol = useCallback(
    (symbol: SymbolRecord) => {
      setSelectedId(symbol.symbol_id);
      setPanelMode("source");
      void loadSource(symbol);
    },
    [loadSource],
  );

  const selectModuleSymbol = useCallback(
    (symbol: SymbolRecord) => {
      setLinkedSymbol(symbol);
      selectSymbol(symbol);
    },
    [selectSymbol],
  );

  const openArchitectureSource = useCallback(
    async ({ indexId, path, startLine, endLine, expectedContentHash }: { indexId: string; path: string; startLine: number; endLine: number; expectedContentHash?: string }) => {
      if (!selectedProjectId) {
        return;
      }
      sourceController.current?.abort();
      const controller = new AbortController();
      sourceController.current = controller;
      const requestId = ++sourceRequestId.current;
      const symbol = {
        symbol_id: `architecture-file:${indexId}:${path}:${startLine}:${endLine}`,
        index_id: indexId,
        path,
        qualified_name: `${path}:L${startLine}–L${endLine}`,
        kind: "file",
        start_line: startLine,
        end_line: endLine,
      } satisfies SymbolRecord;
      setLinkedSymbol(symbol);
      setSelectedId(symbol.symbol_id);
      setPanelMode("source");
      setSourceGenerationId(null);
      setSourceWindowStart(startLine);
      setSourceStatus("loading");
      setSourceResponse(null);
      setSourceError(null);
      try {
        const response = await readProjectSource(
          selectedProjectId,
          indexId,
          path,
          startLine,
          Math.min(endLine, startLine + 199),
          controller.signal,
        );
        if (!isLatestRequest(requestId, sourceRequestId.current)) {
          return;
        }
        if (
          response.index_id !== indexId ||
          response.evidence.index_id !== indexId ||
          response.evidence.path !== path ||
          response.evidence.start_line > startLine ||
          response.evidence.end_line < startLine ||
          expectedContentHash !== undefined && response.evidence.content_hash !== expectedContentHash
        ) {
          throw new Error("源码响应与架构关系所属的固定索引、路径或内容 hash 不一致。");
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
    [selectedProjectId],
  );

  const loadNeighborhood = useCallback(
    async (
      indexId: string,
      symbolId: string,
      replace: boolean,
      requestedDirection = direction,
      requestedEdgeKinds = edgeKinds,
      requestedHttpChain: HttpEntryChain | null = null,
    ) => {
      if (!selectedProjectId) {
        return;
      }
      setHttpEntryChain(requestedHttpChain);
      graphController.current?.abort();
      const controller = new AbortController();
      graphController.current = controller;
      const requestId = ++graphRequestId.current;
      const graphIndexChanged = graphIndexId !== indexId;
      setGraphIndexId(indexId);
      setSelectedId(symbolId);
      setGraphStatus("loading");
      setGraphError(null);
      if (replace || graphIndexChanged) {
        setGraph(emptyGraphState);
        setContextualCalls([]);
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
        if (requestedHttpChain && !isHttpCallChainResponse(response, indexId, requestedHttpChain.handler.symbol_id)) {
          throw new Error("链路响应与选定 HTTP 关联、handler 或静态 calls 快照不一致。");
        }
        setGraph((previous) =>
          mergeNeighborGraph(replace || graphIndexChanged ? emptyGraphState : previous, response),
        );
        setContextualCalls((previous) => mergeContextualCalls(previous, response, replace || graphIndexChanged));
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
    [direction, edgeKinds, graphIndexId, selectedProjectId],
  );

  const loadArchitectureRelations = useCallback(
    async (root: SymbolRecord, requestedDirection: NeighborDirection, requestedEdgeKinds: EdgeKind[]) => {
      if (!selectedProjectId || !indexResponse || root.index_id !== indexResponse.index.index_id) {
        return;
      }
      graphController.current?.abort();
      const controller = new AbortController();
      graphController.current = controller;
      const requestId = ++graphRequestId.current;
      const indexId = indexResponse.index.index_id;
      setGraphIndexId(indexId);
      setSelectedId(root.symbol_id);
      setGraphStatus("loading");
      setGraphError(null);
      setGraph(emptyGraphState);
      setContextualCalls([]);
      setExpandedGroups(new Set());
      knownGroupKeys.current.clear();
      try {
        // ponytail: the existing neighbor endpoint caps each query at 40 edges; add paging if larger modules need full inventories.
        const response = await getProjectNeighbors(
          selectedProjectId,
          indexId,
          root.symbol_id,
          requestedDirection,
          requestedEdgeKinds,
          controller.signal,
          1,
        );
        if (!isLatestRequest(requestId, graphRequestId.current)) return;
        if (
          response.index_id !== indexId ||
          response.root.index_id !== indexId ||
          response.root.symbol_id !== root.symbol_id ||
          response.root.path !== root.path
        ) {
          throw new Error("关系响应与当前固定索引、模块或符号不匹配。");
        }
        setGraph(mergeNeighborGraph(emptyGraphState, response));
        setGraphStatus(response.truncated ? "success" : response.neighbors.length === 0 ? "empty" : "success");
      } catch (error) {
        if (isAbortError(error) || !isLatestRequest(requestId, graphRequestId.current)) return;
        setGraphStatus("error");
        setGraphError(errorMessage(error));
      }
    },
    [indexResponse, selectedProjectId],
  );

  const selectRunSymbol = useCallback(
    (symbolId: string) => {
      if (!runReport || !selectedProjectId) {
        return;
      }
      const symbol = runReport.graph.nodes.find((candidate) => candidate.symbol_id === symbolId);
      if (!symbol) {
        return;
      }
      setPanelMode("graph");
      void loadNeighborhood(runReport.index_id, symbol.symbol_id, true);
    },
    [loadNeighborhood, runReport, selectedProjectId],
  );

  const selectOverviewEntry = useCallback(
    (entry: OverviewEntryPoint) => {
      resetModuleSymbols();
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
      );
    },
    [indexResponse, loadLinkedEvidence, resetModuleSymbols, selectedProjectId],
  );

  const openOverviewEntryGraph = useCallback(
    (entry: OverviewEntryPoint) => {
      if (!indexResponse || !overview) {
        return;
      }
      const symbol = entry.handler_symbol
        ?? overview.modules.find((candidate) => candidate.module_id === entry.symbol_id)?.symbol;
      if (!symbol) {
        return;
      }
      selectSymbol(symbol);
      setViewMode("overview");
      setPanelMode("graph");
      void loadNeighborhood(indexResponse.index.index_id, symbol.symbol_id, true);
    },
    [indexResponse, loadNeighborhood, overview, selectSymbol],
  );

  const openHttpEntryChain = useCallback(
    (association: OverviewHttpAssociation) => {
      if (!indexResponse || !overview || !selectedProjectId) {
        return;
      }
      const indexId = indexResponse.index.index_id;
      const route = overview.entries.find((entry) => entry.entry_point_id === association.route_entry_point_id);
      const handler = route?.handler_symbol;
      if (
        overview.project_id !== selectedProjectId ||
        overview.index.index_id !== indexId ||
        association.index_id !== indexId ||
        association.client_evidence.index_id !== indexId ||
        association.route_evidence.index_id !== indexId ||
        route?.kind !== "fastapi_route" ||
        route.index_id !== indexId ||
        route.http_method !== association.method ||
        route.declared_path !== association.path ||
        !handler ||
        handler.index_id !== indexId
      ) {
        graphController.current?.abort();
        graphRequestId.current += 1;
        setHttpEntryChain(null);
        setGraph(emptyGraphState);
        setGraphIndexId(null);
        setContextualCalls([]);
        setGraphStatus("error");
        setGraphError("HTTP 关联没有匹配当前固定索引的 FastAPI handler。");
        setViewMode("overview");
        setPanelMode("graph");
        return;
      }
      setSelectedOverviewEntryId(route.entry_point_id);
      setViewMode("overview");
      setPanelMode("graph");
      void loadNeighborhood(indexId, handler.symbol_id, true, "callees", ["calls"], {
        association,
        handler,
      });
    },
    [indexResponse, loadNeighborhood, overview, selectedProjectId],
  );

  const exitHttpEntryChain = useCallback(() => {
    graphController.current?.abort();
    graphRequestId.current += 1;
    setHttpEntryChain(null);
    setGraph(emptyGraphState);
    setGraphIndexId(null);
    setContextualCalls([]);
    setGraphStatus("idle");
    setGraphError(null);
    setPanelMode("none");
  }, []);

  const openContextualCallEvidence = useCallback(
    (call: ContextualCall, location: SourceLocation) => {
      if (
        call.index_id !== graphIndexId ||
        httpEntryChain !== null && call.index_id !== httpEntryChain.association.index_id
      ) {
        return;
      }
      void openArchitectureSource({
        indexId: call.index_id,
        path: location.path,
        startLine: location.start_line,
        endLine: location.end_line,
      });
    },
    [graphIndexId, httpEntryChain, openArchitectureSource],
  );

  const selectOverviewGroup = useCallback((group: OverviewGroup) => {
    graphController.current?.abort();
    graphRequestId.current += 1;
    resetModuleSymbols();
    setSelectedOverviewGroupId(group.group_id);
    setSelectedOverviewEntryId(null);
    setHttpEntryChain(null);
    setGraphIndexId(null);
    setContextualCalls([]);
    setPanelMode("none");
    setArchitectureGraphRootId(null);
  }, [resetModuleSymbols]);

  const clearOverviewGroup = useCallback(() => {
    resetModuleSymbols();
    setSelectedOverviewGroupId(null);
  }, [resetModuleSymbols]);

  const openOverviewModule = useCallback(
    (module: OverviewModule) => {
      if (!indexResponse || module.index_id !== indexResponse.index.index_id) {
        return;
      }
      graphController.current?.abort();
      graphRequestId.current += 1;
      setGraphIndexId(module.index_id);
      setGraph(emptyGraphState);
      setGraphStatus("idle");
      setGraphError(null);
      setContextualCalls([]);
      setHttpEntryChain(null);
      setExpandedGroups(new Set());
      knownGroupKeys.current.clear();
      openModuleSymbols(module);
      setLinkedSymbol(module.symbol);
      selectSymbol(module.symbol);
      setArchitectureGraphRootId(module.symbol.symbol_id);
      setViewMode("overview");
      setPanelMode("graph");
    },
    [indexResponse, openModuleSymbols, selectSymbol],
  );

  const openModuleImports = useCallback((module: OverviewModule) => {
    void loadArchitectureRelations(module.symbol, "callees", ["imports"]);
  }, [loadArchitectureRelations]);

  const openSymbolCalls = useCallback((symbol: SymbolRecord) => {
    selectSymbol(symbol);
    setPanelMode("graph");
    void loadArchitectureRelations(symbol, "both", ["calls"]);
  }, [loadArchitectureRelations, selectSymbol]);

  const closeArchitectureGraph = useCallback(() => {
    graphController.current?.abort();
    graphRequestId.current += 1;
    setArchitectureGraphRootId(null);
    setPanelMode("none");
  }, []);

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
      setGraphIndexId(null);
      setContextualCalls([]);
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
      runImpact?.nodes.find((symbol) => symbol.symbol_id === selectedId) ??
      (linkedSymbol?.symbol_id === selectedId ? linkedSymbol : null) ??
      null
    );
  }, [graph.symbols, linkedSymbol, runImpact?.nodes, searchResults, selectedId]);

  const selectedUnresolved = useMemo(
    () => {
      if (!selectedId) {
        return [];
      }
      const fromImpact = runImpact?.nodes.some((symbol) => symbol.symbol_id === selectedId);
      return unresolvedForSymbol(fromImpact ? runImpact?.unresolved ?? [] : graph.unresolved, selectedId);
    },
    [graph.unresolved, runImpact?.nodes, runImpact?.unresolved, selectedId],
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
  const architectureImportTargets = useMemo(
    () => moduleSymbolsRoot ? directImportTargets(graph, moduleSymbolsRoot) : [],
    [graph, moduleSymbolsRoot],
  );

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
        if ((!knownGroupKeys.current.has(group.key) || previous.size === 0) && group.symbols.length <= AUTO_EXPAND_GROUP_SYMBOL_LIMIT) {
          next.add(group.key);
        }
      }
      knownGroupKeys.current = activeKeys;
      return next;
    });
  }, [groups]);

  const flowModel = useMemo(() => {
    if (httpEntryChain) {
      const depthById = new Map<string, number>([[httpEntryChain.handler.symbol_id, 0]]);
      for (let depth = 0; depth < 2; depth += 1) {
        for (const edge of graph.edges) {
          if (edge.kind === "calls" && depthById.get(edge.source_id) === depth && !depthById.has(edge.target_id)) {
            depthById.set(edge.target_id, depth + 1);
          }
        }
      }
      const rowsByDepth = new Map<number, number>();
      const nodes: SymbolFlowNode[] = graph.symbols
        .filter((symbol) => depthById.has(symbol.symbol_id))
        .sort((left, right) =>
          (depthById.get(left.symbol_id) ?? 0) - (depthById.get(right.symbol_id) ?? 0) ||
          left.qualified_name.localeCompare(right.qualified_name),
        )
        .map((symbol) => {
          const depth = depthById.get(symbol.symbol_id) ?? 0;
          const row = rowsByDepth.get(depth) ?? 0;
          rowsByDepth.set(depth, row + 1);
          return {
            id: symbol.symbol_id,
            type: "symbol",
            position: { x: 28 + depth * 226, y: 28 + row * 104 },
            data: {
              symbol,
              selected: symbol.symbol_id === selectedId,
              onSelect: (symbolId) => {
                const next = graph.symbols.find((candidate) => candidate.symbol_id === symbolId);
                if (next) selectSymbol(next);
              },
              onExpand: null,
            },
            style: { width: 196 },
            draggable: false,
          };
        });
      const nodeIds = new Set(nodes.map((node) => node.id));
      const edges: FlowEdge[] = graph.edges
        .filter((edge) => edge.kind === "calls" && nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id))
        .map((edge) => ({
          id: edge.edge_id,
          source: edge.source_id,
          target: edge.target_id,
          sourceHandle: "source",
          targetHandle: "target",
          type: "smoothstep",
          label: "calls",
          data: { kind: "calls" },
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: "#f3a65b", strokeWidth: 2 },
          labelStyle: { fill: "#e7ece8", fontSize: 10, fontWeight: 700 },
          labelBgStyle: { fill: "#152421", fillOpacity: 0.92 },
          labelBgPadding: [5, 3] as [number, number],
        }));
      return { nodes, edges };
    }

    const nodes: FlowNode[] = [];
    const visibleSymbolIds = new Set<string>();
    const groupLayout = layoutSymbolGroups(groups, expandedGroups);
    groups.forEach((group, groupIndex) => {
      const { position, height: groupHeight } = groupLayout[groupIndex];
      const expanded = expandedGroups.has(group.key);
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
          parentId: `group:${group.key}`,
          extent: "parent",
          position: { x: 16, y: 58 + symbolIndex * 78 },
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
  }, [expandedGroups, graph.edges, graph.symbols, groups, httpEntryChain, indexResponse, loadNeighborhood, selectedId, selectSymbol, toggleGroup]);

  const httpChainHasCycle = useMemo(
    () => Boolean(httpEntryChain && hasDirectedCallCycle(httpEntryChain.handler.symbol_id, graph.edges)),
    [graph.edges, httpEntryChain],
  );

  const codeFocusNodeIds = useMemo(() => {
    if (!selectedId || !flowModel.nodes.some((node) => node.id === selectedId)) {
      return [];
    }
    const focusIds = new Set([selectedId]);
    for (const edge of flowModel.edges) {
      if (focusIds.size >= FOCUS_NODE_LIMIT) {
        break;
      }
      if (edge.source === selectedId) {
        focusIds.add(edge.target);
      } else if (edge.target === selectedId) {
        focusIds.add(edge.source);
      }
    }
    return [...focusIds];
  }, [flowModel.edges, flowModel.nodes, selectedId]);

  const impactFlowModel = useMemo(() => {
    if (!runImpact || runImpact.mapping_status !== "mapped") {
      return { nodes: [] as FlowNode[], edges: [] as FlowEdge[] };
    }
    const depthById = new Map<string, number>();
    if (runImpact.root_symbol_id) {
      depthById.set(runImpact.root_symbol_id, 0);
    }
    for (const path of runImpact.paths) {
      path.symbol_ids.forEach((symbolId, index) => {
        const current = depthById.get(symbolId);
        if (current === undefined || index < current) {
          depthById.set(symbolId, index);
        }
      });
    }
    const rowsByDepth = new Map<number, number>();
    const nodes: SymbolFlowNode[] = runImpact.nodes.map((symbol) => {
      const depth = depthById.get(symbol.symbol_id) ?? 1;
      const row = rowsByDepth.get(depth) ?? 0;
      rowsByDepth.set(depth, row + 1);
      return {
        id: symbol.symbol_id,
        type: "symbol",
        position: { x: 28 + depth * 226, y: 28 + row * 104 },
        data: {
          symbol,
          selected: symbol.symbol_id === runImpact.root_symbol_id,
          onSelect: (symbolId) => {
            const next = runImpact.nodes.find((candidate) => candidate.symbol_id === symbolId);
            if (next) {
              selectSymbol(next);
            }
          },
          onExpand: () => undefined,
        },
        style: { width: 196 },
        draggable: false,
      };
    });
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges: FlowEdge[] = runImpact.edges
      .filter((edge) => nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id))
      .map((edge) => ({
        id: edge.edge_id,
        source: edge.source_id,
        target: edge.target_id,
        sourceHandle: "source",
        targetHandle: "target",
        type: "smoothstep",
        label: "calls",
        data: { kind: "calls" },
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: "#ee8b55", strokeWidth: 2.2 },
        labelStyle: { fill: "#e7ece8", fontSize: 10, fontWeight: 700 },
        labelBgStyle: { fill: "#152421", fillOpacity: 0.92 },
        labelBgPadding: [5, 3] as [number, number],
      }));
    return { nodes, edges };
  }, [runImpact, selectSymbol]);

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
    if (panelMode === "impact" && flowInstance && impactFlowModel.nodes.length > 0) {
      const timer = window.setTimeout(() => {
        flowInstance.fitView({ padding: 0.18, duration: 320 });
      }, 50);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [flowInstance, impactFlowModel.nodes.length, panelMode]);

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
    const fromImpact = runImpact?.nodes.some((symbol) => symbol.symbol_id === selectedId);
    const edges = fromImpact ? runImpact?.edges ?? [] : graph.edges;
    return edges.filter((edge) => edge.source_id === selectedId || edge.target_id === selectedId);
  }, [graph.edges, runImpact?.edges, runImpact?.nodes, selectedId]);

  const indexIdentity = indexResponse?.index;
  const selectedProject = useMemo(
    () => projects.find((project) => project.project_id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const sourceIndexIdentity = selectedSymbol
    ? sessions.find((session) => session.session_id === selectedSessionId)?.index.index_id === selectedSymbol.index_id
      ? sessions.find((session) => session.session_id === selectedSessionId)?.index ?? indexIdentity
      : indexIdentity
    : indexIdentity;
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

  const handleMoveProjectToTrash = useCallback(async (projectId: string) => {
    if (trashMutationInFlight.current) return;
    trashMutationInFlight.current = true;
    setTrashMutationBusy(true);
    setTrashError(null);
    try {
      await moveProjectToTrash(projectId);
      projectController.current?.abort();
      projectRequestId.current += 1;
      setProjects((previous) => previous.filter((project) => project.project_id !== projectId));
      setSessionDrafts((previous) => Object.fromEntries(
        Object.entries(previous).filter(([key]) => !key.startsWith(`${projectId}:`)),
      ));
      if (selectedProjectIdRef.current === projectId) {
        indexController.current?.abort();
        indexRequestId.current += 1;
        resetSessions();
        clearExploration();
        setProjectIndexes([]);
        setIndexResponse(null);
        setIndexStatus("unindexed");
        setIndexError(null);
        setSelectedSessionId(null);
        setSelectedProjectId(null);
        const route = readWorkbenchRoute(window.location.search);
        if (route.projectId === projectId || !route.projectId) {
          window.history.replaceState(null, "", window.location.pathname);
        }
        if (window.localStorage.getItem("codetrail.selectedProjectId") === projectId) {
          window.localStorage.removeItem("codetrail.selectedProjectId");
        }
        if (sessionLink.current?.projectId === projectId) {
          sessionLink.current = null;
          sessionLinkApplied.current = true;
        }
        if (evidenceLink.current?.projectId === projectId) {
          evidenceLink.current = null;
          evidenceLinkApplied.current = true;
        }
      }
      await loadTrash();
    } catch (error) {
      setTrashError(errorMessage(error));
      setTrashStatus("error");
    } finally {
      trashMutationInFlight.current = false;
      setTrashMutationBusy(false);
    }
  }, [clearExploration, loadTrash, resetSessions]);

  const handleMoveSessionToTrash = useCallback(async (projectId: string, sessionId: string) => {
    if (trashMutationInFlight.current) return;
    trashMutationInFlight.current = true;
    setTrashMutationBusy(true);
    setTrashError(null);
    try {
      await moveSessionToTrash(projectId, sessionId);
      setSessionDrafts((previous) => {
        const next = { ...previous };
        delete next[sessionDraftStorageKey(projectId, sessionId)];
        return next;
      });
      if (selectedProjectIdRef.current === projectId) {
        sessionController.current?.abort();
        sessionRequestId.current += 1;
        setSessions((previous) => previous.filter((session) => session.session_id !== sessionId));
        if (selectedSessionIdRef.current === sessionId) {
          resetSessions();
          setViewMode("overview");
          const route = readWorkbenchRoute(window.location.search);
          if (route.projectId === projectId && route.sessionId === sessionId) {
            replaceWorkbenchLocation(projectId);
          }
          if (sessionLink.current?.projectId === projectId && sessionLink.current.sessionId === sessionId) {
            sessionLink.current = null;
            sessionLinkApplied.current = true;
          }
          void loadSessions(projectId);
        }
      }
      await loadTrash();
    } catch (error) {
      setTrashError(errorMessage(error));
      setTrashStatus("error");
    } finally {
      trashMutationInFlight.current = false;
      setTrashMutationBusy(false);
    }
  }, [loadSessions, loadTrash, replaceWorkbenchLocation, resetSessions]);

  const handleRestoreProject = useCallback(async (projectId: string) => {
    if (trashMutationInFlight.current) return;
    trashMutationInFlight.current = true;
    setTrashMutationBusy(true);
    setTrashError(null);
    try {
      const project = await restoreProject(projectId);
      projectController.current?.abort();
      projectRequestId.current += 1;
      setProjects((previous) => [project, ...previous.filter((item) => item.project_id !== projectId)]);
      setProjectStatus("success");
      await loadTrash();
    } catch (error) {
      setTrashError(errorMessage(error));
      setTrashStatus("error");
    } finally {
      trashMutationInFlight.current = false;
      setTrashMutationBusy(false);
    }
  }, [loadTrash]);

  const handleRestoreSession = useCallback(async (projectId: string, sessionId: string) => {
    if (trashMutationInFlight.current) return;
    trashMutationInFlight.current = true;
    setTrashMutationBusy(true);
    setTrashError(null);
    try {
      const response = await restoreSession(projectId, sessionId);
      if (selectedProjectIdRef.current === projectId) {
        sessionController.current?.abort();
        sessionRequestId.current += 1;
        setSessions((previous) => [
          response.session,
          ...previous.filter((item) => item.session_id !== sessionId),
        ].sort((left, right) => right.updated_at.localeCompare(left.updated_at)));
        setSessionStatus("success");
      }
      await loadTrash();
    } catch (error) {
      setTrashError(errorMessage(error));
      setTrashStatus("error");
    } finally {
      trashMutationInFlight.current = false;
      setTrashMutationBusy(false);
    }
  }, [loadTrash]);

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
        trash,
        trashStatus,
        trashError,
        trashMutationBusy,
        onLoadTrash: loadTrash,
        onMoveProjectToTrash: handleMoveProjectToTrash,
        onMoveSessionToTrash: handleMoveSessionToTrash,
        onRestoreProject: handleRestoreProject,
        onRestoreSession: handleRestoreSession,
        onSelectProject: (projectId) => {
          if (projectId === selectedProjectId) return;
          graphController.current?.abort();
          graphRequestId.current += 1;
          setHttpEntryChain(null);
          setProjectError(null);
          sessionLink.current = null;
          sessionLinkApplied.current = true;
          evidenceLink.current = null;
          evidenceLinkApplied.current = true;
          setSelectedSessionId(null);
          setViewMode("overview");
          setPanelMode("none");
          setArchitectureGraphRootId(null);
          setSelectedProjectId(projectId);
          replaceWorkbenchLocation(projectId);
        },
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
        onRefresh: refreshLocalProjectImportStatus,
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
        onRefresh: refreshGitHubProjectImportStatus,
        onRetryCleanup: retryGitHubCleanup,
      }}
      index={{
        response: indexResponse,
        indexes: projectIndexes,
        isLatest: Boolean(indexResponse && selectedProject?.latest_index_id === indexResponse.index.index_id),
        status: indexStatus,
        error: indexError,
        freshness: indexFreshness,
        freshnessStatus: indexFreshnessStatus,
        freshnessError: indexFreshnessError,
        refreshMode: indexRefreshMode,
        onCreate: createNewIndex,
        onOpenIndex: openExistingIndex,
        onCheckFreshness: checkCurrentIndexFreshness,
        onRefresh: refreshCurrentIndex,
        onUpdateGitHub: refreshCurrentGitHubSource,
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
        runs: runHistory,
        runsStatus: runHistoryStatus,
        runsError: runHistoryError,
        selectedRunId,
        runStatusResponse,
        runEvents,
        runToolResults,
        runReport,
        runStatus,
        runError,
        runStreamStatus,
        runStreamError,
        runWriteStatus,
        runWriteError,
        onSelectRun: selectRun,
        onStartRun: startSessionRun,
        onCancelRun: cancelSelectedRun,
        onSelectRunEvidence: selectFeatureEvidence,
        onSelectRunSymbol: selectRunSymbol,
        onSelectRunImpact: (selection) => {
          void loadRunImpact(selection);
        },
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
        selectedOverviewGroupId,
        architectureGraphRootId,
        onCloseArchitectureGraph: closeArchitectureGraph,
        moduleSymbolsRoot,
        moduleSymbolsPage,
        moduleSymbolsStatus,
        moduleSymbolsError,
        onSelectOverviewEntry: selectOverviewEntry,
        onSelectOverviewGroup: selectOverviewGroup,
        onClearOverviewGroup: clearOverviewGroup,
        onOpenOverviewGraph: openOverviewEntryGraph,
        onOpenHttpChain: openHttpEntryChain,
        onOpenOverviewModule: openOverviewModule,
        onOpenModuleSymbols: openModuleSymbols,
        onOpenModuleImports: openModuleImports,
        onOpenSymbolCalls: openSymbolCalls,
        onLoadMoreModuleSymbols: loadMoreModuleSymbols,
        onRetryModuleSymbols: retryModuleSymbols,
        onSelectModuleSymbol: selectModuleSymbol,
        onOpenArchitectureSource: openArchitectureSource,
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
        featureSourceScope,
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
        index: sourceIndexIdentity ?? null,
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
        hasHttpChain: httpEntryChain !== null,
        onLoadSource: loadSource,
        onRetrySource: () => {
          if (selectedSymbol) {
            void loadSource(selectedSymbol, sourceWindowStart);
          }
        },
        onExpandRelation: () => {
          if (selectedSymbol && selectedSymbol.kind !== "file") {
            setViewMode("overview");
            setPanelMode("graph");
            void loadNeighborhood(selectedSymbol.index_id, selectedSymbol.symbol_id, false);
          }
        },
        onReturnHttpChain: () => setPanelMode("graph"),
        onClose: () => {
          if (httpEntryChain) {
            exitHttpEntryChain();
          } else {
            setPanelMode("none");
          }
        },
      }}
      graph={{
        panelMode: panelMode === "feature_graph" ? "feature_graph" : panelMode === "impact" ? "impact" : "graph",
        codeModel: flowModel,
        codeEdges: graph.edges,
        directImportTargets: architectureImportTargets,
        codeStatus: graphStatus,
        codeError: graphError,
        codeTruncated: graph.truncated,
        codeTruncationReason: graph.truncationReason,
        codeIndexTruncated: graph.indexTruncated,
        codeIndexTruncationReason: graph.indexTruncationReason,
        codeIndexUnresolvedTruncated: graph.indexUnresolvedTruncated,
        codeIndexUnresolvedTruncationReason: graph.indexUnresolvedTruncationReason,
        codeUnresolved: graph.unresolved,
        contextualCalls,
        onSelectContextualCallEvidence: openContextualCallEvidence,
        codeHasCycle: httpChainHasCycle,
        httpChain: httpEntryChain,
        onSelectHttpChainEvidence: selectFeatureEvidence,
        onRetryHttpChain: () => {
          if (httpEntryChain) {
            openHttpEntryChain(httpEntryChain.association);
          }
        },
        onExitHttpChain: exitHttpEntryChain,
        codeFocusNodeIds,
        codeSymbolCount: graph.symbols.length,
        codeEdgeCount: graph.edges.length,
        impactModel: impactFlowModel,
        impact: runImpact,
        impactStatus: runImpactStatus,
        impactError: runImpactError,
        impactDirection: runImpactDirection,
        impactDepth: runImpactDepth,
        onImpactDirectionChange: changeRunImpactDirection,
        onImpactDepthChange: changeRunImpactDepth,
        onImpactNodeClick: (nodeId) => {
          const symbol = runImpact?.nodes.find((candidate) => candidate.symbol_id === nodeId);
          if (symbol) {
            selectSymbol(symbol);
          }
        },
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
        onClose: () => {
          if (httpEntryChain) {
            exitHttpEntryChain();
          } else {
            setPanelMode("none");
          }
        },
      }}
      viewMode={viewMode}
      onViewModeChange={changeWorkbenchView}
      onImportFlowLeave={invalidateImportNavigation}
      panelMode={panelMode}
      onPanelModeChange={setPanelMode}
    />
  );
}
