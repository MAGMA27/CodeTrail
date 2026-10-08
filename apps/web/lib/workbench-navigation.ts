export type WorkbenchRoute = {
  projectId: string | null;
  sessionId: string | null;
  hasExplicitProject: boolean;
  error: string | null;
};

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
