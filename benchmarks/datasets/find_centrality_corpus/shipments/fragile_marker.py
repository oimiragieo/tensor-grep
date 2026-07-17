"""Sets a handling tag telling warehouse staff to use extra padding and
orientation care for a shipment."""

from shipments import routing_core


def mark_fragile(route):
    route["fragile"] = True
    return routing_core.apply_constraint(route, "fragile")


def is_fragile(route):
    return route.get("fragile", False)
