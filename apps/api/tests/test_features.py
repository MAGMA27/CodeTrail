"""Offline O2 tests for bounded semantic feature generation."""

from __future__ import annotations

import asyncio
import http.server
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.config import MODEL_CONFIG_FILENAME
from app.feature_generation import (
    DEFAULT_REAL_BASE_URL,
    DEFAULT_REAL_MODEL,
    FEATURE_TASK_GOAL,
    FixtureFeatureAdapter,
    MODEL_OUTPUT_CONTRACT,
    MODEL_SYSTEM_PROMPT,
    ModelCall,
    OpenAICompatibleFeatureAdapter,
    resolve_model,
)
from app.contracts import FeatureGeneration, FeatureGenerationRequest
from app.feature_generation import FeatureGenerationEngine
from app.main import create_app


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


async def _request(application, method: str, target: str, payload: dict | None = None) -> tuple[int, dict]:
    parsed = urlsplit(target)
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages: list[dict] = []
    received = False
    response_complete = asyncio.Event()

    async def receive() -> dict:
        nonlocal received
        if received:
            await response_complete.wait()
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

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


def _write_named_repository(tmp_path: Path) -> Path:
    package = tmp_path / "quiet-service" / "quiet_service"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "from .preferences import read_preferences\n"
        "from .transport import open_transport\n\n"
        "def launch() -> None:\n"
        "    preferences = read_preferences()\n"
        "    open_transport(preferences)\n\n"
        "if __name__ == \"__main__\":\n"
        "    launch()\n",
        encoding="utf-8",
    )
    (package / "preferences.py").write_text(
        "def read_preferences() -> dict[str, str]:\n"
        "    return {\"region\": \"local\"}\n",
        encoding="utf-8",
    )
    (package / "transport.py").write_text(
        "def open_transport(preferences: dict[str, str]) -> str:\n"
        "    return preferences[\"region\"]\n",
        encoding="utf-8",
    )
    return package.parent


def _create_index(
    application,
    *,
    repository_id: str = "timeout-demo",
    project_name: str = "Feature project",
) -> tuple[str, str]:
    status, project = request(
        application,
        "POST",
        "/api/projects",
        {"name": project_name, "repository_id": repository_id},
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
    assert generation["rules_version"] == "python-entry-v7+http-shape-v2+architecture-v1"
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
    assert any(item["path"] == "timeout_demo/__main__.py" for item in generation["evidence"])

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


def test_feature_history_normalizes_legacy_usage_and_skips_unrecoverable_rows(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path)
    application = create_app({"timeout-demo": repository})
    project_id, index_id = _create_index(application)
    status, generated_response = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "fixture",
            "idempotency_key": "legacy-history",
        },
    )
    assert status == 200
    generation = generated_response["generation"]
    generation_id = generation["generation_id"]
    database_path = repository / ".codetrail" / "index.sqlite3"

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT result_json FROM feature_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        assert row is not None
        legacy_payload = json.loads(row[0])
        legacy_payload["usage"].pop("model_input_chars")
        legacy_payload["usage"].pop("model_output_chars")
        connection.execute(
            "UPDATE feature_generations SET result_json = ? WHERE generation_id = ?",
            (json.dumps(legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), generation_id),
        )

        unrecoverable_payload = json.loads(row[0])
        unrecoverable_id = "feature-generation-unrecoverable"
        unrecoverable_payload["generation_id"] = unrecoverable_id
        unrecoverable_payload["usage"]["model_calls"] = 2
        unrecoverable_payload["usage"].pop("model_input_chars")
        unrecoverable_payload["usage"].pop("model_output_chars")
        connection.execute(
            "INSERT INTO feature_generations "
            "(generation_id, project_id, index_id, cache_key, idempotency_key, mode, model, "
            "prompt_version, rules_version, status, created_at, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                unrecoverable_id,
                project_id,
                index_id,
                "unrecoverable-cache-key",
                None,
                unrecoverable_payload["mode"],
                unrecoverable_payload["model"],
                unrecoverable_payload["prompt_version"],
                unrecoverable_payload["rules_version"],
                unrecoverable_payload["status"],
                "9999-01-01T00:00:00+00:00",
                json.dumps(
                    unrecoverable_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        connection.commit()

    status, reread = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}",
    )
    assert status == 200
    assert reread == generated_response
    status, reread_again = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}",
    )
    assert status == 200
    assert reread_again == generated_response

    with sqlite3.connect(database_path) as connection:
        stored_payload = json.loads(
            connection.execute(
                "SELECT result_json FROM feature_generations WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()[0]
        )
    assert "model_input_chars" not in stored_payload["usage"]
    assert "model_output_chars" not in stored_payload["usage"]

    status, invalid_read = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{unrecoverable_id}",
    )
    assert status == 500
    assert invalid_read["code"] == "internal_error"
    status, history = request(
        application,
        "GET",
        f"/api/projects/{project_id}/features?index_id={index_id}&limit=40",
    )
    assert status == 200
    assert [item["generation_id"] for item in history["generations"]] == [generation_id]


def test_feature_history_paginates_and_graph_reads_saved_snapshot_without_model(tmp_path: Path) -> None:
    application = create_app(
        {
            "timeout-demo": _copy_fixture(tmp_path),
            "other-demo": _copy_fixture(tmp_path, name="other-demo"),
        }
    )
    project_id, index_id = _create_index(application)
    generation_ids: list[str] = []
    for prompt_version in ("feature-summary-v2", "feature-summary-v3"):
        status, generated = request(
            application,
            "POST",
            f"/api/projects/{project_id}/indexes/{index_id}/features",
            {
                "schema_version": "0.1.0",
                "mode": "fixture",
                "prompt_version": prompt_version,
                "idempotency_key": f"history-{prompt_version}",
            },
        )
        assert status == 200
        generation_ids.append(generated["generation"]["generation_id"])

    status, first_page = request(
        application,
        "GET",
        f"/api/projects/{project_id}/features?index_id={index_id}&limit=1",
    )
    assert status == 200
    assert first_page["project_id"] == project_id
    assert first_page["index_id"] == index_id
    assert len(first_page["generations"]) == 1
    assert first_page["generations"][0]["generation_id"] == generation_ids[-1]
    assert first_page["generations"][0]["feature_count"] > 0
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]

    status, second_page = request(
        application,
        "GET",
        f"/api/projects/{project_id}/features?index_id={index_id}&limit=1&cursor={first_page['next_cursor']}",
    )
    assert status == 200
    assert second_page["generations"][0]["generation_id"] == generation_ids[0]
    assert second_page["generations"][0]["generation_id"] != first_page["generations"][0]["generation_id"]
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None

    status, graph = request(
        application,
        "GET",
        f"/api/projects/{project_id}/indexes/{index_id}/features/{generation_ids[-1]}/graph",
    )
    assert status == 200
    snapshot = graph["graph"]
    assert snapshot["project_id"] == project_id
    assert snapshot["index_id"] == index_id
    assert snapshot["generation_id"] == generation_ids[-1]
    assert {node["kind"] for node in snapshot["nodes"]} >= {"feature", "entry_point", "module", "symbol"}
    assert {edge["origin"] for edge in snapshot["edges"]} >= {"semantic", "static"}
    node_ids = {node["node_id"] for node in snapshot["nodes"]}
    evidence_ids = {evidence["evidence_id"] for evidence in snapshot["evidence"]}
    assert all(edge["source_id"] in node_ids and edge["target_id"] in node_ids for edge in snapshot["edges"])
    assert all(evidence["index_id"] == index_id for evidence in snapshot["evidence"])
    assert all(evidence_id in evidence_ids for node in snapshot["nodes"] for evidence_id in node["evidence_ids"])
    assert any(node["kind"] == "entry_point" and node["path"] and node["start_line"] for node in snapshot["nodes"])

    other_project_id, other_index_id = _create_index(
        application,
        repository_id="other-demo",
        project_name="Other feature project",
    )
    status, other_history = request(
        application,
        "GET",
        f"/api/projects/{other_project_id}/features?index_id={other_index_id}&limit=40",
    )
    assert status == 200
    assert other_history["generations"] == []
    status, cross_graph = request(
        application,
        "GET",
        f"/api/projects/{other_project_id}/indexes/{other_index_id}/features/{generation_ids[-1]}/graph",
    )
    assert status == 404
    assert cross_graph["code"] == "not_found"


def test_success_is_converted_to_failure_if_deadline_expires_before_save(tmp_path: Path) -> None:
    application = create_app({"timeout-demo": _copy_fixture(tmp_path)})
    project_id, index_id = _create_index(application)
    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {"schema_version": "0.1.0", "mode": "fixture", "idempotency_key": "late-save"},
    )
    assert status == 200
    original = FeatureGeneration.model_validate(generated["generation"])
    late = FeatureGenerationEngine(application.state.workspace_service).fail_if_deadline_exceeded(
        original,
        started=time.monotonic() - original.budget.max_seconds - 1,
    )
    assert late.status == "failed"
    assert late.error is not None and late.error.kind == "budget_exceeded"
    assert late.features == []
    assert late.associations == []
    assert late.evidence == original.evidence
    assert late.usage == original.usage


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
    assert generation["model"] == "deepseek-v4-flash"


def test_persistent_deepseek_config_is_loaded_from_service_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "service-data"
    data_root.mkdir()
    (data_root / MODEL_CONFIG_FILENAME).write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "api_key": "sk-persisted-test",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
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
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        timeout_seconds: float,
        max_input_chars: int,
        context,
    ) -> ModelCall:
        del prompt, model, timeout_seconds, max_input_chars, context
        return ModelCall(
            '{"schema_version":"0.1.0","output_id":"bad-output",'
            '"index_id":"other-index","outcome":"insufficient_evidence",'
            '"summary":"invalid"}'
        )

    def repair(
        self,
        prompt: str,
        invalid_output: str,
        validation_error: str,
        *,
        model: str,
        timeout_seconds: float,
        max_input_chars: int,
        context,
    ) -> ModelCall:
        del prompt, invalid_output, validation_error, model, timeout_seconds, max_input_chars, context
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
            "prompt_version": "feature-summary-v3",
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
            "prompt_version": "feature-summary-v3",
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
        system_prompt = requests[0]["payload"]["messages"][0]["content"]
        user_prompt = requests[0]["payload"]["messages"][1]["content"]
        assert FEATURE_TASK_GOAL in system_prompt
        assert MODEL_OUTPUT_CONTRACT in system_prompt
        assert "FeatureModelOutput" in system_prompt
        assert "json" in system_prompt.lower()
        assert requests[0]["payload"]["response_format"] == {"type": "json_object"}
        assert generation["usage"]["model_input_chars"] == [len(system_prompt) + len(user_prompt)]
        assert generation["usage"]["model_output_chars"] == [generation["usage"]["output_chars"]]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_repair_request_repeats_contract_and_records_each_model_call(tmp_path: Path) -> None:
    requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            requests.append(payload)
            prompt = payload["messages"][-1]["content"]
            index_id = next(
                line.split("=", 1)[1]
                for line in prompt.splitlines()
                if line.startswith("index_id=")
            )
            if len(requests) == 1:
                content = json.dumps(
                    {
                        "schema_version": "0.1.0",
                        "output_id": "invalid-output",
                        "index_id": index_id,
                        "outcome": "features_found",
                        "summary": "This deliberately omits the required feature.",
                        "features": [],
                    }
                )
            else:
                content = json.dumps(
                    {
                        "schema_version": "0.1.0",
                        "output_id": "repaired-output",
                        "index_id": index_id,
                        "outcome": "insufficient_evidence",
                        "summary": "The bounded context is insufficient after repair.",
                    }
                )
            response = json.dumps(
                {"choices": [{"message": {"content": content}}]}
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
        data_root = tmp_path / "repair-data"
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
                "budget": {"max_model_calls": 2},
                "idempotency_key": "local-real-repair",
            },
        )
        assert status == 200
        generation = generated["generation"]
        assert generation["status"] == "insufficient_evidence"
        assert len(requests) == 2
        assert all(item["messages"][0]["content"] == MODEL_SYSTEM_PROMPT for item in requests)
        assert FEATURE_TASK_GOAL in requests[1]["messages"][1]["content"]
        assert "Repair the JSON" in requests[1]["messages"][1]["content"]
        assert generation["usage"]["model_calls"] == 2
        assert generation["usage"]["repair_calls"] == 1
        expected_inputs = [
            len(item["messages"][0]["content"]) + len(item["messages"][1]["content"])
            for item in requests
        ]
        assert generation["usage"]["model_input_chars"] == expected_inputs
        assert generation["usage"]["input_chars"] == sum(expected_inputs)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_repair_full_input_is_rejected_when_remaining_budget_is_too_small(tmp_path: Path) -> None:
    state = {"repair_called": False}
    invalid_output = json.dumps(
        {
            "schema_version": "0.1.0",
            "output_id": "invalid-output",
            "index_id": "wrong-index",
            "outcome": "features_found",
            "summary": "invalid output",
            "features": [],
            "padding": "x" * 35_000,
        }
    )

    class Adapter:
        def generate(
            self,
            prompt: str,
            *,
            model: str,
            timeout_seconds: float,
            max_input_chars: int,
            context,
        ) -> ModelCall:
            del model, timeout_seconds, max_input_chars, context
            return ModelCall(
                raw_output=invalid_output,
                input_chars=len(MODEL_SYSTEM_PROMPT) + len(prompt),
            )

        def repair(
            self,
            prompt: str,
            invalid_output: str,
            validation_error: str,
            *,
            model: str,
            timeout_seconds: float,
            max_input_chars: int,
            context,
        ) -> ModelCall:
            del prompt, invalid_output, validation_error, model, timeout_seconds, max_input_chars, context
            state["repair_called"] = True
            raise AssertionError("repair must not be called after the full input budget is exhausted")

    application = create_app(
        {"timeout-demo": _copy_fixture(tmp_path)},
        feature_adapter_factory=lambda _mode: Adapter(),
    )
    project_id, index_id = _create_index(application)
    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "real",
            "budget": {
                "max_model_calls": 2,
                "max_input_chars": 40_000,
                "max_output_chars": 50_000,
            },
            "idempotency_key": "repair-input-budget",
        },
    )
    assert status == 200
    generation = generated["generation"]
    assert generation["status"] == "failed"
    assert generation["error"]["kind"] == "budget_exceeded"
    assert generation["usage"]["model_calls"] == 1
    assert generation["usage"]["repair_calls"] == 0
    assert len(generation["usage"]["model_input_chars"]) == 1
    assert generation["usage"]["input_chars"] == generation["usage"]["model_input_chars"][0]
    assert generation["usage"]["output_chars"] == len(invalid_output)
    assert generation["evidence"]
    assert state["repair_called"] is False


def test_deadline_exhaustion_does_not_start_repair_or_save_success(tmp_path: Path) -> None:
    state = {"repair_called": False}

    class SlowAdapter:
        def generate(
            self,
            prompt: str,
            *,
            model: str,
            timeout_seconds: float,
            max_input_chars: int,
            context,
        ) -> ModelCall:
            del model, timeout_seconds, max_input_chars, context
            time.sleep(1.2)
            return ModelCall(
                raw_output=json.dumps(
                    {
                        "schema_version": "0.1.0",
                        "output_id": "late-output",
                        "index_id": "wrong-index",
                        "outcome": "insufficient_evidence",
                        "summary": "late",
                    }
                ),
                input_chars=len(MODEL_SYSTEM_PROMPT) + len(prompt),
            )

        def repair(
            self,
            prompt: str,
            invalid_output: str,
            validation_error: str,
            *,
            model: str,
            timeout_seconds: float,
            max_input_chars: int,
            context,
        ) -> ModelCall:
            del prompt, invalid_output, validation_error, model, timeout_seconds, max_input_chars, context
            state["repair_called"] = True
            raise AssertionError("repair must not start after the deadline")

    application = create_app(
        {"timeout-demo": _copy_fixture(tmp_path)},
        feature_adapter_factory=lambda _mode: SlowAdapter(),
    )
    project_id, index_id = _create_index(application)
    status, generated = request(
        application,
        "POST",
        f"/api/projects/{project_id}/indexes/{index_id}/features",
        {
            "schema_version": "0.1.0",
            "mode": "real",
            "budget": {"max_seconds": 1, "max_model_calls": 2},
            "idempotency_key": "deadline-no-repair",
        },
    )
    assert status == 200
    generation = generated["generation"]
    assert generation["status"] == "failed"
    assert generation["error"]["kind"] == "budget_exceeded"
    assert generation["usage"]["model_calls"] == 1
    assert generation["usage"]["repair_calls"] == 0
    assert generation["evidence"]
    assert state["repair_called"] is False


def test_context_sorting_uses_entry_distance_for_non_demo_names(tmp_path: Path) -> None:
    repository = _write_named_repository(tmp_path)
    application = create_app({"quiet-service": repository})
    project_id, index_id = _create_index(
        application,
        repository_id="quiet-service",
        project_name="Quiet service",
    )
    context = FeatureGenerationEngine(application.state.workspace_service)._collect_context(
        project_id,
        index_id,
        FeatureGenerationRequest(mode="fixture"),
        started=time.monotonic(),
    )
    names = [symbol.qualified_name for symbol in context.observed_symbols.values()]
    assert names
    assert "quiet_service.__main__.launch" in names
    assert all("timeout_demo" not in name for name in names)
    assert names.index("quiet_service.__main__.launch") < names.index(
        "quiet_service.preferences.read_preferences"
    )


def test_real_http_response_read_obeys_total_deadline(tmp_path: Path) -> None:
    requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(json.loads(self.rfile.read(length).decode("utf-8")))
            response = json.dumps(
                {"choices": [{"message": {"content": "{\"broken\":true}"}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            try:
                self.wfile.write(response[:1])
                self.wfile.flush()
                time.sleep(1.2)
                self.wfile.write(response[1:])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data_root = tmp_path / "slow-data"
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
                "budget": {"max_seconds": 1, "max_model_calls": 2},
                "idempotency_key": "slow-response-deadline",
            },
        )
        assert status == 200
        generation = generated["generation"]
        assert generation["status"] == "failed"
        assert generation["error"]["kind"] == "budget_exceeded"
        assert generation["usage"]["model_calls"] == 1
        assert generation["usage"]["repair_calls"] == 0
        assert generation["evidence"]
        assert len(requests) == 1
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
