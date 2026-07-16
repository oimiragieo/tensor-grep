"""Restarts a process that stopped responding."""

_RESTART_COUNTS = {}


def supervise_process_restart(process_handle):
    count = _RESTART_COUNTS.get(process_handle, 0) + 1
    _RESTART_COUNTS[process_handle] = count
    process_handle.spawn_fresh()
    return count
