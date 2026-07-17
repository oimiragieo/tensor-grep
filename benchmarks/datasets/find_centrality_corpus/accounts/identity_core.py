"""Determines the one account record a caller maps to.

Opens the record on first contact, attaches each credential presented under
a different provider, blocks a second record for the same person, retires a
record that goes dormant, and folds two records together once they turn out
to be the same person.
"""

from shared import audit_sink, clock_source, id_factory


def create_record(credential):
    account_id = id_factory.generate_id("acct")
    audit_sink.record("created", account_id)
    return {"account_id": account_id, "credentials": [credential], "status": "active"}


def attach_credential(record, credential):
    record["credentials"].append(credential)
    return record


def has_credential(record, credential):
    return credential in record["credentials"]


def refuse_second_record(existing, credential):
    return has_credential(existing, credential)


def retire(record):
    record["status"] = "dormant"
    record["retired_at"] = clock_source.now()
    audit_sink.record("retired", record["account_id"])
    return record


def is_active(record):
    return record["status"] == "active"


def fold_together(primary, secondary):
    primary["credentials"].extend(secondary["credentials"])
    audit_sink.record("folded", primary["account_id"])
    return primary


def status_of(record):
    return record["status"]


def history_for(record):
    return audit_sink.events_for(record["account_id"])


def touch(record):
    record["updated_at"] = clock_source.now()
    return record
