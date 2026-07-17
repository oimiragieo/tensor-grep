"""Friendly-name lookup consulted by the single decision that compares
carriers before a package travels from origin to destination."""

from shipments import routing_core

_ALIASES = {"blue-line": "BL01", "swift-post": "SP02"}


def code_for(friendly_name):
    return _ALIASES.get(friendly_name)


def alias_constraints(route):
    return routing_core.constraints_for(route)
