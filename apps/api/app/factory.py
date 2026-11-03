"""Shared construction for API and durable job worker services."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import ModelProviderConfig
from .contracts import ApiErrorResponse
from .service import IndexService, RepositoryRegistry, WorkspaceService
from .storage import StorageError

if TYPE_CHECKING:
    from .feature_generation import FeatureModelAdapter
    from .github import GitHubClient
    from .issue_analysis import IssueModelAdapter


def create_services(
    repository_roots: Mapping[str, str | Path] | None = None,
    *,
    data_root: str | Path | None = None,
    allowed_import_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
    github_import_enabled: bool | None = None,
    mcp_github_import_enabled: bool | None = None,
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
    model_config: ModelProviderConfig | None = None,
) -> WorkspaceService:
    """Build registry and services from the same settings used by the API.

    Environment-backed settings (data directory, import roots, feature flags,
    GitHub credentials/timeouts and TypeScript compiler) are read as the
    registry, GitHub client and index service are constructed.
    """

    from .github import GitHubClient

    registry = (
        RepositoryRegistry.from_environment(
            github_import_enabled=github_import_enabled,
            mcp_github_import_enabled=mcp_github_import_enabled,
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
    return WorkspaceService(
        registry,
        index_service,
        github_client=resolved_github_client,
        feature_adapter_factory=feature_adapter_factory,
        run_model_adapter_factory=run_model_adapter_factory,
        run_max_concurrent_runs=run_max_concurrent_runs,
    )


def register_storage_error_handler(application: FastAPI) -> None:
    """Keep raw storage failures as safe API errors at the composition boundary."""

    @application.exception_handler(StorageError)
    async def handle_storage_error(_request: Request, _exc: StorageError) -> JSONResponse:
        payload = ApiErrorResponse(
            code="internal_error",
            message="local index storage failed",
        )
        return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))
