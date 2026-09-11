"""Configured repository indexing and snapshot query services."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

from .contracts import (
    CreateIndexRequest,
    GetNeighborsParams,
    GetNeighborsSuccess,
    IndexIssue,
    IndexResponse,
    NeighborsIndexResponse,
    ReadSourceParams,
    ReadSourceSuccess,
    SearchCodeParams,
    SearchCodeSuccess,
    SearchIndexResponse,
    SourceIndexResponse,
    ToolError,
    ToolRequest,
    ToolResult,
)
from .indexing import SnapshotBoundaryError, build_source_snapshot, extract_definitions
from .relations import extract_relations
from .storage import (
    IndexStore,
    StorageConflictError,
    StorageError,
    StorageInputError,
    StorageNotFoundError,
)


class ServiceError(RuntimeError):
    """A safe, stable error intended for an API response."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


class RepositoryRegistry:
    """Allow-list of repository IDs and server-owned local roots."""

    def __init__(self, roots: Mapping[str, str | Path]) -> None:
        normalized: dict[str, Path] = {}
        for repository_id, repository_root in roots.items():
            if not isinstance(repository_id, str) or not repository_id or len(repository_id) > 200:
                raise ValueError("repository IDs must be non-empty strings of at most 200 characters")
            if not isinstance(repository_root, (str, Path)):
                raise ValueError("repository roots must be filesystem paths")
            candidate = Path(repository_root)
            if candidate.is_symlink():
                raise ValueError("repository roots must not be symbolic links")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise ValueError("configured repository root cannot be resolved") from exc
            if not resolved.is_dir():
                raise ValueError("configured repository root must be a directory")
            normalized[repository_id] = resolved
        self._roots = normalized

    @classmethod
    def from_environment(cls) -> RepositoryRegistry:
        raw = os.getenv("CODETRAIL_REPOSITORIES", "").strip()
        if not raw:
            return cls({})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("CODETRAIL_REPOSITORIES must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("CODETRAIL_REPOSITORIES must be a JSON object")
        return cls(payload)

    @property
    def repository_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._roots))

    def root_for(self, repository_id: str) -> Path:
        root = self._roots.get(repository_id)
        if root is None:
            raise ServiceError("not_found", 404, "repository is not configured")
        return root

    @contextmanager
    def store_for_repository(self, repository_id: str) -> Iterator[IndexStore]:
        store = IndexStore.for_repository(self.root_for(repository_id))
        try:
            yield store
        finally:
            store.close()

    @contextmanager
    def store_for_index(self, index_id: str) -> Iterator[IndexStore]:
        storage_error = False
        for repository_id in self.repository_ids:
            database_path = self._roots[repository_id] / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            store: IndexStore | None = None
            try:
                store = IndexStore(database_path)
                found = store.has_index(index_id)
            except StorageError:
                storage_error = True
                found = False
            if found:
                try:
                    yield store
                finally:
                    store.close()
                return
            if store is not None:
                store.close()
        if storage_error:
            raise ServiceError("internal_error", 500, "local index storage failed")
        raise ServiceError("not_found", 404, "index was not found")


T = TypeVar("T")


class IndexService:
    """Build immutable indexes and serve bounded queries from persisted snapshots."""

    def __init__(
        self,
        registry: RepositoryRegistry,
        *,
        max_concurrent_indexes: int = 1,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        if max_concurrent_indexes < 1 or max_file_bytes < 1:
            raise ValueError("service budgets must be positive")
        self.registry = registry
        self.max_file_bytes = max_file_bytes
        self._index_slots = threading.BoundedSemaphore(max_concurrent_indexes)

    def create_index(self, request: CreateIndexRequest) -> IndexResponse:
        root = self.registry.root_for(request.repository_id)
        if not self._index_slots.acquire(blocking=False):
            raise ServiceError("budget_exceeded", 429, "index capacity is currently busy")
        try:
            try:
                snapshot = build_source_snapshot(
                    root,
                    repository_id=request.repository_id,
                    revision=request.revision,
                    dirty=request.dirty,
                    max_file_bytes=self.max_file_bytes,
                )
                definitions = extract_definitions(snapshot)
                relation = extract_relations(definitions)
                with self.registry.store_for_repository(request.repository_id) as store:
                    store.write_index(relation)
                    persisted = store.load_index(snapshot.index.index_id)
            except SnapshotBoundaryError as exc:
                raise ServiceError(
                    "internal_error",
                    503,
                    "configured repository is unavailable",
                ) from exc
            except StorageConflictError as exc:
                raise ServiceError("conflict", 409, "index content conflicts with an existing index") from exc
            except StorageError as exc:
                raise ServiceError("internal_error", 500, "local index storage failed") from exc
            return _index_response(persisted)
        finally:
            self._index_slots.release()

    def get_index(self, index_id: str) -> IndexResponse:
        return self._with_index(index_id, lambda store: _index_response(store.load_index(index_id)))

    def search(self, index_id: str, query: str, limit: int) -> SearchIndexResponse:
        def operation(store: IndexStore) -> SearchIndexResponse:
            result = store.search_symbols_bounded(index_id, query, limit=limit)
            return SearchIndexResponse(
                index_id=index_id,
                matches=[item.match for item in result.matches],
                truncated=result.truncated,
                truncation_reason=result.truncation_reason,
            )

        return self._with_index(index_id, operation)

    def read_source(
        self,
        index_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> SourceIndexResponse:
        return self._with_index(
            index_id,
            lambda store: SourceIndexResponse(
                index_id=index_id,
                evidence=store.read_evidence(index_id, path, start_line, end_line),
            ),
        )

    def neighbors(
        self,
        index_id: str,
        symbol_id: str,
        direction: str,
        depth: int,
        limit: int,
        edge_kinds: tuple[str, ...] | None = None,
    ) -> NeighborsIndexResponse:
        def operation(store: IndexStore) -> NeighborsIndexResponse:
            result = store.get_neighbors(
                index_id,
                symbol_id,
                direction,  # type: ignore[arg-type]
                depth,
                limit,
                edge_kinds=edge_kinds or ("imports", "calls"),
            )
            return NeighborsIndexResponse(
                index_id=index_id,
                neighbors=list(result.neighbors),
                truncated=result.truncated,
                truncation_reason=result.truncation_reason,
            )

        return self._with_index(index_id, operation)

    def execute_tool(self, request: ToolRequest, completed_sequence: int = 1) -> ToolResult:
        if completed_sequence < 1:
            raise ValueError("completed_sequence must be positive")
        try:
            if isinstance(request.params, SearchCodeParams):
                result = self.search(request.index_id, request.params.query, request.params.limit)
                return SearchCodeSuccess(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    index_id=request.index_id,
                    completed_sequence=completed_sequence,
                    matches=result.matches,
                    truncated=result.truncated,
                    truncation_reason=result.truncation_reason,
                )
            if isinstance(request.params, ReadSourceParams):
                result = self.read_source(
                    request.index_id,
                    request.params.path,
                    request.params.start_line,
                    request.params.end_line,
                )
                return ReadSourceSuccess(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    index_id=request.index_id,
                    completed_sequence=completed_sequence,
                    evidence=result.evidence,
                )
            if isinstance(request.params, GetNeighborsParams):
                result = self.neighbors(
                    request.index_id,
                    request.params.symbol_id,
                    request.params.direction,
                    request.params.depth,
                    request.params.limit,
                )
                return GetNeighborsSuccess(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    index_id=request.index_id,
                    completed_sequence=completed_sequence,
                    neighbors=result.neighbors,
                    truncated=result.truncated,
                    truncation_reason=result.truncation_reason,
                )
            raise ServiceError("invalid_input", 400, "unsupported tool parameters")
        except ServiceError as exc:
            return _tool_failure(request, completed_sequence, exc.code, exc.message)
        except StorageNotFoundError:
            return _tool_failure(request, completed_sequence, "not_found", "requested index, symbol, or source was not found")
        except StorageInputError:
            return _tool_failure(request, completed_sequence, "invalid_input", "tool parameters are invalid")
        except StorageError:
            return _tool_failure(request, completed_sequence, "internal_error", "local index storage failed")

    def _with_index(self, index_id: str, operation: Callable[[IndexStore], T]) -> T:
        try:
            with self.registry.store_for_index(index_id) as store:
                return operation(store)
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested index, symbol, or source was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "query parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc


def _index_response(relation) -> IndexResponse:
    return IndexResponse(
        index=relation.snapshot.index,
        issues=[
            IndexIssue(path=issue.path, kind=issue.kind, message=issue.message, size=issue.size)
            for issue in relation.issues
        ],
        truncated=relation.truncated,
        truncation_reason=relation.truncation_reason,
    )


def _tool_failure(
    request: ToolRequest,
    completed_sequence: int,
    code: str,
    message: str,
) -> ToolResult:
    tool_error = ToolError(
        code=code if code in {"not_found", "invalid_input", "budget_exceeded", "internal_error"} else "internal_error",  # type: ignore[arg-type]
        message=message,
        retryable=False,
    )
    base = {
        "request_id": request.request_id,
        "run_id": request.run_id,
        "index_id": request.index_id,
        "completed_sequence": completed_sequence,
        "error": tool_error,
    }
    if isinstance(request.params, SearchCodeParams):
        from .contracts import SearchCodeFailure

        return SearchCodeFailure(**base)
    if isinstance(request.params, ReadSourceParams):
        from .contracts import ReadSourceFailure

        return ReadSourceFailure(**base)
    from .contracts import GetNeighborsFailure

    return GetNeighborsFailure(**base)
