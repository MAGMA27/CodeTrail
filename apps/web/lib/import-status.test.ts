import assert from "node:assert/strict";
import test from "node:test";

import { isImportStatusTerminal, waitForImportStatus } from "./import-status.ts";

type AttemptResult = { attempt: { status: "queued" | "acquiring" | "indexing" | "ready" | "failed" } };

const pending: AttemptResult = { attempt: { status: "queued" } };

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
