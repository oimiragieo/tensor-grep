"""Stores a start and end hour during which notifications for the account
are suppressed."""

from accounts import identity_core


def set_quiet_window(record, start_hour, end_hour):
    record["quiet_hours"] = (start_hour, end_hour)
    return record


def is_quiet(record, hour):
    window = record.get("quiet_hours")
    return bool(window and window[0] <= hour < window[1])


def quiet_owner_status(record):
    return identity_core.status_of(record)
