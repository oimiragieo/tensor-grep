from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tensor_grep.cli import session_store


def _project(tmp_path: Path, *, sibling: bool = True) -> tuple[Path, Path]:
    root, scope = tmp_path / "project", tmp_path / "project" / "src"
    scope.mkdir(parents=True)
    (root / ".git").mkdir()
    (scope / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    if sibling:
        (root / "other").mkdir()
        (root / "other" / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    return root.resolve(), scope.resolve()


def _assert_scope(session_id: str, root: Path, scope: Path) -> dict:
    payload = session_store.get_session(session_id, str(root))
    assert payload["root"] == str(root)
    assert payload["repo_map"]["path"] == str(scope)
    assert payload["repo_map"]["files"]
    assert all(Path(path).is_relative_to(scope) for path in payload["repo_map"]["files"])
    assert all(Path(item["path"]).is_relative_to(scope) for item in payload["snapshot"])
    return payload


def test_subtree_context_ignores_siblings_and_refreshes_real_additions(tmp_path: Path) -> None:
    root, scope = _project(tmp_path)
    opened = session_store.open_session(str(scope))
    for _ in range(2):
        answer = session_store.session_context(
            opened.session_id, "alpha", str(root), refresh_on_stale=True
        )
        assert answer["session_id"] == opened.session_id
    payload = _assert_scope(opened.session_id, root, scope)
    assert not session_store._changeset_has_entries(session_store._stale_changeset(payload))
    added = scope / "new.py"
    added.write_text("def gamma():\n    return 3\n", encoding="utf-8")
    assert session_store._stale_changeset(payload)["added"] == [str(added)]
    for _ in range(2):
        session_store.session_context(opened.session_id, "gamma", str(root), refresh_on_stale=True)
    refreshed = _assert_scope(opened.session_id, root, scope)
    assert str(added) in refreshed["repo_map"]["files"]
    assert any(symbol["name"] == "gamma" for symbol in refreshed["repo_map"]["symbols"])
    assert not session_store._changeset_has_entries(session_store._stale_changeset(refreshed))


@pytest.mark.parametrize("incremental_failure", [False, True])
def test_full_refresh_keeps_original_scope(
    tmp_path: Path, monkeypatch, incremental_failure
) -> None:
    root, scope = _project(tmp_path, sibling=False)
    opened = session_store.open_session(str(scope))
    if incremental_failure:
        (scope / "new.py").write_text("value = 2\n", encoding="utf-8")

        def fail_incremental(*args, **kwargs):
            raise RuntimeError("controlled incremental builder failure")

        monkeypatch.setattr(session_store, "build_repo_map_incremental", fail_incremental)
    else:
        # Select the explicit full-build branch; ordinary reconciliation returns an empty dict.
        monkeypatch.setattr(session_store, "_stale_changeset", lambda *args, **kwargs: None)
    result = session_store.refresh_session(opened.session_id, str(root))
    assert result.refresh_type == "full"
    assert result.refresh_fallback_reason == ("incremental_failed" if incremental_failure else None)
    _assert_scope(opened.session_id, root, scope)


def test_legacy_missing_scope_incremental_uses_root_not_cwd(tmp_path: Path, monkeypatch) -> None:
    root, _ = _project(tmp_path)
    opened = session_store.open_session(str(root))
    payload = session_store.get_session(opened.session_id, str(root))
    del payload["repo_map"]["path"]
    session_store.seal_session(payload, root)
    session_store._write_json_atomic(
        session_store._session_payload_path(root, opened.session_id), payload
    )
    original = copy.deepcopy(payload)
    added = root / "new.py"
    added.write_text("def gamma():\n    return 3\n", encoding="utf-8")
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    (unrelated / "unrelated.py").write_text("def unrelated(): pass\n", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    targets = []
    real_incremental = session_store.build_repo_map_incremental

    def capture(previous, changeset, **kwargs):
        targets.append(previous.get("path"))
        assert changeset["added"] == [str(added)]
        assert previous is not payload["repo_map"]
        return real_incremental(previous, changeset, **kwargs)

    monkeypatch.setattr(session_store, "get_session", lambda *args: payload)
    monkeypatch.setattr(session_store, "build_repo_map_incremental", capture)
    refreshed = session_store.refresh_session(opened.session_id, str(root))
    assert refreshed.refresh_type == "incremental"
    assert refreshed.refresh_fallback_reason is None
    assert targets == [str(root)]
    assert payload == original
    assert session_store.verified_session(payload, root)
    monkeypatch.undo()
    current = _assert_scope(opened.session_id, root, root)
    assert str(added) in current["repo_map"]["files"]
    assert not any("unrelated.py" in path for path in current["repo_map"]["files"])


def test_root_scope_detects_sibling_additions(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    opened = session_store.open_session(str(root))
    added = root / "other" / "new.py"
    added.write_text("value = 2\n", encoding="utf-8")
    refreshed = session_store.refresh_session(opened.session_id, str(root))
    assert refreshed.changeset["added"] == [str(added)]
    assert str(added) in _assert_scope(opened.session_id, root, root)["repo_map"]["files"]


@pytest.mark.parametrize("recorded", ["", "src", None, 42, "missing", "outside"])
def test_invalid_recorded_scope_refuses_before_walk_or_build(
    tmp_path: Path, monkeypatch, recorded
) -> None:
    root, scope = _project(tmp_path)
    opened = session_store.open_session(str(scope))
    payload = session_store.get_session(opened.session_id, str(root))
    payload["repo_map"]["path"] = (
        str(root / "missing")
        if recorded == "missing"
        else str(tmp_path / "outside")
        if recorded == "outside"
        else recorded
    )
    session_store.seal_session(payload, root)
    monkeypatch.setattr(session_store, "get_session", lambda *args: payload)

    def unexpected(*args, **kwargs):
        pytest.fail("invalid scope reached a scan or publication")

    for name in (
        "_iter_repo_files",
        "build_repo_map",
        "build_repo_map_incremental",
        "_write_json_atomic",
    ):
        monkeypatch.setattr(session_store, name, unexpected)
    with pytest.raises((ValueError, FileNotFoundError), match=r"scope|Path not found"):
        session_store.refresh_session(opened.session_id, str(root))


def test_unverified_recovery_ignores_old_scope_and_limit(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    opened = session_store.open_session(str(root))
    payload = session_store.get_session(opened.session_id, str(root))
    payload["repo_map"]["path"] = "invalid-relative-scope"
    payload["scan_limit"] = {"max_repo_files": 1}
    payload.pop("session_provenance")
    session_store._write_json_atomic(
        session_store._session_payload_path(root, opened.session_id), payload
    )
    result = session_store.refresh_session(opened.session_id, str(root))
    assert result.refresh_fallback_reason == "unverified_session_provenance"
    assert result.file_count == 2
    _assert_scope(opened.session_id, root, root)


def test_warm_reconciliation_does_not_walk_added_files(tmp_path: Path, monkeypatch) -> None:
    root, scope = _project(tmp_path)
    opened = session_store.open_session(str(scope))
    payload = session_store.get_session(opened.session_id, str(root))

    def unexpected(*args, **kwargs):
        pytest.fail("warm reconciliation walked the tree")

    monkeypatch.setattr(session_store, "_iter_repo_files", unexpected)
    assert not session_store._changeset_has_entries(
        session_store._stale_changeset(payload, detect_added_files=False)
    )


def test_missing_recorded_subtree_does_not_refresh_its_parent(tmp_path: Path) -> None:
    root, scope = _project(tmp_path)
    opened = session_store.open_session(str(scope))
    scope.rename(root / "moved")
    with pytest.raises(FileNotFoundError, match="Session scan scope not found"):
        session_store.refresh_session(opened.session_id, str(root))
    assert session_store.get_session(opened.session_id, str(root))["repo_map"]["path"] == str(scope)


def test_recorded_file_scope_remains_a_file(tmp_path: Path) -> None:
    root, scope = _project(tmp_path)
    opened = session_store.open_session(str(scope))
    target = scope / "a.py"
    payload = session_store.get_session(opened.session_id, str(root))
    payload["repo_map"] = session_store.build_repo_map(target)
    session_store.seal_session(payload, root)
    session_store._write_json_atomic(
        session_store._session_payload_path(root, opened.session_id), payload
    )
    session_store.refresh_session(opened.session_id, str(root))
    refreshed = session_store.get_session(opened.session_id, str(root))
    assert refreshed["repo_map"]["path"] == str(target)
    assert refreshed["repo_map"]["files"] == [str(target)]
