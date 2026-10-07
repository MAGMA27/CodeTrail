from inventory import release, reserve


class PaymentDeclined(Exception):
    pass


def place_order(order, store, charge) -> str:
    reservation_id = reserve(order.sku, order.quantity, store)
    try:
        charge(order.customer_id, order.total_cents)
    except PaymentDeclined:
        release(order.sku, store)
        raise
    return reservation_id
