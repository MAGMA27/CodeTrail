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
from app.main import _allowed_origins, _run_with_disconnect, create_app
from app.service import IndexService, RepositoryRegistry, ServiceError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


def test_http_disconnect_cancels_indexing_operation() -> None:
    started = Event()

    class DisconnectingRequest:
        async def is_disconnected(self) -> bool:
            await asyncio.to_thread(started.wait, 1)
            return True

    def operation(*, cancel_event: Event) -> bool:
        started.set()
        cancel_event.wait(timeout=2)
        return cancel_event.is_set()

    assert asyncio.run(_run_with_disconnect(DisconnectingRequest(), operation)) is True


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
    response_complete = asyncio.Event()

    async def receive() -> dict:
        nonlocal received
        if received:
            await response_complete.wait()
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

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
    application = create_app({}, data_root=tmp_path / "data", real_run_enabled=False)
    second_application = create_app({}, data_root=tmp_path / "data")
    config_path = tmp_path / "data" / "config.json"

    status, initial = request(application, "GET", "/api/model-config")
    assert status == 200
    assert initial["has_api_key"] is False
    assert initial["real_run_enabled"] is False
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
    assert saved["real_run_enabled"] is False
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

    enabled_application = create_app({}, data_root=tmp_path / "enabled", real_run_enabled=True)
    status, enabled_settings = request(enabled_application, "GET", "/api/model-config")
    assert status == 200
    assert enabled_settings["real_run_enabled"] is True
    status, enabled_saved = request(enabled_application, "POST", "/api/model-config", payload)
    assert status == 200
    assert enabled_saved["real_run_enabled"] is True


def test_model_config_write_requires_loopback_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODETRAIL_TRUSTED_WRITE_NETWORKS", "")
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


def test_model_config_write_allows_only_explicit_trusted_networks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODETRAIL_TRUSTED_WRITE_NETWORKS", "172.30.0.0/24")
    application = create_app({}, data_root=tmp_path / "data")
    payload = {
        "schema_version": "0.1.0",
        "provider": "test-provider",
        "base_url": "https://model.example.test/v1",
        "model": "test-model",
        "api_key": "",
        "remove_api_key": False,
    }

    status, response = request(
        application,
        "POST",
        "/api/model-config",
        payload,
        client_host="172.30.0.1",
        origin="http://localhost:3000",
    )
    assert status == 200
    assert response["has_api_key"] is False

    status, response = request(
        application,
        "POST",
        "/api/model-config",
        payload,
        client_host="172.31.0.1",
        origin="http://localhost:3000",
    )
    assert status == 403
    assert response["code"] == "forbidden"


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
    assert {neighbor["depth"] for neighbor in neighbors["neighbors"]} == {1}
    assert neighbors["unresolved"] == []
    assert neighbors["completeness"]["status"] in {"complete", "possibly_incomplete"}
    assert neighbors["completeness"]["unresolved_outgoing_count"] is None
    assert len(neighbors["possible_unresolved_callers"]) <= min(
        10, neighbors["completeness"]["possible_unresolved_caller_count"]
    )

    status, source = request(
        application,
        "GET",
        f"/api/indexes/{index_id}/source?path=timeout_demo%2Fconfig.py&start_line=12&end_line=14",
    )
    assert status == 200
    assert "load_settings" in source["evidence"]["excerpt"]
    assert source["evidence"]["index_id"] == index_id
    for start_line, end_line, message in (
        (1, 300, "read_source supports at most 200 lines"),
        (50, 10, "start_line must be less than or equal to end_line"),
    ):
        status, error = request(
            application,
            "GET",
            f"/api/indexes/{index_id}/source?path=timeout_demo%2Fconfig.py&start_line={start_line}&end_line={end_line}",
        )
        assert status == 400
        assert error["code"] == "invalid_input"
        assert error["message"] == message

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


def test_http_errors_include_shared_details_only_when_available(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository}, data_root=tmp_path / "data")
    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    project_id = next(
        project["project_id"]
        for project in projects["projects"]
        if project["repository_id"] == "timeout-demo"
    )
    source_url = (
        f"/api/indexes/{index_id}/source?path=timeout_demo%2Fconfig.py"
        "&start_line={start}&end_line={end}"
    )
    line_count = len((repository / "timeout_demo" / "config.py").read_text(encoding="utf-8").splitlines())

    expected_details = {
        (1, 300): [{"field": "end_line", "problem": "range_too_long", "max": 200}],
        (50, 10): [{"field": "start_line", "problem": "start_after_end"}],
        (line_count + 1, line_count + 1): [
            {"field": "start_line", "problem": "beyond_end_of_file", "max": line_count}
        ],
    }
    for (start, end), details in expected_details.items():
        status, body = request(application, "GET", source_url.format(start=start, end=end))
        assert status == 400
        assert body["details"] == details

    status, invalid = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/search?limit=1000",
    )
    assert status == 422
    assert invalid["message"] == "request parameters are invalid"
    assert {item["field"]: item["problem"] for item in invalid["details"]} == {
        "query": "required",
        "limit": "too_large",
    }
    assert "pydantic" not in json.dumps(invalid).lower()

    status, no_details = request(application, "GET", "/api/projects/project-does-not-exist")
    assert status == 404
    assert "details" not in no_details
    assert json.dumps(no_details, separators=(",", ":")) == (
        '{"schema_version":"0.1.0","code":"not_found","message":"project was not found"}'
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
    failed_attempt = application.state.registry.management_store.find_attempt_by_idempotency(
        "local-import-failure"
    )
    assert failed_attempt is not None
    assert failed_attempt.status == "failed"
    assert failed_attempt.error_code == "internal_error"
    status, after_failure = request(application, "GET", f"/api/projects/{project_id}")
    assert status == 200
    assert after_failure["latest_index_id"] == reindexed["index"]["index"]["index_id"]

    status, retried = request(application, "POST", f"/api/imports/{failed_attempt.attempt_id}/retry")
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
    unsupported_attempt = application.state.registry.management_store.find_attempt_by_idempotency("empty-source")
    assert unsupported_attempt is not None
    assert unsupported_attempt.status == "failed"
    assert unsupported_attempt.error_code == "invalid_input"

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


def test_local_import_exposes_total_source_limit_failure_and_keeps_project_unindexed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "large-source"
    source.mkdir()
    source_bytes = b"value = '0123456789012345678901234567890123456789'\n"
    (source / "sample.py").write_bytes(source_bytes)
    limit = 16
    monkeypatch.setenv("CODETRAIL_INDEX_MAX_TOTAL_BYTES", str(limit))
    application = create_app({}, data_root=tmp_path / "service-data", allowed_import_roots=(tmp_path,))

    status, rejected = request(
        application,
        "POST",
        "/api/imports/local",
        {
            "name": "Over limit source",
            "path": str(source),
            "revision": "local-v1",
            "dirty": False,
            "idempotency_key": "local-import-over-byte-limit",
        },
    )

    assert status == 200
    assert rejected["attempt"]["status"] == "failed"
    assert rejected["attempt"]["error_code"] == "index_too_large"
    assert f"at least {len(source_bytes)} bytes" in rejected["attempt"]["error_message"]
    assert f"limit {limit} bytes" in rejected["attempt"]["error_message"]
    attempt = application.state.registry.management_store.find_attempt_by_idempotency(
        "local-import-over-byte-limit"
    )
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.error_code == "index_too_large"
    assert f"at least {len(source_bytes)} bytes" in (attempt.error_message or "")
    assert f"limit {limit} bytes" in (attempt.error_message or "")

    status, project = request(application, "GET", f"/api/projects/{attempt.project_id}")
    assert status == 200
    assert project["latest_index_id"] is None
    assert project["available_index_ids"] == []
    assert rejected["attempt"]["index_id"] is None
    status, indexes = request(application, "GET", f"/api/projects/{attempt.project_id}/indexes")
    assert status == 200
    assert indexes["indexes"] == []

    status, direct_rebuild_error = request(
        application,
        "POST",
        f"/api/projects/{attempt.project_id}/indexes",
        {"revision": "direct-rebuild", "dirty": False},
    )
    assert status == 413
    assert direct_rebuild_error["code"] == "index_too_large"
    assert f"at least {len(source_bytes)} bytes" in direct_rebuild_error["message"]
    assert f"limit {limit} bytes" in direct_rebuild_error["message"]
    status, project_after_rebuild = request(application, "GET", f"/api/projects/{attempt.project_id}")
    assert status == 200
    assert project_after_rebuild["latest_index_id"] is None


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


def test_project_search_summary_full_paging_and_legacy_http_shape(tmp_path: Path) -> None:
    application = create_app({"timeout-demo": copy_fixture(tmp_path)}, data_root=tmp_path / "data")
    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    project_id = next(item["project_id"] for item in projects["projects"] if item["repository_id"] == "timeout-demo")
    route = f"/api/projects/{project_id}/indexes/{index_id}/search"

    status, legacy = request(application, "GET", f"/api/indexes/{index_id}/search?query=timeout&limit=40")
    assert status == 200
    status, default_response = request(application, "GET", f"{route}?query=timeout")
    assert status == 200
    assert default_response == legacy

    service = application.state.workspace_service
    full = service.search(project_id, index_id, "timeout", 40, detail="full")
    summary = service.search(project_id, index_id, "timeout", 1, detail="summary")
    assert summary.matches
    assert [item.symbol.id for item in summary.matches] == [full.matches[0].symbol.symbol_id]
    assert summary.matches[0].symbol.id == full.matches[0].symbol.symbol_id
    assert summary.matches[0].symbol.name == full.matches[0].symbol.qualified_name
    for item in summary.matches:
        if item.symbol.kind in {"class", "function"}:
            assert item.snippet
            assert item.snippet.startswith(("def ", "class ", "async def "))
    status, http_summary = request(application, "GET", f"{route}?query=timeout&limit=1&detail=summary")
    assert status == 200 and http_summary["matches"]
    assert http_summary["matches"][0]["symbol"]["id"] == summary.matches[0].symbol.id
    assert all("snippet" not in item or item["snippet"] is not None for item in http_summary["matches"])
    pages = list(summary.matches)
    cursor = summary.page.next_cursor
    while cursor:
        page = service.search(project_id, index_id, "timeout", 1, detail="summary", cursor=cursor)
        pages.extend(page.matches)
        cursor = page.page.next_cursor
    assert [item.symbol.id for item in pages] == [item.symbol.symbol_id for item in full.matches]

    project = service._require_project_index(project_id, index_id)
    with service.registry.store_for_repository(project.repository_id) as store:
        module_id = store.connection.execute(
            "SELECT symbol_id FROM symbols WHERE index_id = ? AND kind = 'module' "
            "AND path = 'timeout_demo/dispatch.py'",
            (index_id,),
        ).fetchone()["symbol_id"]
    full_symbols = service.list_module_symbols(project_id, index_id, module_id, 1, None, detail="full")
    summary_symbols = service.list_module_symbols(project_id, index_id, module_id, 1, None, detail="summary")
    assert summary_symbols.symbols
    assert [item.symbol_id for item in full_symbols.symbols] == [item.id for item in summary_symbols.symbols]
    assert all(item.at.startswith(f"{full_symbols.module.path}:") for item in summary_symbols.symbols)
    status, http_summary_symbols = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols?limit=1&detail=summary",
    )
    assert status == 200 and http_summary_symbols["symbols"]
    assert http_summary_symbols["symbols"][0]["id"] == summary_symbols.symbols[0].id

    from app.service import ServiceError

    try:
        service.search(project_id, index_id, "timeout", 1, detail="summary", cursor="invalid")
    except ServiceError as exc:
        assert exc.message == "invalid or expired cursor"
        assert exc.details == [{"field": "cursor", "problem": "invalid"}]
    else:
        raise AssertionError("invalid search cursor was accepted")


def test_project_search_summary_supports_typescript_and_missing_snippets(tmp_path: Path, monkeypatch) -> None:
    repository = copy_fixture(tmp_path)
    (repository / "overview.tsx").write_text(
        'export function overview() { return "overview"; }\n',
        encoding="utf-8",
    )
    application = create_app({"timeout-demo": repository}, data_root=tmp_path / "data")
    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    project_id = next(
        item["project_id"] for item in projects["projects"]
        if item["repository_id"] == "timeout-demo"
    )
    service = application.state.workspace_service

    summary = service.search(project_id, index_id, "overview", 10, detail="summary")
    ts_match = next(item for item in summary.matches if item.symbol.name.endswith(".overview"))
    assert ts_match.symbol.kind == "function"
    assert ts_match.snippet == "export function overview() { return \"overview\"; }"

    monkeypatch.setattr("app.storage.IndexStore.search_definition_lines", lambda *_args: {})
    without_snippets = service.search(project_id, index_id, "overview", 10, detail="summary")
    ts_match_without_snippet = next(
        item for item in without_snippets.matches if item.symbol.name.endswith(".overview")
    )
    assert ts_match_without_snippet.snippet is None

    monkeypatch.setattr(
        "app.storage.IndexStore.search_definition_lines",
        lambda *_args: {ts_match.symbol.id: "export function\noverview()"},
    )
    with_unusual_snippet = service.search(project_id, index_id, "overview", 10, detail="summary")
    unusual_match = next(
        item for item in with_unusual_snippet.matches if item.symbol.name.endswith(".overview")
    )
    assert unusual_match.snippet is None

    from app.storage import StorageError

    def fail_definition_read(*_args):
        raise StorageError("definition read failed")

    monkeypatch.setattr("app.storage.IndexStore.search_definition_lines", fail_definition_read)
    with_read_failure = service.search(project_id, index_id, "overview", 10, detail="summary")
    failed_read_match = next(
        item for item in with_read_failure.matches if item.symbol.name.endswith(".overview")
    )
    assert failed_read_match.snippet is None


def test_project_neighbors_summary_and_unresolved_pages_share_mcp_service(tmp_path: Path) -> None:
    from app.mcp_server import create_mcp_server

    application = create_app({"timeout-demo": copy_fixture(tmp_path)}, data_root=tmp_path / "data")
    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    project_id = next(item["project_id"] for item in projects["projects"] if item["repository_id"] == "timeout-demo")
    status, found = request(application, "GET", f"/api/indexes/{index_id}/search?query=dispatch&limit=40")
    assert status == 200
    dispatch_id = next(
        item["symbol"]["symbol_id"] for item in found["matches"]
        if item["symbol"]["qualified_name"] == "timeout_demo.dispatch.dispatch"
    )
    neighbors_path = f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
    query = f"symbol_id={dispatch_id}&direction=callees&depth=1&limit=40&edge_kinds=calls"
    status, full = request(application, "GET", f"{neighbors_path}?{query}&detail=full")
    assert status == 200
    status, summary = request(application, "GET", f"{neighbors_path}?{query}&detail=summary")
    assert status == 200
    assert {item["symbol"]["symbol_id"] for item in full["neighbors"]} == {
        item["to"]["id"] for item in summary["neighbors"]
    }
    assert summary["unresolved_summary"]["total"] == len(full["unresolved"])
    assert summary["possible_unresolved_callers"]["total"] == full["completeness"]["possible_unresolved_caller_count"]
    assert "nodes" not in summary
    assert "list_unresolved" in summary["completeness"]["hint"]
    status, start_search = request(application, "GET", f"/api/indexes/{index_id}/search?query=start&limit=40")
    assert status == 200
    start_id = next(
        item["symbol"]["symbol_id"] for item in start_search["matches"]
        if item["symbol"]["qualified_name"] == "timeout_demo.app.start"
    )
    both_query = f"symbol_id={start_id}&direction=both&depth=1&limit=40&edge_kinds=calls"
    status, both_full = request(application, "GET", f"{neighbors_path}?{both_query}&detail=full")
    assert status == 200
    status, both_summary = request(application, "GET", f"{neighbors_path}?{both_query}&detail=summary")
    assert status == 200
    assert both_full["neighbors"] and both_summary["neighbors"]
    full_relations = {
        (
            item["symbol"]["symbol_id"], item["edge"]["kind"],
            "outgoing" if item["edge"]["source_id"] == start_id else "incoming",
        )
        for item in both_full["neighbors"]
    }
    assert {
        (item["to"]["id"], item["edge_kind"], item["orientation"])
        for item in both_summary["neighbors"]
    } == full_relations
    assert {item["orientation"] for item in both_summary["neighbors"]} == {"incoming", "outgoing"}
    service = application.state.workspace_service
    service_summary = service.neighbors(
        project_id, index_id, start_id, "both", 1, 40, ("calls",), detail="summary"
    )
    assert service_summary.neighbors
    assert all(hasattr(item, "to") for item in service_summary.neighbors)

    unresolved_path = f"/api/projects/{project_id}/indexes/{index_id}/unresolved"
    status, root_unresolved = request(
        application,
        "GET",
        f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing",
    )
    assert status == 200
    assert sum(summary["unresolved_summary"]["by_source"].values()) == summary["unresolved_summary"]["total"]
    assert summary["unresolved_summary"]["by_source"].get(dispatch_id, 0) == root_unresolved["page"]["total"]
    status, first = request(
        application, "GET", f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing&limit=1"
    )
    assert status == 200 and first["items"]
    assert first["detail"] == "summary"
    assert set(first["items"][0]) == {
        "id", "at", "expression", "reason", "source_symbol_id", "possible_target_count"
    }
    service_first = service.list_unresolved(
        project_id, index_id, symbol_id=dispatch_id, direction="outgoing", limit=1
    )
    assert service_first.items
    paged = list(first["items"])
    cursor = first["page"]["next_cursor"]
    while cursor:
        status, page = request(
            application,
            "GET",
            f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing&limit=1&cursor={cursor}",
        )
        assert status == 200
        paged.extend(page["items"])
        cursor = page["page"]["next_cursor"]
    assert [item["id"] for item in paged] == [
        item["unresolved_id"] for item in full["unresolved"]
    ]
    status, incoming = request(
        application, "GET", f"{unresolved_path}?symbol_id={dispatch_id}&direction=possible_incoming&limit=1"
    )
    assert status == 200
    assert incoming["page"]["total"] == full["completeness"]["possible_unresolved_caller_count"] or (
        incoming["page"]["total"] == 0 and full["completeness"]["possible_unresolved_caller_count"] is None
    )
    incoming_ids = [item["id"] for item in incoming["items"]]
    incoming_cursor = incoming["page"]["next_cursor"]
    while incoming_cursor:
        status, page = request(
            application,
            "GET",
            f"{unresolved_path}?symbol_id={dispatch_id}&direction=possible_incoming&limit=1&cursor={incoming_cursor}",
        )
        assert status == 200
        incoming_ids.extend(item["id"] for item in page["items"])
        incoming_cursor = page["page"]["next_cursor"]
    assert len(incoming_ids) == incoming["page"]["total"]

    status, invalid = request(application, "GET", f"{unresolved_path}?direction=outgoing")
    assert status == 400 and invalid["code"] == "invalid_input"
    status, invalid = request(
        application,
        "GET",
        f"{unresolved_path}?symbol_id={dispatch_id}&module_id={dispatch_id}&direction=outgoing",
    )
    assert status == 400 and invalid["code"] == "invalid_input"
    status, invalid = request(
        application,
        "GET",
        f"{unresolved_path}?module_id={dispatch_id}&direction=possible_incoming",
    )
    assert status == 400 and invalid["code"] == "invalid_input"
    status, invalid = request(
        application, "GET", f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing&cursor=invalid"
    )
    assert status == 400 and invalid["message"] == "invalid or expired cursor"
    if first["page"]["next_cursor"]:
        status, invalid = request(
            application,
            "GET",
            f"{unresolved_path}?symbol_id={dispatch_id}&direction=possible_incoming&cursor={first['page']['next_cursor']}",
        )
        assert status == 400 and invalid["message"] == "invalid or expired cursor"

    service = application.state.workspace_service
    project = service._require_project_index(project_id, index_id)
    with service.registry.store_for_repository(project.repository_id) as store:
        module_id = store.connection.execute(
            "SELECT symbol_id FROM symbols WHERE index_id = ? AND kind = 'module' "
            "AND path = 'timeout_demo/dispatch.py'",
            (index_id,),
        ).fetchone()["symbol_id"]
    module_page = service.list_unresolved(
        project_id, index_id, module_id=module_id, limit=1
    )
    assert module_page.page.next_cursor
    module_scope_cursor = service._encode_overview_cursor(
        project_id, index_id, "un", 0, f"outgoing:module:{module_id}", None
    )
    status, invalid = request(
        application,
        "GET",
        f"{unresolved_path}?symbol_id={module_id}&direction=outgoing&cursor={module_scope_cursor}",
    )
    assert status == 400 and invalid["message"] == "invalid or expired cursor"

    original_get_index = service.index_service.get_index
    for unresolved_coverage, expected_reason in (
        (None, "index_unresolved_coverage_unknown"),
        (True, "index_unresolved_truncated"),
    ):
        service.index_service.get_index = lambda item, coverage=unresolved_coverage: original_get_index(item).model_copy(
            update={"unresolved_truncated": coverage}
        )
        coverage_page = service.list_unresolved(
            project_id, index_id, symbol_id=dispatch_id, limit=1
        )
        assert expected_reason in coverage_page.completeness.reasons
        assert coverage_page.completeness.status in {"truncated", "possibly_incomplete"}
    service.index_service.get_index = original_get_index

    server = create_mcp_server(application.state.workspace_service)

    async def call_mcp(name: str, arguments: dict) -> dict:
        response = await server.call_tool(name, arguments)
        assert response.is_error is False
        return response.structured_content

    mcp_page = asyncio.run(call_mcp("list_unresolved", {
        "project_id": project_id,
        "index_id": index_id,
        "symbol_id": dispatch_id,
        "direction": "outgoing",
        "limit": 1,
    }))
    assert mcp_page["result"] == first
    if first["page"]["next_cursor"]:
        cursor = first["page"]["next_cursor"]
        status, http_page = request(
            application,
            "GET",
            f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing&limit=1&cursor={cursor}",
        )
        assert status == 200
        mcp_page = asyncio.run(call_mcp("list_unresolved", {
            "project_id": project_id,
            "index_id": index_id,
            "symbol_id": dispatch_id,
            "direction": "outgoing",
            "limit": 1,
            "cursor": cursor,
        }))
        assert mcp_page["result"] == http_page
        status, full_http_page = request(
            application,
            "GET",
            f"{unresolved_path}?symbol_id={dispatch_id}&direction=outgoing&limit=1&cursor={cursor}&detail=full",
        )
        assert status == 200 and full_http_page["detail"] == "full"
        assert full_http_page["page"] == http_page["page"]
    mcp_summary = asyncio.run(call_mcp("get_neighbors", {
        "project_id": project_id,
        "index_id": index_id,
        "symbol_id": dispatch_id,
        "direction": "callees",
        "depth": 1,
        "limit": 40,
        "edge_kinds": ["calls"],
    }))
    assert mcp_summary["result"] == summary


def test_neighbor_summary_expands_non_root_unresolved_sources(tmp_path: Path) -> None:
    from app.mcp_server import create_mcp_server

    repository = tmp_path / "nested-unresolved"
    repository.mkdir()
    (repository / "flow.py").write_text(
        "def root() -> None:\n"
        "    middle()\n"
        "def middle() -> None:\n"
        "    unresolved_call()\n"
        "    unresolved_call_two()\n",
        encoding="utf-8",
    )
    application = create_app({"nested-unresolved": repository}, data_root=tmp_path / "data")
    status, created_project = request(
        application, "POST", "/api/projects", {"name": "Nested unresolved", "repository_id": "nested-unresolved"}
    )
    assert status == 200
    project_id = created_project["project_id"]
    status, created_index = request(
        application, "POST", f"/api/projects/{project_id}/indexes", {"revision": "nested-v1", "dirty": False}
    )
    assert status == 200
    index_id = created_index["index"]["index_id"]
    service = application.state.workspace_service
    root = next(
        item.symbol for item in service.search(project_id, index_id, "root", 10).matches
        if item.symbol.qualified_name == "flow.root"
    )
    full = service.neighbors(project_id, index_id, root.symbol_id, "both", 2, 40, ("calls",), detail="full")
    summary = service.neighbors(project_id, index_id, root.symbol_id, "both", 2, 40, ("calls",), detail="summary")
    assert not full.truncated
    assert full.unresolved and summary.unresolved_summary is not None
    by_source = summary.unresolved_summary.by_source
    assert len(by_source) == 1
    assert next(iter(by_source)) != root.symbol_id
    assert sum(by_source.values()) == summary.unresolved_summary.total == len(full.unresolved)
    assert "for each source in unresolved_summary.by_source" in (summary.completeness.hint or "")
    for source_id, expected_count in by_source.items():
        page = service.list_unresolved(project_id, index_id, symbol_id=source_id, direction="outgoing")
        assert page.page.total == expected_count
    root_page = service.list_unresolved(project_id, index_id, symbol_id=root.symbol_id, direction="outgoing")
    assert root_page.page.total == by_source.get(root.symbol_id, 0)

    first = service.list_unresolved(project_id, index_id, symbol_id=next(iter(by_source)), limit=1)
    assert first.detail == "summary" and first.page.next_cursor
    full_page = service.list_unresolved(
        project_id, index_id, symbol_id=next(iter(by_source)), limit=1,
        cursor=first.page.next_cursor, detail="full",
    )
    summary_page = service.list_unresolved(
        project_id, index_id, symbol_id=next(iter(by_source)), limit=1,
        cursor=first.page.next_cursor, detail="summary",
    )
    assert full_page.detail == "full"
    assert full_page.page == summary_page.page

    response = service.search(project_id, index_id, "root", 10, detail="summary")
    assert response.matches[0].snippet.startswith("def root")


def test_neighbor_summary_marks_recursive_self_edge_and_keeps_completeness_hint(tmp_path: Path) -> None:
    repository = tmp_path / "self-edge"
    repository.mkdir()
    (repository / "recursive.py").write_text(
        "def recurse() -> None:\n"
        "    recurse()\n"
        "    dynamic_call()\n",
        encoding="utf-8",
    )
    application = create_app({"self-edge": repository}, data_root=tmp_path / "data")
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Self edge", "repository_id": "self-edge"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "self-edge-v1", "dirty": False},
    )
    assert status == 200
    index_id = created["index"]["index_id"]
    service = application.state.workspace_service
    match = next(
        item for item in service.search(project_id, index_id, "recurse", 40).matches
        if item.symbol.qualified_name == "recursive.recurse"
    )
    full = service.neighbors(
        project_id, index_id, match.symbol.symbol_id, "callees", 1, 40, ("calls",), detail="full"
    )
    summary = service.neighbors(
        project_id, index_id, match.symbol.symbol_id, "callees", 1, 40, ("calls",), detail="summary"
    )
    assert full.completeness is not None and summary.completeness is not None
    assert summary.completeness.hint is not None
    assert full.completeness.hint is None or full.completeness.hint in summary.completeness.hint
    assert len(summary.neighbors) == 1
    assert summary.neighbors[0].orientation == "self"
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
