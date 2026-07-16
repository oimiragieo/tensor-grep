"""Guards a request body against the declared field contract."""


class SchemaViolation(Exception):
    pass


def reject_nonconforming_payload(body, required_fields):
    missing = [name for name in required_fields if name not in body]
    if missing:
        raise SchemaViolation(f"missing fields: {missing}")
    return body
