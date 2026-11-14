from typing import Any, cast


def _empty_metrics_day_bucket() -> dict[str, Any]:
    return {
        "requests": 0,
        "expensive_requests": 0,
        "distinct_client_pids": 0,
        "max_concurrent_distinct_clients": 0,
        "overlap_events": 0,
        "dup_requests": 0,
        "dup_targets": {},
        "by_command": {},
    }


def _sanitize_metrics_counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    sanitized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            sanitized[str(key)] = int(cast(Any, value))
        except (TypeError, ValueError, OverflowError):
            continue
    return sanitized


def _sanitize_metrics_days(raw: object) -> dict[str, dict[str, Any]]:
    """Defensively normalize a loaded (possibly hand-edited or stale-schema) days dict."""
    sanitized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return sanitized
    for day, bucket in raw.items():
        if not isinstance(day, str) or not isinstance(bucket, dict):
            continue
        clean = _empty_metrics_day_bucket()
        for field in (
            "requests",
            "expensive_requests",
            "distinct_client_pids",
            "max_concurrent_distinct_clients",
            "overlap_events",
            "dup_requests",
        ):
            try:
                clean[field] = int(cast(Any, bucket.get(field, 0)) or 0)
            except (TypeError, ValueError, OverflowError):
                clean[field] = 0
        clean["dup_targets"] = _sanitize_metrics_counts(bucket.get("dup_targets"))
        clean["by_command"] = _sanitize_metrics_counts(bucket.get("by_command"))
        sanitized[day] = clean
    return sanitized
