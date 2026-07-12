from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from tensor_grep.cli._index_lock import IndexLockTimeoutError, index_lock, replace_with_retry
from tensor_grep.cli.runtime_paths import _expected_tg_version
from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
    _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
    _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
    _DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT,
    _SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV,
    _SESSION_VERSION,
    _configured_positive_int,
    _ensure_session_not_stale,
    _index_path,
    _json_size_bytes,
    _load_index,
    _resolve_request_session_target,
    _resolve_root,
    _session_health_payload,
    _session_payload_path,
    _sessions_dir,
    _SessionServeCache,
    _SessionServeResponseCacheEntry,
    _write_index,
    _write_json_atomic,
    open_session,
    refresh_session,
    serve_session_request,
)

_DAEMON_METADATA_FILE = "daemon.json"
_DAEMON_START_LOCK_FILE = ".daemon-start.lock"
_DAEMON_HOST = "127.0.0.1"
_DAEMON_CONNECT_TIMEOUT_SECONDS = 0.5
_DAEMON_RESPONSE_TIMEOUT_SECONDS = 60.0
# moat P0-6 step 5: the client-side socket read timeout for a daemon response is env-configurable so
# a large repo whose warm-daemon graph query legitimately needs >60s is NOT killed by a hard cap that
# returns a bare "timed out" / exit 1 / zero JSON (the recurring dogfood "60s cap errors" complaint).
# Full partial-at-deadline for the DAEMON path needs a separate traversal-deadline (the served graph
# commands run on the cached map, so the scan-deadline of steps 1-4 does not bound them) -- tracked.
_DAEMON_RESPONSE_TIMEOUT_ENV = "TG_SESSION_DAEMON_RESPONSE_TIMEOUT_SECONDS"
_DAEMON_START_TIMEOUT_SECONDS = 5.0
_DAEMON_SESSION_LOOKUP_RETRY_SECONDS = 0.25
_DAEMON_RESPONSE_CACHE_MAX_ENTRIES = 32
_DAEMON_IMPLICIT_SESSION_MAX_ENTRIES = 16
_DAEMON_RESPONSE_CACHE_SCOPE = (
    "daemon-routed top-level/session context-render/edit-plan/defs/impact/refs/callers/"
    "blast-radius requests"
)
_DAEMON_RESPONSE_CACHE_STALE_DETECTION = "snapshot_mtime_only"
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
    metadata_path = _daemon_metadata_path(root)
    if not metadata_path.exists():
        return None
    try:
        return cast(dict[str, Any], json.loads(metadata_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def _write_daemon_metadata(root: Path, payload: dict[str, Any]) -> None:
    from tensor_grep.cli.session_store import _write_json_atomic

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
    import getpass

    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:
        return
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False,
            capture_output=True,
            timeout=5,
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


def _daemon_start_lock_path(root: Path) -> Path:
    return _sessions_dir(root) / _DAEMON_START_LOCK_FILE


def _try_acquire_daemon_start_lock(root: Path) -> bool:
    lock_path = _daemon_start_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > _DAEMON_START_LOCK_STALE_SECONDS:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            return False
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
        finally:
            os.close(fd)
        return True
    return False


def _release_daemon_start_lock(root: Path) -> None:
    try:
        _daemon_start_lock_path(root).unlink()
    except OSError:
        pass


def _remove_daemon_metadata(root: Path) -> None:
    metadata_path = _daemon_metadata_path(root)
    try:
        metadata_path.unlink()
    except OSError:
        pass


def _pid_looks_like_tg_daemon(pid: int) -> bool:
    """Best-effort check that ``pid`` is a tensor-grep session daemon (audit I7).

    Uses psutil (a dev/optional dependency) to inspect the command line so the PID-kill
    fallback never terminates an unrelated process that happens to reuse the recorded pid. If
    psutil is unavailable we cannot prove identity and return ``False`` (skip the kill).
    """
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return False
    return "tensor_grep.cli.session_daemon" in cmdline


def _terminate_daemon_by_pid(metadata: dict[str, Any] | None) -> bool:
    """Terminate the daemon process recorded in ``metadata`` (audit I7).

    Only fires when the pid can be validated as a tensor-grep daemon. Returns True if a
    terminate signal was delivered.
    """
    if not metadata:
        return False
    try:
        pid = int(metadata["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid() or not _pid_looks_like_tg_daemon(pid):
        return False
    try:
        if os.name == "nt":
            import signal

            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, 15)
    except OSError:
        return False
    return True


def _daemon_request(
    host: str,
    port: int,
    request: dict[str, Any],
    *,
    response_timeout: float | None = _DAEMON_RESPONSE_TIMEOUT_SECONDS,
    token: str = "",
) -> dict[str, Any]:
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
        timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS,
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
    try:
        response = _daemon_request(
            str(metadata.get("host", _DAEMON_HOST)),
            int(metadata["port"]),
            {"command": "ping"},
            response_timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS,
            token=_daemon_token(metadata),
        )
    except Exception:
        return None
    if not response.get("ok"):
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
    return metadata


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
    metadata = _read_daemon_metadata(root)
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


def _spawn_daemon_subprocess(root: Path) -> None:
    """Popen the session-daemon child process for ``root`` and return immediately.

    Extracted from ``start_session_daemon`` (task #94 Part A) so the SAME spawn logic can be
    reused by the fire-and-forget ``maybe_autostart_session_daemon_nonblocking`` below without
    duplicating the Popen/PYTHONPATH/creationflags block. Pure side effect: does not wait for
    ``daemon.json`` to appear and does not touch the start-lock -- the caller is responsible for
    both (holding the lock across this call, and deciding whether/how long to wait afterward).
    """
    _remove_daemon_metadata(root)
    creationflags = 0
    env = os.environ.copy()
    # audit B20: only inject the editable-checkout 'src' onto PYTHONPATH when it actually
    # exists (an installed wheel has no sibling src/), and derive cwd from the session root
    # rather than assuming a fixed Path(__file__).parents[3] repo layout.
    repo_src = Path(__file__).resolve().parents[3] / "src"
    if repo_src.exists():
        python_path_parts = [str(repo_src)]
        if env.get("PYTHONPATH"):
            python_path_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    spawn_cwd = root if root.is_dir() else root.parent
    # audit I7: detach the daemon from the launching process group/console so it is not
    # killed by signals delivered to the parent and survives the CLI invocation.
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.session_daemon",
            "--root",
            str(root),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        cwd=str(spawn_cwd),
        env=env,
        **popen_kwargs,
    )


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
        deadline = time.time() + _DAEMON_START_TIMEOUT_SECONDS
        while time.time() < deadline:
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
        raise RuntimeError(f"session daemon did not start for {root}")

    try:
        _spawn_daemon_subprocess(root)

        deadline = time.time() + _DAEMON_START_TIMEOUT_SECONDS
        while time.time() < deadline:
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
        raise RuntimeError(f"session daemon did not start for {root}")
    finally:
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
    if not _try_acquire_daemon_start_lock(root):
        return False
    try:
        _spawn_daemon_subprocess(root)
    finally:
        _release_daemon_start_lock(root)
    return True


def stop_session_daemon(path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    metadata = _probe_daemon(root)
    if metadata is None:
        # audit I7: cooperative probe failed, but a stale daemon may still be running (e.g. its
        # socket is wedged). Fall back to terminating the recorded pid if it validates.
        stale_metadata = _read_daemon_metadata(root)
        killed = _terminate_daemon_by_pid(stale_metadata)
        _remove_daemon_metadata(root)
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "running": False,
            "stopped": killed,
            "stop_method": "pid" if killed else "none",
        }
    response: dict[str, Any]
    stop_method = "cooperative"
    try:
        response = _daemon_request(
            str(metadata.get("host", _DAEMON_HOST)),
            int(metadata["port"]),
            {"command": "stop"},
            token=_daemon_token(metadata),
        )
    except Exception:
        response = {"version": _SESSION_VERSION, "ok": False}
        stop_method = "none"
    deadline = time.time() + _DAEMON_START_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _probe_daemon(root) is None:
            break
        time.sleep(0.05)
    else:
        # audit I7: cooperative stop did not take effect within the deadline; escalate to a
        # validated pid terminate so a wedged daemon is not left running.
        if _terminate_daemon_by_pid(metadata):
            stop_method = "pid"
    _remove_daemon_metadata(root)
    response["running"] = False
    response["root"] = str(root)
    response["stopped"] = True
    response["stop_method"] = stop_method
    return response


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


def _load_payload_with_status_retry(
    cache: _SessionServeCache, session_id: str, path: str
) -> tuple[dict[str, Any], str]:
    deadline = time.time() + _DAEMON_SESSION_LOOKUP_RETRY_SECONDS
    while True:
        try:
            return cache.load_with_status(session_id, path)
        except (FileNotFoundError, json.JSONDecodeError):
            if time.time() >= deadline:
                raise
            time.sleep(0.05)


def _path_cache_key(path: str) -> str:
    resolved = str(_resolve_root(Path(path)))
    return resolved.lower() if sys.platform.startswith("win") else resolved


def _request_cache_value(
    request: dict[str, Any],
    name: str,
    default: Any = "",
) -> str:
    value = request.get(name, default)
    if value in (None, ""):
        return ""
    return str(value)


def _session_payload_fingerprint(payload: dict[str, Any]) -> tuple[str, ...]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    return (
        str(payload.get("root", "")),
        str(payload.get("created_at", "")),
        str(payload.get("refreshed_at", "")),
        str(len(cast(list[Any], repo_map.get("files", [])))),
        str(len(cast(list[Any], repo_map.get("symbols", [])))),
    )


def _context_edit_plan_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    return (
        _path_cache_key(path),
        session_id,
        *_session_payload_fingerprint(payload),
        str(request.get("query", "")).strip(),
        _request_cache_value(request, "max_files", 3),
        _request_cache_value(request, "max_sources"),
        _request_cache_value(request, "max_tokens"),
        _request_cache_value(request, "max_symbols", 5),
        _request_cache_value(
            request,
            "max_repo_files",
            _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
        ),
    )


def _context_render_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    return (
        _path_cache_key(path),
        session_id,
        *_session_payload_fingerprint(payload),
        str(request.get("query", "")).strip(),
        _request_cache_value(request, "max_files", 3),
        _request_cache_value(request, "max_sources", 5),
        _request_cache_value(request, "max_symbols_per_file", 6),
        _request_cache_value(request, "max_render_chars"),
        _request_cache_value(request, "max_tokens"),
        _request_cache_value(request, "model"),
        _request_cache_value(request, "optimize_context", False),
        _request_cache_value(request, "render_profile", "full"),
        _request_cache_value(request, "profile", False),
        _request_cache_value(
            request,
            "max_repo_files",
            _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
        ),
    )


def _symbol_command_response_cache_key(
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    # audit #113: one key function shared by all 5 symbol commands (defs/impact/refs/callers/
    # blast_radius), so `command` MUST be a key field -- otherwise a `callers(foo)` lookup could
    # serve a cached `defs(foo)` response (cross-command response bleed). Completeness of the
    # REST of this tuple is equally load-bearing: a missing field (provider/max_tests/max_depth/
    # max_repo_files) would let two requests that differ only in that field collide on the same
    # cached answer (cross-request response bleed) -- see _symbol_command_response_cache_key's
    # sibling _context_render_response_cache_key above for the same discipline.
    return (
        _path_cache_key(path),
        session_id,
        *_session_payload_fingerprint(payload),
        command,
        str(request.get("symbol", "")).strip(),
        _request_cache_value(request, "provider", "native"),
        _request_cache_value(request, "max_tests"),
        _request_cache_value(request, "max_depth", 3),
        _request_cache_value(
            request,
            "max_repo_files",
            _DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT,
        ),
    )


def _response_cache_key_for_command(
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...] | None:
    if command == "context_render":
        return _context_render_response_cache_key(session_id, path, request, payload)
    if command == "context_edit_plan":
        return _context_edit_plan_response_cache_key(session_id, path, request, payload)
    if command in _IMPLICIT_SESSION_SYMBOL_COMMANDS:
        return _symbol_command_response_cache_key(command, session_id, path, request, payload)
    return None


def _session_payload_is_possibly_truncated(payload: dict[str, Any]) -> bool:
    repo_map = payload.get("repo_map")
    candidates: list[Any] = [payload.get("scan_limit")]
    if isinstance(repo_map, dict):
        candidates.append(repo_map.get("scan_limit"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("possibly_truncated") is True:
            return True
    return False


def _optional_positive_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(1, int(cast(int | str, value)))
    except (TypeError, ValueError):
        return None


# task #94 Part A -- the CORE SCOPE-FIX. Before this, _implicit_session_id_for_request only
# recognized context_render/context_edit_plan: a symbol command (defs/impact/refs/callers/
# blast_radius) sent with no explicit session_id fell through with session_id="" unchanged,
# which reaches get_session("", path) -> FileNotFoundError (session_store.py get_session,
# ~line 810) -> the daemon returns an {"error": ...} response -> every fail-open client wrapper
# (_maybe_symbol_command_via_running_daemon in main.py) reads that as a miss and falls back to
# the cold path FOREVER. That silently defeated the whole point of a default warm-daemon fast
# path for these 5 commands. _IMPLICIT_SESSION_SYMBOL_COMMANDS is deliberately narrower than
# _DAEMON_METRICS_SYMBOL_COMMANDS above (which also counts blast_radius_render/
# blast_radius_plan for demand metrics) -- those two render/plan variants are Tier-2 scope,
# explicitly deferred, and must not gain an implicit session as a side effect of this fix.
_IMPLICIT_SESSION_SYMBOL_COMMANDS = frozenset({
    "defs",
    "impact",
    "refs",
    "callers",
    "blast_radius",
})
_IMPLICIT_SESSION_COMMANDS = frozenset({"context_render", "context_edit_plan"}) | (
    _IMPLICIT_SESSION_SYMBOL_COMMANDS
)


def _implicit_session_max_repo_files(command: str, request: dict[str, Any]) -> int | None:
    requested = _optional_positive_int(request.get("max_repo_files"))
    if requested is not None:
        return requested
    if command == "context_render":
        return _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT
    if command == "context_edit_plan":
        return _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT
    if command in _IMPLICIT_SESSION_SYMBOL_COMMANDS:
        return _DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT
    return None


def _remove_implicit_session_payload(path: str, session_id: str) -> None:
    root = _resolve_root(Path(path))
    try:
        # Round-6/7 r3: serialize the index read-modify-write under the SAME cross-process lock
        # open_session / refresh_session use (session_store.py:617/694). Without it, this unlocked
        # load->filter->write races a concurrent locked insert: this process reads the index, the
        # other inserts a new session record, this process writes back the filtered set WITHOUT
        # that record -> a silently lost session entry (orphaned payload, never retention-pruned).
        with index_lock(_index_path(root)):
            _session_payload_path(root, session_id).unlink(missing_ok=True)
            records = [record for record in _load_index(root) if record.session_id != session_id]
            _write_index(root, records)
    except (OSError, ValueError, IndexLockTimeoutError):
        # ValueError: a traversal-shaped session_id is refused by _session_payload_path.
        # IndexLockTimeoutError: this implicit cleanup is best-effort -- under sustained lock
        # contention, skip it (retention prunes the record later) rather than crash the eviction
        # path; we never WROTE, so no insert is lost by skipping.
        pass


def _implicit_session_id_for_request(
    server: Any,
    *,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
) -> str:
    if session_id or command not in _IMPLICIT_SESSION_COMMANDS:
        return session_id

    max_repo_files = _implicit_session_max_repo_files(command, request)
    key = (_path_cache_key(path), str(max_repo_files or ""))
    with server._implicit_session_lock:
        existing = server.implicit_session_ids.pop(key, None)
        if existing:
            server.implicit_session_ids[key] = existing
            return str(existing)
        opened = open_session(
            path,
            max_repo_files=max_repo_files,
        )
        server.implicit_session_ids[key] = opened.session_id
        while len(server.implicit_session_ids) > _DAEMON_IMPLICIT_SESSION_MAX_ENTRIES:
            evicted_key, evicted_session_id = server.implicit_session_ids.popitem(last=False)
            _remove_implicit_session_payload(evicted_key[0], evicted_session_id)
        return opened.session_id


def _serve_daemon_response_with_cache(
    *,
    server: Any,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    session_request = dict(request)
    if bool(session_request.get("refresh_on_stale")) and _session_payload_is_possibly_truncated(
        payload
    ):
        # Truncated snapshots validate included files below; added-file detection would
        # treat files omitted by the cap as stale and prevent response-cache writes.
        session_request["refresh_on_stale"] = False
    response_cache_key = _response_cache_key_for_command(
        command, session_id, path, session_request, payload
    )
    if response_cache_key is None:
        return serve_session_request(session_id, session_request, path, payload=payload), "bypass"

    # audit #113 trap #1: context_render/context_edit_plan intentionally use
    # detect_added_files=False here (see docs/CONTRACTS.md) so a cache HIT never pays for a
    # directory walk -- new files stay invisible to those two commands until an explicit
    # refresh. The 5 symbol commands are a correctness-sensitive code-intelligence surface (a
    # blast-radius/callers answer that silently omits a newly-added call site is a WRONG
    # answer, not just a stale one), so they must honor the caller's real refresh_on_stale flag
    # here instead of copying the context-command False -- a new call site in a brand-new file
    # (nothing about any already-tracked file changes) must still bust the cache.
    detect_added_files = (
        bool(session_request.get("refresh_on_stale", False))
        if command in _IMPLICIT_SESSION_SYMBOL_COMMANDS
        else False
    )
    _ensure_session_not_stale(payload, detect_added_files=detect_added_files)
    with server._response_cache_lock:
        cached_response = server.response_cache.get(response_cache_key)
    if cached_response is not None:
        cached_response.pop("serve_response_cache", None)
        return cached_response, "hit"

    if detect_added_files:
        # The gate above already walked for added files and proved `payload` fresh for THIS
        # request -- flip refresh_on_stale off before the miss-path recompute so
        # serve_session_request's own internal _ensure_session_not_stale (session_store.py)
        # does not re-walk the same added-file probe a second time in one request (mirrors the
        # truncated-snapshot flip above). Guarded on detect_added_files (not unconditional):
        # context_render/context_edit_plan never ran the walk above (detect_added_files=False
        # for them, by design), so for THEM the inner call below is the ONLY place a miss
        # discovers an added file -- flipping the flag unconditionally would silently disable
        # that discovery (regression caught by
        # test_session_daemon_refresh_on_added_file_response_is_cached).
        session_request["refresh_on_stale"] = False
    response = serve_session_request(session_id, session_request, path, payload=payload)
    with server._response_cache_lock:
        server.response_cache.put(response_cache_key, response)
    return response, "miss"


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


def _metrics_file_path(root: Path) -> Path:
    return _sessions_dir(root) / _DAEMON_METRICS_FILE


def _metrics_target_hash(command: str, target: str) -> str:
    digest = hashlib.sha256(f"{command}\x00{target}".encode()).hexdigest()
    return digest[:16]


def _metrics_target_for_request(command: str, request: dict[str, Any]) -> str:
    if command in _DAEMON_METRICS_SYMBOL_COMMANDS:
        return str(request.get("symbol", "")).strip().lower()
    if command in _DAEMON_METRICS_QUERY_COMMANDS:
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


def _sanitize_metrics_days(raw: object) -> dict[str, dict[str, Any]]:
    """Defensively normalize a loaded (possibly hand-edited or stale-schema) days dict."""
    sanitized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return sanitized
    for day, bucket in raw.items():
        if not isinstance(day, str) or not isinstance(bucket, dict):
            continue
        dup_targets_raw = bucket.get("dup_targets")
        dup_targets = (
            {str(key): int(cast(Any, value)) for key, value in dup_targets_raw.items()}
            if isinstance(dup_targets_raw, dict)
            else {}
        )
        by_command_raw = bucket.get("by_command")
        by_command = (
            {str(key): int(cast(Any, value)) for key, value in by_command_raw.items()}
            if isinstance(by_command_raw, dict)
            else {}
        )
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
            except (TypeError, ValueError):
                clean[field] = 0
        clean["dup_targets"] = dup_targets
        clean["by_command"] = by_command
        sanitized[day] = clean
    return sanitized


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
            self._days = _sanitize_metrics_days(days)

    def record(self, *, command: str, client_pid: object, request: dict[str, Any]) -> None:
        if command in _DAEMON_METRICS_EXCLUDED_COMMANDS or not _daemon_metrics_enabled():
            return
        now_monotonic = monotonic()
        now_wall = time.time()
        day = _utc_day_bucket(now_wall)
        pid = _normalize_client_pid(client_pid)
        with self._lock:
            bucket = self._days.setdefault(day, _empty_metrics_day_bucket())
            bucket["requests"] += 1
            by_command = cast(dict[str, int], bucket["by_command"])
            by_command[command] = by_command.get(command, 0) + 1

            if pid is not None:
                stale = [
                    seen_pid
                    for seen_pid, last_seen in self._clients.items()
                    if now_monotonic - last_seen > _DAEMON_METRICS_CLIENT_WINDOW_SECONDS
                ]
                for seen_pid in stale:
                    del self._clients[seen_pid]
                self._clients[pid] = now_monotonic
                concurrent = len(self._clients)
                bucket["max_concurrent_distinct_clients"] = max(
                    cast(int, bucket["max_concurrent_distinct_clients"]), concurrent
                )
                day_pids = self._day_pid_sets.setdefault(day, set())
                if len(day_pids) < _DAEMON_METRICS_DAY_PID_SET_CAP:
                    day_pids.add(pid)
                bucket["distinct_client_pids"] = max(
                    cast(int, bucket["distinct_client_pids"]), len(day_pids)
                )
                if concurrent >= 2:
                    bucket["overlap_events"] += 1

            if command in _DAEMON_METRICS_EXPENSIVE_COMMANDS:
                bucket["expensive_requests"] += 1
                target = _metrics_target_for_request(command, request)
                target_hash = _metrics_target_hash(command, target)
                last_seen_wall = self._dup_lru.get(target_hash)
                if (
                    last_seen_wall is not None
                    and (now_wall - last_seen_wall) <= _DAEMON_METRICS_DUP_WINDOW_SECONDS
                ):
                    bucket["dup_requests"] += 1
                    dup_targets = cast(dict[str, int], bucket["dup_targets"])
                    if target_hash in dup_targets:
                        dup_targets[target_hash] += 1
                    elif len(dup_targets) < _DAEMON_METRICS_MAX_DUP_TARGETS_PER_DAY:
                        dup_targets[target_hash] = 1
                self._dup_lru[target_hash] = now_wall
                self._dup_lru.move_to_end(target_hash)
                while len(self._dup_lru) > _DAEMON_METRICS_DUP_LRU_MAX_ENTRIES:
                    self._dup_lru.popitem(last=False)

            self._dirty = True

    def consume_dirty(self) -> bool:
        with self._lock:
            was_dirty = self._dirty
            self._dirty = False
            return was_dirty

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if len(self._days) > _DAEMON_METRICS_MAX_DAY_BUCKETS:
                newest_days = sorted(self._days)[-_DAEMON_METRICS_MAX_DAY_BUCKETS:]
                self._days = {day: self._days[day] for day in newest_days}
            return copy.deepcopy(self._days)


def _write_demand_metrics(root: Path, metrics: _DemandMetrics) -> None:
    payload = {
        "version": _DAEMON_METRICS_SCHEMA_VERSION,
        "root": str(root),
        "days": metrics.snapshot(),
    }
    _write_json_atomic(_metrics_file_path(root), payload)


def _flush_demand_metrics_if_dirty(server: _ThreadedSessionDaemon, *, force: bool = False) -> None:
    dirty = server.demand_metrics.consume_dirty()
    if not (force or dirty):
        return
    try:
        _write_demand_metrics(server.root, server.demand_metrics)
    except Exception:
        pass  # metrics persistence must never crash the daemon


def _read_demand_metrics_days(root: Path) -> dict[str, Any]:
    path = _metrics_file_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    days = data.get("days") if isinstance(data, dict) else None
    return days if isinstance(days, dict) else {}


def _demand_metrics_status_payload(
    root: Path, *, reference: datetime | None = None
) -> dict[str, Any]:
    """Read ``daemon_metrics.json`` from disk (works even with the daemon STOPPED) and roll the
    trailing 14 days up into a build-decision-ready summary for the tg-ledger step-0 gate.
    """
    days = _sanitize_metrics_days(_read_demand_metrics_days(root))
    now = reference or datetime.now(UTC)
    cutoff_14 = (now - timedelta(days=_DAEMON_METRICS_ROLLUP_WINDOW_DAYS)).date()
    cutoff_7 = (now - timedelta(days=_DAEMON_METRICS_ROLLUP_SHORT_WINDOW_DAYS)).date()

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
        and days_with_2plus_concurrent >= _DAEMON_METRICS_PRE_GATE_MIN_CONCURRENT_DAYS
        and dup_requests_14d >= _DAEMON_METRICS_PRE_GATE_MIN_DUP_REQUESTS_14D
    )

    return {
        "window_days": _DAEMON_METRICS_ROLLUP_WINDOW_DAYS,
        "days_covered": days_covered,
        "days_with_2plus_concurrent": days_with_2plus_concurrent,
        "overlap_events_14d": overlap_events_14d,
        "dup_requests_7d": dup_requests_7d,
        "dup_requests_14d": dup_requests_14d,
        "max_distinct_client_pids_14d": max_distinct_client_pids_14d,
        "pre_gate_met": pre_gate_met,
        "pre_gate_thresholds": {
            "min_concurrent_days": _DAEMON_METRICS_PRE_GATE_MIN_CONCURRENT_DAYS,
            "min_dup_requests_14d": _DAEMON_METRICS_PRE_GATE_MIN_DUP_REQUESTS_14D,
        },
    }


def _attach_demand_metrics(status: dict[str, Any], metrics_root: Path) -> dict[str, Any]:
    try:
        status["demand_metrics"] = _demand_metrics_status_payload(metrics_root)
    except Exception:
        # Read-back must never break `tg doctor` / `tg session daemon status` (fail-open,
        # mirrors the record()-side contract).
        status["demand_metrics"] = {"error": "unavailable"}
    return status


class _SessionResponseCache:
    def __init__(
        self,
        max_entries: int = _DAEMON_RESPONSE_CACHE_MAX_ENTRIES,
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
        self._lock = threading.RLock()

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            self._entries[key] = entry
            return copy.deepcopy(entry.payload)

    def put(self, key: tuple[str, ...], response: dict[str, Any]) -> None:
        with self._lock:
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
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def puts(self) -> int:
        with self._lock:
            return self._puts

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def oversized_skips(self) -> int:
        with self._lock:
            return self._oversized_skips


class _ThreadedSessionDaemon(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, root: Path, server_address: tuple[str, int], *, token: str = "") -> None:
        super().__init__(server_address, _SessionDaemonHandler)
        self.root = root
        # audit S3: shared secret every client must present before any command is dispatched.
        self.token = token
        self.payload_cache = _SessionServeCache()
        self.response_cache = _SessionResponseCache()
        # tg-ledger step-0: demand instrumentation, see docs/multi_agent_context_plane.md.
        self.demand_metrics = _DemandMetrics()
        self.implicit_session_ids: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.started_at = monotonic()
        self.request_count = 0
        # audit I7: track last client activity so an idle daemon can shut itself down.
        self.last_activity_at = monotonic()
        # audit r8: count in-flight (dispatched, not-yet-completed) requests so the lifecycle
        # monitor never tears the daemon down mid-request. Guarded by _request_lock.
        self.inflight_requests = 0
        self._request_lock = threading.Lock()
        self._response_cache_lock = threading.Lock()
        self._implicit_session_lock = threading.Lock()

    def note_activity(self) -> None:
        # audit I7: bump the idle clock on every authenticated request.
        with self._request_lock:
            self.last_activity_at = monotonic()

    def is_authorized(self, request: dict[str, Any]) -> bool:
        # audit S3: constant-time compare to avoid leaking the token via timing.
        if not self.token:
            return True
        provided = request.get(_DAEMON_TOKEN_FIELD)
        if not isinstance(provided, str) or not provided:
            return False
        return hmac.compare_digest(provided, self.token)


def _read_bounded_request_line(
    rfile: Any, max_bytes: int = _MAX_DAEMON_REQUEST_BYTES
) -> str | None:
    """Read one request line from an untrusted client, bounded to ``max_bytes``.

    audit (round-3 pre-auth DoS): ``rfile.readline()`` with no size argument buffers the
    entire line into memory before the caller can authenticate, so a hostile local client
    can stream unbounded bytes with no newline and exhaust the daemon. Reading ``max_bytes + 1``
    caps the allocation, and a line that exceeds the cap (or a read that errors/times out) is
    refused as ``None`` before any parse or token check. Returns the decoded, stripped line, or
    ``None`` when the client sent nothing, exceeded the cap, or the read failed.
    """
    try:
        raw: bytes = rfile.readline(max_bytes + 1)
    except (TimeoutError, OSError):
        return None
    if not raw or len(raw) > max_bytes:
        return None
    return raw.decode("utf-8", errors="replace").strip()


class _SessionDaemonHandler(socketserver.StreamRequestHandler):
    # audit (round-3 pre-auth DoS): time out a silent/slow client so it cannot pin a worker
    # thread indefinitely before authenticating. StreamRequestHandler.setup() applies this via
    # connection.settimeout().
    timeout = _DAEMON_HANDLER_TIMEOUT_SECONDS

    def handle(self) -> None:
        server = cast(_ThreadedSessionDaemon, self.server)
        line = _read_bounded_request_line(self.rfile)
        if not line:
            return
        response: dict[str, Any]
        with server._request_lock:
            server.request_count += 1
        request_session_id = ""
        request_path = str(server.root)
        inflight_incremented = False

        try:
            request = cast(dict[str, Any], json.loads(line))
            # audit S3: authenticate before dispatching any command or resolving any path.
            if not server.is_authorized(request):
                response = {
                    "version": _SESSION_VERSION,
                    "session_id": "",
                    "error": {"code": "unauthorized", "message": "invalid or missing daemon token"},
                }
                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()
                return
            server.note_activity()
            # audit r8: mark this request in-flight (after auth, so unauthorized clients don't
            # count) so the lifecycle monitor won't shut the daemon down mid-dispatch.
            with server._request_lock:
                server.inflight_requests += 1
            inflight_incremented = True
            session_id = str(request.get("session_id", "")).strip()
            request_path = _resolve_daemon_request_path(
                server.root,
                request.get("path", request.get("root", str(server.root))),
            )
            request = dict(request)
            request["path"] = request_path
            request_session_id, request_path = _resolve_request_session_target(
                request, session_id, request_path
            )
            command = str(request.get("command", "")).strip().lower()
            try:
                server.demand_metrics.record(
                    command=command,
                    client_pid=request.get("client_pid"),
                    request=request,
                )
            except Exception:
                pass  # tg-ledger step-0 metrics must never break serving
            request_session_id = _implicit_session_id_for_request(
                server,
                command=command,
                session_id=request_session_id,
                path=request_path,
                request=request,
            )
            if request_session_id:
                request["session_id"] = request_session_id

            if command == "stop":
                response = {"version": _SESSION_VERSION, "ok": True, "stopping": True}
                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            if command == "ping":
                response = {"version": _SESSION_VERSION, "ok": True}
                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()
                return

            if command == "stats":
                response = {
                    "version": _SESSION_VERSION,
                    "ok": True,
                    "cache_hits": server.payload_cache.hits,
                    "cache_misses": server.payload_cache.misses,
                    "refresh_count": server.payload_cache.refreshes,
                    "root_count": server.payload_cache.root_count,
                    "session_count": server.payload_cache.session_count,
                    "sessions": server.payload_cache.sessions,
                    "cache_size_bytes": server.payload_cache.size_bytes,
                    "response_cache_hits": server.response_cache.hits,
                    "response_cache_misses": server.response_cache.misses,
                    "response_cache_puts": server.response_cache.puts,
                    "response_cache_entries": server.response_cache.entry_count,
                    "response_cache_size_bytes": server.response_cache.size_bytes,
                    "response_cache_max_size_bytes": server.response_cache.max_size_bytes,
                    "response_cache_oversized_skips": server.response_cache.oversized_skips,
                    "response_cache_scope": _DAEMON_RESPONSE_CACHE_SCOPE,
                    "response_cache_stale_detection": _DAEMON_RESPONSE_CACHE_STALE_DETECTION,
                    "response_cache_added_file_detection": _DAEMON_RESPONSE_CACHE_ADDED_FILE_DETECTION,
                    "response_cache_refresh_hint": (
                        "Use tg session refresh or request refresh_on_stale when new files must "
                        "invalidate daemon response-cache hits."
                    ),
                    "uptime_seconds": max(0.0, monotonic() - server.started_at),
                    "request_count": server.request_count,
                    "inflight_requests": server.inflight_requests,
                }
            elif command == "health":
                payload, cache_status = _load_payload_with_status_retry(
                    server.payload_cache,
                    request_session_id,
                    request_path,
                )
                response = _session_health_payload(request_session_id, payload)
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
                }
            else:
                overall_started_at = monotonic()
                response_cache_status = "bypass"
                try:
                    load_started_at = monotonic()
                    payload, cache_status = _load_payload_with_status_retry(
                        server.payload_cache,
                        request_session_id,
                        request_path,
                    )
                    loaded_at = monotonic()
                    response, response_cache_status = _serve_daemon_response_with_cache(
                        server=server,
                        command=command,
                        session_id=request_session_id,
                        path=request_path,
                        request=request,
                        payload=payload,
                    )
                    served_at = monotonic()
                except Exception:
                    refresh_on_stale = bool(request.get("refresh_on_stale", False))
                    if not refresh_on_stale:
                        raise
                    load_started_at = monotonic()
                    refresh_session(
                        request_session_id,
                        request_path,
                        payload_cache=server.payload_cache,
                    )
                    server.payload_cache.record_refresh()
                    payload, cache_status = _load_payload_with_status_retry(
                        server.payload_cache,
                        request_session_id,
                        request_path,
                    )
                    loaded_at = monotonic()
                    response, response_cache_status = _serve_daemon_response_with_cache(
                        server=server,
                        command=command,
                        session_id=request_session_id,
                        path=request_path,
                        request=request,
                        payload=payload,
                    )
                    served_at = monotonic()
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
                }
                # audit #113: observability now covers all 7 response-cacheable commands (the
                # original context_render/context_edit_plan plus the 5 symbol commands) -- the
                # SAME set _response_cache_key_for_command treats as cacheable. session_timing's
                # build_metric naming below stays scoped to the original 2 (out of scope here).
                if command in _IMPLICIT_SESSION_SYMBOL_COMMANDS or command in {
                    "context_edit_plan",
                    "context_render",
                }:
                    response["daemon_response_cache"] = {
                        "status": response_cache_status,
                        "entries": server.response_cache.entry_count,
                        "hits": server.response_cache.hits,
                        "misses": server.response_cache.misses,
                        "size_bytes": server.response_cache.size_bytes,
                        "max_size_bytes": server.response_cache.max_size_bytes,
                        "oversized_skips": server.response_cache.oversized_skips,
                    }
                if command in {"context_edit_plan", "context_render"}:
                    build_metric = (
                        "build_context_render_seconds"
                        if command == "context_render"
                        else "build_edit_plan_seconds"
                    )
                    response["session_timing"] = {
                        "cache_status": cache_status,
                        "response_cache_status": response_cache_status,
                        "load_session_seconds": max(0.0, loaded_at - load_started_at),
                        build_metric: max(0.0, served_at - loaded_at),
                        "total_seconds": max(0.0, served_at - overall_started_at),
                    }
        except Exception as exc:
            response = {
                "version": _SESSION_VERSION,
                "session_id": request_session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        finally:
            if inflight_incremented:
                with server._request_lock:
                    server.inflight_requests -= 1

        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
        self.wfile.flush()


def _run_daemon_lifecycle_monitor(
    server: _ThreadedSessionDaemon, stop_event: threading.Event
) -> None:
    """Self-shutdown the daemon when it goes idle or exceeds its max uptime (audit I7)."""
    idle_limit = _configured_lifecycle_seconds(
        _DAEMON_IDLE_SHUTDOWN_SECONDS_ENV, _DEFAULT_DAEMON_IDLE_SHUTDOWN_SECONDS
    )
    max_uptime = _configured_lifecycle_seconds(
        _DAEMON_MAX_UPTIME_SECONDS_ENV, _DEFAULT_DAEMON_MAX_UPTIME_SECONDS
    )
    if idle_limit <= 0 and max_uptime <= 0:
        return
    # tg-ledger step-0: piggyback a low-frequency demand-metrics flush on this same poll loop
    # (~once per _DAEMON_METRICS_FLUSH_POLL_INTERVALS ticks) rather than a dedicated thread.
    flush_countdown = _DAEMON_METRICS_FLUSH_POLL_INTERVALS
    while not stop_event.wait(_DAEMON_LIFECYCLE_POLL_SECONDS):
        flush_countdown -= 1
        if flush_countdown <= 0:
            flush_countdown = _DAEMON_METRICS_FLUSH_POLL_INTERVALS
            _flush_demand_metrics_if_dirty(server)
        now = monotonic()
        with server._request_lock:
            idle_for = now - server.last_activity_at
            inflight = server.inflight_requests
        uptime = now - server.started_at
        idle_reached = idle_limit > 0 and idle_for >= idle_limit
        uptime_reached = max_uptime > 0 and uptime >= max_uptime
        if not (idle_reached or uptime_reached):
            continue
        # audit r8: never tear the daemon down while a request is in flight -- that resets the
        # client mid-dispatch. Wait for in-flight work to drain. Bound the wait ONLY for the hard
        # max-uptime cap (a wedged request must not postpone shutdown forever); the idle path has
        # no such hard deadline, so it simply waits until inflight == 0.
        if inflight > 0:
            past_hard_drain = (
                max_uptime > 0 and uptime >= max_uptime + _DAEMON_SHUTDOWN_DRAIN_GRACE_SECONDS
            )
            if not past_hard_drain:
                continue
        threading.Thread(target=server.shutdown, daemon=True).start()
        return


def run_session_daemon_server(path: str = ".") -> None:
    root = _resolve_root(Path(path))
    # audit S3: generate a per-daemon token and publish it (0600) so only local clients that can
    # read daemon.json may issue commands.
    token = secrets.token_urlsafe(32)
    with _ThreadedSessionDaemon(root, (_DAEMON_HOST, 0), token=token) as server:
        # tg-ledger step-0: load any prior demand-metrics history for this root before serving,
        # so a daemon restart never clobbers the day-bucket counts a prior run already persisted.
        server.demand_metrics.load(_read_demand_metrics_days(root))
        host, port = cast(tuple[str, int], server.server_address)
        _write_daemon_metadata(
            root,
            {
                "version": _SESSION_VERSION,
                # Task #94 PR-1: the installed tensor-grep PACKAGE version (distinct from
                # `version` above, which is the payload-schema version) -- see the matching
                # `_probe_daemon` skew check for why this is needed.
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": host,
                "port": int(port),
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
                _DAEMON_TOKEN_FIELD: token,
            },
        )
        stop_event = threading.Event()
        lifecycle_thread = threading.Thread(
            target=_run_daemon_lifecycle_monitor,
            args=(server, stop_event),
            daemon=True,
        )
        lifecycle_thread.start()
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            stop_event.set()
            _flush_demand_metrics_if_dirty(server, force=True)
            _remove_daemon_metadata(root)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    run_session_daemon_server(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
