class TemporaryError(Exception):
    pass


def retry_request(send, attempts=3):
    for attempt in range(attempts):
        try:
            return send()
        except TemporaryError:
            raise
