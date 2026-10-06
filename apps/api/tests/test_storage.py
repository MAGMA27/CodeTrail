"""Offline tests for SQLite persistence and bounded index queries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app import storage as storage_module
from app.contracts import CodeEdge, SourceLocation
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


def test_static_impact_keeps_simple_paths_deduplicates_nodes_and_retains_cycle_edges(tmp_path: Path) -> None:
    relation = fixture_relation(index_id="impact-sample")
    by_name = {symbol.qualified_name: symbol for symbol in relation.symbols}
    root = by_name["timeout_demo.__main__.main"]
    branch = by_name["timeout_demo.app.start"]
    target = by_name["timeout_demo.config.load_settings"]
    dynamic_symbol = by_name["timeout_demo.dispatch.dispatch"]

    def edge(edge_id: str, source, target_symbol) -> CodeEdge:
        return CodeEdge(
            edge_id=edge_id,
            index_id=relation.snapshot.index.index_id,
            source_id=source.symbol_id,
            target_id=target_symbol.symbol_id,
            kind="calls",
            extraction_method="fixture",
            source_location=SourceLocation(
                path=source.path,
                start_line=source.start_line,
                end_line=source.start_line,
            ),
        )

    relation = replace(
        relation,
        edges=(
            edge("impact-root-branch", root, branch),
            edge("impact-root-target", root, target),
            edge("impact-branch-target", branch, target),
            edge("impact-branch-root", branch, root),
        ),
    )
    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        result = store.get_static_impact(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "callees",
            depth=2,
            limit=40,
        )
        assert {symbol.symbol_id for symbol in result.nodes} == {
            root.symbol_id,
            branch.symbol_id,
            target.symbol_id,
        }
        assert result.reachable_node_count == 2
        assert result.index_truncated is False
        assert result.index_truncation_reason is None
        assert result.index_unresolved_truncated is False
        assert result.index_unresolved_truncation_reason is None
        assert {tuple(path.symbol_ids) for path in result.paths} == {
            (root.symbol_id, branch.symbol_id),
            (root.symbol_id, target.symbol_id),
            (root.symbol_id, branch.symbol_id, target.symbol_id),
        }
        assert "impact-branch-root" in {edge.edge_id for edge in result.edges}
        assert all(len(path.symbol_ids) == len(set(path.symbol_ids)) for path in result.paths)

        shallow = store.get_static_impact(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "callees",
            depth=1,
            limit=40,
        )
        assert all(path.depth == 1 for path in shallow.paths)

        limited = store.get_static_impact(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "callees",
            depth=2,
            limit=2,
        )
        assert limited.truncated is True
        assert limited.truncation_reason == "max_nodes reached"
        assert limited.reachable_node_count == 1

        dynamic = store.get_static_impact(
            relation.snapshot.index.index_id,
            dynamic_symbol.symbol_id,
            "callees",
            depth=2,
            limit=40,
        )
        assert any(item.reason == "dynamic_call_target" for item in dynamic.unresolved)
        assert not any(
            symbol.qualified_name in {"timeout_demo.handlers.handle", "timeout_demo.other_handlers.handle"}
            for symbol in dynamic.nodes
        )


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


def test_neighbors_exposes_index_relation_truncation_separately(tmp_path: Path) -> None:
    relation = fixture_relation(index_id="index-neighbor-coverage")
    relation = replace(
        relation,
        unresolved_truncated=True,
        unresolved_truncation_reason="max_unresolved reached",
    )
    root = next(symbol for symbol in relation.symbols if symbol.qualified_name == "timeout_demo.app.start")

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        result = store.get_neighbors(
            relation.snapshot.index.index_id,
            root.symbol_id,
            "both",
            depth=1,
            limit=40,
        )

    assert result.truncated is False
    assert result.truncation_reason is None
    assert result.index_truncated is False
    assert result.index_truncation_reason is None
    assert result.index_unresolved_truncated is True
    assert result.index_unresolved_truncation_reason == "max_unresolved reached"


def test_legacy_index_without_unresolved_coverage_is_unknown(tmp_path: Path) -> None:
    relation = fixture_relation(index_id="index-legacy-coverage")
    relation = replace(
        relation,
        truncated=True,
        truncation_reason="max_unresolved reached",
        unresolved_truncated=None,
        unresolved_truncation_reason=None,
    )

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        store.connection.execute(
            "UPDATE index_snapshots SET unresolved_truncated = NULL, "
            "unresolved_truncation_reason = NULL WHERE index_id = ?",
            (relation.snapshot.index.index_id,),
        )
        loaded = store.load_index(relation.snapshot.index.index_id)
        assert loaded.truncated is True
        assert loaded.truncation_reason == "max_unresolved reached"
        assert loaded.unresolved_truncated is None
        assert loaded.unresolved_truncation_reason is None
        root = next(symbol for symbol in loaded.symbols if symbol.qualified_name == "timeout_demo.app.start")
        result = store.get_neighbors(
            loaded.snapshot.index.index_id,
            root.symbol_id,
            "both",
            depth=1,
            limit=40,
        )

    assert result.index_truncated is True
    assert result.index_truncation_reason == "max_unresolved reached"
    assert result.index_unresolved_truncated is None
    assert result.index_unresolved_truncation_reason is None


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


def test_search_prioritizes_exact_names_and_scopes_body_matches_to_symbols(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    package = repository / "pkg"
    package.mkdir(parents=True)
    (package / "one.py").write_text(
        "def handle() -> str:\n"
        "    return 'needle'\n\n"
        "def sibling() -> str:\n"
        "    return 'different'\n",
        encoding="utf-8",
    )
    (package / "two.py").write_text(
        "def handle() -> str:\n"
        "    return 'other'\n",
        encoding="utf-8",
    )
    relation = relation_for_repository(repository, repository_id="search-ranking")
    index_id = relation.snapshot.index.index_id

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)

        leaf_matches = store.search_symbols_bounded(index_id, "handle", limit=2)
        assert [item.symbol.qualified_name for item in leaf_matches.matches] == [
            "pkg.one.handle",
            "pkg.two.handle",
        ]
        assert leaf_matches.truncated is True

        partial_matches = store.search_symbols(index_id, "hand", limit=2)
        assert [item.symbol.qualified_name for item in partial_matches] == [
            "pkg.one.handle",
            "pkg.two.handle",
        ]

        qualified_matches = store.search_symbols(index_id, "pkg.two.handle", limit=1)
        assert qualified_matches[0].symbol.qualified_name == "pkg.two.handle"
        suffix_matches = store.search_symbols(index_id, "two.handle", limit=1)
        assert suffix_matches[0].symbol.qualified_name == "pkg.two.handle"

        body_matches = store.search_symbols(index_id, "needle", limit=4)
        body_names = {item.symbol.qualified_name for item in body_matches}
        assert "pkg.one" in body_names
        assert "pkg.one.handle" in body_names
        assert "pkg.one.sibling" not in body_names
        assert "pkg.two.handle" not in body_names
        assert body_matches == store.search_symbols(index_id, "needle", limit=4)


def test_search_bounds_candidate_batches_and_reads_each_source_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "large-search"
    repository.mkdir()
    functions = []
    for index in range(180):
        body = "rare_body" if index == 179 else "common_body"
        functions.append(
            f"def function_{index:03d}() -> str:\n"
            f"    return {body!r}\n\n"
        )
    (repository / "module.py").write_text("".join(functions), encoding="utf-8")
    relation = relation_for_repository(repository, repository_id="large-search")
    index_id = relation.snapshot.index.index_id
    monkeypatch.setattr(storage_module, "_SEARCH_MAX_CANDIDATES", 64)

    statements: list[str] = []
    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        store.connection.set_trace_callback(statements.append)
        result = store.search_symbols_bounded(index_id, "rare_body", limit=3)
        store.connection.set_trace_callback(None)

        assert result.matches
        assert result.truncated is True
        assert result.truncation_reason == "search budget exceeded: max_candidates"
        source_reads = [
            statement
            for statement in statements
            if "FROM SNAPSHOT_FILES" in statement.upper() and "SOURCE_TEXT" in statement.upper()
        ]
        assert len(source_reads) == 1
        symbol_reads = [statement for statement in statements if "FROM SYMBOLS" in statement.upper()]
        fts_reads = [statement for statement in statements if "FROM SYMBOL_FTS" in statement.upper()]
        assert symbol_reads and all("LIMIT" in statement.upper() for statement in symbol_reads)
        assert fts_reads and all("LIMIT" in statement.upper() for statement in fts_reads)

        exact_target = store.search_symbols_bounded(index_id, "function_179", limit=1)
        assert exact_target.matches[0].symbol.qualified_name == "module.function_179"


def test_search_reports_source_budget_without_loading_an_oversized_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source-budget"
    repository.mkdir()
    source = "def target() -> str:\n    return 'source_budget_token'\n"
    (repository / "module.py").write_text(source, encoding="utf-8")
    relation = relation_for_repository(repository, repository_id="source-budget")
    index_id = relation.snapshot.index.index_id
    monkeypatch.setattr(storage_module, "_SEARCH_MAX_SOURCE_BYTES", 8)

    statements: list[str] = []
    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        store.connection.set_trace_callback(statements.append)
        result = store.search_symbols_bounded(index_id, "source_budget_token", limit=3)
        store.connection.set_trace_callback(None)

    assert result.matches == ()
    assert result.truncated is True
    assert result.truncation_reason == "search budget exceeded: max_source_bytes"
    source_reads = [
        statement
        for statement in statements
        if "FROM SNAPSHOT_FILES" in statement.upper() and "SOURCE_TEXT" in statement.upper()
    ]
    assert source_reads == []


def test_search_sql_progress_handler_interrupts_and_connection_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StepClock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0.0 if self.calls <= 2 else 1.0

    relation = fixture_relation(index_id="search-time-budget")
    index_id = relation.snapshot.index.index_id
    monkeypatch.setattr(storage_module, "_SEARCH_PROGRESS_OPS", 1)

    with IndexStore(tmp_path / "index.sqlite3") as store:
        store.write_index(relation)
        budget = storage_module._SearchBudget(clock=StepClock())
        rows = store._search_query_rows(
            budget,
            "SELECT symbol_id FROM symbols WHERE index_id = ? ORDER BY symbol_id LIMIT ?",
            (index_id, 64),
        )

        assert rows is None
        assert budget.reason == "search budget exceeded: max_seconds"
        assert store.connection.execute("SELECT 1").fetchone()[0] == 1


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


def test_storage_rejects_http_facts_outside_their_snapshot_range(tmp_path: Path) -> None:
    repository = tmp_path / "http-fact-repository"
    repository.mkdir()
    (repository / "client.ts").write_text(
        'const apiBaseUrl = "https://remote.example";\n'
        "function requestJson(path: string, validator: unknown, init: RequestInit = {}) { return fetch(`${apiBaseUrl}${path}`, { ...init, headers: {}, cache: 'no-store' }); }\n"
        'function health() { return requestJson("/api/health", undefined); }\n',
        encoding="utf-8",
    )
    relation = extract_relations(extract_definitions(build_source_snapshot(repository, repository_id="http", revision="test")))
    assert len(relation.http_client_requests) == 1
    invalid_fact = replace(relation.http_client_requests[0], start_line=0)
    invalid_relation = replace(relation, http_client_requests=(invalid_fact,))
    with IndexStore(tmp_path / "index.sqlite3") as store:
        with pytest.raises(StorageInputError, match="HTTP client request fact"):
            store.write_index(invalid_relation)


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
