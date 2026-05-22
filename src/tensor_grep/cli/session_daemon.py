from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

from tensor_grep.cli.session_store import (
    _SESSION_VERSION,
    _resolve_request_session_target,
    _resolve_root,
    _session_health_payload,
    _sessions_dir,
    _SessionServeCache,
    refresh_session,
    serve_session_request,
)

_DAEMON_METADATA_FILE = "daemon.json"
_DAEMON_HOST = "127.0.0.1"
_DAEMON_CONNECT_TIMEOUT_SECONDS = 0.5
_DAEMON_RESPONSE_TIMEOUT_SECONDS = 60.0
_DAEMON_START_TIMEOUT_SECONDS = 5.0
_DAEMON_SESSION_LOOKUP_RETRY_SECONDS = 0.25


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
    metadata_path = _daemon_metadata_path(root)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _remove_daemon_metadata(root: Path) -> None:
    metadata_path = _daemon_metadata_path(root)
    try:
        metadata_path.unlink()
    except OSError:
        pass


def _daemon_request(
    host: str,
    port: int,
    request: dict[str, Any],
    *,
    response_timeout: float | None = _DAEMON_RESPONSE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
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
        )
    except Exception:
        return None
    if not response.get("ok"):
        return None
    return metadata


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
            return {
                "version": _SESSION_VERSION,
                "root": str(discovered_root),
                "requested_root": str(root),
                "discovered": True,
                "running": True,
                "host": str(live.get("host", _DAEMON_HOST)),
                "port": int(live["port"]),
                "pid": int(live["pid"]),
                "started_at": str(live["started_at"]),
            }
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
    return {
        "version": _SESSION_VERSION,
        "root": str(root),
        "discovered": False,
        "running": True,
        "host": str(live.get("host", _DAEMON_HOST)),
        "port": int(live["port"]),
        "pid": int(live["pid"]),
        "started_at": str(live["started_at"]),
    }


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
        }

    _remove_daemon_metadata(root)
    creationflags = 0
    repo_root = Path(__file__).resolve().parents[3]
    repo_src = repo_root / "src"
    env = os.environ.copy()
    python_path_parts = [str(repo_src)]
    if env.get("PYTHONPATH"):
        python_path_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
        cwd=str(repo_root),
        env=env,
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
            }
        time.sleep(0.05)
    raise RuntimeError(f"session daemon did not start for {root}")


def stop_session_daemon(path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    metadata = _probe_daemon(root)
    if metadata is None:
        _remove_daemon_metadata(root)
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "running": False,
            "stopped": False,
        }
    response = _daemon_request(
        str(metadata.get("host", _DAEMON_HOST)),
        int(metadata["port"]),
        {"command": "stop"},
    )
    deadline = time.time() + _DAEMON_START_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _probe_daemon(root) is None:
            break
        time.sleep(0.05)
    _remove_daemon_metadata(root)
    response["running"] = False
    response["root"] = str(root)
    response["stopped"] = True
    return response


def request_session_daemon(path: str, request: dict[str, Any]) -> dict[str, Any]:
    status = start_session_daemon(path)
    return _daemon_request(
        str(status.get("host", _DAEMON_HOST)),
        int(status["port"]),
        request,
    )


def _load_payload_with_status_retry(
    cache: _SessionServeCache, session_id: str, path: str
) -> tuple[dict[str, Any], str]:
    deadline = time.time() + _DAEMON_SESSION_LOOKUP_RETRY_SECONDS
    while True:
        try:
            return cache.load_with_status(session_id, path)
        except FileNotFoundError:
            if time.time() >= deadline:
                raise
            time.sleep(0.05)


class _ThreadedSessionDaemon(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, root: Path, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, _SessionDaemonHandler)
        self.root = root
        self.payload_cache = _SessionServeCache()
        self.started_at = monotonic()
        self.request_count = 0
        self._request_lock = threading.Lock()


class _SessionDaemonHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = cast(_ThreadedSessionDaemon, self.server)
        line = self.rfile.readline().decode("utf-8").strip()
        if not line:
            return
        response: dict[str, Any]
        with server._request_lock:
            server.request_count += 1
        request_session_id = ""
        request_path = str(server.root)

        try:
            request = cast(dict[str, Any], json.loads(line))
            session_id = str(request.get("session_id", "")).strip()
            request_path = str(
                request.get("path", request.get("root", str(server.root)))
            ).strip() or str(server.root)
            request_session_id, request_path = _resolve_request_session_target(
                request, session_id, request_path
            )
            command = str(request.get("command", "")).strip().lower()

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
                try:
                    load_started_at = monotonic()
                    payload, cache_status = _load_payload_with_status_retry(
                        server.payload_cache,
                        request_session_id,
                        request_path,
                    )
                    loaded_at = monotonic()
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
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
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                    )
                    served_at = monotonic()
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
                }
                if command == "context_edit_plan":
                    response["session_timing"] = {
                        "cache_status": cache_status,
                        "load_session_seconds": max(0.0, loaded_at - load_started_at),
                        "build_edit_plan_seconds": max(0.0, served_at - loaded_at),
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


def run_session_daemon_server(path: str = ".") -> None:
    root = _resolve_root(Path(path))
    with _ThreadedSessionDaemon(root, (_DAEMON_HOST, 0)) as server:
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
            },
        )
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
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
