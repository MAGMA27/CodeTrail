import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

import { canShowKnowledgeInspector, readWorkbenchRoute, resolveGraphReturnPanel, resolveWorkbenchProject, workbenchLocation } from "./workbench-navigation.ts";

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
