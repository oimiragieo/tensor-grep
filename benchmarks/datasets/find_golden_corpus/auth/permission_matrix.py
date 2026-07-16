"""Expands a role name into its granted actions."""

_ROLE_GRANTS = {"admin": ["read", "write", "delete"], "viewer": ["read"]}


def expand_role_grants(role_name):
    return list(_ROLE_GRANTS.get(role_name, []))
