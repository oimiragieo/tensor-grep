"""Populates the lookup table ahead of expected demand."""


def prefetch_hot_keys(anticipated_keys, loader_fn):
    return {k: loader_fn(k) for k in anticipated_keys}
