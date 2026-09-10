"""Client construction for the timeout demo."""

from __future__ import annotations

from collections.abc import Mapping


class Client:
    """A local stand-in for a client that requires an explicit timeout."""

    def __init__(self, endpoint: str, timeout: int) -> None:
        self.endpoint = endpoint
        self.timeout = timeout


def initialize_client(settings: Mapping[str, object]) -> Client:
    """Create a client from loaded settings."""

    return Client(endpoint=str(settings["endpoint"]), timeout=int(settings["timeout"]))
