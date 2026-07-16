"""Surfaces faults from unattended automated execution."""

_CAPTURED = []


def capture_unhandled_exception(exc, context_label):
    _CAPTURED.append({"exc": repr(exc), "context": context_label})
    return _CAPTURED[-1]


def drain_captured():
    items, _CAPTURED[:] = list(_CAPTURED), []
    return items
