"""Offline tests for read-only snapshots and Python definition extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pytest

from app.indexing import (
    SnapshotBoundaryError,
    build_source_snapshot,
    extract_definitions,
    scan_source_manifest,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"
MANIFEST_PATH = ROOT / "fixtures" / "scenarios" / "source-manifest.json"


def test_timeout_demo_snapshot_is_reproducible_and_contract_aligned() -> None:
    first = build_source_snapshot(
        FIXTURE_ROOT,
        repository_id="timeout-demo",
        revision="fixture-v1",
        dirty=False,
    )
    second = build_source_snapshot(
        FIXTURE_ROOT,
        repository_id="timeout-demo",
        revision="fixture-v1",
        dirty=False,
    )

    assert first.index.model_dump() == second.index.model_dump()
    assert [(item.path, item.size, item.content_hash) for item in first.files] == [
        (item.path, item.size, item.content_hash) for item in second.files
    ]
    assert first.index.status == "ready"
    assert first.index.snapshot_hash
    assert len(first.files) == 8
    assert not first.issues

    expected_hashes = {
        entry["path"]: entry["sha256"]
        for entry in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"]
    }
    assert {item.path: item.content_hash for item in first.files} == expected_hashes


def test_snapshot_hash_and_index_id_change_with_source_content(tmp_path: Path) -> None:
    source_path = tmp_path / "module.py"
    source_path.write_text("value = 1\n", encoding="utf-8")

    first = build_source_snapshot(tmp_path, repository_id="mutable", revision="working-tree", dirty=True)
    source_path.write_text("value = 2\n", encoding="utf-8")
    second = build_source_snapshot(tmp_path, repository_id="mutable", revision="working-tree", dirty=True)

    assert first.index.snapshot_hash != second.index.snapshot_hash
    assert first.index.index_id != second.index.index_id


def test_relation_rule_identity_separates_same_source_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def first() -> str:\n    return 'same'\n", encoding="utf-8")

    source_identity = build_source_snapshot(
        tmp_path,
        repository_id="versioned",
        revision="fixture-v1",
        dirty=False,
    )
    relation_identity = build_source_snapshot(
        tmp_path,
        repository_id="versioned",
        revision="fixture-v1",
        dirty=False,
        identity_version="relations-v2",
    )

    assert relation_identity.index.snapshot_hash == source_identity.index.snapshot_hash
    assert relation_identity.index.index_id != source_identity.index.index_id


def test_definition_extraction_locates_same_named_handlers_and_config() -> None:
    result = extract_definitions(
        build_source_snapshot(
            FIXTURE_ROOT,
            repository_id="timeout-demo",
            revision="fixture-v1",
            dirty=False,
        )
    )

    assert not result.issues
    symbols = {(symbol.path, symbol.qualified_name): symbol for symbol in result.symbols}
    primary = symbols[("timeout_demo/handlers.py", "timeout_demo.handlers.handle")]
    secondary = symbols[("timeout_demo/other_handlers.py", "timeout_demo.other_handlers.handle")]
    load_settings = symbols[("timeout_demo/config.py", "timeout_demo.config.load_settings")]
    client = symbols[("timeout_demo/client.py", "timeout_demo.client.Client")]

    assert primary.symbol_id != secondary.symbol_id
    assert primary.start_line == 8
    assert secondary.start_line == 8
    assert load_settings.start_line == 12
    assert client.start_line == 8
    assert {symbol.kind for symbol in result.symbols} == {"module", "class", "function"}


def test_symbol_ids_repeat_when_snapshot_is_rebuilt() -> None:
    first = extract_definitions(
        build_source_snapshot(FIXTURE_ROOT, repository_id="timeout-demo", revision="fixture-v1", dirty=False)
    )
    second = extract_definitions(
        build_source_snapshot(FIXTURE_ROOT, repository_id="timeout-demo", revision="fixture-v1", dirty=False)
    )

    assert [symbol.model_dump() for symbol in first.symbols] == [
        symbol.model_dump() for symbol in second.symbols
    ]


def test_snapshot_reads_only_from_its_captured_content() -> None:
    snapshot = build_source_snapshot(
        FIXTURE_ROOT,
        repository_id="timeout-demo",
        revision="fixture-v1",
        dirty=False,
    )

    excerpt = snapshot.read_source("timeout_demo\\config.py", 12, 15)

    assert excerpt.path == "timeout_demo/config.py"
    assert excerpt.excerpt.startswith("def load_settings")
    assert excerpt.content_hash == next(
        item.content_hash for item in snapshot.files if item.path == "timeout_demo/config.py"
    )

    with pytest.raises(SnapshotBoundaryError):
        snapshot.read_source("../outside.py", 1, 1)
    with pytest.raises(SnapshotBoundaryError):
        snapshot.read_source("timeout_demo/config.py", 1, 201)


def test_snapshot_reports_oversized_undecodable_and_syntax_error_files(tmp_path: Path) -> None:
    (tmp_path / "large.py").write_text("x = 1\n" * 4, encoding="utf-8")
    (tmp_path / "bad_encoding.py").write_bytes(b"x = '\xff'\n")
    (tmp_path / "syntax_error.py").write_text("def x(:\n pass\n", encoding="utf-8")

    snapshot = build_source_snapshot(
        tmp_path,
        repository_id="errors",
        revision="fixture-v1",
        dirty=True,
        max_file_bytes=20,
    )
    result = extract_definitions(snapshot)

    assert {issue.kind for issue in result.issues} == {
        "file_too_large",
        "decode_error",
        "syntax_error",
    }
    assert snapshot.index.status == "failed"
    assert not any(item.path == "bad_encoding.py" for item in snapshot.files)


def test_snapshot_excludes_dependency_and_secret_paths(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "evals" / ".runtime").mkdir(parents=True)
    (tmp_path / "evals" / ".runtime" / "generated.py").write_text("value = 3\n", encoding="utf-8")
    (tmp_path / "evals" / ".codetrail-runtime").mkdir()
    (tmp_path / "evals" / ".codetrail-runtime" / "generated.py").write_text("value = 4\n", encoding="utf-8")
    (tmp_path / "secret.py").write_text("token = 'do-not-read'\n", encoding="utf-8")

    snapshot = build_source_snapshot(tmp_path, repository_id="excluded", revision="fixture-v1", dirty=True)

    assert [item.path for item in snapshot.files] == ["main.py"]
    assert not snapshot.issues
    manifest = scan_source_manifest(tmp_path)
    assert [item.path for item in manifest.files] == ["main.py"]
    assert manifest.scan_complete


def test_manifest_catches_growth_past_total_byte_budget_during_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "main.ts"
    source.write_text("x", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def read_grown_file(path: Path) -> bytes:
        if path == source:
            return b"12345"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_grown_file)
    manifest = scan_source_manifest(tmp_path, max_file_bytes=10, max_files=10, max_total_bytes=4)
    assert manifest.scan_truncated is True
    assert manifest.scan_truncation_reason == "max_total_bytes_after_read"
    assert manifest.scan_complete is False
    assert manifest.files == ()
