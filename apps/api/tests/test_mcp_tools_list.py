"""MCP output schema size and structured-result compatibility guard."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator

from app.contracts import (
    AppendSessionMessageRequest,
    CreateProjectIndexRequest,
    CreateProjectRequest,
    CreateRunRequest,
    CreateSessionRequest,
    Issue,
)
from app.main import create_app
from app.mcp_server import _slim_output_schema, create_mcp_server


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "fixtures" / "repos" / "timeout-demo"


def _schema_size(schema: dict) -> int:
    return len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _refs(schema: object) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            found.add(ref)
        for value in schema.values():
            found.update(_refs(value))
    elif isinstance(schema, list):
        for value in schema:
            found.update(_refs(value))
    return found


def _validate_refs(schema: dict) -> None:
    for ref in _refs(schema):
        assert ref.startswith("#/$defs/"), f"Unexpected output schema ref: {ref}"
        name = ref.removeprefix("#/$defs/")
        assert name in schema.get("$defs", {}), f"Unresolved output schema ref: {ref}"


def _schema_metadata_keys(schema: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(schema, dict):
        keys.update(
            key
            for key in ("title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly", "$comment")
            if key in schema
        )
        for key in ("$defs", "properties", "patternProperties", "dependentSchemas"):
            if isinstance(schema.get(key), dict):
                for child in schema[key].values():
                    keys.update(_schema_metadata_keys(child))
        for key in ("anyOf", "oneOf", "allOf", "items", "additionalProperties", "contains", "not", "if", "then", "else"):
            if key in schema:
                keys.update(_schema_metadata_keys(schema[key]))
    elif isinstance(schema, list):
        for child in schema:
            keys.update(_schema_metadata_keys(child))
    return keys


def test_registered_tool_output_schemas_are_compact_and_validate_fixture_results(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "timeout-demo"
    shutil.copytree(FIXTURE_ROOT, repository, ignore=shutil.ignore_patterns("__pycache__", ".codetrail"))
    preferences_repository = tmp_path / "preferences-demo"
    shutil.copytree(
        ROOT / "fixtures" / "repos" / "preferences-demo",
        preferences_repository,
        ignore=shutil.ignore_patterns("__pycache__", ".codetrail"),
    )
    application = create_app(
        {"timeout-demo": repository, "preferences-demo": preferences_repository},
        data_root=tmp_path / "data",
    )
    service = application.state.workspace_service
    project = service.create_project(CreateProjectRequest(name="MCP schema fixture", repository_id="timeout-demo"))
    created = service.create_project_index(
        project.project_id,
        CreateProjectIndexRequest(revision="mcp-schema-fixture-v1", dirty=False),
    )
    index_id = created.index.index_id
    journey_project = service.create_project(
        CreateProjectRequest(name="Journey schema fixture", repository_id="preferences-demo"),
    )
    journey_index = service.create_project_index(
        journey_project.project_id,
        CreateProjectIndexRequest(revision="mcp-journey-schema-v1", dirty=False),
    )
    journey_catalog = service.get_journey_catalog(journey_project.project_id, journey_index.index.index_id)
    assert journey_catalog.journeys
    journey_id = journey_catalog.journeys[0].journey_id
    with application.state.registry.store_for_repository("timeout-demo") as store:
        relation = store.load_index(index_id)
    assert relation is not None

    server = create_mcp_server(service)

    async def exercise() -> None:
        tools = await server.list_tools()
        sizes = {tool.name: _schema_size(tool.output_schema or {}) for tool in tools}
        for name, size in sorted(sizes.items(), key=lambda item: item[1], reverse=True):
            print(f"{name}: outputSchema={size} B")
        total_size = sum(sizes.values())
        print(f"total outputSchema={total_size} B across {len(tools)} tools")
        assert len(tools) == 27
        assert total_size < 180_000

        schemas = {tool.name: tool.output_schema for tool in tools}
        for name, schema in schemas.items():
            assert schema is not None
            _validate_refs(schema)
            Draft202012Validator.check_schema(schema)
            assert schema.get("type") == "object" and schema.get("properties"), name
            assert schema.get("additionalProperties") is False, name
            assert not _schema_metadata_keys(schema), f"Output schema metadata was not stripped: {name}"

        async def call(name: str, *, expect_ok: bool = True, **arguments: object) -> dict:
            response = await server.call_tool(name, arguments)
            assert response.structured_content is not None, name
            assert response.structured_content.get("ok") is expect_ok, (name, response.structured_content)
            errors = list(Draft202012Validator(schemas[name]).iter_errors(response.structured_content))
            def details(error):
                return [(list(item.absolute_path), item.message) for item in error.context] + [
                    nested for item in error.context for nested in details(item)
                ]
            assert not errors, (name, [details(error) for error in errors])
            return response.structured_content

        project_id = project.project_id
        await call("list_projects")
        await call("get_project", project_id=project_id)
        await call("list_indexes", project_id=project_id)
        await call("get_index", project_id=project_id, index_id=index_id)
        await call("check_index_freshness", project_id=project_id, index_id=index_id)
        await call("list_feature_generations", project_id=project_id, index_id=index_id)
        await call("list_sessions", project_id=project_id)
        session = service.create_session(project_id, CreateSessionRequest(topic="schema validation", index_id=index_id)).session
        await call("get_session", project_id=project_id, session_id=session.session_id)
        service.append_session_message(
            project_id,
            session.session_id,
            AppendSessionMessageRequest(source="human", content="Inspect load_settings."),
        )
        await call("list_session_messages", project_id=project_id, session_id=session.session_id)
        run = service.create_run(
            project_id,
            session.session_id,
            CreateRunRequest(
                index_id=index_id,
                issue=Issue(id="schema-fixture-issue", title="Fixture run for schema validation", body="Check load_settings."),
                mode="fixture",
                fixture_scenario="success-timeout-missing",
                idempotency_key="schema-fixture-run",
            ),
        )
        for _ in range(100):
            status = service.get_run(project_id, session.session_id, run.run_id).run.status
            if status in {"succeeded", "failed", "cancelled", "interrupted", "timed_out"}:
                break
            await asyncio.sleep(0.02)
        assert status == "succeeded"
        await call("list_session_runs", project_id=project_id, session_id=session.session_id)
        await call("get_session_run", project_id=project_id, session_id=session.session_id, run_id=run.run_id)
        await call("list_run_events", project_id=project_id, session_id=session.session_id, run_id=run.run_id)
        await call("list_run_tool_results", project_id=project_id, session_id=session.session_id, run_id=run.run_id)
        report_response = await call("get_run_report", project_id=project_id, session_id=session.session_id, run_id=run.run_id)
        report = report_response["report"]
        impact_scope = (
            {"candidate_id": report["candidate_links"][0]["candidate_id"]}
            if report["candidate_links"]
            else {"finding_id": report["findings"][0]["finding_id"]}
        )
        await call(
            "get_run_impact", project_id=project_id, session_id=session.session_id,
            run_id=run.run_id, **impact_scope,
        )
        await call(
            "get_run_impact", project_id=project_id, session_id=session.session_id,
            run_id=run.run_id, detail="full", **impact_scope,
        )

        overview = await call("get_overview", project_id=project_id, index_id=index_id)
        await call("get_overview", project_id=project_id, index_id=index_id, detail="full")
        skeleton = overview["skeleton"]
        module_id = skeleton["modules"][0]["module_id"]
        section = next(item["section"] for item in skeleton["sections"] if item["returned"])
        await call("list_overview_items", project_id=project_id, index_id=index_id, section=section)
        await call("get_module", project_id=project_id, index_id=index_id, module_id=module_id)

        summary_search = await call("search_code", project_id=project_id, index_id=index_id, query="load_settings")
        await call("search_code", project_id=project_id, index_id=index_id, query="load_settings", detail="full")
        symbol_id = summary_search["result"]["matches"][0]["symbol"]["id"]
        await call(
            "list_module_symbols", project_id=project_id, index_id=index_id, module_id=module_id,
        )
        await call(
            "list_module_symbols", project_id=project_id, index_id=index_id, module_id=module_id, detail="full",
        )
        await call(
            "get_neighbors", project_id=project_id, index_id=index_id, symbol_id=symbol_id,
            direction="callers", depth=1,
        )
        await call(
            "get_neighbors", project_id=project_id, index_id=index_id, symbol_id=symbol_id,
            direction="callers", depth=1, detail="full",
        )
        await call("list_unresolved", project_id=project_id, index_id=index_id, symbol_id=symbol_id)
        await call(
            "read_source", project_id=project_id, index_id=index_id,
            path="timeout_demo/config.py", start_line=1, end_line=8,
        )
        await call("get_journey_catalog", project_id=project_id, index_id=index_id)
        await call(
            "get_journey", expect_ok=False,
            project_id=project_id, index_id=index_id, journey_id="journey-not-in-fixture",
        )
        await call(
            "get_journey", expect_ok=False,
            project_id=project_id, index_id=index_id,
            journey_id="journey-not-in-fixture", detail="full",
        )
        await call(
            "get_journey",
            project_id=journey_project.project_id,
            index_id=journey_index.index.index_id,
            journey_id=journey_id,
        )
        await call(
            "get_journey",
            project_id=journey_project.project_id,
            index_id=journey_index.index.index_id,
            journey_id=journey_id,
            detail="full",
        )
        await call(
            "get_feature_generation", expect_ok=False,
            project_id=project_id, index_id=index_id, generation_id="generation-not-in-fixture",
        )
        await call(
            "get_feature_graph", expect_ok=False,
            project_id=project_id, index_id=index_id, generation_id="generation-not-in-fixture",
        )

    asyncio.run(exercise())


def test_slim_output_schema_deduplicates_equivalent_definitions_and_rewrites_refs() -> None:
    schema = {
        "$defs": {
            "First": {
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["a", "b"]}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "Second": {
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["a", "b"]}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
        "type": "object",
        "properties": {
            "first": {"$ref": "#/$defs/First"},
            "second": {"$ref": "#/$defs/Second"},
        },
        "required": ["first", "second"],
        "additionalProperties": False,
    }
    compact = _slim_output_schema(schema)
    assert set(compact["$defs"]) == {"First"}
    assert compact["properties"]["second"]["$ref"] == "#/$defs/First"
    _validate_refs(compact)

    original_validator = Draft202012Validator(schema)
    compact_validator = Draft202012Validator(compact)
    samples = [
        {"first": {"value": "a"}, "second": {"value": "b"}},
        {"first": {"value": "invalid"}, "second": {"value": "a"}},
        {"first": {"value": "a", "extra": True}, "second": {"value": "b"}},
    ]
    assert [original_validator.is_valid(sample) for sample in samples] == [
        compact_validator.is_valid(sample) for sample in samples
    ]


def test_slim_output_schema_traverses_only_schema_positions() -> None:
    shared_type = {"type": "string", "enum": ["first", "second"]}
    shared_enum = {"type": "integer", "minimum": 1, "maximum": 4}
    schema = {
        "$defs": {
            "First": {
                "type": "object",
                "properties": {"type": shared_type, "enum": shared_enum},
                "required": ["type", "enum"],
                "additionalProperties": False,
            },
            "Second": {
                "type": "object",
                "properties": {"type": shared_type, "enum": shared_enum, "extra": {"type": "boolean"}},
                "required": ["type", "enum", "extra"],
                "additionalProperties": False,
            },
        },
        "type": "object",
        "properties": {
            "first": {"$ref": "#/$defs/First"},
            "second": {"$ref": "#/$defs/Second"},
            "enum_value": {"enum": [{"type": "string", "enum": ["literal"]}]},
        },
        "required": ["first", "second", "enum_value"],
        "additionalProperties": False,
    }

    compact = _slim_output_schema(schema)
    assert set(compact["properties"]) == {"first", "second", "enum_value"}
    assert compact["properties"]["enum_value"]["enum"] == [{"type": "string", "enum": ["literal"]}]
    for ref in (compact["properties"]["first"]["$ref"], compact["properties"]["second"]["$ref"]):
        definition = compact["$defs"][ref.removeprefix("#/$defs/")]
        properties = definition["properties"]
        assert set(properties) >= {"type", "enum"}
        assert properties["type"].get("$ref")
        assert properties["enum"].get("$ref")
    _validate_refs(compact)

    samples = [
        {
            "first": {"type": "first", "enum": 2},
            "second": {"type": "second", "enum": 3, "extra": True},
            "enum_value": {"type": "string", "enum": ["literal"]},
        },
        {
            "first": {"type": "rejected", "enum": 2},
            "second": {"type": "second", "enum": 3, "extra": True},
            "enum_value": {"type": "string", "enum": ["literal"]},
        },
    ]
    original_validator = Draft202012Validator(schema)
    compact_validator = Draft202012Validator(compact)
    assert [original_validator.is_valid(sample) for sample in samples] == [
        compact_validator.is_valid(sample) for sample in samples
    ]
