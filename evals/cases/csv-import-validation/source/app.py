from formatter import format_rows
from reader import load_rows


def import_records(payload):
    rows = load_rows(payload)
    return format_rows(rows)
