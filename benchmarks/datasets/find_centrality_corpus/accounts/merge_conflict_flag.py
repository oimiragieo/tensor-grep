"""Marks a pair of account records as suspected duplicates awaiting
manual reconciliation."""

from accounts import identity_core


def flag_pair(record_a, record_b):
    record_a["merge_conflict_with"] = record_b["account_id"]
    return record_a


def is_flagged(record):
    return "merge_conflict_with" in record


def flagged_owner_status(record):
    return identity_core.status_of(record)
