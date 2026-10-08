import type { ImportStatus } from "./api.ts";

export const IMPORT_STATUS_POLL_INTERVAL_MS = 750;
export const IMPORT_STATUS_MAX_POLLS = 40;
export const IMPORT_STATUS_TOTAL_TIMEOUT_MS = 35_000;

export type ImportSubmissionKey = { signature: string; key: string };

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
