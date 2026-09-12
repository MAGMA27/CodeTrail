import {
  isRunEvent,
  isRunStreamEnd,
  type RunEvent,
  type RunStreamEnd,
  type RunTerminalStatus,
} from "./api.ts";

export type RunStreamStatus = "idle" | "connecting" | "connected" | "reconnecting" | "fallback" | "closed";

export const RUN_STREAM_MAX_RECONNECT_ATTEMPTS = 5;

const RUN_EVENT_TYPES = [
  "run.started",
  "stage.started",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "graph.updated",
  "finding.created",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
] as const;

const TERMINAL_RUN_EVENT_TYPES = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
]);

const TERMINAL_RUN_STATUSES = new Set<RunTerminalStatus>([
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
]);

const DEFAULT_RECONNECT_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000] as const;
const FALLBACK_MESSAGE = "实时事件流暂时不可用，已回退到有界轮询。";

export type RunEventSource = {
  addEventListener: (type: string, listener: (event: Event) => void) => void;
  close: () => void;
  onopen: (() => void) | null;
  onerror: (() => void) | null;
};

export type RunEventStreamTimers = {
  setTimeout: (callback: () => void, delayMs: number) => number;
  clearTimeout: (handle: number) => void;
};

export type RunEventStreamOptions = {
  runId: string;
  afterSequence: number;
  getUrl: (afterSequence: number) => string;
  createEventSource?: (url: string) => RunEventSource;
  timers?: RunEventStreamTimers;
  maxReconnectAttempts?: number;
  reconnectDelaysMs?: readonly number[];
  onStatus: (status: RunStreamStatus) => void;
  onError: (message: string | null) => void;
  onEvent: (event: RunEvent) => void;
  onTerminal: (terminal: RunEvent | RunStreamEnd) => void;
  onFallback: () => void;
};

export type RunEventStreamController = {
  start: () => void;
  close: () => void;
  getLastSequence: () => number;
  getReconnectAttempts: () => number;
};

const browserTimers: RunEventStreamTimers = {
  setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

function createBrowserEventSource(url: string): RunEventSource {
  if (typeof EventSource === "undefined") {
    throw new Error("EventSource is not available");
  }
  return new EventSource(url) as unknown as RunEventSource;
}

function readEventData(rawEvent: Event): unknown {
  const data = (rawEvent as MessageEvent<unknown>).data;
  if (typeof data !== "string") {
    throw new Error("SSE event data must be a JSON string");
  }
  return JSON.parse(data) as unknown;
}

export function createRunEventStream(options: RunEventStreamOptions): RunEventStreamController {
  const maxReconnectAttempts = options.maxReconnectAttempts ?? RUN_STREAM_MAX_RECONNECT_ATTEMPTS;
  const reconnectDelaysMs = options.reconnectDelaysMs ?? DEFAULT_RECONNECT_DELAYS_MS;
  if (!Number.isInteger(maxReconnectAttempts) || maxReconnectAttempts < 1) {
    throw new Error("maxReconnectAttempts must be a positive integer");
  }
  if (
    reconnectDelaysMs.length === 0 ||
    reconnectDelaysMs.some((delay) => !Number.isFinite(delay) || delay < 0)
  ) {
    throw new Error("reconnectDelaysMs must contain finite non-negative delays");
  }

  const timers = options.timers ?? browserTimers;
  const createEventSource = options.createEventSource ?? createBrowserEventSource;
  let currentSource: RunEventSource | null = null;
  let reconnectTimer: number | null = null;
  let lastSequence = options.afterSequence;
  let reconnectAttempts = 0;
  let started = false;
  let stopped = false;

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) {
      timers.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const closeCurrentSource = () => {
    currentSource?.close();
    currentSource = null;
  };

  const enterFallback = (message = FALLBACK_MESSAGE) => {
    if (stopped) {
      return;
    }
    stopped = true;
    clearReconnectTimer();
    closeCurrentSource();
    options.onStatus("fallback");
    options.onError(message);
    options.onFallback();
  };

  const finishTerminal = (terminal: RunEvent | RunStreamEnd) => {
    if (stopped) {
      return;
    }
    stopped = true;
    clearReconnectTimer();
    closeCurrentSource();
    options.onStatus("closed");
    options.onError(null);
    options.onTerminal(terminal);
  };

  const protocolFailure = (message: string) => {
    enterFallback(message);
  };

  const connect = () => {
    if (stopped) {
      return;
    }
    let source: RunEventSource;
    try {
      source = createEventSource(options.getUrl(lastSequence));
    } catch {
      enterFallback();
      return;
    }
    currentSource = source;
    const isCurrent = () => !stopped && currentSource === source;

    source.onopen = () => {
      if (!isCurrent()) {
        return;
      }
      // An open handshake is not progress. Keep the counter so repeated
      // open-error cycles still reach the bounded fallback.
      options.onStatus("connected");
      options.onError(null);
    };

    const handleEvent = (expectedType: string) => (rawEvent: Event) => {
      if (!isCurrent()) {
        return;
      }
      let parsed: unknown;
      try {
        parsed = readEventData(rawEvent);
      } catch {
        protocolFailure("实时事件流返回了无法解析的数据。");
        return;
      }
      if (!isRunEvent(parsed) || parsed.run_id !== options.runId || parsed.type !== expectedType) {
        protocolFailure("实时事件流返回了越界或不匹配的事件，已拒绝载入。");
        return;
      }
      if (parsed.sequence <= lastSequence) {
        return;
      }
      if (parsed.sequence !== lastSequence + 1) {
        protocolFailure("实时事件流出现了不连续的 sequence，已回退到轮询。");
        return;
      }
      lastSequence = parsed.sequence;
      // A valid new event is observable progress and starts a fresh retry
      // budget for a later transient disconnect.
      reconnectAttempts = 0;
      options.onEvent(parsed);
      if (TERMINAL_RUN_EVENT_TYPES.has(parsed.type)) {
        finishTerminal(parsed);
      }
    };

    for (const eventType of RUN_EVENT_TYPES) {
      source.addEventListener(eventType, handleEvent(eventType));
    }
    source.addEventListener("run.stream.end", (rawEvent: Event) => {
      if (!isCurrent()) {
        return;
      }
      let parsed: unknown;
      try {
        parsed = readEventData(rawEvent);
      } catch {
        protocolFailure("实时事件流的完成标记无法解析。");
        return;
      }
      if (
        !isRunStreamEnd(parsed) ||
        parsed.run_id !== options.runId ||
        parsed.sequence !== lastSequence ||
        !TERMINAL_RUN_STATUSES.has(parsed.status)
      ) {
        protocolFailure("实时事件流返回了无效的完成标记。");
        return;
      }
      finishTerminal(parsed);
    });
    source.onerror = () => {
      if (!isCurrent()) {
        return;
      }
      closeCurrentSource();
      reconnectAttempts += 1;
      if (reconnectAttempts >= maxReconnectAttempts) {
        enterFallback();
        return;
      }
      options.onStatus("reconnecting");
      options.onError(`实时事件流已断开，正在重连（${reconnectAttempts}/${maxReconnectAttempts}）。`);
      const delayIndex = Math.min(reconnectAttempts - 1, reconnectDelaysMs.length - 1);
      reconnectTimer = timers.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, reconnectDelaysMs[delayIndex]);
    };
  };

  return {
    start: () => {
      if (started || stopped) {
        return;
      }
      started = true;
      options.onStatus("connecting");
      options.onError(null);
      if (!options.createEventSource && typeof EventSource === "undefined") {
        enterFallback();
        return;
      }
      connect();
    },
    close: () => {
      if (stopped) {
        return;
      }
      stopped = true;
      clearReconnectTimer();
      closeCurrentSource();
    },
    getLastSequence: () => lastSequence,
    getReconnectAttempts: () => reconnectAttempts,
  };
}
