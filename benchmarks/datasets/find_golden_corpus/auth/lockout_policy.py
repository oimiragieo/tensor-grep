"""Slows down repeated bad credential guesses from one source."""

_STRIKES = {}


def trip_brute_force_lock(source_ip, strike_ceiling=5):
    count = _STRIKES.get(source_ip, 0) + 1
    _STRIKES[source_ip] = count
    return count >= strike_ceiling
