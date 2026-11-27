from __future__ import annotations

import getpass
import json
import os as os
import secrets
import socket
import subprocess
import sys as sys

if __name__ == "__main__":
    sys.modules.setdefault("tensor_grep.cli.session_daemon", sys.modules[__name__])
import time as time
from pathlib import Path
from time import monotonic as monotonic
from typing import Any, cast
from uuid import uuid4

from tensor_grep.cli._index_lock import (
    IndexLockTimeoutError as IndexLockTimeoutError,
)
from tensor_grep.cli._index_lock import (
    index_lock as index_lock,
)
from tensor_grep.cli._index_lock import (
    replace_with_retry as replace_with_retry,
)
from tensor_grep.cli.runtime_paths import _expected_tg_version as _expected_tg_version
from tensor_grep.cli.session_daemon_deadlines import (
    _load_payload_with_status_retry as _load_payload_with_status_retry,
)
from tensor_grep.cli.session_daemon_deadlines import (
    _read_bounded_request_line as _read_bounded_request_line,
)
from tensor_grep.cli.session_daemon_metrics_sanitize import (
    _empty_metrics_day_bucket as _empty_metrics_day_bucket,
)
from tensor_grep.cli.session_daemon_metrics_sanitize import (
    _sanitize_metrics_days as _sanitize_metrics_days,
)
from tensor_grep.cli.session_daemon_response_cache import (
    _SessionResponseCache as _SessionResponseCache,
)
from tensor_grep.cli.session_daemon_start_capture import (
    cleanup_daemon_start_capture as cleanup_daemon_start_capture,
)
from tensor_grep.cli.session_daemon_start_capture import (
    read_daemon_start_capture as read_daemon_start_capture,
)
from tensor_grep.cli.session_daemon_start_capture import (
    spawn_daemon_subprocess as spawn_daemon_subprocess,
)
from tensor_grep.cli.session_daemon_start_diagnostics import (
    DaemonStartError as DaemonStartError,
)
from tensor_grep.cli.session_daemon_start_diagnostics import (
    daemon_start_failure as daemon_start_failure,
)
from tensor_grep.cli.session_daemon_start_lock import (
    _DAEMON_START_LOCK_FILE as _DAEMON_START_LOCK_FILE,
)
from tensor_grep.cli.session_daemon_start_lock import (
    _daemon_start_lock_path as _daemon_start_lock_path,
)
from tensor_grep.cli.session_daemon_start_lock import (
    _release_daemon_start_lock as _release_daemon_start_lock,
)
from tensor_grep.cli.session_daemon_start_lock import (
    _try_acquire_daemon_start_lock as _try_acquire_daemon_start_lock,
)
from tensor_grep.cli.session_daemon_stop import _stop_rejected_daemon
from tensor_grep.cli.session_daemon_trust import (
    _METADATA_REASONS as _METADATA_REASONS,
)
from tensor_grep.cli.session_daemon_trust import (
    DAEMON_HOST as DAEMON_HOST,
)
from tensor_grep.cli.session_daemon_trust import (
    _attestation_fields as _attestation_fields,
)
from tensor_grep.cli.session_daemon_trust import (
    _await_endpoint_refused as _await_endpoint_refused,
)
from tensor_grep.cli.session_daemon_trust import (
    _classify_daemon_pid as _classify_daemon_pid,
)
from tensor_grep.cli.session_daemon_trust import (
    _daemon_invocation_root as _daemon_invocation_root,
)
from tensor_grep.cli.session_daemon_trust import (
    _daemon_pid_state as _daemon_pid_state,
)
from tensor_grep.cli.session_daemon_trust import (
    _daemon_ping_proof as _daemon_ping_proof,
)
from tensor_grep.cli.session_daemon_trust import (
    _daemon_secret_path as _daemon_secret_path,
)
from tensor_grep.cli.session_daemon_trust import (
    _DaemonRefreshFailed as _DaemonRefreshFailed,
)
from tensor_grep.cli.session_daemon_trust import (
    _endpoint_flag as _endpoint_flag,
)
from tensor_grep.cli.session_daemon_trust import (
    _is_loopback_host as _is_loopback_host,
)
from tensor_grep.cli.session_daemon_trust import (
    _load_or_create_user_secret as _load_or_create_user_secret,
)
from tensor_grep.cli.session_daemon_trust import (
    _metadata_state as _metadata_state,
)
from tensor_grep.cli.session_daemon_trust import (
    _metadata_unconfirmed as _metadata_unconfirmed,
)
from tensor_grep.cli.session_daemon_trust import (
    _ping_proof_fields as _ping_proof_fields,
)
from tensor_grep.cli.session_daemon_trust import (
    _process_info as _process_info,
)
from tensor_grep.cli.session_daemon_trust import (
    _read_user_secret as _read_user_secret,
)
from tensor_grep.cli.session_daemon_trust import (
    _refresh_failed_error as _refresh_failed_error,
)
from tensor_grep.cli.session_daemon_trust import (
    _stale_success_fields as _stale_success_fields,
)
from tensor_grep.cli.session_daemon_trust import (
    _stale_unconfirmed as _stale_unconfirmed,
)
from tensor_grep.cli.session_daemon_trust import (
    _stop_success as _stop_success,
)
from tensor_grep.cli.session_daemon_trust import (
    _terminate_identified as _terminate_identified,
)
from tensor_grep.cli.session_daemon_trust import (
    _unconfirmed_fields as _unconfirmed_fields,
)
from tensor_grep.cli.session_daemon_trust import (
    _valid_daemon_port as _valid_daemon_port,
)
from tensor_grep.cli.session_daemon_trust import (
    _verify_ping_reply as _verify_ping_reply,
)
from tensor_grep.cli.session_daemon_trust import (
    diagnose_user_secret as diagnose_user_secret,
)
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT as _DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT,
)
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT as _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
)
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT as _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
)
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_ORIENT_REPO_MAP_LIMIT as _DEFAULT_SESSION_ORIENT_REPO_MAP_LIMIT,
)
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT as _DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT,
)
from tensor_grep.cli.session_store import (
    _SESSION_VERSION as _SESSION_VERSION,
)
from tensor_grep.cli.session_store import (
    WARM_DAEMON_DEFAULT_DEADLINE_SECONDS as WARM_DAEMON_DEFAULT_DEADLINE_SECONDS,
)
from tensor_grep.cli.session_store import (
    _ensure_session_not_stale as _ensure_session_not_stale,
)
from tensor_grep.cli.session_store import (
    _index_path as _index_path,
)
from tensor_grep.cli.session_store import (
    _load_index as _load_index,
)
from tensor_grep.cli.session_store import (
    _resolve_request_session_target as _resolve_request_session_target,
)
from tensor_grep.cli.session_store import (
    _resolve_root as _resolve_root,
)
from tensor_grep.cli.session_store import (
    _session_health_payload as _session_health_payload,
)
from tensor_grep.cli.session_store import (
    _session_payload_path as _session_payload_path,
)
from tensor_grep.cli.session_store import (
    _sessions_dir as _sessions_dir,
)
from tensor_grep.cli.session_store import (
    _SessionServeCache as _SessionServeCache,
)
from tensor_grep.cli.session_store import (
    _write_index as _write_index,
)
from tensor_grep.cli.session_store import (
    _write_json_atomic as _write_json_atomic,
)
from tensor_grep.cli.session_store import (
    open_session as open_session,
)
from tensor_grep.cli.session_store import (
    refresh_session as refresh_session,
)
from tensor_grep.cli.session_store import (
    serve_session_request as serve_session_request,
)

_DAEMON_METADATA_FILE = "daemon.json"
_DAEMON_HOST = DAEMON_HOST
_DAEMON_CONNECT_TIMEOUT_SECONDS = 0.5
_DAEMON_RESPONSE_TIMEOUT_SECONDS = 60.0
# capability P0-6 step 5: the client-side socket read timeout for a daemon response is env-configurable so
# a large repo whose warm-daemon graph query legitimately needs >60s is NOT killed by a hard cap that
# returns a bare "timed out" / exit 1 / zero JSON (the recurring dogfood "60s cap errors" complaint).
# #200 added the separate traversal-deadline this comment used to flag as "tracked" -- but only for
# the 4 commands session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS covers (agent/orient/
# context_render/context_edit_plan, the DEFAULT no-flag case). The 5 symbol commands (defs/impact/
# refs/callers/blast_radius) plus blast_radius_render/blast_radius_plan/file_importers/context were
# the residual, tracked as #390 -- CLOSED 2026-07-17 by `81b2148` (#203/#652), which bounds all nine
# with the default deadline; refined by #205/#658/#669. `session_store` now computes
# `deadline_monotonic = monotonic() + WARM_DAEMON_DEFAULT_DEADLINE_SECONDS` on every one of those
# branches -- derive the current set from THAT constant's call sites, not from this comment.
# (This block read "#390, still open" for ~2 weeks after the fix landed. A comment that describes a
# gap someone already closed is the self-contradicting-document class in AGENTS.md: it sends the
# next reader to build a fix that exists. Cite the mechanism, not a status word.)
_DAEMON_RESPONSE_TIMEOUT_ENV = "TG_SESSION_DAEMON_RESPONSE_TIMEOUT_SECONDS"
_DAEMON_START_TIMEOUT_SECONDS = 5.0
_DAEMON_SESSION_LOOKUP_RETRY_SECONDS = 0.25
_DAEMON_IMPLICIT_SESSION_MAX_ENTRIES = 16
_DAEMON_RESPONSE_CACHE_SCOPE = (
    "daemon-routed top-level/session context-render/edit-plan/defs/impact/refs/callers/"
    "blast-radius/orient/agent requests"
)
_DAEMON_RESPONSE_CACHE_STALE_DETECTION = "snapshot_content_sha256"
_DAEMON_RESPONSE_CACHE_ADDED_FILE_DETECTION = False
_DAEMON_START_LOCK_STALE_SECONDS = _DAEMON_START_TIMEOUT_SECONDS * 2
# audit S3: per-daemon shared secret guarding the loopback IPC socket. The token is generated
# at startup and written to daemon.json with 0600 perms; clients echo it back on every request.
_DAEMON_TOKEN_FIELD = "token"
_DAEMON_METADATA_MODE = 0o600
# audit (round-3 pre-auth DoS): the handler reads one request line from an UNTRUSTED client
# before the token check. An unbounded readline() lets a hostile local client stream bytes with
# no newline and exhaust daemon memory, and a silent/slow client can pin a worker thread forever.
# Bound the read (session requests are small JSON) and time out the connection.
_MAX_DAEMON_REQUEST_BYTES = 1 * 1024 * 1024  # 1 MiB pre-auth request cap
_DAEMON_HANDLER_TIMEOUT_SECONDS = 30.0  # socket read timeout for a single request
# audit I7: bound daemon lifetime so a forgotten daemon does not linger forever. Either an idle
# stretch (no requests) or a hard max uptime triggers a cooperative self-shutdown. Both are
# env-configurable; non-positive values disable the corresponding limit.
_DAEMON_IDLE_SHUTDOWN_SECONDS_ENV = "TG_SESSION_DAEMON_IDLE_SECONDS"
_DAEMON_MAX_UPTIME_SECONDS_ENV = "TG_SESSION_DAEMON_MAX_UPTIME_SECONDS"
_DEFAULT_DAEMON_IDLE_SHUTDOWN_SECONDS = 900.0
_DEFAULT_DAEMON_MAX_UPTIME_SECONDS = 86400.0
_DAEMON_LIFECYCLE_POLL_SECONDS = 5.0
# audit r8: cap the wait for in-flight requests to drain at the hard max-uptime shutdown, so a
# wedged request cannot postpone the daemon's self-shutdown forever.
_DAEMON_SHUTDOWN_DRAIN_GRACE_SECONDS = 30.0

# tg-ledger step-0 (demand instrumentation, NOT a ledger): a lightweight, fail-open, PII-free
# demand receipt used only to decide whether a shared local code-intelligence plane ("tg
# ledger") is worth building. Never stores raw symbol/query text -- only a truncated SHA-256
# hash -- and never changes response behavior. See docs/multi_agent_context_plane.md.
_DAEMON_METRICS_ENABLED_ENV = "TG_DAEMON_METRICS"
_DAEMON_METRICS_FILE = "daemon_metrics.json"
_DAEMON_METRICS_SCHEMA_VERSION = 1
# Commands that are auto-issued by daemon-lifecycle plumbing itself (health probes, the CLI's
# start-or-reuse probe, stats polling, and stop) and would fabricate demand if counted.
_DAEMON_METRICS_EXCLUDED_COMMANDS = frozenset({"ping", "stats", "stop", "health"})
# The commands that build/return an expensive artifact (a rendered repo map, a symbol graph
# query, a context/edit-plan render) -- the surface where a shared plane would de-duplicate
# repeated work across concurrent agents.
_DAEMON_METRICS_EXPENSIVE_COMMANDS = frozenset({
    "repo_map",
    "context",
    "context_render",
    "context_edit_plan",
    "defs",
    "impact",
    "refs",
    "callers",
    "blast_radius",
    "blast_radius_render",
    "blast_radius_plan",
    # task #108 (Tier-2 daemon capability): orient/agent join the expensive-command set.
    "orient",
    "agent",
})
_DAEMON_METRICS_SYMBOL_COMMANDS = frozenset({
    "defs",
    "impact",
    "refs",
    "callers",
    "blast_radius",
    "blast_radius_render",
    "blast_radius_plan",
})
_DAEMON_METRICS_QUERY_COMMANDS = frozenset({"context", "context_render", "context_edit_plan"})
_DAEMON_METRICS_CLIENT_WINDOW_SECONDS = 300.0
_DAEMON_METRICS_DUP_WINDOW_SECONDS = 900.0
_DAEMON_METRICS_DUP_LRU_MAX_ENTRIES = 256
_DAEMON_METRICS_DAY_PID_SET_CAP = 128
_DAEMON_METRICS_MAX_DAY_BUCKETS = 30
_DAEMON_METRICS_MAX_DUP_TARGETS_PER_DAY = 32
_DAEMON_METRICS_FLUSH_POLL_INTERVALS = 12  # ~60s at the default 5s lifecycle poll
_DAEMON_METRICS_ROLLUP_WINDOW_DAYS = 14
_DAEMON_METRICS_ROLLUP_SHORT_WINDOW_DAYS = 7
# Heuristic build-decision gate for the tg-ledger step-0 demand receipt -- deliberately not a
# hard business commitment, just a documented, adjustable bar for "there is real cross-agent
# concurrency AND real repeated-artifact demand" over the trailing 14 days.
_DAEMON_METRICS_PRE_GATE_MIN_CONCURRENT_DAYS = 3
_DAEMON_METRICS_PRE_GATE_MIN_DUP_REQUESTS_14D = 10


def _daemon_metrics_enabled() -> bool:
    raw = os.environ.get(_DAEMON_METRICS_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip() != "0"


def _daemon_metadata_path(root: Path) -> Path:
    return _sessions_dir(root) / _DAEMON_METADATA_FILE


def _nearby_daemon_roots(path: str = ".") -> list[Path]:
    root = _resolve_root(Path(path))
    candidates: list[Path] = [root]
    candidates.extend(parent for parent in root.parents if parent != root)
    try:
        candidates.extend(child for child in root.iterdir() if child.is_dir())
    except OSError:
        pass

    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if _daemon_metadata_path(resolved).exists():
            roots.append(resolved)
    return roots


def _read_daemon_metadata(root: Path) -> dict[str, Any] | None:
    """The metadata dict, or ``None`` for absent / unreadable / invalid. Callers that must tell
    absence from damage use ``_metadata_state`` (only ABSENT is proof that nothing is recorded)."""
    return _metadata_state(_daemon_metadata_path(root))[1]


def _write_daemon_metadata(root: Path, payload: dict[str, Any]) -> None:

    # audit S3: daemon.json carries the IPC token; write it 0600 so the secret is not exposed.
    metadata_path = _daemon_metadata_path(root)
    if sys.platform == "win32":
        # audit #81 #13: on Windows, os.chmod/the os.open `mode=` bits grant NO real per-user
        # access control (see _restrict_windows_file_to_current_user below), so writing the
        # token via the shared, POSIX-oriented _write_json_atomic and only running icacls
        # AFTERWARD (the old sequence) left the token file world-readable-under-the-parent's-
        # default-ACL for the whole write+rename+icacls duration. Lock the ACL down before any
        # secret byte is written instead -- see _write_daemon_metadata_windows.
        _write_daemon_metadata_windows(metadata_path, payload)
    else:
        # POSIX: _write_json_atomic already creates the temp file AT mode 0600 via
        # os.open(O_CREAT|O_EXCL, mode) -- the kernel applies the mode atomically at creation,
        # so the file is never briefly world-readable between creation and the rename.
        _write_json_atomic(metadata_path, payload, mode=_DAEMON_METADATA_MODE)
    # Defense-in-depth re-assertion on the *published* path. On the Windows path above this is
    # redundant with the temp-file lock (a same-directory rename carries the explicit DACL we
    # just set forward, it is not recomputed from the parent), but it is cheap insurance against
    # any future change to the publish step; on POSIX it is a no-op (sys.platform guard below).
    _restrict_windows_file_to_current_user(metadata_path)


def _write_daemon_metadata_windows(path: Path, payload: dict[str, Any]) -> None:
    """Windows: lock the temp file's ACL down BEFORE the HMAC token is written into it (audit #81 #13).

    Mirrors ``session_store._write_json_atomic``'s create-temp/write/fsync/atomic-rename shape,
    but inserts the ACL lockdown between temp-file *creation* (0 bytes, no secret yet) and the
    write of the token payload, instead of applying it only after the fact on the already-
    published path. The create-then-lock-then-write ordering keeps the same file descriptor open
    across the ``icacls`` subprocess call -- ``os.open()`` on Windows defaults to DENY_NONE
    sharing, so a concurrent process can set the DACL without a sharing violation (verified
    empirically against this repo's CI Windows image) -- so there is no close/reopen gap either.
    The rename stays within the same directory, so the explicit (non-inherited) DACL travels with
    the file rather than being recomputed from the parent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # audit C4/#659 residual (task #211): this Windows-specific copy of the atomic-write pattern
    # (needed so the ACL lockdown below can run between temp-file creation and the token write --
    # see the docstring above) never gained the is_symlink() precheck session_store.
    # _write_json_atomic and evidence_signing._write_private_key_atomic already carry. Add it here
    # too so a pre-existing symlink at daemon.json is refused instead of silently replaced. POSIX
    # O_NOFOLLOW is a no-op on Windows (this function only ever runs on win32, per the caller's
    # `sys.platform == "win32"` guard), so this precheck is the ONLY defense available on this
    # code path; O_EXCL on the temp-file open below still applies as usual.
    if path.is_symlink():
        raise OSError(f"Refusing to write through a symlink: {path}")
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = json.dumps(payload, indent=2)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _DAEMON_METADATA_MODE)
    try:
        # The lock call is INSIDE the fdopen `with` so that if it ever raised (it shouldn't --
        # _restrict_windows_file_to_current_user swallows its own errors -- but this must stay
        # correct even if that changes), the `with` block closes the underlying fd before the
        # `except` below tries to unlink: Windows refuses to delete a file that is still open
        # under this process (WinError 32).
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # ACL-lock BEFORE any token bytes are written -- the file is still 0 bytes here, so
            # there is nothing for another local account to read even if it wins the race to
            # open the (randomly named) temp file before icacls completes.
            _restrict_windows_file_to_current_user(tmp_path)
            handle.write(data)
            handle.flush()
            # M6: fsync the data before the rename so a crash can never publish a truncated
            # token file (mirrors _write_json_atomic, audit I5).
            os.fsync(handle.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # don't leave a partial/unlocked temp behind
        raise
    replace_with_retry(tmp_path, path)
    # Best-effort directory-durability fsync, mirroring _write_json_atomic; a no-op/unsupported
    # on Windows so failures here are expected and non-fatal.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _restrict_windows_file_to_current_user(path: Path) -> None:
    """Best-effort Windows ACL lockdown of the daemon token file (round-7 r7).

    On POSIX the 0600 mode from _write_json_atomic isolates the token. On Windows os.chmod only
    toggles the read-only DOS bit -- it grants NO per-user access control, so any local account that
    can reach the session root could read the IPC token. Remove inherited ACLs and grant only the
    current user. The HMAC compare_digest gate remains the ENFORCED control; this is defense in
    depth and fails OPEN (a failed icacls must never break daemon startup).

    Called both BEFORE the token is written (on the pre-publish temp, the real fix for audit
    #81 #13) and again afterward on the published path (defense-in-depth) -- safe either way
    since it only ever tightens the ACL of whatever path it is given.
    """
    if sys.platform != "win32":
        return
    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:
        return
    if user:
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                stdin=subprocess.DEVNULL,
                check=False,
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass


def _daemon_token(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return ""
    token = metadata.get(_DAEMON_TOKEN_FIELD)
    return str(token) if token else ""


def _configured_lifecycle_seconds(env_var: str, default: float) -> float:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def _confine_path_to_root(root: Path, candidate: Path) -> Path:
    """Reject a resolved request path that escapes ``root`` (audit S3).

    Returns ``candidate`` when it is ``root`` itself or a descendant; otherwise falls back to
    ``root`` so a crafted absolute ``path``/``root`` cannot point the daemon outside the
    directory it was started for.
    """
    if candidate == root:
        return candidate
    try:
        candidate.relative_to(root)
    except ValueError:
        return root
    return candidate


def _remove_daemon_metadata(
    root: Path, *, expected_pid: int | None = None, expected_port: int | None = None
) -> None:
    """Remove ``daemon.json`` for ``root``, guarded against deleting a REPLACEMENT daemon's metadata.

    Task #143a-a (stale-daemon-metadata race): when ``_probe_daemon`` rejects a daemon over a
    ``package_version`` mismatch (see the skew check in ``_probe_daemon`` below), the client spawns
    a replacement daemon (call it B) while the old daemon (A) keeps running until its own
    idle/max-uptime self-shutdown fires. If A's shutdown path (or ``stop_session_daemon``'s
    PID-kill fallback) unlinked ``daemon.json`` unconditionally, it would delete WHATEVER metadata
    currently exists -- including B's, the healthy replacement -- orphaning B (it keeps serving,
    but no client can discover it) and silently piling up orphaned daemon processes over time.

    Passing ``expected_pid``/``expected_port`` re-reads the CURRENT on-disk metadata immediately
    before unlinking and only proceeds if its ``pid``/``port`` fields still match -- i.e. the file
    still identifies the exact instance the caller intends to remove. A mismatch (a replacement has
    since published its own metadata), a missing file, or an unparseable/corrupt file are all
    treated as "not mine" and left untouched -- the fail-safe choice, since we cannot prove the file
    is safe to delete, and a corrupt file self-heals the next time a daemon is (re)started via
    ``_spawn_daemon_subprocess``'s unconditional clear below.

    When BOTH ``expected_pid`` and ``expected_port`` are omitted (the default), removal is
    unconditional -- this mode exists for exactly one legitimate caller, ``_spawn_daemon_subprocess``,
    which holds the daemon-start lock (excluding any concurrent spawn for this root) and is clearing
    the slate for a brand-new instance it is about to launch, so there is no prior "self" identity to
    compare against yet. Every other caller must supply at least one ``expected_*`` value, or skip
    the call entirely when it cannot establish one (see ``stop_session_daemon``).
    """
    metadata_path = _daemon_metadata_path(root)
    if expected_pid is not None or expected_port is not None:
        current = _read_daemon_metadata(root)
        if current is None:
            return
        # NIT (v1.76 #603 gate, "NIT-D3"): compare through `_daemon_identity` on BOTH sides
        # instead of `current.get("pid") != expected_pid` directly. Every writer today always
        # stores pid/port as JSON ints, so the direct compare happens to work -- but trusting
        # that on-disk shape is not type-safe: a future writer that stored either field as a
        # string (or any other non-int JSON value) would make the direct compare silently NEVER
        # match, so this guard would never delete that metadata -- a permanent orphan leak
        # instead of a loud failure. `_daemon_identity` already coerces via `int(...)` (and
        # resolves to `None` on anything unparseable), so routing the on-disk read through it
        # here makes the compare type-safe the same way the callers computing `expected_pid`/
        # `expected_port` already do (see `_daemon_identity`'s callers below).
        current_pid, current_port = _daemon_identity(current)
        if expected_pid is not None and current_pid != expected_pid:
            return
        if expected_port is not None and current_port != expected_port:
            return
    try:
        metadata_path.unlink()
    except OSError:
        pass


def _daemon_identity(metadata: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Best-effort ``(pid, port)`` extraction from daemon metadata (task #143a-a).

    Used by callers of the guarded ``_remove_daemon_metadata`` above to compute the specific
    instance they intend to remove. Missing or non-numeric fields resolve to ``None`` rather than
    raising, so a malformed or partial metadata dict never turns into a removal call the guard
    cannot safely reason about -- the caller is expected to skip the removal when either half of
    the pair it needs comes back ``None`` (see ``stop_session_daemon``).
    """
    if not metadata:
        return None, None
    try:
        pid: int | None = int(metadata["pid"])
    except (KeyError, TypeError, ValueError, OverflowError):
        pid = None
    # Identity-only (guards unlinking daemon.json; never dials), so the tolerant int() coercion
    # pinned by test_remove_daemon_metadata_guarded_matches_string_pid_and_port_on_disk stays --
    # but an inf/huge float must resolve to None instead of raising OverflowError.
    try:
        port: int | None = int(metadata["port"])
    except (KeyError, TypeError, ValueError, OverflowError):
        port = None
    return pid, port


def _pid_looks_like_tg_daemon(pid: int) -> bool:
    """Best-effort: ``pid`` is running a genuine ``python -m tensor_grep.cli.session_daemon`` launch
    (kept for callers/tests; the kill path decides through ``_classify_daemon_pid``)."""
    try:
        return pid > 0 and _daemon_invocation_root(_process_info(pid)[0]) is not None
    except Exception:
        return False


def _terminate_daemon_by_pid(metadata: dict[str, Any] | None, *, root: Path | None = None) -> bool:
    """Terminate the daemon process recorded in ``metadata`` (audit I7).

    Only fires when the pid is provably the tensor-grep daemon serving ``root`` (a genuine
    ``-m tensor_grep.cli.session_daemon --root <root>`` launch) and is still the same process
    (create_time unchanged). Returns True if a terminate signal was delivered.
    """
    state, identity = _classify_daemon_pid(metadata, root)
    return state == "ours" and identity is not None and _terminate_identified(identity)


def _daemon_request(
    host: str,
    port: int,
    request: dict[str, Any],
    *,
    response_timeout: float | None = _DAEMON_RESPONSE_TIMEOUT_SECONDS,
    token: str = "",
    connect_timeout: float | None = None,
) -> dict[str, Any]:
    if not _is_loopback_host(host):
        raise ValueError(f"refusing non-loopback session daemon host: {host!r}")
    if _valid_daemon_port(port) is None:
        raise ValueError(f"refusing malformed session daemon port: {port!r}")
    # audit S3: every request must carry the per-daemon token. Inject it here so the in-process
    # client (which read the token from the 0600 daemon.json) authenticates transparently.
    if token:
        request = {**request, _DAEMON_TOKEN_FIELD: token}
    # tg-ledger step-0 (demand instrumentation): tag every request with the CALLING process's
    # pid so the daemon can distinguish concurrent distinct clients. A fresh TCP connection per
    # request means client_address is ephemeral and the token is shared per-daemon (not
    # per-client), so pid is the only signal available. Every client path (request_session_daemon,
    # request_running_session_daemon, the ping/stats probes) funnels through this function. The
    # pid is diagnostic-only -- never used for auth/routing -- and no response-cache key reads it
    # (see _context_render_response_cache_key / _context_edit_plan_response_cache_key), so it
    # cannot fragment cache hits between otherwise-identical requests.
    request = {**request, "client_pid": os.getpid()}
    with socket.create_connection(
        (host, int(port)),
        timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS if connect_timeout is None else connect_timeout,
    ) as conn:
        conn.settimeout(response_timeout)
        conn.sendall((json.dumps(request) + "\n").encode("utf-8"))
        reader = conn.makefile("r", encoding="utf-8")
        line = reader.readline()
        if not line:
            raise RuntimeError("session daemon closed connection without a response")
        return cast(dict[str, Any], json.loads(line))


def _resolve_daemon_request_path(root: Path, requested_path: object) -> str:
    raw_path = str(requested_path).strip() if requested_path is not None else ""
    if not raw_path:
        return str(root)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        # audit S3: cannot resolve (e.g. missing path) — fall back to the daemon root rather
        # than trusting an unresolved, potentially-escaping request path.
        return str(root)
    # audit S3: confine the resolved request path to the daemon's root so a crafted absolute
    # path cannot drive the daemon to read/serve sessions outside the directory it owns.
    return str(_confine_path_to_root(root, resolved))


def _probe_daemon(root: Path) -> dict[str, Any] | None:
    metadata = _read_daemon_metadata(root)
    if metadata is None:
        return None
    host = metadata.get("host", _DAEMON_HOST)
    if not _is_loopback_host(host):
        return None
    connected_port = _valid_daemon_port(metadata.get("port"))
    if connected_port is None:
        return None
    nonce = secrets.token_hex(16)
    try:
        response = _daemon_request(
            str(host),
            connected_port,
            {"command": "ping", "nonce": nonce},
            response_timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS,
            token=_daemon_token(metadata),
        )
    except Exception:
        return None
    if not response.get("ok"):
        return None
    # F-01: daemon.json is repo-controlled, so "ok" proves nothing -- require the per-user HMAC.
    if not _verify_ping_reply(response, nonce, root, connected_port):
        return None
    # Task #94 PR-1 safety addition: a daemon can survive a `tg upgrade` (daemons live up to
    # TG_SESSION_DAEMON_MAX_UPTIME_SECONDS, 24h default) and keep serving stale-code responses
    # to a freshly-upgraded client. Treat a package-version mismatch -- including a pre-PR-1
    # daemon.json with no `package_version` field at all -- identically to "no daemon reachable":
    # the caller's existing cold path + non-blocking respawn self-heals, since
    # `_spawn_daemon_subprocess` unconditionally removes the stale daemon.json before launching a
    # fresh one. The now-orphaned stale daemon process eventually shuts itself down via the
    # existing idle/max-uptime lifecycle monitor.
    if metadata.get("package_version") != _expected_tg_version():
        return None
    # The metadata's own `pid` is unverified (repo-controlled); the signed reply's is proven.
    # Callers that escalate to a pid kill must only ever see the proven one.
    return {**metadata, "pid": int(response["pid"])}


def _merge_live_daemon_stats(status: dict[str, Any], *, token: str = "") -> dict[str, Any]:
    status.setdefault("response_cache_scope", _DAEMON_RESPONSE_CACHE_SCOPE)
    if not status.get("running"):
        return status
    stat_fields = {
        "version",
        "cache_hits",
        "cache_misses",
        "refresh_count",
        "root_count",
        "session_count",
        "sessions",
        "cache_size_bytes",
        "response_cache_size",
        "response_cache_size_bytes",
        "response_cache_max_size_bytes",
        "response_cache_hits",
        "response_cache_misses",
        "response_cache_puts",
        "response_cache_entries",
        "response_cache_oversized_skips",
        "response_cache_scope",
        "response_cache_stale_detection",
        "response_cache_added_file_detection",
        "response_cache_refresh_hint",
        "uptime_seconds",
        "inflight_requests",
        "skipped_requests",
        "last_full_rebuild_seconds",
    }
    try:
        stats = _daemon_request(
            str(status.get("host", _DAEMON_HOST)),
            int(status["port"]),
            {"command": "stats"},
            response_timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS,
            token=token,
        )
    except Exception as exc:
        status["stats_unavailable"] = str(exc)
        return status
    for key, value in stats.items():
        if key not in stat_fields:
            continue
        status[key] = value
    return status


def get_session_daemon_status(path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    meta_state, metadata = _metadata_state(_daemon_metadata_path(root))
    if meta_state in ("unreadable", "invalid"):  # damage is not absence
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "discovered": False,
            "running": False,
            "stale_metadata": True,
            "metadata_error": _METADATA_REASONS[meta_state],
        }
    if metadata is None:
        for discovered_root in _nearby_daemon_roots(path):
            if discovered_root == root:
                continue
            live = _probe_daemon(discovered_root)
            if live is None:
                continue
            return _attach_demand_metrics(
                _merge_live_daemon_stats(
                    {
                        "version": _SESSION_VERSION,
                        "root": str(discovered_root),
                        "requested_root": str(root),
                        "discovered": True,
                        "running": True,
                        "host": str(live.get("host", _DAEMON_HOST)),
                        "port": int(live["port"]),
                        "pid": int(live["pid"]),
                        "started_at": str(live["started_at"]),
                    },
                    token=_daemon_token(live),
                ),
                discovered_root,
            )
        return _attach_demand_metrics(
            {
                "version": _SESSION_VERSION,
                "root": str(root),
                "discovered": False,
                "running": False,
            },
            root,
        )
    live = _probe_daemon(root)
    if live is None:
        return _attach_demand_metrics(
            {
                "version": _SESSION_VERSION,
                "root": str(root),
                "discovered": False,
                "running": False,
                "stale_metadata": True,
                **_endpoint_flag(metadata),
            },
            root,
        )
    return _attach_demand_metrics(
        _merge_live_daemon_stats(
            {
                "version": _SESSION_VERSION,
                "root": str(root),
                "discovered": False,
                "running": True,
                "host": str(live.get("host", _DAEMON_HOST)),
                "port": int(live["port"]),
                "pid": int(live["pid"]),
                "started_at": str(live["started_at"]),
            },
            token=_daemon_token(live),
        ),
        root,
    )


def _spawn_daemon_subprocess(root: Path) -> tuple[int, Path]:
    """Popen the session-daemon child process for ``root`` and return immediately.

    Extracted from ``start_session_daemon`` (task #94 Part A) so the SAME spawn logic can be
    reused by the fire-and-forget ``maybe_autostart_session_daemon_nonblocking`` below without
    duplicating the Popen/PYTHONPATH/creationflags block. Pure side effect: does not wait for
    ``daemon.json`` to appear and does not touch the start-lock -- the caller is responsible for
    both (holding the lock across this call, and deciding whether/how long to wait afterward).
    """
    return spawn_daemon_subprocess(root, _sessions_dir(root), _remove_daemon_metadata)


def start_session_daemon(path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    existing = _probe_daemon(root)
    if existing is not None:
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "running": True,
            "host": str(existing.get("host", _DAEMON_HOST)),
            "port": int(existing["port"]),
            "pid": int(existing["pid"]),
            "started_at": str(existing["started_at"]),
            "auto_started": False,
            "response_cache_scope": _DAEMON_RESPONSE_CACHE_SCOPE,
        }

    acquired_lock = _try_acquire_daemon_start_lock(root)
    if not acquired_lock:
        deadline = monotonic() + _DAEMON_START_TIMEOUT_SECONDS
        while monotonic() < deadline:
            metadata = _probe_daemon(root)
            if metadata is not None:
                return {
                    "version": _SESSION_VERSION,
                    "root": str(root),
                    "running": True,
                    "host": str(metadata.get("host", _DAEMON_HOST)),
                    "port": int(metadata["port"]),
                    "pid": int(metadata["pid"]),
                    "started_at": str(metadata["started_at"]),
                    "auto_started": False,
                    "response_cache_scope": _DAEMON_RESPONSE_CACHE_SCOPE,
                }
            time.sleep(0.05)
        secret_reason = diagnose_user_secret()
        detail = "daemon startup timed out (another tg process held the start lock)"
        if secret_reason:
            detail = f"{detail} [{secret_reason}]"
        raise DaemonStartError(
            root,
            detail,
            secret_reason or "daemon_start_failed",
        )

    capture_fd: int | None = None
    capture_path: Path | None = None
    try:
        capture_fd, capture_path = _spawn_daemon_subprocess(root)

        deadline = monotonic() + _DAEMON_START_TIMEOUT_SECONDS
        while monotonic() < deadline:
            metadata = _probe_daemon(root)
            if metadata is not None:
                return {
                    "version": _SESSION_VERSION,
                    "root": str(root),
                    "running": True,
                    "host": str(metadata.get("host", _DAEMON_HOST)),
                    "port": int(metadata["port"]),
                    "pid": int(metadata["pid"]),
                    "started_at": str(metadata["started_at"]),
                    "auto_started": True,
                    "response_cache_scope": _DAEMON_RESPONSE_CACHE_SCOPE,
                }
            time.sleep(0.05)
        captured = read_daemon_start_capture(capture_fd)
        raise daemon_start_failure(root, captured)
    finally:
        if capture_fd is not None and capture_path is not None:
            cleanup_daemon_start_capture(capture_fd, capture_path)
        _release_daemon_start_lock(root)


def maybe_autostart_session_daemon_nonblocking(path: str = ".") -> bool:
    """Fire-and-forget daemon spawn for the default Tier-1 fast path (task #94 Part A, must-fix 3).

    Unlike ``start_session_daemon``, this NEVER blocks the caller on the daemon's warmup: it
    makes a single non-blocking attempt to acquire the start lock, ``Popen``s the daemon if it
    got the lock, and returns immediately WITHOUT waiting for ``daemon.json`` to appear. The
    calling request must run cold (the daemon will be warm for the NEXT call, not this one).

    Double-spawn discipline (deliberately narrower than ``start_session_daemon``'s lock usage):
    the start lock is held only across the ``Popen`` call itself, not across a warmup wait --
    holding it any longer would make this function blocking, defeating its purpose. A losing
    racer (lock already held) simply returns False and does not spawn a second daemon. A THIRD
    caller arriving after the lock is released but before the just-spawned daemon's own
    ``daemon.json`` write completes could still observe ``_probe_daemon() is None`` and spawn
    ANOTHER daemon -- this narrow residual race is bounded and self-heals without a busier lock:
    each daemon binds an ephemeral port (``_DAEMON_HOST``, port 0), so there is never a port
    collision; each daemon's ``daemon.json`` write is atomic, so the LAST daemon to finish
    starting wins the metadata race and is the one future callers discover; and any orphaned
    duplicate (metadata overwritten, no longer discoverable) self-reaps via the existing
    idle-shutdown timer (``_DEFAULT_DAEMON_IDLE_SHUTDOWN_SECONDS``, 900s) once it stops
    receiving requests. Returns True only when THIS call actually issued a spawn.
    """
    root = _resolve_root(Path(path))
    if _probe_daemon(root) is not None:
        return False
    secret_reason = diagnose_user_secret()
    if secret_reason in {
        "secret_dir_untrusted_owner_or_acl",
        "secret_ancestor_untrusted",
        "secret_untrusted_present",
        "secret_extra_links",
    }:
        return False
    if not _try_acquire_daemon_start_lock(root):
        return False
    capture_fd: int | None = None
    capture_path: Path | None = None
    try:
        capture_fd, capture_path = _spawn_daemon_subprocess(root)
    finally:
        if capture_fd is not None and capture_path is not None:
            cleanup_daemon_start_capture(capture_fd, capture_path)
        _release_daemon_start_lock(root)
    return True


def stop_session_daemon(path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    metadata = _probe_daemon(root)
    if metadata is None:
        # audit I7: cooperative probe failed, but a stale daemon may still be running (e.g. its
        # socket is wedged). Fall back to terminating the recorded pid if it validates.
        meta_state, stale_metadata = _metadata_state(_daemon_metadata_path(root))
        if meta_state in ("unreadable", "invalid"):  # damage is not absence: keep the file
            return {
                "version": _SESSION_VERSION,
                "root": str(root),
                **_metadata_unconfirmed(meta_state),
            }
        # F-07: a daemon the probe rejected (version skew, ...) is stopped only after it PROVES it
        # serves this root; ineligible / unverifiable endpoints are never signalled or deleted.
        rejected, rejected_outcome = _stop_rejected_daemon(root, stale_metadata)
        if rejected is not None:
            return rejected
        # A listener that answered but could NOT prove it serves this root (e.g. another root's
        # credentials planted here) is never reached by the signal path unless the recorded pid is
        # independently attested as this root's daemon (the real function re-checks as well).
        unprovable = rejected_outcome == "unverified" and (
            _daemon_pid_state(stale_metadata, root) != "ours"
        )
        killed = not unprovable and _terminate_daemon_by_pid(stale_metadata, root=root)
        # Stale metadata is removed only after PROVEN absence: the process is gone ("gone") or, after
        # a delivered signal, the connection is refused. A live process we could not stop (ours but
        # termination failed) or could not prove to be ours is UNCONFIRMED: keep daemon.json.
        state = "gone" if killed else _daemon_pid_state(stale_metadata, root)
        # EXISTING metadata must be proven stale by a REFUSED connection before ANY removal (a dead
        # pid alone does not prove the listener is gone); a missing / null / invalid endpoint cannot
        # be checked, so it is unconfirmed too: keep daemon.json (see _stale_unconfirmed).
        blocked = _stale_unconfirmed(stale_metadata, state, _DAEMON_START_TIMEOUT_SECONDS)
        if blocked is not None:
            return {"version": _SESSION_VERSION, "root": str(root), **blocked}
        # Task #143a-a: only remove the metadata that still identifies the STALE daemon we just
        # targeted -- never whatever happens to be on disk by the time we get here. A concurrent
        # autostart elsewhere may have already spawned and published a healthy replacement's
        # daemon.json in the window between the failed probe above and this cleanup; an
        # unconditional unlink would delete that replacement's metadata too. When the stale
        # metadata itself cannot be identified (missing/corrupt), there is nothing safe to
        # remove -- skip rather than guess.
        stale_pid, stale_port = _daemon_identity(stale_metadata)
        if stale_pid is not None:
            _remove_daemon_metadata(root, expected_pid=stale_pid, expected_port=stale_port)
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            **_stale_success_fields(killed, stale_metadata is not None),
        }
    response: dict[str, Any]
    try:
        response = _daemon_request(
            str(metadata.get("host", _DAEMON_HOST)),
            metadata.get("port", 0),  # _daemon_request refuses a malformed port before connecting
            {"command": "stop"},
            token=_daemon_token(metadata),
        )
    except Exception:
        response = {"version": _SESSION_VERSION, "ok": False}
    # Only an ok:true reply can start a cooperative stop (a failed/unauthorized reply is no
    # evidence of anything), and even then it is not PROOF: see the refusal check below.
    stop_method = "cooperative" if response.get("ok") is True else "none"

    def _refused() -> bool:
        return _await_endpoint_refused(
            metadata.get("host", _DAEMON_HOST), metadata.get("port"), _DAEMON_START_TIMEOUT_SECONDS
        )

    delivered = False

    def _kill() -> bool:
        nonlocal delivered
        delivered = _terminate_daemon_by_pid(metadata, root=root) or delivered
        return delivered

    still_up = False
    deadline = monotonic() + _DAEMON_START_TIMEOUT_SECONDS
    while monotonic() < deadline:
        if _probe_daemon(root) is None:
            if stop_method == "none":
                if _kill():
                    stop_method = "pid"  # stop request failed: a None probe is ambiguous (wedged?)
                else:
                    still_up = _daemon_pid_state(metadata, root) != "gone"
            break
        time.sleep(0.05)
    else:
        # audit I7: no effect within the deadline; escalate to a validated terminate of the pid
        # PROVEN by the signed ping reply.
        stop_method = "pid" if _kill() else "none"
        still_up = stop_method == "none"  # still answering and never signalled
    # An ok reply / a delivered signal is not a stopped daemon: "cooperative" and "pid" are
    # reported only once a connection to the verified endpoint is REFUSED. An unproven cooperative
    # stop falls through to the proven-pid path (which needs the same refusal).
    if stop_method == "cooperative" and not _refused():
        stop_method = "pid" if _kill() else "none"
    if stop_method == "pid" and not _refused():
        stop_method = "none"
    if stop_method == "none" and (still_up or not _refused()):
        still_up = True
    if still_up:
        # Unconfirmed: keep daemon.json and say why.
        state = "gone" if delivered else _daemon_pid_state(metadata, root)
        response.update(root=str(root), **_unconfirmed_fields(state, delivered))
        return response
    # Task #143a-a: only remove daemon.json if it still identifies the SAME daemon this call
    # targeted (captured in `metadata` above) -- a replacement may have spawned and published its
    # own metadata in the window since. See _remove_daemon_metadata's docstring for the full race.
    target_pid, target_port = _daemon_identity(metadata)
    if target_pid is not None:
        _remove_daemon_metadata(root, expected_pid=target_pid, expected_port=target_port)
    return _stop_success(response, root, stop_method)


def _daemon_response_timeout() -> float:
    """Resolve the client read timeout, honoring TG_SESSION_DAEMON_RESPONSE_TIMEOUT_SECONDS. A
    non-positive / unparseable value falls back to the 60s default (never an instant/zero timeout)."""
    raw = os.environ.get(_DAEMON_RESPONSE_TIMEOUT_ENV)
    if raw is None:
        return _DAEMON_RESPONSE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DAEMON_RESPONSE_TIMEOUT_SECONDS
    return value if value > 0 else _DAEMON_RESPONSE_TIMEOUT_SECONDS


def request_session_daemon(path: str, request: dict[str, Any]) -> dict[str, Any]:
    status = start_session_daemon(path)
    # audit S3: read the token from the (0600) daemon.json the daemon just published so the
    # authenticated request reaches a daemon that now requires it.
    root = _resolve_root(Path(str(status.get("root", path))))
    token = _daemon_token(_read_daemon_metadata(root))
    return _daemon_request(
        str(status.get("host", _DAEMON_HOST)),
        int(status["port"]),
        request,
        response_timeout=_daemon_response_timeout(),
        token=token,
    )


def request_running_session_daemon(path: str, request: dict[str, Any]) -> dict[str, Any] | None:
    root = _resolve_root(Path(path))
    metadata = _probe_daemon(root)
    if metadata is None:
        return None
    return _daemon_request(
        str(metadata.get("host", _DAEMON_HOST)),
        int(metadata["port"]),
        request,
        response_timeout=_daemon_response_timeout(),
        token=_daemon_token(metadata),
    )


# --------------------------------------------------------------------------------------------
# tg-ledger step-0: demand instrumentation (docs/multi_agent_context_plane.md)
#
# Answers two questions with real traffic, not intuition, before any ledger/claims/A2A surface
# is built: (1) do multiple distinct agent processes actually hit the same daemon concurrently,
# and (2) do they actually re-request the same expensive artifact (symbol/query) within a short
# window that a shared plane would de-duplicate. This is diagnostic-only: it never changes a
# response, never stores raw symbol/query text (hash only), and a failure in `record()` must
# never break serving (callers wrap it in `except Exception: pass`).
# --------------------------------------------------------------------------------------------


_IMPLICIT_SESSION_SYMBOL_COMMANDS = frozenset({
    "defs",
    "impact",
    "refs",
    "callers",
    "blast_radius",
})
_TIER2_MAP_CAPSULE_COMMANDS = frozenset({"orient", "agent"})
_IMPLICIT_SESSION_COMMANDS = (
    frozenset({"context_render", "context_edit_plan"})
    | _IMPLICIT_SESSION_SYMBOL_COMMANDS
    | _TIER2_MAP_CAPSULE_COMMANDS
)
_ADDED_FILE_SENSITIVE_COMMANDS = _IMPLICIT_SESSION_SYMBOL_COMMANDS | _TIER2_MAP_CAPSULE_COMMANDS

from tensor_grep.cli.state import daemon_cache as _daemon_cache  # noqa: E402
from tensor_grep.cli.state import daemon_metrics as _daemon_metrics  # noqa: E402
from tensor_grep.cli.state import daemon_server as _daemon_server  # noqa: E402

_path_cache_key = _daemon_cache._path_cache_key
_request_cache_value = _daemon_cache._request_cache_value
_session_payload_fingerprint = _daemon_cache._session_payload_fingerprint
_context_edit_plan_response_cache_key = _daemon_cache._context_edit_plan_response_cache_key
_context_render_response_cache_key = _daemon_cache._context_render_response_cache_key
_symbol_command_response_cache_key = _daemon_cache._symbol_command_response_cache_key
_orient_response_cache_key = _daemon_cache._orient_response_cache_key
_agent_response_cache_key = _daemon_cache._agent_response_cache_key
_response_cache_key_for_command = _daemon_cache._response_cache_key_for_command
_session_payload_is_possibly_truncated = _daemon_cache._session_payload_is_possibly_truncated
_optional_positive_int = _daemon_cache._optional_positive_int
_implicit_session_max_repo_files = _daemon_cache._implicit_session_max_repo_files
_remove_implicit_session_payload = _daemon_cache._remove_implicit_session_payload
_implicit_session_id_for_request = _daemon_cache._implicit_session_id_for_request
_release_implicit_session_pin = _daemon_cache._release_implicit_session_pin
_reacquire_missing_implicit_session = _daemon_cache._reacquire_missing_implicit_session
_serve_daemon_response_with_cache = _daemon_cache._serve_daemon_response_with_cache
_metrics_file_path = _daemon_metrics._metrics_file_path
_metrics_target_hash = _daemon_metrics._metrics_target_hash
_metrics_target_for_request = _daemon_metrics._metrics_target_for_request
_utc_day_bucket = _daemon_metrics._utc_day_bucket
_normalize_client_pid = _daemon_metrics._normalize_client_pid
_DemandMetrics = _daemon_metrics._DemandMetrics
_write_demand_metrics = _daemon_metrics._write_demand_metrics
_flush_demand_metrics_if_dirty = _daemon_metrics._flush_demand_metrics_if_dirty
_read_demand_metrics_days = _daemon_metrics._read_demand_metrics_days
_demand_metrics_status_payload = _daemon_metrics._demand_metrics_status_payload
_attach_demand_metrics = _daemon_metrics._attach_demand_metrics
_ThreadedSessionDaemon = _daemon_server._ThreadedSessionDaemon
_SessionDaemonHandler = _daemon_server._SessionDaemonHandler
_run_daemon_lifecycle_monitor = _daemon_server._run_daemon_lifecycle_monitor
run_session_daemon_server = _daemon_server.run_session_daemon_server
_parse_args = _daemon_server._parse_args
main = _daemon_server.main


if __name__ == "__main__":
    raise SystemExit(main())
