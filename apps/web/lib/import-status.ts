import { isGitHubImportResponse, type GitHubImportResponse, type ImportAttemptResponse, type ImportStatus, type JobStatus, type JobStatusResponse } from "./api.ts";

export const IMPORT_STATUS_POLL_INTERVAL_MS = 750;
export const IMPORT_STATUS_MAX_POLLS = 40;
export const IMPORT_STATUS_TOTAL_TIMEOUT_MS = 35_000;
export const JOB_TRACKING_STORAGE_KEY = "codetrail.active-jobs.v1";
export const JOB_POLL_INITIAL_MS = 1_000;
export const JOB_POLL_MAX_MS = 5_000;

export type TrackedJob = { jobId: string; projectId: string | null; kind: JobStatusResponse["kind"] };

export function jobStatusLabel(status: JobStatus, phase: string): string {
  if (status === "queued") return "排队中";
  if (status === "running") {
    const labels: Record<string, string> = { resolving: "解析中", downloading: "下载中", acquiring: "下载中", indexing: "索引中", cleaning: "清理中" };
    return labels[phase] ?? "运行中";
  }
  return ({ retry_wait: "等待重试", succeeded: "已完成", failed: "失败", cancel_requested: "取消中", cancelled: "已取消", timed_out: "已超时" } as const)[status];
}

export function importStatusFromJob(job: JobStatusResponse): ImportStatus {
  if (job.status === "queued") return "queued";
  if (job.status === "running") return job.phase === "indexing" ? "indexing" : "acquiring";
  if (job.status === "retry_wait") return "retry_wait";
  if (job.status === "succeeded") return "ready";
  if (job.status === "cancel_requested") return "cancel_requested";
  if (job.status === "cancelled") return "cancelled";
  if (job.status === "timed_out") return "timed_out";
  return "failed";
}

export async function resolveGitHubImportJobResult(
  job: JobStatusResponse,
  readImportStatus: (attemptId: string) => Promise<ImportAttemptResponse>,
): Promise<GitHubImportResponse | null> {
  if (isGitHubImportResponse(job.result)) return job.result;
  const result = job.result;
  if (job.status !== "succeeded" || result?.truncated !== true ||
    typeof result.project_id !== "string" || typeof result.attempt_id !== "string") return null;
  const status = await readImportStatus(result.attempt_id);
  if (status.project.project_id !== result.project_id || status.attempt.attempt_id !== result.attempt_id ||
    status.attempt.status !== "ready" || status.attempt.source_kind !== "github" ||
    typeof status.attempt.source_url !== "string") return null;
  return {
    schema_version: status.schema_version,
    source_url: status.attempt.source_url,
    requested_ref: status.attempt.requested_ref,
    resolved_ref: status.attempt.resolved_ref,
    resolved_commit: status.attempt.resolved_commit,
    content_hash: status.attempt.content_hash,
    cleanup_status: status.attempt.cleanup_status,
    cleanup_error: status.attempt.cleanup_error,
    project: status.project,
    attempt: status.attempt,
    index: status.latest_index,
  };
}

export function jobErrorMessage(code: string | null, summary: string | null): string {
  const messages: Record<string, string> = {
    download_timeout: "GitHub 下载超时，可稍后重试或调整服务端下载时限。",
    index_timeout: "索引构建超时，可稍后重试或调整 CODETRAIL_INDEX_TIMEOUT。",
    rate_limited: "GitHub 请求受到限流，请稍后重试或配置 GitHub token。",
    budget_exceeded: "GitHub 限流等待预算已用尽，请稍后重试。",
    worker_lost: "后台工作进程中断，任务已按策略恢复或终止。",
    queue_wait_timeout: "任务等待队列超时，请稍后重新提交。",
    job_timeout: "任务执行超时，请稍后重试。",
    job_total_timeout: "任务总执行时间超限，请稍后重试。",
  };
  const localized = code ? messages[code] : undefined;
  return localized && summary ? `${localized}（${summary}）` : localized ?? summary ?? "任务失败。";
}

export function loadTrackedJobs(storage?: Pick<Storage, "getItem">): TrackedJob[] {
  try {
    const target = storage ?? window.localStorage;
    const value: unknown = JSON.parse(target.getItem(JOB_TRACKING_STORAGE_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is TrackedJob => typeof item === "object" && item !== null &&
      typeof item.jobId === "string" && (typeof item.projectId === "string" || item.projectId === null) &&
      ["github_import", "github_retry", "github_cleanup", "github_refresh", "project_index_refresh"].includes(item.kind));
  } catch { return []; }
}

export function trackJob(job: JobStatusResponse, storage?: Pick<Storage, "getItem" | "setItem">): void {
  try {
    const target = storage ?? window.localStorage;
    const jobs = loadTrackedJobs(target).filter((item) => item.jobId !== job.job_id);
    if (!["succeeded", "failed", "cancelled", "timed_out"].includes(job.status)) {
      jobs.push({ jobId: job.job_id, projectId: job.project_id, kind: job.kind });
    }
    target.setItem(JOB_TRACKING_STORAGE_KEY, JSON.stringify(jobs));
  } catch {
    // Storage can be disabled; keep polling for this page session.
  }
}

export function clearTrackedJob(jobId: string, storage?: Pick<Storage, "getItem" | "setItem">): void {
  try {
    const target = storage ?? window.localStorage;
    target.setItem(JOB_TRACKING_STORAGE_KEY, JSON.stringify(loadTrackedJobs(target).filter((item) => item.jobId !== jobId)));
  } catch {
    // Storage can be disabled; terminal status is still shown in memory.
  }
}

export async function waitForJob(
  initial: JobStatusResponse,
  readStatus: (job: JobStatusResponse, signal: AbortSignal) => Promise<JobStatusResponse>,
  signal: AbortSignal,
  onUpdate: (job: JobStatusResponse) => void = () => undefined,
  options: { initialDelayMs?: number; maxDelayMs?: number; storage?: Pick<Storage, "getItem" | "setItem"> } = {},
): Promise<JobStatusResponse> {
  const storage = options.storage;
  let job = initial;
  let delay = options.initialDelayMs ?? JOB_POLL_INITIAL_MS;
  const maxDelay = options.maxDelayMs ?? JOB_POLL_MAX_MS;
  trackJob(job, storage);
  onUpdate(job);
  while (!["succeeded", "failed", "cancelled", "timed_out"].includes(job.status)) {
    await waitForPoll(delay, signal);
    if (signal.aborted) throw new DOMException("The operation was aborted", "AbortError");
    try {
      job = await readStatus(job, signal);
    } catch (error) {
      if (signal.aborted || isJobNotFound(error)) throw error;
      delay = Math.min(maxDelay, Math.ceil(delay * 1.5));
      continue;
    }
    trackJob(job, storage);
    onUpdate(job);
    delay = Math.min(maxDelay, Math.ceil(delay * 1.5));
  }
  clearTrackedJob(job.job_id, storage);
  return job;
}

function isJobNotFound(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === 404;
}

export type ImportSubmissionKey = { signature: string; key: string };

/** Explain GitHub failures while preserving the API message for troubleshooting. */
export function githubImportErrorMessage(message: string, errorCode?: string): string {
  if (errorCode === "download_timeout") {
    return `GitHub 仓库归档下载超时，操作未完成。可能是网络较慢或仓库较大；可重试，或在 API 服务端调高 CODETRAIL_GITHUB_DOWNLOAD_TIMEOUT 后重启服务。（${message}）`;
  }
  if (errorCode === "index_timeout") {
    return `仓库索引构建超时，操作未完成。可重试，或在 API 服务端调高 CODETRAIL_INDEX_TIMEOUT 后重启服务。（${message}）`;
  }
  return /rate limit/i.test(message)
    ? `GitHub API 限流，导入未完成。可稍后重试，或在 API 服务端设置 CODETRAIL_GITHUB_TOKEN 提高限额。（${message}）`
    : message;
}

export function nextImportSubmissionKey(
  current: ImportSubmissionKey | null,
  signature: string,
  createKey: () => string,
): ImportSubmissionKey {
  return current?.signature === signature ? current : { signature, key: createKey() };
}

export function finishImportSubmissionKey(
  current: ImportSubmissionKey | null,
  uncertain: boolean,
): ImportSubmissionKey | null {
  return uncertain ? current : null;
}

const TERMINAL_IMPORT_STATUSES = new Set<ImportStatus>([
  "ready",
  "failed",
  "cancelled",
  "interrupted",
  "timed_out",
]);

export function isImportStatusTerminal(status: ImportStatus): boolean {
  return TERMINAL_IMPORT_STATUSES.has(status);
}

export async function waitForImportStatus<T extends { attempt: { status: ImportStatus } }>(
  initial: T,
  readStatus: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
  options: { intervalMs?: number; maxPolls?: number; timeoutMs?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? IMPORT_STATUS_POLL_INTERVAL_MS;
  const maxPolls = options.maxPolls ?? IMPORT_STATUS_MAX_POLLS;
  const timeoutMs = options.timeoutMs ?? IMPORT_STATUS_TOTAL_TIMEOUT_MS;
  if (
    !Number.isFinite(intervalMs) || intervalMs < 0 ||
    !Number.isInteger(maxPolls) || maxPolls < 0 ||
    !Number.isFinite(timeoutMs) || timeoutMs <= 0
  ) {
    throw new Error("import status polling bounds are invalid");
  }

  let current = initial;
  const deadline = Date.now() + timeoutMs;
  for (let poll = 0; poll < maxPolls && !isImportStatusTerminal(current.attempt.status); poll += 1) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
      break;
    }
    await waitForPoll(Math.min(intervalMs, remainingMs), signal);
    if (signal.aborted) {
      throw new DOMException("The operation was aborted", "AbortError");
    }
    const requestController = new AbortController();
    const abortRequest = () => requestController.abort(signal.reason);
    signal.addEventListener("abort", abortRequest, { once: true });
    const requestRemainingMs = deadline - Date.now();
    if (requestRemainingMs <= 0) {
      signal.removeEventListener("abort", abortRequest);
      break;
    }
    const timer = setTimeout(() => requestController.abort(), requestRemainingMs);
    try {
      current = await readStatus(requestController.signal);
    } catch (error) {
      if (requestController.signal.aborted && !signal.aborted) {
        break;
      }
      throw error;
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abortRequest);
    }
  }
  return current;
}

function waitForPoll(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(new DOMException("The operation was aborted", "AbortError"));
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, delayMs);
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new DOMException("The operation was aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}
