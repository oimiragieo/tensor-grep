"""TDD for task #94: the warm-daemon Tier-1 fast path.

Scope: the 5 symbol commands (defs/impact/refs/callers/blast-radius) gain a routing path
through a running ``tg session daemon``, gated end-to-end behind the ``TG_SESSION_DAEMON_AUTOSTART``
env flag. Part A (this file's original version) shipped the flag DEFAULT OFF (opt-in). Task #94
PR-1 flips it to DEFAULT ON (opt-out): unset -- or any value other than an explicit falsy token
(``0``/``false``/``no``/``off``) -- now enables the fast path; the cold-path behavior when
disabled (explicitly or via the CI/GITHUB_ACTIONS force-off) is still byte-for-byte unchanged.

Covers, in file order, one section per must-fix from the design-gate verdict (SHIP-WITH-CHANGES):

- Must-fix 4: TG_SESSION_DAEMON_AUTOSTART default-ON-unless-explicitly-disabled, plus the
  CI/GITHUB_ACTIONS force-off.
- Must-fix 1 (CORE SCOPE-FIX): ``_implicit_session_id_for_request`` (session_daemon.py)
  previously only recognized context_render/context_edit_plan; a symbol command sent to the
  daemon with no explicit session_id fell through to ``get_session("", path)`` ->
  FileNotFoundError -> an error response -> permanent cold fallback. The "no explicit session"
  section proves the fix.
- Must-fix 5: warm-vs-cold byte identity (all 5 commands), and the exit-2 fault-injection
  contract (docs/CONTRACTS.md:109) via the daemon route.
- Must-fix 3: the fire-and-forget non-blocking spawn primitive never blocks/polls waiting for
  warmup.
- Trap T3 (task #94 PR-1): the autouse tests/conftest.py fixture that forces the now-default-ON
  flag back off for the whole suite, so an ordinary CliRunner test never spawns a real daemon.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli import session_daemon
from tensor_grep.cli.main import _session_daemon_autostart_enabled, app

runner = CliRunner()


def _project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    return project.resolve()


def _flat_repo(root: Path, count: int) -> Path:
    """A project with `count` trivial .py files, so a small --max-repo-files genuinely
    truncates the scan (possibly_truncated=True), not just vendor/cache files."""
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index:03d}.py").write_text(
            f"def helper_{index}():\n    return {index}\n", encoding="utf-8"
        )
    return project.resolve()


def _serve(server: session_daemon._ThreadedSessionDaemon) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _real_daemon(root: Path, token: str = "test-token") -> session_daemon._ThreadedSessionDaemon:
    """Start a REAL (in-process, threaded, loopback) session daemon for `root`."""
    return session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token=token)


def _probe_fake_for(server: session_daemon._ThreadedSessionDaemon, token: str):
    host, port = server.server_address

    def _fake_probe(_root: Path) -> dict[str, Any]:
        return {
            "host": str(host),
            "port": int(port),
            "token": token,
            "pid": 0,
            "started_at": "test",
        }

    return _fake_probe


def _autostart_env(monkeypatch, *, enabled: bool) -> None:
    if enabled:
        monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")
    else:
        # Default-ON (task #94 PR-1): unset no longer means disabled, so "disabled" must be
        # spelled as an explicit falsy token, not a delenv.
        monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "0")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


# ------------------------------------------------------------------------------------------
# Must-fix 4: TG_SESSION_DAEMON_AUTOSTART -- default ON (opt-out), forced off in CI.
# ------------------------------------------------------------------------------------------


def test_autostart_enabled_when_unset(monkeypatch) -> None:
    """Task #94 PR-1: the conscious flip. Unset now means ENABLED, not disabled."""
    monkeypatch.delenv("TG_SESSION_DAEMON_AUTOSTART", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert _session_daemon_autostart_enabled() is True


def test_autostart_enabled_when_flag_truthy(monkeypatch) -> None:
    _autostart_env(monkeypatch, enabled=True)
    assert _session_daemon_autostart_enabled() is True


def test_autostart_disabled_when_explicit_falsy(monkeypatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for token in ("0", "false", "no", "off", "FALSE", "Off"):
        monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", token)
        assert _session_daemon_autostart_enabled() is False, token


def test_autostart_forced_off_in_ci(monkeypatch) -> None:
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert _session_daemon_autostart_enabled() is False


def test_autostart_forced_off_in_github_actions(monkeypatch) -> None:
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _session_daemon_autostart_enabled() is False


def test_autostart_forced_off_in_ci_even_when_unset(monkeypatch) -> None:
    """The CI force-off must win over the new default-ON, not just over an explicit opt-in --
    otherwise a CI job that never touches TG_SESSION_DAEMON_AUTOSTART would now autostart a
    background daemon it never used to."""
    monkeypatch.delenv("TG_SESSION_DAEMON_AUTOSTART", raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert _session_daemon_autostart_enabled() is False


def test_defs_default_off_never_touches_daemon(tmp_path: Path, monkeypatch) -> None:
    """The no-op proof: with the flag unset, the daemon module is never even called into."""
    _autostart_env(monkeypatch, enabled=False)
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("daemon must not be touched when TG_SESSION_DAEMON_AUTOSTART is off")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    monkeypatch.setattr(session_daemon, "maybe_autostart_session_daemon_nonblocking", _boom)

    result = runner.invoke(app, ["defs", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["symbol"] == "helper"
    assert any(d["name"] == "helper" for d in payload["definitions"])


# ------------------------------------------------------------------------------------------
# Must-fix 1 (CORE SCOPE-FIX): the daemon now serves a symbol command with NO explicit
# session_id -- previously get_session("", path) -> FileNotFoundError -> error response.
# ------------------------------------------------------------------------------------------


def test_daemon_serves_defs_with_no_explicit_session(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        response = session_daemon._daemon_request(
            str(server.server_address[0]),
            int(server.server_address[1]),
            {"command": "defs", "path": str(project), "symbol": "helper"},
            token="test-token",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in response, response
    assert response["symbol"] == "helper"
    assert any(d["name"] == "helper" for d in response["definitions"])


def test_daemon_serves_callers_with_no_explicit_session(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        response = session_daemon._daemon_request(
            str(server.server_address[0]),
            int(server.server_address[1]),
            {"command": "callers", "path": str(project), "symbol": "helper"},
            token="test-token",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in response, response
    assert response["symbol"] == "helper"
    assert any(c.get("file", "").endswith("m.py") for c in response["callers"])


def test_daemon_serves_blast_radius_with_no_explicit_session(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        response = session_daemon._daemon_request(
            str(server.server_address[0]),
            int(server.server_address[1]),
            {"command": "blast_radius", "path": str(project), "symbol": "helper"},
            token="test-token",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in response, response
    assert response["symbol"] == "helper"


def test_daemon_still_rejects_unknown_command_with_no_session(tmp_path: Path) -> None:
    """Guard-rail: the fix is scoped to the 7 named commands, not "anything goes"."""
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        response = session_daemon._daemon_request(
            str(server.server_address[0]),
            int(server.server_address[1]),
            {"command": "not_a_real_command", "path": str(project)},
            token="test-token",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert "error" in response


# ------------------------------------------------------------------------------------------
# Must-fix 5: warm-vs-cold byte identity (defs + callers) via the real daemon + CLI.
# ------------------------------------------------------------------------------------------


def _cli_json(args: list[str]) -> dict[str, Any]:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _assert_warm_matches_cold(warm: dict[str, Any], cold: dict[str, Any]) -> None:
    # session_id/serve_cache are daemon-transport-ONLY fields (cold never has them). But
    # routing_reason exists on BOTH arms with a DIFFERENT value by design: build_symbol_*_from_map
    # (shared by both the cold builder and the daemon dispatch) stamps "symbol-defs"; the daemon
    # dispatch (_serve_session_request_from_payload) then overwrites it to "session-defs" to make
    # the response's origin visible. audit #113: daemon_response_cache is ALSO daemon-transport-
    # only (its hit/miss/entries/hits/misses counters are cache-instance state, not part of the
    # answer, and will legitimately differ between the warm and cold arms -- cold never touches
    # the response cache at all). Strip all four from BOTH sides -- a .pop on an absent key is a
    # no-op -- so this proves the actual ANSWER is byte-identical, not that the daemon adds zero
    # provenance metadata (it is expected to add/override session_id/routing_reason/serve_cache/
    # daemon_response_cache).
    warm = dict(warm)
    cold = dict(cold)
    for extra_key in ("session_id", "routing_reason", "serve_cache", "daemon_response_cache"):
        warm.pop(extra_key, None)
        cold.pop(extra_key, None)
    # token_budget.estimated_tokens is a byte-size ESTIMATE of the payload at the moment
    # _apply_symbol_token_budget ran. The warm arm's raw payload already carries the 2
    # harmless additive provenance fields above at that point (the daemon stamps them before
    # returning), which nudges the estimate up by a few dozen tokens with ZERO effect on the
    # actual answer content. Compare the DECISIONS the estimate drives (truncated /
    # primary_truncated), not the raw estimate number, which is expected to differ slightly.
    warm_budget = warm.get("token_budget")
    cold_budget = cold.get("token_budget")
    if isinstance(warm_budget, dict) and isinstance(cold_budget, dict):
        assert warm_budget.get("truncated") == cold_budget.get("truncated")
        assert warm_budget.get("primary_truncated") == cold_budget.get("primary_truncated")
        warm.pop("token_budget", None)
        cold.pop("token_budget", None)
    assert warm == cold


def test_defs_warm_matches_cold_byte_identity(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["defs", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(["defs", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(warm_payload, cold_payload)


def test_callers_warm_matches_cold_byte_identity(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["callers", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(["callers", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(warm_payload, cold_payload)


def test_refs_warm_matches_cold_byte_identity(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["refs", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(["refs", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(warm_payload, cold_payload)


def test_impact_warm_matches_cold_byte_identity(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["impact", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(["impact", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(warm_payload, cold_payload)


def test_blast_radius_warm_matches_cold_byte_identity(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["blast-radius", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        warm_payload = _cli_json(["blast-radius", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(warm_payload, cold_payload)


def test_defs_daemon_routing_skipped_for_non_native_provider(tmp_path: Path, monkeypatch) -> None:
    """--provider lsp/hybrid must never route through the native-only daemon session."""
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("non-native provider must not reach the daemon")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    _autostart_env(monkeypatch, enabled=True)

    result = runner.invoke(app, ["defs", str(project), "helper", "--provider", "lsp", "--json"])
    assert result.exit_code == 0, result.output


# ------------------------------------------------------------------------------------------
# Must-fix 5 (second half): exit-2 fault injection -- a truncated warm payload must still
# exit 2 (docs/CONTRACTS.md:109), exactly like the cold path.
# ------------------------------------------------------------------------------------------


def test_defs_daemon_exit2_on_real_scan_truncation(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: a REAL daemon whose implicit session was capped by --max-repo-files."""
    project = _flat_repo(tmp_path, 6)
    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        result = runner.invoke(
            app,
            ["defs", str(project), "helper_0", "--max-repo-files", "1", "--json"],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["result_incomplete"] is True


def test_callers_daemon_exit2_on_scan_truncation(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, request: dict[str, Any]) -> dict[str, Any]:
        assert request["command"] == "callers"
        return {
            "symbol": "helper",
            "path": str(project),
            "callers": [],
            "import_graph_consumers": [],
            "import_graph_consumer_files": [],
            "import_graph_consumer_count": 0,
            "files": [str(project / "m.py")],
            "tests": [],
            "definitions": [{"name": "helper", "file": str(project / "m.py"), "line": 1}],
            "scan_limit": {"possibly_truncated": True},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = runner.invoke(app, ["callers", str(project), "helper", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is True


def test_blast_radius_daemon_exit2_on_scan_truncation(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, request: dict[str, Any]) -> dict[str, Any]:
        assert request["command"] == "blast_radius"
        return {
            "symbol": "helper",
            "path": str(project),
            "definitions": [{"name": "helper", "file": str(project / "m.py"), "line": 1}],
            "callers": [],
            "caller_tree": [],
            "files": [str(project / "m.py")],
            "tests": [],
            "imports": [],
            "import_graph_consumers": [],
            "blast_radius_score": 0,
            "scan_limit": {"possibly_truncated": True},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = runner.invoke(app, ["blast-radius", str(project), "helper", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True


# ------------------------------------------------------------------------------------------
# Audit #107 (#94 flip blocker): warm/cold blast-radius divergence on a truncated repo. The
# daemon-served build_symbol_blast_radius_from_map has NO literal-seed rescue (unlike the cold
# build_symbol_blast_radius, which retries via _literal_symbol_seed_files when the map-based
# lookup misses on a truncated scan) -- so a symbol sitting outside the daemon session's scan
# window used to come back as a FALSE no_match from the warm route where cold would find it.
# ------------------------------------------------------------------------------------------


def test_blast_radius_daemon_falls_to_cold_on_truncated_no_match(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end with a REAL daemon: a truncated implicit session (--max-repo-files 1) whose
    scan window excludes the target symbol must not report a false no_match -- the client
    discards the unreliable warm no_match and falls through to cold, which finds it via the
    literal-seed rescue."""
    project = _flat_repo(tmp_path, 6)
    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        # helper_5 lives in m005.py, the LAST of 6 files -- outside the 1-file scan window a
        # max-repo-files=1 cap keeps (test_defs_daemon_exit2_on_real_scan_truncation above
        # already proves file index 0, m000.py, is what survives that cap).
        result = runner.invoke(
            app,
            ["blast-radius", str(project), "helper_5", "--max-repo-files", "1", "--json"],
        )
    finally:
        server.shutdown()
        server.server_close()

    payload = json.loads(result.stdout)
    assert payload.get("no_match") is not True, payload
    assert any(d.get("name") == "helper_5" for d in payload.get("definitions", [])), payload
    # The rescued map is still scan-capped at 1 (the seed file is force-injected on top of the
    # cap, not a cap raise) -- exit 2 (incomplete-but-found), never exit 1 (false not-found).
    assert result.exit_code == 2, result.output


def test_blast_radius_daemon_no_match_stays_warm_when_map_complete(
    tmp_path: Path, monkeypatch
) -> None:
    """Precision guard: a warm no_match on a COMPLETE (non-truncated) map is a REAL miss and must
    stay warm. Falling back to cold here would defeat the daemon speedup for every genuine
    no-match, not just the truncated-and-wrong ones -- the guard fires ONLY on
    no_match AND possibly_truncated together."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, request: dict[str, Any]) -> dict[str, Any]:
        assert request["command"] == "blast_radius"
        return {
            "symbol": "totally_missing_symbol",
            "path": str(project),
            "no_match": True,
            "definitions": [],
            "callers": [],
            "caller_tree": [],
            "files": [],
            "tests": [],
            "imports": [],
            "import_graph_consumers": [],
            "blast_radius_score": 0.0,
            "scan_limit": {"possibly_truncated": False},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    from tensor_grep.cli import repo_map as repo_map_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "must not fall to cold when the warm map was COMPLETE (possibly_truncated=False)"
        )

    monkeypatch.setattr(repo_map_module, "build_symbol_blast_radius", _boom)

    result = runner.invoke(app, ["blast-radius", str(project), "totally_missing_symbol", "--json"])
    assert result.exit_code == 1, result.output  # genuine not-found, not a scan truncation
    payload = json.loads(result.stdout)
    assert payload.get("no_match") is True


def test_defs_daemon_complete_stays_exit_0(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": "helper",
            "path": str(project),
            "definitions": [{"name": "helper", "file": str(project / "m.py"), "line": 1}],
            "files": [str(project / "m.py")],
            "tests": [],
            "scan_limit": {"possibly_truncated": False},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = runner.invoke(app, ["defs", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output


# ------------------------------------------------------------------------------------------
# Must-fix 3: the fire-and-forget spawn primitive never blocks/polls waiting for warmup.
# ------------------------------------------------------------------------------------------


def test_maybe_autostart_nonblocking_never_sleeps(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    spawned: list[tuple[object, ...]] = []

    class _FakePopen:
        def __init__(self, *args: object, **kwargs: object) -> None:
            spawned.append(args)

    def _boom_sleep(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not sleep/poll waiting for daemon warmup")

    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(session_daemon.time, "sleep", _boom_sleep)

    spawned_flag = session_daemon.maybe_autostart_session_daemon_nonblocking(str(root))

    assert spawned_flag is True
    assert len(spawned) == 1


def test_maybe_autostart_nonblocking_skips_when_already_running(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    def _boom_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not spawn a second daemon when one is already running")

    monkeypatch.setattr(
        session_daemon, "_probe_daemon", lambda _root: {"port": 1, "pid": 1, "started_at": "x"}
    )
    monkeypatch.setattr(session_daemon.subprocess, "Popen", _boom_popen)

    assert session_daemon.maybe_autostart_session_daemon_nonblocking(str(root)) is False


def test_maybe_autostart_nonblocking_skips_when_lock_held(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()

    def _boom_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not spawn while another process holds the start lock")

    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon.subprocess, "Popen", _boom_popen)

    resolved_root = session_daemon._resolve_root(root)
    assert session_daemon._try_acquire_daemon_start_lock(resolved_root) is True
    try:
        assert session_daemon.maybe_autostart_session_daemon_nonblocking(str(root)) is False
    finally:
        session_daemon._release_daemon_start_lock(resolved_root)


def test_defs_cold_call_not_blocked_by_autostart(tmp_path: Path, monkeypatch) -> None:
    """Noise-band timing proof: cold call #1 must not wait on the ~5s daemon-start deadline."""
    project = _project(tmp_path)
    spawn_calls: list[str] = []

    def _fake_spawn(path: str = ".") -> bool:
        spawn_calls.append(path)
        return True  # pretend a daemon was spawned, without actually launching a subprocess

    monkeypatch.setattr(session_daemon, "maybe_autostart_session_daemon_nonblocking", _fake_spawn)
    _autostart_env(monkeypatch, enabled=True)

    started = time.monotonic()
    result = runner.invoke(app, ["defs", str(project), "helper", "--json"])
    elapsed = time.monotonic() - started

    assert result.exit_code == 0, result.output
    assert len(spawn_calls) == 1
    # _DAEMON_START_TIMEOUT_SECONDS is 5.0s; a blocked call would take >= that. A generous
    # noise band (well under half the blocking threshold) avoids flaking on a loaded CI box
    # while still failing hard if the fast path regresses to the blocking start_session_daemon.
    assert elapsed < 2.5, f"cold call took {elapsed:.2f}s -- must not block on daemon warmup"


# ------------------------------------------------------------------------------------------
# Trap T3 (task #94 PR-1): the autouse tests/conftest.py fixture, not a local monkeypatch,
# must keep an ordinary CliRunner test daemon-free now that the flag defaults ON.
# ------------------------------------------------------------------------------------------


def test_conftest_autouse_fixture_forces_autostart_off_by_default() -> None:
    """Direct proof the global fixture applied, independent of this file's own _autostart_env
    helper (which this test deliberately never calls)."""
    assert os.environ.get("TG_SESSION_DAEMON_AUTOSTART") == "0"
    assert _session_daemon_autostart_enabled() is False


def test_conftest_autouse_fixture_keeps_ordinary_cli_test_daemon_free(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end proof via a plain CliRunner invocation that never calls _autostart_env.

    Records touches to a list rather than raising inside the stub: `_maybe_symbol_command_via_
    running_daemon` in main.py wraps its daemon calls in a broad `except Exception: return None`
    fail-open (by design, see main.py ~:7935), which would silently swallow a raised
    AssertionError and let the command still exit 0 with a correct (cold-path) answer -- masking
    exactly the regression this test exists to catch. Asserting on the list AFTER `runner.invoke`
    returns sidesteps that.
    """
    project = _project(tmp_path)
    touched: list[str] = []

    def _record_request(*args: object, **kwargs: object) -> dict[str, Any] | None:
        touched.append("request_running_session_daemon")
        return None

    def _record_spawn(*args: object, **kwargs: object) -> bool:
        touched.append("maybe_autostart_session_daemon_nonblocking")
        return False

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _record_request)
    monkeypatch.setattr(session_daemon, "maybe_autostart_session_daemon_nonblocking", _record_spawn)

    result = runner.invoke(app, ["defs", str(project), "helper", "--json"])

    assert result.exit_code == 0, result.output
    assert touched == [], (
        "the autouse conftest fixture should have kept TG_SESSION_DAEMON_AUTOSTART off; the "
        f"daemon module was touched anyway: {touched}"
    )
