import assert from "node:assert/strict";
import test from "node:test";

import {
  finishImportSubmissionKey,
  githubImportErrorMessage,
  isImportStatusTerminal,
  nextImportSubmissionKey,
  waitForImportStatus,
} from "./import-status.ts";

type AttemptResult = { attempt: { status: "queued" | "acquiring" | "indexing" | "ready" | "failed" } };

const pending: AttemptResult = { attempt: { status: "queued" } };

test("reuses an import key only while the submission outcome is uncertain", () => {
  let created = 0;
  const createKey = () => `key-${++created}`;
  const signature = JSON.stringify(["project", "https://github.com/org/repo", "main"]);

  const first = nextImportSubmissionKey(null, signature, createKey);
  const uncertain = finishImportSubmissionKey(first, true);
  const reconnect = nextImportSubmissionKey(uncertain, signature, createKey);
  assert.equal(reconnect.key, first.key);

  const changedRef = nextImportSubmissionKey(reconnect, JSON.stringify(["project", "https://github.com/org/repo", "next"]), createKey);
  assert.notEqual(changedRef.key, first.key);

  const afterKnownResponse = finishImportSubmissionKey(changedRef, false);
  assert.equal(afterKnownResponse, null);
  const deliberateRepeat = nextImportSubmissionKey(afterKnownResponse, JSON.stringify(["project", "https://github.com/org/repo", "next"]), createKey);
  assert.notEqual(deliberateRepeat.key, changedRef.key);
  assert.equal(created, 3);
});

test("polls a pending import until it reaches ready", async () => {
  const signal = new AbortController().signal;
  const statuses: AttemptResult[] = [
    { attempt: { status: "indexing" } },
    { attempt: { status: "ready" } },
  ];
  let reads = 0;

  const result = await waitForImportStatus(
    pending,
    async () => {
      reads += 1;
      return statuses.shift() ?? { attempt: { status: "ready" } };
    },
    signal,
    { intervalMs: 0, maxPolls: 4 },
  );

  assert.equal(result.attempt.status, "ready");
  assert.equal(reads, 2);
});

test("stops polling on a failed attempt and returns the latest status", async () => {
  const result = await waitForImportStatus(
    pending,
    async () => ({ attempt: { status: "failed" } }),
    new AbortController().signal,
    { intervalMs: 0, maxPolls: 4 },
  );

  assert.equal(isImportStatusTerminal(result.attempt.status), true);
  assert.equal(result.attempt.status, "failed");
});

test("returns the last pending status when the bounded poll window expires", async () => {
  let reads = 0;
  const result = await waitForImportStatus(
    pending,
    async () => {
      reads += 1;
      return { attempt: { status: "indexing" } };
    },
    new AbortController().signal,
    { intervalMs: 0, maxPolls: 3 },
  );

  assert.equal(reads, 3);
  assert.equal(result.attempt.status, "indexing");
  assert.equal(isImportStatusTerminal(result.attempt.status), false);
});

test("aborts the poll wait without reading another status", async () => {
  const controller = new AbortController();
  let reads = 0;
  const pendingPoll = waitForImportStatus(
    pending,
    async () => {
      reads += 1;
      return pending;
    },
    controller.signal,
    { intervalMs: 60_000, maxPolls: 4 },
  );
  controller.abort();

  await assert.rejects(pendingPoll, { name: "AbortError" });
  assert.equal(reads, 0);
});

test("bounds a status request that never settles and keeps its last attempt status", async () => {
  let reads = 0;
  const result = await waitForImportStatus(
    pending,
    (signal) => {
      reads += 1;
      return new Promise((resolve) => {
        signal.addEventListener("abort", () => resolve({ attempt: { status: "indexing" } }), { once: true });
      });
    },
    new AbortController().signal,
    { intervalMs: 0, maxPolls: 40, timeoutMs: 250 },
  );

  assert.equal(reads, 1);
  assert.equal(result.attempt.status, "indexing");
});

test("explains GitHub rate limit errors and keeps other import errors unchanged", () => {
  const limited = githubImportErrorMessage("GitHub API rate limit reached; the import was not completed. Retry after about 30 seconds.");
  assert.match(limited, /^GitHub API 限流/);
  assert.match(limited, /CODETRAIL_GITHUB_TOKEN/);
  assert.match(limited, /Retry after about 30 seconds/);
  assert.equal(githubImportErrorMessage("GitHub repository or ref was not found"), "GitHub repository or ref was not found");
});

test("explains failed GitHub download attempts without showing the rate-limit hint", () => {
  const failedAttempt = {
    status: "failed",
    error_code: "download_timeout",
    error_message: "The archive download exceeded its time limit.",
  };
  const message = githubImportErrorMessage(failedAttempt.error_message, failedAttempt.error_code);
  assert.match(message, /^GitHub 仓库归档下载超时/);
  assert.match(message, /可重试/);
  assert.match(message, /The archive download exceeded its time limit/);
  assert.doesNotMatch(message, /限流/);
});
