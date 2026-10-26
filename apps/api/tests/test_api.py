"""HTTP and tool-service tests for the local index/query API."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import sqlite3
from threading import Event
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.contracts import (
    CreateIndexRequest,
    GetNeighborsParams,
    GetNeighborsSuccess,
    ReadSourceParams,
    ReadSourceSuccess,
    SearchCodeParams,
    SearchCodeSuccess,
    ToolRequest,
)
from app.main import _allowed_origins, create_app
from app.service import IndexService, RepositoryRegistry, ServiceError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


async def _request(
    application,
    method: str,
    target: str,
    payload: dict | None = None,
    *,
    client_host: str = "127.0.0.1",
    origin: str | None = None,
) -> tuple[int, dict]:
    parsed = urlsplit(target)
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    if origin is not None:
        headers.append((b"origin", origin.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": headers,
        "client": (client_host, 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await application(scope, receive, send)
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return response_start["status"], json.loads(response_body or b"{}")


def request(
    application,
    method: str,
    target: str,
    payload: dict | None = None,
    *,
    client_host: str = "127.0.0.1",
    origin: str | None = None,
) -> tuple[int, dict]:
    return asyncio.run(_request(application, method, target, payload, client_host=client_host, origin=origin))


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def copy_fixture_named(tmp_path: Path, name: str) -> Path:
    destination = tmp_path / name
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def create_request() -> dict:
    return {"repository_id": "timeout-demo", "revision": "fixture-v1", "dirty": False}


def test_model_config_settings_redact_secret_preserve_blank_and_remove_explicitly(tmp_path: Path, monkeypatch) -> None:
    application = create_app({}, data_root=tmp_path / "data")
    second_application = create_app({}, data_root=tmp_path / "data")
    config_path = tmp_path / "data" / "config.json"

    status, initial = request(application, "GET", "/api/model-config")
    assert status == 200
    assert initial["has_api_key"] is False
    assert "api_key" not in initial

    for invalid_settings in (
        {"base_url": "relative/path"},
        {"base_url": "ftp://model.example.test/v1"},
        {"base_url": "https://codetrail-url-secret:model-pass@model.example.test/v1"},
        {"provider": "   "},
        {"model": "\t "},
    ):
        status, invalid = request(
            application,
            "POST",
            "/api/model-config",
            {
                "schema_version": "0.1.0",
                "provider": "test-provider",
                "base_url": "https://model.example.test/v1",
                "model": "test-model",
                "api_key": "codetrail-fake-key",
                "remove_api_key": False,
                **invalid_settings,
            },
        )
        assert status == 422
        assert "codetrail-url-secret" not in json.dumps(invalid)
        assert "codetrail-fake-key" not in json.dumps(invalid)

    payload = {
        "schema_version": "0.1.0",
        "provider": "test-provider",
        "base_url": "https://model.example.test/v1",
        "model": "test-model",
        "api_key": "codetrail-fake-key",
        "remove_api_key": False,
    }
    status, saved = request(application, "POST", "/api/model-config", payload)
    assert status == 200
    assert saved["has_api_key"] is True
    assert "api_key" not in saved
    assert "codetrail-fake-key" not in json.dumps(saved)
    assert second_application.state.registry.model_config.api_key == "codetrail-fake-key"

    status, retained = request(
        application,
        "POST",
        "/api/model-config",
        {**payload, "provider": "updated-provider", "api_key": ""},
    )
    assert status == 200
    assert retained["provider"] == "updated-provider"
    assert retained["has_api_key"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == "codetrail-fake-key"

    before_failed_save = config_path.read_bytes()
    import app.config as config_module

    def fail_replace(_source, _target):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    status, failure = request(application, "POST", "/api/model-config", {**payload, "api_key": "new-fake-key"})
    assert status == 500
    assert "new-fake-key" not in json.dumps(failure)
    assert config_path.read_bytes() == before_failed_save
    assert not list(config_path.parent.glob(".config-*.tmp"))
    monkeypatch.undo()

    status, removed = request(
        application,
        "POST",
        "/api/model-config",
        {**payload, "api_key": "", "remove_api_key": True},
    )
    assert status == 200
    assert removed["has_api_key"] is False
    assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == ""


def test_model_config_write_requires_loopback_client(tmp_path: Path) -> None:
    application = create_app({}, data_root=tmp_path / "data")
    payload = {
        "schema_version": "0.1.0",
        "provider": "test-provider",
        "base_url": "https://model.example.test/v1",
        "model": "test-model",
        "api_key": "codetrail-fake-key",
        "remove_api_key": False,
    }

    status, response = request(application, "POST", "/api/model-config", payload, client_host="198.51.100.20")
    assert status == 403
    assert response["code"] == "forbidden"
    assert not (tmp_path / "data" / "config.json").exists()

    status, response = request(
        application,
        "POST",
        "/api/model-config",
        payload,
        origin="https://attacker.example",
    )
    assert status == 403
    assert response["code"] == "forbidden"
    assert not (tmp_path / "data" / "config.json").exists()

    status, response = request(
        application,
        "POST",
        "/api/model-config",
        payload,
        origin=_allowed_origins()[0],
    )
    assert status == 200
    assert response["has_api_key"] is True


def test_model_config_concurrent_blank_save_keeps_new_key(tmp_path: Path) -> None:
    registry = create_app({}, data_root=tmp_path / "data").state.registry
    first_builder_entered = Event()
    release_first_builder = Event()
    second_attempted = Event()
    second_builder_entered = Event()

    def save_key(current):
        first_builder_entered.set()
        assert release_first_builder.wait(3)
        return current.model_copy(update={"api_key": "codetrail-concurrent-key"})

    def save_blank_key_update():
        second_attempted.set()

        def update(current):
            second_builder_entered.set()
            return current.model_copy(update={"provider": "second-provider"})

        return registry.update_model_config(update)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_save = executor.submit(registry.update_model_config, save_key)
        assert first_builder_entered.wait(2)
        second_save = executor.submit(save_blank_key_update)
        assert second_attempted.wait(2)
        assert not second_builder_entered.wait(0.1)
        release_first_builder.set()
        first_save.result(timeout=3)
        second_save.result(timeout=3)

    assert registry.model_config.provider == "second-provider"
    assert registry.model_config.api_key == "codetrail-concurrent-key"


def file_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_http_index_query_and_snapshot_evidence_flow(tmp_path: Path) -> None:
    application = create_app({"timeout-demo": copy_fixture(tmp_path)})

    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    assert created["index"]["status"] == "ready"
    assert created["issues"] == []
    assert created["unresolved_truncated"] is False
    assert created["unresolved_truncation_reason"] is None

    status, repeated = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    assert repeated == created

    status, loaded = request(application, "GET", f"/api/indexes/{index_id}")
    assert status == 200
    assert loaded == created

    status, search = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/search?query=load_settings&limit=4",
    )
    assert status == 200
    assert any(
        match["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
        for match in search["matches"]
    )
    assert search["index_id"] == index_id

    status, source_search = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/search?query=TIMEOUT_DEMO_TIMEOUT&limit=4",
    )
    assert status == 200
    assert source_search["matches"]

    load_match = next(
        match
        for match in search["matches"]
        if match["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
    )
    symbol_id = load_match["symbol"]["symbol_id"]
    status, neighbors = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/neighbors?symbol_id={symbol_id}&direction=callers&depth=1&limit=40&edge_kinds=calls",
    )
    assert status == 200
    assert neighbors["index_truncated"] is False
    assert neighbors["index_truncation_reason"] is None
    assert neighbors["index_unresolved_truncated"] is False
    assert neighbors["index_unresolved_truncation_reason"] is None
    assert any(
        neighbor["symbol"]["qualified_name"] == "timeout_demo.app.start"
        for neighbor in neighbors["neighbors"]
    )

    status, source = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/source?path=timeout_demo%2Fconfig.py&start_line=12&end_line=14",
    )
    assert status == 200
    assert "load_settings" in source["evidence"]["excerpt"]
    assert source["evidence"]["index_id"] == index_id

    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    legacy_project = next(
        project for project in projects["projects"] if project["repository_id"] == "timeout-demo"
    )
    assert legacy_project["latest_index_id"] == index_id
    assert index_id in legacy_project["available_index_ids"]
    status, project_indexes = request(
        application,
        "GET",
        f"/api/projects/{legacy_project['project_id']}/indexes",
    )
    assert status == 200
    assert [item["index_id"] for item in project_indexes["indexes"]] == [index_id]

    status, dispatch_search = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/search?query=dispatch&limit=4",
    )
    assert status == 200
    dispatch_match = next(
        match
        for match in dispatch_search["matches"]
        if match["symbol"]["qualified_name"] == "timeout_demo.dispatch.dispatch"
    )
    dispatch_id = dispatch_match["symbol"]["symbol_id"]
    status, dispatch_graph = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/neighbors?symbol_id={dispatch_id}&direction=callees&depth=2&limit=40&edge_kinds=calls",
    )
    assert status == 200
    assert dispatch_graph["root"]["symbol_id"] == dispatch_id
    assert any(item["reason"] == "dynamic_call_target" for item in dispatch_graph["unresolved"])
    assert all(
        item["symbol"]["qualified_name"]
        not in {"timeout_demo.handlers.handle", "timeout_demo.other_handlers.handle"}
        for item in dispatch_graph["neighbors"]
    )


def test_project_workspace_isolated_and_restores_after_service_reopen(tmp_path: Path) -> None:
    repository_one = copy_fixture_named(tmp_path, "project-one")
    repository_two = copy_fixture_named(tmp_path, "project-two")
    config_two = repository_two / "timeout_demo" / "config.py"
    config_two.write_text(
        config_two.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        encoding="utf-8",
    )
    roots = {"repo-one": repository_one, "repo-two": repository_two}
    application = create_app(roots)

    status, empty = request(application, "GET", "/api/projects")
    assert status == 200
    assert empty["projects"] == []

    status, project_one = request(
        application,
        "POST",
        "/api/projects",
        {"name": "First fixture", "repository_id": "repo-one"},
    )
    assert status == 200
    status, project_two = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Second fixture", "repository_id": "repo-two"},
    )
    assert status == 200
    assert project_one["project_id"] != project_two["project_id"]

    status, index_one = request(
        application,
        "POST",
        f"/api/projects/{project_one['project_id']}/indexes",
        {"revision": "project-one", "dirty": False},
    )
    assert status == 200
    status, index_two = request(
        application,
        "POST",
        f"/api/projects/{project_two['project_id']}/indexes",
        {"revision": "project-two", "dirty": False},
    )
    assert status == 200
    index_one_id = index_one["index"]["index_id"]
    index_two_id = index_two["index"]["index_id"]
    assert index_one_id != index_two_id

    status, source_one = request(
        application,
        "GET",
        f"/api/projects/{project_one['project_id']}/indexes/{index_one_id}/source?path=timeout_demo%2Fconfig.py&start_line=9&end_line=9",
    )
    assert status == 200
    status, source_two = request(
        application,
        "GET",
        f"/api/projects/{project_two['project_id']}/indexes/{index_two_id}/source?path=timeout_demo%2Fconfig.py&start_line=9&end_line=9",
    )
    assert status == 200
    assert source_one["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 30"
    assert source_two["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 99"

    status, wrong_project = request(
        application,
        "GET",
        f"/api/projects/{project_two['project_id']}/indexes/{index_one_id}",
    )
    assert status == 404
    assert wrong_project["code"] == "not_found"

    original_create = application.state.index_service.create_index

    def fail_create(*_args, **_kwargs):
        raise ServiceError("internal_error", 500, "injected index failure")

    application.state.index_service.create_index = fail_create
    try:
        status, failed = request(
            application,
            "POST",
            f"/api/projects/{project_one['project_id']}/indexes",
            {"revision": "failed-build", "dirty": True},
        )
    finally:
        application.state.index_service.create_index = original_create
    assert status == 500
    assert failed["code"] == "internal_error"

    status, project_after_failure = request(
        application,
        "GET",
        f"/api/projects/{project_one['project_id']}",
    )
    assert status == 200
    assert project_after_failure["latest_index_id"] == index_one_id
    assert project_after_failure["building_index_id"] is None

    reopened = create_app(roots)
    status, projects = request(reopened, "GET", "/api/projects")
    assert status == 200
    assert {project["project_id"] for project in projects["projects"]} == {
        project_one["project_id"],
        project_two["project_id"],
    }
    status, restored_indexes = request(
        reopened,
        "GET",
        f"/api/projects/{project_two['project_id']}/indexes",
    )
    assert status == 200
    assert restored_indexes["latest_index_id"] == index_two_id
    assert [item["index_id"] for item in restored_indexes["indexes"]] == [index_two_id]


def test_project_and_session_trash_restore_preserves_data_across_restart(tmp_path: Path) -> None:
    repository = copy_fixture_named(tmp_path, "trash-project")
    roots = {"trash-project": repository}
    data_root = tmp_path / "service-data"
    application = create_app(roots, data_root=data_root)

    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Recoverable project", "repository_id": "trash-project"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created_index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "trash-test", "dirty": False},
    )
    assert status == 200
    index_id = created_index["index"]["index_id"]
    status, created_session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "Keep this investigation", "index_id": index_id},
    )
    assert status == 200
    session_id = created_session["session"]["session_id"]
    status, other_project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Other project", "repository_id": "trash-project"},
    )
    assert status == 200
    status, appended = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Keep these messages"},
    )
    assert status == 200
    message_id = appended["message"]["message_id"]

    status, removed_session = request(
        application,
        "DELETE",
        f"/api/projects/{project_id}/sessions/{session_id}",
    )
    assert status == 200
    assert removed_session["session"]["session_id"] == session_id
    status, hidden_messages = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
    )
    assert status == 404
    status, trash = request(application, "GET", "/api/trash")
    assert status == 200
    assert [item["session_id"] for item in trash["sessions"]] == [session_id]
    assert trash["projects"] == []
    status, cross_project_restore = request(
        application,
        "POST",
        f"/api/projects/{other_project['project_id']}/sessions/{session_id}/restore",
    )
    assert status == 404

    status, restored_session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/restore",
    )
    assert status == 200
    status, repeated_restore = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/restore",
    )
    assert status == 200
    status, messages = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
    )
    assert status == 200
    assert [item["message_id"] for item in messages["messages"]] == [message_id]

    status, removed_session = request(
        application,
        "DELETE",
        f"/api/projects/{project_id}/sessions/{session_id}",
    )
    assert status == 200
    status, removed_project = request(application, "DELETE", f"/api/projects/{project_id}")
    assert status == 200
    status, hidden_project = request(application, "GET", f"/api/projects/{project_id}")
    assert status == 404
    status, hidden_index = request(application, "GET", f"/api/indexes/{index_id}")
    assert status == 404
    status, hidden_projects = request(application, "GET", "/api/projects")
    assert status == 200
    assert all(item["project_id"] != project_id for item in hidden_projects["projects"])
    status, trash = request(application, "GET", "/api/trash")
    assert status == 200
    assert [item["project_id"] for item in trash["projects"]] == [project_id]
    assert trash["sessions"] == []

    reopened = create_app(roots, data_root=data_root)
    status, still_hidden = request(reopened, "GET", "/api/projects")
    assert status == 200
    assert all(item["project_id"] != project_id for item in still_hidden["projects"])
    status, restored_project = request(reopened, "POST", f"/api/projects/{project_id}/restore")
    assert status == 200
    status, repeated_project_restore = request(reopened, "POST", f"/api/projects/{project_id}/restore")
    assert status == 200
    status, no_auto_restored_session = request(reopened, "GET", f"/api/projects/{project_id}/sessions")
    assert status == 200
    assert no_auto_restored_session["sessions"] == []
    status, restored_session = request(
        reopened,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/restore",
    )
    assert status == 200
    status, messages = request(
        reopened,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
    )
    assert status == 200
    assert [item["message_id"] for item in messages["messages"]] == [message_id]
    status, visible_index = request(reopened, "GET", f"/api/indexes/{index_id}")
    assert status == 200


def test_project_trash_blocks_active_update_and_managed_source_does_not_revive(tmp_path: Path) -> None:
    repository = copy_fixture_named(tmp_path, "trash-managed-source")
    data_root = tmp_path / "service-data"
    application = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, imported = request(
        application,
        "POST",
        "/api/imports/local",
        {"name": "Managed source", "path": str(repository), "revision": "trash-test", "dirty": False},
    )
    assert status == 200
    project_id = imported["project"]["project_id"]
    project = imported["project"]
    operation_id = "project-update-trash-test"
    application.state.registry.management_store.begin_project_update(
        operation_id=operation_id,
        project_id=project_id,
        repository_id=project["repository_id"],
        expected_latest_index_id=project["latest_index_id"],
        owner_pid=None,
    )
    status, conflict = request(application, "DELETE", f"/api/projects/{project_id}")
    assert status == 409
    assert conflict["code"] == "conflict"
    application.state.registry.management_store.abort_project_update(
        operation_id,
        error_code="cancelled",
        error_message="test cleanup",
    )

    status, removed = request(application, "DELETE", f"/api/projects/{project_id}")
    assert status == 200
    reopened = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, projects = request(reopened, "GET", "/api/projects")
    assert status == 200
    assert all(item["project_id"] != project_id for item in projects["projects"])
    status, retry_import = request(
        reopened,
        "POST",
        "/api/imports/local",
        {"name": "Managed source", "path": str(repository), "revision": "trash-again", "dirty": False},
    )
    assert status == 409
    assert retry_import["code"] == "conflict"


def test_legacy_project_trash_is_not_recreated_and_direct_index_write_is_blocked(tmp_path: Path) -> None:
    repository = copy_fixture_named(tmp_path, "trash-legacy-source")
    roots = {"legacy-source": repository}
    data_root = tmp_path / "service-data"
    application = create_app(roots, data_root=data_root)

    status, created = request(
        application,
        "POST",
        "/api/indexes",
        {"repository_id": "legacy-source", "revision": "legacy-v1", "dirty": False},
    )
    assert status == 200
    index_id = created["index"]["index_id"]
    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    legacy_project = next(item for item in projects["projects"] if item["repository_id"] == "legacy-source")

    status, removed = request(application, "DELETE", f"/api/projects/{legacy_project['project_id']}")
    assert status == 200
    status, hidden_projects = request(application, "GET", "/api/projects")
    assert status == 200
    assert all(item["project_id"] != legacy_project["project_id"] for item in hidden_projects["projects"])
    status, hidden_index = request(application, "GET", f"/api/indexes/{index_id}")
    assert status == 404
    status, blocked_write = request(
        application,
        "POST",
        "/api/indexes",
        {"repository_id": "legacy-source", "revision": "legacy-v2", "dirty": False},
    )
    assert status == 404

    reopened = create_app(roots, data_root=data_root)
    status, still_hidden = request(reopened, "GET", "/api/projects")
    assert status == 200
    assert all(item["project_id"] != legacy_project["project_id"] for item in still_hidden["projects"])
    status, trash = request(reopened, "GET", "/api/trash")
    assert status == 200
    assert [item["project_id"] for item in trash["projects"]] == [legacy_project["project_id"]]
    status, restored = request(reopened, "POST", f"/api/projects/{legacy_project['project_id']}/restore")
    assert status == 200
    status, visible_index = request(reopened, "GET", f"/api/indexes/{index_id}")
    assert status == 200


def test_local_import_is_bounded_persistent_idempotent_and_retryable(tmp_path: Path) -> None:
    source = copy_fixture_named(tmp_path, "local-source")
    data_root = tmp_path / "service-data"
    before_manifest = file_manifest(source)
    application = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    payload = {
        "name": "Imported timeout demo",
        "path": str(source),
        "revision": "local-v1",
        "dirty": False,
        "idempotency_key": "local-import-1",
    }

    status, imported = request(application, "POST", "/api/imports/local", payload)
    assert status == 200
    assert imported["attempt"]["status"] == "ready"
    project_id = imported["project"]["project_id"]
    first_index_id = imported["index"]["index"]["index_id"]
    first_attempt_id = imported["attempt"]["attempt_id"]
    assert file_manifest(source) == before_manifest
    assert not (source / ".codetrail").exists()
    assert (data_root / "registry.sqlite3").is_file()
    assert (data_root / "projects" / project_id / ".codetrail" / "index.sqlite3").is_file()

    # Idempotent re-import must resolve path identity after normalize. Case folding
    # is only path-equivalent on Windows; on case-sensitive filesystems use a
    # trailing-separator form that resolve() maps to the same directory.
    alternate_path = str(source).upper() if os.name == "nt" else f"{source}{os.sep}"
    status, repeated = request(
        application,
        "POST",
        "/api/imports/local",
        {**payload, "path": alternate_path},
    )
    assert status == 200
    assert repeated["project"]["project_id"] == project_id
    assert repeated["attempt"]["attempt_id"] == first_attempt_id
    assert repeated["index"]["index"]["index_id"] == first_index_id

    status, conflict = request(
        application,
        "POST",
        "/api/imports/local",
        {**payload, "name": "Changed name"},
    )
    assert status == 409
    assert conflict["code"] == "conflict"

    config_path = source / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        encoding="utf-8",
    )
    modified_manifest = file_manifest(source)
    status, reindexed = request(
        application,
        "POST",
        "/api/imports/local",
        {
            "name": "Ignored duplicate display name",
            "path": str(source),
            "revision": "local-v2",
            "dirty": True,
        },
    )
    assert status == 200
    assert reindexed["project"]["project_id"] == project_id
    assert reindexed["attempt"]["attempt_id"] != first_attempt_id
    assert reindexed["index"]["index"]["index_id"] != first_index_id
    assert file_manifest(source) == modified_manifest

    original_create = application.state.index_service.create_index

    def fail_create(*_args, **_kwargs):
        raise ServiceError("internal_error", 500, "injected import failure")

    application.state.index_service.create_index = fail_create
    try:
        status, failed = request(
            application,
            "POST",
            "/api/imports/local",
            {
                "name": "Imported timeout demo",
                "path": str(source),
                "revision": "local-v2",
                "dirty": True,
                "idempotency_key": "local-import-failure",
            },
        )
    finally:
        application.state.index_service.create_index = original_create
    assert status == 200
    assert failed["attempt"]["status"] == "failed"
    assert failed["attempt"]["error_code"] == "internal_error"
    status, after_failure = request(application, "GET", f"/api/projects/{project_id}")
    assert status == 200
    assert after_failure["latest_index_id"] == reindexed["index"]["index"]["index_id"]

    status, retried = request(application, "POST", f"/api/imports/{failed['attempt']['attempt_id']}/retry")
    assert status == 200
    assert retried["attempt"]["status"] == "ready", (
        retried["attempt"]["error_code"],
        retried["attempt"]["error_message"],
    )
    assert retried["index"]["index"]["index_id"] == reindexed["index"]["index"]["index_id"]

    outside = tmp_path.parent / f"codetrail-outside-{tmp_path.name}"
    outside.mkdir()
    status, boundary_error = request(
        application,
        "POST",
        "/api/imports/local",
        {"name": "Outside", "path": str(outside)},
    )
    assert status == 400
    assert boundary_error["code"] == "invalid_input"

    empty = tmp_path / "empty-source"
    empty.mkdir()
    (empty / "README.md").write_text("not Python", encoding="utf-8")
    status, unsupported = request(
        application,
        "POST",
        "/api/imports/local",
        {"name": "Empty source", "path": str(empty), "idempotency_key": "empty-source"},
    )
    assert status == 200
    assert unsupported["attempt"]["status"] == "failed"
    assert unsupported["attempt"]["error_code"] == "invalid_input"

    status, denied = request(
        application,
        "POST",
        "/api/imports/local",
        payload,
        client_host="10.0.0.4",
    )
    assert status == 403
    assert denied["code"] == "forbidden"

    link = tmp_path / "source-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    else:
        status, link_error = request(
            application,
            "POST",
            "/api/imports/local",
            {"name": "Linked source", "path": str(link)},
        )
        assert status == 400
        assert link_error["code"] == "invalid_input"

    reopened = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, projects = request(reopened, "GET", "/api/projects")
    assert status == 200
    assert project_id in {project["project_id"] for project in projects["projects"]}
    status, attempt_status = request(reopened, "GET", f"/api/imports/{first_attempt_id}")
    assert status == 200
    assert attempt_status["attempt"]["status"] == "ready"


def test_restart_marks_active_local_import_interrupted_without_losing_latest(tmp_path: Path) -> None:
    source = copy_fixture_named(tmp_path, "interrupted-source")
    data_root = tmp_path / "service-data"
    application = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, imported = request(
        application,
        "POST",
        "/api/imports/local",
        {"name": "Interrupted source", "path": str(source), "idempotency_key": "interrupted"},
    )
    assert status == 200
    attempt_id = imported["attempt"]["attempt_id"]
    project_id = imported["project"]["project_id"]
    latest_index_id = imported["index"]["index"]["index_id"]
    application.state.registry.management_store.update_attempt(
        attempt_id,
        status="indexing",
        phase="indexing",
    )

    reopened = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, attempt_status = request(reopened, "GET", f"/api/imports/{attempt_id}")
    assert status == 200
    assert attempt_status["attempt"]["status"] == "interrupted"
    assert attempt_status["attempt"]["error_code"] == "interrupted"
    assert attempt_status["latest_index"]["index"]["index_id"] == latest_index_id
    status, project_status = request(reopened, "GET", f"/api/projects/{project_id}")
    assert status == 200
    assert project_status["latest_index_id"] == latest_index_id


def test_concurrent_duplicate_local_import_uses_one_project_and_attempt(tmp_path: Path) -> None:
    source = copy_fixture_named(tmp_path, "concurrent-source")
    application = create_app({}, data_root=tmp_path / "service-data", allowed_import_roots=(tmp_path,))
    payload = {
        "name": "Concurrent source",
        "path": str(source),
        "revision": "concurrent-v1",
        "dirty": False,
        "idempotency_key": "concurrent-import",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: request(application, "POST", "/api/imports/local", payload),
                range(2),
            )
        )

    assert {status for status, _ in responses} == {200}
    assert len({body["project"]["project_id"] for _, body in responses}) == 1
    assert len({body["attempt"]["attempt_id"] for _, body in responses}) == 1


def test_http_errors_are_stable_and_do_not_expose_repository_paths(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]

    cases = [
        ("GET", "/api/indexes/not-found", None, 404, "not_found"),
        (
            "GET",
            f"/api/indexes/{index_id}/source?path=..%2Fsecret.py&start_line=1&end_line=1",
            None,
            400,
            "invalid_input",
        ),
        (
            "GET",
            f"/api/indexes/{index_id}/search?query=load_settings&limit=41",
            None,
            422,
            "invalid_input",
        ),
        (
            "GET",
            f"/api/indexes/{index_id}/neighbors?symbol_id=missing&direction=both&depth=1&limit=40",
            None,
            404,
            "not_found",
        ),
    ]
    for method, target, payload, expected_status, expected_code in cases:
        status, error = request(application, method, target, payload)
        assert status == expected_status
        assert error["code"] == expected_code
        assert str(repository) not in json.dumps(error)

    status, empty = request(application, "GET", f"/api/indexes/{index_id}/search?query=%40%40%40")
    assert status == 200
    assert empty["matches"] == []

    status, error = request(
        application,
        "POST",
        "/api/indexes",
        {"repository_id": "not-configured"},
    )
    assert status == 404
    assert error["code"] == "not_found"


def test_http_keeps_old_snapshot_when_the_configured_worktree_changes(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    status, old_index = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    old_index_id = old_index["index"]["index_id"]

    config_path = repository / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        encoding="utf-8",
    )
    status, new_index = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    new_index_id = new_index["index"]["index_id"]
    assert new_index_id != old_index_id

    old_status, old_source = request(
        application,
        "GET",
        f"/api/indexes/{old_index_id}/source?path=timeout_demo%2Fconfig.py&start_line=9&end_line=9",
    )
    new_status, new_source = request(
        application,
        "GET",
        f"/api/indexes/{new_index_id}/source?path=timeout_demo%2Fconfig.py&start_line=9&end_line=9",
    )
    assert old_status == new_status == 200
    assert old_source["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 30"
    assert new_source["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 99"
    assert old_source["evidence"]["index_id"] == old_index_id
    assert new_source["evidence"]["index_id"] == new_index_id


def test_tool_adapter_returns_contract_results_and_safe_failures(tmp_path: Path) -> None:
    registry = RepositoryRegistry({"timeout-demo": copy_fixture(tmp_path)})
    service = IndexService(registry)
    index = service.create_index(CreateIndexRequest(**create_request()))
    index_id = index.index.index_id

    search_request = ToolRequest(
        request_id="request-search",
        run_id="run-local",
        index_id=index_id,
        params=SearchCodeParams(query="timeout_demo.dispatch", limit=1),
    )
    search = service.execute_tool(search_request, completed_sequence=3)
    assert isinstance(search, SearchCodeSuccess)
    assert search.completed_sequence == 3
    assert search.truncated is True

    source_request = ToolRequest(
        request_id="request-source",
        run_id="run-local",
        index_id=index_id,
        params=ReadSourceParams(path="timeout_demo/config.py", start_line=12, end_line=14),
    )
    source = service.execute_tool(source_request, completed_sequence=4)
    assert isinstance(source, ReadSourceSuccess)
    assert "load_settings" in source.evidence.excerpt

    unknown_request = ToolRequest(
        request_id="request-unknown",
        run_id="run-local",
        index_id="missing-index",
        params=SearchCodeParams(query="handler", limit=1),
    )
    unknown = service.execute_tool(unknown_request, completed_sequence=5)
    assert unknown.kind == "search_code.failed"
    assert unknown.error.code == "not_found"
    assert str(tmp_path) not in unknown.error.message

    neighbors_request = ToolRequest(
        request_id="request-neighbors",
        run_id="run-local",
        index_id=index_id,
        params=GetNeighborsParams(
            symbol_id=next(
                match.symbol.symbol_id
                for match in search.matches
                if match.symbol.qualified_name == "timeout_demo.dispatch"
            ),
            direction="callees",
            depth=2,
            limit=40,
        ),
    )
    neighbors = service.execute_tool(neighbors_request, completed_sequence=6)
    assert isinstance(neighbors, GetNeighborsSuccess)
    assert neighbors.index_truncated is False
    assert neighbors.index_truncation_reason is None
    assert neighbors.index_unresolved_truncated is False
    assert neighbors.index_unresolved_truncation_reason is None
    assert {
        item.symbol.qualified_name for item in neighbors.neighbors
    }.isdisjoint({"timeout_demo.handlers.handle", "timeout_demo.other_handlers.handle"})


def test_index_capacity_is_bounded(tmp_path: Path) -> None:
    registry = RepositoryRegistry({"timeout-demo": copy_fixture(tmp_path)})
    service = IndexService(registry, max_concurrent_indexes=1)
    assert service._index_slots.acquire(blocking=False)
    try:
        with pytest.raises(ServiceError) as error:
            service.create_index(CreateIndexRequest(**create_request()))
        assert error.value.code == "budget_exceeded"
        assert error.value.status_code == 429
    finally:
        service._index_slots.release()


def test_http_sessions_are_isolated_snapshot_fixed_idempotent_and_reopenable(tmp_path: Path) -> None:
    repository_one = copy_fixture_named(tmp_path, "repo-one")
    repository_two = copy_fixture_named(tmp_path, "repo-two")
    data_root = tmp_path / "service-data"
    roots = {"repo-one": repository_one, "repo-two": repository_two}
    application = create_app(roots, data_root=data_root)

    projects: dict[str, dict] = {}
    indexes: dict[str, dict] = {}
    for repository_id in roots:
        status, project = request(
            application,
            "POST",
            "/api/projects",
            {"name": repository_id, "repository_id": repository_id},
        )
        assert status == 200
        projects[repository_id] = project
        status, index = request(
            application,
            "POST",
            f"/api/projects/{project['project_id']}/indexes",
            {"revision": "session-v1", "dirty": False},
        )
        assert status == 200
        indexes[repository_id] = index

    project_one_id = projects["repo-one"]["project_id"]
    project_two_id = projects["repo-two"]["project_id"]
    first_index_id = indexes["repo-one"]["index"]["index_id"]
    status, first = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "first session", "idempotency_key": "session-one"},
    )
    assert status == 200
    first_session = first["session"]
    assert first_session["project_id"] == project_one_id
    assert first_session["index"]["index_id"] == first_index_id

    # Simulate a row written before the request fingerprint began preserving
    # the caller's omitted-vs-explicit index choice.
    legacy_fingerprint = hashlib.sha256(
        json.dumps(
            ["create", project_one_id, first_index_id, None, "first session"],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(repository_one / ".codetrail" / "index.sqlite3") as connection:
        connection.execute(
            "UPDATE sessions SET request_fingerprint = ? WHERE session_id = ?",
            (legacy_fingerprint, first_session["session_id"]),
        )
        connection.commit()

    status, repeated = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "first session", "idempotency_key": "session-one"},
    )
    assert status == 200
    assert repeated == first

    def create_parallel_session(_: int) -> tuple[int, dict]:
        return request(
            application,
            "POST",
            f"/api/projects/{project_one_id}/sessions",
            {"topic": "parallel session", "idempotency_key": "parallel-session"},
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        parallel_sessions = list(executor.map(create_parallel_session, range(6)))
    assert all(status == 200 for status, _ in parallel_sessions)
    assert len({result["session"]["session_id"] for _, result in parallel_sessions}) == 1

    status, conflict = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "changed topic", "idempotency_key": "session-one"},
    )
    assert status == 409
    assert conflict["code"] == "conflict"

    status, second = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "second session", "idempotency_key": "session-two"},
    )
    assert status == 200
    second_session = second["session"]
    assert second_session["session_id"] != first_session["session_id"]

    first_message_path = f"/api/projects/{project_one_id}/sessions/{first_session['session_id']}/messages"
    status, message = request(
        application,
        "POST",
        first_message_path,
        {"source": "human", "content": "keep this message", "idempotency_key": "message-one"},
    )
    assert status == 200
    assert message["message"]["sequence"] == 1
    status, repeated_message = request(
        application,
        "POST",
        first_message_path,
        {"source": "human", "content": "keep this message", "idempotency_key": "message-one"},
    )
    assert status == 200
    assert repeated_message == message
    status, message_conflict = request(
        application,
        "POST",
        first_message_path,
        {"source": "human", "content": "different", "idempotency_key": "message-one"},
    )
    assert status == 409
    assert message_conflict["code"] == "conflict"

    def append_parallel(number: int) -> tuple[int, dict]:
        return request(
            application,
            "POST",
            first_message_path,
            {
                "source": "agent",
                "content": f"parallel message {number}",
                "idempotency_key": f"parallel-{number}",
            },
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        parallel_results = list(executor.map(append_parallel, range(6)))
    assert all(status == 200 for status, _ in parallel_results)
    assert sorted(result["message"]["sequence"] for _, result in parallel_results) == list(range(2, 8))

    status, first_page = request(
        application,
        "GET",
        f"/api/projects/{project_one_id}/sessions?limit=1",
    )
    assert status == 200
    assert first_page["has_more"] is True
    status, second_page = request(
        application,
        "GET",
        f"/api/projects/{project_one_id}/sessions?limit=1&cursor={first_page['next_cursor']}",
    )
    assert status == 200
    assert {item["session_id"] for item in first_page["sessions"] + second_page["sessions"]} == {
        first_session["session_id"],
        second_session["session_id"],
    }

    status, message_page = request(application, "GET", f"{first_message_path}?limit=2")
    assert status == 200
    assert [item["sequence"] for item in message_page["messages"]] == [1, 2]
    assert message_page["has_more"] is True
    status, next_message_page = request(
        application,
        "GET",
        f"{first_message_path}?limit=2&cursor={message_page['next_cursor']}",
    )
    assert status == 200
    assert [item["sequence"] for item in next_message_page["messages"]] == [3, 4]

    status, isolated = request(
        application,
        "GET",
        f"/api/projects/{project_one_id}/sessions/{second_session['session_id']}/messages",
    )
    assert status == 200
    assert isolated["messages"] == []
    status, cross_project = request(
        application,
        "GET",
        f"/api/projects/{project_two_id}/sessions/{first_session['session_id']}",
    )
    assert status == 404
    status, cross_project_write = request(
        application,
        "POST",
        f"/api/projects/{project_two_id}/sessions/{first_session['session_id']}/messages",
        {"source": "agent", "content": "cross project"},
    )
    assert status == 404

    config_path = repository_one / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 31"),
        encoding="utf-8",
    )
    status, new_index = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/indexes",
        {"revision": "session-v2", "dirty": False},
    )
    assert status == 200
    assert new_index["index"]["index_id"] != first_index_id
    status, fixed_retry = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "first session", "idempotency_key": "session-one"},
    )
    assert status == 200
    assert fixed_retry["session"]["session_id"] == first_session["session_id"]
    assert fixed_retry["session"]["index"]["index_id"] == first_index_id
    assert fixed_retry["session"]["message_count"] == 7

    status, explicit_old = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {
            "topic": "explicit old session",
            "index_id": first_index_id,
            "idempotency_key": "explicit-old-session",
        },
    )
    assert status == 200
    status, explicit_old_retry = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {
            "topic": "explicit old session",
            "index_id": first_index_id,
            "idempotency_key": "explicit-old-session",
        },
    )
    assert status == 200
    assert explicit_old_retry == explicit_old
    status, explicit_index_conflict = request(
        application,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {
            "topic": "explicit old session",
            "index_id": new_index["index"]["index_id"],
            "idempotency_key": "explicit-old-session",
        },
    )
    assert status == 409
    assert explicit_index_conflict["code"] == "conflict"
    status, fixed_session = request(
        application,
        "GET",
        f"/api/projects/{project_one_id}/sessions/{first_session['session_id']}",
    )
    assert status == 200
    assert fixed_session["session"]["index"]["index_id"] == first_index_id

    reopened = create_app(roots, data_root=data_root)
    status, reopened_session = request(
        reopened,
        "GET",
        f"/api/projects/{project_one_id}/sessions/{first_session['session_id']}",
    )
    assert status == 200
    assert reopened_session == fixed_session
    status, reopened_retry = request(
        reopened,
        "POST",
        f"/api/projects/{project_one_id}/sessions",
        {"topic": "first session", "idempotency_key": "session-one"},
    )
    assert status == 200
    assert reopened_retry["session"]["session_id"] == first_session["session_id"]
    assert reopened_retry["session"]["index"]["index_id"] == first_index_id
    status, reopened_messages = request(reopened, "GET", first_message_path)
    assert status == 200
    assert [item["sequence"] for item in reopened_messages["messages"]] == list(range(1, 8))
