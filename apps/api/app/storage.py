"""SQLite/FTS5 persistence and bounded queries for one CodeTrail index."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .contracts import (
    CodeEdge,
    Evidence,
    FeatureGeneration,
    GetNeighborsParams,
    GraphSnapshot,
    IndexSnapshot,
    Neighbor,
    ReadSourceParams,
    SearchCodeParams,
    SearchMatch,
    SourceLocation,
    Symbol,
    UnresolvedReference,
)
from .indexing import DefinitionIndex, SnapshotFile, SnapshotIssue, SourceSnapshot
from .relations import RelationIndex


_UNSET = object()


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
class NeighborQueryResult:
    """Bounded static neighbors and all nodes needed to render their edges."""

    index_id: str
    root: Symbol
    nodes: tuple[Symbol, ...]
    neighbors: tuple[Neighbor, ...]
    unresolved: tuple[UnresolvedReference, ...]
    truncated: bool
    truncation_reason: str | None


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
    status TEXT NOT NULL CHECK (status IN ('ready', 'building', 'failed')),
    truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    truncation_reason TEXT,
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

    def finish_project_index(self, project_id: str, index_id: str) -> None:
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
            return FeatureGeneration.model_validate(json.loads(row["result_json"]))
        except (json.JSONDecodeError, ValidationError) as exc:
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
        if cursor_values is not None:
            created_at, generation_id = cursor_values
            where.append("(created_at < ? OR (created_at = ? AND generation_id < ?))")
            parameters.extend([created_at, created_at, generation_id])
        rows = self._connection.execute(
            "SELECT generation_id, created_at, result_json FROM feature_generations "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC, generation_id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        generations: list[FeatureGeneration] = []
        for row in page_rows:
            try:
                generation = FeatureGeneration.model_validate(json.loads(row["result_json"]))
            except (json.JSONDecodeError, ValidationError) as exc:
                raise StorageError("persisted feature generation is invalid") from exc
            if generation.project_id != project_id or (
                index_id is not None and generation.index.index_id != index_id
            ):
                raise StorageError("persisted feature generation scope is invalid")
            generations.append(generation)
        next_cursor = (
            _encode_feature_generation_cursor(
                str(page_rows[-1]["created_at"]),
                str(page_rows[-1]["generation_id"]),
                project_id=project_id,
                index_id=index_id,
            )
            if has_more and page_rows
            else None
        )
        return FeatureGenerationPage(
            generations=tuple(generations),
            next_cursor=next_cursor,
            has_more=has_more,
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
        """Search and report whether the result cap omitted additional matches."""

        self._require_index(index_id)
        try:
            params = SearchCodeParams(query=query, limit=limit)
        except ValidationError as exc:
            raise StorageInputError(str(exc)) from exc
        tokens = _search_tokens(params.query)
        if not tokens:
            return SearchQueryResult((), False, None)
        fts_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        try:
            rows = self._connection.execute(
                "SELECT symbol_id, qualified_name, source_text FROM symbol_fts "
                "WHERE index_id = ? AND symbol_fts MATCH ? "
                "ORDER BY bm25(symbol_fts), qualified_name COLLATE BINARY, symbol_id "
                "LIMIT ?",
                (index_id, fts_query, params.limit + 1),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise StorageError(f"symbol search failed: {exc}") from exc

        truncated = len(rows) > params.limit
        results: list[SymbolSearchResult] = []
        for rank, row in enumerate(rows[: params.limit], start=1):
            symbol = self._load_symbol(index_id, row["symbol_id"])
            source_text = row["source_text"]
            matched_terms = [
                token
                for token in tokens
                if token.casefold() in f"{row['qualified_name']}\n{source_text}".casefold()
            ]
            results.append(
                SymbolSearchResult(
                    match=SearchMatch(
                        rank=rank,
                        symbol=symbol,
                        matched_terms=matched_terms or [tokens[0]],
                    ),
                    snippet=_snippet(source_text, tokens, row["qualified_name"]),
                )
            )
        return SearchQueryResult(
            matches=tuple(results),
            truncated=truncated,
            truncation_reason="max_results reached" if truncated else None,
        )

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

        self._require_index(index_id)
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
            "snapshot_hash, status, truncated, truncation_reason, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                index.index_id,
                index.schema_version,
                index.repository_id,
                index.revision,
                int(index.dirty),
                index.snapshot_hash,
                index.status,
                int(relation.truncated),
                relation.truncation_reason,
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


def _index_from_row(row: sqlite3.Row) -> IndexSnapshot:
    return IndexSnapshot(
        schema_version=row["schema_version"],
        index_id=row["index_id"],
        repository_id=row["repository_id"],
        revision=row["revision"],
        dirty=bool(row["dirty"]),
        snapshot_hash=row["snapshot_hash"],
        status=row["status"],
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
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _search_tokens(query: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\w+", query.casefold(), flags=re.UNICODE)))


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
