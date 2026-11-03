"""Guard the shared HTTP/MCP backend surface and published request limits."""

from __future__ import annotations

import asyncio

from app.main import create_app
from app.mcp_server import create_mcp_server


# None means that the MCP tool has an intentionally MCP-only workflow. Keep the
# reason beside the entry so adding/removing tools cannot silently skip review.
TOOL_HTTP_ROUTES: dict[str, tuple[str, str] | tuple[None, str]] = {
    "list_projects": ("GET", "/api/projects"),
    "get_project": ("GET", "/api/projects/{project_id}"),
    "list_sessions": ("GET", "/api/projects/{project_id}/sessions"),
    "get_session": ("GET", "/api/projects/{project_id}/sessions/{session_id}"),
    "list_session_messages": ("GET", "/api/projects/{project_id}/sessions/{session_id}/messages"),
    "list_indexes": ("GET", "/api/projects/{project_id}/indexes"),
    "get_index": ("GET", "/api/projects/{project_id}/indexes/{index_id}"),
    "check_index_freshness": ("POST", "/api/projects/{project_id}/indexes/{index_id}/freshness"),
    "refresh_index": ("POST", "/api/projects/{project_id}/indexes/{index_id}/refresh"),
    "search_code": ("GET", "/api/projects/{project_id}/indexes/{index_id}/search"),
    "list_module_symbols": ("GET", "/api/projects/{project_id}/indexes/{index_id}/modules/{module_id}/symbols"),
    "read_source": ("GET", "/api/projects/{project_id}/indexes/{index_id}/source"),
    "get_neighbors": ("GET", "/api/projects/{project_id}/indexes/{index_id}/neighbors"),
    "get_overview": ("GET", "/api/projects/{project_id}/indexes/{index_id}/overview"),
    "get_journey_catalog": ("GET", "/api/projects/{project_id}/indexes/{index_id}/journeys"),
    "get_journey": ("GET", "/api/projects/{project_id}/indexes/{index_id}/journeys/{journey_id}"),
    "list_feature_generations": ("GET", "/api/projects/{project_id}/features"),
    "get_feature_generation": ("GET", "/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}"),
    "get_feature_graph": ("GET", "/api/projects/{project_id}/indexes/{index_id}/features/{generation_id}/graph"),
    "list_session_runs": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs"),
    "get_session_run": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}"),
    "list_run_events": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/events"),
    "list_run_tool_results": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/tool-results"),
    "get_run_report": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/report"),
    "get_run_impact": ("GET", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/impact"),
    "start_session_run": ("POST", "/api/projects/{project_id}/sessions/{session_id}/runs"),
    "cancel_session_run": ("POST", "/api/projects/{project_id}/sessions/{session_id}/runs/{run_id}/cancel"),
    "create_session": ("POST", "/api/projects/{project_id}/sessions"),
    "append_session_message": ("POST", "/api/projects/{project_id}/sessions/{session_id}/messages"),
    "import_local_path": ("POST", "/api/imports/local"),
    "retry_local_import": ("POST", "/api/imports/{attempt_id}/retry"),
    "get_import_status": ("GET", "/api/imports/{attempt_id}"),
    "import_github_repository": ("POST", "/api/imports/github"),
    "refresh_github_source": ("POST", "/api/projects/{project_id}/indexes/{index_id}/github-refresh"),
    "retry_github_import": ("POST", "/api/imports/{attempt_id}/retry-github"),
    "get_job_status": ("GET", "/api/jobs/{job_id}"),
    "cancel_job": (None, "Unbound or project-scoped cancellation selects different HTTP paths."),
    "start_external_run": (None, "External Agent Run lifecycle is exposed through MCP."),
    "run_search_code": (None, "Records a query result inside an MCP external Run."),
    "run_read_source": (None, "Records source evidence inside an MCP external Run."),
    "run_get_neighbors": (None, "Records graph evidence inside an MCP external Run."),
    "submit_project_features": (None, "Persists host Agent feature results; HTTP generation is model-backed."),
    "submit_external_run": (None, "Persists the final result of an MCP external Run."),
    "cancel_external_run": (None, "Cancels an MCP external Run lifecycle."),
}


# Intentional surface differences are explicit and narrowly scoped.
SCHEMA_DIFFERENCES: dict[tuple[str, str, str], str] = {
    ("start_session_run", "mode", "enum"): "HTTP accepts external mode for transport validation; external Runs start through the dedicated MCP lifecycle tool.",
    ("start_session_run", "mode", "default"): "MCP defaults an omitted mode to fixture; the HTTP request requires an explicit mode.",
    ("start_session_run", "fixture_scenario", "default"): "MCP supplies a fixture-only default; the HTTP contract requires an explicit scenario in fixture mode.",
    ("cancel_session_run", "reason", "default"): "HTTP and MCP use adapter-specific cancellation labels for the same cancellation operation.",
    ("get_neighbors", "edge_kinds", "items.enum"): "MCP neighbor tools expose imports/calls traversal; HTTP's broader EdgeKind enum also contains the non-traversal contains relation.",
}

MCP_ONLY_PARAMETERS: dict[tuple[str, str], str] = {
    ("list_projects", "limit"): "MCP applies bounded discovery truncation; HTTP returns the full project list.",
    ("list_indexes", "limit"): "MCP applies bounded discovery truncation; HTTP returns the full index list.",
    ("get_overview", "max_output_bytes"): "MCP additionally bounds its serialized response size.",
}

COMPARABLE_SCHEMA_KEYS = (
    "default", "minimum", "maximum", "minLength", "maxLength", "enum",
    "items.minimum", "items.maximum", "items.minLength", "items.maxLength", "items.enum",
)


def _resolve_schema(openapi: dict, schema: dict) -> dict:
    while "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/components/schemas/"), f"Unexpected OpenAPI ref: {ref}"
        schema = openapi["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    return schema


def _http_parameters(openapi: dict, operation: dict) -> dict[str, dict]:
    parameters = {
        parameter["name"]: _resolve_schema(openapi, parameter.get("schema", {}))
        for parameter in operation.get("parameters", [])
    }
    body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body:
        body_schema = _resolve_schema(openapi, body)
        for name, schema in body_schema.get("properties", {}).items():
            resolved = _resolve_schema(openapi, schema)
            if name in {"budget", "issue"}:
                for budget_name, budget_schema in resolved.get("properties", {}).items():
                    flattened_name = "issue_id" if name == "issue" and budget_name == "id" else budget_name
                    parameters[flattened_name] = _resolve_schema(openapi, budget_schema)
            else:
                parameters[name] = resolved
    return parameters


def _effective_constraints(openapi: dict, schema: dict) -> dict[str, object]:
    """Collect constraints through nullable/union wrappers around JSON Schema values."""

    schema = _resolve_schema(openapi, schema)
    constraints = {key: schema[key] for key in COMPARABLE_SCHEMA_KEYS if key in schema}
    if "enum" in constraints:
        constraints["enum"] = sorted(constraints["enum"], key=repr)
    if schema.get("type") == "integer":
        if "exclusiveMinimum" in schema:
            constraints["minimum"] = max(
                constraints.get("minimum", int(schema["exclusiveMinimum"]) + 1),
                int(schema["exclusiveMinimum"]) + 1,
            )
        if "exclusiveMaximum" in schema:
            constraints["maximum"] = min(
                constraints.get("maximum", int(schema["exclusiveMaximum"]) - 1),
                int(schema["exclusiveMaximum"]) - 1,
            )
    if isinstance(schema.get("items"), dict):
        item_constraints = _effective_constraints(openapi, schema["items"])
        constraints.update({f"items.{key}": value for key, value in item_constraints.items()})
    for combiner in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(combiner, [])
        variant_constraints = [
            _effective_constraints(openapi, variant)
            for variant in variants
            if isinstance(variant, dict) and variant.get("type") != "null"
        ]
        for key in COMPARABLE_SCHEMA_KEYS:
            values = [variant[key] for variant in variant_constraints if key in variant]
            if not values:
                continue
            if key == "enum" and combiner in {"anyOf", "oneOf"}:
                merged = {item for value in values for item in value}
                value: object = sorted(merged, key=repr)
            elif all(value == values[0] for value in values):
                value = values[0]
            else:
                value = tuple(values)
            if key not in constraints:
                constraints[key] = value
            elif constraints[key] != value:
                constraints[key] = (constraints[key], value)
    return constraints


def test_registered_mcp_tools_have_an_explicit_http_parity_entry(tmp_path, monkeypatch) -> None:
    for flag in (
        "CODETRAIL_MCP_LOCAL_IMPORT_ENABLED",
        "CODETRAIL_MCP_GITHUB_IMPORT_ENABLED",
        "CODETRAIL_MCP_INDEX_WRITE_ENABLED",
        "CODETRAIL_MCP_SESSION_WRITE_ENABLED",
        "CODETRAIL_MCP_RUN_WRITE_ENABLED",
        "CODETRAIL_MCP_EXTERNAL_RUN_ENABLED",
        "CODETRAIL_MCP_REAL_RUN_ENABLED",
    ):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("CODETRAIL_GITHUB_IMPORT_ENABLED", "1")
    monkeypatch.setenv("CODETRAIL_REAL_RUN_ENABLED", "1")

    application = create_app({}, data_root=tmp_path / "data")
    server = create_mcp_server(application.state.workspace_service)

    async def registered_tools():
        result = await server.list_tools()
        return {tool.name: tool.input_schema for tool in result}

    tools = asyncio.run(registered_tools())
    assert set(tools) == set(TOOL_HTTP_ROUTES), (
        f"MCP registry/table mismatch: missing table entries={sorted(set(tools) - set(TOOL_HTTP_ROUTES))}; "
        f"stale table entries={sorted(set(TOOL_HTTP_ROUTES) - set(tools))}"
    )

    openapi = application.openapi()
    used_differences: set[tuple[str, str, str]] = set()
    used_mcp_only_parameters: set[tuple[str, str]] = set()
    for tool_name, (method, route_or_reason) in TOOL_HTTP_ROUTES.items():
        if method is None:
            assert route_or_reason.strip(), f"{tool_name} needs a reason for having no HTTP twin"
            continue
        operation = openapi["paths"].get(route_or_reason, {}).get(method.lower())
        assert operation is not None, f"{tool_name} maps to missing OpenAPI operation {method} {route_or_reason}"
        http_parameters = _http_parameters(openapi, operation)
        mcp_parameters = tools[tool_name].get("properties", {})
        for parameter in sorted(set(http_parameters) & set(mcp_parameters)):
            expected = _effective_constraints(openapi, http_parameters[parameter])
            actual = _effective_constraints(openapi, mcp_parameters[parameter])
            for key in COMPARABLE_SCHEMA_KEYS:
                if key not in expected and key not in actual:
                    continue
                if expected.get(key) != actual.get(key):
                    difference = (tool_name, parameter, key)
                    reason = SCHEMA_DIFFERENCES.get(difference)
                    assert reason, (
                        f"{tool_name}.{parameter} {key} mismatch: "
                        f"HTTP={expected.get(key)!r}, MCP={actual.get(key)!r}"
                    )
                    used_differences.add(difference)
        for parameter in set(mcp_parameters) - set(http_parameters):
            pair = (tool_name, parameter)
            reason = MCP_ONLY_PARAMETERS.get(pair)
            assert reason, f"{tool_name}.{parameter} is MCP-only without an explicit reason"
            used_mcp_only_parameters.add(pair)

    assert used_differences == set(SCHEMA_DIFFERENCES), (
        f"Stale schema difference allowlist entries: {sorted(set(SCHEMA_DIFFERENCES) - used_differences)}"
    )
    assert used_mcp_only_parameters == set(MCP_ONLY_PARAMETERS), (
        f"Stale MCP-only parameter allowlist entries: {sorted(set(MCP_ONLY_PARAMETERS) - used_mcp_only_parameters)}"
    )
