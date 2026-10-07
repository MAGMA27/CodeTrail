from typing import Protocol


class PaymentGateway(Protocol):
    def capture(self, reference: str, amount_cents: int) -> str: ...


class Ledger(Protocol):
    def record_settlement(self, payment_id: str, receipt: str) -> None: ...
