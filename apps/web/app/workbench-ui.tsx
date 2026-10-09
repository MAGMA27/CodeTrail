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
import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import type {
  CodeEdge,
  CodeExplanation,
  ContextualCall,
  EdgeKind,
  Evidence,
  FeatureGeneration,
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
  ModuleSymbolsPageResponse,
  ModelConfigSettings,
  NeighborDirection,
  OverviewClassification,
  OverviewClassificationCounts,
  OverviewEntryPoint,
  OverviewHttpAssociation,
  OverviewGroup,
  OverviewModule,
  OverviewModuleRelation,
  ProjectOverview,
  ProjectRecord,
  TrashListResponse,
  RunEvent,
  RunRecord,
  RunReport,
  RunImpactResponse,
  RunScenario,
  RunStatusResponse,
  RunToolResult,
  SearchMatch,
  Session,
  SessionMessage,
  SaveModelConfigRequest,
  SourceLocation,
  SourceResponse,
  SymbolRecord,
  UnresolvedReference,
} from "../lib/api";
import { SCHEMA_VERSION } from "../lib/api";
import { canShowKnowledgeInspector, resolveGraphReturnPanel, type GraphPanelMode } from "../lib/workbench-navigation";
import {
  clampSidebarWidth,
  DEFAULT_SIDEBAR_WIDTH,
  normalizeSidebarWidth,
  readSidebarWidth,
  resolveSidebarWidthRange,
  SIDEBAR_WIDTH_STORAGE_KEY,
  type SidebarWidthRange,
} from "../lib/sidebar-width";
import { moduleDefinitionRows as buildModuleDefinitionRows, type DirectImportTarget, type GraphState } from "../lib/graph";
import { architectureOverviewEdgeVisible, architectureEdgeSides, buildArchitectureGraph, visibleArchitectureRelations, type ArchitectureDirectory, type ArchitecturePosition } from "../lib/architecture-graph";

export type UiAsyncStatus = "idle" | "loading" | "success" | "empty" | "error";
export type UiIndexStatus = "unindexed" | "creating" | "loading" | "ready" | "partial" | "error";
export type UiPanelMode = "none" | "source" | "graph" | "feature_graph" | "impact";
export type OperationPanel = "create-project" | "project-management";
export type OperationTab = "source" | "index";

type OpenOperation = (panel: OperationPanel, tab?: OperationTab) => void;
type OperationPanelState = { mode: OperationPanel; tab: OperationTab };

type FormHandler = (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
type Action = () => void | Promise<void>;
type ImportFormHandler = (event: FormEvent<HTMLFormElement>) => Promise<boolean>;
type ImportAction = () => Promise<boolean>;

type ProjectUi = {
  projects: ProjectRecord[];
  selectedProjectId: string | null;
  selectedProject: ProjectRecord | null;
  status: UiAsyncStatus;
  error: string | null;
  onSelectProject: (projectId: string) => void;
  trash: TrashListResponse | null;
  trashStatus: UiAsyncStatus;
  trashError: string | null;
  trashMutationBusy: boolean;
  onLoadTrash: Action;
  onMoveProjectToTrash: (projectId: string) => void | Promise<void>;
  onMoveSessionToTrash: (projectId: string, sessionId: string) => void | Promise<void>;
  onRestoreProject: (projectId: string) => void | Promise<void>;
  onRestoreSession: (projectId: string, sessionId: string) => void | Promise<void>;
};

type LocalImportUi = {
  name: string;
  path: string;
  status: ImportStatus | null;
  attempt: ImportAttempt | null;
  error: string | null;
  onNameChange: (value: string) => void;
  onPathChange: (value: string) => void;
  onSubmit: ImportFormHandler;
  onRetry: ImportAction;
  onRefresh: ImportAction;
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
  onSubmit: ImportFormHandler;
  onRetry: ImportAction;
  onRefresh: ImportAction;
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
  onCreate: Action;
  onOpenIndex: (indexId: string) => void | Promise<void>;
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
  modelApiKeyConfigured: boolean | null;
  onOpenModelSettings: Action;
  onSelectRun: (runId: string) => void;
  onStartRun: () => void | Promise<void>;
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
  architectureGraphRootId: string | null;
  onCloseArchitectureGraph: Action;
  moduleSymbolsRoot: SymbolRecord | null;
  moduleSymbolsPage: ModuleSymbolsPageResponse | null;
  moduleSymbolsStatus: UiAsyncStatus;
  moduleSymbolsError: string | null;
  onSelectOverviewEntry: (entry: OverviewEntryPoint) => void;
  onSelectOverviewGroup: (group: OverviewGroup) => void;
  onClearOverviewGroup: () => void;
  onOpenOverviewGraph: (entry: OverviewEntryPoint) => void;
  onOpenHttpChain: (association: OverviewHttpAssociation) => void;
  onOpenOverviewModule: (module: OverviewModule) => void;
  onOpenModuleSymbols: (module: OverviewModule) => void;
  onOpenModuleImports: (module: OverviewModule) => void;
  onOpenSymbolCalls: (symbol: SymbolRecord) => void;
  onLoadMoreModuleSymbols: Action;
  onRetryModuleSymbols: Action;
  onSelectModuleSymbol: (symbol: SymbolRecord) => void;
  onOpenArchitectureSource: (location: { indexId: string; path: string; startLine: number; endLine: number; expectedContentHash?: string }) => void;
  onRetryOverview: Action;
  featureStatus: UiAsyncStatus;
  featureGeneration: FeatureGeneration | null;
  featureError: string | null;
  modelApiKeyConfigured: boolean | null;
  onOpenModelSettings: Action;
  featureHistory: FeatureGenerationSummary[];
  featureHistoryStatus: UiAsyncStatus;
  featureHistoryError: string | null;
  featureHistoryHasMore: boolean;
  onLoadMoreFeatureHistory: Action;
  onRetryFeatureHistory: Action;
  selectedFeatureGenerationId: string | null;
  onSelectFeatureGeneration: (generationId: string) => void;
  featureSourceScope: FeatureSourceScope;
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
  hasHttpChain: boolean;
  onLoadSource: (symbol: SymbolRecord, startLine?: number) => void | Promise<void>;
  onRetrySource: Action;
  onExpandRelation: Action;
  onReturnHttpChain: Action;
  onClose: () => void;
};

type GraphUi = {
  panelMode: Exclude<UiPanelMode, "none" | "source">;
  codeModel: { nodes: Node[]; edges: Edge[] };
  codeEdges: CodeEdge[];
  directImportTargets: DirectImportTarget[];
  codeStatus: UiAsyncStatus;
  codeError: string | null;
  codeTruncated: boolean;
  codeTruncationReason: string | null;
  codeIndexTruncated: boolean;
  codeIndexTruncationReason: string | null;
  codeIndexUnresolvedTruncated: boolean | null;
  codeIndexUnresolvedTruncationReason: string | null;
  codeUnresolved: UnresolvedReference[];
  contextualCalls: ContextualCall[];
  onSelectContextualCallEvidence: (call: ContextualCall, location: SourceLocation) => void;
  codeHasCycle: boolean;
  httpChain: { association: OverviewHttpAssociation; handler: SymbolRecord } | null;
  onSelectHttpChainEvidence: (evidence: Evidence) => void;
  onRetryHttpChain: Action;
  onExitHttpChain: Action;
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
  modelSettings: {
    open: boolean;
    status: UiAsyncStatus;
    settings: ModelConfigSettings | null;
    error: string | null;
    saving: boolean;
    saved: boolean;
    onOpen: Action;
    onClose: Action;
    onRetry: Action;
    onSave: (request: SaveModelConfigRequest) => Promise<boolean>;
  };
  knowledge: KnowledgeUi;
  evidence: EvidenceUi;
  graph: GraphUi;
  viewMode: "overview" | "session";
  onViewModeChange: (mode: "overview" | "session") => void;
  onImportFlowLeave: Action;
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

function RowActionsMenu({ label, disabled, onMoveToTrash }: { label: string; disabled: boolean; onMoveToTrash: () => void }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDetailsElement>(null);
  const triggerRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = () => {
      if (menuRef.current) menuRef.current.open = false;
      setOpen(false);
    };
    const onPointerDown = (event: globalThis.PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) close();
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close();
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);
  return (
    <details
      ref={menuRef}
      className="ct-row-actions"
      onToggle={(event) => setOpen(event.currentTarget.open)}
      onBlur={(event) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
          event.currentTarget.open = false;
          setOpen(false);
        }
      }}
    >
      <summary ref={triggerRef} aria-label={`${label}的更多操作`}>更多</summary>
      <div className="ct-row-actions-popover">
        <button
          type="button"
          disabled={disabled}
          onClick={() => {
            if (menuRef.current) menuRef.current.open = false;
            setOpen(false);
            triggerRef.current?.focus();
            onMoveToTrash();
          }}
        >移到回收站</button>
      </div>
    </details>
  );
}

function ProjectRail({
  project,
  session,
  viewMode,
  settingsOpen,
  onOpenOperation,
  onViewKnowledge,
  onOpenSettings,
}: {
  project: ProjectUi;
  session: SessionUi;
  viewMode: "overview" | "session";
  settingsOpen: boolean;
  onOpenOperation: OpenOperation;
  onViewKnowledge: () => void;
  onOpenSettings: Action;
}) {
  const [sessionFormOpen, setSessionFormOpen] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const projectPickerRef = useRef<HTMLDetailsElement>(null);
  const projectPickerTriggerRef = useRef<HTMLElement>(null);
  const selectedProject = project.selectedProject;
  useEffect(() => {
    if (!projectPickerOpen) return;
    const close = () => {
      if (projectPickerRef.current) projectPickerRef.current.open = false;
      setProjectPickerOpen(false);
    };
    const onPointerDown = (event: globalThis.PointerEvent) => {
      if (event.target instanceof Node && !projectPickerRef.current?.contains(event.target)) close();
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        projectPickerTriggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [projectPickerOpen]);
  const closeProjectPicker = (restoreFocus = false) => {
    if (projectPickerRef.current) projectPickerRef.current.open = false;
    setProjectPickerOpen(false);
    if (restoreFocus) projectPickerTriggerRef.current?.focus();
  };
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
        <div className="ct-sidebar-header-actions">
          <button type="button" className="ct-text-button" onClick={() => { closeProjectPicker(); onOpenOperation("create-project"); }}>+ 新建</button>
          <button
            type="button"
            className={`ct-text-button${trashOpen ? " is-active" : ""}`}
            aria-expanded={trashOpen}
            onClick={() => {
              closeProjectPicker();
              const next = !trashOpen;
              setTrashOpen(next);
              if (next) void project.onLoadTrash();
            }}
          >回收站</button>
        </div>
      </div>
      {project.projects.length > 0 ? (
        <div className="ct-sidebar-project-context">
          <details
            ref={projectPickerRef}
            className="ct-project-picker"
            onToggle={(event) => setProjectPickerOpen(event.currentTarget.open)}
            onBlur={(event) => {
              if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
                event.currentTarget.open = false;
                setProjectPickerOpen(false);
              }
            }}
          >
            <summary ref={projectPickerTriggerRef} aria-label={`当前项目：${selectedProject?.name ?? "未选择"}；切换项目`}>
              <span><small>当前项目</small><strong>{selectedProject?.name ?? "选择项目"}</strong></span>
              <span className="ct-project-picker-caret" aria-hidden="true">切换⌄</span>
            </summary>
            <div className="ct-project-picker-list" aria-label="可切换项目">
              {project.projects.map((item) => {
                const selected = item.project_id === project.selectedProjectId;
                const status = item.building_index_id
                  ? "建立中"
                  : item.available_index_ids.length > 0
                    ? `${item.available_index_ids.length} 个快照`
                    : "未索引";
                return (
                  <div className="ct-project-item-row" key={item.project_id}>
                    <button
                      type="button"
                      className={`ct-project-item${selected ? " is-selected" : ""}`}
                      onClick={() => {
                        closeProjectPicker(true);
                        project.onSelectProject(item.project_id);
                      }}
                      aria-current={selected ? "page" : undefined}
                    >
                      <span className="ct-project-initial" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span>
                      <span className="ct-project-copy"><strong>{item.name}</strong><span>{status}</span></span>
                      <span className="ct-project-chevron" aria-hidden="true">{selected ? "✓" : "›"}</span>
                    </button>
                    <RowActionsMenu
                      label={`项目“${item.name}”`}
                      disabled={project.trashMutationBusy || Boolean(item.building_index_id)}
                      onMoveToTrash={() => {
                        if (window.confirm(`将项目“${item.name}”移到回收站？\n索引、会话、消息和报告会保留，可随时恢复。`)) {
                          void project.onMoveProjectToTrash(item.project_id);
                        }
                      }}
                    />
                  </div>
                );
              })}
            </div>
          </details>
          {selectedProject ? <button type="button" className={`ct-nav-item${viewMode === "overview" ? " is-active" : ""}`} aria-current={viewMode === "overview" ? "page" : undefined} onClick={() => { setSessionFormOpen(false); onViewKnowledge(); }}>
            <span className="ct-nav-marker" aria-hidden="true" />
            <span>项目知识</span>
            <span className="ct-nav-arrow" aria-hidden="true">›</span>
          </button> : null}
        </div>
      ) : project.status === "loading" ? (
        <div className="ct-sidebar-loading"><span /><span /><span /></div>
      ) : (
        <div className="ct-sidebar-empty"><strong>还没有项目</strong><span>导入本地目录或 GitHub 仓库，建立一个固定快照。</span></div>
      )}

        {selectedProject ? (
          <section className="ct-project-section" aria-label={`${selectedProject.name}的会话`}>
            <div className="ct-session-heading">
              <span>会话 <small>{session.sessions.length}</small></span>
              <button type="button" className="ct-icon-button" aria-label="新建会话" aria-expanded={sessionFormOpen} onClick={() => setSessionFormOpen((value) => !value)}>+</button>
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
            <div className="ct-session-scroll">
              {session.sessions.length > 0 ? (
                <div className="ct-session-list">
                  {session.sessions.map((item) => {
                    const selected = viewMode === "session" && item.session_id === session.selectedSessionId;
                    return (
                      <div className="ct-session-item-row" key={item.session_id}>
                        <button
                          type="button"
                          className={`ct-session-item${selected ? " is-selected" : ""}`}
                          data-session-id={item.session_id}
                          onClick={() => session.onSelectSession(item.session_id)}
                          aria-current={selected ? "page" : undefined}
                        >
                          <span className="ct-session-item-copy">
                            <strong>{item.topic}</strong>
                            <small>{item.message_count} 条消息</small>
                          </span>
                        </button>
                        <RowActionsMenu
                          label={`会话“${item.topic}”`}
                          disabled={project.trashMutationBusy}
                          onMoveToTrash={() => {
                            if (window.confirm(`将会话“${item.topic}”移到回收站？\n消息、Run 和报告会保留，可在回收站恢复。`)) {
                              void project.onMoveSessionToTrash(item.project_id, item.session_id);
                            }
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : session.status === "empty" ? (
                <p className="ct-sidebar-note">还没有会话。点击右上角 + 开始记录排查主题。</p>
              ) : session.status === "loading" ? (
                <p className="ct-sidebar-note">正在读取会话…</p>
              ) : null}
              {session.hasMore ? <button type="button" className="ct-link-button" onClick={() => void session.onLoadMore()} disabled={session.status === "loading"}>加载更多会话</button> : null}
            </div>
          </section>
        ) : null}
        {trashOpen ? (
          <section className="ct-trash-panel" aria-label="回收站">
            <div className="ct-trash-heading">
              <div><p className="ct-overline">RECOVERABLE</p><strong>回收站</strong></div>
              <button type="button" className="ct-link-button" onClick={() => void project.onLoadTrash()} disabled={project.trashStatus === "loading"}>刷新</button>
            </div>
            {project.trashError ? <p className="ct-inline-error" role="alert">{project.trashError}</p> : null}
            {project.trashStatus === "loading" && !project.trash ? <p className="ct-sidebar-note">正在读取回收站…</p> : null}
            {project.trash ? (
              <>
                {project.trash.projects.map((item) => (
                  <div className="ct-trash-item" key={`project:${item.project_id}`}>
                    <span><strong>{item.name}</strong><small>项目 · {item.available_index_ids.length} 个索引快照</small></span>
                    <button type="button" className="ct-link-button" disabled={project.trashMutationBusy} onClick={() => void project.onRestoreProject(item.project_id)}>恢复</button>
                  </div>
                ))}
                {project.trash.sessions.map((item) => (
                  <div className="ct-trash-item" key={`session:${item.session_id}`}>
                    <span><strong>{item.topic}</strong><small>会话 · {project.projects.find((candidate) => candidate.project_id === item.project_id)?.name ?? item.project_id}</small></span>
                    <button type="button" className="ct-link-button" disabled={project.trashMutationBusy} onClick={() => void project.onRestoreSession(item.project_id, item.session_id)}>恢复</button>
                  </div>
                ))}
                {project.trash.projects.length === 0 && project.trash.sessions.length === 0 ? <p className="ct-sidebar-note">回收站为空。</p> : null}
                {project.trash.projects.length > 0 ? <p className="ct-sidebar-note">恢复项目不会恢复单独移入回收站的会话。</p> : null}
              </>
            ) : null}
          </section>
        ) : null}
      <div className="ct-sidebar-footer">
        <button
          type="button"
          className="ct-sidebar-action ct-sidebar-settings"
          onClick={() => {
            closeProjectPicker();
            void onOpenSettings();
          }}
          aria-haspopup="dialog"
          aria-expanded={settingsOpen}
        >
          <span><span className="ct-sidebar-settings-icon" aria-hidden="true">⚙</span>设置</span>
          <span aria-hidden="true">›</span>
        </button>
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
  const finishImport = async (submit: () => Promise<boolean>) => {
    if (await submit() && mode === "create-project") {
      onClose();
    }
  };
  return (
    <section className="ct-operation-panel" aria-label="项目操作">
      <div className="ct-operation-header">
        <div>
          <p className="ct-overline">PROJECT OPERATIONS</p>
          <h2>{mode === "create-project" ? "创建项目" : "源代码与索引"}</h2>
          <p>{mode === "create-project" ? "导入本地目录或 GitHub HTTPS 仓库，并建立固定索引。" : "在同一处管理项目来源和固定快照；两者保持独立的生命周期。"}</p>
        </div>
        <button type="button" className="ct-close-button" onClick={onClose} aria-label="关闭项目操作">关闭</button>
      </div>

      <div className="ct-operation-body">
        {mode === "project-management" ? (
          <div className="ct-segmented" role="tablist" aria-label="项目管理">
            <button type="button" role="tab" aria-selected={activeTab === "source"} className={activeTab === "source" ? "is-active" : ""} onClick={() => setActiveTab("source")}>源代码来源</button>
            <button type="button" role="tab" aria-selected={activeTab === "index"} className={activeTab === "index" ? "is-active" : ""} onClick={() => setActiveTab("index")} disabled={!selectedProject}>固定索引</button>
          </div>
        ) : null}
          {mode === "create-project" || activeTab === "source" ? (
            <>
              <div className="ct-segmented" role="tablist" aria-label="导入来源">
                <button type="button" role="tab" aria-selected={importMode === "local"} className={importMode === "local" ? "is-active" : ""} onClick={() => setImportMode("local")}>本地目录</button>
                <button type="button" role="tab" aria-selected={importMode === "github"} className={importMode === "github" ? "is-active" : ""} onClick={() => setImportMode("github")}>GitHub HTTPS</button>
              </div>
              {importMode === "local" ? (
                <form className="ct-operation-form" onSubmit={(event) => void finishImport(() => localImport.onSubmit(event))}>
              <div className="ct-form-grid">
                <div>
                  <label htmlFor="operation-local-name">项目名称</label>
                  <input id="operation-local-name" value={localImport.name} onChange={(event) => localImport.onNameChange(event.target.value)} placeholder="例如：我的项目" spellCheck={false} />
                </div>
                <div className="ct-form-wide">
                  <label htmlFor="operation-local-path">API 运行电脑上的本地目录路径</label>
                  <input id="operation-local-path" value={localImport.path} onChange={(event) => localImport.onPathChange(event.target.value)} placeholder="D:\\projects\\my-repo" spellCheck={false} />
                </div>
              </div>
              <p className="ct-form-help">请输入 API 所在电脑上的仓库目录。来源只读扫描，导入完成后会打开固定索引；若目录未开放，页面会说明配置方法。</p>
              {localImport.error ? <p className="ct-inline-error" role="alert">{localImport.error}</p> : null}
              <div className="ct-form-actions">
                <button type="submit" className="ct-button ct-button-primary" disabled={importBusy(localImport.status) || importBusy(localImport.attempt?.status ?? null)}>{localImport.status === "indexing" ? "建立中…" : "导入并建立索引"}</button>
                {localImport.attempt ? <ImportAttemptStatus attempt={localImport.attempt} busy={importBusy(localImport.status)} onRetry={() => void finishImport(localImport.onRetry)} onRefresh={localImport.onRefresh} /> : null}
              </div>
                </form>
              ) : (
                <form className="ct-operation-form" onSubmit={(event) => void finishImport(() => githubImport.onSubmit(event))}>
              <div className="ct-form-grid">
                <div>
                  <label htmlFor="operation-github-name">项目名称</label>
                  <input id="operation-github-name" value={githubImport.name} onChange={(event) => githubImport.onNameChange(event.target.value)} placeholder="例如：我的项目" spellCheck={false} />
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
                <button type="submit" className="ct-button ct-button-primary" disabled={importBusy(githubImport.status) || importBusy(githubImport.attempt?.status ?? null)}>{githubImport.status === "acquiring" ? "获取中…" : githubImport.status === "indexing" ? "建立中…" : "导入 GitHub 仓库"}</button>
                {githubImport.attempt ? <ImportAttemptStatus attempt={githubImport.attempt} busy={importBusy(githubImport.status)} onRetry={() => void finishImport(githubImport.onRetry)} onRefresh={githubImport.onRefresh} onRetryCleanup={githubImport.onRetryCleanup} /> : null}
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
                <span><small>AVAILABLE</small><strong>{index.indexes.length} 个快照</strong></span>
              </div>
              <div className="ct-index-list">
                {index.indexes.length > 0 ? index.indexes.map((item) => (
                  <button
                    type="button"
                    key={item.index_id}
                    className={`ct-index-item${index.response?.index.index_id === item.index_id ? " is-selected" : ""}`}
                    onClick={() => void index.onOpenIndex(item.index_id)}
                    disabled={index.status === "loading"}
                    aria-current={index.response?.index.index_id === item.index_id ? "true" : undefined}
                  >
                    <span><strong>{item.index_id === selectedProject.latest_index_id ? "当前版本" : "历史版本"}</strong><small>{item.status === "ready" ? "固定快照" : item.status === "building" ? "建立中" : "建立失败"}</small></span>
                    <code>{shortHash(item.snapshot_hash)}</code>
                    <span className={`ct-index-state is-${item.status}`}>{item.status === "ready" ? "可用" : item.status === "building" ? "建立中" : "失败"}</span>
                  </button>
                )) : <p className="ct-muted">当前项目没有已保存快照。</p>}
              </div>
              {index.error ? <p className="ct-inline-error" role="alert">{index.error}</p> : null}
              <div className="ct-index-forms">
                <div className="ct-operation-form">
                  <div className="ct-form-heading"><strong>创建新快照</strong><span>{index.status === "creating" ? "建立中" : indexStatusLabel(index.status)}</span></div>
                  <p className="ct-form-help">使用当前项目来源建立固定快照；版本列表可直接打开历史快照。</p>
                  <button type="button" className="ct-button ct-button-primary" onClick={() => void index.onCreate()} disabled={index.status === "creating"}>{index.status === "creating" ? "建立中…" : "创建索引"}</button>
                </div>
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
    </section>
  );
}

function ImportAttemptStatus({ attempt, busy, onRetry, onRefresh, onRetryCleanup }: {
  attempt: ImportAttempt;
  busy: boolean;
  onRetry: Action;
  onRefresh: ImportAction;
  onRetryCleanup?: Action;
}) {
  const pending = attempt.status === "queued" || attempt.status === "acquiring" || attempt.status === "indexing";
  return (
    <div className="ct-import-attempt" data-state={attempt.status}>
      <span>{importStatusLabel(attempt.status)}</span>
      <code>{attempt.source_kind === "github" ? shortHash(attempt.resolved_commit ?? attempt.attempt_id) : shortHash(attempt.attempt_id)}</code>
      {pending ? <button type="button" className="ct-link-button" onClick={() => void onRefresh()} disabled={busy}>{busy ? "查询中…" : "刷新状态"}</button> : null}
      {(attempt.status === "failed" || attempt.status === "interrupted") ? <button type="button" className="ct-link-button" onClick={() => void onRetry()} disabled={busy}>{busy ? "重试中…" : "重试"}</button> : null}
      {attempt.cleanup_status === "failed" && onRetryCleanup ? <button type="button" className="ct-link-button" onClick={() => void onRetryCleanup()} disabled={busy}>{busy ? "处理中…" : "重试清理"}</button> : null}
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
        <h1 title={project.selectedProject?.name ?? "建立一个代码理解工作区"}>{project.selectedProject?.name ?? "建立一个代码理解工作区"}</h1>
        <p>{project.selectedProject ? "浏览固定快照、入口和代码关系。" : "导入本地目录或 GitHub 仓库，开始代码理解工作区。"}</p>
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
  graph,
  panelMode,
  onPanelModeChange,
  onOpenOperation,
}: {
  project: ProjectUi;
  index: IndexUi;
  knowledge: KnowledgeUi;
  graph: GraphUi;
  panelMode: UiPanelMode;
  onPanelModeChange: (mode: UiPanelMode) => void;
  onOpenOperation: OpenOperation;
}) {
  const overview = knowledge.overview;
  const [view, setView] = useState<"architecture" | "features">("architecture");
  const [detailRequest, setDetailRequest] = useState(0);
  const [detail, setDetail] = useState<"search" | "http" | "coverage" | null>(null);
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
              <div className="ct-knowledge-toolbar" aria-label="项目知识视图">
                <div className="ct-panel-switch" role="tablist" aria-label="项目知识">
                  <button type="button" role="tab" aria-selected={view === "architecture"} className={view === "architecture" ? "is-active" : ""} onClick={() => setView("architecture")}>架构</button>
                  <button type="button" role="tab" aria-selected={view === "features"} className={view === "features" ? "is-active" : ""} onClick={() => setView("features")}>功能</button>
                </div>
                <span title={`index ${overview.index.index_id} · snapshot ${overview.index.snapshot_hash}`} className="ct-knowledge-revision">固定版本 · {shortHash(overview.index.snapshot_hash)}</span>
                <button type="button" className="ct-link-button" onClick={() => { setView("architecture"); setDetailRequest((current) => current + 1); setDetail("search"); }}>搜索符号</button>
                <button type="button" className="ct-link-button" onClick={() => { setView("architecture"); setDetailRequest((current) => current + 1); setDetail("http"); }}>HTTP 关联 · {overview.http_associations.length}</button>
                <button type="button" className="ct-link-button" onClick={() => { setView("architecture"); setDetailRequest((current) => current + 1); setDetail("coverage"); }}>版本与覆盖{overview.truncated || overview.classification_coverage.truncated || overview.http_association_coverage.analysis_status === "incomplete" ? " · 部分分析" : ""}</button>
              </div>
              <div className="ct-knowledge-architecture" hidden={view !== "architecture"}>
                <ArchitectureSection overview={overview} knowledge={knowledge} graph={graph} panelMode={panelMode} onPanelModeChange={onPanelModeChange} detail={detail} detailRequest={detailRequest} active={view === "architecture"} onDetailChange={setDetail} />
              </div>
              <div className="ct-knowledge-features" hidden={view !== "features"}><FeatureSection knowledge={knowledge} /></div>
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
  relatedRelationCount: number;
  relatedEdgeCount: number;
  onInspect: (moduleId: string | null) => void;
  onOpen: (module: OverviewModule) => void;
};

const classificationLabels = {
  application_entry: "入口",
  public_api: "API",
  extension_point: "扩展点",
  unknown: "未识别",
} as const;

function ClassificationCounts({ counts, prefix }: { counts: OverviewClassificationCounts; prefix?: string }) {
  const label = `${prefix ? `${prefix}；` : ""}当前规则已识别：入口 ${counts.application_entry}、API ${counts.public_api}、扩展点 ${counts.extension_point}、未识别 ${counts.unknown}`;
  return (
    <span className="ct-classification-counts" aria-label={label} title={label}>
      {`${prefix ? `${prefix} · ` : ""}入口 ${counts.application_entry} · API ${counts.public_api} · 扩展点 ${counts.extension_point} · 未识别 ${counts.unknown}`}
    </span>
  );
}

function ArchitectureDirectoryNode({ data }: NodeProps<Node<ArchitectureDirectoryNodeData, "architecture-directory">>) {
  const { directory } = data;
  const classification = `入口 ${directory.classificationCounts.application_entry} · API ${directory.classificationCounts.public_api} · 扩展点 ${directory.classificationCounts.extension_point} · 未识别 ${directory.classificationCounts.unknown}`;
  const detail = [
    directory.group.group_key,
    `${directory.loadedModuleCount} 个模块`,
    classification,
    directory.internalRelationCount > 0 ? `${directory.internalRelationCount} 组内部关系，${directory.internalEdgeCount} 条源码边` : null,
    directory.dependencyCycleGroupSize ? `属于 ${directory.dependencyCycleGroupSize} 个职责/目录间的循环依赖` : null,
    directory.omittedIndexModuleCount > 0 ? `概览预算省略 ${directory.omittedIndexModuleCount} 个模块` : null,
    directory.omittedByCanvasLimit > 0 ? `画布限额省略 ${directory.omittedByCanvasLimit} 个模块` : null,
  ].filter((item): item is string => Boolean(item)).join("；");
  return (
    <div className={`ct-architecture-directory${data.selected ? " is-selected" : ""}${directory.highlighted ? " is-highlighted" : " is-dimmed"}`}>
      <Handle id="target-top" type="target" position={Position.Top} className="ct-architecture-handle" />
      <Handle id="target-bottom" type="target" position={Position.Bottom} className="ct-architecture-handle" />
      <Handle id="target-right" type="target" position={Position.Right} className="ct-architecture-handle" />
      <Handle id="target-left" type="target" position={Position.Left} className="ct-architecture-handle" />
      <Handle id="source-top" type="source" position={Position.Top} className="ct-architecture-handle" />
      <Handle id="source-bottom" type="source" position={Position.Bottom} className="ct-architecture-handle" />
      <Handle id="source-right" type="source" position={Position.Right} className="ct-architecture-handle" />
      <Handle id="source-left" type="source" position={Position.Left} className="ct-architecture-handle" />
      <div className="ct-architecture-directory-heading">
        <button type="button" className="ct-architecture-directory-title nodrag nopan" onClick={() => data.onSelect(directory.group)} title={directory.group.group_key}>
          <span>{directory.group.group_kind === "responsibility" ? "RESPONSIBILITY" : "SOURCE DIRECTORY"}</span>
          <strong title={detail}>{directory.group.group_kind === "responsibility" ? directory.group.label : directory.group.group_key}</strong>
        </button>
        <button type="button" className="ct-architecture-expand nodrag nopan" onClick={() => data.onToggle(directory.group)} aria-label={`${directory.expanded ? "折叠" : "展开"} ${directory.group.group_key}`} aria-expanded={directory.expanded}>
          {directory.expanded ? "−" : "+"}
        </button>
      </div>
      <div className="ct-architecture-directory-meta" title={detail}>
        <span>{directory.loadedModuleCount} modules</span>
        {directory.internalRelationCount > 0 ? <span>{directory.internalRelationCount} 组内关系</span> : null}
        {directory.dependencyCycleGroupSize ? <span>循环依赖组 · {directory.dependencyCycleGroupSize}</span> : null}
        {directory.omittedIndexModuleCount > 0 ? <span>概览省略 {directory.omittedIndexModuleCount}</span> : null}
        {directory.omittedByCanvasLimit > 0 ? <span>画布省略 {directory.omittedByCanvasLimit}</span> : null}
      </div>
    </div>
  );
}

function ArchitectureModuleNode({ data }: NodeProps<Node<ArchitectureModuleNodeData, "architecture-module">>) {
  const { module } = data;
  const classification = `入口 ${module.classification_counts.application_entry} · API ${module.classification_counts.public_api} · 扩展点 ${module.classification_counts.extension_point} · 未识别 ${module.classification_counts.unknown}`;
  return (
    <button
      type="button"
      className={`ct-architecture-module nodrag nopan${data.highlighted ? " is-highlighted" : " is-dimmed"}`}
      onClick={() => { data.onInspect(null); data.onOpen(data.module); }}
      onMouseEnter={() => data.onInspect(module.module_id)}
      onMouseLeave={(event) => { if (document.activeElement !== event.currentTarget) data.onInspect(null); }}
      onFocus={() => data.onInspect(module.module_id)}
      onBlur={(event) => { if (!event.currentTarget.matches(":hover")) data.onInspect(null); }}
      title={`${module.symbol.path}:L${module.symbol.start_line} · ${classification} · 当前画布返回 ${data.relatedRelationCount} 组相邻模块关系、${data.relatedEdgeCount} 条静态边；悬停或键盘聚焦可显示 · 打开模块关系图`}
    >
      <Handle id="target-top" type="target" position={Position.Top} className="ct-architecture-handle" />
      <Handle id="target-bottom" type="target" position={Position.Bottom} className="ct-architecture-handle" />
      <Handle id="target-right" type="target" position={Position.Right} className="ct-architecture-handle" />
      <Handle id="target-left" type="target" position={Position.Left} className="ct-architecture-handle" />
      <Handle id="source-top" type="source" position={Position.Top} className="ct-architecture-handle" />
      <Handle id="source-bottom" type="source" position={Position.Bottom} className="ct-architecture-handle" />
      <Handle id="source-right" type="source" position={Position.Right} className="ct-architecture-handle" />
      <Handle id="source-left" type="source" position={Position.Left} className="ct-architecture-handle" />
      <strong>{module.symbol.qualified_name}</strong>
      <small>{module.symbol.path}:L{module.symbol.start_line}</small>
      <span>{module.entry_reachable ? "入口可达" : "未命中入口"} · 打开关系图 ↗</span>
    </button>
  );
}

const architectureNodeTypes: NodeTypes = {
  "architecture-directory": ArchitectureDirectoryNode,
  "architecture-module": ArchitectureModuleNode,
};

function ArchitectureSection({ overview, knowledge, graph, panelMode, onPanelModeChange, detail, detailRequest, active, onDetailChange }: { overview: ProjectOverview; knowledge: KnowledgeUi; graph: GraphUi; panelMode: UiPanelMode; onPanelModeChange: (mode: UiPanelMode) => void; detail: "search" | "http" | "coverage" | null; detailRequest: number; active: boolean; onDetailChange: (detail: "search" | "http" | "coverage" | null) => void }) {
  const [expandedGroupIds, setExpandedGroupIds] = useState<string[]>([]);
  const [focusedGroupId, setFocusedGroupId] = useState<string | null>(null);
  const expandedBeforeFocus = useRef<string[] | null>(null);
  const [selectedHttpGroupId, setSelectedHttpGroupId] = useState<string | null>(null);
  const [relationKinds, setRelationKinds] = useState<Array<"imports" | "calls">>(["imports", "calls"]);
  const [showHttpAssociations, setShowHttpAssociations] = useState(true);
  const [showSupportingDirectories, setShowSupportingDirectories] = useState(false);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  useEffect(() => { setSelectedEdgeId(null); }, [detailRequest]);
  const [activeArchitectureModuleId, setActiveArchitectureModuleId] = useState<string | null>(null);
  const [moduleViewState, setModuleViewState] = useState<{ rootId: string; mode: "definitions" | "imports" | "calls"; callSymbol: SymbolRecord | null } | null>(null);
  const [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const httpGroups = overview.groups.filter((group) => group.group_kind === "http_path" && group.index_id === overview.index.index_id);
  const responsibilityStatusLabel = {
    not_configured: "未配置；按源码目录分组",
    ready: "已配置；职责全部匹配",
    partial: "覆盖不完整；保留目录 fallback",
    invalid: "无效；按源码目录回退",
  }[overview.responsibility_status];
  const architectureRoot = overview.modules.find((module) => module.symbol.symbol_id === knowledge.architectureGraphRootId) ?? null;
  const isModuleDrilldown = Boolean(architectureRoot);
  const activeModuleView = architectureRoot && moduleViewState?.rootId === architectureRoot.symbol.symbol_id ? moduleViewState : null;
  const moduleViewMode = activeModuleView?.mode ?? "definitions";
  const callRoot = activeModuleView?.callSymbol ?? null;
  const moduleSymbolsPage = knowledge.moduleSymbolsPage?.module.symbol_id === architectureRoot?.symbol.symbol_id
    ? knowledge.moduleSymbolsPage
    : null;
  const moduleDefinitions = moduleSymbolsPage ? buildModuleDefinitionRows(moduleSymbolsPage.symbols) : [];
  useEffect(() => {
    if (knowledge.architectureGraphRootId && !architectureRoot) knowledge.onCloseArchitectureGraph();
  }, [architectureRoot, knowledge.architectureGraphRootId, knowledge.onCloseArchitectureGraph]);
  const model = useMemo(() => buildArchitectureGraph(overview, {
    expandedGroupIds,
    showSupportingDirectories,
    focusedGroupId,
    selectedHttpGroupId,
    selectedGroupId: knowledge.selectedOverviewGroupId,
    relationKinds,
    showHttpAssociations,
  }), [expandedGroupIds, focusedGroupId, knowledge.selectedOverviewGroupId, overview, relationKinds, selectedHttpGroupId, showHttpAssociations, showSupportingDirectories]);
  const selectedStaticRelation = model.relations.find((relation) => relation.id === selectedEdgeId) ?? null;
  const selectedHttpRelation = model.httpRelations.find((relation) => relation.id === selectedEdgeId) ?? null;
  useEffect(() => {
    if (selectedEdgeId && !selectedStaticRelation && !selectedHttpRelation) setSelectedEdgeId(null);
  }, [selectedEdgeId, selectedStaticRelation, selectedHttpRelation]);
  const endpointContains = (endpoint: string, moduleId: string) => endpoint === moduleId || model.directories.some((directory) => directory.group.group_id === endpoint && directory.group.module_ids.includes(moduleId));
  const displayedHttpAssociations = selectedHttpRelation ? overview.http_associations.filter((association) => endpointContains(selectedHttpRelation.source, association.client_module.symbol_id) && endpointContains(selectedHttpRelation.target, association.server_module.symbol_id)) : overview.http_associations;
  const focusedGroup = model.directories.find((directory) => directory.group.group_id === focusedGroupId)?.group ?? null;
  const selectedArchitectureGroup = model.directories.find((directory) => directory.group.group_id === knowledge.selectedOverviewGroupId)?.group ?? null;
  useEffect(() => {
    if (focusedGroupId && !focusedGroup) {
      setFocusedGroupId(null);
      setExpandedGroupIds(expandedBeforeFocus.current ?? []);
      expandedBeforeFocus.current = null;
    }
  }, [focusedGroup, focusedGroupId]);
  const inspectorVisible = canShowKnowledgeInspector(panelMode, isModuleDrilldown) && Boolean(detail || knowledge.selectedOverviewGroupId || selectedEdgeId);
  const visibleModuleIds = useMemo(() => new Set(model.directories.flatMap((directory) => directory.visibleModules.map(({ module }) => module.module_id))), [model]);
  const displayedArchitectureRelations = useMemo(
    () => visibleArchitectureRelations(model.relations, visibleModuleIds, activeArchitectureModuleId),
    [activeArchitectureModuleId, model, visibleModuleIds],
  );
  useEffect(() => {
    if (activeArchitectureModuleId && !visibleModuleIds.has(activeArchitectureModuleId)) setActiveArchitectureModuleId(null);
  }, [activeArchitectureModuleId, model, visibleModuleIds]);
  const focusedDirectoryNodeIds = useMemo(() => {
    const focused = new Set(model.focusNodeIds);
    return model.directories.filter((directory) => focused.has(directory.group.group_id)).map((directory) => directory.group.group_id);
  }, [model.directories, model.focusNodeIds]);
  useEffect(() => {
    if (!active || !flow || model.directories.length === 0) return;
    const timer = window.setTimeout(() => {
      const nodeIds = focusedDirectoryNodeIds.length > 0
        ? focusedDirectoryNodeIds
        : model.directories.map((directory) => directory.group.group_id);
      void flow.fitView({ nodes: nodeIds.map((id) => ({ id })), padding: focusedDirectoryNodeIds.length > 0 ? 0.3 : 0.16, duration: 260, minZoom: 0.28, maxZoom: 1.15 });
    }, 100);
    return () => window.clearTimeout(timer);
  }, [active, flow, focusedDirectoryNodeIds, inspectorVisible, model.directories]);
  const architectureNodes = useMemo(() => {
    const nodes: Node[] = model.directories.map((directory) => ({
      id: directory.group.group_id,
      type: "architecture-directory",
      position: directory.position,
      data: {
        directory,
        selected: knowledge.selectedOverviewGroupId === directory.group.group_id,
        onSelect: (group: OverviewGroup) => {
          setSelectedHttpGroupId(null);
          onDetailChange(null);
          setSelectedEdgeId(null);
          knowledge.onSelectOverviewGroup(group);
        },
        onToggle: (group: OverviewGroup) => {
          setSelectedHttpGroupId(null);
          onDetailChange(null);
          setSelectedEdgeId(null);
          knowledge.onSelectOverviewGroup(group);
          setExpandedGroupIds((current) => current.includes(group.group_id) ? [] : [group.group_id]);
        },
      },
      style: { width: directory.width, height: directory.height },
      className: "ct-architecture-directory-node",
      zIndex: 0,
    }));
    for (const directory of model.directories) {
      for (const visible of directory.visibleModules) {
        const relatedModuleRelations = model.relations.filter((relation) =>
          (relation.source === visible.module.module_id || relation.target === visible.module.module_id)
          && visibleModuleIds.has(relation.source)
          && visibleModuleIds.has(relation.target),
        );
        nodes.push({
          id: visible.module.module_id,
          type: "architecture-module",
          parentId: directory.group.group_id,
          extent: "parent",
          position: visible.position,
          data: {
            module: visible.module,
            highlighted: visible.highlighted,
            relatedRelationCount: relatedModuleRelations.reduce((total, relation) => total + relation.relationCount, 0),
            relatedEdgeCount: relatedModuleRelations.reduce((total, relation) => total + relation.edgeCount, 0),
            onInspect: setActiveArchitectureModuleId,
            onOpen: knowledge.onOpenOverviewModule,
          },
          style: { width: 156, height: 68 },
          className: "ct-architecture-module-node",
          zIndex: 2,
        });
      }
    }
    return nodes;
  }, [knowledge, model, onDetailChange, visibleModuleIds]);
  const reactFlowModel = useMemo(() => {
    const nodeCenters = new Map<string, ArchitecturePosition>();
    for (const directory of model.directories) {
      nodeCenters.set(directory.group.group_id, { x: directory.position.x + directory.width / 2, y: directory.position.y + directory.height / 2 });
      for (const visible of directory.visibleModules) {
        nodeCenters.set(visible.module.module_id, {
          x: directory.position.x + visible.position.x + 78,
          y: directory.position.y + visible.position.y + 34,
        });
      }
    }
    const handles = (source: string, target: string) => {
      const sourcePoint = nodeCenters.get(source);
      const targetPoint = nodeCenters.get(target);
      const sides = sourcePoint && targetPoint ? architectureEdgeSides(sourcePoint, targetPoint) : { source: "right", target: "left" };
      return { sourceHandle: `source-${sides.source}`, targetHandle: `target-${sides.target}` };
    };
    const staticEdges: Edge[] = displayedArchitectureRelations.map((relation) => ({
      id: relation.id,
      source: relation.source,
      target: relation.target,
      ...handles(relation.source, relation.target),
      type: "smoothstep",
      label: `${relation.relationKinds.map((kind) => kind === "imports" ? "静态导入" : "静态调用").join(" + ")} · ${relation.relationCount} 对 / ${relation.edgeCount} 边`,
      markerEnd: { type: MarkerType.ArrowClosed, color: relation.highlighted ? "#3158d9" : "#b2bac7" },
      style: { stroke: relation.highlighted ? "#3158d9" : "#aeb7c5", strokeWidth: relation.highlighted ? 1.8 : 1.1, opacity: relation.highlighted ? 0.78 : 0.18 },
      labelStyle: { fill: relation.highlighted ? "#43516a" : "#8b93a0", fontSize: 10 },
      labelBgStyle: { fill: "#fff", fillOpacity: relation.highlighted ? 0.96 : 0.85 },
      labelBgPadding: [5, 3] as [number, number],
      interactionWidth: 18,
      data: { relation },
    }));
    const httpEdges: Edge[] = model.httpRelations.map((relation) => ({
      id: relation.id,
      source: relation.source,
      target: relation.target,
      ...handles(relation.source, relation.target),
      type: "smoothstep",
      label: `HTTP ${relation.labels.join(" · ")}${relation.associationCount > relation.labels.length ? ` · +${relation.associationCount - relation.labels.length}` : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: relation.highlighted ? "#087f75" : "#9ba7a9" },
      style: { stroke: relation.highlighted ? "#087f75" : "#9ba7a9", strokeWidth: 2, strokeDasharray: "6 4", opacity: relation.highlighted ? 0.95 : 0.35 },
      labelStyle: { fill: relation.highlighted ? "#075e57" : "#778487", fontSize: 10 },
      labelBgStyle: { fill: "#f5fbfa", fillOpacity: 0.98 },
      labelBgPadding: [5, 3],
      interactionWidth: 20,
      data: { httpRelation: relation },
    }));
    const allEdges = [...staticEdges, ...httpEdges];
    const overviewEdges = focusedGroupId ? allEdges : allEdges.filter((edge) => architectureOverviewEdgeVisible(edge.source, edge.target, model.directories));
    const foldedEdgeCount = allEdges.length - overviewEdges.length;
    const edges = overviewEdges.map((edge) => ({
      ...edge,
      type: focusedGroupId ? edge.type : "straight",
      label: edge.id === hoveredEdgeId || edge.id === selectedEdgeId ? edge.label : undefined,
    }));
    return { edges, foldedEdgeCount };
  }, [displayedArchitectureRelations, focusedGroupId, hoveredEdgeId, model, selectedEdgeId]);
  const coverageWarnings = [
    overview.truncated ? "概览路径、未知边界或扫描分析存在截断" : null,
    overview.coverage.visible_groups < overview.coverage.total_groups ? `概览仅返回 ${overview.coverage.visible_groups}/${overview.coverage.total_groups} 组（含 HTTP 路由组）` : null,
    overview.coverage.visible_modules < overview.coverage.total_modules ? `概览仅返回 ${overview.coverage.visible_modules}/${overview.coverage.total_modules} 个模块` : null,
    overview.coverage.visible_module_relations < overview.coverage.total_module_relations ? `概览仅返回 ${overview.coverage.visible_module_relations}/${overview.coverage.total_module_relations} 条模块关系` : null,
    overview.classification_coverage.truncated ? `分类记录已返回，但部分角色尚未识别或分析受限： ${overview.classification_coverage.visible}/${overview.classification_coverage.total} 条分类记录` : null,
    overview.unresolved.length > 0 ? `${overview.unresolved.length} 条调用尚不能确定目标，未绘制为确定依赖` : null,
    model.omittedRelationCount > 0 ? `${model.omittedRelationCount} 条关系（${model.omittedRelationEdgeCount} 边）因端点不在可见源码目录中未绘制` : null,
    model.unmatchedHttpModuleCount > 0 ? `所选 HTTP 路由有 ${model.unmatchedHttpModuleCount} 个模块未映射到可见源码目录` : null,
    model.omittedHttpAssociationCount > 0 ? `${model.omittedHttpAssociationCount} 条 HTTP 形状关联未绘制，因为端点目录不在可见分组中；HTTP 关联详情仍保留双端证据` : null,
    model.summarizedHttpAssociationCount > 0 ? `${model.summarizedHttpAssociationCount} 条 HTTP 形状关联因 40 模块画布上限折叠到目录节点` : null,
    overview.http_association_coverage.analysis_status === "not_analyzed" ? "此旧索引没有 TypeScript HTTP 分析记录，不能将 0 条关联解释为无调用" : null,
    overview.http_association_coverage.path_analysis === "literal_only" ? "此旧索引只分析静态字面量 URL；参数化路径尚未分析" : null,
    overview.http_association_coverage.analysis_status === "incomplete" ? "部分前端请求尚不能与后端接口确定匹配；不表示请求失败" : null,
    overview.http_association_coverage.visible_match_count < overview.http_association_coverage.matched_count ? `HTTP 形状关联仅显示 ${overview.http_association_coverage.visible_match_count}/${overview.http_association_coverage.matched_count} 条` : null,
    model.focusHiddenGroupCount > 0 ? `职责聚焦隐藏 ${model.focusHiddenGroupCount} 个职责/目录；概览预算截断另行提示` : null,
    model.focusHiddenRelationCount > 0 ? `聚焦范围外还有 ${model.focusHiddenRelationCount} 条可用静态关系` : null,
    model.focusHiddenHttpAssociationCount > 0 ? `聚焦范围外还有 ${model.focusHiddenHttpAssociationCount} 条可用 HTTP 形状关联` : null,
    model.dependencyCycleCount > 0 ? `图中保留 ${model.dependencyCycleCount} 个真实循环依赖组，未合成额外关系` : null,
  ].filter((item): item is string => item !== null);
  const selectHttpGroup = (groupId: string) => {
    onDetailChange(null);
    setSelectedEdgeId(null);
    const group = httpGroups.find((item) => item.group_id === groupId) ?? null;
    setSelectedHttpGroupId(group?.group_id ?? null);
    if (group) {
      knowledge.onSelectOverviewGroup(group);
      const moduleIds = new Set(group.module_ids);
      const directoryIds = overview.groups
        .filter((item) => item.index_id === overview.index.index_id && (item.group_kind === "responsibility" || item.group_kind === "source_directory"))
        .filter((item) => item.module_ids.some((moduleId) => moduleIds.has(moduleId)))
        .map((item) => item.group_id);
      setExpandedGroupIds(directoryIds[0] ? [directoryIds[0]] : []);
      if (directoryIds.some((id) => model.supportingDirectoryCount > 0 && !model.directories.some((directory) => directory.group.group_id === id))) {
        setShowSupportingDirectories(true);
      }
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
    if (!flow || architectureNodes.length === 0) return;
    const nodeIds = focusedDirectoryNodeIds.length > 0 ? focusedDirectoryNodeIds : model.directories.map((directory) => directory.group.group_id);
    void flow.fitView({ nodes: nodeIds.map((id) => ({ id })), padding: 0.18, duration: 240 });
  };
  const focusArchitectureGroup = (group: OverviewGroup) => {
    if (expandedBeforeFocus.current === null) {
      expandedBeforeFocus.current = expandedGroupIds;
    }
    setExpandedGroupIds([group.group_id]);
    setFocusedGroupId(group.group_id);
    setSelectedHttpGroupId(null);
    knowledge.onSelectOverviewGroup(group);
  };
  const returnToOverview = () => {
    onDetailChange(null);
    setSelectedEdgeId(null);
    setFocusedGroupId(null);
    setExpandedGroupIds(expandedBeforeFocus.current ?? []);
    expandedBeforeFocus.current = null;
    setSelectedHttpGroupId(null);
    knowledge.onClearOverviewGroup();
  };
  const resetArchitecture = () => {
    setFocusedGroupId(null);
    expandedBeforeFocus.current = null;
    setExpandedGroupIds([]);
    setSelectedHttpGroupId(null);
    setRelationKinds(["imports", "calls"]);
    setShowHttpAssociations(true);
    knowledge.onClearOverviewGroup();
  };

  return (
    <div className={`ct-knowledge-workspace${inspectorVisible ? " has-detail" : ""}`}>
    <section className="ct-architecture-section" aria-label="项目架构画布">
      <div className="ct-architecture-heading">
        <div><p className="ct-overline">ARCHITECTURE / FIXED INDEX</p><h3>项目架构</h3><p>职责与未归属源码目录作为容器，静态 imports/calls 保持 source → target；HTTP 客户端到服务路由以独立方法与路径形状关联展示。</p></div>
        <div className="ct-architecture-count"><strong>{model.directories.length}</strong><span>职责/目录容器 · {overview.coverage.visible_modules}/{overview.coverage.total_modules} 模块可见{reactFlowModel.foldedEdgeCount > 0 ? ` · ${reactFlowModel.foldedEdgeCount} 条依赖折叠` : ""}</span></div>
      </div>
      {!isModuleDrilldown ? <>
      <div className="ct-architecture-toolbar">
        <div className="ct-architecture-toolbar-group ct-architecture-filters" role="group" aria-label="关系筛选">
          <span className="ct-architecture-toolbar-label">关系筛选</span>
          <label className="ct-architecture-http-entry" title="按 HTTP 方法与路径形状关联选择入口">
            <span>HTTP 入口</span>
            <select aria-label="筛选 HTTP 入口" value={selectedHttpGroupId ?? ""} onChange={(event) => selectHttpGroup(event.target.value)}>
              <option value="">全部 HTTP 入口</option>
              {httpGroups.map((group) => <option value={group.group_id} key={group.group_id}>{group.label} · {group.module_ids.length} 个模块</option>)}
            </select>
          </label>
          <label className={`ct-architecture-kind${relationKinds.includes("imports") ? " is-selected" : ""}`} title="imports">
            <input type="checkbox" checked={relationKinds.includes("imports")} onChange={() => toggleRelationKind("imports")} />
            <span>静态导入</span>
          </label>
          <label className={`ct-architecture-kind${relationKinds.includes("calls") ? " is-selected" : ""}`} title="calls">
            <input type="checkbox" checked={relationKinds.includes("calls")} onChange={() => toggleRelationKind("calls")} />
            <span>静态调用</span>
          </label>
          <label className={`ct-architecture-kind ct-architecture-http-kind${showHttpAssociations ? " is-selected" : ""}`} title="HTTP method/path 形状关联">
            <input type="checkbox" checked={showHttpAssociations} onChange={() => setShowHttpAssociations((current) => !current)} />
            <span>HTTP 关联</span>
          </label>
        </div>
        <div className="ct-architecture-toolbar-group ct-architecture-actions" role="group" aria-label="画布操作">
          <span className="ct-architecture-toolbar-label">画布操作</span>
          <button type="button" className="ct-architecture-action" onClick={focusGraph} disabled={architectureNodes.length === 0}>聚焦画布</button>
          {model.supportingDirectoryCount > 0 ? <button type="button" className={`ct-architecture-action${showSupportingDirectories ? " is-selected" : ""}`} aria-pressed={showSupportingDirectories} onClick={() => setShowSupportingDirectories((current) => !current)}>{showSupportingDirectories ? "隐藏辅助目录" : "显示辅助目录"} · {model.supportingDirectoryCount}</button> : null}
          {focusedGroup ? <span className="ct-architecture-focus-status" role="status">{focusedGroup.group_kind === "responsibility" ? "职责聚焦" : "目录聚焦"} · {focusedGroup.label}</span> : null}
          {!focusedGroup && selectedArchitectureGroup ? <button type="button" className="ct-architecture-action is-primary" onClick={() => focusArchitectureGroup(selectedArchitectureGroup)}>{selectedArchitectureGroup.group_kind === "responsibility" ? "聚焦此职责" : "聚焦此目录"}</button> : null}
          {focusedGroup && selectedArchitectureGroup && selectedArchitectureGroup.group_id !== focusedGroup.group_id ? <button type="button" className="ct-architecture-action is-primary" onClick={() => focusArchitectureGroup(selectedArchitectureGroup)}>切换到所选分组</button> : null}
          {focusedGroup ? <button type="button" className="ct-architecture-action" onClick={returnToOverview}>返回项目总览</button> : null}
          <button type="button" className="ct-architecture-action is-quiet" onClick={resetArchitecture}>重置画布</button>
        </div>
      </div>
      </> : <div className="ct-architecture-drilldown-toolbar">
        <button type="button" className="ct-link-button" onClick={knowledge.onCloseArchitectureGraph}>← 返回项目架构</button>
        <span>项目架构 <b>›</b> 模块内部结构 · <code title={architectureRoot?.symbol.path}>{architectureRoot?.symbol.path}</code></span>
        <div className="ct-architecture-drilldown-modes" role="group" aria-label="模块视图">
          <button type="button" className={`ct-link-button${moduleViewMode === "definitions" ? " is-selected" : ""}`} aria-pressed={moduleViewMode === "definitions"} onClick={() => architectureRoot && setModuleViewState({ rootId: architectureRoot.symbol.symbol_id, mode: "definitions", callSymbol: null })}>内部定义</button>
          <button type="button" className={`ct-link-button${moduleViewMode === "imports" ? " is-selected" : ""}`} aria-pressed={moduleViewMode === "imports"} onClick={() => {
            if (!architectureRoot) return;
            setModuleViewState({ rootId: architectureRoot.symbol.symbol_id, mode: "imports", callSymbol: null });
            knowledge.onOpenModuleImports(architectureRoot);
          }}>静态导入依赖</button>
          {moduleViewMode === "calls" ? <span className="ct-architecture-focus-status" role="status">静态调用 · {callRoot?.qualified_name ?? "所选符号"}</span> : null}
        </div>
        <button type="button" className="ct-button ct-button-secondary" onClick={() => onPanelModeChange("source")} disabled={!graph.selectedId}>查看所选源码</button>
        <small>固定 index · {overview.index.index_id}</small>
      </div>}
      {focusedGroup && model.directories.length === 1 ? <p className="ct-architecture-filter-note">当前筛选下没有一跳相邻职责或目录。</p> : null}
      {isModuleDrilldown ? (
        <div className="ct-architecture-drilldown">
          {moduleViewMode === "definitions" ? <section className="ct-architecture-module-definitions" aria-label="本文件内部定义">
            <div className="ct-structure-subheading"><strong>本文件内部定义</strong>{moduleSymbolsPage ? <span>{moduleSymbolsPage.symbols.length} 项已加载</span> : null}</div>
            <p className="ct-muted">只列出 <code>{architectureRoot?.symbol.path}</code> 的固定快照定义；尚有更多分页时，其他定义仍未加载。仅限定名和源码范围都能确认的类成员会缩进显示，不创建包含连线。</p>
            {knowledge.moduleSymbolsStatus === "loading" && !moduleSymbolsPage ? <p className="ct-muted">正在读取固定索引定义…</p> : null}
            {knowledge.moduleSymbolsStatus === "loading" && moduleSymbolsPage ? <p className="ct-muted">正在读取下一页定义…</p> : null}
            {knowledge.moduleSymbolsError ? <div className="ct-error-block compact" role="alert"><span>{knowledge.moduleSymbolsError}</span><button type="button" className="ct-link-button" onClick={knowledge.onRetryModuleSymbols}>重试</button></div> : null}
            {moduleDefinitions.length > 0 ? <div className="ct-architecture-definition-list">{moduleDefinitions.map(({ symbol, depth }) => <article className="ct-architecture-definition-row" key={symbol.symbol_id} style={{ marginInlineStart: `${depth * 18}px` }}>
              <button type="button" className="ct-architecture-definition-symbol" onClick={() => knowledge.onSelectModuleSymbol(symbol)}>
                <span className="ct-kind-chip">{symbol.kind}</span><span><strong>{symbol.qualified_name}</strong><small>{symbol.path}:L{symbol.start_line}–L{symbol.end_line}</small></span>
              </button>
              <button type="button" className="ct-link-button" onClick={() => {
                setModuleViewState({ rootId: architectureRoot!.symbol.symbol_id, mode: "calls", callSymbol: symbol });
                knowledge.onOpenSymbolCalls(symbol);
              }}>调用关系</button>
            </article>)}</div> : null}
            {knowledge.moduleSymbolsStatus === "empty" ? <p className="ct-muted">当前模块没有捕获到类、函数或测试定义。</p> : null}
            {moduleSymbolsPage?.has_more ? <button type="button" className="ct-link-button" onClick={knowledge.onLoadMoreModuleSymbols} disabled={knowledge.moduleSymbolsStatus === "loading"}>{knowledge.moduleSymbolsStatus === "loading" ? "正在加载…" : "继续加载定义"}</button> : null}
          </section> : moduleViewMode === "imports" ? <section className="ct-architecture-module-relations" aria-label="静态导入依赖">
            <div className="ct-architecture-drilldown-status" role="status">
              <strong>{graph.codeStatus === "loading" ? "正在读取静态导入依赖" : graph.codeStatus === "error" ? "静态导入依赖读取失败" : graph.codeStatus === "empty" ? "当前查询没有返回静态导入" : `${graph.directImportTargets.length} 个目标文件 · ${graph.codeEdgeCount} 条已返回静态边`}</strong>
              {graph.codeTruncated ? <span>导入查询已截断 · {graph.codeTruncationReason}；未显示的目标文件仍可能存在。</span> : null}
              {graph.codeIndexTruncated ? <span>固定索引关系提取未完成 · {graph.codeIndexTruncationReason}；空结果不能证明没有依赖。</span> : null}
              {graph.codeError ? <span role="alert">{graph.codeError}<button type="button" className="ct-link-button" onClick={() => architectureRoot && knowledge.onOpenModuleImports(architectureRoot)}>重试</button></span> : null}
            </div>
            <p className="ct-muted">静态 imports 以目标文件聚合；箭头表示当前文件 → 目标文件，不表示运行时数据流。</p>
            {graph.directImportTargets.map((target) => <article className="ct-architecture-import-target" key={target.path}>
              <strong><code>{architectureRoot?.symbol.path}</code><span aria-hidden="true">→</span><code>{target.path}</code></strong>
              <details><summary>{target.edges.length} 条原始静态边 · 查看源码依据</summary>{target.edges.map((edge) => <button type="button" className="ct-link-button" key={edge.edge_id} onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: edge.source_location.path, startLine: edge.source_location.start_line, endLine: edge.source_location.end_line })}>{edge.source_location.path}:L{edge.source_location.start_line}–L{edge.source_location.end_line}</button>)}</details>
            </article>)}
            {graph.codeStatus === "empty" ? <p className="ct-muted">未返回静态导入边；索引覆盖和查询预算见上方状态。</p> : null}
          </section> : <section className="ct-architecture-module-relations" aria-label="所选符号静态调用关系">
            <div className="ct-architecture-drilldown-status" role="status">
              <strong>{graph.codeStatus === "loading" ? `正在读取 ${callRoot?.qualified_name ?? "所选符号"} 的静态调用` : graph.codeStatus === "error" ? "静态调用读取失败" : graph.codeStatus === "empty" ? "当前符号没有返回静态调用边" : `${graph.codeSymbolCount} 个符号 · ${graph.codeEdgeCount} 条静态 calls`}</strong>
              {graph.codeTruncated ? <span>调用查询已截断 · {graph.codeTruncationReason}；未显示的调用仍可能存在。</span> : null}
              {graph.codeIndexTruncated ? <span>固定索引关系提取未完成 · {graph.codeIndexTruncationReason}；空结果不能证明没有调用。</span> : null}
              {graph.codeError ? <span role="alert">{graph.codeError}<button type="button" className="ct-link-button" onClick={() => callRoot && knowledge.onOpenSymbolCalls(callRoot)}>重试</button></span> : null}
            </div>
            <p className="ct-muted">以 <code>{callRoot?.qualified_name}</code> 为锚点的一跳静态 calls；静态证据不确认运行时一定执行。</p>
            <div className="ct-graph-legend"><span><i className="is-calls" />静态调用 source → target</span><small>箭头只表示调用方向，不代表数据流。</small></div>
            {graph.codeStatus === "loading" && graph.codeModel.nodes.length === 0 ? <p className="ct-muted">正在读取所选符号的固定索引关系…</p> : null}
            <GraphCanvas model={graph.codeModel} nodeTypes={graph.codeNodeTypes} onInit={graph.onCodeInit} onNodeClick={graph.onCodeNodeClick} focusNodeIds={graph.codeFocusNodeIds} emptyTitle="当前符号没有可显示的静态 calls。" />
            {graph.codeUnresolved.length > 0 ? <details className="ct-architecture-drilldown-unresolved"><summary>未知调用边界 · {graph.codeUnresolved.length}</summary>{graph.codeUnresolved.map((item) => <span key={item.unresolved_id}><code>{item.expression}</code> · {item.reason} · {item.path}:L{item.start_line}</span>)}</details> : null}
            {graph.codeEdges.length > 0 ? <details className="ct-architecture-drilldown-unresolved"><summary>已返回静态边源码依据 · {graph.codeEdges.length}</summary>{graph.codeEdges.map((edge) => <button type="button" className="ct-link-button" key={edge.edge_id} onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: edge.source_location.path, startLine: edge.source_location.start_line, endLine: edge.source_location.end_line })}>{edge.source_location.path}:L{edge.source_location.start_line}–L{edge.source_location.end_line}</button>)}</details> : null}
          </section>}
        </div>
      ) : model.directories.length === 0 ? (
        <div className="ct-architecture-empty"><strong>当前索引没有可绘制的职责或源码目录</strong><span>概览可能未返回源码分组；不以 HTTP 路由组替代源码架构。</span></div>
      ) : (
        <div className="ct-architecture-canvas">
          <ReactFlow<Node, Edge>
            nodes={architectureNodes}
            edges={reactFlowModel.edges}
            nodeTypes={architectureNodeTypes}
            onInit={setFlow}
            onEdgeClick={(_event, edge) => {
              setSelectedEdgeId(edge.id);
              onDetailChange(model.httpRelations.some((item) => item.id === edge.id) ? "http" : null);
            }}
            onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
            onEdgeMouseLeave={() => setHoveredEdgeId(null)}
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
    </section>
    {inspectorVisible ? <aside className="ct-knowledge-inspector" aria-label="架构详情">
      <div className="ct-knowledge-inspector-heading"><strong>{detail === "search" ? "搜索符号" : detail === "http" ? "HTTP 关联" : detail === "coverage" ? "版本与覆盖" : selectedEdgeId ? "依赖详情" : "结构详情"}</strong><button type="button" className="ct-link-button" onClick={() => { onDetailChange(null); setSelectedEdgeId(null); knowledge.onClearOverviewGroup(); }}>关闭详情</button></div>
      <div className="ct-knowledge-inspector-content">
      {detail === "search" ? <SearchBlock knowledge={knowledge} disabled={false} /> : detail === "http" ? <>
      <section className="ct-http-associations" aria-label="HTTP 客户端与服务路由形状关联">
        <div className="ct-http-associations-heading">
          <div><strong>HTTP 方法与路径形状关联</strong><span>{overview.http_association_coverage.path_analysis === "literal_only" ? "旧索引仅检查字面路径。" : "检查静态 requestJson 路径形状与 FastAPI 声明路由。"}不确认运行时网络目标。</span></div>
          <small>{overview.http_association_coverage.visible_match_count}/{overview.http_association_coverage.matched_count} matched · {overview.http_association_coverage.unknown_count} unknown</small>
        </div>
        {overview.http_associations.length === 0 ? (
          <p className="ct-http-associations-empty">{overview.http_association_coverage.analysis_status === "not_analyzed" ? "此索引创建早于 TypeScript HTTP 分析，关联状态未知。" : "当前快照没有可显示的唯一静态 HTTP 形状关联。"}</p>
        ) : (
          <ul className="ct-http-association-list">
            {displayedHttpAssociations.map((association) => (
              <li key={association.association_id}>
                <div className="ct-http-association-shape"><strong>{association.method}</strong><code>{association.path}</code><span>{association.client_path_kind === "parameterized" ? "参数化形状匹配" : "字面量形状匹配"}</span><button type="button" className="ct-link-button" onClick={() => knowledge.onOpenHttpChain(association)}>查看链路</button></div>
                <div className="ct-http-association-endpoint"><small>客户端 · {association.client_name}</small>{association.client_path_template ? <code>{association.client_path_template}</code> : null}<code>{association.client_module.path}</code><button type="button" onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: association.client_evidence.path, startLine: association.client_evidence.start_line, endLine: association.client_evidence.end_line, expectedContentHash: association.client_evidence.content_hash })}>查看客户端证据</button></div>
                <div className="ct-http-association-endpoint"><small>FastAPI 路由</small><code>{association.server_module.path}</code><button type="button" onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: association.route_evidence.path, startLine: association.route_evidence.start_line, endLine: association.route_evidence.end_line, expectedContentHash: association.route_evidence.content_hash })}>查看路由证据</button></div>
              </li>
            ))}
          </ul>
        )}
        <p className="ct-http-associations-coverage">覆盖 {overview.http_association_coverage.client_request_count} 个已识别 requestJson 调用：{overview.http_association_coverage.unmatched_count} unmatched、{overview.http_association_coverage.ambiguous_count} ambiguous、{overview.http_association_coverage.unknown_count} unknown。</p>
      </section>
      </> : detail === "coverage" ? <>
        <p className="ct-inline-note">固定 index <code>{overview.index.index_id}</code> · revision <code>{overview.index.revision}</code> · {overview.index.dirty ? "未提交改动" : "干净工作树"}</p>
      <div className="ct-architecture-responsibility">
        <span><strong>职责来源</strong>{responsibilityStatusLabel} · 同一固定 index</span>
        {overview.responsibility_evidence ? (
          <button type="button" className="ct-link-button" onClick={() => {
            const evidence = overview.responsibility_evidence!;
            knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: evidence.path, startLine: evidence.start_line, endLine: evidence.end_line, expectedContentHash: evidence.content_hash });
          }}>打开 codetrail-subsystems.json</button>
        ) : overview.responsibility_status === "ready" || overview.responsibility_status === "partial" ? (
          <span>manifest 已读取并固定于快照，但 excerpt 超出来源展示上限。</span>
        ) : null}
      </div>
      {!isModuleDrilldown && (coverageWarnings.length > 0 || overview.truncated) ? <details className="ct-architecture-warnings">
        <summary>{overview.coverage.visible_modules === overview.coverage.total_modules && overview.coverage.visible_module_relations === overview.coverage.total_module_relations
          ? `概览条目已返回完整（${overview.coverage.visible_modules}/${overview.coverage.total_modules} 模块）；路径、分类或证据覆盖${overview.truncated || overview.classification_coverage.truncated || overview.http_association_coverage.analysis_status === "incomplete" ? "仍有限" : "请查看详情"} · ${coverageWarnings.length} 项提示`
          : `概览条目有省略 · ${coverageWarnings.length} 项覆盖与诊断提示`}</summary>
        {coverageWarnings.map((warning) => <span key={warning}>{warning}</span>)}
        {overview.truncation_reason ? overview.truncation_reason.split(/;\s*/).map((reason) => {
          const translated = reason.includes("max_paths") ? "概览返回的调用路径达到数量上限" : reason.includes("max_unresolved") ? "概览返回的未知调用详情达到数量上限" : reason.includes("edge samples") ? "聚合依赖仅附少量源码位置；完整细节需查询关系图" : reason.startsWith("relation index:") ? "关系索引覆盖不完整" : reason;
          return <span key={reason}>{translated} · 原因 <code>{reason}</code></span>;
        }) : null}
      </details> : null}
      <p className="ct-architecture-filter-note">类型筛选只影响静态 imports/calls 画布。HTTP method/path shape 关联单独列示，不混入静态调用边，也不证明请求实际发送到本地服务。职责聚焦按当前筛选保留一跳双向邻接；邻接职责初始折叠，聚焦范围外事实计数与服务端截断分开显示。{model.directories.some((directory) => directory.expanded && directory.internalRelationCount > 0) ? " 容器显示组内已返回关系计数；模块间边在悬停或键盘聚焦模块时显示其相邻关系。" : ""}</p>
      {reactFlowModel.foldedEdgeCount > 0 ? <p className="ct-architecture-filter-note" role="status">为保持总览清晰，折叠了 {reactFlowModel.foldedEdgeCount} 条跨越其他卡片的聚合依赖。选中职责或目录后点击聚焦按钮查看相关依赖；模块可打开关系图查看详情。此显示折叠不改变索引。</p> : null}
      <div className="ct-architecture-legend"><span><i className="is-directory" />职责或目录容器（可展开）</span><span><i className="is-architecture-edge" />静态导入 / 静态调用</span><span><i className="is-http-architecture-edge" />HTTP method/path 形状关联</span><span>HTTP 关联单独筛选与聚合，不写入 CodeEdge</span><small>静态箭头只表示 source → target，不代表运行时数据流；依赖只展示当前快照已有关系。职责来自文件 manifest，不是模型推断。HTTP 匹配不等同于运行时拓扑。</small></div>        <OverviewSummary overview={overview} />
        <section className="ct-boundaries"><h3>边界与限制</h3>{overview.notes.map((note) => <p key={note}>{note}</p>)}</section>
      </> : selectedStaticRelation ? <section className="ct-section"><h3>静态依赖</h3><p>{selectedStaticRelation.relationCount} 对模块 · {selectedStaticRelation.edgeCount} 条 {selectedStaticRelation.relationKinds.join(" / ")}</p><p className="ct-muted">依赖属于当前固定快照，不代表运行时数据流。</p>{selectedStaticRelation.representative ? <button type="button" className="ct-link-button" onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, ...selectedStaticRelation.representative! })}>查看源码依据</button> : <p className="ct-muted">当前概览未返回源码位置；可聚焦职责并打开模块关系。</p>}</section> : <OverviewSections overview={overview} knowledge={knowledge} />}
      </div>
    </aside> : null}
    </div>
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
        <span><strong>{overview.coverage.reachable_modules}</strong><small>应用入口可达模块</small></span>
        <span><strong>{overview.coverage.unreachable_modules}</strong><small>未从已识别应用入口到达</small></span>
        <span><strong>{overview.paths.length}</strong><small>返回的入口路径</small></span>
        <span><strong>{overview.unresolved.length}</strong><small>目标未确定的调用</small></span>
      </div>
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
      <p className="ct-section-lede">工作台内置识别使用服务端模型，并按预算发送当前索引的源码证据。Codex 外部 Agent 也可通过 MCP 自行识别并保存结果；它使用宿主模型，服务端不会为此再调用模型。</p>
      {knowledge.modelApiKeyConfigured === false ? <div className="ct-model-config-needed" role="status"><span>还没有配置模型密钥。保存配置不会测试供应商或发送源码。</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onOpenModelSettings()}>打开设置</button></div> : null}
      <div className="ct-history-block">
        <div className="ct-history-heading"><strong>已保存版本 · 当前快照</strong><span data-state={knowledge.featureHistoryStatus}>{knowledge.featureHistoryStatus === "loading" ? "载入中" : `${knowledge.featureHistory.length} 个版本`}</span></div>
        {knowledge.featureHistoryError ? <div className="ct-error-block compact" role="alert"><span>{knowledge.featureHistoryError}</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onRetryFeatureHistory()}>重试</button></div> : null}
        {knowledge.featureHistory.length > 0 ? (
          <div className="ct-history-list">
            {knowledge.featureHistory.map((item) => (
              <button type="button" key={item.generation_id} className={`ct-history-item${item.generation_id === knowledge.selectedFeatureGenerationId ? " is-selected" : ""}`} onClick={() => knowledge.onSelectFeatureGeneration(item.generation_id)} aria-pressed={item.generation_id === knowledge.selectedFeatureGenerationId}>
                <span className="ct-history-top"><strong>{savedFeatureStatusLabel(item.status)}</strong><span>{item.source === "external" ? "外部 Agent" : item.mode === "fixture" ? "演示结果" : "服务端 AI"} · {formatDate(item.created_at)}</span></span>
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
        <label>源码范围<select value={knowledge.featureSourceScope} onChange={(event) => knowledge.onFeatureSourceScopeChange(event.target.value as FeatureSourceScope)} disabled={knowledge.featureStatus === "loading"}><option value="entry_reachable">应用入口可达模块</option><option value="visible_modules">当前可见模块</option></select></label>
        <button type="button" className="ct-button ct-button-primary" onClick={() => void knowledge.onRunFeatureGeneration()} disabled={knowledge.featureStatus === "loading"}>{knowledge.featureStatus === "loading" ? "生成中…" : knowledge.modelApiKeyConfigured === false ? "配置模型后识别" : "识别主要功能"}</button>
        {generation ? <button type="button" className="ct-button ct-button-secondary" onClick={() => void knowledge.onReloadFeatureGeneration()} disabled={knowledge.featureStatus === "loading"}>重读已保存结果</button> : null}
      </div>
      <div className="ct-feature-status"><span data-state={knowledge.featureStatus}>{featureStatusLabel(knowledge.featureStatus, generation)}</span><small>{generation?.source === "external" ? "此版本绑定外部 understanding Run；证据来自该 Run 实际返回的源码读取，服务端模型调用为 0。" : knowledge.featureSourceScope === "entry_reachable" ? "只查询从已识别应用入口可达的模块；未查询模块不会被当作不存在。" : "在当前可见模块内查询，仍受工具、源码、输入和输出预算限制。"}</small></div>
      {knowledge.featureStatus === "loading" ? <LoadingNote>正在收集有界证据并生成语义摘要…</LoadingNote> : null}
      {knowledge.featureError && !generation ? <div className="ct-error-block compact" role="alert"><span>{knowledge.featureError}</span><button type="button" className="ct-link-button" onClick={() => void knowledge.onRunFeatureGeneration()}>重试</button></div> : null}
      {generation ? <FeatureGenerationResult generation={generation} knowledge={knowledge} /> : null}
    </section>
  );
}

function FeatureGenerationResult({ generation, knowledge }: { generation: FeatureGeneration; knowledge: KnowledgeUi }) {
  return (
    <div className="ct-feature-result">
      <div className="ct-feature-meta"><span className={`ct-badge is-${generation.status}`}>{generation.status}</span><span className={`ct-badge is-${generation.mode}`}>{generation.source === "external" ? "外部 Agent" : generation.mode === "fixture" ? "演示结果" : "服务端 AI"}</span><code title={generation.source === "external" ? "宿主自报模型名，不代表服务端模型调用或费用" : generation.model ?? "未配置模型"}>{generation.source === "external" ? generation.model ? `宿主：${generation.model}` : "宿主模型未报告" : generation.model ?? "未配置模型"}</code><small>{generation.source_paths.length} 个源码文件 · {generation.usage.tool_calls} 次工具 · 服务端模型 {generation.usage.model_calls} 次 · {generation.usage.elapsed_ms} ms</small></div>
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
    <div className="ct-module-row-container">
      <button type="button" className="ct-module-row" onClick={() => knowledge.onOpenOverviewModule(module)} title="打开模块内部结构">
        <span className={`ct-reachability${module.entry_reachable ? " is-reachable" : ""}`}>{module.entry_reachable ? "应用入口可达" : "未从已识别应用入口到达"}</span>
        <span>
          <strong>{module.symbol.qualified_name}</strong>
          <small>{module.symbol.path} · {module.entry_point_ids.length > 0 ? `${module.entry_point_ids.length} 个应用入口` : "未从已识别应用入口到达"}</small>
          <ClassificationCounts counts={module.classification_counts} />
        </span>
        <span aria-hidden="true">↗</span>
      </button>
      <button
        type="button"
        className={`ct-module-symbols-action${knowledge.moduleSymbolsRoot?.symbol_id === module.symbol.symbol_id ? " is-selected" : ""}`}
        onClick={() => knowledge.onOpenModuleSymbols(module)}
        aria-pressed={knowledge.moduleSymbolsRoot?.symbol_id === module.symbol.symbol_id}
      >
        定义
      </button>
    </div>
  );
}

function OverviewClassificationList({
  classifications,
  entryById,
  overview,
  counts,
  knowledge,
}: {
  classifications: OverviewClassification[];
  entryById: Map<string, OverviewEntryPoint>;
  overview: ProjectOverview;
  counts: OverviewClassificationCounts;
  knowledge: KnowledgeUi;
}) {
  const visible = classifications.slice(0, 12);
  const evidenceFor = (classification: OverviewClassification) => classification.category === "application_entry"
    ? (entryById.get(classification.entry_point_id ?? "")?.evidence ? [entryById.get(classification.entry_point_id ?? "")!.evidence!] : [])
    : classification.evidence;
  const evidenceErrorFor = (classification: OverviewClassification) => classification.category === "application_entry"
    ? entryById.get(classification.entry_point_id ?? "")?.evidence_error ?? classification.evidence_error
    : classification.evidence_error;
  return (
    <section className="ct-structure-classifications" aria-label="当前规则分类">
      <div className="ct-structure-subheading"><strong>当前规则分类</strong><span>{visible.length}/{classifications.length} 条已返回分类</span></div>
      <div className="ct-classification-summary"><ClassificationCounts counts={counts} /></div>
      <p className="ct-muted">分类只表示当前规则已识别的源码声明；“未识别”表示本次规则没有归类。</p>
      {overview.classification_coverage.truncated ? <p className="ct-warning">分类记录已返回，但部分角色尚未识别或分析受限： {overview.classification_coverage.visible}/{overview.classification_coverage.total} 条，当前结构明细可能不完整。</p> : null}
      {classifications.length > 0 ? (
        <div className="ct-classification-list">
          {visible.map((classification) => {
            const evidenceItems = evidenceFor(classification);
            const evidenceError = evidenceErrorFor(classification);
            return (
              <article className="ct-classification-item" key={classification.classification_id}>
                <div className="ct-classification-item-heading">
                  <span className="ct-kind-chip">{classificationLabels[classification.category]}</span>
                  <strong>{classification.label}</strong>
                </div>
                <small>{classification.symbol_name} · {classification.rule_id}</small>
                <div className="ct-evidence-links">
                  {evidenceItems.map((evidence, index) => (
                    <button type="button" key={`${evidence.evidence_id}:${index}`} onClick={() => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, path: evidence.path, startLine: evidence.start_line, endLine: evidence.end_line, expectedContentHash: evidence.content_hash })}>
                      {evidence.path}:L{evidence.start_line}–L{evidence.end_line}
                    </button>
                  ))}
                  {evidenceItems.length === 0 ? <span>{evidenceError ?? "当前快照没有可展示的源码证据"}</span> : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="ct-muted">{overview.classification_coverage.truncated ? "当前规则分类覆盖不完整，未返回明细不能据此判断没有分类。" : "当前规则未识别可展示分类。"}</p>
      )}
      {classifications.length > visible.length ? <p className="ct-muted">仅显示前 {visible.length} 项；当前结构收到 {classifications.length} 条分类。</p> : null}
    </section>
  );
}

function ModuleSymbolList({ knowledge }: { knowledge: KnowledgeUi }) {
  const root = knowledge.moduleSymbolsRoot;
  if (!root) return null;
  const page = knowledge.moduleSymbolsPage?.module.symbol_id === root.symbol_id ? knowledge.moduleSymbolsPage : null;
  return (
    <section className="ct-module-symbols" aria-label="模块定义列表">
      <div className="ct-structure-subheading">
        <strong>{root.qualified_name} · 索引定义</strong>
        {page ? <span>{page.symbols.length} 项已加载</span> : null}
      </div>
      <p className="ct-muted">仅列出固定快照中捕获的类、函数和测试定义；不代表解析覆盖完整。</p>
      {knowledge.moduleSymbolsStatus === "loading" && !page ? <p className="ct-muted">正在读取固定索引…</p> : null}
      {knowledge.moduleSymbolsError ? <div className="ct-error-block compact" role="alert"><span>{knowledge.moduleSymbolsError}</span><button type="button" className="ct-link-button" onClick={knowledge.onRetryModuleSymbols}>重试</button></div> : null}
      {page && page.symbols.length > 0 ? (
        <div className="ct-module-symbol-list">
          {page.symbols.map((symbol) => (
            <button type="button" className="ct-module-symbol-row" key={symbol.symbol_id} onClick={() => knowledge.onSelectModuleSymbol(symbol)}>
              <span className="ct-kind-chip">{symbol.kind}</span>
              <span><strong>{symbol.qualified_name}</strong><small>{symbol.path}:L{symbol.start_line}–L{symbol.end_line}</small></span>
              <span aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      ) : null}
      {knowledge.moduleSymbolsStatus === "empty" ? <p className="ct-muted">当前模块没有捕获到类、函数或测试定义。</p> : null}
      {page?.has_more ? <button type="button" className="ct-link-button" onClick={knowledge.onLoadMoreModuleSymbols} disabled={knowledge.moduleSymbolsStatus === "loading"}>{knowledge.moduleSymbolsStatus === "loading" ? "正在加载…" : "继续加载定义"}</button> : null}
    </section>
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
  const selectedClassifications = selectedGroup
    ? overview.classifications.filter((classification) => selectedGroup.module_ids.includes(classification.module_id) || (classification.entry_point_id !== null && selectedGroup.entry_point_ids.includes(classification.entry_point_id)))
    : [];
  const selectedClassificationCounts = selectedModules.reduce<OverviewClassificationCounts>((counts, module) => ({
    application_entry: counts.application_entry + module.classification_counts.application_entry,
    public_api: counts.public_api + module.classification_counts.public_api,
    extension_point: counts.extension_point + module.classification_counts.extension_point,
    unknown: counts.unknown + module.classification_counts.unknown,
  }), { application_entry: 0, public_api: 0, extension_point: 0, unknown: 0 });
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
              selectedModules.length > 0 ? <div className="ct-module-list">{selectedModules.map((module) => <OverviewModuleRow module={module} knowledge={knowledge} key={module.module_id} />)}</div> : <p className="ct-muted">{selectedGroup.group_kind === "responsibility" && selectedGroup.module_ids.length === 0 ? "该职责的 owner 路径未匹配到已索引模块。" : selectedGroup.module_ids.length > 0 ? `该组 ${selectedGroup.module_ids.length} 个模块被当前概览模块预算省略。` : "该源码目录没有可见模块。"}</p>
            )}
            <OverviewClassificationList classifications={selectedClassifications} entryById={entryById} overview={overview} counts={selectedClassificationCounts} knowledge={knowledge} />
            {selectedGroup.group_kind !== "http_path" ? <ModuleSymbolList knowledge={knowledge} /> : null}
            <div className="ct-structure-related">
              <div className="ct-structure-subheading"><strong>相关模块依赖</strong><span>{relatedRelations.length} 条可见关系</span></div>
              <OverviewModuleRelationList relations={relatedRelations.slice(0, 12)} onOpenSource={(location) => knowledge.onOpenArchitectureSource({ indexId: overview.index.index_id, ...location })} />
              {relatedRelations.length > 12 ? <p className="ct-muted">仅显示 12 条关系；可见关系仍受模块关系预算限制。</p> : null}
            </div>
          </div>
        ) : null}
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
  return run.mode === "real" ? "AI 分析" : "演示结果";
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
      {session.modelApiKeyConfigured === false ? <div className="ct-model-config-needed" role="status"><span>尚未配置模型密钥。先完成配置，再开始分析。</span><button type="button" className="ct-link-button" onClick={() => void session.onOpenModelSettings()}>打开设置</button></div> : null}
      {draftPending ? <p className="ct-run-draft-warning" role="status">当前有未保存草稿：启动 Run 不会包含它，请先保存消息。</p> : null}
      <div className="ct-run-start-row">
        <button type="button" className="ct-button ct-button-primary" onClick={() => void session.onStartRun()} disabled={active || session.runWriteStatus === "loading" || session.messagesStatus === "loading"}>{session.runWriteStatus === "loading" ? "处理中…" : session.modelApiKeyConfigured === false ? "配置模型后分析" : "开始分析"}</button>
      </div>
      <p className="ct-run-real-warning" role="note">配置后显式启动才会把本次 Issue、已保存消息和有界源码证据发送到模型服务，可能产生调用费用。</p>
      {session.runWriteError ? <p className="ct-inline-error" role="alert">{session.runWriteError}</p> : null}
      {session.runsError ? <div className="ct-error-block compact" role="alert"><span>{session.runsError}</span></div> : null}
      {currentRun ? <div className="ct-run-detail">
        <div className="ct-run-detail-heading"><div><strong>{runStatusLabel(currentRun.status)}</strong><span data-state={session.runStatus}>{session.runStatus === "loading" ? "状态同步中" : session.runStatus === "error" ? "状态请求失败" : "已载入"}</span><span data-state={session.runStreamStatus}>事件：{runStreamStatusLabel(session.runStreamStatus)}</span></div>{active ? <button type="button" className="ct-button ct-button-danger" onClick={() => void session.onCancelRun()} disabled={session.runWriteStatus === "loading"}>取消 Run</button> : null}</div>
        {session.runStatusResponse ? <>
          <div className="ct-run-snapshot"><span><small>固定消息</small><code>1–{session.runStatusResponse.input.message_end_sequence}</code></span><span><small>INPUT HASH</small><code title={session.runStatusResponse.input.input_hash}>{shortHash(session.runStatusResponse.input.input_hash, 12)}</code></span><span><small>INDEX</small><code title={session.runStatusResponse.run.index_id}>{shortHash(session.runStatusResponse.run.index_id, 16)}</code></span><span><small>来源</small><code>{runModeLabel(session.runStatusResponse.run)}</code></span><span><small>版本</small><code>{session.runStatusResponse.run.execution_version}</code></span></div>
          {session.runError ? <p className="ct-inline-error" role="alert">{session.runError}</p> : null}
          {session.runStreamError ? <p className="ct-inline-note" role="status">{session.runStreamError}</p> : null}
          {session.runReport ? <div className="ct-run-report"><div className="ct-run-subheading"><strong>{session.runReport.outcome === "candidate_found" ? "候选结论" : session.runReport.outcome === "insufficient_evidence" ? "证据不足" : session.runReport.outcome === "explanation" ? "流程解释" : "执行结果"}</strong><span>{session.runStatusResponse.run.execution_source === "codex" ? "CODEX / EXTERNAL" : session.runReport.fixture_mode ? "演示结果" : "AI 分析"}</span></div><p>{session.runReport.summary}</p>{session.runReport.limitations.length > 0 ? <div className="ct-run-evidence-list"><small>限制</small>{session.runReport.limitations.map((limitation) => <p key={limitation}>{limitation}</p>)}</div> : null}{session.runReport.candidate_links.length > 0 ? <div className="ct-run-impact-links"><small>候选位置与有界影响</small>{session.runReport.candidate_links.map((candidate) => <div className="ct-run-impact-link" key={candidate.candidate_id}><span><code>{candidate.source_symbol_id}</code><small>{candidate.relation}</small></span><button type="button" className="ct-link-button" onClick={() => session.onSelectRunImpact({ candidateId: candidate.candidate_id })}>查看影响范围</button></div>)}</div> : null}{session.runReport.findings.map((finding) => <div className="ct-run-finding" key={finding.finding_id}><strong>{finding.kind}</strong><span>{finding.text}</span>{finding.symbol_ids.length === 1 ? <button type="button" className="ct-link-button" onClick={() => session.onSelectRunImpact({ findingId: finding.finding_id })}>查看潜在影响</button> : <small className="ct-run-impact-unmapped">无唯一可映射符号 · 仅显示证据</small>}{finding.evidence_ids.map((evidenceId) => { const evidence = session.runReport?.evidence.find((item) => item.evidence_id === evidenceId); return evidence ? <button type="button" className="ct-link-button" key={evidenceId} onClick={() => session.onSelectRunEvidence(evidence)}>查看证据 · {evidence.path}:L{evidence.start_line}</button> : null; })}</div>)}{session.runReport.evidence.length > 0 ? <div className="ct-run-evidence-list"><small>本次报告证据</small>{session.runReport.evidence.map((evidence) => <button type="button" className="ct-link-button" key={evidence.evidence_id} onClick={() => session.onSelectRunEvidence(evidence)}>{evidence.path}:L{evidence.start_line}–L{evidence.end_line}</button>)}</div> : null}</div> : null}
          {session.runReport?.explanation ? <RunExplanation explanation={session.runReport.explanation} evidence={session.runReport.evidence} onSelectEvidence={session.onSelectRunEvidence} onSelectSymbol={session.onSelectRunSymbol} /> : null}
        </> : <LoadingNote>正在读取 Run 详情…</LoadingNote>}
      </div> : null}
      {session.runStatusResponse && (session.runEvents.length > 0 || session.runToolResults.length > 0) ? <details className="ct-run-activity-details"><summary>执行历史 · {session.runEvents.length} 个事件 · {session.runToolResults.length} 个工具结果</summary>{session.runEvents.length > 0 ? <div className="ct-run-events"><div className="ct-run-subheading"><strong>公开执行事件</strong><span>{session.runEvents.length} 条</span></div>{session.runEvents.map((event) => <div className="ct-run-event" key={`${event.run_id}-${event.sequence}`}><code>#{event.sequence}</code><span>{runEventLabel(event)}</span></div>)}</div> : null}{session.runToolResults.length > 0 ? <div className="ct-run-tools"><div className="ct-run-subheading"><strong>工具结果</strong><span>{session.runToolResults.length} 项</span></div>{session.runToolResults.map((result) => <div className="ct-run-tool" key={result.request_id}><code>{result.tool}</code><span>{result.kind.endsWith("failed") ? "失败" : "已返回"} · #{result.completed_sequence}</span></div>)}</div> : null}</details> : null}
      {session.runs.length > 0 ? <details className="ct-run-history-details"><summary>Run 历史 · {session.runs.length} 次</summary><div className="ct-run-history" aria-label="Run 历史">{session.runs.map((run) => <button type="button" key={run.run_id} className={`ct-run-history-item${run.run_id === session.selectedRunId ? " is-selected" : ""}`} onClick={() => session.onSelectRun(run.run_id)}><span><strong>{runStatusLabel(run.status)}</strong><small>{run.fixture_scenario ? `演示结果 · ${runScenarioLabel(run.fixture_scenario)}` : runModeLabel(run)}</small></span><code>{run.run_id}</code><small>消息 1–{run.message_end_sequence} · {run.execution_version}</small></button>)}</div></details> : session.runsStatus === "loading" ? <LoadingNote>正在读取 Run 历史…</LoadingNote> : <div className="ct-run-empty">还没有 Run。点击开始分析可创建一次调查；外部 Codex Run 会自动出现。</div>}
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

function EvidencePanel({ evidence, returnPanel, onReturnToPanel }: { evidence: EvidenceUi; returnPanel: "graph" | "feature_graph" | "impact" | null; onReturnToPanel: () => void }) {
  const selected = evidence.selectedSymbol;
  const sourceResponse = evidence.sourceResponse;
  const canExpandRelation = selected !== null && selected.kind !== "file" && !selected.symbol_id.startsWith("evidence-");
  return (
    <aside className="ct-side-panel ct-evidence-panel" aria-label="源码证据面板">
      <div className="ct-side-panel-header"><div><p className="ct-overline">EVIDENCE</p><h2>源码证据</h2></div><div>{returnPanel ? <button type="button" className="ct-link-button" onClick={onReturnToPanel}>返回{ returnPanel === "feature_graph" ? "功能图" : returnPanel === "impact" ? "影响范围" : "代码关系"}</button> : null}<button type="button" className="ct-close-button" onClick={evidence.onClose}>关闭</button></div></div>
      <div className="ct-panel-switch"><button type="button" className="is-active">源码证据</button><button type="button" onClick={() => void evidence.onExpandRelation()} disabled={!canExpandRelation}>代码关系</button>{evidence.hasHttpChain ? <button type="button" onClick={evidence.onReturnHttpChain}>HTTP 链路</button> : null}</div>
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
      <div className="ct-side-panel-header"><div><p className="ct-overline">{isFeature ? "FEATURE TOPOLOGY" : isImpact ? "RUN IMPACT" : "STATIC TOPOLOGY"}</p><h2>{isFeature ? "功能图" : isImpact ? "候选影响" : "代码关系"}</h2></div><button type="button" className="ct-close-button" onClick={graph.httpChain ? graph.onExitHttpChain : graph.onClose}>关闭</button></div>
      <div className="ct-panel-switch"><button type="button" className={!isFeature && !isImpact ? "is-active" : ""} onClick={() => graph.onPanelModeChange("graph")}>代码关系</button><button type="button" className={isFeature ? "is-active" : ""} onClick={() => graph.onPanelModeChange("feature_graph")} disabled={!graph.featureGeneration}>功能图</button><button type="button" className={isImpact ? "is-active" : ""} onClick={() => graph.onPanelModeChange("impact")} disabled={graph.impactStatus === "idle"}>影响范围</button><button type="button" onClick={() => graph.onPanelModeChange("source")} disabled={!graph.selectedId}>源码证据</button></div>
      {!isFeature && !isImpact ? <>
        {graph.httpChain ? <div className="ct-http-chain-context">
          <div className="ct-http-chain-steps" aria-label="HTTP 入口到静态调用链">
            <div className="ct-http-chain-step"><small>客户端锚点</small><strong>{graph.httpChain.association.client_name}</strong><code>{graph.httpChain.association.client_module.path}</code><button type="button" onClick={() => graph.onSelectHttpChainEvidence(graph.httpChain!.association.client_evidence)}>查看客户端证据</button></div>
            <span className="ct-http-chain-arrow" aria-hidden="true">→</span>
            <div className="ct-http-chain-step is-shape"><small>HTTP 形状关联</small><strong>{graph.httpChain.association.method}</strong><code>{graph.httpChain.association.path}</code><span>method/path shape · 非 CodeEdge</span></div>
            <span className="ct-http-chain-arrow" aria-hidden="true">→</span>
            <div className="ct-http-chain-step"><small>FastAPI handler</small><strong title={graph.httpChain.handler.qualified_name}>{graph.httpChain.handler.qualified_name}</strong><code>{graph.httpChain.handler.path}:L{graph.httpChain.handler.start_line}–L{graph.httpChain.handler.end_line}</code><button type="button" onClick={() => graph.onSelectHttpChainEvidence(graph.httpChain!.association.route_evidence)}>查看路由证据</button></div>
          </div>
          <p>客户端函数只作入口锚点；下图从该 handler 查询同一 index 的静态 calls，最多向下 2 层、40 个符号。深度之外、未解析或未提取的关系不会补猜。</p>
          <button type="button" className="ct-link-button" onClick={graph.onExitHttpChain}>退出链路</button>
        </div> : null}
        {graph.httpChain || graph.contextualCalls.length > 0 ? <section className="ct-contextual-calls" aria-label="指定构造上下文调用">
          <div className="ct-section-heading"><div><p className="ct-overline">CONTEXTUAL CALLS</p><h3>指定构造上下文</h3></div><span className="ct-count-label">{graph.contextualCalls.length}</span></div>
          <p>这些记录只表示指定构造上下文下的静态语法关系，不保证运行时一定执行。它们不计入普通 calls 图；未解析调用仍保留在未知边界中。</p>
          {graph.codeTruncated ? <small className="ct-contextual-budget">邻接查询已截断，条件记录可能不完整 · {graph.codeTruncationReason ?? "未提供原因"}</small> : null}
          {graph.contextualCalls.length === 0 ? <small>当前查询没有返回可展示的证明记录；这不表示运行时不存在其他路径。</small> : graph.contextualCalls.map((call) => <article className="ct-contextual-call" key={call.context_id}>
            <strong>{call.activation_source.qualified_name} → {call.source.qualified_name} → {call.target.qualified_name}</strong>
            <code>字段 self.{call.field_name}</code>
            {call.binding_mutation_after_escape_unverified ? <small className="ct-contextual-escape-warning">初始构造绑定，逃逸后是否改写未知；目标和调用仍未确认。</small> : null}
            <div className="ct-contextual-evidence">
              {([
                ["调用源码", call.source_location],
                ["触发调用", call.activation_location],
                ["接收方构造器", call.receiver_constructor_location],
                ["字段赋值", call.field_assignment_location],
              ] as const).map(([label, location]) => <button type="button" key={label} onClick={() => graph.onSelectContextualCallEvidence(call, location)}>
                {label} · {location.path}:L{location.start_line}{location.end_line > location.start_line ? `–L${location.end_line}` : ""}
              </button>)}
            </div>
          </article>)}
        </section> : null}
        {graph.httpChain ? <div className="ct-graph-toolbar ct-http-chain-toolbar"><span>向下 · calls only · 深度 2 · 最多 40 个节点</span><button type="button" className="ct-link-button" onClick={() => graph.flowInstance?.fitView({ padding: 0.18, duration: 240 })} disabled={graph.codeModel.nodes.length === 0}>聚焦</button></div> : <div className="ct-graph-toolbar"><label>方向<select value={graph.direction} onChange={(event) => graph.onChangeDirection(event.target.value as NeighborDirection)} disabled={!graph.selectedId}><option value="both">双向</option><option value="callers">调用方</option><option value="callees">被调用方</option></select></label><label className="ct-check-field"><input type="checkbox" checked={graph.edgeKinds.includes("calls")} onChange={() => graph.onToggleEdgeKind("calls")} disabled={!graph.selectedId} /><span>calls</span></label><label className="ct-check-field"><input type="checkbox" checked={graph.edgeKinds.includes("imports")} onChange={() => graph.onToggleEdgeKind("imports")} disabled={!graph.selectedId} /><span>imports</span></label><button type="button" className="ct-link-button" onClick={() => { if (graph.flowInstance) { void graph.flowInstance.fitBounds(graph.flowInstance.getNodesBounds(codeFocusNodeIds), { padding: 0.18, duration: 240 }); } }} disabled={graph.codeModel.nodes.length === 0}>聚焦</button></div>}
        <div className="ct-panel-status"><span data-state={graph.codeStatus}>{graph.codeStatus === "loading" ? graph.httpChain ? "载入 handler 下游静态 calls" : "载入关系中" : graph.codeStatus === "error" ? "关系请求失败" : graph.codeStatus === "empty" ? graph.httpChain ? "当前有界查询没有返回静态 calls" : graph.codeIndexTruncated ? "当前结果为空（索引关系未完整分析）" : graph.codeIndexUnresolvedTruncated === true ? "当前结果为空（unresolved 详情不完整）" : "当前方向没有静态边" : graph.codeStatus === "success" ? `${graph.codeSymbolCount} 个节点 · ${graph.codeEdgeCount} 条边` : "选择搜索命中后载入关系"}</span>{graph.codeTruncated ? <small>查询已截断 · {graph.codeTruncationReason}</small> : null}</div>
        {graph.codeIndexTruncated ? <p className="ct-graph-coverage-warning" role="status">当前固定索引的关系提取未完成 · {graph.codeIndexTruncationReason}；空邻接不能证明没有关系。</p> : null}
        {graph.codeIndexUnresolvedTruncated === true ? <p className="ct-graph-coverage-warning" role="status">当前固定索引的 unresolved 详情已截断 · {graph.codeIndexUnresolvedTruncationReason}；未返回的 unresolved 不能被当作不存在。</p> : null}
        {graph.codeSymbolCount > 0 && graph.codeIndexUnresolvedTruncated === null ? <p className="ct-graph-coverage-warning" role="status">当前旧索引未记录 unresolved 覆盖状态；不把缺失的 unresolved 当作不存在。</p> : null}
        {graph.codeError ? <p className="ct-inline-error" role="alert">{graph.codeError}{graph.httpChain ? <button type="button" className="ct-link-button" onClick={graph.onRetryHttpChain}>重试</button> : null}</p> : null}
        {graph.codeStatus === "empty" && graph.httpChain ? <p className="ct-graph-coverage-warning" role="status">空结果不能证明 handler 没有服务或存储调用；本查询只覆盖已提取的同快照静态 calls 和前 2 层。</p> : null}
        {graph.httpChain && graph.codeHasCycle ? <p className="ct-graph-coverage-warning" role="status">当前返回的 calls 中检测到循环边；相同符号只展开一次，循环边保留显示。</p> : null}
        {graph.httpChain && graph.codeUnresolved.length > 0 ? <div className="ct-http-chain-unresolved"><strong>未解析调用 · {graph.codeUnresolved.length}</strong>{graph.codeUnresolved.map((item) => <span key={item.unresolved_id}><code>{item.expression}</code> · {item.reason} · {item.path}:L{item.start_line}</span>)}</div> : null}
        <div className="ct-graph-legend"><span><i className="is-calls" />calls</span>{graph.httpChain ? <small>handler 下游局部静态子图 · 每个符号至多展开一次</small> : <><span><i className="is-imports" />imports</span><small>局部静态子图 · 视口聚焦 {graph.codeFocusNodeIds.length > 0 ? `${codeFocusNodeIds.length} 个节点` : "全部节点"}</small></>}</div>
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
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const visibleEdges = useMemo(() => model.edges.map((edge) => ({
    ...edge,
    label: edge.id === hoveredEdgeId || edge.id === selectedEdgeId ? edge.label : undefined,
  })), [hoveredEdgeId, model.edges, selectedEdgeId]);
  if (model.nodes.length === 0) {
    return <div className="ct-graph-empty"><span className="ct-empty-index" aria-hidden="true">∅</span><strong>{emptyTitle}</strong><p>图中只表示当前已加载的受限关系，不代表全仓架构。</p></div>;
  }
  return (
    <div className="ct-graph-canvas">
      <ReactFlow<Node, Edge>
        nodes={model.nodes}
        edges={visibleEdges}
        nodeTypes={nodeTypes}
        onInit={onInit}
        onNodeClick={(_event, node) => onNodeClick(node.id)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        onEdgeClick={(_event, edge) => setSelectedEdgeId(edge.id)}
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

function SettingsDialog({ modelSettings }: { modelSettings: WorkbenchUiProps["modelSettings"] }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [provider, setProvider] = useState("deepseek");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com");
  const [model, setModel] = useState("deepseek-v4-flash");
  const [apiKey, setApiKey] = useState("");
  const [removeApiKey, setRemoveApiKey] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (modelSettings.open && !dialog.open) dialog.showModal();
    if (!modelSettings.open && dialog.open) dialog.close();
    if (!modelSettings.open) {
      setApiKey("");
      setRemoveApiKey(false);
    }
  }, [modelSettings.open]);

  useEffect(() => {
    if (!modelSettings.settings) return;
    setProvider(modelSettings.settings.provider);
    setBaseUrl(modelSettings.settings.base_url);
    setModel(modelSettings.settings.model);
  }, [modelSettings.settings]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const saved = await modelSettings.onSave({
      schema_version: SCHEMA_VERSION,
      provider,
      base_url: baseUrl,
      model,
      api_key: apiKey,
      remove_api_key: removeApiKey,
    });
    if (saved) {
      setApiKey("");
      setRemoveApiKey(false);
    }
  };
  const loading = modelSettings.status === "loading" || !modelSettings.settings;
  const disabled = loading || modelSettings.saving;

  return (
    <dialog
      ref={dialogRef}
      className="ct-settings-dialog"
      aria-labelledby="settings-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!modelSettings.saving) modelSettings.onClose();
      }}
      onClick={(event) => {
        if (event.target === dialogRef.current && !modelSettings.saving) modelSettings.onClose();
      }}
    >
      <div className="ct-form-heading">
        <div><p className="ct-overline">GLOBAL SETTINGS</p><h2 id="settings-dialog-title">设置</h2></div>
        <button type="button" className="ct-close-button" onClick={() => modelSettings.onClose()} disabled={modelSettings.saving}>关闭</button>
      </div>
      <section className="ct-settings-section" aria-labelledby="model-service-heading">
        <h3 id="model-service-heading">模型服务</h3>
        <p className="ct-form-help">适用于所有项目和会话中的内置 AI 分析，配置保存在当前连接的 CodeTrail 服务。Codex 外部 Agent 使用宿主模型，无需在这里配置。</p>
        <p className="ct-form-help">保存不会连接供应商或发送源码；后续新任务读取新配置，运行中的任务继续使用启动时配置。</p>
        {modelSettings.settings ? <p className="ct-model-key-status" role="status">已保存密钥：{modelSettings.settings.has_api_key ? "已配置" : "未配置"}</p> : null}
        {modelSettings.error ? <div className="ct-error-block compact" role="alert"><span>{modelSettings.error}</span><button type="button" className="ct-link-button" onClick={() => void modelSettings.onRetry()}>重试读取</button></div> : null}
        {modelSettings.saved ? <p className="ct-model-config-saved" role="status">配置已保存；网络连通性尚未验证。</p> : null}
        <form className="ct-model-settings-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="model-config-provider">供应商标识</label>
          <input id="model-config-provider" value={provider} onChange={(event) => setProvider(event.target.value)} maxLength={100} required disabled={disabled} />
          <label htmlFor="model-config-base-url">兼容 API 地址</label>
          <input id="model-config-base-url" type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} maxLength={2_000} required disabled={disabled} />
          <label htmlFor="model-config-model">模型名称</label>
          <input id="model-config-model" value={model} onChange={(event) => setModel(event.target.value)} maxLength={200} required disabled={disabled} />
          <label htmlFor="model-config-api-key">API Key</label>
          <input
            id="model-config-api-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => {
              setApiKey(event.target.value);
              setRemoveApiKey(false);
            }}
            maxLength={500}
            placeholder={modelSettings.settings?.has_api_key ? "留空以保留已保存的密钥" : "输入服务端模型密钥"}
            disabled={disabled || removeApiKey}
          />
          {modelSettings.settings?.has_api_key ? <label className="ct-model-remove-key"><input type="checkbox" checked={removeApiKey} onChange={(event) => { setRemoveApiKey(event.target.checked); setApiKey(""); }} disabled={disabled} />明确移除已保存的密钥</label> : null}
          <p className="ct-form-help">密钥不会返回给浏览器或写入浏览器存储。新密钥只在本次表单提交时发送；保存成功后输入会清空。</p>
          <div className="ct-form-actions">
            <button type="submit" className="ct-button ct-button-primary" disabled={disabled}>{modelSettings.saving ? "保存中…" : "保存配置"}</button>
          </div>
        </form>
      </section>
    </dialog>
  );
}

export default function WorkbenchUi(props: WorkbenchUiProps) {
  const appRef = useRef<HTMLElement>(null);
  const workspaceFrameRef = useRef<HTMLDivElement>(null);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [operationPanel, setOperationPanel] = useState<OperationPanelState | null>(null);
  const [panelWidth, setPanelWidth] = useState(360);
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
  const [sidebarRange, setSidebarRange] = useState<SidebarWidthRange>({ min: 220, max: 480 });
  const sidebarRangeRef = useRef(sidebarRange);
  const sidebarDragRef = useRef<{ pointerId: number; startX: number; startWidth: number; width: number } | null>(null);
  const [returnPanel, setReturnPanel] = useState<GraphPanelMode | null>(null);
  const graphScopeKey = `${props.project.selectedProject?.project_id ?? ""}:${props.index.response?.index.index_id ?? ""}`;
  const previousGraphScopeKey = useRef(graphScopeKey);
  const previousPanelMode = useRef(props.panelMode);
  const inlineArchitectureGraph = props.viewMode === "overview" && Boolean(props.knowledge.architectureGraphRootId);
  const showSideGraphPanel = (props.panelMode === "graph" || props.panelMode === "feature_graph" || props.panelMode === "impact") && !inlineArchitectureGraph;
  useEffect(() => {
    const sameScope = previousGraphScopeKey.current === graphScopeKey;
    const returnTarget = resolveGraphReturnPanel(props.panelMode, previousPanelMode.current, sameScope);
    if (returnTarget) {
      setReturnPanel(returnTarget);
    } else if (!sameScope || props.panelMode !== "source") {
      setReturnPanel(null);
    }
    previousGraphScopeKey.current = graphScopeKey;
    previousPanelMode.current = props.panelMode;
  }, [graphScopeKey, props.panelMode]);
  const effectiveSidebarWidth = clampSidebarWidth(sidebarWidth, sidebarRange);
  sidebarRangeRef.current = sidebarRange;
  useEffect(() => {
    try {
      const savedWidth = readSidebarWidth(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY));
      if (savedWidth !== null) setSidebarWidth(savedWidth);
    } catch {
      // Storage can be unavailable in restricted browser contexts.
    }
  }, []);
  useEffect(() => {
    const frame = workspaceFrameRef.current;
    if (!frame) return;
    const updateRange = () => {
      const root = appRef.current;
      const hasFixedSidePanel = root?.classList.contains("has-side-panel") && !root.classList.contains("has-main-graph-panel");
      const sidePanel = hasFixedSidePanel ? frame.lastElementChild as HTMLElement | null : null;
      const reservedContentWidth = hasFixedSidePanel
        ? 360 + (sidePanel?.getBoundingClientRect().width || panelWidth)
        : 480;
      const next = resolveSidebarWidthRange(frame.clientWidth, reservedContentWidth);
      sidebarRangeRef.current = next;
      setSidebarRange((current) => current.min === next.min && current.max === next.max ? current : next);
    };
    updateRange();
    const observer = new ResizeObserver(updateRange);
    observer.observe(frame);
    const root = appRef.current;
    if (root?.classList.contains("has-side-panel") && !root.classList.contains("has-main-graph-panel") && frame.lastElementChild) {
      observer.observe(frame.lastElementChild);
    }
    return () => observer.disconnect();
  }, [panelWidth, props.panelMode, showSideGraphPanel]);
  const saveSidebarWidth = (width: number) => {
    const preferredWidth = normalizeSidebarWidth(width);
    setSidebarWidth(preferredWidth);
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(preferredWidth));
    } catch {
      // Resizing remains available when storage is unavailable.
    }
  };
  const finishSidebarResize = () => {
    const drag = sidebarDragRef.current;
    if (!drag) return;
    sidebarDragRef.current = null;
    appRef.current?.classList.remove("is-resizing-sidebar");
    saveSidebarWidth(drag.width);
  };
  const onSidebarKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const range = sidebarRangeRef.current;
    if (range.max <= range.min) return;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = effectiveSidebarWidth - 16;
    else if (event.key === "ArrowRight") next = effectiveSidebarWidth + 16;
    else if (event.key === "Home") next = range.min;
    else if (event.key === "End") next = range.max;
    if (next === null) return;
    event.preventDefault();
    saveSidebarWidth(clampSidebarWidth(next, range));
  };
  const onSidebarPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !event.isPrimary || sidebarRangeRef.current.max <= sidebarRangeRef.current.min) return;
    event.preventDefault();
    event.stopPropagation();
    sidebarDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: effectiveSidebarWidth,
      width: effectiveSidebarWidth,
    };
    appRef.current?.classList.add("is-resizing-sidebar");
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onSidebarPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = sidebarDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    const width = clampSidebarWidth(drag.startWidth + event.clientX - drag.startX, sidebarRangeRef.current);
    drag.width = width;
    appRef.current?.style.setProperty("--ct-sidebar-width", `${width}px`);
    event.currentTarget.setAttribute("aria-valuenow", String(width));
    event.currentTarget.setAttribute("aria-valuetext", `${width} 像素`);
  };
  const closeOperation = () => {
    props.onImportFlowLeave();
    setOperationPanel(null);
  };
  const openOperation = (mode: OperationPanel, tab: OperationTab = "source") => {
    props.onImportFlowLeave();
    setOperationPanel({ mode, tab });
    setMobileSidebarOpen(false);
  };
  const openSettingsFromSidebar = () => {
    void props.modelSettings.onOpen();
    setMobileSidebarOpen(false);
  };
  const selectProject = (projectId: string) => {
    props.onImportFlowLeave();
    props.knowledge.onCloseArchitectureGraph();
    props.project.onSelectProject(projectId);
    setMobileSidebarOpen(false);
    setOperationPanel(null);
  };
  const selectSession = (sessionId: string) => {
    props.onImportFlowLeave();
    props.knowledge.onCloseArchitectureGraph();
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
    props.onImportFlowLeave();
    props.knowledge.onCloseArchitectureGraph();
    props.onViewModeChange("overview");
    props.onPanelModeChange("none");
  };
  const alert = props.index.error ?? props.project.error ?? props.session.error ?? props.localImport.error ?? props.githubImport.error ?? props.connection.error;
  const style = { "--ct-panel-width": `${panelWidth}px`, "--ct-sidebar-width": `${effectiveSidebarWidth}px` } as CSSProperties;
  return (
    <main ref={appRef} className={`ct-app${props.panelMode === "source" || showSideGraphPanel ? " has-side-panel" : ""}${showSideGraphPanel ? " has-main-graph-panel" : ""}`} style={style}>
      <header className="ct-topbar">
        <button type="button" className="ct-mobile-menu" onClick={() => setMobileSidebarOpen(true)}>项目</button>
        <div className="ct-brand"><span className="ct-brand-mark">CT</span><span><strong>CODETRAIL</strong><small>CODE INTELLIGENCE WORKSPACE</small></span></div>
        <div className="ct-topbar-project"><span>当前项目</span><strong>{props.project.selectedProject?.name ?? "未选择项目"}</strong></div>
        <div className="ct-connection" data-state={props.connection.state}><span className="ct-status-dot" aria-hidden="true" /><span>{props.connection.state === "connected" ? "API ONLINE" : props.connection.state === "checking" ? "API CHECKING" : "API OFFLINE"}</span>{props.connection.health ? <small>v{props.connection.health.version}</small> : null}<button type="button" onClick={() => void props.connection.onCheck()} disabled={props.connection.state === "checking"}>{props.connection.state === "checking" ? "检查中" : "重试"}</button></div>
      </header>
      <div className="ct-workspace-frame" ref={workspaceFrameRef}>
        {mobileSidebarOpen ? <button type="button" className="ct-mobile-scrim" aria-label="关闭项目导航" onClick={() => setMobileSidebarOpen(false)} /> : null}
        <div className={`ct-sidebar-slot${mobileSidebarOpen ? " is-open" : ""}`} id="workspace-sidebar"><ProjectRail project={{ ...props.project, onSelectProject: selectProject }} session={{ ...props.session, onSelectSession: selectSession }} viewMode={props.viewMode} settingsOpen={props.modelSettings.open} onOpenOperation={openOperation} onViewKnowledge={openKnowledge} onOpenSettings={openSettingsFromSidebar} />
          <div
            className="ct-sidebar-resize-handle"
            role="separator"
            aria-label="调整左侧导航宽度"
            aria-controls="workspace-sidebar"
            aria-orientation="vertical"
            aria-valuemin={sidebarRange.min}
            aria-valuemax={sidebarRange.max}
            aria-valuenow={effectiveSidebarWidth}
            aria-valuetext={`${effectiveSidebarWidth} 像素`}
            aria-disabled={sidebarRange.max <= sidebarRange.min}
            tabIndex={0}
            onKeyDown={onSidebarKeyDown}
            onPointerDown={onSidebarPointerDown}
            onPointerMove={onSidebarPointerMove}
            onPointerUp={finishSidebarResize}
            onPointerCancel={finishSidebarResize}
            onLostPointerCapture={finishSidebarResize}
          />
        </div>
        <section className="ct-center-column" aria-label={props.viewMode === "session" ? "Issue 会话" : "项目知识页"}>
          <ContextHeader project={props.project} index={props.index} viewMode={props.viewMode} />
          {alert ? <div className="ct-global-alert" role="alert"><span>{alert}</span>{props.connection.state === "error" ? <button type="button" className="ct-link-button" onClick={() => void props.connection.onCheck()}>重新检查</button> : null}</div> : null}
          {operationPanel ? <OperationPanel mode={operationPanel.mode} initialTab={operationPanel.tab} onClose={closeOperation} project={props.project} localImport={props.localImport} githubImport={props.githubImport} index={props.index} /> : props.viewMode === "session" ? <SessionView session={props.session} onBack={openKnowledge} /> : <KnowledgeView key={graphScopeKey} project={props.project} index={props.index} knowledge={props.knowledge} graph={props.graph} panelMode={props.panelMode} onPanelModeChange={props.onPanelModeChange} onOpenOperation={openOperation} />}
        </section>
        {props.panelMode === "source" ? <EvidencePanel evidence={props.evidence} returnPanel={returnPanel} onReturnToPanel={() => props.onPanelModeChange(returnPanel ?? "graph")} /> : showSideGraphPanel ? <GraphPanel graph={props.graph} /> : null}
      </div>
      {props.panelMode === "source" ? <div className="ct-panel-width-control"><label htmlFor="panel-width">面板宽度</label><input id="panel-width" type="range" min="300" max="440" step="10" value={panelWidth} onChange={(event) => setPanelWidth(Number(event.target.value))} /><output>{panelWidth}px</output></div> : null}
      <footer className="ct-footer"><span>STATIC FACTS / FIXED EVIDENCE</span><span>AI 分析或 Codex 外部 Agent · 有界工具循环 · SSE 事件流与轮询回退</span><span>{props.index.response ? `${props.graph.codeSymbolCount} nodes · ${props.graph.codeEdgeCount} edges` : "No snapshot loaded"}</span></footer>
      <SettingsDialog modelSettings={props.modelSettings} />
    </main>
  );
}
