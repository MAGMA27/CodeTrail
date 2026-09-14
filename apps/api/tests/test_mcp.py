"""Protocol-level tests for the local MCP adapter and external Run boundary."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest

from app.contracts import CreateRunRequest, Issue, RunBudget, SearchCodeParams
from app.main import create_app
from app.service import ServiceError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"
API_ROOT = ROOT / "apps" / "api"
RUN_READ_TOOLS = {
    "list_session_runs",
    "get_session_run",
    "list_run_events",
    "list_run_tool_results",
    "get_run_report",
    "get_run_impact",
}


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
        "client": ("127.0.0.1", 50000),
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
    status, http_overview = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/overview",
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
            http_overview=http_overview,
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

    async def exercise() -> str:
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


def test_mcp_local_import_is_opt_in_and_shares_http_state(tmp_path: Path) -> None:
    http_repository = copy_fixture(tmp_path, name="mcp-http")
    mcp_repository = copy_fixture(tmp_path, name="mcp-agent")
    data_root = tmp_path / "service-data"
    application = create_app({}, data_root=data_root, allowed_import_roots=(tmp_path,))
    status, http_import = request(
        application,
        "POST",
        "/api/imports/local",
        {
            "name": "HTTP imported project",
            "path": str(http_repository),
            "revision": "http-v1",
            "dirty": False,
            "idempotency_key": "http-import",
        },
    )
    assert status == 200
    assert http_import["attempt"]["status"] == "ready"

    common_environment = {
        "CODETRAIL_REPOSITORIES": "{}",
        "CODETRAIL_DATA_DIR": str(data_root),
        "CODETRAIL_ALLOWED_IMPORT_ROOTS": json.dumps([str(tmp_path)]),
        "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
    }

    def parameters(enabled: bool, github: bool = False) -> StdioServerParameters:
        environment = dict(common_environment)
        if enabled:
            environment["CODETRAIL_MCP_LOCAL_IMPORT_ENABLED"] = "1"
        if github:
            environment["CODETRAIL_GITHUB_IMPORT_ENABLED"] = "1"
            environment["CODETRAIL_MCP_GITHUB_IMPORT_ENABLED"] = "1"
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=environment,
            cwd=API_ROOT,
        )

    async def exercise() -> None:
        async with Client(parameters(False), read_timeout_seconds=10) as client:
            disabled_tools = await client.list_tools()
            disabled_names = {tool.name for tool in disabled_tools.tools}
            assert {
                "list_projects",
                "get_project",
                "list_sessions",
                "get_session",
                "list_session_messages",
                "list_indexes",
                "get_index",
                "search_code",
                "read_source",
                "get_neighbors",
                "get_overview",
                "list_feature_generations",
                "get_feature_generation",
                "get_feature_graph",
            } | RUN_READ_TOOLS == disabled_names
            assert "import_local_path" not in disabled_names

        async with Client(parameters(True), read_timeout_seconds=10) as client:
            enabled_tools = await client.list_tools()
            enabled_names = {tool.name for tool in enabled_tools.tools}
            assert {"import_local_path", "get_import_status", "retry_local_import"} <= enabled_names

            imported = await client.call_tool(
                "import_local_path",
                {
                    "name": "MCP imported project",
                    "path": str(mcp_repository),
                    "revision": "mcp-v1",
                    "dirty": False,
                    "idempotency_key": "mcp-import",
                },
            )
            assert imported.structured_content["ok"] is True
            assert imported.structured_content["attempt"]["status"] == "ready"
            mcp_project_id = imported.structured_content["project"]["project_id"]
            mcp_index_id = imported.structured_content["index"]["index"]["index_id"]
            attempt_id = imported.structured_content["attempt"]["attempt_id"]

            status_result = await client.call_tool("get_import_status", {"attempt_id": attempt_id})
            assert status_result.structured_content["ok"] is True
            assert status_result.structured_content["attempt"]["status"] == "ready"

            source_result = await client.call_tool(
                "read_source",
                {
                    "project_id": mcp_project_id,
                    "index_id": mcp_index_id,
                    "path": "timeout_demo/config.py",
                    "start_line": 9,
                    "end_line": 9,
                },
            )
            assert source_result.structured_content["result"]["evidence"]["excerpt"] == "DEFAULT_TIMEOUT = 30"

        async with Client(parameters(False, github=True), read_timeout_seconds=10) as client:
            github_tools = {tool.name for tool in (await client.list_tools()).tools}
            assert {
                "list_projects",
                "get_project",
                "list_sessions",
                "get_session",
                "list_session_messages",
                "list_indexes",
                "get_index",
                "search_code",
                "read_source",
                "get_neighbors",
                "get_overview",
                "get_import_status",
                "list_feature_generations",
                "get_feature_generation",
                "get_feature_graph",
                "import_github_repository",
                "retry_github_import",
            } | RUN_READ_TOOLS == github_tools
        return mcp_project_id

    mcp_project_id = asyncio.run(exercise())

    status, projects = request(application, "GET", "/api/projects")
    assert status == 200
    assert {
        http_import["project"]["project_id"],
        mcp_project_id,
    } <= {project["project_id"] for project in projects["projects"]}


def test_mcp_feature_generation_is_separately_opt_in_and_returns_citations(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "MCP feature fixture", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "mcp-feature-v1", "dirty": False},
    )
    assert status == 200
    index_id = created["index"]["index_id"]

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env={
            "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
            "CODETRAIL_MCP_FEATURE_GENERATION_ENABLED": "1",
            "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
        },
        cwd=API_ROOT,
    )

    async def exercise() -> tuple[dict, dict]:
        async with Client(parameters, read_timeout_seconds=15) as client:
            tool_names = {tool.name for tool in (await client.list_tools()).tools}
            assert {
                "list_projects",
                "get_project",
                "list_sessions",
                "get_session",
                "list_session_messages",
                "list_indexes",
                "get_index",
                "search_code",
                "read_source",
                "get_neighbors",
                "get_overview",
                "list_feature_generations",
                "generate_project_features",
                "get_feature_generation",
                "get_feature_graph",
            } | RUN_READ_TOOLS == tool_names
            generated = await client.call_tool(
                "generate_project_features",
                {
                    "project_id": project_id,
                    "index_id": index_id,
                    "mode": "fixture",
                    "idempotency_key": "mcp-feature-one",
                },
            )
            structured = generated.structured_content
            assert structured["ok"] is True
            generation = structured["generation"]
            assert generation["status"] in {"succeeded", "partial"}
            assert generation["features"]
            assert {reference["evidence_id"] for reference in structured["references"]} == {
                evidence["evidence_id"] for evidence in generation["evidence"]
            }
            assert all(reference["url"].startswith("http://localhost:3000?") for reference in structured["references"])

            reread = await client.call_tool(
                "get_feature_generation",
                {
                    "project_id": project_id,
                    "index_id": index_id,
                    "generation_id": generation["generation_id"],
                },
            )
            assert reread.structured_content["generation"] == generation

            history = await client.call_tool(
                "list_feature_generations",
                {"project_id": project_id, "index_id": index_id, "limit": 40},
            )
            assert history.structured_content["ok"] is True
            assert history.structured_content["result"]["project_id"] == project_id
            assert history.structured_content["result"]["index_id"] == index_id
            assert history.structured_content["result"]["generations"][0]["generation_id"] == generation["generation_id"]

            graph = await client.call_tool(
                "get_feature_graph",
                {
                    "project_id": project_id,
                    "index_id": index_id,
                    "generation_id": generation["generation_id"],
                },
            )
            assert graph.structured_content["ok"] is True
            assert graph.structured_content["graph"]["project_id"] == project_id
            assert graph.structured_content["graph"]["index_id"] == index_id
            assert graph.structured_content["graph"]["generation_id"] == generation["generation_id"]
            assert graph.structured_content["graph"]["nodes"]
            assert {reference["evidence_id"] for reference in graph.structured_content["references"]} == {
                evidence["evidence_id"] for evidence in graph.structured_content["graph"]["evidence"]
            }
            return history.structured_content, graph.structured_content

    history_result, graph_result = asyncio.run(exercise())
    status, http_history = request(
        application,
        "GET",
        f"/api/projects/{project_id}/features?index_id={index_id}&limit=40",
    )
    assert status == 200
    assert history_result["result"] == http_history
    status, http_graph = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{history_result['result']['generations'][0]['generation_id']}/graph",
    )
    assert status == 200
    assert graph_result["graph"] == http_graph["graph"]


def test_mcp_session_read_matches_http_and_writes_are_opt_in(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    data_root = tmp_path / "service-data"
    application = create_app({"timeout-demo": repository}, data_root=data_root)
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "MCP session", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "mcp-session-v1", "dirty": False},
    )
    assert status == 200
    status, created = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "HTTP session", "idempotency_key": "http-session"},
    )
    assert status == 200
    session_id = created["session"]["session_id"]
    status, retry_created = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "shared retry", "idempotency_key": "shared-retry"},
    )
    assert status == 200
    config_path = repository / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 31"),
        encoding="utf-8",
    )
    status, new_index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "mcp-session-v2", "dirty": False},
    )
    assert status == 200
    assert new_index["index"]["index_id"] != index["index"]["index_id"]
    status, http_sessions = request(application, "GET", f"/api/projects/{project_id}/sessions")
    assert status == 200
    status, http_messages = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
    )
    assert status == 200

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env={
            "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
            "CODETRAIL_DATA_DIR": str(data_root),
            "CODETRAIL_MCP_SESSION_WRITE_ENABLED": "1",
            "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
        },
        cwd=API_ROOT,
    )

    async def exercise() -> tuple[dict, dict, dict]:
        async with Client(parameters, read_timeout_seconds=10) as client:
            tool_names = {tool.name for tool in (await client.list_tools()).tools}
            assert {"list_sessions", "get_session", "list_session_messages", "create_session", "append_session_message"} <= tool_names
            sessions_result = await client.call_tool("list_sessions", {"project_id": project_id})
            assert sessions_result.structured_content["result"] == http_sessions
            assert all(reference["project_id"] == project_id for reference in sessions_result.structured_content["references"])
            assert session_id in {
                reference["session_id"] for reference in sessions_result.structured_content["references"]
            }

            retried_by_agent = await client.call_tool(
                "create_session",
                {"project_id": project_id, "topic": "shared retry", "idempotency_key": "shared-retry"},
            )
            assert retried_by_agent.structured_content["session"] == retry_created["session"]

            session_result = await client.call_tool(
                "get_session",
                {"project_id": project_id, "session_id": session_id},
            )
            assert session_result.structured_content["session"] == created["session"]
            assert session_result.structured_content["reference"]["session_id"] == session_id

            messages_result = await client.call_tool(
                "list_session_messages",
                {"project_id": project_id, "session_id": session_id},
            )
            assert messages_result.structured_content["result"] == http_messages

            created_by_agent = await client.call_tool(
                "create_session",
                {"project_id": project_id, "topic": "Agent session", "idempotency_key": "agent-session"},
            )
            agent_session_id = created_by_agent.structured_content["session"]["session_id"]
            appended = await client.call_tool(
                "append_session_message",
                {
                    "project_id": project_id,
                    "session_id": agent_session_id,
                    "source": "agent",
                    "content": "saved through MCP",
                    "idempotency_key": "agent-message",
                },
            )
            return created_by_agent.structured_content, appended.structured_content, {
                "agent_session_id": agent_session_id,
            }

    agent_session, agent_message, ids = asyncio.run(exercise())
    assert agent_session["ok"] is True
    assert agent_session["reference"]["url"].startswith("http://localhost:3000?")
    assert agent_message["ok"] is True
    status, http_agent_messages = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{ids['agent_session_id']}/messages",
    )
    assert status == 200
    assert http_agent_messages["messages"] == [agent_message["message"]]


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
    http_overview: dict,
) -> None:
    expected_tools = {
        "list_projects",
        "get_project",
        "list_sessions",
        "get_session",
        "list_session_messages",
        "list_indexes",
        "get_index",
        "search_code",
        "read_source",
        "get_neighbors",
        "get_overview",
        "list_feature_generations",
        "get_feature_generation",
        "get_feature_graph",
    } | RUN_READ_TOOLS
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

        overview_result = await client.call_tool(
            "get_overview",
            {"project_id": project_id, "index_id": index_id},
        )
        assert overview_result.structured_content["ok"] is True
        assert overview_result.structured_content["overview"] == http_overview
        references = overview_result.structured_content["references"]
        assert references
        assert all(reference["project_id"] == project_id for reference in references)
        assert all(reference["index_id"] == index_id for reference in references)

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


def test_mcp_run_reads_share_http_state_and_writes_are_separately_opt_in(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "MCP Run fixture", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "mcp-run-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]
    status, session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "MCP Run", "index_id": index_id},
    )
    assert status == 200
    session_id = session["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Inspect the timeout initialization path."},
    )
    assert status == 200

    environment = {
        "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
        "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
    }

    def parameters(*, run_write: bool = False, real_run: bool = False) -> StdioServerParameters:
        configured = dict(environment)
        if run_write:
            configured["CODETRAIL_MCP_RUN_WRITE_ENABLED"] = "1"
        if real_run:
            configured["CODETRAIL_MCP_REAL_RUN_ENABLED"] = "1"
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=configured,
            cwd=API_ROOT,
        )

    http_run_payload = {
        "index_id": index_id,
        "issue": {
            "id": "mcp-http-run-issue",
            "title": "timeout missing",
            "body": "Inspect the missing timeout during client initialization.",
        },
        "mode": "fixture",
        "fixture_scenario": "success-timeout-missing",
    }
    status, http_run = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        http_run_payload,
    )
    assert status == 200
    http_run_id = http_run["run_id"]
    for _ in range(100):
        status, http_status = request(
            application,
            "GET",
            f"/api/projects/{project_id}/sessions/{session_id}/runs/{http_run_id}",
        )
        assert status == 200
        if http_status["run"]["status"] not in {"queued", "running"}:
            break
        time.sleep(0.025)
    assert http_status["run"]["status"] == "succeeded"

    async def exercise() -> None:
        async with Client(parameters(), read_timeout_seconds=10) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert RUN_READ_TOOLS <= tools
            assert "start_session_run" not in tools
            assert "cancel_session_run" not in tools

            history = await client.call_tool(
                "list_session_runs",
                {"project_id": project_id, "session_id": session_id},
            )
            assert history.structured_content["ok"] is True
            assert history.structured_content["result"]["runs"][0]["run_id"] == http_run_id

            read = await client.call_tool(
                "get_session_run",
                {"project_id": project_id, "session_id": session_id, "run_id": http_run_id},
            )
            assert read.structured_content["result"]["run"]["index_id"] == index_id
            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": http_run_id},
            )
            assert events.structured_content["result"]["events"][-1]["type"] == "run.completed"
            results = await client.call_tool(
                "list_run_tool_results",
                {"project_id": project_id, "session_id": session_id, "run_id": http_run_id},
            )
            assert [item["tool"] for item in results.structured_content["result"]["results"]] == [
                "search_code",
                "read_source",
                "get_neighbors",
            ]
            report = await client.call_tool(
                "get_run_report",
                {"project_id": project_id, "session_id": session_id, "run_id": http_run_id},
            )
            assert report.structured_content["report"]["outcome"] == "candidate_found"
            candidate_id = report.structured_content["report"]["candidate_links"][0]["candidate_id"]
            status, http_impact = await _request(
                application,
                "GET",
                f"/api/projects/{project_id}/sessions/{session_id}/runs/{http_run_id}/impact"
                f"?candidate_id={candidate_id}&direction=callers&depth=2&limit=40",
            )
            assert status == 200
            impact = await client.call_tool(
                "get_run_impact",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": http_run_id,
                    "candidate_id": candidate_id,
                    "direction": "callers",
                    "depth": 2,
                    "limit": 40,
                },
            )
            assert impact.structured_content["result"] == http_impact

        async with Client(parameters(run_write=True), read_timeout_seconds=10) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert {"start_session_run", "cancel_session_run"} <= tools
            assert "create_session" not in tools
            started = await client.call_tool(
                "start_session_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "mcp-write-run-issue",
                    "title": "timeout missing",
                    "body": "Inspect the timeout initialization path.",
                    "fixture_scenario": "insufficient-external-service",
                },
            )
            assert started.structured_content["ok"] is True
            started_id = started.structured_content["result"]["run_id"]
            for _ in range(100):
                current = await client.call_tool(
                    "get_session_run",
                    {"project_id": project_id, "session_id": session_id, "run_id": started_id},
                )
                current_run = current.structured_content["result"]["run"]["status"]
                if current_run not in {"queued", "running"}:
                    break
                await asyncio.sleep(0.025)
            assert current_run == "succeeded"
            report = await client.call_tool(
                "get_run_report",
                {"project_id": project_id, "session_id": session_id, "run_id": started_id},
            )
            assert report.structured_content["report"]["outcome"] == "insufficient_evidence"

            real_started = await client.call_tool(
                "start_session_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "mcp-real-disabled",
                    "title": "real disabled",
                    "body": "This request must not reach a provider.",
                    "mode": "real",
                },
            )
            assert real_started.structured_content["ok"] is False
            assert real_started.structured_content["error"]["code"] == "forbidden"

        async with Client(parameters(run_write=True, real_run=True), read_timeout_seconds=10) as client:
            real_started = await client.call_tool(
                "start_session_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "mcp-real-no-key",
                    "title": "real without key",
                    "body": "The configured provider is intentionally absent.",
                    "mode": "real",
                },
            )
            assert real_started.structured_content["ok"] is True
            real_run_id = real_started.structured_content["result"]["run_id"]
            for _ in range(100):
                current = await client.call_tool(
                    "get_session_run",
                    {"project_id": project_id, "session_id": session_id, "run_id": real_run_id},
                )
                current_run = current.structured_content["result"]["run"]["status"]
                if current_run not in {"queued", "running"}:
                    break
                await asyncio.sleep(0.025)
            assert current_run == "failed"
            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": real_run_id},
            )
            assert events.structured_content["result"]["events"][-1]["payload"]["error"]["code"] == "model_unavailable"

    asyncio.run(exercise())


def test_mcp_external_run_persists_actual_tools_and_report(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app(
        {"timeout-demo": repository},
        data_root=tmp_path / "data",
        mcp_external_run_enabled=True,
    )
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "External MCP", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "external-mcp-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]
    status, session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "External billing investigation", "index_id": index_id},
    )
    assert status == 200
    session_id = session["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Trace the timeout initialization path."},
    )
    assert status == 200

    base_environment = {
        "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
        "CODETRAIL_DATA_DIR": str(tmp_path / "data"),
        "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
    }
    external_run_id: str | None = None

    async def exercise() -> None:
        nonlocal external_run_id
        read_only_parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=base_environment,
            cwd=API_ROOT,
        )
        async with Client(read_only_parameters, read_timeout_seconds=10) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert "start_external_run" not in tools
            assert "run_search_code" not in tools

        environment = {**base_environment, "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED": "1"}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=environment,
            cwd=API_ROOT,
        )
        async with Client(parameters, read_timeout_seconds=10) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert {
                "start_external_run",
                "run_search_code",
                "run_read_source",
                "run_get_neighbors",
                "submit_external_run",
                "cancel_external_run",
            } <= tools

            started = await client.call_tool(
                "start_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "external-mcp-issue",
                    "title": "timeout initialization",
                    "body": "Trace the timeout initialization path and report only evidence-backed findings.",
                    "idempotency_key": "external-mcp-start",
                },
            )
            assert started.structured_content["ok"] is True
            external_run_id = started.structured_content["result"]["run_id"]
            run_id = external_run_id

            searched = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-search-1",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert searched.structured_content["ok"] is True
            search_result = searched.structured_content["result"]
            assert search_result["kind"] == "search_code.succeeded"
            status, http_search = await _request(
                application,
                "GET",
                f"/api/projects/{project_id}/indexes/{index_id}/search?query=load_settings&limit=8",
            )
            assert status == 200
            assert [
                item["symbol"]["symbol_id"] for item in search_result["matches"]
            ] == [
                item["symbol"]["symbol_id"] for item in http_search["matches"]
            ]
            assert search_result["truncated"] == http_search["truncated"]
            assert search_result["truncation_reason"] == http_search["truncation_reason"]
            match = next(
                item for item in search_result["matches"]
                if item["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
            )
            symbol_id = match["symbol"]["symbol_id"]

            repeated_search = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-search-1",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert repeated_search.structured_content == searched.structured_content

            source = await client.call_tool(
                "run_read_source",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-source-1",
                    "path": match["symbol"]["path"],
                    "start_line": match["symbol"]["start_line"],
                    "end_line": min(match["symbol"]["end_line"], match["symbol"]["start_line"] + 5),
                },
            )
            assert source.structured_content["ok"] is True
            source_result = source.structured_content["result"]
            evidence_id = source_result["evidence"]["evidence_id"]
            assert source.structured_content["references"][0]["project_id"] == project_id

            neighbors = await client.call_tool(
                "run_get_neighbors",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-neighbors-1",
                    "symbol_id": symbol_id,
                    "direction": "callers",
                    "depth": 1,
                    "limit": 40,
                },
            )
            assert neighbors.structured_content["ok"] is True
            assert neighbors.structured_content["result"]["run_id"] == run_id

            rejected = await client.call_tool(
                "submit_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "outcome": "insufficient_evidence",
                    "summary": "The evidence is intentionally invalid.",
                    "evidence_ids": ["evidence-not-returned"],
                },
            )
            assert rejected.structured_content["ok"] is False
            assert rejected.structured_content["error"]["code"] == "conflict"

            completed = await client.call_tool(
                "submit_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "outcome": "candidate_found",
                    "summary": "The timeout initialization path has an evidence-backed candidate.",
                    "findings": [
                        {
                            "finding_id": "finding-external-1",
                            "kind": "hypothesis",
                            "text": "The loaded configuration controls the timeout initialization path.",
                            "symbol_ids": [symbol_id],
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "candidate_links": [
                        {
                            "candidate_id": "candidate-external-1",
                            "index_id": index_id,
                            "source_symbol_id": symbol_id,
                            "relation": "controls timeout initialization",
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "evidence_ids": [evidence_id],
                    "limitations": ["Static calls are not confirmed runtime impact."],
                },
            )
            assert completed.structured_content["ok"] is True
            report = completed.structured_content["report"]
            assert report["fixture_mode"] is False
            assert report["limitations"] == ["Static calls are not confirmed runtime impact."]
            assert completed.structured_content["references"][0]["project_id"] == project_id

            repeated_completion = await client.call_tool(
                "submit_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "outcome": "candidate_found",
                    "summary": "The timeout initialization path has an evidence-backed candidate.",
                    "findings": [
                        {
                            "finding_id": "finding-external-1",
                            "kind": "hypothesis",
                            "text": "The loaded configuration controls the timeout initialization path.",
                            "symbol_ids": [symbol_id],
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "candidate_links": [
                        {
                            "candidate_id": "candidate-external-1",
                            "index_id": index_id,
                            "source_symbol_id": symbol_id,
                            "relation": "controls timeout initialization",
                            "evidence_ids": [evidence_id],
                        }
                    ],
                    "evidence_ids": [evidence_id],
                    "limitations": ["Static calls are not confirmed runtime impact."],
                },
            )
            assert repeated_completion.structured_content == completed.structured_content

            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": run_id},
            )
            event_types = [event["type"] for event in events.structured_content["result"]["events"]]
            assert event_types[0] == "run.started"
            assert event_types.count("tool.started") == 3
            assert event_types.count("tool.completed") == 3
            assert event_types.count("graph.updated") == 1
            assert event_types.count("run.completed") == 1

    asyncio.run(exercise())
    assert external_run_id is not None

    status, http_status = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{external_run_id}",
    )
    assert status == 200
    assert http_status["run"]["mode"] == "external"
    assert http_status["run"]["execution_source"] == "codex"
    status, http_report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{external_run_id}/report",
    )
    assert status == 200
    assert http_report["report"]["candidate_links"][0]["candidate_id"] == "candidate-external-1"

    async def exercise_boundaries() -> None:
        environment = {**base_environment, "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED": "1"}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=environment,
            cwd=API_ROOT,
        )
        async with Client(parameters, read_timeout_seconds=10) as client:
            timed_out = await client.call_tool(
                "start_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "external-timeout-issue",
                    "title": "bounded external Run",
                    "body": "This external Run must expire before its first tool call.",
                    "idempotency_key": "external-timeout-start",
                    "max_seconds": 1,
                },
            )
            assert timed_out.structured_content["ok"] is True
            timed_out_id = timed_out.structured_content["result"]["run_id"]
            await asyncio.sleep(1.1)
            expired_tool = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": timed_out_id,
                    "index_id": index_id,
                    "request_id": "external-timeout-tool",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert expired_tool.structured_content["ok"] is False
            assert expired_tool.structured_content["error"]["code"] == "budget_exceeded"
            status, expired_status = await _request(
                application,
                "GET",
                f"/api/projects/{project_id}/sessions/{session_id}/runs/{timed_out_id}",
            )
            assert status == 200
            assert expired_status["run"]["status"] == "interrupted"

            cancelled = await client.call_tool(
                "start_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "external-cancel-issue",
                    "title": "cancelled external Run",
                    "body": "This external Run must not accept tools after cancellation.",
                    "idempotency_key": "external-cancel-start",
                },
            )
            assert cancelled.structured_content["ok"] is True
            cancelled_id = cancelled.structured_content["result"]["run_id"]
            cancel = await client.call_tool(
                "cancel_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": cancelled_id,
                    "reason": "cancelled in boundary test",
                },
            )
            repeated_cancel = await client.call_tool(
                "cancel_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": cancelled_id,
                    "reason": "different reason is ignored after terminal state",
                },
            )
            assert cancel.structured_content["ok"] is True
            assert cancel.structured_content["result"]["status"] == "cancelled"
            assert repeated_cancel.structured_content["ok"] is True
            assert repeated_cancel.structured_content["result"]["run_id"] == cancelled_id
            assert repeated_cancel.structured_content["result"]["status"] == "cancelled"
            assert repeated_cancel.structured_content["result"]["cancel_event_sequence"] is None
            late_tool = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": cancelled_id,
                    "index_id": index_id,
                    "request_id": "external-late-tool",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert late_tool.structured_content["ok"] is False
            assert late_tool.structured_content["error"]["code"] == "conflict"
            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": cancelled_id},
            )
            event_types = [event["type"] for event in events.structured_content["result"]["events"]]
            assert event_types == ["run.cancelled"]

    asyncio.run(exercise_boundaries())


def test_mcp_external_first_completion_with_multiple_evidence_is_idempotent(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app(
        {"timeout-demo": repository},
        data_root=tmp_path / "data",
        mcp_external_run_enabled=True,
    )
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "External first completion", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "external-first-completion-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]
    status, session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "External first completion", "index_id": index_id},
    )
    assert status == 200
    session_id = session["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Trace the local import flow."},
    )
    assert status == 200

    async def exercise() -> str:
        environment = {
            "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
            "CODETRAIL_DATA_DIR": str(tmp_path / "data"),
            "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
            "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED": "1",
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=environment,
            cwd=API_ROOT,
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            started = await client.call_tool(
                "start_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "issue_id": "external-first-completion",
                    "title": "first completion with multiple evidence",
                    "body": "Trace the local import flow using only returned evidence.",
                    "max_tool_calls": 8,
                    "max_seconds": 30,
                    "max_source_lines": 40,
                    "max_total_source_lines": 100,
                    "max_output_chars": 20_000,
                    "idempotency_key": "external-first-completion-start",
                },
            )
            assert started.structured_content["ok"] is True
            run_id = started.structured_content["result"]["run_id"]

            searched = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-first-completion-search",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert searched.structured_content["ok"] is True
            match = next(
                item
                for item in searched.structured_content["result"]["matches"]
                if item["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
            )

            evidence_ids: list[str] = []
            for number, path in enumerate(
                (
                    "timeout_demo/config.py",
                    "timeout_demo/__main__.py",
                    "timeout_demo/app.py",
                    "timeout_demo/client.py",
                    "timeout_demo/handlers.py",
                ),
                start=1,
            ):
                source = await client.call_tool(
                    "run_read_source",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "run_id": run_id,
                        "index_id": index_id,
                        "request_id": f"external-first-completion-read-{number}",
                        "path": path,
                        "start_line": 1,
                        "end_line": 8,
                    },
                )
                assert source.structured_content["ok"] is True
                evidence_ids.append(source.structured_content["result"]["evidence"]["evidence_id"])

            neighbors = await client.call_tool(
                "run_get_neighbors",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-first-completion-neighbors",
                    "symbol_id": match["symbol"]["symbol_id"],
                    "direction": "callers",
                    "depth": 1,
                    "limit": 40,
                },
            )
            assert neighbors.structured_content["ok"] is True

            conclusion = {
                "project_id": project_id,
                "session_id": session_id,
                "run_id": run_id,
                "index_id": index_id,
                "outcome": "insufficient_evidence",
                "summary": "The local import flow has multiple source observations, but the complete runtime chain is not statically confirmed.",
                "findings": [
                    {
                        "finding_id": "observation-entry",
                        "kind": "observation",
                        "text": "The entry and source evidence were returned by the bound Run.",
                        "evidence_ids": [evidence_ids[0]],
                    },
                    {
                        "finding_id": "observation-storage",
                        "kind": "observation",
                        "text": "Additional source evidence was returned by the same bound Run.",
                        "evidence_ids": evidence_ids[1:4],
                    },
                    {
                        "finding_id": "observation-limits",
                        "kind": "hypothesis",
                        "text": "Coverage and unresolved limits prevent claiming a complete runtime flow.",
                        "evidence_ids": [],
                    },
                ],
                "candidate_links": [],
                "evidence_ids": evidence_ids,
                "limitations": [
                    "The fixture relation result is bounded and cannot confirm every runtime call.",
                    "The report is an understanding result, not a defect candidate.",
                ],
            }
            completed = await client.call_tool("submit_external_run", conclusion)
            assert completed.structured_content["ok"] is True
            assert completed.structured_content["report"]["outcome"] == "insufficient_evidence"
            assert len(completed.structured_content["report"]["evidence"]) == 5
            assert len(completed.structured_content["references"]) == 5

            repeated = await client.call_tool("submit_external_run", conclusion)
            assert repeated.structured_content == completed.structured_content

            conflicting = await client.call_tool(
                "submit_external_run",
                {**conclusion, "summary": "A different conclusion must be rejected."},
            )
            assert conflicting.structured_content["ok"] is False
            assert conflicting.structured_content["error"]["code"] == "conflict"

            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": run_id},
            )
            event_types = [event["type"] for event in events.structured_content["result"]["events"]]
            assert event_types[0] == "run.started"
            assert event_types.count("tool.started") == 7
            assert event_types.count("tool.completed") == 7
            assert event_types.count("graph.updated") == 1
            assert event_types.count("run.completed") == 1
            return run_id

    run_id = asyncio.run(exercise())
    status, report_response = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report_response["report"]["report_id"] == f"report-{run_id}"
    assert len(report_response["report"]["evidence"]) == 5


def test_mcp_external_understanding_run_persists_ordered_explanation(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    data_root = tmp_path / "data"
    application = create_app(
        {"timeout-demo": repository},
        data_root=data_root,
        mcp_external_run_enabled=True,
    )
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "External understanding", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "external-understanding-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]
    status, session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "Trace local import flow", "index_id": index_id},
    )
    assert status == 200
    session_id = session["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "How does the local import request flow through the service?"},
    )
    assert status == 200

    async def exercise() -> dict:
        environment = {
            "CODETRAIL_REPOSITORIES": json.dumps({"timeout-demo": str(repository)}),
            "CODETRAIL_DATA_DIR": str(data_root),
            "CODETRAIL_WORKBENCH_URL": "http://localhost:3000",
            "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED": "1",
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=environment,
            cwd=API_ROOT,
        )
        async with Client(parameters, read_timeout_seconds=15) as client:
            started = await client.call_tool(
                "start_external_run",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "index_id": index_id,
                    "analysis_kind": "understanding",
                    "issue_id": "external-understanding",
                    "title": "local import flow",
                    "body": "Explain the local import request flow with ordered evidence-backed steps.",
                    "idempotency_key": "external-understanding-start",
                },
            )
            assert started.structured_content["ok"] is True
            run_id = started.structured_content["result"]["run_id"]

            run_status = await client.call_tool(
                "get_session_run",
                {"project_id": project_id, "session_id": session_id, "run_id": run_id},
            )
            assert run_status.structured_content["result"]["run"]["analysis_kind"] == "understanding"

            searched = await client.call_tool(
                "run_search_code",
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "run_id": run_id,
                    "index_id": index_id,
                    "request_id": "external-understanding-search",
                    "query": "load_settings",
                    "limit": 8,
                },
            )
            assert searched.structured_content["ok"] is True
            match = next(
                item
                for item in searched.structured_content["result"]["matches"]
                if item["symbol"]["qualified_name"] == "timeout_demo.config.load_settings"
            )
            symbol_id = match["symbol"]["symbol_id"]

            evidence_ids: list[str] = []
            for number, path in enumerate(
                (
                    "timeout_demo/config.py",
                    "timeout_demo/__main__.py",
                    "timeout_demo/app.py",
                ),
                start=1,
            ):
                source = await client.call_tool(
                    "run_read_source",
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "run_id": run_id,
                        "index_id": index_id,
                        "request_id": f"external-understanding-read-{number}",
                        "path": path,
                        "start_line": 1,
                        "end_line": 8,
                    },
                )
                assert source.structured_content["ok"] is True
                evidence_ids.append(source.structured_content["result"]["evidence"]["evidence_id"])

            conclusion = {
                "project_id": project_id,
                "session_id": session_id,
                "run_id": run_id,
                "index_id": index_id,
                "outcome": "explanation",
                "summary": "The visible import entry and configuration step are supported; the runtime handoff remains unconfirmed.",
                "explanation": {
                    "coverage": "partial",
                    "steps": [
                        {
                            "step_id": "step-entry",
                            "kind": "confirmed",
                            "title": "读取本地导入入口",
                            "description": "入口相关源码已由本 Run 返回，并可映射到一个索引符号。",
                            "evidence_ids": [evidence_ids[0]],
                            "symbol_ids": [symbol_id],
                        },
                        {
                            "step_id": "step-validation",
                            "kind": "confirmed",
                            "title": "进入配置读取步骤",
                            "description": "配置读取源码由同一固定快照返回。",
                            "evidence_ids": [evidence_ids[1]],
                            "symbol_ids": [],
                        },
                        {
                            "step_id": "step-runtime-gap",
                            "kind": "unknown",
                            "title": "运行时交接",
                            "description": "当前 Python 静态结果不能确认完整运行时交接。",
                            "evidence_ids": [evidence_ids[2]],
                            "symbol_ids": [],
                        },
                    ],
                },
                "evidence_ids": evidence_ids,
                "limitations": ["当前解释不覆盖未索引的前端或运行时分支。"],
            }
            invalid_scope = {
                **conclusion,
                "explanation": {
                    **conclusion["explanation"],
                    "steps": [
                        {
                            **conclusion["explanation"]["steps"][0],
                            "symbol_ids": ["symbol-not-returned-by-this-run"],
                        },
                        *conclusion["explanation"]["steps"][1:],
                    ],
                },
            }
            invalid_scope_result = await client.call_tool("submit_external_run", invalid_scope)
            assert invalid_scope_result.structured_content["ok"] is False
            assert invalid_scope_result.structured_content["error"]["code"] == "conflict"

            completed = await client.call_tool("submit_external_run", conclusion)
            assert completed.structured_content["ok"] is True
            report = completed.structured_content["report"]
            assert report["outcome"] == "explanation"
            assert report["findings"] == []
            assert report["candidate_links"] == []
            assert report["graph"]["candidate_links"] == []
            assert [step["step_id"] for step in report["explanation"]["steps"]] == [
                "step-entry",
                "step-validation",
                "step-runtime-gap",
            ]
            assert len(report["evidence"]) == 3

            repeated = await client.call_tool("submit_external_run", conclusion)
            assert repeated.structured_content == completed.structured_content

            conflicting = await client.call_tool(
                "submit_external_run",
                {
                    **conclusion,
                    "explanation": {
                        **conclusion["explanation"],
                        "steps": [
                            {
                                **conclusion["explanation"]["steps"][0],
                                "title": "修改后的入口步骤",
                            },
                            *conclusion["explanation"]["steps"][1:],
                        ],
                    },
                },
            )
            assert conflicting.structured_content["ok"] is False
            assert conflicting.structured_content["error"]["code"] == "conflict"

            events = await client.call_tool(
                "list_run_events",
                {"project_id": project_id, "session_id": session_id, "run_id": run_id},
            )
            event_types = [event["type"] for event in events.structured_content["result"]["events"]]
            assert event_types[0] == "run.started"
            assert event_types.count("tool.started") == 4
            assert event_types.count("tool.completed") == 4
            assert event_types.count("graph.updated") == 1
            assert event_types.count("run.completed") == 1
            return report

    mcp_report = asyncio.run(exercise())
    status, http_report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{mcp_report['run_id']}/report",
    )
    assert status == 200
    assert http_report["report"] == mcp_report


def test_external_tool_expiry_interrupts_after_slow_bounded_call(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path)
    application = create_app(
        {"timeout-demo": repository},
        data_root=tmp_path / "data",
        mcp_external_run_enabled=True,
    )
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "External timeout", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "external-timeout-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]
    status, session = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": "External timeout", "index_id": index_id},
    )
    assert status == 200
    session_id = session["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Trace one bounded tool call."},
    )
    assert status == 200

    service = application.state.workspace_service
    created = service.create_run(
        project_id,
        session_id,
        CreateRunRequest(
            index_id=index_id,
            issue=Issue(id="external-timeout", title="External timeout", body="Trace one bounded tool call."),
            mode="external",
            execution_source="codex",
            execution_version="run-external-codex-v1",
            budget=RunBudget(max_seconds=1),
        ),
        via_mcp=True,
    )
    original_execute_tool = service.index_service.execute_tool

    def slow_execute_tool(request, completed_sequence=1):
        time.sleep(1.1)
        return original_execute_tool(request, completed_sequence)

    service.index_service.execute_tool = slow_execute_tool
    with pytest.raises(ServiceError) as error:
        service.execute_external_tool(
            project_id,
            session_id,
            created.run_id,
            "external-slow-tool",
            index_id,
            SearchCodeParams(query="timeout", limit=8),
        )
    assert error.value.code == "budget_exceeded"
    status_response = service.get_run(project_id, session_id, created.run_id)
    assert status_response.run.status == "interrupted"
    with service.registry.store_for_repository("timeout-demo") as store:
        event_types = [event.type for event in store.load_run_events(project_id, session_id, created.run_id).events]
    assert event_types == ["run.started", "tool.started", "run.interrupted"]
