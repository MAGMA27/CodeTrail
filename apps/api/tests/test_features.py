"""Offline O2 tests for bounded semantic feature generation."""

from __future__ import annotations

import asyncio
import http.server
import json
import shutil
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.config import MODEL_CONFIG_FILENAME
from app.feature_generation import (
    DEFAULT_REAL_BASE_URL,
    DEFAULT_REAL_MODEL,
    FixtureFeatureAdapter,
    ModelCall,
    OpenAICompatibleFeatureAdapter,
    resolve_model,
)
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


def _copy_fixture(tmp_path: Path, name: str = "timeout-demo") -> Path:
    destination = tmp_path / name
    shutil.copytree(
        FIXTURE_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    return destination


def _create_index(application) -> tuple[str, str]:
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "Feature project", "repository_id": "timeout-demo"},
    )
    assert status == 200
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "feature-v1", "dirty": False},
    )
    assert status == 200
    return project["project_id"], index["index"]["index_id"]


def test_fixture_generation_uses_real_tools_persists_and_reuses_idempotency(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    project_id, index_id = _create_index(application)
    payload = {
        "schema_version": "0.1.0",
        "mode": "fixture",
        "source_scope": "entry_reachable",
        "idempotency_key": "feature-attempt-one",
    }

    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        payload,
    )
    assert status == 200
    generation = generated["generation"]
    assert generation["mode"] == "fixture"
    assert generation["rules_version"] == "python-entry-v1"
    assert generation["status"] in {"succeeded", "partial"}
    assert generation["features"]
    assert generation["usage"]["tool_calls"] >= 3
    assert generation["usage"]["model_calls"] == 1
    evidence_ids = {item["evidence_id"] for item in generation["evidence"]}
    observed_symbols = set(generation["observed_symbol_ids"])
    assert evidence_ids
    assert observed_symbols
    assert all(
        set(feature["evidence_ids"]).issubset(evidence_ids)
        and set(feature["symbol_ids"]).issubset(observed_symbols)
        for feature in generation["features"]
    )
    assert any(
        item["path"] == "timeout_demo/config.py" and "def load_settings" in item["excerpt"]
        for item in generation["evidence"]
    )

    status, reread = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{generation['generation_id']}",
    )
    assert status == 200
    assert reread == generated

    status, repeated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        payload,
    )
    assert status == 200
    assert repeated == generated

    (repository / "timeout_demo" / "config.py").write_text("changed after snapshot\n", encoding="utf-8")
    status, stable = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{generation['generation_id']}",
    )
    assert status == 200
    assert stable == generated


def test_real_mode_without_server_credentials_is_explicit_and_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODETRAIL_MODEL_API_KEY", "env-value-must-be-ignored")
    monkeypatch.setenv("CODETRAIL_MODEL_BASE_URL", "https://env-value-must-be-ignored.example")
    monkeypatch.setenv("CODETRAIL_MODEL_NAME", "env-model-must-be-ignored")
    application = create_app(
        {"timeout-demo": _copy_fixture(tmp_path)},
        data_root=tmp_path / "service-data",
    )
    project_id, index_id = _create_index(application)
    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {"schema_version": "0.1.0", "mode": "real", "idempotency_key": "real-no-key"},
    )
    assert status == 200
    generation = generated["generation"]
    assert generation["status"] == "failed"
    assert generation["error"]["kind"] == "model_unavailable"
    assert generation["features"] == []
    assert generation["mode"] == "real"
    assert generation["model"] == "deepseek-flash"


def test_persistent_deepseek_config_is_loaded_from_service_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "service-data"
    data_root.mkdir()
    (data_root / MODEL_CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "api_key": "sk-persisted-test",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-flash",
            }
        ),
        encoding="utf-8",
    )
    application = create_app(
        {"timeout-demo": _copy_fixture(tmp_path)},
        data_root=data_root,
    )

    config = application.state.registry.model_config
    adapter = OpenAICompatibleFeatureAdapter.from_config(config)

    assert config.provider == "deepseek"
    assert config.api_key == "sk-persisted-test"
    assert config.base_url == DEFAULT_REAL_BASE_URL
    assert config.model == DEFAULT_REAL_MODEL
    assert adapter.base_url == DEFAULT_REAL_BASE_URL
    assert resolve_model("real", None, model_config=config) == DEFAULT_REAL_MODEL


def test_invalid_persistent_model_config_is_rejected_at_startup(tmp_path: Path) -> None:
    data_root = tmp_path / "service-data"
    data_root.mkdir()
    (data_root / MODEL_CONFIG_FILENAME).write_text(
        json.dumps({"api_key": "test-key", "unexpected": True}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="persistent model config"):
        create_app(
            {"timeout-demo": _copy_fixture(tmp_path)},
            data_root=data_root,
        )


class _InvalidAdapter:
    def generate(self, prompt: str, *, model: str, timeout_seconds: float, context) -> ModelCall:
        del prompt, model, timeout_seconds, context
        return ModelCall(
            '{"schema_version":"0.1.0","output_id":"bad-output",'
            '"index_id":"other-index","outcome":"insufficient_evidence",'
            '"summary":"invalid"}'
        )

    def repair(self, prompt: str, invalid_output: str, validation_error: str, *, model: str, timeout_seconds: float, context) -> ModelCall:
        del prompt, invalid_output, validation_error, model, timeout_seconds, context
        return ModelCall(
            '{"schema_version":"0.1.0","output_id":"bad-output",'
            '"index_id":"other-index","outcome":"insufficient_evidence",'
            '"summary":"invalid"}'
        )


def test_invalid_model_references_are_saved_as_failure_and_do_not_replace_previous_success(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    adapter_state = {"invalid": False}
    application = create_app(
        {"timeout-demo": repository},
        feature_adapter_factory=lambda _mode: _InvalidAdapter() if adapter_state["invalid"] else FixtureFeatureAdapter(),
    )
    project_id, index_id = _create_index(application)
    status, successful = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {"schema_version": "0.1.0", "mode": "fixture", "idempotency_key": "good-output"},
    )
    assert status == 200
    assert successful["generation"]["status"] in {"succeeded", "partial"}

    adapter_state["invalid"] = True
    status, failed = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "fixture",
            "prompt_version": "feature-summary-v2",
            "idempotency_key": "bad-output",
        },
    )
    assert status == 200
    assert failed["generation"]["status"] == "failed"
    assert failed["generation"]["error"]["kind"] == "invalid_model_output"

    status, cached = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {"schema_version": "0.1.0", "mode": "fixture"},
    )
    assert status == 200
    assert cached == successful


def test_feature_cache_includes_budget_and_idempotency_scope_is_explicit(tmp_path: Path) -> None:
    application = create_app({"timeout-demo": _copy_fixture(tmp_path)})
    project_id, index_id = _create_index(application)
    status, successful = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {"schema_version": "0.1.0", "mode": "fixture", "idempotency_key": "budget-success"},
    )
    assert status == 200

    status, narrow = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "fixture",
            "budget": {"max_tool_calls": 3},
        },
    )
    assert status == 200
    assert narrow["generation"]["cache_key"] != successful["generation"]["cache_key"]
    assert narrow["generation"]["status"] == "insufficient_evidence"

    status, conflict = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "fixture",
            "prompt_version": "feature-summary-v2",
            "idempotency_key": "budget-success",
        },
    )
    assert status == 409
    assert conflict["code"] == "conflict"


def test_configured_real_adapter_uses_openai_compatible_json_protocol(tmp_path: Path) -> None:
    requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            requests.append({"path": self.path, "headers": dict(self.headers), "payload": payload})
            prompt = payload["messages"][-1]["content"]
            index_id = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("index_id=")
            )
            content = json.dumps(
                {
                    "schema_version": "0.1.0",
                    "output_id": "local-model-output",
                    "index_id": index_id,
                    "outcome": "insufficient_evidence",
                    "summary": "Local protocol fixture returned no semantic feature.",
                }
            )
            response = json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data_root = tmp_path / "protocol-data"
        data_root.mkdir()
        (data_root / MODEL_CONFIG_FILENAME).write_text(
            json.dumps(
                {
                    "provider": "deepseek",
                    "api_key": "local-test-key",
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "model": "local-test-model",
                }
            ),
            encoding="utf-8",
        )
        application = create_app(
            {"timeout-demo": _copy_fixture(tmp_path)},
            data_root=data_root,
        )
        project_id, index_id = _create_index(application)
        status, generated = request(
            application,
            "POST",
            f"/api/projects/{project_id}/indexes/{index_id}/features",
            {
                "schema_version": "0.1.0",
                "mode": "real",
                "idempotency_key": "local-real-one",
            },
        )
        assert status == 200
        generation = generated["generation"]
        assert generation["status"] == "insufficient_evidence"
        assert generation["model"] == "local-test-model"
        assert generation["usage"]["input_tokens"] == 11
        assert generation["usage"]["output_tokens"] == 7
        assert len(requests) == 1
        assert requests[0]["path"] == "/v1/chat/completions"
        assert requests[0]["headers"]["Authorization"] == "Bearer local-test-key"
        assert requests[0]["payload"]["model"] == "local-test-model"
        assert requests[0]["payload"]["max_tokens"] > 0
        assert "json" in requests[0]["payload"]["messages"][0]["content"]
        assert requests[0]["payload"]["response_format"] == {"type": "json_object"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_no_entry_fixture_returns_insufficient_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "no-entry"
    (repository / "package").mkdir(parents=True)
    (repository / "package" / "plain.py").write_text(
        "def main():\n    return 1\n\n"
        "def helper():\n    return main()\n",
        encoding="utf-8",
    )
    application = create_app({"no-entry": repository})
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": "No entry", "repository_id": "no-entry"},
    )
    assert status == 200
    status, index = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes",
        {"revision": "no-entry-v1", "dirty": False},
    )
    assert status == 200
    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project['project_id']}/indexes/{index['index']['index_id']}/features",
        {"schema_version": "0.1.0", "mode": "fixture"},
    )
    assert status == 200
    assert generated["generation"]["status"] == "insufficient_evidence"
    assert generated["generation"]["features"] == []
    assert any("未识别" in note for note in generated["generation"]["limitations"])
