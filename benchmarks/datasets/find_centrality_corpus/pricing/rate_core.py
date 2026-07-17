"""Computes the amount owed for a line item.

Looks up the base rate, layers on every discount and surcharge the item
qualifies for in a fixed order, refuses to go below the configured floor,
and rounds to the target currency's smallest unit.
"""

from shared import audit_sink, clock_source, id_factory


def base_rate(sku, catalog):
    return catalog.get(sku, 0.0)


def register_adjustment(state, name, amount):
    state.setdefault("adjustments", []).append((name, amount))
    return state


def apply_adjustment(total, name, amount):
    audit_sink.record(f"adjustment:{name}", str(total))
    return total + amount


def apply_all_adjustments(total, state):
    for name, amount in state.get("adjustments", []):
        total = apply_adjustment(total, name, amount)
    return total


def enforce_floor(total, floor):
    return max(total, floor)


def round_to_currency(total, precision):
    return round(total, precision)


def compute_total(sku, catalog, state, floor, precision):
    calc_id = id_factory.generate_id("calc")
    total = base_rate(sku, catalog)
    total = apply_all_adjustments(total, state)
    total = enforce_floor(total, floor)
    total = round_to_currency(total, precision)
    audit_sink.record(f"calc:{calc_id}", str(clock_source.now()))
    return total


def breakdown_for(state):
    return list(state.get("adjustments", []))


def clear_adjustments(state):
    state["adjustments"] = []
    return state


def is_within_floor(total, floor):
    return total >= floor
