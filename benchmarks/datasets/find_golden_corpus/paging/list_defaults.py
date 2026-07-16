"""Baseline cap for any unbounded output batch."""

STANDARD_BATCH_LIMIT = 25


def normalize_requested_limit(requested):
    return requested if requested and requested > 0 else STANDARD_BATCH_LIMIT
