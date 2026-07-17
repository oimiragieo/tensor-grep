"""Marks a case with the highest queue priority so it skips ahead of the
normal processing order."""

from orders import fulfillment_core


def expedite(case):
    case["priority"] = "highest"
    return case


def is_expedited(case):
    return case.get("priority") == "highest"


def rush_stage(case):
    return fulfillment_core.current_stage(case)
