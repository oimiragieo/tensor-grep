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


def write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return str(path)


def test_click_import_does_not_depend_on_cli_module(tmp_path):
    cli = write(tmp_path / "pkg/cli.py", "def thing(): pass\n")
    other = write(tmp_path / "tools/other.py", "import click\n")
    assert not rm._confirm_import_edges(Path(other), cli, tmp_path)
    distances = rm._reverse_import_distances([cli], [cli, other], {other: ["click"]})
    assert other not in distances, "substring cli matched unrelated click import"


@pytest.mark.parametrize("target,expected", [("pkg/a.py", True), ("other/a.py", False)])
def test_duplicate_basename_edges_resolve_exact_module(tmp_path, target, expected):
    write(tmp_path / "pkg/__init__.py", "")
    a = write(tmp_path / "pkg/a.py", "x=1\n")
    other = write(tmp_path / "other/a.py", "x=2\n")
    consumer = write(tmp_path / "consumer.py", "import pkg.a\n")
    selected = str(tmp_path / target)
    confirmed = rm._confirm_import_edges(Path(consumer), selected, tmp_path)
    assert bool(confirmed) is expected, "exact house resolver control failed"
    distances = rm._reverse_import_distances(
        [selected], [a, other, consumer], {consumer: ["pkg.a"]}
    )
    assert (consumer in distances) is expected, "coarse basename edge leaked into distance graph"


@pytest.fixture
def seed_fixtures():
    spec = importlib.util.spec_from_file_location(
        "wave4_seed_baseline",
        Path(__file__).resolve().parents[2] / "tests/unit/test_edit_plan_seed.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["context", "blast"])
def test_preserve_termui_and_edit_order_pins(tmp_path, seed_fixtures, mode):
    p = seed_fixtures._build_python_termui_radius_project(tmp_path / "termui")
    render = rm.build_context_render if mode == "context" else rm.build_symbol_blast_radius_render
    seed = render("unstyle", p["project"])["edit_plan_seed"]
    assert seed["dependent_files"] == [str(p["init"].resolve()), str(p["core"].resolve())]
    assert str(p["decorators"].resolve()) not in seed["dependent_files"]
    assert str(p["example"].resolve()) not in seed["dependent_files"]
    p = seed_fixtures._build_linear_project(tmp_path / "linear")
    render_linear = (
        seed_fixtures._render_context if mode == "context" else seed_fixtures._render_blast_radius
    )
    seed = render_linear(p["project"])["edit_plan_seed"]
    assert seed["edit_ordering"] == [
        str(p[x].resolve()) for x in ["payments", "service", "report", "test"]
    ]


@pytest.mark.parametrize(
    "rule,path,is_dir,expected",
    [
        ("[Oo]bj", "obj", True, True),
        ("[Bb]in/", "bin", True, True),
        ("[!a]x", "bx", False, True),
        ("[!a]x", "ax", False, None),
        ("[]]", "]", False, True),
        ("[abc", "[abc", False, True),
        ("[a/b]", "a/b", False, None),
        ("[a/b]", "a", False, None),
        ("*.log\n!keep.log", "keep.log", False, False),
        ("dist/", "dist/x.py", False, True),
        ("/root.py", "sub/root.py", False, None),
        ("a?b", "a/b", False, None),
    ],
)
def test_gitignore_character_classes_and_preserved_semantics(
    tmp_path, rule, path, is_dir, expected
):
    assert (
        _GitignoreMatcher(tmp_path, rule.splitlines()).check(Path(path), is_dir=is_dir) is expected
    )


@pytest.fixture
def owned_junction(tmp_path, tmp_path_factory, request):
    if sys.platform != "win32" or not hasattr(os.path, "isjunction"):
        pytest.skip("BLOCKED_SETUP: native Windows junction capability unavailable")
    fixture = tmp_path.resolve()
    basetemp = tmp_path_factory.getbasetemp().resolve()
    assert fixture.is_relative_to(basetemp), "fixture must stay under owned pytest basetemp"
    root, target = fixture / "repo", fixture / "outside"
    for p in (root, target):
        assert p.resolve().is_relative_to(fixture)
        p.mkdir()
    write(root / "ordinary.py", "ordinary=1\n")
    write(target / "outside_marker.py", "outside=1\n")
    kind = request.param
    link = root / ("self" if kind == "loop" else "linked")
    destination = root if kind == "loop" else target
    assert link.parent.resolve().is_relative_to(fixture)
    assert destination.resolve().is_relative_to(fixture)
    quoted_link = str(link).replace("'", "''")
    quoted_destination = str(destination).replace("'", "''")
    command = (
        "New-Item -ItemType Junction -Path '"
        + quoted_link
        + "' -Target '"
        + quoted_destination
        + "' -ErrorAction Stop | Out-Null"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode:
        pytest.skip("BLOCKED_SETUP: junction creation refused: " + proc.stderr)
    try:
        assert os.path.isjunction(link), "BLOCKED_SETUP: creation did not produce junction"
        yield root, kind
    finally:
        assert link.parent.resolve().is_relative_to(fixture)
        assert link.resolve().is_relative_to(fixture)
        assert os.path.isjunction(link)
        os.rmdir(link)  # Delete owned LINK only; never recursively touch its target.
        assert not link.exists() and root.exists() and target.exists()
        assert (target / "outside_marker.py").exists()


@pytest.mark.parametrize("owned_junction", ["loop", "escape"], indirect=True)
@pytest.mark.parametrize("walker", ["bucket", "capped"])
def test_repo_walk_refuses_owned_junctions(owned_junction, walker):
    root, kind = owned_junction
    if walker == "bucket":
        files = list(islice(rm._iter_repo_bucket_files(root), 8))
    else:
        files = rm._iter_repo_files(root, max_files=8, deadline_monotonic=time.monotonic() + 3)
    assert root / "ordinary.py" in files, "non-link positive control missing"
    assert files == [root / "ordinary.py"], f"{walker} followed {kind} junction: {files!r}"
