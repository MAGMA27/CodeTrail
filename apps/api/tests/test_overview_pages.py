"""Shared overview skeleton and section pagination regression coverage."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from app.main import create_app
from app.contracts import (
    DEFAULT_SKELETON_ENTRIES,
    MAX_OVERVIEW_CLASSIFICATIONS,
    MAX_OVERVIEW_GROUPS,
    MAX_OVERVIEW_MODULES,
    MAX_OVERVIEW_PATHS,
    MAX_OVERVIEW_RELATIONS,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


async def _request(application, method: str, target: str) -> tuple[int, dict]:
    parsed = urlsplit(target)
    messages: list[dict] = []
    received = False
    response_complete = asyncio.Event()

    async def receive() -> dict:
        nonlocal received
        if received:
            await response_complete.wait()
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

    await application({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], json.loads(body or b"{}")


def request(application, target: str) -> tuple[int, dict]:
    return asyncio.run(_request(application, "GET", target))


def _create_index(tmp_path: Path):
    repository = tmp_path / "timeout-demo"
    shutil.copytree(FIXTURE_ROOT, repository, ignore=shutil.ignore_patterns("__pycache__", ".codetrail"))
    application = create_app({"timeout-demo": repository})
    status, project = asyncio.run(_post(application, "/api/projects", {"name": "Overview pages", "repository_id": "timeout-demo"}))
    assert status == 200
    status, created = asyncio.run(_post(
        application,
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "overview-pages-v1", "dirty": False},
    ))
    assert status == 200
    return application, project["project_id"], created["index"]["index_id"]


async def _post(application, target: str, payload: dict) -> tuple[int, dict]:
    parsed = urlsplit(target)
    body = json.dumps(payload).encode("utf-8")
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

    await application({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http", "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"), "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "root_path": "",
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], json.loads(body or b"{}")


def _catalog(service, project_id: str, index_id: str):
    return service.get_overview(
        project_id,
        index_id,
        max_paths=MAX_OVERVIEW_PATHS,
        max_modules=MAX_OVERVIEW_MODULES,
        max_groups=MAX_OVERVIEW_GROUPS,
        max_module_relations=MAX_OVERVIEW_RELATIONS,
        max_classifications=MAX_OVERVIEW_CLASSIFICATIONS,
    )


def _page_all(application, project_id: str, index_id: str, section: str, query: str = "") -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        params = ["limit=2"]
        if query:
            params.append(query)
        if cursor:
            params.append(f"cursor={cursor}")
        suffix = "?" + "&".join(params)
        status, page = request(
            application,
            f"/api/projects/{project_id}/indexes/{index_id}/overview/sections/{section}{suffix}",
        )
        assert status == 200, page
        items.extend(page["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    return items


def test_skeleton_uses_catalog_counts_sorts_modules_and_pages(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    catalog = _catalog(application.state.workspace_service, project_id, index_id)
    status, skeleton = request(
        application,
        f"/api/projects/{project_id}/indexes/{index_id}/overview/skeleton?module_limit=1",
    )
    assert status == 200
    assert skeleton["sections"]
    catalog_totals = {
        item.section: item.total for item in catalog.section_coverage
    }
    catalog_totals["classifications"] = catalog.classification_coverage.total
    assert {item["section"]: item["total"] for item in skeleton["sections"]} == catalog_totals
    returned = {item["section"]: item["returned"] for item in skeleton["sections"]}
    assert returned["entries"] == min(len(catalog.entries), DEFAULT_SKELETON_ENTRIES)
    assert returned["groups"] == len(catalog.groups)
    assert returned["modules"] == len(skeleton["modules"])
    assert returned["classifications"] == 0
    assert returned["module_relations"] == len(skeleton["module_relations"])
    assert skeleton["classification_counts"] == catalog.classification_coverage.category_counts.model_dump()
    assert [item["path"] for item in skeleton["modules"]] == sorted(
        item.symbol.path for item in catalog.modules
    )[:1]
    if catalog.coverage.total_modules > 1:
        assert skeleton["page"]["next_cursor"]
        assert skeleton["completeness"]["status"] == "truncated"
        assert skeleton["completeness"]["hint"]


def test_section_pages_cover_catalog_without_duplicates_or_gaps(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    catalog = _catalog(application.state.workspace_service, project_id, index_id)
    expected = {
        "modules": [item.model_dump(mode="json") for item in sorted(catalog.modules, key=lambda item: (item.symbol.path, item.module_id))],
        "classifications": [item.model_dump(mode="json") for item in sorted(catalog.classifications, key=lambda item: (item.category, item.classification_id))],
        "module_relations": [item.model_dump(mode="json") for item in sorted(catalog.module_relations, key=lambda item: (-item.edge_count, item.relation_id))],
    }
    for section, catalog_items in expected.items():
        actual = _page_all(application, project_id, index_id, section)
        assert actual == catalog_items
        assert len(actual) == len({
            item["module_id"] if section == "modules" else item["classification_id"] if section == "classifications" else item["relation_id"] for item in actual
        })


def test_cursor_errors_are_structured_and_filter_bound(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    base = f"/api/projects/{project_id}/indexes/{index_id}/overview/sections"
    status, page = request(application, f"{base}/modules?limit=1")
    assert status == 200 and page["page"]["next_cursor"]
    cursor = page["page"]["next_cursor"]
    for target in (
        f"{base}/modules?limit=1&cursor={cursor}x",
        f"{base}/classifications?limit=1&cursor={cursor}",
        f"{base}/modules?limit=1&module_id=some-module&cursor={cursor}",
    ):
        status, error = request(application, target)
        assert status == 400
        assert error["code"] == "invalid_input"
        assert error["message"] == "invalid or expired cursor"
        assert error["details"][0]["field"] == "cursor"


def test_unknown_category_returns_allowed_values(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    status, error = request(
        application,
        f"/api/projects/{project_id}/indexes/{index_id}/overview/sections/classifications?category=bogus",
    )
    assert status == 422
    assert error["code"] == "invalid_input"
    detail = next(item for item in error["details"] if item["field"] == "category")
    assert detail["problem"] == "not_allowed"
    assert detail["allowed"] == ["application_entry", "public_api", "extension_point", "unknown"]


def test_module_card_and_unknown_module(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    catalog = _catalog(application.state.workspace_service, project_id, index_id)
    module_id = catalog.modules[0].module_id
    base = f"/api/projects/{project_id}/indexes/{index_id}/overview/modules"
    status, card = request(application, f"{base}/{module_id}")
    assert status == 200
    assert card["module"]["module_id"] == module_id
    assert all("evidence" not in item for item in card["classifications"])
    status, error = request(application, f"{base}/unknown-module")
    assert status == 404
    assert error["code"] == "not_found"
    assert error["message"] == "overview module was not found"


def test_full_overview_http_response_matches_default_service_result(tmp_path: Path) -> None:
    application, project_id, index_id = _create_index(tmp_path)
    status, response = request(application, f"/api/projects/{project_id}/indexes/{index_id}/overview")
    expected = application.state.workspace_service.get_overview(project_id, index_id).model_dump(mode="json")
    assert status == 200
    assert response == expected
