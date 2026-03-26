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
_DAEMON_START_TIMEOUT_SECONDS = 5.0


def _daemon_metadata_path(root: Path) -> Path:
    return _sessions_dir(root) / _DAEMON_METADATA_FILE


def _read_daemon_metadata(root: Path) -> dict[str, Any] | None:
    metadata_path = _daemon_metadata_path(root)
    if not metadata_path.exists():
        return None
    return cast(dict[str, Any], json.loads(metadata_path.read_text(encoding="utf-8")))


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


def _daemon_request(host: str, port: int, request: dict[str, Any]) -> dict[str, Any]:
    with socket.create_connection(
        (host, int(port)),
        timeout=_DAEMON_CONNECT_TIMEOUT_SECONDS,
    ) as conn:
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
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "running": False,
        }
    live = _probe_daemon(root)
    if live is None:
        return {
            "version": _SESSION_VERSION,
            "root": str(root),
            "running": False,
            "stale_metadata": True,
        }
    return {
        "version": _SESSION_VERSION,
        "root": str(root),
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
        request = cast(dict[str, Any], json.loads(line))
        response: dict[str, Any]
        with server._request_lock:
            server.request_count += 1
        session_id = str(request.get("session_id", "")).strip()
        request_path = str(request.get("path", request.get("root", str(server.root)))).strip() or str(server.root)
        request_session_id, request_path = _resolve_request_session_target(request, session_id, request_path)
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

        try:
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
                payload, cache_status = server.payload_cache.load_with_status(
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
                try:
                    payload, cache_status = server.payload_cache.load_with_status(
                        request_session_id,
                        request_path,
                    )
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                    )
                except Exception:
                    refresh_on_stale = bool(request.get("refresh_on_stale", False))
                    if not refresh_on_stale:
                        raise
                    refresh_session(
                        request_session_id,
                        request_path,
                        payload_cache=server.payload_cache,
                    )
                    server.payload_cache.record_refresh()
                    payload, cache_status = server.payload_cache.load_with_status(
                        request_session_id,
                        request_path,
                    )
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                    )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
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
