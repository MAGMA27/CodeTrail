from typing import Protocol


class GatewayPort(Protocol):
    def send(self, event_id: str, payload: bytes) -> bool:
        """Return the partner gateway's acknowledgement."""
        ...
