"""Keep HTTP and MCP adapters dependent on contract-owned request definitions."""

from __future__ import annotations

import ast
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ERROR_CODES_FILE = APP_ROOT / "contracts.py"
ADAPTER_FILES = (APP_ROOT / "main.py", APP_ROOT / "mcp_server.py")
LIMIT_KEYWORDS = {"ge", "gt", "le", "lt", "default", "min_length", "max_length"}


def _error_codes() -> set[str]:
    tree = ast.parse(ERROR_CODES_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "ApiErrorCode":
            value = node.value
            if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Tuple):
                return {item.value for item in value.slice.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)}
            if isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant) and isinstance(value.slice.value, str):
                return {value.slice.value}
    raise AssertionError("ApiErrorCode Literal not found in contracts.py")


def test_http_and_mcp_request_bounds_use_named_contract_values() -> None:
    violations: list[str] = []
    for path in ADAPTER_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"Query", "Field"}):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg in LIMIT_KEYWORDS and isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
                    violations.append(f"{path.name}:{node.lineno} {node.func.id}({keyword.arg}={value.value!r})")
    assert not violations, "Numeric request bounds/defaults must come from app.contracts:\n" + "\n".join(violations)


def test_http_and_mcp_do_not_define_error_codes_or_details() -> None:
    error_codes = _error_codes()
    violations: list[str] = []
    for path in ADAPTER_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith(("ErrorDetail", "ErrorCode")):
                violations.append(f"{path.name}:{node.lineno} defines {node.name}")
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
                elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                duplicated = sorted(
                    item.value for item in elements
                    if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in error_codes
                )
                if duplicated:
                    violations.append(f"{path.name}:{node.lineno} Literal repeats API error code(s): {', '.join(duplicated)}")
    assert not violations, "Error codes/details belong in app.contracts:\n" + "\n".join(violations)
