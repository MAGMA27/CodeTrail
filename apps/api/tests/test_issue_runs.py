"""Offline protocol tests for bounded model-driven real Runs."""

from __future__ import annotations

import asyncio
import http.server
import json
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.feature_generation import FeatureGenerationErrorBase, ModelCall
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

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": [(b"host", b"testserver"), *(([(b"content-type", b"application/json")] if payload is not None else []))],
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


def setup_workspace(tmp_path: Path, adapter=None, *, real_run_enabled: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = tmp_path / "timeout-demo"
    shutil.copytree(FIXTURE_ROOT, repository, ignore=shutil.ignore_patterns("__pycache__", ".codetrail"))
    application = create_app(
        {"timeout-demo": repository},
        data_root=tmp_path / "data",
        real_run_enabled=real_run_enabled,
        run_model_adapter_factory=(lambda _mode: adapter) if adapter is not None else None,
    )
    status, project = request(application, "POST", "/api/projects", {"name": "Real Run", "repository_id": "timeout-demo"})
    assert status == 200
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "real-run-v1", "dirty": False},
    )
    assert status == 200
    return application, project["project_id"], index["index"]["index_id"]


def create_session(application, project_id: str, index_id: str, content: str) -> str:
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions",
        {"topic": content, "index_id": index_id},
    )
    assert status == 200
    session_id = response["session"]["session_id"]
    status, _ = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/messages",
        {"source": "human", "content": content},
    )
    assert status == 200
    return session_id


def start_real_run(application, project_id: str, session_id: str, index_id: str, **extra: object) -> str:
    payload = {
        "index_id": index_id,
        "issue": {
            "schema_version": "0.1.0",
            "id": "issue-real",
            "title": "inspect timeout initialization",
            "body": "Find the missing timeout in the client initialization path.",
        },
        "mode": "real",
    }
    payload.update(extra)
    status, response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        payload,
    )
    assert status == 200, response
    return response["run_id"]


def wait_for_terminal(application, project_id: str, session_id: str, run_id: str) -> dict:
    for _ in range(100):
        status, response = request(
            application,
            "GET",
            f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}",
        )
        assert status == 200
        if response["run"]["status"] not in {"queued", "running"}:
            return response
        time.sleep(0.01)
    raise AssertionError("real Run did not finish")


class SearchThenEvidenceAdapter:
    def __init__(self, *, query: str = "load_settings", invalid_final: bool = False) -> None:
        self.query = query
        self.invalid_final = invalid_final
        self.prompts: list[str] = []

    def generate(self, prompt: str, **_: object) -> ModelCall:
        self.prompts.append(prompt)
        index_id = re.search(r"^index_id=(\S+)$", prompt, re.MULTILINE).group(1)  # type: ignore[union-attr]
        if len(self.prompts) == 1:
            output = {
                "schema_version": "0.1.0",
                "kind": "tool_call",
                "params": {"tool": "search_code", "query": self.query, "limit": 8},
            }
        elif len(self.prompts) == 2:
            match = re.search(r"- symbol_id=(\S+) path=(\S+) lines=(\d+)-(\d+)", prompt)
            assert match is not None
            output = {
                "schema_version": "0.1.0",
                "kind": "tool_call",
                "params": {
                    "tool": "read_source",
                    "path": match.group(2),
                    "start_line": int(match.group(3)),
                    "end_line": min(int(match.group(4)), int(match.group(3)) + 5),
                },
            }
        else:
            symbol_match = re.search(r"- symbol_id=(\S+) path=", prompt)
            evidence_match = re.search(r"- evidence_id=(\S+) path=", prompt)
            assert symbol_match is not None and evidence_match is not None
            evidence_id = "foreign-evidence" if self.invalid_final else evidence_match.group(1)
            output = {
                "schema_version": "0.1.0",
                "kind": "final",
                "index_id": index_id,
                "outcome": "candidate_found",
                "summary": "The configured client path can use a missing timeout value.",
                "findings": [
                    {
                        "finding_id": "finding-real",
                        "kind": "hypothesis",
                        "text": "The timeout is not initialized before client construction.",
                        "symbol_ids": [symbol_match.group(1)],
                        "evidence_ids": [evidence_id],
                    }
                ],
                "candidate_links": [
                    {
                        "candidate_id": "candidate-real",
                        "index_id": index_id,
                        "source_symbol_id": symbol_match.group(1),
                        "target_symbol_id": None,
                        "relation": "may omit timeout initialization",
                        "evidence_ids": [evidence_id],
                    }
                ],
                "evidence_ids": [evidence_id],
            }
        return ModelCall(raw_output=json.dumps(output), input_chars=None)

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, invalid_output, validation_error
        raise FeatureGenerationErrorBase("model_failed", "repair not expected in this test")


class FailingAdapter:
    def generate(self, prompt: str, **_: object) -> ModelCall:
        del prompt
        raise FeatureGenerationErrorBase("model_failed", "fake provider failed", retryable=True)

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, invalid_output, validation_error
        raise AssertionError("repair must not run after provider failure")


class InsufficientEvidenceAdapter:
    def generate(self, prompt: str, **_: object) -> ModelCall:
        index_id = re.search(r"^index_id=(\S+)$", prompt, re.MULTILINE).group(1)  # type: ignore[union-attr]
        return ModelCall(
            raw_output=json.dumps(
                {
                    "schema_version": "0.1.0",
                    "kind": "final",
                    "index_id": index_id,
                    "outcome": "insufficient_evidence",
                    "summary": "The bounded snapshot does not contain enough evidence.",
                    "findings": [],
                    "candidate_links": [],
                    "evidence_ids": [],
                    "limitations": ["No matching source was requested."],
                }
            )
        )

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, invalid_output, validation_error
        raise AssertionError("repair must not run for a valid insufficient-evidence result")


class InvalidToolAdapter:
    def generate(self, prompt: str, **_: object) -> ModelCall:
        del prompt
        return ModelCall(
            raw_output=json.dumps(
                {
                    "schema_version": "0.1.0",
                    "kind": "tool_call",
                    "params": {"tool": "shell", "command": "cat secrets"},
                }
            )
        )

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, validation_error
        return ModelCall(raw_output=invalid_output)


class BlockingAdapter:
    def __init__(self) -> None:
        import threading

        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def generate(self, prompt: str, **_: object) -> ModelCall:
        del prompt
        self.calls += 1
        self.started.set()
        assert self.release.wait(3)
        return ModelCall(raw_output=json.dumps({"kind": "final"}))

    def repair(self, prompt: str, invalid_output: str, validation_error: str, **_: object) -> ModelCall:
        del prompt, invalid_output, validation_error
        raise AssertionError("repair must not run after cancellation")


def test_real_run_uses_different_model_selected_tools_and_persists_bounded_usage(tmp_path: Path) -> None:
    adapter = SearchThenEvidenceAdapter()
    application, project_id, index_id = setup_workspace(tmp_path, adapter)
    session_id = create_session(application, project_id, index_id, "Please investigate timeout initialization.")
    run_id = start_real_run(application, project_id, session_id, index_id, idempotency_key="real-one")

    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "succeeded"
    assert terminal["run"]["mode"] == "real"
    assert terminal["run"]["model_provider"] == "deepseek"
    assert terminal["run"]["budget"]["model_calls_used"] == 3
    assert terminal["run"]["budget"]["tool_calls_used"] == 2
    assert all("Previous JSON" not in prompt for prompt in adapter.prompts)

    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["fixture_mode"] is False
    assert report["report"]["outcome"] == "candidate_found"
    assert report["report"]["evidence"]
    assert report["report"]["candidate_links"]

    other_adapter = SearchThenEvidenceAdapter(query="initialize_client")
    other_app, other_project, other_index = setup_workspace(tmp_path / "other", other_adapter)
    other_session = create_session(other_app, other_project, other_index, "Please inspect client initialization.")
    other_run = start_real_run(other_app, other_project, other_session, other_index)
    wait_for_terminal(other_app, other_project, other_session, other_run)
    assert adapter.query == "load_settings"
    assert other_adapter.query == "initialize_client"


def test_real_run_provider_failure_is_explicit_and_does_not_fallback(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, FailingAdapter())
    session_id = create_session(application, project_id, index_id, "Provider error test")
    run_id = start_real_run(application, project_id, session_id, index_id)
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "failed"
    assert terminal["run"]["budget"]["model_calls_used"] == 1
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["payload"]["error"]["code"] == "model_failed"
    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["fixture_mode"] is False
    assert report["report"]["outcome"] == "failed"


def test_real_run_can_finish_with_explicit_insufficient_evidence(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, InsufficientEvidenceAdapter())
    session_id = create_session(application, project_id, index_id, "Evidence sufficiency test")
    run_id = start_real_run(application, project_id, session_id, index_id)
    terminal = wait_for_terminal(application, project_id, session_id, run_id)

    assert terminal["run"]["status"] == "succeeded"
    assert terminal["run"]["budget"]["model_calls_used"] == 1
    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["outcome"] == "insufficient_evidence"
    assert report["report"]["candidate_links"] == []


def test_real_run_rejects_arbitrary_model_tools_after_one_finite_repair(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, InvalidToolAdapter())
    session_id = create_session(application, project_id, index_id, "Prompt injection tool test")
    run_id = start_real_run(application, project_id, session_id, index_id)
    terminal = wait_for_terminal(application, project_id, session_id, run_id)

    assert terminal["run"]["status"] == "failed"
    assert terminal["run"]["budget"]["model_calls_used"] == 2
    assert terminal["run"]["budget"]["repair_calls_used"] == 1
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["payload"]["error"]["code"] == "invalid_model_output"


def test_real_run_exhausts_model_call_budget_without_fallback(tmp_path: Path) -> None:
    application, project_id, index_id = setup_workspace(tmp_path, SearchThenEvidenceAdapter())
    session_id = create_session(application, project_id, index_id, "Model budget test")
    run_id = start_real_run(
        application,
        project_id,
        session_id,
        index_id,
        budget={"max_model_calls": 1, "max_repair_calls": 0},
    )
    terminal = wait_for_terminal(application, project_id, session_id, run_id)

    assert terminal["run"]["status"] == "failed"
    assert terminal["run"]["budget"]["model_calls_used"] == 1
    status, events = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events",
    )
    assert status == 200
    assert events["events"][-1]["payload"]["error"]["code"] == "budget_exceeded"


def test_real_run_rejects_unreturned_evidence_after_one_finite_repair(tmp_path: Path) -> None:
    adapter = SearchThenEvidenceAdapter(invalid_final=True)
    application, project_id, index_id = setup_workspace(tmp_path, adapter)
    session_id = create_session(application, project_id, index_id, "Invalid reference test")
    run_id = start_real_run(application, project_id, session_id, index_id)
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "failed"
    assert terminal["run"]["budget"]["model_calls_used"] == 4
    assert terminal["run"]["budget"]["repair_calls_used"] == 1
    status, report = request(
        application,
        "GET",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report",
    )
    assert status == 200
    assert report["report"]["outcome"] == "failed"


def test_real_run_cancel_wins_over_a_late_provider_response_and_releases_capacity_after_return(tmp_path: Path) -> None:
    adapter = BlockingAdapter()
    application, project_id, index_id = setup_workspace(tmp_path, adapter)
    workspace = application.state.workspace_service
    session_id = create_session(application, project_id, index_id, "Cancellation during provider request")
    run_id = start_real_run(application, project_id, session_id, index_id)
    assert adapter.started.wait(2)

    status, cancelled = request(
        application,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel",
        {"reason": "cancel provider request"},
    )
    assert status == 200
    assert cancelled["status"] == "cancelled"
    assert workspace.run_executor.state().reserved == 1

    adapter.release.set()
    terminal = wait_for_terminal(application, project_id, session_id, run_id)
    assert terminal["run"]["status"] == "cancelled"
    for _ in range(100):
        if workspace.run_executor.state().reserved == 0:
            break
        time.sleep(0.01)
    assert workspace.run_executor.state().reserved == 0
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
    assert report["report"]["fixture_mode"] is False


def test_real_run_capability_and_missing_credentials_are_separate(tmp_path: Path) -> None:
    disabled, project_id, index_id = setup_workspace(tmp_path / "disabled", SearchThenEvidenceAdapter(), real_run_enabled=False)
    session_id = create_session(disabled, project_id, index_id, "Capability test")
    status, response = request(
        disabled,
        "POST",
        f"/api/projects/{project_id}/sessions/{session_id}/runs",
        {
            "index_id": index_id,
            "issue": {"id": "issue", "title": "real", "body": "provider"},
            "mode": "real",
        },
    )
    assert status == 403
    assert response["code"] == "forbidden"

    missing, missing_project, missing_index = setup_workspace(tmp_path / "missing")
    missing_session = create_session(missing, missing_project, missing_index, "No key test")
    missing_run = start_real_run(missing, missing_project, missing_session, missing_index)
    terminal = wait_for_terminal(missing, missing_project, missing_session, missing_run)
    assert terminal["run"]["status"] == "failed"
    status, events = request(
        missing,
        "GET",
        f"/api/projects/{missing_project}/sessions/{missing_session}/runs/{missing_run}/events",
    )
    assert status == 200
    assert events["events"][-1]["payload"]["error"]["code"] == "model_unavailable"


def test_real_run_uses_persistent_openai_compatible_provider_protocol(tmp_path: Path) -> None:
    calls: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"] or "0")
            payload = json.loads(self.rfile.read(length))
            calls.append({"payload": payload, "authorization": self.headers.get("Authorization")})
            prompt = payload["messages"][1]["content"]
            index_id = re.search(r"^index_id=(\S+)$", prompt, re.MULTILINE).group(1)  # type: ignore[union-attr]
            if len(calls) == 1:
                output = {
                    "schema_version": "0.1.0",
                    "kind": "tool_call",
                    "params": {"tool": "search_code", "query": "load_settings", "limit": 8},
                }
            elif len(calls) == 2:
                match = re.search(r"- symbol_id=(\S+) path=(\S+) lines=(\d+)-(\d+)", prompt)
                assert match is not None
                output = {
                    "schema_version": "0.1.0",
                    "kind": "tool_call",
                    "params": {
                        "tool": "read_source",
                        "path": match.group(2),
                        "start_line": int(match.group(3)),
                        "end_line": min(int(match.group(4)), int(match.group(3)) + 5),
                    },
                }
            else:
                symbol_match = re.search(r"- symbol_id=(\S+) path=", prompt)
                evidence_match = re.search(r"- evidence_id=(\S+) path=", prompt)
                assert symbol_match is not None and evidence_match is not None
                output = {
                    "schema_version": "0.1.0",
                    "kind": "final",
                    "index_id": index_id,
                    "outcome": "candidate_found",
                    "summary": "The provider returned a bounded evidence-backed candidate.",
                    "findings": [],
                    "candidate_links": [
                        {
                            "candidate_id": "candidate-http",
                            "index_id": index_id,
                            "source_symbol_id": symbol_match.group(1),
                            "target_symbol_id": None,
                            "relation": "provider protocol test",
                            "evidence_ids": [evidence_match.group(1)],
                        }
                    ],
                    "evidence_ids": [evidence_match.group(1)],
                }
            body = json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(output)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data_root = tmp_path / "data"
        data_root.mkdir(parents=True)
        (data_root / "config.json").write_text(
            json.dumps(
                {
                    "provider": "deepseek-local",
                    "api_key": "test-key",
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "model": "deepseek-test",
                }
            ),
            encoding="utf-8",
        )
        repository = tmp_path / "timeout-demo"
        shutil.copytree(FIXTURE_ROOT, repository, ignore=shutil.ignore_patterns("__pycache__", ".codetrail"))
        application = create_app({"timeout-demo": repository}, data_root=data_root, real_run_enabled=True)
        status, project = request(application, "POST", "/api/projects", {"name": "Provider protocol", "repository_id": "timeout-demo"})
        assert status == 200
        status, index = request(
            application,
            "POST",
            f"/api/projects/{project['project_id']}/indexes",
            {"revision": "provider-v1", "dirty": False},
        )
        assert status == 200
        project_id = project["project_id"]
        index_id = index["index"]["index_id"]
        session_id = create_session(application, project_id, index_id, "Provider protocol test")
        run_id = start_real_run(application, project_id, session_id, index_id)
        terminal = wait_for_terminal(application, project_id, session_id, run_id)
        assert terminal["run"]["status"] == "succeeded"
        assert terminal["run"]["model"] == "deepseek-test"
        assert terminal["run"]["model_provider"] == "deepseek-local"
        assert len(calls) == 3
        assert all(call["authorization"] == "Bearer test-key" for call in calls)
        assert all(call["payload"]["model"] == "deepseek-test" for call in calls)
        assert all(call["payload"]["response_format"] == {"type": "json_object"} for call in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
