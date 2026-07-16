"""Bounds on file transfer volume for a single account."""

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def remaining_quota(used_bytes):
    return max(0, MAX_ATTACHMENT_BYTES - used_bytes)
