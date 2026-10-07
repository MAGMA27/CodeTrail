class DeliveryLedger:
    def __init__(self) -> None:
        self._sent_ids: set[str] = set()

    def was_sent(self, notification_id: str) -> bool:
        return notification_id in self._sent_ids

    def mark_sent(self, notification_id: str) -> None:
        self._sent_ids.add(notification_id)
