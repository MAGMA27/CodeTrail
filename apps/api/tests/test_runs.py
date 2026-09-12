"""HTTP lifecycle tests for explicit bounded fixture Runs."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.main import create_app


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


def setup_workspace(tmp_path: Path):
    repository = tmp_path / "timeout-demo"
    shutil.copytree(
        FIXTURE_ROOT,
        repository,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    application = create_app({"timeout-demo": repository}, data_root=tmp_path / "data")
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


def test_restart_marks_incomplete_run_interrupted(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id)
    application.state.workspace_service.run_executor.submit = lambda _run: None
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
