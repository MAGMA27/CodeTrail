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


ApiErrorCode: TypeAlias = Literal[
    "not_found",
    "invalid_input",
    "conflict",
    "budget_exceeded",
    "internal_error",
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
