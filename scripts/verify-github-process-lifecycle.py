"""Verify GitHub update lifecycle across real HTTP and MCP processes.

The parent process drives real HTTP requests and an MCP stdio client. Worker
processes use a deterministic GitHub client unless ``--real-github`` is set.
The public probe is opt-in; the default only prepares the bounded offline
scenarios. The script never uses signal-based PID probing.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
SCRIPT_PATH = Path(__file__).resolve()
GITHUB_URL = "https://github.com/owner/repo"
FIRST_COMMIT = "a" * 40
SECOND_COMMIT = "b" * 40
DEFAULT_SCENARIO_TIMEOUT_SECONDS = 90.0
DEFAULT_RELEASE_TIMEOUT_SECONDS = 30.0
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 3.0
HTTP_REQUEST_TIMEOUT_SECONDS = 5.0
HTTP_STARTUP_TIMEOUT_SECONDS = 15.0
MCP_READ_TIMEOUT_SECONDS = 10.0


def _log(message: str) -> None:
    print(message, flush=True)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ready\n", encoding="utf-8")


class _ScenarioTimeout(RuntimeError):
    """A bounded scenario stopped before it could complete its current stage."""


@dataclass
class _ScenarioBudget:
    name: str
    timeout: float
    release_timeout: float
    started_at: float = field(default_factory=time.monotonic)
    stage: str = "starting"

    @property
    def deadline(self) -> float:
        return self.started_at + self.timeout

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        _log(f"[offline] {self.name}: {stage}")

    def remaining(self, stage: str | None = None) -> float:
        if stage is not None:
            self.set_stage(stage)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _ScenarioTimeout(
                f"scenario={self.name} stage={self.stage} exceeded "
                f"the {self.timeout:.1f}s total timeout"
            )
        return remaining

    def request_timeout(self, stage: str) -> float:
        return min(HTTP_REQUEST_TIMEOUT_SECONDS, self.remaining(stage))

    @property
    def cleanup_timeout(self) -> float:
        return DEFAULT_CLEANUP_TIMEOUT_SECONDS


def _wait_for_release(
    release: Path,
    crash: Path | None,
    gate: Path,
    *,
    timeout: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    stage: str = "release",
) -> None:
    if timeout <= 0:
        raise ValueError("release timeout must be positive")
    _touch(gate)
    _log(f"[worker] stage={stage} waiting for release timeout={timeout:.1f}s")
    deadline = time.monotonic() + timeout
    while not release.exists():
        if crash is not None and crash.exists():
            _log(f"[worker] stage={stage} crash marker received")
            os._exit(91)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log(f"[worker] stage={stage} release wait timed out")
            raise TimeoutError(
                f"stage={stage} release gate {release} was not created "
                f"within {timeout:.1f}s"
            )
        time.sleep(min(0.02, remaining))


class ProcessGitHubClient:
    """Deterministic bounded GitHub client used only by worker processes."""

    def __init__(
        self,
        commits: list[str],
        *,
        pause_download_call: int | None = None,
        gate: Path | None = None,
        release: Path | None = None,
        crash: Path | None = None,
        release_timeout: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    ) -> None:
        self._commits = iter(commits)
        self._pause_download_call = pause_download_call
        self._gate = gate
        self._release = release
        self._crash = crash
        self._release_timeout = release_timeout
        self._download_calls = 0

    def new_budget(self):
        from app.github import GitHubRequestBudget

        # This deterministic process harness tests lifecycle interleavings, not
        # remote acquisition timing; controlled release gates have their own budget.
        return GitHubRequestBudget(float("inf"))

    def resolve(self, url: str, requested_ref: str | None, *, budget=None):
        from app.github import GitHubResolution, parse_github_repository_url

        repository = parse_github_repository_url(url)
        try:
            commit = next(self._commits)
        except StopIteration as exc:
            raise RuntimeError("deterministic GitHub client ran out of commits") from exc
        return GitHubResolution(repository, requested_ref, commit, requested_ref or "main")

    def download_and_extract(self, resolution, destination: Path, *, budget=None, control=None):
        from app.github import GitHubArchive

        if control is not None:
            control.check()
        self._download_calls += 1
        destination.mkdir(parents=True, exist_ok=False)
        payload = (
            f"COMMIT = {resolution.resolved_commit!r}\n\n"
            "def run() -> str:\n"
            "    return COMMIT\n"
        ).encode("utf-8")
        (destination / "module.py").write_bytes(payload)
        if self._pause_download_call == self._download_calls:
            if self._gate is None or self._release is None:
                raise RuntimeError("download pause requires gate and release paths")
            _wait_for_release(
                self._release,
                self._crash,
                self._gate,
                timeout=self._release_timeout,
                stage="github.download.release",
            )
        if control is not None:
            control.check()
        return GitHubArchive(
            resolution,
            hashlib.sha256(payload).hexdigest(),
            1,
            len(payload),
        )


def _install_commit_pause(application, args: argparse.Namespace) -> None:
    if args.block_commit_call is None:
        return
    store = application.state.registry.management_store
    original = store.commit_project_update
    calls = 0

    def commit_project_update(
        operation_id: str,
        *,
        candidate_index_id: str,
        content_hash: str | None,
    ) -> None:
        nonlocal calls
        calls += 1
        _log(f"[worker] stage=registry.commit call={calls}")
        if calls == args.block_commit_call:
            if args.gate is None or args.release is None:
                _log("[worker] commit pause is missing gate or release path")
                raise RuntimeError("commit pause requires gate and release paths")
            _wait_for_release(
                Path(args.release),
                Path(args.crash) if args.crash else None,
                Path(args.gate),
                timeout=args.release_timeout,
                stage="registry.commit.release",
            )
        original(
            operation_id,
            candidate_index_id=candidate_index_id,
            content_hash=content_hash,
        )

    store.commit_project_update = commit_project_update


def _run_worker(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(API_ROOT))
    from app.main import create_app

    commits = json.loads(args.commits)
    if not isinstance(commits, list) or not all(isinstance(commit, str) for commit in commits):
        raise SystemExit("--commits must be a JSON array of strings")
    client = (
        __import__("app.github", fromlist=["GitHubClient"]).GitHubClient()
        if args.real_github
        else ProcessGitHubClient(
            commits,
            pause_download_call=args.pause_download_call,
            gate=Path(args.gate) if args.gate else None,
            release=Path(args.release) if args.release else None,
            crash=Path(args.crash) if args.crash else None,
            release_timeout=args.release_timeout,
        )
    )
    application = create_app(
        {},
        data_root=Path(args.data_root),
        github_import_enabled=True,
        mcp_github_import_enabled=args.worker == "mcp",
        mcp_index_write_enabled=args.worker == "mcp",
        github_client=client,
    )
    _install_commit_pause(application, args)
    if args.worker == "http":
        import uvicorn

        uvicorn.run(
            application,
            host="127.0.0.1",
            port=args.port,
            log_level="critical",
            access_log=False,
        )
        return
    from app.mcp_server import create_mcp_server

    create_mcp_server(application.state.workspace_service).run("stdio")


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(API_ROOT)
        if not existing_pythonpath
        else str(API_ROOT) + os.pathsep + existing_pythonpath
    )
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _stop_http_process(
    process: subprocess.Popen[bytes] | None,
    *,
    timeout: float = DEFAULT_CLEANUP_TIMEOUT_SECONDS,
) -> None:
    if process is None or process.poll() is not None:
        return
    if timeout <= 0:
        raise ValueError("process cleanup timeout must be positive")
    deadline = time.monotonic() + timeout
    process.terminate()
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("HTTP worker did not exit within the cleanup timeout")
        process.wait(timeout=remaining)


def _wait_for_process_exit(process: subprocess.Popen[bytes], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        raise RuntimeError("worker process did not exit within the bounded timeout")


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = HTTP_REQUEST_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    if timeout <= 0:
        raise ValueError("HTTP request timeout must be positive")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), json.loads(response.read() or b"{}")
    except HTTPError as exc:
        return int(exc.code), json.loads(exc.read() or b"{}")


def _wait_for_job(
    base_url: str,
    job_id: str,
    *,
    timeout: float = HTTP_REQUEST_TIMEOUT_SECONDS,
    poll_interval: float = 0.05,
) -> tuple[int, dict[str, Any]]:
    """Poll an asynchronous API job until it reaches a terminal state."""
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError("job wait timeout and poll interval must be positive")
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, latest = _http_json(
            f"{base_url}/api/jobs/{job_id}",
            timeout=min(HTTP_REQUEST_TIMEOUT_SECONDS, max(0.01, deadline - time.monotonic())),
        )
        if status != 200 or latest.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}:
            return status, latest
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
    raise TimeoutError(f"job {job_id} did not reach a terminal state: {latest}")


def _read_log_tail(path: Path, limit: int = 4_000) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _wait_for_http(
    process: subprocess.Popen[bytes],
    base_url: str,
    log_path: Path,
    timeout: float = HTTP_STARTUP_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"HTTP worker exited during startup with code {process.returncode}; "
                f"log={log_path}\n{_read_log_tail(log_path)}"
            )
        try:
            request_timeout = min(HTTP_REQUEST_TIMEOUT_SECONDS, deadline - time.monotonic())
            if request_timeout <= 0:
                break
            status, body = _http_json(
                f"{base_url}/api/health",
                timeout=request_timeout,
            )
            if status == 200 and body.get("status") == "ok":
                return
        except (OSError, URLError):
            pass
        time.sleep(0.05)
    raise RuntimeError(
        f"HTTP worker did not become healthy within the bounded timeout; "
        f"log={log_path}\n{_read_log_tail(log_path)}"
    )


def _start_http_worker(
    data_root: Path,
    commits: list[str],
    *,
    log_dir: Path,
    label: str,
    pause_download_call: int | None = None,
    block_commit_call: int | None = None,
    gate: Path | None = None,
    release: Path | None = None,
    crash: Path | None = None,
    release_timeout: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
    startup_timeout: float = HTTP_STARTUP_TIMEOUT_SECONDS,
    real_github: bool = False,
) -> tuple[subprocess.Popen[bytes], str, Path]:
    port = _free_port()
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--worker",
        "http",
        "--data-root",
        str(data_root),
        "--port",
        str(port),
        "--commits",
        json.dumps(commits),
    ]
    if pause_download_call is not None:
        command.extend(["--pause-download-call", str(pause_download_call)])
    if block_commit_call is not None:
        command.extend(["--block-commit-call", str(block_commit_call)])
    if gate is not None:
        command.extend(["--gate", str(gate)])
    if release is not None:
        command.extend(["--release", str(release)])
    if crash is not None:
        command.extend(["--crash", str(crash)])
    command.extend(["--release-timeout", str(release_timeout)])
    if real_github:
        command.append("--real-github")

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{label}.log"
    log_handle = log_path.open("wb")
    popen_kwargs: dict[str, Any] = {
        "cwd": API_ROOT,
        "env": _child_environment(),
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": log_handle,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    finally:
        log_handle.close()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_http(process, base_url, log_path, timeout=startup_timeout)
    except Exception:
        _stop_http_process(process)
        raise
    return process, base_url, log_path


def _mcp_parameters(
    data_root: Path,
    commits: list[str],
    *,
    pause_download_call: int | None = None,
    block_commit_call: int | None = None,
    gate: Path | None = None,
    release: Path | None = None,
    crash: Path | None = None,
    release_timeout: float = DEFAULT_RELEASE_TIMEOUT_SECONDS,
):
    from mcp.client.stdio import StdioServerParameters

    command = [
        str(SCRIPT_PATH),
        "--worker",
        "mcp",
        "--data-root",
        str(data_root),
        "--commits",
        json.dumps(commits),
    ]
    if pause_download_call is not None:
        command.extend(["--pause-download-call", str(pause_download_call)])
    if block_commit_call is not None:
        command.extend(["--block-commit-call", str(block_commit_call)])
    if gate is not None:
        command.extend(["--gate", str(gate)])
    if release is not None:
        command.extend(["--release", str(release)])
    if crash is not None:
        command.extend(["--crash", str(crash)])
    command.extend(["--release-timeout", str(release_timeout)])
    return StdioServerParameters(
        command=sys.executable,
        args=command,
        env=_child_environment(),
        cwd=API_ROOT,
    )


async def _wait_for_gate(
    path: Path,
    process: subprocess.Popen[bytes] | None = None,
    *,
    timeout: float = HTTP_STARTUP_TIMEOUT_SECONDS,
    stage: str = "gate",
) -> None:
    if timeout <= 0:
        raise ValueError("gate timeout must be positive")
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"stage={stage} worker exited before creating gate: {process.returncode}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"stage={stage} gate {path} was not created within {timeout:.1f}s"
            )
        await asyncio.sleep(min(0.05, remaining))


def _registry_state(data_root: Path, project_id: str) -> dict[str, Any]:
    registry_path = data_root / "registry.sqlite3"
    with sqlite3.connect(registry_path) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute(
            "SELECT * FROM managed_sources WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if source is None:
            raise RuntimeError(f"managed source for {project_id} is missing")
        updates = connection.execute(
            "SELECT * FROM project_updates WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        attempt = connection.execute(
            "SELECT * FROM import_attempts WHERE project_id = ? "
            "ORDER BY created_at DESC, attempt_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    index_path = Path(source["storage_path"]) / ".codetrail" / "index.sqlite3"
    with sqlite3.connect(index_path) as connection:
        connection.row_factory = sqlite3.Row
        project = connection.execute(
            "SELECT latest_index_id, building_index_id FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if project is None:
        raise RuntimeError(f"project row for {project_id} is missing")
    return {
        "latest_index_id": project["latest_index_id"],
        "building_index_id": project["building_index_id"],
        "source_url": source["source_url"],
        "requested_ref": source["requested_ref"],
        "resolved_ref": source["resolved_ref"],
        "resolved_commit": source["resolved_commit"],
        "content_hash": source["content_hash"],
        "updates": [dict(row) for row in updates],
        "attempt": None if attempt is None else dict(attempt),
    }


def _mcp_data(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if not isinstance(structured, dict):
        raise RuntimeError(f"MCP result did not contain structured content: {result!r}")
    return structured


async def _http_request(
    budget: _ScenarioBudget,
    url: str,
    *,
    stage: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    return await asyncio.to_thread(
        _http_json,
        url,
        method,
        payload,
        timeout=budget.request_timeout(stage),
    )


async def _await_bounded(
    budget: _ScenarioBudget,
    awaitable: Any,
    *,
    stage: str,
    timeout: float | None = None,
) -> Any:
    budget.set_stage(stage)
    wait_timeout = budget.remaining()
    if timeout is not None:
        wait_timeout = min(wait_timeout, timeout)
    try:
        return await asyncio.wait_for(awaitable, timeout=wait_timeout)
    except asyncio.TimeoutError as exc:
        raise _ScenarioTimeout(
            f"scenario={budget.name} stage={stage} exceeded its "
            f"{wait_timeout:.1f}s operation timeout"
        ) from exc


async def _cancel_task(task: asyncio.Task[Any] | None, timeout: float) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except BaseException:
        # A cancelled asyncio.to_thread task cannot stop its running thread;
        # _http_json has its own short socket timeout and the worker is closed
        # separately below.
        pass


def _cleanup_http_process(
    process: subprocess.Popen[bytes] | None,
    *,
    timeout: float,
    label: str,
) -> bool:
    if process is None or process.poll() is not None:
        return True
    try:
        _stop_http_process(process, timeout=timeout)
        return True
    except Exception as exc:
        _log(f"[offline] cleanup failed for {label}: {type(exc).__name__}: {exc}")
        return False


async def _scenario_http_active_mcp_conflict(budget: _ScenarioBudget) -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="codetrail-github-http-active-"))
    log_dir = data_root / "logs"
    gate = data_root / "a-download-started"
    release = data_root / "a-download-release"
    process_a: subprocess.Popen[bytes] | None = None
    refresh_task: asyncio.Task[tuple[int, dict[str, Any]]] | None = None
    success = False
    try:
        _log("[offline] HTTP A seeds and pauses during its update")
        budget.set_stage("start HTTP A")
        process_a, base_a, _ = _start_http_worker(
            data_root,
            [FIRST_COMMIT, SECOND_COMMIT, SECOND_COMMIT],
            log_dir=log_dir,
            label="http-a",
            pause_download_call=2,
            gate=gate,
            release=release,
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining()),
        )
        status, imported = await _http_request(
            budget,
            f"{base_a}/api/imports/github",
            stage="seed HTTP A",
            method="POST",
            payload={"name": "Process HTTP A", "url": GITHUB_URL, "ref": "main"},
        )
        if status != 200:
            raise RuntimeError(f"HTTP seed failed: {status} {imported}")
        project_id = imported["project"]["project_id"]
        baseline_index_id = imported["index"]["index"]["index_id"]
        refresh_task = asyncio.create_task(
            _http_request(
                budget,
                f"{base_a}/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh",
                stage="HTTP A refresh",
                method="POST",
            )
        )
        budget.set_stage("wait HTTP A download gate")
        await _wait_for_gate(
            gate,
            process_a,
            timeout=budget.remaining(),
            stage=budget.stage,
        )
        active = _registry_state(data_root, project_id)
        if len(active["updates"]) != 1 or active["attempt"]["status"] != "acquiring":
            raise RuntimeError(f"HTTP A active update was not preserved: {active}")

        from mcp import Client

        _log("[offline] MCP B starts while HTTP A owns the project")
        parameters = _mcp_parameters(
            data_root,
            [SECOND_COMMIT, SECOND_COMMIT],
            release_timeout=budget.release_timeout,
        )
        async with Client(parameters, read_timeout_seconds=MCP_READ_TIMEOUT_SECONDS) as client_b:
            tools = await _await_bounded(budget, client_b.list_tools(), stage="list MCP B tools")
            if not any(tool.name == "refresh_github_source" for tool in tools.tools):
                raise RuntimeError("MCP B did not expose refresh_github_source")
            conflict = _mcp_data(
                await _await_bounded(
                    budget,
                    client_b.call_tool(
                        "refresh_github_source",
                        {"project_id": project_id, "index_id": baseline_index_id},
                    ),
                    stage="MCP B conflict request",
                )
            )
            if conflict.get("ok") is not False or conflict.get("error", {}).get("code") != "conflict":
                raise RuntimeError(f"MCP B did not return project conflict: {conflict}")
            after_b_start = _registry_state(data_root, project_id)
            if len(after_b_start["updates"]) != 1 or after_b_start["attempt"]["status"] != "acquiring":
                raise RuntimeError(f"MCP B startup interrupted HTTP A: {after_b_start}")
            _touch(release)
            status, updated = await _await_bounded(
                budget,
                refresh_task,
                stage="finish HTTP A refresh",
                timeout=20,
            )
            refresh_task = None
            if status != 200 or updated.get("mode") != "created":
                raise RuntimeError(f"HTTP A update failed after MCP conflict: {status} {updated}")
            project = _mcp_data(
                await _await_bounded(
                    budget,
                    client_b.call_tool("get_project", {"project_id": project_id}),
                    stage="MCP B verify latest",
                )
            )
            updated_index_id = updated["index"]["index"]["index_id"]
            if project.get("project", {}).get("latest_index_id") != updated_index_id:
                raise RuntimeError(f"MCP B read disagreed with HTTP A: {project} vs {updated}")
        final = _registry_state(data_root, project_id)
        if final["latest_index_id"] != updated_index_id or final["resolved_commit"] != SECOND_COMMIT:
            raise RuntimeError(f"HTTP A/MCP B final state disagreed: {final}")
        success = True
        return {"scenario": "http_a_mcp_b_active_conflict", "state": final}
    finally:
        _touch(release)
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        _cleanup_http_process(process_a, timeout=budget.cleanup_timeout, label="HTTP A")
        if success:
            shutil.rmtree(data_root, ignore_errors=True)
        else:
            _log(f"[offline] retained failed scenario logs at {data_root}")


async def _scenario_mcp_active_http_conflict(budget: _ScenarioBudget) -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="codetrail-github-mcp-active-"))
    log_dir = data_root / "logs"
    gate = data_root / "a-download-started"
    release = data_root / "a-download-release"
    process_b: subprocess.Popen[bytes] | None = None
    refresh_task: asyncio.Task[Any] | None = None
    success = False
    try:
        from mcp import Client

        _log("[offline] MCP A owns the project while HTTP B starts")
        parameters_a = _mcp_parameters(
            data_root,
            [FIRST_COMMIT, SECOND_COMMIT, SECOND_COMMIT],
            pause_download_call=2,
            gate=gate,
            release=release,
            release_timeout=budget.release_timeout,
        )
        async with Client(parameters_a, read_timeout_seconds=MCP_READ_TIMEOUT_SECONDS) as client_a:
            imported = _mcp_data(
                await _await_bounded(
                    budget,
                    client_a.call_tool(
                        "import_github_repository",
                        {"name": "Process MCP A", "url": GITHUB_URL, "ref": "main"},
                    ),
                    stage="seed MCP A",
                )
            )
            if not imported.get("ok"):
                raise RuntimeError(f"MCP seed failed: {imported}")
            project_id = imported["project"]["project_id"]
            baseline_index_id = imported["index"]["index"]["index_id"]
            refresh_task = asyncio.create_task(
                client_a.call_tool(
                    "refresh_github_source",
                    {"project_id": project_id, "index_id": baseline_index_id},
                )
            )
            await _wait_for_gate(
                gate,
                timeout=budget.remaining("wait MCP A download gate"),
                stage=budget.stage,
            )
            active = _registry_state(data_root, project_id)
            if len(active["updates"]) != 1 or active["attempt"]["status"] != "acquiring":
                raise RuntimeError(f"MCP A active update was not preserved: {active}")

            process_b, base_b, _ = _start_http_worker(
                data_root,
                [SECOND_COMMIT, SECOND_COMMIT],
                log_dir=log_dir,
                label="http-b",
                release_timeout=budget.release_timeout,
                startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining("start HTTP B")),
            )
            status, conflict = await _http_request(
                budget,
                f"{base_b}/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh",
                stage="HTTP B conflict request",
                method="POST",
            )
            if status != 409 or conflict.get("code") != "conflict":
                raise RuntimeError(f"HTTP B did not return project conflict: {status} {conflict}")
            after_b_start = _registry_state(data_root, project_id)
            if len(after_b_start["updates"]) != 1 or after_b_start["attempt"]["status"] != "acquiring":
                raise RuntimeError(f"HTTP B startup interrupted MCP A: {after_b_start}")
            _touch(release)
            updated = _mcp_data(
                await _await_bounded(
                    budget,
                    refresh_task,
                    stage="finish MCP A refresh",
                    timeout=20,
                )
            )
            refresh_task = None
            if not updated.get("ok") or updated["result"].get("mode") != "created":
                raise RuntimeError(f"MCP A update failed after HTTP conflict: {updated}")
            updated_index_id = updated["result"]["index"]["index"]["index_id"]
            status, project = await _http_request(
                budget,
                f"{base_b}/api/projects/{project_id}",
                stage="HTTP B verify latest",
            )
            if status != 200 or project.get("latest_index_id") != updated_index_id:
                raise RuntimeError(f"HTTP B read disagreed with MCP A: {status} {project} vs {updated}")
        final = _registry_state(data_root, project_id)
        if final["latest_index_id"] != updated_index_id or final["resolved_commit"] != SECOND_COMMIT:
            raise RuntimeError(f"MCP A/HTTP B final state disagreed: {final}")
        success = True
        return {"scenario": "mcp_a_http_b_active_conflict", "state": final}
    finally:
        _touch(release)
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        _cleanup_http_process(process_b, timeout=budget.cleanup_timeout, label="HTTP B")
        if success:
            shutil.rmtree(data_root, ignore_errors=True)
        else:
            _log(f"[offline] retained failed scenario logs at {data_root}")


async def _scenario_exit_before_latest(budget: _ScenarioBudget) -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="codetrail-github-exit-before-latest-"))
    log_dir = data_root / "logs"
    gate = data_root / "a-download-started"
    release = data_root / "a-download-release"
    crash = data_root / "a-crash"
    process_a: subprocess.Popen[bytes] | None = None
    refresh_task: asyncio.Task[tuple[int, dict[str, Any]]] | None = None
    recovery: subprocess.Popen[bytes] | None = None
    second_recovery: subprocess.Popen[bytes] | None = None
    success = False
    try:
        _log("[offline] process exit before latest CAS keeps the old source")
        process_a, base_a, _ = _start_http_worker(
            data_root,
            [FIRST_COMMIT, SECOND_COMMIT, SECOND_COMMIT],
            log_dir=log_dir,
            label="before-latest-a",
            pause_download_call=2,
            gate=gate,
            release=release,
            crash=crash,
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining("start HTTP A")),
        )
        status, imported = await _http_request(
            budget,
            f"{base_a}/api/imports/github",
            stage="seed HTTP A",
            method="POST",
            payload={"name": "Exit Before Latest", "url": GITHUB_URL, "ref": "main"},
        )
        if status != 200:
            raise RuntimeError(f"HTTP seed failed: {status} {imported}")
        project_id = imported["project"]["project_id"]
        baseline_index_id = imported["index"]["index"]["index_id"]
        refresh_task = asyncio.create_task(
            _http_request(
                budget,
                f"{base_a}/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh",
                stage="HTTP A refresh",
                method="POST",
            )
        )
        await _wait_for_gate(
            gate,
            process_a,
            timeout=budget.remaining("wait HTTP A download gate"),
            stage=budget.stage,
        )
        before_exit = _registry_state(data_root, project_id)
        interrupted_attempt_id = before_exit["attempt"]["attempt_id"]
        if before_exit["latest_index_id"] != baseline_index_id or len(before_exit["updates"]) != 1:
            raise RuntimeError(f"before-latest window was not captured: {before_exit}")
        _touch(crash)
        await asyncio.to_thread(
            _wait_for_process_exit,
            process_a,
            min(10, budget.remaining("wait HTTP A exit")),
        )
        process_a = None
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        refresh_task = None

        budget.set_stage("start recovery HTTP worker")
        recovery, base_recovery, _ = _start_http_worker(
            data_root,
            [],
            log_dir=log_dir,
            label="before-latest-recovery",
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining()),
        )
        status, attempt = await _http_request(
            budget,
            f"{base_recovery}/api/imports/{interrupted_attempt_id}",
            stage="read before-latest recovery",
        )
        if status != 200 or attempt["attempt"]["status"] != "failed":
            raise RuntimeError(f"before-latest recovery did not fail the interrupted attempt: {status} {attempt}")
        recovered = _registry_state(data_root, project_id)
        if (
            recovered["latest_index_id"] != baseline_index_id
            or recovered["resolved_commit"] != FIRST_COMMIT
            or recovered["updates"]
            or recovered["attempt"]["status"] != "failed"
        ):
            raise RuntimeError(f"before-latest recovery changed old state: {recovered}")
        if not _cleanup_http_process(
            recovery,
            timeout=budget.cleanup_timeout,
            label="before-latest recovery",
        ):
            raise RuntimeError("before-latest recovery worker could not be reclaimed")
        recovery = None
        budget.set_stage("start second recovery HTTP worker")
        second_recovery, base_second, _ = _start_http_worker(
            data_root,
            [],
            log_dir=log_dir,
            label="before-latest-recovery-second",
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining()),
        )
        status, second_attempt = await _http_request(
            budget,
            f"{base_second}/api/imports/{interrupted_attempt_id}",
            stage="read second before-latest recovery",
        )
        if status != 200 or second_attempt["attempt"]["status"] != "failed":
            raise RuntimeError(f"second before-latest restart was not idempotent: {status} {second_attempt}")
        final = _registry_state(data_root, project_id)
        if final["latest_index_id"] != baseline_index_id or final["resolved_commit"] != FIRST_COMMIT or final["updates"]:
            raise RuntimeError(f"second before-latest restart changed state: {final}")
        success = True
        return {"scenario": "http_exit_before_latest", "state": final}
    finally:
        _touch(release)
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        _cleanup_http_process(process_a, timeout=budget.cleanup_timeout, label="before-latest A")
        _cleanup_http_process(recovery, timeout=budget.cleanup_timeout, label="before-latest recovery")
        _cleanup_http_process(
            second_recovery,
            timeout=budget.cleanup_timeout,
            label="before-latest second recovery",
        )
        if success:
            shutil.rmtree(data_root, ignore_errors=True)
        else:
            _log(f"[offline] retained failed scenario logs at {data_root}")


async def _scenario_exit_after_latest_before_registry_commit(budget: _ScenarioBudget) -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="codetrail-github-exit-after-latest-"))
    log_dir = data_root / "logs"
    gate = data_root / "a-registry-commit-started"
    release = data_root / "a-registry-commit-release"
    crash = data_root / "a-crash"
    process_a: subprocess.Popen[bytes] | None = None
    refresh_task: asyncio.Task[tuple[int, dict[str, Any]]] | None = None
    recovery: subprocess.Popen[bytes] | None = None
    second_recovery: subprocess.Popen[bytes] | None = None
    success = False
    try:
        _log("[offline] process exit after latest CAS is recovered by restart")
        process_a, base_a, _ = _start_http_worker(
            data_root,
            [FIRST_COMMIT, SECOND_COMMIT, SECOND_COMMIT],
            log_dir=log_dir,
            label="after-latest-a",
            block_commit_call=2,
            gate=gate,
            release=release,
            crash=crash,
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining("start HTTP A")),
        )
        status, imported = await _http_request(
            budget,
            f"{base_a}/api/imports/github",
            stage="seed HTTP A",
            method="POST",
            payload={"name": "Exit After Latest", "url": GITHUB_URL, "ref": "main"},
        )
        if status != 200:
            raise RuntimeError(f"HTTP seed failed: {status} {imported}")
        project_id = imported["project"]["project_id"]
        baseline_index_id = imported["index"]["index"]["index_id"]
        refresh_task = asyncio.create_task(
            _http_request(
                budget,
                f"{base_a}/api/projects/{project_id}/indexes/{baseline_index_id}/github-refresh",
                stage="HTTP A refresh",
                method="POST",
            )
        )
        await _wait_for_gate(
            gate,
            process_a,
            timeout=budget.remaining("wait HTTP A registry gate"),
            stage=budget.stage,
        )
        before_exit = _registry_state(data_root, project_id)
        interrupted_attempt_id = before_exit["attempt"]["attempt_id"]
        pending = before_exit["updates"]
        if (
            before_exit["latest_index_id"] == baseline_index_id
            or len(pending) != 1
            or pending[0]["phase"] != "latest_committed"
            or pending[0]["candidate_index_id"] != before_exit["latest_index_id"]
            or before_exit["resolved_commit"] != FIRST_COMMIT
        ):
            raise RuntimeError(f"after-latest window was not captured: {before_exit}")
        recovered_index_id = before_exit["latest_index_id"]
        _touch(crash)
        await asyncio.to_thread(
            _wait_for_process_exit,
            process_a,
            min(10, budget.remaining("wait HTTP A exit")),
        )
        process_a = None
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        refresh_task = None

        budget.set_stage("start recovery HTTP worker")
        recovery, base_recovery, _ = _start_http_worker(
            data_root,
            [],
            log_dir=log_dir,
            label="after-latest-recovery",
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining()),
        )
        status, attempt = await _http_request(
            budget,
            f"{base_recovery}/api/imports/{interrupted_attempt_id}",
            stage="read after-latest recovery",
        )
        if status != 200 or attempt["attempt"]["status"] != "ready":
            raise RuntimeError(f"after-latest recovery did not complete the attempt: {status} {attempt}")
        recovered = _registry_state(data_root, project_id)
        if (
            recovered["latest_index_id"] != recovered_index_id
            or recovered["resolved_commit"] != SECOND_COMMIT
            or recovered["updates"]
            or recovered["attempt"]["status"] != "ready"
            or recovered["attempt"]["index_id"] != recovered_index_id
        ):
            raise RuntimeError(f"after-latest recovery did not converge: {recovered}")
        if not _cleanup_http_process(
            recovery,
            timeout=budget.cleanup_timeout,
            label="after-latest recovery",
        ):
            raise RuntimeError("after-latest recovery worker could not be reclaimed")
        recovery = None
        budget.set_stage("start second recovery HTTP worker")
        second_recovery, base_second, _ = _start_http_worker(
            data_root,
            [],
            log_dir=log_dir,
            label="after-latest-recovery-second",
            release_timeout=budget.release_timeout,
            startup_timeout=min(HTTP_STARTUP_TIMEOUT_SECONDS, budget.remaining()),
        )
        status, second_attempt = await _http_request(
            budget,
            f"{base_second}/api/imports/{interrupted_attempt_id}",
            stage="read second after-latest recovery",
        )
        if status != 200 or second_attempt["attempt"]["status"] != "ready":
            raise RuntimeError(f"second after-latest restart was not idempotent: {status} {second_attempt}")
        final = _registry_state(data_root, project_id)
        if final["latest_index_id"] != recovered_index_id or final["resolved_commit"] != SECOND_COMMIT or final["updates"]:
            raise RuntimeError(f"second after-latest restart changed state: {final}")
        success = True
        return {"scenario": "http_exit_after_latest_before_registry_commit", "state": final}
    finally:
        _touch(release)
        await _cancel_task(refresh_task, budget.cleanup_timeout)
        _cleanup_http_process(process_a, timeout=budget.cleanup_timeout, label="after-latest A")
        _cleanup_http_process(recovery, timeout=budget.cleanup_timeout, label="after-latest recovery")
        _cleanup_http_process(
            second_recovery,
            timeout=budget.cleanup_timeout,
            label="after-latest second recovery",
        )
        if success:
            shutil.rmtree(data_root, ignore_errors=True)
        else:
            _log(f"[offline] retained failed scenario logs at {data_root}")


def _try_public_github() -> dict[str, Any]:
    data_root = Path(tempfile.mkdtemp(prefix="codetrail-github-public-"))
    log_dir = data_root / "logs"
    process: subprocess.Popen[bytes] | None = None
    try:
        _log("[public] probing pallets/itsdangerous with the real GitHub client")
        process, base_url, _ = _start_http_worker(
            data_root,
            [],
            log_dir=log_dir,
            label="public-github",
            real_github=True,
        )
        status, imported = _http_json(
            f"{base_url}/api/imports/github",
            "POST",
            {"name": "Public GitHub Probe", "url": "https://github.com/pallets/itsdangerous"},
            timeout=20 * 60,
        )
        job_status = None
        if status == 200:
            status, job = _wait_for_job(base_url, imported["job_id"], timeout=20 * 60)
            job_status = job.get("status")
            imported = job.get("result") or job
        result: dict[str, Any] = {"status": status, "job_status": job_status, "response": imported}
        if status == 200 and job_status == "succeeded":
            project_id = imported["project"]["project_id"]
            index_id = imported["index"]["index"]["index_id"]
            freshness_status, freshness = _http_json(
                f"{base_url}/api/projects/{project_id}/indexes/{index_id}/freshness",
                "POST",
            )
            result["freshness_status"] = freshness_status
            result["freshness"] = freshness
            result["resolved_commit"] = imported["attempt"]["resolved_commit"]
        return result
    except Exception as exc:
        return {"status": "unverified", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _stop_http_process(process)
        shutil.rmtree(data_root, ignore_errors=True)


async def _run_scenario(
    name: str,
    scenario: Any,
    *,
    scenario_timeout: float,
    release_timeout: float,
) -> dict[str, Any]:
    budget = _ScenarioBudget(name, scenario_timeout, release_timeout)
    try:
        return await asyncio.wait_for(
            scenario(budget),
            timeout=scenario_timeout,
        )
    except _ScenarioTimeout:
        raise
    except asyncio.TimeoutError as exc:
        raise _ScenarioTimeout(
            f"scenario={name} stage={budget.stage} exceeded "
            f"the {scenario_timeout:.1f}s total timeout"
        ) from exc


async def _run_parent(args: argparse.Namespace) -> None:
    scenarios = {
        "http-a-mcp-b": _scenario_http_active_mcp_conflict,
        "mcp-a-http-b": _scenario_mcp_active_http_conflict,
        "before-latest": _scenario_exit_before_latest,
        "after-latest": _scenario_exit_after_latest_before_registry_commit,
    }
    selected = tuple(scenarios) if args.scenario == "all" else (args.scenario,)
    results: list[dict[str, Any]] = []
    for name in selected:
        _log(f"[offline] starting scenario {name}")
        try:
            results.append(
                await _run_scenario(
                    name,
                    scenarios[name],
                    scenario_timeout=args.scenario_timeout,
                    release_timeout=args.release_timeout,
                )
            )
        except _ScenarioTimeout as exc:
            _log(f"[offline] bounded scenario failure: {exc}")
            raise
    result: dict[str, Any] = {"offline": results}
    if args.public_github:
        result["public_github"] = await asyncio.to_thread(_try_public_github)
    else:
        result["public_github"] = {"status": "not_run", "reason": "pass --public-github explicitly"}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("http", "mcp"))
    parser.add_argument(
        "--scenario",
        choices=("all", "http-a-mcp-b", "mcp-a-http-b", "before-latest", "after-latest"),
        default="http-a-mcp-b",
    )
    parser.add_argument(
        "--scenario-timeout",
        type=_positive_float,
        default=DEFAULT_SCENARIO_TIMEOUT_SECONDS,
        help="total parent timeout per selected scenario (default: 90s)",
    )
    parser.add_argument(
        "--release-timeout",
        type=_positive_float,
        default=DEFAULT_RELEASE_TIMEOUT_SECONDS,
        help="worker release-gate timeout (default: 30s)",
    )
    parser.add_argument("--public-github", action="store_true")
    parser.add_argument("--data-root")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--commits", default="[]")
    parser.add_argument("--pause-download-call", type=int)
    parser.add_argument("--block-commit-call", type=int)
    parser.add_argument("--gate")
    parser.add_argument("--release")
    parser.add_argument("--crash")
    parser.add_argument("--real-github", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.worker is None:
        try:
            asyncio.run(_run_parent(args))
        except _ScenarioTimeout as exc:
            _log(f"[offline] FAILED: {exc}")
            raise SystemExit(2) from exc
        return
    if not args.data_root:
        raise SystemExit("--data-root is required in worker mode")
    _run_worker(args)


if __name__ == "__main__":
    main()
