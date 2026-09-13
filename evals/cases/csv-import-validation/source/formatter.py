def format_rows(rows):
    return "\n".join(f"{row['name']} <{row['email']}>" for row in rows)
