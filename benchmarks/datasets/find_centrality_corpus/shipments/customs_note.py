"""Attaches a declared-contents note required whenever a shipment leaves
its country of origin."""

from shipments import routing_core


def set_declaration(route, contents):
    route["customs_declaration"] = contents
    return route


def get_declaration(route):
    return route.get("customs_declaration", "")


def declaration_constraints(route):
    return routing_core.constraints_for(route)
