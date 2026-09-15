"""SQLite/FTS5 persistence and bounded queries for one CodeTrail index."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    CodeEdge,
    Event,
    Evidence,
    FeatureGeneration,
    GetNeighborsParams,
    GraphSnapshot,
    ImpactPath,
    IndexSnapshot,
    Neighbor,
    ReadSourceParams,
    Report,
    Run,
    RunInput,
    RunCancelledEvent,
    RunCancelledPayload,
    RunInterruptedEvent,
    RunInterruptedPayload,
    SearchCodeParams,
    SearchMatch,
    Session,
    SessionMessage,
    SourceLocation,
    Symbol,
    ToolRequest,
    UnresolvedReference,
    ToolResult,
)
from .indexing import (
    DefinitionCacheEntry,
    DefinitionIndex,
    SnapshotFile,
    SnapshotIssue,
    SourceSnapshot,
    deserialize_definition_cache_entry,
    serialize_definition_cache_entry,
)
from .relations import (
    RelationCallEvent,
    RelationFileFacts,
    RelationImportEvent,
    RelationIndex,
    RelationScopeFacts,
    RelationScopeEvent,
    deserialize_relation_file_facts,
    serialize_relation_file_facts,
)


_UNSET = object()
_FEATURE_HISTORY_BATCH_SIZE = 40
_FEATURE_HISTORY_SCAN_LIMIT = 200
_RUN_HISTORY_SCAN_LIMIT = 200
_RUN_EVENT_PAGE_SIZE = 100
_RUN_TOOL_RESULT_PAGE_SIZE = 40
logger = logging.getLogger(__name__)
_EVENT_ADAPTER = TypeAdapter(Event)
_TOOL_RESULT_ADAPTER = TypeAdapter(ToolResult)
_SEARCH_MAX_SECONDS = 0.5
_SEARCH_MAX_CANDIDATES = 256
_SEARCH_BODY_BATCH_SIZE = 32
_SEARCH_MAX_SOURCE_FILES = 32
_SEARCH_MAX_SOURCE_BYTES = 256 * 1024
_SEARCH_PROGRESS_OPS = 1_000


def _feature_generation_from_json(result_json: str) -> FeatureGeneration:
    """Validate a saved generation, filling only the known legacy usage shape.

    Early O2 rows recorded aggregate input/output characters but did not keep
    the per-model-call arrays. For the only recoverable legacy case (zero or
    one model call), the aggregate is the complete per-call value. This is a
    read-time, idempotent normalization: the database row is never rewritten,
    and all other generation, evidence, and snapshot fields remain validated
    by the current contract.
    """

    payload = json.loads(result_json)
    if isinstance(payload, dict):
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            model_calls = raw_usage.get("model_calls", 0)
            if (
                isinstance(model_calls, int)
                and not isinstance(model_calls, bool)
                and model_calls in {0, 1}
            ):
                usage = dict(raw_usage)
                if "model_input_chars" not in usage:
                    usage["model_input_chars"] = (
                        [] if model_calls == 0 else [usage.get("input_chars", 0)]
                    )
                if "model_output_chars" not in usage:
                    usage["model_output_chars"] = (
                        [] if model_calls == 0 else [usage.get("output_chars", 0)]
                    )
                payload = dict(payload)
                payload["usage"] = usage
    return FeatureGeneration.model_validate(payload)


class StorageError(RuntimeError):
    """Base error for local index storage and query failures."""


class StorageCapabilityError(StorageError):
    """The host SQLite build cannot provide a required capability."""


class StorageConflictError(StorageError):
    """An index ID already contains a different payload."""


class StorageNotFoundError(StorageError):
    """A requested index, file, symbol, or source range is absent."""


class StorageInputError(StorageError):
    """A storage or query input violates the bounded local contract."""


@dataclass(frozen=True)
class SymbolSearchResult:
    """A contract search match plus a bounded source-text snippet."""

    match: SearchMatch
    snippet: str

    @property
    def symbol(self) -> Symbol:
        return self.match.symbol


@dataclass(frozen=True)
class SearchQueryResult:
    """Bounded symbol search results with an explicit result cap."""

    matches: tuple[SymbolSearchResult, ...]
    truncated: bool
    truncation_reason: str | None


@dataclass(frozen=True)
class _SearchSource:
    """One query-local, line-split source file held within the search budget."""

    lines: tuple[str, ...]
    size: int


class _SearchBudget:
    """Fixed server-side limits shared by SQL reads and Python post-processing."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self.max_seconds = _SEARCH_MAX_SECONDS
        self.max_candidates = _SEARCH_MAX_CANDIDATES
        self.max_source_files = _SEARCH_MAX_SOURCE_FILES
        self.max_source_bytes = _SEARCH_MAX_SOURCE_BYTES
        self._clock = clock or time.monotonic
        self._deadline = self._clock() + self.max_seconds
        self.candidates_seen = 0
        self.source_files = 0
        self.source_bytes = 0
        self.reason: str | None = None

    @property
    def remaining_candidates(self) -> int:
        return max(0, self.max_candidates - self.candidates_seen)

    def check_time(self) -> bool:
        if self.reason is not None:
            return False
        if self._clock() >= self._deadline:
            self.reason = "search budget exceeded: max_seconds"
            return False
        return True

    def progress(self) -> int:
        return 0 if self.check_time() else 1

    def record_candidates(self, count: int) -> bool:
        if self.reason is not None:
            return False
        if count < 0 or self.candidates_seen + count > self.max_candidates:
            self.set_reason("search budget exceeded: max_candidates")
            return False
        self.candidates_seen += count
        return True

    def source_limit_reason(self, size: int) -> str | None:
        if self.source_files >= self.max_source_files:
            return "search budget exceeded: max_source_files"
        if self.source_bytes + size > self.max_source_bytes:
            return "search budget exceeded: max_source_bytes"
        return None

    def reserve_source(self, size: int) -> None:
        self.source_files += 1
        self.source_bytes += size

    def set_reason(self, reason: str | None) -> None:
        if self.reason is None and reason is not None:
            self.reason = reason


@dataclass(frozen=True)
class _SearchCandidate:
    """A candidate accepted before applying the public result cap."""

    symbol: Symbol
    priority: int
    body_score: float
    source_path: str | None


@dataclass(frozen=True)
class RelationCoverage:
    """Persisted coverage facts for one immutable relation index."""

    index_truncated: bool
    index_truncation_reason: str | None
    index_unresolved_truncated: bool | None
    index_unresolved_truncation_reason: str | None


@dataclass(frozen=True)
class NeighborQueryResult:
    """Bounded static neighbors and all nodes needed to render their edges."""

    index_id: str
    root: Symbol
    nodes: tuple[Symbol, ...]
    neighbors: tuple[Neighbor, ...]
    unresolved: tuple[UnresolvedReference, ...]
    truncated: bool
    truncation_reason: str | None
    index_truncated: bool
    index_truncation_reason: str | None
    index_unresolved_truncated: bool | None
    index_unresolved_truncation_reason: str | None


@dataclass(frozen=True)
class ImpactQueryResult:
    """Bounded static calls paths rooted at one exact symbol."""

    index_id: str
    root: Symbol
    nodes: tuple[Symbol, ...]
    edges: tuple[CodeEdge, ...]
    paths: tuple[ImpactPath, ...]
    unresolved: tuple[UnresolvedReference, ...]
    reachable_node_count: int
    truncated: bool
    truncation_reason: str | None
    index_truncated: bool
    index_truncation_reason: str | None
    index_unresolved_truncated: bool | None
    index_unresolved_truncation_reason: str | None


@dataclass(frozen=True)
class ProjectRecord:
    """Persisted project identity and its index associations."""

    project_id: str
    name: str
    repository_id: str
    created_at: datetime
    available_index_ids: tuple[str, ...]
    latest_index_id: str | None
    building_index_id: str | None


@dataclass(frozen=True)
class FeatureGenerationPage:
    """One stable page of saved feature generations."""

    generations: tuple[FeatureGeneration, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class SessionRecord:
    """Stored session metadata kept separate from its immutable index payload."""

    session_id: str
    project_id: str
    index_id: str
    feature_generation_id: str | None
    topic: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    idempotency_key: str | None
    request_fingerprint: str


@dataclass(frozen=True)
class SessionPage:
    """One stable page of project sessions."""

    sessions: tuple[SessionRecord, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class SessionMessagePage:
    """One ascending page of append-only session messages."""

    messages: tuple[SessionMessage, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class RunRecord:
    """Persisted Run metadata plus the complete immutable frozen input."""

    run: Run
    input: RunInput
    idempotency_key: str | None = None
    request_fingerprint: str = ""


@dataclass(frozen=True)
class RunPage:
    """One bounded newest-first Run history page."""

    runs: tuple[Run, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class RunEventPage:
    """One bounded ascending page of public Run events."""

    events: tuple[Event, ...]
    next_after_sequence: int | None
    has_more: bool


@dataclass(frozen=True)
class RunEventCursor:
    """The greatest persisted public event sequence for one Run."""

    latest_sequence: int


@dataclass(frozen=True)
class RunToolResultPage:
    """One bounded ascending page of persisted tool results."""

    results: tuple[ToolResult, ...]
    next_after_sequence: int | None
    has_more: bool


@dataclass(frozen=True)
class ManagedSourceRecord:
    """A persisted source registration, local or remote."""

    repository_id: str
    project_id: str
    name: str
    source_path: str
    normalized_source_path: str
    storage_path: str
    created_at: datetime
    source_kind: str = "local"
    source_url: str | None = None
    requested_ref: str | None = None
    resolved_commit: str | None = None
    content_hash: str | None = None
    cleanup_status: str = "not_required"
    cleanup_error: str | None = None
    temporary_path: str | None = None


@dataclass(frozen=True)
class ImportAttemptRecord:
    """A bounded, restart-aware import lifecycle record."""

    attempt_id: str
    project_id: str
    repository_id: str
    source_path: str
    normalized_source_path: str
    name: str
    revision: str
    dirty: bool
    idempotency_key: str | None
    status: str
    phase: str
    index_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    source_kind: str = "local"
    source_url: str | None = None
    requested_ref: str | None = None
    resolved_commit: str | None = None
    content_hash: str | None = None
    cleanup_status: str = "not_required"
    cleanup_error: str | None = None
    temporary_path: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_snapshots (
    index_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    dirty INTEGER NOT NULL CHECK (dirty IN (0, 1)),
    snapshot_hash TEXT NOT NULL,
    rules_version TEXT,
    configuration_hash TEXT,
    status TEXT NOT NULL CHECK (status IN ('ready', 'building', 'failed')),
    truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    truncation_reason TEXT,
    unresolved_truncated INTEGER CHECK (unresolved_truncated IN (0, 1)),
    unresolved_truncation_reason TEXT,
    payload_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    latest_index_id TEXT,
    building_index_id TEXT,
    FOREIGN KEY (latest_index_id) REFERENCES index_snapshots(index_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS project_indexes (
    project_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    associated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, index_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (index_id) REFERENCES index_snapshots(index_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS project_indexes_index_id
    ON project_indexes(index_id);

CREATE TABLE IF NOT EXISTS snapshot_files (
    index_id TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    content_hash TEXT NOT NULL,
    source_text TEXT NOT NULL,
    PRIMARY KEY (index_id, path),
    FOREIGN KEY (index_id) REFERENCES index_snapshots(index_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS snapshot_issues (
    index_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('snapshot', 'definitions')),
    path TEXT NOT NULL,
    issue_kind TEXT NOT NULL,
    message TEXT NOT NULL,
    size INTEGER,
    PRIMARY KEY (index_id, ordinal),
    FOREIGN KEY (index_id) REFERENCES index_snapshots(index_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS definition_cache (
    scope_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    definition_rules_version TEXT NOT NULL,
    python_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_bytes INTEGER NOT NULL CHECK (result_bytes >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        scope_id,
        repository_id,
        path,
        content_hash,
        definition_rules_version,
        python_version,
        configuration_hash
    )
);

CREATE INDEX IF NOT EXISTS definition_cache_scope
    ON definition_cache(scope_id, repository_id, created_at DESC);

CREATE TABLE IF NOT EXISTS relation_cache (
    scope_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    relation_rules_version TEXT NOT NULL,
    python_version TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_bytes INTEGER NOT NULL CHECK (result_bytes >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        scope_id,
        repository_id,
        path,
        content_hash,
        relation_rules_version,
        python_version,
        configuration_hash
    )
);

CREATE INDEX IF NOT EXISTS relation_cache_scope
    ON relation_cache(scope_id, repository_id, created_at DESC);

CREATE TABLE IF NOT EXISTS symbols (
    index_id TEXT NOT NULL,
    symbol_id TEXT NOT NULL,
    path TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line > 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    PRIMARY KEY (index_id, symbol_id),
    FOREIGN KEY (index_id) REFERENCES index_snapshots(index_id) ON DELETE CASCADE,
    UNIQUE (index_id, symbol_id)
);

CREATE TABLE IF NOT EXISTS static_edges (
    index_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('contains', 'imports', 'calls')),
    extraction_method TEXT NOT NULL CHECK (extraction_method IN ('ast', 'explicit_import', 'fixture')),
    source_path TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line > 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    PRIMARY KEY (index_id, edge_id),
    FOREIGN KEY (index_id, source_id) REFERENCES symbols(index_id, symbol_id) ON DELETE CASCADE,
    FOREIGN KEY (index_id, target_id) REFERENCES symbols(index_id, symbol_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS unresolved_references (
    index_id TEXT NOT NULL,
    unresolved_id TEXT NOT NULL,
    source_symbol_id TEXT NOT NULL,
    path TEXT NOT NULL,
    expression TEXT NOT NULL,
    reason TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line > 0),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    PRIMARY KEY (index_id, unresolved_id),
    FOREIGN KEY (index_id, source_symbol_id) REFERENCES symbols(index_id, symbol_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS unresolved_targets (
    index_id TEXT NOT NULL,
    unresolved_id TEXT NOT NULL,
    target_symbol_id TEXT NOT NULL,
    PRIMARY KEY (index_id, unresolved_id, target_symbol_id),
    FOREIGN KEY (index_id, unresolved_id) REFERENCES unresolved_references(index_id, unresolved_id) ON DELETE CASCADE,
    FOREIGN KEY (index_id, target_symbol_id) REFERENCES symbols(index_id, symbol_id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
    index_id UNINDEXED,
    symbol_id UNINDEXED,
    qualified_name,
    source_text,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS feature_generations (
    generation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    idempotency_key TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('fixture', 'real')),
    model TEXT,
    prompt_version TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'partial', 'insufficient_evidence', 'failed')),
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    FOREIGN KEY (index_id) REFERENCES index_snapshots(index_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS feature_generations_idempotency
    ON feature_generations(project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS feature_generations_cache
    ON feature_generations(project_id, index_id, cache_key, status, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    feature_generation_id TEXT,
    topic TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL,
    FOREIGN KEY (project_id, index_id) REFERENCES project_indexes(project_id, index_id) ON DELETE CASCADE,
    FOREIGN KEY (feature_generation_id) REFERENCES feature_generations(generation_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS sessions_idempotency
    ON sessions(project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS sessions_project_activity
    ON sessions(project_id, updated_at, session_id);

CREATE TABLE IF NOT EXISTS session_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    source TEXT NOT NULL CHECK (source IN ('human', 'agent')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    UNIQUE (session_id, sequence)
);

CREATE UNIQUE INDEX IF NOT EXISTS session_messages_idempotency
    ON session_messages(session_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS session_messages_order
    ON session_messages(session_id, sequence);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL,
    report_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_json TEXT NOT NULL,
    FOREIGN KEY (project_id, index_id) REFERENCES project_indexes(project_id, index_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS runs_idempotency
    ON runs(project_id, session_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS runs_active_session
    ON runs(session_id)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS runs_session_activity
    ON runs(project_id, session_id, created_at DESC, run_id DESC);

CREATE TABLE IF NOT EXISTS run_inputs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    input_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, index_id) REFERENCES project_indexes(project_id, index_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_tool_results (
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    completed_sequence INTEGER NOT NULL CHECK (completed_sequence > 0),
    kind TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id),
    UNIQUE (run_id, completed_sequence),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS run_tool_results_order
    ON run_tool_results(run_id, completed_sequence);

CREATE TABLE IF NOT EXISTS run_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    index_id TEXT NOT NULL,
    report_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, index_id) REFERENCES project_indexes(project_id, index_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
"""


_MANAGEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS managed_sources (
    repository_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    normalized_source_path TEXT NOT NULL UNIQUE,
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'local',
    source_url TEXT,
    requested_ref TEXT,
    resolved_commit TEXT,
    content_hash TEXT,
    cleanup_status TEXT NOT NULL DEFAULT 'not_required',
    cleanup_error TEXT,
    temporary_path TEXT
);

CREATE TABLE IF NOT EXISTS import_attempts (
    attempt_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    normalized_source_path TEXT NOT NULL,
    name TEXT NOT NULL,
    revision TEXT NOT NULL,
    dirty INTEGER NOT NULL CHECK (dirty IN (0, 1)),
    idempotency_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'acquiring', 'indexing', 'ready', 'failed', 'cancelled', 'interrupted')),
    phase TEXT NOT NULL CHECK (phase IN ('queued', 'acquiring', 'indexing', 'ready', 'failed', 'cancelled', 'interrupted')),
    index_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'local',
    source_url TEXT,
    requested_ref TEXT,
    resolved_commit TEXT,
    content_hash TEXT,
    cleanup_status TEXT NOT NULL DEFAULT 'not_required',
    cleanup_error TEXT,
    temporary_path TEXT
);

DROP INDEX IF EXISTS import_attempts_idempotency;

CREATE UNIQUE INDEX IF NOT EXISTS import_attempts_idempotency
    ON import_attempts(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

DROP INDEX IF EXISTS import_attempts_active_source;

CREATE UNIQUE INDEX IF NOT EXISTS import_attempts_active_source
    ON import_attempts(normalized_source_path)
    WHERE status IN ('queued', 'acquiring', 'indexing');

CREATE INDEX IF NOT EXISTS import_attempts_project
    ON import_attempts(project_id, created_at, attempt_id);
"""


class ManagementStore:
    """Persist dynamic source registrations and import attempts in service data."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.executescript(_MANAGEMENT_SCHEMA)
                self._ensure_columns(connection)
        except sqlite3.Error as exc:
            raise StorageError(f"could not initialize management store: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            isolation_level=None,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection) -> None:
        """Migrate W2 management databases without rewriting user state."""

        additions = {
            "managed_sources": {
                "source_kind": "TEXT NOT NULL DEFAULT 'local'",
                "source_url": "TEXT",
                "requested_ref": "TEXT",
                "resolved_commit": "TEXT",
                "content_hash": "TEXT",
                "cleanup_status": "TEXT NOT NULL DEFAULT 'not_required'",
                "cleanup_error": "TEXT",
                "temporary_path": "TEXT",
            },
            "import_attempts": {
                "source_kind": "TEXT NOT NULL DEFAULT 'local'",
                "source_url": "TEXT",
                "requested_ref": "TEXT",
                "resolved_commit": "TEXT",
                "content_hash": "TEXT",
                "cleanup_status": "TEXT NOT NULL DEFAULT 'not_required'",
                "cleanup_error": "TEXT",
                "temporary_path": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def list_sources(self) -> tuple[ManagedSourceRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_sources ORDER BY created_at COLLATE BINARY, repository_id COLLATE BINARY"
            ).fetchall()
        return tuple(_managed_source_from_row(row) for row in rows)

    def find_source(self, normalized_source_path: str) -> ManagedSourceRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_sources WHERE normalized_source_path = ?",
                (normalized_source_path,),
            ).fetchone()
        return None if row is None else _managed_source_from_row(row)

    def load_source(self, repository_id: str) -> ManagedSourceRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_sources WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"managed source {repository_id!r} was not found")
        return _managed_source_from_row(row)

    def register_source(
        self,
        *,
        repository_id: str,
        project_id: str,
        name: str,
        source_path: str,
        normalized_source_path: str,
        storage_path: str,
        created_at: datetime,
        source_kind: str = "local",
        source_url: str | None = None,
        requested_ref: str | None = None,
        resolved_commit: str | None = None,
        content_hash: str | None = None,
        cleanup_status: str = "not_required",
        cleanup_error: str | None = None,
        temporary_path: str | None = None,
    ) -> ManagedSourceRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO managed_sources "
                "(repository_id, project_id, name, source_path, normalized_source_path, storage_path, created_at, "
                "source_kind, source_url, requested_ref, resolved_commit, content_hash, cleanup_status, cleanup_error, temporary_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    repository_id,
                    project_id,
                    name,
                    source_path,
                    normalized_source_path,
                    storage_path,
                    _datetime_text(created_at),
                    source_kind,
                    source_url,
                    requested_ref,
                    resolved_commit,
                    content_hash,
                    cleanup_status,
                    cleanup_error,
                    temporary_path,
                ),
            )
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            existing = self.find_source(normalized_source_path)
            if existing is not None:
                return existing
            raise StorageConflictError("managed source registration conflicts with existing data") from exc
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not register managed source: {exc}") from exc
        finally:
            connection.close()
        return self.load_source(repository_id)

    def create_attempt(
        self,
        *,
        attempt_id: str,
        project_id: str,
        repository_id: str,
        source_path: str,
        normalized_source_path: str,
        name: str,
        revision: str,
        dirty: bool,
        idempotency_key: str | None,
        created_at: datetime,
        source_kind: str = "local",
        source_url: str | None = None,
        requested_ref: str | None = None,
        resolved_commit: str | None = None,
        content_hash: str | None = None,
        cleanup_status: str = "not_required",
        cleanup_error: str | None = None,
        temporary_path: str | None = None,
    ) -> ImportAttemptRecord:
        timestamp = _datetime_text(created_at)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO import_attempts "
                    "(attempt_id, project_id, repository_id, source_path, normalized_source_path, name, revision, dirty, "
                    "idempotency_key, status, phase, created_at, updated_at, source_kind, source_url, requested_ref, "
                    "resolved_commit, content_hash, cleanup_status, cleanup_error, temporary_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id,
                        project_id,
                        repository_id,
                        source_path,
                        normalized_source_path,
                        name,
                        revision,
                        int(dirty),
                        idempotency_key,
                        timestamp,
                        timestamp,
                        source_kind,
                        source_url,
                        requested_ref,
                        resolved_commit,
                        content_hash,
                        cleanup_status,
                        cleanup_error,
                        temporary_path,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageConflictError("import attempt conflicts with existing data") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"could not create import attempt: {exc}") from exc
        return self.load_attempt(attempt_id)

    def load_attempt(self, attempt_id: str) -> ImportAttemptRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"import attempt {attempt_id!r} was not found")
        return _import_attempt_from_row(row)

    def find_attempt_by_idempotency(
        self,
        idempotency_key: str,
    ) -> ImportAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else _import_attempt_from_row(row)

    def find_active_attempt(self, normalized_source_path: str) -> ImportAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_attempts WHERE normalized_source_path = ? "
                "AND status IN ('queued', 'acquiring', 'indexing') "
                "ORDER BY created_at COLLATE BINARY, attempt_id COLLATE BINARY LIMIT 1",
                (normalized_source_path,),
            ).fetchone()
        return None if row is None else _import_attempt_from_row(row)

    def find_attempt_by_commit(
        self,
        normalized_source_path: str,
        resolved_commit: str,
    ) -> ImportAttemptRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_attempts WHERE normalized_source_path = ? AND resolved_commit = ? "
                "ORDER BY created_at COLLATE BINARY DESC, attempt_id COLLATE BINARY DESC LIMIT 1",
                (normalized_source_path, resolved_commit),
            ).fetchone()
        return None if row is None else _import_attempt_from_row(row)

    def update_source(
        self,
        repository_id: str,
        *,
        source_path: str | None = None,
        source_url: str | None | object = _UNSET,
        requested_ref: str | None | object = _UNSET,
        resolved_commit: str | None | object = _UNSET,
        content_hash: str | None | object = _UNSET,
        cleanup_status: str | object = _UNSET,
        cleanup_error: str | None | object = _UNSET,
        temporary_path: str | None | object = _UNSET,
    ) -> ManagedSourceRecord:
        updates: list[str] = []
        values: list[object] = []
        if source_path is not None:
            updates.append("source_path = ?")
            values.append(source_path)
        for column, value in (
            ("source_url", source_url),
            ("requested_ref", requested_ref),
            ("resolved_commit", resolved_commit),
            ("content_hash", content_hash),
            ("cleanup_status", cleanup_status),
            ("cleanup_error", cleanup_error),
            ("temporary_path", temporary_path),
        ):
            if value is not _UNSET:
                updates.append(f"{column} = ?")
                values.append(value)
        if updates:
            values.append(repository_id)
            try:
                with self._connect() as connection:
                    cursor = connection.execute(
                        f"UPDATE managed_sources SET {', '.join(updates)} WHERE repository_id = ?",
                        values,
                    )
                    if cursor.rowcount != 1:
                        raise StorageNotFoundError(f"managed source {repository_id!r} was not found")
            except StorageNotFoundError:
                raise
            except sqlite3.Error as exc:
                raise StorageError(f"could not update managed source: {exc}") from exc
        return self.load_source(repository_id)

    def update_attempt_metadata(
        self,
        attempt_id: str,
        *,
        source_url: str | None | object = _UNSET,
        requested_ref: str | None | object = _UNSET,
        resolved_commit: str | None | object = _UNSET,
        content_hash: str | None | object = _UNSET,
        cleanup_status: str | object = _UNSET,
        cleanup_error: str | None | object = _UNSET,
        temporary_path: str | None | object = _UNSET,
    ) -> ImportAttemptRecord:
        updates: list[str] = []
        values: list[object] = []
        for column, value in (
            ("source_url", source_url),
            ("requested_ref", requested_ref),
            ("resolved_commit", resolved_commit),
            ("content_hash", content_hash),
            ("cleanup_status", cleanup_status),
            ("cleanup_error", cleanup_error),
            ("temporary_path", temporary_path),
        ):
            if value is not _UNSET:
                updates.append(f"{column} = ?")
                values.append(value)
        if updates:
            updates.append("updated_at = ?")
            values.append(_datetime_text(datetime.now(timezone.utc)))
            values.append(attempt_id)
            try:
                with self._connect() as connection:
                    cursor = connection.execute(
                        f"UPDATE import_attempts SET {', '.join(updates)} WHERE attempt_id = ?",
                        values,
                    )
                    if cursor.rowcount != 1:
                        raise StorageNotFoundError(f"import attempt {attempt_id!r} was not found")
            except StorageNotFoundError:
                raise
            except sqlite3.Error as exc:
                raise StorageError(f"could not update import attempt metadata: {exc}") from exc
        return self.load_attempt(attempt_id)

    def update_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        phase: str,
        index_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        updated_at: datetime | None = None,
    ) -> ImportAttemptRecord:
        timestamp = _datetime_text(updated_at or datetime.now(timezone.utc))
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE import_attempts SET status = ?, phase = ?, index_id = ?, error_code = ?, "
                    "error_message = ?, updated_at = ? WHERE attempt_id = ?",
                    (status, phase, index_id, error_code, error_message, timestamp, attempt_id),
                )
                if cursor.rowcount != 1:
                    raise StorageNotFoundError(f"import attempt {attempt_id!r} was not found")
        except StorageNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise StorageError(f"could not update import attempt: {exc}") from exc
        return self.load_attempt(attempt_id)

    def mark_active_attempts_interrupted(self) -> int:
        timestamp = _datetime_text(datetime.now(timezone.utc))
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE import_attempts SET status = 'interrupted', phase = 'interrupted', "
                    "error_code = 'interrupted', error_message = 'import was interrupted by a service restart', updated_at = ? "
                    "WHERE status IN ('queued', 'acquiring', 'indexing')",
                    (timestamp,),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise StorageError(f"could not recover import attempts: {exc}") from exc


class IndexStore:
    """A small transactional SQLite store for immutable CodeTrail indexes."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(
                str(self.database_path),
                isolation_level=None,
                timeout=5.0,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._check_fts5()
            self._connection.executescript(_SCHEMA)
            self._ensure_columns()
        except sqlite3.Error as exc:
            self.close()
            if "fts5" in str(exc).casefold():
                raise StorageCapabilityError("SQLite FTS5 is not available") from exc
            raise StorageError(f"could not initialize SQLite store: {exc}") from exc

    @classmethod
    def for_repository(cls, repository_root: str | Path) -> IndexStore:
        """Use the ignored local runtime directory for a repository."""

        root = Path(repository_root)
        return cls(root / ".codetrail" / "index.sqlite3")

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for local diagnostics and controlled tests."""

        connection = getattr(self, "_connection", None)
        if connection is None:
            raise StorageError("SQLite store is closed")
        return connection

    @property
    def fts5_available(self) -> bool:
        return True

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None  # type: ignore[assignment]

    def _ensure_columns(self) -> None:
        """Migrate legacy index databases without rewriting immutable rows."""

        existing = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(index_snapshots)").fetchall()
        }
        additions = {
            "unresolved_truncated": "INTEGER CHECK (unresolved_truncated IN (0, 1))",
            "unresolved_truncation_reason": "TEXT",
            "rules_version": "TEXT",
            "configuration_hash": "TEXT",
        }
        for column, definition in additions.items():
            if column not in existing:
                self._connection.execute(
                    f"ALTER TABLE index_snapshots ADD COLUMN {column} {definition}"
                )

    def __enter__(self) -> IndexStore:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def has_index(self, index_id: str) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM index_snapshots WHERE index_id = ?",
            (index_id,),
        ).fetchone() is not None

    def has_any_index(self) -> bool:
        return self._connection.execute("SELECT 1 FROM index_snapshots LIMIT 1").fetchone() is not None

    def create_project(
        self,
        project_id: str,
        name: str,
        repository_id: str,
        created_at: datetime,
    ) -> ProjectRecord:
        """Persist one project without implicitly claiming existing indexes."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT INTO projects (project_id, name, repository_id, created_at) VALUES (?, ?, ?, ?)",
                (project_id, name, repository_id, _datetime_text(created_at)),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError(f"project_id {project_id!r} already exists") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not create project: {exc}") from exc
        return self.load_project(project_id)

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        rows = self._connection.execute(
            "SELECT project_id, name, repository_id, created_at, latest_index_id, building_index_id "
            "FROM projects ORDER BY created_at COLLATE BINARY, project_id COLLATE BINARY"
        ).fetchall()
        return tuple(self._project_from_row(row) for row in rows)

    def load_project(self, project_id: str) -> ProjectRecord:
        row = self._connection.execute(
            "SELECT project_id, name, repository_id, created_at, latest_index_id, building_index_id "
            "FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"project {project_id!r} was not found")
        return self._project_from_row(row)

    def ensure_legacy_project(
        self,
        project_id: str,
        name: str,
        repository_id: str,
        created_at: datetime,
    ) -> ProjectRecord | None:
        """Associate pre-project indexes with one explicit repository project."""

        if not self.has_any_index():
            return None
        existing = self._connection.execute(
            "SELECT 1 FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        unowned = self._connection.execute(
            "SELECT 1 FROM index_snapshots AS i "
            "LEFT JOIN project_indexes AS pi ON pi.index_id = i.index_id "
            "WHERE i.repository_id = ? AND pi.index_id IS NULL LIMIT 1",
            (repository_id,),
        ).fetchone()
        if existing is None and unowned is None:
            return None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO projects (project_id, name, repository_id, created_at) VALUES (?, ?, ?, ?)",
                    (project_id, name, repository_id, _datetime_text(created_at)),
                )
            rows = self._connection.execute(
                "SELECT i.index_id FROM index_snapshots AS i "
                "LEFT JOIN project_indexes AS pi ON pi.index_id = i.index_id "
                "WHERE i.repository_id = ? AND pi.index_id IS NULL ORDER BY i.rowid",
                (repository_id,),
            ).fetchall()
            association_time = _datetime_text(datetime.now(timezone.utc))
            for row in rows:
                self._connection.execute(
                    "INSERT INTO project_indexes (project_id, index_id, associated_at) VALUES (?, ?, ?)",
                    (project_id, row["index_id"], association_time),
                )
            latest = self._connection.execute(
                "SELECT index_id FROM project_indexes WHERE project_id = ? "
                "ORDER BY associated_at DESC, index_id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if latest is not None:
                self._connection.execute(
                    "UPDATE projects SET latest_index_id = ? WHERE project_id = ?",
                    (latest["index_id"], project_id),
                )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("legacy indexes could not be associated with a project") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not migrate legacy project indexes: {exc}") from exc
        return self.load_project(project_id)

    def list_project_indexes(self, project_id: str) -> tuple[IndexSnapshot, ...]:
        self.load_project(project_id)
        rows = self._connection.execute(
            "SELECT i.* FROM index_snapshots AS i "
            "JOIN project_indexes AS pi ON pi.index_id = i.index_id "
            "WHERE pi.project_id = ? ORDER BY pi.associated_at DESC, i.index_id DESC",
            (project_id,),
        ).fetchall()
        return tuple(_index_from_row(row) for row in rows)

    def project_has_index(self, project_id: str, index_id: str) -> bool:
        self.load_project(project_id)
        return self._connection.execute(
            "SELECT 1 FROM project_indexes WHERE project_id = ? AND index_id = ?",
            (project_id, index_id),
        ).fetchone() is not None

    def set_project_building(self, project_id: str, building_index_id: str) -> None:
        self.load_project(project_id)
        cursor = self._connection.execute(
            "UPDATE projects SET building_index_id = ? WHERE project_id = ?",
            (building_index_id, project_id),
        )
        if cursor.rowcount != 1:
            raise StorageNotFoundError(f"project {project_id!r} was not found")

    def finish_project_index(
        self,
        project_id: str,
        index_id: str,
        *,
        expected_latest_index_id: str | None = None,
    ) -> None:
        project = self.load_project(project_id)
        index_row = self._require_index(index_id)
        if index_row["repository_id"] != project.repository_id:
            raise StorageInputError("project and index repository IDs do not match")
        if index_row["status"] != "ready":
            raise StorageInputError("only ready indexes can be associated with a project")
        owner = self._connection.execute(
            "SELECT project_id FROM project_indexes WHERE index_id = ?",
            (index_id,),
        ).fetchone()
        if owner is not None and owner["project_id"] != project_id:
            raise StorageConflictError("index already belongs to another project")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current_latest = self._connection.execute(
                "SELECT latest_index_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if current_latest is None:
                self._connection.execute("ROLLBACK")
                raise StorageNotFoundError(f"project {project_id!r} was not found")
            if (
                expected_latest_index_id is not None
                and current_latest["latest_index_id"] != expected_latest_index_id
            ):
                self._connection.execute("ROLLBACK")
                raise StorageConflictError("project latest index changed during the build")
            self._connection.execute(
                "INSERT OR IGNORE INTO project_indexes (project_id, index_id, associated_at) VALUES (?, ?, ?)",
                (project_id, index_id, _datetime_text(datetime.now(timezone.utc))),
            )
            self._connection.execute(
                "UPDATE projects SET latest_index_id = ?, building_index_id = NULL WHERE project_id = ?",
                (index_id, project_id),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("index could not be associated with the project") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not finish project index: {exc}") from exc

    def clear_project_building(self, project_id: str) -> None:
        self._connection.execute(
            "UPDATE projects SET building_index_id = NULL WHERE project_id = ?",
            (project_id,),
        )

    def recover_building_projects(self) -> None:
        """Clear markers left by a synchronous build interrupted by a restart."""

        self._connection.execute("UPDATE projects SET building_index_id = NULL")

    def write_index(self, relation: RelationIndex) -> None:
        """Write one complete relation index atomically and idempotently."""

        self._validate_relation(relation)
        index = relation.snapshot.index
        payload_hash = _payload_hash(relation)
        existing = self._connection.execute(
            "SELECT payload_hash FROM index_snapshots WHERE index_id = ?",
            (index.index_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] == payload_hash:
                return
            raise StorageConflictError(
                f"index_id {index.index_id!r} already contains a different payload"
            )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT payload_hash FROM index_snapshots WHERE index_id = ?",
                (index.index_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] == payload_hash:
                    self._connection.execute("COMMIT")
                    return
                raise StorageConflictError(
                    f"index_id {index.index_id!r} already contains a different payload"
                )
            self._insert_index(relation, payload_hash)
            self._connection.execute("COMMIT")
        except Exception as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, StorageError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise StorageError(f"could not write index: {exc}") from exc
            raise

    def load_index(self, index_id: str) -> RelationIndex:
        """Reload an index, including source text, after the database is reopened."""

        index_row = self._require_index(index_id)
        index = _index_from_row(index_row)
        file_rows = self._connection.execute(
            "SELECT path, size, content_hash, source_text FROM snapshot_files "
            "WHERE index_id = ? ORDER BY path COLLATE BINARY",
            (index_id,),
        ).fetchall()
        files = tuple(
            SnapshotFile(
                path=row["path"],
                size=row["size"],
                content_hash=row["content_hash"],
                text=row["source_text"],
            )
            for row in file_rows
        )
        issue_rows = self._connection.execute(
            "SELECT ordinal, stage, path, issue_kind, message, size FROM snapshot_issues "
            "WHERE index_id = ? ORDER BY ordinal",
            (index_id,),
        ).fetchall()
        all_issues = tuple(
            SnapshotIssue(
                path=row["path"],
                kind=row["issue_kind"],  # type: ignore[arg-type]
                message=row["message"],
                size=row["size"],
            )
            for row in issue_rows
        )
        snapshot_issues = tuple(
            issue for issue, row in zip(all_issues, issue_rows) if row["stage"] == "snapshot"
        )
        symbol_rows = self._connection.execute(
            "SELECT symbol_id, path, qualified_name, kind, start_line, end_line FROM symbols "
            "WHERE index_id = ? ORDER BY path COLLATE BINARY, start_line, end_line, kind, qualified_name",
            (index_id,),
        ).fetchall()
        symbols = tuple(_symbol_from_row(index_id, row) for row in symbol_rows)
        edge_rows = self._connection.execute(
            "SELECT edge_id, source_id, target_id, kind, extraction_method, source_path, "
            "start_line, end_line FROM static_edges WHERE index_id = ? "
            "ORDER BY source_path COLLATE BINARY, start_line, kind, edge_id",
            (index_id,),
        ).fetchall()
        edges = tuple(_edge_from_row(index_id, row) for row in edge_rows)
        target_rows = self._connection.execute(
            "SELECT unresolved_id, target_symbol_id FROM unresolved_targets "
            "WHERE index_id = ? ORDER BY unresolved_id, target_symbol_id",
            (index_id,),
        ).fetchall()
        targets: dict[str, list[str]] = {}
        for row in target_rows:
            targets.setdefault(row["unresolved_id"], []).append(row["target_symbol_id"])
        unresolved_rows = self._connection.execute(
            "SELECT unresolved_id, source_symbol_id, path, expression, reason, start_line, end_line "
            "FROM unresolved_references WHERE index_id = ? ORDER BY path COLLATE BINARY, "
            "start_line, expression, unresolved_id",
            (index_id,),
        ).fetchall()
        unresolved = tuple(
            UnresolvedReference(
                unresolved_id=row["unresolved_id"],
                index_id=index_id,
                source_symbol_id=row["source_symbol_id"],
                path=row["path"],
                expression=row["expression"],
                reason=row["reason"],
                possible_target_ids=targets.get(row["unresolved_id"], []),
                start_line=row["start_line"],
                end_line=row["end_line"],
            )
            for row in unresolved_rows
        )
        snapshot = SourceSnapshot(index=index, files=files, issues=snapshot_issues)
        definitions = DefinitionIndex(snapshot=snapshot, symbols=symbols, issues=all_issues)
        return RelationIndex(
            definitions=definitions,
            edges=edges,
            unresolved=unresolved,
            truncated=bool(index_row["truncated"]),
            truncation_reason=index_row["truncation_reason"],
            unresolved_truncated=_nullable_bool(index_row["unresolved_truncated"]),
            unresolved_truncation_reason=index_row["unresolved_truncation_reason"],
        )

    def load_index_snapshot(self, index_id: str) -> IndexSnapshot:
        """Read only index metadata without loading source, symbols, or relations."""

        return _index_from_row(self._require_index(index_id))

    def load_snapshot_manifest(
        self,
        index_id: str,
    ) -> tuple[tuple[SnapshotFile, ...], tuple[SnapshotIssue, ...]]:
        """Read source hashes and snapshot-stage issues without loading graph data."""

        self._require_index(index_id)
        file_rows = self._connection.execute(
            "SELECT path, size, content_hash FROM snapshot_files WHERE index_id = ? ORDER BY path",
            (index_id,),
        ).fetchall()
        issue_rows = self._connection.execute(
            "SELECT path, issue_kind, message, size FROM snapshot_issues "
            "WHERE index_id = ? AND stage = 'snapshot' ORDER BY path, issue_kind, ordinal",
            (index_id,),
        ).fetchall()
        files = tuple(
            SnapshotFile(
                path=row["path"],
                size=row["size"],
                content_hash=row["content_hash"],
                text="",
            )
            for row in file_rows
        )
        issues = tuple(
            SnapshotIssue(
                path=row["path"],
                kind=row["issue_kind"],  # type: ignore[arg-type]
                message=row["message"],
                size=row["size"],
            )
            for row in issue_rows
        )
        return files, issues

    def load_definition_cache(
        self,
        *,
        scope_id: str,
        repository_id: str,
        source_files: tuple[SnapshotFile, ...],
        definition_rules_version: str,
        python_version: str,
        configuration_hash: str,
        max_bytes: int,
    ) -> dict[str, DefinitionCacheEntry]:
        """Load validated definitions for the exact current source files.

        Cache rows are an optional performance hint.  A malformed, stale, or
        over-sized row is ignored as a miss; only an unavailable SQLite read is
        raised so the build service can fall back to cold extraction.
        """

        if not scope_id or not repository_id or max_bytes < 1:
            raise StorageInputError("definition cache scope and budget are invalid")
        source_by_path = _definition_cache_sources(source_files)
        if not source_by_path:
            return {}
        try:
            rows = self._connection.execute(
                "SELECT path, content_hash, result_json, result_bytes "
                "FROM definition_cache WHERE scope_id = ? AND repository_id = ? "
                "AND definition_rules_version = ? AND python_version = ? "
                "AND configuration_hash = ? AND result_bytes <= ?",
                (
                    scope_id,
                    repository_id,
                    definition_rules_version,
                    python_version,
                    configuration_hash,
                    max_bytes,
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"could not read definition cache: {exc}") from exc

        entries: dict[str, DefinitionCacheEntry] = {}
        for row in rows:
            path = row["path"]
            source_file = source_by_path.get(path)
            if source_file is None or row["content_hash"] != source_file.content_hash:
                continue
            serialized = row["result_json"]
            result_bytes = row["result_bytes"]
            if (
                not isinstance(serialized, str)
                or not isinstance(result_bytes, int)
                or isinstance(result_bytes, bool)
                or result_bytes != len(serialized.encode("utf-8"))
            ):
                logger.warning("ignoring invalid definition cache size for %s", path)
                continue
            try:
                entry = deserialize_definition_cache_entry(
                    serialized,
                    expected_path=source_file.path,
                )
                _validate_definition_cache_entry_lines(entry, source_file)
            except (UnicodeError, ValueError, TypeError):
                logger.warning("ignoring invalid definition cache entry for %s", path)
                continue
            entries[source_file.path] = entry
        return entries

    def write_definition_cache(
        self,
        *,
        scope_id: str,
        repository_id: str,
        source_files: tuple[SnapshotFile, ...],
        entries: tuple[tuple[str, DefinitionCacheEntry], ...],
        definition_rules_version: str,
        python_version: str,
        configuration_hash: str,
        max_entries: int,
        max_bytes: int,
    ) -> int:
        """Persist parsed definitions and prune the shared derived-cache budget.

        Serialization happens before the SQLite transaction.  The transaction
        then inserts complete rows and applies both capacity limits atomically;
        an index or its historical evidence is never part of cache cleanup.
        """

        if (
            not scope_id
            or not repository_id
            or max_entries < 1
            or max_bytes < 1
        ):
            raise StorageInputError("definition cache scope and budgets are invalid")
        source_by_path = _definition_cache_sources(source_files)
        rows: list[tuple[object, ...]] = []
        seen_paths: set[str] = set()
        timestamp = _datetime_text(datetime.now(timezone.utc))
        for path, entry in entries:
            if path in seen_paths:
                raise StorageInputError("definition cache contains duplicate paths")
            seen_paths.add(path)
            source_file = source_by_path.get(path)
            if source_file is None:
                continue
            try:
                _validate_definition_cache_entry_lines(entry, source_file)
                serialized = serialize_definition_cache_entry(entry)
            except (TypeError, ValueError):
                logger.warning("skipping invalid definition cache entry for %s", path)
                continue
            result_bytes = len(serialized.encode("utf-8"))
            if result_bytes > max_bytes:
                logger.warning("skipping over-sized definition cache entry for %s", path)
                continue
            rows.append(
                (
                    scope_id,
                    repository_id,
                    path,
                    source_file.content_hash,
                    definition_rules_version,
                    python_version,
                    configuration_hash,
                    serialized,
                    result_bytes,
                    timestamp,
                )
            )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                self._connection.execute(
                    "INSERT OR REPLACE INTO definition_cache ("
                    "scope_id, repository_id, path, content_hash, "
                    "definition_rules_version, python_version, configuration_hash, "
                    "result_json, result_bytes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
            self._prune_derived_cache(
                scope_id=scope_id,
                repository_id=repository_id,
                max_entries=max_entries,
                max_bytes=max_bytes,
            )
            self._connection.execute("COMMIT")
        except Exception as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, StorageError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise StorageError(f"could not write definition cache: {exc}") from exc
            raise
        return len(rows)

    def load_relation_cache(
        self,
        *,
        scope_id: str,
        repository_id: str,
        source_files: tuple[SnapshotFile, ...],
        relation_rules_version: str,
        python_version: str,
        configuration_hash: str,
        max_bytes: int,
    ) -> dict[str, RelationFileFacts]:
        """Load validated relation facts for the exact current source files."""

        if not scope_id or not repository_id or max_bytes < 1:
            raise StorageInputError("relation cache scope and budget are invalid")
        source_by_path = _definition_cache_sources(source_files)
        if not source_by_path:
            return {}
        try:
            rows = self._connection.execute(
                "SELECT path, content_hash, result_json, result_bytes "
                "FROM relation_cache WHERE scope_id = ? AND repository_id = ? "
                "AND relation_rules_version = ? AND python_version = ? "
                "AND configuration_hash = ? AND result_bytes <= ?",
                (
                    scope_id,
                    repository_id,
                    relation_rules_version,
                    python_version,
                    configuration_hash,
                    max_bytes,
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"could not read relation cache: {exc}") from exc

        entries: dict[str, RelationFileFacts] = {}
        for row in rows:
            path = row["path"]
            source_file = source_by_path.get(path)
            if source_file is None or row["content_hash"] != source_file.content_hash:
                continue
            serialized = row["result_json"]
            result_bytes = row["result_bytes"]
            if (
                not isinstance(serialized, str)
                or not isinstance(result_bytes, int)
                or isinstance(result_bytes, bool)
                or result_bytes != len(serialized.encode("utf-8"))
            ):
                logger.warning("ignoring invalid relation cache size for %s", path)
                continue
            try:
                facts = deserialize_relation_file_facts(
                    serialized,
                    expected_path=source_file.path,
                )
                _validate_relation_cache_lines(facts, source_file)
            except (UnicodeError, ValueError, TypeError, RecursionError):
                logger.warning("ignoring invalid relation cache entry for %s", path)
                continue
            entries[source_file.path] = facts
        return entries

    def write_relation_cache(
        self,
        *,
        scope_id: str,
        repository_id: str,
        source_files: tuple[SnapshotFile, ...],
        entries: tuple[tuple[str, RelationFileFacts], ...],
        relation_rules_version: str,
        python_version: str,
        configuration_hash: str,
        max_entries: int,
        max_bytes: int,
    ) -> int:
        """Persist relation facts and prune the shared derived-cache budget."""

        if not scope_id or not repository_id or max_entries < 1 or max_bytes < 1:
            raise StorageInputError("relation cache scope and budgets are invalid")
        source_by_path = _definition_cache_sources(source_files)
        rows: list[tuple[object, ...]] = []
        seen_paths: set[str] = set()
        timestamp = _datetime_text(datetime.now(timezone.utc))
        for path, facts in entries:
            if path in seen_paths:
                raise StorageInputError("relation cache contains duplicate paths")
            seen_paths.add(path)
            source_file = source_by_path.get(path)
            if source_file is None:
                continue
            try:
                _validate_relation_cache_lines(facts, source_file)
                serialized = serialize_relation_file_facts(facts)
            except (TypeError, ValueError):
                logger.warning("skipping invalid relation cache entry for %s", path)
                continue
            result_bytes = len(serialized.encode("utf-8"))
            if result_bytes > max_bytes:
                logger.warning("skipping over-sized relation cache entry for %s", path)
                continue
            rows.append(
                (
                    scope_id,
                    repository_id,
                    path,
                    source_file.content_hash,
                    relation_rules_version,
                    python_version,
                    configuration_hash,
                    serialized,
                    result_bytes,
                    timestamp,
                )
            )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                self._connection.execute(
                    "INSERT OR REPLACE INTO relation_cache ("
                    "scope_id, repository_id, path, content_hash, "
                    "relation_rules_version, python_version, configuration_hash, "
                    "result_json, result_bytes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
            self._prune_derived_cache(
                scope_id=scope_id,
                repository_id=repository_id,
                max_entries=max_entries,
                max_bytes=max_bytes,
            )
            self._connection.execute("COMMIT")
        except Exception as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, StorageError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise StorageError(f"could not write relation cache: {exc}") from exc
            raise
        return len(rows)

    def _prune_derived_cache(
        self,
        *,
        scope_id: str,
        repository_id: str,
        max_entries: int,
        max_bytes: int,
    ) -> None:
        """Keep definition and relation rows within one shared scope budget."""

        cache_rows = self._connection.execute(
            "SELECT 'definition' AS cache_kind, rowid, result_bytes, created_at "
            "FROM definition_cache WHERE scope_id = ? AND repository_id = ? "
            "UNION ALL "
            "SELECT 'relation' AS cache_kind, rowid, result_bytes, created_at "
            "FROM relation_cache WHERE scope_id = ? AND repository_id = ? "
            "ORDER BY created_at DESC, cache_kind DESC, rowid DESC",
            (scope_id, repository_id, scope_id, repository_id),
        ).fetchall()
        kept_count = 0
        kept_bytes = 0
        delete_rows: list[tuple[str, int]] = []
        for cache_row in cache_rows:
            cache_kind = cache_row["cache_kind"]
            rowid = cache_row["rowid"]
            result_bytes = cache_row["result_bytes"]
            if (
                cache_kind in {"definition", "relation"}
                and isinstance(rowid, int)
                and isinstance(result_bytes, int)
                and not isinstance(result_bytes, bool)
                and kept_count < max_entries
                and result_bytes >= 0
                and kept_bytes + result_bytes <= max_bytes
            ):
                kept_count += 1
                kept_bytes += result_bytes
            elif isinstance(cache_kind, str) and isinstance(rowid, int):
                delete_rows.append((cache_kind, rowid))
        for cache_kind, rowid in delete_rows:
            table = "definition_cache" if cache_kind == "definition" else "relation_cache"
            self._connection.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))

    def get_relation_coverage(self, index_id: str) -> RelationCoverage:
        """Read relation coverage facts without loading the immutable graph payload."""

        row = self._require_index(index_id)
        return RelationCoverage(
            index_truncated=bool(row["truncated"]),
            index_truncation_reason=row["truncation_reason"],
            index_unresolved_truncated=_nullable_bool(row["unresolved_truncated"]),
            index_unresolved_truncation_reason=row["unresolved_truncation_reason"],
        )

    def save_feature_generation(
        self,
        generation: FeatureGeneration,
        *,
        cache_key: str,
        idempotency_key: str | None = None,
    ) -> FeatureGeneration:
        """Persist one validated feature result without replacing another result."""

        self._require_index(generation.index.index_id)
        if generation.cache_key != cache_key:
            raise StorageInputError("feature generation cache key does not match the persisted result")
        serialized = json.dumps(
            generation.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._connection.execute(
                "INSERT INTO feature_generations "
                "(generation_id, project_id, index_id, cache_key, idempotency_key, mode, model, "
                "prompt_version, rules_version, status, created_at, result_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generation.generation_id,
                    generation.project_id,
                    generation.index.index_id,
                    cache_key,
                    idempotency_key,
                    generation.mode,
                    generation.model,
                    generation.prompt_version,
                    generation.rules_version,
                    generation.status,
                    _datetime_text(generation.created_at),
                    serialized,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageConflictError("feature generation conflicts with existing data") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"could not save feature generation: {exc}") from exc
        return generation

    def load_feature_generation(self, generation_id: str) -> FeatureGeneration:
        """Read one persisted generation without re-running retrieval or a model."""

        row = self._connection.execute(
            "SELECT result_json FROM feature_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"feature generation {generation_id!r} was not found")
        try:
            return _feature_generation_from_json(row["result_json"])
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise StorageError("persisted feature generation is invalid") from exc

    def find_feature_generation_by_idempotency(
        self,
        project_id: str,
        idempotency_key: str,
    ) -> tuple[str, FeatureGeneration] | None:
        row = self._connection.execute(
            "SELECT generation_id FROM feature_generations "
            "WHERE project_id = ? AND idempotency_key = ?",
            (project_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return row["generation_id"], self.load_feature_generation(row["generation_id"])

    def find_cached_feature_generation(
        self,
        project_id: str,
        index_id: str,
        cache_key: str,
    ) -> tuple[str, FeatureGeneration] | None:
        row = self._connection.execute(
            "SELECT generation_id FROM feature_generations "
            "WHERE project_id = ? AND index_id = ? AND cache_key = ? "
            "AND status IN ('succeeded', 'partial', 'insufficient_evidence') "
            "ORDER BY created_at DESC, generation_id DESC LIMIT 1",
            (project_id, index_id, cache_key),
        ).fetchone()
        if row is None:
            return None
        return row["generation_id"], self.load_feature_generation(row["generation_id"])

    def list_feature_generations(
        self,
        project_id: str,
        *,
        index_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> FeatureGenerationPage:
        """List saved generations in stable newest-first order without invoking a model."""

        self.load_project(project_id)
        if limit < 1 or limit > 40:
            raise StorageInputError("feature generation history limit is outside the allowed range")
        if index_id is not None and not self.project_has_index(project_id, index_id):
            raise StorageNotFoundError(f"index {index_id!r} does not belong to project {project_id!r}")

        cursor_values = _decode_feature_generation_cursor(
            cursor,
            project_id=project_id,
            index_id=index_id,
        )
        where = ["project_id = ?"]
        parameters: list[object] = [project_id]
        if index_id is not None:
            where.append("index_id = ?")
            parameters.append(index_id)
        scan_cursor = cursor_values
        scanned = 0
        exhausted = False
        last_scanned: tuple[str, str] | None = None
        valid_rows: list[tuple[sqlite3.Row, FeatureGeneration]] = []
        while len(valid_rows) <= limit and scanned < _FEATURE_HISTORY_SCAN_LIMIT:
            batch_where = list(where)
            batch_parameters = list(parameters)
            if scan_cursor is not None:
                created_at, generation_id = scan_cursor
                batch_where.append("(created_at < ? OR (created_at = ? AND generation_id < ?))")
                batch_parameters.extend([created_at, created_at, generation_id])
            batch_limit = min(_FEATURE_HISTORY_BATCH_SIZE, _FEATURE_HISTORY_SCAN_LIMIT - scanned)
            rows = self._connection.execute(
                "SELECT generation_id, created_at, result_json FROM feature_generations "
                f"WHERE {' AND '.join(batch_where)} "
                "ORDER BY created_at DESC, generation_id DESC LIMIT ?",
                (*batch_parameters, batch_limit),
            ).fetchall()
            if not rows:
                exhausted = True
                break
            for row in rows:
                scanned += 1
                last_scanned = (str(row["created_at"]), str(row["generation_id"]))
                try:
                    generation = _feature_generation_from_json(row["result_json"])
                except (json.JSONDecodeError, ValidationError, TypeError):
                    logger.warning(
                        "skipping invalid feature generation history row generation_id=%s",
                        row["generation_id"],
                    )
                    continue
                if generation.project_id != project_id or (
                    index_id is not None and generation.index.index_id != index_id
                ):
                    logger.warning(
                        "skipping out-of-scope feature generation history row generation_id=%s",
                        row["generation_id"],
                    )
                    continue
                valid_rows.append((row, generation))
                if len(valid_rows) > limit:
                    break
            if len(valid_rows) > limit:
                break
            if len(rows) < batch_limit:
                exhausted = True
                break
            scan_cursor = last_scanned

        has_more = len(valid_rows) > limit or not exhausted
        page_rows = valid_rows[:limit]
        generations = [generation for _, generation in page_rows]
        next_cursor_values: tuple[str, str] | None = None
        if has_more:
            if len(valid_rows) > limit and page_rows:
                row = page_rows[-1][0]
                next_cursor_values = (str(row["created_at"]), str(row["generation_id"]))
            else:
                next_cursor_values = last_scanned
        next_cursor = (
            _encode_feature_generation_cursor(
                next_cursor_values[0],
                next_cursor_values[1],
                project_id=project_id,
                index_id=index_id,
            )
            if next_cursor_values is not None
            else None
        )
        return FeatureGenerationPage(
            generations=tuple(generations),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def create_session(
        self,
        session: Session,
        *,
        idempotency_key: str | None,
        request_fingerprint: str,
    ) -> SessionRecord:
        """Persist one empty session after its project/index scope was validated."""

        if session.message_count != 0:
            raise StorageInputError("new sessions must start without messages")
        self._require_index(session.index.index_id)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT INTO sessions "
                "(session_id, project_id, index_id, feature_generation_id, topic, created_at, updated_at, "
                "message_count, idempotency_key, request_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.session_id,
                    session.project_id,
                    session.index.index_id,
                    session.feature_generation_id,
                    session.topic,
                    _datetime_text(session.created_at),
                    _datetime_text(session.updated_at),
                    session.message_count,
                    idempotency_key,
                    request_fingerprint,
                ),
            )
            self._connection.execute("COMMIT")
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("session conflicts with existing data") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not create session: {exc}") from exc
        return self.load_session(session.session_id)

    def load_session(self, session_id: str) -> SessionRecord:
        """Read one session metadata record without loading its messages."""

        row = self._connection.execute(
            "SELECT session_id, project_id, index_id, feature_generation_id, topic, created_at, updated_at, "
            "message_count, idempotency_key, request_fingerprint FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"session {session_id!r} was not found")
        return _session_from_row(row)

    def find_session_by_idempotency(
        self,
        project_id: str,
        idempotency_key: str,
    ) -> SessionRecord | None:
        row = self._connection.execute(
            "SELECT session_id, project_id, index_id, feature_generation_id, topic, created_at, updated_at, "
            "message_count, idempotency_key, request_fingerprint FROM sessions "
            "WHERE project_id = ? AND idempotency_key = ?",
            (project_id, idempotency_key),
        ).fetchone()
        return None if row is None else _session_from_row(row)

    def list_sessions(
        self,
        project_id: str,
        *,
        limit: int = 40,
        cursor: str | None = None,
    ) -> SessionPage:
        """List sessions by latest activity with a project-bound opaque cursor."""

        self.load_project(project_id)
        if limit < 1 or limit > 40:
            raise StorageInputError("session list limit is outside the allowed range")
        cursor_values = _decode_session_cursor(cursor, project_id=project_id)
        where = ["project_id = ?"]
        parameters: list[object] = [project_id]
        if cursor_values is not None:
            updated_at, session_id = cursor_values
            where.append("(updated_at < ? OR (updated_at = ? AND session_id < ?))")
            parameters.extend([updated_at, updated_at, session_id])
        rows = self._connection.execute(
            "SELECT session_id, project_id, index_id, feature_generation_id, topic, created_at, updated_at, "
            "message_count, idempotency_key, request_fingerprint FROM sessions "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC, session_id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        records = tuple(_session_from_row(row) for row in page_rows)
        next_cursor = (
            _encode_session_cursor(
                records[-1].updated_at,
                records[-1].session_id,
                project_id=project_id,
            )
            if has_more and records
            else None
        )
        return SessionPage(sessions=records, next_cursor=next_cursor, has_more=has_more)

    def append_session_message(
        self,
        session_id: str,
        *,
        message_id: str,
        source: str,
        content: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        created_at: datetime,
    ) -> SessionMessage:
        """Append one message while atomically assigning the next session sequence."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            session_row = self._connection.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise StorageNotFoundError(f"session {session_id!r} was not found")
            if idempotency_key is not None:
                existing_row = self._connection.execute(
                    "SELECT message_id, session_id, sequence, source, content, created_at, request_fingerprint "
                    "FROM session_messages WHERE session_id = ? AND idempotency_key = ?",
                    (session_id, idempotency_key),
                ).fetchone()
                if existing_row is not None:
                    if existing_row["request_fingerprint"] != request_fingerprint:
                        raise StorageConflictError(
                            "message idempotency key is already bound to different content"
                        )
                    self._connection.execute("COMMIT")
                    return _session_message_from_row(existing_row)
            sequence_row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence = int(sequence_row["next_sequence"])
            timestamp = _datetime_text(created_at)
            self._connection.execute(
                "INSERT INTO session_messages "
                "(message_id, session_id, sequence, source, content, created_at, idempotency_key, request_fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    session_id,
                    sequence,
                    source,
                    content,
                    timestamp,
                    idempotency_key,
                    request_fingerprint,
                ),
            )
            self._connection.execute(
                "UPDATE sessions SET message_count = message_count + 1, updated_at = ? "
                "WHERE session_id = ?",
                (timestamp, session_id),
            )
            self._connection.execute("COMMIT")
            return SessionMessage(
                message_id=message_id,
                session_id=session_id,
                sequence=sequence,
                source=source,  # type: ignore[arg-type]
                content=content,
                created_at=created_at,
            )
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("session message conflicts with existing data") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not append session message: {exc}") from exc

    def list_session_messages(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SessionMessagePage:
        """Read messages in server sequence order with a project/session cursor."""

        self.load_project(project_id)
        session_row = self._connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND project_id = ?",
            (session_id, project_id),
        ).fetchone()
        if session_row is None:
            raise StorageNotFoundError(f"session {session_id!r} was not found")
        if limit < 1 or limit > 100:
            raise StorageInputError("session message limit is outside the allowed range")
        cursor_sequence = _decode_session_message_cursor(
            cursor,
            project_id=project_id,
            session_id=session_id,
        )
        parameters: list[object] = [session_id]
        where = ["session_id = ?"]
        if cursor_sequence is not None:
            where.append("sequence > ?")
            parameters.append(cursor_sequence)
        rows = self._connection.execute(
            "SELECT message_id, session_id, sequence, source, content, created_at "
            f"FROM session_messages WHERE {' AND '.join(where)} "
            "ORDER BY sequence ASC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        messages = tuple(_session_message_from_row(row) for row in page_rows)
        next_cursor = (
            _encode_session_message_cursor(
                messages[-1].sequence,
                project_id=project_id,
                session_id=session_id,
            )
            if has_more and messages
            else None
        )
        return SessionMessagePage(messages=messages, next_cursor=next_cursor, has_more=has_more)

    def load_session_messages_for_run(
        self,
        project_id: str,
        session_id: str,
        message_end_sequence: int,
    ) -> tuple[SessionMessage, ...]:
        """Read the complete saved message prefix that a Run is allowed to freeze."""

        self.load_project(project_id)
        if message_end_sequence < 1 or message_end_sequence > 1_000:
            raise StorageInputError("Run message cutoff is outside the allowed range")
        session_row = self._connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ? AND project_id = ?",
            (session_id, project_id),
        ).fetchone()
        if session_row is None:
            raise StorageNotFoundError(f"session {session_id!r} was not found")
        rows = self._connection.execute(
            "SELECT message_id, session_id, sequence, source, content, created_at "
            "FROM session_messages WHERE session_id = ? AND sequence <= ? "
            "ORDER BY sequence ASC LIMIT ?",
            (session_id, message_end_sequence, message_end_sequence + 1),
        ).fetchall()
        if len(rows) != message_end_sequence:
            raise StorageInputError("Run message cutoff is beyond the saved message sequence")
        messages = tuple(_session_message_from_row(row) for row in rows)
        if [message.sequence for message in messages] != list(range(1, message_end_sequence + 1)):
            raise StorageError("saved session messages are not contiguous")
        return messages

    def create_run(
        self,
        run: Run,
        run_input: RunInput,
        *,
        idempotency_key: str | None,
        request_fingerprint: str,
    ) -> tuple[RunRecord, bool]:
        """Create one queued Run and its complete frozen input atomically."""

        if run.status != "queued":
            raise StorageInputError("new Runs must start queued")
        if run.input_hash != run_input.input_hash or run.input_chars != run_input.input_chars:
            raise StorageInputError("Run input metadata does not match the Run")
        if run.project_id != run_input.project_id or run.session_id != run_input.session_id:
            raise StorageInputError("Run input scope does not match the Run")
        if run.index_id != run_input.index_id or run.message_end_sequence != run_input.message_end_sequence:
            raise StorageInputError("Run input snapshot does not match the Run")
        self._require_index(run.index_id)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            session_row = self._connection.execute(
                "SELECT project_id, index_id FROM sessions WHERE session_id = ?",
                (run.session_id,),
            ).fetchone()
            if session_row is None or session_row["project_id"] != run.project_id:
                raise StorageNotFoundError("Run session was not found")
            if session_row["index_id"] != run.index_id:
                raise StorageInputError("Run index does not match the Session snapshot")
            if not self.project_has_index(run.project_id, run.index_id):
                raise StorageNotFoundError("Run index is outside the project")
            if idempotency_key is not None:
                existing_row = self._connection.execute(
                    "SELECT run_id, request_fingerprint FROM runs "
                    "WHERE project_id = ? AND session_id = ? AND idempotency_key = ?",
                    (run.project_id, run.session_id, idempotency_key),
                ).fetchone()
                if existing_row is not None:
                    if existing_row["request_fingerprint"] != request_fingerprint:
                        raise StorageConflictError("Run idempotency key is bound to different input")
                    self._connection.execute("COMMIT")
                    return self.load_run_record(existing_row["run_id"]), False
            serialized_run = _json_text(run)
            serialized_input = _json_text(run_input)
            self._connection.execute(
                "INSERT INTO runs (run_id, project_id, session_id, index_id, status, idempotency_key, "
                "request_fingerprint, report_id, created_at, updated_at, run_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.project_id,
                    run.session_id,
                    run.index_id,
                    run.status,
                    idempotency_key,
                    request_fingerprint,
                    run.report_id,
                    _datetime_text(run.created_at),
                    _datetime_text(run.created_at),
                    serialized_run,
                ),
            )
            self._connection.execute(
                "INSERT INTO run_inputs (run_id, project_id, session_id, index_id, input_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run_input.project_id,
                    run_input.session_id,
                    run_input.index_id,
                    serialized_input,
                ),
            )
            self._connection.execute("COMMIT")
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("Run conflicts with an existing active Run") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not create Run: {exc}") from exc
        return self.load_run_record(run.run_id), True

    def load_run_record(self, run_id: str) -> RunRecord:
        """Read one Run and its separately persisted frozen input."""

        row = self._connection.execute(
            "SELECT run_json, idempotency_key, request_fingerprint FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"Run {run_id!r} was not found")
        input_row = self._connection.execute(
            "SELECT input_json FROM run_inputs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if input_row is None:
            raise StorageError("persisted Run is missing its frozen input")
        try:
            run = Run.model_validate(json.loads(row["run_json"]))
            run_input = RunInput.model_validate(json.loads(input_row["input_json"]))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise StorageError("persisted Run data is invalid") from exc
        if (
            run.run_id != run_id
            or run.project_id != run_input.project_id
            or run.session_id != run_input.session_id
            or run.index_id != run_input.index_id
            or run.input_hash != run_input.input_hash
            or run.input_chars != run_input.input_chars
            or run.message_end_sequence != run_input.message_end_sequence
        ):
            raise StorageError("persisted Run data crosses its frozen input boundary")
        return RunRecord(
            run=run,
            input=run_input,
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
        )

    def find_run_by_idempotency(
        self,
        project_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> RunRecord | None:
        row = self._connection.execute(
            "SELECT run_id FROM runs WHERE project_id = ? AND session_id = ? AND idempotency_key = ?",
            (project_id, session_id, idempotency_key),
        ).fetchone()
        return None if row is None else self.load_run_record(row["run_id"])

    def list_runs(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 40,
        cursor: str | None = None,
    ) -> RunPage:
        """List one Session's Runs by stable creation order."""

        session_row = self._connection.execute(
            "SELECT project_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if session_row is None or session_row["project_id"] != project_id:
            raise StorageNotFoundError("Run session was not found")
        if limit < 1 or limit > 40:
            raise StorageInputError("Run history limit is outside the allowed range")
        cursor_values = _decode_run_cursor(cursor, project_id=project_id, session_id=session_id)
        where = ["project_id = ?", "session_id = ?"]
        parameters: list[object] = [project_id, session_id]
        if cursor_values is not None:
            created_at, run_id = cursor_values
            where.append("(created_at < ? OR (created_at = ? AND run_id < ?))")
            parameters.extend([created_at, created_at, run_id])
        rows = self._connection.execute(
            "SELECT run_id, created_at FROM runs "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC, run_id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        records = tuple(self.load_run_record(row["run_id"]).run for row in page_rows)
        next_cursor = (
            _encode_run_cursor(
                str(page_rows[-1]["created_at"]),
                str(page_rows[-1]["run_id"]),
                project_id=project_id,
                session_id=session_id,
            )
            if has_more and page_rows
            else None
        )
        return RunPage(runs=records, next_cursor=next_cursor, has_more=has_more)

    def load_run_events(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = _RUN_EVENT_PAGE_SIZE,
    ) -> RunEventPage:
        """Read public Run events after a bounded sequence cursor."""

        self._require_run_scope(project_id, session_id, run_id)
        if after_sequence < 0 or limit < 1 or limit > _RUN_EVENT_PAGE_SIZE:
            raise StorageInputError("Run event pagination is outside the allowed range")
        rows = self._connection.execute(
            "SELECT sequence, event_json FROM run_events "
            "WHERE run_id = ? AND sequence > ? ORDER BY sequence ASC LIMIT ?",
            (run_id, after_sequence, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        try:
            events = tuple(_EVENT_ADAPTER.validate_json(row["event_json"]) for row in page_rows)
        except (TypeError, ValueError, ValidationError) as exc:
            raise StorageError("persisted Run event is invalid") from exc
        next_after_sequence = int(page_rows[-1]["sequence"]) if has_more and page_rows else None
        return RunEventPage(
            events=events,
            next_after_sequence=next_after_sequence,
            has_more=has_more,
        )

    def load_run_event_cursor(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
    ) -> RunEventCursor:
        """Read the persisted upper bound used to validate an SSE resume cursor."""

        self._require_run_scope(project_id, session_id, run_id)
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS latest_sequence FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StorageError("could not read Run event cursor")
        return RunEventCursor(latest_sequence=int(row["latest_sequence"]))

    def load_run_tool_results(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = _RUN_TOOL_RESULT_PAGE_SIZE,
    ) -> RunToolResultPage:
        """Read persisted bounded tool results in completion order."""

        self._require_run_scope(project_id, session_id, run_id)
        if after_sequence < 0 or limit < 1 or limit > _RUN_TOOL_RESULT_PAGE_SIZE:
            raise StorageInputError("Run tool-result pagination is outside the allowed range")
        rows = self._connection.execute(
            "SELECT completed_sequence, result_json FROM run_tool_results "
            "WHERE run_id = ? AND completed_sequence > ? ORDER BY completed_sequence ASC LIMIT ?",
            (run_id, after_sequence, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        try:
            results = tuple(_TOOL_RESULT_ADAPTER.validate_json(row["result_json"]) for row in page_rows)
        except (TypeError, ValueError, ValidationError) as exc:
            raise StorageError("persisted Run tool result is invalid") from exc
        next_after_sequence = int(page_rows[-1]["completed_sequence"]) if has_more and page_rows else None
        return RunToolResultPage(
            results=results,
            next_after_sequence=next_after_sequence,
            has_more=has_more,
        )

    def load_run_tool_result(self, run_id: str, request_id: str) -> ToolResult | None:
        """Read one persisted tool result for external-call idempotency."""

        row = self._connection.execute(
            "SELECT result_json FROM run_tool_results WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return _TOOL_RESULT_ADAPTER.validate_json(row["result_json"])
        except (TypeError, ValueError, ValidationError) as exc:
            raise StorageError("persisted Run tool result is invalid") from exc

    def load_run_tool_request(self, run_id: str, request_id: str) -> ToolRequest | None:
        """Read the original request event for one persisted external tool call."""

        rows = self._connection.execute(
            "SELECT event_json FROM run_events WHERE run_id = ? AND type = 'tool.started' ORDER BY sequence ASC",
            (run_id,),
        ).fetchall()
        try:
            for row in rows:
                event = _EVENT_ADAPTER.validate_json(row["event_json"])
                if event.type == "tool.started" and event.payload.request.request_id == request_id:
                    return event.payload.request
        except (TypeError, ValueError, ValidationError) as exc:
            raise StorageError("persisted Run tool request is invalid") from exc
        return None

    def start_run(self, run_id: str, started_at: datetime, event: Event) -> RunRecord | None:
        """Atomically move a queued Run to running and append run.started."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run_id)
            if record.run.status != "queued":
                self._connection.execute("COMMIT")
                return None
            if event.type != "run.started" or event.run_id != run_id:
                raise StorageInputError("Run start event is invalid")
            next_sequence = self._next_run_sequence(run_id)
            if event.sequence != next_sequence:
                raise StorageInputError("Run start event sequence is invalid")
            updated_budget = record.run.budget.model_copy(
                update={"events_used": record.run.budget.events_used + 1}
            )
            updated_run = _validated_run(
                record.run,
                status="running",
                started_at=started_at,
                budget=updated_budget,
            )
            self._insert_run_event(event)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run_id)
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not start Run: {exc}") from exc

    def append_run_event(self, event: Event) -> int:
        """Append one non-terminal event while reserving room for a terminal event."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(event.run_id)
            if record.run.status not in {"queued", "running"}:
                raise StorageConflictError("Run is already terminal")
            if event.type in {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}:
                raise StorageInputError("terminal events require terminal Run transition")
            next_sequence = self._next_run_sequence(event.run_id)
            if event.sequence != next_sequence:
                raise StorageInputError("Run event sequence is invalid")
            if record.run.budget.events_used + 1 >= record.run.budget.max_events:
                raise StorageInputError("Run event budget is exhausted")
            updated_budget = record.run.budget.model_copy(
                update={"events_used": record.run.budget.events_used + 1}
            )
            updated_run = _validated_run(record.run, budget=updated_budget)
            self._insert_run_event(event)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return event.sequence
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not append Run event: {exc}") from exc

    def append_run_tool_result(self, event: Event, result: ToolResult) -> int:
        """Append a tool completion event and result atomically."""

        if event.type not in {"tool.completed", "tool.failed"}:
            raise StorageInputError("tool result requires a tool completion event")
        if event.run_id != result.run_id or event.sequence != result.completed_sequence:
            raise StorageInputError("tool result does not match its completion event")
        payload_result = getattr(event.payload, "result", None)
        if payload_result is None or payload_result.request_id != result.request_id:
            raise StorageInputError("tool completion event does not match its result")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(event.run_id)
            if record.run.status not in {"queued", "running"}:
                raise StorageConflictError("Run is already terminal")
            next_sequence = self._next_run_sequence(event.run_id)
            if event.sequence != next_sequence:
                raise StorageInputError("Run tool event sequence is invalid")
            budget = record.run.budget
            source_lines = 0
            if hasattr(result, "evidence"):
                evidence = getattr(result, "evidence")
                source_lines = evidence.end_line - evidence.start_line + 1
            output_chars = len(_json_text(result))
            updated_budget = budget.model_copy(
                update={
                    "tool_calls_used": budget.tool_calls_used + 1,
                    "source_lines_used": budget.source_lines_used + source_lines,
                    "output_chars_used": budget.output_chars_used + output_chars,
                    "events_used": budget.events_used + 1,
                }
            )
            if updated_budget.events_used >= budget.max_events:
                raise StorageInputError("Run event budget is exhausted")
            if updated_budget.tool_calls_used > budget.max_tool_calls:
                raise StorageInputError("Run tool-call budget is exhausted")
            if source_lines > budget.max_source_lines:
                raise StorageInputError("Run per-source line budget is exhausted")
            if updated_budget.source_lines_used > budget.max_total_source_lines:
                raise StorageInputError("Run source-line budget is exhausted")
            if output_chars > budget.max_output_chars or updated_budget.output_chars_used > budget.max_output_chars:
                raise StorageInputError("Run output budget is exhausted")
            self._connection.execute(
                "INSERT INTO run_tool_results (run_id, request_id, completed_sequence, kind, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    result.run_id,
                    result.request_id,
                    result.completed_sequence,
                    result.kind,
                    _json_text(result),
                ),
            )
            self._insert_run_event(event)
            updated_run = _validated_run(record.run, budget=updated_budget)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return event.sequence
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("Run tool result conflicts with an existing request") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not persist Run tool result: {exc}") from exc

    def reserve_run_model_call(
        self,
        run_id: str,
        *,
        input_chars: int,
        repair: bool = False,
    ) -> RunRecord:
        """Reserve one model request and its complete prompt before sending it."""

        if input_chars < 1:
            raise StorageInputError("Run model input must contain at least one character")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run_id)
            if record.run.status not in {"queued", "running"}:
                self._connection.execute("COMMIT")
                return record
            if record.run.status != "running":
                raise StorageConflictError("Run model calls require a running Run")
            budget = record.run.budget
            updated_budget = budget.model_copy(
                update={
                    "model_calls_used": budget.model_calls_used + 1,
                    "repair_calls_used": budget.repair_calls_used + (1 if repair else 0),
                    "model_input_chars_used": budget.model_input_chars_used + input_chars,
                }
            )
            if updated_budget.model_calls_used > budget.max_model_calls:
                raise StorageInputError("Run model-call budget is exhausted")
            if updated_budget.repair_calls_used > budget.max_repair_calls:
                raise StorageInputError("Run model repair budget is exhausted")
            if updated_budget.model_input_chars_used > budget.max_model_input_chars:
                raise StorageInputError("Run model input budget is exhausted")
            updated_run = _validated_run(record.run, budget=updated_budget)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run_id)
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not reserve Run model call: {exc}") from exc

    def record_run_model_output(
        self,
        run_id: str,
        *,
        output_chars: int,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> RunRecord:
        """Persist bounded model output usage without persisting provider text."""

        if output_chars < 0:
            raise StorageInputError("Run model output usage is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run_id)
            if record.run.status not in {"queued", "running"}:
                self._connection.execute("COMMIT")
                return record
            if record.run.status != "running":
                raise StorageConflictError("Run model output requires a running Run")
            budget = record.run.budget
            updated_budget = budget.model_copy(
                update={
                    "model_output_chars_used": budget.model_output_chars_used + output_chars,
                    "output_chars_used": budget.output_chars_used + output_chars,
                    "model_input_tokens_used": _accumulate_model_tokens(
                        budget.model_input_tokens_used,
                        input_tokens,
                        budget.model_calls_used,
                    ),
                    "model_output_tokens_used": _accumulate_model_tokens(
                        budget.model_output_tokens_used,
                        output_tokens,
                        budget.model_calls_used,
                    ),
                }
            )
            if updated_budget.model_output_chars_used > budget.max_model_output_chars:
                raise StorageInputError("Run model output budget is exhausted")
            if updated_budget.output_chars_used > budget.max_output_chars:
                raise StorageInputError("Run output budget is exhausted")
            updated_run = _validated_run(record.run, budget=updated_budget)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run_id)
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not record Run model output: {exc}") from exc

    def finish_run(
        self,
        run: Run,
        event: Event,
        report: Report,
    ) -> RunRecord:
        """Persist one terminal event, report, and terminal Run state atomically."""

        terminal_types = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
        if event.type not in terminal_types or event.run_id != run.run_id:
            raise StorageInputError("invalid terminal Run event")
        if run.status not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise StorageInputError("terminal Run status is invalid")
        expected_status = {
            "run.completed": "succeeded",
            "run.failed": "failed",
            "run.cancelled": "cancelled",
            "run.interrupted": "interrupted",
        }[event.type]
        if run.status != expected_status:
            raise StorageInputError("terminal event and Run status do not agree")
        if not isinstance(report, Report):
            raise StorageInputError("terminal Runs require a validated report")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run.run_id)
            if record.run.status not in {"queued", "running"}:
                self._connection.execute("COMMIT")
                return record
            if (
                run.project_id != record.run.project_id
                or run.session_id != record.run.session_id
                or run.index_id != record.run.index_id
                or run.input_hash != record.run.input_hash
            ):
                raise StorageInputError("terminal Run crossed its persisted scope")
            next_sequence = self._next_run_sequence(run.run_id)
            if event.sequence != next_sequence:
                raise StorageInputError("terminal Run event sequence is invalid")
            if record.run.budget.events_used + 1 > record.run.budget.max_events:
                raise StorageInputError("Run event budget is exhausted")
            updated_budget = record.run.budget.model_copy(update={"events_used": record.run.budget.events_used + 1})
            updated_run = _validated_run(
                record.run,
                status=run.status,
                started_at=run.started_at,
                ended_at=run.ended_at,
                report_id=run.report_id or report.report_id,
                budget=updated_budget,
            )
            self._validate_terminal_report(report, updated_run, event)
            self._insert_run_report(report)
            self._insert_run_event(event)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run.run_id)
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.IntegrityError as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageConflictError("Run report conflicts with existing data") from exc
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not finish Run: {exc}") from exc

    def cancel_run(self, run_id: str, reason: str, cancelled_at: datetime) -> tuple[RunRecord, int | None]:
        """Cancel a queued/running Run once; repeated calls return its terminal state."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run_id)
            if record.run.status not in {"queued", "running"}:
                self._connection.execute("COMMIT")
                return record, None
            next_sequence = self._next_run_sequence(run_id)
            if record.run.budget.events_used + 1 > record.run.budget.max_events:
                raise StorageInputError("Run event budget is exhausted")
            event = RunCancelledEvent(
                run_id=run_id,
                sequence=next_sequence,
                timestamp=cancelled_at,
                payload=RunCancelledPayload(status="cancelled", reason=reason),
            )
            updated_budget = record.run.budget.model_copy(
                update={"events_used": record.run.budget.events_used + 1}
            )
            updated_run = _validated_run(
                record.run,
                status="cancelled",
                ended_at=cancelled_at,
                report_id=f"report-{run_id}",
                budget=updated_budget,
            )
            report = _terminal_run_report(
                record.run,
                status="cancelled",
                terminal_sequence=next_sequence,
                summary="Run was cancelled before it produced a complete analysis conclusion.",
            )
            self._insert_run_report(report)
            self._insert_run_event(event)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run_id), next_sequence
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not cancel Run: {exc}") from exc

    def interrupt_run(self, run_id: str, reason: str, interrupted_at: datetime) -> tuple[RunRecord, int | None]:
        """Interrupt one queued/running Run once; repeated calls return its terminal state."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            record = self.load_run_record(run_id)
            if record.run.status not in {"queued", "running"}:
                self._connection.execute("COMMIT")
                return record, None
            next_sequence = self._next_run_sequence(run_id)
            if record.run.budget.events_used + 1 > record.run.budget.max_events:
                raise StorageInputError("Run event budget is exhausted")
            event = RunInterruptedEvent(
                run_id=run_id,
                sequence=next_sequence,
                timestamp=interrupted_at,
                payload=RunInterruptedPayload(status="interrupted", reason=reason),
            )
            updated_budget = record.run.budget.model_copy(
                update={"events_used": record.run.budget.events_used + 1}
            )
            updated_run = _validated_run(
                record.run,
                status="interrupted",
                ended_at=interrupted_at,
                report_id=f"report-{run_id}",
                budget=updated_budget,
            )
            report = _terminal_run_report(
                record.run,
                status="interrupted",
                terminal_sequence=next_sequence,
                summary="Run was interrupted after its wall-clock budget was exhausted.",
            )
            self._insert_run_report(report)
            self._insert_run_event(event)
            self._update_run_row(updated_run)
            self._connection.execute("COMMIT")
            return self.load_run_record(run_id), next_sequence
        except StorageError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise StorageError(f"could not interrupt Run: {exc}") from exc

    def recover_incomplete_runs(self, interrupted_at: datetime) -> int:
        """Mark stale queued/running Runs interrupted after a service restart."""

        rows = self._connection.execute(
            "SELECT run_id FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at, run_id"
        ).fetchall()
        recovered = 0
        for row in rows:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                record = self.load_run_record(row["run_id"])
                if record.run.status not in {"queued", "running"}:
                    self._connection.execute("COMMIT")
                    continue
                if record.run.mode == "external":
                    active_since = record.run.created_at
                    if interrupted_at >= active_since and interrupted_at - active_since < timedelta(
                        seconds=record.run.budget.max_seconds
                    ):
                        self._connection.execute("COMMIT")
                        continue
                next_sequence = self._next_run_sequence(record.run.run_id)
                if record.run.budget.events_used + 1 > record.run.budget.max_events:
                    raise StorageError("incomplete Run has no room for interruption event")
                event = RunInterruptedEvent(
                    run_id=record.run.run_id,
                    sequence=next_sequence,
                    timestamp=interrupted_at,
                    payload=RunInterruptedPayload(
                        status="interrupted",
                        reason="Run was interrupted by a service restart",
                    ),
                )
                updated_budget = record.run.budget.model_copy(
                    update={"events_used": record.run.budget.events_used + 1}
                )
                updated_run = _validated_run(
                    record.run,
                    status="interrupted",
                    ended_at=interrupted_at,
                    report_id=f"report-{record.run.run_id}",
                    budget=updated_budget,
                )
                report = _terminal_run_report(
                    record.run,
                    status="interrupted",
                    terminal_sequence=next_sequence,
                    summary="Run was interrupted by a service restart before it produced a complete analysis conclusion.",
                )
                self._insert_run_report(report)
                self._insert_run_event(event)
                self._update_run_row(updated_run)
                self._connection.execute("COMMIT")
                recovered += 1
            except StorageError:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except sqlite3.Error as exc:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise StorageError(f"could not recover Run: {exc}") from exc
        return recovered

    def load_run_report(self, project_id: str, session_id: str, run_id: str):
        """Read a saved report only inside its project/session scope."""

        self._require_run_scope(project_id, session_id, run_id)
        row = self._connection.execute(
            "SELECT report_json FROM run_reports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError("Run report was not found")
        try:
            return Report.model_validate(json.loads(row["report_json"]))
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise StorageError("persisted Run report is invalid") from exc

    def _require_run_scope(self, project_id: str, session_id: str, run_id: str) -> RunRecord:
        record = self.load_run_record(run_id)
        if record.run.project_id != project_id or record.run.session_id != session_id:
            raise StorageNotFoundError("Run is outside the requested project/session")
        return record

    def _next_run_sequence(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def _insert_run_event(self, event: Event) -> None:
        self._connection.execute(
            "INSERT INTO run_events (run_id, sequence, type, timestamp, event_json) VALUES (?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.sequence,
                event.type,
                _datetime_text(event.timestamp),
                _json_text(event),
            ),
        )

    def _insert_run_report(self, report: Report) -> None:
        self._connection.execute(
            "INSERT INTO run_reports (report_id, run_id, project_id, session_id, index_id, report_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                report.report_id,
                report.run_id,
                report.project_id,
                report.session_id,
                report.index_id,
                _json_text(report),
            ),
        )

    @staticmethod
    def _validate_terminal_report(report: Report, run: Run, event: Event) -> None:
        if (
            report.run_id != run.run_id
            or report.project_id != run.project_id
            or report.session_id != run.session_id
            or report.index_id != run.index_id
            or report.status != run.status
            or run.report_id != report.report_id
            or report.terminal_sequence != event.sequence
        ):
            raise StorageInputError("terminal report does not match the Run transition")
        event_report_id = getattr(event.payload, "report_id", None)
        if event_report_id is not None and event_report_id != report.report_id:
            raise StorageInputError("terminal event does not match its report")

    def _update_run_row(self, run: Run) -> None:
        self._connection.execute(
            "UPDATE runs SET status = ?, report_id = ?, updated_at = ?, run_json = ? WHERE run_id = ?",
            (
                run.status,
                run.report_id,
                _datetime_text(run.ended_at or run.started_at or run.created_at),
                _json_text(run),
                run.run_id,
            ),
        )

    def search_symbols(
        self,
        index_id: str,
        query: str,
        *,
        limit: int = 40,
    ) -> tuple[SymbolSearchResult, ...]:
        """Search qualified names and captured source text using safe FTS tokens."""

        return self.search_symbols_bounded(index_id, query, limit=limit).matches

    def search_symbols_bounded(
        self,
        index_id: str,
        query: str,
        *,
        limit: int = 40,
    ) -> SearchQueryResult:
        """Search names first, then scoped body matches within fixed budgets.

        The file-level FTS rows are only a candidate index. Body matches are
        rechecked against each symbol's immutable source span so a token in one
        function does not make every function in that file a match. Module
        symbols intentionally retain the full-file span for navigation. Every
        database read and source-file load is bounded by the query-local budget;
        a partial result explains which budget stopped the search.
        """

        self._require_index(index_id)
        try:
            params = SearchCodeParams(query=query, limit=limit)
        except ValidationError as exc:
            raise StorageInputError(str(exc)) from exc
        tokens = _search_tokens(params.query)
        if not tokens:
            return SearchQueryResult((), False, None)
        fts_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        name_query = ".".join(tokens)
        budget = _SearchBudget()
        name_candidates: dict[str, _SearchCandidate] = {}

        def name_layer(where_sql: str, where_params: tuple[object, ...], priority: int) -> bool:
            """Load one deterministic, bounded name layer.

            ``True`` means the public result probe is full and the body layer
            must not run. A full probe is enough to report max_results without
            reading lower-priority candidates.
            """

            target = params.limit + 1
            remaining = budget.remaining_candidates
            if remaining <= 0:
                budget.set_reason("search budget exceeded: max_candidates")
                return True
            read_limit = min(target, remaining)
            excluded = tuple(name_candidates)
            exclusion_sql = ""
            if excluded:
                placeholders = ", ".join("?" for _ in excluded)
                exclusion_sql = f" AND symbol_id NOT IN ({placeholders})"
            rows = self._search_query_rows(
                budget,
                "SELECT symbol_id, path, qualified_name, kind, start_line, end_line FROM symbols "
                f"WHERE index_id = ? AND ({where_sql}){exclusion_sql} "
                "ORDER BY qualified_name COLLATE NOCASE, path COLLATE BINARY, symbol_id COLLATE BINARY "
                "LIMIT ?",
                (index_id, *where_params, *excluded, read_limit),
            )
            if rows is None or budget.reason is not None:
                return True
            if not budget.record_candidates(len(rows)):
                return True
            for row in rows:
                symbol = _symbol_from_row(index_id, row)
                name_candidates[symbol.symbol_id] = _SearchCandidate(
                    symbol=symbol,
                    priority=priority,
                    body_score=math.inf,
                    source_path=None,
                )
            if len(rows) >= read_limit:
                if read_limit < target:
                    budget.set_reason("search budget exceeded: max_candidates")
                else:
                    budget.set_reason("max_results reached")
                return True
            return False

        escaped_name = _escape_search_like(name_query)
        if len(tokens) > 1:
            exact_where = (
                "qualified_name COLLATE NOCASE = ? OR "
                "qualified_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
            )
            if name_layer(exact_where, (name_query, f"%.{escaped_name}"), priority=0):
                return self._finish_symbol_search(budget, tokens, params.limit, name_candidates, {})
        else:
            leaf_where = (
                "qualified_name COLLATE NOCASE = ? OR "
                "qualified_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
            )
            if name_layer(leaf_where, (name_query, f"%.{escaped_name}"), priority=1):
                return self._finish_symbol_search(budget, tokens, params.limit, name_candidates, {})

        partial_where = " AND ".join(
            "qualified_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
            for _ in tokens
        )
        partial_params = tuple(f"%{_escape_search_like(token)}%" for token in tokens)
        if name_layer(partial_where, partial_params, priority=2):
            return self._finish_symbol_search(budget, tokens, params.limit, name_candidates, {})

        body_candidates: dict[str, _SearchCandidate] = {}
        source_cache: dict[str, _SearchSource] = {}
        token_set = set(tokens)
        body_offset = 0
        body_exhausted = False
        while not body_exhausted and len(name_candidates) + len(body_candidates) < params.limit + 1:
            if not budget.check_time():
                break
            remaining = budget.remaining_candidates
            if remaining <= 0:
                budget.set_reason("search budget exceeded: max_candidates")
                break
            batch_limit = min(_SEARCH_BODY_BATCH_SIZE, remaining)
            excluded = tuple(name_candidates)
            exclusion_sql = ""
            if excluded:
                placeholders = ", ".join("?" for _ in excluded)
                exclusion_sql = f" AND symbols.symbol_id NOT IN ({placeholders})"
            body_rows = self._search_query_rows(
                budget,
                "SELECT symbol_fts.symbol_id, symbols.path, symbols.qualified_name, symbols.kind, "
                "symbols.start_line, symbols.end_line, bm25(symbol_fts) AS body_score "
                "FROM symbol_fts JOIN symbols ON symbols.index_id = symbol_fts.index_id "
                "AND symbols.symbol_id = symbol_fts.symbol_id "
                "WHERE symbol_fts.index_id = ? AND symbol_fts MATCH ?"
                f"{exclusion_sql} "
                "ORDER BY body_score, symbols.qualified_name COLLATE NOCASE, "
                "symbols.path COLLATE BINARY, symbols.symbol_id COLLATE BINARY "
                "LIMIT ? OFFSET ?",
                (index_id, fts_query, *excluded, batch_limit, body_offset),
            )
            if body_rows is None or budget.reason is not None:
                break
            if not budget.record_candidates(len(body_rows)):
                break
            if not body_rows:
                body_exhausted = True
                break

            source_paths = [row["path"] for row in body_rows if row["symbol_id"] not in name_candidates]
            self._load_search_sources(index_id, source_paths, source_cache, budget)
            if budget.reason == "search budget exceeded: max_seconds":
                break
            for row in body_rows:
                if budget.reason == "search budget exceeded: max_seconds":
                    break
                if budget.reason is None and not budget.check_time():
                    break
                symbol_id = row["symbol_id"]
                if symbol_id in name_candidates or symbol_id in body_candidates:
                    continue
                source = source_cache.get(row["path"])
                if source is None:
                    continue
                symbol = _symbol_from_row(index_id, row)
                if not token_set.issubset(_symbol_source_tokens(source.lines, symbol)):
                    continue
                body_candidates[symbol_id] = _SearchCandidate(
                    symbol=symbol,
                    priority=3,
                    body_score=float(row["body_score"]),
                    source_path=symbol.path,
                )
            body_offset += len(body_rows)
            if (
                budget.reason is None
                and len(name_candidates) + len(body_candidates) >= params.limit + 1
            ):
                budget.set_reason("max_results reached")
            elif len(body_rows) < batch_limit:
                body_exhausted = True
            elif budget.remaining_candidates <= 0:
                budget.set_reason("search budget exceeded: max_candidates")

        return self._finish_symbol_search(budget, tokens, params.limit, name_candidates, body_candidates, source_cache)

    def _search_query_rows(
        self,
        budget: _SearchBudget,
        statement: str,
        parameters: tuple[object, ...],
    ) -> list[sqlite3.Row] | None:
        """Run one bounded search query with a SQLite-level deadline hook."""

        if not budget.check_time():
            return None
        try:
            self._connection.set_progress_handler(budget.progress, _SEARCH_PROGRESS_OPS)
            rows = self._connection.execute(statement, parameters).fetchall()
        except sqlite3.OperationalError as exc:
            if budget.reason is not None:
                return None
            raise StorageError(f"symbol search failed: {exc}") from exc
        finally:
            self._connection.set_progress_handler(None, 0)
        budget.check_time()
        return rows

    def _load_search_sources(
        self,
        index_id: str,
        paths: list[str],
        cache: dict[str, _SearchSource],
        budget: _SearchBudget,
    ) -> None:
        """Load each body-candidate file at most once within source budgets."""

        pending_paths = list(dict.fromkeys(path for path in paths if path not in cache))
        if not pending_paths or not budget.check_time():
            return
        placeholders = ", ".join("?" for _ in pending_paths)
        metadata_rows = self._search_query_rows(
            budget,
            "SELECT path, size FROM snapshot_files "
            f"WHERE index_id = ? AND path IN ({placeholders}) "
            "ORDER BY path COLLATE BINARY LIMIT ?",
            (index_id, *pending_paths, len(pending_paths)),
        )
        if metadata_rows is None or budget.reason == "search budget exceeded: max_seconds":
            return
        sizes = {row["path"]: int(row["size"]) for row in metadata_rows}
        allowed: list[str] = []
        deferred_reason: str | None = None
        for path in pending_paths:
            size = sizes.get(path)
            if size is None:
                continue
            reason = budget.source_limit_reason(size)
            if reason is not None:
                deferred_reason = reason
                break
            budget.reserve_source(size)
            allowed.append(path)
        if not allowed:
            budget.set_reason(deferred_reason)
            return
        if not budget.check_time():
            return
        allowed_placeholders = ", ".join("?" for _ in allowed)
        source_rows = self._search_query_rows(
            budget,
            "SELECT path, size, source_text FROM snapshot_files "
            f"WHERE index_id = ? AND path IN ({allowed_placeholders}) "
            "ORDER BY path COLLATE BINARY LIMIT ?",
            (index_id, *allowed, len(allowed)),
        )
        if source_rows is None or budget.reason == "search budget exceeded: max_seconds":
            return
        for row in source_rows:
            source_text = row["source_text"]
            cache[row["path"]] = _SearchSource(
                lines=tuple(source_text.splitlines()),
                size=len(source_text.encode("utf-8")),
            )
        budget.set_reason(deferred_reason)

    def _finish_symbol_search(
        self,
        budget: _SearchBudget,
        tokens: list[str],
        limit: int,
        name_candidates: dict[str, _SearchCandidate],
        body_candidates: dict[str, _SearchCandidate],
        source_cache: dict[str, _SearchSource] | None = None,
    ) -> SearchQueryResult:
        """Sort accepted candidates and render snippets without new wide reads."""

        candidates = [*name_candidates.values(), *body_candidates.values()]
        if budget.reason is None:
            budget.check_time()
        candidates.sort(key=_search_candidate_sort_key)
        selected = candidates[:limit]
        source_cache = source_cache or {}
        results: list[SymbolSearchResult] = []
        for rank, candidate in enumerate(selected, start=1):
            source = source_cache.get(candidate.source_path or "")
            source_text = ""
            if source is not None:
                source_text = _symbol_source_text_from_lines(source.lines, candidate.symbol)
            results.append(
                SymbolSearchResult(
                    match=SearchMatch(
                        rank=rank,
                        symbol=candidate.symbol,
                        matched_terms=list(tokens),
                    ),
                    snippet=_snippet(source_text, tokens, candidate.symbol.qualified_name),
                )
            )
        if budget.reason is not None:
            truncated = True
            reason = budget.reason
        else:
            truncated = len(candidates) > limit
            reason = "max_results reached" if truncated else None
        return SearchQueryResult(tuple(results), truncated, reason)

    def read_evidence(
        self,
        index_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> Evidence:
        """Read a bounded excerpt from the persisted snapshot, not the worktree."""

        try:
            params = ReadSourceParams(path=path, start_line=start_line, end_line=end_line)
        except ValidationError as exc:
            raise StorageInputError(str(exc)) from exc
        self._require_index(index_id)
        row = self._connection.execute(
            "SELECT content_hash, source_text FROM snapshot_files WHERE index_id = ? AND path = ?",
            (index_id, params.path),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"source path {params.path!r} is not in index {index_id!r}")
        lines = _source_lines(row["source_text"])
        if params.end_line > len(lines):
            raise StorageInputError("source line range is outside the persisted snapshot")
        excerpt = "\n".join(lines[params.start_line - 1 : params.end_line])
        if not excerpt:
            raise StorageInputError("source excerpt is empty and cannot form Evidence")
        if len(excerpt.encode("utf-8")) > 50_000:
            raise StorageInputError("source excerpt exceeds the Evidence output limit")
        evidence_identity = "\0".join(
            [index_id, params.path, str(params.start_line), str(params.end_line), row["content_hash"]]
        )
        evidence_id = f"evidence-{hashlib.sha256(evidence_identity.encode('utf-8')).hexdigest()[:32]}"
        return Evidence(
            evidence_id=evidence_id,
            index_id=index_id,
            path=params.path,
            start_line=params.start_line,
            end_line=params.end_line,
            excerpt=excerpt,
            content_hash=row["content_hash"],
            kind="source",
        )

    def get_neighbors(
        self,
        index_id: str,
        symbol_id: str,
        direction: Literal["callers", "callees", "both"],
        depth: int = 2,
        limit: int = 40,
        *,
        edge_kinds: tuple[str, ...] = ("imports", "calls"),
        max_edges: int = 200,
        max_seconds: float = 1.0,
        max_output_bytes: int = 64_000,
    ) -> NeighborQueryResult:
        """Run bounded BFS over static edges only.

        ``limit`` bounds distinct returned nodes; parallel edges at different
        source locations remain visible until ``max_edges`` or the output budget
        is reached.
        """

        index_row = self._require_index(index_id)
        index_truncated = bool(index_row["truncated"])
        index_truncation_reason = index_row["truncation_reason"]
        index_unresolved_truncated = _nullable_bool(index_row["unresolved_truncated"])
        index_unresolved_truncation_reason = index_row["unresolved_truncation_reason"]
        try:
            params = GetNeighborsParams(
                symbol_id=symbol_id,
                direction=direction,
                depth=depth,
                limit=limit,
            )
        except ValidationError as exc:
            raise StorageInputError(str(exc)) from exc
        if not edge_kinds or any(kind not in {"imports", "calls"} for kind in edge_kinds):
            raise StorageInputError("edge_kinds must contain only imports or calls")
        if (
            max_edges < 1
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
            or max_output_bytes < 1
        ):
            raise StorageInputError("neighbor budgets must be positive")

        root = self._load_symbol(index_id, params.symbol_id)
        direction = params.direction
        nodes: dict[str, Symbol] = {root.symbol_id: root}
        queue: deque[tuple[str, int]] = deque([(root.symbol_id, 0)])
        neighbors: list[Neighbor] = []
        seen_edge_ids: set[str] = set()
        deadline = time.monotonic() + max_seconds
        truncated = False
        truncation_reason: str | None = None
        edge_placeholders = ",".join("?" for _ in edge_kinds)
        output_bytes = _model_size(root)
        if output_bytes > max_output_bytes:
            truncated, truncation_reason = True, "max_output_bytes reached"

        while queue and not truncated:
            if time.monotonic() >= deadline:
                truncated, truncation_reason = True, "max_seconds exceeded"
                break
            current_id, current_depth = queue.popleft()
            if current_depth >= params.depth:
                continue
            if direction == "callers":
                relation_clause = "target_id = ?"
                relation_parameters: tuple[object, ...] = (current_id,)
            elif direction == "callees":
                relation_clause = "source_id = ?"
                relation_parameters = (current_id,)
            else:
                relation_clause = "(source_id = ? OR target_id = ?)"
                relation_parameters = (current_id, current_id)
            rows = self._connection.execute(
                "SELECT edge_id, source_id, target_id, kind, extraction_method, source_path, "
                "start_line, end_line FROM static_edges WHERE index_id = ? "
                f"AND kind IN ({edge_placeholders}) AND {relation_clause} "
                "ORDER BY source_id COLLATE BINARY, target_id COLLATE BINARY, kind, edge_id",
                (index_id, *edge_kinds, *relation_parameters),
            ).fetchall()
            for row in rows:
                if time.monotonic() >= deadline:
                    truncated, truncation_reason = True, "max_seconds exceeded"
                    break
                if row["edge_id"] in seen_edge_ids:
                    continue
                if len(neighbors) >= max_edges:
                    truncated, truncation_reason = True, "max_edges reached"
                    break
                seen_edge_ids.add(row["edge_id"])
                edge = _edge_from_row(index_id, row)
                if direction == "callers":
                    neighbor_id = row["source_id"]
                elif direction == "callees":
                    neighbor_id = row["target_id"]
                elif row["source_id"] == current_id:
                    neighbor_id = row["target_id"]
                else:
                    neighbor_id = row["source_id"]
                neighbor = nodes.get(neighbor_id)
                loaded_neighbor = neighbor
                if neighbor is None:
                    if len(nodes) >= params.limit:
                        truncated, truncation_reason = True, "max_nodes reached"
                        break
                    loaded_neighbor = self._load_symbol(index_id, neighbor_id)
                candidate = Neighbor(symbol=loaded_neighbor, edge=edge)
                candidate_size = _model_size(candidate)
                if neighbor is None:
                    candidate_size += _model_size(loaded_neighbor)
                if output_bytes + candidate_size > max_output_bytes:
                    truncated, truncation_reason = True, "max_output_bytes reached"
                    break
                if neighbor is None:
                    nodes[neighbor_id] = loaded_neighbor
                    if current_depth + 1 < params.depth:
                        queue.append((neighbor_id, current_depth + 1))
                neighbors.append(candidate)
                output_bytes += candidate_size

        return NeighborQueryResult(
            index_id=index_id,
            root=root,
            nodes=tuple(nodes.values()),
            neighbors=tuple(neighbors),
            unresolved=self._load_unresolved(index_id, tuple(nodes)),
            truncated=truncated,
            truncation_reason=truncation_reason,
            index_truncated=index_truncated,
            index_truncation_reason=index_truncation_reason,
            index_unresolved_truncated=index_unresolved_truncated,
            index_unresolved_truncation_reason=index_unresolved_truncation_reason,
        )

    def get_static_impact(
        self,
        index_id: str,
        symbol_id: str,
        direction: Literal["callers", "callees"],
        depth: int = 2,
        limit: int = 40,
        *,
        max_edges: int = 80,
        max_paths: int = 40,
        max_unresolved: int = 40,
        max_seconds: float = 1.0,
        max_output_bytes: int = 64_000,
    ) -> ImpactQueryResult:
        """Enumerate bounded simple paths over same-snapshot static ``calls`` edges."""

        index_row = self._require_index(index_id)
        index_truncated = bool(index_row["truncated"])
        index_truncation_reason = index_row["truncation_reason"]
        index_unresolved_truncated = _nullable_bool(index_row["unresolved_truncated"])
        index_unresolved_truncation_reason = index_row["unresolved_truncation_reason"]
        if direction not in {"callers", "callees"}:
            raise StorageInputError("impact direction must be callers or callees")
        if (
            depth < 1
            or depth > 2
            or limit < 1
            or limit > 40
            or max_edges < 1
            or max_paths < 1
            or max_unresolved < 1
            or not math.isfinite(max_seconds)
            or max_seconds <= 0
            or max_output_bytes < 1
        ):
            raise StorageInputError("impact budgets must be within the supported positive limits")

        root = self._load_symbol(index_id, symbol_id)
        nodes: dict[str, Symbol] = {root.symbol_id: root}
        edges: dict[str, CodeEdge] = {}
        paths: list[ImpactPath] = []
        seen_path_edges: set[tuple[str, ...]] = set()
        queue: deque[tuple[str, int, tuple[str, ...], tuple[str, ...]]] = deque(
            [(root.symbol_id, 0, (root.symbol_id,), ())]
        )
        deadline = time.monotonic() + max_seconds
        output_bytes = _model_size(root)
        truncated = output_bytes > max_output_bytes
        truncation_reason: str | None = "max_output_bytes reached" if truncated else None

        def stop(reason: str) -> None:
            nonlocal truncated, truncation_reason
            truncated = True
            truncation_reason = reason

        while queue and not truncated:
            if time.monotonic() >= deadline:
                stop("max_seconds exceeded")
                break
            current_id, current_depth, symbol_path, edge_path = queue.popleft()
            if current_depth >= depth:
                continue
            rows = self._connection.execute(
                "SELECT edge_id, source_id, target_id, kind, extraction_method, source_path, "
                "start_line, end_line FROM static_edges WHERE index_id = ? AND kind = 'calls' "
                f"AND {'target_id = ?' if direction == 'callers' else 'source_id = ?'} "
                "ORDER BY source_id COLLATE BINARY, target_id COLLATE BINARY, edge_id",
                (index_id, current_id),
            ).fetchall()
            for row in rows:
                if time.monotonic() >= deadline:
                    stop("max_seconds exceeded")
                    break
                edge = _edge_from_row(index_id, row)
                neighbor_id = row["source_id"] if direction == "callers" else row["target_id"]
                if neighbor_id in symbol_path:
                    if edge.edge_id not in edges:
                        if len(edges) >= max_edges:
                            stop("max_edges reached")
                            break
                        if output_bytes + _model_size(edge) > max_output_bytes:
                            stop("max_output_bytes reached")
                            break
                        edges[edge.edge_id] = edge
                        output_bytes += _model_size(edge)
                    # Keep the traversal finite and the returned paths simple;
                    # the cycle-closing edge is retained as a static boundary edge.
                    continue
                next_edge_path = (*edge_path, edge.edge_id)
                if next_edge_path in seen_path_edges:
                    continue
                if len(paths) >= max_paths:
                    stop("max_paths reached")
                    break
                if edge.edge_id not in edges and len(edges) >= max_edges:
                    stop("max_edges reached")
                    break
                neighbor = nodes.get(neighbor_id)
                loaded_neighbor = neighbor
                if neighbor is None:
                    if len(nodes) >= limit:
                        stop("max_nodes reached")
                        break
                    loaded_neighbor = self._load_symbol(index_id, neighbor_id)
                path_edges = [edges.get(edge_id) for edge_id in next_edge_path]
                path_edges[-1] = edge
                if any(item is None for item in path_edges):
                    raise StorageError("impact path references an edge that was not loaded")
                path = ImpactPath(
                    path_id=_impact_path_id(index_id, direction, (*symbol_path, neighbor_id), next_edge_path),
                    index_id=index_id,
                    symbol_ids=[*symbol_path, neighbor_id],
                    edges=[item for item in path_edges if item is not None],
                    depth=current_depth + 1,
                )
                candidate_size = _model_size(path)
                if loaded_neighbor is not None and neighbor is None:
                    candidate_size += _model_size(loaded_neighbor)
                if output_bytes + candidate_size > max_output_bytes:
                    stop("max_output_bytes reached")
                    break
                if neighbor is None:
                    assert loaded_neighbor is not None
                    nodes[neighbor_id] = loaded_neighbor
                edges.setdefault(edge.edge_id, edge)
                paths.append(path)
                seen_path_edges.add(next_edge_path)
                output_bytes += candidate_size
                if current_depth + 1 < depth:
                    queue.append((neighbor_id, current_depth + 1, (*symbol_path, neighbor_id), next_edge_path))

        unresolved = self._load_unresolved(index_id, tuple(nodes))
        if len(unresolved) > max_unresolved:
            unresolved = unresolved[:max_unresolved]
            if not truncated:
                stop("max_unresolved reached")
        bounded_unresolved: list[UnresolvedReference] = []
        for item in unresolved:
            if output_bytes + _model_size(item) > max_output_bytes:
                if not truncated:
                    stop("max_output_bytes reached")
                break
            bounded_unresolved.append(item)
            output_bytes += _model_size(item)

        return ImpactQueryResult(
            index_id=index_id,
            root=root,
            nodes=tuple(nodes.values()),
            edges=tuple(edges.values()),
            paths=tuple(paths),
            unresolved=tuple(bounded_unresolved),
            reachable_node_count=max(0, len(nodes) - 1),
            truncated=truncated,
            truncation_reason=truncation_reason,
            index_truncated=index_truncated,
            index_truncation_reason=index_truncation_reason,
            index_unresolved_truncated=index_unresolved_truncated,
            index_unresolved_truncation_reason=index_unresolved_truncation_reason,
        )

    def _load_unresolved(
        self,
        index_id: str,
        symbol_ids: tuple[str, ...],
    ) -> tuple[UnresolvedReference, ...]:
        if not symbol_ids:
            return ()
        placeholders = ",".join("?" for _ in symbol_ids)
        unresolved_rows = self._connection.execute(
            "SELECT unresolved_id, source_symbol_id, path, expression, reason, start_line, end_line "
            "FROM unresolved_references WHERE index_id = ? AND source_symbol_id IN ("
            f"{placeholders}) ORDER BY path COLLATE BINARY, start_line, expression, unresolved_id",
            (index_id, *symbol_ids),
        ).fetchall()
        if not unresolved_rows:
            return ()
        unresolved_ids = tuple(row["unresolved_id"] for row in unresolved_rows)
        target_placeholders = ",".join("?" for _ in unresolved_ids)
        target_rows = self._connection.execute(
            "SELECT unresolved_id, target_symbol_id FROM unresolved_targets "
            f"WHERE index_id = ? AND unresolved_id IN ({target_placeholders}) "
            "ORDER BY unresolved_id, target_symbol_id",
            (index_id, *unresolved_ids),
        ).fetchall()
        targets: dict[str, list[str]] = {}
        for row in target_rows:
            targets.setdefault(row["unresolved_id"], []).append(row["target_symbol_id"])
        return tuple(
            UnresolvedReference(
                unresolved_id=row["unresolved_id"],
                index_id=index_id,
                source_symbol_id=row["source_symbol_id"],
                path=row["path"],
                expression=row["expression"],
                reason=row["reason"],
                possible_target_ids=targets.get(row["unresolved_id"], []),
                start_line=row["start_line"],
                end_line=row["end_line"],
            )
            for row in unresolved_rows
        )

    def _project_from_row(self, row: sqlite3.Row) -> ProjectRecord:
        available_rows = self._connection.execute(
            "SELECT index_id FROM project_indexes WHERE project_id = ? "
            "ORDER BY associated_at DESC, index_id DESC",
            (row["project_id"],),
        ).fetchall()
        return ProjectRecord(
            project_id=row["project_id"],
            name=row["name"],
            repository_id=row["repository_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            available_index_ids=tuple(item["index_id"] for item in available_rows),
            latest_index_id=row["latest_index_id"],
            building_index_id=row["building_index_id"],
        )

    def _check_fts5(self) -> None:
        try:
            self._connection.execute(
                "CREATE VIRTUAL TABLE temp._codetrail_fts_probe USING fts5(value)"
            )
            self._connection.execute("DROP TABLE temp._codetrail_fts_probe")
        except sqlite3.OperationalError as exc:
            raise StorageCapabilityError("SQLite FTS5 is not available") from exc

    def _insert_index(self, relation: RelationIndex, payload_hash: str) -> None:
        index = relation.snapshot.index
        self._connection.execute(
            "INSERT INTO index_snapshots (index_id, schema_version, repository_id, revision, dirty, "
            "snapshot_hash, rules_version, configuration_hash, status, truncated, truncation_reason, "
            "unresolved_truncated, unresolved_truncation_reason, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                index.index_id,
                index.schema_version,
                index.repository_id,
                index.revision,
                int(index.dirty),
                index.snapshot_hash,
                index.rules_version,
                index.configuration_hash,
                index.status,
                int(relation.truncated),
                relation.truncation_reason,
                None if relation.unresolved_truncated is None else int(relation.unresolved_truncated),
                relation.unresolved_truncation_reason,
                payload_hash,
            ),
        )
        for ordinal, row in enumerate(_issue_rows(relation)):
            self._connection.execute(
                "INSERT INTO snapshot_issues (index_id, ordinal, stage, path, issue_kind, message, size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    index.index_id,
                    ordinal,
                    row["stage"],
                    row["path"],
                    row["kind"],
                    row["message"],
                    row["size"],
                ),
            )
        for source_file in relation.snapshot.files:
            self._connection.execute(
                "INSERT INTO snapshot_files (index_id, path, size, content_hash, source_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    index.index_id,
                    source_file.path,
                    source_file.size,
                    source_file.content_hash,
                    source_file.text,
                ),
            )
        for symbol in relation.symbols:
            self._connection.execute(
                "INSERT INTO symbols (index_id, symbol_id, path, qualified_name, kind, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    index.index_id,
                    symbol.symbol_id,
                    symbol.path,
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.start_line,
                    symbol.end_line,
                ),
            )
        file_texts = {source_file.path: source_file.text for source_file in relation.snapshot.files}
        for edge in relation.edges:
            self._connection.execute(
                "INSERT INTO static_edges (index_id, edge_id, source_id, target_id, kind, extraction_method, "
                "source_path, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    index.index_id,
                    edge.edge_id,
                    edge.source_id,
                    edge.target_id,
                    edge.kind,
                    edge.extraction_method,
                    edge.source_location.path,
                    edge.source_location.start_line,
                    edge.source_location.end_line,
                ),
            )
        for unresolved in relation.unresolved:
            self._connection.execute(
                "INSERT INTO unresolved_references (index_id, unresolved_id, source_symbol_id, path, expression, "
                "reason, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    index.index_id,
                    unresolved.unresolved_id,
                    unresolved.source_symbol_id,
                    unresolved.path,
                    unresolved.expression,
                    unresolved.reason,
                    unresolved.start_line,
                    unresolved.end_line,
                ),
            )
            for target_id in unresolved.possible_target_ids:
                self._connection.execute(
                    "INSERT INTO unresolved_targets (index_id, unresolved_id, target_symbol_id) VALUES (?, ?, ?)",
                    (index.index_id, unresolved.unresolved_id, target_id),
                )
        for symbol in relation.symbols:
            self._connection.execute(
                "INSERT INTO symbol_fts (index_id, symbol_id, qualified_name, source_text) VALUES (?, ?, ?, ?)",
                (index.index_id, symbol.symbol_id, symbol.qualified_name, file_texts[symbol.path]),
            )

    def _require_index(self, index_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM index_snapshots WHERE index_id = ?",
            (index_id,),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(f"index {index_id!r} was not found")
        return row

    def _load_symbol(self, index_id: str, symbol_id: str) -> Symbol:
        row = self._connection.execute(
            "SELECT symbol_id, path, qualified_name, kind, start_line, end_line FROM symbols "
            "WHERE index_id = ? AND symbol_id = ?",
            (index_id, symbol_id),
        ).fetchone()
        if row is None:
            raise StorageNotFoundError(
                f"symbol {symbol_id!r} was not found in index {index_id!r}"
            )
        return _symbol_from_row(index_id, row)

    def _validate_relation(self, relation: RelationIndex) -> None:
        index = relation.snapshot.index
        symbols = relation.symbols
        symbol_by_id: dict[str, Symbol] = {}
        if relation.truncated and not relation.truncation_reason:
            raise StorageInputError("truncated relations must explain the truncation")
        if not relation.truncated and relation.truncation_reason:
            raise StorageInputError("non-truncated relations cannot have a truncation reason")
        if relation.unresolved_truncated is True and not relation.unresolved_truncation_reason:
            raise StorageInputError("truncated unresolved details must explain the truncation")
        if relation.unresolved_truncated is not True and relation.unresolved_truncation_reason:
            raise StorageInputError(
                "non-truncated or unknown unresolved details cannot have a truncation reason"
            )
        files = {source_file.path: source_file for source_file in relation.snapshot.files}
        if len(files) != len(relation.snapshot.files):
            raise StorageInputError("snapshot file paths must be unique")
        for source_file in relation.snapshot.files:
            if not re.fullmatch(r"[0-9a-f]{64}", source_file.content_hash):
                raise StorageInputError("snapshot file content hashes must be SHA-256")
        for symbol in symbols:
            if symbol.index_id != index.index_id:
                raise StorageInputError("symbol belongs to a different index_id")
            if symbol.symbol_id in symbol_by_id:
                raise StorageInputError("symbol IDs must be unique within an index")
            symbol_by_id[symbol.symbol_id] = symbol
        try:
            GraphSnapshot(
                index_id=index.index_id,
                nodes=list(symbols),
                static_edges=list(relation.edges),
                unresolved=list(relation.unresolved),
                truncated=relation.truncated,
                truncation_reason=relation.truncation_reason,
            )
        except ValidationError as exc:
            raise StorageInputError(str(exc)) from exc
        for edge in relation.edges:
            location = edge.source_location
            source_file = files.get(location.path)
            if source_file is None or location.end_line > len(_source_lines(source_file.text)):
                raise StorageInputError("edge source location is outside the snapshot")
        for unresolved in relation.unresolved:
            source_file = files.get(unresolved.path)
            if source_file is None or unresolved.end_line > len(_source_lines(source_file.text)):
                raise StorageInputError("unresolved location is outside the snapshot")
            if not set(unresolved.possible_target_ids).issubset(symbol_by_id):
                raise StorageInputError("unresolved candidate crosses the snapshot boundary")


def _nullable_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _index_from_row(row: sqlite3.Row) -> IndexSnapshot:
    return IndexSnapshot(
        schema_version=row["schema_version"],
        index_id=row["index_id"],
        repository_id=row["repository_id"],
        revision=row["revision"],
        dirty=bool(row["dirty"]),
        snapshot_hash=row["snapshot_hash"],
        status=row["status"],
        rules_version=row["rules_version"],
        configuration_hash=row["configuration_hash"],
    )


def _symbol_from_row(index_id: str, row: sqlite3.Row) -> Symbol:
    return Symbol(
        symbol_id=row["symbol_id"],
        index_id=index_id,
        path=row["path"],
        qualified_name=row["qualified_name"],
        kind=row["kind"],
        start_line=row["start_line"],
        end_line=row["end_line"],
    )


def _edge_from_row(index_id: str, row: sqlite3.Row) -> CodeEdge:
    return CodeEdge(
        edge_id=row["edge_id"],
        index_id=index_id,
        source_id=row["source_id"],
        target_id=row["target_id"],
        kind=row["kind"],
        extraction_method=row["extraction_method"],
        source_location=SourceLocation(
            path=row["source_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
        ),
    )


def _definition_cache_sources(
    source_files: tuple[SnapshotFile, ...],
) -> dict[str, SnapshotFile]:
    source_by_path: dict[str, SnapshotFile] = {}
    for source_file in source_files:
        if source_file.path in source_by_path:
            raise StorageInputError("source snapshot contains duplicate paths")
        source_by_path[source_file.path] = source_file
    return source_by_path


def _validate_definition_cache_entry_lines(
    entry: DefinitionCacheEntry,
    source_file: SnapshotFile,
) -> None:
    line_count = max(1, len(source_file.text.splitlines()))
    for definition in entry.definitions:
        if definition.end_line > line_count:
            raise ValueError("definition cache line range exceeds source")
    if entry.issue is not None and entry.issue.path != source_file.path:
        raise ValueError("definition cache issue path does not match source")


def _validate_relation_cache_lines(
    facts: RelationFileFacts,
    source_file: SnapshotFile,
) -> None:
    if facts.path != source_file.path:
        raise ValueError("relation cache path does not match source")
    if facts.syntax_error:
        return
    line_count = max(1, len(source_file.text.splitlines()))

    def check_scope(scope: RelationScopeFacts) -> None:
        if not isinstance(scope, RelationScopeFacts):
            raise ValueError("relation cache scope is invalid")
        if scope.start_line > line_count:
            raise ValueError("relation cache scope line exceeds source")
        for import_fact in scope.bindings.imports:
            check_range(import_fact.start_line, import_fact.end_line, "import")
        for event in scope.events:
            if isinstance(event, RelationImportEvent):
                fact = event.fact
                check_range(fact.start_line, fact.end_line, "import")
            elif isinstance(event, RelationCallEvent):
                fact = event.fact
                check_range(fact.start_line, fact.end_line, "call")
            elif isinstance(event, RelationScopeEvent):
                check_scope(event.scope)
            else:
                raise ValueError("relation cache event is invalid")

    def check_range(start_line: int, end_line: int, label: str) -> None:
        if start_line > line_count or end_line > line_count:
            raise ValueError(f"relation cache {label} line exceeds source")

    if facts.scope is None:
        raise ValueError("valid relation cache facts require a scope")
    check_scope(facts.scope)


def _issue_key(issue: SnapshotIssue) -> tuple[object, ...]:
    return (issue.path, issue.kind, issue.message, issue.size)


def _issue_rows(relation: RelationIndex) -> list[dict[str, object]]:
    snapshot_counts = Counter(_issue_key(issue) for issue in relation.snapshot.issues)
    rows: list[dict[str, object]] = []
    for issue in relation.issues:
        key = _issue_key(issue)
        stage = "snapshot" if snapshot_counts[key] else "definitions"
        if snapshot_counts[key]:
            snapshot_counts[key] -= 1
        rows.append(
            {
                "stage": stage,
                "path": issue.path,
                "kind": issue.kind,
                "message": issue.message,
                "size": issue.size,
            }
        )
    return rows


def _payload_hash(relation: RelationIndex) -> str:
    payload = {
        "index": relation.snapshot.index.model_dump(mode="json"),
        "files": [
            {
                "path": source_file.path,
                "size": source_file.size,
                "content_hash": source_file.content_hash,
                "text": source_file.text,
            }
            for source_file in relation.snapshot.files
        ],
        "issues": _issue_rows(relation),
        "symbols": [symbol.model_dump(mode="json") for symbol in relation.symbols],
        "edges": [edge.model_dump(mode="json") for edge in relation.edges],
        "unresolved": [item.model_dump(mode="json") for item in relation.unresolved],
        "truncated": relation.truncated,
        "truncation_reason": relation.truncation_reason,
        "unresolved_truncated": relation.unresolved_truncated,
        "unresolved_truncation_reason": relation.unresolved_truncation_reason,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _search_tokens(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", query.casefold(), flags=re.UNICODE)))


def _escape_search_like(value: str) -> str:
    """Escape user text used in a SQLite LIKE pattern."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_candidate_sort_key(candidate: _SearchCandidate) -> tuple[int, float, str, str, str]:
    """Sort name matches ahead of body matches with deterministic ties."""

    return (
        candidate.priority,
        candidate.body_score if candidate.priority == 3 else 0.0,
        candidate.symbol.qualified_name.casefold(),
        candidate.symbol.path,
        candidate.symbol.symbol_id,
    )


def _symbol_source_text(source_text: str, symbol: Symbol) -> str:
    """Return only the immutable source span owned by one indexed symbol."""

    return _symbol_source_text_from_lines(tuple(source_text.splitlines()), symbol)


def _symbol_source_text_from_lines(lines: tuple[str, ...], symbol: Symbol) -> str:
    """Return one symbol span from a file that has already been line-split."""

    start = max(0, symbol.start_line - 1)
    end = min(len(lines), symbol.end_line)
    if start >= end:
        return ""
    return "\n".join(lines[start:end])


def _symbol_source_tokens(lines: tuple[str, ...], symbol: Symbol) -> set[str]:
    """Tokenize only one symbol span from a cached source file."""

    start = max(0, symbol.start_line - 1)
    end = min(len(lines), symbol.end_line)
    if start >= end:
        return set()
    tokens: set[str] = set()
    for line in lines[start:end]:
        tokens.update(_search_tokens(line))
    return tokens


def _snippet(source_text: str, tokens: list[str], qualified_name: str, max_chars: int = 240) -> str:
    folded = source_text.casefold()
    positions = [folded.find(token.casefold()) for token in tokens if folded.find(token.casefold()) >= 0]
    if not positions:
        return qualified_name[:max_chars]
    position = min(positions)
    start = max(0, position - 80)
    end = min(len(source_text), start + max_chars)
    snippet = source_text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(source_text):
        snippet += "…"
    return snippet


def _source_lines(source_text: str) -> list[str]:
    lines = source_text.splitlines()
    return lines or [""]


def _datetime_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _datetime_value(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _encode_feature_generation_cursor(
    created_at: str,
    generation_id: str,
    *,
    project_id: str,
    index_id: str | None,
) -> str:
    payload = json.dumps(
        {
            "created_at": created_at,
            "generation_id": generation_id,
            "project_id": project_id,
            "index_id": index_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_feature_generation_cursor(
    cursor: str | None,
    *,
    project_id: str,
    index_id: str | None,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 500:
        raise StorageInputError("feature generation history cursor is invalid")
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        created_at = payload["created_at"]
        generation_id = payload["generation_id"]
        cursor_project_id = payload["project_id"]
        cursor_index_id = payload["index_id"]
        _datetime_value(created_at)
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise StorageInputError("feature generation history cursor is invalid") from exc
    if (
        not isinstance(created_at, str)
        or not isinstance(generation_id, str)
        or not generation_id
        or len(generation_id) > 200
        or cursor_project_id != project_id
        or cursor_index_id != index_id
    ):
        raise StorageInputError("feature generation history cursor does not match the requested scope")
    return created_at, generation_id


def _encode_session_cursor(
    updated_at: datetime,
    session_id: str,
    *,
    project_id: str,
) -> str:
    payload = json.dumps(
        {
            "kind": "sessions",
            "updated_at": _datetime_text(updated_at),
            "session_id": session_id,
            "project_id": project_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_session_cursor(
    cursor: str | None,
    *,
    project_id: str,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 500:
        raise StorageInputError("session cursor is invalid")
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        kind = payload["kind"]
        updated_at = payload["updated_at"]
        session_id = payload["session_id"]
        cursor_project_id = payload["project_id"]
        _datetime_value(updated_at)
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise StorageInputError("session cursor is invalid") from exc
    if (
        kind != "sessions"
        or not isinstance(updated_at, str)
        or not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 200
        or cursor_project_id != project_id
    ):
        raise StorageInputError("session cursor does not match the requested project")
    return updated_at, session_id


def _encode_run_cursor(
    created_at: str,
    run_id: str,
    *,
    project_id: str,
    session_id: str,
) -> str:
    payload = json.dumps(
        {
            "kind": "runs",
            "created_at": created_at,
            "run_id": run_id,
            "project_id": project_id,
            "session_id": session_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_run_cursor(
    cursor: str | None,
    *,
    project_id: str,
    session_id: str,
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 500:
        raise StorageInputError("Run cursor is invalid")
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        kind = payload["kind"]
        created_at = payload["created_at"]
        run_id = payload["run_id"]
        cursor_project_id = payload["project_id"]
        cursor_session_id = payload["session_id"]
        _datetime_value(created_at)
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise StorageInputError("Run cursor is invalid") from exc
    if (
        kind != "runs"
        or not isinstance(created_at, str)
        or not isinstance(run_id, str)
        or not run_id
        or len(run_id) > 200
        or cursor_project_id != project_id
        or cursor_session_id != session_id
    ):
        raise StorageInputError("Run cursor does not match the requested project/session")
    return created_at, run_id


def _encode_session_message_cursor(
    sequence: int,
    *,
    project_id: str,
    session_id: str,
) -> str:
    payload = json.dumps(
        {
            "kind": "session_messages",
            "sequence": sequence,
            "project_id": project_id,
            "session_id": session_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_session_message_cursor(
    cursor: str | None,
    *,
    project_id: str,
    session_id: str,
) -> int | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 500:
        raise StorageInputError("session message cursor is invalid")
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        kind = payload["kind"]
        sequence = payload["sequence"]
        cursor_project_id = payload["project_id"]
        cursor_session_id = payload["session_id"]
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise StorageInputError("session message cursor is invalid") from exc
    if (
        kind != "session_messages"
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
        or cursor_project_id != project_id
        or cursor_session_id != session_id
    ):
        raise StorageInputError("session message cursor does not match the requested session")
    return sequence


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        project_id=row["project_id"],
        index_id=row["index_id"],
        feature_generation_id=row["feature_generation_id"],
        topic=row["topic"],
        created_at=_datetime_value(row["created_at"]),
        updated_at=_datetime_value(row["updated_at"]),
        message_count=int(row["message_count"]),
        idempotency_key=row["idempotency_key"],
        request_fingerprint=row["request_fingerprint"],
    )


def _session_message_from_row(row: sqlite3.Row) -> SessionMessage:
    return SessionMessage(
        message_id=row["message_id"],
        session_id=row["session_id"],
        sequence=int(row["sequence"]),
        source=row["source"],  # type: ignore[arg-type]
        content=row["content"],
        created_at=_datetime_value(row["created_at"]),
    )


def _managed_source_from_row(row: sqlite3.Row) -> ManagedSourceRecord:
    return ManagedSourceRecord(
        repository_id=row["repository_id"],
        project_id=row["project_id"],
        name=row["name"],
        source_path=row["source_path"],
        normalized_source_path=row["normalized_source_path"],
        storage_path=row["storage_path"],
        created_at=_datetime_value(row["created_at"]),
        source_kind=row["source_kind"],
        source_url=row["source_url"],
        requested_ref=row["requested_ref"],
        resolved_commit=row["resolved_commit"],
        content_hash=row["content_hash"],
        cleanup_status=row["cleanup_status"],
        cleanup_error=row["cleanup_error"],
        temporary_path=row["temporary_path"],
    )


def _import_attempt_from_row(row: sqlite3.Row) -> ImportAttemptRecord:
    return ImportAttemptRecord(
        attempt_id=row["attempt_id"],
        project_id=row["project_id"],
        repository_id=row["repository_id"],
        source_path=row["source_path"],
        normalized_source_path=row["normalized_source_path"],
        name=row["name"],
        revision=row["revision"],
        dirty=bool(row["dirty"]),
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        phase=row["phase"],
        index_id=row["index_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_datetime_value(row["created_at"]),
        updated_at=_datetime_value(row["updated_at"]),
        source_kind=row["source_kind"],
        source_url=row["source_url"],
        requested_ref=row["requested_ref"],
        resolved_commit=row["resolved_commit"],
        content_hash=row["content_hash"],
        cleanup_status=row["cleanup_status"],
        cleanup_error=row["cleanup_error"],
        temporary_path=row["temporary_path"],
    )


def _model_size(model: object) -> int:
    model_dump_json = getattr(model, "model_dump_json", None)
    if callable(model_dump_json):
        return len(model_dump_json().encode("utf-8"))
    return len(json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _impact_path_id(
    index_id: str,
    direction: str,
    symbol_ids: tuple[str, ...],
    edge_ids: tuple[str, ...],
) -> str:
    identity = "\0".join((index_id, direction, *symbol_ids, *edge_ids))
    return f"impact-path-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _json_text(value: object) -> str:
    """Serialize a contract or plain payload canonically for storage and hashing."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validated_run(run: Run, **updates: object) -> Run:
    """Apply a persisted Run transition through the current contract validators."""

    return Run.model_validate(run.model_copy(update=updates))


def _accumulate_model_tokens(
    existing: int | None,
    current: int | None,
    attempted_calls: int,
) -> int | None:
    """Keep token usage unknown once any provider response omits usage."""

    if current is None or existing is None and attempted_calls > 1:
        return None
    return current if existing is None else existing + current


def _terminal_run_report(
    run: Run,
    *,
    status: Literal["cancelled", "interrupted"],
    terminal_sequence: int,
    summary: str,
) -> Report:
    """Create the bounded report retained for non-analysis terminal states."""

    return Report(
        report_id=f"report-{run.run_id}",
        run_id=run.run_id,
        project_id=run.project_id,
        session_id=run.session_id,
        index_id=run.index_id,
        status=status,
        outcome=status,
        fixture_mode=run.mode == "fixture",
        summary=summary,
        graph=GraphSnapshot(index_id=run.index_id),
        terminal_sequence=terminal_sequence,
    )
