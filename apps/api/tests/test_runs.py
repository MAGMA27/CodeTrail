"""HTTP lifecycle tests for explicit bounded fixture Runs."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.main import create_app
from app.run_sse import RunEventStreamConfig
from app.storage import IndexStore, StorageError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


async def _request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    parsed = urlsplit(target)
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await application(scope, receive, send)
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return response_start["status"], json.loads(response_body or b"{}")


def request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    return asyncio.run(_request(application, method, target, payload))


def setup_workspace(
    tmp_path: Path,
    *,
    run_max_concurrent_runs: int | None = None,
    run_sse_config: RunEventStreamConfig | None = None,
):
    repository = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        repository,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    application = create_app(
        {"timeout-demo": repository},
        data_root=tmp_path / "data",
        run_max_concurrent_runs=run_max_concurrent_runs,
        run_sse_config=run_sse_config,
    )
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Run fixture", "repository_id": "timeout-demo"},
    )
    assert status == 200
    project_id = project["project_id"]
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "run-fixture-v1", "dirty": False},
    )
    assert status == 200
    return application, project_id, index["index"]["index_id"]


def create_session(application, project_id: str, index_id: str, topic: str = "timeout") -> str:
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": topic, "index_id": index_id},
    )
    assert status == 200
    session_id = response["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "Please inspect the timeout initialization path."},
    )
    assert status == 200
    return session_id


def run_payload(index_id: str, scenario: str = "success-timeout-missing", **extra: object) -> dict:
    payload = {
        "index_id": index_id,
        "issue": {
            "schema_version": "0.1.0",
            "id": "timeout-issue",
            "title": "timeout missing",
            "body": "Investigate the missing timeout during client initialization.",
        },
        "mode": "fixture",
        "fixture_scenario": scenario,
    }
    payload.update(extra)
    return payload


def start_run(application, project_id: str, session_id: str, payload: dict) -> str:
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        payload,
    )
    assert status == 200, response
    return response["run_id"]


def wait_for_terminal(application, project_id: str, session_id: str, run_id: str) -> dict:
    last: dict = {}
    for _ in range(100):
        status, last = request(
            application,
            "GET",
            f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
        )
        assert status == 200, last
        if last["run"]["status"] not in {"queued", "running"}:
            return last
        time.sleep(0.025)
    raise AssertionError(f"Run did not finish: {last}")


def test_http_run_freezes_saved_input_and_returns_same_snapshot_evidence(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id)
    run_id = start_run(application, project_id, session_id, run_payload(index_id))

    status, appended = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": "This message was appended after Run creation."},
    )
    assert status == 200
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "succeeded"
    assert terminal["input"]["message_end_sequence"] == 1
    assert len(terminal["input"]["messages"]) == 1

    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.completed"
    assert {event["type"] for event in events["events"]} >= {
        "run.started",
        "tool.started",
        "tool.completed",
        "graph.updated",
        "finding.created",
    }

    status, results = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/tool-results",
    )
    assert status == 200
    assert [result["tool"] for result in results["results"]] == [
        "search_code",
        "read_source",
        "get_neighbors",
    ]

    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["outcome"] == "candidate_found"
    assert report["report"]["evidence"]
    assert all(item["index_id"] == index_id for item in report["report"]["evidence"])
    assert report["report"]["graph"]["index_id"] == index_id
    assert appended["message"]["sequence"] == 2

    source_root = application.state.workspace_service.registry.root_for("timeout-demo")
    config_path = source_root / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n# A later source revision must not change the frozen Run.\n",
        encoding="utf-8",
    )
    status, new_index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "run-fixture-v2", "dirty": False},
    )
    assert status == 200
    assert new_index["index"]["index_id"] != index_id
    status, reread = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
    )
    assert status == 200
    assert reread["run"]["index_id"] == index_id
    status, health = request(application, "GET", "/api/health")
    assert status == 200
    assert health["status"] == "ok"


def test_fixture_run_outcomes_and_real_mode_boundary(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    for scenario, expected_status, expected_outcome in (
        ("insufficient-external-service", "succeeded", "insufficient_evidence"),
        ("tool-failure-read-source", "failed", "failed"),
    ):
        session_id = create_session(application, project_id, index_id, scenario)
        run_id = start_run(application, project_id, session_id, run_payload(index_id, scenario))
        terminal = wait_for_terminal(application, project_id, session_id, run_id)
        assert terminal["run"]["status"] == expected_status
        status, report = request(
            application,
            "GET",
            f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
        )
        assert status == 200
        assert report["report"]["outcome"] == expected_outcome
        if scenario == "tool-failure-read-source":
            assert report["report"]["evidence"]

    session_id = create_session(application, project_id, index_id, "real boundary")
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        {
            "index_id": index_id,
            "issue": {"id": "real-issue", "title": "real", "body": "real provider"},
            "mode": "real",
        },
    )
    assert status == 403
    assert response["code"] == "forbidden"


def test_run_impact_is_report_scoped_bounded_and_empty_without_a_candidate(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id, "impact")
    run_id = start_run(application, project_id, session_id, run_payload(index_id))
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "succeeded"
    status, report_response = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    candidate_id = report_response["report"]["candidate_links"][0]["candidate_id"]
    candidate_symbol_id = report_response["report"]["candidate_links"][0]["source_symbol_id"]
    finding_id = report_response["report"]["findings"][0]["finding_id"]
    finding_symbol_id = report_response["report"]["findings"][0]["symbol_ids"][0]

    status, callers = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"
        f"?candidate_id={candidate_id}&direction=callers&depth=2&limit=40",
    )
    assert status == 200, callers
    assert callers["project_id"] == project_id
    assert callers["session_id"] == session_id
    assert callers["run_id"] == run_id
    assert callers["index_id"] == index_id
    assert callers["candidate_id"] == candidate_id
    assert callers["finding_id"] is None
    assert callers["mapping_status"] == "mapped"
    assert callers["direction"] == "callers"
    assert callers["max_depth"] == 2
    assert callers["root_symbol_id"] == candidate_symbol_id
    assert callers["reachable_node_count"] == len(callers["nodes"]) - 1
    assert all(edge["kind"] == "calls" for edge in callers["edges"])
    assert all(path["symbol_ids"][0] == candidate_symbol_id for path in callers["paths"])
    assert "静态可达不等同于已确认影响" in callers["scope_note"]

    source_root = application.state.workspace_service.registry.root_for("timeout-demo")
    config_path = source_root / "timeout_demo" / "config.py"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n# A later source revision must not change the frozen Run.\n",
        encoding="utf-8",
    )
    status, newer_index = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes",
        {"revision": "run-fixture-v2", "dirty": False},
    )
    assert status == 200, newer_index
    assert newer_index["index"]["index_id"] != index_id
    status, old_report_impact = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"
        f"?candidate_id={candidate_id}",
    )
    assert status == 200, old_report_impact
    assert old_report_impact["index_id"] == index_id

    status, finding_impact = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"
        f"?finding_id={finding_id}&direction=callees&depth=1&limit=40",
    )
    assert status == 200, finding_impact
    assert finding_impact["finding_id"] == finding_id
    assert finding_impact["candidate_id"] is None
    assert finding_impact["direction"] == "callees"
    assert finding_impact["max_depth"] == 1
    assert finding_impact["root_symbol_id"] == finding_symbol_id
    assert all(path["depth"] == 1 for path in finding_impact["paths"])

    status, invalid_scope = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"
        f"?candidate_id={candidate_id}&finding_id={finding_id}",
    )
    assert status == 400
    assert invalid_scope["code"] == "invalid_input"

    status, unknown_candidate = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"
        "?candidate_id=not-in-report",
    )
    assert status == 404
    assert unknown_candidate["code"] == "not_found"

    failed_session = create_session(application, project_id, index_id, "failed impact")
    failed_run = start_run(
        application,
        project_id,
        failed_session,
        run_payload(index_id, "tool-failure-read-source"),
    )
    wait_for_terminal(application, project_id, failed_session, failed_run)
    status, unmapped = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{failed_session}/runs/{failed_run}/impact"
        f"?finding_id=finding-{failed_run}-failure",
    )
    assert status == 200, unmapped
    assert unmapped["mapping_status"] == "unmapped"
    assert unmapped["nodes"] == []
    assert unmapped["paths"] == []
    assert unmapped["reachable_node_count"] == 0


def test_run_idempotency_active_conflict_and_cancellation_are_persisted(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    workspace = application.state.workspace_service
    workspace.run_executor.tool_delay_seconds = 0.12
    session_id = create_session(application, project_id, index_id)
    payload = run_payload(index_id, idempotency_key="same-run")
    run_id = start_run(application, project_id, session_id, payload)
    status, repeated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        payload,
    )
    assert status == 200
    assert repeated["run_id"] == run_id
    status, active_conflict = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        run_payload(index_id, issue={"id": "other", "title": "other", "body": "other"}),
    )
    assert status == 409
    assert active_conflict["code"] == "conflict"

    status, cancelled = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel",
        {"reason": "test cancellation"},
    )
    assert status == 200
    assert cancelled["status"] == "cancelled"
    status, cancelled_again = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel",
        {"reason": "repeat cancellation"},
    )
    assert status == 200
    assert cancelled_again["status"] == "cancelled"
    time.sleep(0.3)
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "cancelled"
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.cancelled"
    assert not any(event["type"] == "run.completed" for event in events["events"])
    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["status"] == "cancelled"
    assert report["report"]["outcome"] == "cancelled"
    assert workspace.run_executor.state().reserved == 0


def test_run_capacity_is_rejected_before_persistence_and_released_after_cancel(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, run_max_concurrent_runs=1)
    workspace = application.state.workspace_service
    workspace.run_executor.tool_delay_seconds = 0.4
    sessions = {
        "one": create_session(application, project_id, index_id, "capacity one"),
        "two": create_session(application, project_id, index_id, "capacity two"),
    }
    barrier = threading.Barrier(2)

    def submit(label: str) -> tuple[int, dict]:
        barrier.wait()
        return request(
            application,
            "POST",
            f"/api/projects/{project_id}/sessions/{sessions[label]}/runs",
            run_payload(index_id, idempotency_key=f"capacity-{label}"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {label: pool.submit(submit, label) for label in sessions}
        responses = {label: future.result() for label, future in futures.items()}

    accepted = [label for label, (status, _) in responses.items() if status == 200]
    rejected = [label for label, (status, _) in responses.items() if status == 429]
    assert len(accepted) == 1
    assert rejected == [next(label for label in sessions if label not in accepted)]
    accepted_label = accepted[0]
    rejected_label = rejected[0]
    accepted_status, accepted_response = responses[accepted_label]
    rejected_status, rejected_response = responses[rejected_label]
    assert accepted_status == 200
    assert rejected_response["code"] == "budget_exceeded"
    assert "capacity" in rejected_response["message"].lower()

    status, rejected_history = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{sessions[rejected_label]}/runs",
    )
    assert status == 200
    assert rejected_history["runs"] == []

    status, repeated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{sessions[accepted_label]}/runs",
        run_payload(index_id, idempotency_key=f"capacity-{accepted_label}"),
    )
    assert status == 200
    assert repeated["run_id"] == accepted_response["run_id"]
    state = workspace.run_executor.state()
    assert state.capacity == 1
    assert state.reserved == 1
    assert state.worker_threads <= 1
    assert state.tracked_threads <= 1
    assert state.waiting_runs == 0

    status, health = request(application, "GET", "/api/health")
    assert status == 200
    assert health["status"] == "ok"
    accepted_run_id = accepted_response["run_id"]
    status, cancelled = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{sessions[accepted_label]}/runs/{accepted_run_id}/cancel",
        {"reason": "release capacity"},
    )
    assert status == 200
    assert cancelled["status"] == "cancelled"
    terminal = wait_for_terminal(
        application,
        project_id,
        sessions[accepted_label],
        accepted_run_id,
    )
    assert terminal["run"]["status"] == "cancelled"
    for _ in range(40):
        if workspace.run_executor.state().reserved == 0:
            break
        time.sleep(0.025)
    assert workspace.run_executor.state().reserved == 0

    status, released_response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{sessions[rejected_label]}/runs",
        run_payload(index_id, idempotency_key="capacity-after-release"),
    )
    assert status == 200
    released_terminal = wait_for_terminal(
        application,
        project_id,
        sessions[rejected_label],
        released_response["run_id"],
    )
    assert released_terminal["run"]["status"] == "succeeded"
    assert workspace.run_executor.state().reserved == 0


def test_run_cancellation_during_queued_worker_start_has_one_terminal_state(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, run_max_concurrent_runs=1)
    workspace = application.state.workspace_service
    start_gate = threading.Event()

    class BlockingStartThread(threading.Thread):
        def start(self) -> None:
            assert start_gate.wait(2)
            super().start()

    workspace.run_executor.thread_factory = BlockingStartThread
    session_id = create_session(application, project_id, index_id, "queued cancellation")
    payload = run_payload(index_id, idempotency_key="queued-cancel")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            request,
            application,
            "POST",
            f"/api/projects/{project_id}/sessions/{session_id}/runs",
            payload,
        )
        run_id: str | None = None
        for _ in range(80):
            status, history = request(
                application,
                "GET",
                f"/api/projects/{project_id}/sessions/{session_id}/runs",
            )
            assert status == 200
            if history["runs"]:
                run_id = history["runs"][0]["run_id"]
                assert history["runs"][0]["status"] == "queued"
                break
            time.sleep(0.025)
        assert run_id is not None
        status, cancelled = request(
            application,
            "POST",
            f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel",
            {"reason": "cancel before worker start"},
        )
        assert status == 200
        assert cancelled["status"] == "cancelled"
        start_gate.set()
        pending_status, pending_response = pending.result(timeout=3)
    assert pending_status == 200
    assert pending_response["run_id"] == run_id
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "cancelled"
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.cancelled"
    assert not any(event["type"] == "run.started" for event in events["events"])
    for _ in range(40):
        state = workspace.run_executor.state()
        if state.reserved == 0 and state.tracked_threads == 0:
            break
        time.sleep(0.025)
    state = workspace.run_executor.state()
    assert state.reserved == 0
    assert state.tracked_threads == 0


def test_run_worker_start_failure_is_terminal_and_releases_capacity(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, run_max_concurrent_runs=1)
    workspace = application.state.workspace_service

    class FailedStartThread(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("injected worker start failure")

    workspace.run_executor.thread_factory = FailedStartThread
    session_id = create_session(application, project_id, index_id, "worker start failure")
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        run_payload(index_id, idempotency_key="worker-start-failure"),
    )
    assert status == 503
    assert response["code"] == "internal_error"
    status, history = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
    )
    assert status == 200
    assert len(history["runs"]) == 1
    run_id = history["runs"][0]["run_id"]
    assert history["runs"][0]["status"] == "failed"
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.failed"
    assert events["events"][-1]["payload"]["error"]["code"] == "internal_error"
    assert workspace.run_executor.state().reserved == 0
    assert workspace.run_executor.state().tracked_threads == 0


def test_run_storage_failure_does_not_leak_reserved_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, run_max_concurrent_runs=1)
    workspace = application.state.workspace_service
    session_id = create_session(application, project_id, index_id, "storage failure")

    def fail_create_run(*args: object, **kwargs: object) -> None:
        raise StorageError("injected Run storage failure")

    monkeypatch.setattr(IndexStore, "create_run", fail_create_run)
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        run_payload(index_id, idempotency_key="storage-failure"),
    )
    assert status == 500
    assert response["code"] == "internal_error"
    assert workspace.run_executor.state().reserved == 0
    assert workspace.run_executor.state().tracked_threads == 0


def test_run_execution_exception_and_timeout_release_capacity_with_one_terminal_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, run_max_concurrent_runs=1)
    workspace = application.state.workspace_service
    session_id = create_session(application, project_id, index_id, "execution exception")

    def fail_tool(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected tool exception")

    monkeypatch.setattr(workspace.index_service, "execute_tool", fail_tool)
    run_id = start_run(application, project_id, session_id, run_payload(index_id, idempotency_key="tool-exception"))
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "failed"
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert [event["type"] for event in events["events"]].count("run.failed") == 1
    assert workspace.run_executor.state().reserved == 0

    monkeypatch.undo()
    timeout_session = create_session(application, project_id, index_id, "execution timeout")
    workspace.run_executor.tool_delay_seconds = 0.6
    timeout_run_id = start_run(
        application,
        project_id,
        timeout_session,
        run_payload(index_id, idempotency_key="tool-timeout", budget={"max_seconds": 1}),
    )
    timeout_terminal = wait_for_terminal(application, project_id, timeout_session, timeout_run_id)
    assert timeout_terminal["run"]["status"] == "failed"
    status, timeout_events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{timeout_session}/runs/{timeout_run_id}/events",
    )
    assert status == 200
    assert timeout_events["events"][-1]["payload"]["error"]["code"] == "budget_exceeded"
    assert [event["type"] for event in timeout_events["events"]].count("run.failed") == 1
    for _ in range(40):
        if workspace.run_executor.state().reserved == 0:
            break
        time.sleep(0.025)
    assert workspace.run_executor.state().reserved == 0


def test_restart_marks_incomplete_run_interrupted(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id)
    def leave_run_queued(_run: object, *, reservation: object) -> None:
        reservation.release()  # type: ignore[attr-defined]

    application.state.workspace_service.run_executor.submit = leave_run_queued  # type: ignore[method-assign]
    run_id = start_run(application, project_id, session_id, run_payload(index_id))
    status, queued = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
    )
    assert status == 200
    assert queued["run"]["status"] == "queued"

    repository = tmp_path / "timeout-demo"
    restarted = create_app({"timeout-demo": repository}, data_root=tmp_path / "data")
    status, recovered = request(
        restarted,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
    )
    assert status == 200
    assert recovered["run"]["status"] == "interrupted"
    status, events = request(
        restarted,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.interrupted"
    status, report = request(
        restarted,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["status"] == "interrupted"
    assert report["report"]["outcome"] == "interrupted"


def test_run_budget_failure_is_terminal_and_reported(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id, "event budget")
    run_id = start_run(
        application,
        project_id,
        session_id,
        run_payload(index_id, budget={"max_events": 2}),
    )
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "failed"
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["type"] == "run.failed"
    assert events["events"][-1]["payload"]["error"]["code"] == "budget_exceeded"
    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["outcome"] == "failed"
