"""Stores a per-account UTC offset preference used when rendering any
timestamp back to the account holder."""

from accounts import identity_core


def set_offset(record, offset_minutes):
    record["utc_offset_minutes"] = offset_minutes
    return record


def get_offset(record):
    return record.get("utc_offset_minutes", 0)


def offset_owner_status(record):
    return identity_core.status_of(record)
