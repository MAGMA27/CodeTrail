def reserve(sku: str, quantity: int, store) -> str:
    return store.create_reservation(sku, quantity)


def release(reservation_id: str, store) -> None:
    store.delete_reservation(reservation_id)
