import assert from "node:assert/strict";
import test from "node:test";

import {
  createRunEventStream,
  type RunEventSource,
  type RunEventStreamController,
  type RunEventStreamTimers,
  type RunStreamStatus,
} from "./run-event-stream.ts";
import type { RunEvent, RunStreamEnd, RunTerminalStatus } from "./api.ts";

const runId = "run-stream-test";

class FakeEventSource implements RunEventSource {
  readonly listeners = new Map<string, Array<(event: Event) => void>>();
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.();
  }

  error(): void {
    this.onerror?.();
  }

  emit(type: string, payload: unknown): void {
    const event = { data: JSON.stringify(payload) } as unknown as Event;
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

class FakeTimers implements RunEventStreamTimers {
  private nextId = 1;
  private callbacks = new Map<number, () => void>();

  setTimeout(callback: () => void, _delayMs: number): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  }

  clearTimeout(handle: number): void {
    this.callbacks.delete(handle);
  }

  runNext(): void {
    const next = this.callbacks.entries().next();
    assert.equal(next.done, false);
    const [id, callback] = next.value as [number, () => void];
    this.callbacks.delete(id);
    callback();
  }

  get pending(): number {
    return this.callbacks.size;
  }
}

function runEvent(sequence: number, type = "stage.started"): RunEvent {
  return {
    run_id: runId,
    sequence,
    type,
    timestamp: "2026-09-12T00:00:00Z",
    payload: {},
  };
}

function streamEnd(sequence: number, status: RunTerminalStatus): RunStreamEnd {
  return {
    schema_version: "0.1.0",
    run_id: runId,
    sequence,
    reason: "terminal",
    status,
  };
}

function harness(maxReconnectAttempts = 5) {
  const timers = new FakeTimers();
  const sources: FakeEventSource[] = [];
  const statuses: RunStreamStatus[] = [];
  const errors: Array<string | null> = [];
  const events: RunEvent[] = [];
  const terminals: Array<RunEvent | RunStreamEnd> = [];
  let fallbackCount = 0;
  const controller: RunEventStreamController = createRunEventStream({
    runId,
    afterSequence: 0,
    getUrl: (sequence) => `/events/stream?after_sequence=${sequence}`,
    createEventSource: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      return source;
    },
    timers,
    maxReconnectAttempts,
    reconnectDelaysMs: [1, 2, 3, 4, 5],
    onStatus: (status) => statuses.push(status),
    onError: (message) => errors.push(message),
    onEvent: (event) => events.push(event),
    onTerminal: (terminal) => terminals.push(terminal),
    onFallback: () => {
      fallbackCount += 1;
    },
  });
  return { controller, timers, sources, statuses, errors, events, terminals, get fallbackCount() { return fallbackCount; } };
}

test("accepts all terminal stream-end statuses without confusing event types and statuses", () => {
  for (const status of ["succeeded", "failed", "cancelled", "interrupted"] as const) {
    const state = harness();
    state.controller.start();
    state.sources[0].emit("run.stream.end", streamEnd(0, status));

    assert.deepEqual(state.statuses, ["connecting", "closed"]);
    assert.equal(state.terminals.length, 1);
    const terminal = state.terminals[0];
    assert.ok("status" in terminal);
    assert.equal(terminal.status, status);
    assert.equal(state.fallbackCount, 0);
  }
});

test("closes on a terminal public event and reports it as the terminal", () => {
  const state = harness();
  state.controller.start();
  state.sources[0].emit("run.failed", runEvent(1, "run.failed"));

  assert.deepEqual(state.events.map((event) => event.sequence), [1]);
  assert.deepEqual(state.statuses, ["connecting", "closed"]);
  assert.equal(state.terminals.length, 1);
  const terminal = state.terminals[0];
  assert.ok(terminal && "type" in terminal);
  assert.equal(terminal.type, "run.failed");
  assert.equal(state.fallbackCount, 0);
});

test("rejects nonterminal and foreign stream-end control frames", () => {
  const nonterminal = harness();
  nonterminal.controller.start();
  nonterminal.sources[0].emit("run.stream.end", {
    schema_version: "0.1.0",
    run_id: runId,
    sequence: 0,
    reason: "terminal",
    status: "running",
  });

  assert.equal(nonterminal.fallbackCount, 1);
  assert.equal(nonterminal.statuses.at(-1), "fallback");
  assert.equal(nonterminal.terminals.length, 0);

  const foreign = harness();
  foreign.controller.start();
  foreign.sources[0].emit("run.stream.end", {
    ...streamEnd(0, "succeeded"),
    run_id: "another-run",
  });

  assert.equal(foreign.fallbackCount, 1);
  assert.equal(foreign.statuses.at(-1), "fallback");
  assert.equal(foreign.terminals.length, 0);
});

test("counts repeated open-error cycles even though each connection opens", () => {
  const state = harness(3);
  state.controller.start();

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const source = state.sources.at(-1);
    assert.ok(source);
    source.open();
    source.error();
    if (attempt < 3) {
      assert.equal(state.controller.getReconnectAttempts(), attempt);
      state.timers.runNext();
    }
  }

  assert.equal(state.sources.length, 3);
  assert.equal(state.controller.getReconnectAttempts(), 3);
  assert.equal(state.fallbackCount, 1);
  assert.equal(state.timers.pending, 0);
  assert.equal(state.statuses.at(-1), "fallback");
  assert.ok(state.sources.every((source) => source.closed));
});

test("resets retry budget only after valid sequence progress and resumes from it", () => {
  const state = harness(2);
  state.controller.start();
  state.sources[0].open();
  state.sources[0].error();
  state.timers.runNext();

  assert.equal(state.sources[1].url, "/events/stream?after_sequence=0");
  state.sources[1].open();
  assert.equal(state.controller.getReconnectAttempts(), 1);
  state.sources[1].emit("stage.started", runEvent(1));
  assert.equal(state.controller.getReconnectAttempts(), 0);
  state.sources[1].error();
  assert.equal(state.fallbackCount, 0);
  state.timers.runNext();

  assert.equal(state.sources[2].url, "/events/stream?after_sequence=1");
  state.sources[2].emit("stage.started", runEvent(2));
  assert.deepEqual(state.events.map((event) => event.sequence), [1, 2]);
  assert.equal(state.fallbackCount, 0);
});

test("deduplicates handled events, rejects gaps, and ignores stale callbacks after close", () => {
  const state = harness();
  state.controller.start();
  const source = state.sources[0];
  source.emit("stage.started", runEvent(1));
  source.emit("stage.started", runEvent(1));
  assert.deepEqual(state.events.map((event) => event.sequence), [1]);

  source.emit("stage.started", runEvent(3));
  assert.equal(state.fallbackCount, 1);
  assert.equal(state.statuses.at(-1), "fallback");

  const stale = harness();
  stale.controller.start();
  stale.controller.close();
  stale.sources[0].open();
  stale.sources[0].emit("stage.started", runEvent(1));
  assert.deepEqual(stale.events, []);
  assert.equal(stale.fallbackCount, 0);
});
