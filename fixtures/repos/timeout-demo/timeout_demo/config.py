"""Configuration loading for the timeout demo."""

from __future__ import annotations

import os
from collections.abc import Mapping


DEFAULT_TIMEOUT = 30


def load_settings(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Load client settings from environment variables.

    Read the endpoint and optional timeout from the supplied mapping.
    Return the settings used to initialize the local client.
    """

    source = os.environ if env is None else env
    settings: dict[str, object] = {
        "endpoint": source.get("TIMEOUT_DEMO_ENDPOINT", "https://example.test"),
    }
    if "TIMEOUT_DEMO_TIMEOUT" in source:
        settings["timeout"] = int(source["TIMEOUT_DEMO_TIMEOUT"])
    return settings
