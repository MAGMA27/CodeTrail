import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

import { canShowKnowledgeInspector, readWorkbenchRoute, resolveGraphReturnPanel, resolveWorkbenchProject, workbenchLocation } from "./workbench-navigation.ts";
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
