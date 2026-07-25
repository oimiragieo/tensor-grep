from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map, session_store


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"

    core_path = _write(
        src_dir / "core.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    service_path = _write(
        src_dir / "service.py",
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    helper_path = _write(
        src_dir / "helpers.py",
        "def format_invoice_label(invoice_id):\n    return f'invoice-{invoice_id}'\n",
    )
    test_path = _write(
        tests_dir / "test_service.py",
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
    )

    return {
        "project": project,
        "core": core_path,
        "service": service_path,
        "helper": helper_path,
        "test": test_path,
    }


def _open_session(project: Path) -> str:
    return session_store.open_session(str(project)).session_id


def _session_payload(project: Path, session_id: str) -> dict[str, object]:
    return session_store.get_session(session_id, str(project))


def _changeset_for_session(project: Path, session_id: str) -> dict[str, list[str]]:
    changeset = session_store._stale_changeset(_session_payload(project, session_id))
    assert changeset is not None
    return changeset


def _empty_changeset() -> dict[str, list[str]]:
    return {"added": [], "modified": [], "removed": []}


def test_stale_changeset_returns_empty_lists_for_fresh_session(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])

    assert _changeset_for_session(paths["project"], session_id) == _empty_changeset()


def test_stale_changeset_does_not_resolve_each_absolute_snapshot_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    payload = _session_payload(paths["project"], session_id)
    snapshot_paths = {
        str(entry["path"])
        for entry in payload["snapshot"]
        if isinstance(entry, dict) and Path(str(entry.get("path", ""))).is_absolute()
    }
    original_resolve = Path.resolve

    monkeypatch.setattr(session_store, "_resolve_root", lambda _path: paths["project"])

    def fail_snapshot_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if str(self) in snapshot_paths:
            raise AssertionError("absolute snapshot paths should not be resolved per warm request")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_snapshot_resolve)

    assert session_store._stale_changeset(payload, detect_added_files=False) == _empty_changeset()


def test_stale_changeset_detects_modified_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n    subtotal = total + 1\n    return subtotal\n",
        encoding="utf-8",
    )

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [],
        "modified": [str(paths["core"].resolve())],
        "removed": [],
    }


def test_stale_changeset_detects_added_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    added_path = _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [str(added_path.resolve())],
        "modified": [],
        "removed": [],
    }


def test_stale_changeset_detects_removed_file(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["helper"].unlink()

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [],
        "modified": [],
        "removed": [str(paths["helper"].resolve())],
    }


def test_stale_changeset_detects_added_modified_and_removed_files(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    amount = create_invoice(total)\n"
        "    return amount\n",
        encoding="utf-8",
    )
    added_path = _write(
        paths["project"] / "src" / "api.py",
        "from src.service import build_invoice\n\n"
        "def present_invoice(total):\n"
        "    return build_invoice(total)\n",
    )
    paths["helper"].unlink()

    assert _changeset_for_session(paths["project"], session_id) == {
        "added": [str(added_path.resolve())],
        "modified": [str(paths["service"].resolve())],
        "removed": [str(paths["helper"].resolve())],
    }


def test_build_repo_map_incremental_matches_full_build_for_mixed_changes(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    previous_map = _session_payload(paths["project"], session_id)["repo_map"]
    assert isinstance(previous_map, dict)

    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    amount = create_invoice(total)\n"
        "    return amount + 2\n",
        encoding="utf-8",
    )
    _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    paths["helper"].unlink()

    changeset = _changeset_for_session(paths["project"], session_id)
    incremental_map = repo_map.build_repo_map_incremental(previous_map, changeset)
    full_map = repo_map.build_repo_map(paths["project"])

    assert incremental_map == full_map


def test_build_repo_map_incremental_only_reparses_changed_files(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])

    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total) + 5\n",
        encoding="utf-8",
    )
    added_path = _write(
        paths["project"] / "src" / "billing.py",
        "def issue_invoice(total):\n    return total\n",
    )

    parsed_paths: list[str] = []
    original = repo_map._imports_and_symbols_for_path

    def tracking_parser(path: Path) -> tuple[list[str], list[dict[str, object]]]:
        parsed_paths.append(str(path.resolve()))
        return original(path)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", tracking_parser)

    incremental_map = repo_map.build_repo_map_incremental(
        previous_map,
        {
            "added": [str(added_path.resolve())],
            "modified": [str(paths["service"].resolve())],
            "removed": [],
        },
    )

    assert set(parsed_paths) == {
        str(added_path.resolve()),
        str(paths["service"].resolve()),
    }
    helper_symbols_before = [
        symbol
        for symbol in previous_map["symbols"]
        if symbol["file"] == str(paths["helper"].resolve())
    ]
    helper_symbols_after = [
        symbol
        for symbol in incremental_map["symbols"]
        if symbol["file"] == str(paths["helper"].resolve())
    ]
    assert helper_symbols_after == helper_symbols_before


def test_build_repo_map_incremental_removes_deleted_entries_from_payload(tmp_path: Path) -> None:
    """NOTE for future readers: the pruning asserted here does NOT come from `changeset["removed"]`
    being consumed. The files are unlinked from disk BEFORE the call, so they disappear simply
    because `_iter_repo_files` only yields files that still exist. `build_repo_map_incremental`
    ignores `removed` entirely (its D2 comment says so, and a probe passing a false `removed` for
    files still on disk evicts nothing). Without this note the test reads like a contradiction of
    that fact -- which is exactly the wrong inference task #286 was fixed on.
    """
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])
    paths["helper"].unlink()
    paths["test"].unlink()

    incremental_map = repo_map.build_repo_map_incremental(
        previous_map,
        {
            "added": [],
            "modified": [],
            "removed": [
                str(paths["helper"].resolve()),
                str(paths["test"].resolve()),
            ],
        },
    )

    assert str(paths["helper"].resolve()) not in incremental_map["files"]
    assert str(paths["test"].resolve()) not in incremental_map["tests"]
    assert str(paths["helper"].resolve()) not in incremental_map["related_paths"]
    assert all(
        entry["file"] != str(paths["helper"].resolve()) for entry in incremental_map["imports"]
    )
    assert all(
        symbol["file"] != str(paths["helper"].resolve()) for symbol in incremental_map["symbols"]
    )


def test_incremental_repo_map_matches_full_graph_outputs_after_import_change(
    tmp_path: Path,
) -> None:
    paths = _build_project(tmp_path)
    previous_map = repo_map.build_repo_map(paths["project"])
    api_path = _write(
        paths["project"] / "src" / "api.py",
        "from src.service import build_invoice\n\n"
        "def present_invoice(total):\n"
        "    return build_invoice(total)\n",
    )
    changeset = {"added": [str(api_path.resolve())], "modified": [], "removed": []}

    incremental_map = repo_map.build_repo_map_incremental(previous_map, changeset)
    full_map = repo_map.build_repo_map(paths["project"])

    assert repo_map.build_context_pack_from_map(
        incremental_map, "invoice"
    ) == repo_map.build_context_pack_from_map(
        full_map,
        "invoice",
    )


def test_refresh_session_uses_incremental_builder_when_changeset_available(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n    return total + 2\n",
        encoding="utf-8",
    )

    incremental_calls = {"count": 0}
    full_calls = {"count": 0}
    original_incremental = session_store.build_repo_map_incremental

    def tracking_incremental(
        previous_map: dict[str, object],
        changeset: dict[str, list[str]],
        *,
        max_repo_files: int | None = None,
    ) -> dict[str, object]:
        incremental_calls["count"] += 1
        assert max_repo_files == session_store.DEFAULT_AGENT_REPO_MAP_LIMIT
        return original_incremental(previous_map, changeset, max_repo_files=max_repo_files)

    def unexpected_full_build(
        path: str | Path = ".",
        *,
        max_repo_files: int | None = None,
    ) -> dict[str, object]:
        full_calls["count"] += 1
        return repo_map.build_repo_map(path, max_repo_files=max_repo_files)

    monkeypatch.setattr(session_store, "build_repo_map_incremental", tracking_incremental)
    monkeypatch.setattr(session_store, "build_repo_map", unexpected_full_build)

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))

    assert refreshed.refresh_type == "incremental"
    assert incremental_calls["count"] == 1
    assert full_calls["count"] == 0


def test_refresh_session_falls_back_to_full_rebuild_when_incremental_fails(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n    return total + 3\n",
        encoding="utf-8",
    )

    full_calls = {"count": 0}

    def failing_incremental(
        previous_map: dict[str, object],
        changeset: dict[str, list[str]],
        *,
        max_repo_files: int | None = None,
    ) -> dict[str, object]:
        assert max_repo_files == session_store.DEFAULT_AGENT_REPO_MAP_LIMIT
        raise RuntimeError("boom")

    def tracking_full_build(
        path: str | Path = ".",
        *,
        max_repo_files: int | None = None,
    ) -> dict[str, object]:
        full_calls["count"] += 1
        assert max_repo_files == session_store.DEFAULT_AGENT_REPO_MAP_LIMIT
        return repo_map.build_repo_map(path, max_repo_files=max_repo_files)

    monkeypatch.setattr(session_store, "build_repo_map_incremental", failing_incremental)
    monkeypatch.setattr(session_store, "build_repo_map", tracking_full_build)

    with caplog.at_level(logging.WARNING, logger="tensor_grep.cli.session_store"):
        refreshed = session_store.refresh_session(session_id, str(paths["project"]))

    assert refreshed.refresh_type == "full"
    assert refreshed.refresh_fallback_reason == "incremental_failed"
    assert full_calls["count"] == 1
    assert any(
        "falling back to full rebuild" in record.message and "boom" in record.message
        for record in caplog.records
    )

    payload = _session_payload(paths["project"], session_id)
    assert payload["refresh_type"] == "full"
    assert payload.get("refresh_fallback_reason") == "incremental_failed"


def test_refresh_session_persists_changeset_and_refresh_type_in_payload(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["service"].write_text(
        "from src.core import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total) + 4\n",
        encoding="utf-8",
    )
    added_path = _write(
        paths["project"] / "src" / "billing.py", "def issue_invoice(total):\n    return total\n"
    )
    paths["helper"].unlink()

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    payload = _session_payload(paths["project"], session_id)

    assert refreshed.refresh_type == "incremental"
    assert refreshed.changeset == {
        "added": [str(added_path.resolve())],
        "modified": [str(paths["service"].resolve())],
        "removed": [str(paths["helper"].resolve())],
    }
    assert payload["refresh_type"] == "incremental"
    assert payload["changeset"] == refreshed.changeset


def test_refresh_session_incremental_repo_map_matches_full_rebuild(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n    base = total + 1\n    return base * 2\n",
        encoding="utf-8",
    )
    _write(
        paths["project"] / "src" / "billing.py",
        "from src.core import create_invoice\n\n"
        "def issue_invoice(total):\n"
        "    return create_invoice(total)\n",
    )

    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    payload = _session_payload(paths["project"], session_id)

    assert refreshed.refresh_type == "incremental"
    assert payload["repo_map"] == repo_map.build_repo_map(
        paths["project"],
        max_repo_files=session_store.DEFAULT_AGENT_REPO_MAP_LIMIT,
    )


def test_session_context_raises_stale_error_with_changeset_summary(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["core"].write_text(
        "def create_invoice(total):\n    return total + 10\n",
        encoding="utf-8",
    )

    with pytest.raises(session_store.SessionStaleError, match="changed on disk") as exc_info:
        session_store.session_context(session_id, "invoice", str(paths["project"]))

    assert str(paths["core"].resolve()) in str(exc_info.value)


def test_incremental_refresh_preserves_plan_seed_for_unchanged_symbol(tmp_path: Path) -> None:
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])

    before = session_store.session_context_render(
        session_id,
        "create invoice",
        str(paths["project"]),
        max_files=3,
        max_sources=3,
    )

    paths["helper"].write_text(
        "def format_invoice_label(invoice_id):\n"
        "    prefix = 'invoice'\n"
        "    return f'{prefix}-{invoice_id}'\n",
        encoding="utf-8",
    )
    refreshed = session_store.refresh_session(session_id, str(paths["project"]))
    after = session_store.session_context_render(
        session_id,
        "create invoice",
        str(paths["project"]),
        max_files=3,
        max_sources=3,
    )

    assert refreshed.refresh_type == "incremental"
    assert after["edit_plan_seed"] == before["edit_plan_seed"]


def test_incremental_repo_map_is_faster_than_full_rebuild_for_small_changes(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(80):
        _write(
            src_dir / f"module_{index}.py",
            f"def value_{index}():\n    return {index}\n",
        )

    previous_map = repo_map.build_repo_map(project)
    changed_path = src_dir / "module_0.py"
    changed_path.write_text("def value_0():\n    return 999\n", encoding="utf-8")

    original = repo_map._imports_and_symbols_for_path

    # The per-file parse cost must DOMINATE the fixed graph/PageRank/assembly
    # overhead that BOTH paths share (the ``all_files`` loop in
    # build_repo_map_incremental + build_repo_map). With a small sleep that
    # shared overhead was ~equal to the sleep-savings, pinning the ratio at
    # ~0.5 and flaking on CI (assert 0.2126 < 0.2113, missed by 0.0013s). A
    # larger per-file sleep makes the timing reflect the real file-count
    # savings (incremental reparses 1 of 80 files, ratio -> ~0.13), so the
    # threshold below has ~14x headroom against overhead noise.
    per_file_parse_cost = 0.02

    def slow_parser(path: Path) -> tuple[list[str], list[dict[str, object]]]:
        time.sleep(per_file_parse_cost)
        return original(path)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", slow_parser)

    start = time.perf_counter()
    repo_map.build_repo_map_incremental(
        previous_map,
        {"added": [], "modified": [str(changed_path.resolve())], "removed": []},
    )
    incremental_duration = time.perf_counter() - start

    start = time.perf_counter()
    repo_map.build_repo_map(project)
    full_duration = time.perf_counter() - start

    # Incremental reparses 1 file; full reparses all 80. The real ratio is
    # ~0.13; 0.65 catches the regression class (incremental accidentally
    # reparsing every file -> ratio -> ~1.0) while tolerating CI overhead noise.
    assert incremental_duration < (full_duration * 0.65)


# ---------------------------------------------------------------------------
# (#284) build_repo_map_incremental must emit `unreadable_paths` like build_repo_map
# ---------------------------------------------------------------------------


def _deny_scandir_for(monkeypatch, denied_dir: Path) -> None:
    """Make `os.scandir(denied_dir)` raise PermissionError; everything else untouched.

    Deliberately NOT a real chmod/ACL fixture. Task #281 burned a probe on exactly that: the ACL
    silently failed to apply, so the "hostile" arm was a readable directory and the run would have
    declared a live defect ABSENT. A monkeypatched raise cannot silently no-op.
    """
    import os as _os

    real_scandir = _os.scandir
    target = str(denied_dir.resolve())

    def _fake_scandir(path=".", *args, **kwargs):
        if str(Path(path).resolve()) == target:
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(_os, "scandir", _fake_scandir)


def test_incremental_map_reports_unreadable_paths_exactly_like_the_full_builder(
    tmp_path: Path, monkeypatch
) -> None:
    """Task #284. `build_repo_map_incremental` returns a payload of the SAME SHAPE as
    `build_repo_map`, so a consumer cannot tell which produced it -- but it never passed
    `unreadable_hit=`, so it could never emit `unreadable_paths` AT ALL. Every consumer on the
    incremental path kept getting the silent lie the full builder stopped telling in #276 slice 1
    (and `tg orient` already reads that key at orient_capsule.py:1159).

    Asserts the OUTCOME via the strongest oracle this module already trusts -- the two builders
    must agree -- rather than merely checking a key exists. Pre-fix this FAILS because only the
    full map carries `unreadable_paths`.
    """
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    denied = paths["project"] / "src" / "denied_pkg"
    denied.mkdir()
    _write(denied / "hidden.py", "def hidden():\n    return 1\n")
    _write(paths["project"] / "src" / "billing.py", "def bill():\n    return 2\n")

    changeset = _changeset_for_session(paths["project"], session_id)
    _deny_scandir_for(monkeypatch, denied)
    incremental_map = repo_map.build_repo_map_incremental(
        repo_map.build_repo_map(paths["project"]), changeset
    )
    full_map = repo_map.build_repo_map(paths["project"])

    # Both builders saw the same denied subtree, so both must report it identically.
    assert full_map.get("unreadable_paths", {}).get("count", 0) >= 1
    assert incremental_map.get("unreadable_paths") == full_map.get("unreadable_paths")


def test_incremental_map_omits_unreadable_paths_on_a_clean_walk(tmp_path: Path) -> None:
    """Control arm: the same corpus with nothing denied must emit the key on NEITHER builder, so a
    complete incremental map stays byte-identical to before this change. Without this the
    assertion above could pass for a builder that emitted the key unconditionally.
    """
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    _write(paths["project"] / "src" / "billing.py", "def bill():\n    return 2\n")

    changeset = _changeset_for_session(paths["project"], session_id)
    incremental_map = repo_map.build_repo_map_incremental(
        repo_map.build_repo_map(paths["project"]), changeset
    )

    assert "unreadable_paths" not in incremental_map
    assert "unreadable_paths" not in repo_map.build_repo_map(paths["project"])


# (#286) An UNREADABLE file must never be reported as REMOVED
# ---------------------------------------------------------------------------


def _deny_stat_under(monkeypatch, denied_dir: Path) -> None:
    """Make `os.stat` raise PermissionError for anything under `denied_dir`.

    Patched NARROWLY on purpose: an earlier probe patched `os.stat` globally and broke pytest's
    OWN failure reporting -- the instrument took down the harness. Scoping the raise to one
    subtree keeps pytest able to report a failure if this test fails.
    """
    import os as _os

    real_stat = _os.stat
    # Normalize with PURE STRING ops only. A first version called `Path(...).resolve()` inside the
    # fake, and `resolve()` itself calls `os.stat` -- instant RecursionError. The instrument ate
    # itself. `os.path.normpath`/`abspath` touch no filesystem.
    target = _os.path.normcase(_os.path.normpath(_os.path.abspath(str(denied_dir))))

    def _fake_stat(path, *args, **kwargs):
        candidate = _os.path.normcase(_os.path.normpath(_os.path.abspath(str(path))))
        if candidate.startswith(target):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(_os, "stat", _fake_stat)


def test_unreadable_file_is_not_reported_as_removed(tmp_path: Path, monkeypatch) -> None:
    """Task #286, MEASURED defect. `_stale_changeset` wrapped `os.stat` in a bare `except OSError`
    and appended to `removed`, so it could not tell "the file is gone" (FileNotFoundError) from
    "I am not allowed to look at it" (PermissionError).

    What that breaks: a false `removed` entry reaches `_changeset_has_entries` ->
    `_ensure_session_not_stale`, which raises SessionStaleError naming files nobody touched;
    `_session_health_payload` RECOMPUTES the same changeset and serves it with `stale: true`, so
    the `health` request on the session serve/daemon protocol reports live files as deleted; and
    on MCP `refresh_on_stale=True` it forces a needless rebuild.

    THREE THINGS EARLIER DRAFTS OF THIS DOCSTRING GOT WRONG, every one by naming a consumer
    without checking it: (1) it does NOT evict anything from the repo map --
    `build_repo_map_incremental` ignores `removed` entirely (see its D2 comment); (2) health does
    NOT re-serve the `changeset` key persisted in the session payload -- that copy has no reader
    in `src/`; (3) there is no `tg session health` CLI command and no `tg_session_health` MCP tool
    -- `health` is only a request kind on the serve/daemon protocol. GREP THE NAME FIRST.
    """
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    denied_dir = paths["project"] / "src"

    # PRECONDITION: without this the test can go inert. If `_build_project`'s layout ever stops
    # putting files under src/, the "in p" filter below matches nothing and the assertion passes
    # vacuously -- declaring the defect absent instead of testing for it.
    payload = _session_payload(paths["project"], session_id)
    snapshot_src_entries = [e for e in payload["snapshot"] if "src" in str(e["path"])]
    assert len(snapshot_src_entries) == 3, (
        "fixture drift: expected 3 snapshot entries under src/ for the deny-stat arm to bite, "
        f"got {len(snapshot_src_entries)}"
    )

    _deny_stat_under(monkeypatch, denied_dir)
    changeset = session_store._stale_changeset(payload, detect_added_files=False)

    assert changeset is not None
    unreadable_reported_as_removed = [p for p in changeset["removed"] if "src" in p]
    assert unreadable_reported_as_removed == [], (
        "a permission-denied file was reported as REMOVED; that false report raises "
        "SessionStaleError and is recomputed and served again by the health request: "
        f"{unreadable_reported_as_removed}"
    )


def test_genuinely_deleted_file_is_still_reported_as_removed(tmp_path: Path) -> None:
    """CONTROL ARM. Without this, the fix above could be "never report removals" -- which
    satisfies the first assertion while destroying the feature entirely. A real deletion must
    still land in `removed`.
    """
    paths = _build_project(tmp_path)
    session_id = _open_session(paths["project"])
    paths["helper"].unlink()

    changeset = session_store._stale_changeset(
        _session_payload(paths["project"], session_id), detect_added_files=False
    )

    assert changeset is not None
    assert any("helpers.py" in p for p in changeset["removed"]), (
        f"a genuinely deleted file vanished from `removed`: {changeset['removed']}"
    )
