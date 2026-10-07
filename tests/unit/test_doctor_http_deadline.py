from __future__ import annotations

import socketserver
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from tensor_grep.cli import freshness_process, native_frontdoor, process_containment, pypi_probe


@contextmanager
def index_server(scenario):
    stopped = threading.Event()
    stalled = threading.Event()
    disconnected = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 8192:
                data = self.request.recv(1024)
                if not data:
                    return
                request += data
            is_json = request.split(b" ")[1] == b"/json"
            if scenario == "success" or (scenario == "best-then-body" and is_json):
                body = (
                    b'{"info":{"version":"1.0.0"}}' if is_json else b"<a>tensor_grep-1.1.0.whl</a>"
                )
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + body
                )
                return
            stalled.set()
            prefix = (
                b"HTTP/1.1 200 OK\r\nX-Trickle: "
                if scenario == "headers"
                else b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n"
            )
            try:
                self.request.sendall(prefix)
                # Each byte arrives faster than the socket timeout. The finite
                # fixture lifetime also bounds regression runs on the old code.
                end = time.monotonic() + 4
                while time.monotonic() < end and not stopped.wait(0.03):
                    self.request.sendall(b"x")
            except OSError:
                pass
            finally:
                disconnected.set()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = False

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", stalled, disconnected
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "scenario,expected", [("headers", None), ("body", None), ("best-then-body", "1.0.0")]
)
def test_http_worker_bounds_trickling_io_and_cleans_up(monkeypatch, scenario, expected):
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)
    children = []
    verified_live_counts = []
    original_spawn = process_containment.spawn_contained

    def spawn(*args, **kwargs):
        pair = original_spawn(*args, **kwargs)
        containment = pair[1]
        original_survivors = containment.survivors

        def survivors(deadline):
            if containment.level == process_containment.LEVEL_JOB_OBJECT:
                assert containment._job is not None, "verify before releasing the job handle"
            failures = original_survivors(deadline)
            verified_live_counts.append(containment._alive_count())
            return failures

        monkeypatch.setattr(containment, "survivors", survivors)
        children.append(pair)
        return pair

    monkeypatch.setattr(process_containment, "spawn_contained", spawn)
    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pip_index", lambda _: [])
    # Warm the lazy CLI binding independently of the HTTP deadline fixture.
    native_frontdoor._self._cli_package_version()
    with index_server(scenario) as (base, stalled, disconnected):
        monkeypatch.setattr(native_frontdoor, "_PYPI_JSON_URL", base + "/json")
        monkeypatch.setattr(native_frontdoor, "_PYPI_SIMPLE_URL", base + "/simple")
        before_threads = set(threading.enumerate())
        started = time.monotonic()
        result = native_frontdoor._latest_pypi_tensor_grep_version(timeout_seconds=1.5)
        elapsed = time.monotonic() - started
        assert stalled.is_set(), "the fixture must reach the intended stalled HTTP operation"
        assert elapsed < 2.5, f"freshness exceeded its process budget: {elapsed:.3f}s"
        assert result == expected
        assert len(children) == 1, "both indices must run in one contained child"
        process, _ = children[0]
        assert process.poll() is not None
        assert verified_live_counts == [0]
        assert disconnected.wait(timeout=1), "a killed worker must release the HTTP connection"
        assert not [
            thread
            for thread in set(threading.enumerate()) - before_threads
            if "_readerthread" in thread.name
        ]


def test_http_success_retains_best_observed_and_remaining_pip_budget(monkeypatch):
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)
    timeouts = []

    def pip(timeout):
        timeouts.append(timeout)
        return ["1.2.0"]

    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pip_index", pip)
    with index_server("success") as (base, _, _):
        monkeypatch.setattr(native_frontdoor, "_PYPI_JSON_URL", base + "/json")
        monkeypatch.setattr(native_frontdoor, "_PYPI_SIMPLE_URL", base + "/simple")
        assert native_frontdoor._latest_pypi_tensor_grep_version(3) == "1.2.0"
    assert len(timeouts) == 1 and 0 < timeouts[0] < 3


def test_http_body_limit_is_active_before_parse(monkeypatch):
    events = []
    requested_sizes = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            requested_sizes.append(size)
            return b"x" * size

    monkeypatch.setattr(pypi_probe.urllib.request, "urlopen", lambda *a, **k: Response())
    pypi_probe.probe_indices(
        time.monotonic() + 3, ["http://fixture/json", "http://fixture/simple"], {}, events.append
    )
    assert requested_sizes == [pypi_probe._MAX_INDEX_BYTES + 1] * 2
    assert all(event["version"] is None and event["error"] == "ValueError" for event in events)


def test_pip_capture_has_a_hard_process_budget_and_keeps_completed_output(monkeypatch):
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)
    native_frontdoor._self._cli_package_version()
    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pypi_indices", lambda *a: [])
    calls = []

    def capture(argv, timeout_seconds, **kwargs):
        calls.append(argv)
        return freshness_process.capture_probe(
            [
                sys.executable,
                "-c",
                "import time; print('tensor-grep (1.0.0)',flush=True); time.sleep(10)",
            ],
            timeout_seconds,
            **kwargs,
        )

    monkeypatch.setattr(native_frontdoor, "_capture_freshness_probe", capture)
    started = time.monotonic()
    assert native_frontdoor._latest_pypi_tensor_grep_version(1.5) == "1.0.0"
    assert time.monotonic() - started < 2.5
    assert len(calls) == 1 and calls[0][1:5] == ["-m", "pip", "index", "versions"]


def test_kill_failure_is_reported_and_survivors_verified_before_release(monkeypatch, caplog):
    order = []

    class Process:
        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class Containment:
        def kill(self):
            order.append("kill")
            return ["simulated kill failure"]

        def survivors(self, deadline):
            order.append("verify")
            assert "release" not in order
            return []

        def release(self):
            order.append("release")

    def spawn(argv, **kwargs):
        kwargs["stdout"].write(b"completed evidence")
        kwargs["stdout"].flush()
        return Process(), Containment()

    monkeypatch.setattr(process_containment, "spawn_contained", spawn)
    assert freshness_process.capture_probe(["fixture"], 1) == (b"completed evidence", b"")
    assert order == ["kill", "verify", "release"]
    assert "simulated kill failure" in caplog.text


def test_captured_output_is_bounded_before_decoding(monkeypatch, caplog):
    class Process:
        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class Containment:
        def kill(self):
            return []

        def survivors(self, deadline):
            return []

        def release(self):
            pass

    def spawn(argv, **kwargs):
        kwargs["stdout"].write(b"x" * (freshness_process._MAX_OUTPUT_BYTES + 1))
        kwargs["stdout"].flush()
        return Process(), Containment()

    monkeypatch.setattr(process_containment, "spawn_contained", spawn)
    assert freshness_process.capture_probe(["fixture"], 1) == (b"", b"")
    assert "output limit" in caplog.text
