"""Marks a SKU line as pending when the warehouse cannot fill it
immediately."""

from orders import fulfillment_core


def mark_pending(case, sku_line):
    case.setdefault("pending_lines", []).append(sku_line)
    return case


def has_pending(case):
    return bool(case.get("pending_lines"))


def pending_stage(case):
    return fulfillment_core.current_stage(case)
