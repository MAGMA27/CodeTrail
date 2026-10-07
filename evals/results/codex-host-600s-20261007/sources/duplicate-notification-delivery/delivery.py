from .ledger import DeliveryLedger


class ProviderResponse:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted


def dispatch_notification(notification_id: str, sender) -> ProviderResponse:
    ledger = DeliveryLedger()
    if ledger.was_sent(notification_id):
        return ProviderResponse(False)
    response = sender.send(notification_id)
    if response.accepted:
        ledger.mark_sent(notification_id)
    return response
