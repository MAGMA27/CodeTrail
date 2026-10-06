"""Minimal CodeTrail API application."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .contracts import (
    ApiErrorResponse,
    CreateIndexRequest,
    CreateProjectIndexRequest,
    CreateProjectRequest,
    AppendSessionMessageRequest,
    CancelRunRequest,
    CancelRunResponse,
    CreateRunRequest,
    CreateRunResponse,
    CreateSessionRequest,
    EdgeKind,
    FeatureGraphResponse,
    FeatureGenerationRequest,
    FeatureGenerationListResponse,
    FeatureGenerationResponse,
    IndexFreshness,
    GitHubImportRequest,
    GitHubImportResponse,
    ImportAttemptResponse,
    ImpactDirection,
    IndexResponse,
    IndexRefreshResponse,
    LocalImportRequest,
    LocalImportResponse,
    NeighborsIndexResponse,
    Project,
    ProjectIndexListResponse,
    ProjectListResponse,
    ProjectOverview,
    RefreshProjectIndexRequest,
    ReportResponse,
    RunImpactResponse,
    RunEventsResponse,
    RunListResponse,
    RunStatusResponse,
    RunToolResultsResponse,
    SearchIndexResponse,
    ModuleSymbolsPageResponse,
    SessionListResponse,
    SessionMessageResponse,
    SessionMessagesResponse,
    SessionResponse,
    SourceIndexResponse,
)
from .github import GitHubClient
from .config import ModelProviderConfig
from .feature_generation import FeatureModelAdapter
from .issue_analysis import IssueModelAdapter
from .run_sse import (
    RunEventStreamConfig,
    RunEventStreamLease,
    RunEventStreamRegistry,
    SseCapacityError,
    SseCursorError,
    encode_sse_comment,
    encode_sse_event,
    encode_sse_stream_end,
    resolve_sse_cursor,
)
from .service import IndexService, RepositoryRegistry, ServiceError, WorkspaceService, is_loopback_host
from .storage import StorageError


class HealthResponse(BaseModel):
    """Stable response contract for the local health check."""

    status: Literal["ok"]
    service: Literal["codetrail-api"]
    version: str


_API_ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
    429: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    500: {"model": ApiErrorResponse},
    503: {"model": ApiErrorResponse},
}


def _allowed_origins() -> list[str]:
    configured_origins = os.getenv(
        "CODETRAIL_WEB_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]


def create_app(
    repository_roots: Mapping[str, str | Path] | None = None,
    *,
    data_root: str | Path | None = None,
    allowed_import_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
    github_import_enabled: bool | None = None,
    mcp_github_import_enabled: bool | None = None,
    mcp_feature_generation_enabled: bool | None = None,
    mcp_index_write_enabled: bool | None = None,
    mcp_session_write_enabled: bool | None = None,
    mcp_run_write_enabled: bool | None = None,
    mcp_external_run_enabled: bool | None = None,
    real_run_enabled: bool | None = None,
    mcp_real_run_enabled: bool | None = None,
    github_client: GitHubClient | None = None,
    feature_adapter_factory: Callable[[str], FeatureModelAdapter] | None = None,
    run_model_adapter_factory: Callable[[str], IssueModelAdapter] | None = None,
    run_max_concurrent_runs: int | None = None,
    run_sse_config: RunEventStreamConfig | None = None,
    model_config: ModelProviderConfig | None = None,
) -> FastAPI:
    """Create an API with a server-owned repository allow-list."""

    registry = (
        RepositoryRegistry.from_environment(
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
        if repository_roots is None
        else RepositoryRegistry(
            repository_roots,
            data_root=data_root,
            allowed_import_roots=allowed_import_roots,
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
    )
    resolved_github_client = github_client or GitHubClient()
    index_service = IndexService(registry, github_client=resolved_github_client)
    workspace_service = WorkspaceService(
        registry,
        index_service,
        github_client=resolved_github_client,
        feature_adapter_factory=feature_adapter_factory,
        run_model_adapter_factory=run_model_adapter_factory,
        run_max_concurrent_runs=run_max_concurrent_runs,
    )
    application = FastAPI(title="CodeTrail API", version="0.1.0")
    run_event_streams = RunEventStreamRegistry(run_sse_config)
    application.state.index_service = index_service
    application.state.registry = registry
    application.state.workspace_service = workspace_service
    application.state.run_event_streams = run_event_streams
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @application.exception_handler(ServiceError)
    async def handle_service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @application.exception_handler(StorageError)
    async def handle_storage_error(_request: Request, _exc: StorageError) -> JSONResponse:
        return _error_response(500, "internal_error", "local index storage failed")

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "invalid_input", "request parameters are invalid")

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Report that the local API is available."""

        return HealthResponse(status="ok", service="codetrail-api", version="0.1.0")

    @application.post(
        "/api/imports/local",
        response_model=LocalImportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def import_local(http_request: Request, payload: LocalImportRequest) -> LocalImportResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(workspace_service.import_local, payload)

    @application.post(
        "/api/imports/github",
        response_model=GitHubImportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def import_github(http_request: Request, payload: GitHubImportRequest) -> GitHubImportResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(workspace_service.import_github, payload)

    @application.get(
        "/api/imports/{attempt_id}",
        response_model=ImportAttemptResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_import_status(attempt_id: str) -> ImportAttemptResponse:
        return await run_in_threadpool(workspace_service.get_import_status, attempt_id)

    @application.post(
        "/api/imports/{attempt_id}/retry",
        response_model=LocalImportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def retry_import(http_request: Request, attempt_id: str) -> LocalImportResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(workspace_service.retry_import, attempt_id)

    @application.post(
        "/api/imports/{attempt_id}/retry-github",
        response_model=GitHubImportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def retry_github_import(http_request: Request, attempt_id: str) -> GitHubImportResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(workspace_service.retry_github_import, attempt_id)

    @application.post(
        "/api/imports/{attempt_id}/cleanup",
        response_model=GitHubImportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def retry_import_cleanup(http_request: Request, attempt_id: str) -> GitHubImportResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(workspace_service.retry_import_cleanup, attempt_id)

    @application.post(
        "/api/projects",
        response_model=Project,
        responses=_API_ERROR_RESPONSES,
    )
    async def create_project(request: CreateProjectRequest) -> Project:
        return await run_in_threadpool(workspace_service.create_project, request)

    @application.get(
        "/api/projects",
        response_model=ProjectListResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_projects() -> ProjectListResponse:
        return await run_in_threadpool(workspace_service.list_projects)

    @application.post(
        "/api/projects/{project_id}/sessions",
        response_model=SessionResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def create_session(project_id: str, request: CreateSessionRequest) -> SessionResponse:
        return await run_in_threadpool(workspace_service.create_session, project_id, request)

    @application.get(
        "/api/projects/{project_id}/sessions",
        response_model=SessionListResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_sessions(
        project_id: str,
        limit: int = Query(default=40, ge=1, le=40),
        cursor: str | None = Query(default=None, max_length=500),
    ) -> SessionListResponse:
        return await run_in_threadpool(
            workspace_service.list_sessions,
            project_id,
            limit=limit,
            cursor=cursor,
        )

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}",
        response_model=SessionResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_session(project_id: str, session_id: str) -> SessionResponse:
        return await run_in_threadpool(workspace_service.get_session, project_id, session_id)

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_session_messages(
        project_id: str,
        session_id: str,
        limit: int = Query(default=100, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=500),
    ) -> SessionMessagesResponse:
        return await run_in_threadpool(
            workspace_service.list_session_messages,
            project_id,
            session_id,
            limit=limit,
            cursor=cursor,
        )

    @application.post(
        "/api/projects/{project_id}/sessions/{session_id}/messages",
        response_model=SessionMessageResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def append_session_message(
        project_id: str,
        session_id: str,
        request: AppendSessionMessageRequest,
    ) -> SessionMessageResponse:
        message = await run_in_threadpool(
            workspace_service.append_session_message,
            project_id,
            session_id,
            request,
        )
        return SessionMessageResponse(message=message)

    @application.post(
        "/api/projects/{project_id}/sessions/{session_id}/runs",
        response_model=CreateRunResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def create_run(
        project_id: str,
        session_id: str,
        request: CreateRunRequest,
    ) -> CreateRunResponse:
        return await run_in_threadpool(workspace_service.create_run, project_id, session_id, request)

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs",
        response_model=RunListResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_runs(
        project_id: str,
        session_id: str,
        limit: int = Query(default=40, ge=1, le=40),
        cursor: str | None = Query(default=None, max_length=500),
    ) -> RunListResponse:
        return await run_in_threadpool(
            workspace_service.list_runs,
            project_id,
            session_id,
            limit=limit,
            cursor=cursor,
        )

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
        response_model=RunStatusResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_run(project_id: str, session_id: str, run_id: str) -> RunStatusResponse:
        return await run_in_threadpool(workspace_service.get_run, project_id, session_id, run_id)

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
        response_model=RunEventsResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_run_events(
        project_id: str,
        session_id: str,
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> RunEventsResponse:
        return await run_in_threadpool(
            workspace_service.list_run_events,
            project_id,
            session_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events/stream",
        responses=_API_ERROR_RESPONSES,
    )
    async def stream_run_events(
        project_id: str,
        session_id: str,
        run_id: str,
        after_sequence: int | None = Query(default=None, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID", max_length=500),
    ) -> StreamingResponse:
        try:
            cursor = resolve_sse_cursor(
                run_id,
                after_sequence=after_sequence,
                last_event_id=last_event_id,
            )
        except SseCursorError as exc:
            raise ServiceError("invalid_input", 400, str(exc)) from exc

        run_response = await run_in_threadpool(workspace_service.get_run, project_id, session_id, run_id)
        latest_sequence = await run_in_threadpool(
            workspace_service.latest_run_event_sequence,
            project_id,
            session_id,
            run_id,
        )
        if cursor > latest_sequence:
            raise ServiceError(
                "invalid_input",
                400,
                "SSE cursor is ahead of the latest persisted Run event",
            )
        try:
            lease = run_event_streams.acquire(run_id)
        except SseCapacityError as exc:
            raise ServiceError("budget_exceeded", 429, str(exc)) from exc

        async def body() -> AsyncIterator[bytes]:
            terminal_statuses = {"succeeded", "failed", "cancelled", "interrupted"}
            current_cursor = cursor
            started_at = asyncio.get_running_loop().time()
            next_heartbeat_at = started_at + run_event_streams.config.idle_heartbeat_seconds
            try:
                while True:
                    now = asyncio.get_running_loop().time()
                    if now - started_at >= run_event_streams.config.max_lifetime_seconds:
                        yield encode_sse_comment("stream lifetime limit reached")
                        return

                    page = await run_in_threadpool(
                        workspace_service.list_run_events,
                        project_id,
                        session_id,
                        run_id,
                        after_sequence=current_cursor,
                        limit=run_event_streams.config.batch_size,
                    )
                    if page.events:
                        for event in page.events:
                            try:
                                frame = encode_sse_event(
                                    event,
                                    max_bytes=run_event_streams.config.max_event_bytes,
                                )
                            except ValueError:
                                yield encode_sse_comment("event frame exceeds the configured byte limit")
                                return
                            yield frame
                            current_cursor = event.sequence
                            if event.type in {
                                "run.completed",
                                "run.failed",
                                "run.cancelled",
                                "run.interrupted",
                            }:
                                return
                        if page.has_more:
                            continue
                        next_heartbeat_at = asyncio.get_running_loop().time() + run_event_streams.config.idle_heartbeat_seconds
                    elif run_response.run.status in terminal_statuses:
                        try:
                            yield encode_sse_stream_end(
                                run_id=run_id,
                                sequence=latest_sequence,
                                status=run_response.run.status,
                                max_bytes=run_event_streams.config.max_event_bytes,
                            )
                        except ValueError:
                            yield encode_sse_comment("stream end frame exceeds the configured byte limit")
                        return

                    now = asyncio.get_running_loop().time()
                    if now >= next_heartbeat_at:
                        yield encode_sse_comment("keep-alive")
                        next_heartbeat_at = now + run_event_streams.config.idle_heartbeat_seconds
                    await asyncio.sleep(run_event_streams.config.poll_interval_seconds)
            finally:
                lease.release()

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/tool-results",
        response_model=RunToolResultsResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_run_tool_results(
        project_id: str,
        session_id: str,
        run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=40, ge=1, le=40),
    ) -> RunToolResultsResponse:
        return await run_in_threadpool(
            workspace_service.list_run_tool_results,
            project_id,
            session_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
        response_model=ReportResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_run_report(project_id: str, session_id: str, run_id: str) -> ReportResponse:
        return await run_in_threadpool(workspace_service.get_run_report, project_id, session_id, run_id)

    @application.get(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact",
        response_model=RunImpactResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_run_impact(
        project_id: str,
        session_id: str,
        run_id: str,
        candidate_id: str | None = Query(default=None, max_length=200),
        finding_id: str | None = Query(default=None, max_length=200),
        direction: ImpactDirection = Query(default="callers"),
        depth: int = Query(default=2, ge=1, le=2),
        limit: int = Query(default=40, ge=1, le=40),
    ) -> RunImpactResponse:
        return await run_in_threadpool(
            workspace_service.get_run_impact,
            project_id,
            session_id,
            run_id,
            candidate_id=candidate_id,
            finding_id=finding_id,
            direction=direction,
            depth=depth,
            limit=limit,
        )

    @application.post(
        "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel",
        response_model=CancelRunResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def cancel_run(
        project_id: str,
        session_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        return await run_in_threadpool(
            workspace_service.cancel_run,
            project_id,
            session_id,
            run_id,
            request,
        )

    @application.get(
        "/api/projects/{project_id}/indexes",
        response_model=ProjectIndexListResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_project_indexes(project_id: str) -> ProjectIndexListResponse:
        return await run_in_threadpool(workspace_service.list_project_indexes, project_id)

    @application.post(
        "/api/projects/{project_id}/indexes",
        response_model=IndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def create_project_index(
        project_id: str,
        request: CreateProjectIndexRequest,
    ) -> IndexResponse:
        return await run_in_threadpool(workspace_service.create_project_index, project_id, request)

    @application.post(
        "/api/projects/{project_id}/indexes/{index_id}/freshness",
        response_model=IndexFreshness,
        responses=_API_ERROR_RESPONSES,
    )
    async def check_project_index_freshness(project_id: str, index_id: str) -> IndexFreshness:
        return await run_in_threadpool(workspace_service.check_index_freshness, project_id, index_id)

    @application.post(
        "/api/projects/{project_id}/indexes/{index_id}/refresh",
        response_model=IndexRefreshResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def refresh_project_index(
        http_request: Request,
        project_id: str,
        index_id: str,
        request: RefreshProjectIndexRequest,
    ) -> IndexRefreshResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(
            workspace_service.refresh_project_index,
            project_id,
            index_id,
            request,
        )

    @application.post(
        "/api/projects/{project_id}/indexes/{index_id}/github-refresh",
        response_model=IndexRefreshResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def refresh_github_source(
        http_request: Request,
        project_id: str,
        index_id: str,
    ) -> IndexRefreshResponse:
        _require_local_write(http_request)
        return await run_in_threadpool(
            workspace_service.refresh_github_source,
            project_id,
            index_id,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}",
        response_model=IndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_index(project_id: str, index_id: str) -> IndexResponse:
        return await run_in_threadpool(workspace_service.get_project_index, project_id, index_id)

    @application.get(
        "/api/projects/{project_id}/features",
        response_model=FeatureGenerationListResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_project_features(
        project_id: str,
        index_id: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=20, ge=1, le=40),
        cursor: str | None = Query(default=None, max_length=500),
    ) -> FeatureGenerationListResponse:
        return await run_in_threadpool(
            workspace_service.list_feature_generations,
            project_id,
            index_id=index_id,
            limit=limit,
            cursor=cursor,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/overview",
        response_model=ProjectOverview,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_overview(
        project_id: str,
        index_id: str,
        max_depth: int = Query(default=4, ge=1, le=8),
        max_paths: int = Query(default=40, ge=1, le=40),
        max_modules: int = Query(default=100, ge=1, le=200),
        max_groups: int = Query(default=80, ge=1, le=200),
        max_module_relations: int = Query(default=200, ge=1, le=400),
    ) -> ProjectOverview:
        return await run_in_threadpool(
            workspace_service.get_overview,
            project_id,
            index_id,
            max_depth=max_depth,
            max_paths=max_paths,
            max_modules=max_modules,
            max_groups=max_groups,
            max_module_relations=max_module_relations,
        )

    @application.post(
        "/api/projects/{project_id}/indexes/{index_id}/features",
        response_model=FeatureGenerationResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def generate_project_features(
        project_id: str,
        index_id: str,
        request: FeatureGenerationRequest,
    ) -> FeatureGenerationResponse:
        return await run_in_threadpool(
            workspace_service.generate_features,
            project_id,
            index_id,
            request,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}/graph",
        response_model=FeatureGraphResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_feature_graph(
        project_id: str,
        index_id: str,
        generation_id: str,
        max_nodes: int = Query(default=40, ge=1, le=40),
        max_edges: int = Query(default=80, ge=1, le=120),
    ) -> FeatureGraphResponse:
        return await run_in_threadpool(
            workspace_service.get_feature_graph,
            project_id,
            index_id,
            generation_id,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}",
        response_model=FeatureGenerationResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_features(
        project_id: str,
        index_id: str,
        generation_id: str,
    ) -> FeatureGenerationResponse:
        return await run_in_threadpool(
            workspace_service.get_feature_generation,
            project_id,
            index_id,
            generation_id,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/search",
        response_model=SearchIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def search_project_index(
        project_id: str,
        index_id: str,
        query: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=40, ge=1, le=40),
    ) -> SearchIndexResponse:
        return await run_in_threadpool(workspace_service.search, project_id, index_id, query, limit)

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols",
        response_model=ModuleSymbolsPageResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def list_project_module_symbols(
        project_id: str,
        index_id: str,
        module_id: str,
        limit: int = Query(default=40, ge=1, le=40),
        cursor: str | None = Query(default=None, max_length=500),
    ) -> ModuleSymbolsPageResponse:
        return await run_in_threadpool(
            workspace_service.list_module_symbols,
            project_id,
            index_id,
            module_id,
            limit,
            cursor,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/neighbors",
        response_model=NeighborsIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_neighbors(
        project_id: str,
        index_id: str,
        symbol_id: str,
        direction: Literal["callers", "callees", "both"],
        depth: int = Query(default=2, ge=1, le=2),
        limit: int = Query(default=40, ge=1, le=40),
        edge_kinds: list[EdgeKind] | None = Query(default=None),
    ) -> NeighborsIndexResponse:
        return await run_in_threadpool(
            workspace_service.neighbors,
            project_id,
            index_id,
            symbol_id,
            direction,
            depth,
            limit,
            tuple(edge_kinds) if edge_kinds else None,
        )

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}/source",
        response_model=SourceIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def read_project_source(
        project_id: str,
        index_id: str,
        path: str = Query(min_length=1, max_length=500),
        start_line: int = Query(ge=1),
        end_line: int = Query(ge=1),
    ) -> SourceIndexResponse:
        return await run_in_threadpool(
            workspace_service.read_source,
            project_id,
            index_id,
            path,
            start_line,
            end_line,
        )

    @application.get(
        "/api/projects/{project_id}",
        response_model=Project,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project(project_id: str) -> Project:
        return await run_in_threadpool(workspace_service.get_project, project_id)

    @application.post(
        "/api/indexes",
        response_model=IndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def create_index(request: CreateIndexRequest) -> IndexResponse:
        return await run_in_threadpool(index_service.create_index, request)

    @application.get(
        "/api/indexes/{index_id}",
        response_model=IndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_index(index_id: str) -> IndexResponse:
        return await run_in_threadpool(index_service.get_index, index_id)

    @application.get(
        "/api/indexes/{index_id}/search",
        response_model=SearchIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def search_index(
        index_id: str,
        query: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=40, ge=1, le=40),
    ) -> SearchIndexResponse:
        return await run_in_threadpool(index_service.search, index_id, query, limit)

    @application.get(
        "/api/indexes/{index_id}/neighbors",
        response_model=NeighborsIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_neighbors(
        index_id: str,
        symbol_id: str,
        direction: Literal["callers", "callees", "both"],
        depth: int = Query(default=2, ge=1, le=2),
        limit: int = Query(default=40, ge=1, le=40),
        edge_kinds: list[EdgeKind] | None = Query(default=None),
    ) -> NeighborsIndexResponse:
        return await run_in_threadpool(
            index_service.neighbors,
            index_id,
            symbol_id,
            direction,
            depth,
            limit,
            tuple(edge_kinds) if edge_kinds else None,
        )

    @application.get(
        "/api/indexes/{index_id}/source",
        response_model=SourceIndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def read_source(
        index_id: str,
        path: str = Query(min_length=1, max_length=500),
        start_line: int = Query(ge=1),
        end_line: int = Query(ge=1),
    ) -> SourceIndexResponse:
        return await run_in_threadpool(
            index_service.read_source,
            index_id,
            path,
            start_line,
            end_line,
        )

    return application


def _require_local_write(request: Request) -> None:
    host = request.client.host if request.client is not None else None
    if not is_loopback_host(host):
        raise ServiceError("forbidden", 403, "local import writes require a loopback client")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ApiErrorResponse(code=code, message=message)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


app = create_app()
