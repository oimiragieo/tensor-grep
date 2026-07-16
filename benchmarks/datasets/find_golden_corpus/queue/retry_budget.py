"""Caps how many times a work item may be re-attempted."""

MAX_RETRY_CEILING = 3


def attempts_remaining(already_tried):
    return max(0, MAX_RETRY_CEILING - already_tried)
