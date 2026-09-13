import requests


def fetch_invoice(endpoint, account_id):
    return requests.get(f"{endpoint}/invoices/{account_id}")
