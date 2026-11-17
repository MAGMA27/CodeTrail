import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

import { canShowKnowledgeInspector, mergeWorkbenchRouteState, readWorkbenchRoute, readWorkbenchViewState, resolveGraphReturnPanel, resolveWorkbenchProject, resolveWorkbenchRouteState, sessionIndexMatches, workbenchLocation, workbenchScopeMatches, workbenchStateLocation, type WorkbenchRouteState } from "./workbench-navigation.ts";
import { clampSidebarWidth, normalizeSidebarWidth, readSidebarWidth, resolveSidebarWidthRange } from "./sidebar-width.ts";
import { clampStructureDetailWidth, normalizeStructureDetailWidth, readStructureDetailWidth, resolveStructureDetailWidthRange } from "./structure-detail-width.ts";

test("sidebar width preferences reject invalid values and stay within the saved range", () => {
  assert.equal(readSidebarWidth(null), null);
  assert.equal(readSidebarWidth(" "), null);
  assert.equal(readSidebarWidth("Infinity"), null);
  assert.equal(readSidebarWidth("180"), 220);
  assert.equal(readSidebarWidth("500"), 480);
  assert.equal(readSidebarWidth("316.4"), 316);
  assert.equal(normalizeSidebarWidth(Number.NaN), 264);
});

test("sidebar width range preserves central space when the workspace is constrained", () => {
  assert.deepEqual(resolveSidebarWidthRange(1_600, 480), { min: 220, max: 480 });
  assert.deepEqual(resolveSidebarWidthRange(900, 680), { min: 220, max: 220 });
  const narrow = resolveSidebarWidthRange(768, 680);
  assert.deepEqual(narrow, { min: 88, max: 88 });
  assert.equal(clampSidebarWidth(220, narrow), 88);
  assert.equal(clampSidebarWidth(Number.NaN, narrow), 88);
});

test("structure detail width persists valid preferences and yields space to the architecture canvas", () => {
  assert.equal(readStructureDetailWidth(null), null);
  assert.equal(readStructureDetailWidth("Infinity"), null);
  assert.equal(readStructureDetailWidth("240"), 280);
  assert.equal(readStructureDetailWidth("600"), 560);
  assert.equal(normalizeStructureDetailWidth(Number.NaN), 360);

  assert.deepEqual(resolveStructureDetailWidthRange(1_600), { min: 280, max: 560 });
  const constrained = resolveStructureDetailWidthRange(768);
  assert.deepEqual(constrained, { min: 274, max: 274 });
  assert.equal(768 - constrained.max, 494, "the detail panel leaves the canvas minimum and grid gap available");
  assert.equal(clampStructureDetailWidth(360, constrained), 274);
  assert.equal(clampStructureDetailWidth(Number.NaN, constrained), 274);
});

test("knowledge details yield to evidence and external graphs, then resume on return", () => {
  assert.equal(canShowKnowledgeInspector("none", false), true);
  assert.equal(canShowKnowledgeInspector("graph", true), true);
  for (const panel of ["source", "feature_graph", "impact", "graph"] as const) {
    assert.equal(canShowKnowledgeInspector(panel, false), false);
  }
  assert.equal(canShowKnowledgeInspector("source", true), false);
  assert.equal(canShowKnowledgeInspector("graph", true), true);
});

test("an explicit project URL wins over another tab's saved project and opens architecture", () => {
  const route = readWorkbenchRoute("?project_id=project-url");
  const resolved = resolveWorkbenchProject(route, null, ["project-url", "project-saved"], "project-saved");

  assert.equal(resolved.projectId, "project-url");
  assert.equal(route.sessionId, null);
});

test("only a complete project and session URL restores a session", () => {
  assert.deepEqual(readWorkbenchRoute("?project_id=project-a"), {
    projectId: "project-a",
    sessionId: null,
    hasExplicitProject: true,
    error: null,
  });
  assert.deepEqual(readWorkbenchRoute("?project_id=project-a&session_id=session-a"), {
    projectId: "project-a",
    sessionId: "session-a",
    hasExplicitProject: true,
    error: null,
  });
  assert.match(readWorkbenchRoute("?session_id=session-a").error ?? "", /项目或会话/);
});

test("returning to architecture replaces the session URL while preserving the hash", () => {
  assert.equal(
    workbenchLocation("https://codetrail.local/?project_id=project-a&session_id=session-a#overview", "project-a"),
    "/?project_id=project-a#overview",
  );
  assert.equal(
    workbenchLocation("https://codetrail.local/?project_id=project-a#overview", "project-a", "session-a"),
    "/?project_id=project-a&session_id=session-a#overview",
  );
});

test("refresh route retains each supported journey ID and the pinned citation", () => {
  for (const journeyId of ["tsx-cli-journey", "native-api-journey"]) {
    const url = workbenchStateLocation("https://codetrail.local/?project_id=p&index_id=old#source", {
      view: "features", journeyId, projectId: "p", indexId: "old",
      evidence: { path: "apps/api/app/main.py", startLine: 12, endLine: 18, contentHash: "sha256:old", evidenceId: "evidence-old" },
    });
    const state = readWorkbenchViewState(new URL(url, "https://codetrail.local").search);
    assert.equal(state.view, "features");
    assert.equal(state.journeyId, journeyId);
    assert.equal(state.indexId, "old");
    assert.deepEqual(state.evidence, {
      path: "apps/api/app/main.py", startLine: 12, endLine: 18, contentHash: "sha256:old", evidenceId: "evidence-old",
    });
  }
});

test("Session and citation links preserve the Features subview and reject a different fixed index", () => {
  const url = workbenchStateLocation("https://codetrail.local/", {
    view: "features", journeyId: "tsx-cli-journey", projectId: "p", indexId: "old",
    sessionId: "session-old",
    evidence: { path: "src/cli.tsx", startLine: 4, endLine: 8, contentHash: "hash-old", evidenceId: "e-old" },
  });
  const params = new URL(url, "https://codetrail.local").searchParams;
  assert.equal(params.get("session_id"), "session-old");
  assert.equal(params.get("view"), "features");
  assert.equal(readWorkbenchViewState(url.split("?")[1]).indexId, "old");
  assert.equal(sessionIndexMatches("index-old", "index-old"), true);
  assert.equal(sessionIndexMatches("index-old", "index-new"), false, "an old Session is never silently retargeted to the new citation index");
  assert.equal(sessionIndexMatches("index-old", null), true, "a Session-only link needs no extra citation-index comparison");
  const source = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  assert.match(source, /sessionIndexMatches\(session\.index\.index_id, linkedIndexId\)/);
  assert.match(source, /Session 固定索引与链接中的 index_id\/源码证据不一致/);
  assert.match(source, /routeIndexId && !projectIndexResponse\.indexes\.some\(\(index\) => index\.index_id === routeIndexId\)[\s\S]*?已不可用，已拒绝回退到最新索引/);
});

test("source and journey restore updates preserve each other's URL fields in either completion order", () => {
  const initial: WorkbenchRouteState = {
    view: "architecture",
    journeyId: null,
    projectId: "p",
    indexId: "old",
    evidence: null,
    sessionId: "session-old",
  };
  const citation = { path: "src/main.tsx", startLine: 11, endLine: 16, contentHash: "hash-old", evidenceId: "e-old" };
  const restoreEvidence = (state: WorkbenchRouteState) => mergeWorkbenchRouteState(state, { evidence: citation, indexId: "old" });
  const restoreJourney = (state: WorkbenchRouteState) => mergeWorkbenchRouteState(state, { view: "features", journeyId: "tsx-cli-journey", indexId: "old" });
  for (const state of [restoreJourney(restoreEvidence(initial)), restoreEvidence(restoreJourney(initial))]) {
    const url = workbenchStateLocation("https://codetrail.local/", state);
    const restored = readWorkbenchViewState(new URL(url, "https://codetrail.local").search);
    const params = new URL(url, "https://codetrail.local").searchParams;
    assert.equal(restored.view, "features");
    assert.equal(restored.journeyId, "tsx-cli-journey");
    assert.equal(restored.indexId, "old");
    assert.deepEqual(restored.evidence, citation);
    assert.equal(params.get("session_id"), "session-old");
  }
});

test("restore callback wiring keeps source and journey route fields across either completion order", () => {
  const source = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  const routeWriter = source.match(/const persistKnowledgeRoute = useCallback\(([\s\S]*?)\n  \}, \[\]\);/);
  assert.ok(routeWriter, "route persistence must have a stable callback identity");
  assert.match(routeWriter[1], /knowledgeRouteState\.current/);
  assert.match(routeWriter[1], /resolveWorkbenchRouteState/);
  assert.match(routeWriter[1], /sourceIndexId/);
  assert.match(routeWriter[1], /sessionActive: current\.viewMode === "session"/);
  assert.match(routeWriter[1], /current\.journeyIndexId/);
  assert.match(routeWriter[1], /requestedJourneyId\.current/);
  assert.match(routeWriter[1], /overrides\.scope\.projectId !== current\.projectId/);
  assert.match(source, /const loadLinkedEvidence = useCallback\([\s\S]*?\[persistKnowledgeRoute\],\n  \);/);
  assert.match(source, /const loadSelectedProject = useCallback\([\s\S]*?\[applyIndexResponse, clearExploration, loadLinkedEvidence\],\n  \);/);
  assert.match(source, /void selectJourney\(requested\)/);
  const journeyStart = source.indexOf("const selectJourney = useCallback");
  const journeyEnd = source.indexOf("const loadModuleSymbolsPage", journeyStart);
  assert.ok(journeyStart >= 0 && journeyEnd > journeyStart);
  assert.doesNotMatch(source.slice(journeyStart, journeyEnd), /evidence:\s*null/);
  assert.doesNotMatch(routeWriter[1], /selectedSessionId \?\? sessionLink\.current\.sessionId/);
});

test("the stable source restore writer ignores responses from old project or index scopes", () => {
  const source = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  assert.match(source, /scope: \{ projectId: link\.projectId, indexId: link\.indexId \}/);
  assert.match(source, /scope: \{ projectId: selectedProjectId, indexId: response\.index_id \}/);
  assert.match(source, /scope: \{ projectId: selectedProjectId, indexId \}/);
  assert.match(source, /workbenchScopeMatches\(allowedIndexId, overrides\.scope\.indexId, explicitEvidenceIndexId\)/);
});

test("invalid and incomplete deep link state is reported instead of retargeted", () => {
  assert.match(readWorkbenchViewState("?project_id=p&view=unknown").error ?? "", /view/);
  assert.match(readWorkbenchViewState("?project_id=p&view=session").error ?? "", /session_id/);
  assert.match(readWorkbenchViewState("?project_id=p&journey_id=j").error ?? "", /index/);
  assert.match(readWorkbenchViewState("?project_id=p&index_id=old&evidence_id=e").error ?? "", /证据/);
  assert.match(readWorkbenchViewState("?project_id=p&index_id=").error ?? "", /index_id/);
  assert.match(readWorkbenchViewState("?project_id=p&journey_id=j&index_id=").error ?? "", /index_id/);
  assert.match(readWorkbenchViewState("?project_id=p&journey_id=&index_id=old").error ?? "", /journey_id/);
  assert.equal(readWorkbenchViewState("?project_id=p").error, null, "omitted IDs retain their default behavior");
});

test("an old Session citation pins route serialization across view changes, then dismissal restores knowledge scope", () => {
  const citation = { path: "src/old.tsx", startLine: 7, endLine: 10, contentHash: "hash-old", evidenceId: "e-old" };
  const current: WorkbenchRouteState = {
    view: "features", journeyId: "journey-new", projectId: "p", indexId: "index-new",
    evidence: citation, sessionId: null,
  };
  const switchedView = resolveWorkbenchRouteState(current, { view: "architecture" }, {
    sourceIndexId: "index-old", sessionIndexId: "index-old", sessionActive: false,
    journeyIndexId: "index-new",
  });
  assert.equal(switchedView.indexId, "index-old", "an active citation keeps its own immutable index");
  assert.equal(switchedView.journeyId, null, "a journey from the newer knowledge index is not paired with old evidence");
  const citationUrl = workbenchStateLocation("https://codetrail.local/", switchedView);
  const citationState = readWorkbenchViewState(new URL(citationUrl, "https://codetrail.local").search);
  assert.equal(citationState.indexId, "index-old");
  assert.deepEqual(citationState.evidence, citation);
  assert.equal(citationState.journeyId, null);

  const dismissed = resolveWorkbenchRouteState(switchedView, { evidence: null }, {
    sourceIndexId: "index-old", sessionIndexId: "index-old", sessionActive: false,
    journeyIndexId: "index-new",
  });
  assert.equal(dismissed.indexId, "index-old", "dismissing evidence preserves the cited fixed index");
  assert.equal(dismissed.evidence, null);
  assert.equal(dismissed.journeyId, null, "a journey from another index remains cleared after dismissal");
  const dismissedState = readWorkbenchViewState(new URL(workbenchStateLocation("https://codetrail.local/", dismissed), "https://codetrail.local").search);
  assert.equal(dismissedState.evidence, null);
  assert.equal(dismissedState.indexId, "index-old");
});

test("explicit journey and citation intent wins over stale cached scope in the serialized URL", () => {
  const empty: WorkbenchRouteState = {
    view: "architecture", journeyId: null, projectId: "p", indexId: null, evidence: null, sessionId: null,
  };
  const selectedJourney = resolveWorkbenchRouteState(empty, {
    view: "features", journeyId: "journey-new", indexId: "index-new",
  }, {
    sourceIndexId: null, sessionIndexId: null, sessionActive: false, journeyIndexId: null,
  });
  const journeyUrl = workbenchStateLocation("https://codetrail.local/", selectedJourney);
  const journeyState = readWorkbenchViewState(new URL(journeyUrl, "https://codetrail.local").search);
  assert.equal(journeyState.journeyId, "journey-new");
  assert.equal(journeyState.indexId, "index-new");

  const oldCitation = { path: "src/old.tsx", startLine: 7, endLine: 10, contentHash: "hash-old", evidenceId: "e-old" };
  const newCitation = { path: "src/new.tsx", startLine: 3, endLine: 5, contentHash: "hash-new", evidenceId: "e-new" };
  const oldSource: WorkbenchRouteState = {
    view: "architecture", journeyId: null, projectId: "p", indexId: "index-old", evidence: oldCitation, sessionId: null,
  };
  const loadedNewCitation = resolveWorkbenchRouteState(oldSource, {
    evidence: newCitation, indexId: "index-new",
  }, {
    sourceIndexId: "index-old", sessionIndexId: null, sessionActive: false, journeyIndexId: null,
  });
  const citationUrl = workbenchStateLocation("https://codetrail.local/", loadedNewCitation);
  const citationState = readWorkbenchViewState(new URL(citationUrl, "https://codetrail.local").search);
  assert.equal(citationState.indexId, "index-new");
  assert.deepEqual(citationState.evidence, newCitation);

  const tabChange = resolveWorkbenchRouteState(oldSource, { view: "features" }, {
    sourceIndexId: "index-old", sessionIndexId: null, sessionActive: false, journeyIndexId: null,
  });
  assert.equal(tabChange.indexId, "index-old", "a tab change keeps the already pinned citation index");
  assert.equal(workbenchScopeMatches("index-new", "index-old", null), false, "a late old-index response cannot rewrite a manually switched scope");
  assert.equal(workbenchScopeMatches("index-new", "index-new", null), true);
  assert.equal(workbenchScopeMatches("index-new", "index-old", "index-old"), true, "an explicit new citation may target its validated fixed index");
});

test("source dismissal clears cached evidence and aborts before leaving the source panel", () => {
  const source = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  const dismissal = source.match(/const dismissActiveEvidence = useCallback\(\(\) => \{([\s\S]*?)\n  \}, \[persistKnowledgeRoute\]\);/);
  assert.ok(dismissal);
  assert.match(dismissal[1], /sourceController\.current\?\.abort\(\)/);
  assert.match(dismissal[1], /evidenceLink\.current = null/);
  assert.match(dismissal[1], /setSourceResponse\(null\)/);
  assert.match(dismissal[1], /persistKnowledgeRoute\(\{ evidence: null \}\)/);
  assert.match(source, /onExpandRelation: \(\) => \{[\s\S]*?dismissActiveEvidence\(\);[\s\S]*?loadNeighborhood/);
  assert.match(source, /onClose: \(\) => \{\n          dismissActiveEvidence\(\);\n          if \(httpEntryChain \|\| overviewEntryCallChain\)/);
  const indexSwitchStart = source.indexOf("const openExistingIndex = useCallback(");
  const indexSwitchEnd = source.indexOf("\n  );", indexSwitchStart);
  assert.ok(indexSwitchStart >= 0 && indexSwitchEnd > indexSwitchStart);
  assert.match(source.slice(indexSwitchStart, indexSwitchEnd), /evidenceLink\.current = null/);
});

test("project switch writes a fresh scope and cannot carry the previous project's citation", () => {
  const previous = workbenchStateLocation("https://codetrail.local/?project_id=old", {
    view: "features", projectId: "old", indexId: "old-index", journeyId: "old-journey",
    evidence: { path: "src/old.tsx", startLine: 2, endLine: 4, contentHash: "hash-old", evidenceId: "e-old" },
  });
  const switched = workbenchStateLocation(`https://codetrail.local${previous}`, { view: "architecture", projectId: "new" });
  const state = readWorkbenchViewState(new URL(switched, "https://codetrail.local").search);
  assert.equal(state.projectId, "new");
  assert.equal(state.indexId, null);
  assert.equal(state.journeyId, null);
  assert.equal(state.evidence, null);
});

test("graph evidence returns to the same panel and scope changes clear that return", () => {
  const returnPanel = resolveGraphReturnPanel("source", "graph", true);

  assert.equal(returnPanel, "graph");
  assert.equal(resolveGraphReturnPanel(returnPanel ?? "source", "source", true), null);
  assert.equal(resolveGraphReturnPanel("source", "graph", false), null);
});

test("production analysis actions send real mode without fixture scenarios", () => {
  const source = ts.createSourceFile("workbench.tsx", readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const seen = new Set<string>();
  function visit(node: ts.Node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ["generateProjectFeatures", "createProjectSessionRun"].includes(node.expression.text)) {
      const payload = node.arguments.find(ts.isObjectLiteralExpression);
      assert.ok(payload, "analysis action must send an explicit payload");
      const mode = payload.properties.find((property) => ts.isPropertyAssignment(property) && property.name.getText(source) === "mode");
      assert.ok(mode && ts.isPropertyAssignment(mode) && ts.isStringLiteral(mode.initializer));
      assert.equal(mode.initializer.text, "real");
      assert.ok(!payload.properties.some((property) => /scenario/.test(property.getText(source))));
      seen.add(node.expression.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.equal(seen.size, 2);
});

test("Run panel blocks real runs when the server capability switch is off and keeps the Codex fallback", () => {
  const source = ts.createSourceFile("workbench-ui.tsx", readFileSync(new URL("../app/workbench-ui.tsx", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const ui = source.text;
  assert.match(ui, /const realRunDisabled = session\.modelApiKeyConfigured === true && session\.realRunEnabled === false/);
  assert.match(ui, /服务端真实模型 Run 开关尚未启用/);
  assert.match(ui, /"real_run_enabled": true/);
  assert.match(ui, /CODETRAIL_REAL_RUN_ENABLED=1/);
  assert.match(ui, /已保存的 Session 消息和有界源码证据会发送给模型供应商/);
  assert.match(ui, /\(session\.modelApiKeyConfigured === false \|\| realRunDisabled\) && analysisKind === "understanding" \? <div className="ct-codex-task"/);
  const workbench = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  assert.match(workbench, /modelConfigSettings\?\.has_api_key && !modelConfigSettings\.real_run_enabled/);
});

test("row trash actions keep an accessible disclosure and explicit close behavior", () => {
  const source = ts.createSourceFile("workbench-ui.tsx", readFileSync(new URL("../app/workbench-ui.tsx", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let rowActionsMenu: ts.FunctionDeclaration | undefined;
  function visit(node: ts.Node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "RowActionsMenu") rowActionsMenu = node;
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(rowActionsMenu, "Project and Session rows must share the accessible action menu");

  const component = rowActionsMenu.getText(source);
  assert.match(component, /<details[\s\S]*?<summary[^>]*aria-label=\{`\$\{label\}的更多操作`\}[^>]*>更多<\/summary>/);
  assert.match(component, /event\.key === "Escape"[\s\S]*?event\.stopPropagation\(\)[\s\S]*?triggerRef\.current\?\.focus\(\)/);
  assert.match(component, /addEventListener\("pointerdown"/);
  assert.match(component, /onBlur=\{/);
  assert.match(component, /<button[\s\S]*?disabled=\{disabled\}[\s\S]*?onMoveToTrash\(\)[\s\S]*?>移到回收站<\/button>/);
});

test("global settings stay in the sidebar footer and explain their shared scope", () => {
  const source = ts.createSourceFile("workbench-ui.tsx", readFileSync(new URL("../app/workbench-ui.tsx", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const ui = source.text;
  const workbench = readFileSync(new URL("../app/workbench.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const railStart = ui.indexOf("function ProjectRail(");
  const railEnd = ui.indexOf("function OperationPanel(", railStart);
  const rail = ui.slice(railStart, railEnd);
  const footerStart = rail.indexOf('<div className="ct-sidebar-footer">');
  const footerEnd = rail.indexOf("</div>", footerStart);
  const footer = rail.slice(footerStart, footerEnd);
  const dialogStart = ui.indexOf("function SettingsDialog(");
  const dialogEnd = ui.indexOf("export default function WorkbenchUi(", dialogStart);
  const dialog = ui.slice(dialogStart, dialogEnd);

  assert.ok(footerStart >= 0, "the project rail always renders the settings footer");
  assert.match(footer, /ct-sidebar-settings[\s\S]*?onOpenSettings[\s\S]*?>[\s\S]*?设置/);
  assert.match(footer, /aria-haspopup="dialog"[\s\S]*?aria-expanded=\{settingsOpen\}/);
  assert.match(css, /\.ct-sidebar-footer \{[^}]*margin-top:\s*auto/);
  assert.doesNotMatch(ui, /ct-model-settings-trigger|modelSettingsLabel/);
  assert.match(ui, /onOpenSettings=\{openSettingsFromSidebar\}/);
  assert.match(ui, /const openSettingsFromSidebar = \(\) => \{\s*void props\.modelSettings\.onOpen\(\);\s*setMobileSidebarOpen\(false\);/);
  assert.equal((workbench.match(/onOpenModelSettings: openModelSettings/g) ?? []).length, 2, "feature and session prompts share the same settings action");
  assert.match(ui, /<ProjectRail[\s\S]*?settingsOpen=\{props\.modelSettings\.open\}/);
  assert.match(ui, /<SettingsDialog modelSettings=\{props\.modelSettings\} \/>/);
  assert.match(dialog, /<h2 id="settings-dialog-title">设置<\/h2>/);
  assert.match(dialog, /<h3 id="model-service-heading">模型服务<\/h3>/);
  assert.match(dialog, /所有项目和会话中的内置 AI 分析/);
  assert.match(dialog, /Codex 外部 Agent 使用宿主模型，无需在这里配置/);
});
