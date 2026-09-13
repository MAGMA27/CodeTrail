from cache import ProfileCache
from repository import load_user


def get_profile(cache: ProfileCache, tenant_id: str, user_id: str):
    key = f"profile:{user_id}"
    return cache.get_or_set(key, lambda: load_user(tenant_id, user_id))
