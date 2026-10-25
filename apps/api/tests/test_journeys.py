from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import Evidence, JourneyCoverage, IndexSnapshot, JourneySummary
from app.indexing import SnapshotFile, SourceSnapshot, extract_definitions
from app.journeys import _extract_ui_facts, build_journey_catalog, build_project_journey
from app.mcp_server import _mcp_journey_response
from app.overview import build_project_overview
from app.relations import extract_relations


ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATHS = (
    "apps/web/app/workbench-ui.tsx",
    "apps/web/app/workbench.tsx",
    "apps/web/lib/api.ts",
    "apps/api/app/main.py",
    "apps/api/app/service.py",
)


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


def _preferences_source_relation():
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
