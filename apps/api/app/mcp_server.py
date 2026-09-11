"""Local read-only MCP adapter for the CodeTrail workspace service."""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlencode, urlsplit, urlunsplit

from mcp.server import MCPServer
from pydantic import Field

from .contracts import (
    EvidenceReference,
    FeatureGenerationBudget,
    FeatureGenerationRequest,
    FeatureGenerationResponse,
    FeatureGenerationMode,
    FeatureSourceScope,
    GitHubImportRequest,
    GitHubImportResponse,
    Identifier,
    McpError,
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
    McpSourceResponse,
    LocalImportRequest,
    LocalImportResponse,
    ProjectIndexListResponse,
    ProjectOverview,
)
from .service import IndexService, RepositoryRegistry, ServiceError, WorkspaceService


logger = logging.getLogger(__name__)

_WORKBENCH_URL_ENV = "CODETRAIL_WORKBENCH_URL"
_DEFAULT_WORKBENCH_URL = "http://localhost:3000"
_MAX_DISCOVERY_ITEMS = 100
_RETRYABLE_CODES = {"budget_exceeded", "internal_error"}
_NeighborEdgeKind = Literal["imports", "calls"]
_ProjectId = Annotated[Identifier, Field(description="Stable CodeTrail project ID.")]
_IndexId = Annotated[Identifier, Field(description="Immutable CodeTrail index ID.")]
_Query = Annotated[str, Field(min_length=1, max_length=500, description="Symbol or source text query.")]
_Path = Annotated[str, Field(min_length=1, max_length=500, description="Relative path in the indexed snapshot.")]
_LocalPath = Annotated[str, Field(min_length=1, max_length=4_000, description="Absolute path visible to the local API service.")]
_Line = Annotated[int, Field(ge=1, description="One-based source line.")]
_Limit = Annotated[int, Field(ge=1, le=40, description="Maximum number of returned items.")]
_Depth = Annotated[int, Field(ge=1, le=2, description="Static graph traversal depth.")]
_DiscoveryLimit = Annotated[int, Field(ge=1, le=_MAX_DISCOVERY_ITEMS, description="Maximum discovery items.")]
_OverviewDepth = Annotated[int, Field(ge=1, le=8, description="Maximum static overview path depth.")]
_OverviewPaths = Annotated[int, Field(ge=1, le=40, description="Maximum returned overview paths.")]
_OverviewModules = Annotated[int, Field(ge=1, le=200, description="Maximum returned overview modules.")]
McpResponse = TypeVar("McpResponse", bound=McpResultBase)


def create_mcp_server(
    workspace_service: WorkspaceService | None = None,
    *,
    workbench_url: str | None = None,
) -> MCPServer:
    """Create a read-only MCP server backed by one workspace service instance."""

    service = workspace_service or _workspace_from_environment()
    citation_url = _normalize_workbench_url(workbench_url or os.getenv(_WORKBENCH_URL_ENV))
    local_import_enabled = service.registry.mcp_local_import_enabled
    github_import_enabled = service.registry.mcp_github_import_enabled
    feature_generation_enabled = service.registry.mcp_feature_generation_enabled
    server_description = (
        "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "All default tools are read-only. Explicitly enabled local and GitHub import/status/retry tools are bounded."
        if local_import_enabled or github_import_enabled or feature_generation_enabled
        else "Discover CodeTrail projects and query one explicitly selected immutable index. "
        "All tools are read-only and never execute repository code."
    )
    server = MCPServer(
        name="codetrail",
        title="CodeTrail workspace" if local_import_enabled or github_import_enabled or feature_generation_enabled else "CodeTrail read-only workspace",
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
            "Build a deterministic entry-point and module overview for one explicitly selected immutable index. "
            "The first rule set covers Python __main__.py and top-level __name__ == __main__ guards; "
            "unresolved calls remain unresolved and the result is bounded."
        ),
        structured_output=True,
    )
    def get_overview(
        project_id: _ProjectId,
        index_id: _IndexId,
        max_depth: _OverviewDepth = 4,
        max_paths: _OverviewPaths = 40,
        max_modules: _OverviewModules = 100,
    ) -> McpOverviewResponse:
        try:
            result = service.get_overview(
                project_id,
                index_id,
                max_depth=max_depth,
                max_paths=max_paths,
                max_modules=max_modules,
            )
            return _mcp_overview_response(result, citation_url)
        except ServiceError as exc:
            return _failure(McpOverviewResponse, exc)
        except Exception:
            return _unexpected_failure("get_overview", McpOverviewResponse)

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
            prompt_version: Annotated[str, Field(min_length=1, max_length=200)] = "feature-summary-v1",
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
                            max_seconds=max_seconds,
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
