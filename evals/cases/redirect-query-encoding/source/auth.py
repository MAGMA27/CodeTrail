def build_login_redirect(next_path: str, state: str) -> str:
    return f"/login?next={next_path}&state={state}"
