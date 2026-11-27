from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli import session_daemon


def _metrics_file_path(root: Path) -> Path:
    return session_daemon._sessions_dir(root) / session_daemon._DAEMON_METRICS_FILE


def _metrics_target_hash(command: str, target: str) -> str:
    digest = hashlib.sha256(f"{command}\x00{target}".encode()).hexdigest()
    return digest[:16]


def _metrics_target_for_request(command: str, request: dict[str, Any]) -> str:
    if command in session_daemon._DAEMON_METRICS_SYMBOL_COMMANDS:
        return str(request.get("symbol", "")).strip().lower()
    if command in session_daemon._DAEMON_METRICS_QUERY_COMMANDS:
        return str(request.get("query", "")).strip().lower()
    return ""


def _utc_day_bucket(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime("%Y-%m-%d")


def _normalize_client_pid(client_pid: object) -> int | None:
    try:
        pid = int(cast(Any, client_pid))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


class _DemandMetrics:
    """In-memory, fail-open, PII-free demand counters for the tg-ledger step-0 gate.

    Guarded by its own lock (independent of ``server._request_lock`` /
    ``_response_cache_lock``) so metrics bookkeeping never contends with request dispatch or
    the response cache. Every public method is defensive: bad input degrades to a no-op rather
    than raising, because a metrics bug must never take the daemon down.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, float] = {}  # client_pid -> last_seen (monotonic)
        self._day_pid_sets: dict[str, set[int]] = {}  # day -> distinct client pids seen this run
        self._dup_lru: OrderedDict[str, float] = OrderedDict()  # target_hash -> last_seen (wall)
        self._days: dict[str, dict[str, Any]] = {}
        self._dirty = False

    def load(self, days: object) -> None:
        """Seed from a previously-persisted ``daemon_metrics.json`` (startup load-merge)."""
        with self._lock:
            self._days = session_daemon._sanitize_metrics_days(days)

    def record(self, *, command: str, client_pid: object, request: dict[str, Any]) -> None:
        if (
            command in session_daemon._DAEMON_METRICS_EXCLUDED_COMMANDS
            or not session_daemon._daemon_metrics_enabled()
        ):
            return
        now_monotonic = session_daemon.monotonic()
        now_wall = session_daemon.time.time()
        day = session_daemon._utc_day_bucket(now_wall)
        pid = session_daemon._normalize_client_pid(client_pid)
        with self._lock:
            bucket = self._days.setdefault(day, session_daemon._empty_metrics_day_bucket())
            bucket["requests"] += 1
            by_command = cast(dict[str, int], bucket["by_command"])
            by_command[command] = by_command.get(command, 0) + 1

            if pid is not None:
                stale = [
                    seen_pid
                    for seen_pid, last_seen in self._clients.items()
                    if now_monotonic - last_seen
                    > session_daemon._DAEMON_METRICS_CLIENT_WINDOW_SECONDS
                ]
                for seen_pid in stale:
                    del self._clients[seen_pid]
                self._clients[pid] = now_monotonic
                concurrent = len(self._clients)
                bucket["max_concurrent_distinct_clients"] = max(
                    cast(int, bucket["max_concurrent_distinct_clients"]), concurrent
                )
                day_pids = self._day_pid_sets.setdefault(day, set())
                if len(day_pids) < session_daemon._DAEMON_METRICS_DAY_PID_SET_CAP:
                    day_pids.add(pid)
                bucket["distinct_client_pids"] = max(
                    cast(int, bucket["distinct_client_pids"]), len(day_pids)
                )
                if concurrent >= 2:
                    bucket["overlap_events"] += 1

            if command in session_daemon._DAEMON_METRICS_EXPENSIVE_COMMANDS:
                bucket["expensive_requests"] += 1
                target = session_daemon._metrics_target_for_request(command, request)
                target_hash = session_daemon._metrics_target_hash(command, target)
                last_seen_wall = self._dup_lru.get(target_hash)
                if (
                    last_seen_wall is not None
                    and (now_wall - last_seen_wall)
                    <= session_daemon._DAEMON_METRICS_DUP_WINDOW_SECONDS
                ):
                    bucket["dup_requests"] += 1
                    dup_targets = cast(dict[str, int], bucket["dup_targets"])
                    if target_hash in dup_targets:
                        dup_targets[target_hash] += 1
                    elif len(dup_targets) < session_daemon._DAEMON_METRICS_MAX_DUP_TARGETS_PER_DAY:
                        dup_targets[target_hash] = 1
                self._dup_lru[target_hash] = now_wall
                self._dup_lru.move_to_end(target_hash)
                while len(self._dup_lru) > session_daemon._DAEMON_METRICS_DUP_LRU_MAX_ENTRIES:
                    self._dup_lru.popitem(last=False)

            self._dirty = True

    def consume_dirty(self) -> bool:
        with self._lock:
            was_dirty = self._dirty
            self._dirty = False
            return was_dirty

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if len(self._days) > session_daemon._DAEMON_METRICS_MAX_DAY_BUCKETS:
                newest_days = sorted(self._days)[-session_daemon._DAEMON_METRICS_MAX_DAY_BUCKETS :]
                self._days = {day: self._days[day] for day in newest_days}
            return copy.deepcopy(self._days)


def _write_demand_metrics(root: Path, metrics: session_daemon._DemandMetrics) -> None:
    payload = {
        "version": session_daemon._DAEMON_METRICS_SCHEMA_VERSION,
        "root": str(root),
        "days": metrics.snapshot(),
    }
    session_daemon._write_json_atomic(session_daemon._metrics_file_path(root), payload)


def _flush_demand_metrics_if_dirty(
    server: session_daemon._ThreadedSessionDaemon, *, force: bool = False
) -> None:
    dirty = server.demand_metrics.consume_dirty()
    if not (force or dirty):
        return
    try:
        session_daemon._write_demand_metrics(server.root, server.demand_metrics)
    except Exception:
        pass  # metrics persistence must never crash the daemon


def _read_demand_metrics_days(root: Path) -> dict[str, Any]:
    path = session_daemon._metrics_file_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    return days if isinstance(days, dict) else {}


def _demand_metrics_status_payload(
    root: Path, *, reference: datetime | None = None
) -> dict[str, Any]:
    """Read ``daemon_metrics.json`` from disk (works even with the daemon STOPPED) and roll the
    trailing 14 days up into a build-decision-ready summary for the tg-ledger step-0 gate.
    """
    days = session_daemon._sanitize_metrics_days(session_daemon._read_demand_metrics_days(root))
    now = reference or datetime.now(UTC)
    cutoff_14 = (now - timedelta(days=session_daemon._DAEMON_METRICS_ROLLUP_WINDOW_DAYS)).date()
    cutoff_7 = (
        now - timedelta(days=session_daemon._DAEMON_METRICS_ROLLUP_SHORT_WINDOW_DAYS)
    ).date()

    days_covered = 0
    days_with_2plus_concurrent = 0
    overlap_events_14d = 0
    dup_requests_7d = 0
    dup_requests_14d = 0
    max_distinct_client_pids_14d = 0

    for day_str, bucket in days.items():
        try:
            day_date = datetime.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day_date > now.date() or day_date < cutoff_14:
            continue
        days_covered += 1
        if cast(int, bucket["max_concurrent_distinct_clients"]) >= 2:
            days_with_2plus_concurrent += 1
        overlap_events_14d += cast(int, bucket["overlap_events"])
        dup_requests_14d += cast(int, bucket["dup_requests"])
        if day_date >= cutoff_7:
            dup_requests_7d += cast(int, bucket["dup_requests"])
        max_distinct_client_pids_14d = max(
            max_distinct_client_pids_14d, cast(int, bucket["distinct_client_pids"])
        )

    pre_gate_met = (
        days_covered > 0
        and days_with_2plus_concurrent
        >= session_daemon._DAEMON_METRICS_PRE_GATE_MIN_CONCURRENT_DAYS
        and dup_requests_14d >= session_daemon._DAEMON_METRICS_PRE_GATE_MIN_DUP_REQUESTS_14D
    )

    return {
        "window_days": session_daemon._DAEMON_METRICS_ROLLUP_WINDOW_DAYS,
        "days_covered": days_covered,
        "days_with_2plus_concurrent": days_with_2plus_concurrent,
        "overlap_events_14d": overlap_events_14d,
        "dup_requests_7d": dup_requests_7d,
        "dup_requests_14d": dup_requests_14d,
        "max_distinct_client_pids_14d": max_distinct_client_pids_14d,
        "pre_gate_met": pre_gate_met,
        "pre_gate_thresholds": {
            "min_concurrent_days": session_daemon._DAEMON_METRICS_PRE_GATE_MIN_CONCURRENT_DAYS,
            "min_dup_requests_14d": session_daemon._DAEMON_METRICS_PRE_GATE_MIN_DUP_REQUESTS_14D,
        },
    }


def _attach_demand_metrics(status: dict[str, Any], metrics_root: Path) -> dict[str, Any]:
    try:
        status["demand_metrics"] = session_daemon._demand_metrics_status_payload(metrics_root)
    except Exception:
        # Read-back must never break `tg doctor` / `tg session daemon status` (fail-open,
        # mirrors the record()-side contract).
        status["demand_metrics"] = {"error": "unavailable"}
    return status
