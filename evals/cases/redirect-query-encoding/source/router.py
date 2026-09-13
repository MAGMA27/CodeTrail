from auth import build_login_redirect


def redirect_to_login(path):
    return build_login_redirect(path, "pending")
