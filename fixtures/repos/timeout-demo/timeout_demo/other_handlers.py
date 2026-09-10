"""Secondary handler module with an intentionally identical function name."""

from __future__ import annotations

from .client import Client


def handle(client: Client) -> dict[str, object]:
    """Return a deterministic result for the secondary handler."""

    return {
        "handler": "secondary",
        "endpoint": client.endpoint,
        "timeout": client.timeout,
    }
