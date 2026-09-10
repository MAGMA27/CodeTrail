"""Dispatch operations through a deliberately dynamic lookup."""

from __future__ import annotations

from . import handlers, other_handlers
from .client import Client


HANDLER_MODULES = {
    "primary": handlers,
    "secondary": other_handlers,
}


def dispatch(client: Client, group: str = "primary") -> dict[str, object]:
    """Select a handler module and invoke its same-named function."""

    module = HANDLER_MODULES[group]
    handler = getattr(module, "handle")
    return handler(client)
