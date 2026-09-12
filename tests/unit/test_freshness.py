"""`tg freshness` -- has tg's persisted state drifted from the code?

The load-bearing test here is the one asserting that NO persisted state reports UNRESOLVED
rather than "current". A freshness command that gives a repo it has never indexed a clean
bill of health converts absence of evidence into evidence of currency, which is a worse
failure than not having the command.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.freshness import check_freshness, render_freshness_text


def test_no_persisted_state_is_unresolved_not_current(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    payload = check_freshness(tmp_path)

    assert payload["session_count"] == 0
    assert payload["stale_count"] == 0
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "no_persisted_state"
    assert "not a clean bill of health" in payload["remediation"]

    rendered = render_freshness_text(payload)
    assert "INCOMPLETE RESULT" in rendered


def test_zero_stale_alone_never_reads_as_healthy(tmp_path: Path) -> None:
    """MUTATION CONTROL for the test above, stated as the property a caller relies on:
    `stale_count == 0` is NOT sufficient to conclude "current" -- `result_incomplete` has to
    be consulted too. If the floor were removed, this asserts the exact reading that would
    then become wrong.
    """
    payload = check_freshness(tmp_path)

    healthy = payload["stale_count"] == 0 and not payload.get("result_incomplete")
    assert not healthy, (
        "an unindexed repo must never satisfy the healthy predicate; if it does, "
        "stale_count==0 has become a false all-clear"
    )


def test_an_unreadable_session_index_is_unresolved(tmp_path: Path, monkeypatch) -> None:
    """A check that cannot RUN has not passed. If the session index blows up, freshness is
    UNRESOLVED -- never silently reported as zero stale sessions.
    """
    import tensor_grep.cli.session_store as session_store

    def _boom(_path: str):
        raise OSError("index unreadable")

    monkeypatch.setattr(session_store, "list_sessions", _boom)

    payload = check_freshness(tmp_path)

    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "session_index_unreadable"
    assert "UNRESOLVED" in payload["remediation"]


def test_a_session_that_cannot_be_read_is_unknown_not_current(tmp_path: Path, monkeypatch) -> None:
    """An individual session tg cannot load must be UNKNOWN. Counting it as `current` would
    let one broken session silently certify the whole tree as fresh.
    """
    import tensor_grep.cli.session_store as session_store

    class _Record:
        session_id = "session-broken"

    monkeypatch.setattr(session_store, "list_sessions", lambda _path: [_Record()])

    def _boom(_session_id: str, _path: str):
        raise OSError("payload gone")

    monkeypatch.setattr(session_store, "get_session", _boom)

    payload = check_freshness(tmp_path)

    assert payload["session_count"] == 1
    assert payload["unknown_count"] == 1
    assert payload["stale_count"] == 0
    assert payload["sessions"][0]["status"] == "unknown"
    # Crucially: an unknown session does NOT let the tree report clean.
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "session_state_unreadable"


def test_a_current_session_reports_current(tmp_path: Path, monkeypatch) -> None:
    """The positive arm. Without it, every test above would pass against an implementation
    that simply never returns "current" for anything.
    """
    import tensor_grep.cli.session_store as session_store

    class _Record:
        session_id = "session-ok"

    monkeypatch.setattr(session_store, "list_sessions", lambda _path: [_Record()])
    monkeypatch.setattr(session_store, "get_session", lambda _s, _p: {"snapshot": []})
    monkeypatch.setattr(
        session_store, "_ensure_session_not_stale", lambda _payload, **_kwargs: None
    )

    payload = check_freshness(tmp_path)

    assert payload["session_count"] == 1
    assert payload["stale_count"] == 0
    assert payload["unknown_count"] == 0
    assert payload["sessions"][0]["status"] == "current"
    assert not payload.get("result_incomplete"), (
        "a repo with one genuinely current session IS a clean bill of health"
    )


def test_a_stale_session_is_counted_and_explained(tmp_path: Path, monkeypatch) -> None:
    import tensor_grep.cli.session_store as session_store

    class _Record:
        session_id = "session-stale"

    def _stale(_payload, **_kwargs):
        raise session_store.SessionStaleError("cached session files changed on disk")

    monkeypatch.setattr(session_store, "list_sessions", lambda _path: [_Record()])
    monkeypatch.setattr(session_store, "get_session", lambda _s, _p: {"snapshot": []})
    monkeypatch.setattr(session_store, "_ensure_session_not_stale", _stale)

    payload = check_freshness(tmp_path)

    assert payload["stale_count"] == 1
    assert payload["sessions"][0]["status"] == "stale"
    # The reason is carried through, not flattened to a bare boolean -- an agent that is told
    # "stale" without being told what moved cannot decide whether it matters.
    assert "changed on disk" in payload["sessions"][0]["detail"]
    assert "changed on disk" in render_freshness_text(payload)
