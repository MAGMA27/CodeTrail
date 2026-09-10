"""Application entry point for the timeout demo."""

from __future__ import annotations

from collections.abc import Mapping

from .client import Client, initialize_client
from .config import load_settings
from .dispatch import dispatch


def start(
    env: Mapping[str, str] | None = None,
    handler_group: str = "primary",
) -> dict[str, object]:
    """Load settings, initialize the client, and dispatch one operation."""

    settings = load_settings(env)
    client: Client = initialize_client(settings)
    return dispatch(client, handler_group)
