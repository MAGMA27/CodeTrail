"""Versioned data contracts shared by the API, fixtures, and future UI."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from .config import normalize_model_config_base_url, normalize_model_config_text


SCHEMA_VERSION = "0.1.0"
SchemaVersion: TypeAlias = Literal["0.1.0"]
Identifier: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Sha256: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RelativePath: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class ContractModel(BaseModel):
    """Base model that rejects undeclared fields at every contract boundary."""

    model_config = ConfigDict(extra="forbid")


class VersionedModel(ContractModel):
    """Top-level contract carrying the schema version."""

    schema_version: SchemaVersion = SCHEMA_VERSION


def _validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise ValueError("path must be relative to the indexed repository")
    if any(part in {"", ".", ".."} for part in PurePosixPath(normalized).parts):
        raise ValueError("path must not contain empty, dot, or parent segments")
    return normalized


class LineRangeModel(ContractModel):
    start_line: PositiveInt
    end_line: PositiveInt

    @model_validator(mode="after")
    def validate_line_range(self) -> LineRangeModel:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class SourceLocation(LineRangeModel):
    path: RelativePath

    _normalize_path = field_validator("path")(_validate_relative_path)


class Issue(VersionedModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    issue_id: Identifier = Field(alias="id")
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    stacktrace: str | None = Field(default=None, max_length=20_000)
    mentioned_paths: list[RelativePath] = Field(default_factory=list)

    @field_validator("mentioned_paths")
    @classmethod
    def normalize_paths(cls, paths: list[str]) -> list[str]:
        return [_validate_relative_path(path) for path in paths]


class IndexSnapshot(VersionedModel):
    index_id: Identifier
    repository_id: Identifier
    revision: str = Field(min_length=1, max_length=200)
    dirty: bool
    snapshot_hash: Sha256
    status: Literal["ready", "building", "failed"]
    rules_version: str | None = Field(default=None, min_length=1, max_length=200)
    configuration_hash: Sha256 | None = None


FreshnessStatus: TypeAlias = Literal["unchecked", "consistent", "changed", "unknown"]
RemoteFreshnessStatus: TypeAlias = Literal["not_applicable", "not_checked", "consistent", "changed", "unknown"]
FreshnessReasonCode: TypeAlias = Literal[
    "missing_index_metadata",
    "rules_changed",
    "configuration_changed",
    "source_changed",
    "source_read_error",
    "source_unavailable",
    "manifest_invalid",
    "decode_error",
    "file_too_large",
    "symlink",
    "outside_root",
    "file_changed_during_check",
    "budget_exceeded",
    "remote_not_checked",
    "remote_metadata_missing",
]


class IndexFreshness(VersionedModel):
    """Bounded observation of one project's current source versus an index."""

    project_id: Identifier
    baseline_index_id: Identifier
    status: FreshnessStatus
    checked_at: datetime | None = None
    added_count: NonNegativeInt = 0
    modified_count: NonNegativeInt = 0
    deleted_count: NonNegativeInt = 0
    changed_paths: list[RelativePath] = Field(default_factory=list, max_length=100)
    changed_paths_truncated: bool = False
    scanned_files: NonNegativeInt = 0
    scanned_bytes: NonNegativeInt = 0
    scan_complete: bool = False
    scan_truncated: bool = False
    scan_truncation_reason: str | None = Field(default=None, max_length=200)
    current_rules_version: str | None = Field(default=None, min_length=1, max_length=200)
    current_configuration_hash: Sha256 | None = None
    reasons: list[FreshnessReasonCode] = Field(default_factory=list, max_length=20)
    remote_status: RemoteFreshnessStatus = "not_applicable"
    remote_source_url: str | None = Field(default=None, max_length=4_000)
    remote_requested_ref: str | None = Field(default=None, max_length=200)
    remote_resolved_ref: str | None = Field(default=None, max_length=200)
    remote_resolved_commit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_observation(self) -> IndexFreshness:
        if self.status == "unchecked":
            if self.checked_at is not None:
                raise ValueError("unchecked freshness cannot have a check time")
        elif self.checked_at is None:
            raise ValueError("checked freshness must have a check time")
        if self.scan_truncated and not self.scan_truncation_reason:
            raise ValueError("truncated freshness scans must explain the truncation")
        if not self.scan_truncated and self.scan_truncation_reason:
            raise ValueError("non-truncated freshness scans cannot have a truncation reason")
        remote_values = (
            self.remote_source_url,
            self.remote_requested_ref,
            self.remote_resolved_ref,
            self.remote_resolved_commit,
        )
        if self.remote_status == "not_applicable" and any(value is not None for value in remote_values):
            raise ValueError("non-remote freshness cannot include remote metadata")
        if self.remote_status in {"consistent", "changed"} and (
            self.remote_source_url is None or self.remote_resolved_commit is None
        ):
            raise ValueError("checked remote freshness must include source URL and commit")
        if self.remote_status == "unknown" and self.status != "unknown":
            raise ValueError("unknown remote freshness cannot be reported as a known status")
        if self.remote_status == "changed" and self.status == "consistent":
            raise ValueError("changed remote freshness cannot be reported as consistent")
        if self.status == "consistent" and (
            not self.scan_complete
            or self.added_count
            or self.modified_count
            or self.deleted_count
            or self.changed_paths
            or self.reasons
        ):
            raise ValueError("consistent freshness must describe a complete unchanged scan")
        return self


IndexIssueKind: TypeAlias = Literal[
    "decode_error",
    "file_too_large",
    "read_error",
    "symlink",
    "syntax_error",
    "outside_root",
    "capture_incomplete",
    "parser_unavailable",
    "parser_error",
    "typescript_syntax_error",
    "manifest_invalid",
]


class IndexIssue(ContractModel):
    path: str = Field(min_length=1, max_length=500)
    kind: IndexIssueKind
    message: str = Field(min_length=1, max_length=2_000)
    size: NonNegativeInt | None = None


class CreateIndexRequest(VersionedModel):
    repository_id: Identifier
    revision: str = Field(default="working-tree", min_length=1, max_length=200)
    dirty: bool = True


class CreateProjectRequest(VersionedModel):
    name: str = Field(min_length=1, max_length=200)
    repository_id: Identifier


class LocalImportRequest(VersionedModel):
    """A server-local source registration and bounded synchronous import request."""

    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=4_000)
    revision: str = Field(default="working-tree", min_length=1, max_length=200)
    dirty: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class GitHubImportRequest(VersionedModel):
    """A bounded import request for one public GitHub repository."""

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=4_000)
    ref: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class CreateProjectIndexRequest(VersionedModel):
    revision: str = Field(default="working-tree", min_length=1, max_length=200)
    dirty: bool = True


class RefreshProjectIndexRequest(VersionedModel):
    """Options for an explicit project-local index refresh."""

    revision: str = Field(default="working-tree", min_length=1, max_length=200)
    dirty: bool = True


class IndexResponse(VersionedModel):
    index: IndexSnapshot
    issues: list[IndexIssue] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)
    unresolved_truncated: bool | None = None
    unresolved_truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> IndexResponse:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated indexes must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated indexes cannot have a truncation reason")
        if self.unresolved_truncated is True and not self.unresolved_truncation_reason:
            raise ValueError("truncated unresolved details must explain the truncation")
        if self.unresolved_truncated is not True and self.unresolved_truncation_reason:
            raise ValueError(
                "non-truncated or unknown unresolved details cannot have a truncation reason"
            )
        return self


class IndexRefreshResponse(VersionedModel):
    """Result of an explicit project index update or compatible snapshot reuse."""

    project_id: Identifier
    baseline_index_id: Identifier
    mode: Literal["reused", "historical_reused", "created"]
    index: IndexResponse
    freshness: IndexFreshness

    @model_validator(mode="after")
    def validate_refresh_scope(self) -> IndexRefreshResponse:
        if self.freshness.baseline_index_id != self.baseline_index_id:
            raise ValueError("refresh freshness must use the same baseline index")
        if self.mode == "reused" and self.index.index.index_id != self.baseline_index_id:
            raise ValueError("reused refresh must return the baseline index")
        if self.mode in {"historical_reused", "created"} and self.index.index.index_id == self.baseline_index_id:
            raise ValueError(f"{self.mode} refresh must return a different index")
        return self


class Project(VersionedModel):
    project_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    repository_id: Identifier
    created_at: datetime
    available_index_ids: list[Identifier] = Field(default_factory=list)
    latest_index_id: Identifier | None = None
    building_index_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_index_associations(self) -> Project:
        available_ids = set(self.available_index_ids)
        if len(available_ids) != len(self.available_index_ids):
            raise ValueError("project available index IDs must be unique")
        if self.latest_index_id is not None and self.latest_index_id not in available_ids:
            raise ValueError("project latest index must be available")
        if self.building_index_id is not None and self.building_index_id in available_ids:
            raise ValueError("project building index must not be marked available")
        return self


class ProjectListResponse(VersionedModel):
    projects: list[Project] = Field(default_factory=list)


class ProjectIndexListResponse(VersionedModel):
    project_id: Identifier
    indexes: list[IndexSnapshot] = Field(default_factory=list)
    latest_index_id: Identifier | None = None
    building_index_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_index_list(self) -> ProjectIndexListResponse:
        index_ids = {index.index_id for index in self.indexes}
        if len(index_ids) != len(self.indexes):
            raise ValueError("project indexes must have unique index IDs")
        if self.latest_index_id is not None and self.latest_index_id not in index_ids:
            raise ValueError("project latest index must be present in the index list")
        return self


SessionMessageSource: TypeAlias = Literal["human", "agent"]


class CreateSessionRequest(VersionedModel):
    """Create one investigation session fixed to a ready project snapshot."""

    topic: str = Field(min_length=1, max_length=500)
    index_id: Identifier | None = None
    feature_generation_id: Identifier | None = None
    idempotency_key: Identifier | None = None


class AppendSessionMessageRequest(VersionedModel):
    """Append one visible human or external-agent message to a session."""

    source: SessionMessageSource
    content: str = Field(min_length=1, max_length=20_000)
    idempotency_key: Identifier | None = None


class Session(VersionedModel):
    """A project-scoped investigation with one immutable index binding."""

    session_id: Identifier
    project_id: Identifier
    index: IndexSnapshot
    feature_generation_id: Identifier | None = None
    topic: str = Field(min_length=1, max_length=500)
    created_at: datetime
    updated_at: datetime
    message_count: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_session_binding(self) -> Session:
        if self.index.status != "ready":
            raise ValueError("sessions must use a ready index")
        if self.updated_at < self.created_at:
            raise ValueError("session updated_at cannot be earlier than created_at")
        return self


class SessionMessage(VersionedModel):
    """One append-only visible message with a server-assigned sequence."""

    message_id: Identifier
    session_id: Identifier
    sequence: PositiveInt
    source: SessionMessageSource
    content: str = Field(min_length=1, max_length=20_000)
    created_at: datetime


class SessionResponse(VersionedModel):
    session: Session


class SessionMessageResponse(VersionedModel):
    message: SessionMessage


class SessionListResponse(VersionedModel):
    """Stable newest-activity-first session navigation for one project."""

    project_id: Identifier
    sessions: list[Session] = Field(default_factory=list, max_length=40)
    next_cursor: str | None = Field(default=None, max_length=500)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_session_scope(self) -> SessionListResponse:
        session_ids = {session.session_id for session in self.sessions}
        if len(session_ids) != len(self.sessions):
            raise ValueError("session list must contain unique session IDs")
        if any(session.project_id != self.project_id for session in self.sessions):
            raise ValueError("session list crossed the project boundary")
        if self.has_more and not self.next_cursor:
            raise ValueError("paginated session lists must include a next cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("complete session lists cannot include a next cursor")
        return self


class SessionMessagesResponse(VersionedModel):
    """One ascending, bounded page of append-only messages."""

    project_id: Identifier
    session_id: Identifier
    messages: list[SessionMessage] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=500)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_message_scope(self) -> SessionMessagesResponse:
        sequences = [message.sequence for message in self.messages]
        if any(message.session_id != self.session_id for message in self.messages):
            raise ValueError("session messages crossed the session boundary")
        if len(set(sequences)) != len(sequences) or sequences != sorted(sequences):
            raise ValueError("session messages must be unique and ascending")
        if self.has_more and not self.next_cursor:
            raise ValueError("paginated session messages must include a next cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("complete session messages cannot include a next cursor")
        return self


class SessionReference(VersionedModel):
    """A trusted workbench URL for an explicit project/session pair."""

    project_id: Identifier
    session_id: Identifier
    url: str = Field(min_length=1, max_length=4_000)


ImportStatus: TypeAlias = Literal[
    "queued",
    "acquiring",
    "indexing",
    "ready",
    "failed",
    "cancelled",
    "interrupted",
]

SourceKind: TypeAlias = Literal["local", "github"]
CleanupStatus: TypeAlias = Literal["not_required", "pending", "succeeded", "failed"]


class ImportAttempt(VersionedModel):
    """Persisted lifecycle state for one bounded source import attempt."""

    attempt_id: Identifier
    project_id: Identifier
    repository_id: Identifier
    source_path: str = Field(min_length=1, max_length=4_000)
    status: ImportStatus
    phase: ImportStatus
    index_id: Identifier | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2_000)
    source_kind: SourceKind = "local"
    source_url: str | None = Field(default=None, max_length=4_000)
    requested_ref: str | None = Field(default=None, max_length=200)
    resolved_ref: str | None = Field(default=None, max_length=200)
    resolved_commit: str | None = Field(default=None, max_length=64)
    content_hash: Sha256 | None = None
    cleanup_status: CleanupStatus = "not_required"
    cleanup_error: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_lifecycle(self) -> ImportAttempt:
        if self.status != self.phase:
            raise ValueError("import status and phase must agree")
        if self.status == "ready" and self.index_id is None:
            raise ValueError("ready imports must provide an index_id")
        if self.status in {"failed", "cancelled", "interrupted"} and not self.error_code:
            raise ValueError("unsuccessful imports must provide an error code")
        if self.error_code is None and self.error_message is not None:
            raise ValueError("import error message requires an error code")
        return self


class LocalImportResponse(VersionedModel):
    """HTTP response for one completed or failed local import attempt."""

    source_path: str = Field(min_length=1, max_length=4_000)
    project: Project
    attempt: ImportAttempt
    index: IndexResponse | None = None

    @model_validator(mode="after")
    def validate_index_result(self) -> LocalImportResponse:
        if self.attempt.status == "ready":
            if self.index is None or self.index.index.index_id != self.attempt.index_id:
                raise ValueError("ready import response must include its created index")
        elif self.index is not None:
            raise ValueError("failed or pending import response cannot include a created index")
        return self


class GitHubImportResponse(VersionedModel):
    """HTTP response for a public GitHub repository import."""

    source_url: str = Field(min_length=1, max_length=4_000)
    requested_ref: str | None = Field(default=None, max_length=200)
    resolved_ref: str | None = Field(default=None, max_length=200)
    resolved_commit: str | None = Field(default=None, max_length=64)
    content_hash: Sha256 | None = None
    cleanup_status: CleanupStatus = "not_required"
    cleanup_error: str | None = Field(default=None, max_length=2_000)
    project: Project
    attempt: ImportAttempt
    index: IndexResponse | None = None

    @model_validator(mode="after")
    def validate_index_result(self) -> GitHubImportResponse:
        if self.attempt.source_kind != "github" or self.attempt.source_url != self.source_url:
            raise ValueError("GitHub import response must contain a GitHub attempt")
        if self.attempt.status == "ready":
            if self.index is None or self.index.index.index_id != self.attempt.index_id:
                raise ValueError("ready GitHub import response must include its created index")
            if self.resolved_commit is None or self.content_hash is None:
                raise ValueError("ready GitHub import response must include fixed source identity")
        elif self.index is not None:
            raise ValueError("failed or pending GitHub import response cannot include a created index")
        return self


class ImportAttemptResponse(VersionedModel):
    """HTTP response for a persisted import status lookup."""

    source_path: str = Field(min_length=1, max_length=4_000)
    project: Project
    attempt: ImportAttempt
    latest_index: IndexResponse | None = None


JobStatus: TypeAlias = Literal["queued", "running", "retry_wait", "succeeded", "failed", "cancel_requested", "cancelled", "timed_out"]


class JobAttempt(ContractModel):
    attempt: PositiveInt
    started_at: datetime
    ended_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=200)
    retry_count: NonNegativeInt = 0
    phase: str = Field(min_length=1, max_length=100)


class JobStatusResponse(VersionedModel):
    """Durable state for one asynchronous import or index job."""
    job_id: Identifier
    project_id: Identifier | None = None
    kind: Literal["github_import", "github_retry", "github_cleanup", "github_refresh", "project_index_refresh"]
    status: JobStatus
    phase: str = Field(min_length=1, max_length=100)
    progress: dict[str, object] = Field(default_factory=dict)
    attempts: list[JobAttempt] = Field(default_factory=list, max_length=20)
    result: dict[str, object] | None = None
    error_code: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime
    execution_deadline_at: datetime | None = None
    merged_into: Identifier | None = None


SymbolKind: TypeAlias = Literal["module", "file", "class", "function", "test"]


class Symbol(LineRangeModel):
    symbol_id: Identifier
    index_id: Identifier
    path: RelativePath
    qualified_name: str = Field(min_length=1, max_length=500)
    kind: SymbolKind

    _normalize_path = field_validator("path")(_validate_relative_path)


EdgeKind: TypeAlias = Literal["contains", "imports", "calls"]

ImpactDirection: TypeAlias = Literal["callers", "callees"]
ImpactMappingStatus: TypeAlias = Literal["mapped", "unmapped"]
ImpactLayerKind: TypeAlias = Literal[
    "report_anchor",
    "static_direct",
    "static_indirect",
    "unknown",
]
ImpactLayerDetermination: TypeAlias = Literal[
    "saved_report_symbol",
    "static_calls",
    "unresolved_reference",
]
_IMPACT_LAYER_KINDS: tuple[ImpactLayerKind, ...] = (
    "report_anchor",
    "static_direct",
    "static_indirect",
    "unknown",
)
_IMPACT_LAYER_DETERMINATION: dict[ImpactLayerKind, ImpactLayerDetermination] = {
    "report_anchor": "saved_report_symbol",
    "static_direct": "static_calls",
    "static_indirect": "static_calls",
    "unknown": "unresolved_reference",
}


class CodeEdge(ContractModel):
    edge_id: Identifier
    index_id: Identifier
    source_id: Identifier
    target_id: Identifier
    kind: EdgeKind
    extraction_method: Literal["ast", "explicit_import", "fixture"]
    source_location: SourceLocation


class UnresolvedReference(LineRangeModel):
    unresolved_id: Identifier
    index_id: Identifier
    source_symbol_id: Identifier
    path: RelativePath
    expression: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1_000)
    possible_target_ids: list[Identifier] = Field(default_factory=list)

    _normalize_path = field_validator("path")(_validate_relative_path)


class Evidence(LineRangeModel):
    evidence_id: Identifier
    index_id: Identifier
    path: RelativePath
    excerpt: str = Field(min_length=1, max_length=50_000)
    content_hash: Sha256
    kind: Literal["source", "stacktrace", "metadata"]

    _normalize_path = field_validator("path")(_validate_relative_path)

    @model_validator(mode="after")
    def validate_excerpt_lines(self) -> Evidence:
        excerpt_line_count = len(self.excerpt.splitlines())
        if self.excerpt.endswith(("\n", "\r")):
            excerpt_line_count += 1
        expected_line_count = self.end_line - self.start_line + 1
        if excerpt_line_count != expected_line_count:
            raise ValueError("excerpt line count must match the evidence line range")
        if expected_line_count > 200:
            raise ValueError("evidence may contain at most 200 source lines")
        return self


class EvidenceReference(VersionedModel):
    """A stable source citation that can reopen the same project snapshot."""

    project_id: Identifier
    index_id: Identifier
    path: RelativePath
    start_line: PositiveInt
    end_line: PositiveInt
    content_hash: Sha256
    evidence_id: Identifier
    url: str = Field(min_length=1, max_length=2_000)

    _normalize_path = field_validator("path")(_validate_relative_path)

    @model_validator(mode="after")
    def validate_reference_range(self) -> EvidenceReference:
        if self.end_line < self.start_line:
            raise ValueError("evidence reference end_line must be greater than or equal to start_line")
        return self


class CandidateLink(ContractModel):
    candidate_id: Identifier
    index_id: Identifier
    source_symbol_id: Identifier
    target_symbol_id: Identifier | None = None
    relation: str = Field(min_length=1, max_length=500)
    evidence_ids: list[Identifier] = Field(min_length=1)


class GraphSnapshot(ContractModel):
    index_id: Identifier
    nodes: list[Symbol] = Field(default_factory=list)
    static_edges: list[CodeEdge] = Field(default_factory=list)
    unresolved: list[UnresolvedReference] = Field(default_factory=list)
    candidate_links: list[CandidateLink] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_graph_references(self) -> GraphSnapshot:
        node_ids = {node.symbol_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("graph nodes must have unique symbol_id values")
        for node in self.nodes:
            if node.index_id != self.index_id:
                raise ValueError("graph node belongs to a different index_id")
        for edge in self.static_edges:
            if edge.index_id != self.index_id:
                raise ValueError("static edge belongs to a different index_id")
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                raise ValueError("static edge must reference graph nodes")
        for unresolved in self.unresolved:
            if unresolved.index_id != self.index_id:
                raise ValueError("unresolved reference belongs to a different index_id")
            if unresolved.source_symbol_id not in node_ids:
                raise ValueError("unresolved reference must reference a graph node")
        for link in self.candidate_links:
            if link.index_id != self.index_id:
                raise ValueError("candidate link belongs to a different index_id")
            if link.source_symbol_id not in node_ids:
                raise ValueError("candidate link must reference a graph source node")
            if link.target_symbol_id is not None and link.target_symbol_id not in node_ids:
                raise ValueError("candidate link must reference a graph target node")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated graphs must explain the truncation")
        return self


class RunBudget(ContractModel):
    """Server-enforced limits and usage counters for one Run."""

    max_tool_calls: PositiveInt = Field(default=8, le=12)
    max_model_calls: PositiveInt = Field(default=4, le=6)
    max_repair_calls: NonNegativeInt = Field(default=1, le=2)
    max_seconds: PositiveInt = Field(default=600, le=600)
    max_source_lines: PositiveInt = Field(default=200, le=200)
    max_total_source_lines: PositiveInt = Field(default=800, le=2_000)
    max_input_chars: PositiveInt = Field(default=60_000, le=100_000)
    max_model_input_chars: PositiveInt = Field(default=80_000, le=100_000)
    max_output_chars: PositiveInt = Field(default=50_000, le=100_000)
    max_model_output_chars: PositiveInt = Field(default=20_000, le=50_000)
    max_events: PositiveInt = Field(default=100, ge=2, le=200)
    tool_calls_used: NonNegativeInt = 0
    model_calls_used: NonNegativeInt = 0
    repair_calls_used: NonNegativeInt = 0
    source_lines_used: NonNegativeInt = 0
    input_chars_used: NonNegativeInt = 0
    model_input_chars_used: NonNegativeInt = 0
    output_chars_used: NonNegativeInt = 0
    model_output_chars_used: NonNegativeInt = 0
    model_input_tokens_used: NonNegativeInt | None = None
    model_output_tokens_used: NonNegativeInt | None = None
    events_used: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_budget_usage(self) -> RunBudget:
        if self.tool_calls_used > self.max_tool_calls:
            raise ValueError("tool_calls_used cannot exceed max_tool_calls")
        if self.model_calls_used > self.max_model_calls:
            raise ValueError("model_calls_used cannot exceed max_model_calls")
        if self.repair_calls_used > self.max_repair_calls:
            raise ValueError("repair_calls_used cannot exceed max_repair_calls")
        if self.repair_calls_used > self.model_calls_used:
            raise ValueError("repair_calls_used cannot exceed model_calls_used")
        if self.source_lines_used > self.max_total_source_lines:
            raise ValueError("source_lines_used cannot exceed max_total_source_lines")
        if self.input_chars_used > self.max_input_chars:
            raise ValueError("input_chars_used cannot exceed max_input_chars")
        if self.model_input_chars_used > self.max_model_input_chars:
            raise ValueError("model_input_chars_used cannot exceed max_model_input_chars")
        if self.output_chars_used > self.max_output_chars:
            raise ValueError("output_chars_used cannot exceed max_output_chars")
        if self.model_output_chars_used > self.max_model_output_chars:
            raise ValueError("model_output_chars_used cannot exceed max_model_output_chars")
        if self.events_used > self.max_events:
            raise ValueError("events_used cannot exceed max_events")
        if self.max_total_source_lines < self.max_source_lines:
            raise ValueError("total source line budget must cover one source read")
        if self.max_model_output_chars > self.max_output_chars:
            raise ValueError("model output budget cannot exceed total output budget")
        return self


RunMode: TypeAlias = Literal["fixture", "real", "external"]
RunAnalysisKind: TypeAlias = Literal["issue", "understanding"]
RunExecutionSource: TypeAlias = Literal["internal", "codex"]
FixtureScenario: TypeAlias = Literal[
    "success-timeout-missing",
    "insufficient-external-service",
    "tool-failure-read-source",
]
RunStatus: TypeAlias = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
]
RunTerminalStatus: TypeAlias = Literal["succeeded", "failed", "cancelled", "interrupted"]
ReportOutcome: TypeAlias = Literal[
    "candidate_found",
    "insufficient_evidence",
    "explanation",
    "failed",
    "cancelled",
    "interrupted",
]
ModelOutputOutcome: TypeAlias = Literal["candidate_found", "insufficient_evidence", "failed"]


class RunInput(VersionedModel):
    """The complete saved Session context frozen for one Run."""

    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    feature_generation_id: Identifier | None = None
    message_end_sequence: PositiveInt
    messages: list[SessionMessage] = Field(min_length=1, max_length=1_000)
    input_hash: Sha256
    input_chars: PositiveInt
    frozen_at: datetime

    @model_validator(mode="after")
    def validate_input_sequence(self) -> RunInput:
        expected = list(range(1, self.message_end_sequence + 1))
        sequences = [message.sequence for message in self.messages]
        if sequences != expected:
            raise ValueError("run input messages must contain every sequence through the cutoff")
        if any(message.session_id != self.session_id for message in self.messages):
            raise ValueError("run input messages must belong to the input session")
        if self.input_chars != sum(len(message.content) for message in self.messages):
            raise ValueError("run input character count must match the frozen messages")
        return self


class Run(VersionedModel):
    run_id: Identifier
    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    feature_generation_id: Identifier | None = None
    issue: Issue
    mode: RunMode
    analysis_kind: RunAnalysisKind = "issue"
    execution_source: RunExecutionSource = "internal"
    fixture_scenario: FixtureScenario | None = None
    model_provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    execution_version: Identifier = "run-fixture-v1"
    status: RunStatus
    budget: RunBudget
    message_end_sequence: PositiveInt
    input_hash: Sha256
    input_chars: PositiveInt
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    report_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_run_consistency(self) -> Run:
        if self.issue.schema_version != SCHEMA_VERSION:
            raise ValueError("issue schema_version must match the contract version")
        if self.mode == "fixture" and self.fixture_scenario is None:
            raise ValueError("fixture runs must declare a fixture scenario")
        if self.mode == "real" and self.fixture_scenario is not None:
            raise ValueError("real runs cannot declare a fixture scenario")
        if self.mode == "external" and self.fixture_scenario is not None:
            raise ValueError("external Runs cannot declare a fixture scenario")
        if self.mode == "external" and self.execution_source != "codex":
            raise ValueError("external Runs must use the Codex execution source")
        if self.mode != "external" and self.execution_source != "internal":
            raise ValueError("fixture and real Runs must use the internal execution source")
        if self.mode == "fixture" and self.analysis_kind != "issue":
            raise ValueError("fixture Runs only support Issue analysis")
        if self.input_chars != self.budget.input_chars_used:
            raise ValueError("run input usage must be reflected in the run budget")
        if self.input_chars > self.budget.max_input_chars:
            raise ValueError("run input exceeds the input character budget")
        if self.ended_at and self.ended_at < self.created_at:
            raise ValueError("ended_at cannot be earlier than created_at")
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("ended_at cannot be earlier than started_at")
        if self.status in {"succeeded", "failed", "cancelled", "interrupted"} and not self.ended_at:
            raise ValueError("terminal runs must have ended_at")
        return self


FindingKind: TypeAlias = Literal["observation", "hypothesis", "suggestion"]


class Finding(ContractModel):
    finding_id: Identifier
    kind: FindingKind
    text: str = Field(min_length=1, max_length=5_000)
    symbol_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)


ExplanationStepKind: TypeAlias = Literal["confirmed", "inferred", "unknown"]
ExplanationCoverage: TypeAlias = Literal["complete", "partial"]


class ExplanationStep(ContractModel):
    """One ordered, evidence-aware step in a code understanding report."""

    step_id: Identifier
    kind: ExplanationStepKind
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    symbol_ids: list[Identifier] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_step_evidence(self) -> ExplanationStep:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("explanation step evidence IDs must be unique")
        if len(set(self.symbol_ids)) != len(self.symbol_ids):
            raise ValueError("explanation step symbol IDs must be unique")
        if self.kind == "confirmed" and not self.evidence_ids:
            raise ValueError("confirmed explanation steps require evidence")
        return self


class CodeExplanation(ContractModel):
    """Ordered process explanation separated from static graph facts."""

    coverage: ExplanationCoverage
    steps: list[ExplanationStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_explanation_steps(self) -> CodeExplanation:
        step_ids = {step.step_id for step in self.steps}
        if len(step_ids) != len(self.steps):
            raise ValueError("explanation step IDs must be unique")
        if self.coverage == "complete" and any(step.kind == "unknown" for step in self.steps):
            raise ValueError("complete explanations cannot contain unknown steps")
        return self


class Report(VersionedModel):
    report_id: Identifier
    run_id: Identifier
    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    status: Literal["succeeded", "failed", "cancelled", "interrupted"]
    outcome: ReportOutcome
    fixture_mode: bool = True
    summary: str = Field(min_length=1, max_length=5_000)
    graph: GraphSnapshot
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    candidate_links: list[CandidateLink] = Field(default_factory=list)
    explanation: CodeExplanation | None = None
    limitations: list[str] = Field(default_factory=list, max_length=30)
    terminal_sequence: PositiveInt

    @model_validator(mode="after")
    def validate_report_consistency(self) -> Report:
        if self.graph.index_id != self.index_id:
            raise ValueError("report graph must use the report index_id")
        evidence_ids = {evidence.evidence_id for evidence in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("report evidence IDs must be unique")
        for evidence in self.evidence:
            if evidence.index_id != self.index_id:
                raise ValueError("report evidence belongs to a different index_id")
        for finding in self.findings:
            if not set(finding.evidence_ids).issubset(evidence_ids):
                raise ValueError("finding references evidence absent from the report")
        for link in self.candidate_links:
            if link.index_id != self.index_id or not set(link.evidence_ids).issubset(evidence_ids):
                raise ValueError("candidate link has an invalid report evidence reference")
        if self.explanation is not None:
            explanation_evidence_ids = {
                evidence_id
                for step in self.explanation.steps
                for evidence_id in step.evidence_ids
            }
            if not explanation_evidence_ids.issubset(evidence_ids):
                raise ValueError("explanation references evidence absent from the report")
            graph_symbol_ids = {symbol.symbol_id for symbol in self.graph.nodes}
            if not all(
                set(step.symbol_ids).issubset(graph_symbol_ids)
                for step in self.explanation.steps
            ):
                raise ValueError("explanation references a symbol absent from the report graph")
            if not any(evidence.kind == "source" for evidence in self.evidence):
                raise ValueError("explanation reports must include source evidence")
        if self.outcome == "candidate_found" and not self.candidate_links:
            raise ValueError("candidate_found reports must include candidate links")
        if self.outcome == "insufficient_evidence" and self.candidate_links:
            raise ValueError("insufficient_evidence reports cannot include candidate links")
        if self.outcome == "explanation" and self.explanation is None:
            raise ValueError("explanation reports must include an explanation")
        if self.outcome == "explanation" and (self.findings or self.candidate_links):
            raise ValueError("explanation reports cannot include Issue findings or candidate links")
        if self.outcome != "explanation" and self.explanation is not None:
            raise ValueError("non-explanation reports cannot include an explanation")
        expected_status = {
            "candidate_found": "succeeded",
            "insufficient_evidence": "succeeded",
            "explanation": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
            "interrupted": "interrupted",
        }[self.outcome]
        if self.status != expected_status:
            raise ValueError("report outcome and status must agree")
        return self


class SearchCodeParams(ContractModel):
    tool: Literal["search_code"] = "search_code"
    query: str = Field(min_length=1, max_length=500)
    limit: PositiveInt = Field(le=40)


class ReadSourceParams(ContractModel):
    tool: Literal["read_source"] = "read_source"
    path: RelativePath
    start_line: PositiveInt
    end_line: PositiveInt

    _normalize_path = field_validator("path")(_validate_relative_path)

    @model_validator(mode="after")
    def validate_read_limit(self) -> ReadSourceParams:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.end_line - self.start_line + 1 > 200:
            raise ValueError("read_source cannot return more than 200 lines")
        return self


class GetNeighborsParams(ContractModel):
    tool: Literal["get_neighbors"] = "get_neighbors"
    symbol_id: Identifier
    direction: Literal["callers", "callees", "both"]
    depth: PositiveInt = Field(le=2)
    limit: PositiveInt = Field(le=40)


ToolParams: TypeAlias = Annotated[
    SearchCodeParams | ReadSourceParams | GetNeighborsParams,
    Field(discriminator="tool"),
]


class IssueModelToolCall(VersionedModel):
    """One allow-listed tool decision returned by the Issue analysis model."""

    kind: Literal["tool_call"] = "tool_call"
    params: ToolParams


class IssueModelFinal(VersionedModel):
    """A structured Issue conclusion before server-side reference validation."""

    kind: Literal["final"] = "final"
    index_id: Identifier
    outcome: Literal["candidate_found", "insufficient_evidence"]
    summary: str = Field(min_length=1, max_length=5_000)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    candidate_links: list[CandidateLink] = Field(default_factory=list, max_length=20)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_issue_output(self) -> IssueModelFinal:
        if self.outcome == "candidate_found" and not self.candidate_links:
            raise ValueError("candidate_found Issue output must include candidate links")
        if self.outcome == "insufficient_evidence" and self.candidate_links:
            raise ValueError("insufficient_evidence Issue output cannot include candidate links")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Issue output evidence IDs must be unique")
        finding_ids = {finding.finding_id for finding in self.findings}
        if len(finding_ids) != len(self.findings):
            raise ValueError("Issue output finding IDs must be unique")
        candidate_ids = {link.candidate_id for link in self.candidate_links}
        if len(candidate_ids) != len(self.candidate_links):
            raise ValueError("Issue output candidate IDs must be unique")
        referenced_evidence = set(self.evidence_ids)
        for finding in self.findings:
            if not set(finding.evidence_ids).issubset(referenced_evidence):
                raise ValueError("Issue finding references evidence absent from output")
        for link in self.candidate_links:
            if not set(link.evidence_ids).issubset(referenced_evidence):
                raise ValueError("Issue candidate link references evidence absent from output")
        return self


class CodeUnderstandingFinal(VersionedModel):
    """A structured code-flow conclusion for an understanding Run."""

    kind: Literal["explanation"] = "explanation"
    index_id: Identifier
    outcome: Literal["explanation"] = "explanation"
    summary: str = Field(min_length=1, max_length=5_000)
    explanation: CodeExplanation
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_understanding_output(self) -> CodeUnderstandingFinal:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("understanding output evidence IDs must be unique")
        referenced_by_steps = {
            evidence_id
            for step in self.explanation.steps
            for evidence_id in step.evidence_ids
        }
        if set(self.evidence_ids) != referenced_by_steps:
            raise ValueError("understanding output evidence must exactly support the explanation steps")
        return self


ExternalRunFinal: TypeAlias = IssueModelFinal | CodeUnderstandingFinal


IssueModelOutput: TypeAlias = Annotated[
    IssueModelToolCall | IssueModelFinal,
    Field(discriminator="kind"),
]
UnderstandingModelOutput: TypeAlias = Annotated[
    IssueModelToolCall | CodeUnderstandingFinal,
    Field(discriminator="kind"),
]


class ToolRequest(ContractModel):
    request_id: Identifier
    run_id: Identifier
    index_id: Identifier
    params: ToolParams


class ToolError(ContractModel):
    code: Literal[
        "fixture_read_source_error",
        "not_found",
        "invalid_input",
        "budget_exceeded",
        "internal_error",
    ]
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class SearchMatch(ContractModel):
    rank: PositiveInt
    symbol: Symbol
    matched_terms: list[str] = Field(min_length=1)


class Neighbor(ContractModel):
    symbol: Symbol
    edge: CodeEdge


class ContextualCall(ContractModel):
    """One self-field call proven only for a specific constructed receiver."""

    context_id: Identifier
    index_id: Identifier
    activation_edge_id: Identifier
    activation_source: Symbol
    source: Symbol
    target: Symbol
    activation_location: SourceLocation
    source_location: SourceLocation
    receiver_constructor_location: SourceLocation
    field_assignment_location: SourceLocation
    field_name: str = Field(min_length=1, max_length=200)
    binding_mutation_after_escape_unverified: bool = False

    @model_validator(mode="after")
    def validate_context_scope(self) -> ContextualCall:
        if any(
            symbol.index_id != self.index_id
            for symbol in (self.activation_source, self.source, self.target)
        ):
            raise ValueError("contextual calls must use one index_id")
        if self.source.kind not in {"function", "test"} or self.target.kind not in {"function", "test"}:
            raise ValueError("contextual calls must reference method symbols")
        if not self.field_name.isidentifier():
            raise ValueError("contextual call field names must be identifiers")
        if (
            self.activation_location.path != self.activation_source.path
            or self.source_location.path != self.source.path
            or not self.activation_source.start_line <= self.activation_location.start_line
            or self.activation_location.end_line > self.activation_source.end_line
            or not self.source.start_line <= self.source_location.start_line
            or self.source_location.end_line > self.source.end_line
        ):
            raise ValueError("contextual call locations must belong to their source symbols")
        return self


class ToolResultBase(ContractModel):
    request_id: Identifier
    run_id: Identifier
    index_id: Identifier
    completed_sequence: PositiveInt


class SearchCodeSuccess(ToolResultBase):
    kind: Literal["search_code.succeeded"] = "search_code.succeeded"
    tool: Literal["search_code"] = "search_code"
    matches: list[SearchMatch]
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> SearchCodeSuccess:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated searches must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated searches cannot have a truncation reason")
        return self


class SearchCodeFailure(ToolResultBase):
    kind: Literal["search_code.failed"] = "search_code.failed"
    tool: Literal["search_code"] = "search_code"
    error: ToolError


class ReadSourceSuccess(ToolResultBase):
    kind: Literal["read_source.succeeded"] = "read_source.succeeded"
    tool: Literal["read_source"] = "read_source"
    evidence: Evidence


class ReadSourceFailure(ToolResultBase):
    kind: Literal["read_source.failed"] = "read_source.failed"
    tool: Literal["read_source"] = "read_source"
    error: ToolError


class GetNeighborsSuccess(ToolResultBase):
    kind: Literal["get_neighbors.succeeded"] = "get_neighbors.succeeded"
    tool: Literal["get_neighbors"] = "get_neighbors"
    root: Symbol | None = None
    nodes: list[Symbol] = Field(default_factory=list, max_length=40)
    neighbors: list[Neighbor]
    contextual_calls: list[ContextualCall] = Field(default_factory=list, max_length=200)
    unresolved: list[UnresolvedReference] = Field(default_factory=list, max_length=40)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_truncated: bool = False
    index_truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_unresolved_truncated: bool | None = None
    index_unresolved_truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> GetNeighborsSuccess:
        neighbor_edges = {neighbor.edge.edge_id: neighbor.edge for neighbor in self.neighbors}
        returned_symbols: dict[str, Symbol] = {}

        def include_returned_symbol(symbol: Symbol) -> None:
            existing = returned_symbols.get(symbol.symbol_id)
            if existing is not None and existing != symbol:
                raise ValueError("neighbor results contain conflicting symbol metadata")
            returned_symbols.setdefault(symbol.symbol_id, symbol)

        for symbol in self.nodes:
            if symbol.index_id != self.index_id:
                raise ValueError("neighbor node belongs to a different index_id")
            include_returned_symbol(symbol)
        if self.root is not None:
            if self.root.index_id != self.index_id:
                raise ValueError("neighbor root belongs to a different index_id")
            include_returned_symbol(self.root)
        for neighbor in self.neighbors:
            if neighbor.symbol.index_id != self.index_id or neighbor.edge.index_id != self.index_id:
                raise ValueError("neighbor result belongs to a different index_id")
            include_returned_symbol(neighbor.symbol)
        context_ids: set[str] = set()
        for contextual_call in self.contextual_calls:
            context_ids.add(contextual_call.context_id)
            edge = neighbor_edges.get(contextual_call.activation_edge_id)
            if (
                contextual_call.index_id != self.index_id
                or edge is None
                or edge.kind != "calls"
                or edge.source_id != contextual_call.activation_source.symbol_id
                or edge.target_id != contextual_call.source.symbol_id
                or edge.source_location != contextual_call.activation_location
                or returned_symbols.get(edge.source_id) != contextual_call.activation_source
                or returned_symbols.get(edge.target_id) != contextual_call.source
            ):
                raise ValueError("contextual calls must bind to a returned activation edge")
        if len(context_ids) != len(self.contextual_calls):
            raise ValueError("contextual call IDs must be unique")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated neighbor results must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated neighbor results cannot have a truncation reason")
        if self.index_truncated and not self.index_truncation_reason:
            raise ValueError("truncated neighbor indexes must explain the truncation")
        if not self.index_truncated and self.index_truncation_reason:
            raise ValueError("non-truncated neighbor indexes cannot have a truncation reason")
        if self.index_unresolved_truncated is True and not self.index_unresolved_truncation_reason:
            raise ValueError("truncated unresolved details must explain the truncation")
        if self.index_unresolved_truncated is not True and self.index_unresolved_truncation_reason:
            raise ValueError(
                "non-truncated or unknown unresolved details cannot have a truncation reason"
            )
        return self


class GetNeighborsFailure(ToolResultBase):
    kind: Literal["get_neighbors.failed"] = "get_neighbors.failed"
    tool: Literal["get_neighbors"] = "get_neighbors"
    error: ToolError


class SearchIndexResponse(VersionedModel):
    index_id: Identifier
    matches: list[SearchMatch] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> SearchIndexResponse:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated searches must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated searches cannot have a truncation reason")
        return self


class ModuleSymbolsPageResponse(VersionedModel):
    """One bounded page of definitions from a fixed snapshot module.

    This lists definitions persisted by the indexer for the module's captured
    path; it does not assert that parsing or symbol extraction was complete.
    """

    project_id: Identifier
    index_id: Identifier
    module: Symbol
    symbols: list[Symbol] = Field(default_factory=list, max_length=40)
    next_cursor: str | None = Field(default=None, max_length=500)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_module_scope(self) -> ModuleSymbolsPageResponse:
        if self.module.index_id != self.index_id or self.module.kind != "module":
            raise ValueError("module symbols page must include its module root from the selected index")
        symbol_ids = {symbol.symbol_id for symbol in self.symbols}
        if len(symbol_ids) != len(self.symbols):
            raise ValueError("module symbols page must contain unique symbols")
        if any(
            symbol.index_id != self.index_id
            or symbol.path != self.module.path
            or symbol.kind not in {"class", "function", "test"}
            for symbol in self.symbols
        ):
            raise ValueError("module symbols page crossed its index or module scope")
        order_keys = [(symbol.start_line, symbol.symbol_id) for symbol in self.symbols]
        if order_keys != sorted(order_keys):
            raise ValueError("module symbol pages must use stable source order")
        if self.has_more and (not self.next_cursor or not self.symbols):
            raise ValueError("module symbol pages with more results must include a cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("complete module symbol pages cannot include a cursor")
        return self


class SourceIndexResponse(VersionedModel):
    index_id: Identifier
    evidence: Evidence


class NeighborsIndexResponse(VersionedModel):
    index_id: Identifier
    root: Symbol
    nodes: list[Symbol] = Field(default_factory=list)
    neighbors: list[Neighbor] = Field(default_factory=list)
    contextual_calls: list[ContextualCall] = Field(default_factory=list, max_length=200)
    unresolved: list[UnresolvedReference] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_truncated: bool = False
    index_truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_unresolved_truncated: bool | None = None
    index_unresolved_truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> NeighborsIndexResponse:
        if self.root.index_id != self.index_id:
            raise ValueError("neighbor root must use the response index_id")
        node_ids = {node.symbol_id for node in self.nodes}
        if self.root.symbol_id not in node_ids:
            raise ValueError("neighbor nodes must include the root symbol")
        if len(node_ids) != len(self.nodes):
            raise ValueError("neighbor nodes must have unique symbol IDs")
        for node in self.nodes:
            if node.index_id != self.index_id:
                raise ValueError("neighbor node belongs to a different index_id")
        for neighbor in self.neighbors:
            if neighbor.symbol.index_id != self.index_id or neighbor.edge.index_id != self.index_id:
                raise ValueError("neighbor result belongs to a different index_id")
            if neighbor.symbol.symbol_id not in node_ids:
                raise ValueError("neighbor result must reference a returned node")
            if neighbor.edge.source_id not in node_ids or neighbor.edge.target_id not in node_ids:
                raise ValueError("neighbor edge must reference returned nodes")
        neighbor_edges = {neighbor.edge.edge_id: neighbor.edge for neighbor in self.neighbors}
        nodes_by_id = {node.symbol_id: node for node in self.nodes}
        context_ids: set[str] = set()
        for contextual_call in self.contextual_calls:
            context_ids.add(contextual_call.context_id)
            edge = neighbor_edges.get(contextual_call.activation_edge_id)
            if (
                contextual_call.index_id != self.index_id
                or edge is None
                or edge.kind != "calls"
                or edge.source_id != contextual_call.activation_source.symbol_id
                or edge.target_id != contextual_call.source.symbol_id
                or edge.source_location != contextual_call.activation_location
                or nodes_by_id.get(edge.source_id) != contextual_call.activation_source
                or nodes_by_id.get(edge.target_id) != contextual_call.source
            ):
                raise ValueError("contextual calls must bind to a returned activation edge")
        if len(context_ids) != len(self.contextual_calls):
            raise ValueError("contextual call IDs must be unique")
        for unresolved in self.unresolved:
            if unresolved.index_id != self.index_id or unresolved.source_symbol_id not in node_ids:
                raise ValueError("unresolved reference must use returned nodes")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated neighbor results must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated neighbor results cannot have a truncation reason")
        if self.index_truncated and not self.index_truncation_reason:
            raise ValueError("truncated neighbor indexes must explain the truncation")
        if not self.index_truncated and self.index_truncation_reason:
            raise ValueError("non-truncated neighbor indexes cannot have a truncation reason")
        if self.index_unresolved_truncated is True and not self.index_unresolved_truncation_reason:
            raise ValueError("truncated unresolved details must explain the truncation")
        if self.index_unresolved_truncated is not True and self.index_unresolved_truncation_reason:
            raise ValueError(
                "non-truncated or unknown unresolved details cannot have a truncation reason"
            )
        return self


class ImpactLayer(ContractModel):
    """One read-only slice of a bounded Issue impact query.

    Membership comes from the saved report symbol, a shortest static ``calls``
    path, or an unresolved reference. None of these slices is confirmed impact.
    """

    kind: ImpactLayerKind
    determination: ImpactLayerDetermination
    symbol_ids: list[Identifier] = Field(default_factory=list, max_length=40)
    unresolved_ids: list[Identifier] = Field(default_factory=list, max_length=40)
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_layer_shape(self) -> ImpactLayer:
        if self.determination != _IMPACT_LAYER_DETERMINATION[self.kind]:
            raise ValueError("impact layer determination does not match its kind")
        if self.kind == "unknown" and self.symbol_ids:
            raise ValueError("unknown impact layer cannot contain symbols")
        if self.kind != "unknown" and self.unresolved_ids:
            raise ValueError("symbol impact layers cannot contain unresolved ids")
        if self.kind == "report_anchor" and len(self.symbol_ids) > 1:
            raise ValueError("report anchor contains at most one symbol")
        if len(set(self.symbol_ids)) != len(self.symbol_ids):
            raise ValueError("impact layer symbol ids must be unique")
        if len(set(self.unresolved_ids)) != len(self.unresolved_ids):
            raise ValueError("impact layer unresolved ids must be unique")
        return self


class ImpactPath(ContractModel):
    """One explainable static calls path from an impact root."""

    path_id: Identifier
    index_id: Identifier
    symbol_ids: list[Identifier] = Field(min_length=2, max_length=3)
    edges: list[CodeEdge] = Field(min_length=1, max_length=2)
    depth: PositiveInt = Field(le=2)

    @model_validator(mode="after")
    def validate_path_shape(self) -> ImpactPath:
        if len(self.edges) != len(self.symbol_ids) - 1:
            raise ValueError("impact path edges must join every adjacent symbol")
        if self.depth != len(self.edges):
            raise ValueError("impact path depth must match its edge count")
        if len(set(self.symbol_ids)) != len(self.symbol_ids):
            raise ValueError("impact paths must not repeat symbols")
        symbol_ids = set(self.symbol_ids)
        for edge in self.edges:
            if edge.index_id != self.index_id or edge.kind != "calls":
                raise ValueError("impact paths may contain only same-index calls edges")
            if edge.source_id not in symbol_ids or edge.target_id not in symbol_ids:
                raise ValueError("impact path edges must reference path symbols")
        return self


class RunImpactResponse(VersionedModel):
    """A bounded static impact view derived from one saved Run report."""

    project_id: Identifier
    session_id: Identifier
    run_id: Identifier
    index_id: Identifier
    candidate_id: Identifier | None = None
    finding_id: Identifier | None = None
    mapping_status: ImpactMappingStatus
    direction: ImpactDirection
    max_depth: PositiveInt = Field(le=2)
    root_symbol_id: Identifier | None = None
    root: Symbol | None = None
    nodes: list[Symbol] = Field(default_factory=list, max_length=40)
    edges: list[CodeEdge] = Field(default_factory=list, max_length=80)
    paths: list[ImpactPath] = Field(default_factory=list, max_length=40)
    unresolved: list[UnresolvedReference] = Field(default_factory=list, max_length=40)
    layers: list[ImpactLayer] = Field(min_length=4, max_length=4)
    reachable_node_count: NonNegativeInt = 0
    returned_node_count: NonNegativeInt = 0
    scope_note: str = Field(min_length=1, max_length=1_000)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_truncated: bool = False
    index_truncation_reason: str | None = Field(default=None, max_length=1_000)
    index_unresolved_truncated: bool | None = None
    index_unresolved_truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_impact_consistency(self) -> RunImpactResponse:
        if self.index_truncated and not self.index_truncation_reason:
            raise ValueError("truncated impact indexes must explain the truncation")
        if not self.index_truncated and self.index_truncation_reason:
            raise ValueError("non-truncated impact indexes cannot have a truncation reason")
        if self.index_unresolved_truncated is True and not self.index_unresolved_truncation_reason:
            raise ValueError("truncated unresolved details must explain the truncation")
        if self.index_unresolved_truncated is not True and self.index_unresolved_truncation_reason:
            raise ValueError(
                "non-truncated or unknown unresolved details cannot have a truncation reason"
            )
        if (self.candidate_id is None) == (self.finding_id is None):
            raise ValueError("impact response must identify exactly one candidate or finding")
        if self.returned_node_count != len(self.nodes):
            raise ValueError("returned_node_count must match the returned nodes")
        if self.reachable_node_count != max(0, len(self.nodes) - 1):
            raise ValueError("reachable_node_count must exclude the root node")
        if self.mapping_status == "mapped":
            if self.root is None or self.root_symbol_id is None:
                raise ValueError("mapped impact responses require a root symbol")
            if self.root.symbol_id != self.root_symbol_id:
                raise ValueError("impact root_symbol_id must match root")
        elif self.root is not None or self.root_symbol_id is not None:
            raise ValueError("unmapped impact responses cannot include a root symbol")
        node_ids = {node.symbol_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("impact nodes must have unique symbol IDs")
        for node in self.nodes:
            if node.index_id != self.index_id:
                raise ValueError("impact node belongs to a different index_id")
        if self.mapping_status == "mapped":
            assert self.root is not None
            if self.root.index_id != self.index_id or self.root.symbol_id not in node_ids:
                raise ValueError("impact root must belong to the returned index and nodes")
        edge_ids: set[str] = set()
        for edge in self.edges:
            if edge.edge_id in edge_ids:
                raise ValueError("impact edges must have unique edge IDs")
            edge_ids.add(edge.edge_id)
            if edge.index_id != self.index_id or edge.kind != "calls":
                raise ValueError("impact edges must be same-index calls edges")
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                raise ValueError("impact edges must reference returned nodes")
        path_ids: set[str] = set()
        edge_by_id = {edge.edge_id: edge for edge in self.edges}
        for path in self.paths:
            if path.path_id in path_ids:
                raise ValueError("impact paths must have unique path IDs")
            path_ids.add(path.path_id)
            if path.index_id != self.index_id:
                raise ValueError("impact path belongs to a different index_id")
            if self.mapping_status != "mapped" or self.root_symbol_id is None:
                raise ValueError("unmapped impact responses cannot include paths")
            if path.symbol_ids[0] != self.root_symbol_id:
                raise ValueError("impact paths must start at the root")
            for symbol_id in path.symbol_ids:
                if symbol_id not in node_ids:
                    raise ValueError("impact paths must reference returned nodes")
            for edge in path.edges:
                if edge.edge_id not in edge_by_id:
                    raise ValueError("impact paths must reference returned edges")
            for source_id, target_id, edge in zip(path.symbol_ids, path.symbol_ids[1:], path.edges):
                expected_source, expected_target = (
                    (target_id, source_id) if self.direction == "callers" else (source_id, target_id)
                )
                if edge.source_id != expected_source or edge.target_id != expected_target:
                    raise ValueError("impact path edge direction does not match the requested direction")
        if self.mapping_status == "unmapped" and (
            self.nodes or self.edges or self.paths or self.unresolved or self.reachable_node_count
        ):
            raise ValueError("unmapped impact responses must be empty")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated impact responses must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated impact responses cannot have a truncation reason")
        if tuple(layer.kind for layer in self.layers) != _IMPACT_LAYER_KINDS:
            raise ValueError(
                "impact layers must be report_anchor, static_direct, static_indirect, and unknown"
            )
        anchor, direct, indirect, unknown = self.layers
        if self.mapping_status == "unmapped":
            if any(layer.symbol_ids or layer.unresolved_ids for layer in self.layers):
                raise ValueError("unmapped impact layers must be empty")
            return self
        if anchor.symbol_ids != [self.root_symbol_id]:
            raise ValueError("report anchor must be the impact root")
        if self.root_symbol_id in direct.symbol_ids or self.root_symbol_id in indirect.symbol_ids:
            raise ValueError("impact root stays in the report anchor layer")
        if set(direct.symbol_ids) & set(indirect.symbol_ids):
            raise ValueError("a symbol cannot sit in both static impact layers")
        covered = set(anchor.symbol_ids) | set(direct.symbol_ids) | set(indirect.symbol_ids)
        if covered != node_ids:
            raise ValueError("impact layers must cover every returned node exactly once")
        shortest: dict[str, int] = {}
        for path in self.paths:
            for index, symbol_id in enumerate(path.symbol_ids):
                current = shortest.get(symbol_id)
                if current is None or index < current:
                    shortest[symbol_id] = index
        if any(shortest.get(symbol_id) != 1 for symbol_id in direct.symbol_ids):
            raise ValueError("static_direct symbols must have shortest calls depth 1")
        if any(shortest.get(symbol_id) != 2 for symbol_id in indirect.symbol_ids):
            raise ValueError("static_indirect symbols must have shortest calls depth 2")
        if self.max_depth == 1 and indirect.symbol_ids:
            raise ValueError("depth 1 impact cannot include an indirect layer")
        if unknown.unresolved_ids != [item.unresolved_id for item in self.unresolved]:
            raise ValueError("unknown layer must list returned unresolved ids in order")
        return self


OverviewEntryKind: TypeAlias = Literal["python_main_module", "python_main_guard", "fastapi_route", "pluggy_hookimpl"]
OverviewHttpMethod: TypeAlias = Literal[
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "HEAD",
    "TRACE",
]
OverviewEntryStatus: TypeAlias = Literal["identified", "not_identified", "partial"]
OverviewGroupKind: TypeAlias = Literal["http_path", "source_directory", "responsibility"]
OverviewModuleRelationKind: TypeAlias = Literal["imports", "calls"]
OverviewClassificationKind: TypeAlias = Literal[
    "application_entry", "public_api", "extension_point", "unknown"
]


class OverviewClassificationCounts(ContractModel):
    application_entry: NonNegativeInt = 0
    public_api: NonNegativeInt = 0
    extension_point: NonNegativeInt = 0
    unknown: NonNegativeInt = 0

    def total(self) -> int:
        return self.application_entry + self.public_api + self.extension_point + self.unknown


class OverviewClassificationCoverage(ContractModel):
    total: NonNegativeInt
    visible: NonNegativeInt
    category_counts: OverviewClassificationCounts
    visible_category_counts: OverviewClassificationCounts
    truncated: bool

    @model_validator(mode="after")
    def validate_counts(self) -> OverviewClassificationCoverage:
        if self.visible > self.total or self.category_counts.total() != self.total:
            raise ValueError("architecture classification coverage counts are inconsistent")
        if self.visible_category_counts.total() != self.visible:
            raise ValueError("visible architecture classification counts are inconsistent")
        if any(
            getattr(self.visible_category_counts, name) > getattr(self.category_counts, name)
            for name in ("application_entry", "public_api", "extension_point", "unknown")
        ):
            raise ValueError("visible architecture classification categories cannot exceed totals")
        return self


class TrashListResponse(VersionedModel):
    """Recoverable projects and independently removed sessions."""

    projects: list[Project] = Field(default_factory=list)
    sessions: list[Session] = Field(default_factory=list)


class OverviewClassification(ContractModel):
    """One rule-confirmed architecture anchor, separate from static relations."""

    classification_id: Identifier
    index_id: Identifier
    category: OverviewClassificationKind
    module_id: Identifier
    symbol_id: Identifier | None = None
    symbol_name: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=500)
    rule_id: Identifier
    entry_point_id: Identifier | None = None
    evidence: list[Evidence] = Field(default_factory=list, max_length=4)
    evidence_error: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_evidence(self) -> OverviewClassification:
        if not self.evidence and self.evidence_error is None and self.entry_point_id is None:
            raise ValueError("architecture classifications require evidence or an entry reference")
        if any(evidence.index_id != self.index_id or evidence.kind != "source" for evidence in self.evidence):
            raise ValueError("architecture classification evidence must belong to its source index")
        if self.category == "application_entry":
            if self.entry_point_id is None:
                raise ValueError("application classifications must reference an overview entry")
            if self.evidence:
                raise ValueError("application classifications reuse entry evidence")
        elif self.entry_point_id is not None:
            raise ValueError("only application classifications can reference an overview entry")
        if self.category == "unknown" and self.symbol_id is not None:
            raise ValueError("unknown classifications identify a module region, not a symbol")
        if self.category in {"public_api", "extension_point"} and self.symbol_id is None:
            raise ValueError("identified API and extension points require a symbol")
        return self


class OverviewEntryPoint(LineRangeModel):
    """One rule-supported Python or FastAPI entry candidate in an immutable snapshot."""

    entry_point_id: Identifier
    index_id: Identifier
    path: RelativePath
    kind: OverviewEntryKind
    rule_id: Identifier
    label: str = Field(min_length=1, max_length=500)
    symbol_id: Identifier | None = None
    http_method: OverviewHttpMethod | None = None
    declared_path: str | None = Field(default=None, min_length=1, max_length=2_000)
    handler_symbol: Symbol | None = None
    evidence: Evidence | None = None
    evidence_error: str | None = Field(default=None, max_length=1_000)
    direct_symbol_ids: list[Identifier] = Field(default_factory=list)
    unresolved_ids: list[Identifier] = Field(default_factory=list)

    _normalize_path = field_validator("path")(_validate_relative_path)

    @model_validator(mode="after")
    def validate_evidence(self) -> OverviewEntryPoint:
        if self.evidence is None and self.evidence_error is None:
            raise ValueError("overview entry points must include evidence or an evidence error")
        if self.evidence is not None:
            if (
                self.evidence.index_id != self.index_id
                or self.evidence.path != self.path
                or self.evidence.start_line < self.start_line
                or self.evidence.end_line > self.end_line
            ):
                raise ValueError("overview entry evidence must belong to the entry range")
        if self.kind == "fastapi_route":
            if self.http_method is None or self.declared_path is None or self.handler_symbol is None:
                raise ValueError("FastAPI route entries must include method, declared path, and handler symbol")
            if not self.declared_path.startswith("/"):
                raise ValueError("FastAPI route declared paths must start with '/'")
            if self.symbol_id != self.handler_symbol.symbol_id:
                raise ValueError("FastAPI route symbol_id must identify the handler symbol")
            if (
                self.handler_symbol.index_id != self.index_id
                or self.handler_symbol.path != self.path
                or self.handler_symbol.kind != "function"
                or self.handler_symbol.start_line < self.start_line
                or self.handler_symbol.end_line > self.end_line
            ):
                raise ValueError("FastAPI route handler must belong to the entry source range")
        elif self.http_method is not None or self.declared_path is not None or self.handler_symbol is not None:
            raise ValueError("non-route overview entries cannot include FastAPI route fields")
        if len(set(self.direct_symbol_ids)) != len(self.direct_symbol_ids):
            raise ValueError("overview entry direct symbols must be unique")
        if len(set(self.unresolved_ids)) != len(self.unresolved_ids):
            raise ValueError("overview entry unresolved IDs must be unique")
        return self


class OverviewModule(ContractModel):
    """A Python module classified by entry reachability, not by business meaning."""

    module_id: Identifier
    index_id: Identifier
    symbol: Symbol
    entry_point_ids: list[Identifier] = Field(default_factory=list)
    entry_reachable: bool = False
    classification_counts: OverviewClassificationCounts = Field(default_factory=OverviewClassificationCounts)

    @model_validator(mode="after")
    def validate_module_identity(self) -> OverviewModule:
        if self.symbol.index_id != self.index_id or self.module_id != self.symbol.symbol_id:
            raise ValueError("overview module must use its module symbol identity")
        if self.symbol.kind != "module":
            raise ValueError("overview modules must reference module symbols")
        if len(set(self.entry_point_ids)) != len(self.entry_point_ids):
            raise ValueError("overview module entry IDs must be unique")
        return self


class OverviewGroup(ContractModel):
    """A deterministic structural group, never a model-confirmed feature."""

    group_id: Identifier
    index_id: Identifier
    group_kind: OverviewGroupKind
    group_key: str = Field(min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=500)
    module_ids: list[Identifier] = Field(default_factory=list)
    entry_point_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_group_shape(self) -> OverviewGroup:
        if len(set(self.module_ids)) != len(self.module_ids):
            raise ValueError("overview group module IDs must be unique")
        if len(set(self.entry_point_ids)) != len(self.entry_point_ids):
            raise ValueError("overview group entry IDs must be unique")
        if self.group_kind == "http_path":
            if not self.group_key.startswith("/"):
                raise ValueError("HTTP overview group keys must be path prefixes")
            if not self.entry_point_ids:
                raise ValueError("HTTP overview groups must contain entries")
        elif self.group_kind == "source_directory" and not self.module_ids:
            raise ValueError("source directory overview groups must contain modules")
        elif self.group_kind == "responsibility" and self.entry_point_ids:
            raise ValueError("responsibility groups cannot contain entry points")
        return self


class OverviewModuleRelation(ContractModel):
    """Aggregated immutable module dependency with representative source edges."""

    relation_id: Identifier
    index_id: Identifier
    source_module_id: Identifier
    target_module_id: Identifier
    source_module: Symbol
    target_module: Symbol
    relation_kinds: list[OverviewModuleRelationKind] = Field(min_length=1, max_length=2)
    edge_count: PositiveInt
    edges: list[CodeEdge] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_relation_shape(self) -> OverviewModuleRelation:
        if self.source_module_id == self.target_module_id:
            raise ValueError("overview module relations cannot be self edges")
        if (
            self.source_module.index_id != self.index_id
            or self.target_module.index_id != self.index_id
            or self.source_module.symbol_id != self.source_module_id
            or self.target_module.symbol_id != self.target_module_id
            or self.source_module.kind != "module"
            or self.target_module.kind != "module"
        ):
            raise ValueError("overview module relation modules must match the snapshot")
        if len(set(self.relation_kinds)) != len(self.relation_kinds):
            raise ValueError("overview module relation kinds must be unique")
        if len(set(edge.edge_id for edge in self.edges)) != len(self.edges):
            raise ValueError("overview module relation edges must be unique")
        if self.edge_count < len(self.edges):
            raise ValueError("overview module relation edge_count cannot undercount samples")
        for edge in self.edges:
            if edge.index_id != self.index_id or edge.kind not in self.relation_kinds:
                raise ValueError("overview module relation edges must match the snapshot and kinds")
        return self


class OverviewHttpAssociationCoverage(ContractModel):
    """Coverage for verified static client requests matched to same-index routes."""

    analysis_status: Literal["complete", "incomplete", "not_analyzed"] = "not_analyzed"
    path_analysis: Literal["parameterized", "literal_only", "not_analyzed"] = "not_analyzed"
    client_request_count: NonNegativeInt
    matched_count: NonNegativeInt
    visible_match_count: NonNegativeInt
    unmatched_count: NonNegativeInt
    ambiguous_count: NonNegativeInt
    unknown_count: NonNegativeInt

    @model_validator(mode="after")
    def validate_counts(self) -> OverviewHttpAssociationCoverage:
        if self.visible_match_count > self.matched_count:
            raise ValueError("visible HTTP associations cannot exceed matched requests")
        if self.matched_count + self.unmatched_count + self.ambiguous_count + self.unknown_count != self.client_request_count:
            raise ValueError("HTTP association coverage must account for each verified client request")
        if self.analysis_status == "not_analyzed" and any(
            (self.client_request_count, self.matched_count, self.visible_match_count,
             self.unmatched_count, self.ambiguous_count, self.unknown_count)
        ):
            raise ValueError("unanalysed HTTP coverage cannot claim observed request counts")
        if (self.analysis_status == "not_analyzed") != (self.path_analysis == "not_analyzed"):
            raise ValueError("HTTP path analysis status must agree with analysis availability")
        return self


class OverviewHttpAssociation(ContractModel):
    """A method/path shape match with evidence at both client and route declarations."""

    association_id: Identifier
    index_id: Identifier
    match_kind: Literal["method_path_shape"] = "method_path_shape"
    method: OverviewHttpMethod
    path: str = Field(min_length=1, max_length=2_000)
    client_path_kind: Literal["literal", "parameterized"] = "literal"
    client_path_template: str | None = Field(default=None, max_length=2_000)
    client_name: str = Field(min_length=1, max_length=500)
    client_module: Symbol
    server_module: Symbol
    route_entry_point_id: Identifier
    client_evidence: Evidence
    route_evidence: Evidence

    @model_validator(mode="after")
    def validate_association(self) -> OverviewHttpAssociation:
        if not self.path.startswith("/api/") or any(marker in self.path for marker in "?#"):
            raise ValueError("HTTP association paths must be static API paths without query or fragment")
        if self.client_path_kind == "literal":
            if self.client_path_template is not None:
                raise ValueError("literal HTTP clients cannot carry a path template")
        elif (
            self.client_path_template is None
            or not self.client_path_template.startswith("/api/")
            or any(marker in self.client_path_template for marker in "?#")
            or "{*}" not in self.client_path_template
            or any(
                ("{" in segment or "}" in segment) and segment != "{*}"
                for segment in self.client_path_template.split("/")
            )
        ):
            raise ValueError("parameterized HTTP clients require a whole-segment path template")
        if (
            self.client_module.index_id != self.index_id
            or self.server_module.index_id != self.index_id
            or self.client_module.kind != "module"
            or self.server_module.kind != "module"
        ):
            raise ValueError("HTTP association modules must belong to the same index")
        if (
            self.client_evidence.index_id != self.index_id
            or self.route_evidence.index_id != self.index_id
            or self.client_evidence.kind != "source"
            or self.route_evidence.kind != "source"
        ):
            raise ValueError("HTTP association evidence must belong to the same source snapshot")
        if self.client_evidence.path != self.client_module.path or self.route_evidence.path != self.server_module.path:
            raise ValueError("HTTP association evidence paths must match their endpoint modules")
        return self


class OverviewPath(ContractModel):
    """One bounded static path from an entry symbol to a reached symbol."""

    path_id: Identifier
    index_id: Identifier
    entry_point_id: Identifier
    symbol_ids: list[Identifier] = Field(min_length=1)
    edges: list[CodeEdge] = Field(default_factory=list)
    depth: NonNegativeInt

    @model_validator(mode="after")
    def validate_path_edges(self) -> OverviewPath:
        if self.depth != len(self.edges) or len(self.symbol_ids) != len(self.edges) + 1:
            raise ValueError("overview path depth must match its edge and symbol counts")
        for offset, edge in enumerate(self.edges):
            if edge.index_id != self.index_id:
                raise ValueError("overview path edge belongs to a different index_id")
            if edge.source_id != self.symbol_ids[offset] or edge.target_id != self.symbol_ids[offset + 1]:
                raise ValueError("overview path edges must form a consecutive symbol path")
        return self


class OverviewCoverage(ContractModel):
    """Counts that distinguish the visible bounded result from the snapshot."""

    total_modules: NonNegativeInt
    visible_modules: NonNegativeInt
    reachable_modules: NonNegativeInt
    unreachable_modules: NonNegativeInt
    entry_point_count: NonNegativeInt
    unresolved_count: NonNegativeInt
    total_groups: NonNegativeInt
    visible_groups: NonNegativeInt
    total_module_relations: NonNegativeInt
    visible_module_relations: NonNegativeInt

    @model_validator(mode="after")
    def validate_counts(self) -> OverviewCoverage:
        if self.visible_modules > self.total_modules:
            raise ValueError("visible overview modules cannot exceed total modules")
        if self.reachable_modules + self.unreachable_modules != self.total_modules:
            raise ValueError("overview module reachability counts must cover all modules")
        if self.visible_groups > self.total_groups:
            raise ValueError("visible overview groups cannot exceed total groups")
        if self.visible_module_relations > self.total_module_relations:
            raise ValueError("visible overview module relations cannot exceed total relations")
        return self


class JourneySummary(ContractModel):
    """A lightweight same-index action summary with an optional HTTP shape match."""

    journey_id: Identifier
    index_id: Identifier
    action_label: str = Field(min_length=1, max_length=300)
    label_candidates: list[str] = Field(default_factory=list, max_length=5)
    label_dynamic: bool = False
    event_name: Literal["onSubmit", "onClick"]
    action_path: RelativePath
    action_start_line: PositiveInt
    method: OverviewHttpMethod | None
    path: str | None = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_http_shape_pair(self) -> JourneySummary:
        if (self.method is None) != (self.path is None):
            raise ValueError("journey HTTP method and path must both be set or both be null")
        return self


class JourneyCoverage(ContractModel):
    """Bounded UI action extraction and route-matching coverage."""

    parser_status: Literal["ready", "unavailable", "invalid", "truncated"] = "ready"
    http_association_status: Literal["complete", "incomplete", "not_analyzed"] = "not_analyzed"
    scanned_file_count: NonNegativeInt = 0
    action_count: NonNegativeInt = 0
    journey_count: NonNegativeInt = 0
    unsupported_action_count: NonNegativeInt = 0
    action_facts_omitted_count: NonNegativeInt = 0
    prop_facts_omitted_count: NonNegativeInt = 0
    action_facts_truncated: bool = False
    prop_facts_truncated: bool = False
    relation_truncated: bool = False
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_counts(self) -> JourneyCoverage:
        if self.truncated != (self.parser_status == "truncated") and self.parser_status != "unavailable":
            raise ValueError("truncated journey coverage must report parser truncation")
        return self


class ProjectJourneyCatalog(VersionedModel):
    """Lightweight action journey summaries for one immutable project index."""

    project_id: Identifier
    index: IndexSnapshot
    rules_version: Identifier
    journeys: list[JourneySummary] = Field(default_factory=list, max_length=40)
    coverage: JourneyCoverage

    @model_validator(mode="after")
    def validate_catalog(self) -> ProjectJourneyCatalog:
        if any(item.index_id != self.index.index_id for item in self.journeys):
            raise ValueError("journey summary belongs to a different index_id")
        if len({item.journey_id for item in self.journeys}) != len(self.journeys):
            raise ValueError("journey summary IDs must be unique")
        if self.coverage.journey_count != len(self.journeys):
            raise ValueError("visible journey count must match journey summaries")
        return self


class JourneyStep(ContractModel):
    """One user-language responsibility step with explicitly typed evidence."""

    step_id: Identifier
    role: Literal["trigger", "client_processing", "request", "server_processing", "result"]
    title: str = Field(min_length=1, max_length=300)
    relation_kind: Literal["ui_action", "ui_callback", "calls", "http_shape", "result_display", "unresolved"]
    certainty: Literal["static", "shape", "unconfirmed"]
    explanation: str = Field(min_length=1, max_length=1_000)
    symbols: list[Symbol] = Field(default_factory=list, max_length=8)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)


class ProjectJourney(VersionedModel):
    """A bounded action-to-handler journey derived from one index snapshot."""

    project_id: Identifier
    index: IndexSnapshot
    rules_version: Identifier
    journey_id: Identifier
    summary: JourneySummary
    steps: list[JourneyStep] = Field(min_length=1, max_length=8)
    coverage: JourneyCoverage
    markers: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_journey(self) -> ProjectJourney:
        if self.summary.journey_id != self.journey_id or self.summary.index_id != self.index.index_id:
            raise ValueError("journey summary must match the response identity and snapshot")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("journey step IDs must be unique")
        for step in self.steps:
            if any(symbol.index_id != self.index.index_id for symbol in step.symbols):
                raise ValueError("journey symbols must belong to the same index")
            if any(evidence.index_id != self.index.index_id for evidence in step.evidence):
                raise ValueError("journey evidence must belong to the same snapshot")
        return self


class ProjectOverview(VersionedModel):
    """Deterministic, rule-only overview of one project index snapshot."""

    project_id: Identifier
    index: IndexSnapshot
    rules_version: Identifier
    entry_status: OverviewEntryStatus
    responsibility_status: Literal["not_configured", "ready", "partial", "invalid"] = "not_configured"
    responsibility_evidence: Evidence | None = None
    entries: list[OverviewEntryPoint] = Field(default_factory=list)
    groups: list[OverviewGroup] = Field(default_factory=list)
    modules: list[OverviewModule] = Field(default_factory=list)
    classifications: list[OverviewClassification] = Field(default_factory=list)
    classification_coverage: OverviewClassificationCoverage = Field(
        default_factory=lambda: OverviewClassificationCoverage(
            total=0,
            visible=0,
            category_counts=OverviewClassificationCounts(),
            visible_category_counts=OverviewClassificationCounts(),
            truncated=False,
        )
    )
    module_relations: list[OverviewModuleRelation] = Field(default_factory=list)
    http_associations: list[OverviewHttpAssociation] = Field(default_factory=list)
    http_association_coverage: OverviewHttpAssociationCoverage = Field(
        default_factory=lambda: OverviewHttpAssociationCoverage(
            analysis_status="not_analyzed",
            client_request_count=0,
            matched_count=0,
            visible_match_count=0,
            unmatched_count=0,
            ambiguous_count=0,
            unknown_count=0,
        )
    )
    paths: list[OverviewPath] = Field(default_factory=list)
    unresolved: list[UnresolvedReference] = Field(default_factory=list)
    coverage: OverviewCoverage
    notes: list[str] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_overview_references(self) -> ProjectOverview:
        if self.entry_status == "not_identified" and self.entries:
            raise ValueError("an overview without identified entries cannot contain entry points")
        if self.entry_status != "not_identified" and not self.entries:
            raise ValueError("an identified overview must contain entry points")
        if len({entry.entry_point_id for entry in self.entries}) != len(self.entries):
            raise ValueError("overview entry IDs must be unique")
        if len({group.group_id for group in self.groups}) != len(self.groups):
            raise ValueError("overview group IDs must be unique")
        if len({module.module_id for module in self.modules}) != len(self.modules):
            raise ValueError("overview module IDs must be unique")
        if len({item.classification_id for item in self.classifications}) != len(self.classifications):
            raise ValueError("overview classification IDs must be unique")
        if len({relation.relation_id for relation in self.module_relations}) != len(self.module_relations):
            raise ValueError("overview module relation IDs must be unique")
        if len({path.path_id for path in self.paths}) != len(self.paths):
            raise ValueError("overview path IDs must be unique")
        entry_ids = {entry.entry_point_id for entry in self.entries}
        has_responsibility_groups = any(group.group_kind == "responsibility" for group in self.groups)
        if self.responsibility_status == "not_configured":
            if self.responsibility_evidence is not None or has_responsibility_groups:
                raise ValueError("unconfigured responsibility overview cannot include manifest data")
        elif self.responsibility_status in {"ready", "partial"}:
            if not has_responsibility_groups:
                raise ValueError("valid responsibility overview requires responsibility groups")
        elif has_responsibility_groups:
            raise ValueError("invalid responsibility metadata cannot produce responsibility groups")
        if (
            self.responsibility_evidence is not None
            and (
                self.responsibility_evidence.index_id != self.index.index_id
                or self.responsibility_evidence.path != "codetrail-subsystems.json"
            )
        ):
            raise ValueError("responsibility evidence must identify the same-index manifest")
        for entry in self.entries:
            if entry.index_id != self.index.index_id:
                raise ValueError("overview entry belongs to a different index_id")
        for group in self.groups:
            if group.index_id != self.index.index_id:
                raise ValueError("overview group belongs to a different index_id")
            if not set(group.entry_point_ids).issubset(entry_ids):
                raise ValueError("overview group references an unknown entry point")
        for module in self.modules:
            if module.index_id != self.index.index_id:
                raise ValueError("overview module belongs to a different index_id")
            if not set(module.entry_point_ids).issubset(entry_ids):
                raise ValueError("overview module references an unknown entry point")
        entries_by_id = {entry.entry_point_id: entry for entry in self.entries}
        modules_by_id = {module.module_id: module for module in self.modules}
        visible_classifications_by_module: dict[str, dict[str, int]] = {}
        for item in self.classifications:
            if item.index_id != self.index.index_id:
                raise ValueError("overview classification belongs to a different index_id")
            module = modules_by_id.get(item.module_id)
            if module is None:
                raise ValueError("overview classification references a module outside the visible overview")
            counts = visible_classifications_by_module.setdefault(
                item.module_id,
                {"application_entry": 0, "public_api": 0, "extension_point": 0, "unknown": 0},
            )
            counts[item.category] += 1
            if any(evidence.path != module.symbol.path for evidence in item.evidence):
                raise ValueError("overview classification evidence must identify its module source")
            if item.entry_point_id is not None:
                entry = entries_by_id.get(item.entry_point_id)
                if entry is None or item.category != "application_entry":
                    raise ValueError("application classification references an unknown entry")
        if self.classification_coverage.visible != len(self.classifications):
            raise ValueError("visible architecture classification count must match returned items")
        for module in self.modules:
            visible_counts = visible_classifications_by_module.get(module.module_id, {})
            if any(
                visible_counts.get(name, 0) > getattr(module.classification_counts, name)
                for name in ("application_entry", "public_api", "extension_point", "unknown")
            ):
                raise ValueError("visible module classifications exceed module totals")
        for relation in self.module_relations:
            if relation.index_id != self.index.index_id:
                raise ValueError("overview module relation belongs to a different index_id")
        for association in self.http_associations:
            if association.index_id != self.index.index_id:
                raise ValueError("HTTP association belongs to a different index_id")
            if association.route_entry_point_id not in entry_ids:
                raise ValueError("HTTP association references an unknown FastAPI route")
            route = next(entry for entry in self.entries if entry.entry_point_id == association.route_entry_point_id)
            if route.kind != "fastapi_route" or route.http_method != association.method or route.declared_path != association.path:
                raise ValueError("HTTP association must match its route declaration")
        if self.http_association_coverage.visible_match_count != len(self.http_associations):
            raise ValueError("visible HTTP association count must match returned associations")
        for path in self.paths:
            if path.index_id != self.index.index_id or path.entry_point_id not in entry_ids:
                raise ValueError("overview path has an invalid snapshot or entry reference")
        for unresolved in self.unresolved:
            if unresolved.index_id != self.index.index_id:
                raise ValueError("overview unresolved reference belongs to a different index_id")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated overviews must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated overviews cannot have a truncation reason")
        if self.entry_status == "partial" and not self.truncated:
            raise ValueError("partial overviews must be truncated")
        if self.entry_status == "not_identified" and self.coverage.entry_point_count != 0:
            raise ValueError("unidentified overviews cannot report entry points")
        return self


FeatureGenerationMode: TypeAlias = Literal["fixture", "real"]
FeatureSourceScope: TypeAlias = Literal["entry_reachable", "visible_modules"]
FeatureGenerationSource: TypeAlias = Literal["server", "external"]
FeatureGenerationSourceScope: TypeAlias = Literal["entry_reachable", "visible_modules", "external_run"]
FeatureGenerationStatus: TypeAlias = Literal[
    "succeeded",
    "partial",
    "insufficient_evidence",
    "failed",
]
FeatureGenerationErrorKind: TypeAlias = Literal[
    "budget_exceeded",
    "retrieval_failed",
    "model_unavailable",
    "model_failed",
    "invalid_model_output",
    "internal_error",
]
FeatureAssociationRelation: TypeAlias = Literal[
    "triggered_by",
    "implemented_by",
    "related_to",
]
FeatureAssociationTargetKind: TypeAlias = Literal["entry_point", "module", "symbol"]


class FeatureGenerationBudget(ContractModel):
    """Server-enforced limits for one explicit feature generation."""

    max_tool_calls: PositiveInt = Field(default=8, le=12)
    max_model_calls: PositiveInt = Field(default=1, le=2)
    max_seconds: PositiveInt = Field(default=20, le=120)
    max_source_lines: PositiveInt = Field(default=120, le=200)
    max_total_source_lines: PositiveInt = Field(default=800, le=2_000)
    max_input_chars: PositiveInt = Field(default=60_000, le=100_000)
    max_features: PositiveInt = Field(default=8, le=20)
    max_output_chars: PositiveInt = Field(default=20_000, le=50_000)

    @model_validator(mode="after")
    def validate_generation_budget(self) -> FeatureGenerationBudget:
        if self.max_total_source_lines < self.max_source_lines:
            raise ValueError("total source line budget must cover one source read")
        return self


class FeatureGenerationRequest(VersionedModel):
    """Explicit request for one bounded semantic feature generation."""

    mode: FeatureGenerationMode
    model: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_version: Identifier = "feature-summary-v2"
    source_scope: FeatureSourceScope = "entry_reachable"
    budget: FeatureGenerationBudget = Field(default_factory=FeatureGenerationBudget)
    idempotency_key: Identifier | None = None


class Feature(ContractModel):
    """A semantic feature supported by observed snapshot symbols and evidence."""

    feature_id: Identifier
    index_id: Identifier
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5_000)
    entry_point_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    module_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    symbol_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_feature_lists(self) -> Feature:
        for values, label in (
            (self.entry_point_ids, "entry point"),
            (self.module_ids, "module"),
            (self.symbol_ids, "symbol"),
            (self.evidence_ids, "evidence"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"feature {label} IDs must be unique")
        return self


class FeatureAssociation(ContractModel):
    """A semantic feature mapping kept separate from static CodeEdge data."""

    association_id: Identifier
    index_id: Identifier
    feature_id: Identifier
    relation: FeatureAssociationRelation
    target_kind: FeatureAssociationTargetKind
    target_id: Identifier
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_association_evidence(self) -> FeatureAssociation:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("feature association evidence IDs must be unique")
        return self


class FeatureModelOutput(VersionedModel):
    """Untrusted structured adapter output before context reference validation."""

    output_id: Identifier
    index_id: Identifier
    outcome: Literal["features_found", "insufficient_evidence"]
    summary: str = Field(min_length=1, max_length=5_000)
    features: list[Feature] = Field(default_factory=list, max_length=20)
    associations: list[FeatureAssociation] = Field(default_factory=list, max_length=200)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    observed_symbol_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_model_output(self) -> FeatureModelOutput:
        if self.outcome == "features_found" and not self.features:
            raise ValueError("features_found output must include at least one feature")
        if self.outcome == "insufficient_evidence" and self.features:
            raise ValueError("insufficient_evidence output cannot include features")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("model output evidence IDs must be unique")
        if len(set(self.observed_symbol_ids)) != len(self.observed_symbol_ids):
            raise ValueError("model output symbol IDs must be unique")
        feature_ids = {feature.feature_id for feature in self.features}
        if len(feature_ids) != len(self.features):
            raise ValueError("model output feature IDs must be unique")
        if any(not feature.evidence_ids for feature in self.features):
            raise ValueError("every model output feature must cite evidence")
        association_ids = {item.association_id for item in self.associations}
        if len(association_ids) != len(self.associations):
            raise ValueError("model output association IDs must be unique")
        if not {item.feature_id for item in self.associations}.issubset(feature_ids):
            raise ValueError("feature association references an unknown feature")
        return self


class FeatureGenerationError(ContractModel):
    kind: FeatureGenerationErrorKind
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class FeatureGenerationUsage(ContractModel):
    """Auditable bounded usage for retrieval and every model response."""

    tool_calls: NonNegativeInt = 0
    model_calls: NonNegativeInt = 0
    repair_calls: NonNegativeInt = 0
    source_lines: NonNegativeInt = 0
    input_chars: NonNegativeInt = 0
    output_chars: NonNegativeInt = 0
    model_input_chars: list[NonNegativeInt] = Field(default_factory=list, max_length=2)
    model_output_chars: list[NonNegativeInt] = Field(default_factory=list, max_length=2)
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    elapsed_ms: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_repairs(self) -> FeatureGenerationUsage:
        if self.repair_calls > self.model_calls:
            raise ValueError("repair calls cannot exceed model calls")
        if len(self.model_input_chars) != self.model_calls:
            raise ValueError("model input usage must record every attempted model call")
        if len(self.model_output_chars) > self.model_calls:
            raise ValueError("model output usage cannot exceed model calls")
        if sum(self.model_input_chars) != self.input_chars:
            raise ValueError("cumulative input chars must match per-call model input usage")
        if sum(self.model_output_chars) != self.output_chars:
            raise ValueError("cumulative output chars must match per-call model output usage")
        return self


class FeatureGeneration(VersionedModel):
    """Persisted semantic feature result for one immutable project snapshot."""

    generation_id: Identifier
    project_id: Identifier
    index: IndexSnapshot
    mode: FeatureGenerationMode
    source: FeatureGenerationSource = "server"
    external_run_id: Identifier | None = None
    external_session_id: Identifier | None = None
    model: str | None = Field(default=None, max_length=200)
    prompt_version: Identifier
    rules_version: Identifier
    source_scope: FeatureGenerationSourceScope
    status: FeatureGenerationStatus
    summary: str = Field(min_length=1, max_length=5_000)
    features: list[Feature] = Field(default_factory=list, max_length=20)
    associations: list[FeatureAssociation] = Field(default_factory=list, max_length=200)
    evidence: list[Evidence] = Field(default_factory=list, max_length=200)
    observed_entry_point_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    observed_module_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    observed_symbol_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    source_paths: list[RelativePath] = Field(default_factory=list, max_length=200)
    omitted_module_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    usage: FeatureGenerationUsage
    budget: FeatureGenerationBudget
    cache_key: Sha256
    created_at: datetime
    error: FeatureGenerationError | None = None
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    _normalize_paths = field_validator("source_paths")(
        lambda paths: [_validate_relative_path(path) for path in paths]
    )

    @model_validator(mode="after")
    def validate_generation_result(self) -> FeatureGeneration:
        if self.index.status != "ready":
            raise ValueError("feature generations must use a ready index")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("feature generation evidence IDs must be unique")
        for item in self.evidence:
            if item.index_id != self.index.index_id:
                raise ValueError("feature generation evidence must use the result index")
        feature_ids = {item.feature_id for item in self.features}
        if len(feature_ids) != len(self.features):
            raise ValueError("feature generation feature IDs must be unique")
        for feature in self.features:
            if feature.index_id != self.index.index_id:
                raise ValueError("feature must use the generation index")
            if not feature.evidence_ids:
                raise ValueError("every persisted feature must cite evidence")
            if not set(feature.evidence_ids).issubset(evidence_ids):
                raise ValueError("feature references evidence absent from the generation")
        association_ids = {item.association_id for item in self.associations}
        if len(association_ids) != len(self.associations):
            raise ValueError("feature generation association IDs must be unique")
        for association in self.associations:
            if association.index_id != self.index.index_id:
                raise ValueError("feature association must use the generation index")
            if association.feature_id not in feature_ids:
                raise ValueError("feature association references an unknown feature")
            if not set(association.evidence_ids).issubset(evidence_ids):
                raise ValueError("feature association references evidence absent from the generation")
        if len(set(self.observed_entry_point_ids)) != len(self.observed_entry_point_ids):
            raise ValueError("observed entry point IDs must be unique")
        if len(set(self.observed_module_ids)) != len(self.observed_module_ids):
            raise ValueError("observed module IDs must be unique")
        if len(set(self.observed_symbol_ids)) != len(self.observed_symbol_ids):
            raise ValueError("observed symbol IDs must be unique")
        if len(set(self.omitted_module_ids)) != len(self.omitted_module_ids):
            raise ValueError("omitted module IDs must be unique")
        if self.status == "failed":
            if self.error is None:
                raise ValueError("failed feature generations must include an error")
            if self.features or self.associations:
                raise ValueError("failed feature generations cannot expose successful features")
        elif self.error is not None:
            raise ValueError("successful feature generations cannot include an error")
        if self.status == "insufficient_evidence" and self.features:
            raise ValueError("insufficient evidence generations cannot include features")
        if self.status == "succeeded" and not self.features:
            raise ValueError("succeeded feature generations must include features")
        if self.status == "partial" and not self.features:
            raise ValueError("partial feature generations must include partial features")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated feature generations must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated feature generations cannot have a truncation reason")
        if self.status == "partial" and not self.truncated:
            raise ValueError("partial feature generations must be truncated")
        if self.usage.tool_calls > self.budget.max_tool_calls:
            raise ValueError("feature generation tool usage exceeds its budget")
        if self.usage.model_calls > self.budget.max_model_calls:
            raise ValueError("feature generation model usage exceeds its budget")
        if self.usage.source_lines > self.budget.max_total_source_lines:
            raise ValueError("feature generation source usage exceeds its budget")
        if self.usage.input_chars > self.budget.max_input_chars:
            raise ValueError("feature generation input exceeds its budget")
        if self.usage.output_chars > self.budget.max_output_chars:
            raise ValueError("feature generation output exceeds its budget")
        if self.source_scope == "external_run" and self.source != "external":
            raise ValueError("external Run feature scope requires an external source")
        if (self.external_run_id is None) != (self.external_session_id is None):
            raise ValueError("external feature Run provenance must be complete")
        if self.source == "server" and self.external_run_id is not None:
            raise ValueError("server feature generations cannot claim external Run provenance")
        if self.source == "external" and (
            self.mode != "real"
            or self.source_scope != "external_run"
            or self.usage.model_calls != 0
            or self.usage.repair_calls != 0
            or self.usage.model_input_chars
            or self.usage.model_output_chars
            or self.usage.input_tokens is not None
            or self.usage.output_tokens is not None
        ):
            raise ValueError("external feature results cannot claim server model usage")
        return self


class FeatureGenerationResponse(VersionedModel):
    generation: FeatureGeneration


class FeatureGenerationSummary(VersionedModel):
    """Bounded metadata for one saved feature generation in a history page."""

    generation_id: Identifier
    project_id: Identifier
    index: IndexSnapshot
    mode: FeatureGenerationMode
    source: FeatureGenerationSource = "server"
    external_run_id: Identifier | None = None
    external_session_id: Identifier | None = None
    model: str | None = Field(default=None, max_length=200)
    prompt_version: Identifier
    rules_version: Identifier
    source_scope: FeatureGenerationSourceScope
    status: FeatureGenerationStatus
    summary: str = Field(min_length=1, max_length=5_000)
    feature_count: NonNegativeInt
    evidence_count: NonNegativeInt
    usage: FeatureGenerationUsage
    created_at: datetime
    error: FeatureGenerationError | None = None
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_summary_index(self) -> FeatureGenerationSummary:
        if self.index.status != "ready":
            raise ValueError("feature generation summaries must use a ready index")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated feature generation summaries must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated feature generation summaries cannot have a truncation reason")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed feature generation summaries must include an error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("successful feature generation summaries cannot include an error")
        if self.source_scope == "external_run" and self.source != "external":
            raise ValueError("external Run feature summary scope requires an external source")
        if (self.external_run_id is None) != (self.external_session_id is None):
            raise ValueError("external feature summary Run provenance must be complete")
        if self.source == "server" and self.external_run_id is not None:
            raise ValueError("server feature summaries cannot claim external Run provenance")
        if self.source == "external" and (
            self.mode != "real"
            or self.source_scope != "external_run"
            or self.usage.model_calls != 0
            or self.usage.repair_calls != 0
            or self.usage.model_input_chars
            or self.usage.model_output_chars
            or self.usage.input_tokens is not None
            or self.usage.output_tokens is not None
        ):
            raise ValueError("external feature summaries cannot claim server model usage")
        return self


class FeatureGenerationListResponse(VersionedModel):
    """Stable, read-only history for one project or one selected index."""

    project_id: Identifier
    index_id: Identifier | None = None
    generations: list[FeatureGenerationSummary] = Field(default_factory=list, max_length=40)
    next_cursor: str | None = Field(default=None, max_length=500)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_history_scope(self) -> FeatureGenerationListResponse:
        for generation in self.generations:
            if generation.project_id != self.project_id:
                raise ValueError("feature generation history crossed the project boundary")
            if self.index_id is not None and generation.index.index_id != self.index_id:
                raise ValueError("feature generation history crossed the index boundary")
        if self.has_more and not self.next_cursor:
            raise ValueError("paginated feature history must include a next cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("complete feature history cannot include a next cursor")
        return self


FeatureGraphNodeKind: TypeAlias = Literal["entry_point", "feature", "module", "symbol"]
FeatureGraphOrigin: TypeAlias = Literal["semantic", "static"]
FeatureGraphRelation: TypeAlias = Literal[
    "triggered_by",
    "implemented_by",
    "related_to",
    "contains",
    "imports",
    "calls",
]


class FeatureGraphNode(ContractModel):
    """One bounded node in a saved feature-to-code graph."""

    node_id: Identifier
    index_id: Identifier
    kind: FeatureGraphNodeKind
    label: str = Field(min_length=1, max_length=500)
    detail: str | None = Field(default=None, max_length=5_000)
    feature_id: Identifier | None = None
    entry_point_id: Identifier | None = None
    symbol: Symbol | None = None
    path: RelativePath | None = None
    start_line: PositiveInt | None = None
    end_line: PositiveInt | None = None
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)

    _normalize_path = field_validator("path")(_validate_relative_path)

    @model_validator(mode="after")
    def validate_node_identity(self) -> FeatureGraphNode:
        if self.kind == "feature":
            if self.feature_id is None or self.node_id != f"feature:{self.feature_id}":
                raise ValueError("feature graph feature nodes must use their stable feature ID")
            if self.entry_point_id is not None or self.symbol is not None:
                raise ValueError("feature graph feature nodes cannot carry symbol or entry identity")
            if self.path is not None or self.start_line is not None or self.end_line is not None:
                raise ValueError("feature graph feature nodes cannot carry source coordinates")
        elif self.kind == "entry_point":
            if self.entry_point_id is None or self.node_id != f"entry:{self.entry_point_id}":
                raise ValueError("feature graph entry nodes must use their stable entry ID")
            if self.path is None or self.start_line is None or self.end_line is None:
                raise ValueError("feature graph entry nodes require source coordinates")
            if self.end_line < self.start_line or self.symbol is not None or self.feature_id is not None:
                raise ValueError("feature graph entry node identity is invalid")
        else:
            if self.symbol is None or self.node_id != self.symbol.symbol_id:
                raise ValueError("feature graph code nodes must use their stable symbol ID")
            if self.symbol.index_id != self.index_id:
                raise ValueError("feature graph code node belongs to a different index_id")
            if self.kind == "module" and self.symbol.kind != "module":
                raise ValueError("feature graph module nodes must reference module symbols")
            if self.kind == "symbol" and self.symbol.kind == "module":
                raise ValueError("feature graph module symbols must use the module node kind")
            if self.feature_id is not None or self.entry_point_id is not None:
                raise ValueError("feature graph code nodes cannot carry feature or entry identity")
            if self.path is not None or self.start_line is not None or self.end_line is not None:
                raise ValueError("feature graph code nodes use their embedded symbol coordinates")
        return self


class FeatureGraphEdge(ContractModel):
    """A semantic association or an existing static CodeTrail edge."""

    edge_id: Identifier
    index_id: Identifier
    source_id: Identifier
    target_id: Identifier
    origin: FeatureGraphOrigin
    relation: FeatureGraphRelation
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_edge_origin(self) -> FeatureGraphEdge:
        semantic = {"triggered_by", "implemented_by", "related_to"}
        static = {"contains", "imports", "calls"}
        allowed = semantic if self.origin == "semantic" else static
        if self.relation not in allowed:
            raise ValueError("feature graph edge relation does not match its origin")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("feature graph edge evidence IDs must be unique")
        return self


class FeatureGraphSnapshot(VersionedModel):
    """Saved feature mappings overlaid with bounded static relations from one index."""

    project_id: Identifier
    index_id: Identifier
    generation_id: Identifier
    status: FeatureGenerationStatus
    nodes: list[FeatureGraphNode] = Field(default_factory=list, max_length=40)
    edges: list[FeatureGraphEdge] = Field(default_factory=list, max_length=120)
    evidence: list[Evidence] = Field(default_factory=list, max_length=200)
    unavailable_target_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_graph_references(self) -> FeatureGraphSnapshot:
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("feature graph node IDs must be unique")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("feature graph evidence IDs must be unique")
        for item in self.evidence:
            if item.index_id != self.index_id:
                raise ValueError("feature graph evidence must use the graph index")
        for node in self.nodes:
            if node.index_id != self.index_id or not set(node.evidence_ids).issubset(evidence_ids):
                raise ValueError("feature graph node references an invalid evidence item")
        edge_ids = {edge.edge_id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("feature graph edge IDs must be unique")
        for edge in self.edges:
            if (
                edge.index_id != self.index_id
                or edge.source_id not in node_ids
                or edge.target_id not in node_ids
                or not set(edge.evidence_ids).issubset(evidence_ids)
            ):
                raise ValueError("feature graph edge references an invalid node or evidence item")
        if len(set(self.unavailable_target_ids)) != len(self.unavailable_target_ids):
            raise ValueError("feature graph unavailable target IDs must be unique")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated feature graphs must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated feature graphs cannot have a truncation reason")
        return self


class FeatureGraphResponse(VersionedModel):
    graph: FeatureGraphSnapshot


ApiErrorCode: TypeAlias = Literal[
    "not_found",
    "invalid_input",
    "conflict",
    "budget_exceeded",
    "download_timeout",
    "index_timeout",
    "index_cancelled",
    "internal_error",
    "forbidden",
]


class ApiErrorResponse(VersionedModel):
    code: ApiErrorCode
    message: str = Field(min_length=1, max_length=2_000)


class SaveModelConfigRequest(VersionedModel):
    provider: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    base_url: str = Field(
        min_length=1,
        max_length=2_000,
        json_schema_extra={"format": "uri", "pattern": r"^https?://", "description": "Absolute HTTP(S) URL without userinfo credentials."},
    )
    model: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    api_key: str | None = Field(default=None, max_length=500)
    remove_api_key: bool = False

    @field_validator("provider", "model")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return normalize_model_config_text(value)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return normalize_model_config_base_url(value)

    @model_validator(mode="after")
    def reject_key_and_removal_together(self) -> SaveModelConfigRequest:
        if self.remove_api_key and self.api_key:
            raise ValueError("api_key cannot be replaced and removed in the same request")
        return self


class ModelConfigSettingsResponse(VersionedModel):
    provider: str
    base_url: str
    model: str
    has_api_key: bool


McpErrorCode: TypeAlias = Literal[
    "not_found",
    "invalid_input",
    "conflict",
    "budget_exceeded",
    "download_timeout",
    "index_timeout",
    "index_cancelled",
    "internal_error",
    "forbidden",
]


class McpError(VersionedModel):
    """Domain failure returned inside a read-only MCP tool result."""

    code: McpErrorCode
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class McpResultBase(VersionedModel):
    """Common structured MCP result envelope."""

    ok: bool
    error: McpError | None = None

    @model_validator(mode="after")
    def validate_result_error(self) -> McpResultBase:
        if self.ok and self.error is not None:
            raise ValueError("successful MCP results cannot include an error")
        if not self.ok and self.error is None:
            raise ValueError("failed MCP results must include an error")
        return self


class McpProjectListResponse(McpResultBase):
    projects: list[Project] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> McpProjectListResponse:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated MCP project lists must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated MCP project lists cannot have a truncation reason")
        return self


class McpProjectResponse(McpResultBase):
    project: Project | None = None


class McpProjectIndexesResponse(McpResultBase):
    project_id: Identifier
    indexes: list[IndexSnapshot] = Field(default_factory=list)
    latest_index_id: Identifier | None = None
    building_index_id: Identifier | None = None
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> McpProjectIndexesResponse:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated MCP index lists must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated MCP index lists cannot have a truncation reason")
        return self


class McpIndexResponse(McpResultBase):
    index: IndexResponse | None = None


class McpIndexFreshnessResponse(McpResultBase):
    result: IndexFreshness | None = None

    @model_validator(mode="after")
    def validate_freshness_result(self) -> McpIndexFreshnessResponse:
        if self.ok and self.result is None:
            raise ValueError("successful freshness results require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed freshness results cannot include freshness data")
        return self


class McpIndexRefreshResponse(McpResultBase):
    result: IndexRefreshResponse | None = None

    @model_validator(mode="after")
    def validate_refresh_result(self) -> McpIndexRefreshResponse:
        if self.ok and self.result is None:
            raise ValueError("successful refresh results require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed refresh results cannot include refresh data")
        return self


class McpSearchResponse(McpResultBase):
    result: SearchIndexResponse | None = None


class McpModuleSymbolsResponse(McpResultBase):
    result: ModuleSymbolsPageResponse | None = None


class McpSourceResponse(McpResultBase):
    result: SourceIndexResponse | None = None
    reference: EvidenceReference | None = None

    @model_validator(mode="after")
    def validate_source_reference(self) -> McpSourceResponse:
        if self.ok:
            if self.result is None or self.reference is None:
                raise ValueError("successful source results require evidence and a reference")
            evidence = self.result.evidence
            if (
                evidence.index_id != self.reference.index_id
                or evidence.path != self.reference.path
                or evidence.start_line != self.reference.start_line
                or evidence.end_line != self.reference.end_line
                or evidence.content_hash != self.reference.content_hash
                or evidence.evidence_id != self.reference.evidence_id
            ):
                raise ValueError("source reference must match returned evidence")
        elif self.result is not None or self.reference is not None:
            raise ValueError("failed source results cannot include evidence or a reference")
        return self


class McpNeighborsResponse(McpResultBase):
    result: NeighborsIndexResponse | None = None


class McpOverviewResponse(McpResultBase):
    """Read-only MCP response for one deterministic project overview."""

    overview: ProjectOverview | None = None
    references: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_overview_result(self) -> McpOverviewResponse:
        if self.ok:
            if self.overview is None:
                raise ValueError("successful overview results require an overview")
            evidence_ids = {
                entry.evidence.evidence_id
                for entry in self.overview.entries
                if entry.evidence is not None
            }
            evidence_ids.update(
                evidence.evidence_id
                for item in self.overview.classifications
                for evidence in item.evidence
            )
            if self.overview.responsibility_evidence is not None:
                evidence_ids.add(self.overview.responsibility_evidence.evidence_id)
            for reference in self.references:
                if reference.index_id != self.overview.index.index_id or reference.evidence_id not in evidence_ids:
                    raise ValueError("overview references must match returned evidence")
        elif self.overview is not None or self.references:
            raise ValueError("failed overview results cannot include overview data")
        return self


class McpJourneyCatalogResponse(McpResultBase):
    """Read-only MCP envelope for lightweight action journey summaries."""

    catalog: ProjectJourneyCatalog | None = None

    @model_validator(mode="after")
    def validate_catalog_result(self) -> McpJourneyCatalogResponse:
        if self.ok and self.catalog is None:
            raise ValueError("successful journey catalogs require a catalog")
        if not self.ok and self.catalog is not None:
            raise ValueError("failed journey catalogs cannot include a catalog")
        return self


class McpJourneyResponse(McpResultBase):
    """Read-only MCP envelope for one action journey with source citations."""

    journey: ProjectJourney | None = None
    references: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_journey_result(self) -> McpJourneyResponse:
        if self.ok:
            if self.journey is None:
                raise ValueError("successful journey results require a journey")
            evidence_ids = {
                evidence.evidence_id
                for step in self.journey.steps
                for evidence in step.evidence
            }
            if any(
                reference.index_id != self.journey.index.index_id
                or reference.evidence_id not in evidence_ids
                for reference in self.references
            ):
                raise ValueError("journey references must match returned source evidence")
        elif self.journey is not None or self.references:
            raise ValueError("failed journeys cannot include journey data")
        return self


class McpFeatureGenerationResponse(McpResultBase):
    """Structured MCP response for a saved or externally submitted feature generation."""

    generation: FeatureGeneration | None = None
    references: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_generation_result(self) -> McpFeatureGenerationResponse:
        if self.ok and self.generation is None:
            raise ValueError("successful feature generation results require a generation")
        if self.ok and self.generation is not None:
            evidence_ids = {item.evidence_id for item in self.generation.evidence}
            for reference in self.references:
                if reference.index_id != self.generation.index.index_id or reference.evidence_id not in evidence_ids:
                    raise ValueError("feature generation references must match returned evidence")
        if not self.ok and (self.generation is not None or self.references):
            raise ValueError("failed feature generation results cannot include generation data")
        return self


class McpFeatureGenerationListResponse(McpResultBase):
    """Read-only MCP envelope for project or index feature history."""

    result: FeatureGenerationListResponse | None = None

    @model_validator(mode="after")
    def validate_history_result(self) -> McpFeatureGenerationListResponse:
        if self.ok and self.result is None:
            raise ValueError("successful feature history results require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed feature history results cannot include a result")
        return self


class McpFeatureGraphResponse(McpResultBase):
    """Read-only MCP envelope for one saved feature graph."""

    graph: FeatureGraphSnapshot | None = None
    references: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph_result(self) -> McpFeatureGraphResponse:
        if self.ok and self.graph is None:
            raise ValueError("successful feature graph results require a graph")
        if self.ok and self.graph is not None:
            evidence_ids = {item.evidence_id for item in self.graph.evidence}
            if any(reference.evidence_id not in evidence_ids for reference in self.references):
                raise ValueError("feature graph references must match returned evidence")
            if any(reference.index_id != self.graph.index_id for reference in self.references):
                raise ValueError("feature graph references must match the graph index")
        if not self.ok and (self.graph is not None or self.references):
            raise ValueError("failed feature graph results cannot include graph data")
        return self


class McpSessionListResponse(McpResultBase):
    """Read-only MCP envelope for one project's session navigation."""

    result: SessionListResponse | None = None
    references: list[SessionReference] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_session_list_result(self) -> McpSessionListResponse:
        if self.ok and self.result is None:
            raise ValueError("successful session lists require a result")
        if self.ok and self.result is not None:
            session_ids = {session.session_id for session in self.result.sessions}
            if any(
                reference.project_id != self.result.project_id
                or reference.session_id not in session_ids
                for reference in self.references
            ):
                raise ValueError("session references must match the returned project sessions")
        if not self.ok and (self.result is not None or self.references):
            raise ValueError("failed session lists cannot include session data")
        return self


class McpSessionResponse(McpResultBase):
    """MCP envelope for one session read or explicitly enabled creation."""

    session: Session | None = None
    reference: SessionReference | None = None

    @model_validator(mode="after")
    def validate_session_result(self) -> McpSessionResponse:
        if self.ok:
            if self.session is None or self.reference is None:
                raise ValueError("successful session results require a session and reference")
            if (
                self.reference.project_id != self.session.project_id
                or self.reference.session_id != self.session.session_id
            ):
                raise ValueError("session reference must match the returned session")
        elif self.session is not None or self.reference is not None:
            raise ValueError("failed session results cannot include session data")
        return self


class McpSessionMessagesResponse(McpResultBase):
    """Read-only MCP envelope for one bounded session-message page."""

    result: SessionMessagesResponse | None = None
    reference: SessionReference | None = None

    @model_validator(mode="after")
    def validate_session_messages_result(self) -> McpSessionMessagesResponse:
        if self.ok:
            if self.result is None or self.reference is None:
                raise ValueError("successful session message results require a result and reference")
            if (
                self.reference.project_id != self.result.project_id
                or self.reference.session_id != self.result.session_id
            ):
                raise ValueError("session message reference must match the returned session")
        elif self.result is not None or self.reference is not None:
            raise ValueError("failed session message results cannot include message data")
        return self


class McpSessionMessageResponse(McpResultBase):
    """MCP envelope for one explicitly appended session message."""

    message: SessionMessage | None = None
    reference: SessionReference | None = None

    @model_validator(mode="after")
    def validate_session_message_result(self) -> McpSessionMessageResponse:
        if self.ok:
            if self.message is None or self.reference is None:
                raise ValueError("successful session message results require a message and reference")
            if self.reference.session_id != self.message.session_id:
                raise ValueError("session message reference must match the returned message")
        elif self.message is not None or self.reference is not None:
            raise ValueError("failed session message results cannot include message data")
        return self


class McpRunListResponse(McpResultBase):
    """Read-only MCP envelope for one Session's Run history."""

    result: RunListResponse | None = None

    @model_validator(mode="after")
    def validate_run_list_result(self) -> McpRunListResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run lists require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run lists cannot include Run data")
        return self


class McpRunResponse(McpResultBase):
    """MCP envelope for one Run status and its frozen input."""

    result: RunStatusResponse | None = None

    @model_validator(mode="after")
    def validate_run_result(self) -> McpRunResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run reads require a result")
        if self.ok and self.result is not None:
            if (
                self.result.run.project_id != self.result.input.project_id
                or self.result.run.session_id != self.result.input.session_id
                or self.result.run.index_id != self.result.input.index_id
            ):
                raise ValueError("Run status and frozen input must share their scope")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run reads cannot include Run data")
        return self


class McpRunEventsResponse(McpResultBase):
    """Read-only MCP envelope for bounded Run events."""

    result: RunEventsResponse | None = None

    @model_validator(mode="after")
    def validate_run_events_result(self) -> McpRunEventsResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run event reads require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run event reads cannot include events")
        return self


class McpRunToolResultsResponse(McpResultBase):
    """Read-only MCP envelope for bounded persisted tool results."""

    result: RunToolResultsResponse | None = None

    @model_validator(mode="after")
    def validate_run_tool_results_result(self) -> McpRunToolResultsResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run tool-result reads require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run tool-result reads cannot include results")
        return self


class McpRunReportResponse(McpResultBase):
    """Read-only MCP envelope for a completed Run report."""

    report: Report | None = None

    @model_validator(mode="after")
    def validate_run_report_result(self) -> McpRunReportResponse:
        if self.ok and self.report is None:
            raise ValueError("successful Run report reads require a report")
        if self.ok and self.report is not None:
            if self.report.run_id == "":
                raise ValueError("Run report must identify its Run")
        if not self.ok and self.report is not None:
            raise ValueError("failed Run report reads cannot include a report")
        return self


class McpRunImpactResponse(McpResultBase):
    """Read-only MCP envelope for a bounded Run candidate impact query."""

    result: RunImpactResponse | None = None

    @model_validator(mode="after")
    def validate_run_impact_result(self) -> McpRunImpactResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run impact reads require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run impact reads cannot include a result")
        return self


class McpRunStartResponse(McpResultBase):
    """MCP envelope for the explicitly enabled fixture Run start."""

    result: CreateRunResponse | None = None

    @model_validator(mode="after")
    def validate_run_start_result(self) -> McpRunStartResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run starts require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run starts cannot include Run data")
        return self


class McpRunCancelResponse(McpResultBase):
    """MCP envelope for the explicitly enabled Run cancellation."""

    result: CancelRunResponse | None = None

    @model_validator(mode="after")
    def validate_run_cancel_result(self) -> McpRunCancelResponse:
        if self.ok and self.result is None:
            raise ValueError("successful Run cancellations require a result")
        if not self.ok and self.result is not None:
            raise ValueError("failed Run cancellations cannot include Run data")
        return self


class McpExternalToolResponse(McpResultBase):
    """Structured response for one tool call bound to an external Run."""

    result: ToolResult | None = None
    references: list[EvidenceReference] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_external_tool_result(self) -> McpExternalToolResponse:
        if self.ok:
            if self.result is None:
                raise ValueError("successful external tool calls require a result")
            if self.result.kind != "read_source.succeeded" and self.references:
                raise ValueError("only successful source reads may return evidence references")
            if self.result.kind == "read_source.succeeded":
                evidence = self.result.evidence
                for reference in self.references:
                    if (
                        reference.index_id != evidence.index_id
                        or reference.evidence_id != evidence.evidence_id
                        or reference.path != evidence.path
                        or reference.start_line != evidence.start_line
                        or reference.end_line != evidence.end_line
                        or reference.content_hash != evidence.content_hash
                    ):
                        raise ValueError("external tool references must match returned source evidence")
        elif self.result is not None or self.references:
            raise ValueError("failed external tool calls cannot include tool data")
        return self


class McpExternalCompleteResponse(McpResultBase):
    """Structured response for a completed external Run and its report."""

    result: RunStatusResponse | None = None
    report: Report | None = None
    references: list[EvidenceReference] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_external_completion(self) -> McpExternalCompleteResponse:
        if self.ok:
            if self.result is None or self.report is None:
                raise ValueError("successful external completion requires Run status and report")
            if (
                self.result.run.run_id != self.report.run_id
                or self.result.run.project_id != self.report.project_id
                or self.result.run.session_id != self.report.session_id
                or self.result.run.index_id != self.report.index_id
                or self.result.run.status != "succeeded"
                or self.report.status != "succeeded"
            ):
                raise ValueError("external completion status and report must share one succeeded Run")
            evidence_ids = {evidence.evidence_id for evidence in self.report.evidence}
            if any(
                reference.index_id != self.report.index_id or reference.evidence_id not in evidence_ids
                for reference in self.references
            ):
                raise ValueError("external completion references must match report evidence")
        elif self.result is not None or self.report is not None or self.references:
            raise ValueError("failed external completions cannot include Run data")
        return self


class McpImportResponse(McpResultBase):
    """Structured MCP result for an enabled local or GitHub import tool."""

    source_path: str | None = Field(default=None, max_length=4_000)
    project: Project | None = None
    attempt: ImportAttempt | None = None
    index: IndexResponse | None = None
    source_url: str | None = Field(default=None, max_length=4_000)
    requested_ref: str | None = Field(default=None, max_length=200)
    resolved_ref: str | None = Field(default=None, max_length=200)
    resolved_commit: str | None = Field(default=None, max_length=64)
    content_hash: Sha256 | None = None
    cleanup_status: CleanupStatus | None = None
    cleanup_error: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_import_result(self) -> McpImportResponse:
        if self.ok:
            if self.source_path is None or self.project is None or self.attempt is None:
                raise ValueError("successful import results require source, project, and attempt")
            if self.attempt.status == "ready":
                if self.index is None or self.index.index.index_id != self.attempt.index_id:
                    raise ValueError("ready import results require the created index")
            elif self.index is not None:
                raise ValueError("pending or failed import results cannot include an index")
        elif any(value is not None for value in (self.source_path, self.project, self.attempt, self.index, self.source_url)):
            raise ValueError("failed import results cannot include import data")
        return self


class McpImportStatusResponse(McpResultBase):
    """Structured MCP result for a bounded import status lookup."""

    source_path: str | None = Field(default=None, max_length=4_000)
    project: Project | None = None
    attempt: ImportAttempt | None = None
    latest_index: IndexResponse | None = None

    @model_validator(mode="after")
    def validate_import_status(self) -> McpImportStatusResponse:
        if self.ok:
            if self.source_path is None or self.project is None or self.attempt is None:
                raise ValueError("successful import status requires source, project, and attempt")
        elif any(value is not None for value in (self.source_path, self.project, self.attempt, self.latest_index)):
            raise ValueError("failed import status cannot include import data")
        return self


class McpJobResponse(McpResultBase):
    job: JobStatusResponse | None = None

    @model_validator(mode="after")
    def validate_job_result(self) -> McpJobResponse:
        if self.ok != (self.job is not None):
            raise ValueError("successful MCP job response must contain job status")
        return self


ToolSuccessResult: TypeAlias = Annotated[
    SearchCodeSuccess | ReadSourceSuccess | GetNeighborsSuccess,
    Field(discriminator="kind"),
]
ToolFailureResult: TypeAlias = Annotated[
    SearchCodeFailure | ReadSourceFailure | GetNeighborsFailure,
    Field(discriminator="kind"),
]
ToolResult: TypeAlias = Annotated[
    SearchCodeSuccess
    | SearchCodeFailure
    | ReadSourceSuccess
    | ReadSourceFailure
    | GetNeighborsSuccess
    | GetNeighborsFailure,
    Field(discriminator="kind"),
]


class ModelOutput(VersionedModel):
    output_id: Identifier
    run_id: Identifier
    index_id: Identifier
    fixture_mode: Literal[True] = True
    generated_after_sequence: PositiveInt
    outcome: ModelOutputOutcome
    summary: str = Field(min_length=1, max_length=5_000)
    findings: list[Finding] = Field(default_factory=list)
    candidate_links: list[CandidateLink] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_output_links(self) -> ModelOutput:
        if self.outcome == "candidate_found" and not self.candidate_links:
            raise ValueError("candidate_found model output must include candidate links")
        if self.outcome == "insufficient_evidence" and self.candidate_links:
            raise ValueError("insufficient_evidence model output cannot include candidate links")
        referenced_ids = set(self.evidence_ids)
        for finding in self.findings:
            if not set(finding.evidence_ids).issubset(referenced_ids):
                raise ValueError("finding references evidence absent from model output")
        for link in self.candidate_links:
            if not set(link.evidence_ids).issubset(referenced_ids):
                raise ValueError("candidate link references evidence absent from model output")
        return self


class RunStartedPayload(ContractModel):
    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    issue_id: Identifier
    mode: RunMode
    analysis_kind: RunAnalysisKind = "issue"
    execution_source: RunExecutionSource = "internal"
    message_end_sequence: PositiveInt
    input_hash: Sha256
    feature_generation_id: Identifier | None = None
    execution_version: Identifier
    budget: RunBudget


class StageStartedPayload(ContractModel):
    stage: Literal["retrieval", "analysis", "reporting"]


class ToolStartedPayload(ContractModel):
    request: ToolRequest


class ToolCompletedPayload(ContractModel):
    result: ToolSuccessResult


class ToolFailedPayload(ContractModel):
    result: ToolFailureResult


class GraphUpdatedPayload(ContractModel):
    graph: GraphSnapshot


class FindingCreatedPayload(ContractModel):
    finding: Finding


class RunCompletedPayload(ContractModel):
    status: Literal["succeeded"]
    outcome: Literal["candidate_found", "insufficient_evidence", "explanation"]
    report_id: Identifier


class RunError(ContractModel):
    code: Literal[
        "tool_failed",
        "budget_exceeded",
        "model_unavailable",
        "model_failed",
        "invalid_model_output",
        "internal_error",
    ]
    message: str = Field(min_length=1, max_length=2_000)


class RunFailedPayload(ContractModel):
    status: Literal["failed"]
    report_id: Identifier
    error: RunError


class RunCancelledPayload(ContractModel):
    status: Literal["cancelled"]
    reason: str = Field(min_length=1, max_length=1_000)


class RunInterruptedPayload(ContractModel):
    status: Literal["interrupted"]
    reason: str = Field(min_length=1, max_length=1_000)


class EventBase(ContractModel):
    run_id: Identifier
    sequence: PositiveInt
    timestamp: datetime


class RunStartedEvent(EventBase):
    type: Literal["run.started"] = "run.started"
    payload: RunStartedPayload


class StageStartedEvent(EventBase):
    type: Literal["stage.started"] = "stage.started"
    payload: StageStartedPayload


class ToolStartedEvent(EventBase):
    type: Literal["tool.started"] = "tool.started"
    payload: ToolStartedPayload


class ToolCompletedEvent(EventBase):
    type: Literal["tool.completed"] = "tool.completed"
    payload: ToolCompletedPayload


class ToolFailedEvent(EventBase):
    type: Literal["tool.failed"] = "tool.failed"
    payload: ToolFailedPayload


class GraphUpdatedEvent(EventBase):
    type: Literal["graph.updated"] = "graph.updated"
    payload: GraphUpdatedPayload


class FindingCreatedEvent(EventBase):
    type: Literal["finding.created"] = "finding.created"
    payload: FindingCreatedPayload


class RunCompletedEvent(EventBase):
    type: Literal["run.completed"] = "run.completed"
    payload: RunCompletedPayload


class RunFailedEvent(EventBase):
    type: Literal["run.failed"] = "run.failed"
    payload: RunFailedPayload


class RunCancelledEvent(EventBase):
    type: Literal["run.cancelled"] = "run.cancelled"
    payload: RunCancelledPayload


class RunInterruptedEvent(EventBase):
    type: Literal["run.interrupted"] = "run.interrupted"
    payload: RunInterruptedPayload


Event: TypeAlias = Annotated[
    RunStartedEvent
    | StageStartedEvent
    | ToolStartedEvent
    | ToolCompletedEvent
    | ToolFailedEvent
    | GraphUpdatedEvent
    | FindingCreatedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent
    | RunInterruptedEvent,
    Field(discriminator="type"),
]
EventType: TypeAlias = Literal[
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
]


class EventSequence(VersionedModel):
    run_id: Identifier
    events: list[Event] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_sequence(self) -> EventSequence:
        terminal_types = {"run.completed", "run.failed", "run.cancelled", "run.interrupted"}
        expected_sequence = 1
        terminal_seen = False
        for event in self.events:
            if event.run_id != self.run_id:
                raise ValueError("event run_id must match the sequence run_id")
            if event.sequence != expected_sequence:
                raise ValueError("event sequence must be contiguous and start at 1")
            if terminal_seen:
                raise ValueError("no event may follow a terminal run event")
            expected_sequence += 1
            if event.type in terminal_types:
                terminal_seen = True
        if not terminal_seen:
            raise ValueError("event sequence must end with a terminal run event")
        return self


class SseEventFrame(VersionedModel):
    """One persisted public Run event encoded as an SSE frame."""

    id: str = Field(min_length=3, max_length=402)
    event: EventType
    data: Event

    @model_validator(mode="after")
    def validate_frame(self) -> SseEventFrame:
        expected_id = f"{self.data.run_id}:{self.data.sequence}"
        if self.id != expected_id or self.event != self.data.type:
            raise ValueError("SSE frame metadata must match the embedded event")
        return self


SseStreamEndReason: TypeAlias = Literal["terminal"]


class SseStreamEnd(VersionedModel):
    """Non-persisted control data emitted when a terminal cursor is resumed."""

    run_id: Identifier
    sequence: NonNegativeInt
    reason: SseStreamEndReason
    status: RunTerminalStatus


class SseStreamEndFrame(VersionedModel):
    """A finite-stream marker; it never advances the persisted event sequence."""

    id: str = Field(min_length=3, max_length=402)
    event: Literal["run.stream.end"] = "run.stream.end"
    data: SseStreamEnd

    @model_validator(mode="after")
    def validate_frame(self) -> SseStreamEndFrame:
        expected_id = f"{self.data.run_id}:{self.data.sequence}"
        if self.id != expected_id:
            raise ValueError("SSE stream-end metadata must match the terminal cursor")
        return self


class CreateRunRequest(VersionedModel):
    """Start one explicit fixture or real Run from a saved Session cutoff."""

    index_id: Identifier
    issue: Issue
    mode: RunMode
    analysis_kind: RunAnalysisKind = "issue"
    execution_source: RunExecutionSource = "internal"
    fixture_scenario: FixtureScenario | None = None
    feature_generation_id: Identifier | None = None
    message_end_sequence: PositiveInt | None = None
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    execution_version: Identifier = "run-fixture-v1"
    budget: RunBudget = Field(default_factory=RunBudget)
    idempotency_key: Identifier | None = None

    @model_validator(mode="after")
    def validate_run_request(self) -> CreateRunRequest:
        if self.mode == "fixture" and self.fixture_scenario is None:
            raise ValueError("fixture requests must declare a fixture scenario")
        if self.mode == "real" and self.fixture_scenario is not None:
            raise ValueError("real requests cannot declare a fixture scenario")
        if self.mode == "external" and self.fixture_scenario is not None:
            raise ValueError("external requests cannot declare a fixture scenario")
        if self.mode == "external" and self.execution_source != "codex":
            raise ValueError("external requests must use the Codex execution source")
        if self.mode != "external" and self.execution_source != "internal":
            raise ValueError("fixture and real requests must use the internal execution source")
        if self.mode == "fixture" and self.analysis_kind != "issue":
            raise ValueError("fixture requests only support Issue analysis")
        if self.mode == "external" and self.model is not None:
            raise ValueError("external requests cannot declare an internal model")
        if any(
            value
            for value in (
                self.budget.tool_calls_used,
                self.budget.model_calls_used,
                self.budget.repair_calls_used,
                self.budget.source_lines_used,
                self.budget.input_chars_used,
                self.budget.model_input_chars_used,
                self.budget.output_chars_used,
                self.budget.model_output_chars_used,
                self.budget.events_used,
            )
        ):
            raise ValueError("run request budgets must start with zero usage")
        return self


class CreateRunResponse(VersionedModel):
    run_id: Identifier
    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    status: Literal["queued"]


class RunStatusResponse(VersionedModel):
    run: Run
    input: RunInput


class RunListResponse(VersionedModel):
    """Bounded newest-first Run history for one project Session."""

    project_id: Identifier
    session_id: Identifier
    runs: list[Run] = Field(default_factory=list, max_length=40)
    next_cursor: str | None = Field(default=None, max_length=500)
    has_more: bool = False

    @model_validator(mode="after")
    def validate_run_scope(self) -> RunListResponse:
        run_ids = {run.run_id for run in self.runs}
        if len(run_ids) != len(self.runs):
            raise ValueError("run list must contain unique run IDs")
        if any(run.project_id != self.project_id or run.session_id != self.session_id for run in self.runs):
            raise ValueError("run list crossed the project or session boundary")
        if self.has_more and not self.next_cursor:
            raise ValueError("paginated run lists must include a next cursor")
        if not self.has_more and self.next_cursor is not None:
            raise ValueError("complete run lists cannot include a next cursor")
        return self


class RunEventsResponse(VersionedModel):
    """A bounded event page ordered by the server-assigned sequence."""

    project_id: Identifier
    session_id: Identifier
    run_id: Identifier
    events: list[Event] = Field(default_factory=list, max_length=100)
    next_after_sequence: NonNegativeInt | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def validate_event_scope(self) -> RunEventsResponse:
        sequences = [event.sequence for event in self.events]
        if any(event.run_id != self.run_id for event in self.events):
            raise ValueError("run events crossed the run boundary")
        if len(set(sequences)) != len(sequences) or sequences != sorted(sequences):
            raise ValueError("run events must be unique and ascending")
        if self.has_more and self.next_after_sequence is None:
            raise ValueError("paginated run events must include a next sequence")
        if not self.has_more and self.next_after_sequence is not None:
            raise ValueError("complete run events cannot include a next sequence")
        return self


class RunToolResultsResponse(VersionedModel):
    """A bounded page of persisted fixture tool results."""

    project_id: Identifier
    session_id: Identifier
    run_id: Identifier
    results: list[ToolResult] = Field(default_factory=list, max_length=40)
    next_after_sequence: NonNegativeInt | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def validate_tool_result_scope(self) -> RunToolResultsResponse:
        sequences = [result.completed_sequence for result in self.results]
        if any(result.run_id != self.run_id for result in self.results):
            raise ValueError("run tool results crossed the run boundary")
        if len(set(sequences)) != len(sequences) or sequences != sorted(sequences):
            raise ValueError("run tool results must be ordered by completion sequence")
        if self.has_more and self.next_after_sequence is None:
            raise ValueError("paginated run tool results must include a next sequence")
        if not self.has_more and self.next_after_sequence is not None:
            raise ValueError("complete run tool results cannot include a next sequence")
        return self


class GraphResponse(VersionedModel):
    graph: GraphSnapshot


class ReportResponse(VersionedModel):
    report: Report


class EvidenceResponse(VersionedModel):
    evidence: Evidence


class CancelRunRequest(VersionedModel):
    reason: str = Field(min_length=1, max_length=1_000)


class CancelRunResponse(VersionedModel):
    run_id: Identifier
    project_id: Identifier
    session_id: Identifier
    index_id: Identifier
    status: RunStatus
    cancel_event_sequence: PositiveInt | None = None


class FixtureResponseSet(VersionedModel):
    scenario_id: Identifier
    mode: Literal["fixture"]
    index: IndexSnapshot
    run: Run
    input: RunInput
    tool_results: list[ToolResult] = Field(min_length=1)
    model_output: ModelOutput
    report: Report
    events: EventSequence

    @model_validator(mode="after")
    def validate_fixture_consistency(self) -> FixtureResponseSet:
        index_id = self.index.index_id
        run_id = self.run.run_id
        if self.run.index_id != index_id:
            raise ValueError("run must use the fixture index_id")
        if self.input.project_id != self.run.project_id or self.input.session_id != self.run.session_id:
            raise ValueError("run input must use the fixture project and session")
        if self.input.index_id != index_id or self.input.message_end_sequence != self.run.message_end_sequence:
            raise ValueError("run input must use the fixture run snapshot and cutoff")
        if self.input.input_hash != self.run.input_hash or self.input.input_chars != self.run.input_chars:
            raise ValueError("run input must match the fixture run input metadata")
        if self.run.status != self.report.status:
            raise ValueError("run and report statuses must match")
        if self.model_output.run_id != run_id or self.report.run_id != run_id:
            raise ValueError("all run artifacts must use the fixture run_id")
        if self.model_output.index_id != index_id or self.report.index_id != index_id:
            raise ValueError("all analysis artifacts must use the fixture index_id")
        if self.events.run_id != run_id:
            raise ValueError("event sequence must use the fixture run_id")
        for result in self.tool_results:
            if result.run_id != run_id or result.index_id != index_id:
                raise ValueError("tool result must use the fixture run and index")

        tool_request_ids = {result.request_id for result in self.tool_results}
        event_request_ids = {
            event.payload.request.request_id
            for event in self.events.events
            if event.type == "tool.started"
        }
        event_result_ids = {
            event.payload.result.request_id
            for event in self.events.events
            if event.type in {"tool.completed", "tool.failed"}
        }
        if tool_request_ids != event_request_ids or tool_request_ids != event_result_ids:
            raise ValueError("every tool result must have matching started and finished events")

        available_evidence: dict[str, tuple[Evidence, int]] = {}
        for result in self.tool_results:
            if isinstance(result, ReadSourceSuccess):
                evidence = result.evidence
                if evidence.index_id != index_id:
                    raise ValueError("tool evidence must use the fixture index_id")
                if evidence.evidence_id in available_evidence:
                    raise ValueError("tool evidence IDs must be unique")
                available_evidence[evidence.evidence_id] = (evidence, result.completed_sequence)

        allowed_before_model = {
            evidence_id
            for evidence_id, (_, sequence) in available_evidence.items()
            if sequence < self.model_output.generated_after_sequence
        }
        if not set(self.model_output.evidence_ids).issubset(allowed_before_model):
            raise ValueError("model output may reference only evidence returned earlier by tools")
        if self.model_output.outcome != self.report.outcome:
            raise ValueError("model output and report outcomes must match")

        report_evidence_ids = {evidence.evidence_id for evidence in self.report.evidence}
        if not report_evidence_ids.issubset(allowed_before_model):
            raise ValueError("report may reference only evidence returned earlier by tools")
        for evidence in self.report.evidence:
            tool_evidence = available_evidence.get(evidence.evidence_id)
            if not tool_evidence or tool_evidence[0] != evidence:
                raise ValueError("report evidence must match the corresponding tool result")

        final_event = self.events.events[-1]
        if self.report.terminal_sequence != final_event.sequence:
            raise ValueError("report terminal_sequence must match the final event")
        if final_event.type == "run.completed":
            if self.report.status != "succeeded" or final_event.payload.outcome != self.report.outcome:
                raise ValueError("run.completed must match a succeeded report outcome")
        elif final_event.type == "run.failed":
            if self.report.status != "failed" or self.report.outcome != "failed":
                raise ValueError("run.failed must match a failed report")
        elif final_event.type == "run.cancelled":
            if self.report.status != "cancelled":
                raise ValueError("run.cancelled must match a cancelled report")
        elif final_event.type == "run.interrupted":
            if self.report.status != "interrupted":
                raise ValueError("run.interrupted must match an interrupted report")
        else:
            raise ValueError("fixture event sequence must end with a terminal event")
        return self
