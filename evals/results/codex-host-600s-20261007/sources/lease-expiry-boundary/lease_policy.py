from collections.abc import Callable


def lease_is_active(now: int, expires_at: int) -> bool:
    return now <= expires_at


def authorize_action(now: int, expires_at: int, action: Callable[[], bool]) -> bool:
    if not lease_is_active(now, expires_at):
        return False
    return action()
