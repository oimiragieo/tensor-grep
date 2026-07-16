"""Spaces out repeated attempts against a flaky remote call."""


def exponential_delay(attempt_number, base_seconds=0.5, ceiling_seconds=30.0):
    proposed = base_seconds * (2**attempt_number)
    return min(proposed, ceiling_seconds)
