"""Avoids re-resolving the same hostname on every outbound call."""

ADDRESS_TTL_SECONDS = 120


_RESOLVED = {}


def cached_address(hostname, resolver_fn, now_epoch):
    entry = _RESOLVED.get(hostname)
    if entry and now_epoch - entry[1] < ADDRESS_TTL_SECONDS:
        return entry[0]
    address = resolver_fn(hostname)
    _RESOLVED[hostname] = (address, now_epoch)
    return address
