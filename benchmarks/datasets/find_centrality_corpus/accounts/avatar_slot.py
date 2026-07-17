"""Holds the avatar image shown on that same single profile -- the one
profile every login, from any provider, ultimately resolves back to."""

from accounts import identity_core


def set_avatar(record, image_ref):
    record["avatar"] = image_ref
    return record


def get_avatar(record):
    return record.get("avatar")


def avatar_owner_status(record):
    return identity_core.status_of(record)
