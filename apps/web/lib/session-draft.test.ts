import assert from "node:assert/strict";
import test from "node:test";

import {
  nextSessionDraftRevision,
  shouldClearSubmittedSessionDraft,
} from "./session-draft.ts";

test("a delayed save does not clear a newer draft in the same session", async () => {
  let draft = "first message";
  let draftRevision = 0;
  const submission = { projectId: "project-a", sessionId: "session-a", draftRevision };
  let release!: () => void;
  const response = new Promise<void>((resolve) => {
    release = resolve;
  });
  const save = response.then(() => {
    if (shouldClearSubmittedSessionDraft(
      { projectId: "project-a", sessionId: "session-a", draftRevision },
      submission,
    )) {
      draft = "";
    }
  });

  draft = "follow-up while saving";
  draftRevision = nextSessionDraftRevision(draftRevision);
  release();
  await save;

  assert.equal(draft, "follow-up while saving");
  assert.equal(
    shouldClearSubmittedSessionDraft(
      { projectId: "project-a", sessionId: "session-a", draftRevision },
      submission,
    ),
    false,
  );
});

test("draft identity includes session and revision rather than only text", () => {
  const submission = { projectId: "project-a", sessionId: "session-a", draftRevision: 4 };
  assert.equal(
    shouldClearSubmittedSessionDraft(
      { projectId: "project-a", sessionId: "session-a", draftRevision: 4 },
      submission,
    ),
    true,
  );
  assert.equal(
    shouldClearSubmittedSessionDraft(
      { projectId: "project-a", sessionId: "session-a", draftRevision: 5 },
      submission,
    ),
    false,
  );
  assert.equal(
    shouldClearSubmittedSessionDraft(
      { projectId: "project-b", sessionId: "session-a", draftRevision: 4 },
      submission,
    ),
    false,
  );
});
