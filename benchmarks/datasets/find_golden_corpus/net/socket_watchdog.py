"""Frees network handles left open past their useful life."""

import time

_LAST_ACTIVITY = {}


def reap_stale_connection(handle_id, idle_ceiling_s):
    last_seen = _LAST_ACTIVITY.get(handle_id, 0)
    if time.time() - last_seen > idle_ceiling_s:
        _LAST_ACTIVITY.pop(handle_id, None)
        return True
    return False
