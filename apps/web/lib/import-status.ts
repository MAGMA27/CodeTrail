import type { ImportStatus } from "./api.ts";

export const IMPORT_STATUS_POLL_INTERVAL_MS = 750;
export const IMPORT_STATUS_MAX_POLLS = 40;

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
  readStatus: () => Promise<T>,
  signal: AbortSignal,
  options: { intervalMs?: number; maxPolls?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? IMPORT_STATUS_POLL_INTERVAL_MS;
  const maxPolls = options.maxPolls ?? IMPORT_STATUS_MAX_POLLS;
  if (!Number.isFinite(intervalMs) || intervalMs < 0 || !Number.isInteger(maxPolls) || maxPolls < 0) {
    throw new Error("import status polling bounds are invalid");
  }

  let current = initial;
  for (let poll = 0; poll < maxPolls && !isImportStatusTerminal(current.attempt.status); poll += 1) {
    await waitForPoll(intervalMs, signal);
    current = await readStatus();
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
