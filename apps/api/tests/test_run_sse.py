"""Persistence-backed Run SSE replay, resume, and capacity tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.run_sse import RunEventStreamConfig
from test_runs import create_session, request, run_payload, setup_workspace, start_run, wait_for_terminal


def _scope(target: str, *, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    parsed = urlsplit(target)
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": [(b"host", b"testserver"), *(headers or [])],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }


async def _json_request(application, target: str, headers: list[tuple[bytes, bytes]] | None = None) -> tuple[int, dict]:
    messages: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await application(_scope(target, headers=headers), receive, send)
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return response_start["status"], json.loads(body or b"{}")


async def _collect_stream(
    application,
    target: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    disconnect_after: int | None = None,
    disconnect_event: asyncio.Event | None = None,
) -> list[dict]:
    messages: list[dict] = []
    received = False
    body_count = 0
    disconnect = disconnect_event or asyncio.Event()

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal body_count
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            body_count += 1
            if disconnect_after is not None and body_count >= disconnect_after:
                disconnect.set()

    task = asyncio.create_task(application(_scope(target, headers=headers), receive, send))
    await asyncio.wait_for(task, timeout=5)
    return messages


def _sse_frames(messages: list[dict]) -> list[dict]:
    payload = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ).decode("utf-8")
    frames: list[dict] = []
    for block in payload.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        frame: dict[str, object] = {}
        for line in block.splitlines():
            key, value = line.split(": ", 1)
            frame[key] = json.loads(value) if key == "data" else value
        frames.append(frame)
    return frames


def _stream_url(project_id: str, session_id: str, run_id: str, after: int | None = None) -> str:
    suffix = "" if after is None else f"?after_sequence={after}"
    return f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events/stream{suffix}"


@pytest.mark.parametrize("field", [
    "poll_interval_seconds",
    "idle_heartbeat_seconds",
    "max_lifetime_seconds",
])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sse_config_rejects_non_finite_time_limits(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        RunEventStreamConfig(**{field: value})


def test_sse_replays_persisted_events_and_resumes_by_both_cursor_forms(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id, "sse replay")
    run_id = start_run(application, project_id, session_id, run_payload(index_id))
    terminal = wait_for_terminal(application, project_id, session_id, run_id)

    status, persisted = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    messages = asyncio.run(_collect_stream(application, _stream_url(project_id, session_id, run_id)))
    frames = _sse_frames(messages)
    event_frames = [frame for frame in frames if frame["event"] != "run.stream.end"]
    assert [frame["data"]["sequence"] for frame in event_frames] == [  # type: ignore[index]
        event["sequence"] for event in persisted["events"]
    ]
    assert [frame["event"] for frame in event_frames] == [  # type: ignore[index]
        event["type"] for event in persisted["events"]
    ]
    assert all(frame["id"] == f"{run_id}:{frame['data']['sequence']}" for frame in event_frames)  # type: ignore[index]
    assert event_frames[-1]["event"] == "run.completed"

    resumed = asyncio.run(
        _collect_stream(
            application,
            _stream_url(project_id, session_id, run_id, 2),
            headers=[(b"last-event-id", f"{run_id}:2".encode("ascii"))],
        )
    )
    resumed_frames = _sse_frames(resumed)
    assert [frame["data"]["sequence"] for frame in resumed_frames if frame["event"] != "run.stream.end"] == [  # type: ignore[index]
        event["sequence"] for event in persisted["events"] if event["sequence"] > 2
    ]

    end_messages = asyncio.run(
        _collect_stream(application, _stream_url(project_id, session_id, run_id, len(persisted["events"])))
    )
    end_frames = _sse_frames(end_messages)
    assert end_frames == [
        {
            "id": f"{run_id}:{len(persisted['events'])}",
            "event": "run.stream.end",
            "data": {
                "schema_version": "0.1.0",
                "run_id": run_id,
                "sequence": len(persisted["events"]),
                "reason": "terminal",
                "status": terminal["run"]["status"],
            },
        }
    ]


def test_sse_rejects_ambiguous_cross_run_and_future_cursors(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path)
    session_id = create_session(application, project_id, index_id, "sse cursor")
    run_id = start_run(application, project_id, session_id, run_payload(index_id))
    wait_for_terminal(application, project_id, session_id, run_id)
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    latest = events["events"][-1]["sequence"]

    status, response = asyncio.run(
        _json_request(
            application,
            _stream_url(project_id, session_id, run_id, 1),
            headers=[(b"last-event-id", f"{run_id}:2".encode("ascii"))],
        )
    )
    assert status == 400
    assert response["code"] == "invalid_input"

    status, response = asyncio.run(
        _json_request(
            application,
            _stream_url(project_id, session_id, run_id),
            headers=[(b"last-event-id", b"other-run:1")],
        )
    )
    assert status == 400
    assert response["code"] == "invalid_input"

    status, response = asyncio.run(
        _json_request(application, _stream_url(project_id, session_id, run_id, latest + 1))
    )
    assert status == 400
    assert response["code"] == "invalid_input"


def test_sse_disconnect_releases_capacity_without_blocking_health_or_cancel(tmp_path: Path) -> None:
    config = RunEventStreamConfig(
        max_connections=1,
        max_per_run=1,
        poll_interval_seconds=0.01,
        idle_heartbeat_seconds=0.03,
        max_lifetime_seconds=2,
    )
    application, project_id, index_id = setup_workspace(tmp_path, run_sse_config=config)
    workspace = application.state.workspace_service
    workspace.run_executor.tool_delay_seconds = 0.5
    session_id = create_session(application, project_id, index_id, "sse capacity")
    run_id = start_run(application, project_id, session_id, run_payload(index_id))

    async def exercise() -> None:
        disconnect = asyncio.Event()
        stream_task = asyncio.create_task(
            _collect_stream(
                application,
                _stream_url(project_id, session_id, run_id),
                disconnect_event=disconnect,
            )
        )
        for _ in range(100):
            if application.state.run_event_streams.state().total == 1:
                break
            await asyncio.sleep(0.01)
        assert application.state.run_event_streams.state().total == 1
        status, response = await _json_request(application, _stream_url(project_id, session_id, run_id))
        assert status == 429
        assert response["code"] == "budget_exceeded"
        disconnect.set()
        await asyncio.wait_for(stream_task, timeout=2)

    asyncio.run(exercise())
    assert application.state.run_event_streams.state().total == 0
    status, health = request(application, "GET", "/api/health")
    assert status == 200
    assert health["status"] == "ok"
    wait_for_terminal(application, project_id, session_id, run_id)
