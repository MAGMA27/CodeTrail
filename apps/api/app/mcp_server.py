"""Local MCP adapter for the CodeTrail workspace service."""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlencode, urlsplit, urlunsplit

from mcp.server import MCPServer
from pydantic import Field

from .contracts import (
    CandidateLink,
    EvidenceReference,
    CancelRunRequest,
    CreateRunRequest,
    FeatureGraphResponse,
    FeatureGenerationBudget,
    FeatureGenerationListResponse,
    FeatureGenerationRequest,
    FeatureGenerationResponse,
    FeatureGenerationMode,
    FeatureSourceScope,
    GitHubImportRequest,
    GitHubImportResponse,
    Identifier,
    Issue,
    IssueModelFinal,
    Finding,
    AppendSessionMessageRequest,
    CreateSessionRequest,
    McpError,
    McpFeatureGenerationListResponse,
    McpFeatureGraphResponse,
    McpFeatureGenerationResponse,
    McpImportResponse,
    McpImportStatusResponse,
    McpIndexResponse,
    McpNeighborsResponse,
    McpOverviewResponse,
    McpProjectIndexesResponse,
    McpProjectListResponse,
    McpProjectResponse,
    McpResultBase,
    McpSearchResponse,
    McpSessionListResponse,
    McpSessionMessageResponse,
    McpSessionMessagesResponse,
    McpSessionResponse,
    McpSourceResponse,
    McpRunCancelResponse,
    McpRunEventsResponse,
    McpRunListResponse,
    McpRunReportResponse,
    McpRunImpactResponse,
    McpRunResponse,
    McpRunStartResponse,
    McpRunToolResultsResponse,
    McpExternalCompleteResponse,
    McpExternalToolResponse,
    LocalImportRequest,
    LocalImportResponse,
    ProjectIndexListResponse,
    ProjectOverview,
    ReportResponse,
    RunEventsResponse,
    RunImpactResponse,
    RunListResponse,
    ReadSourceParams,
    ReadSourceSuccess,
    RunStatusResponse,
    RunToolResultsResponse,
    SearchCodeParams,
    GetNeighborsParams,
    ToolResult,
    SessionListResponse,
    SessionMessage,
    SessionMessagesResponse,
    SessionReference,
    SessionResponse,
)
from .service import IndexService, RepositoryRegistry, ServiceError, WorkspaceService


logger = logging.getLogger(__name__)

_WORKBENCH_URL_ENV = "CODETRAIL_WORKBENCH_URL"
_DEFAULT_WORKBENCH_URL = "http://localhost:3000"
_MAX_DISCOVERY_ITEMS = 100
_RETRYABLE_CODES = {"budget_exceeded", "internal_error"}
_NeighborEdgeKind = Literal["imports", "calls"]
_ImpactDirection = Literal["callers", "callees"]
_ProjectId = Annotated[Identifier, Field(description="Stable CodeTrail project ID.")]
_IndexId = Annotated[Identifier, Field(description="Immutable CodeTrail index ID.")]
_Query = Annotated[str, Field(min_length=1, max_length=500, description="Symbol or source text query.")]
_Path = Annotated[str, Field(min_length=1, max_length=500, description="Relative path in the indexed snapshot.")]
_LocalPath = Annotated[str, Field(min_length=1, max_length=4_000, description="Absolute path visible to the local API service.")]
_Line = Annotated[int, Field(ge=1, description="One-based source line.")]
_Limit = Annotated[int, Field(ge=1, le=40, description="Maximum number of returned items.")]
_Depth = Annotated[int, Field(ge=1, le=2, description="Static graph traversal depth.")]
_ImpactId = Annotated[str | None, Field(min_length=1, max_length=200, description="Exact candidate or finding ID from the saved Run report.")]
_DiscoveryLimit = Annotated[int, Field(ge=1, le=_MAX_DISCOVERY_ITEMS, description="Maximum discovery items.")]
_OverviewDepth = Annotated[int, Field(ge=1, le=8, description="Maximum static overview path depth.")]
_OverviewPaths = Annotated[int, Field(ge=1, le=40, description="Maximum returned overview paths.")]
_OverviewModules = Annotated[int, Field(ge=1, le=200, description="Maximum returned overview modules.")]
_OverviewGroups = Annotated[int, Field(ge=1, le=200, description="Maximum returned overview groups.")]
_OverviewModuleRelations = Annotated[
    int,
    Field(ge=1, le=400, description="Maximum returned aggregated module relations."),
]
_FeatureHistoryCursor = Annotated[str | None, Field(max_length=500, description="Opaque history cursor from a previous page.")]
_FeatureGraphNodes = Annotated[int, Field(ge=1, le=40, description="Maximum feature graph nodes.")]
_FeatureGraphEdges = Annotated[int, Field(ge=1, le=120, description="Maximum feature graph edges.")]
_SessionCursor = Annotated[str | None, Field(max_length=500, description="Opaque session cursor from a previous page.")]
_SessionMessageLimit = Annotated[int, Field(ge=1, le=100, description="Maximum session messages.")]
_SessionMessageCursor = Annotated[str | None, Field(max_length=500, description="Opaque message cursor from a previous page.")]
_SessionId = Annotated[Identifier, Field(description="Stable CodeTrail session ID.")]
_RunCursor = Annotated[str | None, Field(max_length=500, description="Opaque Run history cursor from a previous page.")]
_RunSequence = Annotated[int, Field(ge=0, description="Return events or results after this sequence.")]
_RunEventLimit = Annotated[int, Field(ge=1, le=100, description="Maximum Run events.")]
_RunToolLimit = Annotated[int, Field(ge=1, le=40, description="Maximum persisted tool results.")]
_RunScenario = Literal[
    "success-timeout-missing",
    "insufficient-external-service",
    "tool-failure-read-source",
]
_RunId = Annotated[Identifier, Field(description="Stable CodeTrail Run ID.")]
_RequestId = Annotated[Identifier, Field(description="Idempotency key for one external tool request.")]
McpResponse = TypeVar("McpResponse", bound=McpResultBase)


def create_mcp_server(
    workspace_service: WorkspaceService | None = None,
    *,
    workbench_url: str | None = None,
) -> MCPServer:
    """Create an MCP server backed by one workspace service instance."""

    service = workspace_service or _workspace_from_environment()
    citation_url = _normalize_workbench_url(workbench_url or os.getenv(_WORKBENCH_URL_ENV))
    local_import_enabled = service.registry.mcp_local_import_enabled
    github_import_enabled = service.registry.mcp_github_import_enabled
    feature_generation_enabled = service.registry.mcp_feature_generation_enabled
    session_write_enabled = service.registry.mcp_session_write_enabled
    run_write_enabled = service.registry.mcp_run_write_enabled
    external_run_enabled = service.registry.mcp_external_run_enabled
    server_description = (
        "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "Default tools are read-only. Explicitly enabled session writes, bounded fixture/real Run start/cancel, "
        "Codex external-Agent Run tools, "
        "local/GitHub import/status/retry, and feature generation are separately gated."
        if local_import_enabled or github_import_enabled or feature_generation_enabled or session_write_enabled or run_write_enabled or external_run_enabled
        else "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "All tools are read-only and never execute repository code."
    )
    server = MCPServer(
        name="codetrail",
        title="CodeTrail workspace" if local_import_enabled or github_import_enabled or feature_generation_enabled or session_write_enabled or run_write_enabled or external_run_enabled else "CodeTrail read-only workspace",
        description=server_description,
        version="0.1.0",
        log_level="WARNING",
    )

    @server.tool(
        name="list_projects",
        title="List CodeTrail projects",
        description=(
            "List the configured local CodeTrail projects and their available immutable indexes. "
            f"This is the only discovery tool that does not require a project_id; output is capped at {_MAX_DISCOVERY_ITEMS}."
        ),
        structured_output=True,
    )
    def list_projects(limit: _DiscoveryLimit = _MAX_DISCOVERY_ITEMS) -> McpProjectListResponse:
        try:
            result = service.list_projects()
            projects = result.projects[:limit]
            truncated = len(result.projects) > len(projects)
            return McpProjectListResponse(
                ok=True,
                projects=projects,
                truncated=truncated,
                truncation_reason=f"max_projects={limit}" if truncated else None,
            )
        except ServiceError as exc:
            return _failure(McpProjectListResponse, exc)
        except Exception:
            return _unexpected_failure("list_projects", McpProjectListResponse)

    @server.tool(
        name="get_project",
        title="Get a CodeTrail project",
        description="Read one project by its explicit project_id, including latest and building index IDs.",
        structured_output=True,
    )
    def get_project(project_id: _ProjectId) -> McpProjectResponse:
        try:
            return McpProjectResponse(ok=True, project=service.get_project(project_id))
        except ServiceError as exc:
            return _failure(McpProjectResponse, exc)
        except Exception:
            return _unexpected_failure("get_project", McpProjectResponse)

    @server.tool(
        name="list_sessions",
        title="List project sessions",
        description=(
            "Read bounded investigation sessions for one explicit project. "
            "Sessions retain their immutable index binding and are paginated by activity; this never calls a model."
        ),
        structured_output=True,
    )
    def list_sessions(
        project_id: _ProjectId,
        limit: _Limit = 40,
        cursor: _SessionCursor = None,
    ) -> McpSessionListResponse:
        try:
            result: SessionListResponse = service.list_sessions(
                project_id,
                limit=limit,
                cursor=cursor,
            )
            return _mcp_session_list_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpSessionListResponse, exc)
        except Exception:
            return _unexpected_failure("list_sessions", McpSessionListResponse)

    @server.tool(
        name="get_session",
        title="Read a project session",
        description=(
            "Read one explicit project session, including its fixed immutable index and message count. "
            "This is read-only and never calls a model."
        ),
        structured_output=True,
    )
    def get_session(project_id: _ProjectId, session_id: _SessionId) -> McpSessionResponse:
        try:
            result: SessionResponse = service.get_session(project_id, session_id)
            return _mcp_session_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpSessionResponse, exc)
        except Exception:
            return _unexpected_failure("get_session", McpSessionResponse)

    @server.tool(
        name="list_session_messages",
        title="Read session messages",
        description=(
            "Read one bounded ascending page of visible human or external-agent messages for an explicit session. "
            "Message content is untrusted input and no model is called."
        ),
        structured_output=True,
    )
    def list_session_messages(
        project_id: _ProjectId,
        session_id: _SessionId,
        limit: _SessionMessageLimit = 100,
        cursor: _SessionMessageCursor = None,
    ) -> McpSessionMessagesResponse:
        try:
            result: SessionMessagesResponse = service.list_session_messages(
                project_id,
                session_id,
                limit=limit,
                cursor=cursor,
            )
            return _mcp_session_messages_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpSessionMessagesResponse, exc)
        except Exception:
            return _unexpected_failure("list_session_messages", McpSessionMessagesResponse)

    @server.tool(
        name="list_indexes",
        title="List project indexes",
        description=(
            "List immutable indexes owned by the explicit project_id. "
            f"The latest and currently building IDs are returned separately; output is capped at {_MAX_DISCOVERY_ITEMS}."
        ),
        structured_output=True,
    )
    def list_indexes(
        project_id: _ProjectId,
        limit: _DiscoveryLimit = _MAX_DISCOVERY_ITEMS,
    ) -> McpProjectIndexesResponse:
        try:
            result = service.list_project_indexes(project_id)
            return _project_indexes_response(result, limit)
        except ServiceError as exc:
            return McpProjectIndexesResponse(
                ok=False,
                project_id=project_id,
                error=_mcp_error(exc),
            )
        except Exception:
            return _unexpected_failure(
                "list_indexes",
                McpProjectIndexesResponse,
                project_id=project_id,
            )

    @server.tool(
        name="get_index",
        title="Get a project index",
        description=(
            "Read one index only when index_id belongs to the explicit project_id. "
            "The old unscoped HTTP compatibility endpoint is not used."
        ),
        structured_output=True,
    )
    def get_index(project_id: _ProjectId, index_id: _IndexId) -> McpIndexResponse:
        try:
            return McpIndexResponse(ok=True, index=service.get_project_index(project_id, index_id))
        except ServiceError as exc:
            return _failure(McpIndexResponse, exc)
        except Exception:
            return _unexpected_failure("get_index", McpIndexResponse)

    @server.tool(
        name="search_code",
        title="Search one CodeTrail index",
        description=(
            "Search symbols and captured source text in one explicitly selected project/index pair. "
            "Results are capped at 40 and report truncation."
        ),
        structured_output=True,
    )
    def search_code(
        project_id: _ProjectId,
        index_id: _IndexId,
        query: _Query,
        limit: _Limit = 40,
    ) -> McpSearchResponse:
        try:
            return McpSearchResponse(
                ok=True,
                result=service.search(project_id, index_id, query, limit),
            )
        except ServiceError as exc:
            return _failure(McpSearchResponse, exc)
        except Exception:
            return _unexpected_failure("search_code", McpSearchResponse)

    @server.tool(
        name="read_source",
        title="Read immutable source evidence",
        description=(
            "Read at most 200 lines from the immutable snapshot selected by project_id and index_id. "
            "The result includes a content hash, evidence_id, and a trusted workbench URL for the same citation."
        ),
        structured_output=True,
    )
    def read_source(
        project_id: _ProjectId,
        index_id: _IndexId,
        path: _Path,
        start_line: _Line,
        end_line: _Line,
    ) -> McpSourceResponse:
        try:
            result = service.read_source(project_id, index_id, path, start_line, end_line)
            reference = EvidenceReference(
                project_id=project_id,
                index_id=index_id,
                path=result.evidence.path,
                start_line=result.evidence.start_line,
                end_line=result.evidence.end_line,
                content_hash=result.evidence.content_hash,
                evidence_id=result.evidence.evidence_id,
                url=_citation_url(
                    citation_url,
                    project_id=project_id,
                    index_id=index_id,
                    path=result.evidence.path,
                    start_line=result.evidence.start_line,
                    end_line=result.evidence.end_line,
                    content_hash=result.evidence.content_hash,
                    evidence_id=result.evidence.evidence_id,
                ),
            )
            return McpSourceResponse(ok=True, result=result, reference=reference)
        except ServiceError as exc:
            return _failure(McpSourceResponse, exc)
        except Exception:
            return _unexpected_failure("read_source", McpSourceResponse)

    @server.tool(
        name="get_neighbors",
        title="Get static code neighbors",
        description=(
            "Traverse bounded static imports/calls around one symbol in an explicitly selected project/index pair. "
            "Dynamic or unresolved references remain marked and are not promoted to confirmed edges."
        ),
        structured_output=True,
    )
    def get_neighbors(
        project_id: _ProjectId,
        index_id: _IndexId,
        symbol_id: _IndexId,
        direction: Literal["callers", "callees", "both"],
        depth: _Depth = 2,
        limit: _Limit = 40,
        edge_kinds: list[_NeighborEdgeKind] | None = None,
    ) -> McpNeighborsResponse:
        try:
            result = service.neighbors(
                project_id,
                index_id,
                symbol_id,
                direction,
                depth,
                limit,
                tuple(edge_kinds) if edge_kinds else None,
            )
            return McpNeighborsResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpNeighborsResponse, exc)
        except Exception:
            return _unexpected_failure("get_neighbors", McpNeighborsResponse)

    @server.tool(
        name="get_overview",
        title="Get a project overview",
        description=(
            "Build a deterministic entry-point, structural group, and module overview for one explicitly selected immutable index. "
            "Rules cover Python __main__.py, top-level __name__ == __main__ guards, and conservatively confirmed "
            "FastAPI routes with literal declared paths; structural groups and aggregated imports/calls remain "
            "separate from semantic features, unresolved calls and unsupported dynamic registration remain explicit, "
            "and the result is bounded."
        ),
        structured_output=True,
    )
    def get_overview(
        project_id: _ProjectId,
        index_id: _IndexId,
        max_depth: _OverviewDepth = 4,
        max_paths: _OverviewPaths = 40,
        max_modules: _OverviewModules = 100,
        max_groups: _OverviewGroups = 80,
        max_module_relations: _OverviewModuleRelations = 200,
    ) -> McpOverviewResponse:
        try:
            result = service.get_overview(
                project_id,
                index_id,
                max_depth=max_depth,
                max_paths=max_paths,
                max_modules=max_modules,
                max_groups=max_groups,
                max_module_relations=max_module_relations,
            )
            return _mcp_overview_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpOverviewResponse, exc)
        except Exception:
            return _unexpected_failure("get_overview", McpOverviewResponse)

    @server.tool(
        name="list_feature_generations",
        title="List saved project feature versions",
        description=(
            "Read saved feature-generation metadata for one project, optionally filtered to one immutable index. "
            "This is read-only, paginated, and never calls retrieval or a model."
        ),
        structured_output=True,
    )
    def list_feature_generations(
        project_id: _ProjectId,
        index_id: _IndexId | None = None,
        limit: _Limit = 20,
        cursor: _FeatureHistoryCursor = None,
    ) -> McpFeatureGenerationListResponse:
        try:
            result: FeatureGenerationListResponse = service.list_feature_generations(
                project_id,
                index_id=index_id,
                limit=limit,
                cursor=cursor,
            )
            return McpFeatureGenerationListResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpFeatureGenerationListResponse, exc)
        except Exception:
            return _unexpected_failure("list_feature_generations", McpFeatureGenerationListResponse)

    @server.tool(
        name="get_feature_generation",
        title="Read saved project features",
        description=(
            "Read a previously saved feature generation for the explicit project/index pair. "
            "This is read-only and never calls a model."
        ),
        structured_output=True,
    )
    def get_feature_generation(
        project_id: _ProjectId,
        index_id: _IndexId,
        generation_id: _IndexId,
    ) -> McpFeatureGenerationResponse:
        try:
            result = service.get_feature_generation(project_id, index_id, generation_id)
            return _mcp_feature_generation_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpFeatureGenerationResponse, exc)
        except Exception:
            return _unexpected_failure("get_feature_generation", McpFeatureGenerationResponse)

    @server.tool(
        name="get_feature_graph",
        title="Read saved feature graph",
        description=(
            "Build a bounded read-only graph from one saved feature generation and the same immutable index. "
            "Semantic feature mappings and static code relations are labeled separately; no model is called."
        ),
        structured_output=True,
    )
    def get_feature_graph(
        project_id: _ProjectId,
        index_id: _IndexId,
        generation_id: _IndexId,
        max_nodes: _FeatureGraphNodes = 40,
        max_edges: _FeatureGraphEdges = 80,
    ) -> McpFeatureGraphResponse:
        try:
            result: FeatureGraphResponse = service.get_feature_graph(
                project_id,
                index_id,
                generation_id,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
            return _mcp_feature_graph_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpFeatureGraphResponse, exc)
        except Exception:
            return _unexpected_failure("get_feature_graph", McpFeatureGraphResponse)

    @server.tool(
        name="list_session_runs",
        title="List session Runs",
        description=(
            "Read bounded explicit Run history for one project session. "
            "This is read-only and returns the frozen snapshot metadata for each Run."
        ),
        structured_output=True,
    )
    def list_session_runs(
        project_id: _ProjectId,
        session_id: _SessionId,
        limit: _Limit = 40,
        cursor: _RunCursor = None,
    ) -> McpRunListResponse:
        try:
            result: RunListResponse = service.list_runs(
                project_id,
                session_id,
                limit=limit,
                cursor=cursor,
            )
            return McpRunListResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpRunListResponse, exc)
        except Exception:
            return _unexpected_failure("list_session_runs", McpRunListResponse)

    @server.tool(
        name="get_session_run",
        title="Read a session Run",
        description="Read one Run's immutable input snapshot and current lifecycle status without starting work.",
        structured_output=True,
    )
    def get_session_run(
        project_id: _ProjectId,
        session_id: _SessionId,
        run_id: _ProjectId,
    ) -> McpRunResponse:
        try:
            result: RunStatusResponse = service.get_run(project_id, session_id, run_id)
            return McpRunResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpRunResponse, exc)
        except Exception:
            return _unexpected_failure("get_session_run", McpRunResponse)

    @server.tool(
        name="list_run_events",
        title="Read Run events",
        description="Read the bounded public lifecycle/tool events for one Run in server sequence order.",
        structured_output=True,
    )
    def list_run_events(
        project_id: _ProjectId,
        session_id: _SessionId,
        run_id: _ProjectId,
        after_sequence: _RunSequence = 0,
        limit: _RunEventLimit = 100,
    ) -> McpRunEventsResponse:
        try:
            result: RunEventsResponse = service.list_run_events(
                project_id,
                session_id,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            return McpRunEventsResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpRunEventsResponse, exc)
        except Exception:
            return _unexpected_failure("list_run_events", McpRunEventsResponse)

    @server.tool(
        name="list_run_tool_results",
        title="Read Run tool results",
        description="Read bounded persisted allowlisted-tool results for one Run; no tool is executed by this read.",
        structured_output=True,
    )
    def list_run_tool_results(
        project_id: _ProjectId,
        session_id: _SessionId,
        run_id: _ProjectId,
        after_sequence: _RunSequence = 0,
        limit: _RunToolLimit = 40,
    ) -> McpRunToolResultsResponse:
        try:
            result: RunToolResultsResponse = service.list_run_tool_results(
                project_id,
                session_id,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            return McpRunToolResultsResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpRunToolResultsResponse, exc)
        except Exception:
            return _unexpected_failure("list_run_tool_results", McpRunToolResultsResponse)

    @server.tool(
        name="get_run_report",
        title="Read a Run report",
        description="Read the saved evidence-backed report for one completed Run without re-running analysis.",
        structured_output=True,
    )
    def get_run_report(
        project_id: _ProjectId,
        session_id: _SessionId,
        run_id: _ProjectId,
    ) -> McpRunReportResponse:
        try:
            result: ReportResponse = service.get_run_report(project_id, session_id, run_id)
            return McpRunReportResponse(ok=True, report=result.report)
        except ServiceError as exc:
            return _failure(McpRunReportResponse, exc)
        except Exception:
            return _unexpected_failure("get_run_report", McpRunReportResponse)

    @server.tool(
        name="get_run_impact",
        title="Read bounded Run impact",
        description=(
            "Read a bounded static calls impact view for exactly one candidate_id or finding_id from a saved Run report. "
            "The result stays on the Run's immutable index snapshot, defaults to callers at depth 2, preserves unresolved "
            "boundaries, and never calls a model or executes repository source."
        ),
        structured_output=True,
    )
    def get_run_impact(
        project_id: _ProjectId,
        session_id: _SessionId,
        run_id: _ProjectId,
        candidate_id: _ImpactId = None,
        finding_id: _ImpactId = None,
        direction: _ImpactDirection = "callers",
        depth: _Depth = 2,
        limit: _Limit = 40,
    ) -> McpRunImpactResponse:
        try:
            result: RunImpactResponse = service.get_run_impact(
                project_id,
                session_id,
                run_id,
                candidate_id=candidate_id,
                finding_id=finding_id,
                direction=direction,
                depth=depth,
                limit=limit,
            )
            return McpRunImpactResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpRunImpactResponse, exc)
        except Exception:
            return _unexpected_failure("get_run_impact", McpRunImpactResponse)

    if session_write_enabled:

        @server.tool(
            name="create_session",
            title="Create a project session",
            description=(
                "Create an empty investigation session fixed to one ready project index. "
                "The server chooses the latest available index when omitted; this only saves session metadata and never calls a model."
            ),
            structured_output=True,
        )
        def create_session(
            project_id: _ProjectId,
            topic: Annotated[str, Field(min_length=1, max_length=500, description="Issue or investigation topic.")],
            index_id: _IndexId | None = None,
            feature_generation_id: Annotated[str | None, Field(max_length=200, description="Optional saved feature generation on the same snapshot.")] = None,
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        ) -> McpSessionResponse:
            try:
                result: SessionResponse = service.create_session(
                    project_id,
                    CreateSessionRequest(
                        topic=topic,
                        index_id=index_id,
                        feature_generation_id=feature_generation_id,
                        idempotency_key=idempotency_key,
                    ),
                )
                return _mcp_session_response(result, citation_url)
            except ServiceError as exc:
                return _failure(McpSessionResponse, exc)
            except Exception:
                return _unexpected_failure("create_session", McpSessionResponse)

        @server.tool(
            name="append_session_message",
            title="Append a session message",
            description=(
                "Append one visible human or external-agent message to an explicit session. "
                "The server assigns its sequence; this only persists content and never starts a Run or calls a model."
            ),
            structured_output=True,
        )
        def append_session_message(
            project_id: _ProjectId,
            session_id: _SessionId,
            source: Literal["human", "agent"],
            content: Annotated[str, Field(min_length=1, max_length=20_000, description="Untrusted visible message content.")],
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        ) -> McpSessionMessageResponse:
            try:
                message = service.append_session_message(
                    project_id,
                    session_id,
                    AppendSessionMessageRequest(
                        source=source,
                        content=content,
                        idempotency_key=idempotency_key,
                    ),
                )
                return _mcp_session_message_response(message, citation_url, project_id)
            except ServiceError as exc:
                return _failure(McpSessionMessageResponse, exc)
            except Exception:
                return _unexpected_failure("append_session_message", McpSessionMessageResponse)

    if run_write_enabled:

        @server.tool(
            name="start_session_run",
            title="Start a bounded session Run",
            description=(
                "Explicitly start one bounded fixture or real analysis for a saved project session. "
                "The server freezes all saved messages through message_end_sequence (or the current saved cutoff), "
                "uses only search_code/get_neighbors/read_source. Real mode is separately gated by "
                "CODETRAIL_MCP_REAL_RUN_ENABLED, may send the frozen Issue/messages and bounded source evidence "
                "to the configured provider, and can incur cost; fixture mode never invokes a provider. "
                "Both modes share the Run write switch, capacity, and budget boundaries."
            ),
            structured_output=True,
        )
        def start_session_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            index_id: _IndexId,
            issue_id: Annotated[str, Field(min_length=1, max_length=200)],
            title: Annotated[str, Field(min_length=1, max_length=500)],
            body: Annotated[str, Field(min_length=1, max_length=20_000)],
            fixture_scenario: _RunScenario = "success-timeout-missing",
            mode: Literal["fixture", "real"] = "fixture",
            feature_generation_id: Annotated[str | None, Field(max_length=200)] = None,
            message_end_sequence: Annotated[int | None, Field(ge=1)] = None,
            model: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
            prompt_version: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
            max_tool_calls: Annotated[int, Field(ge=1, le=12)] = 8,
            max_model_calls: Annotated[int, Field(ge=1, le=6)] = 4,
            max_repair_calls: Annotated[int, Field(ge=0, le=2)] = 1,
            max_seconds: Annotated[int, Field(ge=1, le=120)] = 30,
            max_source_lines: Annotated[int, Field(ge=1, le=200)] = 200,
            max_total_source_lines: Annotated[int, Field(ge=1, le=2_000)] = 800,
            max_input_chars: Annotated[int, Field(ge=1, le=100_000)] = 60_000,
            max_model_input_chars: Annotated[int, Field(ge=1, le=100_000)] = 80_000,
            max_output_chars: Annotated[int, Field(ge=1, le=100_000)] = 50_000,
            max_model_output_chars: Annotated[int, Field(ge=1, le=50_000)] = 20_000,
            max_events: Annotated[int, Field(ge=2, le=200)] = 100,
        ) -> McpRunStartResponse:
            try:
                from .contracts import RunBudget

                result = service.create_run(
                    project_id,
                    session_id,
                    CreateRunRequest(
                        index_id=index_id,
                        issue=Issue(id=issue_id, title=title, body=body),
                        mode=mode,
                        fixture_scenario=fixture_scenario if mode == "fixture" else None,
                        feature_generation_id=feature_generation_id,
                        message_end_sequence=message_end_sequence,
                        model=model,
                        prompt_version=prompt_version,
                        idempotency_key=idempotency_key,
                        budget=RunBudget(
                            max_tool_calls=max_tool_calls,
                            max_model_calls=max_model_calls,
                            max_repair_calls=max_repair_calls,
                            max_seconds=max_seconds,
                            max_source_lines=max_source_lines,
                            max_total_source_lines=max_total_source_lines,
                            max_input_chars=max_input_chars,
                            max_model_input_chars=max_model_input_chars,
                            max_output_chars=max_output_chars,
                            max_model_output_chars=max_model_output_chars,
                            max_events=max_events,
                        ),
                    ),
                    via_mcp=True,
                )
                return McpRunStartResponse(ok=True, result=result)
            except ServiceError as exc:
                return _failure(McpRunStartResponse, exc)
            except Exception:
                return _unexpected_failure("start_session_run", McpRunStartResponse)

        @server.tool(
            name="cancel_session_run",
            title="Cancel a session Run",
            description=(
                "Explicitly cancel one queued or running Run. Cancellation is idempotent, persisted as one terminal event, "
                "and does not grant permission to append session messages."
            ),
            structured_output=True,
        )
        def cancel_session_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _ProjectId,
            reason: Annotated[str, Field(min_length=1, max_length=1_000)] = "cancelled by MCP caller",
        ) -> McpRunCancelResponse:
            try:
                result = service.cancel_run(
                    project_id,
                    session_id,
                    run_id,
                    CancelRunRequest(reason=reason),
                )
                return McpRunCancelResponse(ok=True, result=result)
            except ServiceError as exc:
                return _failure(McpRunCancelResponse, exc)
            except Exception:
                return _unexpected_failure("cancel_session_run", McpRunCancelResponse)

    if external_run_enabled:

        @server.tool(
            name="start_external_run",
            title="Start a Codex external Run",
            description=(
                "Start one queued Run for a Codex-led investigation. The server freezes the selected Session's saved "
                "messages and immutable index, but does not start the internal worker; the external Agent must call the "
                "bound run_* tools and submit one structured conclusion."
            ),
            structured_output=True,
        )
        def start_external_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            index_id: _IndexId,
            issue_id: Annotated[str, Field(min_length=1, max_length=200)],
            title: Annotated[str, Field(min_length=1, max_length=500)],
            body: Annotated[str, Field(min_length=1, max_length=20_000)],
            stacktrace: Annotated[str | None, Field(max_length=20_000)] = None,
            mentioned_paths: list[_Path] | None = None,
            feature_generation_id: Annotated[str | None, Field(max_length=200)] = None,
            message_end_sequence: Annotated[int | None, Field(ge=1)] = None,
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
            max_tool_calls: Annotated[int, Field(ge=1, le=12)] = 8,
            max_model_calls: Annotated[int, Field(ge=1, le=6)] = 4,
            max_repair_calls: Annotated[int, Field(ge=0, le=2)] = 1,
            max_seconds: Annotated[int, Field(ge=1, le=120)] = 30,
            max_source_lines: Annotated[int, Field(ge=1, le=200)] = 200,
            max_total_source_lines: Annotated[int, Field(ge=1, le=2_000)] = 800,
            max_input_chars: Annotated[int, Field(ge=1, le=100_000)] = 60_000,
            max_model_input_chars: Annotated[int, Field(ge=1, le=100_000)] = 80_000,
            max_output_chars: Annotated[int, Field(ge=1, le=100_000)] = 50_000,
            max_model_output_chars: Annotated[int, Field(ge=1, le=50_000)] = 20_000,
            max_events: Annotated[int, Field(ge=2, le=200)] = 100,
        ) -> McpRunStartResponse:
            try:
                from .contracts import RunBudget

                result = service.create_run(
                    project_id,
                    session_id,
                    CreateRunRequest(
                        index_id=index_id,
                        issue=Issue(
                            id=issue_id,
                            title=title,
                            body=body,
                            stacktrace=stacktrace,
                            mentioned_paths=mentioned_paths or [],
                        ),
                        mode="external",
                        execution_source="codex",
                        fixture_scenario=None,
                        feature_generation_id=feature_generation_id,
                        message_end_sequence=message_end_sequence,
                        execution_version="run-external-codex-v1",
                        idempotency_key=idempotency_key,
                        budget=RunBudget(
                            max_tool_calls=max_tool_calls,
                            max_model_calls=max_model_calls,
                            max_repair_calls=max_repair_calls,
                            max_seconds=max_seconds,
                            max_source_lines=max_source_lines,
                            max_total_source_lines=max_total_source_lines,
                            max_input_chars=max_input_chars,
                            max_model_input_chars=max_model_input_chars,
                            max_output_chars=max_output_chars,
                            max_model_output_chars=max_model_output_chars,
                            max_events=max_events,
                        ),
                    ),
                    via_mcp=True,
                )
                return McpRunStartResponse(ok=True, result=result)
            except ServiceError as exc:
                return _failure(McpRunStartResponse, exc)
            except Exception:
                return _unexpected_failure("start_external_run", McpRunStartResponse)

        @server.tool(
            name="run_search_code",
            title="Search code inside an external Run",
            description=(
                "Execute search_code and persist its actual bounded result on one active Codex external Run. "
                "Reuse the same request_id to receive the idempotent prior result."
            ),
            structured_output=True,
        )
        def run_search_code(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            request_id: _RequestId,
            query: _Query,
            limit: _Limit = 40,
        ) -> McpExternalToolResponse:
            try:
                result = service.execute_external_tool(
                    project_id,
                    session_id,
                    run_id,
                    request_id,
                    index_id,
                    SearchCodeParams(query=query, limit=limit),
                )
                return _mcp_external_tool_response(result, citation_url, project_id)
            except ServiceError as exc:
                return _failure(McpExternalToolResponse, exc)
            except Exception:
                return _unexpected_failure("run_search_code", McpExternalToolResponse)

        @server.tool(
            name="run_read_source",
            title="Read source inside an external Run",
            description=(
                "Read immutable source evidence and persist the actual result on one active Codex external Run. "
                "The response includes a same-snapshot citation when the read succeeds."
            ),
            structured_output=True,
        )
        def run_read_source(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            request_id: _RequestId,
            path: _Path,
            start_line: _Line,
            end_line: _Line,
        ) -> McpExternalToolResponse:
            try:
                result = service.execute_external_tool(
                    project_id,
                    session_id,
                    run_id,
                    request_id,
                    index_id,
                    ReadSourceParams(path=path, start_line=start_line, end_line=end_line),
                )
                return _mcp_external_tool_response(result, citation_url, project_id)
            except ServiceError as exc:
                return _failure(McpExternalToolResponse, exc)
            except Exception:
                return _unexpected_failure("run_read_source", McpExternalToolResponse)

        @server.tool(
            name="run_get_neighbors",
            title="Read neighbors inside an external Run",
            description=(
                "Traverse bounded static callers/callees and persist the actual result on one active Codex external Run. "
                "Unresolved references remain explicit and are never promoted to confirmed impact."
            ),
            structured_output=True,
        )
        def run_get_neighbors(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            request_id: _RequestId,
            symbol_id: _IndexId,
            direction: Literal["callers", "callees", "both"],
            depth: _Depth = 2,
            limit: _Limit = 40,
        ) -> McpExternalToolResponse:
            try:
                result = service.execute_external_tool(
                    project_id,
                    session_id,
                    run_id,
                    request_id,
                    index_id,
                    GetNeighborsParams(
                        symbol_id=symbol_id,
                        direction=direction,
                        depth=depth,
                        limit=limit,
                    ),
                )
                return _mcp_external_tool_response(result, citation_url, project_id)
            except ServiceError as exc:
                return _failure(McpExternalToolResponse, exc)
            except Exception:
                return _unexpected_failure("run_get_neighbors", McpExternalToolResponse)

        @server.tool(
            name="submit_external_run",
            title="Submit a Codex Run conclusion",
            description=(
                "Submit one structured candidate_found or insufficient_evidence conclusion. The server verifies every "
                "evidence, symbol, candidate, and index reference against results actually returned by this Run before "
                "persisting the report. Repeating the same conclusion is idempotent."
            ),
            structured_output=True,
        )
        def submit_external_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            outcome: Literal["candidate_found", "insufficient_evidence"],
            summary: Annotated[str, Field(min_length=1, max_length=5_000)],
            findings: list[Finding] | None = None,
            candidate_links: list[CandidateLink] | None = None,
            evidence_ids: list[Identifier] | None = None,
            limitations: list[Annotated[str, Field(min_length=1, max_length=1_000)]] | None = None,
        ) -> McpExternalCompleteResponse:
            try:
                status, report = service.complete_external_run(
                    project_id,
                    session_id,
                    run_id,
                    IssueModelFinal(
                        index_id=index_id,
                        outcome=outcome,
                        summary=summary,
                        findings=findings or [],
                        candidate_links=candidate_links or [],
                        evidence_ids=evidence_ids or [],
                        limitations=limitations or [],
                    ),
                )
                return McpExternalCompleteResponse(
                    ok=True,
                    result=status,
                    report=report,
                    references=_report_evidence_references(report, citation_url),
                )
            except ServiceError as exc:
                return _failure(McpExternalCompleteResponse, exc)
            except Exception:
                return _unexpected_failure("submit_external_run", McpExternalCompleteResponse)

        @server.tool(
            name="cancel_external_run",
            title="Cancel a Codex external Run",
            description=(
                "Cancel one queued or running Codex external Run. Cancellation is persisted as one terminal event and "
                "repeated cancellation returns the existing terminal state."
            ),
            structured_output=True,
        )
        def cancel_external_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            reason: Annotated[str, Field(min_length=1, max_length=1_000)] = "cancelled by external Agent",
        ) -> McpRunCancelResponse:
            try:
                result = service.cancel_external_run(
                    project_id,
                    session_id,
                    run_id,
                    CancelRunRequest(reason=reason),
                )
                return McpRunCancelResponse(ok=True, result=result)
            except ServiceError as exc:
                return _failure(McpRunCancelResponse, exc)
            except Exception:
                return _unexpected_failure("cancel_external_run", McpRunCancelResponse)

    if feature_generation_enabled:

        @server.tool(
            name="generate_project_features",
            title="Generate project features",
            description=(
                "Explicitly generate evidence-linked semantic features for one selected immutable index. "
                "This tool is disabled by default because real mode can call a configured model and incur cost; "
                "fixture mode is deterministic and bounded."
            ),
            structured_output=True,
        )
        def generate_project_features(
            project_id: _ProjectId,
            index_id: _IndexId,
            mode: FeatureGenerationMode,
            model: Annotated[str | None, Field(min_length=1, max_length=200, description="Server-configured model name; optional in fixture mode.")] = None,
            source_scope: FeatureSourceScope = "entry_reachable",
            prompt_version: Annotated[str, Field(min_length=1, max_length=200)] = "feature-summary-v2",
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
            max_tool_calls: Annotated[int, Field(ge=1, le=12)] = 8,
            max_model_calls: Annotated[int, Field(ge=1, le=2)] = 1,
            max_seconds: Annotated[int, Field(ge=1, le=120)] = 20,
            max_source_lines: Annotated[int, Field(ge=1, le=200)] = 120,
            max_total_source_lines: Annotated[int, Field(ge=1, le=2_000)] = 800,
            max_input_chars: Annotated[int, Field(ge=1, le=100_000)] = 60_000,
            max_features: Annotated[int, Field(ge=1, le=20)] = 8,
            max_output_chars: Annotated[int, Field(ge=1, le=50_000)] = 20_000,
        ) -> McpFeatureGenerationResponse:
            try:
                result = service.generate_features(
                    project_id,
                    index_id,
                    FeatureGenerationRequest(
                        mode=mode,
                        model=model,
                        source_scope=source_scope,
                        prompt_version=prompt_version,
                        idempotency_key=idempotency_key,
                        budget=FeatureGenerationBudget(
                            max_tool_calls=max_tool_calls,
                            max_model_calls=max_model_calls,
                            max_source_lines=max_source_lines,
                            max_total_source_lines=max_total_source_lines,
                            max_input_chars=max_input_chars,
                            max_features=max_features,
                            max_output_chars=max_output_chars,
                        ),
                    ),
                )
                return _mcp_feature_generation_response(result, citation_url)
            except ServiceError as exc:
                return _failure(McpFeatureGenerationResponse, exc)
            except Exception:
                return _unexpected_failure("generate_project_features", McpFeatureGenerationResponse)

    if local_import_enabled:

        @server.tool(
            name="import_local_path",
            title="Import a local CodeTrail path",
            description=(
                "Register and index one absolute local directory already inside the server's allowed import roots. "
                "This is a bounded write; it never writes to the source directory and returns a persisted attempt."
            ),
            structured_output=True,
        )
        def import_local_path(
            name: Annotated[str, Field(min_length=1, max_length=200, description="Project display name.")],
            path: _LocalPath,
            revision: Annotated[str, Field(min_length=1, max_length=200)] = "working-tree",
            dirty: bool = True,
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        ) -> McpImportResponse:
            try:
                result = service.import_local(
                    LocalImportRequest(
                        name=name,
                        path=path,
                        revision=revision,
                        dirty=dirty,
                        idempotency_key=idempotency_key,
                    ),
                    via_mcp=True,
                )
                return _mcp_import_response(result)
            except ServiceError as exc:
                return _failure(McpImportResponse, exc)
            except Exception:
                return _unexpected_failure("import_local_path", McpImportResponse)

        @server.tool(
            name="retry_local_import",
            title="Retry a local import",
            description="Retry one failed or interrupted local import attempt using its persisted bounded request.",
            structured_output=True,
        )
        def retry_local_import(attempt_id: _ProjectId) -> McpImportResponse:
            try:
                result = service.retry_import(attempt_id, via_mcp=True)
                return _mcp_import_response(result)
            except ServiceError as exc:
                return _failure(McpImportResponse, exc)
            except Exception:
                return _unexpected_failure("retry_local_import", McpImportResponse)

    if local_import_enabled or github_import_enabled:

        @server.tool(
            name="get_import_status",
            title="Get import status",
            description="Read one bounded persisted local or GitHub import attempt without changing it.",
            structured_output=True,
        )
        def get_import_status(attempt_id: _ProjectId) -> McpImportStatusResponse:
            try:
                result = service.get_import_status(attempt_id)
                return McpImportStatusResponse(
                    ok=True,
                    source_path=result.source_path,
                    project=result.project,
                    attempt=result.attempt,
                    latest_index=result.latest_index,
                )
            except ServiceError as exc:
                return _failure(McpImportStatusResponse, exc)
            except Exception:
                return _unexpected_failure("get_import_status", McpImportStatusResponse)

    if github_import_enabled:

        @server.tool(
            name="import_github_repository",
            title="Import a public GitHub repository",
            description=(
                "Resolve a public GitHub HTTPS repository ref to one commit, download only the official bounded archive, "
                "index supported Python files, and return persisted source identity and cleanup status."
            ),
            structured_output=True,
        )
        def import_github_repository(
            name: Annotated[str, Field(min_length=1, max_length=200, description="Project display name.")],
            url: Annotated[str, Field(min_length=1, max_length=4_000, description="Official GitHub HTTPS repository URL.")],
            ref: Annotated[str | None, Field(min_length=1, max_length=200, description="Optional branch, tag, or commit ref.")] = None,
            idempotency_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        ) -> McpImportResponse:
            try:
                result = service.import_github(
                    GitHubImportRequest(
                        name=name,
                        url=url,
                        ref=ref,
                        idempotency_key=idempotency_key,
                    ),
                    via_mcp=True,
                )
                return _mcp_github_import_response(result)
            except ServiceError as exc:
                return _failure(McpImportResponse, exc)
            except Exception:
                return _unexpected_failure("import_github_repository", McpImportResponse)

        @server.tool(
            name="retry_github_import",
            title="Retry a GitHub import",
            description="Retry one failed or interrupted fixed-commit GitHub import using its persisted request.",
            structured_output=True,
        )
        def retry_github_import(attempt_id: _ProjectId) -> McpImportResponse:
            try:
                result = service.retry_github_import(attempt_id, via_mcp=True)
                return _mcp_github_import_response(result)
            except ServiceError as exc:
                return _failure(McpImportResponse, exc)
            except Exception:
                return _unexpected_failure("retry_github_import", McpImportResponse)

    return server


def _workspace_from_environment() -> WorkspaceService:
    registry = RepositoryRegistry.from_environment()
    return WorkspaceService(registry, IndexService(registry))


def _normalize_workbench_url(value: str | None) -> str:
    raw = (value or _DEFAULT_WORKBENCH_URL).strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{_WORKBENCH_URL_ENV} must be an absolute http(s) URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _citation_url(
    base_url: str,
    *,
    project_id: str,
    index_id: str,
    path: str,
    start_line: int,
    end_line: int,
    content_hash: str,
    evidence_id: str,
) -> str:
    query = urlencode(
        {
            "project_id": project_id,
            "index_id": index_id,
            "path": path,
            "start_line": str(start_line),
            "end_line": str(end_line),
            "content_hash": content_hash,
            "evidence_id": evidence_id,
        }
    )
    return f"{base_url}?{query}"


def _project_indexes_response(result: ProjectIndexListResponse, limit: int) -> McpProjectIndexesResponse:
    indexes = list(result.indexes)
    truncated = len(indexes) > limit
    visible_indexes = indexes[:limit]
    if result.latest_index_id and result.latest_index_id not in {index.index_id for index in visible_indexes}:
        latest = next((index for index in indexes if index.index_id == result.latest_index_id), None)
        if latest is not None and visible_indexes:
            visible_indexes[-1] = latest
    return McpProjectIndexesResponse(
        ok=True,
        project_id=result.project_id,
        indexes=visible_indexes,
        latest_index_id=result.latest_index_id,
        building_index_id=result.building_index_id,
        truncated=truncated,
        truncation_reason=f"max_indexes={limit}" if truncated else None,
    )


def _mcp_error(exc: ServiceError) -> McpError:
    code = exc.code if exc.code in {"not_found", "invalid_input", "conflict", "budget_exceeded", "internal_error", "forbidden"} else "internal_error"
    message = exc.message if code == exc.code else "local MCP query failed"
    return McpError(code=code, message=message, retryable=code in _RETRYABLE_CODES)


def _mcp_import_response(result: LocalImportResponse) -> McpImportResponse:
    return McpImportResponse(
        ok=True,
        source_path=result.source_path,
        project=result.project,
        attempt=result.attempt,
        index=result.index,
    )


def _mcp_github_import_response(result: GitHubImportResponse) -> McpImportResponse:
    return McpImportResponse(
        ok=True,
        source_path=result.source_url,
        source_url=result.source_url,
        requested_ref=result.requested_ref,
        resolved_commit=result.resolved_commit,
        content_hash=result.content_hash,
        cleanup_status=result.cleanup_status,
        cleanup_error=result.cleanup_error,
        project=result.project,
        attempt=result.attempt,
        index=result.index,
    )


def _mcp_overview_response(result: ProjectOverview, base_url: str) -> McpOverviewResponse:
    references: list[EvidenceReference] = []
    seen_evidence_ids: set[str] = set()
    for entry in result.entries:
        evidence = entry.evidence
        if evidence is None or evidence.evidence_id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(evidence.evidence_id)
        references.append(
            EvidenceReference(
                project_id=result.project_id,
                index_id=evidence.index_id,
                path=evidence.path,
                start_line=evidence.start_line,
                end_line=evidence.end_line,
                content_hash=evidence.content_hash,
                evidence_id=evidence.evidence_id,
                url=_citation_url(
                    base_url,
                    project_id=result.project_id,
                    index_id=evidence.index_id,
                    path=evidence.path,
                    start_line=evidence.start_line,
                    end_line=evidence.end_line,
                    content_hash=evidence.content_hash,
                    evidence_id=evidence.evidence_id,
                ),
            )
        )
    return McpOverviewResponse(ok=True, overview=result, references=references)


def _mcp_feature_generation_response(
    result: FeatureGenerationResponse,
    base_url: str,
) -> McpFeatureGenerationResponse:
    generation = result.generation
    references = [
        EvidenceReference(
            project_id=generation.project_id,
            index_id=evidence.index_id,
            path=evidence.path,
            start_line=evidence.start_line,
            end_line=evidence.end_line,
            content_hash=evidence.content_hash,
            evidence_id=evidence.evidence_id,
            url=_citation_url(
                base_url,
                project_id=generation.project_id,
                index_id=evidence.index_id,
                path=evidence.path,
                start_line=evidence.start_line,
                end_line=evidence.end_line,
                content_hash=evidence.content_hash,
                evidence_id=evidence.evidence_id,
            ),
        )
        for evidence in generation.evidence
    ]
    return McpFeatureGenerationResponse(ok=True, generation=generation, references=references)


def _mcp_feature_graph_response(
    result: FeatureGraphResponse,
    base_url: str,
) -> McpFeatureGraphResponse:
    graph = result.graph
    references = [
        EvidenceReference(
            project_id=graph.project_id,
            index_id=evidence.index_id,
            path=evidence.path,
            start_line=evidence.start_line,
            end_line=evidence.end_line,
            content_hash=evidence.content_hash,
            evidence_id=evidence.evidence_id,
            url=_citation_url(
                base_url,
                project_id=graph.project_id,
                index_id=evidence.index_id,
                path=evidence.path,
                start_line=evidence.start_line,
                end_line=evidence.end_line,
                content_hash=evidence.content_hash,
                evidence_id=evidence.evidence_id,
            ),
        )
        for evidence in graph.evidence
    ]
    return McpFeatureGraphResponse(ok=True, graph=graph, references=references)


def _mcp_external_tool_response(
    result: ToolResult,
    base_url: str,
    project_id: str,
) -> McpExternalToolResponse:
    references: list[EvidenceReference] = []
    if isinstance(result, ReadSourceSuccess):
        evidence = result.evidence
        references.append(
            EvidenceReference(
                project_id=project_id,
                index_id=evidence.index_id,
                path=evidence.path,
                start_line=evidence.start_line,
                end_line=evidence.end_line,
                content_hash=evidence.content_hash,
                evidence_id=evidence.evidence_id,
                url=_citation_url(
                    base_url,
                    project_id=project_id,
                    index_id=evidence.index_id,
                    path=evidence.path,
                    start_line=evidence.start_line,
                    end_line=evidence.end_line,
                    content_hash=evidence.content_hash,
                    evidence_id=evidence.evidence_id,
                ),
            )
        )
    return McpExternalToolResponse(ok=True, result=result, references=references)


def _report_evidence_references(report, base_url: str) -> list[EvidenceReference]:
    return [
        EvidenceReference(
            project_id=report.project_id,
            index_id=evidence.index_id,
            path=evidence.path,
            start_line=evidence.start_line,
            end_line=evidence.end_line,
            content_hash=evidence.content_hash,
            evidence_id=evidence.evidence_id,
            url=_citation_url(
                base_url,
                project_id=report.project_id,
                index_id=evidence.index_id,
                path=evidence.path,
                start_line=evidence.start_line,
                end_line=evidence.end_line,
                content_hash=evidence.content_hash,
                evidence_id=evidence.evidence_id,
            ),
        )
        for evidence in report.evidence
    ]


def _session_url(base_url: str, project_id: str, session_id: str) -> str:
    return f"{base_url}?{urlencode({'project_id': project_id, 'session_id': session_id})}"


def _mcp_session_reference(base_url: str, project_id: str, session_id: str) -> SessionReference:
    return SessionReference(
        project_id=project_id,
        session_id=session_id,
        url=_session_url(base_url, project_id, session_id),
    )


def _mcp_session_list_response(
    result: SessionListResponse,
    base_url: str,
) -> McpSessionListResponse:
    return McpSessionListResponse(
        ok=True,
        result=result,
        references=[
            _mcp_session_reference(base_url, session.project_id, session.session_id)
            for session in result.sessions
        ],
    )


def _mcp_session_response(result: SessionResponse, base_url: str) -> McpSessionResponse:
    session = result.session
    return McpSessionResponse(
        ok=True,
        session=session,
        reference=_mcp_session_reference(base_url, session.project_id, session.session_id),
    )


def _mcp_session_messages_response(
    result: SessionMessagesResponse,
    base_url: str,
) -> McpSessionMessagesResponse:
    return McpSessionMessagesResponse(
        ok=True,
        result=result,
        reference=_mcp_session_reference(base_url, result.project_id, result.session_id),
    )


def _mcp_session_message_response(
    message: SessionMessage,
    base_url: str,
    project_id: str,
) -> McpSessionMessageResponse:
    return McpSessionMessageResponse(
        ok=True,
        message=message,
        reference=_mcp_session_reference(base_url, project_id, message.session_id),
    )


def _failure(model: type[McpResponse], exc: ServiceError, **fields: object) -> McpResponse:
    return model(ok=False, error=_mcp_error(exc), **fields)


def _unexpected_failure(
    tool_name: str,
    model: type[McpResponse],
    **fields: object,
) -> McpResponse:
    logger.exception("MCP tool %s failed unexpectedly", tool_name)
    return model(
        ok=False,
        error=McpError(
            code="internal_error",
            message="local MCP query failed",
            retryable=True,
        ),
        **fields,
    )


def main() -> None:
    """Run the MCP server over stdio; stdout is reserved for protocol frames."""

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    create_mcp_server().run("stdio")


if __name__ == "__main__":
    main()
