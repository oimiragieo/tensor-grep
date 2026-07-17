"""Chosen rounding mode consulted at the very end of the ordered chain
that turns a raw rate into the final price a customer actually pays."""

from pricing import rate_core


def set_mode(state, mode):
    state["rounding_mode"] = mode
    return state


def get_mode(state):
    return state.get("rounding_mode", "half_up")


def mode_adjustments(state):
    return rate_core.breakdown_for(state)
