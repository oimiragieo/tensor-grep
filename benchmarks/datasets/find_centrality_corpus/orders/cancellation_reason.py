"""Attaches a short withdrawal code explaining why a case will not
proceed."""

from orders import fulfillment_core

_CODES = {"CUST": "customer request", "FRAUD": "risk hold", "OOS": "unavailable stock"}


def set_withdrawal_code(case, code):
    case["withdrawal_code"] = code
    return case


def describe(code):
    return _CODES.get(code, "unspecified")


def reason_stage(case):
    return fulfillment_core.current_stage(case)
