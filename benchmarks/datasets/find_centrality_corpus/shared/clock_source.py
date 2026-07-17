"""Single abstraction for reading the current time, so callers never call the system clock directly."""

import time as _time

_FROZEN = None


def now():
    return _FROZEN if _FROZEN is not None else _time.time()


def freeze(moment):
    global _FROZEN
    _FROZEN = moment


def unfreeze():
    global _FROZEN
    _FROZEN = None
