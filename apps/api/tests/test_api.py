"""HTTP and tool-service tests for the local index/query API."""

from __future__ import annotations

import asyncio
import json
import shutil
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
from app.main import create_app
from app.service import IndexService, RepositoryRegistry, ServiceError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


async def _request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
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
        "client": ("testclient", 50000),
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


def request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    return asyncio.run(_request(application, method, target, payload))


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def create_request() -> dict:
    return {"repository_id": "timeout-demo", "revision": "fixture-v1", "dirty": False}


def test_http_index_query_and_snapshot_evidence_flow(tmp_path: Path) -> None:
    application = create_app({"timeout-demo": copy_fixture(tmp_path)})

    status, created = request(application, "POST", "/api/indexes", create_request())
    assert status == 200
    index_id = created["index"]["index_id"]
    assert created["index"]["status"] == "ready"
    assert created["issues"] == []

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
        params=SearchCodeParams(query="handler", limit=1),
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
