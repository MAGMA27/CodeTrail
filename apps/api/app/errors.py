"""Shared, safe error details for the HTTP and MCP adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal, get_args, get_origin

from pydantic import ValidationError

from .contracts import ApiErrorCode, ErrorDetail


ERROR_CODES = frozenset(get_args(ApiErrorCode))


def safe_error_message(message: str) -> str:
    """Hide Pydantic implementation text from public error messages."""

    if re.search(r"validation error for|errors\.pydantic\.dev", message, re.IGNORECASE):
        return "invalid input"
    return message


def validation_error_details(
    error: ValidationError | list[dict[str, Any]],
    *,
    arg_annotations: Mapping[str, Any] | None = None,
) -> list[ErrorDetail]:
    """Map Pydantic/FastAPI validation issues to stable, value-safe details."""

    items = error.errors() if isinstance(error, ValidationError) else error
    mapping = {
        "missing": "required", "literal_error": "not_allowed", "enum": "not_allowed",
        "greater_than": "too_small", "greater_than_equal": "too_small",
        "less_than": "too_large", "less_than_equal": "too_large",
        "string_too_short": "too_short", "too_short": "too_short",
        "string_too_long": "too_long", "too_long": "too_long",
    }
    details: list[ErrorDetail] = []
    for item in items:
        location = tuple(item.get("loc", ()))
        if location and str(location[0]) in {"query", "body", "path"}:
            location = location[1:]
        field_path = ".".join(str(part) for part in location if part != "") or "arguments"
        error_type = str(item.get("type", ""))
        problem = mapping.get(error_type)
        if problem is None:
            problem = "wrong_type" if error_type.endswith(("_type", "_parsing")) else "invalid"

        annotation = (arg_annotations or {}).get(str(location[0])) if location else None
        union_args = get_args(annotation)
        non_nullable_args = [part for part in union_args if part is not type(None)]
        if len(non_nullable_args) == 1 and len(non_nullable_args) != len(union_args):
            annotation = non_nullable_args[0]
        if any(isinstance(part, int) for part in location[1:]):
            annotation_args = get_args(annotation)
            if annotation_args:
                annotation = annotation_args[0]
        if get_origin(annotation) is Literal:
            allowed = list(get_args(annotation))
        elif isinstance(annotation, type) and issubclass(annotation, Enum):
            allowed = [part.value for part in annotation]
        else:
            allowed = None

        ctx = item.get("ctx") or {}
        if allowed is None and error_type == "literal_error" and ctx.get("expected"):
            expected = str(ctx["expected"])
            allowed = [
                part.strip().strip("\"'")
                for part in re.split(r"\s*,\s*|\s+or\s+", expected)
                if part.strip().strip("\"'")
            ]
        minimum = ctx.get("ge", ctx.get("gt", ctx.get("min_length")))
        maximum = ctx.get("le", ctx.get("lt", ctx.get("max_length")))
        raw = item.get("input")
        sensitive_field = any(
            marker in field_path.lower()
            for marker in ("key", "token", "secret", "password", "url", "content", "body")
        )
        body_field = bool(item.get("loc")) and str(tuple(item.get("loc", ()))[0]) == "body"
        safe_scalar = isinstance(raw, (bool, int, float))
        safe_empty_string = isinstance(raw, str) and raw == ""
        safe_numeric_string = False
        if isinstance(raw, str) and len(raw) <= 80:
            try:
                int(raw)
                safe_numeric_string = True
            except ValueError:
                try:
                    float(raw)
                    safe_numeric_string = True
                except ValueError:
                    pass
        safe_short_literal = problem == "not_allowed" and isinstance(raw, str) and len(raw) <= 80
        if problem == "required" or body_field or sensitive_field:
            got = None
        elif safe_scalar or safe_empty_string or safe_numeric_string or safe_short_literal:
            got = str(raw)
        else:
            got = None
        details.append(ErrorDetail(
            field=field_path,
            problem=problem,
            allowed=allowed,
            min=minimum,
            max=maximum,
            got=got,
        ))
    return details
