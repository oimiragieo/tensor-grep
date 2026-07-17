"""Looks up an additive amount owed for orders bound for a small set of
listed territories."""

from pricing import rate_core

_ZONES = {"remote-north": 4.50, "remote-south": 3.75}


def surcharge_for(zone_code):
    return _ZONES.get(zone_code, 0.0)


def surcharge_adjustments(state):
    return rate_core.breakdown_for(state)
