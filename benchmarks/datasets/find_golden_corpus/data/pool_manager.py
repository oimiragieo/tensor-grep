"""Shares a fixed set of expensive handles across many callers."""


class _Reserve:
    def __init__(self, capacity):
        self._free = list(range(capacity))


_DEFAULT = _Reserve(8)


def lease_handle_from_reserve(reserve=_DEFAULT):
    if not reserve._free:
        raise RuntimeError("reserve exhausted")
    return reserve._free.pop()


def return_handle_to_reserve(handle_id, reserve=_DEFAULT):
    reserve._free.append(handle_id)
