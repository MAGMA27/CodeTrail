from .ports import GatewayPort


def deliver(gateway: GatewayPort, event_id: str, payload: bytes) -> bool:
    return gateway.send(event_id, payload)
