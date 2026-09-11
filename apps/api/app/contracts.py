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


IndexIssueKind: TypeAlias = Literal[
    "decode_error",
    "file_too_large",
    "read_error",
    "symlink",
    "syntax_error",
    "outside_root",
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


class IndexResponse(VersionedModel):
    index: IndexSnapshot
    issues: list[IndexIssue] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> IndexResponse:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated indexes must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated indexes cannot have a truncation reason")
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


SymbolKind: TypeAlias = Literal["module", "file", "class", "function", "test"]


class Symbol(LineRangeModel):
    symbol_id: Identifier
    index_id: Identifier
    path: RelativePath
    qualified_name: str = Field(min_length=1, max_length=500)
    kind: SymbolKind

    _normalize_path = field_validator("path")(_validate_relative_path)


EdgeKind: TypeAlias = Literal["contains", "imports", "calls"]


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
    max_tool_calls: PositiveInt = Field(le=12)
    max_seconds: PositiveInt = Field(le=120)
    max_source_lines: PositiveInt = Field(le=200)
    tool_calls_used: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_tool_budget(self) -> RunBudget:
        if self.tool_calls_used > self.max_tool_calls:
            raise ValueError("tool_calls_used cannot exceed max_tool_calls")
        return self


RunMode: TypeAlias = Literal["fixture", "real"]
RunStatus: TypeAlias = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "interrupted"
]
ReportOutcome: TypeAlias = Literal["candidate_found", "insufficient_evidence", "failed"]


class Run(VersionedModel):
    run_id: Identifier
    index_id: Identifier
    issue: Issue
    mode: RunMode
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    status: RunStatus
    budget: RunBudget
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def validate_run_consistency(self) -> Run:
        if self.issue.schema_version != SCHEMA_VERSION:
            raise ValueError("issue schema_version must match the contract version")
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


class Report(VersionedModel):
    report_id: Identifier
    run_id: Identifier
    index_id: Identifier
    status: Literal["succeeded", "failed", "cancelled", "interrupted"]
    outcome: ReportOutcome
    fixture_mode: Literal[True] = True
    summary: str = Field(min_length=1, max_length=5_000)
    graph: GraphSnapshot
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    candidate_links: list[CandidateLink] = Field(default_factory=list)
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
        if self.outcome == "candidate_found" and not self.candidate_links:
            raise ValueError("candidate_found reports must include candidate links")
        if self.outcome == "insufficient_evidence" and self.candidate_links:
            raise ValueError("insufficient_evidence reports cannot include candidate links")
        if self.outcome == "failed" and self.status != "failed":
            raise ValueError("failed reports must have failed status")
        if self.outcome != "failed" and self.status != "succeeded":
            raise ValueError("non-failed outcomes must have succeeded status")
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
    neighbors: list[Neighbor]
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_truncation(self) -> GetNeighborsSuccess:
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated neighbor results must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated neighbor results cannot have a truncation reason")
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


class SourceIndexResponse(VersionedModel):
    index_id: Identifier
    evidence: Evidence


class NeighborsIndexResponse(VersionedModel):
    index_id: Identifier
    root: Symbol
    nodes: list[Symbol] = Field(default_factory=list)
    neighbors: list[Neighbor] = Field(default_factory=list)
    unresolved: list[UnresolvedReference] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = Field(default=None, max_length=1_000)

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
        for unresolved in self.unresolved:
            if unresolved.index_id != self.index_id or unresolved.source_symbol_id not in node_ids:
                raise ValueError("unresolved reference must use returned nodes")
        if self.truncated and not self.truncation_reason:
            raise ValueError("truncated neighbor results must explain the truncation")
        if not self.truncated and self.truncation_reason:
            raise ValueError("non-truncated neighbor results cannot have a truncation reason")
        return self


OverviewEntryKind: TypeAlias = Literal["python_main_module", "python_main_guard"]
OverviewEntryStatus: TypeAlias = Literal["identified", "not_identified", "partial"]


class OverviewEntryPoint(LineRangeModel):
    """One rule-supported Python entry candidate in an immutable snapshot."""

    entry_point_id: Identifier
    index_id: Identifier
    path: RelativePath
    kind: OverviewEntryKind
    rule_id: Identifier
    label: str = Field(min_length=1, max_length=500)
    symbol_id: Identifier | None = None
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

    @model_validator(mode="after")
    def validate_module_identity(self) -> OverviewModule:
        if self.symbol.index_id != self.index_id or self.module_id != self.symbol.symbol_id:
            raise ValueError("overview module must use its module symbol identity")
        if self.symbol.kind != "module":
            raise ValueError("overview modules must reference module symbols")
        if len(set(self.entry_point_ids)) != len(self.entry_point_ids):
            raise ValueError("overview module entry IDs must be unique")
        return self


class OverviewPath(ContractModel):
    """One bounded static path from an entry module to a reached symbol."""

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

    @model_validator(mode="after")
    def validate_counts(self) -> OverviewCoverage:
        if self.visible_modules > self.total_modules:
            raise ValueError("visible overview modules cannot exceed total modules")
        if self.reachable_modules + self.unreachable_modules != self.total_modules:
            raise ValueError("overview module reachability counts must cover all modules")
        return self


class ProjectOverview(VersionedModel):
    """Deterministic, rule-only overview of one project index snapshot."""

    project_id: Identifier
    index: IndexSnapshot
    rules_version: Identifier
    entry_status: OverviewEntryStatus
    entries: list[OverviewEntryPoint] = Field(default_factory=list)
    modules: list[OverviewModule] = Field(default_factory=list)
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
        if len({module.module_id for module in self.modules}) != len(self.modules):
            raise ValueError("overview module IDs must be unique")
        if len({path.path_id for path in self.paths}) != len(self.paths):
            raise ValueError("overview path IDs must be unique")
        entry_ids = {entry.entry_point_id for entry in self.entries}
        for entry in self.entries:
            if entry.index_id != self.index.index_id:
                raise ValueError("overview entry belongs to a different index_id")
        for module in self.modules:
            if module.index_id != self.index.index_id:
                raise ValueError("overview module belongs to a different index_id")
            if not set(module.entry_point_ids).issubset(entry_ids):
                raise ValueError("overview module references an unknown entry point")
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
    model: str | None = Field(default=None, max_length=200)
    prompt_version: Identifier
    rules_version: Identifier
    source_scope: FeatureSourceScope
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
        return self


class FeatureGenerationResponse(VersionedModel):
    generation: FeatureGeneration


class FeatureGenerationSummary(VersionedModel):
    """Bounded metadata for one saved feature generation in a history page."""

    generation_id: Identifier
    project_id: Identifier
    index: IndexSnapshot
    mode: FeatureGenerationMode
    model: str | None = Field(default=None, max_length=200)
    prompt_version: Identifier
    rules_version: Identifier
    source_scope: FeatureSourceScope
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
    "internal_error",
    "forbidden",
]


class ApiErrorResponse(VersionedModel):
    code: ApiErrorCode
    message: str = Field(min_length=1, max_length=2_000)


McpErrorCode: TypeAlias = Literal[
    "not_found",
    "invalid_input",
    "conflict",
    "budget_exceeded",
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


class McpSearchResponse(McpResultBase):
    result: SearchIndexResponse | None = None


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
            for reference in self.references:
                if reference.index_id != self.overview.index.index_id or reference.evidence_id not in evidence_ids:
                    raise ValueError("overview references must match returned entry evidence")
        elif self.overview is not None or self.references:
            raise ValueError("failed overview results cannot include overview data")
        return self


class McpFeatureGenerationResponse(McpResultBase):
    """Structured MCP response for an explicitly enabled feature generation."""

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


class McpImportResponse(McpResultBase):
    """Structured MCP result for an enabled local or GitHub import tool."""

    source_path: str | None = Field(default=None, max_length=4_000)
    project: Project | None = None
    attempt: ImportAttempt | None = None
    index: IndexResponse | None = None
    source_url: str | None = Field(default=None, max_length=4_000)
    requested_ref: str | None = Field(default=None, max_length=200)
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
    outcome: ReportOutcome
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
    index_id: Identifier
    issue_id: Identifier
    mode: RunMode
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
    outcome: Literal["candidate_found", "insufficient_evidence"]
    report_id: Identifier


class RunError(ContractModel):
    code: Literal["tool_failed", "budget_exceeded", "internal_error"]
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
    events: list[Event] = Field(min_length=1)

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
    id: PositiveInt
    event: EventType
    data: Event

    @model_validator(mode="after")
    def validate_frame(self) -> SseEventFrame:
        if self.id != self.data.sequence or self.event != self.data.type:
            raise ValueError("SSE frame metadata must match the embedded event")
        return self


class CreateRunRequest(VersionedModel):
    index_id: Identifier
    issue: Issue
    mode: RunMode
    model: str | None = Field(default=None, max_length=200)
    prompt_version: str | None = Field(default=None, max_length=200)
    budget: RunBudget


class CreateRunResponse(VersionedModel):
    run_id: Identifier
    index_id: Identifier
    status: Literal["queued"]


class RunStatusResponse(VersionedModel):
    run: Run


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
    index_id: Identifier
    status: Literal["cancelled"]
    cancel_event_sequence: PositiveInt


class FixtureResponseSet(VersionedModel):
    scenario_id: Identifier
    mode: Literal["fixture"]
    index: IndexSnapshot
    run: Run
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
