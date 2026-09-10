"""Primary handler module."""

from __future__ import annotations

from .client import Client


def handle(client: Client) -> dict[str, object]:
    """Return a deterministic result for the primary handler."""

    return {
        "handler": "primary",
        "endpoint": client.endpoint,
        "timeout": client.timeout,
    }
