"""Hands items off to asynchronous processors."""

_PENDING = []


def enqueue_deferred_item(item_payload):
    _PENDING.append(item_payload)
    return len(_PENDING)


def drain_pending():
    items, _PENDING[:] = [*_PENDING], []
    return items
