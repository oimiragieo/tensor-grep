from __future__ import annotations

import argparse
import copy
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
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

from tensor_grep.cli.session_store import (
    _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
    _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
    _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
    _SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV,
    _SESSION_VERSION,
    _configured_positive_int,
    _ensure_session_not_stale,
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
    open_session,
    refresh_session,
    serve_session_request,
)

_DAEMON_METADATA_FILE = "daemon.json"
_DAEMON_START_LOCK_FILE = ".daemon-start.lock"
_DAEMON_HOST = "127.0.0.1"
_DAEMON_CONNECT_TIMEOUT_SECONDS = 0.5
_DAEMON_RESPONSE_TIMEOUT_SECONDS = 60.0
_DAEMON_START_TIMEOUT_SECONDS = 5.0
_DAEMON_SESSION_LOOKUP_RETRY_SECONDS = 0.25
_DAEMON_RESPONSE_CACHE_MAX_ENTRIES = 32
_DAEMON_IMPLICIT_SESSION_MAX_ENTRIES = 16
_DAEMON_RESPONSE_CACHE_SCOPE = "daemon-routed top-level/session context-render/edit-plan requests"
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
    _write_json_atomic(_daemon_metadata_path(root), payload, mode=_DAEMON_METADATA_MODE)


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
            return _merge_live_daemon_stats(
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
            )
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "discovered": False,
            "running": False,
        }
    live = _probe_daemon(root)
    if live is None:
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "discovered": False,
            "running": False,
            "stale_metadata": True,
        }
    return _merge_live_daemon_stats(
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


def _implicit_session_max_repo_files(command: str, request: dict[str, Any]) -> int | None:
    requested = _optional_positive_int(request.get("max_repo_files"))
    if requested is not None:
        return requested
    if command == "context_render":
        return _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT
    if command == "context_edit_plan":
        return _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT
    return None


def _remove_implicit_session_payload(path: str, session_id: str) -> None:
    root = _resolve_root(Path(path))
    try:
        _session_payload_path(root, session_id).unlink(missing_ok=True)
        records = [record for record in _load_index(root) if record.session_id != session_id]
        _write_index(root, records)
    except (OSError, ValueError):
        # ValueError: a traversal-shaped session_id is refused by _session_payload_path;
        # treat implicit cleanup as best-effort (fails closed) rather than raising.
        pass


def _implicit_session_id_for_request(
    server: Any,
    *,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
) -> str:
    if session_id or command not in {"context_render", "context_edit_plan"}:
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

    _ensure_session_not_stale(payload, detect_added_files=False)
    with server._response_cache_lock:
        cached_response = server.response_cache.get(response_cache_key)
    if cached_response is not None:
        cached_response.pop("serve_response_cache", None)
        return cached_response, "hit"

    response = serve_session_request(session_id, session_request, path, payload=payload)
    with server._response_cache_lock:
        server.response_cache.put(response_cache_key, response)
    return response, "miss"


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
        self.implicit_session_ids: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.started_at = monotonic()
        self.request_count = 0
        # audit I7: track last client activity so an idle daemon can shut itself down.
        self.last_activity_at = monotonic()
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
                if command in {"context_edit_plan", "context_render"}:
                    response["daemon_response_cache"] = {
                        "status": response_cache_status,
                        "entries": server.response_cache.entry_count,
                        "hits": server.response_cache.hits,
                        "misses": server.response_cache.misses,
                        "size_bytes": server.response_cache.size_bytes,
                        "max_size_bytes": server.response_cache.max_size_bytes,
                        "oversized_skips": server.response_cache.oversized_skips,
                    }
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
    while not stop_event.wait(_DAEMON_LIFECYCLE_POLL_SECONDS):
        now = monotonic()
        with server._request_lock:
            idle_for = now - server.last_activity_at
        uptime = now - server.started_at
        if (idle_limit > 0 and idle_for >= idle_limit) or (max_uptime > 0 and uptime >= max_uptime):
            threading.Thread(target=server.shutdown, daemon=True).start()
            return


def run_session_daemon_server(path: str = ".") -> None:
    root = _resolve_root(Path(path))
    # audit S3: generate a per-daemon token and publish it (0600) so only local clients that can
    # read daemon.json may issue commands.
    token = secrets.token_urlsafe(32)
    with _ThreadedSessionDaemon(root, (_DAEMON_HOST, 0), token=token) as server:
        host, port = cast(tuple[str, int], server.server_address)
        _write_daemon_metadata(
            root,
            {
                "version": _SESSION_VERSION,
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
