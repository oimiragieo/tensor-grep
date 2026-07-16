"""Isolates work items that repeatedly cannot complete."""

_PARKED = []


def quarantine_failed_item(item_payload, failure_count):
    _PARKED.append({"payload": item_payload, "attempts": failure_count})
    return len(_PARKED)
