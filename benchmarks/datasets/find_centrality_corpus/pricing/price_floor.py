"""Caps how far combined adjustments may reduce an item's rate below its
configured lower bound."""

from pricing import rate_core


def clamp(total, lower_bound):
    return total if total > lower_bound else lower_bound


def floor_adjustments(state):
    return rate_core.breakdown_for(state)
