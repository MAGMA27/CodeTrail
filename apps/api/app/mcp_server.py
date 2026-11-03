"""Local MCP adapter for the CodeTrail workspace service."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlencode, urlsplit, urlunsplit

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import Field, TypeAdapter, ValidationError

from . import contracts as shared
from .contracts import (
    CandidateLink,
    CodeExplanation,
    CodeUnderstandingFinal,
    EvidenceReference,
    CancelRunRequest,
    CreateRunRequest,
    FeatureGraphResponse,
    FeatureGenerationListResponse,
    FeatureGenerationResponse,
    FeatureModelOutput,
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
    McpJobResponse,
    McpIndexResponse,
    McpIndexFreshnessResponse,
    McpIndexRefreshResponse,
    McpJourneyCatalogResponse,
    McpJourneyResponse,
    McpNeighborsResponse,
    McpModuleSymbolsResponse,
    McpOverviewResponse,
    McpOverviewItemsResponse,
    McpOverviewModuleResponse,
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
    OverviewSectionCoverage,
    OverviewSection,
    OverviewClassificationKind,
    OverviewItemsPage,
    OverviewModuleCard,
    ProjectOverview,
    ProjectJourney,
    ProjectJourneyCatalog,
    RefreshProjectIndexRequest,
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
from .errors import ERROR_CODES, safe_error_message, validation_error_details


logger = logging.getLogger(__name__)


class _CodeTrailMCPServer(MCPServer):
    """Normalize CodeTrail tool failures and text output at the SDK boundary."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            result = await super().call_tool(name, arguments, context)
        except ToolError as exc:
            cause = exc.__cause__
            tool = self._tool_manager.get_tool(name)
            response_model = tool.fn_metadata.output_model if tool is not None else None
            if not isinstance(response_model, type) or not hasattr(response_model, "model_fields"):
                raise
            if isinstance(cause, ValidationError):
                if any(item.get("type") == "extra_forbidden" for item in cause.errors()):
                    raise
                annotations = {
                    name: field.annotation
                    for name, field in tool.fn_metadata.arg_model.model_fields.items()
                }
                details = validation_error_details(cause, arg_annotations=annotations)
                count = len({detail.field.split(".", 1)[0] for detail in details})
                error = McpError(
                    code="invalid_input",
                    message=f"{count} tool argument{'s are' if count != 1 else ' is'} invalid",
                    details=details or None,
                )
            else:
                logger.exception("MCP tool %s failed unexpectedly", name, exc_info=exc)
                error = McpError(code="internal_error", message="local MCP query failed", retryable=True)
            payload: dict[str, object] = {"ok": False, "error": error}
            for field_name, field in response_model.model_fields.items():
                if field_name in payload:
                    continue
                if not field.is_required():
                    continue
                if field_name == "ok":
                    continue
                if field_name not in arguments:
                    continue
                candidate = arguments[field_name]
                try:
                    candidate = TypeAdapter(field.annotation).validate_python(candidate)
                except ValidationError:
                    # Keep the caller's value for traceability. Validation has
                    # already failed, so constructing without revalidating lets
                    # the MCP error explain the bad argument without inventing
                    # a replacement identifier.
                    pass
                payload[field_name] = candidate
            try:
                failure = response_model.model_validate(payload)
            except ValidationError:
                failure = response_model.model_construct(**payload)
            structured = failure.model_dump(mode="json", by_alias=True)
            return CallToolResult(
                content=[TextContent(type="text", text=_compact_json(structured))],
                structured_content=structured,
                is_error=True,
            )
        if isinstance(result, CallToolResult) and result.structured_content is not None:
            structured = result.structured_content
            failed = isinstance(structured, dict) and structured.get("ok") is False
            return result.model_copy(
                update={
                    "content": [TextContent(type="text", text=_compact_json(structured))],
                    "is_error": result.is_error or failed,
                }
            )
        return result


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_WORKBENCH_URL_ENV = "CODETRAIL_WORKBENCH_URL"
_DEFAULT_WORKBENCH_URL = "http://localhost:3000"
_MAX_DISCOVERY_ITEMS = 100
_RETRYABLE_CODES = {"budget_exceeded", "download_timeout", "index_timeout", "index_cancelled", "internal_error"}
_NeighborEdgeKind = Literal["imports", "calls"]
_ImpactDirection = Literal["callers", "callees"]
_ProjectId = Annotated[Identifier, Field(description="Stable CodeTrail project ID.")]
_IndexId = Annotated[Identifier, Field(description="Immutable CodeTrail index ID.")]
_ModuleId = Annotated[Identifier, Field(description="Module symbol ID from the selected immutable index.")]
_Query = Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_QUERY_LENGTH, description="Symbol or source text query.")]
_Path = Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_PATH_LENGTH, description="Relative path in the indexed snapshot.")]
_LocalPath = Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_LOCAL_PATH_LENGTH, description="Absolute path visible to the local API service.")]
_Line = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, description="One-based source line.")]
_Limit = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_PAGE_SIZE, description="Maximum number of returned items.")]
_Depth = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_GRAPH_DEPTH, description="Static graph traversal depth.")]
_ImpactId = Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH, description="Exact candidate or finding ID from the saved Run report.")]
_DiscoveryLimit = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=_MAX_DISCOVERY_ITEMS, description="Maximum discovery items.")]
_OverviewDepth = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_DEPTH, description="Maximum static overview path depth.")]
_OverviewPaths = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_PATHS, description="Maximum returned overview paths.")]
_OverviewModules = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_MODULES, description="Maximum returned overview modules.")]
_OverviewGroups = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_GROUPS, description="Maximum returned overview groups.")]
_OverviewModuleRelations = Annotated[
    int,
    Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_RELATIONS, description="Maximum returned aggregated module relations."),
]
_SkeletonModuleLimit = Annotated[
    int,
    Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_SKELETON_MODULES, description="Maximum modules in the summary skeleton."),
]
_SkeletonRelationLimit = Annotated[
    int,
    Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_RELATIONS, description="Maximum relations in the summary skeleton."),
]
_OverviewPageLimit = Annotated[
    int,
    Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_PAGE_LIMIT, description="Maximum items in one overview section page."),
]
_OverviewCursor = Annotated[str | None, Field(max_length=shared.MAX_OVERVIEW_CURSOR_LENGTH, description="Opaque cursor from a previous page; round-trip it unchanged. Invalid or expired cursor values are rejected.")]
_OverviewModuleFilter = Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)]
_OverviewClassifications = Annotated[
    int,
    Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_OVERVIEW_CLASSIFICATIONS, description="Maximum returned architecture classifications."),
]
_FeatureHistoryCursor = Annotated[str | None, Field(max_length=shared.MAX_CURSOR_LENGTH, description="Opaque history cursor from a previous page.")]
_ModuleSymbolsCursor = Annotated[str | None, Field(max_length=shared.MAX_CURSOR_LENGTH, description="Opaque cursor from a previous module-symbol page.")]
_FeatureGraphNodes = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_FEATURE_GRAPH_NODES, description="Maximum feature graph nodes.")]
_FeatureGraphEdges = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_FEATURE_GRAPH_EDGES, description="Maximum feature graph edges.")]
_SessionCursor = Annotated[str | None, Field(max_length=shared.MAX_CURSOR_LENGTH, description="Opaque session cursor from a previous page.")]
_SessionMessageLimit = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_SESSION_MESSAGES, description="Maximum session messages.")]
_SessionMessageCursor = Annotated[str | None, Field(max_length=shared.MAX_CURSOR_LENGTH, description="Opaque message cursor from a previous page.")]
_SessionId = Annotated[Identifier, Field(description="Stable CodeTrail session ID.")]
_RunCursor = Annotated[str | None, Field(max_length=shared.MAX_CURSOR_LENGTH, description="Opaque Run history cursor from a previous page.")]
_RunSequence = Annotated[int, Field(ge=shared.MIN_RUN_SEQUENCE, description="Return events or results after this sequence.")]
_RunEventLimit = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_EVENTS, description="Maximum Run events.")]
_RunToolLimit = Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_TOOL_RESULTS, description="Maximum persisted tool results.")]
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
    index_write_enabled = service.registry.mcp_index_write_enabled
    session_write_enabled = service.registry.mcp_session_write_enabled
    run_write_enabled = service.registry.mcp_run_write_enabled
    external_run_enabled = service.registry.mcp_external_run_enabled
    server_description = (
        "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "Default tools are read-only. Explicitly enabled session writes, bounded fixture/real Run start/cancel, "
        "Codex external-Agent Run and feature-submission tools, "
        "local/GitHub import/status/retry, and index refresh are separately gated."
        if local_import_enabled or github_import_enabled or index_write_enabled or session_write_enabled or run_write_enabled or external_run_enabled
        else "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "All tools are read-only and never execute repository code."
    )
    server = _CodeTrailMCPServer(
        name="codetrail",
        title="CodeTrail workspace" if local_import_enabled or github_import_enabled or index_write_enabled or session_write_enabled or run_write_enabled or external_run_enabled else "CodeTrail read-only workspace",
        description=server_description,
        version="0.2.0",
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
        limit: _Limit = shared.MAX_PAGE_SIZE,
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
        limit: _SessionMessageLimit = shared.MAX_SESSION_MESSAGES,
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
        name="check_index_freshness",
        title="Check index freshness",
        description=(
            "Compare one explicitly selected project index with its current source. Local sources use a bounded content-hash "
            "manifest scan; GitHub sources resolve only the tracked ref metadata without downloading an archive. "
            "The check is read-only and never creates an index."
        ),
        structured_output=True,
    )
    def check_index_freshness(
        project_id: _ProjectId,
        index_id: _IndexId,
    ) -> McpIndexFreshnessResponse:
        try:
            return McpIndexFreshnessResponse(
                ok=True,
                result=service.check_index_freshness(project_id, index_id),
            )
        except ServiceError as exc:
            return _failure(McpIndexFreshnessResponse, exc)
        except Exception:
            return _unexpected_failure("check_index_freshness", McpIndexFreshnessResponse)

    if index_write_enabled:

        @server.tool(
            name="refresh_index",
            title="Refresh project index",
            description=(
                "Submit a background recheck/rebuild for the project's current latest index and return a job ID; poll "
                "get_job_status for its result. This write is separately enabled and the baseline must remain latest."
            ),
            structured_output=True,
        )
        def refresh_index(
            project_id: _ProjectId,
            index_id: _IndexId,
            revision: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = shared.DEFAULT_SOURCE_REVISION,
            dirty: bool = True,
        ) -> McpJobResponse:
            try:
                job = service.submit_job("project_index_refresh", project_id, {
                    "index_id": index_id,
                    "request": RefreshProjectIndexRequest(revision=revision, dirty=dirty).model_dump(mode="json", exclude={"schema_version"}),
                })
                return McpJobResponse(ok=True, job=job)
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("refresh_index", McpJobResponse)

    @server.tool(
        name="search_code",
        title="Search one CodeTrail index",
        description=(
            "Search symbols and captured source text in one explicitly selected project/index pair. "
            "Exact qualified and leaf symbol names rank before partial names and symbol-scoped body matches; "
            "results are capped at 40 and report truncation."
        ),
        structured_output=True,
    )
    def search_code(
        project_id: _ProjectId,
        index_id: _IndexId,
        query: _Query,
        limit: _Limit = shared.MAX_PAGE_SIZE,
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
        name="list_module_symbols",
        title="List definitions in an indexed module",
        description=(
            "List indexed class, function, and test definitions from one module path in the selected immutable project/index. "
            "This reports definitions captured by the index and does not imply complete parsing. Pages are bounded to 40 items "
            "and share the API output budget; pass the returned cursor unchanged to continue."
        ),
        structured_output=True,
    )
    def list_module_symbols(
        project_id: _ProjectId,
        index_id: _IndexId,
        module_id: _ModuleId,
        limit: _Limit = shared.MAX_PAGE_SIZE,
        cursor: _ModuleSymbolsCursor = None,
    ) -> McpModuleSymbolsResponse:
        try:
            return McpModuleSymbolsResponse(
                ok=True,
                result=service.list_module_symbols(project_id, index_id, module_id, limit, cursor),
            )
        except ServiceError as exc:
            return _failure(McpModuleSymbolsResponse, exc)
        except Exception:
            return _unexpected_failure("list_module_symbols", McpModuleSymbolsResponse)

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
        title="Get code neighbors and conditional contexts",
        description=(
            "Traverse bounded static imports/calls around one symbol in an explicitly selected project/index pair. "
            "The result may separately include contextual_calls for one explicitly constructed receiver and its direct "
            "field binding. These are conditional records, not static call edges or confirmed impact; "
            "binding_mutation_after_escape_unverified marks a receiver stored through an attribute whose later "
            "mutation is unknown. Dynamic or unresolved references remain marked and are not promoted to confirmed edges. "
            "Edges come from conservative static analysis: calls through receivers whose type cannot be proven "
            "(unannotated parameters, star-import re-exports, dynamic dispatch, monkeypatched attributes, "
            "decorated or overridden methods) are left unresolved, so an empty or short list is not proof of no "
            "callers or callees. Always read completeness: status possibly_incomplete lists reasons and a hint; "
            "possible_unresolved_callers samples unresolved calls that name the root symbol. In callers mode "
            "unresolved is empty; in callees/both it lists unresolved calls made by returned nodes. Each neighbor "
            "carries depth, the hop at which its edge was reached."
        ),
        structured_output=True,
    )
    def get_neighbors(
        project_id: _ProjectId,
        index_id: _IndexId,
        symbol_id: _IndexId,
        direction: Literal["callers", "callees", "both"],
        depth: _Depth = shared.MAX_GRAPH_DEPTH,
        limit: _Limit = shared.MAX_PAGE_SIZE,
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
            "Build a deterministic entry-point, structural group, module, and evidence-backed architecture classification overview for one explicitly selected immutable index. "
            "Rules cover Python __main__.py, top-level __name__ == __main__ guards, and conservatively confirmed "
            "FastAPI routes with literal declared paths, public API names in static __all__, and top-level pluggy "
            "hookspec functions bound to a real HookspecMarker import. Classifications are independent from calls "
            "paths; unknown does not mean unused or unreachable, TypeScript export facts are not persisted, and "
            "the result and source evidence are bounded. Default detail='summary' returns a compact skeleton; "
            "use detail='full' to return the complete ProjectOverview with evidence references and coverage. "
            "Expand a summary with get_module and list_overview_items; round-trip cursors unchanged. "
            "classification_coverage.analysis_incomplete marks rule analysis that stopped early."
        ),
        structured_output=True,
    )
    def get_overview(
        project_id: _ProjectId,
        index_id: _IndexId,
        detail: Literal["summary", "full"] = "summary",
        module_limit: Annotated[_SkeletonModuleLimit, Field(description="Used only in summary mode; ignored in full mode.")] = shared.DEFAULT_SKELETON_MODULES,
        relation_limit: Annotated[_SkeletonRelationLimit, Field(description="Used only in summary mode; ignored in full mode.")] = shared.DEFAULT_SKELETON_RELATIONS,
        cursor: Annotated[_OverviewCursor, Field(description="Used only in summary mode; ignored in full mode.")] = None,
        max_depth: Annotated[_OverviewDepth, Field(description="Used only in full mode; ignored in summary mode.")] = shared.DEFAULT_OVERVIEW_DEPTH,
        max_paths: Annotated[_OverviewPaths, Field(description="Used only in full mode; ignored in summary mode.")] = shared.MAX_OVERVIEW_PATHS,
        max_modules: Annotated[_OverviewModules, Field(description="Used only in full mode; ignored in summary mode.")] = shared.DEFAULT_OVERVIEW_MODULES,
        max_groups: Annotated[_OverviewGroups, Field(description="Used only in full mode; ignored in summary mode.")] = shared.DEFAULT_OVERVIEW_GROUPS,
        max_module_relations: Annotated[_OverviewModuleRelations, Field(description="Used only in full mode; ignored in summary mode.")] = shared.DEFAULT_OVERVIEW_RELATIONS,
        max_classifications: Annotated[_OverviewClassifications, Field(description="Used only in full mode; ignored in summary mode.")] = shared.DEFAULT_OVERVIEW_CLASSIFICATIONS,
    ) -> McpOverviewResponse:
        try:
            if detail == "summary":
                result = service.get_overview_skeleton(
                    project_id,
                    index_id,
                    module_limit=module_limit,
                    relation_limit=relation_limit,
                    cursor=cursor,
                )
                return McpOverviewResponse(
                    ok=True,
                    skeleton=result,
                    expand={
                        "get_module": "get_module(module_id)",
                        "list_overview_items": "list_overview_items(section=..., cursor=...)",
                    },
                )
            result = service.get_overview(
                project_id,
                index_id,
                max_depth=max_depth,
                max_paths=max_paths,
                max_modules=max_modules,
                max_groups=max_groups,
                max_module_relations=max_module_relations,
                max_classifications=max_classifications,
            )
            return _mcp_overview_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpOverviewResponse, exc)
        except Exception:
            return _unexpected_failure("get_overview", McpOverviewResponse)

    @server.tool(
        name="list_overview_items",
        title="List overview section items",
        description=(
            "Read one page from an overview section. detail='summary' omits module symbols and classification evidence; "
            "detail='full' returns current full items. Cursors work across detail levels; round-trip unchanged. "
            "Invalid or expired cursors are rejected."
        ),
        structured_output=True,
    )
    def list_overview_items(
        project_id: _ProjectId,
        index_id: _IndexId,
        section: OverviewSection,
        cursor: _OverviewCursor = None,
        limit: _OverviewPageLimit = shared.DEFAULT_OVERVIEW_PAGE_LIMIT,
        module_id: _OverviewModuleFilter = None,
        category: OverviewClassificationKind | None = None,
        detail: Literal["summary", "full"] = "summary",
    ) -> McpOverviewItemsResponse:
        try:
            result = service.list_overview_items(
                project_id, index_id, section, cursor=cursor, limit=limit, module_id=module_id,
                category=category, detail=detail,
            )
            return McpOverviewItemsResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpOverviewItemsResponse, exc)
        except Exception:
            return _unexpected_failure("list_overview_items", McpOverviewItemsResponse)

    @server.tool(
        name="get_module",
        title="Get overview module details",
        description="Read one module card from the selected index, including bounded relations and classifications.",
        structured_output=True,
    )
    def get_module(project_id: _ProjectId, index_id: _IndexId, module_id: _ModuleId) -> McpOverviewModuleResponse:
        try:
            result = service.get_overview_module(project_id, index_id, module_id)
            return McpOverviewModuleResponse(ok=True, result=result)
        except ServiceError as exc:
            return _failure(McpOverviewModuleResponse, exc)
        except Exception:
            return _unexpected_failure("get_module", McpOverviewModuleResponse)

    @server.tool(
        name="get_journey_catalog",
        title="List action journeys for an index",
        description=(
            "Read lightweight UI action journey summaries from one immutable project index. "
            "The bounded catalog uses same-snapshot JSX action anchors, static calls, and HTTP method/path shape matches; "
            "it does not load source excerpts or claim runtime execution. Use get_journey for one selected journey's steps and evidence."
        ),
        structured_output=True,
    )
    def get_journey_catalog(project_id: _ProjectId, index_id: _IndexId) -> McpJourneyCatalogResponse:
        try:
            result = service.get_journey_catalog(project_id, index_id)
            return McpJourneyCatalogResponse(ok=True, catalog=result)
        except ServiceError as exc:
            return _failure(McpJourneyCatalogResponse, exc)
        except Exception:
            return _unexpected_failure("get_journey_catalog", McpJourneyCatalogResponse)

    @server.tool(
        name="get_journey",
        title="Read one action journey",
        description=(
            "Read at most eight responsibility steps and source excerpts for one exact journey_id from one immutable index. "
            "Static calls, HTTP shape matches, and unconfirmed UI/result relationships remain separately labeled."
        ),
        structured_output=True,
    )
    def get_journey(
        project_id: _ProjectId,
        index_id: _IndexId,
        journey_id: _IndexId,
    ) -> McpJourneyResponse:
        try:
            result = service.get_journey(project_id, index_id, journey_id)
            return _mcp_journey_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpJourneyResponse, exc)
        except Exception:
            return _unexpected_failure("get_journey", McpJourneyResponse)

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
        limit: _Limit = shared.DEFAULT_FEATURE_HISTORY_PAGE_SIZE,
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
        max_nodes: _FeatureGraphNodes = shared.MAX_FEATURE_GRAPH_NODES,
        max_edges: _FeatureGraphEdges = shared.DEFAULT_FEATURE_GRAPH_EDGES,
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
        limit: _Limit = shared.MAX_PAGE_SIZE,
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
        after_sequence: _RunSequence = shared.MIN_RUN_SEQUENCE,
        limit: _RunEventLimit = shared.MAX_EVENT_PAGE_SIZE,
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
        after_sequence: _RunSequence = shared.MIN_RUN_SEQUENCE,
        limit: _RunToolLimit = shared.MAX_RUN_TOOL_RESULTS,
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
            "The result stays on the Run's immutable index snapshot, defaults to callers at depth 2, and classifies four "
            "layers: report anchor, direct static calls, indirect static calls, and unknown unresolved references. "
            "Static reachability is not confirmed impact. Calls whose receiver type cannot be proven statically are "
            "absent from the static layers, so missing callers or callees are possible; use get_neighbors completeness "
            "for a per-symbol signal. This tool never calls a model or executes repository source."
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
        depth: _Depth = shared.MAX_GRAPH_DEPTH,
        limit: _Limit = shared.MAX_PAGE_SIZE,
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
            topic: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_QUERY_LENGTH, description="Issue or investigation topic.")],
            index_id: _IndexId | None = None,
            feature_generation_id: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH, description="Optional saved feature generation on the same snapshot.")] = None,
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
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
            content: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_MESSAGE_LENGTH, description="Untrusted visible message content.")],
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
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
                "CODETRAIL_MCP_REAL_RUN_ENABLED and may send the task-specific frozen context and bounded source "
                "evidence to the configured provider, which can incur cost. analysis_kind=understanding is allowed "
                "for real Runs and uses the saved Session messages as the user question; fixture Runs only support "
                "Issue analysis and never invoke a provider. "
                "Both modes share the Run write switch, capacity, and budget boundaries."
            ),
            structured_output=True,
        )
        def start_session_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            index_id: _IndexId,
            issue_id: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)],
            title: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_TITLE_LENGTH)],
            body: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_MESSAGE_LENGTH)],
            fixture_scenario: _RunScenario = "success-timeout-missing",
            mode: Literal["fixture", "real"] = "fixture",
            analysis_kind: Literal["issue", "understanding"] = "issue",
            feature_generation_id: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            message_end_sequence: Annotated[int | None, Field(ge=shared.MIN_REQUEST_VALUE)] = None,
            model: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            prompt_version: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            max_tool_calls: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_TOOL_CALLS)] = shared.DEFAULT_RUN_TOOL_CALLS,
            max_model_calls: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_MODEL_CALLS)] = shared.DEFAULT_RUN_MODEL_CALLS,
            max_repair_calls: Annotated[int, Field(ge=shared.MIN_RUN_SEQUENCE, le=shared.MAX_RUN_REPAIR_CALLS)] = shared.DEFAULT_RUN_REPAIR_CALLS,
            max_seconds: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_SECONDS)] = shared.DEFAULT_RUN_SECONDS,
            max_source_lines: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_SOURCE_LINES)] = shared.DEFAULT_RUN_SOURCE_LINES,
            max_total_source_lines: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_TOTAL_SOURCE_LINES)] = shared.DEFAULT_RUN_TOTAL_SOURCE_LINES,
            max_input_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_INPUT_CHARS,
            max_model_input_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_MODEL_INPUT_CHARS,
            max_output_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_OUTPUT_CHARS,
            max_model_output_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_OUTPUT_CHARS)] = shared.DEFAULT_RUN_MODEL_OUTPUT_CHARS,
            max_events: Annotated[int, Field(ge=shared.MIN_RUN_EVENTS, le=shared.MAX_RUN_EVENT_BUDGET)] = shared.DEFAULT_RUN_EVENTS,
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
                        analysis_kind=analysis_kind,
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
            except ValidationError:
                return _failure(
                    McpRunStartResponse,
                    ServiceError("invalid_input", 400, "Run parameters are invalid"),
                )
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
            reason: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_REASON_LENGTH)] = "cancelled by MCP caller",
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
                "bound run_* tools and submit one structured conclusion. By default analysis_kind is issue; choose "
                "understanding when the Run should accept a code-flow explanation conclusion."
            ),
            structured_output=True,
        )
        def start_external_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            index_id: _IndexId,
            issue_id: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)],
            title: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_TITLE_LENGTH)],
            body: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_MESSAGE_LENGTH)],
            stacktrace: Annotated[str | None, Field(max_length=shared.MAX_MESSAGE_LENGTH)] = None,
            mentioned_paths: list[_Path] | None = None,
            feature_generation_id: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            message_end_sequence: Annotated[int | None, Field(ge=shared.MIN_REQUEST_VALUE)] = None,
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
            max_tool_calls: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_TOOL_CALLS)] = shared.DEFAULT_RUN_TOOL_CALLS,
            max_model_calls: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_MODEL_CALLS)] = shared.DEFAULT_RUN_MODEL_CALLS,
            max_repair_calls: Annotated[int, Field(ge=shared.MIN_RUN_SEQUENCE, le=shared.MAX_RUN_REPAIR_CALLS)] = shared.DEFAULT_RUN_REPAIR_CALLS,
            max_seconds: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_SECONDS)] = shared.DEFAULT_RUN_SECONDS,
            max_source_lines: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_SOURCE_LINES)] = shared.DEFAULT_RUN_SOURCE_LINES,
            max_total_source_lines: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_TOTAL_SOURCE_LINES)] = shared.DEFAULT_RUN_TOTAL_SOURCE_LINES,
            max_input_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_INPUT_CHARS,
            max_model_input_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_MODEL_INPUT_CHARS,
            max_output_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_INPUT_CHARS)] = shared.DEFAULT_RUN_OUTPUT_CHARS,
            max_model_output_chars: Annotated[int, Field(ge=shared.MIN_REQUEST_VALUE, le=shared.MAX_RUN_OUTPUT_CHARS)] = shared.DEFAULT_RUN_MODEL_OUTPUT_CHARS,
            max_events: Annotated[int, Field(ge=shared.MIN_RUN_EVENTS, le=shared.MAX_RUN_EVENT_BUDGET)] = shared.DEFAULT_RUN_EVENTS,
            analysis_kind: Literal["issue", "understanding"] = "issue",
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
                        analysis_kind=analysis_kind,
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
            limit: _Limit = shared.MAX_PAGE_SIZE,
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
                "The result may separately include constructor-bound contextual_calls; these are conditional records, "
                "not static call edges or confirmed impact. binding_mutation_after_escape_unverified marks an object "
                "stored through an attribute whose later mutation is unknown. Unresolved references remain explicit."
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
            depth: _Depth = shared.MAX_GRAPH_DEPTH,
            limit: _Limit = shared.MAX_PAGE_SIZE,
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
            name="submit_project_features",
            title="Save host-recognized project features",
            description=(
                "Validate and save FeatureModelOutput produced by the host model for one active external understanding Run. "
                "Evidence IDs, entries, modules, and symbols must be supported by this fixed snapshot and the Run's actual "
                "saved tool results; source ranges and hashes are loaded from those results. The service does not call a "
                "model. The optional model name is self-reported and does not represent server token or cost usage. "
                "Submit this before submit_external_run; the same idempotency_key and content reuse the saved generation, "
                "while different content conflicts."
            ),
            structured_output=True,
        )
        def submit_project_features(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            output: FeatureModelOutput,
            idempotency_key: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)],
            model: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
        ) -> McpFeatureGenerationResponse:
            try:
                result = service.submit_external_features(
                    project_id,
                    session_id,
                    run_id,
                    index_id,
                    output,
                    model=model,
                    idempotency_key=idempotency_key,
                )
                return _mcp_feature_generation_response(result, citation_url)
            except ServiceError as exc:
                return _failure(McpFeatureGenerationResponse, exc)
            except Exception:
                return _unexpected_failure("submit_project_features", McpFeatureGenerationResponse)

        @server.tool(
            name="submit_external_run",
            title="Submit a Codex Run conclusion",
            description=(
                "Submit one conclusion whose shape must match analysis_kind selected by start_external_run: issue Runs "
                "accept candidate_found or insufficient_evidence and forbid explanation; understanding Runs accept "
                "explanation and forbid findings and candidate_links. candidate_found requires at least one "
                "candidate_links entry; insufficient_evidence forbids candidate_links. Top-level evidence_ids must include "
                "every evidence ID referenced by findings and candidate_links. For explanation, evidence_ids must "
                "exactly match the IDs referenced by its ordered steps. Every evidence, symbol, candidate, and index "
                "reference must come from results actually returned by this Run. Repeating the same conclusion is "
                "idempotent."
            ),
            structured_output=True,
        )
        def submit_external_run(
            project_id: _ProjectId,
            session_id: _SessionId,
            run_id: _RunId,
            index_id: _IndexId,
            outcome: Literal["candidate_found", "insufficient_evidence", "explanation"],
            summary: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_SUMMARY_LENGTH)],
            findings: list[Finding] | None = None,
            candidate_links: list[CandidateLink] | None = None,
            evidence_ids: list[Identifier] | None = None,
            limitations: list[Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_REASON_LENGTH)]] | None = None,
            explanation: CodeExplanation | None = None,
        ) -> McpExternalCompleteResponse:
            try:
                if outcome == "explanation":
                    if explanation is None or findings or candidate_links:
                        raise ValueError(
                            "explanation conclusions require explanation and cannot include Issue findings or candidate links"
                        )
                    final = CodeUnderstandingFinal(
                        index_id=index_id,
                        summary=summary,
                        explanation=explanation,
                        evidence_ids=evidence_ids or [],
                        limitations=limitations or [],
                    )
                else:
                    if explanation is not None:
                        raise ValueError("Issue conclusions cannot include an explanation")
                    final = IssueModelFinal(
                        index_id=index_id,
                        outcome=outcome,
                        summary=summary,
                        findings=findings or [],
                        candidate_links=candidate_links or [],
                        evidence_ids=evidence_ids or [],
                        limitations=limitations or [],
                    )
                status, report = service.complete_external_run(
                    project_id,
                    session_id,
                    run_id,
                    final,
                )
                return McpExternalCompleteResponse(
                    ok=True,
                    result=status,
                    report=report,
                    references=_report_evidence_references(report, citation_url),
                )
            except ServiceError as exc:
                return _failure(McpExternalCompleteResponse, exc)
            except ValueError as exc:
                return _failure(
                    McpExternalCompleteResponse,
                    ServiceError("invalid_input", 400, str(exc)),
                )
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
            reason: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_REASON_LENGTH)] = "cancelled by external Agent",
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
            name: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH, description="Project display name.")],
            path: _LocalPath,
            revision: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = shared.DEFAULT_SOURCE_REVISION,
            dirty: bool = True,
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
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

    if local_import_enabled or github_import_enabled or index_write_enabled:

        @server.tool(name="get_job_status", title="Get background job status", description="Poll one durable import or index job.", structured_output=True)
        def get_job_status(job_id: _ProjectId, project_id: _ProjectId | None = None) -> McpJobResponse:
            try:
                return McpJobResponse(ok=True, job=service.get_job(job_id, project_id=project_id))
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("get_job_status", McpJobResponse)

        @server.tool(name="cancel_job", title="Cancel background job", description="Request cancellation of an unbound import job or a project-owned job with its project ID.", structured_output=True)
        def cancel_job(job_id: _ProjectId, project_id: _ProjectId | None = None) -> McpJobResponse:
            try:
                return McpJobResponse(ok=True, job=service.cancel_job(job_id, project_id=project_id))
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("cancel_job", McpJobResponse)

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
                "Submit a public GitHub HTTPS repository import and return a durable job ID immediately. Poll "
                "get_job_status and then get_import_status for the created attempt."
            ),
            structured_output=True,
        )
        def import_github_repository(
            name: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH, description="Project display name.")],
            url: Annotated[str, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_LOCAL_PATH_LENGTH, description="Official GitHub HTTPS repository URL.")],
            ref: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH, description="Optional branch, tag, or commit ref.")] = None,
            idempotency_key: Annotated[str | None, Field(min_length=shared.MIN_REQUEST_VALUE, max_length=shared.MAX_IDENTIFIER_LENGTH)] = None,
        ) -> McpJobResponse:
            try:
                request = GitHubImportRequest(
                        name=name,
                        url=url,
                        ref=ref,
                        idempotency_key=idempotency_key,
                    )
                job = service.submit_job("github_import", None, request.model_dump(mode="json", exclude={"schema_version", "idempotency_key"}), idempotency_key=idempotency_key)
                return McpJobResponse(ok=True, job=job)
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("import_github_repository", McpJobResponse)

        @server.tool(
            name="refresh_github_source",
            title="Refresh a tracked GitHub source",
            description=(
                "Submit an explicit update for the tracked public GitHub ref and return a job ID. Poll get_job_status "
                "for progress and terminal result; failed updates keep the old latest index."
            ),
            structured_output=True,
        )
        def refresh_github_source(
            project_id: _ProjectId,
            index_id: _IndexId,
        ) -> McpJobResponse:
            try:
                job = service.submit_job("github_refresh", project_id, {"index_id": index_id})
                return McpJobResponse(ok=True, job=job)
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("refresh_github_source", McpJobResponse)

        @server.tool(
            name="retry_github_import",
            title="Retry a GitHub import",
            description="Submit a retry for one failed or interrupted fixed-commit GitHub import and return its job ID.",
            structured_output=True,
        )
        def retry_github_import(attempt_id: _ProjectId) -> McpJobResponse:
            try:
                attempt = service.registry.management_store.load_attempt(attempt_id)
                job = service.submit_job("github_retry", attempt.project_id, {"attempt_id": attempt_id})
                return McpJobResponse(ok=True, job=job)
            except ServiceError as exc:
                return _failure(McpJobResponse, exc)
            except Exception:
                return _unexpected_failure("retry_github_import", McpJobResponse)

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
    code = exc.code if exc.code in ERROR_CODES else "internal_error"
    message = safe_error_message(exc.message) if code == exc.code else "local MCP query failed"
    return McpError(
        code=code,
        message=message,
        retryable=code in _RETRYABLE_CODES,
        details=exc.details,
    )


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
        resolved_ref=result.resolved_ref,
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
    evidence_items = [entry.evidence for entry in result.entries]
    evidence_items.append(result.responsibility_evidence)
    evidence_items.extend(
        evidence
        for classification in result.classifications
        for evidence in classification.evidence
    )
    for evidence in evidence_items:
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
    return McpOverviewResponse(
        ok=True,
        overview=result,
        references=references,
        reference_coverage=OverviewSectionCoverage(
            section="references", total=len(references), returned=len(references), truncated=False,
        ),
    )


def _mcp_journey_response(result: ProjectJourney, base_url: str) -> McpJourneyResponse:
    references: list[EvidenceReference] = []
    seen: set[str] = set()
    for step in result.steps:
        for evidence in step.evidence:
            if evidence.evidence_id in seen:
                continue
            seen.add(evidence.evidence_id)
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
    return McpJourneyResponse(ok=True, journey=result, references=references)


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
