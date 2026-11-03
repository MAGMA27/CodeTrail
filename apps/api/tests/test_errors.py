"""Tests for safe, adapter-shared validation error details."""

from app.errors import validation_error_details


def test_validation_details_only_echo_explicitly_safe_values() -> None:
    details = validation_error_details(
        [
            {"loc": ("query", "base_url"), "type": "string_type", "input": "https://user:password@host.test"},
            {"loc": ("body", "provider"), "type": "literal_error", "input": "private-provider"},
            {"loc": ("query", "direction"), "type": "literal_error", "input": "up"},
            {"loc": ("query", "depth"), "type": "greater_than", "input": 5, "ctx": {"gt": 2}},
            {"loc": ("query", "depth_text"), "type": "greater_than", "input": "5", "ctx": {"gt": 2}},
            {"loc": ("query", "long_depth_text"), "type": "greater_than", "input": "1" * 81, "ctx": {"gt": 2}},
            {"loc": ("query", "limit"), "type": "greater_than", "input": 1000, "ctx": {"gt": 0}},
            {"loc": ("query", "secret_limit"), "type": "greater_than", "input": "5", "ctx": {"gt": 2}},
            {"loc": ("body", "limit"), "type": "greater_than", "input": "5", "ctx": {"gt": 2}},
            {"loc": ("query", "query"), "type": "string_too_short", "input": ""},
            {"loc": ("query", "description"), "type": "string_type", "input": "private text"},
            {"loc": ("query", "required_field"), "type": "missing"},
        ]
    )

    assert [detail.got for detail in details] == [
        None, None, "up", "5", "5", None, "1000", None, None, "", None, None,
    ]
