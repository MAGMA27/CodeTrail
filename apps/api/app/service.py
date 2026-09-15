"""Configured repository indexing and snapshot query services."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, TypeVar
from uuid import uuid4

from .contracts import (
    CandidateLink,
    CreateIndexRequest,
    CreateProjectIndexRequest,
    CreateProjectRequest,
    CreateSessionRequest,
    AppendSessionMessageRequest,
    CreateRunRequest,
    CreateRunResponse,
    CodeUnderstandingFinal,
    CancelRunRequest,
    CancelRunResponse,
    Event,
    FeatureGeneration,
    FeatureGenerationListResponse,
    FeatureGenerationRequest,
    FeatureGenerationResponse,
    FeatureGenerationSummary,
    FeatureGraphResponse,
    FreshnessReasonCode,
    GetNeighborsParams,
    GetNeighborsFailure,
    GetNeighborsSuccess,
    GraphUpdatedEvent,
    GraphUpdatedPayload,
    GraphSnapshot,
    Finding,
    ImpactDirection,
    ImportAttempt,
    ImportAttemptResponse,
    GitHubImportRequest,
    GitHubImportResponse,
    IndexFreshness,
    IndexIssue,
    IndexResponse,
    IndexRefreshResponse,
    IndexSnapshot,
    IssueModelFinal,
    LocalImportRequest,
    LocalImportResponse,
    NeighborsIndexResponse,
    ProjectOverview,
    Project,
    ProjectIndexListResponse,
    ProjectListResponse,
    Report,
    RunImpactResponse,
    ReportResponse,
    ReadSourceParams,
    ReadSourceFailure,
    ReadSourceSuccess,
    RefreshProjectIndexRequest,
    Run,
    ExternalRunFinal,
    RunBudget,
    RunEventsResponse,
    RunInput,
    RunListResponse,
    RunStatusResponse,
    RunToolResultsResponse,
    RunStartedEvent,
    RunStartedPayload,
    RunCompletedEvent,
    RunCompletedPayload,
    ToolCompletedEvent,
    ToolCompletedPayload,
    ToolFailedEvent,
    ToolFailedPayload,
    ToolStartedEvent,
    ToolStartedPayload,
    ToolParams,
    SearchCodeParams,
    SearchCodeFailure,
    SearchCodeSuccess,
    SearchIndexResponse,
    Session,
    SessionListResponse,
    SessionMessage,
    SessionMessagesResponse,
    SessionResponse,
    SourceIndexResponse,
    ToolError,
    ToolRequest,
    ToolResult,
)
from .config import ModelProviderConfig, load_model_config
from .indexing import (
    SnapshotBoundaryError,
    build_source_snapshot,
    current_source_rules_version,
    extract_definitions,
    index_configuration_hash,
    scan_source_manifest,
)
from .github import GitHubArchive, GitHubClient, GitHubError, GitHubRepository, GitHubResolution, parse_github_repository_url
from .relations import (
    INDEX_BUILD_IDENTITY_VERSION,
    RELATION_INDEX_IDENTITY_VERSION,
    extract_relations,
    relation_coverage_limitations,
)
from .overview import build_project_overview
from .feature_generation import (
    FeatureGenerationEngine,
    FeatureModelAdapter,
    build_cache_key,
    new_generation_id,
    resolve_model,
)
from .issue_analysis import ISSUE_EXECUTION_VERSION, ISSUE_PROMPT_VERSION, IssueModelAdapter
from .feature_graph import build_feature_graph
from .storage import (
    IndexStore,
    ImportAttemptRecord,
    ManagedSourceRecord,
    ManagementStore,
    ProjectRecord,
    StorageConflictError,
    StorageError,
    StorageInputError,
    StorageNotFoundError,
    SessionRecord,
    RunRecord,
)
from .run_execution import (
    RunExecutor,
    RunExecutorCapacityError,
    RunExecutorStartupError,
    RunReservation,
)


class ServiceError(RuntimeError):
    """A safe, stable error intended for an API response."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class RepositorySpec:
    """Source and storage locations for one configured or managed repository."""

    repository_id: str
    source_root: Path
    storage_root: Path
    normalized_source_path: str
    managed: bool
    project_id: str | None = None
    source_kind: str = "local"
    source_url: str | None = None


class RepositoryRegistry:
    """Allow-list plus persistent managed local sources and service storage."""

    def __init__(
        self,
        roots: Mapping[str, str | Path],
        *,
        data_root: str | Path | None = None,
        allowed_import_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
        mcp_local_import_enabled: bool | None = None,
        github_import_enabled: bool | None = None,
        mcp_github_import_enabled: bool | None = None,
        mcp_feature_generation_enabled: bool | None = None,
        mcp_index_write_enabled: bool | None = None,
        mcp_session_write_enabled: bool | None = None,
        mcp_run_write_enabled: bool | None = None,
        mcp_external_run_enabled: bool | None = None,
        real_run_enabled: bool | None = None,
        mcp_real_run_enabled: bool | None = None,
        model_config: ModelProviderConfig | None = None,
    ) -> None:
        normalized: dict[str, RepositorySpec] = {}
        for repository_id, repository_root in roots.items():
            if not isinstance(repository_id, str) or not repository_id or len(repository_id) > 200:
                raise ValueError("repository IDs must be non-empty strings of at most 200 characters")
            if not isinstance(repository_root, (str, Path)):
                raise ValueError("repository roots must be filesystem paths")
            candidate = Path(repository_root)
            if _is_reparse_point(candidate):
                raise ValueError("repository roots must not be symbolic links or junctions")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise ValueError("configured repository root cannot be resolved") from exc
            if not resolved.is_dir():
                raise ValueError("configured repository root must be a directory")
            normalized[repository_id] = RepositorySpec(
                repository_id=repository_id,
                source_root=resolved,
                storage_root=resolved,
                normalized_source_path=_normalized_path_key(resolved),
                managed=False,
                source_kind="local",
            )
        configured_data_root = data_root or os.getenv("CODETRAIL_DATA_DIR") or (Path.cwd() / ".codetrail")
        self._data_root = _prepare_data_root(configured_data_root)
        self._model_config = model_config if model_config is not None else load_model_config(self._data_root)
        self._management_store = ManagementStore(self._data_root / "registry.sqlite3")
        self._allowed_import_roots = self._prepare_allowed_import_roots(
            allowed_import_roots
            if allowed_import_roots is not None
            else tuple(spec.source_root for spec in normalized.values())
        )
        self._mcp_local_import_enabled = (
            _env_flag("CODETRAIL_MCP_LOCAL_IMPORT_ENABLED")
            if mcp_local_import_enabled is None
            else mcp_local_import_enabled
        )
        self._github_import_enabled = (
            _env_flag("CODETRAIL_GITHUB_IMPORT_ENABLED")
            if github_import_enabled is None
            else github_import_enabled
        )
        self._mcp_github_import_enabled = (
            _env_flag("CODETRAIL_MCP_GITHUB_IMPORT_ENABLED")
            if mcp_github_import_enabled is None
            else mcp_github_import_enabled
        )
        self._mcp_feature_generation_enabled = (
            _env_flag("CODETRAIL_MCP_FEATURE_GENERATION_ENABLED")
            if mcp_feature_generation_enabled is None
            else mcp_feature_generation_enabled
        )
        self._mcp_index_write_enabled = (
            _env_flag("CODETRAIL_MCP_INDEX_WRITE_ENABLED")
            if mcp_index_write_enabled is None
            else mcp_index_write_enabled
        )
        self._mcp_session_write_enabled = (
            _env_flag("CODETRAIL_MCP_SESSION_WRITE_ENABLED")
            if mcp_session_write_enabled is None
            else mcp_session_write_enabled
        )
        self._mcp_run_write_enabled = (
            _env_flag("CODETRAIL_MCP_RUN_WRITE_ENABLED")
            if mcp_run_write_enabled is None
            else mcp_run_write_enabled
        )
        self._mcp_external_run_enabled = (
            _env_flag("CODETRAIL_MCP_EXTERNAL_RUN_ENABLED")
            if mcp_external_run_enabled is None
            else mcp_external_run_enabled
        )
        self._real_run_enabled = (
            _env_flag("CODETRAIL_REAL_RUN_ENABLED")
            if real_run_enabled is None
            else real_run_enabled
        )
        self._mcp_real_run_enabled = (
            _env_flag("CODETRAIL_MCP_REAL_RUN_ENABLED")
            if mcp_real_run_enabled is None
            else mcp_real_run_enabled
        )
        self._lock = threading.RLock()
        self._runtime_source_roots: dict[str, Path] = {}
        self._specs = normalized
        self.refresh_managed_sources()

    @classmethod
    def from_environment(
        cls,
        *,
        github_import_enabled: bool | None = None,
        mcp_github_import_enabled: bool | None = None,
        mcp_feature_generation_enabled: bool | None = None,
        mcp_index_write_enabled: bool | None = None,
        mcp_session_write_enabled: bool | None = None,
        mcp_run_write_enabled: bool | None = None,
        mcp_external_run_enabled: bool | None = None,
        real_run_enabled: bool | None = None,
        mcp_real_run_enabled: bool | None = None,
        model_config: ModelProviderConfig | None = None,
    ) -> RepositoryRegistry:
        raw = os.getenv("CODETRAIL_REPOSITORIES", "").strip()
        payload: dict[str, str] = {}
        if raw:
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CODETRAIL_REPOSITORIES must be a JSON object") from exc
            if not isinstance(decoded, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()):
                raise ValueError("CODETRAIL_REPOSITORIES must be a JSON object")
            payload = decoded
        allowed_raw = os.getenv("CODETRAIL_ALLOWED_IMPORT_ROOTS", "").strip()
        allowed: list[str] | None = None
        if allowed_raw:
            try:
                allowed_payload = json.loads(allowed_raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CODETRAIL_ALLOWED_IMPORT_ROOTS must be a JSON array") from exc
            if not isinstance(allowed_payload, list) or not all(isinstance(item, str) for item in allowed_payload):
                raise ValueError("CODETRAIL_ALLOWED_IMPORT_ROOTS must be a JSON array of paths")
            allowed = allowed_payload
        return cls(
            payload,
            allowed_import_roots=allowed,
            github_import_enabled=github_import_enabled,
            mcp_github_import_enabled=mcp_github_import_enabled,
            mcp_feature_generation_enabled=mcp_feature_generation_enabled,
            mcp_index_write_enabled=mcp_index_write_enabled,
            mcp_session_write_enabled=mcp_session_write_enabled,
            mcp_run_write_enabled=mcp_run_write_enabled,
            mcp_external_run_enabled=mcp_external_run_enabled,
            real_run_enabled=real_run_enabled,
            mcp_real_run_enabled=mcp_real_run_enabled,
            model_config=model_config,
        )

    @property
    def repository_ids(self) -> tuple[str, ...]:
        self.refresh_managed_sources()
        with self._lock:
            return tuple(sorted(self._specs))

    @property
    def data_root(self) -> Path:
        return self._data_root

    @property
    def model_config(self) -> ModelProviderConfig:
        return self._model_config

    @property
    def management_store(self) -> ManagementStore:
        return self._management_store

    @property
    def mcp_local_import_enabled(self) -> bool:
        return self._mcp_local_import_enabled

    @property
    def github_import_enabled(self) -> bool:
        return self._github_import_enabled

    @property
    def mcp_github_import_enabled(self) -> bool:
        return self._mcp_github_import_enabled

    @property
    def mcp_feature_generation_enabled(self) -> bool:
        return self._mcp_feature_generation_enabled

    @property
    def mcp_index_write_enabled(self) -> bool:
        return self._mcp_index_write_enabled

    @property
    def mcp_session_write_enabled(self) -> bool:
        return self._mcp_session_write_enabled

    @property
    def mcp_run_write_enabled(self) -> bool:
        return self._mcp_run_write_enabled

    @property
    def mcp_external_run_enabled(self) -> bool:
        return self._mcp_external_run_enabled

    @property
    def real_run_enabled(self) -> bool:
        return self._real_run_enabled

    @property
    def mcp_real_run_enabled(self) -> bool:
        return self._mcp_real_run_enabled

    @property
    def allowed_import_roots(self) -> tuple[Path, ...]:
        return self._allowed_import_roots

    def refresh_managed_sources(self) -> None:
        records = self._management_store.list_sources()
        with self._lock:
            for record in records:
                existing = self._specs.get(record.repository_id)
                if existing is not None and not existing.managed:
                    continue
                storage_root = Path(record.storage_path)
                if not _path_is_within(storage_root, self._data_root):
                    continue
                self._specs[record.repository_id] = RepositorySpec(
                    repository_id=record.repository_id,
                    source_root=Path(record.source_path),
                    storage_root=storage_root,
                    normalized_source_path=record.normalized_source_path,
                    managed=True,
                    project_id=record.project_id,
                    source_kind=record.source_kind,
                    source_url=record.source_url,
                )

    def spec_for(self, repository_id: str) -> RepositorySpec:
        self.refresh_managed_sources()
        with self._lock:
            spec = self._specs.get(repository_id)
        if spec is None:
            raise ServiceError("not_found", 404, "repository is not configured")
        return spec

    def root_for(self, repository_id: str) -> Path:
        return self.source_root_for(repository_id)

    def source_root_for(self, repository_id: str) -> Path:
        spec = self.spec_for(repository_id)
        with self._lock:
            return self._runtime_source_roots.get(repository_id, spec.source_root)

    def bind_runtime_source(self, repository_id: str, source_root: Path) -> None:
        root = source_root.resolve(strict=False)
        if not _path_is_within(root, self.github_temp_root):
            raise ServiceError("internal_error", 500, "managed GitHub temp path is invalid")
        with self._lock:
            self._runtime_source_roots[repository_id] = root

    def unbind_runtime_source(self, repository_id: str) -> None:
        with self._lock:
            self._runtime_source_roots.pop(repository_id, None)

    @property
    def github_temp_root(self) -> Path:
        root = (self._data_root / "github-imports").resolve(strict=False)
        if not _path_is_within(root, self._data_root):
            raise ServiceError("internal_error", 500, "managed GitHub temp root is invalid")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def github_temp_path(self, attempt_id: str) -> Path:
        if not attempt_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in attempt_id):
            raise ServiceError("internal_error", 500, "managed GitHub temp path identifier is invalid")
        path = (self.github_temp_root / attempt_id).resolve(strict=False)
        if not _path_is_within(path, self.github_temp_root):
            raise ServiceError("internal_error", 500, "managed GitHub temp path is invalid")
        return path

    def storage_root_for(self, repository_id: str) -> Path:
        return self.spec_for(repository_id).storage_root

    def is_managed(self, repository_id: str) -> bool:
        return self.spec_for(repository_id).managed

    def managed_storage_path(self, project_id: str) -> Path:
        storage_root = (self._data_root / "projects" / project_id).resolve(strict=False)
        if not _path_is_within(storage_root, self._data_root):
            raise ServiceError("internal_error", 500, "managed storage path is invalid")
        return storage_root

    def repository_for_source(self, normalized_source_path: str) -> RepositorySpec | None:
        self.refresh_managed_sources()
        with self._lock:
            return next(
                (spec for spec in self._specs.values() if spec.normalized_source_path == normalized_source_path),
                None,
            )

    def validate_import_source(self, path: str) -> tuple[Path, str]:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ServiceError("invalid_input", 400, "local import path must be absolute")
        if _is_reparse_point(candidate):
            raise ServiceError("invalid_input", 400, "local import path must not be a symbolic link or junction")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ServiceError("invalid_input", 400, "local import directory cannot be resolved") from exc
        if not resolved.is_dir():
            raise ServiceError("invalid_input", 400, "local import path must be a directory")
        if not os.access(resolved, os.R_OK):
            raise ServiceError("invalid_input", 400, "local import directory is not readable")
        allowed_root = next(
            (root for root in self._allowed_import_roots if _path_is_within(resolved, root)),
            None,
        )
        if allowed_root is None:
            raise ServiceError("invalid_input", 400, "local import path is outside allowed roots")
        if _contains_reparse_point(resolved, allowed_root):
            raise ServiceError("invalid_input", 400, "local import path crosses a symbolic link or junction")
        if _path_is_within(self._data_root, resolved) or _path_is_within(resolved, self._data_root):
            raise ServiceError("invalid_input", 400, "local import path conflicts with service data")
        try:
            next(resolved.iterdir(), None)
        except OSError as exc:
            raise ServiceError("invalid_input", 400, "local import directory cannot be read") from exc
        return resolved, _normalized_path_key(resolved)

    @contextmanager
    def store_for_repository(self, repository_id: str) -> Iterator[IndexStore]:
        store = IndexStore.for_repository(self.storage_root_for(repository_id))
        try:
            yield store
        finally:
            store.close()

    @contextmanager
    def store_for_index(self, index_id: str) -> Iterator[IndexStore]:
        storage_error = False
        for repository_id in self.repository_ids:
            database_path = self.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
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

    def _prepare_allowed_import_roots(
        self,
        roots: tuple[str | Path, ...] | list[str | Path],
    ) -> tuple[Path, ...]:
        normalized: list[Path] = []
        for raw_root in roots:
            candidate = Path(raw_root)
            if _is_reparse_point(candidate):
                raise ValueError("allowed import roots must not be symbolic links or junctions")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise ValueError("allowed import root cannot be resolved") from exc
            if not resolved.is_dir():
                raise ValueError("allowed import root must be a directory")
            if resolved not in normalized:
                normalized.append(resolved)
        return tuple(normalized)


T = TypeVar("T")

_FRESHNESS_MAX_FILES = 2_000
_FRESHNESS_MAX_TOTAL_BYTES = 20 * 1024 * 1024
_FRESHNESS_MAX_SECONDS = 2.0
_FRESHNESS_MAX_CHANGED_PATHS = 100
_LEGACY_REBUILDABLE_FRESHNESS_REASONS: frozenset[FreshnessReasonCode] = frozenset(
    {
        "missing_index_metadata",
        "rules_changed",
        "configuration_changed",
        "source_changed",
    }
)


class IndexService:
    """Build immutable indexes and serve bounded queries from persisted snapshots."""

    def __init__(
        self,
        registry: RepositoryRegistry,
        *,
        max_concurrent_indexes: int = 1,
        max_file_bytes: int = 1_000_000,
        freshness_max_files: int = _FRESHNESS_MAX_FILES,
        freshness_max_total_bytes: int = _FRESHNESS_MAX_TOTAL_BYTES,
        freshness_max_seconds: float = _FRESHNESS_MAX_SECONDS,
    ) -> None:
        if (
            max_concurrent_indexes < 1
            or max_file_bytes < 1
            or freshness_max_files < 1
            or freshness_max_total_bytes < 1
            or freshness_max_seconds <= 0
        ):
            raise ValueError("service budgets must be positive")
        self.registry = registry
        self.max_file_bytes = max_file_bytes
        self.freshness_max_files = freshness_max_files
        self.freshness_max_total_bytes = freshness_max_total_bytes
        self.freshness_max_seconds = freshness_max_seconds
        self.rules_version = current_source_rules_version(RELATION_INDEX_IDENTITY_VERSION)
        self.configuration_hash = index_configuration_hash(
            max_file_bytes=max_file_bytes,
            identity_version=INDEX_BUILD_IDENTITY_VERSION,
            rules_version=self.rules_version,
        )
        self._index_slots = threading.BoundedSemaphore(max_concurrent_indexes)

    def create_index(
        self,
        request: CreateIndexRequest,
        *,
        on_snapshot_ready: Callable[[str], None] | None = None,
        identity_version: str | None = None,
    ) -> IndexResponse:
        root = self.registry.root_for(request.repository_id)
        build_identity_version = identity_version or INDEX_BUILD_IDENTITY_VERSION
        # The rebuild identity is only a namespace for legacy immutable rows;
        # it must not make an otherwise current source configuration stale.
        build_configuration_hash = self.configuration_hash
        if not self._index_slots.acquire(blocking=False):
            raise ServiceError("budget_exceeded", 429, "index capacity is currently busy")
        try:
            try:
                snapshot = build_source_snapshot(
                    root,
                    repository_id=request.repository_id,
                    revision=request.revision,
                    dirty=request.dirty,
                    identity_version=build_identity_version,
                    max_file_bytes=self.max_file_bytes,
                    rules_version=self.rules_version,
                    configuration_hash=build_configuration_hash,
                )
                if not snapshot.files:
                    raise ServiceError(
                        "invalid_input",
                        400,
                        "source directory contains no supported Python files",
                    )
                definitions = extract_definitions(snapshot)
                relation = extract_relations(definitions)
                if on_snapshot_ready is not None:
                    on_snapshot_ready(snapshot.index.index_id)
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

    def check_source_freshness(
        self,
        project_id: str,
        baseline: IndexSnapshot,
    ) -> IndexFreshness:
        """Compare one local source manifest with a persisted index without parsing it."""

        checked_at = datetime.now(timezone.utc)
        current_rules_version = self.rules_version
        current_configuration_hash = self.configuration_hash
        spec = self.registry.spec_for(baseline.repository_id)
        if spec.source_kind == "github":
            return IndexFreshness(
                project_id=project_id,
                baseline_index_id=baseline.index_id,
                status="unknown",
                checked_at=checked_at,
                scan_complete=False,
                current_rules_version=current_rules_version,
                current_configuration_hash=current_configuration_hash,
                reasons=["remote_not_checked"],
            )

        try:
            scan = scan_source_manifest(
                self.registry.source_root_for(baseline.repository_id),
                max_file_bytes=self.max_file_bytes,
                max_files=self.freshness_max_files,
                max_total_bytes=self.freshness_max_total_bytes,
                max_seconds=self.freshness_max_seconds,
            )
            with self.registry.store_for_repository(baseline.repository_id) as store:
                stored_files, stored_issues = store.load_snapshot_manifest(baseline.index_id)
        except SnapshotBoundaryError:
            return IndexFreshness(
                project_id=project_id,
                baseline_index_id=baseline.index_id,
                status="unknown",
                checked_at=checked_at,
                scan_complete=False,
                current_rules_version=current_rules_version,
                current_configuration_hash=current_configuration_hash,
                reasons=["source_unavailable"],
            )
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested project index was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc

        stored_by_path = {
            source_file.path: (source_file.size, source_file.content_hash)
            for source_file in stored_files
        }
        current_by_path = {
            source_file.path: (source_file.size, source_file.content_hash)
            for source_file in scan.files
        }
        added_paths = sorted(set(current_by_path) - set(stored_by_path))
        deleted_paths = sorted(set(stored_by_path) - set(current_by_path))
        modified_paths = sorted(
            path
            for path in set(current_by_path) & set(stored_by_path)
            if current_by_path[path] != stored_by_path[path]
        )
        stored_issue_keys = {(issue.path, issue.kind, issue.size) for issue in stored_issues}
        current_issue_keys = {(issue.path, issue.kind, issue.size) for issue in scan.issues}
        issue_paths = sorted(
            {
                path
                for path, _kind, _size in stored_issue_keys ^ current_issue_keys
                if path != "."
            }
        )
        changed_paths = sorted(
            set(added_paths) | set(deleted_paths) | set(modified_paths) | set(issue_paths)
        )
        changed_paths_truncated = len(changed_paths) > _FRESHNESS_MAX_CHANGED_PATHS
        reasons: list[FreshnessReasonCode] = []

        def add_reason(reason: FreshnessReasonCode) -> None:
            if reason not in reasons:
                reasons.append(reason)

        if changed_paths or stored_issue_keys != current_issue_keys:
            add_reason("source_changed")
        if baseline.rules_version is None:
            add_reason("missing_index_metadata")
        elif baseline.rules_version != current_rules_version:
            add_reason("rules_changed")
        if baseline.configuration_hash is None:
            if "missing_index_metadata" not in reasons:
                add_reason("missing_index_metadata")
        elif baseline.configuration_hash != current_configuration_hash:
            add_reason("configuration_changed")
        for issue in scan.issues:
            if issue.kind == "read_error":
                add_reason("source_read_error")
            elif issue.kind in {"decode_error", "file_too_large", "symlink", "outside_root"}:
                add_reason(issue.kind)  # type: ignore[arg-type]
        if scan.unstable_paths:
            add_reason("file_changed_during_check")
        if scan.scan_truncated:
            add_reason("budget_exceeded")
        if not scan.scan_complete and not scan.scan_truncated and not scan.unstable_paths:
            add_reason("source_read_error")

        uncertain = (
            not scan.scan_complete
            or bool(scan.issues)
            or bool(scan.unstable_paths)
            or baseline.rules_version is None
            or baseline.configuration_hash is None
        )
        status: Literal["consistent", "changed", "unknown"]
        if uncertain:
            status = "unknown"
        elif reasons:
            status = "changed"
        else:
            status = "consistent"
        return IndexFreshness(
            project_id=project_id,
            baseline_index_id=baseline.index_id,
            status=status,
            checked_at=checked_at,
            added_count=len(added_paths),
            modified_count=len(modified_paths),
            deleted_count=len(deleted_paths),
            changed_paths=changed_paths[:_FRESHNESS_MAX_CHANGED_PATHS],
            changed_paths_truncated=changed_paths_truncated,
            scanned_files=scan.scanned_files,
            scanned_bytes=scan.scanned_bytes,
            scan_complete=scan.scan_complete,
            scan_truncated=scan.scan_truncated,
            scan_truncation_reason=scan.scan_truncation_reason,
            current_rules_version=current_rules_version,
            current_configuration_hash=current_configuration_hash,
            reasons=reasons,
        )

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
                root=result.root,
                nodes=list(result.nodes),
                neighbors=list(result.neighbors),
                unresolved=list(result.unresolved),
                truncated=result.truncated,
                truncation_reason=result.truncation_reason,
                index_truncated=result.index_truncated,
                index_truncation_reason=result.index_truncation_reason,
                index_unresolved_truncated=result.index_unresolved_truncated,
                index_unresolved_truncation_reason=result.index_unresolved_truncation_reason,
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
                    root=result.root,
                    nodes=list(result.nodes),
                    neighbors=result.neighbors,
                    unresolved=list(result.unresolved),
                    truncated=result.truncated,
                    truncation_reason=result.truncation_reason,
                    index_truncated=result.index_truncated,
                    index_truncation_reason=result.index_truncation_reason,
                    index_unresolved_truncated=result.index_unresolved_truncated,
                    index_unresolved_truncation_reason=result.index_unresolved_truncation_reason,
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


class WorkspaceService:
    """Own project identity, index associations, and project-scoped queries."""

    def __init__(
        self,
        registry: RepositoryRegistry,
        index_service: IndexService,
        *,
        github_client: GitHubClient | None = None,
        feature_adapter_factory: Callable[[str], FeatureModelAdapter] | None = None,
        run_model_adapter_factory: Callable[[str], IssueModelAdapter] | None = None,
        run_max_concurrent_runs: int | None = None,
    ) -> None:
        self.registry = registry
        self.index_service = index_service
        self.github_client = github_client or GitHubClient()
        self.feature_adapter_factory = feature_adapter_factory
        self.run_model_adapter_factory = run_model_adapter_factory
        self._index_mutation_lock = threading.RLock()
        self._recover_interrupted_builds()
        self._recover_interrupted_runs()
        self.run_executor = RunExecutor(
            self,
            max_concurrent_runs=_configured_run_capacity(run_max_concurrent_runs),
            model_adapter_factory=run_model_adapter_factory,
        )

    def create_project(self, request: CreateProjectRequest) -> Project:
        spec = self.registry.spec_for(request.repository_id)
        project_id = _new_project_id()
        with self.registry.store_for_repository(request.repository_id) as store:
            if not spec.managed:
                store.ensure_legacy_project(
                    _legacy_project_id(request.repository_id),
                    request.repository_id,
                    request.repository_id,
                    datetime.now(timezone.utc),
                )
            record = store.create_project(
                project_id,
                request.name,
                request.repository_id,
                datetime.now(timezone.utc),
            )
        return _project_response(record)

    def list_projects(self) -> ProjectListResponse:
        self._ensure_legacy_projects()
        self._ensure_managed_projects()
        records: list[ProjectRecord] = []
        for repository_id in self.registry.repository_ids:
            database_path = self.registry.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            with self.registry.store_for_repository(repository_id) as store:
                records.extend(store.list_projects())
        records.sort(key=lambda record: (record.created_at, record.project_id))
        return ProjectListResponse(projects=[_project_response(record) for record in records])

    def get_project(self, project_id: str) -> Project:
        return _project_response(self._find_project(project_id))

    def list_project_indexes(self, project_id: str) -> ProjectIndexListResponse:
        project = self._find_project(project_id)
        with self.registry.store_for_repository(project.repository_id) as store:
            indexes = store.list_project_indexes(project.project_id)
            current = store.load_project(project.project_id)
        return ProjectIndexListResponse(
            project_id=project.project_id,
            indexes=list(indexes),
            latest_index_id=current.latest_index_id,
            building_index_id=current.building_index_id,
        )

    def create_session(self, project_id: str, request: CreateSessionRequest) -> SessionResponse:
        """Create one empty session fixed to an available immutable project index."""

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                # The v2 fingerprint records the caller's original index choice. The
                # bound index is stored separately in the session row and must not
                # change when the project's latest index changes between retries.
                fingerprint = _session_creation_fingerprint(
                    project_id,
                    request.index_id,
                    request.feature_generation_id,
                    request.topic,
                )
                record: SessionRecord | None = None
                index: IndexSnapshot | None = None
                if request.idempotency_key is not None:
                    existing = store.find_session_by_idempotency(project_id, request.idempotency_key)
                    if existing is not None:
                        if not _session_creation_fingerprint_matches(existing, project_id, request):
                            raise ServiceError(
                                "conflict",
                                409,
                                "session idempotency key is already bound to a different request",
                            )
                        record = existing
                        if not store.project_has_index(project_id, existing.index_id):
                            raise StorageError("persisted session index is outside its project")
                        index = store.load_index_snapshot(existing.index_id)
                if record is None:
                    current_project = store.load_project(project_id)
                    index_id = request.index_id or current_project.latest_index_id
                    if index_id is None:
                        raise ServiceError("conflict", 409, "project has no available index")
                    if not store.project_has_index(project_id, index_id):
                        raise ServiceError("not_found", 404, "index does not belong to the project")
                    index = store.load_index_snapshot(index_id)
                    if index.status != "ready":
                        raise ServiceError("conflict", 409, "session requires a ready project index")
                    if request.feature_generation_id is not None:
                        generation = store.load_feature_generation(request.feature_generation_id)
                        if generation.project_id != project_id or generation.index.index_id != index_id:
                            raise StorageNotFoundError("feature generation is outside the session snapshot")
                    assert index is not None
                    now = datetime.now(timezone.utc)
                    session = Session(
                        session_id=_new_session_id(),
                        project_id=project_id,
                        index=index,
                        feature_generation_id=request.feature_generation_id,
                        topic=request.topic,
                        created_at=now,
                        updated_at=now,
                    )
                    try:
                        record = store.create_session(
                            session,
                            idempotency_key=request.idempotency_key,
                            request_fingerprint=fingerprint,
                        )
                    except StorageConflictError:
                        if request.idempotency_key is None:
                            raise
                        existing = store.find_session_by_idempotency(project_id, request.idempotency_key)
                        if existing is None or not _session_creation_fingerprint_matches(
                            existing,
                            project_id,
                            request,
                        ):
                            raise
                        record = existing
                        if not store.project_has_index(project_id, existing.index_id):
                            raise StorageError("persisted session index is outside its project")
                        index = store.load_index_snapshot(existing.index_id)
        except ServiceError:
            raise
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "session conflicts with an existing request") from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested project, index, or feature generation was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "session parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local session storage failed") from exc
        assert record is not None and index is not None
        return SessionResponse(session=_session_response(record, index))

    def list_sessions(
        self,
        project_id: str,
        *,
        limit: int = 40,
        cursor: str | None = None,
    ) -> SessionListResponse:
        """Read bounded project sessions without changing the current index selection."""

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.list_sessions(project_id, limit=limit, cursor=cursor)
                index_cache: dict[str, IndexSnapshot] = {}
                sessions: list[Session] = []
                for record in page.sessions:
                    index = index_cache.get(record.index_id)
                    if index is None:
                        index = store.load_index_snapshot(record.index_id)
                        index_cache[record.index_id] = index
                    sessions.append(_session_response(record, index))
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested project, session, or index was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "session list parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local session storage failed") from exc
        return SessionListResponse(
            project_id=project_id,
            sessions=sessions,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def get_session(self, project_id: str, session_id: str) -> SessionResponse:
        """Read one session and its fixed snapshot metadata."""

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store.load_session(session_id)
                if record.project_id != project_id:
                    raise StorageNotFoundError("session is outside the requested project")
                index = store.load_index_snapshot(record.index_id)
                session = _session_response(record, index)
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "session was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local session storage failed") from exc
        return SessionResponse(session=session)

    def list_session_messages(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SessionMessagesResponse:
        """Read one session's append-only messages in server sequence order."""

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.list_session_messages(
                    project_id,
                    session_id,
                    limit=limit,
                    cursor=cursor,
                )
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "session was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "session message parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local session storage failed") from exc
        return SessionMessagesResponse(
            project_id=project_id,
            session_id=session_id,
            messages=list(page.messages),
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def append_session_message(
        self,
        project_id: str,
        session_id: str,
        request: AppendSessionMessageRequest,
    ) -> SessionMessage:
        """Append one visible message without starting a Run or invoking a model."""

        project = self._find_project(project_id)
        fingerprint = _session_message_fingerprint(project_id, session_id, request.source, request.content)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store.load_session(session_id)
                if record.project_id != project_id:
                    raise StorageNotFoundError("session is outside the requested project")
                return store.append_session_message(
                    session_id,
                    message_id=_new_session_message_id(),
                    source=request.source,
                    content=request.content,
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=fingerprint,
                    created_at=datetime.now(timezone.utc),
                )
        except ServiceError:
            raise
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "session message conflicts with an existing request") from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "session was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "session message parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local session storage failed") from exc

    def create_run(
        self,
        project_id: str,
        session_id: str,
        request: CreateRunRequest,
        *,
        via_mcp: bool = False,
    ) -> CreateRunResponse:
        """Freeze a saved Session prefix and submit one explicit bounded Run."""

        if request.mode == "external":
            if not via_mcp or not self.registry.mcp_external_run_enabled:
                raise ServiceError(
                    "forbidden",
                    403,
                    "external Agent Runs require the explicit MCP external-run capability switch",
                )
        elif request.mode == "real":
            enabled = self.registry.mcp_real_run_enabled if via_mcp else self.registry.real_run_enabled
            if not enabled:
                raise ServiceError(
                    "forbidden",
                    403,
                    "real model Runs require the explicit real-run capability switch",
                )
        project = self._find_project(project_id)
        fingerprint = _run_creation_fingerprint(project_id, session_id, request)
        reservation: RunReservation | None = None
        handed_to_worker = False
        created = False
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                if request.idempotency_key is not None:
                    existing = store.find_run_by_idempotency(
                        project_id,
                        session_id,
                        request.idempotency_key,
                    )
                    if existing is not None:
                        if existing.request_fingerprint != fingerprint:
                            raise ServiceError(
                                "conflict",
                                409,
                                "Run idempotency key is already bound to a different request",
                            )
                        return _run_create_response(existing.run)

                session = store.load_session(session_id)
                if session.project_id != project_id:
                    raise StorageNotFoundError("session is outside the requested project")
                if request.index_id != session.index_id:
                    raise ServiceError(
                        "conflict",
                        409,
                        "Run index must match the Session's fixed snapshot",
                    )
                index = store.load_index_snapshot(session.index_id)
                if index.status != "ready":
                    raise ServiceError("conflict", 409, "Run requires a ready Session snapshot")
                feature_generation_id = request.feature_generation_id or session.feature_generation_id
                if feature_generation_id is not None:
                    generation = store.load_feature_generation(feature_generation_id)
                    if generation.project_id != project_id or generation.index.index_id != session.index_id:
                        raise StorageNotFoundError("feature generation is outside the Run snapshot")
                cutoff = request.message_end_sequence or session.message_count
                if cutoff < 1:
                    raise ServiceError("conflict", 409, "Run requires at least one saved Session message")
                messages = store.load_session_messages_for_run(project_id, session_id, cutoff)
                input_chars = sum(len(message.content) for message in messages)
                try:
                    budget = RunBudget.model_validate(
                        {**request.budget.model_dump(mode="python"), "input_chars_used": input_chars}
                    )
                except ValueError as exc:
                    raise ServiceError("budget_exceeded", 409, "Run input exceeds its configured budget") from exc
                input_hash = _run_input_hash(messages)
                frozen_at = datetime.now(timezone.utc)
                run_input = RunInput(
                    project_id=project_id,
                    session_id=session_id,
                    index_id=session.index_id,
                    feature_generation_id=feature_generation_id,
                    message_end_sequence=cutoff,
                    messages=list(messages),
                    input_hash=input_hash,
                    input_chars=input_chars,
                    frozen_at=frozen_at,
                )
                resolved_model = (
                    resolve_model("real", request.model, model_config=self.registry.model_config)
                    if request.mode == "real"
                    else request.model
                )
                run = Run(
                    run_id=f"run-{uuid4().hex}",
                    project_id=project_id,
                    session_id=session_id,
                    index_id=session.index_id,
                    feature_generation_id=feature_generation_id,
                    issue=request.issue,
                    mode=request.mode,
                    analysis_kind=request.analysis_kind,
                    execution_source=request.execution_source,
                    fixture_scenario=request.fixture_scenario,
                    model_provider=(self.registry.model_config.provider if request.mode == "real" else None),
                    model=resolved_model,
                    prompt_version=(request.prompt_version or ISSUE_PROMPT_VERSION) if request.mode == "real" else request.prompt_version,
                    execution_version=(
                        request.execution_version
                        if request.mode == "fixture" or request.execution_version != "run-fixture-v1"
                        else ISSUE_EXECUTION_VERSION
                    ),
                    status="queued",
                    budget=budget,
                    message_end_sequence=cutoff,
                    input_hash=input_hash,
                    input_chars=input_chars,
                    created_at=frozen_at,
                )
                if request.mode != "external":
                    try:
                        reservation = self.run_executor.reserve()
                    except RunExecutorCapacityError as exc:
                        raise ServiceError(
                            "budget_exceeded",
                            429,
                            "Run executor capacity is full; retry this Run later",
                        ) from exc
                try:
                    record, created = store.create_run(
                        run,
                        run_input,
                        idempotency_key=request.idempotency_key,
                        request_fingerprint=fingerprint,
                    )
                except StorageConflictError:
                    try:
                        if request.idempotency_key is None:
                            raise
                        existing = store.find_run_by_idempotency(project_id, session_id, request.idempotency_key)
                        if existing is None or existing.request_fingerprint != fingerprint:
                            raise
                        return _run_create_response(existing.run)
                    finally:
                        if reservation is not None:
                            reservation.release()
                except Exception:
                    if reservation is not None:
                        reservation.release()
                    raise
        except ServiceError:
            raise
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "Session already has an active Run") from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "project, Session, index, or generation was not found") from exc
        except StorageInputError as exc:
            message = str(exc)
            code = "budget_exceeded" if "budget" in message.lower() else "invalid_input"
            raise ServiceError(code, 409 if code == "budget_exceeded" else 400, message) from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run storage failed") from exc
        try:
            if created:
                if request.mode != "external":
                    assert reservation is not None
                    self.run_executor.submit(record.run, reservation=reservation)
                    handed_to_worker = True
            elif reservation is not None:
                reservation.release()
            return _run_create_response(record.run)
        except RunExecutorStartupError as exc:
            raise ServiceError("internal_error", 503, "Run was saved but its worker could not start") from exc
        finally:
            if reservation is not None and not handed_to_worker:
                reservation.release()

    def execute_external_tool(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        index_id: str,
        params: ToolParams,
    ) -> ToolResult:
        """Execute and persist one allow-listed tool call for a Codex-led Run."""

        project = self._find_project(project_id)
        request = ToolRequest(
            request_id=request_id,
            run_id=run_id,
            index_id=index_id,
            params=params,
        )
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store._require_run_scope(project_id, session_id, run_id)
                self._require_external_run(record)
                if record.run.index_id != index_id:
                    raise ServiceError(
                        "conflict",
                        409,
                        "external tool index must match the Run's fixed snapshot",
                    )
                existing = store.load_run_tool_result(run_id, request_id)
                if existing is not None:
                    previous_request = store.load_run_tool_request(run_id, request_id)
                    if previous_request is None or previous_request.params != params or previous_request.index_id != index_id:
                        raise ServiceError(
                            "conflict",
                            409,
                            "external tool request_id is already bound to different parameters",
                        )
                    return existing

                previous_request = store.load_run_tool_request(run_id, request_id)
                if previous_request is not None and (
                    previous_request.params != params or previous_request.index_id != index_id
                ):
                    raise ServiceError(
                        "conflict",
                        409,
                        "external tool request_id is already bound to different parameters",
                    )
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("conflict", 409, "external Run is already terminal")

                record = self._expire_external_run_if_needed(store, record)
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("budget_exceeded", 409, "external Run time budget is exhausted")
                record = self._ensure_external_run_started(store, record)
                record = self._expire_external_run_if_needed(store, record)
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("budget_exceeded", 409, "external Run time budget is exhausted")
                if record.run.budget.tool_calls_used >= record.run.budget.max_tool_calls:
                    raise ServiceError("budget_exceeded", 409, "Run tool-call budget is exhausted")
                if record.run.budget.events_used + 3 > record.run.budget.max_events:
                    raise ServiceError(
                        "budget_exceeded",
                        409,
                        "Run event budget cannot fit a tool call and terminal conclusion",
                    )

                if previous_request is None:
                    started_event = ToolStartedEvent(
                        run_id=run_id,
                        sequence=record.run.budget.events_used + 1,
                        timestamp=datetime.now(timezone.utc),
                        payload=ToolStartedPayload(request=request),
                    )
                    store.append_run_event(started_event)
                    record = store.load_run_record(run_id)
                completed_sequence = record.run.budget.events_used + 1
                try:
                    result = self.index_service.execute_tool(
                        request,
                        completed_sequence=completed_sequence,
                    )
                except Exception:
                    result = _tool_failure(
                        request,
                        completed_sequence,
                        "internal_error",
                        "external tool execution failed unexpectedly",
                    )
                record = self._expire_external_run_if_needed(store, store.load_run_record(run_id))
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("budget_exceeded", 409, "external Run time budget is exhausted")
                if isinstance(result, (SearchCodeFailure, ReadSourceFailure, GetNeighborsFailure)):
                    event: Event = ToolFailedEvent(
                        run_id=run_id,
                        sequence=completed_sequence,
                        timestamp=datetime.now(timezone.utc),
                        payload=ToolFailedPayload(result=result),
                    )
                else:
                    event = ToolCompletedEvent(
                        run_id=run_id,
                        sequence=completed_sequence,
                        timestamp=datetime.now(timezone.utc),
                        payload=ToolCompletedPayload(result=result),
                    )
                store.append_run_tool_result(event, result)
                return result
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "external Run was not found") from exc
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "external Run tool request conflicts with existing data") from exc
        except StorageInputError as exc:
            message = str(exc)
            code = "budget_exceeded" if "budget" in message.lower() else "invalid_input"
            raise ServiceError(code, 409 if code == "budget_exceeded" else 400, message) from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local external Run storage failed") from exc

    def complete_external_run(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        final: ExternalRunFinal,
    ) -> tuple[RunStatusResponse, Report]:
        """Validate and persist a structured Codex conclusion for one external Run."""

        try:
            if isinstance(final, CodeUnderstandingFinal):
                validated_final: ExternalRunFinal = CodeUnderstandingFinal.model_validate(final)
            else:
                validated_final = IssueModelFinal.model_validate(final)
        except ValueError as exc:
            raise ServiceError("invalid_input", 400, "external Run conclusion is invalid") from exc

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store._require_run_scope(project_id, session_id, run_id)
                self._require_external_run(record)
                self._require_external_final_kind(record.run.analysis_kind, validated_final)
                if validated_final.index_id != record.run.index_id:
                    raise ServiceError(
                        "conflict",
                        409,
                        "external conclusion index does not match the Run snapshot",
                    )
                if record.run.status in {"succeeded", "failed", "cancelled", "interrupted"}:
                    if record.run.status != "succeeded":
                        raise ServiceError("conflict", 409, "external Run is already terminal")
                    report = store.load_run_report(project_id, session_id, run_id)
                    result_page = store.load_run_tool_results(
                        project_id,
                        session_id,
                        run_id,
                        limit=40,
                    )
                    _, _, _, _, _, _, coverage_limitations = _external_graph_data(
                        list(result_page.results),
                        run_id=run_id,
                        index_id=record.run.index_id,
                    )
                    if not _external_report_matches_final(
                        report,
                        validated_final,
                        derived_limitations=coverage_limitations,
                    ):
                        raise ServiceError(
                            "conflict",
                            409,
                            "external Run already has a different conclusion",
                        )
                    return RunStatusResponse(run=record.run, input=record.input), report

                record = self._expire_external_run_if_needed(store, record)
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("budget_exceeded", 409, "external Run time budget is exhausted")
                record = self._ensure_external_run_started(store, record)
                record = self._expire_external_run_if_needed(store, record)
                if record.run.status not in {"queued", "running"}:
                    raise ServiceError("budget_exceeded", 409, "external Run time budget is exhausted")
                result_page = store.load_run_tool_results(
                    project_id,
                    session_id,
                    run_id,
                    limit=40,
                )
                tool_results = list(result_page.results)
                (
                    nodes,
                    static_edges,
                    unresolved,
                    evidence_by_id,
                    truncated,
                    truncation_reason,
                    coverage_limitations,
                ) = _external_graph_data(
                    tool_results,
                    run_id=run_id,
                    index_id=record.run.index_id,
                )
                self._validate_external_final(
                    validated_final,
                    record.run,
                    evidence_by_id=evidence_by_id,
                    symbol_ids={symbol.symbol_id for symbol in nodes},
                )
                graph = GraphSnapshot(
                    index_id=record.run.index_id,
                    nodes=nodes,
                    static_edges=static_edges,
                    unresolved=unresolved,
                    candidate_links=(
                        list(validated_final.candidate_links)
                        if isinstance(validated_final, IssueModelFinal)
                        else []
                    ),
                    truncated=truncated,
                    truncation_reason=truncation_reason,
                )
                selected_evidence = [evidence_by_id[evidence_id] for evidence_id in validated_final.evidence_ids]

                current = store.load_run_record(run_id)
                if current.run.budget.events_used + 2 <= current.run.budget.max_events:
                    graph_event = GraphUpdatedEvent(
                        run_id=run_id,
                        sequence=current.run.budget.events_used + 1,
                        timestamp=datetime.now(timezone.utc),
                        payload=GraphUpdatedPayload(graph=graph),
                    )
                    store.append_run_event(graph_event)
                    current = store.load_run_record(run_id)
                terminal_sequence = current.run.budget.events_used + 1
                report_id = f"report-{run_id}"
                report = Report(
                    report_id=report_id,
                    run_id=run_id,
                    project_id=project_id,
                    session_id=session_id,
                    index_id=current.run.index_id,
                    status="succeeded",
                    outcome=validated_final.outcome,
                    fixture_mode=False,
                    summary=validated_final.summary,
                    graph=graph,
                    evidence=selected_evidence,
                    findings=(
                        list(validated_final.findings)
                        if isinstance(validated_final, IssueModelFinal)
                        else []
                    ),
                    candidate_links=(
                        list(validated_final.candidate_links)
                        if isinstance(validated_final, IssueModelFinal)
                        else []
                    ),
                    explanation=(
                        validated_final.explanation
                        if isinstance(validated_final, CodeUnderstandingFinal)
                        else None
                    ),
                    limitations=list(
                        dict.fromkeys([*validated_final.limitations, *coverage_limitations])
                    )[:30],
                    terminal_sequence=terminal_sequence,
                )
                event = RunCompletedEvent(
                    run_id=run_id,
                    sequence=terminal_sequence,
                    timestamp=datetime.now(timezone.utc),
                    payload=RunCompletedPayload(
                        status="succeeded",
                        outcome=validated_final.outcome,
                        report_id=report_id,
                    ),
                )
                updated_run = current.run.model_copy(
                    update={
                        "status": "succeeded",
                        "ended_at": event.timestamp,
                        "report_id": report_id,
                    }
                )
                finished = store.finish_run(updated_run, event, report)
                persisted_report = store.load_run_report(project_id, session_id, run_id)
                if not _external_report_matches_final(
                    persisted_report,
                    validated_final,
                    derived_limitations=coverage_limitations,
                ):
                    raise ServiceError(
                        "conflict",
                        409,
                        "external Run completed concurrently with a different conclusion",
                    )
                return RunStatusResponse(run=finished.run, input=finished.input), persisted_report
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "external Run was not found") from exc
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "external Run completion conflicts with existing data") from exc
        except StorageInputError as exc:
            message = str(exc)
            code = "budget_exceeded" if "budget" in message.lower() else "invalid_input"
            raise ServiceError(code, 409 if code == "budget_exceeded" else 400, message) from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local external Run completion failed") from exc
        except ValueError as exc:
            raise ServiceError("invalid_input", 409, "external Run references are invalid") from exc

    def cancel_external_run(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        """Cancel only a Codex-owned external Run."""

        record = self._load_run_record(project_id, session_id, run_id)
        self._require_external_run(record)
        return self.cancel_run(project_id, session_id, run_id, request)

    @staticmethod
    def _require_external_run(record: RunRecord) -> None:
        if record.run.mode != "external" or record.run.execution_source != "codex":
            raise ServiceError("conflict", 409, "Run is not a Codex external Run")

    @staticmethod
    def _require_external_final_kind(
        analysis_kind: str,
        final: ExternalRunFinal,
    ) -> None:
        is_understanding = isinstance(final, CodeUnderstandingFinal)
        if (analysis_kind == "understanding") != is_understanding:
            expected = "code understanding" if analysis_kind == "understanding" else "Issue"
            raise ServiceError(
                "conflict",
                409,
                f"external conclusion must be a {expected} conclusion for this Run",
            )

    @staticmethod
    def _ensure_external_run_started(store: IndexStore, record: RunRecord) -> RunRecord:
        if record.run.status != "queued":
            return record
        started_at = datetime.now(timezone.utc)
        event = RunStartedEvent(
            run_id=record.run.run_id,
            sequence=record.run.budget.events_used + 1,
            timestamp=started_at,
            payload=RunStartedPayload(
                project_id=record.run.project_id,
                session_id=record.run.session_id,
                index_id=record.run.index_id,
                issue_id=record.run.issue.issue_id,
                mode=record.run.mode,
                analysis_kind=record.run.analysis_kind,
                execution_source=record.run.execution_source,
                message_end_sequence=record.run.message_end_sequence,
                input_hash=record.run.input_hash,
                feature_generation_id=record.run.feature_generation_id,
                execution_version=record.run.execution_version,
                budget=record.run.budget,
            ),
        )
        started = store.start_run(record.run.run_id, started_at, event)
        return started or store.load_run_record(record.run.run_id)

    @staticmethod
    def _expire_external_run_if_needed(store: IndexStore, record: RunRecord) -> RunRecord:
        """Interrupt only this external Run once its wall-clock budget is exhausted."""

        if record.run.status not in {"queued", "running"}:
            return record
        active_since = record.run.created_at
        now = datetime.now(timezone.utc)
        if now < active_since or now - active_since < timedelta(seconds=record.run.budget.max_seconds):
            return record
        store.interrupt_run(
            record.run.run_id,
            reason="external Run exceeded its wall-clock budget",
            interrupted_at=now,
        )
        return store.load_run_record(record.run.run_id)

    @staticmethod
    def _validate_external_final(
        final: ExternalRunFinal,
        run: Run,
        *,
        evidence_by_id: Mapping[str, object],
        symbol_ids: set[str],
    ) -> None:
        if final.index_id != run.index_id:
            raise ServiceError("conflict", 409, "external conclusion index does not match the Run snapshot")
        returned_evidence_ids = set(evidence_by_id)
        if not set(final.evidence_ids).issubset(returned_evidence_ids):
            raise ServiceError(
                "conflict",
                409,
                "external conclusion references evidence not returned by this Run",
            )
        if isinstance(final, CodeUnderstandingFinal):
            if not any(
                getattr(evidence_by_id[evidence_id], "kind", None) == "source"
                for evidence_id in final.evidence_ids
            ):
                raise ServiceError(
                    "conflict",
                    409,
                    "external explanations must include returned source evidence",
                )
            for step in final.explanation.steps:
                if not set(step.evidence_ids).issubset(returned_evidence_ids):
                    raise ServiceError(
                        "conflict",
                        409,
                        "external explanation references evidence not returned by this Run",
                    )
                if not set(step.symbol_ids).issubset(symbol_ids):
                    raise ServiceError(
                        "conflict",
                        409,
                        "external explanation references a symbol not returned by this Run",
                    )
            return
        for finding in final.findings:
            if not set(finding.symbol_ids).issubset(symbol_ids):
                raise ServiceError(
                    "conflict",
                    409,
                    "external finding references a symbol not returned by this Run",
                )
        for link in final.candidate_links:
            if link.index_id != run.index_id:
                raise ServiceError("conflict", 409, "external candidate link uses a different index")
            if link.source_symbol_id not in symbol_ids or (
                link.target_symbol_id is not None and link.target_symbol_id not in symbol_ids
            ):
                raise ServiceError(
                    "conflict",
                    409,
                    "external candidate link references a symbol not returned by this Run",
                )

    def get_run(self, project_id: str, session_id: str, run_id: str) -> RunStatusResponse:
        record = self._load_run_record(project_id, session_id, run_id)
        return RunStatusResponse(run=record.run, input=record.input)

    def list_runs(
        self,
        project_id: str,
        session_id: str,
        *,
        limit: int = 40,
        cursor: str | None = None,
    ) -> RunListResponse:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.list_runs(project_id, session_id, limit=limit, cursor=cursor)
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "session was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "Run history parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run storage failed") from exc
        return RunListResponse(
            project_id=project_id,
            session_id=session_id,
            runs=list(page.runs),
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def list_run_events(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> RunEventsResponse:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.load_run_events(
                    project_id,
                    session_id,
                    run_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "Run event parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run event storage failed") from exc
        return RunEventsResponse(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            events=list(page.events),
            next_after_sequence=page.next_after_sequence,
            has_more=page.has_more,
        )

    def latest_run_event_sequence(self, project_id: str, session_id: str, run_id: str) -> int:
        """Return the persisted event upper bound for strict SSE cursor validation."""

        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                return store.load_run_event_cursor(project_id, session_id, run_id).latest_sequence
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run event storage failed") from exc

    def list_run_tool_results(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 40,
    ) -> RunToolResultsResponse:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.load_run_tool_results(
                    project_id,
                    session_id,
                    run_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "Run tool-result parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run result storage failed") from exc
        return RunToolResultsResponse(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            results=list(page.results),
            next_after_sequence=page.next_after_sequence,
            has_more=page.has_more,
        )

    def get_run_report(self, project_id: str, session_id: str, run_id: str) -> ReportResponse:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                report = store.load_run_report(project_id, session_id, run_id)
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run report is not available") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run report storage failed") from exc
        return ReportResponse(report=report)

    def get_run_impact(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        *,
        candidate_id: str | None = None,
        finding_id: str | None = None,
        direction: ImpactDirection = "callers",
        depth: int = 2,
        limit: int = 40,
    ) -> RunImpactResponse:
        """Read a bounded static impact view rooted in one saved report symbol."""

        if (candidate_id is None) == (finding_id is None):
            raise ServiceError(
                "invalid_input",
                400,
                "provide exactly one candidate_id or finding_id",
            )
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store._require_run_scope(project_id, session_id, run_id)
                report = store.load_run_report(project_id, session_id, run_id)
                if (
                    report.project_id != project_id
                    or report.session_id != session_id
                    or report.run_id != run_id
                    or report.index_id != record.run.index_id
                ):
                    raise StorageInputError("Run report does not match the requested Run scope")
                coverage = store.get_relation_coverage(record.run.index_id)

                root_symbol_id: str | None = None
                mapping_status = "mapped"
                if report.outcome != "candidate_found":
                    mapping_status = "unmapped"
                elif candidate_id is not None:
                    candidate = next(
                        (item for item in report.candidate_links if item.candidate_id == candidate_id),
                        None,
                    )
                    if candidate is None:
                        raise StorageNotFoundError("candidate was not found in the Run report")
                    root_symbol_id = candidate.source_symbol_id
                else:
                    finding = next(
                        (item for item in report.findings if item.finding_id == finding_id),
                        None,
                    )
                    if finding is None:
                        raise StorageNotFoundError("finding was not found in the Run report")
                    if len(finding.symbol_ids) == 1:
                        root_symbol_id = finding.symbol_ids[0]
                    else:
                        mapping_status = "unmapped"

                graph_symbol_ids = {symbol.symbol_id for symbol in report.graph.nodes}
                if root_symbol_id is not None and root_symbol_id not in graph_symbol_ids:
                    mapping_status = "unmapped"
                    root_symbol_id = None

                scope_note = _impact_scope_note(direction, depth)
                if mapping_status == "unmapped":
                    return RunImpactResponse(
                        project_id=project_id,
                        session_id=session_id,
                        run_id=run_id,
                        index_id=record.run.index_id,
                        candidate_id=candidate_id,
                        finding_id=finding_id,
                        mapping_status="unmapped",
                        direction=direction,
                        max_depth=depth,
                        scope_note=(
                            "当前报告没有一个可用于影响遍历的唯一候选符号；只保留报告中的源码证据。"
                        ),
                        index_truncated=coverage.index_truncated,
                        index_truncation_reason=coverage.index_truncation_reason,
                        index_unresolved_truncated=coverage.index_unresolved_truncated,
                        index_unresolved_truncation_reason=coverage.index_unresolved_truncation_reason,
                    )

                result = store.get_static_impact(
                    record.run.index_id,
                    root_symbol_id,
                    direction,
                    depth,
                    limit,
                )
                return RunImpactResponse(
                    project_id=project_id,
                    session_id=session_id,
                    run_id=run_id,
                    index_id=record.run.index_id,
                    candidate_id=candidate_id,
                    finding_id=finding_id,
                    mapping_status="mapped",
                    direction=direction,
                    max_depth=depth,
                    root_symbol_id=result.root.symbol_id,
                    root=result.root,
                    nodes=list(result.nodes),
                    edges=list(result.edges),
                    paths=list(result.paths),
                    unresolved=list(result.unresolved),
                    reachable_node_count=result.reachable_node_count,
                    returned_node_count=len(result.nodes),
                    scope_note=scope_note,
                    truncated=result.truncated,
                    truncation_reason=result.truncation_reason,
                    index_truncated=result.index_truncated,
                    index_truncation_reason=result.index_truncation_reason,
                    index_unresolved_truncated=result.index_unresolved_truncated,
                    index_unresolved_truncation_reason=result.index_unresolved_truncation_reason,
                )
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run, report, candidate, or finding was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "Run impact parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run impact storage failed") from exc

    def cancel_run(
        self,
        project_id: str,
        session_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                record = store._require_run_scope(project_id, session_id, run_id)
                cancelled, event_sequence = store.cancel_run(
                    run_id,
                    request.reason,
                    datetime.now(timezone.utc),
                )
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("conflict", 409, str(exc)) from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run cancellation failed") from exc
        self.run_executor.signal_cancel(run_id)
        return CancelRunResponse(
            run_id=cancelled.run.run_id,
            project_id=cancelled.run.project_id,
            session_id=cancelled.run.session_id,
            index_id=cancelled.run.index_id,
            status=cancelled.run.status,
            cancel_event_sequence=event_sequence,
        )

    def _load_run_record(self, project_id: str, session_id: str, run_id: str) -> RunRecord:
        project = self._find_project(project_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                return store._require_run_scope(project_id, session_id, run_id)
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "Run was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local Run storage failed") from exc

    def create_project_index(
        self,
        project_id: str,
        request: CreateProjectIndexRequest,
    ) -> IndexResponse:
        with self._index_mutation_lock:
            return self._create_project_index(
                project_id,
                request,
                expected_latest_index_id=None,
            )

    def _create_project_index(
        self,
        project_id: str,
        request: CreateProjectIndexRequest,
        *,
        expected_latest_index_id: str | None,
        identity_version: str | None = None,
    ) -> IndexResponse:
        project = self._find_project(project_id)
        spec = self.registry.spec_for(project.repository_id)
        if (
            spec.source_kind == "github"
            and not self.registry.source_root_for(project.repository_id).is_dir()
        ):
            raise ServiceError(
                "conflict",
                409,
                "GitHub sources are immutable; import the repository again to create a new fixed-commit index",
            )
        pending_id = f"pending-{uuid4().hex}"
        with self.registry.store_for_repository(project.repository_id) as store:
            current_project = store.load_project(project.project_id)
            if (
                expected_latest_index_id is not None
                and current_project.latest_index_id != expected_latest_index_id
            ):
                raise ServiceError("conflict", 409, "project latest index changed before the update")
            store.set_project_building(project.project_id, pending_id)

        def mark_snapshot_ready(index_id: str) -> None:
            with self.registry.store_for_repository(project.repository_id) as store:
                store.set_project_building(project.project_id, index_id)

        try:
            response = self.index_service.create_index(
                CreateIndexRequest(
                    repository_id=project.repository_id,
                    revision=request.revision,
                    dirty=request.dirty,
                ),
                on_snapshot_ready=mark_snapshot_ready,
                identity_version=identity_version,
            )
            with self.registry.store_for_repository(project.repository_id) as store:
                store.finish_project_index(
                    project.project_id,
                    response.index.index_id,
                    expected_latest_index_id=expected_latest_index_id,
                )
            return response
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, str(exc)) from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "project or index was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc
        finally:
            with self.registry.store_for_repository(project.repository_id) as store:
                store.clear_project_building(project.project_id)

    def check_index_freshness(self, project_id: str, index_id: str) -> IndexFreshness:
        """Check one project index against its current local source manifest."""

        project = self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                baseline = store.load_index_snapshot(index_id)
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "index does not belong to the project") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc
        if baseline.status != "ready":
            raise ServiceError("conflict", 409, "freshness requires a ready project index")
        return self.index_service.check_source_freshness(project_id, baseline)

    def refresh_project_index(
        self,
        project_id: str,
        index_id: str,
        request: RefreshProjectIndexRequest,
    ) -> IndexRefreshResponse:
        """Recheck and explicitly reuse or rebuild the project's current index."""

        with self._index_mutation_lock:
            project = self._require_project_index(project_id, index_id)
            try:
                with self.registry.store_for_repository(project.repository_id) as store:
                    current_project = store.load_project(project_id)
                    if current_project.latest_index_id != index_id:
                        raise ServiceError(
                            "conflict",
                            409,
                            "only the project's current latest index can be updated",
                        )
                    baseline = store.load_index_snapshot(index_id)
            except StorageNotFoundError as exc:
                raise ServiceError("not_found", 404, "index does not belong to the project") from exc
            except StorageError as exc:
                raise ServiceError("internal_error", 500, "local index storage failed") from exc
            if baseline.status != "ready":
                raise ServiceError("conflict", 409, "update requires a ready project index")

            freshness = self.index_service.check_source_freshness(project_id, baseline)
            metadata_requires_new_identity = bool(
                set(freshness.reasons)
                & {"missing_index_metadata", "rules_changed", "configuration_changed"}
            )
            legacy_metadata_rebuildable = (
                freshness.status == "unknown"
                and "missing_index_metadata" in freshness.reasons
                and freshness.scan_complete
                and not freshness.scan_truncated
                and set(freshness.reasons) <= _LEGACY_REBUILDABLE_FRESHNESS_REASONS
            )
            if freshness.status == "consistent":
                return IndexRefreshResponse(
                    project_id=project_id,
                    baseline_index_id=index_id,
                    mode="reused",
                    index=self.index_service.get_index(index_id),
                    freshness=freshness,
                )
            if freshness.status == "unknown" and not legacy_metadata_rebuildable:
                if "budget_exceeded" in freshness.reasons:
                    raise ServiceError(
                        "budget_exceeded",
                        429,
                        "source freshness check exceeded its bounded scan budget",
                    )
                raise ServiceError(
                    "conflict",
                    409,
                    "source freshness could not be established; the existing index was kept",
                )
            created = self._create_project_index(
                project_id,
                CreateProjectIndexRequest(revision=request.revision, dirty=request.dirty),
                expected_latest_index_id=index_id,
                identity_version=(
                    f"{INDEX_BUILD_IDENTITY_VERSION}-rebuild"
                    if metadata_requires_new_identity
                    else None
                ),
            )
            return IndexRefreshResponse(
                project_id=project_id,
                baseline_index_id=index_id,
                mode="created",
                index=created,
                freshness=freshness,
            )

    def get_project_index(self, project_id: str, index_id: str) -> IndexResponse:
        self._require_project_index(project_id, index_id)
        return self.index_service.get_index(index_id)

    def search(self, project_id: str, index_id: str, query: str, limit: int) -> SearchIndexResponse:
        self._require_project_index(project_id, index_id)
        return self.index_service.search(index_id, query, limit)

    def neighbors(
        self,
        project_id: str,
        index_id: str,
        symbol_id: str,
        direction: str,
        depth: int,
        limit: int,
        edge_kinds: tuple[str, ...] | None = None,
    ) -> NeighborsIndexResponse:
        self._require_project_index(project_id, index_id)
        return self.index_service.neighbors(index_id, symbol_id, direction, depth, limit, edge_kinds)

    def read_source(
        self,
        project_id: str,
        index_id: str,
        path: str,
        start_line: int,
        end_line: int,
    ) -> SourceIndexResponse:
        self._require_project_index(project_id, index_id)
        return self.index_service.read_source(index_id, path, start_line, end_line)

    def get_overview(
        self,
        project_id: str,
        index_id: str,
        *,
        max_depth: int = 4,
        max_paths: int = 40,
        max_modules: int = 100,
        max_groups: int = 80,
        max_module_relations: int = 200,
    ) -> ProjectOverview:
        """Build a deterministic entry/module overview from one persisted snapshot."""

        project = self._find_project(project_id)
        self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                relation = store.load_index(index_id)
                return build_project_overview(
                    project_id,
                    relation,
                    store.read_evidence,
                    max_depth=max_depth,
                    max_paths=max_paths,
                    max_modules=max_modules,
                    max_groups=max_groups,
                    max_module_relations=max_module_relations,
                )
        except ServiceError:
            raise
        except ValueError as exc:
            raise ServiceError("invalid_input", 400, "overview parameters are invalid") from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested index or source evidence was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "overview parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc

    def generate_features(
        self,
        project_id: str,
        index_id: str,
        request: FeatureGenerationRequest,
    ) -> FeatureGenerationResponse:
        """Generate, validate, persist, and return one bounded semantic result."""

        project = self._find_project(project_id)
        self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                relation = store.load_index(index_id)
                index = relation.snapshot.index
                model = resolve_model(request.mode, request.model, model_config=self.registry.model_config)
                cache_key = build_cache_key(
                    index.index_id,
                    index.snapshot_hash,
                    request.mode,
                    model,
                    request.prompt_version,
                    request.source_scope,
                    request.budget,
                )
                if request.idempotency_key:
                    existing = store.find_feature_generation_by_idempotency(
                        project_id,
                        request.idempotency_key,
                    )
                    if existing is not None:
                        _, generation = existing
                        if generation.index.index_id != index_id or generation.cache_key != cache_key:
                            raise ServiceError(
                                "conflict",
                                409,
                                "idempotency key is already bound to another feature request",
                            )
                        return FeatureGenerationResponse(generation=generation)
                cached = store.find_cached_feature_generation(project_id, index_id, cache_key)
                if cached is not None:
                    return FeatureGenerationResponse(generation=cached[1])
                generation_engine = FeatureGenerationEngine(
                    self,
                    adapter_factory=self.feature_adapter_factory,
                    model_config=self.registry.model_config,
                )
                generation_started = time.monotonic()
                generation = generation_engine.generate(
                    project_id,
                    index_id,
                    index,
                    request,
                    generation_id=new_generation_id(),
                    model=model,
                    cache_key=cache_key,
                    started=generation_started,
                )
                generation = generation_engine.fail_if_deadline_exceeded(
                    generation,
                    started=generation_started,
                )
                store.save_feature_generation(
                    generation,
                    cache_key=cache_key,
                    idempotency_key=request.idempotency_key,
                )
                return FeatureGenerationResponse(generation=generation)
        except ServiceError:
            raise
        except StorageConflictError as exc:
            raise ServiceError("conflict", 409, "feature generation conflicts with an existing request") from exc
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested project, index, or feature generation was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "feature generation parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc

    def get_feature_generation(
        self,
        project_id: str,
        index_id: str,
        generation_id: str,
    ) -> FeatureGenerationResponse:
        """Read a saved feature result without invoking retrieval or a model."""

        project = self._find_project(project_id)
        self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                generation = store.load_feature_generation(generation_id)
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "feature generation was not found") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc
        if generation.project_id != project_id or generation.index.index_id != index_id:
            raise ServiceError("not_found", 404, "feature generation does not belong to the selected snapshot")
        return FeatureGenerationResponse(generation=generation)

    def list_feature_generations(
        self,
        project_id: str,
        *,
        index_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> FeatureGenerationListResponse:
        """Read stable saved-generation metadata for a project or selected index."""

        project = self._find_project(project_id)
        if index_id is not None:
            self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                page = store.list_feature_generations(
                    project_id,
                    index_id=index_id,
                    limit=limit,
                    cursor=cursor,
                )
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "requested project or index was not found") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "feature generation history parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc
        return FeatureGenerationListResponse(
            project_id=project_id,
            index_id=index_id,
            generations=[_feature_generation_summary(generation) for generation in page.generations],
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def get_feature_graph(
        self,
        project_id: str,
        index_id: str,
        generation_id: str,
        *,
        max_nodes: int = 40,
        max_edges: int = 80,
    ) -> FeatureGraphResponse:
        """Build a read-only graph from one saved generation and its immutable snapshot."""

        project = self._find_project(project_id)
        self._require_project_index(project_id, index_id)
        try:
            with self.registry.store_for_repository(project.repository_id) as store:
                generation = store.load_feature_generation(generation_id)
                if generation.project_id != project_id or generation.index.index_id != index_id:
                    raise StorageNotFoundError("feature generation is outside the requested scope")
                relation = store.load_index(index_id)
                overview = build_project_overview(
                    project_id,
                    relation,
                    store.read_evidence,
                )
                graph = build_feature_graph(
                    project_id,
                    generation,
                    relation,
                    overview,
                    max_nodes=max_nodes,
                    max_edges=max_edges,
                )
        except ServiceError:
            raise
        except StorageNotFoundError as exc:
            raise ServiceError("not_found", 404, "feature generation or index was not found") from exc
        except ValueError as exc:
            raise ServiceError("invalid_input", 400, "feature graph parameters are invalid") from exc
        except StorageInputError as exc:
            raise ServiceError("invalid_input", 400, "feature graph parameters are invalid") from exc
        except StorageError as exc:
            raise ServiceError("internal_error", 500, "local index storage failed") from exc
        return FeatureGraphResponse(graph=graph)

    def import_local(
        self,
        request: LocalImportRequest,
        *,
        via_mcp: bool = False,
    ) -> LocalImportResponse:
        """Register, index, and persist one server-local source directory."""

        if via_mcp and not self.registry.mcp_local_import_enabled:
            raise ServiceError("forbidden", 403, "local MCP import is disabled")
        source_path, normalized_path = self.registry.validate_import_source(request.path)
        spec = self.registry.repository_for_source(normalized_path)
        if spec is None:
            project_id = _new_project_id()
            repository_id = _managed_repository_id(normalized_path)
            record = self.registry.management_store.register_source(
                repository_id=repository_id,
                project_id=project_id,
                name=request.name,
                source_path=str(source_path),
                normalized_source_path=normalized_path,
                storage_path=str(self.registry.managed_storage_path(project_id)),
                created_at=datetime.now(timezone.utc),
            )
            self.registry.refresh_managed_sources()
            self._ensure_managed_project(record)
            spec = self.registry.spec_for(record.repository_id)
        elif spec.managed:
            record = self.registry.management_store.load_source(spec.repository_id)
            self._ensure_managed_project(record)
        else:
            project = self._configured_project_for_source(spec, request.name)
            return self._run_import_for_project(
                project,
                spec,
                source_path,
                normalized_path,
                request,
            )

        project = self._load_project_from_spec(spec)
        return self._run_import_for_project(
            project,
            spec,
            source_path,
            normalized_path,
            request,
        )

    def import_github(
        self,
        request: GitHubImportRequest,
        *,
        via_mcp: bool = False,
    ) -> GitHubImportResponse:
        """Resolve, fetch, safely unpack, index, and clean one GitHub commit."""

        self._require_github_import(via_mcp=via_mcp)
        try:
            repository = parse_github_repository_url(request.url)
        except GitHubError as exc:
            raise ServiceError(exc.code, exc.status_code, exc.message) from exc

        existing_by_key = (
            self.registry.management_store.find_attempt_by_idempotency(request.idempotency_key)
            if request.idempotency_key is not None
            else None
        )
        if existing_by_key is not None:
            if not _same_github_request(existing_by_key, repository, request):
                raise ServiceError("conflict", 409, "idempotency key conflicts with a different import request")
            return self._github_import_response(existing_by_key)

        resolution = self._resolve_github(repository, request)
        normalized_path = resolution.repository.identity
        spec = self.registry.repository_for_source(normalized_path)
        if spec is None:
            project_id = _new_project_id()
            repository_id = _managed_repository_id(normalized_path)
            record = self.registry.management_store.register_source(
                repository_id=repository_id,
                project_id=project_id,
                name=request.name,
                source_path=resolution.repository.canonical_url,
                normalized_source_path=normalized_path,
                storage_path=str(self.registry.managed_storage_path(project_id)),
                created_at=datetime.now(timezone.utc),
                source_kind="github",
                source_url=resolution.repository.canonical_url,
                requested_ref=resolution.requested_ref,
                resolved_commit=resolution.resolved_commit,
            )
            self.registry.refresh_managed_sources()
            self._ensure_managed_project(record)
            spec = self.registry.spec_for(record.repository_id)
        elif spec.source_kind != "github":
            raise ServiceError("conflict", 409, "the normalized GitHub identity conflicts with a local source")
        else:
            record = self.registry.management_store.load_source(spec.repository_id)
            self._ensure_managed_project(record)

        project = self._load_project_from_spec(spec)
        same_commit = self.registry.management_store.find_attempt_by_commit(
            normalized_path,
            resolution.resolved_commit,
        )
        if same_commit is not None:
            return self._github_import_response(same_commit)
        active = self.registry.management_store.find_active_attempt(normalized_path)
        if active is not None:
            return self._github_import_response(active)

        try:
            attempt = self.registry.management_store.create_attempt(
                attempt_id=f"import-{uuid4().hex}",
                project_id=project.project_id,
                repository_id=spec.repository_id,
                source_path=resolution.repository.canonical_url,
                normalized_source_path=normalized_path,
                name=project.name,
                revision=resolution.resolved_commit,
                dirty=False,
                idempotency_key=request.idempotency_key,
                created_at=datetime.now(timezone.utc),
                source_kind="github",
                source_url=resolution.repository.canonical_url,
                requested_ref=resolution.requested_ref,
                resolved_commit=resolution.resolved_commit,
            )
        except StorageConflictError:
            if request.idempotency_key is not None:
                existing = self.registry.management_store.find_attempt_by_idempotency(request.idempotency_key)
                if existing is not None and _same_github_request(existing, repository, request):
                    return self._github_import_response(existing)
            active = self.registry.management_store.find_active_attempt(normalized_path)
            if active is not None:
                return self._github_import_response(active)
            raise ServiceError("conflict", 409, "an equivalent GitHub import is already running")

        self.registry.management_store.update_source(
            spec.repository_id,
            source_url=resolution.repository.canonical_url,
            requested_ref=resolution.requested_ref,
            resolved_commit=resolution.resolved_commit,
            content_hash=None,
            cleanup_status="not_required",
            cleanup_error=None,
            temporary_path=None,
        )
        return self._run_github_import_attempt(attempt.attempt_id)

    def retry_github_import(
        self,
        attempt_id: str,
        *,
        via_mcp: bool = False,
    ) -> GitHubImportResponse:
        self._require_github_import(via_mcp=via_mcp)
        attempt = self.registry.management_store.load_attempt(attempt_id)
        if attempt.source_kind != "github":
            raise ServiceError("conflict", 409, "the import attempt is not a GitHub import")
        if attempt.status not in {"failed", "interrupted", "cancelled"}:
            raise ServiceError("conflict", 409, "only failed or interrupted imports can be retried")
        self.registry.management_store.update_attempt(
            attempt_id,
            status="queued",
            phase="queued",
            index_id=None,
            error_code=None,
            error_message=None,
        )
        self.registry.management_store.update_attempt_metadata(
            attempt_id,
            content_hash=None,
            cleanup_status="not_required",
            cleanup_error=None,
            temporary_path=None,
        )
        return self._run_github_import_attempt(attempt_id)

    def get_import_status(self, attempt_id: str) -> ImportAttemptResponse:
        attempt = self.registry.management_store.load_attempt(attempt_id)
        project = self._find_project(attempt.project_id)
        latest_index = (
            self.get_project_index(project.project_id, project.latest_index_id)
            if project.latest_index_id is not None
            else None
        )
        return ImportAttemptResponse(
            source_path=attempt.source_path,
            project=_project_response(project),
            attempt=_import_attempt_response(attempt),
            latest_index=latest_index,
        )

    def retry_import_cleanup(self, attempt_id: str) -> GitHubImportResponse:
        attempt = self.registry.management_store.load_attempt(attempt_id)
        if attempt.source_kind != "github":
            raise ServiceError("conflict", 409, "only GitHub import temp directories support cleanup retry")
        if attempt.cleanup_status != "failed":
            raise ServiceError("conflict", 409, "GitHub import cleanup does not need a retry")
        cleanup_error = self._cleanup_github_temp(attempt.temporary_path, attempt.attempt_id)
        if cleanup_error is None:
            updated = self.registry.management_store.update_attempt_metadata(
                attempt_id,
                cleanup_status="succeeded",
                cleanup_error=None,
                temporary_path=None,
            )
            self.registry.management_store.update_source(
                attempt.repository_id,
                cleanup_status="succeeded",
                cleanup_error=None,
                temporary_path=None,
            )
            return self._github_import_response(updated)
        updated = self.registry.management_store.update_attempt_metadata(
            attempt_id,
            cleanup_status="failed",
            cleanup_error=cleanup_error,
        )
        return self._github_import_response(updated)

    def retry_import(self, attempt_id: str, *, via_mcp: bool = False) -> LocalImportResponse:
        if via_mcp and not self.registry.mcp_local_import_enabled:
            raise ServiceError("forbidden", 403, "local MCP import is disabled")
        attempt = self.registry.management_store.load_attempt(attempt_id)
        if attempt.status not in {"failed", "interrupted", "cancelled"}:
            raise ServiceError("conflict", 409, "only failed or interrupted imports can be retried")
        self.registry.management_store.update_attempt(
            attempt_id,
            status="queued",
            phase="queued",
            index_id=None,
            error_code=None,
            error_message=None,
        )
        return self._run_import_attempt(attempt_id)

    def _run_import_for_project(
        self,
        project: ProjectRecord,
        spec: RepositorySpec,
        source_path: Path,
        normalized_path: str,
        request: LocalImportRequest,
    ) -> LocalImportResponse:
        existing = (
            self.registry.management_store.find_attempt_by_idempotency(
                request.idempotency_key,
            )
            if request.idempotency_key is not None
            else None
        )
        if existing is not None:
            if not _same_import_request(existing, project, normalized_path, request):
                raise ServiceError("conflict", 409, "idempotency key conflicts with a different import request")
            return self._local_import_response(existing)

        if request.idempotency_key is None:
            active = self.registry.management_store.find_active_attempt(normalized_path)
            if active is not None:
                return self._local_import_response(active)

        try:
            attempt = self.registry.management_store.create_attempt(
                attempt_id=f"import-{uuid4().hex}",
                project_id=project.project_id,
                repository_id=spec.repository_id,
                source_path=str(source_path),
                normalized_source_path=normalized_path,
                name=project.name,
                revision=request.revision,
                dirty=request.dirty,
                idempotency_key=request.idempotency_key,
                created_at=datetime.now(timezone.utc),
            )
        except StorageConflictError:
            if request.idempotency_key is None:
                active = self.registry.management_store.find_active_attempt(normalized_path)
                if active is not None:
                    return self._local_import_response(active)
                raise ServiceError("conflict", 409, "an equivalent import is already running")
            existing = self.registry.management_store.find_attempt_by_idempotency(
                request.idempotency_key,
            )
            if existing is None:
                raise ServiceError("conflict", 409, "import request conflicts with existing data")
            if not _same_import_request(existing, project, normalized_path, request):
                raise ServiceError("conflict", 409, "idempotency key conflicts with a different import request")
            return self._local_import_response(existing)
        return self._run_import_attempt(attempt.attempt_id)

    def _run_import_attempt(self, attempt_id: str) -> LocalImportResponse:
        attempt = self.registry.management_store.load_attempt(attempt_id)
        try:
            self.registry.management_store.update_attempt(
                attempt_id,
                status="acquiring",
                phase="acquiring",
            )
            source_path, normalized_path = self.registry.validate_import_source(attempt.source_path)
            if normalized_path != attempt.normalized_source_path:
                raise ServiceError("invalid_input", 400, "registered source path changed")
            self.registry.management_store.update_attempt(
                attempt_id,
                status="indexing",
                phase="indexing",
            )
            self.create_project_index(
                attempt.project_id,
                CreateProjectIndexRequest(revision=attempt.revision, dirty=attempt.dirty),
            )
            project = self._find_project(attempt.project_id)
            index_id = project.latest_index_id
            if index_id is None:
                raise ServiceError("internal_error", 500, "import completed without an available index")
            completed = self.registry.management_store.update_attempt(
                attempt_id,
                status="ready",
                phase="ready",
                index_id=index_id,
                error_code=None,
                error_message=None,
            )
            return self._local_import_response(completed)
        except ServiceError as exc:
            failed = self.registry.management_store.update_attempt(
                attempt_id,
                status="failed",
                phase="failed",
                index_id=None,
                error_code=exc.code,
                error_message=exc.message,
            )
            return self._local_import_response(failed)
        except Exception:
            failed = self.registry.management_store.update_attempt(
                attempt_id,
                status="failed",
                phase="failed",
                index_id=None,
                error_code="internal_error",
                error_message="local import failed",
            )
            return self._local_import_response(failed)

    def _require_github_import(self, *, via_mcp: bool) -> None:
        if not self.registry.github_import_enabled:
            raise ServiceError("forbidden", 403, "GitHub import is disabled")
        if via_mcp and not self.registry.mcp_github_import_enabled:
            raise ServiceError("forbidden", 403, "GitHub MCP import is disabled")

    def _resolve_github(
        self,
        repository: GitHubRepository,
        request: GitHubImportRequest,
    ) -> GitHubResolution:
        try:
            return self.github_client.resolve(repository.canonical_url, request.ref)
        except GitHubError as exc:
            raise ServiceError(exc.code, exc.status_code, exc.message) from exc
        except Exception as exc:
            raise ServiceError("internal_error", 503, "GitHub could not resolve the requested repository ref") from exc

    def _run_github_import_attempt(self, attempt_id: str) -> GitHubImportResponse:
        attempt = self.registry.management_store.load_attempt(attempt_id)
        if attempt.source_kind != "github" or not attempt.source_url or not attempt.resolved_commit:
            raise ServiceError("internal_error", 500, "GitHub import metadata is incomplete")
        temp_root = self.registry.github_temp_path(attempt.attempt_id)
        source_root = temp_root / "source"
        temp_created = False
        try:
            self.registry.management_store.update_attempt(
                attempt_id,
                status="acquiring",
                phase="acquiring",
            )
            self._prepare_github_temp(temp_root, attempt.attempt_id)
            temp_created = True
            self.registry.management_store.update_attempt_metadata(
                attempt_id,
                cleanup_status="pending",
                cleanup_error=None,
                temporary_path=str(temp_root),
            )
            self.registry.management_store.update_source(
                attempt.repository_id,
                cleanup_status="pending",
                cleanup_error=None,
                temporary_path=str(temp_root),
            )
            repository = parse_github_repository_url(attempt.source_url)
            resolution = GitHubResolution(
                repository=repository,
                requested_ref=attempt.requested_ref,
                resolved_commit=attempt.resolved_commit,
            )
            self.github_client.download_and_extract(resolution, source_root)
            self.registry.bind_runtime_source(attempt.repository_id, source_root)
            self.registry.management_store.update_attempt(
                attempt_id,
                status="indexing",
                phase="indexing",
            )
            index_response = self.create_project_index(
                attempt.project_id,
                CreateProjectIndexRequest(revision=attempt.resolved_commit, dirty=False),
            )
            content_hash = index_response.index.snapshot_hash
            self.registry.management_store.update_attempt_metadata(
                attempt_id,
                content_hash=content_hash,
            )
            self.registry.management_store.update_source(
                attempt.repository_id,
                resolved_commit=attempt.resolved_commit,
                content_hash=content_hash,
            )
            completed = self.registry.management_store.update_attempt(
                attempt_id,
                status="ready",
                phase="ready",
                index_id=index_response.index.index_id,
                error_code=None,
                error_message=None,
            )
        except GitHubError as exc:
            failed = self.registry.management_store.update_attempt(
                attempt_id,
                status="failed",
                phase="failed",
                index_id=None,
                error_code=exc.code,
                error_message=exc.message,
            )
            completed = failed
        except ServiceError as exc:
            failed = self.registry.management_store.update_attempt(
                attempt_id,
                status="failed",
                phase="failed",
                index_id=None,
                error_code=exc.code,
                error_message=exc.message,
            )
            completed = failed
        except Exception:
            failed = self.registry.management_store.update_attempt(
                attempt_id,
                status="failed",
                phase="failed",
                index_id=None,
                error_code="internal_error",
                error_message="GitHub import failed",
            )
            completed = failed
        finally:
            self.registry.unbind_runtime_source(attempt.repository_id)
            if temp_created:
                cleanup_error = self._cleanup_github_temp(str(temp_root), attempt.attempt_id)
                if cleanup_error is None:
                    completed = self.registry.management_store.update_attempt_metadata(
                        attempt_id,
                        cleanup_status="succeeded",
                        cleanup_error=None,
                        temporary_path=None,
                    )
                    self.registry.management_store.update_source(
                        attempt.repository_id,
                        cleanup_status="succeeded",
                        cleanup_error=None,
                        temporary_path=None,
                    )
                else:
                    completed = self.registry.management_store.update_attempt_metadata(
                        attempt_id,
                        cleanup_status="failed",
                        cleanup_error=cleanup_error,
                        temporary_path=str(temp_root),
                    )
                    self.registry.management_store.update_source(
                        attempt.repository_id,
                        cleanup_status="failed",
                        cleanup_error=cleanup_error,
                        temporary_path=str(temp_root),
                    )
        return self._github_import_response(completed)

    def _prepare_github_temp(self, temp_root: Path, attempt_id: str) -> None:
        if temp_root.exists():
            cleanup_error = self._cleanup_github_temp(str(temp_root), attempt_id)
            if cleanup_error is not None:
                raise ServiceError("conflict", 409, cleanup_error)
        temp_root.mkdir(parents=True, exist_ok=False)
        marker = temp_root / ".codetrail-owner"
        try:
            marker.write_text(attempt_id, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise ServiceError("internal_error", 500, "GitHub temp directory could not be prepared") from exc

    def _cleanup_github_temp(self, raw_path: str | None, attempt_id: str) -> str | None:
        if raw_path is None:
            return None
        candidate = Path(raw_path).resolve(strict=False)
        root = self.registry.github_temp_root
        if candidate == root or not _path_is_within(candidate, root):
            return "GitHub temp directory is outside the managed temp root"
        if not candidate.exists():
            return None
        marker = candidate / ".codetrail-owner"
        try:
            if not marker.is_file() or marker.read_text(encoding="utf-8") != attempt_id:
                return "GitHub temp directory ownership marker does not match the import attempt"
            shutil.rmtree(candidate)
        except OSError as exc:
            return f"GitHub temp directory cleanup failed: {exc}"
        return None

    def _local_import_response(self, attempt: ImportAttemptRecord) -> LocalImportResponse:
        project = self._find_project(attempt.project_id)
        index = None
        if attempt.status == "ready" and attempt.index_id is not None:
            index = self.get_project_index(project.project_id, attempt.index_id)
        return LocalImportResponse(
            source_path=attempt.source_path,
            project=_project_response(project),
            attempt=_import_attempt_response(attempt),
            index=index,
        )

    def _github_import_response(self, attempt: ImportAttemptRecord) -> GitHubImportResponse:
        project = self._find_project(attempt.project_id)
        index = None
        if attempt.status == "ready" and attempt.index_id is not None:
            index = self.get_project_index(project.project_id, attempt.index_id)
        source_url = attempt.source_url or attempt.source_path
        return GitHubImportResponse(
            source_url=source_url,
            requested_ref=attempt.requested_ref,
            resolved_commit=attempt.resolved_commit,
            content_hash=attempt.content_hash,
            cleanup_status=attempt.cleanup_status,  # type: ignore[arg-type]
            cleanup_error=attempt.cleanup_error,
            project=_project_response(project),
            attempt=_import_attempt_response(attempt),
            index=index,
        )

    def _load_project_from_spec(self, spec: RepositorySpec) -> ProjectRecord:
        with self.registry.store_for_repository(spec.repository_id) as store:
            try:
                return store.load_project(spec.project_id or "")
            except StorageNotFoundError as exc:
                raise ServiceError("internal_error", 500, "managed project registration is incomplete") from exc

    def _configured_project_for_source(self, spec: RepositorySpec, name: str) -> ProjectRecord:
        with self.registry.store_for_repository(spec.repository_id) as store:
            projects = [project for project in store.list_projects() if not project.project_id.startswith("project-legacy-")]
            if projects:
                return projects[0]
        return self._find_project(self.create_project(CreateProjectRequest(name=name, repository_id=spec.repository_id)).project_id)

    def _ensure_managed_project(self, record: ManagedSourceRecord) -> None:
        self.registry.refresh_managed_sources()
        with self.registry.store_for_repository(record.repository_id) as store:
            try:
                store.load_project(record.project_id)
            except StorageNotFoundError:
                try:
                    store.create_project(record.project_id, record.name, record.repository_id, record.created_at)
                except StorageConflictError:
                    store.load_project(record.project_id)

    def _find_project(self, project_id: str) -> ProjectRecord:
        self._ensure_legacy_projects()
        self._ensure_managed_projects()
        for repository_id in self.registry.repository_ids:
            database_path = self.registry.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            with self.registry.store_for_repository(repository_id) as store:
                try:
                    project = store.load_project(project_id)
                except StorageNotFoundError:
                    continue
                if project.repository_id != repository_id:
                    raise ServiceError("internal_error", 500, "project repository association is invalid")
                return project
        raise ServiceError("not_found", 404, "project was not found")

    def _require_project_index(self, project_id: str, index_id: str) -> ProjectRecord:
        project = self._find_project(project_id)
        with self.registry.store_for_repository(project.repository_id) as store:
            if not store.project_has_index(project_id, index_id):
                raise ServiceError("not_found", 404, "index does not belong to the project")
        return project

    def _ensure_legacy_projects(self) -> None:
        for repository_id in self.registry.repository_ids:
            spec = self.registry.spec_for(repository_id)
            if spec.managed:
                continue
            database_path = self.registry.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            with self.registry.store_for_repository(repository_id) as store:
                store.ensure_legacy_project(
                    _legacy_project_id(repository_id),
                    repository_id,
                    repository_id,
                    datetime.now(timezone.utc),
                )

    def _ensure_managed_projects(self) -> None:
        self.registry.refresh_managed_sources()
        for record in self.registry.management_store.list_sources():
            self._ensure_managed_project(record)

    def _recover_interrupted_builds(self) -> None:
        self.registry.management_store.mark_active_attempts_interrupted()
        self._ensure_managed_projects()
        for repository_id in self.registry.repository_ids:
            database_path = self.registry.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            with self.registry.store_for_repository(repository_id) as store:
                store.recover_building_projects()

    def _recover_interrupted_runs(self) -> None:
        """Close persisted queued/running Runs before accepting new work."""

        now = datetime.now(timezone.utc)
        for repository_id in self.registry.repository_ids:
            database_path = self.registry.storage_root_for(repository_id) / ".codetrail" / "index.sqlite3"
            if not database_path.is_file():
                continue
            with self.registry.store_for_repository(repository_id) as store:
                store.recover_incomplete_runs(now)


def is_loopback_host(host: str | None) -> bool:
    if host is None or host.casefold() == "localhost":
        return host is not None
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _managed_repository_id(normalized_source_path: str) -> str:
    digest = hashlib.sha256(normalized_source_path.encode("utf-8")).hexdigest()[:32]
    return f"import-{digest}"


def _same_import_request(
    attempt: ImportAttemptRecord,
    project: ProjectRecord,
    normalized_path: str,
    request: LocalImportRequest,
) -> bool:
    return (
        attempt.project_id == project.project_id
        and attempt.normalized_source_path == normalized_path
        and attempt.name == project.name == request.name
        and attempt.revision == request.revision
        and attempt.dirty == request.dirty
    )


def _same_github_request(
    attempt: ImportAttemptRecord,
    repository: GitHubRepository,
    request: GitHubImportRequest,
) -> bool:
    return (
        attempt.source_kind == "github"
        and attempt.normalized_source_path == repository.identity
        and attempt.source_url == repository.canonical_url
        and attempt.name == request.name
        and attempt.requested_ref == request.ref
    )


def _import_attempt_response(record: ImportAttemptRecord) -> ImportAttempt:
    return ImportAttempt(
        attempt_id=record.attempt_id,
        project_id=record.project_id,
        repository_id=record.repository_id,
        source_path=record.source_path,
        status=record.status,  # type: ignore[arg-type]
        phase=record.phase,  # type: ignore[arg-type]
        index_id=record.index_id,
        error_code=record.error_code,
        error_message=record.error_message,
        source_kind=record.source_kind,  # type: ignore[arg-type]
        source_url=record.source_url,
        requested_ref=record.requested_ref,
        resolved_commit=record.resolved_commit,
        content_hash=record.content_hash,
        cleanup_status=record.cleanup_status,  # type: ignore[arg-type]
        cleanup_error=record.cleanup_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _feature_generation_summary(generation: FeatureGeneration) -> FeatureGenerationSummary:
    """Project one full saved generation into the bounded history-list contract."""

    return FeatureGenerationSummary(
        generation_id=generation.generation_id,
        project_id=generation.project_id,
        index=generation.index,
        mode=generation.mode,
        model=generation.model,
        prompt_version=generation.prompt_version,
        rules_version=generation.rules_version,
        source_scope=generation.source_scope,
        status=generation.status,
        summary=generation.summary,
        feature_count=len(generation.features),
        evidence_count=len(generation.evidence),
        usage=generation.usage,
        created_at=generation.created_at,
        error=generation.error,
        truncated=generation.truncated,
        truncation_reason=generation.truncation_reason,
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _configured_run_capacity(value: int | None) -> int:
    """Resolve the finite process-local Run admission capacity."""

    if value is None:
        raw = os.getenv("CODETRAIL_RUN_MAX_CONCURRENT_RUNS", "2").strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("CODETRAIL_RUN_MAX_CONCURRENT_RUNS must be a positive integer") from exc
    if value < 1:
        raise ValueError("run_max_concurrent_runs must be positive")
    return value


def _prepare_data_root(value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.exists() and _is_reparse_point(candidate):
        raise ValueError("service data directory must not be a symbolic link or junction")
    resolved = candidate.resolve(strict=False)
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("service data directory must be a directory")
    return resolved


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path_key = os.path.normcase(str(path.resolve(strict=False)))
        parent_key = os.path.normcase(str(parent.resolve(strict=False)))
        return os.path.commonpath((path_key, parent_key)) == parent_key
    except (OSError, ValueError):
        return False


def _contains_reparse_point(path: Path, ancestor: Path) -> bool:
    try:
        relative_parts = path.resolve(strict=True).relative_to(ancestor.resolve(strict=True)).parts
    except (OSError, ValueError):
        return True
    current = ancestor.resolve(strict=True)
    if _is_reparse_point(current):
        return True
    for part in relative_parts:
        current = current / part
        if _is_reparse_point(current):
            return True
    return False


def _normalized_path_key(path: Path) -> str:
    value = os.path.normcase(str(path.resolve(strict=False)))
    return value.rstrip("\\/") or value


def _new_project_id() -> str:
    return f"project-{uuid4().hex}"


def _new_session_id() -> str:
    return f"session-{uuid4().hex}"


def _new_session_message_id() -> str:
    return f"message-{uuid4().hex}"


def _legacy_project_id(repository_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"legacy:{repository_id}".encode("utf-8")).hexdigest()[:24]
    return f"project-legacy-{digest}"


def _project_response(record: ProjectRecord) -> Project:
    return Project(
        project_id=record.project_id,
        name=record.name,
        repository_id=record.repository_id,
        created_at=record.created_at,
        available_index_ids=list(record.available_index_ids),
        latest_index_id=record.latest_index_id,
        building_index_id=record.building_index_id,
    )


def _session_response(record: SessionRecord, index: IndexSnapshot) -> Session:
    return Session(
        session_id=record.session_id,
        project_id=record.project_id,
        index=index,
        feature_generation_id=record.feature_generation_id,
        topic=record.topic,
        created_at=record.created_at,
        updated_at=record.updated_at,
        message_count=record.message_count,
    )


def _session_creation_fingerprint(
    project_id: str,
    requested_index_id: str | None,
    feature_generation_id: str | None,
    topic: str,
) -> str:
    return _session_fingerprint(
        "create-v2",
        project_id,
        requested_index_id,
        feature_generation_id,
        topic,
    )


def _legacy_session_creation_fingerprint(
    project_id: str,
    bound_index_id: str,
    feature_generation_id: str | None,
    topic: str,
) -> str:
    """Reproduce the pre-v2 fingerprint for existing session rows."""

    return _session_fingerprint(
        "create",
        project_id,
        bound_index_id,
        feature_generation_id,
        topic,
    )


def _session_creation_fingerprint_matches(
    record: SessionRecord,
    project_id: str,
    request: CreateSessionRequest,
) -> bool:
    """Compare a retry with the original request while accepting legacy rows safely."""

    fingerprint = _session_creation_fingerprint(
        project_id,
        request.index_id,
        request.feature_generation_id,
        request.topic,
    )
    if record.request_fingerprint == fingerprint:
        return True

    # Legacy rows only stored the resolved index. An omitted index and an
    # explicit retry of that same bound index are the only safe compatibility
    # matches; a different explicit index remains a conflict.
    if request.index_id is not None and request.index_id != record.index_id:
        return False
    return record.request_fingerprint == _legacy_session_creation_fingerprint(
        project_id,
        record.index_id,
        request.feature_generation_id,
        request.topic,
    )


def _session_message_fingerprint(
    project_id: str,
    session_id: str,
    source: str,
    content: str,
) -> str:
    return _session_fingerprint("append", project_id, session_id, source, content)


def _run_creation_fingerprint(
    project_id: str,
    session_id: str,
    request: CreateRunRequest,
) -> str:
    request_payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    # Keep the fingerprint stable for pre-analysis-kind requests while making
    # understanding Runs distinct from the legacy Issue request shape.
    if request.analysis_kind == "issue":
        request_payload.pop("analysis_kind", None)
    payload = {
        "kind": "create-run-v1",
        "project_id": project_id,
        "session_id": session_id,
        "request": request_payload,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _run_input_hash(messages: tuple[SessionMessage, ...]) -> str:
    payload = [message.model_dump(mode="json") for message in messages]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _run_create_response(run: Run) -> CreateRunResponse:
    return CreateRunResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        session_id=run.session_id,
        index_id=run.index_id,
        status="queued",
    )


def _session_fingerprint(*parts: str | None) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _impact_scope_note(direction: ImpactDirection, depth: int) -> str:
    direction_label = "调用方" if direction == "callers" else "被调用方"
    return (
        f"仅统计当前 Run 固定 index 快照内的 calls 静态边，方向为{direction_label}、最大深度 {depth}、"
        "并受节点/边/路径/时间/输出预算限制；静态可达不等同于已确认影响。"
    )


def _index_response(relation) -> IndexResponse:
    return IndexResponse(
        index=relation.snapshot.index,
        issues=[
            IndexIssue(path=issue.path, kind=issue.kind, message=issue.message, size=issue.size)
            for issue in relation.issues
        ],
        truncated=relation.truncated,
        truncation_reason=relation.truncation_reason,
        unresolved_truncated=relation.unresolved_truncated,
        unresolved_truncation_reason=relation.unresolved_truncation_reason,
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


def _external_graph_data(
    results: list[ToolResult],
    *,
    run_id: str,
    index_id: str,
) -> tuple[
    list[object],
    list[object],
    list[object],
    dict[str, object],
    bool,
    str | None,
    list[str],
]:
    """Collect only symbols, edges, unresolved references, and evidence returned by one Run."""

    nodes: dict[str, object] = {}
    static_edges: dict[str, object] = {}
    unresolved: dict[str, object] = {}
    evidence: dict[str, object] = {}
    coverage_limitations: list[str] = []
    truncated = False
    truncation_reason: str | None = None

    def add_item(target: dict[str, object], key: str, value: object, label: str) -> None:
        existing = target.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"external {label} payload conflicts for {key}")
        target[key] = value

    for result in results:
        if result.run_id != run_id or result.index_id != index_id:
            raise ValueError("external tool result crossed the Run snapshot boundary")
        if isinstance(result, SearchCodeSuccess):
            for match in result.matches:
                add_item(nodes, match.symbol.symbol_id, match.symbol, "symbol")
            truncated = truncated or result.truncated
            truncation_reason = truncation_reason or result.truncation_reason
        elif isinstance(result, ReadSourceSuccess):
            add_item(evidence, result.evidence.evidence_id, result.evidence, "evidence")
        elif isinstance(result, GetNeighborsSuccess):
            if result.root is not None:
                add_item(nodes, result.root.symbol_id, result.root, "symbol")
            for symbol in result.nodes:
                add_item(nodes, symbol.symbol_id, symbol, "symbol")
            for neighbor in result.neighbors:
                add_item(nodes, neighbor.symbol.symbol_id, neighbor.symbol, "symbol")
                add_item(static_edges, neighbor.edge.edge_id, neighbor.edge, "edge")
            for item in result.unresolved:
                add_item(unresolved, item.unresolved_id, item, "unresolved reference")
            truncated = truncated or result.truncated
            truncation_reason = truncation_reason or result.truncation_reason
            coverage_limitations.extend(
                relation_coverage_limitations(
                    truncated=result.index_truncated,
                    truncation_reason=result.index_truncation_reason,
                    unresolved_truncated=result.index_unresolved_truncated,
                    unresolved_truncation_reason=result.index_unresolved_truncation_reason,
                )
            )

    if truncated and not truncation_reason:
        truncation_reason = "one or more external tool results were truncated"
    return (
        list(nodes.values()),
        list(static_edges.values()),
        list(unresolved.values()),
        evidence,
        truncated,
        truncation_reason,
        list(dict.fromkeys(coverage_limitations)),
    )


def _external_report_matches_final(
    report: Report,
    final: ExternalRunFinal,
    *,
    derived_limitations: list[str] | tuple[str, ...] = (),
) -> bool:
    """Compare the durable public conclusion for external completion idempotency."""

    matches_common = (
        report.status == "succeeded"
        and report.outcome == final.outcome
        and report.summary == final.summary
        and (
            report.limitations == final.limitations
            or report.limitations
            == list(dict.fromkeys([*final.limitations, *derived_limitations])[:30])
        )
        and [evidence.evidence_id for evidence in report.evidence] == final.evidence_ids
    )
    if not matches_common:
        return False
    if isinstance(final, CodeUnderstandingFinal):
        return (
            report.findings == []
            and report.candidate_links == []
            and report.explanation == final.explanation
        )
    return (
        report.explanation is None
        and report.findings == final.findings
        and report.candidate_links == final.candidate_links
    )
