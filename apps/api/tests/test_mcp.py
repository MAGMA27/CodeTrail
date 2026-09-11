"""Protocol-level tests for the local read-only MCP adapter."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from app.main import create_app


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"
API_ROOT = ROOT / "apps" / "api"


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


def copy_fixture(tmp_path: Path, *, name: str = "timeout-demo", default_timeout: int = 30) -> Path:
    destination = tmp_path / name
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    if default_timeout != 30:
        config_path = destination / "timeout_demo" / "config.py"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "DEFAULT_TIMEOUT = 30",
                f"DEFAULT_TIMEOUT = {default_timeout}",
            ),
            encoding="utf-8",
        )
    return destination


def test_real_stdio_client_matches_http_and_returns_evidence_link(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    roots = {"timeout-demo": repository}
    application = create_app(roots)

    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "MCP fixture", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "mcp-fixture", "dirty": False},
    )
    assert status == 200
    index_id = created["index"]["index_id"]

    status, http_project = request(application, "GET", f"/api/projects/{project_id}")
    assert status == 200
    status, http_indexes = request(application, "GET", f"/api/projects/{project_id}/indexes")
    assert status == 200
    status, http_index = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}")
    assert status == 200
    status, http_search = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/search?query=load_settings&limit=4",
    )
    assert status == 200
    load_match = next(
        match
        for match in http_search["matches"]
        if match["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
    )
    symbol_id = load_match["symbol"]["symbol_id"]
    status, http_neighbors = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={symbol_id}&direction=callers&depth=1&limit=40&edge_kinds=calls",
    )
    assert status == 200
    status, http_source = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/source"
        "?path=timeout_demo%2Fconfig.py&start_line=12&end_line=14",
    )
    assert status == 200

    environment = {
        "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
        "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env=environment,
        cwd=API_ROOT,
    )
    asyncio.run(
        _exercise_stdio(
            parameters,
            project_id=project_id,
            index_id=index_id,
            symbol_id=symbol_id,
            http_project=http_project,
            http_indexes=http_indexes,
            http_index=http_index,
            http_search=http_search,
            http_neighbors=http_neighbors,
            http_source=http_source,
        )
    )


def test_real_stdio_client_scopes_two_projects_with_same_named_symbols(tmp_path: Path) -> None:
    repository_one = copy_fixture(tmp_path, name="repo-one", default_timeout=30)
    repository_two = copy_fixture(tmp_path, name="repo-two", default_timeout=99)
    application = create_app({"repo-one": repository_one, "repo-two": repository_two})

    project_ids: dict[str, str] = {}
    index_ids: dict[str, str] = {}
    http_searches: dict[str, dict] = {}
    http_sources: dict[str, dict] = {}
    for repository_id in ("repo-one", "repo-two"):
        status, project = request(
            application,
            "POST",
            "/api/projects",
            {"name": f"Project {repository_id}", "repository_id": repository_id},
        )
        assert status == 200
        project_ids[repository_id] = project["project_id"]
        status, created = request(
            application,
            "POST",
            f"/api/projects/{project['project_id']}/indexes",
            {"revision": repository_id, "dirty": False},
        )
        assert status == 200
        index_ids[repository_id] = created["index"]["index_id"]
        status, http_searches[repository_id] = request(
            application,
            "GET",
            f"/api/projects/{project['project_id']}/indexes/{created['index']['index_id']}"
            "/search?query=DEFAULT_TIMEOUT&limit=4",
        )
        assert status == 200
        status, http_sources[repository_id] = request(
            application,
            "GET",
            f"/api/projects/{project['project_id']}/indexes/{created['index']['index_id']}"
            "/source?path=timeout_demo%2Fconfig.py&start_line=9&end_line=9",
        )
        assert status == 200

    assert http_sources["repo-one"]["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 30"
    assert http_sources["repo-two"]["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 99"
    assert http_sources["repo-one"]["evidence"]["content_hash"] != http_sources["repo-two"]["evidence"]["content_hash"]

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env={
            "CODETRAIL_REPOSITORIES": json.dumps(
                {"repo-one": str(repository_one), "repo-two": str(repository_two)}
            ),
            "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
        },
        cwd=API_ROOT,
    )

    async def exercise() -> None:
        async with Client(parameters, read_timeout_seconds=10) as client:
            projects_result = await client.call_tool("list_projects")
            assert {
                project["project_id"] for project in projects_result.structured_content["projects"]
            } == set(project_ids.values())

            for repository_id in ("repo-one", "repo-two"):
                source_result = await client.call_tool(
                    "read_source",
                    {
                        "project_id": project_ids[repository_id],
                        "index_id": index_ids[repository_id],
                        "path": "timeout_demo/config.py",
                        "start_line": 9,
                        "end_line": 9,
                    },
                )
                assert source_result.structured_content["result"] == http_sources[repository_id]

                search_result = await client.call_tool(
                    "search_code",
                    {
                        "project_id": project_ids[repository_id],
                        "index_id": index_ids[repository_id],
                        "query": "DEFAULT_TIMEOUT",
                        "limit": 4,
                    },
                )
                assert search_result.structured_content["result"] == http_searches[repository_id]

            cross_project = await client.call_tool(
                "get_index",
                {
                    "project_id": project_ids["repo-one"],
                    "index_id": index_ids["repo-two"],
                },
            )
            assert cross_project.structured_content["error"] == {
                "schema_version": "0.1.0",
                "code": "not_found",
                "message": "index does not belong to the project",
                "retryable": False,
            }

            out_of_range = await client.call_tool(
                "read_source",
                {
                    "project_id": project_ids["repo-one"],
                    "index_id": index_ids["repo-one"],
                    "path": "timeout_demo/config.py",
                    "start_line": 9,
                    "end_line": 10_000,
                },
            )
            assert out_of_range.structured_content["error"]["code"] == "invalid_input"

    asyncio.run(exercise())


async def _exercise_stdio(
    parameters: StdioServerParameters,
    *,
    project_id: str,
    index_id: str,
    symbol_id: str,
    http_project: dict,
    http_indexes: dict,
    http_index: dict,
    http_search: dict,
    http_neighbors: dict,
    http_source: dict,
) -> None:
    expected_tools = {
        "list_projects",
        "get_project",
        "list_indexes",
        "get_index",
        "search_code",
        "read_source",
        "get_neighbors",
    }
    async with Client(parameters, read_timeout_seconds=10) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == expected_tools
        assert all(tool.output_schema for tool in tools.tools)
        assert all(tool.description for tool in tools.tools)

        project_result = await client.call_tool("get_project", {"project_id": project_id})
        assert project_result.structured_content == {
            "schema_version": "0.1.0",
            "ok": True,
            "error": None,
            "project": http_project,
        }

        projects_result = await client.call_tool("list_projects")
        assert projects_result.structured_content["ok"] is True
        assert projects_result.structured_content["projects"] == [http_project]
        assert projects_result.structured_content["truncated"] is False
        assert projects_result.structured_content["truncation_reason"] is None

        indexes_result = await client.call_tool("list_indexes", {"project_id": project_id})
        assert indexes_result.structured_content == {
            "schema_version": "0.1.0",
            "ok": True,
            "error": None,
            "project_id": project_id,
            "indexes": http_indexes["indexes"],
            "latest_index_id": http_indexes["latest_index_id"],
            "building_index_id": http_indexes["building_index_id"],
            "truncated": False,
            "truncation_reason": None,
        }

        index_result = await client.call_tool(
            "get_index",
            {"project_id": project_id, "index_id": index_id},
        )
        assert index_result.structured_content == {
            "schema_version": "0.1.0",
            "ok": True,
            "error": None,
            "index": http_index,
        }

        search_result = await client.call_tool(
            "search_code",
            {"project_id": project_id, "index_id": index_id, "query": "load_settings", "limit": 4},
        )
        assert search_result.structured_content == {
            "schema_version": "0.1.0",
            "ok": True,
            "error": None,
            "result": http_search,
        }

        neighbors_result = await client.call_tool(
            "get_neighbors",
            {
                "project_id": project_id,
                "index_id": index_id,
                "symbol_id": symbol_id,
                "direction": "callers",
                "depth": 1,
                "limit": 40,
                "edge_kinds": ["calls"],
            },
        )
        assert neighbors_result.structured_content == {
            "schema_version": "0.1.0",
            "ok": True,
            "error": None,
            "result": http_neighbors,
        }

        source_result = await client.call_tool(
            "read_source",
            {
                "project_id": project_id,
                "index_id": index_id,
                "path": "timeout_demo/config.py",
                "start_line": 12,
                "end_line": 14,
            },
        )
        source_payload = source_result.structured_content
        assert source_payload["ok"] is True
        assert source_payload["result"] == http_source
        reference = source_payload["reference"]
        assert reference["project_id"] == project_id
        assert reference["index_id"] == index_id
        assert reference["evidence_id"] == http_source["evidence"]["evidence_id"]
        assert reference["content_hash"] == http_source["evidence"]["content_hash"]
        assert reference["url"].startswith("http://localhost:3000?")
        assert "project_id=" in reference["url"]
        assert "content_hash=" in reference["url"]

        wrong_project = await client.call_tool(
            "get_index",
            {"project_id": "project-does-not-exist", "index_id": index_id},
        )
        assert wrong_project.structured_content["ok"] is False
        assert wrong_project.structured_content["error"] == {
            "schema_version": "0.1.0",
            "code": "not_found",
            "message": "project was not found",
            "retryable": False,
        }
