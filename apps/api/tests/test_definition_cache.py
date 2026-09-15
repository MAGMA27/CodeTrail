"""Offline tests for file-level definition extraction reuse."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        assert _write_cache(store, snapshot, cold) == 3
        cached = store.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )
    with IndexStore(database) as reopened:
        cached_after_restart = reopened.load_definition_cache(
            **_cache_kwargs(snapshot),
            max_bytes=100_000,
        )

    assert cached_after_restart == cached
    hot = extract_definitions(snapshot, cached=cached_after_restart)
    assert hot.symbols == cold.symbols
    assert hot.issues == cold.issues
    assert hot.ast_parse_count == 0
    assert hot.cache_hits == 3
    assert hot.cache_misses == 0
    cold_relations = extract_relations(cold)
    hot_relations = extract_relations(hot)
    assert hot_relations.edges == cold_relations.edges
    assert hot_relations.unresolved == cold_relations.unresolved
    assert hot_relations.ast_parse_count == 3
    assert cold_relations.ast_parse_count == 3

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

    database = tmp_path / "index.sqlite3"
    with IndexStore(database) as store:
        _write_cache(store, first_snapshot, first_definitions)

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
        changed = extract_definitions(changed_snapshot, cached=changed_cached)
        assert set(changed_cached) == {"a.py", "c.py"}
        assert (changed.cache_hits, changed.cache_misses, changed.ast_parse_count) == (2, 1, 1)

        (repository / "a.py").rename(repository / "renamed.py")
        (repository / "c.py").unlink()
        (repository / "d.py").write_text("def delta() -> None:\n    pass\n", encoding="utf-8")
        renamed_snapshot = build_source_snapshot(repository, repository_id="sample", index_id="index-renamed")
        renamed_cached = store.load_definition_cache(
            **_cache_kwargs(renamed_snapshot),
            max_bytes=100_000,
        )
        renamed = extract_definitions(renamed_snapshot, cached=renamed_cached)
        assert renamed_cached == {}
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
        first_stats[0].definition_cache_entries_written,
    ) == (0, 3, 3, 3, 3)

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
        second_stats[0].definition_cache_entries_written,
    ) == (3, 0, 0, 3, 0)

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
        changed_stats[0].definition_cache_entries_written,
    ) == (2, 1, 1, 3, 1)


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

    def fail_relations(_definitions):
        raise RuntimeError("simulated relation failure")

    monkeypatch.setattr(service_module, "extract_relations", fail_relations)
    with pytest.raises(RuntimeError, match="simulated relation failure"):
        service.create_index(CreateIndexRequest(repository_id="sample"))

    with IndexStore.for_repository(repository) as store:
        assert not store.has_any_index()
        assert store.connection.execute("SELECT COUNT(*) FROM definition_cache").fetchone()[0] == 0
