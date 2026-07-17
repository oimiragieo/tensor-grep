"""Holds the quantity cutoffs at which a cheaper per-unit charge
applies."""

from pricing import rate_core

_TIERS = [(1, 1.0), (10, 0.95), (100, 0.85)]


def tier_for(quantity):
    applicable = 1.0
    for cutoff, factor in _TIERS:
        if quantity >= cutoff:
            applicable = factor
    return applicable


def tier_adjustments(state):
    return rate_core.breakdown_for(state)
