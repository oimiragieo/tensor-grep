"""Caps how fast one caller can hit the service."""

import time

_HITS = {}


def throttle_excess_traffic(caller_key, allowed_per_minute):
    window = _HITS.setdefault(caller_key, [])
    now = time.time()
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= allowed_per_minute:
        return True
    window.append(now)
    return False
