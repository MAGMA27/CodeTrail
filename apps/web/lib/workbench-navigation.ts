export type WorkbenchRoute = {
  projectId: string | null;
  sessionId: string | null;
  hasExplicitProject: boolean;
  error: string | null;
};

export type WorkbenchKnowledgeView = "architecture" | "features";
export type WorkbenchEvidenceRoute = {
  path: string;
  startLine: number;
  endLine: number;
  contentHash: string;
  evidenceId: string;
};
export type WorkbenchViewState = {
  view: WorkbenchKnowledgeView;
  journeyId: string | null;
  projectId: string | null;
  indexId: string | null;
  evidence: WorkbenchEvidenceRoute | null;
  error: string | null;
};

export type WorkbenchRouteState = {
  view: WorkbenchKnowledgeView;
  journeyId: string | null;
  projectId: string;
  indexId: string | null;
  evidence: WorkbenchEvidenceRoute | null;
  sessionId: string | null;
};

export function mergeWorkbenchRouteState(
  current: WorkbenchRouteState,
  patch: Partial<WorkbenchRouteState>,
): WorkbenchRouteState {
  return { ...current, ...patch };
}

export function resolveWorkbenchRouteState(
  current: WorkbenchRouteState,
  patch: Partial<WorkbenchRouteState>,
  scope: {
    sourceIndexId: string | null;
    sessionIndexId: string | null;
    sessionActive: boolean;
    journeyIndexId: string | null;
  },
): WorkbenchRouteState {
  const resolved = mergeWorkbenchRouteState(current, patch);
  const explicitEvidenceIndexId = patch.evidence ? patch.indexId : null;
  const indexId = explicitEvidenceIndexId ?? scope.sourceIndexId ?? (scope.sessionActive ? scope.sessionIndexId : null) ?? resolved.indexId;
  const journeyIndexId = patch.journeyId ? patch.indexId ?? scope.journeyIndexId : scope.journeyIndexId;
  return {
    ...resolved,
    indexId,
    journeyId: resolved.journeyId && journeyIndexId !== indexId ? null : resolved.journeyId,
  };
}

export function workbenchScopeMatches(currentIndexId: string | null, scopedIndexId: string | null | undefined, explicitEvidenceIndexId: string | null): boolean {
  return scopedIndexId === undefined || scopedIndexId === currentIndexId || explicitEvidenceIndexId !== null && scopedIndexId === explicitEvidenceIndexId;
}

export function sessionIndexMatches(sessionIndexId: string, linkedIndexId: string | null): boolean {
  return linkedIndexId === null || sessionIndexId === linkedIndexId;
}

export function readWorkbenchViewState(search: string): WorkbenchViewState {
  const params = new URLSearchParams(search);
  const rawView = params.get("view");
  const view = rawView === "features" ? "features" : "architecture";
  const citationKeys = ["path", "start_line", "end_line", "content_hash", "evidence_id"] as const;
  const hasEvidence = citationKeys.some((key) => params.has(key));
  const projectId = params.get("project_id");
  const indexId = params.get("index_id");
  const journeyId = params.get("journey_id");
  let error: string | null = null;
  let evidence: WorkbenchEvidenceRoute | null = null;
  if (rawView !== null && rawView !== "architecture" && rawView !== "features" && rawView !== "session") {
    error = "工作台链接的 view 无效，已回退到架构视图。";
  } else if (rawView === "session" && !params.get("session_id")) {
    error = "工作台 Session 视图链接缺少 session_id，已拒绝打开。";
  } else if (params.has("index_id") && !indexId) {
    error = "工作台链接的 index_id 无效，已拒绝打开。";
  } else if (params.has("journey_id") && !journeyId) {
    error = "工作台链接的 journey_id 无效，已拒绝打开。";
  } else if (hasEvidence) {
    const path = params.get("path");
    const startLine = Number(params.get("start_line"));
    const endLine = Number(params.get("end_line"));
    const contentHash = params.get("content_hash");
    const evidenceId = params.get("evidence_id");
    if (!projectId || !indexId || !path || !contentHash || !evidenceId || !params.has("start_line") || !params.has("end_line") ||
      !Number.isInteger(startLine) || !Number.isInteger(endLine) || startLine < 1 || endLine < startLine) {
      error = "工作台链接的源码证据引用不完整或行号无效，已拒绝打开。";
    } else {
      evidence = { path, startLine, endLine, contentHash, evidenceId };
    }
  }
  if (!error && journeyId && (!projectId || !indexId)) {
    error = "工作台旅程链接缺少 project_id 或 index_id，已拒绝打开。";
  }
  return { view, journeyId, projectId, indexId, evidence, error };
}

export function workbenchStateLocation(
  currentUrl: string,
  state: {
    view: WorkbenchKnowledgeView;
    journeyId?: string | null;
    projectId: string;
    indexId?: string | null;
    evidence?: WorkbenchEvidenceRoute | null;
    sessionId?: string | null;
  },
): string {
  const url = new URL(currentUrl);
  url.search = "";
  url.searchParams.set("project_id", state.projectId);
  if (state.sessionId) url.searchParams.set("session_id", state.sessionId);
  if (state.view !== "architecture" || state.journeyId || state.evidence || state.indexId) {
    url.searchParams.set("view", state.view);
  }
  if (state.indexId) url.searchParams.set("index_id", state.indexId);
  if (state.journeyId) url.searchParams.set("journey_id", state.journeyId);
  if (state.evidence) {
    url.searchParams.set("path", state.evidence.path);
    url.searchParams.set("start_line", String(state.evidence.startLine));
    url.searchParams.set("end_line", String(state.evidence.endLine));
    url.searchParams.set("content_hash", state.evidence.contentHash);
    url.searchParams.set("evidence_id", state.evidence.evidenceId);
  }
  return `${url.pathname}?${url.searchParams.toString()}${url.hash}`;
}

export type GraphPanelMode = "graph" | "feature_graph" | "impact";
type WorkbenchPanelMode = GraphPanelMode | "source" | "none";

export function canShowKnowledgeInspector(panel: WorkbenchPanelMode, inlineArchitectureGraph: boolean): boolean {
  return panel === "none" || (panel === "graph" && inlineArchitectureGraph);
}

export function resolveGraphReturnPanel(
  currentPanel: WorkbenchPanelMode,
  previousPanel: WorkbenchPanelMode,
  sameScope: boolean,
): GraphPanelMode | null {
  if (!sameScope || currentPanel !== "source") {
    return null;
  }
  return previousPanel === "graph" || previousPanel === "feature_graph" || previousPanel === "impact"
    ? previousPanel
    : null;
}

export function readWorkbenchRoute(search: string): WorkbenchRoute {
  const params = new URLSearchParams(search);
  const hasExplicitProject = params.has("project_id");
  const projectId = params.get("project_id");
  const sessionId = params.get("session_id");
  if (hasExplicitProject && !projectId) {
    return { projectId, sessionId, hasExplicitProject, error: "工作台引用的 project_id 无效。" };
  }
  if (sessionId !== null && (!projectId || !sessionId)) {
    return {
      projectId,
      sessionId,
      hasExplicitProject,
      error: "工作台会话引用缺少必要的项目或会话参数。",
    };
  }
  return { projectId, sessionId, hasExplicitProject, error: null };
}

export function resolveWorkbenchProject(
  route: WorkbenchRoute,
  linkedProjectId: string | null,
  projectIds: string[],
  savedProjectId: string | null,
): { projectId: string | null; requestedProjectId: string | null } {
  const requestedProjectId = linkedProjectId ?? route.projectId;
  const saved = route.hasExplicitProject || linkedProjectId
    ? null
    : savedProjectId;
  return {
    projectId: requestedProjectId
      ? projectIds.includes(requestedProjectId) ? requestedProjectId : null
      : projectIds.includes(saved ?? "") ? saved : projectIds[0] ?? null,
    requestedProjectId,
  };
}

export function workbenchLocation(currentUrl: string, projectId: string, sessionId?: string): string {
  const url = new URL(currentUrl);
  url.search = "";
  url.searchParams.set("project_id", projectId);
  if (sessionId) {
    url.searchParams.set("session_id", sessionId);
  }
  return `${url.pathname}?${url.searchParams.toString()}${url.hash}`;
}
