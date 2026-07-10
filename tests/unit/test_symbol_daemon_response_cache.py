"""TDD for audit #113: extend the daemon's response cache to the 5 symbol commands.

Task #94 Part A (PR #492) already wired defs/impact/refs/callers/blast_radius to route
through a warm ``tg session daemon`` (an OPTIONAL fast path behind ``TG_SESSION_DAEMON_
AUTOSTART``). That fixed the REPO-MAP build cost (warm defs 4.98s -> 0.89s), but every
daemon-routed symbol request still ran the full per-symbol pipeline (caller-scan / blast-
radius graph walk) from scratch -- callers/blast-radius stayed 4-5.5s even warm. This module
proves the response cache already used for ``context_render``/``context_edit_plan``
(session_daemon.py's ``_SessionResponseCache``) now ALSO serves identical repeated symbol
requests straight from cache.

Reuses the real-daemon harness from test_symbol_daemon_autostart.py (task #94 Part A):
``_real_daemon``/``_serve`` start a genuine in-process ``_ThreadedSessionDaemon``;
``_probe_fake_for`` lets the CLI's daemon-discovery see it without a real ``daemon.json`` on
disk; ``_project``/``_flat_repo``/``_autostart_env``/``_cli_json``/``_assert_warm_matches_cold``
are the same fixtures/helpers the byte-identity suite already uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli import session_daemon
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


def _strip_cache_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    for key in ("daemon_response_cache", "serve_cache", "session_timing"):
        clean.pop(key, None)
    return clean


# ------------------------------------------------------------------------------------------
# Pure key-function coverage: every field the design calls out MUST change the cache key, or
# two DIFFERENT requests could collide on the same entry (cross-request response bleed).
# ------------------------------------------------------------------------------------------


def test_symbol_response_cache_key_isolates_all_fields() -> None:
    payload = {
        "root": "C:/proj",
        "created_at": "t0",
        "refreshed_at": "t0",
        "repo_map": {"files": ["a.py"], "symbols": ["helper"]},
    }
    base_request: dict[str, Any] = {
        "command": "callers",
        "symbol": "helper",
        "provider": "native",
        "max_tests": 5,
        "max_depth": 3,
        "max_repo_files": 2000,
    }

    def _key(**overrides: object) -> tuple[str, ...] | None:
        request = {**base_request, **overrides}
        command = str(request["command"])
        return session_daemon._response_cache_key_for_command(
            command, "session-1", "C:/proj", request, payload
        )

    base_key = _key()
    assert base_key is not None, "symbol commands must now be cacheable (key must not be None)"
    assert _key() == base_key  # determinism: identical inputs -> identical key

    varied = {
        "command": _key(command="blast_radius"),
        "symbol": _key(symbol="other"),
        "provider": _key(provider="lsp"),
        "max_tests": _key(max_tests=9),
        "max_depth": _key(max_depth=1),
        "max_repo_files": _key(max_repo_files=1),
    }
    for field, varied_key in varied.items():
        assert varied_key != base_key, f"varying {field!r} did not change the cache key"

    # session_id and the payload fingerprint must also participate (same discipline as the
    # sibling context-render/edit-plan key functions).
    assert _key() != session_daemon._response_cache_key_for_command(
        "callers", "session-2", "C:/proj", base_request, payload
    )
    other_payload = {**payload, "refreshed_at": "t1"}
    assert base_key != session_daemon._response_cache_key_for_command(
        "callers", "session-1", "C:/proj", base_request, other_payload
    )


def test_defs_impact_refs_are_also_cacheable() -> None:
    """The other 3 of the 5 symbol commands (not just callers/blast_radius) must also gain a
    non-None cache key -- the fix is scoped to all 5, not just the two exercised end-to-end
    below."""
    payload = {
        "root": "C:/proj",
        "created_at": "t0",
        "refreshed_at": "t0",
        "repo_map": {"files": ["a.py"], "symbols": ["helper"]},
    }
    request = {"command": "defs", "symbol": "helper", "provider": "native"}
    for command in ("defs", "impact", "refs"):
        request["command"] = command
        key = session_daemon._response_cache_key_for_command(
            command, "session-1", "C:/proj", request, payload
        )
        assert key is not None, f"{command} must be cacheable"


# ------------------------------------------------------------------------------------------
# Warm repeat -> hit, for callers and blast_radius (the two commands the audit measured at
# 4-5.5s even warm).
# ------------------------------------------------------------------------------------------


def test_callers_daemon_response_cache_hit_on_repeat(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "callers",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, request)
        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in first, first
    assert "error" not in second, second
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert _strip_cache_provenance(first) == _strip_cache_provenance(second)


def test_blast_radius_daemon_response_cache_hit_on_repeat(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "blast_radius",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
            "max_depth": 3,
        }
        first = _request(server, request)
        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in first, first
    assert "error" not in second, second
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert _strip_cache_provenance(first) == _strip_cache_provenance(second)


# ------------------------------------------------------------------------------------------
# Invalidation: a MODIFIED tracked file, an ADDED file (the trap-#1 killer), and a REMOVED
# file must all bust the cache on the NEXT request instead of serving a stale answer.
# ------------------------------------------------------------------------------------------


def test_callers_daemon_response_cache_invalidates_on_modified_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    module = project / "m.py"
    module.write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "callers",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, request)
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"
        assert len(first.get("callers", [])) == 1

        # Append a new call site to the SAME (already-tracked) file -- a size change, so
        # modified-file detection (unconditional, does not need detect_added_files) catches it.
        with module.open("a", encoding="utf-8") as handle:
            handle.write("\n\ndef third():\n    return helper()\n")

        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in second, second
    assert second["daemon_response_cache"]["status"] == "miss"
    assert len(second.get("callers", [])) == 2


def test_callers_daemon_response_cache_invalidates_on_added_file(tmp_path: Path) -> None:
    """The trap-#1 killer: a naive hardcoded ``detect_added_files=False`` (copied verbatim
    from the context-render/edit-plan gate) would let a NEW call site in a brand-new file stay
    permanently invisible to a warm callers answer once the response is cached, because
    nothing about any ALREADY-TRACKED file changed (mtime/size-only staleness sees nothing)."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "callers",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, request)
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"
        assert first.get("callers", []) == []

        (project / "caller.py").write_text(
            "from m import helper\n\n\ndef wrapper():\n    return helper()\n",
            encoding="utf-8",
        )

        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in second, second
    caller_files = {str(c.get("file", "")) for c in second.get("callers", [])}
    assert any(str(project / "caller.py") in entry for entry in caller_files), second.get("callers")
    # This is the assertion a hardcoded detect_added_files=False would fail: it would serve
    # the FIRST (empty-callers) response back out of cache forever instead of recomputing.
    assert second["daemon_response_cache"]["status"] == "miss"


def test_callers_daemon_response_cache_invalidates_on_removed_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "m.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    caller_file = project / "caller.py"
    caller_file.write_text(
        "from m import helper\n\n\ndef wrapper():\n    return helper()\n",
        encoding="utf-8",
    )

    server = _real_daemon(project)
    _serve(server)
    try:
        request = {
            "command": "callers",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, request)
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"
        assert len(first.get("callers", [])) == 1

        caller_file.unlink()

        second = _request(server, request)
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in second, second
    assert second["daemon_response_cache"]["status"] == "miss"
    assert len(second.get("callers", [])) == 0


# ------------------------------------------------------------------------------------------
# Key isolation end-to-end: distinct max_tests / max_depth must never cross-hit each other's
# cached entry.
# ------------------------------------------------------------------------------------------


def test_callers_daemon_response_cache_isolates_by_max_tests(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        base = {
            "command": "callers",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, {**base, "max_tests": 5})
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"

        different = _request(server, {**base, "max_tests": 9})
        assert "error" not in different, different
        assert different["daemon_response_cache"]["status"] == "miss"

        repeat = _request(server, {**base, "max_tests": 5})
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in repeat, repeat
    assert repeat["daemon_response_cache"]["status"] == "hit"


def test_blast_radius_daemon_response_cache_isolates_by_max_depth(tmp_path: Path) -> None:
    project = _project(tmp_path)
    server = _real_daemon(project)
    _serve(server)
    try:
        base = {
            "command": "blast_radius",
            "path": str(project),
            "symbol": "helper",
            "provider": "native",
            "refresh_on_stale": True,
            "max_repo_files": 2000,
        }
        first = _request(server, {**base, "max_depth": 2})
        assert "error" not in first, first
        assert first["daemon_response_cache"]["status"] == "miss"

        different = _request(server, {**base, "max_depth": 4})
        assert "error" not in different, different
        assert different["daemon_response_cache"]["status"] == "miss"

        repeat = _request(server, {**base, "max_depth": 2})
    finally:
        server.shutdown()
        server.server_close()

    assert "error" not in repeat, repeat
    assert repeat["daemon_response_cache"]["status"] == "hit"


# ------------------------------------------------------------------------------------------
# #107 interaction: a truncated (possibly_truncated) implicit-session snapshot must still be
# cacheable, and result_incomplete must survive a cache HIT so the CLI still exits 2.
# ------------------------------------------------------------------------------------------


def test_defs_daemon_response_cache_hit_survives_truncation_cli_exit2(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _flat_repo(tmp_path, 6)
    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        first = runner.invoke(
            app, ["defs", str(project), "helper_0", "--max-repo-files", "1", "--json"]
        )
        second = runner.invoke(
            app, ["defs", str(project), "helper_0", "--max-repo-files", "1", "--json"]
        )
    finally:
        server.shutdown()
        server.server_close()

    assert first.exit_code == 2, first.output
    assert second.exit_code == 2, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["scan_limit"]["possibly_truncated"] is True
    assert first_payload["result_incomplete"] is True
    assert second_payload["scan_limit"]["possibly_truncated"] is True
    assert second_payload["result_incomplete"] is True
    assert first_payload["daemon_response_cache"]["status"] == "miss"
    assert second_payload["daemon_response_cache"]["status"] == "hit"
    # The two payloads carry a byte-identical ANSWER (the hit is a deepcopy of what the miss
    # cached) -- that is the property cache correctness rests on, and it is what
    # _assert_warm_matches_cold verifies. token_budget.estimated_tokens, though, is NOT part of
    # the answer: it is measured CLIENT-SIDE (_apply_symbol_token_budget, main.py) on the FULL
    # received payload INCLUDING the daemon transport provenance (serve_cache /
    # daemon_response_cache), whose "miss" vs "hit" status strings differ by ~2 bytes. That is
    # occasionally enough to cross the ceil(len/3.5) token-estimate boundary depending on the
    # absolute payload length -- observed macOS-ONLY in CI (its /private/var tmp paths are long
    # enough to land on the boundary; ubuntu/windows tmp paths are not; reproduced on Windows
    # too with a longer tmp root). It is a benign transport-provenance artifact, NOT a content
    # divergence: the content, and the content-only token estimate, are byte-identical
    # miss-vs-hit. So assert same-answer via the established _assert_warm_matches_cold discipline
    # (byte-identical content + identical token_budget DECISION -- truncated/primary_truncated --
    # rather than the raw estimate), exactly as the warm-vs-cold byte-identity suite does. NOTE
    # this is NOT the same as stripping estimated_tokens to force a pass: the truncation DECISION
    # is still checked, and the answer content is still asserted byte-identical.
    _assert_warm_matches_cold(second_payload, first_payload)


# ------------------------------------------------------------------------------------------
# Warm-repeat (a genuine cache HIT, not just the first warm/miss call) stays byte-identical to
# cold, exactly like the miss arm test_symbol_daemon_autostart.py already proves.
# ------------------------------------------------------------------------------------------


def test_callers_warm_cache_hit_matches_cold_byte_identity(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["callers", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        first_warm = _cli_json(["callers", str(project), "helper", "--json"])
        assert first_warm["daemon_response_cache"]["status"] == "miss"
        second_warm = _cli_json(["callers", str(project), "helper", "--json"])
        assert second_warm["daemon_response_cache"]["status"] == "hit"
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(second_warm, cold_payload)


def test_blast_radius_warm_cache_hit_matches_cold_byte_identity(
    tmp_path: Path, monkeypatch: Any
) -> None:
    project = _project(tmp_path)
    cold_payload = _cli_json(["blast-radius", str(project), "helper", "--json"])

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        first_warm = _cli_json(["blast-radius", str(project), "helper", "--json"])
        assert first_warm["daemon_response_cache"]["status"] == "miss"
        second_warm = _cli_json(["blast-radius", str(project), "helper", "--json"])
        assert second_warm["daemon_response_cache"]["status"] == "hit"
    finally:
        server.shutdown()
        server.server_close()

    _assert_warm_matches_cold(second_warm, cold_payload)


# ------------------------------------------------------------------------------------------
# Observability: the response_cache_scope string must now name the symbol commands too.
# ------------------------------------------------------------------------------------------


def test_daemon_response_cache_scope_names_symbol_commands() -> None:
    scope = session_daemon._DAEMON_RESPONSE_CACHE_SCOPE
    for command in ("defs", "impact", "refs", "callers", "blast", "context-render", "edit-plan"):
        assert command in scope, f"{command!r} not named in {scope!r}"
