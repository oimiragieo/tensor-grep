"""Computes a short same-day interval during which a courier is expected
at the address for a shipment."""

from shipments import routing_core


def window_for(ready_hour):
    return (ready_hour, min(ready_hour + 2, 23))


def window_constraints(route):
    return routing_core.constraints_for(route)
