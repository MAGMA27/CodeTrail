"""Offline tests for SQLite persistence and bounded index queries."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.indexing import build_source_snapshot, extract_definitions
from app.relations import RelationIndex, extract_relations
from app.storage import (
    IndexStore,
    StorageConflictError,
    StorageInputError,
    StorageNotFoundError,
    StorageError,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


def fixture_relation(
    *,
    revision: str = "fixture-v1",
    dirty: bool = False,
    index_id: str | None = None,
) -> RelationIndex:
    snapshot = build_source_snapshot(
        FIXTURE_ROOT,
        repository_id="timeout-demo",
        revision=revision,
        dirty=dirty,
        index_id=index_id,
    )
    return extract_relations(extract_definitions(snapshot))


def relation_for_repository(
    repository: Path,
    *,
    repository_id: str,
    index_id: str | None = None,
) -> RelationIndex:
    snapshot = build_source_snapshot(
        repository,
        repository_id=repository_id,
        revision="working-tree",
        dirty=True,
        index_id=index_id,
    )
    return extract_relations(extract_definitions(snapshot))


def test_fts5_roundtrip_and_reopen_preserve_the_complete_relation_index(tmp_path: Path) -> None:
    relation = fixture_relation()
    database = tmp_path / "index.sqlite3"

    with IndexStore(database) as store:
        assert store.fts5_available is True
        store.write_index(relation)
        assert store.load_index(relation.snapshot.index.index_id) == relation

    with IndexStore(database) as reopened:
        assert reopened.load_index(relation.snapshot.index.index_id) == relation


def test_roundtrip_preserves_definition_issues_and_relation_truncation(tmp_path: Path) -> None:
    repository = tmp_path / "issues"
    repository.mkdir()
    (repository / "valid.py").write_text("def valid() -> None:\n    pass\n", encoding="utf-8")
    (repository / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    relation_with_issue = relation_for_repository(repository, repository_id="issues")
    truncated = fixture_relation(index_id="index-truncated")
    truncated = extract_relations(truncated.definitions, max_edges=2)

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation_with_issue)
        store.write_index(truncated)
        assert store.load_index(relation_with_issue.snapshot.index.index_id) == relation_with_issue
        assert store.load_index(truncated.snapshot.index.index_id) == truncated


def test_search_is_snapshot_scoped_deterministic_and_safe_for_special_queries(tmp_path: Path) -> None:
    relation = fixture_relation()
    index_id = relation.snapshot.index.index_id

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)

        symbol_results = store.search_symbols(index_id, "load_settings", limit=4)
        assert any(item.symbol.qualified_name == "timeout_demo.config.load_settings" for item in symbol_results)

        source_results = store.search_symbols(index_id, "TIMEOUT_DEMO_TIMEOUT", limit=4)
        assert any(item.symbol.qualified_name == "timeout_demo.config.load_settings" for item in source_results)
        assert all(item.snippet for item in source_results)
        assert source_results == store.search_symbols(index_id, "TIMEOUT_DEMO_TIMEOUT", limit=4)
        assert len(store.search_symbols(index_id, "handler", limit=1)) == 1
        assert store.search_symbols(index_id, "@@@") == ()

        with pytest.raises(StorageInputError):
            store.search_symbols(index_id, "")
        with pytest.raises(StorageNotFoundError):
            store.search_symbols("missing-index", "load_settings")
        with pytest.raises(StorageInputError):
            store.search_symbols(index_id, "load_settings", limit=41)


def test_read_evidence_uses_the_persisted_snapshot_after_worktree_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    source = repository / "module.py"
    source.write_text("def first() -> str:\n    return 'old'\n", encoding="utf-8")
    relation = relation_for_repository(repository, repository_id="mutable")
    source_file = relation.snapshot.files[0]

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        source.write_text("def second() -> str:\n    return 'new'\n", encoding="utf-8")

        evidence = store.read_evidence(
            relation.snapshot.index.index_id,
            "module.py",
            1,
            2,
        )
        assert evidence.excerpt == "def first() -> str:\n    return 'old'"
        assert evidence.content_hash == source_file.content_hash

        with pytest.raises(StorageNotFoundError):
            store.read_evidence(relation.snapshot.index.index_id, "missing.py", 1, 1)
        with pytest.raises(StorageInputError):
            store.read_evidence(relation.snapshot.index.index_id, "../module.py", 1, 1)
        with pytest.raises(StorageInputError):
            store.read_evidence(relation.snapshot.index.index_id, "module.py", 1, 3)
        with pytest.raises(StorageNotFoundError):
            store.read_evidence("missing-index", "module.py", 1, 1)


def test_duplicate_write_is_idempotent_and_conflicting_payload_is_rejected(tmp_path: Path) -> None:
    relation = fixture_relation()
    index_id = relation.snapshot.index.index_id

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        counts_before = {
            table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("index_snapshots", "snapshot_files", "symbols", "static_edges", "unresolved_references", "symbol_fts")
        }
        store.write_index(relation)
        counts_after = {
            table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in counts_before
        }
        assert counts_after == counts_before

        conflicting = fixture_relation(revision="fixture-v2", dirty=True, index_id=index_id)
        with pytest.raises(StorageConflictError):
            store.write_index(conflicting)


def test_failed_transaction_leaves_no_partial_index_or_fts_rows(tmp_path: Path) -> None:
    relation = fixture_relation()

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.connection.execute(
            """
            CREATE TRIGGER fail_symbol_insert
            BEFORE INSERT ON symbols
            BEGIN
                SELECT RAISE(ABORT, 'injected symbol failure');
            END
            """
        )

        with pytest.raises(StorageError):
            store.write_index(relation)

        assert store.has_index(relation.snapshot.index.index_id) is False
        for table in (
            "snapshot_files",
            "snapshot_issues",
            "symbols",
            "static_edges",
            "unresolved_references",
            "unresolved_targets",
            "symbol_fts",
        ):
            assert store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_two_snapshots_are_isolated_in_search_evidence_and_neighbors(tmp_path: Path) -> None:
    repo_one = tmp_path / "repo-one"
    repo_two = tmp_path / "repo-two"
    repo_one.mkdir()
    repo_two.mkdir()
    (repo_one / "module.py").write_text(
        "def alpha() -> str:\n    return 'one'\n",
        encoding="utf-8",
    )
    (repo_two / "module.py").write_text(
        "def beta() -> str:\n    return 'two'\n",
        encoding="utf-8",
    )
    relation_one = relation_for_repository(repo_one, repository_id="one")
    relation_two = relation_for_repository(repo_two, repository_id="two")
    id_one = relation_one.snapshot.index.index_id
    id_two = relation_two.snapshot.index.index_id

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation_one)
        store.write_index(relation_two)
        assert store.search_symbols(id_one, "alpha")
        assert store.search_symbols(id_one, "beta") == ()
        assert store.search_symbols(id_two, "beta")
        assert store.search_symbols(id_two, "alpha") == ()
        assert store.read_evidence(id_one, "module.py", 1, 2).excerpt.endswith("'one'")
        assert store.read_evidence(id_two, "module.py", 1, 2).excerpt.endswith("'two'")

        with pytest.raises(StorageNotFoundError):
            store.get_neighbors(id_one, relation_two.symbols[0].symbol_id, "both", 1, 40)


def test_neighbor_bfs_supports_direction_depth_filters_cycles_and_budgets(tmp_path: Path) -> None:
    relation = fixture_relation()
    index_id = relation.snapshot.index.index_id
    by_name = {symbol.qualified_name: symbol for symbol in relation.symbols}

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        start_id = by_name["timeout_demo.app.start"].symbol_id
        depth_one = store.get_neighbors(
            index_id,
            start_id,
            "callees",
            depth=1,
            limit=40,
            edge_kinds=("calls",),
        )
        assert {item.symbol.qualified_name for item in depth_one.neighbors} == {
            "timeout_demo.client.initialize_client",
            "timeout_demo.config.load_settings",
            "timeout_demo.dispatch.dispatch",
        }
        assert all(item.edge.kind == "calls" for item in depth_one.neighbors)

        depth_two = store.get_neighbors(
            index_id,
            start_id,
            "callees",
            depth=2,
            limit=40,
            edge_kinds=("calls",),
        )
        assert "timeout_demo.client.Client" in {item.qualified_name for item in depth_two.nodes}
        assert all(
            endpoint in {node.symbol_id for node in depth_two.nodes}
            for item in depth_two.neighbors
            for endpoint in (item.edge.source_id, item.edge.target_id)
        )

        callers = store.get_neighbors(
            index_id,
            start_id,
            "callers",
            depth=1,
            limit=40,
            edge_kinds=("calls",),
        )
        assert {item.symbol.qualified_name for item in callers.neighbors} == {"timeout_demo.__main__.main"}

        limited = store.get_neighbors(
            index_id,
            start_id,
            "callees",
            depth=2,
            limit=2,
            edge_kinds=("calls",),
        )
        assert len(limited.nodes) == 2
        assert limited.truncated is True
        assert limited.truncation_reason == "max_nodes reached"

        edge_limited = store.get_neighbors(
            index_id,
            start_id,
            "callees",
            depth=2,
            limit=40,
            edge_kinds=("calls",),
            max_edges=1,
        )
        assert len(edge_limited.neighbors) == 1
        assert edge_limited.truncation_reason == "max_edges reached"

        output_limited = store.get_neighbors(
            index_id,
            start_id,
            "callees",
            depth=2,
            limit=40,
            edge_kinds=("calls",),
            max_output_bytes=1,
        )
        assert output_limited.neighbors == ()
        assert output_limited.truncation_reason == "max_output_bytes reached"

        with pytest.raises(StorageInputError):
            store.get_neighbors(index_id, start_id, "callees", depth=3, limit=40)
        with pytest.raises(StorageNotFoundError):
            store.get_neighbors(index_id, "missing-symbol", "both", 1, 40)


def test_neighbor_bfs_keeps_parallel_edges_and_terminates_on_a_cycle(tmp_path: Path) -> None:
    repository = tmp_path / "cycle"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def first() -> None:\n"
        "    second()\n"
        "    second()\n\n"
        "def second() -> None:\n"
        "    first()\n",
        encoding="utf-8",
    )
    relation = relation_for_repository(repository, repository_id="cycle")
    first = next(symbol for symbol in relation.symbols if symbol.qualified_name == "module.first")

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        result = store.get_neighbors(
            relation.snapshot.index.index_id,
            first.symbol_id,
            "both",
            depth=2,
            limit=3,
            edge_kinds=("calls",),
        )

        assert {node.qualified_name for node in result.nodes} == {"module.first", "module.second"}
        assert len(result.neighbors) == 3
        assert len({item.edge.edge_id for item in result.neighbors}) == 3
        assert result.truncated is False


def test_dynamic_unresolved_references_are_not_static_neighbors(tmp_path: Path) -> None:
    relation = fixture_relation()
    dispatch = next(
        symbol for symbol in relation.symbols if symbol.qualified_name == "timeout_demo.dispatch.dispatch"
    )

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        result = store.get_neighbors(
            relation.snapshot.index.index_id,
            dispatch.symbol_id,
            "callees",
            depth=2,
            limit=40,
            edge_kinds=("calls",),
        )
        names = {item.symbol.qualified_name for item in result.neighbors}
        assert "timeout_demo.handlers.handle" not in names
        assert "timeout_demo.other_handlers.handle" not in names
        assert any(item.reason == "dynamic_call_target" for item in relation.unresolved)
