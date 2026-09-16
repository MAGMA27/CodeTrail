"""Offline tests for file-level definition extraction reuse."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from app import service as service_module
from app.contracts import CreateIndexRequest
from app.indexing import (
    DEFINITION_EXTRACTION_RULES_VERSION,
    PYTHON_AST_VERSION,
    build_source_snapshot,
    extract_definitions,
)
from app.relations import extract_relations
from app.relations import RELATION_FACT_EXTRACTION_RULES_VERSION
from app.service import IndexService, RepositoryRegistry
from app.storage import IndexStore, StorageError


def _cache_kwargs(
    snapshot,
    *,
    scope_id: str = "project-a",
    configuration_hash: str = "config-a",
    definition_rules_version: str = DEFINITION_EXTRACTION_RULES_VERSION,
) -> dict:
    return {
        "scope_id": scope_id,
        "repository_id": snapshot.index.repository_id,
        "source_files": snapshot.files,
        "definition_rules_version": definition_rules_version,
        "python_version": PYTHON_AST_VERSION,
        "configuration_hash": configuration_hash,
    }


def _write_cache(
    store: IndexStore,
    snapshot,
    definitions,
    *,
    scope_id: str = "project-a",
    max_entries: int = 100,
    max_bytes: int = 100_000,
) -> int:
    return store.write_definition_cache(
        **_cache_kwargs(snapshot, scope_id=scope_id),
        entries=definitions.cache_entries,
        max_entries=max_entries,
        max_bytes=max_bytes,
    )


def _relation_cache_kwargs(
    snapshot,
    *,
    scope_id: str = "project-a",
    configuration_hash: str = "config-a",
) -> dict:
    return {
        "scope_id": scope_id,
        "repository_id": snapshot.index.repository_id,
        "source_files": snapshot.files,
        "relation_rules_version": RELATION_FACT_EXTRACTION_RULES_VERSION,
        "python_version": PYTHON_AST_VERSION,
        "configuration_hash": configuration_hash,
        "max_bytes": 100_000,
    }


def _write_relation_cache(
    store: IndexStore,
    snapshot,
    relation,
    *,
    scope_id: str = "project-a",
    max_entries: int = 100,
    max_bytes: int = 100_000,
) -> int:
    return store.write_relation_cache(
        scope_id=scope_id,
        repository_id=snapshot.index.repository_id,
        source_files=snapshot.files,
        entries=relation.fact_cache_entries,
        relation_rules_version=RELATION_FACT_EXTRACTION_RULES_VERSION,
        python_version=PYTHON_AST_VERSION,
        configuration_hash="config-a",
        max_entries=max_entries,
        max_bytes=max_bytes,
    )


def _three_file_repository(root: Path) -> None:
    (root / "a.py").write_text("def alpha() -> None:\n    pass\n", encoding="utf-8")
    (root / "b.py").write_text(
        "from a import alpha\n\n"
        "def beta() -> None:\n"
        "    alpha()\n",
        encoding="utf-8",
    )
    (root / "c.py").write_text("def gamma() -> None:\n    pass\n", encoding="utf-8")


def test_cold_and_hot_definition_results_match_and_relation_parse_stays_full(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")

    cold = extract_definitions(snapshot)
    assert cold.ast_parse_count == 3
    assert cold.cache_hits == 0
    assert cold.cache_misses == 3

    cold_relations = extract_relations(cold)
    assert cold_relations.ast_parse_count == 0
    assert cold_relations.shared_ast_reuse_count == 3
    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        assert _write_cache(store, snapshot, cold) == 3
        assert _write_relation_cache(store, snapshot, cold_relations) == 3
        cached = store.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
        relation_cached = store.load_relation_cache(**_relation_cache_kwargs(snapshot))
    with IndexStore(database) as reopened:
        cached_after_restart = reopened.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
        relation_cached_after_restart = reopened.load_relation_cache(
            **_relation_cache_kwargs(snapshot)
        )

    assert cached_after_restart == cached
    assert relation_cached_after_restart == relation_cached
    hot = extract_definitions(snapshot, cached=cached_after_restart)
    assert hot.symbols == cold.symbols
    assert hot.issues == cold.issues
    assert hot.ast_parse_count == 0
    assert hot.cache_hits == 3
    assert hot.cache_misses == 0
    hot_relations = extract_relations(hot, cached=relation_cached_after_restart)
    assert hot_relations.edges == cold_relations.edges
    assert hot_relations.unresolved == cold_relations.unresolved
    assert hot_relations.ast_parse_count == 0
    assert hot_relations.fact_cache_hits == 3
    assert hot_relations.fact_cache_misses == 0
    assert hot_relations.shared_ast_reuse_count == 0

    new_snapshot = build_source_snapshot(
        repository,
        repository_id="sample",
        index_id="index-next",
    )
    hot_new_index = extract_definitions(new_snapshot, cached=cached_after_restart)
    assert hot_new_index.cache_hits == 3
    assert hot_new_index.ast_parse_count == 0
    assert {symbol.symbol_id for symbol in hot_new_index.symbols}.isdisjoint(
        {symbol.symbol_id for symbol in cold.symbols}
    )
    assert [
        symbol.model_dump(exclude={"symbol_id", "index_id"}) for symbol in hot_new_index.symbols
    ] == [
        symbol.model_dump(exclude={"symbol_id", "index_id"}) for symbol in cold.symbols
    ]


def test_cache_keys_invalidate_changes_additions_deletions_renames_and_settings(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    first_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-first")
    first_definitions = extract_definitions(first_snapshot)
    first_relations = extract_relations(first_definitions)

    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        _write_cache(store, first_snapshot, first_definitions)
        _write_relation_cache(store, first_snapshot, first_relations)

        (repository / "b.py").write_text(
            "from a import alpha\n\n"
            "def beta() -> None:\n"
            "    alpha()\n\n"
            "def changed() -> None:\n"
            "    pass\n",
            encoding="utf-8",
        )
        changed_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-changed")
        changed_cached = store.load_definition_cache(
            **_cache_kwargs(changed_snapshot),
            max_bytes=100_000,
        )
        changed_relation_cached = store.load_relation_cache(
            **_relation_cache_kwargs(changed_snapshot)
        )
        changed = extract_definitions(changed_snapshot, cached=changed_cached)
        assert set(changed_cached) == {"a.py", "c.py"}
        assert set(changed_relation_cached) == {"a.py", "c.py"}
        assert (changed.cache_hits, changed.cache_misses, changed.ast_parse_count) == (2, 1, 1)

        (repository / "a.py").rename(repository / "renamed.py")
        (repository / "c.py").unlink()
        (repository / "d.py").write_text("def delta() -> None:\n    pass\n", encoding="utf-8")
        renamed_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-renamed")
        renamed_cached = store.load_definition_cache(
            **_cache_kwargs(renamed_snapshot),
            max_bytes=100_000,
        )
        renamed_relation_cached = store.load_relation_cache(
            **_relation_cache_kwargs(renamed_snapshot)
        )
        renamed = extract_definitions(renamed_snapshot, cached=renamed_cached)
        assert renamed_cached == {}
        assert renamed_relation_cached == {}
        assert (renamed.cache_hits, renamed.cache_misses, renamed.ast_parse_count) == (0, 3, 3)

        assert (
            store.load_definition_cache(
                **_cache_kwargs(renamed_snapshot, configuration_hash="different-config"),
                max_bytes=100_000,
            )
            == {}
        )
        assert (
            store.load_definition_cache(
                **_cache_kwargs(renamed_snapshot, definition_rules_version="different-rules"),
                max_bytes=100_000,
            )
            == {}
        )
        assert (
            store.load_relation_cache(
                **_relation_cache_kwargs(renamed_snapshot, configuration_hash="different-config")
            )
            == {}
        )
        assert (
            store.load_relation_cache(
                **_relation_cache_kwargs(renamed_snapshot)
                | {"relation_rules_version": "different-rules"}
            )
            == {}
        )


def test_relation_facts_rebind_unchanged_callers_to_current_targets(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "target.py").write_text(
        "def target() -> None:\n    pass\n",
        encoding="utf-8",
    )
    (repository / "caller.py").write_text(
        "from target import target\n\n"
        "def caller() -> None:\n"
        "    target()\n",
        encoding="utf-8",
    )
    first_snapshot = build_source_snapshot(
        repository,
        repository_id="sample",
        index_id="index-first",
    )
    first_definitions = extract_definitions(first_snapshot)
    first_relations = extract_relations(first_definitions)
    first_names = {symbol.symbol_id: symbol.qualified_name for symbol in first_relations.symbols}
    assert (
        "caller.caller",
        "target.target",
    ) in {
        (first_names[edge.source_id], first_names[edge.target_id])
        for edge in first_relations.edges
        if edge.kind == "calls"
    }

    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        _write_cache(store, first_snapshot, first_definitions)
        _write_relation_cache(store, first_snapshot, first_relations)

        (repository / "target.py").write_text(
            "def replacement() -> None:\n    pass\n",
            encoding="utf-8",
        )
        changed_snapshot = build_source_snapshot(
            repository,
            repository_id="sample",
            index_id="index-changed",
        )
        changed_definition_cache = store.load_definition_cache(
            **_cache_kwargs(changed_snapshot),
            max_bytes=100_000,
        )
        changed_relation_cache = store.load_relation_cache(
            **_relation_cache_kwargs(changed_snapshot)
        )
        assert set(changed_definition_cache) == {"caller.py"}
        assert set(changed_relation_cache) == {"caller.py"}

    changed_definitions = extract_definitions(
        changed_snapshot,
        cached=changed_definition_cache,
    )
    changed_relations = extract_relations(
        changed_definitions,
        cached=changed_relation_cache,
    )
    changed_names = {symbol.symbol_id: symbol.qualified_name for symbol in changed_relations.symbols}
    changed_calls = {
        (changed_names[edge.source_id], changed_names[edge.target_id])
        for edge in changed_relations.edges
        if edge.kind == "calls"
    }
    assert ("caller.caller", "target.target") not in changed_calls
    assert any(
        unresolved.reason in {"external_import", "call_target_not_in_snapshot"}
        for unresolved in changed_relations.unresolved
    )
    assert changed_relations.fact_cache_hits == 1
    assert changed_relations.fact_cache_misses == 1
    assert changed_relations.ast_parse_count == 0

    (repository / "target.py").write_text(
        "def target() -> None:\n    pass\n",
        encoding="utf-8",
    )
    restored_snapshot = build_source_snapshot(
        repository,
        repository_id="sample",
        index_id="index-restored",
    )
    restored_definitions = extract_definitions(
        restored_snapshot,
        cached=changed_definition_cache,
    )
    restored_relations = extract_relations(
        restored_definitions,
        cached=changed_relation_cache,
    )
    restored_names = {
        symbol.symbol_id: symbol.qualified_name for symbol in restored_relations.symbols
    }
    assert (
        "caller.caller",
        "target.target",
    ) in {
        (restored_names[edge.source_id], restored_names[edge.target_id])
        for edge in restored_relations.edges
        if edge.kind == "calls"
    }
    assert restored_relations.fact_cache_hits == 1
    assert restored_relations.fact_cache_misses == 1


def test_cached_relation_facts_preserve_alias_shadowing_and_unknown_semantics(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "targets.py").write_text(
        "def target() -> None:\n"
        "    pass\n\n"
        "def handle() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "other.py").write_text(
        "def handle() -> None:\n"
        "    pass\n",
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
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    cold = extract_relations(definitions)
    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        _write_cache(store, snapshot, definitions)
        _write_relation_cache(store, snapshot, cold)
        definition_cache = store.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
        relation_cache = store.load_relation_cache(**_relation_cache_kwargs(snapshot))
    hot_definitions = extract_definitions(snapshot, cached=definition_cache)
    hot = extract_relations(hot_definitions, cached=relation_cache)

    assert hot.edges == cold.edges
    assert hot.unresolved == cold.unresolved
    assert hot.fact_cache_hits == len(snapshot.files)
    assert hot.fact_cache_misses == 0
    assert hot.ast_parse_count == 0


def test_cached_object_method_facts_rebind_to_changed_and_restored_targets(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    model_source = (
        "class Widget:\n"
        "    def run(self) -> None:\n"
        "        pass\n"
    )
    (package / "models.py").write_text(model_source, encoding="utf-8")
    (package / "caller.py").write_text(
        "from .models import Widget\n\n"
        "def call() -> None:\n"
        "    obj = Widget()\n"
        "    obj.run()\n",
        encoding="utf-8",
    )
    database = tmp_path / "index.sqlite3"

    snapshot_one = build_source_snapshot(
        repository, repository_id="sample", index_id="index-one"
    )
    definitions_one = extract_definitions(snapshot_one)
    relations_one = extract_relations(definitions_one)
    names_one = {symbol.symbol_id: symbol.qualified_name for symbol in relations_one.symbols}
    assert (
        "sample.caller.call",
        "sample.models.Widget.run",
    ) in {
        (names_one[edge.source_id], names_one[edge.target_id])
        for edge in relations_one.edges
        if edge.kind == "calls"
    }

    with IndexStore(database) as store:
        _write_cache(store, snapshot_one, definitions_one)
        _write_relation_cache(store, snapshot_one, relations_one)

        (package / "models.py").write_text(
            "class Widget:\n"
            "    def stop(self) -> None:\n"
            "        pass\n",
            encoding="utf-8",
        )
        snapshot_two = build_source_snapshot(
            repository, repository_id="sample", index_id="index-two"
        )
        definitions_two = extract_definitions(
            snapshot_two,
            cached=store.load_definition_cache(
                **_cache_kwargs(snapshot_two), max_bytes=100_000
            ),
        )
        relations_two = extract_relations(
            definitions_two,
            cached=store.load_relation_cache(**_relation_cache_kwargs(snapshot_two)),
        )
        assert any(
            item.reason == "object_method_not_resolved"
            for item in relations_two.unresolved
            if item.expression == "obj.run()"
        )
        assert not any(
            names_one.get(edge.target_id) == "sample.models.Widget.run"
            for edge in relations_two.edges
        )

        (package / "models.py").write_text(model_source, encoding="utf-8")
        snapshot_three = build_source_snapshot(
            repository, repository_id="sample", index_id="index-three"
        )
        definitions_three = extract_definitions(
            snapshot_three,
            cached=store.load_definition_cache(
                **_cache_kwargs(snapshot_three), max_bytes=100_000
            ),
        )
        relations_three = extract_relations(
            definitions_three,
            cached=store.load_relation_cache(**_relation_cache_kwargs(snapshot_three)),
        )

    names_three = {symbol.symbol_id: symbol.qualified_name for symbol in relations_three.symbols}
    restored_edges = {
        (names_three[edge.source_id], names_three[edge.target_id])
        for edge in relations_three.edges
        if edge.kind == "calls"
    }
    assert ("sample.caller.call", "sample.models.Widget.run") in restored_edges
    assert relations_three.fact_cache_hits == len(snapshot_three.files)
    assert all(edge.index_id == "index-three" for edge in relations_three.edges)
    assert not any(
        edge.target_id in {item.target_id for item in relations_one.edges}
        for edge in relations_three.edges
    )


def test_relation_fact_cache_preserves_syntax_diagnostics_and_skips_incomplete_walks(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (repository / "good.py").write_text("def good() -> None:\n    missing()\n", encoding="utf-8")
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    cold = extract_relations(definitions)
    assert cold.fact_cache_misses == 2
    assert cold.ast_parse_count == 0
    assert cold.shared_ast_reuse_count == 1
    assert len(cold.fact_cache_entries) == 2

    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        _write_cache(store, snapshot, definitions)
        _write_relation_cache(store, snapshot, cold)
        definition_cache = store.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
        relation_cache = store.load_relation_cache(**_relation_cache_kwargs(snapshot))
    hot_definitions = extract_definitions(snapshot, cached=definition_cache)
    hot = extract_relations(hot_definitions, cached=relation_cache)
    assert hot.edges == cold.edges
    assert hot.unresolved == cold.unresolved
    assert hot.issues == cold.issues
    assert hot.fact_cache_hits == 2
    assert hot.fact_cache_misses == 0
    assert hot.ast_parse_count == 0
    assert hot.shared_ast_reuse_count == 0

    incomplete = extract_relations(definitions, max_seconds=1e-12)
    assert incomplete.truncated is True
    assert incomplete.truncation_reason == "max_seconds exceeded"
    assert {path for path, _facts in incomplete.fact_cache_entries} == {"bad.py"}
    with IndexStore(tmp_path / "incomplete.sqlite3") as store:
        assert _write_relation_cache(store, snapshot, incomplete) == 1
        assert store.connection.execute("SELECT COUNT(*) FROM relation_cache").fetchone()[0] == 1


def test_relation_cache_corruption_and_shared_capacity_are_bounded(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    relations = extract_relations(definitions)
    database = tmp_path / "index.sqlite3"

    with IndexStore(database) as store:
        _write_cache(store, snapshot, definitions)
        _write_relation_cache(store, snapshot, relations, max_entries=4)
        total = store.connection.execute(
            "SELECT COUNT(*) FROM definition_cache WHERE scope_id = 'project-a' "
            "UNION ALL SELECT COUNT(*) FROM relation_cache WHERE scope_id = 'project-a'"
        ).fetchall()
        assert sum(row[0] for row in total) <= 4
        original = store.connection.execute(
            "SELECT result_json FROM relation_cache WHERE scope_id = ? LIMIT 1",
            ("project-a",),
        ).fetchone()
        assert original is not None
        mismatched_module = json.loads(original["result_json"])
        mismatched_module["module_name"] = "wrong_module"
        store.connection.execute(
            "UPDATE relation_cache SET result_json = ?, result_bytes = ? WHERE scope_id = ?",
            (
                json.dumps(mismatched_module, separators=(",", ":")),
                len(json.dumps(mismatched_module, separators=(",", ":")).encode("utf-8")),
                "project-a",
            ),
        )
        assert store.load_relation_cache(**_relation_cache_kwargs(snapshot)) == {}
        store.connection.execute(
            "UPDATE relation_cache SET result_json = ? WHERE scope_id = ?",
            ("{not-json", "project-a"),
        )
        assert store.load_relation_cache(**_relation_cache_kwargs(snapshot)) == {}
        unsupported = original["result_json"].replace(
            '"format_version":"relation-facts-v2"',
            '"format_version":"relation-facts-v0"',
        )
        store.connection.execute(
            "UPDATE relation_cache SET result_json = ?, result_bytes = ? WHERE scope_id = ?",
            (unsupported, len(unsupported.encode("utf-8")), "project-a"),
        )
        assert store.load_relation_cache(**_relation_cache_kwargs(snapshot)) == {}


def test_cache_keeps_package_init_semantics_separate_from_normal_modules(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    package = repository / "pkg"
    package.mkdir(parents=True)
    content = "def same() -> None:\n    pass\n"
    (package / "__init__.py").write_text(content, encoding="utf-8")
    init_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-init")
    init_definitions = extract_definitions(init_snapshot)
    database = tmp_path / "index.sqlite3"

    with IndexStore(database) as store:
        _write_cache(store, init_snapshot, init_definitions)
        (package / "__init__.py").rename(package / "module.py")
        module_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-module")
        assert (
            store.load_definition_cache(
                **_cache_kwargs(module_snapshot),
                max_bytes=100_000,
            )
            == {}
        )

    module_definitions = extract_definitions(module_snapshot)
    names = {symbol.kind: symbol.qualified_name for symbol in module_definitions.symbols}
    assert names == {"module": "pkg.module", "function": "pkg.module.same"}


def test_cache_is_project_scoped_and_corruption_is_a_safe_miss(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    database = tmp_path / "index.sqlite3"

    with IndexStore(database) as store:
        _write_cache(store, snapshot, definitions, scope_id="project-a")
        assert (
            store.load_definition_cache(
                **_cache_kwargs(snapshot, scope_id="project-b"),
                max_bytes=100_000,
            )
            == {}
        )
        store.connection.execute(
            "UPDATE definition_cache SET result_json = ? WHERE scope_id = ?",
            ("{not-json", "project-a"),
        )
        corrupted = store.load_definition_cache(
            **_cache_kwargs(snapshot, scope_id="project-a"),
            max_bytes=100_000,
        )
        store.connection.execute(
            "UPDATE definition_cache SET result_json = ? WHERE scope_id = ?",
            (
                '{"definitions":[],"format_version":"definition-cache-v0","issue":null}',
                "project-a",
            ),
        )
        unsupported = store.load_definition_cache(
            **_cache_kwargs(snapshot, scope_id="project-a"),
            max_bytes=100_000,
        )

    assert corrupted == {}
    assert unsupported == {}
    cold_after_corruption = extract_definitions(snapshot, cached=corrupted)
    assert (cold_after_corruption.cache_hits, cold_after_corruption.cache_misses) == (0, 3)


def test_cache_capacity_skips_large_values_and_prunes_only_cache_rows(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    database = tmp_path / "index.sqlite3"

    with IndexStore(database) as store:
        assert _write_cache(store, snapshot, definitions, max_entries=1, max_bytes=100_000) == 3
        assert store.connection.execute("SELECT COUNT(*) FROM definition_cache").fetchone()[0] == 1
        assert len(
            store.load_definition_cache(
                **_cache_kwargs(snapshot),
                max_bytes=100_000,
            )
        ) == 1

        oversized_database = tmp_path / "oversized.sqlite3"
    with IndexStore(oversized_database) as oversized:
        assert _write_cache(oversized, snapshot, definitions, max_bytes=1) == 0
        assert oversized.connection.execute("SELECT COUNT(*) FROM definition_cache").fetchone()[0] == 0


def test_concurrent_cache_writes_are_transactional(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-sample")
    definitions = extract_definitions(snapshot)
    database = tmp_path / "index.sqlite3"

    def write_once() -> int:
        with IndexStore(database) as store:
            return _write_cache(store, snapshot, definitions)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _item: write_once(), range(2))) == [3, 3]

    with IndexStore(database) as store:
        cached = store.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
    assert set(cached) == {"a.py", "b.py", "c.py"}


def test_index_service_reports_cold_hot_restart_and_changed_file_counts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    data_root = tmp_path / "data"
    request = CreateIndexRequest(repository_id="sample", revision="working-tree", dirty=True)

    first_stats = []
    first_service = IndexService(RepositoryRegistry({"sample": repository}, data_root=data_root))
    first = first_service.create_index(
        request,
        cache_scope_id="project-a",
        on_build_stats=first_stats.append,
    )
    assert len(first_stats) == 1
    assert (
        first_stats[0].definition_cache_hits,
        first_stats[0].definition_cache_misses,
        first_stats[0].definition_ast_parse_count,
            first_stats[0].relation_ast_parse_count,
            first_stats[0].relation_fact_cache_hits,
            first_stats[0].relation_fact_cache_misses,
            first_stats[0].relation_shared_ast_reuse_count,
            first_stats[0].definition_cache_entries_written,
            first_stats[0].relation_cache_entries_written,
        ) == (0, 3, 3, 0, 0, 3, 3, 3, 3)

    second_stats = []
    restarted_service = IndexService(RepositoryRegistry({"sample": repository}, data_root=data_root))
    second = restarted_service.create_index(
        request,
        cache_scope_id="project-a",
        on_build_stats=second_stats.append,
    )
    assert second == first
    assert (
        second_stats[0].definition_cache_hits,
        second_stats[0].definition_cache_misses,
        second_stats[0].definition_ast_parse_count,
            second_stats[0].relation_ast_parse_count,
            second_stats[0].relation_fact_cache_hits,
            second_stats[0].relation_fact_cache_misses,
            second_stats[0].relation_shared_ast_reuse_count,
            second_stats[0].definition_cache_entries_written,
            second_stats[0].relation_cache_entries_written,
        ) == (3, 0, 0, 0, 3, 0, 0, 0, 0)

    (repository / "b.py").write_text(
        "from a import alpha\n\n"
        "def beta() -> None:\n"
        "    alpha()\n\n"
        "def changed() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    changed_stats = []
    changed = restarted_service.create_index(
        request,
        cache_scope_id="project-a",
        on_build_stats=changed_stats.append,
    )
    assert changed.index.index_id != first.index.index_id
    assert (
        changed_stats[0].definition_cache_hits,
        changed_stats[0].definition_cache_misses,
        changed_stats[0].definition_ast_parse_count,
            changed_stats[0].relation_ast_parse_count,
            changed_stats[0].relation_fact_cache_hits,
            changed_stats[0].relation_fact_cache_misses,
            changed_stats[0].relation_shared_ast_reuse_count,
            changed_stats[0].definition_cache_entries_written,
            changed_stats[0].relation_cache_entries_written,
        ) == (2, 1, 1, 0, 2, 1, 1, 1, 1)


def test_cache_write_failure_does_not_fail_or_partially_replace_index(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    service = IndexService(RepositoryRegistry({"sample": repository}, data_root=tmp_path / "data"))

    def fail_write(_store, **_kwargs):
        raise StorageError("simulated cache write failure")

    monkeypatch.setattr(IndexStore, "write_definition_cache", fail_write)
    response = service.create_index(CreateIndexRequest(repository_id="sample"))

    with IndexStore.for_repository(repository) as store:
        assert store.has_index(response.index.index_id)
        assert store.connection.execute("SELECT COUNT(*) FROM definition_cache").fetchone()[0] == 0


def test_build_failure_does_not_leave_definition_cache_rows(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _three_file_repository(repository)
    service = IndexService(RepositoryRegistry({"sample": repository}, data_root=tmp_path / "data"))

    def fail_relations(_definitions, **_kwargs):
        raise RuntimeError("simulated relation failure")

    monkeypatch.setattr(service_module, "extract_relations", fail_relations)
    with pytest.raises(RuntimeError, match="simulated relation failure"):
        service.create_index(CreateIndexRequest(repository_id="sample"))

    with IndexStore.for_repository(repository) as store:
        assert not store.has_any_index()
        assert store.connection.execute("SELECT COUNT(*) FROM definition_cache").fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM relation_cache").fetchone()[0] == 0
