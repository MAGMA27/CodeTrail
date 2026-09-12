"""Bounded, persistence-backed SSE delivery for public Run events."""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from typing import Final

from .contracts import Event, RunStatus, SseEventFrame, SseStreamEnd, SseStreamEndFrame


_DEFAULT_MAX_CONNECTIONS: Final = 8
_DEFAULT_MAX_PER_RUN: Final = 2
_DEFAULT_BATCH_SIZE: Final = 20
_DEFAULT_POLL_INTERVAL_SECONDS: Final = 0.1
_DEFAULT_IDLE_HEARTBEAT_SECONDS: Final = 10.0
_DEFAULT_MAX_LIFETIME_SECONDS: Final = 300.0
_DEFAULT_MAX_EVENT_BYTES: Final = 256_000


class SseCursorError(ValueError):
    """The requested SSE cursor is not valid for the selected Run."""


class SseCapacityError(RuntimeError):
    """The process-local SSE subscription capacity has been reached."""


def _positive_int(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class RunEventStreamConfig:
    """Finite limits for one process-local Run event stream service."""

    max_connections: int = _DEFAULT_MAX_CONNECTIONS
    max_per_run: int = _DEFAULT_MAX_PER_RUN
    batch_size: int = _DEFAULT_BATCH_SIZE
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS
    idle_heartbeat_seconds: float = _DEFAULT_IDLE_HEARTBEAT_SECONDS
    max_lifetime_seconds: float = _DEFAULT_MAX_LIFETIME_SECONDS
    max_event_bytes: int = _DEFAULT_MAX_EVENT_BYTES

    def __post_init__(self) -> None:
        _positive_int(self.max_connections, "SSE max_connections")
        _positive_int(self.max_per_run, "SSE max_per_run")
        if self.batch_size < 1 or self.batch_size > 100:
            raise ValueError("SSE batch_size must be between 1 and 100")
        _positive_float(self.poll_interval_seconds, "SSE poll_interval_seconds")
        _positive_float(self.idle_heartbeat_seconds, "SSE idle_heartbeat_seconds")
        _positive_float(self.max_lifetime_seconds, "SSE max_lifetime_seconds")
        _positive_int(self.max_event_bytes, "SSE max_event_bytes")
        if self.idle_heartbeat_seconds < self.poll_interval_seconds:
            raise ValueError("SSE idle heartbeat must not be shorter than its poll interval")
        if self.max_lifetime_seconds < self.poll_interval_seconds:
            raise ValueError("SSE max lifetime must not be shorter than its poll interval")

    @classmethod
    def from_environment(cls) -> RunEventStreamConfig:
        return cls(
            max_connections=_env_int("CODETRAIL_RUN_SSE_MAX_CONNECTIONS", _DEFAULT_MAX_CONNECTIONS),
            max_per_run=_env_int("CODETRAIL_RUN_SSE_MAX_PER_RUN", _DEFAULT_MAX_PER_RUN),
            batch_size=_env_int("CODETRAIL_RUN_SSE_BATCH_SIZE", _DEFAULT_BATCH_SIZE),
            poll_interval_seconds=_env_float(
                "CODETRAIL_RUN_SSE_POLL_INTERVAL_SECONDS",
                _DEFAULT_POLL_INTERVAL_SECONDS,
            ),
            idle_heartbeat_seconds=_env_float(
                "CODETRAIL_RUN_SSE_IDLE_HEARTBEAT_SECONDS",
                _DEFAULT_IDLE_HEARTBEAT_SECONDS,
            ),
            max_lifetime_seconds=_env_float(
                "CODETRAIL_RUN_SSE_MAX_LIFETIME_SECONDS",
                _DEFAULT_MAX_LIFETIME_SECONDS,
            ),
            max_event_bytes=_env_int("CODETRAIL_RUN_SSE_MAX_EVENT_BYTES", _DEFAULT_MAX_EVENT_BYTES),
        )


@dataclass(frozen=True)
class RunEventStreamState:
    total: int
    per_run: dict[str, int]


class RunEventStreamLease:
    """Idempotent ownership of one bounded subscription slot."""

    def __init__(self, registry: RunEventStreamRegistry, run_id: str) -> None:
        self._registry = registry
        self._run_id = run_id
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry._release(self._run_id)

    def __enter__(self) -> RunEventStreamLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class RunEventStreamRegistry:
    """Track active streams without queues, publishers, or unbounded buffers."""

    def __init__(self, config: RunEventStreamConfig | None = None) -> None:
        self.config = config or RunEventStreamConfig.from_environment()
        self._lock = threading.Lock()
        self._total = 0
        self._per_run: dict[str, int] = {}

    def acquire(self, run_id: str) -> RunEventStreamLease:
        with self._lock:
            run_count = self._per_run.get(run_id, 0)
            if self._total >= self.config.max_connections:
                raise SseCapacityError("Run event stream capacity is full")
            if run_count >= self.config.max_per_run:
                raise SseCapacityError("Run event stream capacity for this Run is full")
            self._total += 1
            self._per_run[run_id] = run_count + 1
        return RunEventStreamLease(self, run_id)

    def _release(self, run_id: str) -> None:
        with self._lock:
            if self._total > 0:
                self._total -= 1
            run_count = self._per_run.get(run_id, 0)
            if run_count <= 1:
                self._per_run.pop(run_id, None)
            else:
                self._per_run[run_id] = run_count - 1

    def state(self) -> RunEventStreamState:
        with self._lock:
            return RunEventStreamState(total=self._total, per_run=dict(self._per_run))


def parse_sse_event_id(value: str, run_id: str) -> int:
    """Parse the exact `{run_id}:{sequence}` cursor used by the wire protocol."""

    if not value or len(value) > 500:
        raise SseCursorError("Last-Event-ID must be a non-empty bounded cursor")
    prefix, separator, sequence_text = value.partition(":")
    if not separator or not prefix or prefix != run_id or not sequence_text.isdigit():
        raise SseCursorError("Last-Event-ID must match {run_id}:{sequence}")
    if len(sequence_text) > 10 or (len(sequence_text) > 1 and sequence_text.startswith("0")):
        raise SseCursorError("Last-Event-ID sequence is outside the allowed range")
    sequence = int(sequence_text)
    if sequence < 0:
        raise SseCursorError("Last-Event-ID sequence must not be negative")
    return sequence


def resolve_sse_cursor(
    run_id: str,
    *,
    after_sequence: int | None,
    last_event_id: str | None,
) -> int:
    """Resolve the initial query/header cursor and reject ambiguous resumes."""

    if after_sequence is not None and after_sequence < 0:
        raise SseCursorError("after_sequence must not be negative")
    header_sequence = None if last_event_id is None else parse_sse_event_id(last_event_id, run_id)
    if after_sequence is not None and header_sequence is not None and after_sequence != header_sequence:
        raise SseCursorError("after_sequence and Last-Event-ID must identify the same cursor")
    return header_sequence if header_sequence is not None else after_sequence or 0


def encode_sse_event(event: Event, *, max_bytes: int) -> bytes:
    frame = SseEventFrame(
        id=f"{event.run_id}:{event.sequence}",
        event=event.type,
        data=event,
    )
    return _encode_frame(frame.id, frame.event, frame.data.model_dump(mode="json"), max_bytes=max_bytes)


def encode_sse_stream_end(
    *,
    run_id: str,
    sequence: int,
    status: RunStatus,
    max_bytes: int,
) -> bytes:
    frame = SseStreamEndFrame(
        id=f"{run_id}:{sequence}",
        data=SseStreamEnd(run_id=run_id, sequence=sequence, reason="terminal", status=status),
    )
    return _encode_frame(frame.id, frame.event, frame.data.model_dump(mode="json"), max_bytes=max_bytes)


def encode_sse_comment(comment: str) -> bytes:
    safe_comment = comment.replace("\r", " ").replace("\n", " ")
    return f": {safe_comment}\n\n".encode("utf-8")


def _encode_frame(frame_id: str, event_name: str, data: object, *, max_bytes: int) -> bytes:
    encoded_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = f"id: {frame_id}\nevent: {event_name}\ndata: {encoded_data}\n\n".encode("utf-8")
    if len(payload) > max_bytes:
        raise ValueError("Run event SSE frame exceeds the configured byte limit")
    return payload
