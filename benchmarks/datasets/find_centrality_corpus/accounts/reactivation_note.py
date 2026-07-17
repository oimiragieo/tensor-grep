"""Attaches a short justification the support team enters when an
inactive account record is switched back to live."""

from accounts import identity_core


def set_justification(record, text):
    record["reactivation_note"] = text
    return record


def get_justification(record):
    return record.get("reactivation_note", "")


def justification_owner_status(record):
    return identity_core.status_of(record)
