"""Simple named counters for internal telemetry."""

_COUNTS = {}


def increment_named_counter(counter_name, amount=1):
    _COUNTS[counter_name] = _COUNTS.get(counter_name, 0) + amount
    return _COUNTS[counter_name]
