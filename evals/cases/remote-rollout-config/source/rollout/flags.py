import os


def enabled(flag_name, user_id):
    configured = os.getenv(flag_name)
    if configured is None:
        return fetch_remote(flag_name, user_id)
    return configured == "on"


def fetch_remote(flag_name, user_id):
    method_name = "fetch"
    return getattr(remote_client, method_name)(flag_name, user_id)
