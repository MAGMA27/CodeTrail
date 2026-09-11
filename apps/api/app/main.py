"""Minimal CodeTrail API application."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .contracts import (
    ApiErrorResponse,
    CreateIndexRequest,
    CreateProjectIndexRequest,
    CreateProjectRequest,
    EdgeKind,
    ImportAttemptResponse,
    IndexResponse,
    LocalImportRequest,
    LocalImportResponse,
    NeighborsIndexResponse,
    Project,
    ProjectIndexListResponse,
    ProjectListResponse,
    SearchIndexResponse,
    SourceIndexResponse,
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
) -> FastAPI:
    """Create an API with a server-owned repository allow-list."""

    registry = (
        RepositoryRegistry.from_environment()
        if repository_roots is None
        else RepositoryRegistry(
            repository_roots,
            data_root=data_root,
            allowed_import_roots=allowed_import_roots,
        )
    )
    index_service = IndexService(registry)
    workspace_service = WorkspaceService(registry, index_service)
    application = FastAPI(title="CodeTrail API", version="0.1.0")
    application.state.index_service = index_service
    application.state.registry = registry
    application.state.workspace_service = workspace_service
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

    @application.get(
        "/api/projects/{project_id}/indexes/{index_id}",
        response_model=IndexResponse,
        responses=_API_ERROR_RESPONSES,
    )
    async def get_project_index(project_id: str, index_id: str) -> IndexResponse:
        return await run_in_threadpool(workspace_service.get_project_index, project_id, index_id)

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
