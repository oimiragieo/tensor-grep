"""A session's persisted generation must represent the bytes actually used by its map."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic

import pytest

from tensor_grep.cli import session_content_reconciliation, session_store


def _session(root: Path) -> tuple[Path, str, dict]:
    source = root / "a.py"
    source.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    opened = session_store.open_session(str(root))
    return source, opened.session_id, session_store.get_session(opened.session_id, str(root))


def _rewrite_same_metadata(path: Path) -> None:
    info = path.stat()
    path.write_text(path.read_text().replace("alpha", "bravo"), encoding="utf-8")
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert path.stat().st_size == info.st_size
    assert path.stat().st_mtime_ns == info.st_mtime_ns


def test_warm_retrieval_refuses_same_mtime_edit_and_refresh_recovers(tmp_path: Path) -> None:
    source, session_id, payload = _session(tmp_path)
    assert len(payload["snapshot"][0]["content_sha256"]) == 64
    fresh = session_store.serve_session_request(session_id, {"command": "repo_map"}, str(tmp_path))
    assert fresh["symbols"][0]["name"] == "alpha"
    _rewrite_same_metadata(source)
    with pytest.raises(session_store.SessionStaleError, match="modified"):
        session_store.serve_session_request(session_id, {"command": "repo_map"}, str(tmp_path))
    refreshed = session_store.refresh_session(session_id, str(tmp_path))
    assert refreshed.changeset["modified"] == [str(source)]
    current = session_store.serve_session_request(
        session_id, {"command": "repo_map"}, str(tmp_path)
    )
    assert current["symbols"][0]["name"] == "bravo"


def test_legacy_metadata_snapshot_requires_refresh_before_reuse(tmp_path: Path) -> None:
    source, session_id, payload = _session(tmp_path)
    for snapshot in payload["snapshot"]:
        snapshot.pop("content_sha256")
    with pytest.raises(session_store.SessionStaleError, match="modified"):
        session_store._ensure_session_not_stale(payload)
    changes = session_store._stale_changeset(payload, detect_added_files=False)
    assert changes["modified"] == [str(source)]
    assert session_store.get_session(session_id, str(tmp_path))["snapshot"][0]["content_sha256"]


def test_uncertain_content_read_is_not_a_deletion_or_fresh_session(
    tmp_path: Path, monkeypatch
) -> None:
    source, _, payload = _session(tmp_path)
    actual_digest = session_store.content_digest

    def denied(*args, **kwargs):
        raise PermissionError("source content unavailable")

    monkeypatch.setattr(session_store, "content_digest", denied)
    changes = session_store._stale_changeset(payload, detect_added_files=False)
    assert changes["removed"] == []
    assert changes["unverified"] == [str(source)]
    with pytest.raises(session_store.SessionStaleError, match="unverified"):
        session_store._ensure_session_not_stale(payload)
    monkeypatch.setattr(session_store, "content_digest", actual_digest)
    session_store._ensure_session_not_stale(payload)


def test_capture_race_cannot_stamp_changed_bytes_as_the_old_maps_generation(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "a.py"
    path.write_text("def alpha(): pass\n")
    capture = session_store._capture_snapshot

    def racing_capture(*args, **kwargs):
        path.write_text("def bravo(): pass\n")
        return capture(*args, **kwargs)

    monkeypatch.setattr(session_store, "_capture_snapshot", racing_capture)
    opened = session_store.open_session(str(tmp_path))
    payload = session_store.get_session(opened.session_id, str(tmp_path))
    assert payload["repo_map"]["symbols"][0]["name"] == "alpha"
    assert payload["snapshot"] == []
    assert payload["snapshot_unreadable_paths"]["count"] == 1
    with pytest.raises(session_store.SessionStaleError, match="incomplete"):
        session_store._ensure_session_not_stale(payload)


def test_stale_reconciliation_respects_exhausted_shared_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    source, _, payload = _session(tmp_path)
    monkeypatch.setattr(
        session_store, "content_digest", lambda *a, **k: pytest.fail("read after deadline")
    )
    changes = session_store._stale_changeset(
        payload,
        detect_added_files=False,
        deadline_monotonic=monotonic() - 1,
    )
    assert changes["unverified"] == [str(source)]
    with pytest.raises(session_store.SessionStaleError, match="unverified"):
        session_store._ensure_session_not_stale(payload, deadline_monotonic=monotonic() - 1)


def test_snapshot_source_cap_and_confinement_have_readable_positive_control(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def alpha(): pass\n")
    with pytest.raises(OSError, match="exceeds"):
        session_content_reconciliation.content_digest(tmp_path, source, 1)
    with pytest.raises(OSError, match="outside captured root"):
        session_content_reconciliation.content_digest(tmp_path / "other", source, 100)
    assert len(session_content_reconciliation.content_digest(tmp_path, source, 100)) == 64


def test_default_scope_still_requires_explicit_added_file_discovery(tmp_path: Path) -> None:
    _, _, payload = _session(tmp_path)
    extra = tmp_path / "new.py"
    extra.write_text("def other(): pass\n")
    session_store._ensure_session_not_stale(payload)
    with pytest.raises(session_store.SessionStaleError, match="added"):
        session_store._ensure_session_not_stale(payload, detect_added_files=True)


def test_stream_response_cache_detects_same_mtime_change(tmp_path: Path) -> None:
    from io import StringIO

    source, session_id, _ = _session(tmp_path)
    request = json.dumps({"command": "context_render", "query": "alpha"}) + "\n"

    class _Requests:
        def __iter__(self):
            yield request
            _rewrite_same_metadata(source)
            yield request

    output = StringIO()
    session_store.serve_session_stream(
        session_id, str(tmp_path), input_stream=_Requests(), output_stream=output
    )
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["serve_response_cache"]["status"] == "miss"
    assert replies[1]["error"]["code"] == "stale_session"


def test_empty_session_expired_inventory_walk_is_unverified(tmp_path: Path) -> None:
    opened = session_store.open_session(str(tmp_path))
    payload = session_store.get_session(opened.session_id, str(tmp_path))
    assert payload["snapshot"] == []
    source = tmp_path / "new.py"
    source.write_text("def newly_added(): pass\n")
    expired = session_store._stale_changeset(
        payload,
        detect_added_files=True,
        deadline_monotonic=monotonic() - 1,
    )
    assert expired["unverified"] == [str(tmp_path)]
    with pytest.raises(session_store.SessionStaleError, match="unverified"):
        session_store._ensure_session_not_stale(
            payload,
            detect_added_files=True,
            deadline_monotonic=monotonic() - 1,
        )
    complete = session_store._stale_changeset(payload, detect_added_files=True)
    assert complete["added"] == [str(source)]
    assert "unverified" not in complete


def test_stream_cache_gate_and_builder_share_one_deadline(tmp_path: Path, monkeypatch) -> None:
    from io import StringIO

    _, session_id, _ = _session(tmp_path)
    clock = {"now": 1000.0}
    checked: list[float] = []
    built: list[float] = []

    def verify(payload, *, deadline_monotonic=None, **kwargs):
        checked.append(deadline_monotonic)
        clock["now"] += 5

    def render(repo_map, query, *, deadline_monotonic=None, **kwargs):
        built.append(deadline_monotonic)
        return {"rendered": "verified context"}

    monkeypatch.setattr(session_store, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_store, "_ensure_session_not_stale", verify)
    monkeypatch.setattr(session_store, "build_context_render_from_map", render)
    session_store.serve_session_stream(
        session_id,
        str(tmp_path),
        input_stream=StringIO('{"command":"context_render","query":"alpha"}\n'),
        output_stream=StringIO(),
    )
    expected = 1000 + session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
    assert checked == [expected, expected]
    assert built == [expected]


def test_daemon_cache_gate_and_builder_share_one_deadline(tmp_path: Path, monkeypatch) -> None:
    import threading
    from types import SimpleNamespace

    from tensor_grep.cli import session_daemon

    _, session_id, payload = _session(tmp_path)
    clock = {"now": 1000.0}
    checked: list[float] = []
    built: list[float] = []

    def verify(payload, *, deadline_monotonic=None, **kwargs):
        checked.append(deadline_monotonic)
        clock["now"] += 5

    def render(repo_map, query, *, deadline_monotonic=None, **kwargs):
        built.append(deadline_monotonic)
        return {"rendered": "verified context"}

    monkeypatch.setattr(session_store, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_daemon, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_store, "_ensure_session_not_stale", verify)
    monkeypatch.setattr(session_daemon, "_ensure_session_not_stale", verify)
    monkeypatch.setattr(session_store, "build_context_render_from_map", render)
    server = SimpleNamespace(
        _response_cache_lock=threading.Lock(),
        response_cache=session_store._SessionServeResponseCache(),
    )
    _, status = session_daemon._serve_daemon_response_with_cache(
        server=server,
        command="context_render",
        session_id=session_id,
        path=str(tmp_path),
        request={"command": "context_render", "query": "alpha"},
        payload=payload,
    )
    expected = 1000 + session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
    assert status == "miss"
    assert checked == [expected, expected]
    assert built == [expected]


def test_capture_exhausted_deadline_skips_stat_and_counts_omissions(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli.repo_map import _UnreadablePathFlag

    path = tmp_path / "a.py"
    path.write_text("def alpha(): pass\n")
    stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda *a, **k: pytest.fail("stat after exhausted deadline"))
    flag = _UnreadablePathFlag()
    assert (
        session_store._capture_snapshot(
            [str(path), str(path)],
            unreadable_hit=flag,
            deadline_monotonic=monotonic() - 1,
        )
        == []
    )
    assert flag.count == 2
    monkeypatch.setattr(Path, "stat", stat)
    assert len(session_store._capture_snapshot([str(path)])) == 1


def test_forged_persisted_session_map_is_refused_and_explicit_refresh_regenerates(
    tmp_path: Path,
) -> None:
    _, session_id, payload = _session(tmp_path)
    payload["repo_map"]["symbols"][0]["name"] = "forged_target"
    location = session_store._session_payload_path(tmp_path, session_id)
    location.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(session_store.SessionStaleError, match=r"authentication.*refresh"):
        session_store.get_session(session_id, str(tmp_path))
    refreshed = session_store.refresh_session(session_id, str(tmp_path))
    assert refreshed.refresh_fallback_reason == "unverified_session_provenance"
    trusted = session_store.get_session(session_id, str(tmp_path))
    assert trusted["repo_map"]["symbols"][0]["name"] == "alpha"
    assert trusted["session_provenance"]["scheme"] == "machine-hmac-sha256-v1"


def test_unsigned_legacy_session_requires_explicit_refresh(tmp_path: Path) -> None:
    _, session_id, payload = _session(tmp_path)
    payload.pop("session_provenance")
    session_store._session_payload_path(tmp_path, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(session_store.SessionStaleError, match="unsigned legacy"):
        session_store.get_session(session_id, str(tmp_path))
    session_store.refresh_session(session_id, str(tmp_path))
    assert session_store.get_session(session_id, str(tmp_path))["session_provenance"]["tag"]


def test_refresh_of_forged_session_cannot_expand_into_an_unrelated_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _, session_id, payload = _session(root)
    (outside / "foreign.py").write_text("def foreign_target(): pass\n")
    payload["repo_map"]["path"] = str(outside)
    payload["scan_limit"] = {"max_repo_files": 999999}
    session_store._session_payload_path(root, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    session_store.refresh_session(session_id, str(root))
    trusted = session_store.get_session(session_id, str(root))
    assert {s["name"] for s in trusted["repo_map"]["symbols"]} == {"alpha"}
    assert trusted["scan_limit"]["max_repo_files"] == session_store.DEFAULT_AGENT_REPO_MAP_LIMIT


def test_unavailable_machine_key_refuses_persisted_session_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_provenance

    _, session_id, _ = _session(tmp_path)
    monkeypatch.setattr(session_provenance, "load_machine_key", lambda root: None)
    with pytest.raises(session_store.SessionStaleError, match=r"authentication.*unavailable"):
        session_store.get_session(session_id, str(tmp_path))


@pytest.mark.parametrize("raw", ["[]", "[" * 2000 + "]" * 2000])
def test_invalid_session_json_refuses_reuse_and_refresh_recovers(tmp_path: Path, raw: str) -> None:
    _, session_id, _ = _session(tmp_path)
    location = session_store._session_payload_path(tmp_path, session_id)
    location.write_text(raw, encoding="utf-8")
    with pytest.raises(session_store.SessionStaleError, match=r"session payload.*refresh"):
        session_store.get_session(session_id, str(tmp_path))
    session_store.refresh_session(session_id, str(tmp_path))
    assert (
        session_store.get_session(session_id, str(tmp_path))["repo_map"]["symbols"][0]["name"]
        == "alpha"
    )


def test_session_payload_read_cap_rejects_oversize_before_json_decode(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_provenance

    location = tmp_path / "state.json"
    location.write_text('{"valid":true}', encoding="utf-8")
    monkeypatch.setattr(session_provenance, "_PAYLOAD_LIMIT", 32)
    assert session_provenance.read_session_payload(tmp_path, location) == {"valid": True}
    location.write_text(" " * 33, encoding="utf-8")
    monkeypatch.setattr(
        session_provenance.json, "loads", lambda *a: pytest.fail("decoded oversize state")
    )
    with pytest.raises(session_provenance.SessionPayloadError, match="exceeds"):
        session_provenance.read_session_payload(tmp_path, location)


def test_signed_session_payload_cannot_be_reused_under_another_session_identity(
    tmp_path: Path,
) -> None:
    _, session_id, payload = _session(tmp_path)
    alias = "aliased-session"
    location = session_store._session_payload_path(tmp_path, alias)
    location.write_text(json.dumps(payload), encoding="utf-8")
    assert session_store.get_session(session_id, str(tmp_path))["session_id"] == session_id
    with pytest.raises(session_store.SessionStaleError, match="authentication"):
        session_store.get_session(alias, str(tmp_path))


@pytest.mark.parametrize("last_prepare", [1, ["forged"], {"query": "forged decision"}])
def test_untrusted_session_recovery_discards_all_preparation_metadata(
    tmp_path: Path, last_prepare: object
) -> None:
    _, session_id, payload = _session(tmp_path)
    payload["repo_map"]["symbols"][0]["name"] = "forged_target"
    payload["last_prepare"] = last_prepare
    session_store._session_payload_path(tmp_path, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    refreshed = session_store.refresh_session(session_id, str(tmp_path))
    assert refreshed.refresh_fallback_reason == "unverified_session_provenance"
    trusted = session_store.get_session(session_id, str(tmp_path))
    assert "last_prepare" not in trusted
    assert trusted["repo_map"]["symbols"][0]["name"] == "alpha"


def test_trusted_session_refresh_preserves_dictionary_preparation_metadata(tmp_path: Path) -> None:
    from tensor_grep.cli.session_provenance import seal_session

    _, session_id, payload = _session(tmp_path)
    payload["last_prepare"] = {"query": "verified session decision"}
    seal_session(payload, tmp_path)
    session_store._session_payload_path(tmp_path, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert session_store.get_session(session_id, str(tmp_path))["last_prepare"]
    session_store.refresh_session(session_id, str(tmp_path))
    trusted = session_store.get_session(session_id, str(tmp_path))
    assert trusted["last_prepare"]["query"] == "verified session decision"
    assert trusted["last_prepare"]["current_generation"] == trusted["current_generation"]


@pytest.mark.parametrize("last_prepare", [1, ["invalid"]])
def test_trusted_session_refresh_discards_malformed_preparation_metadata(
    tmp_path: Path, last_prepare: object
) -> None:
    from tensor_grep.cli.session_provenance import seal_session

    _, session_id, payload = _session(tmp_path)
    payload["last_prepare"] = last_prepare
    seal_session(payload, tmp_path)
    session_store._session_payload_path(tmp_path, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    session_store.refresh_session(session_id, str(tmp_path))
    assert "last_prepare" not in session_store.get_session(session_id, str(tmp_path))


def test_prepared_decision_receipt_refuses_tampering_before_resume(tmp_path: Path) -> None:
    from tensor_grep.cli.session_resume_service import session_prepare, session_resume

    _, session_id, _ = _session(tmp_path)
    prepared = session_prepare(session_id, "alpha", str(tmp_path))
    resumed = session_resume(session_id, str(tmp_path))
    assert prepared["session_id"] == session_id
    assert resumed["last_prepare"]["query"] == "alpha"
    assert resumed["last_prepare"]["decision_freshness"] == "current"
    payload = session_store.get_session(session_id, str(tmp_path))
    payload["last_prepare"]["query"] = "forged current decision"
    session_store._session_payload_path(tmp_path, session_id).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(session_store.SessionStaleError, match="authentication"):
        session_resume(session_id, str(tmp_path))
    session_store.refresh_session(session_id, str(tmp_path))
    assert session_resume(session_id, str(tmp_path))["last_prepare"] is None


@pytest.mark.parametrize("foreign_root", [False, True])
def test_refresh_does_not_carry_decision_from_another_authenticated_scope(
    tmp_path: Path, monkeypatch, foreign_root: bool
) -> None:
    from tensor_grep.cli.session_provenance import seal_session

    _, session_id, payload = _session(tmp_path)
    other_root = tmp_path / "other" if foreign_root else tmp_path
    other_root.mkdir(exist_ok=True)
    _, other_id, other = _session(other_root)
    if foreign_root:
        other["session_id"] = session_id
    else:
        assert other_id != session_id
    other["last_prepare"] = {"query": "other authenticated scope"}
    seal_session(other, other_root)
    target = session_store._session_payload_path(tmp_path, session_id)
    read = session_store.read_session_payload
    reads = 0

    def substituted(root, path):
        nonlocal reads
        actual = read(root, path)
        if path == target:
            reads += 1
            if reads == 2:
                return other
        return actual

    monkeypatch.setattr(session_store, "read_session_payload", substituted)
    session_store.refresh_session(session_id, str(tmp_path))
    assert reads == 2
    monkeypatch.setattr(session_store, "read_session_payload", read)
    refreshed = session_store.get_session(session_id, str(tmp_path))
    assert "last_prepare" not in refreshed
    assert refreshed["root"] == payload["root"]
    assert refreshed["session_id"] == session_id


def test_incremental_refresh_fallback_does_not_log_exception_payload(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    source, session_id, _ = _session(tmp_path)
    source.write_text(source.read_text().replace("alpha", "bravo"), encoding="utf-8")
    calls = []

    def failed_incremental(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("secret-sentinel")

    monkeypatch.setattr(session_store, "build_repo_map_incremental", failed_incremental)
    refreshed = session_store.refresh_session(session_id, str(tmp_path))
    assert calls == [True]
    assert refreshed.refresh_fallback_reason == "incremental_failed"
    assert "RuntimeError" in caplog.text
    assert "secret-sentinel" not in caplog.text
    trusted = session_store.get_session(session_id, str(tmp_path))
    assert {symbol["name"] for symbol in trusted["repo_map"]["symbols"]} == {"bravo"}
