"""Stores and validates the short display nickname shown on a person's
single profile -- the one profile every login is linked back to, no matter
which provider issued it."""

from accounts import identity_core


def set_nickname(record, nickname):
    record["nickname"] = nickname
    return record


def get_nickname(record):
    return record.get("nickname", "")


def nickname_owner_status(record):
    return identity_core.status_of(record)
