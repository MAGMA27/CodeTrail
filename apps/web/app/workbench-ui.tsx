import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  ReactFlow,
  Position,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useEffect, useMemo, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type ReactNode } from "react";

import type {
  CodeEdge,
  CodeExplanation,
  EdgeKind,
  Evidence,
  FeatureGeneration,
  FeatureGenerationMode,
  FeatureGenerationSummary,
  FeatureGraph,
  FeatureSourceScope,
  HealthResponse,
  ImpactDirection,
  ImportAttempt,
  ImportStatus,
  IndexFreshness,
  IndexRefreshMode,
  IndexResponse,
  IndexSnapshot,
  NeighborDirection,
  OverviewEntryPoint,
  OverviewGroup,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
  ProjectRecord,
  RunEvent,
  RunMode,
  RunRecord,
  RunReport,
  RunImpactResponse,
  RunScenario,
  RunStatusResponse,
  RunToolResult,
  SearchMatch,
  Session,
  SessionMessage,
  SourceResponse,
  SymbolRecord,
  UnresolvedReference,
} from "../lib/api";
import type { GraphState } from "../lib/graph";
import { buildArchitectureGraph, type ArchitectureDirectory } from "../lib/architecture-graph";

export type UiAsyncStatus = "idle" | "loading" | "success" | "empty" | "error";
export type UiIndexStatus = "unindexed" | "creating" | "loading" | "ready" | "partial" | "error";
export type UiPanelMode = "none" | "source" | "graph" | "feature_graph" | "impact";
export type OperationPanel = "create-project" | "project-management";
export type OperationTab = "source" | "index";

type OpenOperation = (panel: OperationPanel, tab?: OperationTab) => void;
type OperationPanelState = { mode: OperationPanel; tab: OperationTab };

type FormHandler = (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
type Action = () => void | Promise<void>;

type ProjectUi = {
  projects: ProjectRecord[];
  selectedProjectId: string | null;
  selectedProject: ProjectRecord | null;
  status: UiAsyncStatus;
  error: string | null;
  name: string;
  repositoryId: string;
  onNameChange: (value: string) => void;
  onRepositoryIdChange: (value: string) => void;
  onSelectProject: (projectId: string) => void;
  onCreate: FormHandler;
};

type LocalImportUi = {
  name: string;
  path: string;
  status: ImportStatus | null;
  attempt: ImportAttempt | null;
  error: string | null;
  onNameChange: (value: string) => void;
  onPathChange: (value: string) => void;
  onSubmit: FormHandler;
  onRetry: Action;
};

type GitHubImportUi = {
  name: string;
  url: string;
  ref: string;
  status: ImportStatus | null;
  attempt: ImportAttempt | null;
  error: string | null;
  onNameChange: (value: string) => void;
  onUrlChange: (value: string) => void;
  onRefChange: (value: string) => void;
  onSubmit: FormHandler;
  onRetry: Action;
  onRetryCleanup: Action;
};

type IndexUi = {
  response: IndexResponse | null;
  indexes: IndexSnapshot[];
  isLatest: boolean;
  status: UiIndexStatus;
  error: string | null;
  freshness: IndexFreshness | null;
  freshnessStatus: "idle" | "checking" | "refreshing";
  freshnessError: string | null;
  refreshMode: IndexRefreshMode | null;
  revision: string;
  dirty: boolean;
  indexIdInput: string;
  onRevisionChange: (value: string) => void;
  onDirtyChange: (value: boolean) => void;
  onIndexIdChange: (value: string) => void;
  onCreate: FormHandler;
  onOpen: FormHandler;
  onCheckFreshness: Action;
  onRefresh: Action;
  onUpdateGitHub: Action;
};

type SessionUi = {
  sessions: Session[];
  cursor: string | null;
  hasMore: boolean;
  status: UiAsyncStatus;
  error: string | null;
  selectedSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onLoadMore: Action;
  topic: string;
  onTopicChange: (value: string) => void;
  writeStatus: UiAsyncStatus;
  onCreate: FormHandler;
  messages: SessionMessage[];
  messageCursor: string | null;
  messagesHasMore: boolean;
  messagesStatus: UiAsyncStatus;
  messagesError: string | null;
  onLoadMoreMessages: Action;
  onRetryMessages: Action;
  draft: string;
  onDraftChange: (value: string) => void;
  onSendMessage: Action;
  runs: RunRecord[];
  runsStatus: UiAsyncStatus;
  runsError: string | null;
  selectedRunId: string | null;
  runStatusResponse: RunStatusResponse | null;
  runEvents: RunEvent[];
  runToolResults: RunToolResult[];
  runReport: RunReport | null;
  runStatus: UiAsyncStatus;
  runError: string | null;
  runStreamStatus: "idle" | "connecting" | "connected" | "reconnecting" | "fallback" | "closed";
  runStreamError: string | null;
  runWriteStatus: UiAsyncStatus;
  runWriteError: string | null;
  onSelectRun: (runId: string) => void;
  onStartRun: (mode: RunMode, scenario: RunScenario) => void | Promise<void>;
  onCancelRun: Action;
  onSelectRunEvidence: (evidence: Evidence) => void;
  onSelectRunSymbol: (symbolId: string) => void;
  onSelectRunImpact: (selection: { candidateId?: string; findingId?: string }) => void;
};

type KnowledgeUi = {
  searchQuery: string;
  searchStatus: UiAsyncStatus;
  searchResults: SearchMatch[];
  searchError: string | null;
  onSearchQueryChange: (value: string) => void;
  onSearch: FormHandler;
  onSelectSearchResult: (match: SearchMatch) => void;
  overviewStatus: UiAsyncStatus;
  overview: ProjectOverview | null;
  overviewError: string | null;
  selectedOverviewEntryId: string | null;
  selectedOverviewGroupId: string | null;
  onSelectOverviewEntry: (entry: OverviewEntryPoint) => void;
  onSelectOverviewGroup: (group: OverviewGroup) => void;
  onClearOverviewGroup: () => void;
  onOpenOverviewGraph: (entry: OverviewEntryPoint) => void;
  onOpenOverviewModule: (module: OverviewModule) => void;
  onOpenArchitectureSource: (location: { indexId: string; path: string; startLine: number; endLine: number; expectedContentHash?: string }) => void;
  onRetryOverview: Action;
  featureStatus: UiAsyncStatus;
  featureGeneration: FeatureGeneration | null;
  featureError: string | null;
  featureHistory: FeatureGenerationSummary[];
  featureHistoryStatus: UiAsyncStatus;
  featureHistoryError: string | null;
  featureHistoryHasMore: boolean;
  onLoadMoreFeatureHistory: Action;
  onRetryFeatureHistory: Action;
  selectedFeatureGenerationId: string | null;
  onSelectFeatureGeneration: (generationId: string) => void;
  featureMode: FeatureGenerationMode;
  featureSourceScope: FeatureSourceScope;
  onFeatureModeChange: (mode: FeatureGenerationMode) => void;
  onFeatureSourceScopeChange: (scope: FeatureSourceScope) => void;
  onRunFeatureGeneration: (retryFailed?: boolean) => void | Promise<void>;
  onReloadFeatureGeneration: Action;
  onOpenFeatureGraph: Action;
  onSelectFeatureEvidence: (evidence: Evidence) => void;
};

type EvidenceUi = {
  projectId: string | null;
  index: IndexSnapshot | null;
  generationId: string | null;
  selectedSymbol: SymbolRecord | null;
  sourceStatus: UiAsyncStatus;
  sourceResponse: SourceResponse | null;
  sourceError: string | null;
  sourceWindowStart: number;
  sourceHasPrevious: boolean;
  sourceHasNext: boolean;
  connectedEdges: CodeEdge[];
  selectedUnresolved: UnresolvedReference[];
  onLoadSource: (symbol: SymbolRecord, startLine?: number) => void | Promise<void>;
  onRetrySource: Action;
  onExpandRelation: Action;
  onClose: () => void;
};

type GraphUi = {
  panelMode: Exclude<UiPanelMode, "none" | "source">;
  codeModel: { nodes: Node[]; edges: Edge[] };
  codeStatus: UiAsyncStatus;
  codeError: string | null;
  codeTruncated: boolean;
  codeTruncationReason: string | null;
  codeIndexTruncated: boolean;
  codeIndexTruncationReason: string | null;
  codeIndexUnresolvedTruncated: boolean | null;
  codeIndexUnresolvedTruncationReason: string | null;
  codeFocusNodeIds: string[];
  codeSymbolCount: number;
  codeEdgeCount: number;
  impactModel: { nodes: Node[]; edges: Edge[] };
  impact: RunImpactResponse | null;
  impactStatus: UiAsyncStatus;
  impactError: string | null;
  impactDirection: ImpactDirection;
  impactDepth: number;
  onImpactDirectionChange: (direction: ImpactDirection) => void;
  onImpactDepthChange: (depth: number) => void;
  onImpactNodeClick: (nodeId: string) => void;
  featureModel: { nodes: Node[]; edges: Edge[] };
  featureStatus: UiAsyncStatus;
  featureError: string | null;
  featureGraph: FeatureGraph | null;
  featureGeneration: FeatureGeneration | null;
  direction: NeighborDirection;
  edgeKinds: EdgeKind[];
  onToggleEdgeKind: (kind: EdgeKind) => void;
  onChangeDirection: (direction: NeighborDirection) => void;
  onCodeNodeClick: (nodeId: string) => void;
  onFeatureNodeClick: (nodeId: string) => void;
  onCodeInit: (instance: ReactFlowInstance<Node, Edge>) => void;
  onFeatureInit: (instance: ReactFlowInstance<Node, Edge>) => void;
  codeNodeTypes: NodeTypes;
  featureNodeTypes: NodeTypes;
  flowInstance: ReactFlowInstance<Node, Edge> | null;
  featureFlowInstance: ReactFlowInstance<Node, Edge> | null;
  selectedId: string | null;
  onPanelModeChange: (mode: UiPanelMode) => void;
  onClose: () => void;
};

export type WorkbenchUiProps = {
  connection: {
    state: "checking" | "connected" | "error";
    health: HealthResponse | null;
    error: string | null;
    onCheck: Action;
  };
  project: ProjectUi;
  localImport: LocalImportUi;
  githubImport: GitHubImportUi;
  index: IndexUi;
  session: SessionUi;
  knowledge: KnowledgeUi;
  evidence: EvidenceUi;
  graph: GraphUi;
  viewMode: "overview" | "session";
  onViewModeChange: (mode: "overview" | "session") => void;
  panelMode: UiPanelMode;
  onPanelModeChange: (mode: UiPanelMode) => void;
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function shortHash(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : "—";
}

function asyncStatusLabel(status: UiAsyncStatus): string {
  return {
    idle: "待命",
    loading: "载入中",
    success: "已就绪",
    empty: "暂无数据",
    error: "请求失败",
  }[status];
}

function indexStatusLabel(status: UiIndexStatus): string {
  return {
    unindexed: "未打开索引",
    creating: "正在建立快照",
    loading: "正在读取快照",
    ready: "快照已就绪",
    partial: "快照部分完成",
    error: "索引请求失败",
  }[status];
}

function freshnessStatusLabel(status: IndexFreshness["status"]): string {
  return {
    unchecked: "尚未检查",
    consistent: "源码一致",
    changed: "发现变化",
    unknown: "无法确定",
  }[status];
}

function remoteFreshnessStatusLabel(status: IndexFreshness["remote_status"]): string {
  return {
    not_applicable: "不适用",
    not_checked: "尚未检查",
    consistent: "远端 commit 一致",
    changed: "远端 commit 已变化",
    unknown: "远端无法确定",
  }[status];
}

function freshnessReasonLabel(reason: string): string {
  return {
    missing_index_metadata: "旧索引缺少规则/配置元数据",
    rules_changed: "解析或关系规则版本变化",
    configuration_changed: "索引配置指纹变化",
    source_changed: "纳入范围的源码 hash 变化",
    source_read_error: "读取来源失败",
    source_unavailable: "来源目录不可用",
    decode_error: "存在无法按 UTF-8 读取的文件",
    file_too_large: "存在超过索引上限的文件",
    symlink: "发现不允许索引的符号链接",
    outside_root: "发现越过来源根目录的路径",
    file_changed_during_check: "文件在检查期间发生变化",
    budget_exceeded: "检查达到扫描预算",
    remote_not_checked: "GitHub 远端未检查",
    remote_metadata_missing: "GitHub 历史来源缺少原始 ref 元数据",
  }[reason] ?? reason;
}

const SAFE_LEGACY_REBUILD_REASONS = new Set<IndexFreshness["reasons"][number]>([
  "missing_index_metadata",
  "rules_changed",
  "configuration_changed",
  "source_changed",
]);

function canSafelyRebuildLegacyIndex(freshness: IndexFreshness): boolean {
  return (
    freshness.status === "unknown" &&
    freshness.scan_complete &&
    !freshness.scan_truncated &&
    freshness.reasons.includes("missing_index_metadata") &&
    freshness.reasons.every((reason) => SAFE_LEGACY_REBUILD_REASONS.has(reason))
  );
}

function importStatusLabel(status: ImportStatus | null): string {
  if (!status) {
    return "未开始";
  }
  return {
    queued: "排队中",
    acquiring: "获取源代码",
    indexing: "建立快照",
    ready: "导入完成",
    failed: "导入失败",
    cancelled: "已取消",
    interrupted: "中断待重试",
  }[status];
}

function featureStatusLabel(status: UiAsyncStatus, generation: FeatureGeneration | null): string {
  if (status === "loading") {
    return "生成中";
  }
  if (status === "error") {
    return "生成失败";
  }
  if (!generation) {
    return "未选择功能版本";
  }
  return {
    succeeded: "已生成",
    partial: "部分完成",
    insufficient_evidence: "证据不足",
    failed: "失败，可重试",
  }[generation.status];
}

function savedFeatureStatusLabel(status: FeatureGenerationSummary["status"]): string {
  return {
    succeeded: "已生成",
    partial: "部分完成",
    insufficient_evidence: "证据不足",
    failed: "失败",
  }[status];
}

function evidenceLines(evidence: Evidence): Array<{ number: number; text: string }> {
  return evidence.excerpt.split(/\r?\n/).map((text, index) => ({
    number: evidence.start_line + index,
    text,
  }));
}

function LoadingNote({ children }: { children: ReactNode }) {
  return <p className="ct-loading"><span className="ct-spinner" aria-hidden="true" />{children}</p>;
}

function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return (
    <div className="ct-empty-state">
      <span className="ct-empty-index" aria-hidden="true">—</span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action ? <div className="ct-empty-actions">{action}</div> : null}
    </div>
  );
}

function ProjectRail({
  project,
  session,
  onOpenOperation,
  onViewKnowledge,
}: {
  project: ProjectUi;
  session: SessionUi;
  onOpenOperation: OpenOperation;
  onViewKnowledge: () => void;
}) {
  const [sessionFormOpen, setSessionFormOpen] = useState(false);
  const selectedProject = project.selectedProject;
  useEffect(() => {
    if (session.writeStatus === "success") {
      setSessionFormOpen(false);
    }
  }, [session.writeStatus]);
  return (
    <aside className="ct-sidebar" aria-label="项目与会话导航">
      <div className="ct-sidebar-header">
        <div>
          <p className="ct-overline">PROJECTS</p>
          <h2>项目</h2>
        </div>
        <button type="button" className="ct-text-button" onClick={() => onOpenOperation("create-project")}>+ 新建</button>
      </div>
      <div className="ct-sidebar-scroll">
        <div className="ct-sidebar-caption">
          <span>可切换工作区</span>
          <span>{project.status === "loading" ? "同步中" : `${project.projects.length} 个`}</span>
        </div>
        {project.projects.length > 0 ? (
          <div className="ct-project-list">
            {project.projects.map((item) => {
              const selected = item.project_id === project.selectedProjectId;
              const status = item.building_index_id
                ? "建立中"
                : item.available_index_ids.length > 0
                  ? `${item.available_index_ids.length} 个快照`
                  : "未索引";
              return (
                <button
                  type="button"
                  key={item.project_id}
                  className={`ct-project-item${selected ? " is-selected" : ""}`}
                  onClick={() => project.onSelectProject(item.project_id)}
                  aria-current={selected ? "page" : undefined}
                >
                  <span className="ct-project-initial" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span>
                  <span className="ct-project-copy">
                    <strong>{item.name}</strong>
                    <small>{item.repository_id}</small>
                    <span>{status}</span>
                  </span>
                  <span className="ct-project-chevron" aria-hidden="true">{selected ? "·" : "›"}</span>
                </button>
              );
            })}
          </div>
        ) : project.status === "loading" ? (
          <div className="ct-sidebar-loading"><span /><span /><span /></div>
        ) : (
          <div className="ct-sidebar-empty"><strong>还没有项目</strong><span>创建或导入一个服务端可见的仓库。</span></div>
        )}

        {selectedProject ? (
          <div className="ct-project-section">
            <div className="ct-project-section-heading">
              <div>
                <p className="ct-overline">CURRENT PROJECT</p>
                <strong>{selectedProject.name}</strong>
              </div>
              <button
                type="button"
                className="ct-icon-button"
                aria-label="新建会话"
                onClick={() => setSessionFormOpen((value) => !value)}
              >
                +
              </button>
            </div>
            <button type="button" className="ct-nav-item is-active" onClick={() => { setSessionFormOpen(false); onViewKnowledge(); }}>
              <span className="ct-nav-marker" aria-hidden="true" />
              <span>项目知识</span>
              <span className="ct-nav-arrow" aria-hidden="true">›</span>
            </button>
            <div className="ct-session-heading">
              <span>会话</span>
              <span>{session.sessions.length}</span>
            </div>
            {sessionFormOpen ? (
              <form className="ct-session-create" onSubmit={(event) => void session.onCreate(event)}>
                <label htmlFor="rail-session-topic">会话主题</label>
                <input
                  id="rail-session-topic"
                  value={session.topic}
                  onChange={(event) => session.onTopicChange(event.target.value)}
                  placeholder="例如：timeout 配置为什么失败？"
                  maxLength={500}
                />
                <button type="submit" className="ct-button ct-button-primary" disabled={session.writeStatus === "loading"}>
                  {session.writeStatus === "loading" ? "保存中…" : "创建并打开"}
                </button>
              </form>
            ) : null}
            {session.error ? <p className="ct-inline-error" role="alert">{session.error}</p> : null}
            {session.sessions.length > 0 ? (
              <div className="ct-session-list">
                {session.sessions.map((item) => (
                  <button
                    type="button"
                    key={item.session_id}
                    className={`ct-session-item${item.session_id === session.selectedSessionId ? " is-selected" : ""}`}
                    data-session-id={item.session_id}
                    onClick={() => session.onSelectSession(item.session_id)}
                    aria-current={item.session_id === session.selectedSessionId ? "page" : undefined}
                  >
                    <span className="ct-session-item-copy">
                      <strong>{item.topic}</strong>
                      <small>{item.message_count} 条消息 · {item.index.revision}</small>
                    </span>
                    <span className="ct-session-item-time">{formatDate(item.updated_at)}</span>
                  </button>
                ))}
              </div>
            ) : session.status === "empty" ? (
              <p className="ct-sidebar-note">还没有会话。点击右上角 + 开始记录排查主题。</p>
            ) : session.status === "loading" ? (
              <p className="ct-sidebar-note">正在读取会话…</p>
            ) : null}
            {session.hasMore ? (
              <button type="button" className="ct-link-button" onClick={() => void session.onLoadMore()} disabled={session.status === "loading"}>
                加载更多会话
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="ct-sidebar-footer">
        <button type="button" className="ct-sidebar-action" onClick={() => onOpenOperation("project-management", "source")}>
          <span>源代码与索引</span><span aria-hidden="true">↗</span>
        </button>
        <p>来源目录只读 · 不执行仓库代码</p>
      </div>
    </aside>
  );
}

function OperationPanel({
  mode,
  initialTab,
  onClose,
  project,
  localImport,
  githubImport,
  index,
}: {
  mode: OperationPanel;
  initialTab: OperationTab;
  onClose: () => void;
  project: ProjectUi;
  localImport: LocalImportUi;
  githubImport: GitHubImportUi;
  index: IndexUi;
}) {
  const [importMode, setImportMode] = useState<"local" | "github">("local");
  const [activeTab, setActiveTab] = useState<OperationTab>(initialTab);
  const selectedProject = project.selectedProject;
  const importBusy = (status: ImportStatus | null) => status === "queued" || status === "acquiring" || status === "indexing";
  const baselineIndex = index.freshness
    ? index.indexes.find((item) => item.index_id === index.freshness?.baseline_index_id)
    : null;
  const githubUpdateRequired = Boolean(
    index.freshness &&
    ["consistent", "changed"].includes(index.freshness.remote_status) &&
    index.freshness.status !== "consistent",
  );
  const githubCheckUncertain = index.freshness?.remote_status === "unknown";
  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);
  return (
    <section className="ct-operation-panel" aria-label="项目操作">
      <div className="ct-operation-header">
        <div>
          <p className="ct-overline">PROJECT OPERATIONS</p>
          <h2>{mode === "create-project" ? "创建项目" : "源代码与索引"}</h2>
          <p>{mode === "create-project" ? "注册一个服务端 allow-list 中的仓库。" : "在同一处管理项目来源和固定快照；两者保持独立的生命周期。"}</p>
        </div>
        <button type="button" className="ct-close-button" onClick={onClose} aria-label="关闭项目操作">关闭</button>
      </div>

      {mode === "create-project" ? (
        <form className="ct-operation-form ct-operation-form-narrow" onSubmit={(event) => void project.onCreate(event)}>
          <label htmlFor="operation-project-name">项目名称</label>
          <input id="operation-project-name" value={project.name} onChange={(event) => project.onNameChange(event.target.value)} placeholder="Timeout Demo" />
          <label htmlFor="operation-project-repository">来源 repository_id</label>
          <input id="operation-project-repository" value={project.repositoryId} onChange={(event) => project.onRepositoryIdChange(event.target.value)} placeholder="timeout-demo" spellCheck={false} />
          <p className="ct-form-help">必须是 API 服务端配置的 repository_id；创建项目不会自动建立索引。</p>
          {project.error ? <p className="ct-inline-error" role="alert">{project.error}</p> : null}
          <button type="submit" className="ct-button ct-button-primary" disabled={project.status === "loading"}>{project.status === "loading" ? "保存中…" : "创建项目"}</button>
        </form>
      ) : (
        <div className="ct-operation-body">
          <div className="ct-segmented" role="tablist" aria-label="项目管理">
            <button type="button" role="tab" aria-selected={activeTab === "source"} className={activeTab === "source" ? "is-active" : ""} onClick={() => setActiveTab("source")}>源代码来源</button>
            <button type="button" role="tab" aria-selected={activeTab === "index"} className={activeTab === "index" ? "is-active" : ""} onClick={() => setActiveTab("index")} disabled={!selectedProject}>固定索引</button>
          </div>
          {activeTab === "source" ? (
            <>
              <div className="ct-segmented" role="tablist" aria-label="导入来源">
                <button type="button" role="tab" aria-selected={importMode === "local"} className={importMode === "local" ? "is-active" : ""} onClick={() => setImportMode("local")}>本地目录</button>
                <button type="button" role="tab" aria-selected={importMode === "github"} className={importMode === "github" ? "is-active" : ""} onClick={() => setImportMode("github")}>GitHub HTTPS</button>
              </div>
              {importMode === "local" ? (
                <form className="ct-operation-form" onSubmit={(event) => void localImport.onSubmit(event)}>
              <div className="ct-form-grid">
                <div>
                  <label htmlFor="operation-local-name">项目名称</label>
                  <input id="operation-local-name" value={localImport.name} onChange={(event) => localImport.onNameChange(event.target.value)} placeholder="Local project" spellCheck={false} />
                </div>
                <div className="ct-form-wide">
                  <label htmlFor="operation-local-path">API 服务端绝对路径</label>
                  <input id="operation-local-path" value={localImport.path} onChange={(event) => localImport.onPathChange(event.target.value)} placeholder="D:\\projects\\my-repo" spellCheck={false} />
                </div>
              </div>
              <p className="ct-form-help">服务端会校验允许根目录、符号链接和 loopback 边界；来源目录保持只读。导入完成后会注册项目并建立固定快照。</p>
              {localImport.error ? <p className="ct-inline-error" role="alert">{localImport.error}</p> : null}
              <div className="ct-form-actions">
                <button type="submit" className="ct-button ct-button-primary" disabled={importBusy(localImport.status)}>{localImport.status === "indexing" ? "建立中…" : "导入并建立索引"}</button>
                {localImport.attempt ? <ImportAttemptStatus attempt={localImport.attempt} onRetry={localImport.onRetry} /> : null}
              </div>
                </form>
              ) : (
                <form className="ct-operation-form" onSubmit={(event) => void githubImport.onSubmit(event)}>
              <div className="ct-form-grid">
                <div>
                  <label htmlFor="operation-github-name">项目名称</label>
                  <input id="operation-github-name" value={githubImport.name} onChange={(event) => githubImport.onNameChange(event.target.value)} placeholder="GitHub Python project" spellCheck={false} />
                </div>
                <div>
                  <label htmlFor="operation-github-ref">分支 / tag / commit</label>
                  <input id="operation-github-ref" value={githubImport.ref} onChange={(event) => githubImport.onRefChange(event.target.value)} placeholder="留空使用默认分支" spellCheck={false} />
                </div>
                <div className="ct-form-wide">
                  <label htmlFor="operation-github-url">公开 GitHub HTTPS 地址</label>
                  <input id="operation-github-url" value={githubImport.url} onChange={(event) => githubImport.onUrlChange(event.target.value)} placeholder="https://github.com/owner/repository" spellCheck={false} inputMode="url" />
                </div>
              </div>
              <p className="ct-form-help">仅获取 GitHub 官方 archive，先固定到解析出的 commit；索引 Python、TypeScript 和 TSX 文件。当前环境公网限流时会显示失败原因。</p>
              {githubImport.error ? <p className="ct-inline-error" role="alert">{githubImport.error}</p> : null}
              <div className="ct-form-actions">
                <button type="submit" className="ct-button ct-button-primary" disabled={importBusy(githubImport.status)}>{githubImport.status === "acquiring" ? "获取中…" : githubImport.status === "indexing" ? "建立中…" : "导入 GitHub 仓库"}</button>
                {githubImport.attempt ? <ImportAttemptStatus attempt={githubImport.attempt} onRetry={githubImport.onRetry} onRetryCleanup={githubImport.onRetryCleanup} /> : null}
              </div>
                </form>
              )}
            </>
          ) : (
            <>
          {!selectedProject ? (
            <EmptyState title="先选择项目" detail="索引属于项目；选择项目后才能创建或打开固定版本。" />
          ) : (
            <>
              <div className="ct-operation-context">
                <span><small>PROJECT</small><strong>{selectedProject.name}</strong></span>
                <code>{selectedProject.repository_id}</code>
                <span><small>AVAILABLE</small><strong>{index.indexes.length} 个快照</strong></span>
              </div>
              <div className="ct-index-list">
                {index.indexes.length > 0 ? index.indexes.map((item) => (
                  <button type="button" key={item.index_id} className={`ct-index-item${index.response?.index.index_id === item.index_id ? " is-selected" : ""}`} onClick={() => index.onIndexIdChange(item.index_id)}>
                    <span><strong>{item.revision}</strong><small>{formatDate(item.index_id.split("-").at(-1) ?? "")}</small></span>
                    <code>{shortHash(item.snapshot_hash)}</code>
                    <span className={`ct-index-state is-${item.status}`}>{item.status === "ready" ? "可用" : item.status === "building" ? "建立中" : "失败"}</span>
                  </button>
                )) : <p className="ct-muted">当前项目没有已保存快照。</p>}
              </div>
              {index.error ? <p className="ct-inline-error" role="alert">{index.error}</p> : null}
              <div className="ct-index-forms">
                <form className="ct-operation-form" onSubmit={(event) => void index.onCreate(event)}>
                  <div className="ct-form-heading"><strong>创建新快照</strong><span>{index.status === "creating" ? "建立中" : indexStatusLabel(index.status)}</span></div>
                  <label htmlFor="operation-index-revision">revision</label>
                  <input id="operation-index-revision" value={index.revision} onChange={(event) => index.onRevisionChange(event.target.value)} placeholder="working-tree" spellCheck={false} />
                  <label className="ct-check-field"><input type="checkbox" checked={index.dirty} onChange={(event) => index.onDirtyChange(event.target.checked)} /><span>标记为 dirty</span></label>
                  <button type="submit" className="ct-button ct-button-primary" disabled={index.status === "creating"}>{index.status === "creating" ? "建立中…" : "创建索引"}</button>
                </form>
                <form className="ct-operation-form" onSubmit={(event) => void index.onOpen(event)}>
                  <div className="ct-form-heading"><strong>打开已有快照</strong><span>固定读取</span></div>
                  <label htmlFor="operation-index-id">index_id</label>
                  <input id="operation-index-id" value={index.indexIdInput} onChange={(event) => index.onIndexIdChange(event.target.value)} placeholder="index-timeout-demo-…" spellCheck={false} />
                  <p className="ct-form-help">打开后知识页、图和证据都绑定该 index_id。</p>
                  <button type="submit" className="ct-button ct-button-secondary" disabled={index.status === "loading"}>{index.status === "loading" ? "载入中…" : "打开固定版本"}</button>
                </form>
              </div>
              <div className="ct-index-freshness">
                <div className="ct-form-heading">
                  <strong>源码新鲜度</strong>
                  <span>{index.freshness ? freshnessStatusLabel(index.freshness.status) : "尚未检查"}{!index.isLatest ? " · 仅 latest 可更新" : ""}</span>
                </div>
                <p className="ct-form-help">{index.freshness?.remote_status && index.freshness.remote_status !== "not_applicable" ? "GitHub 检查只解析跟踪 ref 的远端 commit，不下载 archive；发现变化后需显式更新。" : "检查会读取当前纳入索引范围的 Python、TypeScript 和 TSX 文件并计算 hash，不会自动重建；更新时服务端会再次核对。"}</p>
                <div className="ct-form-actions">
                  <button
                    type="button"
                    className="ct-button ct-button-secondary"
                    onClick={() => void index.onCheckFreshness()}
                    disabled={!index.response || index.freshnessStatus !== "idle"}
                  >
                    {index.freshnessStatus === "checking" ? "检查中…" : "检查更新"}
                  </button>
                  <button
                    type="button"
                    className="ct-button ct-button-primary"
                    onClick={() => void (githubUpdateRequired ? index.onUpdateGitHub() : index.onRefresh())}
                    disabled={!index.response || !index.isLatest || index.freshnessStatus !== "idle" || githubCheckUncertain}
                  >
                    {index.freshnessStatus === "refreshing" ? "更新中…" : githubUpdateRequired ? "更新 GitHub 索引" : "更新索引"}
                  </button>
                </div>
                {!index.isLatest ? <p className="ct-form-help">当前打开的是历史快照；可以检查源码，但更新必须先切换到项目 latest。</p> : null}
                {githubCheckUncertain ? <p className="ct-warning">GitHub 远端状态无法确定，已停用更新；请先重试检查。</p> : null}
                {index.freshnessError ? <p className="ct-inline-error" role="alert">{index.freshnessError}</p> : null}
                {index.refreshMode ? <p className="ct-card-note">本次显式更新：{index.refreshMode === "reused" ? "复用当前快照，未重新解析。" : index.refreshMode === "historical_reused" ? "复用兼容历史快照，未重新解析；项目 latest 已切换到该快照，历史元数据保持不变。" : "已生成新的不可变快照；旧快照仍保留。"}</p> : null}
                {index.freshness ? (
                  <div className="ct-freshness-result" data-state={index.freshness.status}>
                    <div className="ct-freshness-meta">
                      <strong>{freshnessStatusLabel(index.freshness.status)}</strong>
                      <span>{index.freshness.checked_at ? formatDate(index.freshness.checked_at) : "未检查"}</span>
                      <code>{index.freshness.baseline_index_id}</code>
                    </div>
                    {index.freshness.remote_status !== "not_applicable" ? (
                      <div className="ct-freshness-remote">
                        <div className="ct-freshness-meta">
                          <strong>{remoteFreshnessStatusLabel(index.freshness.remote_status)}</strong>
                          {index.freshness.remote_source_url ? <code>{index.freshness.remote_source_url}</code> : null}
                        </div>
                        <div className="ct-freshness-stats">
                          <span>跟踪 ref：<strong>{index.freshness.remote_requested_ref ?? "默认分支"}</strong></span>
                          <span>解析 ref：<strong>{index.freshness.remote_resolved_ref ?? "未知"}</strong></span>
                          <span>远端 commit：<strong>{index.freshness.remote_resolved_commit ? shortHash(index.freshness.remote_resolved_commit) : "未知"}</strong></span>
                          <span>基线索引 commit：<strong>{baselineIndex?.revision ? shortHash(baselineIndex.revision) : "未知"}</strong></span>
                        </div>
                      </div>
                    ) : null}
                    <div className="ct-freshness-stats">
                      <span><strong>{index.freshness.added_count}</strong> 新增</span>
                      <span><strong>{index.freshness.modified_count}</strong> 修改</span>
                      <span><strong>{index.freshness.deleted_count}</strong> 删除</span>
                      <span><strong>{index.freshness.scanned_files}</strong> 文件 / {index.freshness.scanned_bytes} bytes</span>
                    </div>
                    {index.freshness.remote_status === "not_applicable" && !index.freshness.scan_complete ? <p className="ct-warning">扫描未完成{index.freshness.scan_truncation_reason ? "：" + index.freshness.scan_truncation_reason : ""}，不能据此判定一致。</p> : null}
                    {canSafelyRebuildLegacyIndex(index.freshness) ? <p className="ct-card-note">旧索引缺少规则/配置元数据；当前来源已完整安全扫描，可通过“更新索引”完成一次全量重建。</p> : null}
                    {index.freshness.changed_paths.length > 0 ? (
                      <div className="ct-freshness-paths">
                        {index.freshness.changed_paths.map((path) => <code key={path}>{path}</code>)}
                        {index.freshness.changed_paths_truncated ? <small>还有未展示路径</small> : null}
                      </div>
                    ) : null}
                    {index.freshness.reasons.length > 0 ? <p className="ct-card-note">原因：{index.freshness.reasons.map(freshnessReasonLabel).join(" · ")}</p> : null}
                  </div>
                ) : <p className="ct-muted">当前索引尚未执行新鲜度检查。</p>}
              </div>
            </>
          )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function ImportAttemptStatus({ attempt, onRetry, onRetryCleanup }: { attempt: ImportAttempt; onRetry: Action; onRetryCleanup?: Action }) {
  return (
    <div className="ct-import-attempt" data-state={attempt.status}>
      <span>{importStatusLabel(attempt.status)}</span>
      <code>{attempt.source_kind === "github" ? shortHash(attempt.resolved_commit ?? attempt.attempt_id) : shortHash(attempt.attempt_id)}</code>
      {attempt.status === "failed" || attempt.status === "interrupted" ? <button type="button" className="ct-link-button" onClick={() => void onRetry()}>重试</button> : null}
      {attempt.cleanup_status === "failed" && onRetryCleanup ? <button type="button" className="ct-link-button" onClick={() => void onRetryCleanup()}>重试清理</button> : null}
      {attempt.status === "ready" ? <small>{attempt.resolved_commit ? `commit ${shortHash(attempt.resolved_commit)} · ` : ""}snapshot {shortHash(attempt.content_hash)}{attempt.cleanup_status === "failed" ? " · 清理待重试" : ""}</small> : null}
    </div>
  );
}

function ContextHeader({
  project,
  index,
  viewMode,
}: {
  project: ProjectUi;
  index: IndexUi;
  viewMode: "overview" | "session";
}) {
  return (
    <div className="ct-context-header">
      <div className="ct-context-copy">
        <p className="ct-overline">{viewMode === "session" ? "ISSUE SESSION" : "PROJECT KNOWLEDGE"}</p>
        <h1>{project.selectedProject?.name ?? "建立一个代码理解工作区"}</h1>
        <p>{project.selectedProject ? <><code>{project.selectedProject.project_id}</code><span className="ct-context-separator">/</span><code>{project.selectedProject.repository_id}</code></> : "从服务端项目开始，浏览固定快照、入口和代码关系。"}</p>
      </div>
      {project.selectedProject ? (
        <div className="ct-context-actions">
          <div className="ct-context-status" data-state={index.status}>
            <span className="ct-status-dot" aria-hidden="true" />
            <span>{indexStatusLabel(index.status)}</span>
            {index.response ? <code>{shortHash(index.response.index.snapshot_hash)}</code> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SearchBlock({ knowledge, disabled }: { knowledge: KnowledgeUi; disabled: boolean }) {
  return (
    <section className="ct-search-block" aria-label="当前快照搜索">
      <form onSubmit={(event) => void knowledge.onSearch(event)}>
        <div className="ct-search-label"><span>SEARCH SNAPSHOT</span><small>仅搜索当前 project_id / index_id</small></div>
        <div className="ct-search-row">
          <input id="workspace-search" value={knowledge.searchQuery} onChange={(event) => knowledge.onSearchQueryChange(event.target.value)} placeholder="输入符号名或源码关键词，例如 load_settings" spellCheck={false} disabled={disabled} />
          <button type="submit" className="ct-button ct-button-primary" disabled={disabled || knowledge.searchStatus === "loading"}>{knowledge.searchStatus === "loading" ? "检索中…" : "检索"}</button>
        </div>
      </form>
      {knowledge.searchError ? <p className="ct-inline-error" role="alert">{knowledge.searchError}</p> : null}
      {knowledge.searchStatus === "empty" ? <p className="ct-search-empty">没有找到匹配的符号或源码。</p> : null}
      {knowledge.searchResults.length > 0 ? (
        <div className="ct-search-results" aria-live="polite">
          <div className="ct-search-results-heading"><strong>{knowledge.searchResults.length} 个候选</strong><span>点击后在右侧查看快照证据</span></div>
          {knowledge.searchResults.map((match) => (
            <button type="button" className="ct-search-result" key={match.symbol.symbol_id} onClick={() => knowledge.onSelectSearchResult(match)}>
              <span className="ct-result-rank">{String(match.rank).padStart(2, "0")}</span>
              <span><strong>{match.symbol.qualified_name}</strong><small>{match.symbol.path}:L{match.symbol.start_line} · {match.symbol.kind}</small></span>
              <span aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function KnowledgeView({
  project,
  index,
  knowledge,
  onOpenOperation,
}: {
  project: ProjectUi;
  index: IndexUi;
  knowledge: KnowledgeUi;
  onOpenOperation: OpenOperation;
}) {
  const overview = knowledge.overview;
  return (
    <div className="ct-page ct-knowledge-page">
      {!project.selectedProject ? (
        <EmptyState
          title="还没有选中的项目"
          detail="项目是所有索引、Session 和证据的归属边界。"
          action={<button type="button" className="ct-button ct-button-primary" onClick={() => onOpenOperation("create-project")}>创建项目</button>}
        />
      ) : !index.response ? (
        <EmptyState
          title="当前项目还没有可用快照"
          detail="可以在“源代码与索引”的“固定索引”中创建或打开一个固定版本；没有索引时不会伪造概览或关系图。"
          action={<button type="button" className="ct-button ct-button-primary" onClick={() => onOpenOperation("project-management", "index")}>打开源代码与索引</button>}
        />
      ) : (
        <>
          {knowledge.overviewStatus === "loading" ? <LoadingNote>正在从当前不可变快照生成概览…</LoadingNote> : null}
          {knowledge.overviewStatus === "error" ? (
            <div className="ct-error-block" role="alert"><div><strong>项目概览请求失败</strong><span>{knowledge.overviewError ?? "请重试或切换到其他索引。"}</span></div><button type="button" className="ct-button ct-button-secondary" onClick={() => void knowledge.onRetryOverview()}>重试</button></div>
          ) : overview ? (
            <>
              <ArchitectureSection key={`${overview.project_id}:${overview.index.index_id}`} overview={overview} knowledge={knowledge} />
              <div className="ct-index-strip" aria-label="当前固定索引">
                <div className="ct-index-strip-primary"><span className="ct-status-dot" aria-hidden="true" /><strong>{indexStatusLabel(index.status)}</strong></div>
                <span><small>REVISION</small><code>{index.response.index.revision}</code></span>
                <span><small>INDEX</small><code title={index.response.index.index_id}>{index.response.index.index_id}</code></span>
                <span><small>SNAPSHOT</small><code title={index.response.index.snapshot_hash}>{shortHash(index.response.index.snapshot_hash)}</code></span>
                <span><small>WORKTREE</small><code>{index.response.index.dirty ? "DIRTY" : "CLEAN"}</code></span>
              </div>
              <div className="ct-knowledge-content">
                <SearchBlock knowledge={knowledge} disabled={!index.response} />
                <OverviewSummary overview={overview} />
                <FeatureSection knowledge={knowledge} />
                <OverviewSections overview={overview} knowledge={knowledge} />
              </div>
            </>
          ) : (
            <EmptyState title="等待项目概览" detail="概览绑定同一 project_id 与 index_id，不执行来源仓库代码。" />
          )}
        </>
      )}
    </div>
  );
}

type ArchitectureDirectoryNodeData = {
  directory: ArchitectureDirectory;
  selected: boolean;
  onSelect: (group: OverviewGroup) => void;
  onToggle: (group: OverviewGroup) => void;
};

type ArchitectureModuleNodeData = {
  module: OverviewModule;
  highlighted: boolean;
  onOpen: (module: OverviewModule) => void;
};

function ArchitectureDirectoryNode({ data }: NodeProps<Node<ArchitectureDirectoryNodeData, "architecture-directory">>) {
  const { directory } = data;
  return (
    <div className={`ct-architecture-directory${data.selected ? " is-selected" : ""}${directory.highlighted ? " is-highlighted" : " is-dimmed"}`}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="ct-architecture-directory-heading">
        <button type="button" className="ct-architecture-directory-title" onClick={() => data.onSelect(directory.group)} title={directory.group.group_key}>
          <span>SOURCE DIRECTORY</span><strong>{directory.group.group_key}</strong>
        </button>
        <button type="button" className="ct-architecture-expand" onClick={() => data.onToggle(directory.group)} aria-label={`${directory.expanded ? "折叠" : "展开"} ${directory.group.group_key}`} aria-expanded={directory.expanded}>
          {directory.expanded ? "−" : "+"}
        </button>
      </div>
      <div className="ct-architecture-directory-meta">
        <span>{directory.loadedModuleCount} modules</span>
        {directory.internalRelationCount > 0 ? <span>{directory.internalRelationCount} 内部关系 · {directory.internalEdgeCount} 边</span> : null}
        {directory.internalHttpAssociationCount > 0 ? <span>{directory.internalHttpAssociationCount} 内部 HTTP 关联</span> : null}
        {directory.omittedIndexModuleCount > 0 ? <span>索引未返回 {directory.omittedIndexModuleCount}</span> : null}
        {directory.omittedByCanvasLimit > 0 ? <span>画布限额省略 {directory.omittedByCanvasLimit}</span> : null}
      </div>
    </div>
  );
}

function ArchitectureModuleNode({ data }: NodeProps<Node<ArchitectureModuleNodeData, "architecture-module">>) {
  return (
    <button type="button" className={`ct-architecture-module${data.highlighted ? " is-highlighted" : " is-dimmed"}`} onClick={() => data.onOpen(data.module)} title={`${data.module.symbol.path}:L${data.module.symbol.start_line} · 打开模块关系图`}>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <strong>{data.module.symbol.qualified_name}</strong>
      <small>{data.module.symbol.path}:L{data.module.symbol.start_line}</small>
      <span>{data.module.entry_reachable ? "入口可达" : "静态模块"} · 打开关系图 ↗</span>
    </button>
  );
}

const architectureNodeTypes: NodeTypes = {
  "architecture-directory": ArchitectureDirectoryNode,
  "architecture-module": ArchitectureModuleNode,
};

function ArchitectureSection({ overview, knowledge }: { overview: ProjectOverview; knowledge: KnowledgeUi }) {
  const [expandedGroupIds, setExpandedGroupIds] = useState<string[]>([]);
  const [selectedHttpGroupId, setSelectedHttpGroupId] = useState<string | null>(null);
  const [relationKinds, setRelationKinds] = useState<Array<"imports" | "calls">>(["imports", "calls"]);
  const [showHttpAssociations, setShowHttpAssociations] = useState(true);
  const [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const httpGroups = overview.groups.filter((group) => group.group_kind === "http_path" && group.index_id === overview.index.index_id);
  const model = useMemo(() => buildArchitectureGraph(overview, {
    expandedGroupIds,
    selectedHttpGroupId,
    selectedGroupId: knowledge.selectedOverviewGroupId,
    relationKinds,
    showHttpAssociations,
  }), [expandedGroupIds, knowledge.selectedOverviewGroupId, overview, relationKinds, selectedHttpGroupId, showHttpAssociations]);
  const focusedDirectoryNodeIds = useMemo(() => {
    const focused = new Set(model.focusNodeIds);
    return model.directories.filter((directory) => focused.has(directory.group.group_id)).map((directory) => directory.group.group_id);
  }, [model.directories, model.focusNodeIds]);
  useEffect(() => {
    if (!flow || model.directories.length === 0) return;
    const timer = window.setTimeout(() => {
      const nodeIds = focusedDirectoryNodeIds.length > 0
        ? focusedDirectoryNodeIds
        : model.directories.map((directory) => directory.group.group_id);
      void flow.fitView({ nodes: nodeIds.map((id) => ({ id })), padding: focusedDirectoryNodeIds.length > 0 ? 0.3 : 0.16, duration: 260, minZoom: 0.28, maxZoom: 1.15 });
    }, 100);
    return () => window.clearTimeout(timer);
  }, [flow, focusedDirectoryNodeIds, model.directories]);
  const reactFlowModel = useMemo(() => {
    const nodes: Node[] = model.directories.map((directory) => ({
      id: directory.group.group_id,
      type: "architecture-directory",
      position: directory.position,
      data: {
        directory,
        selected: knowledge.selectedOverviewGroupId === directory.group.group_id,
        onSelect: (group: OverviewGroup) => {
          setSelectedHttpGroupId(null);
          knowledge.onSelectOverviewGroup(group);
        },
        onToggle: (group: OverviewGroup) => {
          setSelectedHttpGroupId(null);
          knowledge.onSelectOverviewGroup(group);
          setExpandedGroupIds((current) => current.includes(group.group_id)
            ? current.filter((item) => item !== group.group_id)
            : [...current, group.group_id]);
        },
      },
      style: { width: directory.width, height: directory.height },
      className: "ct-architecture-directory-node",
      zIndex: 0,
    }));
    for (const directory of model.directories) {
      for (const visible of directory.visibleModules) {
        nodes.push({
          id: visible.module.module_id,
          type: "architecture-module",
          parentId: directory.group.group_id,
          extent: "parent",
          position: visible.position,
          data: {
            module: visible.module,
            highlighted: visible.highlighted,
            onOpen: knowledge.onOpenOverviewModule,
          },
          style: { width: 156, height: 68 },
          className: "ct-architecture-module-node",
          zIndex: 2,
        });
      }
    }
    const staticEdges: Edge[] = model.relations.map((relation) => ({
      id: relation.id,
      source: relation.source,
      target: relation.target,
      type: "smoothstep",
      label: `${relation.relationKinds.join(" + ")} · ${relation.relationCount} 对 / ${relation.edgeCount} 边`,
      markerEnd: { type: MarkerType.ArrowClosed, color: relation.highlighted ? "#3158d9" : "#b2bac7" },
      style: { stroke: relation.highlighted ? "#3158d9" : "#aeb7c5", strokeWidth: relation.highlighted ? 1.8 : 1.1, opacity: relation.highlighted ? 0.78 : 0.18 },
      labelStyle: { fill: relation.highlighted ? "#43516a" : "#8b93a0", fontSize: 10 },
      labelBgStyle: { fill: "#fff", fillOpacity: relation.highlighted ? 0.92 : 0.55 },
      interactionWidth: 18,
      data: { relation },
    }));
    const httpEdges: Edge[] = model.httpRelations.map((relation) => ({
      id: relation.id,
      source: relation.source,
      target: relation.target,
      type: "smoothstep",
      label: `HTTP ${relation.labels.join(" · ")}${relation.associationCount > relation.labels.length ? ` · +${relation.associationCount - relation.labels.length}` : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: relation.highlighted ? "#087f75" : "#9ba7a9" },
      style: { stroke: relation.highlighted ? "#087f75" : "#9ba7a9", strokeWidth: 2, strokeDasharray: "6 4", opacity: relation.highlighted ? 0.95 : 0.35 },
      labelStyle: { fill: relation.highlighted ? "#075e57" : "#778487", fontSize: 10 },
      labelBgStyle: { fill: "#f5fbfa", fillOpacity: 0.96 },
      interactionWidth: 20,
      data: { httpRelation: relation },
    }));
    return { nodes, edges: [...staticEdges, ...httpEdges] };
  }, [knowledge, model, overview.index.index_id]);
  const coverageWarnings = [
    overview.truncated ? `概览已截断：${overview.truncation_reason ?? "服务端预算"}` : null,
    overview.coverage.visible_groups < overview.coverage.total_groups ? `概览仅返回 ${overview.coverage.visible_groups}/${overview.coverage.total_groups} 组（含 HTTP 路由组）` : null,
    overview.coverage.visible_modules < overview.coverage.total_modules ? `概览仅返回 ${overview.coverage.visible_modules}/${overview.coverage.total_modules} 个模块` : null,
    overview.coverage.visible_module_relations < overview.coverage.total_module_relations ? `概览仅返回 ${overview.coverage.visible_module_relations}/${overview.coverage.total_module_relations} 条模块关系` : null,
    overview.unresolved.length > 0 ? `${overview.unresolved.length} 条 unresolved 保留为未知，不绘制成确定依赖` : null,
    model.omittedRelationCount > 0 ? `${model.omittedRelationCount} 条关系（${model.omittedRelationEdgeCount} 边）因端点不在可见源码目录中未绘制` : null,
    model.unmatchedHttpModuleCount > 0 ? `所选 HTTP 路由有 ${model.unmatchedHttpModuleCount} 个模块未映射到可见源码目录` : null,
    model.omittedHttpAssociationCount > 0 ? `${model.omittedHttpAssociationCount} 条 HTTP 形状关联未绘制，因为端点目录不在可见分组中；下方双端证据列表仍保留` : null,
    model.summarizedHttpAssociationCount > 0 ? `${model.summarizedHttpAssociationCount} 条 HTTP 形状关联因 40 模块画布上限折叠到目录节点` : null,
    overview.http_association_coverage.analysis_status === "not_analyzed" ? "此旧索引没有 TypeScript HTTP 分析记录，不能将 0 条关联解释为无调用" : null,
    overview.http_association_coverage.analysis_status === "incomplete" ? "TypeScript HTTP 形状关联分析不完整，部分客户端或路由仍为 unknown" : null,
    overview.http_association_coverage.visible_match_count < overview.http_association_coverage.matched_count ? `HTTP 形状关联仅显示 ${overview.http_association_coverage.visible_match_count}/${overview.http_association_coverage.matched_count} 条` : null,
  ].filter((item): item is string => item !== null);
  const selectHttpGroup = (groupId: string) => {
    const group = httpGroups.find((item) => item.group_id === groupId) ?? null;
    setSelectedHttpGroupId(group?.group_id ?? null);
    if (group) {
      knowledge.onSelectOverviewGroup(group);
      const moduleIds = new Set(group.module_ids);
      const directoryIds = model.directories
        .filter((directory) => directory.group.module_ids.some((moduleId) => moduleIds.has(moduleId)))
        .map((directory) => directory.group.group_id);
      setExpandedGroupIds((current) => [...new Set([...current, ...directoryIds])]);
    } else {
      knowledge.onClearOverviewGroup();
    }
  };
  const toggleRelationKind = (kind: "imports" | "calls") => {
    setRelationKinds((current) => current.includes(kind)
      ? current.length === 1 ? current : current.filter((item) => item !== kind)
      : [...current, kind]);
  };
  const focusGraph = () => {
    if (!flow || reactFlowModel.nodes.length === 0) return;
    const nodeIds = focusedDirectoryNodeIds.length > 0 ? focusedDirectoryNodeIds : model.directories.map((directory) => directory.group.group_id);
    void flow.fitView({ nodes: nodeIds.map((id) => ({ id })), padding: 0.18, duration: 240 });
  };

  return (
    <section className="ct-architecture-section" aria-label="项目架构画布">
      <div className="ct-architecture-heading">
        <div><p className="ct-overline">ARCHITECTURE / FIXED INDEX</p><h3>项目架构</h3><p>先看源码目录间 imports/calls；HTTP 客户端到服务路由以独立方法与路径形状关联展示。两端证据均来自同一固定快照。</p></div>
        <div className="ct-architecture-count"><strong>{model.directories.length}</strong><span>源码目录 · {overview.coverage.visible_modules}/{overview.coverage.total_modules} 模块可见</span></div>
      </div>
      <div className="ct-architecture-toolbar">
        <label>HTTP 入口<select aria-label="筛选 HTTP 入口" value={selectedHttpGroupId ?? ""} onChange={(event) => selectHttpGroup(event.target.value)}><option value="">全部源码依赖</option>{httpGroups.map((group) => <option value={group.group_id} key={group.group_id}>{group.label} · {group.module_ids.length} modules</option>)}</select></label>
        <label className="ct-architecture-kind"><input type="checkbox" checked={relationKinds.includes("imports")} onChange={() => toggleRelationKind("imports")} /><span>imports</span></label>
        <label className="ct-architecture-kind"><input type="checkbox" checked={relationKinds.includes("calls")} onChange={() => toggleRelationKind("calls")} /><span>calls</span></label>
        <label className="ct-architecture-kind ct-architecture-http-kind"><input type="checkbox" checked={showHttpAssociations} onChange={() => setShowHttpAssociations((current) => !current)} /><span>HTTP 形状</span></label>
        <button type="button" className="ct-button ct-button-secondary" onClick={focusGraph} disabled={reactFlowModel.nodes.length === 0}>聚焦画布</button>
        <button type="button" className="ct-link-button" onClick={() => {
          setExpandedGroupIds([]);
          setSelectedHttpGroupId(null);
          setRelationKinds(["imports", "calls"]);
          setShowHttpAssociations(true);
          knowledge.onClearOverviewGroup();
        }}>重置画布</button>
      </div>
      <p className="ct-architecture-filter-note">类型筛选只影响静态 imports/calls 画布。HTTP method/path shape 关联单独列示，不混入静态调用边，也不证明请求实际发送到本地服务。</p>
      {coverageWarnings.length > 0 ? <div className="ct-architecture-warnings" role="status">{coverageWarnings.map((warning) => <span key={warning}>{warning}</span>)}</div> : null}
      {model.directories.length === 0 ? (
        <div className="ct-architecture-empty"><strong>当前索引没有可绘制的源码目录</strong><span>概览可能未返回源码分组；不以 HTTP 路由组替代源码架构。</span></div>
      ) : (
        <div className="ct-architecture-canvas">
          <ReactFlow<Node, Edge>
            nodes={reactFlowModel.nodes}
            edges={reactFlowModel.edges}
            nodeTypes={architectureNodeTypes}
            onInit={setFlow}
            onEdgeClick={(_event, edge) => {
              const httpRelation = model.httpRelations.find((item) => item.id === edge.id);
              if (httpRelation?.representative) {
                const evidence = httpRelation.representative.client_evidence;
                knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: evidence.path, startLine: evidence.start_line, endLine: evidence.end_line, expectedContentHash: evidence.content_hash });
                return;
              }
              const relation = model.relations.find((item) => item.id === edge.id);
              if (relation?.representative) knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, ...relation.representative });
            }}
            nodesConnectable={false}
            nodesDraggable={false}
            elementsSelectable
            minZoom={0.18}
            maxZoom={1.8}
            fitView
            fitViewOptions={{ padding: 0.16, minZoom: 0.18, maxZoom: 0.9 }}
          >
            <Background color="#d7dee8" gap={28} size={1} />
            <Controls showInteractive={false} position="bottom-left" />
            <MiniMap pannable zoomable nodeColor={(node) => node.type === "architecture-directory" ? "#aab5c4" : "#3158d9"} maskColor="rgba(247, 248, 250, 0.78)" position="bottom-right" />
          </ReactFlow>
        </div>
      )}
      <section className="ct-http-associations" aria-label="HTTP 客户端与服务路由形状关联">
        <div className="ct-http-associations-heading">
          <div><strong>HTTP 方法与路径形状关联</strong><span>静态 requestJson 调用与 FastAPI 声明路由相同，不确认运行时网络目标。</span></div>
          <small>{overview.http_association_coverage.visible_match_count}/{overview.http_association_coverage.matched_count} matched · {overview.http_association_coverage.unknown_count} unknown</small>
        </div>
        {overview.http_associations.length === 0 ? (
          <p className="ct-http-associations-empty">{overview.http_association_coverage.analysis_status === "not_analyzed" ? "此索引创建早于 TypeScript HTTP 分析，关联状态未知。" : "当前快照没有可显示的唯一静态 HTTP 形状关联。"}</p>
        ) : (
          <ul className="ct-http-association-list">
            {overview.http_associations.map((association) => (
              <li key={association.association_id}>
                <div className="ct-http-association-shape"><strong>{association.method}</strong><code>{association.path}</code><span>形状匹配</span></div>
                <div className="ct-http-association-endpoint"><small>客户端 · {association.client_name}</small><code>{association.client_module.path}</code><button type="button" onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: association.client_evidence.path, startLine: association.client_evidence.start_line, endLine: association.client_evidence.end_line, expectedContentHash: association.client_evidence.content_hash })}>查看客户端证据</button></div>
                <div className="ct-http-association-endpoint"><small>FastAPI 路由</small><code>{association.server_module.path}</code><button type="button" onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: association.route_evidence.path, startLine: association.route_evidence.start_line, endLine: association.route_evidence.end_line, expectedContentHash: association.route_evidence.content_hash })}>查看路由证据</button></div>
              </li>
            ))}
          </ul>
        )}
        <p className="ct-http-associations-coverage">覆盖 {overview.http_association_coverage.client_request_count} 个已识别 requestJson 调用：{overview.http_association_coverage.unmatched_count} unmatched、{overview.http_association_coverage.ambiguous_count} ambiguous、{overview.http_association_coverage.unknown_count} unknown。</p>
      </section>
      <div className="ct-architecture-legend"><span><i className="is-directory" />源码目录（可展开）</span><span><i className="is-architecture-edge" />静态 Python/TypeScript imports / calls</span><span><i className="is-http-architecture-edge" />HTTP method/path 形状关联</span><span>HTTP 关联单独筛选与聚合，不写入 CodeEdge</span><small>TypeScript 静态事实使用锁定语法解析器；HTTP 匹配不等同于实际运行调用或本地部署拓扑。</small></div>
    </section>
  );
}

function OverviewSummary({ overview }: { overview: ProjectOverview }) {
  return (
    <section className="ct-summary-card">
      <div className="ct-summary-copy">
        <p className="ct-overline">RULES / {overview.rules_version}</p>
        <h3>{overview.entry_status === "not_identified" ? "没有命中当前入口规则" : `${overview.entries.length} 个入口候选`}</h3>
        <p>只展示当前固定索引的静态事实；未识别不等于项目没有入口。</p>
      </div>
      <div className="ct-metric-grid">
        <span><strong>{overview.coverage.reachable_modules}</strong><small>reachable modules</small></span>
        <span><strong>{overview.coverage.unreachable_modules}</strong><small>other modules</small></span>
        <span><strong>{overview.paths.length}</strong><small>entry paths</small></span>
        <span><strong>{overview.unresolved.length}</strong><small>unresolved</small></span>
      </div>
      {overview.truncated ? <p className="ct-warning">结果已截断：{overview.truncation_reason}</p> : null}
    </section>
  );
}

function FeatureSection({ knowledge }: { knowledge: KnowledgeUi }) {
  const generation = knowledge.featureGeneration;
  return (
    <section className="ct-section ct-feature-section" aria-label="主要功能语义识别">
      <div className="ct-section-heading">
        <div><p className="ct-overline">O2 / FEATURE SEMANTICS</p><h3>项目功能</h3></div>
        <div className="ct-section-heading-actions">
          {generation ? <button type="button" className="ct-button ct-button-secondary" onClick={() => void knowledge.onOpenFeatureGraph()}>打开功能图</button> : null}
          <span className="ct-count-label">{generation?.features.length ?? "—"}</span>
        </div>
      </div>
      <p className="ct-section-lede">显式选择 fixture 或 real 后，服务端只使用当前索引中实际返回的入口、模块、符号和源码证据生成有边界的描述。</p>
      <div className="ct-history-block">
        <div className="ct-history-heading"><strong>已保存版本 · 当前快照</strong><span data-state={knowledge.featureHistoryStatus}>{knowledge.featureHistoryStatus === "loading" ? "载入中" : `${knowledge.featureHistory.length} 个版本`}</span></div>
        {knowledge.featureHistoryError ? <div className="ct-error-block compact" role="alert"><span>{knowledge.featureHistoryError}</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onRetryFeatureHistory()}>重试</button></div> : null}
        {knowledge.featureHistory.length > 0 ? (
          <div className="ct-history-list">
            {knowledge.featureHistory.map((item) => (
              <button type="button" key={item.generation_id} className={`ct-history-item${item.generation_id === knowledge.selectedFeatureGenerationId ? " is-selected" : ""}`} onClick={() => knowledge.onSelectFeatureGeneration(item.generation_id)} aria-pressed={item.generation_id === knowledge.selectedFeatureGenerationId}>
                <span className="ct-history-top"><strong>{savedFeatureStatusLabel(item.status)}</strong><span>{item.mode === "fixture" ? "FIXTURE" : "REAL"} · {formatDate(item.created_at)}</span></span>
                <span className="ct-history-id"><code>{item.generation_id}</code><code>{item.index.revision} · {shortHash(item.index.snapshot_hash)}</code></span>
                <span className="ct-history-stats">{item.feature_count} 功能 · {item.evidence_count} 证据 · {item.rules_version}</span>
                <span className="ct-history-summary">{item.summary}</span>
                {item.error ? <span className="ct-history-error">{item.error.message}</span> : null}
              </button>
            ))}
          </div>
        ) : knowledge.featureHistoryStatus === "empty" ? <p className="ct-muted">当前 index_id 没有已保存版本；点击下方按钮显式生成。</p> : null}
        {knowledge.featureHistoryHasMore ? <button type="button" className="ct-link-button" onClick={() => void knowledge.onLoadMoreFeatureHistory()} disabled={knowledge.featureHistoryStatus === "loading"}>加载更多版本</button> : null}
      </div>
      <div className="ct-feature-controls">
        <label>模式<select value={knowledge.featureMode} onChange={(event) => knowledge.onFeatureModeChange(event.target.value as FeatureGenerationMode)} disabled={knowledge.featureStatus === "loading"}><option value="fixture">fixture / 离线</option><option value="real">real / 服务端模型</option></select></label>
        <label>源码范围<select value={knowledge.featureSourceScope} onChange={(event) => knowledge.onFeatureSourceScopeChange(event.target.value as FeatureSourceScope)} disabled={knowledge.featureStatus === "loading"}><option value="entry_reachable">入口可达模块</option><option value="visible_modules">当前可见模块</option></select></label>
        <button type="button" className="ct-button ct-button-primary" onClick={() => void knowledge.onRunFeatureGeneration()} disabled={knowledge.featureStatus === "loading"}>{knowledge.featureStatus === "loading" ? "生成中…" : "识别主要功能"}</button>
        {generation ? <button type="button" className="ct-button ct-button-secondary" onClick={() => void knowledge.onReloadFeatureGeneration()} disabled={knowledge.featureStatus === "loading"}>重读已保存结果</button> : null}
      </div>
      <div className="ct-feature-status"><span data-state={knowledge.featureStatus}>{featureStatusLabel(knowledge.featureStatus, generation)}</span><small>{knowledge.featureSourceScope === "entry_reachable" ? "只查询入口可达模块；未查询模块不会被当作不存在。" : "在当前可见模块内查询，仍受工具、源码、输入和输出预算限制。"}</small></div>
      {knowledge.featureStatus === "loading" ? <LoadingNote>正在收集有界证据并生成语义摘要…</LoadingNote> : null}
      {knowledge.featureError && !generation ? <div className="ct-error-block compact" role="alert"><span>{knowledge.featureError}</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onRunFeatureGeneration()}>重试</button></div> : null}
      {generation ? <FeatureGenerationResult generation={generation} knowledge={knowledge} /> : null}
    </section>
  );
}

function FeatureGenerationResult({ generation, knowledge }: { generation: FeatureGeneration; knowledge: KnowledgeUi }) {
  return (
    <div className="ct-feature-result">
      <div className="ct-feature-meta"><span className={`ct-badge is-${generation.status}`}>{generation.status}</span><span className={`ct-badge is-${generation.mode}`}>{generation.mode === "fixture" ? "FIXTURE / OFFLINE" : "REAL / SERVER CONFIG"}</span><code title={generation.model ?? "未配置模型"}>{generation.model ?? "未配置模型"}</code><small>{generation.source_paths.length} 个源码文件 · {generation.usage.tool_calls} 次工具 · {generation.usage.model_calls} 次模型 · {generation.usage.elapsed_ms} ms</small></div>
      <p className="ct-feature-summary">{generation.summary}</p>
      {generation.error ? <div className="ct-error-block compact" role="alert"><span>{generation.error.message}</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onRunFeatureGeneration(true)}>重试</button></div> : null}
      {generation.truncated ? <p className="ct-warning">上下文已截断：{generation.truncation_reason}</p> : null}
      {generation.omitted_module_ids.length > 0 ? <p className="ct-warning">未查询模块 {generation.omitted_module_ids.length} 个；它们未被当作没有功能。</p> : null}
      {generation.features.length > 0 ? (
        <div className="ct-feature-list">
          {generation.features.map((feature) => (
            <article className="ct-feature-card" key={feature.feature_id}>
              <div className="ct-feature-card-heading"><h4>{feature.name}</h4><span>{feature.symbol_ids.length} symbols · {feature.evidence_ids.length} evidence</span></div>
              <p>{feature.description}</p>
              <div className="ct-ref-list"><span>入口 {feature.entry_point_ids.length}</span><span>模块 {feature.module_ids.length}</span><span>静态符号 {feature.symbol_ids.length}</span></div>
              <div className="ct-evidence-links">
                {feature.evidence_ids.map((evidenceId) => {
                  const evidence = generation.evidence.find((item) => item.evidence_id === evidenceId);
                  return evidence ? <button type="button" key={evidenceId} onClick={() => knowledge.onSelectFeatureEvidence(evidence)}>{evidence.path}:L{evidence.start_line}–L{evidence.end_line}</button> : <span key={evidenceId}>缺少证据 {evidenceId}</span>;
                })}
              </div>
              {feature.limitations.length > 0 ? <small className="ct-card-note">{feature.limitations.join(" · ")}</small> : null}
            </article>
          ))}
        </div>
      ) : <p className="ct-muted">{generation.status === "insufficient_evidence" ? "当前快照没有收集到足够的入口或源码证据，未生成功能结论。" : "本次没有返回可展示的功能。"}</p>}
      {generation.associations.length > 0 ? <details className="ct-associations"><summary>查看 {generation.associations.length} 条功能关联（与静态 CodeEdge 分开）</summary>{generation.associations.map((association) => <div className="ct-association" key={association.association_id}><code>{association.relation}</code><span>{association.target_kind}:{association.target_id}</span><small>{association.evidence_ids.length} 条证据</small></div>)}</details> : null}
      {generation.limitations.length > 0 ? <div className="ct-limitations"><strong>边界与限制</strong>{generation.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}</div> : null}
    </div>
  );
}

function OverviewEntryRow({ entry, knowledge }: { entry: OverviewEntryPoint; knowledge: KnowledgeUi }) {
  const isRoute = entry.kind === "fastapi_route";
  return (
    <div className={`ct-entry-row${knowledge.selectedOverviewEntryId === entry.entry_point_id ? " is-selected" : ""}`}>
      <button type="button" className="ct-entry-main" onClick={() => knowledge.onSelectOverviewEntry(entry)}>
        <span className="ct-kind-chip">{isRoute ? "HTTP" : entry.kind === "python_main_module" ? "MODULE" : "GUARD"}</span>
        <span>
          <strong>{isRoute ? `${entry.http_method} ${entry.declared_path}` : entry.label}</strong>
          <small>{isRoute ? `处理函数 ${entry.handler_symbol?.qualified_name ?? entry.label} · ${entry.path}:L${entry.start_line}–L${entry.end_line} · 声明路径（未解析挂载前缀）` : `${entry.path}:L${entry.start_line}–L${entry.end_line} · ${entry.rule_id}`}</small>
        </span>
        <span aria-hidden="true">↗</span>
      </button>
      <button type="button" className="ct-link-button" onClick={() => knowledge.onOpenOverviewGraph(entry)} disabled={!entry.symbol_id}>关系图</button>
    </div>
  );
}

function OverviewModuleRow({ module, knowledge }: { module: OverviewModule; knowledge: KnowledgeUi }) {
  return (
    <button type="button" className="ct-module-row" onClick={() => knowledge.onOpenOverviewModule(module)}>
      <span className={`ct-reachability${module.entry_reachable ? " is-reachable" : ""}`}>{module.entry_reachable ? "REACHABLE" : "OTHER"}</span>
      <span>
        <strong>{module.symbol.qualified_name}</strong>
        <small>{module.symbol.path} · {module.entry_point_ids.length > 0 ? `${module.entry_point_ids.length} 个入口` : "未从入口路径到达"}</small>
      </span>
      <span aria-hidden="true">↗</span>
    </button>
  );
}

function OverviewModuleRelationList({ relations, onOpenSource }: { relations: OverviewModuleRelation[]; onOpenSource: (location: { path: string; startLine: number; endLine: number }) => void }) {
  if (relations.length === 0) {
    return <p className="ct-muted">当前可见结构没有可确认的跨模块 imports/calls 边。</p>;
  }
  return (
    <div className="ct-structure-relation-list">
      {relations.map((relation) => {
        const representative = relation.edges.find((edge) => edge.index_id === relation.index_id) ?? null;
        const locations = relation.edges
          .slice(0, 3)
          .map((edge) => `${edge.source_location.path}:L${edge.source_location.start_line}`)
          .join(" · ");
        const extraLocations = relation.edge_count - relation.edges.length;
        return (
          <article className="ct-structure-relation" key={relation.relation_id}>
            <div className="ct-structure-relation-main">
              <strong>{relation.source_module.qualified_name}</strong>
              <span aria-hidden="true">→</span>
              <strong>{relation.target_module.qualified_name}</strong>
            </div>
            <div className="ct-structure-relation-meta">
              <span className="ct-kind-chip">{relation.relation_kinds.join(" + ")}</span>
              <small>{relation.edge_count} 条静态边 · {locations}{extraLocations > 0 ? ` · 代表位置 +${extraLocations}` : ""}</small>
              {representative ? <button type="button" className="ct-link-button" onClick={() => onOpenSource({ path: representative.source_location.path, startLine: representative.source_location.start_line, endLine: representative.source_location.end_line })}>打开代表源码</button> : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function OverviewSections({ overview, knowledge }: { overview: ProjectOverview; knowledge: KnowledgeUi }) {
  const entryById = new Map(overview.entries.map((entry) => [entry.entry_point_id, entry]));
  const moduleById = new Map(overview.modules.map((module) => [module.module_id, module]));
  const selectedGroup = overview.groups.find((group) => group.group_id === knowledge.selectedOverviewGroupId) ?? null;
  const selectedEntries = selectedGroup
    ? selectedGroup.entry_point_ids.map((entryId) => entryById.get(entryId)).filter((entry): entry is OverviewEntryPoint => entry !== undefined)
    : [];
  const selectedModules = selectedGroup
    ? selectedGroup.module_ids.map((moduleId) => moduleById.get(moduleId)).filter((module): module is OverviewModule => module !== undefined)
    : [];
  const relatedRelations = selectedGroup
    ? overview.module_relations.filter((relation) => selectedGroup.module_ids.includes(relation.source_module_id) || selectedGroup.module_ids.includes(relation.target_module_id))
    : overview.module_relations;
  return (
    <>
      <section className="ct-section ct-structure-section" aria-label="所选结构详情">
        <div className="ct-section-heading">
          <div><p className="ct-overline">STRUCTURE DETAILS / FIXED INDEX</p><h3>结构详情</h3></div>
          <span className="ct-count-label">{overview.coverage.visible_groups}/{overview.coverage.total_groups} 组可见</span>
        </div>
        {selectedGroup ? (
          <div className="ct-structure-detail">
            <div className="ct-structure-detail-heading">
              <div><p className="ct-overline">SELECTED GROUP</p><h4>{selectedGroup.label}</h4></div>
              <button type="button" className="ct-link-button" onClick={knowledge.onClearOverviewGroup}>返回结构总览</button>
            </div>
            {selectedGroup.group_kind === "http_path" ? (
              selectedEntries.length > 0 ? <div className="ct-entry-list">{selectedEntries.map((entry) => <OverviewEntryRow entry={entry} knowledge={knowledge} key={entry.entry_point_id} />)}</div> : <p className="ct-muted">该组入口因当前概览预算未返回。</p>
            ) : (
              selectedModules.length > 0 ? <div className="ct-module-list">{selectedModules.map((module) => <OverviewModuleRow module={module} knowledge={knowledge} key={module.module_id} />)}</div> : <p className="ct-muted">该源码目录的模块未包含在当前可见模块预算中。</p>
            )}
            <div className="ct-structure-related">
              <div className="ct-structure-subheading"><strong>相关模块依赖</strong><span>{relatedRelations.length} 条可见关系</span></div>
              <OverviewModuleRelationList relations={relatedRelations.slice(0, 12)} onOpenSource={(location) => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, ...location })} />
              {relatedRelations.length > 12 ? <p className="ct-muted">仅显示 12 条关系；可见关系仍受模块关系预算限制。</p> : null}
            </div>
          </div>
        ) : null}
      </section>
      <section className="ct-section ct-boundaries" aria-label="边界与限制">
        <div className="ct-section-heading"><div><p className="ct-overline">BOUNDARIES</p><h3>边界与限制</h3></div><span className="ct-count-label">{overview.notes.length}</span></div>
        {overview.notes.map((note) => <p key={note}>{note}</p>)}
      </section>
    </>
  );
}

function runStatusLabel(status: RunRecord["status"]): string {
  return {
    queued: "排队中",
    running: "执行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  }[status];
}

function runStreamStatusLabel(status: SessionUi["runStreamStatus"]): string {
  return {
    idle: "未连接",
    connecting: "实时连接中",
    connected: "实时已连接",
    reconnecting: "实时重连中",
    fallback: "已回退轮询",
    closed: "实时已结束",
  }[status];
}

function runScenarioLabel(scenario: RunScenario): string {
  return {
    "success-timeout-missing": "候选：timeout 缺失",
    "insufficient-external-service": "证据不足：外部服务",
    "tool-failure-read-source": "工具失败：read_source",
  }[scenario];
}

function runModeLabel(run: RunRecord): string {
  if (run.execution_source === "codex" || run.mode === "external") {
    return "CODEX / 外部 Agent";
  }
  return run.mode === "real" ? "REAL / 模型分析" : "FIXTURE / 离线";
}

function explanationStepKindLabel(kind: CodeExplanation["steps"][number]["kind"]): string {
  return {
    confirmed: "已确认",
    inferred: "推断",
    unknown: "未确认",
  }[kind];
}

function RunExplanation({
  explanation,
  evidence,
  onSelectEvidence,
  onSelectSymbol,
}: {
  explanation: CodeExplanation;
  evidence: Evidence[];
  onSelectEvidence: (item: Evidence) => void;
  onSelectSymbol: (symbolId: string) => void;
}) {
  return (
    <div className="ct-run-explanation">
      <div className="ct-run-explanation-heading">
        <small>流程解释</small>
        <span className={explanation.coverage === "partial" ? "is-partial" : ""}>
          {explanation.coverage === "complete" ? "所问范围内完整" : "部分覆盖"}
        </span>
      </div>
      {explanation.coverage === "partial" ? <p className="ct-run-explanation-note">存在未确认环节；以下步骤只覆盖当前工具返回的证据范围。</p> : null}
      <ol className="ct-run-explanation-steps">
        {explanation.steps.map((step) => (
          <li className={`ct-run-explanation-step is-${step.kind}`} key={step.step_id}>
            <div className="ct-run-explanation-step-heading">
              <strong>{step.title}</strong>
              <span>{explanationStepKindLabel(step.kind)}</span>
            </div>
            <p>{step.description}</p>
            <div className="ct-run-explanation-links">
              {step.evidence_ids.map((evidenceId) => {
                const item = evidence.find((candidate) => candidate.evidence_id === evidenceId);
                return item ? <button type="button" className="ct-link-button" key={evidenceId} onClick={() => onSelectEvidence(item)}>查看证据 · {item.path}:L{item.start_line}</button> : null;
              })}
              {step.symbol_ids.length === 1 ? <button type="button" className="ct-link-button" onClick={() => onSelectSymbol(step.symbol_ids[0])}>进入局部关系图 · {step.symbol_ids[0]}</button> : step.symbol_ids.length > 1 ? <small>涉及多个符号 · 保留源码证据</small> : null}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function runEventLabel(event: RunEvent): string {
  if (event.type === "tool.started") {
    const request = event.payload.request;
    if (request && typeof request === "object" && "params" in request && request.params && typeof request.params === "object" && "tool" in request.params) {
      return `调用 ${String(request.params.tool)}`;
    }
  }
  if (event.type === "tool.completed" || event.type === "tool.failed") {
    const result = event.payload.result;
    if (result && typeof result === "object" && "tool" in result) {
      return `${String(result.tool)} ${event.type === "tool.failed" ? "失败" : "完成"}`;
    }
  }
  return {
    "run.started": "Run 已冻结并开始",
    "stage.started": "阶段开始",
    "graph.updated": "关系图已更新",
    "finding.created": "发现已创建",
    "run.completed": "Run 完成",
    "run.failed": "Run 失败",
    "run.cancelled": "Run 已取消",
    "run.interrupted": "Run 被中断",
  }[event.type] ?? event.type;
}

function RunPanel({ session }: { session: SessionUi }) {
  const [mode, setMode] = useState<RunMode>("fixture");
  const [scenario, setScenario] = useState<RunScenario>("success-timeout-missing");
  const selectedRun = session.runs.find((run) => run.run_id === session.selectedRunId) ?? null;
  const currentRun = session.runStatusResponse?.run ?? selectedRun;
  const active = currentRun?.status === "queued" || currentRun?.status === "running";
  const draftPending = session.draft.trim().length > 0;
  return (
    <section className="ct-run-panel" aria-label="显式 Issue Run">
      <div className="ct-run-heading">
        <div><p className="ct-overline">R2 / EXPLICIT RUN</p><h3>Issue 分析</h3></div>
        <span className="ct-count-label">{session.runs.length} 次</span>
      </div>
      <p className="ct-run-lede">显式启动后，服务端只冻结当前已保存消息到一个不可变快照；当前草稿不会进入本次 Run，也不会隐式触发分析。</p>
      {draftPending ? <p className="ct-run-draft-warning" role="status">当前有未保存草稿：启动 Run 不会包含它，请先保存消息。</p> : null}
      <div className="ct-run-start-row">
        <label htmlFor="run-mode">模式<select id="run-mode" value={mode} onChange={(event) => setMode(event.target.value as RunMode)} disabled={active || session.runWriteStatus === "loading"}><option value="fixture">fixture / 离线演示</option><option value="real">real / 模型分析</option></select></label>
        {mode === "fixture" ? <label htmlFor="run-scenario">fixture 场景<select id="run-scenario" value={scenario} onChange={(event) => setScenario(event.target.value as RunScenario)} disabled={active || session.runWriteStatus === "loading"}><option value="success-timeout-missing">{runScenarioLabel("success-timeout-missing")}</option><option value="insufficient-external-service">{runScenarioLabel("insufficient-external-service")}</option><option value="tool-failure-read-source">{runScenarioLabel("tool-failure-read-source")}</option></select></label> : null}
        <button type="button" className="ct-button ct-button-primary" onClick={() => void session.onStartRun(mode, scenario)} disabled={active || session.runWriteStatus === "loading" || session.messagesStatus === "loading"}>{session.runWriteStatus === "loading" ? "处理中…" : mode === "real" ? "启动真实分析" : "启动离线分析"}</button>
      </div>
      {mode === "real" ? <p className="ct-run-real-warning" role="note">真实模式会把冻结的 Issue、已保存消息和有界源码证据发送到服务端配置的模型，可能产生费用；未配置或失败时不会回退到 fixture。</p> : null}
      {session.runWriteError ? <p className="ct-inline-error" role="alert">{session.runWriteError}</p> : null}
      {session.runsError ? <div className="ct-error-block compact" role="alert"><span>{session.runsError}</span></div> : null}
      {session.runs.length > 0 ? <div className="ct-run-history" aria-label="Run 历史">{session.runs.map((run) => <button type="button" key={run.run_id} className={`ct-run-history-item${run.run_id === session.selectedRunId ? " is-selected" : ""}`} onClick={() => session.onSelectRun(run.run_id)}><span><strong>{runStatusLabel(run.status)}</strong><small>{run.fixture_scenario ? runScenarioLabel(run.fixture_scenario) : runModeLabel(run)}</small></span><code>{run.run_id}</code><small>消息 1–{run.message_end_sequence} · {run.execution_version}</small></button>)}</div> : session.runsStatus === "loading" ? <LoadingNote>正在读取 Run 历史…</LoadingNote> : <div className="ct-run-empty">还没有 Run。可选择 fixture/real 显式启动；外部 Codex Run 会自动出现。</div>}
      {currentRun ? <div className="ct-run-detail">
        <div className="ct-run-detail-heading"><div><strong>{runStatusLabel(currentRun.status)}</strong><span data-state={session.runStatus}>{session.runStatus === "loading" ? "状态同步中" : session.runStatus === "error" ? "状态请求失败" : "已载入"}</span><span data-state={session.runStreamStatus}>事件：{runStreamStatusLabel(session.runStreamStatus)}</span></div>{active ? <button type="button" className="ct-button ct-button-danger" onClick={() => void session.onCancelRun()} disabled={session.runWriteStatus === "loading"}>取消 Run</button> : null}</div>
        {session.runStatusResponse ? <>
          <div className="ct-run-snapshot"><span><small>固定消息</small><code>1–{session.runStatusResponse.input.message_end_sequence}</code></span><span><small>INPUT HASH</small><code title={session.runStatusResponse.input.input_hash}>{shortHash(session.runStatusResponse.input.input_hash, 12)}</code></span><span><small>INDEX</small><code title={session.runStatusResponse.run.index_id}>{shortHash(session.runStatusResponse.run.index_id, 16)}</code></span><span><small>来源</small><code>{runModeLabel(session.runStatusResponse.run)}</code></span><span><small>版本</small><code>{session.runStatusResponse.run.execution_version}</code></span></div>
          {session.runError ? <p className="ct-inline-error" role="alert">{session.runError}</p> : null}
          {session.runStreamError ? <p className="ct-inline-note" role="status">{session.runStreamError}</p> : null}
          {session.runEvents.length > 0 ? <div className="ct-run-events"><div className="ct-run-subheading"><strong>公开执行事件</strong><span>{session.runEvents.length} 条</span></div>{session.runEvents.map((event) => <div className="ct-run-event" key={`${event.run_id}-${event.sequence}`}><code>#{event.sequence}</code><span>{runEventLabel(event)}</span></div>)}</div> : null}
          {session.runToolResults.length > 0 ? <div className="ct-run-tools"><div className="ct-run-subheading"><strong>工具结果</strong><span>{session.runToolResults.length} 项</span></div>{session.runToolResults.map((result) => <div className="ct-run-tool" key={result.request_id}><code>{result.tool}</code><span>{result.kind.endsWith("failed") ? "失败" : "已返回"} · #{result.completed_sequence}</span></div>)}</div> : null}
          {session.runReport ? <div className="ct-run-report"><div className="ct-run-subheading"><strong>{session.runReport.outcome === "candidate_found" ? "候选结论" : session.runReport.outcome === "insufficient_evidence" ? "证据不足" : session.runReport.outcome === "explanation" ? "流程解释" : "执行结果"}</strong><span>{session.runStatusResponse.run.execution_source === "codex" ? "CODEX / EXTERNAL" : session.runReport.fixture_mode ? "FIXTURE" : "REAL"}</span></div><p>{session.runReport.summary}</p>{session.runReport.limitations.length > 0 ? <div className="ct-run-evidence-list"><small>限制</small>{session.runReport.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}</div> : null}{session.runReport.candidate_links.length > 0 ? <div className="ct-run-impact-links"><small>候选位置与有界影响</small>{session.runReport.candidate_links.map((candidate) => <div className="ct-run-impact-link" key={candidate.candidate_id}><span><code>{candidate.source_symbol_id}</code><small>{candidate.relation}</small></span><button type="button" className="ct-link-button" onClick={() => session.onSelectRunImpact({ candidateId: candidate.candidate_id })}>查看影响范围</button></div>)}</div> : null}{session.runReport.findings.map((finding) => <div className="ct-run-finding" key={finding.finding_id}><strong>{finding.kind}</strong><span>{finding.text}</span>{finding.symbol_ids.length === 1 ? <button type="button" className="ct-link-button" onClick={() => session.onSelectRunImpact({ findingId: finding.finding_id })}>查看潜在影响</button> : <small className="ct-run-impact-unmapped">无唯一可映射符号 · 仅显示证据</small>}{finding.evidence_ids.map((evidenceId) => { const evidence = session.runReport?.evidence.find((item) => item.evidence_id === evidenceId); return evidence ? <button type="button" className="ct-link-button" key={evidenceId} onClick={() => session.onSelectRunEvidence(evidence)}>查看证据 · {evidence.path}:L{evidence.start_line}</button> : null; })}</div>)}{session.runReport.evidence.length > 0 ? <div className="ct-run-evidence-list"><small>本次报告证据</small>{session.runReport.evidence.map((evidence) => <button type="button" className="ct-link-button" key={evidence.evidence_id} onClick={() => session.onSelectRunEvidence(evidence)}>{evidence.path}:L{evidence.start_line}–L{evidence.end_line}</button>)}</div> : null}</div> : null}
          {session.runReport?.explanation ? <RunExplanation explanation={session.runReport.explanation} evidence={session.runReport.evidence} onSelectEvidence={session.onSelectRunEvidence} onSelectSymbol={session.onSelectRunSymbol} /> : null}
        </> : <LoadingNote>正在读取 Run 详情…</LoadingNote>}
      </div> : null}
    </section>
  );
}

function SessionView({ session, onBack }: { session: SessionUi; onBack: () => void }) {
  const selected = session.sessions.find((item) => item.session_id === session.selectedSessionId) ?? null;
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void session.onSendMessage();
    }
  };
  if (!selected) {
    return <div className="ct-page ct-session-page"><EmptyState title="从左侧选择一个会话" detail="每个会话只属于一个项目，并固定到创建时的不可变快照。" /></div>;
  }
  return (
    <div className="ct-page ct-session-page">
      <div className="ct-session-header">
        <div><button type="button" className="ct-back-button" onClick={onBack}>← 返回项目知识</button><p className="ct-overline">SESSION / FIXED SNAPSHOT</p><h2>{selected.topic}</h2><p>消息与 Run 分开保存；分析只会处理显式启动时已保存的内容。<span className="ct-session-fixed-version">固定版本 · {selected.index.revision} · {shortHash(selected.index.snapshot_hash, 10)}</span></p></div>
        <details className="ct-session-details">
          <summary>查看会话身份</summary>
          <div className="ct-session-identity"><span><small>SESSION ID</small><code>{selected.session_id}</code></span><span><small>PROJECT ID</small><code>{selected.project_id}</code></span><span><small>REVISION</small><code>{selected.index.revision}</code></span><span><small>SNAPSHOT HASH</small><code>{selected.index.snapshot_hash}</code></span></div>
        </details>
      </div>
      <div className="ct-message-area" aria-live="polite">
        {session.messagesStatus === "loading" && session.messages.length === 0 ? <LoadingNote>正在读取这个会话的消息…</LoadingNote> : session.messagesStatus === "error" && session.messages.length === 0 ? <div className="ct-error-block"><div><strong>会话消息请求失败</strong><span>{session.messagesError ?? "请重试。"}</span></div><button type="button" className="ct-button ct-button-secondary" onClick={() => void session.onRetryMessages()}>重试</button></div> : session.messages.length > 0 ? <>{session.messages.map((message) => <article className={`ct-message is-${message.source}`} key={message.message_id}><div className="ct-message-heading"><strong>{message.source === "human" ? "你" : "Agent"}</strong><span>#{message.sequence} · {formatDate(message.created_at)}</span></div><p>{message.content}</p></article>)}{session.messagesHasMore ? <div className="ct-message-pager"><span>当前按服务端 sequence 递增读取</span><button type="button" className="ct-button ct-button-secondary" onClick={() => void session.onLoadMoreMessages()} disabled={session.messagesStatus === "loading"}>{session.messagesStatus === "loading" ? "载入中…" : "加载后续消息"}</button></div> : null}</> : <div className="ct-message-empty"><strong>还没有消息</strong><span>先写下问题、约束或补充；保存后仍不会生成回复。</span></div>}
        {session.messagesStatus === "error" && session.messages.length > 0 ? <p className="ct-inline-error" role="alert">{session.messagesError}<button type="button" className="ct-link-button" onClick={() => void session.onRetryMessages()}>重试</button></p> : null}
      </div>
      <RunPanel session={session} />
      <form className="ct-composer" onSubmit={(event) => { event.preventDefault(); void session.onSendMessage(); }}>
        <div className="ct-composer-heading"><label htmlFor="session-message">追加消息</label><span data-state={session.writeStatus}>{session.writeStatus === "loading" ? "保存中" : session.writeStatus === "success" ? "已保存" : session.writeStatus === "error" ? "保存失败" : "仅保存消息"}</span></div>
        <textarea id="session-message" value={session.draft} onChange={(event) => session.onDraftChange(event.target.value)} onKeyDown={handleKeyDown} placeholder="输入要交给下一步人或 Agent 的问题、约束或补充…" maxLength={20_000} rows={4} />
        <div className="ct-composer-footer"><small>Enter 保存 · Shift+Enter 换行 · 输入法组合输入期间不会提交</small><button type="submit" className="ct-button ct-button-primary" disabled={session.writeStatus === "loading"}>{session.writeStatus === "loading" ? "保存中…" : "保存消息"}</button></div>
        {session.writeStatus === "error" ? <p className="ct-inline-error" role="alert">消息保存失败，草稿和原幂等键已保留。<button type="button" className="ct-link-button" onClick={() => void session.onSendMessage()}>重试保存</button></p> : null}
      </form>
    </div>
  );
}

function EvidencePanel({ evidence }: { evidence: EvidenceUi }) {
  const selected = evidence.selectedSymbol;
  const sourceResponse = evidence.sourceResponse;
  const canExpandRelation = selected !== null && selected.kind !== "file" && !selected.symbol_id.startsWith("evidence-");
  return (
    <aside className="ct-side-panel ct-evidence-panel" aria-label="源码证据面板">
      <div className="ct-side-panel-header"><div><p className="ct-overline">EVIDENCE</p><h2>源码证据</h2></div><button type="button" className="ct-close-button" onClick={evidence.onClose}>关闭</button></div>
      <div className="ct-panel-switch"><button type="button" className="is-active">源码证据</button><button type="button" onClick={() => void evidence.onExpandRelation()} disabled={!canExpandRelation}>代码关系</button></div>
      <div className="ct-evidence-context"><span><small>PROJECT</small><code>{evidence.projectId ?? "—"}</code></span><span><small>INDEX</small><code>{evidence.index?.index_id ?? selected?.index_id ?? "—"}</code></span>{evidence.generationId ? <span><small>GENERATION</small><code>{evidence.generationId}</code></span> : null}{evidence.sourceResponse ? <span><small>EVIDENCE</small><code>{evidence.sourceResponse.evidence.evidence_id}</code></span> : null}</div>
      <div className="ct-panel-scroll">
        {selected ? <>
          <div className="ct-selected-symbol"><span className="ct-kind-chip">{selected.kind}</span><h3 title={selected.qualified_name}>{selected.qualified_name}</h3><p>{selected.path} · L{selected.start_line}–L{selected.end_line}</p></div>
          <div className="ct-evidence-actions"><button type="button" className="ct-button ct-button-secondary" onClick={() => void evidence.onExpandRelation()} disabled={!canExpandRelation}>展开关系</button><span>{canExpandRelation ? `${evidence.connectedEdges.length} 条静态边` : "证据片段无符号节点"}</span></div>
          {evidence.sourceError ? <div className="ct-error-block compact" role="alert"><span>{evidence.sourceError}</span><button type="button" className="ct-link-button" onClick={() => void evidence.onRetrySource()}>重试</button></div> : null}
          {evidence.sourceStatus === "loading" ? <LoadingNote>正在从不可变快照读取…</LoadingNote> : null}
          {sourceResponse ? <>
            <div className="ct-source-meta"><span>{sourceResponse.evidence.kind} · L{sourceResponse.evidence.start_line}–L{sourceResponse.evidence.end_line}</span><code title={sourceResponse.evidence.content_hash}>{shortHash(sourceResponse.evidence.content_hash)}</code></div>
            <div className="ct-source-window">{evidenceLines(sourceResponse.evidence).map((line) => <div className={`ct-source-line${line.number >= selected.start_line && line.number <= selected.end_line ? " is-definition" : ""}`} key={line.number}><span>{String(line.number).padStart(3, "0")}</span><code>{line.text || " "}</code></div>)}</div>
            <div className="ct-source-pager"><button type="button" onClick={() => void evidence.onLoadSource(selected, Math.max(selected.start_line, evidence.sourceWindowStart - 200))} disabled={!evidence.sourceHasPrevious}>上一段</button><span>{sourceResponse.evidence.start_line}–{sourceResponse.evidence.end_line} / {selected.end_line}</span><button type="button" onClick={() => void evidence.onLoadSource(selected, sourceResponse.evidence.end_line + 1)} disabled={!evidence.sourceHasNext}>下一段</button></div>
          </> : null}
          {evidence.selectedUnresolved.length > 0 ? <div className="ct-unresolved"><div className="ct-section-heading"><div><p className="ct-overline">UNRESOLVED</p><h3>未解析调用</h3></div><span className="ct-count-label">{evidence.selectedUnresolved.length}</span></div>{evidence.selectedUnresolved.map((item) => <div className="ct-unresolved-item" key={item.unresolved_id}><code>{item.expression}</code><p>{item.reason} · L{item.start_line}</p><small>保留为未解析关系，不绘制确定调用边。</small></div>)}</div> : null}
        </> : <EmptyState title="尚未选择证据" detail="从知识页入口、搜索结果或功能结果打开同一快照的源码片段。" />}
      </div>
    </aside>
  );
}

function GraphPanel({ graph }: { graph: GraphUi }) {
  const isFeature = graph.panelMode === "feature_graph";
  const isImpact = graph.panelMode === "impact";
  const featureGraph = graph.featureGraph;
  const codeFocusNodeIds = graph.codeFocusNodeIds.length > 0
    ? graph.codeFocusNodeIds
    : graph.codeModel.nodes.map((node) => node.id);
  const codeViewModel = useMemo(() => {
    if (graph.codeFocusNodeIds.length === 0 || !graph.selectedId) {
      return graph.codeModel;
    }
    const focusedIds = new Set(graph.codeFocusNodeIds);
    const selected = graph.codeModel.nodes.find((node) => node.id === graph.selectedId);
    if (!selected) {
      return graph.codeModel;
    }
    const neighbors = graph.codeFocusNodeIds.filter((nodeId) => nodeId !== graph.selectedId);
    return {
      ...graph.codeModel,
      nodes: graph.codeModel.nodes.map((node) => {
        if (node.type === "group") {
          return { ...node, style: { ...node.style, width: 750 } };
        }
        if (!focusedIds.has(node.id)) {
          return node;
        }
        const focusIndex = node.id === graph.selectedId
          ? 0
          : neighbors.indexOf(node.id) + 1;
        return {
          ...node,
          position: {
            x: selected.position.x + 370 + (focusIndex % 2) * 190,
            y: selected.position.y + Math.floor(focusIndex / 2) * 78,
          },
          style: { ...node.style, width: 160 },
        };
      }),
    };
  }, [graph.codeFocusNodeIds, graph.codeModel, graph.selectedId]);
  return (
    <aside className="ct-side-panel ct-graph-panel" aria-label={isFeature ? "功能图面板" : isImpact ? "候选影响面板" : "代码关系面板"}>
      <div className="ct-side-panel-header"><div><p className="ct-overline">{isFeature ? "FEATURE TOPOLOGY" : isImpact ? "RUN IMPACT" : "STATIC TOPOLOGY"}</p><h2>{isFeature ? "功能图" : isImpact ? "候选影响" : "代码关系"}</h2></div><button type="button" className="ct-close-button" onClick={graph.onClose}>关闭</button></div>
      <div className="ct-panel-switch"><button type="button" className={!isFeature && !isImpact ? "is-active" : ""} onClick={() => graph.onPanelModeChange("graph")}>代码关系</button><button type="button" className={isFeature ? "is-active" : ""} onClick={() => graph.onPanelModeChange("feature_graph")} disabled={!graph.featureGeneration}>功能图</button><button type="button" className={isImpact ? "is-active" : ""} onClick={() => graph.onPanelModeChange("impact")} disabled={graph.impactStatus === "idle"}>影响范围</button><button type="button" onClick={() => graph.onPanelModeChange("source")} disabled={!graph.selectedId}>源码证据</button></div>
      {!isFeature && !isImpact ? <>
        <div className="ct-graph-toolbar"><label>方向<select value={graph.direction} onChange={(event) => graph.onChangeDirection(event.target.value as NeighborDirection)} disabled={!graph.selectedId}><option value="both">双向</option><option value="callers">调用方</option><option value="callees">被调用方</option></select></label><label className="ct-check-field"><input type="checkbox" checked={graph.edgeKinds.includes("calls")} onChange={() => graph.onToggleEdgeKind("calls")} disabled={!graph.selectedId} /><span>calls</span></label><label className="ct-check-field"><input type="checkbox" checked={graph.edgeKinds.includes("imports")} onChange={() => graph.onToggleEdgeKind("imports")} disabled={!graph.selectedId} /><span>imports</span></label><button type="button" className="ct-link-button" onClick={() => { if (graph.flowInstance) { void graph.flowInstance.fitBounds(graph.flowInstance.getNodesBounds(codeFocusNodeIds), { padding: 0.18, duration: 240 }); } }} disabled={graph.codeModel.nodes.length === 0}>聚焦</button></div>
        <div className="ct-panel-status"><span data-state={graph.codeStatus}>{graph.codeStatus === "loading" ? "载入关系中" : graph.codeStatus === "error" ? "关系请求失败" : graph.codeStatus === "empty" ? graph.codeIndexTruncated ? "当前结果为空（索引关系未完整分析）" : graph.codeIndexUnresolvedTruncated === true ? "当前结果为空（unresolved 详情不完整）" : "当前方向没有静态边" : graph.codeStatus === "success" ? `${graph.codeSymbolCount} 个节点 · ${graph.codeEdgeCount} 条边` : "选择搜索命中后载入关系"}</span>{graph.codeTruncated ? <small>查询已截断 · {graph.codeTruncationReason}</small> : null}</div>
        {graph.codeIndexTruncated ? <p className="ct-graph-coverage-warning" role="status">当前固定索引的关系提取未完成 · {graph.codeIndexTruncationReason}；空邻接不能证明没有关系。</p> : null}
        {graph.codeIndexUnresolvedTruncated === true ? <p className="ct-graph-coverage-warning" role="status">当前固定索引的 unresolved 详情已截断 · {graph.codeIndexUnresolvedTruncationReason}；未返回的 unresolved 不能被当作不存在。</p> : null}
        {graph.codeSymbolCount > 0 && graph.codeIndexUnresolvedTruncated === null ? <p className="ct-graph-coverage-warning" role="status">当前旧索引未记录 unresolved 覆盖状态；不把缺失的 unresolved 当作不存在。</p> : null}
        {graph.codeError ? <p className="ct-inline-error" role="alert">{graph.codeError}</p> : null}
        <div className="ct-graph-legend"><span><i className="is-calls" />calls</span><span><i className="is-imports" />imports</span><small>局部静态子图 · 视口聚焦 {graph.codeFocusNodeIds.length > 0 ? `${codeFocusNodeIds.length} 个节点` : "全部节点"}</small></div>
        <GraphCanvas model={codeViewModel} nodeTypes={graph.codeNodeTypes} onInit={graph.onCodeInit} onNodeClick={graph.onCodeNodeClick} focusNodeIds={graph.codeFocusNodeIds} emptyTitle="建立或打开索引后，这里会显示关系图。" />
      </> : isFeature ? <>
        <div className="ct-graph-toolbar"><span className="ct-graph-legend"><span><i className="is-semantic" />语义关联</span><span><i className="is-static" />静态 CodeEdge</span></span><button type="button" className="ct-link-button" onClick={() => graph.featureFlowInstance?.fitView({ padding: 0.18, duration: 240 })} disabled={graph.featureModel.nodes.length === 0}>聚焦</button></div>
        <div className="ct-panel-status"><span data-state={graph.featureStatus}>{graph.featureStatus === "loading" ? "功能图载入中" : graph.featureStatus === "error" ? "功能图请求失败" : graph.featureStatus === "empty" ? "当前版本没有可展开节点" : graph.featureStatus === "success" ? `${featureGraph?.nodes.length ?? 0} 个节点 · 语义 / 静态关系已分开` : "选择一个已保存版本载入功能图"}</span>{featureGraph?.truncated ? <small>已截断 · {featureGraph.truncation_reason}</small> : null}</div>
        {graph.featureError ? <p className="ct-inline-error" role="alert">{graph.featureError}</p> : null}
        <div className="ct-graph-legend"><span><i className="is-semantic" />语义关联</span><span><i className="is-static" />静态 CodeEdge</span></div>
        <GraphCanvas model={graph.featureModel} nodeTypes={graph.featureNodeTypes} onInit={graph.onFeatureInit} onNodeClick={graph.onFeatureNodeClick} emptyTitle="选择一个已保存功能版本。" />
        {featureGraph && (featureGraph.limitations.length > 0 || featureGraph.unavailable_target_ids.length > 0) ? <div className="ct-graph-limitations"><strong>图边界</strong>{featureGraph.limitations.map((item) => <span key={item}>{item}</span>)}{featureGraph.unavailable_target_ids.length > 0 ? <span>未映射目标 {featureGraph.unavailable_target_ids.length} 个</span> : null}</div> : null}
      </> : <>
        <div className="ct-graph-toolbar"><label>方向<select value={graph.impactDirection} onChange={(event) => graph.onImpactDirectionChange(event.target.value as ImpactDirection)}><option value="callers">调用方</option><option value="callees">被调用方</option></select></label><label>深度<select value={graph.impactDepth} onChange={(event) => graph.onImpactDepthChange(Number(event.target.value))}><option value={1}>1 层</option><option value={2}>2 层</option></select></label><button type="button" className="ct-link-button" onClick={() => graph.flowInstance?.fitView({ padding: 0.18, duration: 240 })} disabled={graph.impactModel.nodes.length === 0}>聚焦</button></div>
        <div className="ct-panel-status"><span data-state={graph.impactStatus}>{graph.impactStatus === "loading" ? "影响范围载入中" : graph.impactStatus === "error" ? "影响范围请求失败" : graph.impact?.mapping_status === "unmapped" ? "没有可映射的唯一符号" : graph.impactStatus === "success" ? `${graph.impact?.reachable_node_count ?? 0} 个可达节点 · ${graph.impact?.paths.length ?? 0} 条路径` : graph.impactStatus === "empty" ? "当前方向没有静态 calls 可达节点" : "从 Run 报告选择一个候选或发现"}</span>{graph.impact?.truncated ? <small>已截断 · {graph.impact.truncation_reason}</small> : null}</div>
        {graph.impact?.index_truncated ? <p className="ct-graph-coverage-warning" role="status">影响查询使用的固定索引扫描未完成 · {graph.impact.index_truncation_reason}；空邻接不能证明没有关系。</p> : null}
        {graph.impact?.index_unresolved_truncated === true ? <p className="ct-graph-coverage-warning" role="status">影响查询使用的 unresolved 详情已截断 · {graph.impact.index_unresolved_truncation_reason}；未返回的 unresolved 不能被当作不存在。</p> : null}
        {graph.impact && graph.impact.index_unresolved_truncated === null ? <p className="ct-graph-coverage-warning" role="status">影响查询使用的旧索引未记录 unresolved 覆盖状态。</p> : null}
        {graph.impactError ? <p className="ct-inline-error" role="alert">{graph.impactError}</p> : null}
        <div className="ct-graph-legend"><span><i className="is-impact-root" />候选根</span><span><i className="is-calls" />静态 calls</span><span><i className="is-unknown" />未知边界</span></div>
        {graph.impactStatus === "loading" ? <LoadingNote>正在从 Run 固定快照计算有界路径…</LoadingNote> : graph.impact?.mapping_status === "unmapped" ? <EmptyState title="报告没有可遍历的唯一符号" detail="保留报告源码证据；不会根据同名符号猜测影响范围。" /> : <GraphCanvas model={graph.impactModel} nodeTypes={graph.codeNodeTypes} onInit={graph.onCodeInit} onNodeClick={graph.onImpactNodeClick} emptyTitle="当前方向没有静态 calls 可达节点。" />}
        {graph.impact && graph.impact.paths.length > 0 ? <div className="ct-impact-paths"><div className="ct-section-heading"><div><p className="ct-overline">STATIC PATHS</p><h3>可解释路径</h3></div><span className="ct-count-label">{graph.impact.paths.length}</span></div>{graph.impact.paths.slice(0, 12).map((path) => { const names = new Map(graph.impact?.nodes.map((node) => [node.symbol_id, node.qualified_name])); return <div className="ct-impact-path" key={path.path_id}><code>{path.symbol_ids.map((symbolId) => names.get(symbolId) ?? symbolId).join(graph.impact?.direction === "callers" ? " ← " : " → ")}</code><small>{path.edges.map((edge) => `${edge.source_location.path}:L${edge.source_location.start_line}`).join(" · ")}</small></div>; })}</div> : null}
        {graph.impact && graph.impact.unresolved.length > 0 ? <div className="ct-impact-unknown"><strong>未知边界 · {graph.impact.unresolved.length}</strong><small>动态调用或无法解析的引用不会绘制为确定影响边。</small>{graph.impact.unresolved.map((item) => <span key={item.unresolved_id}><code>{item.expression}</code> · {item.reason}</span>)}</div> : null}
        {graph.impact ? <p className="ct-graph-scope-note">{graph.impact.scope_note}</p> : null}
      </>}
    </aside>
  );
}

function GraphCanvas({
  model,
  nodeTypes,
  onInit,
  onNodeClick,
  emptyTitle,
  focusNodeIds = [],
}: {
  model: { nodes: Node[]; edges: Edge[] };
  nodeTypes: NodeTypes;
  onInit: (instance: ReactFlowInstance<Node, Edge>) => void;
  onNodeClick: (nodeId: string) => void;
  emptyTitle: string;
  focusNodeIds?: string[];
}) {
  if (model.nodes.length === 0) {
    return <div className="ct-graph-empty"><span className="ct-empty-index" aria-hidden="true">∅</span><strong>{emptyTitle}</strong><p>图中只表示当前已加载的受限关系，不代表全仓架构。</p></div>;
  }
  return (
    <div className="ct-graph-canvas">
      <ReactFlow<Node, Edge>
        nodes={model.nodes}
        edges={model.edges}
        nodeTypes={nodeTypes}
        onInit={onInit}
        onNodeClick={(_event, node) => onNodeClick(node.id)}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable
        minZoom={0.25}
        maxZoom={1.8}
      >
        <GraphFitController model={model} focusNodeIds={focusNodeIds} />
        <Background color="#d7dee8" gap={24} size={1} />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap pannable zoomable nodeColor={(node) => node.type === "group" ? "#aab5c4" : node.type === "feature" ? "#3158d9" : "#7790c7"} maskColor="rgba(247, 248, 250, 0.78)" position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

function GraphFitController({
  model,
  focusNodeIds,
}: {
  model: { nodes: Node[]; edges: Edge[] };
  focusNodeIds: string[];
}) {
  const flow = useReactFlow<Node, Edge>();

  useEffect(() => {
    if (model.nodes.length === 0) {
      return;
    }
    const timer = window.setTimeout(() => {
      const nodeIds = focusNodeIds.length > 0
        ? focusNodeIds
        : model.nodes.map((node) => node.id);
      const bounds = flow.getNodesBounds(nodeIds);
      if (bounds.width === 0 && bounds.height === 0) {
        return;
      }
      void flow.fitBounds(bounds, { padding: 0.18, duration: 320 });
    }, 100);
    return () => window.clearTimeout(timer);
  }, [flow, focusNodeIds, model.nodes]);

  return null;
}

export default function WorkbenchUi(props: WorkbenchUiProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [operationPanel, setOperationPanel] = useState<OperationPanelState | null>(null);
  const [panelWidth, setPanelWidth] = useState(360);
  const closeOperation = () => setOperationPanel(null);
  const openOperation = (mode: OperationPanel, tab: OperationTab = "source") => {
    setOperationPanel({ mode, tab });
    setMobileSidebarOpen(false);
  };
  const selectProject = (projectId: string) => {
    props.project.onSelectProject(projectId);
    setMobileSidebarOpen(false);
    setOperationPanel(null);
  };
  const selectSession = (sessionId: string) => {
    if (!sessionId) {
      props.onViewModeChange("overview");
      props.onPanelModeChange("none");
      return;
    }
    props.session.onSelectSession(sessionId);
    setMobileSidebarOpen(false);
    setOperationPanel(null);
    props.onViewModeChange("session");
  };
  const openKnowledge = () => {
    props.onViewModeChange("overview");
    props.onPanelModeChange("none");
  };
  const alert = props.index.error ?? props.project.error ?? props.session.error ?? props.localImport.error ?? props.githubImport.error ?? props.connection.error;
  const style = { "--ct-panel-width": `${panelWidth}px` } as CSSProperties;
  return (
    <main className={`ct-app${props.panelMode !== "none" ? " has-side-panel" : ""}${props.panelMode === "impact" ? " has-impact-panel" : ""}`} style={style}>
      <header className="ct-topbar">
        <button type="button" className="ct-mobile-menu" onClick={() => setMobileSidebarOpen(true)}>项目</button>
        <div className="ct-brand"><span className="ct-brand-mark">CT</span><span><strong>CODETRAIL</strong><small>CODE INTELLIGENCE WORKSPACE</small></span></div>
        <div className="ct-topbar-project"><span>当前项目</span><strong>{props.project.selectedProject?.name ?? "未选择项目"}</strong></div>
        <div className="ct-connection" data-state={props.connection.state}><span className="ct-status-dot" aria-hidden="true" /><span>{props.connection.state === "connected" ? "API ONLINE" : props.connection.state === "checking" ? "API CHECKING" : "API OFFLINE"}</span>{props.connection.health ? <small>v{props.connection.health.version}</small> : null}<button type="button" onClick={() => void props.connection.onCheck()} disabled={props.connection.state === "checking"}>{props.connection.state === "checking" ? "检查中" : "重试"}</button></div>
      </header>
      <div className="ct-workspace-frame">
        {mobileSidebarOpen ? <button type="button" className="ct-mobile-scrim" aria-label="关闭项目导航" onClick={() => setMobileSidebarOpen(false)} /> : null}
        <div className={`ct-sidebar-slot${mobileSidebarOpen ? " is-open" : ""}`}><ProjectRail project={{ ...props.project, onSelectProject: selectProject }} session={{ ...props.session, onSelectSession: selectSession }} onOpenOperation={openOperation} onViewKnowledge={openKnowledge} /></div>
        <section className="ct-center-column" aria-label={props.viewMode === "session" ? "Issue 会话" : "项目知识页"}>
          <ContextHeader project={props.project} index={props.index} viewMode={props.viewMode} />
          {alert ? <div className="ct-global-alert" role="alert"><span>{alert}</span>{props.connection.state === "error" ? <button type="button" className="ct-link-button" onClick={() => void props.connection.onCheck()}>重新检查</button> : null}</div> : null}
          {operationPanel ? <OperationPanel mode={operationPanel.mode} initialTab={operationPanel.tab} onClose={closeOperation} project={props.project} localImport={props.localImport} githubImport={props.githubImport} index={props.index} /> : props.viewMode === "session" ? <SessionView session={props.session} onBack={openKnowledge} /> : <KnowledgeView project={props.project} index={props.index} knowledge={props.knowledge} onOpenOperation={openOperation} />}
        </section>
        {props.panelMode === "source" ? <EvidencePanel evidence={props.evidence} /> : props.panelMode === "graph" || props.panelMode === "feature_graph" || props.panelMode === "impact" ? <GraphPanel graph={props.graph} /> : null}
      </div>
      {props.panelMode !== "none" && props.panelMode !== "impact" ? <div className="ct-panel-width-control"><label htmlFor="panel-width">面板宽度</label><input id="panel-width" type="range" min="300" max="440" step="10" value={panelWidth} onChange={(event) => setPanelWidth(Number(event.target.value))} /><output>{panelWidth}px</output></div> : null}
      <footer className="ct-footer"><span>STATIC FACTS / FIXED EVIDENCE</span><span>Run：fixture / real 或 Codex 外部 Agent · 有界工具循环 · SSE 事件流与轮询回退</span><span>{props.index.response ? `${props.graph.codeSymbolCount} nodes · ${props.graph.codeEdgeCount} edges` : "No snapshot loaded"}</span></footer>
    </main>
  );
}
