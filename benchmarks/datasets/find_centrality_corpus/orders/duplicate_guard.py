"""Rejects a second case opened with an identical fingerprint inside a
brief interval, guarding against a flaky retry that would otherwise double
up on one submission."""

from orders import fulfillment_core

_RECENT = {}


def seen_recently(fingerprint, moment):
    last = _RECENT.get(fingerprint)
    return last is not None and moment - last < 30


def remember(fingerprint, moment):
    _RECENT[fingerprint] = moment


def guard_stage(case):
    return fulfillment_core.current_stage(case)
