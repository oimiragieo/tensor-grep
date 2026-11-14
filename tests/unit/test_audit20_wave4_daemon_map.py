"""Daemon/map regressions with disposable ordinary fixtures and bounded handshakes."""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from itertools import islice
from pathlib import Path
from types import SimpleNamespace

import pytest

from tensor_grep.cli import repo_map as rm
from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.repo_map_gitignore import _GitignoreMatcher


def project(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.0.0"\n')
    return path


def server():
    return SimpleNamespace(
        _implicit_session_lock=threading.Lock(),
        _implicit_inflight={},
        _implicit_active_pins={},
        _implicit_pending_evictions=set(),
        _implicit_cleanup_owned=set(),
        implicit_session_ids=OrderedDict(),
    )


def request(srv, path):
    return sd._implicit_session_id_for_request(
        srv,
        command="context_render",
        session_id="",
        path=str(path),
        request={"max_repo_files": 20},
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_scope_key_separates_root_and_subtree(tmp_path, monkeypatch, reverse):
    root = project(tmp_path / "project")
    sub = root / "src"
    sub.mkdir()
    srv = server()
    calls = []

    def opener(path, **kwargs):
        calls.append(str(path))
        return SimpleNamespace(session_id="fake-" + str(len(calls)))

    monkeypatch.setattr(sd, "open_session", opener)
    scopes = [root, sub] if reverse else [sub, root]
    one, two = [request(srv, scope) for scope in scopes]
    assert one != two, "distinct requested scopes reused one implicit session"
    assert request(srv, scopes[0]) == one
    assert len(calls) == 2


def test_warm_key_completes_while_other_key_cold_build_waits(tmp_path, monkeypatch):
    root, cold = project(tmp_path / "warm"), project(tmp_path / "cold")
    srv = server()
    monkeypatch.setattr(sd, "open_session", lambda *a, **k: SimpleNamespace(session_id="warm"))
    assert request(srv, root) == "warm"
    cold_entered, release, warm_attempt, warm_done = [threading.Event() for _ in range(4)]
    original_lock = srv._implicit_session_lock

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "warm":
                warm_attempt.set()
            original_lock.acquire()
            return self

        def __exit__(self, *args):
            original_lock.release()

    srv._implicit_session_lock = ObservedLock()
    errors, answers = [], {}

    def opener(*args, **kwargs):
        cold_entered.set()
        if not release.wait(8):
            raise TimeoutError("test cold-open release event was not signaled")
        return SimpleNamespace(session_id="cold")

    monkeypatch.setattr(sd, "open_session", opener)

    def run(label, path):
        try:
            answers[label] = request(srv, path)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if label == "warm":
                warm_done.set()

    a = threading.Thread(target=run, args=("cold", cold), name="cold", daemon=True)
    b = threading.Thread(target=run, args=("warm", root), name="warm", daemon=True)
    a.start()
    try:
        assert cold_entered.wait(3), "BLOCKED_SETUP: cold opener did not start"
        b.start()
        assert warm_attempt.wait(3), "BLOCKED_SETUP: warm thread did not reach global lock"
        completed_before_release = warm_done.wait(2)
    finally:
        release.set()
        a.join(3)
        if b.ident is not None:
            b.join(3)
    assert not a.is_alive() and not b.is_alive(), "threads failed bounded cleanup"
    assert not errors, repr(errors)
    assert completed_before_release, "warm cached key blocked behind unrelated cold open"
    assert answers == {"cold": "cold", "warm": "warm"}


@pytest.mark.parametrize(
    "bad_bucket",
    [
        {"dup_targets": {"a": None}},
        {"dup_targets": {"a": float("inf")}},
        {"by_command": {"context_render": None}},
        {"by_command": {"context_render": float("inf")}},
        {"requests": float("inf")},
    ],
)
def test_startup_metrics_load_preserves_valid_day_with_bad_counters(
    tmp_path, monkeypatch, bad_bucket
):
    # Exact startup read->load seam; no real server, socket, or per-user secret.
    data = {"days": {"2026-11-11": {"requests": 2}, "2026-11-12": bad_bucket}}
    path = tmp_path / "daemon_metrics.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(sd, "_metrics_file_path", lambda root: path)
    metrics = sd._DemandMetrics()
    metrics.load(sd._read_demand_metrics_days(tmp_path))
    assert metrics._days["2026-11-11"]["requests"] == 2


def test_startup_metrics_positive_control(tmp_path, monkeypatch):
    path = tmp_path / "daemon_metrics.json"
    path.write_text('{"days":{"2026-11-12":{"requests":2,"by_command":{"defs":2}}}}')
    monkeypatch.setattr(sd, "_metrics_file_path", lambda root: path)
    metrics = sd._DemandMetrics()
    metrics.load(sd._read_demand_metrics_days(tmp_path))
    assert metrics._days["2026-11-12"]["requests"] == 2
    assert metrics._days["2026-11-12"]["by_command"] == {"defs": 2}
