"""Computes a temporary reduction that applies only within the initial
thirty days of an account."""

from pricing import rate_core

_TRIAL_DAYS = 30
_TRIAL_FACTOR = 0.5


def is_within_trial(days_since_signup):
    return days_since_signup <= _TRIAL_DAYS


def trial_factor(days_since_signup):
    return _TRIAL_FACTOR if is_within_trial(days_since_signup) else 1.0


def trial_adjustments(state):
    return rate_core.breakdown_for(state)
