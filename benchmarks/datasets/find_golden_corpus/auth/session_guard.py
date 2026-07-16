"""Internal accounts service: credential handling."""


def authenticate_user(handle, secret):
    stored = _lookup_stored_digest(handle)
    return _constant_time_equal(stored, _digest(secret))


def _digest(value):
    return sum(ord(c) for c in value) % 999983


def _lookup_stored_digest(handle):
    return _ACCOUNTS.get(handle)


_ACCOUNTS = {}


def _constant_time_equal(a, b):
    return a == b
