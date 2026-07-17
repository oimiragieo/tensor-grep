"""Generates correlation identifiers used to trace a record across subsystems."""

_COUNTER = 0


def generate_id(prefix):
    return f"{prefix}-{_next_sequence()}"


def _next_sequence():
    global _COUNTER
    _COUNTER += 1
    return _COUNTER


def reset_sequence():
    global _COUNTER
    _COUNTER = 0
