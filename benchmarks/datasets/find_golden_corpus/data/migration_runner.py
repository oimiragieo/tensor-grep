"""Applies pending storage schema changes in order."""


def apply_pending_schema_change(pending_versions, applied_versions):
    return [v for v in pending_versions if v not in applied_versions]
