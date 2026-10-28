import assert from "node:assert/strict";
import test from "node:test";
import type { JobStatusResponse } from "./api.ts";

import {
  clearTrackedJob,
  finishImportSubmissionKey,
  githubImportErrorMessage,
  importStatusFromJob,
  isImportStatusTerminal,
  jobErrorMessage,
  jobStatusLabel,
  loadTrackedJobs,
  nextImportSubmissionKey,
  resolveGitHubImportJobResult,
  trackJob,
  waitForJob,
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

function makeJob(status: "queued" | "running" | "succeeded" | "failed", phase = "queued"): JobStatusResponse {
  return {
    schema_version: "0.1.0" as const, job_id: "job-1", project_id: null, kind: "github_import" as const,
    status, phase, progress: {}, attempts: [], result: null, error_code: null, summary: null,
    created_at: "now", updated_at: "now", deadline_at: "later", execution_deadline_at: null, merged_into: null,
  };
}

test("job status labels and error codes are localized", () => {
  assert.equal(jobStatusLabel("running", "indexing"), "索引中");
  assert.equal(jobStatusLabel("retry_wait", "retry_wait"), "等待重试");
  assert.match(jobErrorMessage("worker_lost", null), /工作进程中断/);
  assert.match(jobErrorMessage("rate_limited", null), /限流/);
  assert.equal(importStatusFromJob(makeJob("running", "downloading")), "acquiring");
  assert.equal(importStatusFromJob(makeJob("running", "indexing")), "indexing");
});

test("re-fetches GitHub import status when the succeeded job result is truncated", async () => {
  const job = makeJob("succeeded", "succeeded");
  job.project_id = "project-1";
  job.result = { truncated: true, project_id: "project-1", attempt_id: "attempt-1" };
  let fetchedAttempt = "";
  const response = await resolveGitHubImportJobResult(job, async (attemptId) => {
    fetchedAttempt = attemptId;
    return {
      schema_version: "0.1.0",
      source_path: "/data/project-1",
      project: {
        schema_version: "0.1.0", project_id: "project-1", name: "repo", repository_id: "repo-1",
        created_at: "now", available_index_ids: ["index-1"], latest_index_id: "index-1", building_index_id: null,
      },
      attempt: {
        schema_version: "0.1.0", attempt_id: "attempt-1", project_id: "project-1", repository_id: "repo-1",
        source_path: "/data/project-1", status: "ready", phase: "ready", index_id: "index-1",
        error_code: null, error_message: null, source_kind: "github", source_url: "https://github.com/org/repo",
        requested_ref: "main", resolved_ref: "main", resolved_commit: "abc123", content_hash: "hash",
        cleanup_status: "succeeded", cleanup_error: null, created_at: "now", updated_at: "now",
      },
      latest_index: null,
    };
  });
  assert.equal(fetchedAttempt, "attempt-1");
  assert.equal(response?.attempt.status, "ready");
  assert.equal(response?.project.project_id, "project-1");
  assert.equal(response?.source_url, "https://github.com/org/repo");
});

test("polls job through terminal state with persisted tracking and clears it", async () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
  let reads = 0;
  const result = await waitForJob(
    makeJob("queued"),
    async () => ++reads === 1 ? makeJob("running", "indexing") : makeJob("succeeded", "succeeded"),
    new AbortController().signal,
    () => undefined,
    { initialDelayMs: 0, maxDelayMs: 0, storage },
  );
  assert.equal(result.status, "succeeded");
  assert.deepEqual(loadTrackedJobs(storage), []);
  trackJob(makeJob("running", "indexing"), storage);
  assert.equal(loadTrackedJobs(storage)[0]?.jobId, "job-1");
  clearTrackedJob("job-1", storage);
  assert.deepEqual(loadTrackedJobs(storage), []);
});

test("job polling keeps trying after a transient status request error", async () => {
  let reads = 0;
  const job = await waitForJob(
    makeJob("queued"),
    async () => {
      reads += 1;
      if (reads === 1) throw new Error("temporary network failure");
      return makeJob("succeeded", "succeeded");
    },
    new AbortController().signal,
    () => undefined,
    { initialDelayMs: 0, maxDelayMs: 0, storage: { getItem: () => "[]", setItem: () => undefined } },
  );
  assert.equal(job.status, "succeeded");
  assert.equal(reads, 2);
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

test("explains GitHub index timeouts with the server setting", () => {
  const message = githubImportErrorMessage("Indexing timed out; raise CODETRAIL_INDEX_TIMEOUT", "index_timeout");
  assert.match(message, /^仓库索引构建超时/);
  assert.match(message, /CODETRAIL_INDEX_TIMEOUT/);
  assert.match(message, /Indexing timed out/);
});
