"""Offline checks for bounded TypeScript snapshot facts and HTTP shape links."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import app.indexing as indexing
from app.indexing import IndexingAborted, IndexingControl, SnapshotFile, build_source_snapshot, extract_definitions
from app.main import create_app
from app.relations import extract_relations


async def _request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
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
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": parsed.path, "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"), "headers": headers,
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "root_path": "",
    }
    await application(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    response = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return start["status"], json.loads(response or b"{}")


def request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    return asyncio.run(_request(application, method, target, payload))


def _write_http_project(root: Path, *, duplicate_route: bool = False) -> None:
    (root / "server").mkdir(parents=True)
    (root / "web").mkdir()
    route = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/health')\n"
        "def health(): return {'ok': True}\n"
    )
    if duplicate_route:
        route += "@app.get('/api/health')\ndef health_again(): return {'ok': True}\n"
    (root / "server" / "app.py").write_text(route, encoding="utf-8")
    (root / "web" / "api.ts").write_text(
        'const apiBaseUrl = "http://remote.example";\n'
        "function requestJson(path: string, validator: unknown, init: RequestInit = {}) {\n"
        "  return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' });\n"
        "}\n"
        'export function getHealth() { return requestJson("/api/health", undefined); }\n'
        'export const arrowHealth = () => requestJson("/api/health", undefined);\n'
        'export function getDynamic(path: string) { return requestJson(path, undefined); }\n',
        encoding="utf-8",
    )


def _write_parameterized_http_project(
    root: Path,
    *,
    overlapping_literal: bool = False,
    only_literal: bool = False,
) -> None:
    (root / "server").mkdir(parents=True)
    (root / "web").mkdir()
    routes = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/api/projects/{project_id}/indexes/{index_id}/overview')\n"
        "def get_overview(project_id: str, index_id: str): return {}\n"
        "@app.get('/api/projects/{project_id}/indexes/{index_id}/source')\n"
        "def read_source(project_id: str, index_id: str): return {}\n"
        "@app.post('/api/projects/{project_id}/sessions')\n"
        "def create_session(project_id: str): return {}\n"
        "@app.get('/api/files/{rest:path}')\n"
        "def read_file(rest: str): return {}\n"
    )
    if overlapping_literal or only_literal:
        routes += (
            "@app.get('/api/projects/me/indexes/{index_id}/overview')\n"
            "def get_me_overview(index_id: str): return {}\n"
        )
    if only_literal:
        routes = routes.replace(
            "@app.get('/api/projects/{project_id}/indexes/{index_id}/overview')\n"
            "def get_overview(project_id: str, index_id: str): return {}\n",
            "",
        )
    (root / "server" / "app.py").write_text(routes, encoding="utf-8")
    client = '''const apiBaseUrl = "http://remote.example";
function requestJson(path: string, validator: unknown, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' });
}
export function getOverview(projectId: string, indexId: string) {
  const params = new URLSearchParams({ max_depth: "4" });
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/overview?${params.toString()}`, undefined);
}
export function readProjectSource(projectId: string, indexId: string) {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/indexes/${encodeURIComponent(indexId)}/source`, undefined);
}
export function createProjectSession(projectId: string) {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/sessions`, undefined, { method: "POST" });
}
export function getUnsafe(projectId: string) {
  return requestJson(`/api/projects/prefix-${encodeURIComponent(projectId)}/overview`, undefined);
}
export function getUnencoded(projectId: string) {
  return requestJson(`/api/projects/${projectId}/overview`, undefined);
}
export function getUnsafeQuery(projectId: string, cursor: string) {
  return requestJson(`/api/projects/${encodeURIComponent(projectId)}/overview?cursor=${cursor}`, undefined);
}
export function getLiteralBraces() {
  const params = new URLSearchParams();
  return requestJson(`/api/items/{*}?${params.toString()}`, undefined);
}
'''
    (root / "web" / "api.ts").write_text(client, encoding="utf-8")


def _create_index(application) -> tuple[str, str]:
    status, project = request(application, "POST", "/api/projects", {"name": "TypeScript", "repository_id": "ts"})
    assert status == 200
    status, result = request(application, "POST", f"/api/projects/{project['project_id']}/indexes", {"revision": "test", "dirty": True})
    assert status == 200
    return project["project_id"], result["index"]["index_id"]


def test_typescript_facts_use_locked_ast_and_keep_languages_and_generated_dirs_separate(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("def python_fn(): pass\n", encoding="utf-8")
    (tmp_path / "foo.ts").write_text('import { x } from "./other"; export function tsFn() { return x; }\n', encoding="utf-8")
    (tmp_path / "other.tsx").write_text('export const View = () => <div />;\n', encoding="utf-8")
    (tmp_path / ".next" / "server").mkdir(parents=True)
    (tmp_path / ".next" / "server" / "generated.ts").write_text("export function ignored() {}\n", encoding="utf-8")

    snapshot = build_source_snapshot(tmp_path, repository_id="ts", revision="test")
    definitions = extract_definitions(snapshot)
    symbols = definitions.symbols
    module_ids = {item.path: item.symbol_id for item in symbols if item.kind == "module"}
    names = {item.qualified_name for item in symbols}

    assert snapshot.index.status == "ready"
    assert ".next/server/generated.ts" not in {item.path for item in snapshot.files}
    assert module_ids["foo.py"] != module_ids["foo.ts"]
    assert "foo.tsFn" in names
    assert "other.View" in names
    assert definitions.typescript_parse_file_count == 2

    cached = dict(definitions.cache_entries)
    repeated = extract_definitions(snapshot, cached=cached)
    assert repeated.cache_hits == 1
    assert repeated.typescript_parse_file_count == 2


def test_typescript_syntax_or_missing_locked_parser_fails_index_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "broken.ts"
    source.write_text("export function broken( {\n", encoding="utf-8")
    snapshot = build_source_snapshot(tmp_path, repository_id="ts", revision="bad")
    broken = extract_definitions(snapshot)
    assert broken.snapshot.index.status == "failed"
    assert any(issue.kind == "typescript_syntax_error" for issue in broken.issues)

    source.write_text("export const ok = 1;\n", encoding="utf-8")
    snapshot = build_source_snapshot(tmp_path, repository_id="ts", revision="no-node")
    monkeypatch.setattr(indexing.shutil, "which", lambda _name: None)
    unavailable = extract_definitions(snapshot)
    assert unavailable.snapshot.index.status == "failed"
    assert any(issue.kind == "parser_unavailable" for issue in unavailable.issues)


def test_cancel_during_slow_typescript_parse_kills_parser_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "parser.pid"
    script = "import pathlib,sys,time; sys.stdin.read(); pathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid())); time.sleep(60)"
    original_popen = subprocess.Popen
    children: list[subprocess.Popen] = []

    def slow_parser(*_args, **kwargs):
        child = original_popen([sys.executable, "-c", script, str(pid_file)], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(indexing, "_TYPESCRIPT_PARSER_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(indexing.subprocess, "Popen", slow_parser)
    control = IndexingControl()
    failures: list[BaseException] = []
    source = SnapshotFile("slow.ts", 1, "hash", "export const slow = true;\n")

    def parse() -> None:
        try:
            indexing._extract_typescript_facts((source,), control=control)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=parse)
    worker.start()
    deadline = time.monotonic() + 5
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.exists(), "slow parser did not start"
    control.cancel()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], IndexingAborted)
    assert failures[0].code == "index_cancelled"
    assert len(children) == 1 and children[0].poll() is not None


def test_unresolved_capacity_does_not_hide_later_typescript_import_edges(tmp_path: Path) -> None:
    (tmp_path / "caller.ts").write_text(
        'import { first } from "external-one";\n'
        'import { second } from "external-two";\n'
        'import { left } from "./left";\n'
        'import { right } from "./right";\n',
        encoding="utf-8",
    )
    (tmp_path / "left.ts").write_text("export const left = 1;\n", encoding="utf-8")
    (tmp_path / "right.ts").write_text("export const right = 1;\n", encoding="utf-8")
    definitions = extract_definitions(build_source_snapshot(tmp_path, repository_id="ts", revision="test"))
    result = extract_relations(definitions, max_unresolved=1)
    assert {(edge.source_location.path, edge.target_id) for edge in result.edges if edge.kind == "imports"} == {
        ("caller.ts", next(symbol.symbol_id for symbol in result.symbols if symbol.path == "left.ts" and symbol.kind == "module")),
        ("caller.ts", next(symbol.symbol_id for symbol in result.symbols if symbol.path == "right.ts" and symbol.kind == "module")),
    }
    assert result.unresolved_truncated is True


def test_typescript_calls_resolve_proven_bindings_and_keep_ambiguous_calls_unresolved(tmp_path: Path) -> None:
    (tmp_path / "lib.ts").write_text(
        "export function importedTarget() {}\n"
        "function anotherTarget() {}\n"
        "function noop() {}\n"
        "export function wrap(callback: () => void) { return noop; }\n"
        "export const wrapped = wrap(() => importedTarget());\n"
        "export { importedTarget as duplicated };\n"
        "export { anotherTarget as duplicated };\n"
        "export function f() {}\n"
        "export function sibling() {}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text(
        'import { importedTarget as alias } from "./lib";\n'
        'import { f, wrapped as importedWrapped } from "./lib";\n'
        'import { sibling } from "./lib";\n'
        'import { absent, duplicated } from "./lib";\n'
        'import { readProjectSource } from "@/lib/api";\n'
        "function localTarget() {}\n"
        "const functionValue = () => {};\n"
        "function withCallback(callback: () => void) { callback(); }\n"
        "export function entry() {\n"
        "  localTarget();\n"
        "  functionValue();\n"
        "  alias();\n"
        "}\n"
        "function missingExport() { absent(); }\n"
        "function ambiguousExport() { duplicated(); }\n"
        "function runWrapped() {\n"
        "  const wrapped = withCallback(() => functionValue(), []);\n"
        "  return wrapped;\n"
        "}\n"
        "export const openSource = withCallback(() => readProjectSource(), []);\n"
        "function shadow(leaf: () => void) { leaf(); }\n"
        "function rebound() {\n"
        "  let changing = functionValue;\n"
        "  changing();\n"
        "  changing = () => {};\n"
        "  changing();\n"
        "}\n"
        "function closureParameter(f: () => void) { function inner() { f(); } }\n"
        "function closureVariable() { const f = () => importedTarget(); function inner() { f(); } }\n"
        "function closureRebound() { let f = () => {}; function inner() { f(); } f = () => {}; }\n"
        "function closureFunctionRebound() { function f() {} function inner() { f(); } f = () => {}; }\n"
        "function blockScopes() { { const sibling = () => {}; sibling(); } { sibling(); } }\n"
        "const namedExpression = function f() { f(); };\n"
        "function invokeWrapped() { importedWrapped(); }\n"
        "function invokesLocalWrapper() { const localWrapped = withCallback(() => alias(), []); localWrapped(); }\n"
        "function usesMethod(service: { run: () => void }) { service.run(); }\n"
        "function catches() { try {} catch (localTarget) { localTarget(); } }\n"
        "function duplicateFunctionBindings() { function f() {} function f() {} f(); }\n"
        "const shadowedNamedExpression = function f() { var f; f(); };\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(build_source_snapshot(tmp_path, repository_id="ts", revision="calls"))
    result = extract_relations(definitions)
    symbols = {symbol.symbol_id: symbol for symbol in result.symbols}
    resolved = {
        (symbols[edge.source_id].qualified_name, symbols[edge.target_id].qualified_name, edge.source_location.start_line)
        for edge in result.edges
        if edge.kind == "calls" and edge.extraction_method == "ast"
    }
    assert resolved == {
        ("main.entry", "main.localTarget", 10),
        ("main.entry", "main.functionValue", 11),
        ("main.entry", "lib.importedTarget", 12),
        ("main.runWrapped", "main.withCallback", 17),
        ("main.runWrapped.wrapped", "main.functionValue", 17),
        ("main.invokesLocalWrapper", "main.withCallback", 35),
        ("main.closureVariable.inner", "main.closureVariable.f", 29),
        ("main.blockScopes", "lib.sibling", 32),
        ("main.namedExpression", "main.namedExpression", 33),
        ("main.invokesLocalWrapper.localWrapped", "lib.importedTarget", 35),
        ("lib.wrapped", "lib.importedTarget", 5),
    }
    unresolved = {(item.expression, item.reason) for item in result.unresolved}
    assert ("readProjectSource", "external_import_or_path_alias") in unresolved
    assert ("leaf", "name_shadowed_in_scope") in unresolved
    assert ("changing", "name_rebound_in_scope") in unresolved
    assert ("service.run", "object_method_not_resolved") in unresolved
    assert ("localTarget", "name_shadowed_in_scope") in unresolved
    assert ("f", "name_rebound_in_scope") in unresolved
    assert ("f", "name_shadowed_in_scope") in unresolved
    assert ("sibling", "name_shadowed_in_scope") in unresolved
    assert ("f", "ambiguous_call_target") in unresolved
    unresolved_sources = {
        (symbols[item.source_symbol_id].qualified_name, item.expression, item.reason)
        for item in result.unresolved
    }
    assert ("main.duplicateFunctionBindings", "f", "ambiguous_call_target") in unresolved_sources
    assert ("main.shadowedNamedExpression", "f", "name_shadowed_in_scope") in unresolved_sources
    assert ("importedWrapped", "missing_named_export") in unresolved
    assert ("localWrapped", "name_shadowed_in_scope") in unresolved
    assert ("absent", "missing_named_export") in unresolved
    assert ("duplicated", "ambiguous_named_import_target") in unresolved
    assert not result.truncated


def test_typescript_call_budget_marks_relation_coverage_truncated(tmp_path: Path) -> None:
    calls = "\n".join("  target();" for _ in range(5_001))
    (tmp_path / "many.ts").write_text(f"function target() {{}}\nfunction caller() {{\n{calls}\n}}\n", encoding="utf-8")

    definitions = extract_definitions(build_source_snapshot(tmp_path, repository_id="ts", revision="budget"))
    assert len(definitions.typescript_calls) == 5_000
    assert definitions.typescript_calls_truncated
    result = extract_relations(definitions, max_edges=6_000)
    assert result.truncated
    assert result.truncation_reason == "max_typescript_calls reached"


def test_http_request_shape_rejects_shadowed_or_mutable_fetch_wrappers(tmp_path: Path) -> None:
    _write_http_project(tmp_path)
    (tmp_path / "web" / "api.ts").write_text("export const placeholder = 1;\n", encoding="utf-8")
    wrapper = (
        'const apiBaseUrl = "http://remote.example";\n'
        "function requestJson(path: string, validator: unknown, init: RequestInit = {}) {\n"
        "  return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' });\n"
        "}\n"
    )
    clients = 'function getHealth() { return requestJson("/api/health", undefined); }\n'
    (tmp_path / "web" / "shadowed.ts").write_text(
        'const { fetch } = globalThis;\n' + wrapper + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "override.ts").write_text(
        wrapper.replace("{ ...init, headers:", '{ ...init, method: "POST", headers:') + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "mutated.ts").write_text(
        wrapper.replace("  return fetch", '  init.method = "POST";\n  return fetch') + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "reassigned.ts").write_text(
        wrapper + clients + 'requestJson = (path, validator, init) => fetch(path, init);\n',
        encoding="utf-8",
    )
    (tmp_path / "web" / "nested.ts").write_text(
        wrapper + "function getHealth({ requestJson }) { requestJson('/api/health', undefined); }\n",
        encoding="utf-8",
    )
    nested_fetch_wrapper = wrapper.replace(
        "  return fetch(`", '  setTimeout(() => fetch("/api/nested-callback"), 0);\n  return fetch(`', 1
    )
    (tmp_path / "web" / "nested-fetch.ts").write_text(
        nested_fetch_wrapper + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "body-shadow.ts").write_text(
        wrapper.replace("  return fetch", "  const { init } = options;\n  return fetch") + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "duplicate.ts").write_text(
        wrapper + 'function getHealth() { return requestJson("/api/health", undefined, { method: "GET", method: "POST" }); }\n',
        encoding="utf-8",
    )
    (tmp_path / "web" / "computed.ts").write_text(
        wrapper + 'function getHealth() { return requestJson("/api/health", undefined, { ["method"]: "POST" }); }\n',
        encoding="utf-8",
    )
    (tmp_path / "web" / "shorthand.ts").write_text(
        wrapper.replace("headers: {},", "headers,") + clients,
        encoding="utf-8",
    )
    (tmp_path / "web" / "ambient.ts").write_text(
        'const apiBaseUrl = "http://remote.example";\n'
        'declare function requestJson(path: string, validator: unknown, init?: RequestInit): Promise<unknown>;\n'
        'function getHealth() { return requestJson("/api/health", undefined); }\n',
        encoding="utf-8",
    )

    definitions = extract_definitions(build_source_snapshot(tmp_path, repository_id="ts", revision="test"))
    requests = {path: fact for path, fact in definitions.typescript_http_requests}
    assert set(requests) == {"web/duplicate.ts", "web/computed.ts", "web/shorthand.ts", "web/nested-fetch.ts"}
    assert all(requests[path].method is None and requests[path].unknown_reason == "dynamic_method" for path in ("web/duplicate.ts", "web/computed.ts"))
    assert requests["web/shorthand.ts"].method == "GET"
    assert requests["web/shorthand.ts"].unknown_reason is None
    assert requests["web/nested-fetch.ts"].method == "GET"
    assert requests["web/nested-fetch.ts"].unknown_reason is None


def test_parameterized_paths_require_unshadowed_globals_and_top_level_query_params(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    wrapper = '''const apiBaseUrl = "http://remote.example";
function requestJson(path: string, validator: unknown, init: RequestInit = {}) {
  return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' });
}
'''
    cases = {
        "good.ts": '''function good(id: string) {
  const params = new URLSearchParams();
  return requestJson(`/api/projects/${encodeURIComponent(id)}?${params.toString()}`, undefined);
}
''',
        "shadowed-encode.ts": '''function bad(encodeURIComponent: (value: string) => string, id: string) {
  return requestJson(`/api/projects/${encodeURIComponent(id)}`, undefined);
}
''',
        "written-encode.ts": '''encodeURIComponent = (value) => value;
function bad(id: string) { return requestJson(`/api/projects/${encodeURIComponent(id)}`, undefined); }
''',
        "shadowed-search.ts": '''function bad(URLSearchParams: unknown, id: string) {
  const params = new URLSearchParams();
  return requestJson(`/api/projects/${encodeURIComponent(id)}?${params.toString()}`, undefined);
}
''',
        "nested-query.ts": '''function bad(params: unknown, id: string) {
  if (id) { const params = new URLSearchParams(); }
  return requestJson(`/api/projects/${encodeURIComponent(id)}?${params.toString()}`, undefined);
}
''',
        "aliased-query.ts": '''function bad(id: string) {
  const params = new URLSearchParams();
  const alias = params;
  alias.set("cursor", "anything");
  return requestJson(`/api/projects/${encodeURIComponent(id)}?${params.toString()}`, undefined);
}
''',
    }
    for name, body in cases.items():
        (web / name).write_text(wrapper + body, encoding="utf-8")

    definitions = extract_definitions(build_source_snapshot(tmp_path, repository_id="ts", revision="test"))
    requests = {path: fact for path, fact in definitions.typescript_http_requests}
    assert requests["web/good.ts"].path_kind == "parameterized"
    assert requests["web/good.ts"].path_template == "/api/projects/{*}"
    assert requests["web/good.ts"].unknown_reason is None
    assert all(
        requests[path].unknown_reason == "dynamic_path"
        for path in (
            "web/shadowed-encode.ts",
            "web/written-encode.ts",
            "web/shadowed-search.ts",
            "web/nested-query.ts",
            "web/aliased-query.ts",
        )
    )


def test_overview_adds_separate_same_index_http_shape_associations(tmp_path: Path) -> None:
    _write_http_project(tmp_path)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    assert overview["http_association_coverage"] == {
        "analysis_status": "complete",
        "path_analysis": "parameterized",
        "client_request_count": 3,
        "matched_count": 2,
        "visible_match_count": 2,
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "unknown_count": 1,
    }
    assert {(item["method"], item["path"], item["client_name"]) for item in overview["http_associations"]} == {
        ("GET", "/api/health", "getHealth"),
        ("GET", "/api/health", "arrowHealth"),
    }
    assert all(item["index_id"] == index_id and item["client_evidence"]["index_id"] == index_id for item in overview["http_associations"])
    assert all(item["client_module"]["path"] == "web/api.ts" and item["server_module"]["path"] == "server/app.py" for item in overview["http_associations"])
    assert any("apiBaseUrl 可配置为远端" in note for note in overview["notes"])


def test_parameterized_request_templates_match_same_shape_and_strip_safe_query(tmp_path: Path) -> None:
    _write_parameterized_http_project(tmp_path)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    coverage = overview["http_association_coverage"]
    assert coverage == {
        "analysis_status": "complete",
        "path_analysis": "parameterized",
        "client_request_count": 7,
        "matched_count": 3,
        "visible_match_count": 3,
        "unmatched_count": 1,
        "ambiguous_count": 0,
        "unknown_count": 3,
    }
    found = {item["client_name"]: item for item in overview["http_associations"]}
    assert set(found) == {"getOverview", "readProjectSource", "createProjectSession"}
    assert found["getOverview"]["path"] == "/api/projects/{project_id}/indexes/{index_id}/overview"
    assert found["getOverview"]["client_path_kind"] == "parameterized"
    assert found["getOverview"]["client_path_template"] == "/api/projects/{*}/indexes/{*}/overview"
    assert found["createProjectSession"]["method"] == "POST"
    assert all(item["index_id"] == index_id for item in found.values())


@pytest.mark.parametrize("only_literal", [False, True])
def test_parameterized_path_overlaps_are_ambiguous_or_unknown_not_literal_matches(
    tmp_path: Path,
    only_literal: bool,
) -> None:
    _write_parameterized_http_project(tmp_path, overlapping_literal=True, only_literal=only_literal)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    coverage = overview["http_association_coverage"]
    if only_literal:
        assert coverage["matched_count"] == 2
        assert coverage["ambiguous_count"] == 0
        assert coverage["unknown_count"] == 4
    else:
        assert coverage["matched_count"] == 2
        assert coverage["ambiguous_count"] == 1
    assert not any(item["client_name"] == "getOverview" for item in overview["http_associations"])


def test_persisted_v1_literal_http_fact_survives_additive_path_shape_migration(tmp_path: Path) -> None:
    _write_http_project(tmp_path)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    with application.state.registry.store_for_repository("ts") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = ? WHERE index_id = ?",
            ("source-v3+python-3.13+typescript-5.9.3+typescript-facts-v1+local", index_id),
        )
    database = tmp_path / ".codetrail" / "index.sqlite3"
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE http_client_requests DROP COLUMN request_path_template")
        connection.execute("ALTER TABLE http_client_requests DROP COLUMN path_kind")

    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    assert overview["http_association_coverage"]["path_analysis"] == "literal_only"
    assert overview["http_association_coverage"]["analysis_status"] == "complete"
    assert overview["http_association_coverage"]["matched_count"] == 2
    assert {(item["method"], item["path"]) for item in overview["http_associations"]} == {("GET", "/api/health")}


@pytest.mark.parametrize(
    "rules_version",
    [
        "python-source-v2+python-3.13+relations-v3",
        "source-v3+python-3.13+typescript-5.9.3+relations-v6",
    ],
)
def test_snapshot_without_typescript_fact_rule_says_not_analyzed(
    tmp_path: Path,
    rules_version: str,
) -> None:
    (tmp_path / "app.py").write_text("def main(): pass\n", encoding="utf-8")
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    with application.state.registry.store_for_repository("ts") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = ? WHERE index_id = ?",
            (rules_version, index_id),
        )
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    assert overview["http_association_coverage"]["analysis_status"] == "not_analyzed"
    assert overview["http_association_coverage"]["client_request_count"] == 0
    assert any("coverage 为 not_analyzed" in note for note in overview["notes"])


def test_typescript_source_change_refreshes_to_new_index_and_keeps_old_source(tmp_path: Path) -> None:
    client = tmp_path / "client.ts"
    client.write_text("export const value = 'old';\n", encoding="utf-8")
    application = create_app({"ts": tmp_path})
    project_id, old_index_id = _create_index(application)
    client.write_text("export const value = 'new';\n", encoding="utf-8")

    status, freshness = request(application, "POST", f"/api/projects/{project_id}/indexes/{old_index_id}/freshness")
    assert status == 200
    assert freshness["status"] == "changed"
    assert "client.ts" in freshness["changed_paths"]
    status, refreshed = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{old_index_id}/refresh",
        {"revision": "changed-ts", "dirty": True},
    )
    assert status == 200
    new_index_id = refreshed["index"]["index"]["index_id"]
    assert refreshed["mode"] == "created"
    assert new_index_id != old_index_id
    with application.state.registry.store_for_repository("ts") as store:
        assert store.read_evidence(old_index_id, "client.ts", 1, 1).excerpt == "export const value = 'old';"
        assert store.read_evidence(new_index_id, "client.ts", 1, 1).excerpt == "export const value = 'new';"


def test_typescript_rule_upgrade_changes_index_identity_and_preserves_v1_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.service as service_module

    _write_http_project(tmp_path)
    application = create_app({"ts": tmp_path})
    status, project = request(application, "POST", "/api/projects", {"name": "TypeScript v1", "repository_id": "ts"})
    assert status == 200
    project_id = project["project_id"]
    monkeypatch.setattr(service_module, "INDEX_BUILD_IDENTITY_VERSION", "relations-v12")
    status, old_result = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "same-source", "dirty": True},
    )
    assert status == 200
    old_index_id = old_result["index"]["index_id"]
    with application.state.registry.store_for_repository("ts") as store:
        old_row = store.connection.execute(
            "SELECT snapshot_hash FROM index_snapshots WHERE index_id = ?",
            (old_index_id,),
        ).fetchone()
        old_hash = old_row["snapshot_hash"]
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = ? WHERE index_id = ?",
            ("source-v3+python-3.13+typescript-5.9.3+typescript-facts-v3+relations-v12", old_index_id),
        )

    monkeypatch.setattr(service_module, "INDEX_BUILD_IDENTITY_VERSION", "relations-v13")
    status, upgraded = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "same-source", "dirty": True},
    )
    assert status == 200
    new_index_id = upgraded["index"]["index_id"]
    assert new_index_id != old_index_id
    with application.state.registry.store_for_repository("ts") as store:
        preserved = store.connection.execute(
            "SELECT snapshot_hash, rules_version FROM index_snapshots WHERE index_id = ?",
            (old_index_id,),
        ).fetchone()
        current = store.connection.execute(
            "SELECT rules_version FROM index_snapshots WHERE index_id = ?",
            (new_index_id,),
        ).fetchone()
        assert preserved["snapshot_hash"] == old_hash
        assert "typescript-facts-v3" in preserved["rules_version"]
        assert "typescript-facts-v5" in current["rules_version"]
        assert store.read_evidence(old_index_id, "web/api.ts", 1, 1).excerpt.startswith("const apiBaseUrl")


def test_truncated_route_candidates_never_claim_unique_http_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.overview as overview_module

    _write_http_project(tmp_path, duplicate_route=True)
    monkeypatch.setattr(overview_module, "_MAX_ENTRY_CANDIDATES", 1)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    coverage = overview["http_association_coverage"]
    assert coverage["analysis_status"] == "incomplete"
    assert coverage["matched_count"] == 0
    assert coverage["unknown_count"] == 3
    assert overview["http_associations"] == []


def test_http_visible_match_limit_marks_coverage_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.overview as overview_module

    _write_http_project(tmp_path)
    monkeypatch.setattr(overview_module, "_MAX_HTTP_ASSOCIATIONS", 1)
    application = create_app({"ts": tmp_path})
    project_id, index_id = _create_index(application)
    status, overview = request(application, "GET", f"/api/projects/{project_id}/indexes/{index_id}/overview")
    assert status == 200
    coverage = overview["http_association_coverage"]
    assert coverage["analysis_status"] == "incomplete"
    assert coverage["matched_count"] == 2
    assert coverage["visible_match_count"] == 1
    assert len(overview["http_associations"]) == 1
