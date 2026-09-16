"""Bounded local source freshness and explicit index refresh tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import app.service as service_module
from app.contracts import (
    CreateProjectIndexRequest,
    CreateProjectRequest,
    RefreshProjectIndexRequest,
)
from app.indexing import (
    SnapshotIssue,
    SourceManifestScan,
    current_source_rules_version,
    index_configuration_hash,
)
from app.main import create_app
from app.relations import INDEX_BUILD_IDENTITY_VERSION, RELATION_SEMANTICS_VERSION
from app.service import IndexService, ServiceError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


def _copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def _workspace(tmp_path: Path):
    repository = _copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    workspace = application.state.workspace_service
    project = workspace.create_project(
        CreateProjectRequest(name="Freshness fixture", repository_id="timeout-demo")
    )
    index = workspace.create_project_index(
        project.project_id,
        CreateProjectIndexRequest(revision="freshness-v1", dirty=False),
    )
    return application, repository, project, index


def test_freshness_detects_same_size_content_after_mtime_restore(tmp_path: Path) -> None:
    application, repository, project, index = _workspace(tmp_path)
    source = repository / "timeout_demo" / "config.py"
    original = source.read_bytes()
    original_stat = source.stat()
    changed = original.replace(b"DEFAULT_TIMEOUT = 30", b"DEFAULT_TIMEOUT = 99")
    assert len(changed) == len(original)
    source.write_bytes(changed)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )

    assert freshness.status == "changed"
    assert freshness.modified_count == 1
    assert freshness.added_count == 0
    assert freshness.deleted_count == 0
    assert freshness.changed_paths == ["timeout_demo/config.py"]
    assert freshness.scan_complete is True


def test_excluded_files_are_not_changes_and_added_deleted_files_are_reported(tmp_path: Path) -> None:
    application, repository, project, index = _workspace(tmp_path)
    ignored = repository / "node_modules" / "ignored.py"
    ignored.parent.mkdir()
    ignored.write_text("ignored = True\n", encoding="utf-8")
    deleted = repository / "timeout_demo" / "other_handlers.py"
    deleted.unlink()
    added = repository / "timeout_demo" / "new_module.py"
    added.write_text("VALUE = 1\n", encoding="utf-8")

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )

    assert freshness.status == "changed"
    assert freshness.added_count == 1
    assert freshness.deleted_count == 1
    assert freshness.modified_count == 0
    assert freshness.changed_paths == ["timeout_demo/new_module.py", "timeout_demo/other_handlers.py"]
    assert "node_modules/ignored.py" not in freshness.changed_paths


def test_unchanged_refresh_skips_definition_and_relation_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _repository, project, index = _workspace(tmp_path)

    def fail(*_args, **_kwargs):
        raise AssertionError("unchanged refresh must not parse or extract relations")

    monkeypatch.setattr(service_module, "extract_definitions", fail)
    monkeypatch.setattr(service_module, "extract_relations", fail)

    refreshed = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-v2", dirty=True),
    )

    assert refreshed.mode == "reused"
    assert refreshed.index.index.index_id == index.index.index_id
    assert refreshed.freshness.status == "consistent"


def test_changed_refresh_creates_new_snapshot_and_keeps_old_index(tmp_path: Path) -> None:
    application, repository, project, index = _workspace(tmp_path)
    source = repository / "timeout_demo" / "config.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        encoding="utf-8",
    )

    refreshed = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-v2", dirty=True),
    )

    assert refreshed.mode == "created"
    assert refreshed.freshness.status == "changed"
    assert refreshed.index.index.index_id != index.index.index_id
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == refreshed.index.index.index_id
    assert index.index.index_id in current.available_index_ids


def test_refresh_reuses_compatible_historical_index_after_source_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repository, project, index_a = _workspace(tmp_path)
    source = repository / "timeout_demo" / "config.py"
    original = source.read_bytes()
    source.write_bytes(original.replace(b"DEFAULT_TIMEOUT = 30", b"DEFAULT_TIMEOUT = 99"))

    index_b = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index_a.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-b", dirty=True),
    )
    assert index_b.mode == "created"
    assert index_b.index.index.index_id != index_a.index.index_id

    source.write_bytes(original)

    def fail(*_args, **_kwargs):
        raise AssertionError("historical refresh must not parse or extract relations")

    monkeypatch.setattr(service_module, "extract_definitions", fail)
    monkeypatch.setattr(service_module, "extract_relations", fail)
    restored = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index_b.index.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-a-restored", dirty=True),
    )

    assert restored.mode == "historical_reused"
    assert restored.freshness.status == "changed"
    assert restored.index.index.index_id == index_a.index.index_id
    assert restored.index.index.revision == "freshness-v1"
    assert restored.index.index.dirty is False
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == index_a.index.index_id
    assert set(current.available_index_ids) == {
        index_a.index.index_id,
        index_b.index.index.index_id,
    }

    repeated = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index_a.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-repeat", dirty=True),
    )
    assert repeated.mode == "reused"
    assert repeated.index.index.index_id == index_a.index.index_id

    reopened = create_app({"timeout-demo": repository})
    reopened_project = reopened.state.workspace_service.get_project(project.project_id)
    assert reopened_project.latest_index_id == index_a.index.index_id
    after_restart = reopened.state.workspace_service.refresh_project_index(
        project.project_id,
        index_a.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-after-restart", dirty=True),
    )
    assert after_restart.mode == "reused"
    assert after_restart.index.index.index_id == index_a.index.index_id


def test_refresh_does_not_reuse_history_when_configuration_changes(tmp_path: Path) -> None:
    application, repository, project, index_a = _workspace(tmp_path)
    source = repository / "timeout_demo" / "config.py"
    original = source.read_bytes()
    source.write_bytes(original.replace(b"DEFAULT_TIMEOUT = 30", b"DEFAULT_TIMEOUT = 99"))
    index_b = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index_a.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-b", dirty=True),
    )
    assert index_b.mode == "created"
    source.write_bytes(original)

    changed_index_service = IndexService(application.state.registry, max_file_bytes=500_000)
    application.state.workspace_service.index_service = changed_index_service
    restored = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index_b.index.index.index_id,
        RefreshProjectIndexRequest(revision="freshness-new-config", dirty=True),
    )

    assert restored.mode == "created"
    assert restored.freshness.status == "changed"
    assert "configuration_changed" in restored.freshness.reasons
    assert restored.index.index.index_id not in {
        index_a.index.index_id,
        index_b.index.index.index_id,
    }


def test_relation_semantics_upgrade_rebuilds_same_source_with_new_identity(tmp_path: Path) -> None:
    application, _repository, project, index = _workspace(tmp_path)
    index_service = application.state.index_service
    old_rules_version = current_source_rules_version("relations-v2")
    old_configuration_hash = index_configuration_hash(
        max_file_bytes=index_service.max_file_bytes,
        identity_version="relations-v3",
        rules_version=old_rules_version,
    )
    with application.state.registry.store_for_repository("timeout-demo") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = ?, configuration_hash = ? WHERE index_id = ?",
            (old_rules_version, old_configuration_hash, index.index.index_id),
        )

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "changed"
    assert freshness.reasons == ["rules_changed", "configuration_changed"]
    assert freshness.current_rules_version.endswith(f"+{RELATION_SEMANTICS_VERSION}")
    assert INDEX_BUILD_IDENTITY_VERSION == "relations-v4"

    refreshed = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index.index.index_id,
        RefreshProjectIndexRequest(revision="semantic-upgrade", dirty=True),
    )
    assert refreshed.mode == "created"
    assert refreshed.index.index.index_id != index.index.index_id
    assert "relations-v4-rebuild" in refreshed.index.index.index_id
    assert refreshed.index.index.rules_version == freshness.current_rules_version
    assert refreshed.index.index.configuration_hash == index_service.configuration_hash

    upgraded_freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        refreshed.index.index.index_id,
    )
    assert upgraded_freshness.status == "consistent"
    repeated = application.state.workspace_service.refresh_project_index(
        project.project_id,
        refreshed.index.index.index_id,
        RefreshProjectIndexRequest(revision="semantic-upgrade-repeat", dirty=True),
    )
    assert repeated.mode == "reused"
    assert repeated.index.index.index_id == refreshed.index.index.index_id
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == refreshed.index.index.index_id
    assert index.index.index_id in current.available_index_ids


def test_budget_exhaustion_is_unknown_and_keeps_latest_on_refresh(tmp_path: Path) -> None:
    application, _repository, project, index = _workspace(tmp_path)
    application.state.index_service.freshness_max_files = 1

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "unknown"
    assert freshness.scan_complete is False
    assert freshness.scan_truncated is True
    assert "budget_exceeded" in freshness.reasons

    with pytest.raises(ServiceError, match="bounded scan budget"):
        application.state.workspace_service.refresh_project_index(
            project.project_id,
            index.index.index_id,
            RefreshProjectIndexRequest(),
        )
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == index.index.index_id


def test_legacy_metadata_requires_rebuild(tmp_path: Path) -> None:
    application, _repository, project, index = _workspace(tmp_path)
    with application.state.registry.store_for_repository("timeout-demo") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = NULL, configuration_hash = NULL WHERE index_id = ?",
            (index.index.index_id,),
        )

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "unknown"
    assert freshness.reasons == ["missing_index_metadata"]

    refreshed = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index.index.index_id,
        RefreshProjectIndexRequest(),
    )
    assert refreshed.mode == "created"
    assert refreshed.index.index.index_id != index.index.index_id

    repaired_freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        refreshed.index.index.index_id,
    )
    assert repaired_freshness.status == "consistent"
    reused = application.state.workspace_service.refresh_project_index(
        project.project_id,
        refreshed.index.index.index_id,
        RefreshProjectIndexRequest(),
    )
    assert reused.mode == "reused"
    assert reused.index.index.index_id == refreshed.index.index.index_id


def test_legacy_metadata_and_source_changes_can_rebuild(tmp_path: Path) -> None:
    application, repository, project, index = _workspace(tmp_path)
    source = repository / "timeout_demo" / "config.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace("DEFAULT_TIMEOUT = 30", "DEFAULT_TIMEOUT = 99"),
        encoding="utf-8",
    )
    with application.state.registry.store_for_repository("timeout-demo") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = NULL, configuration_hash = NULL WHERE index_id = ?",
            (index.index.index_id,),
        )

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "unknown"
    assert freshness.scan_complete is True
    assert freshness.scan_truncated is False
    assert freshness.reasons == ["source_changed", "missing_index_metadata"]

    refreshed = application.state.workspace_service.refresh_project_index(
        project.project_id,
        index.index.index_id,
        RefreshProjectIndexRequest(revision="legacy-repair-v1", dirty=True),
    )
    assert refreshed.mode == "created"
    assert refreshed.freshness.status == "unknown"
    assert refreshed.index.index.index_id != index.index.index_id

    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == refreshed.index.index.index_id
    assert index.index.index_id in current.available_index_ids

    repaired_freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        refreshed.index.index.index_id,
    )
    assert repaired_freshness.status == "consistent"
    reused = application.state.workspace_service.refresh_project_index(
        project.project_id,
        refreshed.index.index.index_id,
        RefreshProjectIndexRequest(),
    )
    assert reused.mode == "reused"
    assert reused.index.index.index_id == refreshed.index.index.index_id


def test_legacy_metadata_with_budget_exhaustion_keeps_latest(tmp_path: Path) -> None:
    application, _repository, project, index = _workspace(tmp_path)
    with application.state.registry.store_for_repository("timeout-demo") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = NULL, configuration_hash = NULL WHERE index_id = ?",
            (index.index.index_id,),
        )
    application.state.index_service.freshness_max_files = 1

    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "unknown"
    assert "missing_index_metadata" in freshness.reasons
    assert "budget_exceeded" in freshness.reasons

    with pytest.raises(ServiceError, match="bounded scan budget"):
        application.state.workspace_service.refresh_project_index(
            project.project_id,
            index.index.index_id,
            RefreshProjectIndexRequest(),
        )
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == index.index.index_id


@pytest.mark.parametrize(
    ("issue_kind", "expected_reason", "scan_complete"),
    [("read_error", "source_read_error", False), ("outside_root", "outside_root", True)],
)
def test_legacy_metadata_with_unsafe_scan_reason_keeps_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    issue_kind: str,
    expected_reason: str,
    scan_complete: bool,
) -> None:
    application, _repository, project, index = _workspace(tmp_path)
    with application.state.registry.store_for_repository("timeout-demo") as store:
        store.connection.execute(
            "UPDATE index_snapshots SET rules_version = NULL, configuration_hash = NULL WHERE index_id = ?",
            (index.index.index_id,),
        )

    def fake_scan(*_args, **_kwargs) -> SourceManifestScan:
        return SourceManifestScan(
            files=(),
            issues=(SnapshotIssue("timeout_demo/blocked.py", issue_kind, "blocked"),),  # type: ignore[arg-type]
            scanned_files=0,
            scanned_bytes=0,
            scan_complete=scan_complete,
            scan_truncated=False,
            scan_truncation_reason=None,
        )

    monkeypatch.setattr(service_module, "scan_source_manifest", fake_scan)
    freshness = application.state.workspace_service.check_index_freshness(
        project.project_id,
        index.index.index_id,
    )
    assert freshness.status == "unknown"
    assert expected_reason in freshness.reasons

    with pytest.raises(ServiceError, match="freshness could not be established"):
        application.state.workspace_service.refresh_project_index(
            project.project_id,
            index.index.index_id,
            RefreshProjectIndexRequest(),
        )
    current = application.state.workspace_service.get_project(project.project_id)
    assert current.latest_index_id == index.index.index_id
