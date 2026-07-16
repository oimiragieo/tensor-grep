"""Bounds how long a blocking call may run."""


class TimeoutExceededFault(Exception):
    """Raised once a call has run past its permitted duration."""


def enforce_deadline(elapsed_s, ceiling_s):
    if elapsed_s > ceiling_s:
        raise TimeoutExceededFault(f"{elapsed_s}s over {ceiling_s}s ceiling")
