HANDLERS = {"email": "send_email"}


def dispatch(kind, payload):
    handler_name = HANDLERS[kind]
    return globals()[handler_name](payload)


def send_email(payload):
    return {"sent": payload}
