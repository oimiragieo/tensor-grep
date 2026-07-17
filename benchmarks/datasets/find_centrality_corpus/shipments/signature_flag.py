"""Marks a shipment as needing a recipient's confirmation at the door
before it can be left."""

from shipments import routing_core


def require_signature(route):
    route["signature_required"] = True
    return routing_core.apply_constraint(route, "signature")


def needs_signature(route):
    return route.get("signature_required", False)
