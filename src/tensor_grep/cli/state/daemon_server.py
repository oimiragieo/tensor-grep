from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socketserver
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli import session_daemon
from tensor_grep.cli.session_daemon_deadlines import _HandlerAdmissionThreads
from tensor_grep.cli.session_daemon_trust import _DaemonRefreshFailed


class _ThreadedSessionDaemon(_HandlerAdmissionThreads, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    _max_concurrent_connections = 32

    def __init__(self, root: Path, server_address: tuple[str, int], *, token: str = "") -> None:
        super().__init__(server_address, session_daemon._SessionDaemonHandler)
        self._init_handler_admission(session_daemon._DAEMON_HANDLER_TIMEOUT_SECONDS)
        self.root = root
        # audit S3: shared secret every client must present before any command is dispatched.
        self.token = token
        self.payload_cache = session_daemon._SessionServeCache()
        self.response_cache = session_daemon._SessionResponseCache()
        # tg-ledger step-0: demand instrumentation, see docs/multi_agent_context_plane.md.
        self.demand_metrics = session_daemon._DemandMetrics()
        self.implicit_session_ids: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._implicit_inflight: dict[tuple[str, str, str], threading.Event] = {}
        self._implicit_active_pins: dict[tuple[str, str], int] = {}
        self._implicit_pending_evictions: set[tuple[str, str]] = set()
        self._implicit_cleanup_owned: set[tuple[str, str]] = set()
        self.started_at = session_daemon.monotonic()
        self.request_count = 0
        # audit I7: track last client activity so an idle daemon can shut itself down.
        self.last_activity_at = session_daemon.monotonic()
        # audit r8: count in-flight (dispatched, not-yet-completed) requests so the lifecycle
        # monitor never tears the daemon down mid-request. Guarded by _request_lock.
        self.inflight_requests = 0
        self._request_lock = threading.Lock()
        self._response_cache_lock = threading.Lock()
        self._implicit_session_lock = threading.Lock()

    def note_activity(self) -> None:
        # audit I7: bump the idle clock on every authenticated request.
        with self._request_lock:
            self.last_activity_at = session_daemon.monotonic()

    def is_authorized(self, request: dict[str, Any]) -> bool:
        # POLICY REVERSAL (2026-08-01, PR-B): a tokenless daemon fails CLOSED. This
        # reverses the earlier pinned behavior (tokenless => authorize everything,
        # "legacy/in-test path") that test_tokenless_server_stays_backward_compatible
        # used to pin. Production always generates a token in the serve path
        # (run_session_daemon_server -> secrets.token_urlsafe(32)); a tokenless daemon
        # is a misconfiguration and must refuse everything, never accept everything.
        # Tokenless CONSTRUCTION stays legal (see
        # test_lifecycle_monitor_returns_when_both_limits_disabled) -- only tokenless
        # AUTHORIZATION is forbidden. Hold this invariant in this one place only; the
        # constructor deliberately does not also raise on an empty token.
        if not self.token:
            return False
        # audit S3: constant-time compare to avoid leaking the token via timing.
        provided = request.get(session_daemon._DAEMON_TOKEN_FIELD)
        if not isinstance(provided, str) or not provided:
            return False
        return hmac.compare_digest(provided, self.token)


class _SessionDaemonHandler(socketserver.StreamRequestHandler):
    # audit (round-3 pre-auth DoS): time out a silent/slow client so it cannot pin a worker
    # thread indefinitely before authenticating. StreamRequestHandler.setup() applies this via
    # connection.settimeout().
    timeout = session_daemon._DAEMON_HANDLER_TIMEOUT_SECONDS

    def _write_response(self, response: dict[str, Any], deadline: float) -> bool:
        remaining = deadline - session_daemon.monotonic()
        if remaining <= 0:
            self.connection.close()
            return False
        self.connection.settimeout(remaining)
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
        self.wfile.flush()
        return True

    def handle(self) -> None:
        server = cast(session_daemon._ThreadedSessionDaemon, self.server)
        with server._handler_deadline_lock:
            handler_request = getattr(self, "request", None)
            admitted_at = server._handler_admissions.get(
                handler_request, session_daemon.monotonic()
            )
            handler_deadline = server._handler_deadlines.get(handler_request)
        if handler_deadline is None:
            handler_deadline = admitted_at + session_daemon._DAEMON_HANDLER_TIMEOUT_SECONDS
        work_deadline = admitted_at + session_daemon.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
        line = session_daemon._read_bounded_request_line(
            self.rfile,
            max_bytes=session_daemon._MAX_DAEMON_REQUEST_BYTES,
            deadline=handler_deadline,
            connection=getattr(self, "connection", None),
        )
        if not line:
            return
        response: dict[str, Any]
        with server._request_lock:
            server.request_count += 1
        request_session_id = ""
        request_path = str(server.root)
        implicit_pin_receipt: list[Any] = []
        inflight_incremented = False

        try:
            request = cast(dict[str, Any], json.loads(line))
            # audit S3: authenticate before dispatching any command or resolving any path.
            if not server.is_authorized(request):
                response = {
                    "version": session_daemon._SESSION_VERSION,
                    "session_id": "",
                    "error": {"code": "unauthorized", "message": "invalid or missing daemon token"},
                }
                self._write_response(response, work_deadline)
                return
            server.note_activity()
            # audit r8: mark this request in-flight (after auth, so unauthorized clients don't
            # count) so the lifecycle monitor won't shut the daemon down mid-dispatch.
            with server._request_lock:
                server.inflight_requests += 1
            inflight_incremented = True
            session_id = str(request.get("session_id", "")).strip()
            request_path = session_daemon._resolve_daemon_request_path(
                server.root,
                request.get("path", request.get("root", str(server.root))),
            )
            request = dict(request)
            request["path"] = request_path
            request_session_id, request_path = session_daemon._resolve_request_session_target(
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
            request_session_id = session_daemon._implicit_session_id_for_request(
                server,
                command=command,
                session_id=request_session_id,
                path=request_path,
                request=request,
                deadline_monotonic=work_deadline,
                pin_receipt=implicit_pin_receipt,
            )
            if request_session_id:
                request["session_id"] = request_session_id

            if command == "stop":
                response = {
                    "version": session_daemon._SESSION_VERSION,
                    "ok": True,
                    "stopping": True,
                }
                if not self._write_response(response, work_deadline):
                    return
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            if command == "ping":
                response = {"version": session_daemon._SESSION_VERSION, "ok": True}
                response.update(
                    session_daemon._ping_proof_fields(
                        request.get("nonce"), server.root, int(server.server_address[1])
                    )
                )
                self._write_response(response, work_deadline)
                return

            if command == "stats":
                response = {
                    "version": session_daemon._SESSION_VERSION,
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
                    "response_cache_scope": session_daemon._DAEMON_RESPONSE_CACHE_SCOPE,
                    "response_cache_stale_detection": session_daemon._DAEMON_RESPONSE_CACHE_STALE_DETECTION,
                    "response_cache_added_file_detection": session_daemon._DAEMON_RESPONSE_CACHE_ADDED_FILE_DETECTION,
                    "response_cache_refresh_hint": (
                        "Use tg session refresh or request refresh_on_stale when new files must "
                        "invalidate daemon response-cache hits."
                    ),
                    "uptime_seconds": max(0.0, session_daemon.monotonic() - server.started_at),
                    "request_count": server.request_count,
                    "inflight_requests": server.inflight_requests,
                }
            elif command == "health":
                payload, cache_status = session_daemon._load_payload_with_status_retry(
                    server.payload_cache,
                    request_session_id,
                    request_path,
                    deadline_monotonic=work_deadline,
                )
                response = session_daemon._session_health_payload(
                    request_session_id, payload, deadline_monotonic=work_deadline
                )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
                }
            else:
                overall_started_at = admitted_at
                response_cache_status = "bypass"
                refresh_trigger = ""
                try:
                    load_started_at = session_daemon.monotonic()
                    try:
                        payload, cache_status = session_daemon._load_payload_with_status_retry(
                            server.payload_cache,
                            request_session_id,
                            request_path,
                            deadline_monotonic=work_deadline,
                        )
                    except FileNotFoundError as missing:
                        replacement = session_daemon._reacquire_missing_implicit_session(
                            server,
                            path=request_path,
                            request=request,
                            session_id=request_session_id,
                            pin_receipt=implicit_pin_receipt,
                            deadline_monotonic=work_deadline,
                            exc=missing,
                        )
                        if replacement is None:
                            raise
                        request_session_id = replacement
                        request["session_id"] = replacement
                        payload, cache_status = session_daemon._load_payload_with_status_retry(
                            server.payload_cache,
                            request_session_id,
                            request_path,
                            deadline_monotonic=work_deadline,
                        )
                    loaded_at = session_daemon.monotonic()
                    response, response_cache_status = (
                        session_daemon._serve_daemon_response_with_cache(
                            server=server,
                            command=command,
                            session_id=request_session_id,
                            path=request_path,
                            request=request,
                            payload=payload,
                            deadline_monotonic=work_deadline,
                        )
                    )
                    served_at = session_daemon.monotonic()
                except Exception as exc:
                    refresh_on_stale = bool(request.get("refresh_on_stale", False))
                    if not refresh_on_stale:
                        raise
                    refresh_trigger = type(exc).__name__  # ANY error rebuilds; name it (disclosed)
                    original_error = str(exc)
                    load_started_at = session_daemon.monotonic()
                    # Freshness, rebuilding, and inference share this request deadline.
                    try:
                        session_daemon.refresh_session(
                            request_session_id,
                            request_path,
                            payload_cache=server.payload_cache,
                            deadline_monotonic=work_deadline,
                        )
                        server.payload_cache.record_refresh()
                        payload, cache_status = session_daemon._load_payload_with_status_retry(
                            server.payload_cache,
                            request_session_id,
                            request_path,
                            deadline_monotonic=work_deadline,
                        )
                        loaded_at = session_daemon.monotonic()
                        response, response_cache_status = (
                            session_daemon._serve_daemon_response_with_cache(
                                server=server,
                                command=command,
                                session_id=request_session_id,
                                path=request_path,
                                request=request,
                                payload=payload,
                                deadline_monotonic=work_deadline,
                            )
                        )
                        served_at = session_daemon.monotonic()
                    except Exception as rebuild_exc:
                        raise _DaemonRefreshFailed(
                            refresh_trigger, original_error, str(rebuild_exc)
                        ) from rebuild_exc
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": server.payload_cache.session_count,
                    "root_count": server.payload_cache.root_count,
                    **({"refresh_trigger": refresh_trigger} if refresh_trigger else {}),
                }
                # audit #113 (task #108: extended to 9): observability now covers all 9
                # response-cacheable commands (the original context_render/context_edit_plan, the
                # 5 symbol commands, and orient/agent) -- the SAME set _response_cache_key_for_
                # command treats as cacheable. session_timing's build_metric naming below stays
                # scoped to the original 2 (out of scope here).
                if command in session_daemon._ADDED_FILE_SENSITIVE_COMMANDS or command in {
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
        except _DaemonRefreshFailed as failed:
            response = {
                "version": session_daemon._SESSION_VERSION,
                "session_id": request_session_id,
                "error": session_daemon._refresh_failed_error(failed),
            }
        except Exception as exc:
            response = {
                "version": session_daemon._SESSION_VERSION,
                "session_id": request_session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        finally:
            session_daemon._release_implicit_session_pin(
                server, implicit_pin_receipt, deadline_monotonic=work_deadline
            )
            if inflight_incremented:
                with server._request_lock:
                    server.inflight_requests -= 1

        self._write_response(response, work_deadline)


def _run_daemon_lifecycle_monitor(
    server: session_daemon._ThreadedSessionDaemon, stop_event: threading.Event
) -> None:
    """Self-shutdown the daemon when it goes idle or exceeds its max uptime (audit I7)."""
    idle_limit = session_daemon._configured_lifecycle_seconds(
        session_daemon._DAEMON_IDLE_SHUTDOWN_SECONDS_ENV,
        session_daemon._DEFAULT_DAEMON_IDLE_SHUTDOWN_SECONDS,
    )
    max_uptime = session_daemon._configured_lifecycle_seconds(
        session_daemon._DAEMON_MAX_UPTIME_SECONDS_ENV,
        session_daemon._DEFAULT_DAEMON_MAX_UPTIME_SECONDS,
    )
    if idle_limit <= 0 and max_uptime <= 0:
        return
    # tg-ledger step-0: piggyback a low-frequency demand-metrics flush on this same poll loop
    # (~once per _DAEMON_METRICS_FLUSH_POLL_INTERVALS ticks) rather than a dedicated thread.
    flush_countdown = session_daemon._DAEMON_METRICS_FLUSH_POLL_INTERVALS
    while not stop_event.wait(session_daemon._DAEMON_LIFECYCLE_POLL_SECONDS):
        flush_countdown -= 1
        if flush_countdown <= 0:
            flush_countdown = session_daemon._DAEMON_METRICS_FLUSH_POLL_INTERVALS
            session_daemon._flush_demand_metrics_if_dirty(server)
        now = session_daemon.monotonic()
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
                max_uptime > 0
                and uptime >= max_uptime + session_daemon._DAEMON_SHUTDOWN_DRAIN_GRACE_SECONDS
            )
            if not past_hard_drain:
                continue
        threading.Thread(target=server.shutdown, daemon=True).start()
        return


def run_session_daemon_server(path: str = ".") -> None:
    root = session_daemon._resolve_root(Path(path))
    # audit S3: generate a per-daemon token and publish it (0600) so only local clients that can
    # read daemon.json may issue commands.
    token = secrets.token_urlsafe(32)
    if session_daemon._load_or_create_user_secret() is None:
        reason = session_daemon.diagnose_user_secret() or "secret_unavailable"
        raise RuntimeError(f"cannot establish per-user daemon secret ({reason}); refusing to serve")
    with session_daemon._ThreadedSessionDaemon(
        root, (session_daemon._DAEMON_HOST, 0), token=token
    ) as server:
        # tg-ledger step-0: load any prior demand-metrics history for this root before serving,
        # so a daemon restart never clobbers the day-bucket counts a prior run already persisted.
        server.demand_metrics.load(session_daemon._read_demand_metrics_days(root))
        host, port = cast(tuple[str, int], server.server_address)
        session_daemon._write_daemon_metadata(
            root,
            {
                "version": session_daemon._SESSION_VERSION,
                # Task #94 PR-1: the installed tensor-grep PACKAGE version (distinct from
                # `version` above, which is the payload-schema version) -- see the matching
                # `_probe_daemon` skew check for why this is needed.
                "package_version": session_daemon._expected_tg_version(),
                "root": str(root),
                "host": host,
                "port": int(port),
                "pid": session_daemon.os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
                session_daemon._DAEMON_TOKEN_FIELD: token,
                **session_daemon._attestation_fields(
                    root, int(port)
                ),  # HMAC proof of "this is the real daemon"
            },
        )
        stop_event = threading.Event()
        lifecycle_thread = threading.Thread(
            target=session_daemon._run_daemon_lifecycle_monitor,
            args=(server, stop_event),
            daemon=True,
        )
        lifecycle_thread.start()
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            stop_event.set()
            session_daemon._flush_demand_metrics_if_dirty(server, force=True)
            # Task #143a-a: this daemon must only ever remove ITS OWN daemon.json on shutdown
            # (idle timeout, max-uptime, or a cooperative "stop" command). If a client already
            # rejected this daemon over a package_version mismatch and spawned a healthy
            # replacement, that replacement's metadata is what is on disk by the time THIS daemon
            # finally shuts itself down -- an unconditional unlink here would delete it out from
            # under the replacement, orphaning it with no discoverable daemon.json.
            session_daemon._remove_daemon_metadata(
                root, expected_pid=session_daemon.os.getpid(), expected_port=int(port)
            )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = session_daemon._parse_args(argv or session_daemon.sys.argv[1:])
    session_daemon.run_session_daemon_server(args.root)
    return 0
