"""TDD for #200: the warm session-daemon dispatch path (``_serve_session_request_from_payload``)
never threaded a wall-clock deadline into the post-map builders for ``agent``/``orient``/
``context_render``/``context_edit_plan``. The cold CLI path's implicit 60s bound
(``agent_capsule.DEFAULT_AGENT_CLI_DEADLINE_SECONDS``) lives INSIDE the cold branch only, so a
request served warm -- the DEFAULT, no ``--deadline`` flag -- ran fully unbounded: ``grep -n
deadline session_store.py`` was ZERO matches before this fix.

This module proves:

(a) a warm-daemon ``agent``/``orient``/``context_render``/``context_edit_plan`` request whose
    underlying builder overruns the new default deadline comes back ``partial=True``/
    ``partial_reason="deadline"`` (``session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS``), and
    the CLI surfaces that as exit 2 -- never a silent exit 0.
(b) a ``partial=True`` response is NEVER written into the daemon's response cache -- a follow-up
    identical request RECOMPUTES instead of replaying a truncated answer that a later, unhurried
    request could have finished (``session_daemon._serve_daemon_response_with_cache``).
(c) a normal (well within budget) warm response is unaffected -- no spurious ``partial`` -- and
    still cached exactly as before.

Deterministic latency injection only (mirrors ``test_repo_map_deadline.py`` /
``test_cli_deadline_coverage_gaps.py``'s proven technique: an ALREADY-EXPIRED absolute
``deadline_monotonic``, no sleep/timing race). Section (a)'s dispatcher-level cases call
``_serve_session_request_from_payload`` directly (no daemon spawn); one end-to-end case drives
the real in-process threaded daemon + CLI (reusing ``test_symbol_daemon_autostart.py``'s proven
harness) to prove the full exit-2 contract, not just the internal dict shape.

Also covers the orient-specific deviation this fix required: unlike
``build_agent_capsule_from_map``/``build_context_render_from_map``/
``build_context_edit_plan_from_map`` (which already accepted ``deadline_monotonic`` per
#639/#642/#645), ``build_orient_capsule_from_map`` did NOT -- verified against the real code,
not assumed. Extending it was necessary to avoid a ``TypeError`` the moment the dispatcher passed
the new kwarg, since ``tg orient``'s COLD path deliberately defaults to unbounded (see
``orient_capsule.build_orient_capsule``'s own docstring/CLI help) and was left untouched; only
the warm-daemon dispatch path supplies a deadline now.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import orient_capsule, repo_map, session_daemon, session_store
from tensor_grep.cli.main import app
from tests.unit.test_symbol_daemon_autostart import (
    _autostart_env,
    _probe_fake_for,
    _real_daemon,
    _serve,
)

runner = CliRunner()


def _project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "m.py").write_text(
        "def helper():\n    return 1\n\n\ndef other():\n    return helper()\n",
        encoding="utf-8",
    )
    return project.resolve()


def _payload_for(project: Path) -> dict[str, Any]:
    opened = session_store.open_session(str(project))
    return session_store.get_session(opened.session_id, str(project))


# ------------------------------------------------------------------------------------------
# (a) an already-overrun default deadline stamps partial=True / partial_reason="deadline" for
#     each of the 4 named commands, instead of silently completing.
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "extra_request", "routing_reason", "expect_partial_reason"),
    [
        # agent/orient stamp partial_reason="deadline" (agent_capsule.py's existing catch-all
        # shape, mirrored for orient by this fix -- see orient_capsule.py).
        ("agent", {"query": "helper"}, "session-agent", True),
        ("orient", {}, "session-orient", True),
        # context_render/context_edit_plan route through repo_map.build_context_pack_from_map,
        # whose PRE-EXISTING deadline contract (test_repo_map_deadline.py) is partial=True +
        # deadline_limit only -- it never sets partial_reason. Verified against the real code,
        # not assumed uniform: this test originally asserted partial_reason for all 4 commands
        # and caught its own wrong assumption when it went red post-fix on these two.
        ("context_render", {"query": "helper"}, "session-context-render", False),
        ("context_edit_plan", {"query": "helper"}, "session-context-edit-plan", False),
    ],
)
def test_warm_daemon_default_deadline_overrun_marks_partial(
    tmp_path: Path,
    monkeypatch: Any,
    command: str,
    extra_request: dict[str, Any],
    routing_reason: str,
    expect_partial_reason: bool,
) -> None:
    project = _project(tmp_path)
    payload = _payload_for(project)

    # Deterministic: force the default budget to already be exhausted BEFORE the builder ever
    # checks time.monotonic() against it -- no sleep, no timing race (same technique
    # test_repo_map_deadline.py uses via a raw `deadline_monotonic=time.monotonic() - 1.0`).
    monkeypatch.setattr(session_store, "WARM_DAEMON_DEFAULT_DEADLINE_SECONDS", -1000.0)

    request = {"command": command, **extra_request}
    response = session_store._serve_session_request_from_payload("session-x", request, payload)

    assert response["routing_reason"] == routing_reason
    assert response.get("partial") is True, response
    if expect_partial_reason:
        assert response.get("partial_reason") == "deadline", response
    else:
        assert isinstance(response.get("deadline_limit"), dict), response
        assert response["deadline_limit"].get("deadline_exceeded") is True, response


def test_agent_warm_daemon_deadline_overrun_exits_2_end_to_end(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """True end-to-end proof of (a): `tg agent` (no --deadline flag -- the daemon's own default)
    exits 2 with partial=True in the JSON when the warm daemon's default deadline is already
    exhausted, never a silent exit 0. Real in-process threaded daemon (test_orient_agent_daemon.py's
    proven harness), monkeypatch applies in-process so the daemon thread sees the same patched
    constant."""
    project = _project(tmp_path)
    monkeypatch.setattr(session_store, "WARM_DAEMON_DEFAULT_DEADLINE_SECONDS", -1000.0)

    server = _real_daemon(project)
    _serve(server)
    try:
        monkeypatch.setattr(session_daemon, "_probe_daemon", _probe_fake_for(server, "test-token"))
        _autostart_env(monkeypatch, enabled=True)
        result = runner.invoke(app, ["agent", str(project), "helper", "--json"])
    finally:
        server.shutdown()
        server.server_close()

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["routing_reason"] == "session-agent"
    assert payload.get("partial") is True, payload
    assert payload.get("partial_reason") == "deadline", payload


def test_warm_daemon_normal_response_unaffected_no_partial(tmp_path: Path) -> None:
    """(c): the real (non-monkeypatched) 60s default is nowhere near exhausted by a trivial
    fixture repo -- no spurious partial=True from the new plumbing on the common case."""
    project = _project(tmp_path)
    payload = _payload_for(project)

    for command, extra in (
        ("agent", {"query": "helper"}),
        ("orient", {}),
        ("context_render", {"query": "helper"}),
        ("context_edit_plan", {"query": "helper"}),
    ):
        request = {"command": command, **extra}
        response = session_store._serve_session_request_from_payload("session-x", request, payload)
        assert response.get("partial") is not True, (command, response)
        assert "partial_reason" not in response, (command, response)


# ------------------------------------------------------------------------------------------
# (b) a partial=True response must never be written into the daemon's response cache -- a
#     follow-up identical request RECOMPUTES rather than replaying a truncated answer.
# ------------------------------------------------------------------------------------------


class _FakeServer:
    def __init__(self) -> None:
        self.response_cache = session_daemon._SessionResponseCache()
        self._response_cache_lock = threading.Lock()


def test_partial_response_is_not_cached_and_recomputes(tmp_path: Path, monkeypatch: Any) -> None:
    project = _project(tmp_path)
    payload = _payload_for(project)
    server = _FakeServer()

    calls = {"count": 0}

    def _fake_serve(
        _session_id: str, _request: dict[str, Any], _path: str, *, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls["count"] += 1
        return {
            "session_id": "session-x",
            "routing_reason": "session-agent",
            "partial": True,
            "partial_reason": "deadline",
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", _fake_serve)

    request = {"command": "agent", "query": "helper", "path": str(project)}
    first, _first_status = session_daemon._serve_daemon_response_with_cache(
        server=server,
        command="agent",
        session_id="session-x",
        path=str(project),
        request=request,
        payload=payload,
    )
    second, _second_status = session_daemon._serve_daemon_response_with_cache(
        server=server,
        command="agent",
        session_id="session-x",
        path=str(project),
        request=request,
        payload=payload,
    )

    assert first.get("partial") is True
    assert second.get("partial") is True
    assert calls["count"] == 2, "a partial response must force a recompute, never a cache hit"
    assert server.response_cache.entry_count == 0, "a partial response must never be cached"


def test_non_partial_response_is_still_cached(tmp_path: Path, monkeypatch: Any) -> None:
    """Companion golden-parity case: the exclusion is SPECIFIC to partial=True, not a general
    cache regression -- a normal complete response is cached exactly as before."""
    project = _project(tmp_path)
    payload = _payload_for(project)
    server = _FakeServer()

    calls = {"count": 0}

    def _fake_serve(
        _session_id: str, _request: dict[str, Any], _path: str, *, payload: dict[str, Any]
    ) -> dict[str, Any]:
        calls["count"] += 1
        return {"session_id": "session-x", "routing_reason": "session-agent"}

    monkeypatch.setattr(session_daemon, "serve_session_request", _fake_serve)

    request = {"command": "agent", "query": "helper", "path": str(project)}
    session_daemon._serve_daemon_response_with_cache(
        server=server,
        command="agent",
        session_id="session-x",
        path=str(project),
        request=request,
        payload=payload,
    )
    _second, second_status = session_daemon._serve_daemon_response_with_cache(
        server=server,
        command="agent",
        session_id="session-x",
        path=str(project),
        request=request,
        payload=payload,
    )

    assert calls["count"] == 1, "a normal response should be served from cache on the 2nd call"
    assert second_status == "hit"
    assert server.response_cache.entry_count == 1


# ------------------------------------------------------------------------------------------
# orient_capsule.build_orient_capsule_from_map: direct unit coverage of the NEW deadline_monotonic
# parameter. This builder did NOT already accept one (verified against the real code -- unlike
# its 3 siblings, which already did per #639/#642/#645) so it had to be extended as part of #200.
# ------------------------------------------------------------------------------------------


def test_orient_from_map_deadline_none_is_byte_identical_to_omitted(tmp_path: Path) -> None:
    """Golden-parity guard: deadline_monotonic=None (the default, and every pre-#200 call site,
    including the cold `build_orient_capsule` wrapper) must be a no-op -- required by
    test_orient_agent_daemon.py's existing warm==cold byte-identity contract, which never passes
    this kwarg at all."""
    project = _project(tmp_path)
    rm = repo_map.build_repo_map(str(project), max_repo_files=2000)
    without_kwarg = orient_capsule.build_orient_capsule_from_map(rm)
    with_none = orient_capsule.build_orient_capsule_from_map(rm, deadline_monotonic=None)
    assert without_kwarg == with_none


def test_orient_from_map_already_expired_deadline_marks_partial(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rm = repo_map.build_repo_map(str(project), max_repo_files=2000)
    result = orient_capsule.build_orient_capsule_from_map(
        rm, deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result.get("partial_reason") == "deadline"


def test_orient_snippet_loop_breaks_mid_loop_not_just_pre_check(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Strengthens the already-expired-deadline coverage above with the sharper claim (Opus-gate
    nit, PR #647): the deadline crosses WHILE the snippet loop is running, not merely pre-expired
    before the first iteration -- so only SOME central files get a snippet and the loop
    demonstrably stopped early rather than exhausting all of them. Mirrors
    test_repo_map_deadline.py's proven ...breaks_mid_loop_not_just_pre_check technique: a STATIC
    fake clock, manually advanced only inside the wrapped per-item work function
    (_ast_chunked_snippet), so the result is immune to any OTHER incidental time.monotonic() call
    elsewhere in the function (a `lambda: clock["t"]` read-only patch has no side effect on its
    own -- only the wrapper's explicit `clock["t"] += 1.0` moves the clock)."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    for index in range(6):
        (src / f"m{index:03d}.py").write_text(
            f"def helper_{index}():\n    return {index}\n", encoding="utf-8"
        )
    rm = repo_map.build_repo_map(str(project.resolve()), max_repo_files=2000)

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(orient_capsule.time, "monotonic", lambda: clock["t"])
    original_snippet = orient_capsule._ast_chunked_snippet
    call_count = {"n": 0}

    def _advancing_snippet(path_str: str, symbols: list[Any]) -> str | None:
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_snippet(path_str, symbols)

    monkeypatch.setattr(orient_capsule, "_ast_chunked_snippet", _advancing_snippet)

    result = orient_capsule.build_orient_capsule_from_map(
        rm, max_central_files=6, max_snippet_files=6, deadline_monotonic=base + 3.0
    )

    assert result.get("partial") is True
    assert result.get("partial_reason") == "deadline"
    assert 0 < call_count["n"] < 6, "cut short mid-loop, not exhausted"
    assert len(result["snippets"]) < 6, "partial snippet set, not the full 6"


def test_orient_cold_wrapper_stays_unbounded_by_default(tmp_path: Path) -> None:
    """The cold CLI path (`tg orient`, no --deadline) is a deliberate product decision to stay
    unbounded (see orient_capsule.build_orient_capsule's docstring + the CLI --no-deadline help
    text) -- #200 only bounds the WARM daemon dispatch path, so the cold wrapper must never gain
    a spurious partial=True."""
    project = _project(tmp_path)
    cold = orient_capsule.build_orient_capsule(str(project), max_repo_files=2000)
    assert cold.get("partial") is not True
