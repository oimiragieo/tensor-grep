"""Placeholder coupon lookup, one link early in the same ordered chain
that turns a raw rate into the final price before a customer ever sees
it."""

from pricing import rate_core

_COUPONS = {"WELCOME10": 0.10}


def lookup(code):
    return _COUPONS.get(code, 0.0)


def coupon_adjustments(state):
    return rate_core.breakdown_for(state)
