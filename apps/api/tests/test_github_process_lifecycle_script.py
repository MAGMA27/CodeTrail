"""Bounded-failure regression tests for the isolated GitHub lifecycle script."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "verify-github-process-lifecycle.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("codetrail_github_lifecycle_script", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_wait_is_bounded_and_reports_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script()
    release = tmp_path / "release"
    gate = tmp_path / "gate"

    with pytest.raises(TimeoutError, match=r"stage=missing-release"):
        script._wait_for_release(
            release,
            None,
            gate,
            timeout=0.05,
            stage="missing-release",
        )

    assert gate.is_file()
    assert "stage=missing-release" in capsys.readouterr().out


def test_scenario_timeout_reports_last_stage() -> None:
    script = _load_script()

    async def hanging_scenario(budget) -> dict[str, object]:
        budget.set_stage("missing-release")
        await asyncio.sleep(1)
        return {}

    with pytest.raises(script._ScenarioTimeout, match=r"scenario=timeout stage=missing-release"):
        asyncio.run(
            script._run_scenario(
                "timeout",
                hanging_scenario,
                scenario_timeout=0.05,
                release_timeout=0.05,
            )
        )


def test_parent_defaults_to_one_bounded_offline_scenario() -> None:
    script = _load_script()

    args = script._parser().parse_args([])

    assert args.scenario == "http-a-mcp-b"
    assert args.scenario_timeout == script.DEFAULT_SCENARIO_TIMEOUT_SECONDS
    assert args.release_timeout == script.DEFAULT_RELEASE_TIMEOUT_SECONDS


def test_missing_release_recycles_worker_and_retains_diagnostic_log(tmp_path: Path) -> None:
    script = _load_script()
    data_root = tmp_path / "data"
    log_dir = data_root / "logs"
    gate = data_root / "download-started"
    release = data_root / "release-never-created"
    process = None
    try:
        process, base_url, log_path = script._start_http_worker(
            data_root,
            [script.FIRST_COMMIT],
            log_dir=log_dir,
            label="missing-release",
            pause_download_call=1,
            gate=gate,
            release=release,
            release_timeout=0.2,
            startup_timeout=5,
        )
        status, body = script._http_json(
            f"{base_url}/api/imports/github",
            "POST",
            {"name": "Missing Release", "url": script.GITHUB_URL, "ref": "main"},
            timeout=3,
        )
        assert status == 200
        assert body["job_id"]
        status, job = script._wait_for_job(base_url, body["job_id"], timeout=5)
        assert status == 200
        assert job["status"] == "failed"
        assert job["result"]["attempt"]["status"] == "failed"
        assert gate.is_file()
        assert "stage=github.download.release" in script._read_log_tail(log_path)
    finally:
        script._cleanup_http_process(process, timeout=1, label="missing-release")

    assert process is not None
    assert process.poll() is not None
