"""Offline tests for deterministic entry-point and module overviews."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from app.main import create_app


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


def _copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def _create_index(
    application,
    project_name: str = "Overview project",
    repository_id: str = "timeout-demo",
) -> tuple[str, str]:
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": project_name, "repository_id": repository_id},
    )
    assert status == 200
    status, created = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "overview-v1", "dirty": False},
    )
    assert status == 200
    return project["project_id"], created["index"]["index_id"]


def test_overview_identifies_main_rules_paths_and_stays_on_snapshot(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    project_id, index_id = _create_index(application)

    status, overview = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/overview",
    )
    assert status == 200
    assert overview["entry_status"] == "identified"
    assert overview["rules_version"] == "python-entry-v2"
    assert {entry["kind"] for entry in overview["entries"]} == {
        "python_main_module",
        "python_main_guard",
    }
    guard = next(entry for entry in overview["entries"] if entry["kind"] == "python_main_guard")
    assert guard["start_line"] == 17
    assert guard["end_line"] == 18
    assert guard["evidence"]["excerpt"] == 'if __name__ == "__main__":\n    main()'
    assert any(
        path["entry_point_id"] == guard["entry_point_id"]
        and any(edge["kind"] == "calls" for edge in path["edges"])
        for path in overview["paths"]
    )
    assert any(item["reason"] == "dynamic_call_target" for item in overview["unresolved"])
    assert any(module["entry_reachable"] for module in overview["modules"])
    assert any(not module["entry_reachable"] for module in overview["modules"])

    status, repeated = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/overview",
    )
    assert status == 200
    assert repeated == overview

    (repository / "timeout_demo" / "__main__.py").write_text(
        "# changed after the immutable index was created\n",
        encoding="utf-8",
    )
    status, old_snapshot = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/overview",
    )
    assert status == 200
    assert old_snapshot == overview


def test_overview_identifies_confirmed_fastapi_routes_and_rejects_ambiguous_bindings(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "fastapi-demo"
    package = repository / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text(
        "from fastapi import FastAPI as Api\n"
        "import fastapi as fastapi_alias\n"
        "\n"
        "application = Api()\n"
        "module_application = fastapi_alias.FastAPI()\n"
        "\n"
        "@application.get('/sync')\n"
        "def sync_handler():\n"
        "    return {'ok': True}\n"
        "\n"
        "@module_application.get('/module-alias')\n"
        "async def async_handler():\n"
        "    return {'ok': True}\n"
        "\n"
        "@application.api_route('/multi', methods=['POST', 'GET'])\n"
        "async def multi_handler():\n"
        "    return {'ok': True}\n"
        "\n"
        "DYNAMIC_PATH = '/dynamic'\n"
        "@application.get(DYNAMIC_PATH)\n"
        "def dynamic_handler():\n"
        "    return {'ok': True}\n"
        "\n"
        "ordinary = object()\n"
        "@ordinary.get('/ordinary')\n"
        "def ordinary_handler():\n"
        "    return {'ok': True}\n"
        "\n"
        "def shadowed(application):\n"
        "    @application.get('/shadowed')\n"
        "    def shadowed_handler():\n"
        "        return {'ok': True}\n"
        "\n"
        "application = object()\n"
        "@application.get('/rebound')\n"
        "def rebound_handler():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    application = create_app({"fastapi-demo": repository})
    project_id, index_id = _create_index(application, "FastAPI demo", "fastapi-demo")

    status, overview = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/overview",
    )
    assert status == 200
    routes = [entry for entry in overview["entries"] if entry["kind"] == "fastapi_route"]
    assert {
        (entry["http_method"], entry["declared_path"], entry["handler_symbol"]["qualified_name"])
        for entry in routes
    } == {
        ("GET", "/sync", "demo.app.sync_handler"),
        ("GET", "/module-alias", "demo.app.async_handler"),
        ("GET", "/multi", "demo.app.multi_handler"),
        ("POST", "/multi", "demo.app.multi_handler"),
    }
    for entry in routes:
        assert entry["symbol_id"] == entry["handler_symbol"]["symbol_id"]
        assert entry["handler_symbol"]["kind"] == "function"
        assert entry["start_line"] <= entry["handler_symbol"]["start_line"] <= entry["end_line"]
        assert entry["evidence"]["start_line"] == entry["start_line"]
        assert entry["evidence"]["end_line"] <= entry["end_line"]
        assert all(
            path["symbol_ids"][0] == entry["symbol_id"]
            for path in overview["paths"]
            if path["entry_point_id"] == entry["entry_point_id"]
        )
    assert not any(
        entry["declared_path"] in {"/dynamic", "/ordinary", "/shadowed", "/rebound"}
        for entry in routes
    )
    assert any("声明路径" in note for note in overview["notes"])


def test_overview_does_not_promote_main_names_or_nested_guards(tmp_path: Path) -> None:
    repository = tmp_path / "no-entry"
    (repository / "package").mkdir(parents=True)
    (repository / "package" / "plain.py").write_text(
        "def main():\n    return 1\n\n"
        "def nested():\n    if __name__ == '__main__':\n        return 2\n",
        encoding="utf-8",
    )
    application = create_app({"no-entry": repository})
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "No entry", "repository_id": "no-entry"},
    )
    assert status == 200
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "no-entry-v1", "dirty": False},
    )
    assert status == 200
    index_id = index["index"]["index_id"]

    status, overview = request(
        application,
        "GET",
        f"/api/projects/{project['project_id']}/indexes/{index_id}/overview",
    )
    assert status == 200
    assert overview["entry_status"] == "not_identified"
    assert overview["entries"] == []
    assert overview["paths"] == []
    assert any("未识别入口" in note for note in overview["notes"])


def test_overview_scope_and_budget_are_explicit(tmp_path: Path) -> None:
    repository_one = _copy_fixture(tmp_path)
    repository_two = tmp_path / "timeout-demo-two"
    shutil.copytree(repository_one, repository_two)
    application = create_app({"one": repository_one, "two": repository_two})

    status, first_project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "One", "repository_id": "one"},
    )
    assert status == 200
    status, first_index = request(
        application,
        "POST",
        f"/api/projects/{first_project['project_id']}/indexes",
        {"revision": "one-v1", "dirty": False},
    )
    assert status == 200
    index_id = first_index["index"]["index_id"]

    status, other_project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Two", "repository_id": "two"},
    )
    assert status == 200
    status, scoped = request(
        application,
        "GET",
        f"/api/projects/{other_project['project_id']}/indexes/{index_id}/overview",
    )
    assert status == 404
    assert scoped["code"] == "not_found"

    status, bounded = request(
        application,
        "GET",
        f"/api/projects/{first_project['project_id']}/indexes/{index_id}/overview"
        "?max_depth=1&max_paths=1&max_modules=1",
    )
    assert status == 200
    assert bounded["truncated"] is True
    assert bounded["truncation_reason"]


def test_overview_keeps_cycles_bounded_without_inventing_edges(tmp_path: Path) -> None:
    repository = tmp_path / "cycle-demo"
    package = repository / "pkg"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text(
        "from .a import start\n\n"
        "if __name__ == '__main__':\n"
        "    start()\n",
        encoding="utf-8",
    )
    (package / "a.py").write_text(
        "from .b import loop_b\n\n"
        "def start():\n"
        "    return loop_b()\n",
        encoding="utf-8",
    )
    (package / "b.py").write_text(
        "from .a import start\n\n"
        "def loop_b():\n"
        "    return start()\n",
        encoding="utf-8",
    )
    application = create_app({"cycle": repository})
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Cycle demo", "repository_id": "cycle"},
    )
    assert status == 200
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "cycle-v1", "dirty": False},
    )
    assert status == 200

    status, overview = request(
        application,
        "GET",
        f"/api/projects/{project['project_id']}/indexes/{index['index']['index_id']}/overview"
        "?max_depth=6",
    )
    assert status == 200
    assert any("循环调用" in note for note in overview["notes"])
    assert len(overview["paths"]) <= 40
