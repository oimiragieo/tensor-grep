from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, TextIO, cast
from uuid import uuid4

from tensor_grep.cli._index_lock import atomic_write_json, index_lock, record_from_entry
from tensor_grep.cli.agent_capsule import build_agent_capsule_from_map
from tensor_grep.cli.orient_capsule import build_orient_capsule_from_map
from tensor_grep.cli.repo_map import (
    DEFAULT_AGENT_REPO_MAP_LIMIT,
    _clear_all_source_caches,
    _DeadlineBreakFlag,
    _is_repo_context_file,
    _iter_repo_files,
    _max_parse_bytes,
    _UnreadablePathFlag,
    apply_repo_map_output_limits,
    build_context_edit_plan_from_map,
    build_context_pack_from_map,
    build_context_render_from_map,
    build_file_importers_from_map,
    build_repo_map,
    build_repo_map_incremental,
    build_symbol_blast_radius_from_map,
    build_symbol_blast_radius_plan_from_map,
    build_symbol_blast_radius_render_from_map,
    build_symbol_callers_from_map,
    build_symbol_defs_from_map,
    build_symbol_impact_from_map,
    build_symbol_refs_from_map,
)
from tensor_grep.cli.session_content_reconciliation import capture_snapshot, content_digest
from tensor_grep.cli.session_provenance import (
    SessionPayloadError,
    read_session_payload,
    seal_session,
    verified_session,
)

# Project-root resolution lives in `session_root` (split out when the file-size ratchet
# refused this file's growth). Re-exported here because `session_daemon`, `ledger_store`
# and `evidence_receipt` import these names from `session_store`; moving them without the
# re-export would be an invisible break for three modules. `X as X` is the EXPLICIT
# re-export form -- mypy runs with implicit re-export disabled, so the plain spelling
# fails CI for every consumer. The aliases are load-bearing; do not tidy them away.
from tensor_grep.cli.session_root import (
    _MAX_PROJECT_ROOT_ASCENT as _MAX_PROJECT_ROOT_ASCENT,
)
from tensor_grep.cli.session_root import (
    _PROJECT_MANIFEST_MARKERS as _PROJECT_MANIFEST_MARKERS,
)
from tensor_grep.cli.session_root import (
    _SESSION_NEARBY_LOOKUP_ENV as _SESSION_NEARBY_LOOKUP_ENV,
)
from tensor_grep.cli.session_root import _carry_last_prepare, _snapshot_generation
from tensor_grep.cli.session_root import (
    _find_project_root as _find_project_root,
)
from tensor_grep.cli.session_root import (
    _index_path as _index_path,
)
from tensor_grep.cli.session_root import (
    _nearby_lookup_enabled as _nearby_lookup_enabled,
)
from tensor_grep.cli.session_root import (
    _nearby_session_roots as _nearby_session_roots,
)
from tensor_grep.cli.session_root import (
    _resolve_literal_dir as _resolve_literal_dir,
)
from tensor_grep.cli.session_root import (
    _resolve_root as _resolve_root,
)
from tensor_grep.cli.session_root import (
    _session_payload_path as _session_payload_path,
)
from tensor_grep.cli.session_root import (
    _session_root_for_payload as _session_root_for_payload,
)
from tensor_grep.cli.session_root import (
    _sessions_dir as _sessions_dir,
)
from tensor_grep.cli.session_root import (
    _shared_territory_roots as _shared_territory_roots,
)
from tensor_grep.cli.symbols_cache import collect_content_receipts

logger = logging.getLogger(__name__)

# Task #287. Windows ERROR_PATH_NOT_FOUND: a path COMPONENT did not resolve, as opposed to
# ERROR_FILE_NOT_FOUND (2), where the parent resolved and only the file is absent. Both surface as
# `FileNotFoundError`; only the winerror separates them. `getattr(exc, "winerror", None)` is None
# on POSIX, so every non-Windows platform keeps the pre-#287 behaviour unchanged -- branch on the
# ATTRIBUTE being present, never on `sys.platform`.
_WINERROR_PATH_NOT_FOUND = 3

_SESSION_VERSION = 1
_TG_DIRNAME = ".tensor-grep"
_SESSIONS_SUBDIR = "sessions"
_INDEX_FILE = "index.json"
_SESSION_SERVE_CACHE_MAX_ENTRIES = 32
_SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES = 32
_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV = "TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES"
_DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
_DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
_DEFAULT_SESSION_BLAST_RADIUS_PLAN_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
# task #94 Part A: the implicit-session default repo-file limit for the 5 symbol commands
# (defs/impact/refs/callers/blast_radius) opened via the daemon's no-explicit-session path.
# Same underlying value as the context-render/edit-plan limits above (kept as a distinct name
# for intent clarity at the call site, not a distinct behavior).
_DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
# task #108 (Tier-2 daemon capability): same underlying default as every limit above -- `orient` has no
# CLI --max-repo-files flag at all (always resolves this value), `agent`'s mirrors the
# symbol-command default. Kept as distinct names for intent clarity at the call site, not a
# distinct behavior.
_DEFAULT_SESSION_ORIENT_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
_DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT = DEFAULT_AGENT_REPO_MAP_LIMIT
# #200 (Backend Fail-Closed / scale-honesty gap): `_serve_session_request_from_payload` serves
# agent/orient/context_render/context_edit_plan from an ALREADY-CACHED repo_map, so
# build_repo_map's own --deadline (agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS on the cold
# CLI path) never applies here -- the scan already happened, possibly long ago. Before this fix
# these 4 warm-daemon branches never threaded ANY wall-clock budget into their builders (`grep -n
# deadline session_store.py` was ZERO matches), so the DEFAULT (no --deadline flag) warm-daemon
# request ran fully unbounded. Matches the cold path's own default exactly (a SAFE first cut: a
# warm response finishing inside 60s, the overwhelming common case, is unaffected; a tighter
# warm-specific value is a benchmark-gated follow-on, not this fix) so a warm request that
# genuinely runs long now comes back partial=True/exit-2 instead of blocking the daemon worker
# forever. See session_daemon.py's _DAEMON_RESPONSE_TIMEOUT_SECONDS comment, which already
# flagged this exact gap as "tracked" before this fix closed it.
WARM_DAEMON_DEFAULT_DEADLINE_SECONDS = 60.0
# audit I2: bound on-disk session retention; keep at most N newest explicit sessions per root
# so per-open cost and disk usage stay bounded. Configurable via TG_SESSION_MAX.
_SESSION_MAX_ENV = "TG_SESSION_MAX"
_DEFAULT_SESSION_MAX = 64
# audit S9: nearby-root session discovery loads payloads from parent/sibling dirs. Confine
# resolution to the explicit root by default; opt back in with TG_SESSION_NEARBY_LOOKUP=1.


@dataclass
class SessionRecord:
    version: int
    session_id: str
    root: str
    created_at: str
    file_count: int
    symbol_count: int


@dataclass
class SessionOpenResult:
    session_id: str
    root: str
    created_at: str
    file_count: int
    symbol_count: int
    refresh_type: str
    changeset: dict[str, list[str]]
    scan_limit: dict[str, Any] | None = None
    build_seconds: float | None = None


@dataclass
class SessionRefreshResult:
    session_id: str
    root: str
    refreshed_at: str
    file_count: int
    symbol_count: int
    refresh_type: str
    changeset: dict[str, list[str]]
    scan_limit: dict[str, Any] | None = None
    refresh_fallback_reason: str | None = None


class SessionStaleError(RuntimeError):
    pass


@dataclass
class _SessionServeCacheEntry:
    payload: dict[str, Any]
    size_bytes: int


@dataclass
class _SessionServeResponseCacheEntry:
    payload: dict[str, Any]
    size_bytes: int


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _configured_session_max() -> int:
    return _configured_positive_int(_SESSION_MAX_ENV, _DEFAULT_SESSION_MAX)


def _json_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class _SessionServeCache:
    def __init__(self, max_entries: int = _SESSION_SERVE_CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[tuple[str, str], _SessionServeCacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0
        self._refreshes = 0
        self._lock = threading.RLock()

    def _key(self, session_id: str, path: str) -> tuple[str, str]:
        return (str(_session_root_for_payload(session_id, path)), session_id)

    def get(self, session_id: str, path: str) -> dict[str, Any] | None:
        key = self._key(session_id, path)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            self._entries[key] = entry
            return entry.payload

    def put(self, session_id: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._key(session_id, path)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size_bytes -= previous.size_bytes

            entry = _SessionServeCacheEntry(
                payload=payload,
                size_bytes=_json_size_bytes(payload),
            )
            self._entries[key] = entry
            self._size_bytes += entry.size_bytes

            while len(self._entries) > self._max_entries:
                _, evicted = self._entries.popitem(last=False)
                self._size_bytes -= evicted.size_bytes

            return payload

    def load(self, session_id: str, path: str) -> dict[str, Any]:
        cached = self.get(session_id, path)
        if cached is not None:
            return cached
        return self.put(session_id, path, get_session(session_id, path))

    def load_with_status(self, session_id: str, path: str) -> tuple[dict[str, Any], str]:
        cached = self.get(session_id, path)
        if cached is not None:
            return cached, "hit"
        return self.put(session_id, path, get_session(session_id, path)), "miss"

    def record_refresh(self) -> None:
        with self._lock:
            self._refreshes += 1

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size_bytes

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def refreshes(self) -> int:
        with self._lock:
            return self._refreshes

    @property
    def root_count(self) -> int:
        with self._lock:
            return len({root for root, _ in self._entries})

    @property
    def sessions(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"root": root, "session_id": session_id}
                for root, session_id in self._entries.keys()
            ]


class _SessionServeResponseCache:
    def __init__(
        self,
        max_entries: int = _SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES,
        max_size_bytes: int | None = None,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._max_size_bytes = (
            _configured_positive_int(
                _SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV,
                _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
            )
            if max_size_bytes is None
            else max(1, int(max_size_bytes))
        )
        self._entries: OrderedDict[tuple[str, ...], _SessionServeResponseCacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._oversized_skips = 0

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        entry = self._entries.pop(key, None)
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries[key] = entry
        return copy.deepcopy(entry.payload)

    def put(self, key: tuple[str, ...], response: dict[str, Any]) -> None:
        self._puts += 1
        size_bytes = _json_size_bytes(response)
        if size_bytes > self._max_size_bytes:
            self._oversized_skips += 1
            return
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size_bytes -= previous.size_bytes
        entry = _SessionServeResponseCacheEntry(
            payload=copy.deepcopy(response),
            size_bytes=size_bytes,
        )
        self._entries[key] = entry
        self._size_bytes += entry.size_bytes
        while len(self._entries) > self._max_entries or self._size_bytes > self._max_size_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._size_bytes -= evicted.size_bytes

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def puts(self) -> int:
        return self._puts

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def oversized_skips(self) -> int:
        return self._oversized_skips


def _load_index(root: Path) -> list[SessionRecord]:
    index_path = _index_path(root)
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [record_from_entry(SessionRecord, entry) for entry in payload]


def _write_json_atomic(path: Path, payload: Any, *, mode: int | None = None) -> None:
    """Write ``payload`` as pretty JSON to ``path`` atomically, refusing a pre-existing symlink.

    Thin wrapper over the shared C4/#659 hardening baseline
    (`_index_lock.atomic_write_json`/`atomic_write_bytes` -- see those docstrings for the full
    precheck + same-dir-temp + fsync + os.replace (+ POSIX O_EXCL|O_NOFOLLOW) rationale). Every
    caller of this function (session payloads/index, the daemon token + metrics files via
    session_daemon, and, via the C4 fix, the evidence-receipt and review-bundle CLI/MCP writers)
    gets the same protection, uniformized (task #211) across every sibling writer in the `cli`
    package instead of being re-implemented per module.
    """
    atomic_write_json(path, payload, mode=mode)


def _write_index(root: Path, records: list[SessionRecord]) -> None:
    _write_json_atomic(_index_path(root), [asdict(record) for record in records])


def _prune_session_records(
    root: Path,
    records: list[SessionRecord],
    *,
    max_records: int | None = None,
) -> list[SessionRecord]:
    """Bound on-disk session retention (audit I2).

    Keep at most ``max_records`` newest records. Each dropped record's payload file is
    unlinked before the trimmed list is returned so disk usage stays bounded and the index
    never references a removed payload.

    M8: ``created_at`` is stamped BEFORE the caller acquires ``index_lock``, so under
    concurrent writers the insert (lock-arrival) order does not reliably match creation
    order -- trusting list position for the ``[:limit]`` cut can prune a genuinely newer
    session and keep an older one. Re-sort by ``created_at`` (newest first) immediately
    before slicing, mirroring ``list_sessions_with_discovery``.
    """
    limit = _configured_session_max() if max_records is None else max(1, int(max_records))
    if len(records) <= limit:
        return records
    ordered = sorted(records, key=lambda record: record.created_at, reverse=True)
    retained = ordered[:limit]
    for dropped in ordered[limit:]:
        try:
            _session_payload_path(root, dropped.session_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            # ValueError: a locally-tampered index.json record with a traversal-shaped
            # id is refused by _session_payload_path — skip it rather than break pruning.
            pass
    return retained


def _capture_snapshot(
    file_paths: list[str],
    *,
    unreadable_hit: _UnreadablePathFlag | None = None,
    root: Path | None = None,
    deadline_monotonic: float | None = None,
    expected: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Capture bounded content receipts; disclose unreadable or generation-racing files."""
    return capture_snapshot(
        file_paths,
        limit=_max_parse_bytes(),
        root=root,
        deadline=deadline_monotonic,
        expected=expected,
        unreadable_hit=unreadable_hit,
    )


def _snapshot_path_key(raw_path: object) -> str:
    path = Path(str(raw_path)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(path.resolve())


def _empty_changeset() -> dict[str, list[str]]:
    return {"added": [], "modified": [], "removed": []}


def _changeset_has_entries(changeset: dict[str, list[str]] | None) -> bool:
    return bool(
        changeset
        and any(changeset.get(key) for key in ("added", "modified", "removed", "unverified"))
    )


def _changeset_message(changeset: dict[str, list[str]]) -> str:
    details: list[str] = []
    for key in ("modified", "added", "removed", "unverified"):
        paths = changeset.get(key, [])
        if not paths:
            continue
        details.append(f"{key} {len(paths)}: {paths[0]}")
    if not details:
        return "cached session files changed on disk"
    return f"cached session files changed on disk ({'; '.join(details)})"


def _stale_changeset(
    payload: dict[str, Any],
    *,
    detect_added_files: bool = True,
    deadline_monotonic: float | None = None,
) -> dict[str, list[str]] | None:
    snapshot = cast(list[dict[str, Any]], payload.get("snapshot") or [])
    if not snapshot and not detect_added_files:
        return None

    root = _resolve_root(Path(str(payload.get("root", payload.get("path", ".")))))
    reconciliation_deadline = (
        deadline_monotonic
        if deadline_monotonic is not None
        else monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
    )
    snapshot_by_path = {_snapshot_path_key(entry["path"]): entry for entry in snapshot}

    added: list[str] = []
    walk_unverified: list[str] = []
    current_paths: dict[str, Path] = {}
    if detect_added_files:
        context_root = root if root.is_dir() else root.parent
        # M3 : bound the added-file probe walk to the session's
        # own recorded scan cap (or the shared default) instead of an unbounded full
        # recursive enumeration -- this is reachable from MCP on every tg_session_* call
        # with refresh_on_stale=True (_load_session_payload / refresh_session).
        probe_max_files = _effective_session_max_repo_files(None, payload)
        walk_deadline, walk_unreadable = _DeadlineBreakFlag(), _UnreadablePathFlag()
        current_files = [
            current
            for current in _iter_repo_files(
                root,
                max_files=probe_max_files,
                deadline_monotonic=reconciliation_deadline,
                deadline_hit=walk_deadline,
                unreadable_hit=walk_unreadable,
            )
            if _is_repo_context_file(current, context_root)
        ]
        current_paths = {str(current): current for current in current_files}
        added = sorted(path for path in current_paths if path not in snapshot_by_path)
        if walk_deadline.hit or walk_unreadable.hit:
            walk_unverified.append(str(root))

    removed: list[str] = []
    modified: list[str] = []
    # Accumulate indeterminate paths and emit ONE warning after the loop. Logging per file would
    # be unbounded-ish spam: `_stale_changeset` runs on EVERY session-serving request (both
    # `_load_session_payload` and `_session_health_payload`), the snapshot is capped only by
    # DEFAULT_AGENT_REPO_MAP_LIMIT (2000), and no handler is configured in this package -- so
    # Python's lastResort would put up to ~2000 lines on stderr per request.
    indeterminate: list[str] = walk_unverified
    indeterminate_kinds: set[str] = {"WalkIncomplete"} if walk_unverified else set()
    # Task #287. Entries whose stat failed with ERROR_PATH_NOT_FOUND: a path COMPONENT did not
    # resolve. Held back from `removed` until the loop ends, because one root probe decides the
    # whole batch (see the classification block after the loop).
    path_unresolved: list[str] = []
    for position, (current_path, snapshot_entry) in enumerate(snapshot_by_path.items()):
        if monotonic() >= reconciliation_deadline:
            indeterminate.extend(list(snapshot_by_path)[position:])
            indeterminate_kinds.add("ReconciliationDeadline")
            break
        try:
            stat = os.stat(current_paths.get(current_path) or current_path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            # The file is genuinely GONE (or a parent stopped being a directory) -- USUALLY. A
            # disconnected Windows UNC share raises FileNotFoundError for EVERY file under it, so
            # a dropped mount would otherwise land here and report the whole tree as removed: the
            # same false-deletion class #286 fixed, arriving through the one arm that is supposed
            # to mean "really gone".
            #
            # MEASURED (task #287; an earlier version of this comment said "winerror 53", a value
            # that NEVER OCCURS here -- do not go hunting for it):
            #     missing local file                -> winerror 2  (ERROR_FILE_NOT_FOUND)
            #     unreachable UNC host              -> winerror 3  (ERROR_PATH_NOT_FOUND)
            #     missing file on a REACHABLE share -> winerror 3
            # winerror 2 is therefore a clean deletion signal. winerror 3 is AMBIGUOUS per file --
            # it is shared by "the share is gone" and "a parent directory was removed from a live
            # share" -- so it is deferred and decided at the TREE level after the loop.
            #
            # The discriminator keys on this asymmetry: a mass delete leaves the session root
            # reachable, a dropped mount does not. On any winerror-3 entry, probe the root ONCE
            # after the loop; if the root is also unreachable, those entries are indeterminate
            # rather than removed. Design + both required control arms are on task #287.
            if getattr(exc, "winerror", None) == _WINERROR_PATH_NOT_FOUND:
                path_unresolved.append(current_path)
            else:
                removed.append(current_path)
            continue
        except OSError as exc:
            # Refuse uncertain freshness; unreadable sources are never classified as deletions.
            indeterminate.append(current_path)
            indeterminate_kinds.add(type(exc).__name__)
            continue
        if int(stat.st_size) != int(snapshot_entry["size"]) or int(stat.st_mtime_ns) != int(
            snapshot_entry["mtime_ns"]
        ):
            modified.append(current_path)
            continue
        if not snapshot_entry.get("content_sha256"):
            modified.append(current_path)  # legacy metadata-only snapshots need one refresh
            continue
        try:
            digest = content_digest(
                root, Path(current_path), _max_parse_bytes(), reconciliation_deadline
            )
        except OSError as exc:
            indeterminate.append(current_path)
            indeterminate_kinds.add(type(exc).__name__)
        else:
            if digest != snapshot_entry["content_sha256"]:
                modified.append(current_path)

    if path_unresolved:
        # Task #287, the TREE-LEVEL discriminator. Each of these failed with ERROR_PATH_NOT_FOUND,
        # which per file cannot tell "the share dropped" from "a parent directory was deleted".
        # ONE probe of the session root decides the whole batch, because the two cases differ
        # there and only there: a mass delete leaves the root reachable, a dropped mount does not.
        #
        # Cost is one stat per changeset, and only when a path-level failure actually occurred --
        # the common path pays nothing.
        try:
            os.stat(root)
            root_reachable = True
        except OSError:
            root_reachable = False
        if root_reachable:
            # The mount is alive, so a missing path component is a REAL removal (a deleted parent
            # directory is a genuine deletion of everything under it). Original behaviour.
            removed.extend(path_unresolved)
        else:
            # The root itself is gone. Reporting these as deleted would be the #286 false-deletion
            # class arriving through the "really gone" arm. Fail SAFE: indeterminate, not removed.
            indeterminate.extend(path_unresolved)
            indeterminate_kinds.add("UnreachableRoot")

    if indeterminate:
        logger.warning(
            "session staleness check could not verify %d file(s) (%s); freshness is "
            "unverified rather than removed. Sample: %s",
            len(indeterminate),
            ", ".join(sorted(indeterminate_kinds)),
            ", ".join(indeterminate[:3]),
        )

    return {
        "added": added,
        "modified": sorted(dict.fromkeys(modified)),
        "removed": removed,
        **({"unverified": indeterminate} if indeterminate else {}),
    }


def _ensure_session_not_stale(
    payload: dict[str, Any],
    *,
    detect_added_files: bool = False,
    deadline_monotonic: float | None = None,
) -> None:
    if payload.get("snapshot_unreadable_paths", {}).get("count"):
        raise SessionStaleError("session content snapshot is incomplete; refresh before reuse")
    changeset = _stale_changeset(
        payload,
        detect_added_files=detect_added_files,
        deadline_monotonic=deadline_monotonic,
    )
    if _changeset_has_entries(changeset):
        raise SessionStaleError(_changeset_message(cast(dict[str, list[str]], changeset)))


def _resolve_request_session_target(
    request: dict[str, Any], session_id: str, path: str
) -> tuple[str, str]:
    requested_session_id = str(request.get("session_id", session_id)).strip() or session_id
    requested_path = request.get("path", request.get("root", path))
    resolved_path = str(requested_path).strip() if requested_path is not None else path
    return requested_session_id, resolved_path or path


def _new_session_id(root: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"session-{timestamp}-{root.name}-{uuid4().hex[:8]}"


def _effective_session_max_repo_files(
    requested: int | None,
    payload: dict[str, Any] | None = None,
) -> int | None:
    if requested is not None:
        return max(1, int(requested))
    if payload is not None:
        scan_limit = payload.get("scan_limit")
        if isinstance(scan_limit, dict) and scan_limit.get("max_repo_files") is not None:
            try:
                return max(1, int(cast(int | str, scan_limit["max_repo_files"])))
            except (TypeError, ValueError):
                pass
    return DEFAULT_AGENT_REPO_MAP_LIMIT


def open_session(
    path: str = ".",
    *,
    max_repo_files: int | None = DEFAULT_AGENT_REPO_MAP_LIMIT,
    deadline_monotonic: float | None = None,
) -> SessionOpenResult:
    """Task #304: `deadline_monotonic` defaults to None, which is exactly today's behaviour.

    It exists because this call and the two in `refresh_session` were the only `build_repo_map`
    sites in the codebase with no time bound at all, while every symbol command already had one.
    A daemon serving a stale session therefore rebuilt the whole map unbounded, the client hit its
    own 60s timeout, and the cold path then anchored a FRESH budget -- so the caller's single
    stated deadline could be exceeded roughly twofold with no disclosure anywhere.
    """
    root = _resolve_root(Path(path), must_exist=True)
    # SCAN THE CALLER'S PATH, STORE AT THE ANCHORED ROOT. These are two different questions and
    # conflating them is a real regression: once `_resolve_root` anchors a subtree to the project
    # root (G4.1/G4.2), passing `root` here would silently widen `tg session open src` from
    # scanning 119 files to scanning 2,226 -- a ~19x blow-up, measured on this repo. A council
    # seat caught this; the plan that introduced the anchoring did not name it.
    #
    # The anchored `root` is still what locates the session store and the daemon, which is the
    # whole point of the fix. Only the SCAN scope stays where the caller pointed.
    scan_target = Path(path).expanduser().resolve()
    if not scan_target.is_dir():
        scan_target = scan_target.parent
    started_at = monotonic()
    effective_max_repo_files = _effective_session_max_repo_files(max_repo_files)
    with collect_content_receipts() as content_receipts:
        repo_map = build_repo_map(
            scan_target,
            max_repo_files=effective_max_repo_files,
            deadline_monotonic=deadline_monotonic,
        )
        built_at = monotonic()
    created_at = datetime.now(UTC).isoformat()
    session_id = _new_session_id(root)
    changeset = _empty_changeset()
    scan_limit = cast(dict[str, Any] | None, repo_map.get("scan_limit"))
    snapshot_unreadable = _UnreadablePathFlag()
    snapshot = _capture_snapshot(
        repo_map["related_paths"],
        unreadable_hit=snapshot_unreadable,
        root=root,
        deadline_monotonic=deadline_monotonic,
        expected=content_receipts,
    )
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "repo_map": repo_map,
        "snapshot": snapshot,
        "refresh_type": "full",
        "changeset": changeset,
        "scan_limit": scan_limit,
        "build_seconds": max(0.0, built_at - started_at),
        "current_generation": _snapshot_generation(snapshot),
    }
    if snapshot_unreadable.hit:
        # Task #288: mirrors `build_repo_map`'s `unreadable_paths = {count, sample}` shape (#276).
        # Emitted ONLY when it fired, so a clean capture is byte-identical to the old payload and
        # a reader can trust the key's ABSENCE to mean "the snapshot covered everything".
        payload["snapshot_unreadable_paths"] = {
            "count": snapshot_unreadable.count,
            "sample": list(snapshot_unreadable.sample),
        }
    # Create the sessions dir up front so _session_payload_path's sessions_dir.resolve() is stable:
    # under concurrent first-time opens, resolving while another writer is still mkdir-ing the dir
    # can transiently mis-resolve and trip the containment guard on a VALID session id (Windows).
    _sessions_dir(root).mkdir(parents=True, exist_ok=True)
    session_path = _session_payload_path(root, session_id)
    seal_session(payload, root)
    _write_json_atomic(session_path, payload)

    record = SessionRecord(
        version=_SESSION_VERSION,
        session_id=session_id,
        root=str(root),
        created_at=created_at,
        file_count=len(repo_map["files"]),
        symbol_count=len(repo_map["symbols"]),
    )
    # q10 RMW race: load->mutate->write must be atomic w.r.t. every other writer of this
    # index.json, cross-process and cross-thread, or a concurrent insert can be lost (see
    # _index_lock.index_lock docstring / AGENTS.md Backend Fail-Closed Contract).
    with index_lock(_index_path(root)):
        records = [existing for existing in _load_index(root) if existing.session_id != session_id]
        records.insert(0, record)
        # audit I2: bound retention so disk and index size do not grow without limit; unlink
        # the payload files for any records dropped past the configured cap before rewriting
        # the index.
        records = _prune_session_records(root, records)
        _write_index(root, records)
    return SessionOpenResult(
        session_id=session_id,
        root=str(root),
        created_at=created_at,
        file_count=record.file_count,
        symbol_count=record.symbol_count,
        refresh_type="full",
        changeset=changeset,
        scan_limit=scan_limit,
        build_seconds=max(0.0, built_at - started_at),
    )


def refresh_session(
    session_id: str,
    path: str = ".",
    *,
    max_repo_files: int | None = None,
    payload_cache: _SessionServeCache | None = None,
    deadline_monotonic: float | None = None,
) -> SessionRefreshResult:
    # Invalidate process-local products before rederiving a session from current sources.
    _clear_all_source_caches()
    root = _resolve_root(Path(path))
    verified_existing = True
    try:
        existing = get_session(session_id, path)
    except SessionStaleError:
        try:
            existing = _get_session_unverified(session_id, path)
        except SessionStaleError:
            existing = {"root": str(root)}
        verified_existing = False
    effective_max_repo_files = _effective_session_max_repo_files(
        max_repo_files,
        existing if verified_existing else None,
    )
    changeset = (
        _stale_changeset(
            existing,
            detect_added_files=True,
            deadline_monotonic=deadline_monotonic,
        )
        if verified_existing
        else None
    )
    refresh_type = "full"
    refresh_fallback_reason: str | None = (
        None if verified_existing else "unverified_session_provenance"
    )
    with collect_content_receipts() as content_receipts:
        if changeset is not None:
            try:
                repo_map = build_repo_map_incremental(
                    cast(dict[str, Any], existing["repo_map"]),
                    changeset,
                    max_repo_files=effective_max_repo_files,
                    deadline_monotonic=deadline_monotonic,
                )
                refresh_type = "incremental"
            except Exception:
                logger.warning("Incremental session refresh failed; performing a full rebuild")
                repo_map = build_repo_map(
                    root,
                    max_repo_files=effective_max_repo_files,
                    deadline_monotonic=deadline_monotonic,
                )
                refresh_type = "full"
                refresh_fallback_reason = "incremental_failed"
        else:
            repo_map = build_repo_map(
                root,
                max_repo_files=effective_max_repo_files,
                deadline_monotonic=deadline_monotonic,
            )
            changeset = _empty_changeset()
    refreshed_at = datetime.now(UTC).isoformat()
    created_at = str(existing.get("created_at", refreshed_at))
    snapshot_unreadable = _UnreadablePathFlag()  # task #288, see open_session
    snapshot = _capture_snapshot(
        repo_map["related_paths"],
        unreadable_hit=snapshot_unreadable,
        root=root,
        deadline_monotonic=deadline_monotonic,
        expected=content_receipts,
    )
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "refreshed_at": refreshed_at,
        "repo_map": repo_map,
        "snapshot": snapshot,
        "refresh_type": refresh_type,
        "changeset": changeset,
        "scan_limit": cast(dict[str, Any] | None, repo_map.get("scan_limit")),
        "current_generation": _snapshot_generation(snapshot),
    }
    if snapshot_unreadable.hit:
        # Task #288: mirrors `build_repo_map`'s `unreadable_paths = {count, sample}` shape (#276).
        # Emitted ONLY when it fired, so a clean capture is byte-identical to the old payload and
        # a reader can trust the key's ABSENCE to mean "the snapshot covered everything".
        payload["snapshot_unreadable_paths"] = {
            "count": snapshot_unreadable.count,
            "sample": list(snapshot_unreadable.sample),
        }
    if refresh_fallback_reason is not None:
        payload["refresh_fallback_reason"] = refresh_fallback_reason
    session_path = _session_payload_path(root, session_id)

    # Serialize payload and index publication with every other session writer under one lock.
    with index_lock(_index_path(root)):
        # Preserve a concurrent prepare only from a still-authenticated session payload.
        current_on_disk = existing
        if session_path.exists():
            try:
                current_on_disk = read_session_payload(root, session_path)
            except (OSError, SessionPayloadError):
                # Payload unreadable at this instant (e.g. mid-write elsewhere); fall back to
                # the pre-lock snapshot rather than failing the refresh outright.
                current_on_disk = existing
        carried = current_on_disk.get("last_prepare")
        if (
            verified_existing
            and isinstance(carried, dict)
            and current_on_disk.get("session_id") == session_id
            and current_on_disk.get("root") == existing.get("root")
            and verified_session(current_on_disk, root)
        ):
            payload["last_prepare"] = _carry_last_prepare(carried, _snapshot_generation(snapshot))
        seal_session(payload, root)
        _write_json_atomic(session_path, payload)
        if payload_cache is not None:
            payload_cache.put(session_id, str(root), payload)

        records = _load_index(root)
        for index, record in enumerate(records):
            if record.session_id == session_id:
                records[index] = SessionRecord(
                    version=_SESSION_VERSION,
                    session_id=session_id,
                    root=str(root),
                    created_at=created_at,
                    file_count=len(repo_map["files"]),
                    symbol_count=len(repo_map["symbols"]),
                )
                break
        else:
            records.insert(
                0,
                SessionRecord(
                    version=_SESSION_VERSION,
                    session_id=session_id,
                    root=str(root),
                    created_at=created_at,
                    file_count=len(repo_map["files"]),
                    symbol_count=len(repo_map["symbols"]),
                ),
            )
        _write_index(root, records)

    return SessionRefreshResult(
        session_id=session_id,
        root=str(root),
        refreshed_at=refreshed_at,
        file_count=len(repo_map["files"]),
        symbol_count=len(repo_map["symbols"]),
        refresh_type=refresh_type,
        changeset=changeset,
        scan_limit=cast(dict[str, Any] | None, repo_map.get("scan_limit")),
        refresh_fallback_reason=refresh_fallback_reason,
    )


def list_sessions(path: str = ".") -> list[SessionRecord]:
    root = _resolve_root(Path(path))
    return _load_index(root)


def list_sessions_with_discovery(path: str = ".") -> tuple[list[SessionRecord], str, bool]:
    root = _resolve_root(Path(path))
    direct = _load_index(root)
    if direct:
        return direct, str(root), False

    # audit S9: discovery here only reads index *metadata* (not session payloads), and is a
    # documented `tg session list` convenience for locating a child scope from a parent cwd.
    # Payload *loading* confinement is enforced in `_session_root_for_payload`/`get_session`.
    records: list[SessionRecord] = []
    discovered_roots = [candidate for candidate in _nearby_session_roots(path) if candidate != root]
    for discovered_root in discovered_roots:
        records.extend(_load_index(discovered_root))

    records.sort(key=lambda record: record.created_at, reverse=True)
    return (
        records,
        str(discovered_roots[0]) if len(discovered_roots) == 1 else str(root),
        bool(records),
    )


def _get_session_unverified(session_id: str, path: str = ".") -> dict[str, Any]:
    root = _session_root_for_payload(session_id, path)
    session_path = _session_payload_path(root, session_id)
    if not session_path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    try:
        payload = read_session_payload(root, session_path)
    except SessionPayloadError as exc:
        raise SessionStaleError(str(exc)) from exc
    # audit S9: verify the payload was written for the directory we loaded it from, so a
    # payload that escaped (or was planted) under a mismatched root is not silently served.
    recorded_root = payload.get("root")
    if recorded_root is not None:
        try:
            resolved_recorded_root: Path | None = _resolve_root(Path(str(recorded_root)))
        except OSError:
            # Cannot resolve the recorded root (e.g. transient FS error); skip the check
            # rather than failing a payload we loaded from the requested root.
            resolved_recorded_root = None
        if resolved_recorded_root is not None and resolved_recorded_root != root:
            raise FileNotFoundError(
                f"Session root mismatch for {session_id}: payload root {recorded_root!r} "
                f"does not match {root}"
            )
    return payload


def get_session(session_id: str, path: str = ".") -> dict[str, Any]:
    payload = _get_session_unverified(session_id, path)
    root = _session_root_for_payload(session_id, path)
    if payload.get("session_id") != session_id or not verified_session(payload, root):
        raise SessionStaleError(
            "session snapshot authentication failed or is unavailable; "
            "run tg session refresh before reuse (unsigned legacy sessions require refresh)"
        )
    return payload


def _load_session_payload(
    session_id: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
    payload_cache: _SessionServeCache | None = None,
) -> dict[str, Any]:
    try:
        payload = get_session(session_id, path)
        _ensure_session_not_stale(payload, detect_added_files=refresh_on_stale)
    except SessionStaleError:
        if not refresh_on_stale:
            raise
        refresh_session(session_id, path, payload_cache=payload_cache)
        payload = get_session(session_id, path)
        _ensure_session_not_stale(payload, detect_added_files=True)
    return payload


def _session_health_payload(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    changeset = _stale_changeset(payload, detect_added_files=False) or _empty_changeset()
    stale = _changeset_has_entries(changeset)
    return {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(payload.get("root", repo_map.get("path", "."))),
        "created_at": str(payload.get("created_at", "")),
        "refreshed_at": str(payload.get("refreshed_at", payload.get("created_at", ""))),
        "refresh_type": str(payload.get("refresh_type", "full")),
        "file_count": len(cast(list[Any], repo_map.get("files", []))),
        "symbol_count": len(cast(list[Any], repo_map.get("symbols", []))),
        "ok": not stale,
        "stale": stale,
        "changeset": changeset,
    }


def session_context(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    context = build_context_pack_from_map(payload["repo_map"], query)
    context["session_id"] = session_id
    context["routing_reason"] = "session-context"
    return context


def session_context_edit_plan(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    max_repo_files: int | None = _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = monotonic()
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    loaded_at = monotonic()
    repo_map = _limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        max_symbols=max_symbols,
    )
    built_at = monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-edit-plan"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_edit_plan_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def _limited_session_repo_map(
    repo_map: dict[str, Any],
    *,
    max_repo_files: int | None,
) -> dict[str, Any]:
    if max_repo_files is None:
        return repo_map
    return apply_repo_map_output_limits(repo_map, max_files=max(1, int(max_repo_files)))


def _serve_request_cache_value(request: dict[str, Any], name: str, default: Any = "") -> str:
    value = request.get(name, default)
    if value in (None, ""):
        return ""
    return str(value)


def _serve_payload_fingerprint(payload: dict[str, Any]) -> tuple[str, ...]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    return (
        str(payload.get("root", "")),
        str(payload.get("created_at", "")),
        str(payload.get("refreshed_at", "")),
        str(len(cast(list[Any], repo_map.get("files", [])))),
        str(len(cast(list[Any], repo_map.get("symbols", [])))),
    )


def _serve_response_cache_key(
    *,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    command = str(request.get("command", "")).strip().lower()
    common = (
        str(_session_root_for_payload(session_id, path)),
        session_id,
        *_serve_payload_fingerprint(payload),
        command,
        str(request.get("query", "")).strip(),
        _serve_request_cache_value(request, "max_files", 3),
        _serve_request_cache_value(request, "max_sources"),
        _serve_request_cache_value(request, "max_tokens"),
        _serve_request_cache_value(request, "max_repo_files"),
    )
    if command == "context_render":
        return (
            *common,
            _serve_request_cache_value(request, "max_symbols_per_file", 6),
            _serve_request_cache_value(request, "max_render_chars"),
            _serve_request_cache_value(request, "model"),
            _serve_request_cache_value(request, "optimize_context", False),
            _serve_request_cache_value(request, "render_profile", "full"),
            _serve_request_cache_value(request, "profile", False),
        )
    return (
        *common,
        _serve_request_cache_value(request, "max_symbols", 5),
    )


def session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    max_repo_files: int | None = _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = monotonic()
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    loaded_at = monotonic()
    repo_map = _limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
    )
    built_at = monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-render"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_context_render_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_symbol_blast_radius_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius"
    return response


def session_file_importers(
    session_id: str,
    file_path: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    """Zero-reparse reverse #74 lookup: who imports ``file_path``, served from the session's
    cached repo map (no fresh repo scan, unlike the cold ``build_file_importers``)."""
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_file_importers_from_map(payload["repo_map"], file_path)
    response["session_id"] = session_id
    response["routing_reason"] = "session-file-importers"
    return response


def session_blast_radius_plan(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    max_repo_files: int | None = _DEFAULT_SESSION_BLAST_RADIUS_PLAN_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    repo_map = _limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    response = build_symbol_blast_radius_plan_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-plan"
    return response


def session_blast_radius_render(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_symbol_blast_radius_render_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-render"
    return response


def _serve_session_request_from_payload(
    session_id: str,
    request: dict[str, Any],
    payload: dict[str, Any],
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    repo_map = cast(dict[str, Any], payload["repo_map"])
    command = str(request.get("command", "")).strip().lower()

    if command == "ping":
        return {"version": _SESSION_VERSION, "session_id": session_id, "ok": True}

    if command == "show":
        response = dict(payload)
        response["session_id"] = session_id
        return response

    if deadline_monotonic is None:
        deadline_monotonic = monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
    _ensure_session_not_stale(
        payload,
        detect_added_files=bool(request.get("refresh_on_stale", False)),
        deadline_monotonic=deadline_monotonic,
    )

    if command == "repo_map":
        response = dict(repo_map)
        response["session_id"] = session_id
        response["routing_reason"] = "session-repo-map"
        return response

    if command == "context":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context requests require a non-empty query")
        response = build_context_pack_from_map(
            repo_map, query, deadline_monotonic=deadline_monotonic
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context"
        return response

    if command == "context_render":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_render requests require a non-empty query")
        max_repo_files = request.get(
            "max_repo_files",
            _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
        )
        response = build_context_render_from_map(
            _limited_session_repo_map(
                repo_map,
                max_repo_files=(
                    None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
                ),
            ),
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            model=(None if request.get("model") in (None, "") else str(request["model"])),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-render"
        return response

    if command == "context_edit_plan":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_edit_plan requests require a non-empty query")
        max_repo_files = request.get("max_repo_files", _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT)
        scoped_repo_map = _limited_session_repo_map(
            repo_map,
            max_repo_files=(
                None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
            ),
        )
        response = build_context_edit_plan_from_map(
            scoped_repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=(
                None if request.get("max_sources") in (None, "") else int(request["max_sources"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            max_symbols=int(request.get("max_symbols", 5)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-edit-plan"
        return response

    provider = str(request.get("provider", "native"))
    raw_max_tests = request.get("max_tests")
    max_tests = None if raw_max_tests in (None, "") else int(cast(int | str, raw_max_tests))
    if command == "defs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("defs requests require a non-empty symbol")
        response = build_symbol_defs_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-defs"
        return response

    if command == "impact":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("impact requests require a non-empty symbol")
        response = build_symbol_impact_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-impact"
        return response

    if command == "refs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("refs requests require a non-empty symbol")
        response = build_symbol_refs_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-refs"
        return response

    if command == "callers":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("callers requests require a non-empty symbol")
        response = build_symbol_callers_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-callers"
        return response

    if command == "file_importers":
        target_file = str(request.get("file", "")).strip()
        if not target_file:
            raise ValueError("file_importers requests require a non-empty file")
        response = build_file_importers_from_map(
            repo_map, target_file, deadline_monotonic=deadline_monotonic
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-file-importers"
        return response

    if command == "blast_radius":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius requests require a non-empty symbol")
        response = build_symbol_blast_radius_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius"
        return response

    if command == "blast_radius_render":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_render requests require a non-empty symbol")
        response = build_symbol_blast_radius_render_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-render"
        return response

    if command == "blast_radius_plan":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_plan requests require a non-empty symbol")
        max_repo_files = request.get(
            "max_repo_files",
            _DEFAULT_SESSION_BLAST_RADIUS_PLAN_REPO_MAP_LIMIT,
        )
        response = build_symbol_blast_radius_plan_from_map(
            _limited_session_repo_map(
                repo_map,
                max_repo_files=(
                    None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
                ),
            ),
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_symbols=int(request.get("max_symbols", 5)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-plan"
        return response

    if command == "orient":
        response = build_orient_capsule_from_map(
            repo_map,
            max_tokens=int(request.get("max_tokens", 3000)),
            max_central_files=int(request.get("max_central_files", 10)),
            ignore=tuple(request.get("ignore") or ()),
            auto_deweight=bool(request.get("auto_deweight", True)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-orient"
        return response

    if command == "agent":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("agent requests require a non-empty query")
        raw_max_tokens = request.get("max_tokens", 1200)
        raw_max_repo_files = request.get("max_repo_files", _DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT)
        response = build_agent_capsule_from_map(
            repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_tokens=(
                None if raw_max_tokens in (None, "") else int(cast(int | str, raw_max_tokens))
            ),
            max_repo_files=(
                None
                if raw_max_repo_files in (None, "")
                else int(cast(int | str, raw_max_repo_files))
            ),
            model=(None if request.get("model") in (None, "") else str(request["model"])),
            semantic_provider=provider,
            ignore=tuple(request.get("ignore") or ()),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-agent"
        return response

    raise ValueError(f"unknown session command: {command or '<empty>'}")


def serve_session_request(
    session_id: str,
    request: dict[str, Any],
    path: str = ".",
    *,
    payload: dict[str, Any] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    resolved_session_id, resolved_path = _resolve_request_session_target(request, session_id, path)
    session_payload = (
        payload if payload is not None else get_session(resolved_session_id, resolved_path)
    )
    return _serve_session_request_from_payload(
        resolved_session_id,
        request,
        session_payload,
        deadline_monotonic=deadline_monotonic,
    )


def serve_session_stream(
    session_id: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    request_stream = input_stream or sys.stdin
    response_stream = output_stream or sys.stdout
    request_count = 0
    started_at = monotonic()
    payload_cache = _SessionServeCache()
    response_cache = _SessionServeResponseCache()

    for raw_line in request_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_count += 1
        request_deadline = monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
        request_session_id = session_id
        request_path = path
        response: dict[str, Any]
        try:
            request = cast(dict[str, Any], json.loads(line))
            request_session_id, request_path = _resolve_request_session_target(
                request, session_id, path
            )
            if refresh_on_stale and not bool(request.get("refresh_on_stale", False)):
                request = dict(request)
                request["refresh_on_stale"] = True
            command = str(request.get("command", "")).strip().lower()
            if command == "stats":
                response = {
                    "version": _SESSION_VERSION,
                    "ok": True,
                    "cache_hits": payload_cache.hits,
                    "cache_misses": payload_cache.misses,
                    "refresh_count": payload_cache.refreshes,
                    "root_count": payload_cache.root_count,
                    "session_count": payload_cache.session_count,
                    "sessions": payload_cache.sessions,
                    "cache_size_bytes": payload_cache.size_bytes,
                    "response_cache_hits": response_cache.hits,
                    "response_cache_misses": response_cache.misses,
                    "response_cache_puts": response_cache.puts,
                    "response_cache_entries": response_cache.entry_count,
                    "response_cache_size_bytes": response_cache.size_bytes,
                    "response_cache_max_size_bytes": response_cache.max_size_bytes,
                    "response_cache_oversized_skips": response_cache.oversized_skips,
                    "response_cache_stale_detection": "snapshot_content_sha256",
                    "response_cache_added_file_detection": False,
                    "response_cache_refresh_hint": (
                        "Use tg session refresh or request refresh_on_stale when new files must "
                        "invalidate daemon response-cache hits."
                    ),
                    "uptime_seconds": max(0.0, monotonic() - started_at),
                    "request_count": request_count,
                }
            elif command == "health":
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = _session_health_payload(request_session_id, payload)
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response_cache_status = "bypass"
                cacheable_response_command = command in {
                    "context_edit_plan",
                    "context_render",
                } and not bool(request.get("refresh_on_stale", False))
                if cacheable_response_command:
                    _ensure_session_not_stale(
                        payload,
                        detect_added_files=False,
                        deadline_monotonic=request_deadline,
                    )
                    response_cache_key = _serve_response_cache_key(
                        session_id=request_session_id,
                        path=request_path,
                        request=request,
                        payload=payload,
                    )
                    cached_response = response_cache.get(response_cache_key)
                    if cached_response is not None:
                        response = cached_response
                        response_cache_status = "hit"
                    else:
                        response = serve_session_request(
                            request_session_id,
                            request,
                            request_path,
                            payload=payload,
                            deadline_monotonic=request_deadline,
                        )
                        response_cache.put(response_cache_key, response)
                        response_cache_status = "miss"
                else:
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                        deadline_monotonic=request_deadline,
                    )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
                if cacheable_response_command:
                    response["serve_response_cache"] = {
                        "status": response_cache_status,
                        "entries": response_cache.entry_count,
                        "hits": response_cache.hits,
                        "misses": response_cache.misses,
                        "size_bytes": response_cache.size_bytes,
                        "max_size_bytes": response_cache.max_size_bytes,
                        "oversized_skips": response_cache.oversized_skips,
                    }
        except SessionStaleError as exc:
            if refresh_on_stale:
                refresh_session(
                    request_session_id,
                    request_path,
                    payload_cache=payload_cache,
                    deadline_monotonic=request_deadline,
                )
                payload_cache.record_refresh()
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = serve_session_request(
                    request_session_id,
                    request,
                    request_path,
                    payload=payload,
                    deadline_monotonic=request_deadline,
                )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                response = {
                    "version": _SESSION_VERSION,
                    "session_id": request_session_id,
                    "error": {"code": "stale_session", "message": str(exc)},
                }
        except Exception as exc:
            response = {
                "version": _SESSION_VERSION,
                "session_id": request_session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        response_stream.write(json.dumps(response) + "\n")
        response_stream.flush()

    return request_count
