"""SQLite/FTS5 persistence and bounded queries for one CodeTrail index."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .contracts import (
    CodeEdge,
    Evidence,
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
    truncated: bool
    truncation_reason: str | None


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
"""


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
            truncated=truncated,
            truncation_reason=truncation_reason,
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


def _model_size(model: object) -> int:
    model_dump_json = getattr(model, "model_dump_json", None)
    if callable(model_dump_json):
        return len(model_dump_json().encode("utf-8"))
    return len(json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
