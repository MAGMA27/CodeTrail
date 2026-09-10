"""Offline tests for conservative Python imports and calls."""

from __future__ import annotations

from pathlib import Path

from app.contracts import GraphSnapshot
from app.indexing import build_source_snapshot, extract_definitions
from app.relations import RelationIndex, extract_relations


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


def fixture_relations() -> RelationIndex:
    definitions = extract_definitions(
        build_source_snapshot(
            FIXTURE_ROOT,
            repository_id="timeout-demo",
            revision="fixture-v1",
            dirty=False,
        )
    )
    return extract_relations(definitions)


def test_timeout_demo_has_explicit_calls_and_dynamic_handler_unresolved() -> None:
    result = fixture_relations()
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}

    expected_calls = {
        ("timeout_demo.__main__.main", "timeout_demo.app.start"),
        ("timeout_demo.app.start", "timeout_demo.config.load_settings"),
        ("timeout_demo.app.start", "timeout_demo.client.initialize_client"),
        ("timeout_demo.app.start", "timeout_demo.dispatch.dispatch"),
        ("timeout_demo.client.initialize_client", "timeout_demo.client.Client"),
    }
    actual_calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }
    assert expected_calls <= actual_calls

    dynamic = [item for item in result.unresolved if item.expression.startswith("handler(")]
    assert dynamic
    assert all(item.reason == "dynamic_call_target" for item in dynamic)
    assert not any(
        names.get(edge.target_id) in {"timeout_demo.handlers.handle", "timeout_demo.other_handlers.handle"}
        for edge in result.edges
        if edge.kind == "calls"
    )


def test_import_edges_resolve_relative_modules_aliases_and_symbols() -> None:
    result = fixture_relations()
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    imported = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "imports"
    }

    assert ("timeout_demo.app", "timeout_demo.client.Client") in imported
    assert ("timeout_demo.app", "timeout_demo.client.initialize_client") in imported
    assert ("timeout_demo.dispatch", "timeout_demo.handlers") in imported
    assert ("timeout_demo.dispatch", "timeout_demo.other_handlers") in imported
    assert any(item.reason == "external_import" for item in result.unresolved)


def test_relations_validate_as_one_snapshot_graph() -> None:
    result = fixture_relations()
    graph = GraphSnapshot(
        index_id=result.snapshot.index.index_id,
        nodes=list(result.symbols),
        static_edges=list(result.edges),
        unresolved=list(result.unresolved),
    )

    assert graph.index_id == result.snapshot.index.index_id
    assert all(edge.extraction_method in {"ast", "explicit_import", "fixture"} for edge in graph.static_edges)
    assert all(edge.source_location.path in {item.path for item in result.snapshot.files} for edge in graph.static_edges)


def test_relation_output_is_reproducible_and_locations_are_bounded() -> None:
    first = fixture_relations()
    second = fixture_relations()

    assert [edge.model_dump() for edge in first.edges] == [edge.model_dump() for edge in second.edges]
    assert [item.model_dump() for item in first.unresolved] == [item.model_dump() for item in second.unresolved]
    assert first.truncated is False
    assert first.truncation_reason is None
    for edge in first.edges:
        assert edge.source_location.start_line <= edge.source_location.end_line
        assert edge.source_location.end_line <= 40


def test_relation_budget_returns_partial_results_and_reason() -> None:
    definitions = extract_definitions(
        build_source_snapshot(FIXTURE_ROOT, repository_id="timeout-demo", revision="fixture-v1", dirty=False)
    )
    result = extract_relations(definitions, max_edges=2)

    assert result.truncated is True
    assert result.truncation_reason == "max_edges reached"
    assert len(result.edges) == 2


def test_relation_time_budget_is_observable() -> None:
    definitions = extract_definitions(
        build_source_snapshot(FIXTURE_ROOT, repository_id="timeout-demo", revision="fixture-v1", dirty=False)
    )

    result = extract_relations(definitions, max_seconds=1e-12)

    assert result.truncated is True
    assert result.truncation_reason == "max_seconds exceeded"


def test_aliases_parameter_shadowing_rebinding_and_unknown_imports(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "targets.py").write_text(
        "def target() -> None:\n    pass\n\ndef handle() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (package / "other.py").write_text(
        "def handle() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (package / "caller.py").write_text(
        "from .targets import target as run\n"
        "from . import targets as target_module\n"
        "from .missing import absent\n"
        "from .targets import *\n\n"
        "def explicit() -> None:\n"
        "    run()\n"
        "    target_module.target()\n\n"
        "def parameter_shadow(run) -> None:\n"
        "    run()\n\n"
        "def local_rebind() -> None:\n"
        "    run = lambda: None\n"
        "    run()\n\n"
        "def dynamic_lookup() -> None:\n"
        "    handler = getattr(target_module, 'target')\n"
        "    handler()\n\n"
        "def ambiguous() -> None:\n"
        "    handle()\n\n"
        "def object_method() -> None:\n"
        "    obj.run()\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(tmp_path, repository_id="sample", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert ("sample.caller.explicit", "sample.targets.target") in calls
    assert any(item.reason == "name_rebound_in_scope" for item in result.unresolved)
    assert any(item.reason == "dynamic_call_target" for item in result.unresolved)
    assert any(item.reason == "missing_relative_import" for item in result.unresolved)
    assert any(item.reason == "star_import_not_resolved" for item in result.unresolved)
    assert any(item.reason == "ambiguous_name" and len(item.possible_target_ids) == 2 for item in result.unresolved)
    assert any(item.reason == "object_method_not_resolved" for item in result.unresolved)


def test_old_snapshot_is_unchanged_when_worktree_changes(tmp_path: Path) -> None:
    source_path = tmp_path / "module.py"
    source_path.write_text("def first() -> None:\n    first()\n", encoding="utf-8")
    snapshot = build_source_snapshot(tmp_path, repository_id="mutable", revision="working-tree", dirty=True)
    definitions = extract_definitions(snapshot)
    first = extract_relations(definitions)

    source_path.write_text("def second() -> None:\n    pass\n", encoding="utf-8")
    second = extract_relations(definitions)

    assert first.edges
    assert [edge.model_dump() for edge in first.edges] == [edge.model_dump() for edge in second.edges]
    assert [item.model_dump() for item in first.unresolved] == [item.model_dump() for item in second.unresolved]
