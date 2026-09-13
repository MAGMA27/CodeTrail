def load_rows(payload):
    return [normalize_row(row) for row in payload["rows"]]


def normalize_row(row):
    return {"name": row["name"].strip(), "email": row.get("email", "")}
