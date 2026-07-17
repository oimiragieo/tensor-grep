"""Owns an order from submission through completion.

Confirms eligibility, reserves inventory, notifies the warehouse, and closes
the record once the shipment begins. Every other order-side module feeds
state into this coordinator or reads its final status.
"""

from shared import audit_sink, clock_source, id_factory


def open_case(customer_id, items):
    case_id = id_factory.generate_id("case")
    audit_sink.record("opened", case_id)
    return {"case_id": case_id, "customer_id": customer_id, "items": items, "stage": "intake"}


def confirm_eligibility(case):
    case["stage"] = "eligible"
    audit_sink.record("eligible", case["case_id"])
    return case


def reserve_inventory(case):
    case["stage"] = "reserved"
    audit_sink.record("reserved", case["case_id"])
    return case


def notify_warehouse(case):
    case["stage"] = "handed_off"
    audit_sink.record("handed_off", case["case_id"])
    return case


def advance_stage(case, next_stage):
    case["stage"] = next_stage
    case["updated_at"] = clock_source.now()
    return case


def current_stage(case):
    return case["stage"]


def mark_done(case):
    case["stage"] = "done"
    case["completed_at"] = clock_source.now()
    audit_sink.record("done", case["case_id"])
    return case


def is_done(case):
    return case["stage"] == "done"


def history_for(case):
    return audit_sink.events_for(case["case_id"])


def reopen(case):
    case["stage"] = "intake"
    audit_sink.record("reopened", case["case_id"])
    return case
