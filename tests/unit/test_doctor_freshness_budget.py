from __future__ import annotations

import io
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from tensor_grep.cli import doctor_report, freshness_process, main, native_frontdoor, pypi_probe


@pytest.fixture
def clock(monkeypatch):
    value = [100.0]
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)
    # The module already imports time after the repair; adding an attribute also
    # permits running these regressions against the unmodified implementation.
    import time

    monkeypatch.setattr(native_frontdoor, "time", time, raising=False)
    monkeypatch.setattr(time, "monotonic", lambda: value[0])

    def direct_indices(timeout_seconds, headers):
        events = []
        pypi_probe.probe_indices(
            value[0] + timeout_seconds,
            [native_frontdoor._PYPI_JSON_URL, native_frontdoor._PYPI_SIMPLE_URL],
            headers,
            events.append,
        )
        return [event["version"] for event in events if event["version"] is not None]

    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pypi_indices", direct_indices)
    return value


def test_freshness_operations_share_remaining_budget(monkeypatch, clock) -> None:
    timeouts = []

    def open_index(request, timeout):
        timeouts.append(timeout)
        clock[0] += 4
        if request.full_url.endswith("/json"):
            return io.BytesIO(b'{"info":{"version":"1.0.0"}}')
        return io.BytesIO(b"<a>tensor_grep-1.1.0.whl</a>")

    def pip(timeout_seconds):
        timeouts.append(timeout_seconds)
        return ["1.2.0"]

    monkeypatch.setattr(urllib.request, "urlopen", open_index)
    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pip_index", pip)
    assert native_frontdoor._latest_pypi_tensor_grep_version() == "1.2.0"
    assert timeouts == [15.0, 11.0, 7.0]


@pytest.mark.parametrize(
    "body,expected", [(b'{"info":{"version":"1.0.0"}}', "1.0.0"), (b"bad", None)]
)
def test_exhausted_budget_preserves_observed_version_or_unknown(monkeypatch, clock, body, expected):
    calls = []

    def open_index(request, timeout):
        calls.append(request.full_url)
        clock[0] += timeout
        return io.BytesIO(body)

    def no_pip(_timeout):
        pytest.fail("pip must not start after the shared deadline")

    monkeypatch.setattr(urllib.request, "urlopen", open_index)
    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pip_index", no_pip)
    assert native_frontdoor._latest_pypi_tensor_grep_version() == expected
    assert len(calls) == 1


def test_offline_control_never_probes(monkeypatch) -> None:
    monkeypatch.setenv("TG_DOCTOR_OFFLINE", "1")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("offline network"))
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("offline child"))
    assert native_frontdoor._latest_pypi_tensor_grep_version() is None


def test_pip_freshness_child_is_noninteractive(monkeypatch) -> None:
    calls = []
    original_spawn = freshness_process.process_containment.spawn_contained

    def spawn(args, **kwargs):
        calls.append(kwargs)
        return original_spawn([sys.executable, "-c", "print('tensor-grep (1.0.0)')"], **kwargs)

    monkeypatch.setattr(freshness_process.process_containment, "spawn_contained", spawn)
    assert native_frontdoor._candidate_versions_from_pip_index(1) == ["1.0.0"]
    assert calls[0]["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("probe", ["_doctor_rust_binary_version", "_doctor_tg_candidate_version"])
def test_doctor_version_children_are_noninteractive(monkeypatch, probe) -> None:
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, b"tensor-grep 1.0.0", b"")

    monkeypatch.setattr(main.subprocess, "run", run)
    assert getattr(doctor_report, probe)(Path("tg.exe")) == "tensor-grep 1.0.0"
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_nested_native_version_probe_is_noninteractive(monkeypatch) -> None:
    from tensor_grep.cli import runtime_paths

    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, b"tg 1.0.0", b"")

    monkeypatch.setattr(runtime_paths.subprocess, "run", run)
    assert runtime_paths._native_tg_version(Path("tg.exe")) == "tg 1.0.0"
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_ast_binary_probe_is_noninteractive(monkeypatch) -> None:
    from tensor_grep.backends import ast_wrapper_backend

    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, b"ast-grep 0.39.0", b"")

    monkeypatch.setattr(ast_wrapper_backend.subprocess, "run", run)
    assert ast_wrapper_backend._is_ast_grep_sg_binary("sg") is True
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_doctor_gpu_probe_child_is_noninteractive(monkeypatch, tmp_path) -> None:
    calls = []
    candidate = tmp_path / "tg.exe"
    candidate.touch()

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            args, 0, b'{"routing_backend":"NativeGpuBackend","sidecar_used":false}', b""
        )

    monkeypatch.setattr(main.subprocess, "run", run)
    monkeypatch.setattr(main, "is_cross_domain_native_binary", lambda _: False)
    assert doctor_report._doctor_gpu_search_runtime_probe(candidate)["status"] == "supported"
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_latest_pypi_probe_uses_pip_index_when_json_and_simple_are_stale(monkeypatch):
    import time

    from tensor_grep.cli import native_frontdoor, pypi_probe

    class _FakeResponse:
        def __init__(self, body: str) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1) -> bytes:
            return self.body.encode("utf-8")

    stale_json = json.dumps({
        "info": {"version": "0.33.0"},
        "releases": {
            "0.32.0": [{"yanked": False}],
            "0.33.0": [{"yanked": False}],
        },
    })
    stale_simple = """
    <a href="tensor_grep-0.33.0-py3-none-any.whl">tensor_grep-0.33.0-py3-none-any.whl</a>
    """
    calls: list[list[str]] = []

    def _fake_urlopen(request, timeout=None):
        url = request.get_full_url()
        if url.endswith("/json"):
            return _FakeResponse(stale_json)
        if url.endswith("/simple/tensor-grep/"):
            return _FakeResponse(stale_simple)
        raise AssertionError(f"unexpected url: {url}")

    def _fake_run(cmd, **_kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "tensor-grep (0.34.0)\n"
                "Available versions: 0.34.0, 0.33.0, 0.32.0\n"
                "  LATEST:    0.34.0\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    def direct_indices(timeout_seconds, headers):
        events = []
        pypi_probe.probe_indices(
            time.monotonic() + timeout_seconds,
            [native_frontdoor._PYPI_JSON_URL, native_frontdoor._PYPI_SIMPLE_URL],
            headers,
            events.append,
        )
        return [event["version"] for event in events if event["version"] is not None]

    monkeypatch.setattr(native_frontdoor, "_candidate_versions_from_pypi_indices", direct_indices)

    def capture(args, timeout_seconds, **kwargs):
        result = _fake_run(args, **kwargs)
        return result.stdout, result.stderr

    monkeypatch.setattr(native_frontdoor, "_capture_freshness_probe", capture)
    # The autouse _doctor_offline fixture sets TG_DOCTOR_OFFLINE=1; this test exercises the REAL
    # probe, so clear it (raising=False: absent in some collection orders).
    monkeypatch.delenv("TG_DOCTOR_OFFLINE", raising=False)

    assert main._latest_pypi_tensor_grep_version(timeout_seconds=1.0) == "0.34.0"
    assert calls
    assert calls[0][1:8] == ["-I", "-X", "utf8", "-m", "pip", "index", "versions"]
    assert "--no-cache-dir" in calls[0]
