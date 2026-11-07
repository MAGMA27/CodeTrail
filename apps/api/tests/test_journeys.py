from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from app.contracts import (
    CreateProjectIndexRequest,
    CreateProjectRequest,
    Evidence,
    JourneyCoverage,
    IndexSnapshot,
    JourneySummary,
)
from app.indexing import SnapshotFile, SourceSnapshot, extract_definitions
from app.main import create_app
from app.journeys import _extract_ui_facts, build_journey_catalog, build_project_journey
from app.mcp_server import _mcp_journey_response
from app.overview import build_project_overview
from app.relations import HTTP_REQUEST_FACTS_INCOMPLETE_REASON, extract_relations


ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATHS = (
    "apps/web/app/workbench-ui.tsx",
    "apps/web/app/workbench.tsx",
    "apps/web/lib/api.ts",
    "apps/api/app/main.py",
    "apps/api/app/service.py",
)


async def _request(application, target: str) -> dict:
    parsed = urlsplit(target)
    messages: list[dict] = []
    response_complete = asyncio.Event()
    received = False

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

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode("ascii"),
            "query_string": parsed.query.encode("ascii"),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
        send,
    )
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    assert response_start["status"] == 200, body
    return json.loads(body or b"{}")


def _codetrail_source_relation():
    files = []
    for relative_path in SOURCE_PATHS:
        raw = (ROOT / relative_path).read_bytes()
        text = raw.decode("utf-8")
        files.append(
            SnapshotFile(
                path=relative_path,
                size=len(raw),
                content_hash=hashlib.sha256(raw).hexdigest(),
                text=text,
            )
        )
    snapshot_hash = hashlib.sha256("".join(item.content_hash for item in files).encode()).hexdigest()
    index = IndexSnapshot(
        index_id="journey-source-test",
        repository_id="journey-test-repository",
        revision="source-test",
        dirty=True,
        snapshot_hash=snapshot_hash,
        status="ready",
        rules_version="in-memory-source-test+typescript-facts-v5",
    )
    snapshot = SourceSnapshot(index=index, files=tuple(files), issues=())
    relation = extract_relations(extract_definitions(snapshot), max_seconds=8)
    file_by_path = {item.path: item for item in files}

    def read_evidence(index_id: str, path: str, start_line: int, end_line: int) -> Evidence:
        assert index_id == index.index_id
        source = file_by_path[path]
        lines = source.text.splitlines()
        excerpt = "\n".join(lines[start_line - 1 : end_line])
        identity = f"{index_id}\0{path}\0{start_line}\0{end_line}\0{source.content_hash}"
        return Evidence(
            evidence_id=f"evidence-{hashlib.sha256(identity.encode()).hexdigest()[:32]}",
            index_id=index_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            excerpt=excerpt,
            content_hash=source.content_hash,
            kind="source",
        )

    return relation, read_evidence


def _preferences_source_relation(
    *,
    extra_python_paths: tuple[str, ...] = (),
    max_edges: int | None = None,
    max_seconds: float = 8,
):
    fixture_root = ROOT / "fixtures" / "repos" / "preferences-demo"
    files = []
    for path in sorted(fixture_root.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(fixture_root).as_posix()
        raw = text.encode("utf-8")
        files.append(
            SnapshotFile(
                path=relative_path,
                size=len(raw),
                content_hash=hashlib.sha256(raw).hexdigest(),
                text=text,
            )
        )
    for path in extra_python_paths:
        source = "\n".join(
            [*(f"def target_{number}() -> None: pass" for number in range(12)),
             "def flood() -> None:",
             *(f"    target_{number}()" for number in range(12)),
             ""]
        )
        raw = source.encode("utf-8")
        files.append(
            SnapshotFile(
                path=path,
                size=len(raw),
                content_hash=hashlib.sha256(raw).hexdigest(),
                text=source,
            )
        )
    files.sort(key=lambda item: item.path)
    snapshot_hash = hashlib.sha256("".join(item.content_hash for item in files).encode()).hexdigest()
    index = IndexSnapshot(
        index_id="journey-preferences-test",
        repository_id="preferences-demo",
        revision="source-test",
        dirty=True,
        snapshot_hash=snapshot_hash,
        status="ready",
        rules_version="in-memory-source-test+typescript-facts-v5",
    )
    snapshot = SourceSnapshot(index=index, files=tuple(files), issues=())
    definitions = extract_definitions(snapshot)
    relation = extract_relations(
        definitions,
        max_seconds=max_seconds,
        **({} if max_edges is None else {"max_edges": max_edges}),
    )
    file_by_path = {item.path: item for item in files}

    def read_evidence(index_id: str, path: str, start_line: int, end_line: int) -> Evidence:
        assert index_id == index.index_id
        source = file_by_path[path]
        lines = source.text.splitlines()
        excerpt = "\n".join(lines[start_line - 1 : end_line])
        identity = f"{index_id}\0{path}\0{start_line}\0{end_line}\0{source.content_hash}"
        return Evidence(
            evidence_id=f"evidence-{hashlib.sha256(identity.encode()).hexdigest()[:32]}",
            index_id=index_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            excerpt=excerpt,
            content_hash=source.content_hash,
            kind="source",
        )

    return relation, read_evidence


@pytest.mark.parametrize("generated_path", ("00_generated_edges.py", "zz_generated_edges.py"))
def test_edge_budget_preserves_http_facts_associations_and_journey_ids(generated_path: str) -> None:
    unlimited, read_evidence = _preferences_source_relation(extra_python_paths=(generated_path,))
    limited, _ = _preferences_source_relation(extra_python_paths=(generated_path,), max_edges=1)
    unlimited_overview = build_project_overview("preferences-demo", unlimited, read_evidence)
    limited_overview = build_project_overview("preferences-demo", limited, read_evidence)
    unlimited_catalog, _ = build_journey_catalog("preferences-demo", unlimited, unlimited_overview)
    limited_catalog, _ = build_journey_catalog("preferences-demo", limited, limited_overview)

    assert unlimited.http_client_requests
    assert any(edge.source_location.path == generated_path for edge in unlimited.edges)
    assert limited.http_client_requests == unlimited.http_client_requests
    assert [item.model_dump() for item in limited_overview.http_associations] == [
        item.model_dump() for item in unlimited_overview.http_associations
    ]
    assert limited_overview.http_association_coverage.client_request_count == (
        unlimited_overview.http_association_coverage.client_request_count
    )
    assert limited_overview.http_association_coverage.analysis_status == (
        unlimited_overview.http_association_coverage.analysis_status
    )
    assert limited_overview.http_association_coverage.analysis_status == "complete"
    assert [item.journey_id for item in limited_catalog.journeys] == [
        item.journey_id for item in unlimited_catalog.journeys
    ]
    assert limited.truncated is True
    assert limited_catalog.coverage.relation_truncated is True
    assert any("关系索引扫描未完成" in item for item in limited_catalog.coverage.limitations)


def test_python_only_project_has_no_actions_and_is_not_truncated() -> None:
    fixture_root = ROOT / "fixtures" / "repos" / "timeout-demo"
    files = []
    for path in sorted(fixture_root.rglob("*.py")):
        raw = path.read_bytes()
        files.append(
            SnapshotFile(
                path=path.relative_to(fixture_root).as_posix(),
                size=len(raw),
                content_hash=hashlib.sha256(raw).hexdigest(),
                text=raw.decode("utf-8"),
            )
        )
    snapshot_hash = hashlib.sha256("".join(item.content_hash for item in files).encode()).hexdigest()
    index = IndexSnapshot(
        index_id="journey-python-only-test",
        repository_id="timeout-demo",
        revision="source-test",
        dirty=True,
        snapshot_hash=snapshot_hash,
        status="ready",
        rules_version="in-memory-source-test",
    )
    relation = extract_relations(extract_definitions(SourceSnapshot(index=index, files=tuple(files), issues=())))
    file_by_path = {item.path: item for item in files}

    def read_evidence(index_id: str, path: str, start_line: int, end_line: int) -> Evidence:
        source = file_by_path[path]
        identity = f"{index_id}\0{path}\0{start_line}\0{end_line}\0{source.content_hash}"
        return Evidence(
            evidence_id=f"evidence-{hashlib.sha256(identity.encode()).hexdigest()[:32]}",
            index_id=index_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            excerpt="\n".join(source.text.splitlines()[start_line - 1 : end_line]),
            content_hash=source.content_hash,
            kind="source",
        )

    overview = build_project_overview("timeout-demo", relation, read_evidence)

    catalog, _specs = build_journey_catalog("timeout-demo", relation, overview)

    assert catalog.journeys == []
    assert catalog.coverage.action_count == 0
    assert catalog.coverage.truncated is False
    assert catalog.coverage.relation_truncated is False


def test_relation_deadline_marks_http_request_facts_incomplete() -> None:
    relation, _read_evidence = _preferences_source_relation(max_seconds=1e-12)

    assert relation.truncated is True
    assert relation.truncation_reason is not None
    assert HTTP_REQUEST_FACTS_INCOMPLETE_REASON in relation.truncation_reason
    assert relation.http_client_requests == ()


def _preferences_journey_catalog():
    relation, read_evidence = _preferences_source_relation()
    overview = build_project_overview(
        "preferences-demo",
        relation,
        read_evidence,
        max_depth=2,
        max_paths=20,
        max_modules=100,
        max_groups=20,
        max_module_relations=20,
    )
    catalog, specs = build_journey_catalog("preferences-demo", relation, overview)
    return relation, read_evidence, overview, catalog, specs


def test_real_local_import_source_builds_bounded_journey_with_explicit_gaps() -> None:
    relation, read_evidence = _codetrail_source_relation()
    overview = build_project_overview(
        "journey-source-project",
        relation,
        read_evidence,
        max_depth=2,
        max_paths=2,
        max_modules=200,
        max_groups=20,
        max_module_relations=20,
    )
    catalog, _specs = build_journey_catalog("journey-source-project", relation, overview)
    summary = next(
        item for item in catalog.journeys
        if item.action_path == "apps/web/app/workbench-ui.tsx"
        and item.event_name == "onSubmit"
        and "导入并建立索引" in item.label_candidates
    )
    assert summary.method == "POST"
    assert summary.path == "/api/imports/local"
    assert summary.label_dynamic is True

    journey = build_project_journey(
        "journey-source-project",
        relation,
        overview,
        summary.journey_id,
        read_evidence,
    )
    assert journey is not None
    assert len(journey.steps) <= 8
    assert journey.steps[0].evidence[0].path == "apps/web/app/workbench-ui.tsx"
    callback_step = next(step for step in journey.steps if step.relation_kind == "ui_callback")
    assert callback_step.certainty == "unconfirmed"
    assert any(symbol.qualified_name.endswith(".importLocalProject") for symbol in callback_step.symbols)
    static_client_step = next(
        step for step in journey.steps
        if step.relation_kind == "calls" and step.role == "client_processing"
    )
    assert any(symbol.qualified_name.endswith(".importLocalPath") for symbol in static_client_step.symbols)
    route_step = next(step for step in journey.steps if step.role == "server_processing")
    assert any(symbol.qualified_name.endswith(".import_local") for symbol in route_step.symbols)
    result_step = journey.steps[-1]
    assert result_step.relation_kind == "result_display"
    assert result_step.certainty == "unconfirmed"
    assert not result_step.evidence
    assert any("界面更新" in marker for marker in journey.markers)
    mcp_journey = _mcp_journey_response(journey, "http://localhost:3000")
    assert mcp_journey.ok is True
    assert mcp_journey.journey is not None
    assert mcp_journey.journey.project_id == journey.project_id
    assert mcp_journey.journey.index.index_id == journey.index.index_id
    assert {item.evidence_id for item in mcp_journey.references} == {
        evidence.evidence_id for step in journey.steps for evidence in step.evidence
    }

    missing_evidence = build_project_journey(
        "journey-source-project",
        relation,
        overview,
        summary.journey_id,
        lambda *_args: None,
    )
    assert missing_evidence is not None
    assert any("操作源码证据" in marker for marker in missing_evidence.markers)
    assert any("处理符号的源码证据" in marker for marker in missing_evidence.markers)
    assert all(evidence is not None for step in missing_evidence.steps for evidence in step.evidence)


def test_journey_summary_keeps_reopenable_evidence_and_http_default_is_full(tmp_path: Path) -> None:
    fixture_root = ROOT / "fixtures" / "repos" / "preferences-demo"
    repository = tmp_path / "preferences-demo"
    shutil.copytree(fixture_root, repository, ignore=shutil.ignore_patterns("__pycache__", ".codetrail"))
    application = create_app({"preferences-demo": repository}, data_root=tmp_path / "data")
    service = application.state.workspace_service
    project = service.create_project(CreateProjectRequest(name="Journey fixture", repository_id="preferences-demo"))
    indexed = service.create_project_index(
        project.project_id,
        CreateProjectIndexRequest(revision="journey-summary-test", dirty=False),
    )
    catalog = service.get_journey_catalog(project.project_id, indexed.index.index_id)
    assert catalog.journeys
    journey_id = catalog.journeys[0].journey_id

    full = service.get_journey(project.project_id, indexed.index.index_id, journey_id)
    summary = service.get_journey(project.project_id, indexed.index.index_id, journey_id, detail="summary")
    assert summary.summary == full.summary
    assert summary.action_label == full.summary.action_label
    assert summary.coverage == full.coverage
    assert summary.markers == full.markers
    assert summary.summary is not None
    assert full.summary is not None
    assert (
        summary.summary.event_name,
        summary.summary.action_path,
        summary.summary.action_start_line,
        summary.summary.method,
        summary.summary.path,
    ) == (
        full.summary.event_name,
        full.summary.action_path,
        full.summary.action_start_line,
        full.summary.method,
        full.summary.path,
    )
    for full_step, summary_step in zip(full.steps, summary.steps, strict=True):
        assert summary_step.step_id == full_step.step_id
        assert [symbol.id for symbol in summary_step.symbols] == [symbol.symbol_id for symbol in full_step.symbols]
        assert [item.evidence_id for item in summary_step.evidence] == [
            item.evidence_id for item in full_step.evidence
        ]
        for evidence_ref, evidence in zip(summary_step.evidence, full_step.evidence, strict=True):
            assert (evidence_ref.path, evidence_ref.start_line, evidence_ref.end_line) == (
                evidence.path,
                evidence.start_line,
                evidence.end_line,
            )
            reopened = service.read_source(
                project.project_id,
                indexed.index.index_id,
                evidence_ref.path,
                evidence_ref.start_line,
                evidence_ref.end_line,
            )
            assert reopened.evidence.excerpt == evidence.excerpt

    mcp_summary = _mcp_journey_response(summary, "http://localhost:3000")
    assert {item.evidence_id for item in mcp_summary.references} == {
        evidence.evidence_id for step in full.steps for evidence in step.evidence
    }

    async def verify_http_shapes() -> None:
        route = (
            f"/api/projects/{project.project_id}/indexes/{indexed.index.index_id}"
            f"/journeys/{journey_id}"
        )
        assert await _request(application, route) == full.model_dump(mode="json")
        assert await _request(application, f"{route}?detail=summary") == summary.model_dump(mode="json")

    asyncio.run(verify_http_shapes())


def test_one_ui_action_may_have_multiple_http_journeys() -> None:
    coverage = JourneyCoverage(
        action_count=1,
        journey_count=2,
        unsupported_action_count=0,
    )
    assert coverage.action_count == 1
    assert coverage.journey_count == 2


def test_action_parser_reports_budget_truncation() -> None:
    buttons = "".join(
        f'<button onClick={{() => submit({number})}}>保存</button>'
        for number in range(1_001)
    )
    source = f"export function Actions() {{ function submit(value: number) {{}} return <main>{buttons}</main>; }}"
    raw = source.encode("utf-8")
    snapshot_file = SnapshotFile(
        path="ui.tsx",
        size=len(raw),
        content_hash=hashlib.sha256(raw).hexdigest(),
        text=source,
    )

    actions, _bindings, coverage = _extract_ui_facts((snapshot_file,))

    assert len(actions) == 1_000
    assert coverage["parser_status"] == "truncated"
    assert coverage["action_facts_truncated"] is True
    assert coverage["action_facts_omitted_count"] == 1

    properties = ",".join("onSubmit: callback" for _ in range(2_001))
    binding_source = "export function Actions() { return <Operation handlers={{" + properties + "}} />; }"
    binding_raw = binding_source.encode("utf-8")
    binding_file = SnapshotFile(
        path="bindings.tsx",
        size=len(binding_raw),
        content_hash=hashlib.sha256(binding_raw).hexdigest(),
        text=binding_source,
    )
    _actions, bindings, binding_coverage = _extract_ui_facts((binding_file,))
    assert len(bindings) == 2_000
    assert binding_coverage["parser_status"] == "truncated"
    assert binding_coverage["prop_facts_truncated"] is True
    assert binding_coverage["prop_facts_omitted_count"] == 1
