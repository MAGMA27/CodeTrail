"""Guard the shared HTTP/MCP backend surface and published request limits."""

from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from pydantic import TypeAdapter

from app.main import create_app
from app.mcp_server import create_mcp_server


# None means that the MCP tool has an intentionally MCP-only workflow. Keep the
# reason beside the entry so adding/removing tools cannot silently skip review.
TOOL_HTTP_ROUTES: dict[str, tuple[str, str] | tuple[None, str] | tuple[tuple[str, str], ...]] = {
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
    "list_unresolved": ("GET", "/api/projects/{project_id}/indexes/{index_id}/unresolved"),
    "get_overview": (("GET", "/api/projects/{project_id}/indexes/{index_id}/overview"), ("GET", "/api/projects/{project_id}/indexes/{index_id}/overview/skeleton")),
    "list_overview_items": (("GET", "/api/projects/{project_id}/indexes/{index_id}/overview/sections/{section}"),),
    "get_module": (("GET", "/api/projects/{project_id}/indexes/{index_id}/overview/modules/{module_id}"),),
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
    ("get_overview", "detail"): "MCP selects the summary skeleton or full HTTP overview representation.",
}

# These defaults intentionally serve the legacy HTTP shape and compact MCP shape.
# Bounds remain compared below, while this rule ties each adapter default to contracts.py.
ADAPTER_DEFAULTS = {
    (tool, "detail"): ("HTTP_DEFAULT_DETAIL", "MCP_DEFAULT_DETAIL")
    for tool in ("search_code", "list_module_symbols", "get_neighbors", "get_journey", "get_run_impact")
} | {
    ("search_code", "limit"): ("DEFAULT_SEARCH_LIMIT", "MCP_DEFAULT_SEARCH_LIMIT"),
} | {
    (tool, "limit"): ("HTTP_DEFAULT_DISCOVERY_LIMIT", "MCP_DEFAULT_DISCOVERY_LIMIT")
    for tool in ("list_projects", "list_indexes")
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
    for tool_name, route_mapping in TOOL_HTTP_ROUTES.items():
        if len(route_mapping) == 2 and (isinstance(route_mapping[0], str) or route_mapping[0] is None):
            routes = (route_mapping,)
        else:
            routes = route_mapping
        if routes and routes[0][0] is None:
            assert routes[0][1].strip(), f"{tool_name} needs a reason for having no HTTP twin"
            continue
        operations = []
        for method, route in routes:
            operation = openapi["paths"].get(route, {}).get(method.lower())
            assert operation is not None, f"{tool_name} maps to missing OpenAPI operation {method} {route}"
            operations.append(operation)
        http_parameters = {}
        for operation in operations:
            http_parameters.update(_http_parameters(openapi, operation))
        mcp_parameters = tools[tool_name].get("properties", {})
        for parameter in sorted(set(http_parameters) & set(mcp_parameters)):
            expected = _effective_constraints(openapi, http_parameters[parameter])
            actual = _effective_constraints(openapi, mcp_parameters[parameter])
            for key in COMPARABLE_SCHEMA_KEYS:
                adapter_default = ADAPTER_DEFAULTS.get((tool_name, parameter)) if key == "default" else None
                if adapter_default is not None:
                    from app import contracts
                    http_default = getattr(contracts, adapter_default[0])
                    mcp_default = getattr(contracts, adapter_default[1])
                    assert expected.get(key) == http_default
                    assert actual.get(key) == mcp_default
                    continue
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


def test_import_request_fields_come_from_the_same_contract_aliases(tmp_path, monkeypatch) -> None:
    from app import contracts

    for flag in (
        "CODETRAIL_MCP_LOCAL_IMPORT_ENABLED",
        "CODETRAIL_MCP_GITHUB_IMPORT_ENABLED",
    ):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("CODETRAIL_GITHUB_IMPORT_ENABLED", "1")

    application = create_app({}, data_root=tmp_path / "data")
    tools = asyncio.run(
        _registered_tool_schemas(
            create_mcp_server(application.state.workspace_service)
        )
    )
    openapi = application.openapi()
    http_fields = {
        "import_local_path": _http_parameters(openapi, openapi["paths"]["/api/imports/local"]["post"]),
        "import_github_repository": _http_parameters(openapi, openapi["paths"]["/api/imports/github"]["post"]),
    }
    aliases = {
        "import_local_path": {
            "name": contracts.ImportProjectName,
            "path": contracts.ImportLocalPath,
            "revision": contracts.ImportRevision,
            "dirty": contracts.ImportDirty,
            "idempotency_key": contracts.ImportIdempotencyKey,
        },
        "import_github_repository": {
            "name": contracts.ImportProjectName,
            "url": contracts.GitHubImportUrl,
            "ref": contracts.GitHubImportRef,
            "idempotency_key": contracts.ImportIdempotencyKey,
        },
    }

    app_root = Path(__file__).resolve().parents[1] / "app"
    contract_tree = ast.parse((app_root / "contracts.py").read_text(encoding="utf-8"))
    model_nodes = {
        node.name: node
        for node in contract_tree.body
        if isinstance(node, ast.ClassDef) and node.name in {"LocalImportRequest", "GitHubImportRequest"}
    }
    expected_model_annotations = {
        "LocalImportRequest": {
            "name": "ImportProjectName", "path": "ImportLocalPath", "revision": "ImportRevision",
            "dirty": "ImportDirty",
            "idempotency_key": "ImportIdempotencyKey",
        },
        "GitHubImportRequest": {
            "name": "ImportProjectName", "url": "GitHubImportUrl", "ref": "GitHubImportRef",
            "idempotency_key": "ImportIdempotencyKey",
        },
    }
    for model_name, fields in expected_model_annotations.items():
        model_fields = {
            node.target.id: ast.unparse(node.annotation)
            for node in model_nodes[model_name].body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        for field_name, alias_name in fields.items():
            assert model_fields[field_name] == alias_name

    mcp_tree = ast.parse((app_root / "mcp_server.py").read_text(encoding="utf-8"))
    tool_nodes = {
        node.name: node
        for node in ast.walk(mcp_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"import_local_path", "import_github_repository"}
    }
    expected_mcp_annotations = {
        "import_local_path": {
            "name": "shared.ImportProjectName", "path": "shared.ImportLocalPath",
            "revision": "shared.ImportRevision", "idempotency_key": "shared.ImportIdempotencyKey",
            "dirty": "shared.ImportDirty",
        },
        "import_github_repository": {
            "name": "shared.ImportProjectName", "url": "shared.GitHubImportUrl",
            "ref": "shared.GitHubImportRef", "idempotency_key": "shared.ImportIdempotencyKey",
        },
    }
    for tool_name, fields in aliases.items():
        argument_annotations = {
            argument.arg: ast.unparse(argument.annotation)
            for argument in tool_nodes[tool_name].args.args
            if argument.annotation is not None
        }
        for field_name, alias_annotation in expected_mcp_annotations[tool_name].items():
            assert argument_annotations[field_name] == alias_annotation

    for tool_name, fields in aliases.items():
        mcp_fields = tools[tool_name]["properties"]
        for field_name, alias in fields.items():
            alias_schema = TypeAdapter(alias).json_schema()
            expected = _effective_constraints(openapi, alias_schema)
            expected.pop("default", None)  # Defaults are supplied by each transport signature.
            http_constraints = _effective_constraints(openapi, http_fields[tool_name][field_name])
            mcp_constraints = _effective_constraints(openapi, mcp_fields[field_name])
            http_constraints.pop("default", None)
            mcp_constraints.pop("default", None)
            assert http_constraints == expected
            assert mcp_constraints == expected
            assert _resolve_schema(openapi, http_fields[tool_name][field_name]).get("description") == alias_schema.get("description")
            assert _resolve_schema(openapi, mcp_fields[field_name]).get("description") == alias_schema.get("description")

    shared_defaults = {
        ("import_local_path", "revision"): contracts.DEFAULT_SOURCE_REVISION,
        ("import_local_path", "dirty"): contracts.DEFAULT_IMPORT_DIRTY,
        ("import_local_path", "idempotency_key"): contracts.DEFAULT_IMPORT_IDEMPOTENCY_KEY,
        ("import_github_repository", "ref"): contracts.DEFAULT_IMPORT_REF,
        ("import_github_repository", "idempotency_key"): contracts.DEFAULT_IMPORT_IDEMPOTENCY_KEY,
    }
    for (tool_name, field_name), expected_default in shared_defaults.items():
        http_default = _resolve_schema(openapi, http_fields[tool_name][field_name]).get("default")
        mcp_default = _resolve_schema(openapi, tools[tool_name]["properties"][field_name]).get("default")
        assert http_default == mcp_default == expected_default


async def _registered_tool_schemas(server) -> dict[str, dict]:
    return {tool.name: tool.input_schema for tool in await server.list_tools()}
