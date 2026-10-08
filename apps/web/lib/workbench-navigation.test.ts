import assert from "node:assert/strict";
import test from "node:test";

import { readWorkbenchRoute, resolveWorkbenchProject, workbenchLocation } from "./workbench-navigation.ts";

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
