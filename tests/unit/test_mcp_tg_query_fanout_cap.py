"""Audit C3 (MCP fan-out amplifier): `tg_query`'s `workspace_roots` param had no cap on the
number of roots, and its per-root dispatch loop handed the SAME full `deadline` to every root
with no shared wall-clock budget -- `tg_query(action="find", deadline=60,
workspace_roots=[r1..r20])` could run up to 20x60=1200s from a single MCP call instead of the
documented "wall-clock budget in seconds" bounding the WHOLE call.

Covers, RED-then-GREEN against `tensor_grep/cli/mcp_server.py::tg_query`:
  1. workspace_roots over the `_MAX_WORKSPACE_ROOTS` cap -> fail-closed structured error,
     never a crash and never a silent truncation to the cap.
  2. the shared-deadline behavior -- once the shared wall-clock budget is exhausted, the
     remaining roots are OMITTED (never dispatched) and reported via `omitted_roots` +
     `partial: true`, instead of each root getting its own fresh copy of `deadline`.
  3. a normal multi-root call with an ample deadline still dispatches every root and returns
     the pre-existing response shape unchanged (no `omitted_roots`/`partial` noise).

Sibling file: test_mcp_server.py (see test_tg_query_workspace_roots_dispatches_once_per_root /
test_tg_query_workspace_roots_one_bad_element_fails_whole_call for the pre-existing
confinement-only coverage this file must not regress).
"""

import json
import time
from unittest.mock import MagicMock


def test_tg_query_workspace_roots_over_cap_fails_closed(monkeypatch, tmp_path):
    """More than _MAX_WORKSPACE_ROOTS roots (all otherwise valid, in-root paths) must be
    refused fail-closed BEFORE any root is dispatched -- never a crash, never a silent
    truncation to the first N roots."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    cap = mcp_server._MAX_WORKSPACE_ROOTS
    roots = []
    for i in range(cap + 1):
        name = f"root_{i}"
        (tmp_path / name).mkdir()
        roots.append(name)

    spy = MagicMock(return_value="{}")
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    out = mcp_server.tg_query(action="text", pattern="foo", workspace_roots=roots)
    payload = json.loads(out)

    assert payload["error"]["code"] == "invalid_input"
    assert str(cap) in payload["error"]["message"]
    assert "results_by_root" not in payload
    spy.assert_not_called()  # fail-closed BEFORE any root is queried


def test_tg_query_workspace_roots_at_cap_is_not_rejected(monkeypatch, tmp_path):
    """Exactly _MAX_WORKSPACE_ROOTS roots is the boundary-legal case -- must NOT be rejected
    (the cap is a limit, not an off-by-one trap)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    cap = mcp_server._MAX_WORKSPACE_ROOTS
    roots = []
    for i in range(cap):
        name = f"root_{i}"
        (tmp_path / name).mkdir()
        roots.append(name)

    spy = MagicMock(side_effect=lambda **kwargs: json.dumps({"path": kwargs["path"]}))
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    out = mcp_server.tg_query(action="text", pattern="foo", workspace_roots=roots)
    payload = json.loads(out)

    assert "error" not in payload
    assert spy.call_count == cap
    assert len(payload["results_by_root"]) == cap


def test_tg_query_workspace_roots_shared_deadline_omits_remaining_roots(monkeypatch, tmp_path):
    """The `deadline` must bound the WHOLE multi-root call as ONE shared wall-clock budget,
    not be handed unchanged to every root. Simulates a slow first root (via a monkeypatched
    clock that jumps forward inside the dispatch spy) that alone consumes the entire shared
    deadline; the remaining roots must be skipped (never dispatched) and reported via
    `omitted_roots` + `partial: true` rather than each getting a fresh full deadline."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_c = tmp_path / "root_c"
    for root in (root_a, root_b, root_c):
        root.mkdir()

    clock = {"now": 0.0}
    # Patch the actual stdlib `time` module (not an mcp_server-local alias) -- mcp_server.py's
    # own `import time` binds the SAME module object, so this is visible there too, and the
    # test exercises the real loop against unfixed code instead of erroring on setup.
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    def slow_dispatch(*args, **kwargs):
        # Simulate the first root's search alone consuming the entire shared deadline
        # budget (far more than the 10s `deadline` below).
        clock["now"] += 100.0
        return json.dumps({"path": kwargs["path"]})

    spy = MagicMock(side_effect=slow_dispatch)
    monkeypatch.setattr(mcp_server, "_tg_query_dispatch", spy)

    out = mcp_server.tg_query(
        action="find",
        query="x",
        deadline=10,
        workspace_roots=["root_a", "root_b", "root_c"],
    )
    payload = json.loads(out)

    # Only the first root was actually dispatched -- the shared budget was gone before the
    # loop reached root_b/root_c.
    assert spy.call_count == 1
    assert payload["partial"] is True
    assert len(payload["omitted_roots"]) == 2
    assert set(payload["results_by_root"]) == {str(root_a.resolve())}
    # The un-run roots are explicitly named, never silently dropped.
    assert str(root_b.resolve()) in payload["omitted_roots"]
    assert str(root_c.resolve()) in payload["omitted_roots"]


def test_tg_query_workspace_roots_deadline_not_multiplied_per_root(monkeypatch, tmp_path):
    """Direct regression test for the audit-C3 amplifier shape: each dispatched root must
    receive the REMAINING shared budget, not a fresh copy of the original `deadline` value.
    The second root's forwarded deadline must be smaller than the first root's."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    clock = {"now": 0.0}
    # Patch the actual stdlib `time` module (not an mcp_server-local alias) -- mcp_server.py's
    # own `import time` binds the SAME module object, so this is visible there too, and the
    # test exercises the real loop against unfixed code instead of erroring on setup.
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    def dispatch_and_advance(*args, **kwargs):
        clock["now"] += 20.0  # each root "takes" 20s of wall-clock time
        return json.dumps({"path": kwargs["path"]})

    spy = MagicMock(side_effect=dispatch_and_advance)
    monkeypatch.setattr(mcp_server, "_tg_query_dispatch", spy)

    mcp_server.tg_query(
        action="find",
        query="x",
        deadline=60,
        workspace_roots=["root_a", "root_b"],
    )

    assert spy.call_count == 2
    first_deadline = spy.call_args_list[0].kwargs["deadline"]
    second_deadline = spy.call_args_list[1].kwargs["deadline"]
    # Root 1 gets ~the full 60s budget; root 2 must get strictly less (the remaining ~40s),
    # never another fresh 60s -- this is the exact amplifier the audit flagged.
    assert first_deadline > second_deadline
    assert second_deadline <= 40.0 + 1e-6


def test_tg_query_workspace_roots_ample_deadline_returns_all_roots_unchanged(monkeypatch, tmp_path):
    """A normal multi-root call whose deadline is never actually exhausted must dispatch every
    root and preserve the EXISTING response shape exactly -- no `omitted_roots`/`partial` keys
    at all when nothing was omitted (schema-additive, not schema-noisy)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    spy = MagicMock(side_effect=lambda **kwargs: json.dumps({"path": kwargs["path"]}))
    monkeypatch.setattr(mcp_server, "tg_find", spy)

    out = mcp_server.tg_query(
        action="find",
        query="foo",
        deadline=9999,
        workspace_roots=["root_a", "root_b"],
    )
    payload = json.loads(out)

    assert spy.call_count == 2
    assert "omitted_roots" not in payload
    assert "partial" not in payload
    called_paths = {call.kwargs["path"] for call in spy.call_args_list}
    assert called_paths == {str(root_a.resolve()), str(root_b.resolve())}
    assert set(payload["results_by_root"]) == called_paths
    # First root should be handed close to the full budget (only a tiny slice elapsed).
    assert spy.call_args_list[0].kwargs["deadline"] > 9990


def test_tg_query_workspace_roots_no_deadline_unchanged_behavior(monkeypatch, tmp_path):
    """When no `deadline` is supplied at all (the pre-existing default), behavior must be
    byte-for-byte unchanged: every root dispatched with `deadline=None`, no omitted_roots."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    spy = MagicMock(side_effect=lambda **kwargs: json.dumps({"path": kwargs["path"]}))
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    out = mcp_server.tg_query(action="text", pattern="foo", workspace_roots=["root_a", "root_b"])
    payload = json.loads(out)

    assert spy.call_count == 2
    assert "omitted_roots" not in payload
    assert "partial" not in payload
    for call in spy.call_args_list:
        assert call.kwargs.get("deadline") is None
