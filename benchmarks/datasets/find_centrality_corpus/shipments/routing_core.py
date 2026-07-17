"""Decides the physical path a shipment takes.

Weighs eligible carriers on cost, lays out the sequence of regional
waypoints it will pass through, folds in any handling constraints that
apply, and locks in the route before the shipment leaves the yard.
"""

from shared import audit_sink, clock_source, id_factory


def eligible_carriers(catalog, zone):
    return [carrier for carrier, zones in catalog.items() if zone in zones]


def cheapest_of(carriers, rates):
    return min(carriers, key=lambda carrier: rates.get(carrier, float("inf")))


def add_waypoint(route, waypoint):
    route.setdefault("waypoints", []).append(waypoint)
    return route


def waypoints_for(route):
    return list(route.get("waypoints", []))


def apply_constraint(route, name):
    route.setdefault("constraints", []).append(name)
    return route


def constraints_for(route):
    return list(route.get("constraints", []))


def lock_route(route):
    route["locked"] = True
    route["locked_at"] = clock_source.now()
    route_id = route.get("shipment_id") or id_factory.generate_id("route")
    audit_sink.record("route_locked", route_id)
    return route


def is_locked(route):
    return route.get("locked", False)


def route_summary(route):
    return {"waypoints": waypoints_for(route), "constraints": constraints_for(route)}


def reset_route(route):
    route["waypoints"] = []
    route["constraints"] = []
    route["locked"] = False
    return route
