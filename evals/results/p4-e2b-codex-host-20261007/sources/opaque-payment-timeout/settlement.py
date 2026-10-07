from ports import Ledger, PaymentGateway


def settle(payment, gateway: PaymentGateway, ledger: Ledger) -> str:
    receipt = gateway.capture(payment.provider_reference, payment.amount_cents)
    ledger.record_settlement(payment.id, receipt)
    return receipt
