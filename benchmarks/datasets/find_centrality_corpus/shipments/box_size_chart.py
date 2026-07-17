"""Volume-to-box-size chart referenced early in that same single decision,
before a package's route from origin to destination is set."""

from shipments import routing_core

_CHART = [(500, "S"), (2000, "M"), (8000, "L")]


def box_for(volume_cm3):
    for cutoff, size in _CHART:
        if volume_cm3 <= cutoff:
            return size
    return "XL"


def chart_constraints(route):
    return routing_core.constraints_for(route)
