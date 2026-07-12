"""TDD for task #108: extend the warm session daemon (task #94 Tier-1) to serve
``tg orient`` and ``tg agent`` (Tier-2 of the latency moat).

Neither command was daemon-served before this: `orient` called `build_orient_capsule`
directly and `agent` called `build_agent_capsule` directly, so every call paid the full
repo-map build cost even with a warm daemon running for every other command family. This
module proves the same warm-vs-cold parity contract task #94/#107/#113 already established
for context-render/edit-plan/the 5 symbol commands, plus two Tier-2-specific correctness
traps identified in the design review:

- TRAP A (agent only): `build_symbol_blast_radius_from_map` (used by the daemon's cached-map
  call-site-evidence collector) has NO literal-seed rescue, unlike the cold
  `build_symbol_blast_radius` wrapper `_collect_capsule_call_site_evidence` calls (which DOES
  retry via `_literal_symbol_seed_files` on a truncated no_match). A warm agent capsule must
  detect that condition and signal the client to discard the response and fall to cold,
  exactly like the existing #107 fix for the standalone `blast-radius` command.
- TRAP B (agent only): `suggested_scope` is computed in the `build_context_render` WRAPPER
  (repo_map.py), not `build_context_render_from_map` -- `build_agent_capsule_from_map` must
  replicate that block against its own map or a warm capsule silently drops `suggested_scope`
  on a truncated scan.

Reuses the real-daemon harness from test_symbol_daemon_autostart.py (task #94 Part A):
``_real_daemon``/``_serve`` start a genuine in-process ``_ThreadedSessionDaemon``;
``_probe_fake_for`` lets the CLI's daemon-discovery see it without a real ``daemon.json`` on
disk; ``_project``/``_flat_repo``/``_autostart_env``/``_cli_json``/``_assert_warm_matches_cold``
are the same fixtures/helpers the byte-identity suite already uses.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, orient_capsule, repo_map, session_daemon
from tensor_grep.cli.main import app
from tests.unit.test_symbol_daemon_autostart import (
    _assert_warm_matches_cold,
    _autostart_env,
    _cli_json,
    _flat_repo,
    _probe_fake_for,
    _project,
    _real_daemon,
    _serve,
)

runner = CliRunner()


def _request(
    server: session_daemon._ThreadedSessionDaemon,
    request: dict[str, Any],
    token: str = "test-token",
) -> dict[str, Any]:
    host, port = server.server_address
    return session_daemon._daemon_request(str(host), int(port), request, token=token)


# ------------------------------------------------------------------------------------------
# 1. orient/agent _from_map == cold, byte-identical (parity by construction for the map).
# ------------------------------------------------------------------------------------------


def test_orient_from_map_matches_cold_byte_identical(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rm = repo_map.build_repo_map(str(project), max_repo_files=2000)
    warm = orient_capsule.build_orient_capsule_from_map(rm)
    cold = orient_capsule.build_orient_capsule(str(project), max_repo_files=2000)
    assert warm == cold


def test_agent_from_map_matches_cold_byte_identical(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rm = repo_map.build_repo_map(str(project), max_repo_files=2000)
    warm = agent_capsule.build_agent_capsule_from_map(rm, "helper", max_repo_files=2000)
    cold = agent_capsule.build_agent_capsule("helper", str(project), max_repo_files=2000)
    assert warm == cold


# ------------------------------------------------------------------------------------------
# 2. warm == cold end-to-end, through a REAL spawned daemon.
# ------------------------------------------------------------------------------------------


def test_orient_warm_matches_cold_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["orient", str(project), "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        first_warm = _cli_json(["orient", str(project), "--json"])
        second_warm = _cli_json(["orient", str(project), "--json"])
    finally:
        server.shutdown()
        server.server_close()

    assert second_warm["daemon_response_cache"]["status"] == "hit"
    assert first_warm["routing_reason"] == "session-orient"
    _assert_warm_matches_cold(second_warm, cold_payload)


def test_agent_warm_matches_cold_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["agent", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        first_warm = _cli_json(["agent", str(project), "helper", "--json"])
        second_warm = _cli_json(["agent", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    assert second_warm["daemon_response_cache"]["status"] == "hit"
    assert first_warm["routing_reason"] == "session-agent"
    _assert_warm_matches_cold(second_warm, cold_payload)


# ------------------------------------------------------------------------------------------
# 3. TRAP A: a warm call-site-evidence no_match on a possibly-truncated map is unreliable (no
#    literal-seed rescue on the _from_map path) and must be discarded client-side.
# ------------------------------------------------------------------------------------------


def test_agent_call_site_evidence_from_map_flags_truncated_no_match_unreliable(
    tmp_path: Path,
) -> None:
    """Direct unit coverage of the trap-detection logic: a target symbol that does not exist
    anywhere in a possibly-truncated map must come back flagged unreliable, not just skipped,
    so the caller knows to fall back to cold rather than trust the omission."""
    project = _flat_repo(tmp_path, 6)
    # max_repo_files=1 truncates the scan (possibly_truncated=True); the target symbol below
    # does not exist in ANY of the 6 files, so blast-radius genuinely finds nothing -- but on a
    # truncated map that no_match is unreliable (no literal-seed rescue on the _from_map path).
    rm = repo_map.build_repo_map(str(project), max_repo_files=1)
    assert rm["scan_limit"]["possibly_truncated"] is True
    target = {"symbol": "totally_missing_symbol", "confidence": 0.9}
    related, evidence, unreliable = agent_capsule._collect_capsule_call_site_evidence_from_map(
        "totally_missing_symbol",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
    )
    assert related == []
    assert evidence["status"] == "skipped"
    assert unreliable is True


def test_agent_call_site_evidence_from_map_complete_map_not_unreliable(tmp_path: Path) -> None:
    """Precision guard mirroring test_blast_radius_daemon_no_match_stays_warm_when_map_complete:
    a genuine no_match on a COMPLETE (non-truncated) map must NOT be flagged unreliable, or
    every real miss would be discarded and the daemon speedup defeated for the common case."""
    project = _project(tmp_path)
    rm = repo_map.build_repo_map(str(project), max_repo_files=2000)
    assert "scan_limit" not in rm or not rm["scan_limit"].get("possibly_truncated")
    target = {"symbol": "totally_missing_symbol", "confidence": 0.9}
    related, _evidence, unreliable = agent_capsule._collect_capsule_call_site_evidence_from_map(
        "totally_missing_symbol",
        rm,
        target,
        include_blast_radius=True,
        max_files=3,
        seed_confidence=0.9,
    )
    assert related == []
    assert unreliable is False


def test_agent_daemon_falls_to_cold_on_unreliable_call_site_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """End-to-end client-side discard: a daemon response carrying the internal
    `daemon_evidence_unreliable` sentinel must never reach the user -- the client wrapper
    discards it and the CLI falls through to a real cold build instead."""
    project = _project(tmp_path)
    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, request: dict[str, Any]) -> dict[str, Any]:
        assert request["command"] == "agent"
        return {
            "version": 1,
            "path": str(project),
            "query": "helper",
            "primary_target": {"file": str(project / "m.py"), "symbol": "helper", "line": 1},
            "alternative_targets": [],
            "confidence": {"overall": 0.9},
            "ask_user_before_editing": {"required": False, "reasons": []},
            "call_site_evidence": {"status": "skipped", "reason": "not found by blast-radius"},
            "daemon_evidence_unreliable": True,
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = runner.invoke(app, ["agent", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # The mocked sentinel payload must NOT have been served -- a real cold build ran instead,
    # which resolves the real primary target evidence for this fixture (a genuine caller edge).
    assert "daemon_evidence_unreliable" not in payload
    assert payload["routing_reason"] == "agent-context-capsule"


# ------------------------------------------------------------------------------------------
# 4. TRAP B: suggested_scope (computed in the WRAPPER, not _from_map) must be replicated by
#    build_agent_capsule_from_map on a truncated scan.
# ------------------------------------------------------------------------------------------


def test_agent_from_map_preserves_suggested_scope_on_truncated_scan(tmp_path: Path) -> None:
    project = _flat_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(project), max_repo_files=1)
    assert rm["scan_limit"]["possibly_truncated"] is True

    warm = agent_capsule.build_agent_capsule_from_map(rm, "helper_0", max_repo_files=1)
    cold = agent_capsule.build_agent_capsule("helper_0", str(project), max_repo_files=1)

    assert "suggested_scope" in cold, "fixture must actually exercise suggested_scope"
    assert warm.get("suggested_scope") == cold.get("suggested_scope")
    assert warm["suggested_scope"]["dirs"] == [str(project / "src")]


def test_agent_daemon_end_to_end_preserves_suggested_scope(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _flat_repo(tmp_path, 6)
    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        result = runner.invoke(
            app,
            ["agent", str(project), "helper_0", "--max-repo-files", "1", "--json"],
        )
    finally:
        server.shutdown()
        server.server_close()

    assert result.exit_code == 2, result.output  # scan-truncated -> exit 2, per _scan_incomplete
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload.get("suggested_scope", {}).get("dirs") == [str(project / "src")]


# ------------------------------------------------------------------------------------------
# 5. Stale-invalidation on an added file (the #113 trap-#1 killer, extended to orient/agent).
# ------------------------------------------------------------------------------------------


def test_agent_daemon_response_cache_invalidates_on_added_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "agent",
            "path": str(project),
            "query": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, request)
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"
        assert first["call_site_evidence"].get("returned_call_sites", 0) == 0

        (project / "caller.py").write_text(
            "from m import helper\n\n\ndef wrapper():\n    return helper()\n",
            encoding="utf-8",
        )

        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in second, second
    assert second["daemon_response_cache"]["status"] == "miss"
    related = second.get("related_call_sites", [])
    assert any(str(project / "caller.py") in str(r.get("file", "")) for r in related), second


def test_orient_daemon_response_cache_invalidates_on_added_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "orient",
            "path": str(project),
            "refresh_on_stale": True,
        }
        first = _request(server, request)
        assert "error" not in first, first
        first_files = {c["file"] for c in first.get("central_files", [])}
        assert str(project / "caller.py") not in first_files

        (project / "caller.py").write_text(
            "from m import helper\n\n\ndef wrapper():\n    return helper()\n",
            encoding="utf-8",
        )

        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in second, second
    second_files = {c["file"] for c in second.get("central_files", [])}
    assert str(project / "caller.py") in second_files, second.get("central_files")


# ------------------------------------------------------------------------------------------
# 6. Fail-open when no daemon is reachable -- the CLI must still succeed via the cold path.
# ------------------------------------------------------------------------------------------


def test_orient_fails_open_when_daemon_down(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    _autostart_env(monkeypatch, enabled=True)

    def _no_daemon(_root: Path) -> None:
        return None

    monkeypatch.setattr(session_daemon, "_probe_daemon", _no_daemon)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no autostart spawn expected in this fail-open probe-miss test")

    # A probe miss is allowed to fire a non-blocking autostart -- stub it inert instead of
    # asserting it is never called (see _maybe_symbol_command_via_running_daemon's own
    # must-fix-3 contract: THIS call must still run cold, but a spawn attempt is legitimate).
    monkeypatch.setattr(
        session_daemon, "maybe_autostart_session_daemon_nonblocking", lambda *a, **k: None
    )

    result = runner.invoke(app, ["orient", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "orient"  # cold value, never overwritten to session-orient


def test_agent_fails_open_when_daemon_down(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    _autostart_env(monkeypatch, enabled=True)

    def _no_daemon(_root: Path) -> None:
        return None

    monkeypatch.setattr(session_daemon, "_probe_daemon", _no_daemon)
    monkeypatch.setattr(
        session_daemon, "maybe_autostart_session_daemon_nonblocking", lambda *a, **k: None
    )

    result = runner.invoke(app, ["agent", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "agent-context-capsule"


def test_agent_daemon_error_response_falls_open(tmp_path: Path, monkeypatch: Any) -> None:
    """A daemon that responds but with an error payload must be treated identically to no
    daemon at all -- never surfaced to the user as a failure."""
    project = _project(tmp_path)
    _autostart_env(monkeypatch, enabled=True)

    def fake_request(_path: str, _request: dict[str, Any]) -> dict[str, Any]:
        return {"version": 1, "error": {"code": "boom", "message": "synthetic failure"}}

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = runner.invoke(app, ["agent", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output


# ------------------------------------------------------------------------------------------
# 7. agent: native-only provider gate + GPU requests must never reach the daemon.
# ------------------------------------------------------------------------------------------


def test_agent_daemon_routing_skipped_for_non_native_provider(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("non-native provider must not reach the daemon")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    _autostart_env(monkeypatch, enabled=True)

    result = runner.invoke(app, ["agent", str(project), "helper", "--provider", "lsp", "--json"])
    assert result.exit_code == 0, result.output


def test_agent_daemon_routing_skipped_for_gpu_device_ids(tmp_path: Path, monkeypatch: Any) -> None:
    """GPU evidence shells out to a fresh `tg search` subprocess (agent_capsule.
    _agent_gpu_evidence) and must never run inside a daemon worker thread -- always cold."""
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a --gpu-device-ids request must not reach the daemon")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    _autostart_env(monkeypatch, enabled=True)

    result = runner.invoke(
        app, ["agent", str(project), "helper", "--gpu-device-ids", "0", "--json"]
    )
    assert result.exit_code == 0, result.output


# ------------------------------------------------------------------------------------------
# 8. Response-cache bounded: orient/agent reuse the SAME byte-budget LRU (no new cache), so an
#    oversized response must still be skipped, not cached unbounded.
# ------------------------------------------------------------------------------------------


def test_orient_and_agent_response_cache_keys_are_cacheable() -> None:
    payload = {
        "root": "C:/proj",
        "created_at": "t0",
        "refreshed_at": "t0",
        "repo_map": {"files": ["a.py"], "symbols": ["helper"]},
    }
    orient_key = session_daemon._response_cache_key_for_command(
        "orient", "session-1", "C:/proj", {"command": "orient"}, payload
    )
    agent_key = session_daemon._response_cache_key_for_command(
        "agent",
        "session-1",
        "C:/proj",
        {"command": "agent", "query": "helper"},
        payload,
    )
    assert orient_key is not None
    assert agent_key is not None


def test_orient_response_cache_oversized_response_is_skipped() -> None:
    cache = session_daemon._SessionResponseCache(max_entries=32, max_size_bytes=64)
    key = session_daemon._orient_response_cache_key(
        "session-1", "C:/proj", {"command": "orient"}, {"repo_map": {}}
    )
    huge_response = {"central_files": ["x" * 1000]}
    cache.put(key, huge_response)
    assert cache.oversized_skips == 1
    assert cache.get(key) is None


# ------------------------------------------------------------------------------------------
# 9. Concurrency: many concurrent orient/agent requests against one real daemon must not race
#    or corrupt the shared response cache / implicit-session bookkeeping.
# ------------------------------------------------------------------------------------------


def _request_with_connect_retry(
    server: session_daemon._ThreadedSessionDaemon, request: dict[str, Any]
) -> dict[str, Any]:
    """`_daemon_request`'s connect timeout (0.5s, session_daemon._DAEMON_CONNECT_TIMEOUT_SECONDS)
    is tuned for the single-client happy path; under N simultaneous connects the loopback accept
    backlog can transiently make one attempt time out even though the daemon itself is healthy.
    A bounded retry absorbs that connect-level environment jitter without masking a genuine
    threading race, which would surface as wrong DATA/an error response, not a connect timeout."""
    last_error: TimeoutError | OSError | None = None
    for _attempt in range(5):
        try:
            return _request(server, request)
        except (TimeoutError, OSError) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def test_concurrent_orient_and_agent_requests_no_race(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        requests = [
            {"command": "orient", "path": str(project), "refresh_on_stale": True} for _ in range(4)
        ] + [
            {
                "command": "agent",
                "path": str(project),
                "query": "helper",
                "provider": "native",
                "refresh_on_stale": True,
            }
            for _ in range(4)
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda req: _request_with_connect_retry(server, req), requests))
    finally:
        server.shutdown()
        server.server_close()

    for response in results:
        assert "error" not in response, response
    orient_paths = {r["path"] for r in results if r.get("routing_reason") == "session-orient"}
    agent_paths = {r["path"] for r in results if r.get("routing_reason") == "session-agent"}
    assert orient_paths == {str(project)}
    assert agent_paths == {str(project)}


# ------------------------------------------------------------------------------------------
# 10. Cross-command key isolation: an orient response must never satisfy an agent cache lookup
#     (or vice versa), even against the same session/payload fingerprint.
# ------------------------------------------------------------------------------------------


def test_orient_key_never_collides_with_agent_key() -> None:
    payload = {
        "root": "C:/proj",
        "created_at": "t0",
        "refreshed_at": "t0",
        "repo_map": {"files": ["a.py"], "symbols": ["helper"]},
    }
    orient_key = session_daemon._orient_response_cache_key(
        "session-1", "C:/proj", {"command": "orient"}, payload
    )
    agent_key = session_daemon._agent_response_cache_key(
        "session-1", "C:/proj", {"command": "agent", "query": ""}, payload
    )
    assert orient_key != agent_key

    cache = session_daemon._SessionResponseCache()
    cache.put(orient_key, {"kind": "orient-answer"})
    assert cache.get(agent_key) is None
    assert cache.get(orient_key) == {"kind": "orient-answer"}


def test_agent_response_cache_key_isolates_all_fields() -> None:
    payload = {
        "root": "C:/proj",
        "created_at": "t0",
        "refreshed_at": "t0",
        "repo_map": {"files": ["a.py"], "symbols": ["helper"]},
    }
    base_request: dict[str, Any] = {
        "command": "agent",
        "query": "helper",
        "provider": "native",
        "max_files": 3,
        "max_sources": 5,
        "max_tokens": 1200,
        "model": None,
        "max_repo_files": 2000,
    }

    def _key(**overrides: object) -> tuple[str, ...] | None:
        request = {**base_request, **overrides}
        return session_daemon._response_cache_key_for_command(
            "agent", "session-1", "C:/proj", request, payload
        )

    base_key = _key()
    assert base_key is not None
    assert _key() == base_key

    varied = {
        "query": _key(query="other"),
        "max_files": _key(max_files=9),
        "max_sources": _key(max_sources=9),
        "max_tokens": _key(max_tokens=99),
        "model": _key(model="gpt"),
        "provider": _key(provider="lsp"),
        "max_repo_files": _key(max_repo_files=1),
    }
    for field, varied_key in varied.items():
        assert varied_key != base_key, f"varying {field!r} did not change the agent cache key"


# ------------------------------------------------------------------------------------------
# 11. Forced off in CI: orient/agent must inherit Tier-1's CI/GITHUB_ACTIONS force-off, exactly
#     like the 5 symbol commands (no separate flag).
# ------------------------------------------------------------------------------------------


def test_orient_never_touches_daemon_when_forced_off_in_ci(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("daemon must not be touched when CI forces autostart off")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    monkeypatch.setattr(session_daemon, "maybe_autostart_session_daemon_nonblocking", _boom)

    result = runner.invoke(app, ["orient", str(project), "--json"])
    assert result.exit_code == 0, result.output


def test_agent_never_touches_daemon_when_forced_off_in_ci(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "1")
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    project = _project(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("daemon must not be touched when CI forces autostart off")

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", _boom)
    monkeypatch.setattr(session_daemon, "maybe_autostart_session_daemon_nonblocking", _boom)

    result = runner.invoke(app, ["agent", str(project), "helper", "--json"])
    assert result.exit_code == 0, result.output
