from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def get_or_load(cache: dict[str, T], key: str, load: Callable[[], T]) -> T:
    """Return the stored value for key, loading it when absent."""
    value = cache.get(key)
    if not value:
        value = load()
        cache[key] = value
    return value
