"""File-level import navigation through shared HTTP and MCP query logic."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.main import create_app
from test_mcp import ROOT, request


def test_file_import_neighbors_round_trip_http_and_mcp_on_fixed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp_server import create_mcp_server

    compiler = os.getenv("CODETRAIL_TYPESCRIPT_COMPILER") or str(
        ROOT / "apps" / "web" / "node_modules" / "typescript" / "lib" / "typescript.js"
    )
    if not Path(compiler).is_file():
        pytest.skip("locked TypeScript compiler is unavailable")
    monkeypatch.setenv("CODETRAIL_TYPESCRIPT_COMPILER", compiler)

    repository = tmp_path / "file-imports"
    for package in ("left", "right", "ui"):
        (repository / package).mkdir(parents=True)
    for package in ("left", "right"):
        (repository / package / "__init__.py").write_text("", encoding="utf-8")
    (repository / "left" / "consumer.py").write_text(
        "def load():\n    from .provider import fetch\n    return fetch()\n", encoding="utf-8"
    )
    (repository / "left" / "provider.py").write_text(
        "from . import core\ndef fetch():\n    return core.value()\n", encoding="utf-8"
    )
    (repository / "left" / "core.py").write_text(
        "def value():\n    return 'core'\n", encoding="utf-8"
    )
    (repository / "right" / "consumer.py").write_text(
        "def load():\n    from .provider import fetch\n    return fetch()\n", encoding="utf-8"
    )
    (repository / "right" / "provider.py").write_text("def fetch():\n    return 'right'\n", encoding="utf-8")
    (repository / "left" / "missing.py").write_text(
        "def load():\n    from .ghost import absent\n", encoding="utf-8"
    )
    (repository / "left" / "many_missing.py").write_text(
        "def load():\n" + "".join(
            f"    from .missing_{number} import item\n" for number in range(12)
        ),
        encoding="utf-8",
    )
    (repository / "ui" / "consumer.ts").write_text(
        "import { read } from './provider';\nexport function load() { return read(); }\n", encoding="utf-8"
    )
    (repository / "ui" / "provider.ts").write_text(
        "export function read() { return 1; }\n", encoding="utf-8"
    )

    application = create_app({"file-imports": repository}, data_root=tmp_path / "service-data")
    status, project = request(
        application, "POST", "/api/projects",
        {"name": "File import navigation", "repository_id": "file-imports"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application, "POST", f"/api/projects/{project_id}/indexes",
        {"revision": "file-imports-v1", "dirty": False},
    )
    assert status == 200, created
    index_id = created["index"]["index_id"]

    def module_id(path: str, at_index: str = index_id) -> str:
        query = Path(path).stem
        status, result = request(
            application, "GET",
            f"/api/projects/{project_id}/indexes/{at_index}/search?query={query}&limit=40",
        )
        assert status == 200
        return next(
            item["symbol"]["symbol_id"] for item in result["matches"]
            if item["symbol"]["path"] == path and item["symbol"]["kind"] == "module"
        )

    def get_neighbors(
        root_id: str, direction: str, at_index: str = index_id, limit: int = 40, depth: int = 1,
    ):
        return request(
            application, "GET",
            f"/api/projects/{project_id}/indexes/{at_index}/neighbors"
            f"?symbol_id={root_id}&direction={direction}&depth={depth}&limit={limit}"
            "&edge_kinds=imports&detail=full",
        )

    left_consumer = module_id("left/consumer.py")
    left_provider = module_id("left/provider.py")
    right_provider = module_id("right/provider.py")
    status, forward = get_neighbors(left_consumer, "callees")
    assert status == 200 and len(forward["neighbors"]) == 1
    item = forward["neighbors"][0]
    assert item["symbol"]["path"] == "left/provider.py"
    assert item["edge"]["kind"] == "imports"
    assert item["edge"]["source_location"] == {
        "path": "left/consumer.py", "start_line": 2, "end_line": 2,
    }
    edge_nodes = {node["symbol_id"]: node for node in forward["nodes"]}
    assert edge_nodes[item["edge"]["source_id"]]["kind"] == "function"
    assert edge_nodes[item["edge"]["target_id"]]["qualified_name"].endswith(".fetch")

    status, calls_only = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={left_consumer}&direction=callees&depth=1&limit=40&edge_kinds=calls&detail=full",
    )
    assert status == 200 and calls_only["neighbors"] == []

    status, reverse = get_neighbors(left_provider, "callers")
    assert status == 200
    assert [item["symbol"]["path"] for item in reverse["neighbors"]] == ["left/consumer.py"]
    status, right_reverse = get_neighbors(right_provider, "callers")
    assert status == 200
    assert [item["symbol"]["path"] for item in right_reverse["neighbors"]] == ["right/consumer.py"]

    status, source = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/source?path=left%2Fconsumer.py&start_line=2&end_line=2",
    )
    assert status == 200 and "from .provider import fetch" in source["evidence"]["excerpt"]
    assert source["evidence"]["index_id"] == index_id

    missing_module = module_id("left/missing.py")
    status, unresolved = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/unresolved"
        f"?module_id={missing_module}&direction=outgoing&detail=full",
    )
    assert status == 200 and any("ghost" in item["expression"] for item in unresolved["items"])
    status, missing_neighbors = get_neighbors(missing_module, "callees")
    assert status == 200 and missing_neighbors["neighbors"] == []
    assert [item["reason"] for item in missing_neighbors["unresolved"]] == ["missing_relative_import"]
    assert "unresolved_outgoing_file_references" in missing_neighbors["completeness"]["reasons"]
    assert "unresolved_outgoing_calls" not in missing_neighbors["completeness"]["reasons"]
    assert "list_unresolved(module_id=" in missing_neighbors["completeness"]["hint"]
    status, missing_calls_only = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={missing_module}&direction=callees&depth=1&limit=40&edge_kinds=calls&detail=full",
    )
    assert status == 200 and missing_calls_only["unresolved"] == []
    assert missing_calls_only["completeness"]["status"] == "complete"
    missing_function = next(
        item["symbol"]["symbol_id"] for item in request(
            application, "GET",
            f"/api/projects/{project_id}/indexes/{index_id}/search?query=load&limit=40",
        )[1]["matches"]
        if item["symbol"]["path"] == "left/missing.py" and item["symbol"]["kind"] == "function"
    )
    status, exact_symbol_imports = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={missing_function}&direction=callees&depth=1&limit=40&edge_kinds=imports&detail=full",
    )
    assert status == 200 and len(exact_symbol_imports["unresolved"]) == 1
    assert "unresolved_outgoing_calls" in exact_symbol_imports["completeness"]["reasons"]
    status, missing_summary = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={missing_module}&direction=callees&depth=1&limit=40&edge_kinds=imports&detail=summary",
    )
    assert status == 200
    assert missing_summary["unresolved_summary"]["total"] == 1
    assert missing_summary["unresolved_summary"]["by_reason"] == {"missing_relative_import": 1}
    assert "unresolved_outgoing_file_references" in missing_summary["completeness"]["reasons"]
    assert "unresolved_outgoing_calls" not in missing_summary["completeness"]["reasons"]
    many_missing_module = module_id("left/many_missing.py")
    status, many_missing = get_neighbors(many_missing_module, "callees")
    assert status == 200 and len(many_missing["unresolved"]) == 10
    assert many_missing["truncated"] is True
    assert many_missing["truncation_reason"] == "max_file_references reached"
    assert "unresolved_outgoing_file_references" in many_missing["completeness"]["reasons"]
    status, many_missing_summary = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={many_missing_module}&direction=callees&depth=1&limit=40&edge_kinds=imports&detail=summary",
    )
    assert status == 200 and many_missing_summary["unresolved_summary"]["total"] == 10
    assert "query_truncated" in many_missing_summary["completeness"]["reasons"]

    status, limited = get_neighbors(left_consumer, "callees", limit=1)
    assert status == 200 and limited["truncated"] is True
    assert limited["truncation_reason"] == "max_nodes reached"
    status, shallow = get_neighbors(left_consumer, "callees", depth=1)
    assert status == 200 and [x["symbol"]["path"] for x in shallow["neighbors"]] == ["left/provider.py"]
    status, deeper = get_neighbors(left_consumer, "callees", depth=2)
    assert status == 200
    assert {x["symbol"]["path"] for x in deeper["neighbors"]} == {
        "left/provider.py", "left/core.py",
    }

    ui_consumer = module_id("ui/consumer.ts")
    ui_provider = module_id("ui/provider.ts")

    async def compare_mcp() -> None:
        server = create_mcp_server(application.state.workspace_service)
        forward_result = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": left_consumer,
             "direction": "callees", "depth": 1, "limit": 40,
             "edge_kinds": ["imports"], "detail": "full"},
        )
        assert forward_result.structured_content["ok"] is True
        assert forward_result.structured_content["result"] == forward
        limited_result = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": left_consumer,
             "direction": "callees", "depth": 1, "limit": 1,
             "edge_kinds": ["imports"], "detail": "full"},
        )
        assert limited_result.structured_content["ok"] is True
        assert limited_result.structured_content["result"] == limited
        result = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": left_provider,
             "direction": "callers", "depth": 1, "limit": 40,
             "edge_kinds": ["imports"], "detail": "full"},
        )
        assert result.structured_content["ok"] is True
        assert result.structured_content["result"] == reverse
        source_result = await server.call_tool(
            "read_source",
            {"project_id": project_id, "index_id": index_id, "path": "left/consumer.py",
             "start_line": 2, "end_line": 2},
        )
        assert source_result.structured_content["ok"] is True
        assert source_result.structured_content["result"]["evidence"] == source["evidence"]
        summary = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": left_provider,
             "direction": "callers", "depth": 1, "limit": 40, "edge_kinds": ["imports"]},
        )
        compact = summary.structured_content["result"]["neighbors"][0]
        assert compact["to"]["at"] == "left/consumer.py:1-3"
        assert compact["edge_kind"] == "imports"
        assert compact["orientation"] == "incoming"
        assert compact["via"] == "left/consumer.py:2"
        missing_full = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": missing_module,
             "direction": "callees", "depth": 1, "limit": 40,
             "edge_kinds": ["imports"], "detail": "full"},
        )
        assert missing_full.structured_content["ok"] is True
        assert missing_full.structured_content["result"] == missing_neighbors
        missing_compact = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": missing_module,
             "direction": "callees", "depth": 1, "limit": 40,
             "edge_kinds": ["imports"]},
        )
        missing_result = missing_compact.structured_content["result"]
        assert missing_result["unresolved_summary"]["total"] == 1
        assert missing_result["unresolved_summary"]["by_reason"] == {"missing_relative_import": 1}
        assert "unresolved_outgoing_file_references" in missing_result["completeness"]["reasons"]
        assert "unresolved_outgoing_calls" not in missing_result["completeness"]["reasons"]
        ts_result = await server.call_tool(
            "get_neighbors",
            {"project_id": project_id, "index_id": index_id, "symbol_id": ui_provider,
             "direction": "callers", "depth": 1, "limit": 40,
             "edge_kinds": ["imports"], "detail": "full"},
        )
        assert ts_result.structured_content["ok"] is True
        assert [x["symbol"]["path"] for x in ts_result.structured_content["result"]["neighbors"]] == [
            "ui/consumer.ts",
        ]

    asyncio.run(compare_mcp())

    # TS imports already use module-to-module edges; verify the shared projection at both surfaces.
    status, ts_forward = get_neighbors(ui_consumer, "callees")
    assert status == 200 and [x["symbol"]["path"] for x in ts_forward["neighbors"]] == ["ui/provider.ts"]
    status, ts_reverse = get_neighbors(ui_provider, "callers")
    assert status == 200 and [x["symbol"]["path"] for x in ts_reverse["neighbors"]] == ["ui/consumer.ts"]

    (repository / "left" / "consumer.py").unlink()
    (repository / "left" / "new_consumer.py").write_text(
        "from .provider import fetch\n", encoding="utf-8"
    )
    status, newer = request(
        application, "POST", f"/api/projects/{project_id}/indexes",
        {"revision": "file-imports-v2", "dirty": True},
    )
    assert status == 200, newer
    new_index_id = newer["index"]["index_id"]
    status, old_reverse = get_neighbors(left_provider, "callers")
    assert status == 200 and [x["symbol"]["path"] for x in old_reverse["neighbors"]] == ["left/consumer.py"]
    old_source_status, old_source = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/source?path=left%2Fconsumer.py&start_line=2&end_line=2",
    )
    assert old_source_status == 200
    assert old_source["evidence"]["index_id"] == index_id
    assert "from .provider import fetch" in old_source["evidence"]["excerpt"]
    status, new_reverse = get_neighbors(module_id("left/provider.py", new_index_id), "callers", new_index_id)
    assert status == 200
    assert {x["symbol"]["path"] for x in new_reverse["neighbors"]} == {"left/new_consumer.py"}


def test_possible_incoming_file_references_mark_http_and_mcp_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp_server import create_mcp_server

    compiler = os.getenv("CODETRAIL_TYPESCRIPT_COMPILER") or str(
        ROOT / "apps" / "web" / "node_modules" / "typescript" / "lib" / "typescript.js"
    )
    if not Path(compiler).is_file():
        pytest.skip("locked TypeScript compiler is unavailable")
    monkeypatch.setenv("CODETRAIL_TYPESCRIPT_COMPILER", compiler)

    repository = tmp_path / "ambiguous-file-imports"
    (repository / "ui" / "ambiguous").mkdir(parents=True)
    (repository / "ui" / "consumer.ts").write_text(
        "import { read } from './ambiguous';\nexport function load() { return read(); }\n",
        encoding="utf-8",
    )
    (repository / "ui" / "ambiguous.ts").write_text(
        "export function read() { return 1; }\n", encoding="utf-8"
    )
    (repository / "ui" / "ambiguous" / "index.ts").write_text(
        "export function read() { return 2; }\n", encoding="utf-8"
    )
    for number in range(11):
        (repository / "ui" / f"consumer_{number}.ts").write_text(
            "import { read } from './ambiguous';\nexport function load() { return read(); }\n",
            encoding="utf-8",
        )
    application = create_app(
        {"ambiguous-file-imports": repository}, data_root=tmp_path / "service-data"
    )
    status, project = request(
        application, "POST", "/api/projects",
        {"name": "Ambiguous file imports", "repository_id": "ambiguous-file-imports"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application, "POST", f"/api/projects/{project_id}/indexes",
        {"revision": "ambiguous-file-imports-v1", "dirty": False},
    )
    assert status == 200, created
    index_id = created["index"]["index_id"]

    def module_id(path: str) -> str:
        status, result = request(
            application, "GET",
            f"/api/projects/{project_id}/indexes/{index_id}/search?query=read&limit=40",
        )
        assert status == 200
        return next(
            item["symbol"]["symbol_id"] for item in result["matches"]
            if item["symbol"]["path"] == path and item["symbol"]["kind"] == "module"
        )

    status, consumer_unresolved = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/unresolved"
        f"?module_id={module_id('ui/consumer.ts')}&direction=outgoing&detail=full",
    )
    assert status == 200
    ambiguity = next(
        item for item in consumer_unresolved["items"]
        if item["reason"] == "ambiguous_relative_import"
        and item["possible_target_ids"]
    )
    assert len(ambiguity["possible_target_ids"]) == 2

    server = create_mcp_server(application.state.workspace_service)
    for path in ("ui/ambiguous.ts", "ui/ambiguous/index.ts"):
        target_id = module_id(path)
        target_query = (
            f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
            f"?symbol_id={target_id}&direction=callers&depth=1&limit=40"
        )
        status, full = request(
            application, "GET", target_query + "&edge_kinds=imports&detail=full"
        )
        assert status == 200 and full["neighbors"] == []
        assert len(full["unresolved"]) == 10
        assert any(item["unresolved_id"] == ambiguity["unresolved_id"] for item in full["unresolved"])
        assert full["truncated"] is True
        assert full["truncation_reason"] == "max_possible_incoming_file_references reached"
        assert "possible_incoming_file_references" in full["completeness"]["reasons"]
        assert "confirmed imports or calls" in full["completeness"]["hint"]

        status, summary = request(
            application, "GET", target_query + "&edge_kinds=imports&detail=summary"
        )
        assert status == 200 and summary["neighbors"] == []
        assert summary["unresolved_summary"]["total"] == 10
        assert summary["unresolved_summary"]["by_reason"] == {"ambiguous_relative_import": 10}
        assert "possible_incoming_file_references" in summary["completeness"]["reasons"]
        assert "query_truncated" in summary["completeness"]["reasons"]

        async def mcp_neighbors(detail: str | None = None):
            arguments = {
                "project_id": project_id,
                "index_id": index_id,
                "symbol_id": target_id,
                "direction": "callers",
                "depth": 1,
                "limit": 40,
                "edge_kinds": ["imports"],
            }
            if detail is not None:
                arguments["detail"] = detail
            return await server.call_tool("get_neighbors", arguments)

        mcp_full = asyncio.run(mcp_neighbors("full")).structured_content["result"]
        assert mcp_full == full
        mcp_summary = asyncio.run(mcp_neighbors()).structured_content["result"]
        assert mcp_summary == summary

        # Calls-only remains on the exact symbol graph and does not inherit import candidates.
        status, calls_only = request(
            application, "GET", target_query + "&edge_kinds=calls&detail=full"
        )
        assert status == 200
        assert "possible_incoming_file_references" not in calls_only["completeness"]["reasons"]


def test_file_reference_projection_keeps_open_reasons_and_mixed_facts_generic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp_server import create_mcp_server

    compiler = os.getenv("CODETRAIL_TYPESCRIPT_COMPILER") or str(
        ROOT / "apps" / "web" / "node_modules" / "typescript" / "lib" / "typescript.js"
    )
    if not Path(compiler).is_file():
        pytest.skip("locked TypeScript compiler is unavailable")
    monkeypatch.setenv("CODETRAIL_TYPESCRIPT_COMPILER", compiler)

    repository = tmp_path / "open-reason-file-refs"
    repository.mkdir()
    (repository / "consumer.ts").write_text(
        "import { run } from '@/missing';\nexport function entry() { run(); }\n",
        encoding="utf-8",
    )
    (repository / "provider.ts").write_text(
        "export function run() { return 1; }\n", encoding="utf-8"
    )
    application = create_app(
        {"open-reason-file-refs": repository}, data_root=tmp_path / "service-data"
    )
    status, project = request(
        application, "POST", "/api/projects",
        {"name": "Open reason references", "repository_id": "open-reason-file-refs"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application, "POST", f"/api/projects/{project_id}/indexes",
        {"revision": "open-reason-v1", "dirty": False},
    )
    assert status == 200, created
    index_id = created["index"]["index_id"]

    def search(query: str) -> dict:
        status, result = request(
            application, "GET",
            f"/api/projects/{project_id}/indexes/{index_id}/search?query={query}&limit=40",
        )
        assert status == 200
        return result

    consumer_module = next(
        item["symbol"]["symbol_id"] for item in search("consumer")["matches"]
        if item["symbol"]["path"] == "consumer.ts" and item["symbol"]["kind"] == "module"
    )
    provider_module = next(
        item["symbol"]["symbol_id"] for item in search("provider")["matches"]
        if item["symbol"]["path"] == "provider.ts" and item["symbol"]["kind"] == "module"
    )
    entry_function = next(
        item["symbol"]["symbol_id"] for item in search("entry")["matches"]
        if item["symbol"]["qualified_name"].endswith(".entry")
    )

    # Synthetic persisted open reason models alias reasons introduced by newer analyzers.
    # The legacy baseline import and failed call already share external_import_or_path_alias.
    with application.state.workspace_service.registry.store_for_repository(
        "open-reason-file-refs"
    ) as store:
        store._connection.execute(
            "INSERT INTO unresolved_references "
            "(index_id, unresolved_id, source_symbol_id, path, expression, reason, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                index_id, "unresolved-synthetic-invalid-tsconfig", consumer_module,
                "consumer.ts", "@/provider", "invalid_tsconfig", 1, 1,
            ),
        )
        store._connection.execute(
            "INSERT INTO unresolved_targets (index_id, unresolved_id, target_symbol_id) "
            "VALUES (?, ?, ?)",
            (index_id, "unresolved-synthetic-invalid-tsconfig", provider_module),
        )
        store._connection.commit()

    module_url = f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
    status, outgoing_full = request(
        application, "GET",
        f"{module_url}?symbol_id={consumer_module}&direction=callees&depth=1&limit=40"
        "&edge_kinds=imports&detail=full",
    )
    assert status == 200 and outgoing_full["neighbors"] == []
    assert {item["reason"] for item in outgoing_full["unresolved"]} == {
        "external_import_or_path_alias", "invalid_tsconfig",
    }
    assert sum(item["expression"] == "run" for item in outgoing_full["unresolved"]) == 1
    assert outgoing_full["completeness"]["unresolved_outgoing_count"] is None
    assert "unresolved_outgoing_file_references" in outgoing_full["completeness"]["reasons"]
    assert "do not always distinguish imports from calls" in outgoing_full["completeness"]["hint"]

    status, outgoing_summary = request(
        application, "GET",
        f"{module_url}?symbol_id={consumer_module}&direction=callees&depth=1&limit=40"
        "&edge_kinds=imports&detail=summary",
    )
    assert status == 200 and outgoing_summary["neighbors"] == []
    assert outgoing_summary["unresolved_summary"]["total"] == 3
    assert outgoing_summary["unresolved_summary"]["by_reason"] == {
        "external_import_or_path_alias": 2, "invalid_tsconfig": 1,
    }

    status, incoming_full = request(
        application, "GET",
        f"{module_url}?symbol_id={provider_module}&direction=callers&depth=1&limit=40"
        "&edge_kinds=imports&detail=full",
    )
    assert status == 200 and incoming_full["neighbors"] == []
    assert [item["reason"] for item in incoming_full["unresolved"]] == ["invalid_tsconfig"]
    assert "possible_incoming_file_references" in incoming_full["completeness"]["reasons"]
    status, incoming_summary = request(
        application, "GET",
        f"{module_url}?symbol_id={provider_module}&direction=callers&depth=1&limit=40"
        "&edge_kinds=imports&detail=summary",
    )
    assert status == 200 and incoming_summary["neighbors"] == []
    assert incoming_summary["unresolved_summary"]["by_reason"] == {"invalid_tsconfig": 1}

    # Calls-only exact symbol queries retain their existing unresolved call count.
    status, exact_calls = request(
        application, "GET",
        f"{module_url}?symbol_id={entry_function}&direction=callees&depth=1&limit=40"
        "&edge_kinds=calls&detail=full",
    )
    assert status == 200
    assert [item["expression"] for item in exact_calls["unresolved"]] == ["run"]
    assert exact_calls["completeness"]["unresolved_outgoing_count"] == 1

    server = create_mcp_server(application.state.workspace_service)

    async def mcp_result(symbol_id: str, direction: str, detail: str | None = None):
        arguments = {
            "project_id": project_id,
            "index_id": index_id,
            "symbol_id": symbol_id,
            "direction": direction,
            "depth": 1,
            "limit": 40,
            "edge_kinds": ["imports"],
        }
        if detail is not None:
            arguments["detail"] = detail
        result = await server.call_tool("get_neighbors", arguments)
        assert result.structured_content["ok"] is True
        return result.structured_content["result"]

    assert asyncio.run(mcp_result(consumer_module, "callees", "full")) == outgoing_full
    assert asyncio.run(mcp_result(consumer_module, "callees")) == outgoing_summary
    assert asyncio.run(mcp_result(provider_module, "callers", "full")) == incoming_full
    assert asyncio.run(mcp_result(provider_module, "callers")) == incoming_summary


def test_file_reference_unresolved_scan_is_bounded_and_root_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp_server import create_mcp_server

    compiler = os.getenv("CODETRAIL_TYPESCRIPT_COMPILER") or str(
        ROOT / "apps" / "web" / "node_modules" / "typescript" / "lib" / "typescript.js"
    )
    if not Path(compiler).is_file():
        pytest.skip("locked TypeScript compiler is unavailable")
    monkeypatch.setenv("CODETRAIL_TYPESCRIPT_COMPILER", compiler)

    repository = tmp_path / "bounded-file-refs"
    repository.mkdir()
    consumer_source = (
        "import { target } from './provider';\n"
        + "\n".join(f"unknown_top_{number}();" for number in range(8))
        + "\nexport function entry() {\n"
        + "\n".join(f"  unknown_nested_{number}();" for number in range(16))
        + "\n}\n"
    )
    provider_source = (
        "export function target() {\n"
        + "\n".join(f"  unresolved_provider_{number}();" for number in range(30))
        + "\n}\n"
    )
    (repository / "consumer.ts").write_text(consumer_source, encoding="utf-8")
    (repository / "provider.ts").write_text(provider_source, encoding="utf-8")
    application = create_app(
        {"bounded-file-refs": repository}, data_root=tmp_path / "service-data"
    )
    status, project = request(
        application, "POST", "/api/projects",
        {"name": "Bounded file references", "repository_id": "bounded-file-refs"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, created = request(
        application, "POST", f"/api/projects/{project_id}/indexes",
        {"revision": "bounded-file-refs-v1", "dirty": False},
    )
    assert status == 200, created
    index_id = created["index"]["index_id"]
    status, search_result = request(
        application, "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/search?query=consumer&limit=20",
    )
    assert status == 200
    consumer_module = next(
        item["symbol"]["symbol_id"] for item in search_result["matches"]
        if item["symbol"]["kind"] == "module"
    )

    query = (
        f"/api/projects/{project_id}/indexes/{index_id}/neighbors"
        f"?symbol_id={consumer_module}&direction=callees&depth=2&limit=40"
        "&edge_kinds=imports&detail=full"
    )
    status, full = request(application, "GET", query)
    assert status == 200
    assert [item["symbol"]["path"] for item in full["neighbors"]] == ["provider.ts"]
    assert len(full["unresolved"]) == 10
    assert {item["path"] for item in full["unresolved"]} == {"consumer.ts"}
    assert full["truncated"] is True
    assert full["truncation_reason"] == "max_file_references reached"
    assert "file_reference_scope_limited" in full["completeness"]["reasons"]
    assert "only for the root file" in full["completeness"]["hint"]

    status, summary = request(application, "GET", query.replace("detail=full", "detail=summary"))
    assert status == 200
    assert summary["unresolved_summary"]["total"] == 10
    assert summary["unresolved_summary"]["by_source"]
    assert "file_reference_scope_limited" in summary["completeness"]["reasons"]

    server = create_mcp_server(application.state.workspace_service)

    async def mcp_query(detail: str | None = None):
        arguments = {
            "project_id": project_id,
            "index_id": index_id,
            "symbol_id": consumer_module,
            "direction": "callees",
            "depth": 2,
            "limit": 40,
            "edge_kinds": ["imports"],
        }
        if detail is not None:
            arguments["detail"] = detail
        result = await server.call_tool("get_neighbors", arguments)
        assert result.structured_content["ok"] is True
        return result.structured_content["result"]

    assert asyncio.run(mcp_query("full")) == full
    assert asyncio.run(mcp_query()) == summary

    # A direct storage query with a tiny output allowance must not return over-budget details.
    with application.state.workspace_service.registry.store_for_repository("bounded-file-refs") as store:
        bounded = store.get_neighbors(
            index_id,
            consumer_module,
            "callees",
            depth=2,
            limit=40,
            edge_kinds=("imports",),
            max_output_bytes=1,
        )
    assert bounded.truncated is True
    assert bounded.truncation_reason == "max_output_bytes reached"
    assert bounded.unresolved == ()
