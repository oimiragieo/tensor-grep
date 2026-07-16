"""A bounded in-memory lookup table."""

_ORDER = []
_VALUES = {}


def evict_oldest_slot():
    if not _ORDER:
        return None
    oldest_key = _ORDER.pop(0)
    return _VALUES.pop(oldest_key, None)


def remember(key, value):
    _ORDER.append(key)
    _VALUES[key] = value
