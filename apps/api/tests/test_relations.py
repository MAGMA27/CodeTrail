"""Offline tests for conservative Python imports and calls."""

from __future__ import annotations

from pathlib import Path

import app.relations as relations_module
from app.contracts import GraphSnapshot
from app.indexing import build_source_snapshot, extract_definitions
from app.relations import (
    RelationBindings,
    RelationFileFacts,
    RelationIndex,
    RelationReturnEvent,
    RelationScopeFacts,
    _DefinitionMaps,
    _RelationBudget,
    _resolve_constructor_attribute_bindings,
    deserialize_relation_file_facts,
    extract_relations,
    serialize_relation_file_facts,
)


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
    assert first.unresolved_truncated is False
    assert first.unresolved_truncation_reason is None
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


def test_constructor_binding_collection_checks_deadline_per_event(monkeypatch) -> None:
    class Clock:
        now = 0.0

    clock = Clock()

    class Events(tuple):
        yielded = 0

        def __iter__(self):
            for event in super().__iter__():
                self.yielded += 1
                yield event
                if self.yielded == 1:
                    clock.now = 10.0

    events = Events(RelationReturnEvent(None) for _ in range(100))
    bindings = RelationBindings(
        imports=(),
        assigned=(),
        definition_names=(),
        global_names=(),
        dynamic_names=(),
        parameter_names=(),
        positional_parameter_names=(),
        has_variadic_parameters=False,
        nonlocal_names=(),
        attribute_write_names=(),
        indirect_attribute_write_names=(),
    )
    scope = RelationScopeFacts(
        qualified_name="sample",
        kind="module",
        start_line=1,
        bindings=bindings,
        events=events,
        has_decorators=False,
        has_bases=False,
    )
    facts = RelationFileFacts("sample.py", "sample", False, scope)
    monkeypatch.setattr(relations_module.time, "monotonic", lambda: clock.now)
    budget = _RelationBudget(10, 10, 10.0)

    _resolve_constructor_attribute_bindings(
        {"sample.py": facts},
        {},
        _DefinitionMaps(()),
        [],
        set(),
        set(),
        {},
        budget,
    )

    assert budget.truncated is True
    assert budget.reason == "max_seconds exceeded"
    assert events.yielded == 2


def test_unresolved_budget_does_not_stop_later_static_relation_extraction(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "a_noise.py").write_text(
        "def noisy() -> None:\n"
        "    missing_one()\n"
        "    missing_two()\n",
        encoding="utf-8",
    )
    (package / "targets.py").write_text(
        "def target() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (package / "z_later.py").write_text(
        "from .targets import target\n\n"
        "def caller() -> None:\n"
        "    target()\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(tmp_path, repository_id="unresolved-budget", revision="fixture-v1", dirty=False)
    )
    result = extract_relations(definitions, max_edges=20, max_unresolved=1, max_seconds=1.0)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}

    assert result.truncated is False
    assert result.truncation_reason is None
    assert result.unresolved_truncated is True
    assert result.unresolved_truncation_reason == "max_unresolved reached"
    assert len(result.unresolved) == 1
    assert (
        "sample.z_later.caller",
        "sample.targets.target",
    ) in {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }


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


def test_local_constructed_objects_resolve_only_direct_unique_methods(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "left.py").write_text(
        "class Widget:\n"
        "    def run(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "right.py").write_text(
        "class Widget:\n"
        "    def run(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "caller.py").write_text(
        "from .left import Widget as LeftWidget\n"
        "from .right import Widget as RightWidget\n\n"
        "class LocalWidget:\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "def positive() -> None:\n"
        "    local = LocalWidget()\n"
        "    local.run()\n"
        "    left = LeftWidget()\n"
        "    left.run()\n"
        "    right = RightWidget()\n"
        "    right.run()\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(repository, repository_id="constructed", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert {
        ("sample.caller.positive", "sample.caller.LocalWidget.run"),
        ("sample.caller.positive", "sample.left.Widget.run"),
        ("sample.caller.positive", "sample.right.Widget.run"),
    } <= calls
    assert len([item for item in result.unresolved if item.expression.endswith(".run()")]) == 0


def test_verified_threadpool_wrappers_forward_only_proven_callables(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "target.py").write_text(
        "def work() -> None:\n    pass\n\n"
        "def run_in_threadpool(value) -> None:\n    pass\n",
        encoding="utf-8",
    )
    source = (
        "from fastapi.concurrency import run_in_threadpool as dispatch\n"
        "from starlette.concurrency import run_in_threadpool as dispatch_again\n"
        "from third_party import run_in_threadpool as other_dispatch\n"
        "from .target import work\n\n"
        "class Service:\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "class Duplicate:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "service = Service()\n\n"
        "async def forwarded_function() -> None:\n"
        "    await dispatch(work)\n\n"
        "async def forwarded_method() -> None:\n"
        "    await dispatch_again(service.run)\n\n"
        "async def shadowed_wrapper(dispatch) -> None:\n"
        "    await dispatch(work)\n\n"
        "async def aliased_wrapper() -> None:\n"
        "    local_dispatch = dispatch\n"
        "    await local_dispatch(work)\n\n"
        "async def ambiguous_method() -> None:\n"
        "    duplicate = Duplicate()\n"
        "    await dispatch(duplicate.run)\n\n"
        "async def unsupported_class_target() -> None:\n"
        "    await dispatch(Service)\n\n"
        "async def untrusted_wrapper() -> None:\n"
        "    await other_dispatch(work)\n\n"
        "async def dynamic_callable(value) -> None:\n"
        "    await dispatch(getattr(value, 'run'))\n\n"
        "async def local_call_before_wrapper_import() -> None:\n"
        "    await local_dispatch(work)\n"
        "    from fastapi.concurrency import run_in_threadpool as local_dispatch\n\n"
        "async def local_call_after_wrapper_import() -> None:\n"
        "    from fastapi.concurrency import run_in_threadpool as local_dispatch\n"
        "    await local_dispatch(work)\n\n"
        "async def outer_wrapper_import_after_handler() -> None:\n"
        "    await late_dispatch(work)\n\n"
        "def reassigned_capture():\n"
        "    local_service = Service()\n"
        "    async def child() -> None:\n"
        "        await dispatch(local_service.run)\n"
        "    local_service = Service()\n"
        "    return child\n\n"
        "def nested_reassignment():\n"
        "    local_service = Service()\n"
        "    def rebind():\n"
        "        nonlocal local_service\n"
        "        local_service = Service()\n"
        "    async def child() -> None:\n"
        "        await dispatch(local_service.run)\n"
        "    return child\n\n"
        "def nested_wrapper_reassignment():\n"
        "    from fastapi.concurrency import run_in_threadpool as local_dispatch\n"
        "    def rebind():\n"
        "        nonlocal local_dispatch\n"
        "        local_dispatch = lambda func: func\n"
        "    async def child() -> None:\n"
        "        await local_dispatch(work)\n"
        "    return child\n\n"
        "class Container:\n"
        "    from starlette.concurrency import run_in_threadpool as class_dispatch\n"
        "    async def class_attribute_is_not_a_closure(self) -> None:\n"
        "        await class_dispatch(work)\n"
        "\nfrom fastapi.concurrency import run_in_threadpool as late_dispatch\n"
    )
    (package / "app.py").write_text(source, encoding="utf-8")

    definitions = extract_definitions(
        build_source_snapshot(repository, repository_id="wrappers", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    facts = dict(result.fact_cache_entries)["sample/app.py"]
    assert deserialize_relation_file_facts(
        serialize_relation_file_facts(facts), expected_path="sample/app.py"
    ) == facts
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id]): edge
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert ("sample.app.forwarded_function", "sample.target.work") in calls
    assert ("sample.app.local_call_after_wrapper_import", "sample.target.work") in calls
    method_edge = calls[("sample.app.forwarded_method", "sample.app.Service.run")]
    assert method_edge.extraction_method == "ast"
    assert method_edge.source_location.start_line == 22
    assert ("sample.app.ambiguous_method", "sample.app.Duplicate.run") not in calls
    assert not any(source_name in {"sample.app.unsupported_class_target", "sample.app.untrusted_wrapper", "sample.app.dynamic_callable", "sample.app.local_call_before_wrapper_import", "sample.app.outer_wrapper_import_after_handler", "sample.app.reassigned_capture.child", "sample.app.nested_reassignment.child", "sample.app.nested_wrapper_reassignment.child", "sample.app.Container.class_attribute_is_not_a_closure"}
                   and target_name == "sample.target.work"
                   for source_name, target_name in calls)

    unresolved_sources = {
        names[item.source_symbol_id]
        for item in result.unresolved
        if item.expression.startswith(("dispatch(", "dispatch_again(", "other_dispatch(", "local_dispatch(", "class_dispatch(", "late_dispatch("))
    }
    assert {
        "sample.app.shadowed_wrapper",
        "sample.app.aliased_wrapper",
        "sample.app.ambiguous_method",
        "sample.app.unsupported_class_target",
        "sample.app.untrusted_wrapper",
        "sample.app.dynamic_callable",
        "sample.app.local_call_before_wrapper_import",
        "sample.app.outer_wrapper_import_after_handler",
        "sample.app.reassigned_capture.child",
        "sample.app.nested_reassignment.child",
        "sample.app.nested_wrapper_reassignment.child",
        "sample.app.Container.class_attribute_is_not_a_closure",
    } <= unresolved_sources


def test_local_wrapper_shaped_module_is_not_treated_as_threadpool_wrapper(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    local_framework = repository / "fastapi"
    local_framework.mkdir()
    (local_framework / "concurrency.py").write_text(
        "def run_in_threadpool(func) -> None:\n    pass\n",
        encoding="utf-8",
    )
    (package / "app.py").write_text(
        "from fastapi.concurrency import run_in_threadpool as dispatch\n\n"
        "def work() -> None:\n    pass\n\n"
        "async def caller() -> None:\n    await dispatch(work)\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(repository, repository_id="local-wrapper", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert ("sample.app.caller", "fastapi.concurrency.run_in_threadpool") in calls
    assert ("sample.app.caller", "sample.app.work") not in calls


def test_constructed_object_method_resolution_rejects_unsafe_patterns(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "caller.py").write_text(
        "def replacement(self) -> None:\n"
        "    pass\n\n"
        "class Plain:\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "class Inherited(Plain):\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "class Decorated:\n"
        "    @replacement\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "class ClassOverride:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
        "    run = replacement\n\n"
        "class InstanceOverride:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
        "    def __init__(self) -> None:\n"
        "        self.run = replacement\n\n"
        "class Duplicate:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
        "    def run(self) -> None:\n"
        "        pass\n\n"
        "def reassigned() -> None:\n"
        "    obj = Plain()\n"
        "    obj = replacement\n"
        "    obj.run()\n\n"
        "def parameter_shadow(obj) -> None:\n"
        "    obj.run()\n\n"
        "def conditional(flag: bool) -> None:\n"
        "    if flag:\n"
        "        obj = Plain()\n"
        "    obj.run()\n\n"
        "def constructor_shadow(Plain) -> None:\n"
        "    obj = Plain()\n"
        "    obj.run()\n\n"
        "def inherited() -> None:\n"
        "    obj = Inherited()\n"
        "    obj.run()\n\n"
        "def decorated() -> None:\n"
        "    obj = Decorated()\n"
        "    obj.run()\n\n"
        "def class_override() -> None:\n"
        "    obj = ClassOverride()\n"
        "    obj.run()\n\n"
        "def instance_override() -> None:\n"
        "    obj = InstanceOverride()\n"
        "    obj.run()\n\n"
        "def call_site_override() -> None:\n"
        "    obj = Plain()\n"
        "    obj.run = replacement\n"
        "    obj.run()\n\n"
        "def duplicate() -> None:\n"
        "    obj = Duplicate()\n"
        "    obj.run()\n\n"
        "def dynamic() -> None:\n"
        "    obj = Plain()\n"
        "    getattr(obj, 'run')()\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(repository, repository_id="unsafe", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert all(
        (source, target) not in calls
        for source in {
            "sample.caller.reassigned",
            "sample.caller.parameter_shadow",
            "sample.caller.conditional",
            "sample.caller.constructor_shadow",
            "sample.caller.inherited",
            "sample.caller.decorated",
            "sample.caller.class_override",
            "sample.caller.instance_override",
            "sample.caller.call_site_override",
            "sample.caller.duplicate",
        }
        for target in {
            "sample.caller.Plain.run",
            "sample.caller.Inherited.run",
            "sample.caller.Decorated.run",
            "sample.caller.ClassOverride.run",
            "sample.caller.InstanceOverride.run",
            "sample.caller.Duplicate.run",
        }
    )
    unresolved_by_source = {
        names[item.source_symbol_id]: item.reason for item in result.unresolved
    }
    assert unresolved_by_source["sample.caller.parameter_shadow"] == "name_rebound_in_scope"
    assert unresolved_by_source["sample.caller.conditional"] == "name_rebound_in_scope"
    assert unresolved_by_source["sample.caller.inherited"] == "object_method_not_resolved"
    assert unresolved_by_source["sample.caller.decorated"] == "object_method_not_resolved"
    assert unresolved_by_source["sample.caller.call_site_override"] == "object_method_not_resolved"
    assert unresolved_by_source["sample.caller.dynamic"] == "dynamic_call_target"


def test_constructor_parameter_attribute_resolves_only_proven_instance_type(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "backend.py").write_text(
        "class Backend:\n"
        "    def read_source(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "workspace.py").write_text(
        "class Workspace:\n"
        "    def __init__(self, backend, diagnostics) -> None:\n"
        "        self.backend = backend\n"
        "        self.diagnostics = diagnostics\n"
        "    def read(self) -> None:\n"
        "        self.backend.read_source()\n",
        encoding="utf-8",
    )
    (package / "diagnostics.py").write_text(
        "class Diagnostics:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "factory.py").write_text(
        "from .backend import Backend\n"
        "from .diagnostics import Diagnostics\n"
        "from .workspace import Workspace\n\n"
        "def create() -> Workspace:\n"
        "    backend = Backend()\n"
        "    return Workspace(backend, Diagnostics())\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(tmp_path, repository_id="constructor", revision="fixture-v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert ("sample.workspace.Workspace.read", "sample.backend.Backend.read_source") in calls


def test_code_trail_workspace_service_reader_stays_unresolved_after_new_bypass() -> None:
    definitions = extract_definitions(
        build_source_snapshot(
            ROOT,
            repository_id="codetrail-root-probe",
            revision="working-tree",
            dirty=True,
        )
    )
    # Self-index of this growing repo exceeds the production defaults (6k edges /
    # 5k unresolved); raise only this probe's budget so truncation does not hide
    # the WorkspaceService.read_source unresolved + contextual-call facts under test.
    result = extract_relations(
        definitions,
        max_seconds=60,
        max_edges=20_000,
        max_unresolved=20_000,
    )
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    assert result.truncated is False
    assert any(
        names[edge.source_id].endswith("Workbench.openArchitectureSource")
        and names[edge.target_id].endswith("api.readProjectSource")
        and edge.source_location.path == "apps/web/app/workbench.tsx"
        for edge in result.edges
        if edge.kind == "calls" and edge.extraction_method == "ast"
    )
    assert (
        "apps.api.app.service.WorkspaceService.read_source",
        "apps.api.app.service.IndexService.read_source",
    ) not in calls
    unresolved = [
        item
        for item in result.unresolved
        if names[item.source_symbol_id] == "apps.api.app.service.WorkspaceService.read_source"
        and item.expression.startswith("self.index_service.read_source(")
    ]
    assert len(unresolved) == 1
    assert unresolved[0].reason == "object_method_not_resolved"
    handler_contexts = [
        context
        for context in result.contextual_calls
        if context.source.qualified_name == "apps.api.app.service.WorkspaceService.read_source"
        and context.target.qualified_name == "apps.api.app.service.IndexService.read_source"
        and context.activation_location.path == "apps/api/app/main.py"
    ]
    assert len(handler_contexts) == 1
    assert handler_contexts[0].field_name == "index_service"
    assert handler_contexts[0].binding_mutation_after_escape_unverified is True
    assert handler_contexts[0].activation_source.qualified_name == (
        "apps.api.app.main.create_app.read_project_source"
    )
    main_lines = (ROOT / "apps" / "api" / "app" / "main.py").read_text(encoding="utf-8").splitlines()
    expected_constructor_line = next(
        line_number
        for line_number, line in enumerate(main_lines, start=1)
        if line.strip().startswith("workspace_service = WorkspaceService(")
    )
    assert handler_contexts[0].receiver_constructor_location.path == "apps/api/app/main.py"
    assert handler_contexts[0].receiver_constructor_location.start_line == expected_constructor_line
    service_lines = (ROOT / "apps" / "api" / "app" / "service.py").read_text(
        encoding="utf-8"
    ).splitlines()
    expected_assignment_line = next(
        line_number
        for line_number, line in enumerate(service_lines, start=1)
        if line.strip() == "self.index_service = index_service"
    )
    assert handler_contexts[0].field_assignment_location.path == "apps/api/app/service.py"
    assert handler_contexts[0].field_assignment_location.start_line == expected_assignment_line


def test_contextual_calls_bind_each_constructor_site_and_reject_unproven_transfers(
    tmp_path: Path,
) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "dependencies.py").write_text(
        "class First:\n"
        "    def work(self) -> None:\n"
        "        pass\n\n"
        "class Second:\n"
        "    def work(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "wrappers.py").write_text(
        "from .dependencies import First, Second\n\n"
        "class Wrapper:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.backend = dependency\n"
        "    def read(self) -> None:\n"
        "        self.backend.work()\n\n"
        "class PassingWrapper:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.client = dependency\n"
        "    def read(self) -> None:\n"
        "        alias = self.client\n"
        "        mutate(alias)\n"
        "        self.client.work()\n\n"
        "class ClosureWrapper:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.connection = dependency\n"
        "    def read(self) -> None:\n"
        "        def mutate_later() -> None:\n"
        "            mutate(self.connection)\n"
        "        mutate_later()\n"
        "        self.connection.work()\n\n"
        "class ReceiverPassingWrapper:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.transport = dependency\n"
        "    def read(self) -> None:\n"
        "        mutate(self)\n"
        "        self.transport.work()\n\n"
        "def separate_sites() -> None:\n"
        "    first = Wrapper(First())\n"
        "    second = Wrapper(Second())\n"
        "    first.read()\n"
        "    second.read()\n"
        "    Wrapper.__new__(Wrapper)\n\n"
        "def same_line_sites() -> None:\n"
        "    first = Wrapper(First()); second = Wrapper(Second())\n"
        "    first.read()\n"
        "    second.read()\n\n"
        "def escaped_receiver() -> None:\n"
        "    receiver = Wrapper(First())\n"
        "    consume(receiver)\n"
        "    receiver.read()\n\n"
        "def aliased_receiver() -> None:\n"
        "    receiver = Wrapper(First())\n"
        "    alias = receiver\n"
        "    receiver.read()\n\n"
        "def reassigned_receiver() -> None:\n"
        "    receiver = Wrapper(First())\n"
        "    receiver = Wrapper(Second())\n"
        "    receiver.read()\n\n"
        "def transferred_dependency() -> None:\n"
        "    receiver = PassingWrapper(First())\n"
        "    receiver.read()\n\n"
        "def closure_transfer() -> None:\n"
        "    receiver = ClosureWrapper(First())\n"
        "    receiver.read()\n\n"
        "def receiver_transfer() -> None:\n"
        "    receiver = ReceiverPassingWrapper(First())\n"
        "    receiver.read()\n\n"
        "def shadowed_outer_receiver() -> None:\n"
        "    receiver = Wrapper(First())\n"
        "    def nested() -> None:\n"
        "        receiver = Wrapper(Second())\n"
        "        receiver.read()\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(package.parent, repository_id="contextual-calls", revision="v1", dirty=True)
    )
    result = extract_relations(definitions)
    separate_contexts = sorted(
        (
            item
            for item in result.contextual_calls
            if item.activation_source.qualified_name == "sample.wrappers.separate_sites"
        ),
        key=lambda item: item.activation_location.start_line,
    )
    assert len(separate_contexts) == 2
    assert [item.target.qualified_name for item in separate_contexts] == [
        "sample.dependencies.First.work",
        "sample.dependencies.Second.work",
    ]
    assert all(item.source.qualified_name == "sample.wrappers.Wrapper.read" for item in separate_contexts)
    assert all(not item.binding_mutation_after_escape_unverified for item in separate_contexts)
    assert len(result.contextual_calls) == 3
    nested_context = next(
        item
        for item in result.contextual_calls
        if item.activation_source.qualified_name == "sample.wrappers.shadowed_outer_receiver.nested"
    )
    assert nested_context.target.qualified_name == "sample.dependencies.Second.work"
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    assert not any(
        names[edge.source_id] == "sample.wrappers.Wrapper.read"
        and names[edge.target_id] in {"sample.dependencies.First.work", "sample.dependencies.Second.work"}
        for edge in result.edges
        if edge.kind == "calls"
    )


def test_constructor_attribute_binding_rejects_ambiguous_or_custom_attribute_behavior(
    tmp_path: Path,
) -> None:
    package = tmp_path / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "dependencies.py").write_text(
        "class First:\n"
        "    def work(self) -> None:\n"
        "        pass\n\n"
        "class Second:\n"
        "    def work(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "wrappers.py").write_text(
        "from .dependencies import First, Second\n\n"
        "Alias = First\n\n"
        "class Ambiguous:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.ambiguous_dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.ambiguous_dependency.work()\n\n"
        "class Aliased:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.aliased_dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.aliased_dependency.work()\n\n"
        "class Escaped:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.escaped_dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.escaped_dependency.work()\n\n"
        "class ReassignedParameter:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.reassigned_dependency = dependency\n"
        "        dependency = Second()\n"
        "    def read(self) -> None:\n"
        "        self.reassigned_dependency.work()\n\n"
        "class NestedWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.nested_dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.nested_dependency.work()\n\n"
        "class PassedToFactory:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.factory_dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.factory_dependency.work()\n\n"
        "class CustomAttributeProtocol:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.protocol_dependency = dependency\n"
        "    def __getattribute__(self, name):\n"
        "        return super().__getattribute__(name)\n"
        "    def read(self) -> None:\n"
        "        self.protocol_dependency.work()\n\n"
        "def return_escaped_class():\n"
        "    return Escaped\n\n"
        "def make_ambiguous():\n"
        "    Ambiguous(First())\n"
        "    Ambiguous(Second())\n\n"
        "def make_alias():\n"
        "    Aliased(Alias())\n\n"
        "def make_escaped():\n"
        "    Escaped(First())\n\n"
        "def make_reassigned():\n"
        "    ReassignedParameter(First())\n\n"
        "def make_nested_write(external):\n"
        "    external.child.nested_dependency = Second()\n"
        "    NestedWrite(First())\n\n"
        "def make_custom_protocol():\n"
        "    CustomAttributeProtocol(First())\n\n"
        "def pass_class_to_factory():\n"
        "    register_wrapper(PassedToFactory, Second())\n"
        "    PassedToFactory(First())\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(package.parent, repository_id="constructor-safety", revision="v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    resolved_calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    for method, field in (
        ("Ambiguous.read", "ambiguous_dependency"),
        ("Aliased.read", "aliased_dependency"),
        ("Escaped.read", "escaped_dependency"),
        ("ReassignedParameter.read", "reassigned_dependency"),
        ("NestedWrite.read", "nested_dependency"),
        ("PassedToFactory.read", "factory_dependency"),
        ("CustomAttributeProtocol.read", "protocol_dependency"),
    ):
        assert not any(
            source.endswith(method) and target.endswith(".work")
            for source, target in resolved_calls
        ), field


def test_constructor_attribute_binding_rejects_mutating_fields(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "dependency.py").write_text(
        "class Dependency:\n"
        "    def work(self) -> None:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package / "wrappers.py").write_text(
        "from .dependency import Dependency\n\n"
        "class ConditionalWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.conditional = dependency\n"
        "    def mutate(self, dependency, enabled):\n"
        "        if enabled:\n"
        "            self.conditional = dependency\n"
        "    def read(self) -> None:\n"
        "        self.conditional.work()\n\n"
        "class AugmentedWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.augmented = dependency\n"
        "    def mutate(self, dependency):\n"
        "        self.augmented += dependency\n"
        "    def read(self) -> None:\n"
        "        self.augmented.work()\n\n"
        "class DeletedWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.deleted = dependency\n"
        "    def mutate(self):\n"
        "        del self.deleted\n"
        "    def read(self) -> None:\n"
        "        self.deleted.work()\n\n"
        "class AnnotatedWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.annotated: Dependency = dependency\n"
        "    def read(self) -> None:\n"
        "        self.annotated.work()\n\n"
        "class SetattrWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.setattr_field = dependency\n"
        "    def mutate(self, other, dependency):\n"
        "        setattr(other, 'setattr_field', dependency)\n"
        "    def read(self) -> None:\n"
        "        self.setattr_field.work()\n\n"
        "class DelattrWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.delattr_field = dependency\n"
        "    def mutate(self, other):\n"
        "        delattr(other, 'delattr_field')\n"
        "    def read(self) -> None:\n"
        "        self.delattr_field.work()\n\n"
        "class DynamicSetattrWrite:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.dynamic = dependency\n"
        "    def mutate(self, other, field, dependency):\n"
        "        setattr(other, field, dependency)\n"
        "    def read(self) -> None:\n"
        "        self.dynamic.work()\n\n"
        "class BypassedInit:\n"
        "    def __init__(self, dependency) -> None:\n"
        "        self.bypassed = dependency\n"
        "    def read(self) -> None:\n"
        "        self.bypassed.work()\n\n"
        "def construct():\n"
        "    ConditionalWrite(Dependency())\n"
        "    AugmentedWrite(Dependency())\n"
        "    DeletedWrite(Dependency())\n"
        "    AnnotatedWrite(Dependency())\n"
        "    SetattrWrite(Dependency())\n"
        "    DelattrWrite(Dependency())\n"
        "    DynamicSetattrWrite(Dependency())\n"
        "    BypassedInit.__new__(BypassedInit)\n"
        "    BypassedInit(Dependency())\n",
        encoding="utf-8",
    )

    definitions = extract_definitions(
        build_source_snapshot(package.parent, repository_id="constructor-writes", revision="v1", dirty=True)
    )
    result = extract_relations(definitions)
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}
    resolved_calls = {
        (names[edge.source_id], names[edge.target_id])
        for edge in result.edges
        if edge.kind == "calls"
    }

    for method in (
        "ConditionalWrite.read",
        "AugmentedWrite.read",
        "DeletedWrite.read",
        "AnnotatedWrite.read",
        "SetattrWrite.read",
        "DelattrWrite.read",
        "DynamicSetattrWrite.read",
        "BypassedInit.read",
    ):
        assert not any(
            source.endswith(method) and target == "sample.dependency.Dependency.work"
            for source, target in resolved_calls
        ), method


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


def test_pluggy_hook_dispatch_stays_unresolved_with_same_name_candidates(tmp_path: Path) -> None:
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "plugin.py").write_text(
        "def demo_greet(name):\n"
        "    return name\n",
        encoding="utf-8",
    )
    (package / "host.py").write_text(
        "class Host:\n"
        "    def run(self, pm):\n"
        "        first = pm.hook.demo_greet(name='x')\n"
        "        return self.pm.hook.demo_greet(name='y'), first\n",
        encoding="utf-8",
    )
    result = extract_relations(
        extract_definitions(
            build_source_snapshot(tmp_path, repository_id="pluggy", revision="r1", dirty=False)
        )
    )
    names = {symbol.symbol_id: symbol.qualified_name for symbol in result.symbols}

    dispatches = [item for item in result.unresolved if ".hook.demo_greet(" in item.expression]
    assert len(dispatches) == 2
    assert {item.reason for item in dispatches} == {"pluggy_hook_dispatch"}
    assert all(
        [names[target] for target in item.possible_target_ids] == ["demo.plugin.demo_greet"]
        for item in dispatches
    )
    assert not any(
        edge.kind == "calls" and names[edge.target_id] == "demo.plugin.demo_greet"
        for edge in result.edges
    )
