"""#304: the session rebuild must honour a deadline -- on BOTH refresh branches.

`session_store`'s three `build_repo_map` calls were the only ones in the codebase with no time
bound, while every symbol command already had one. A daemon serving a stale session rebuilt the
whole map unbounded, the client gave up at its own 60s, and the cold path then anchored a FRESH
budget -- so one stated deadline could be exceeded roughly twofold with the truncation disclosed
nowhere.

THE TRAP THIS FILE EXISTS TO CATCH: `build_repo_map_incremental` had no `deadline_monotonic`
parameter at all, so threading a deadline through the session layer bounds only the FULL-rebuild
branch. A refresh takes the INCREMENTAL branch whenever a changeset exists -- the common case for
a warm session. Testing only the full path would leave the common case unbounded while every test
passed. Both branches are asserted here for that reason, and each has its own control.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Any

from tensor_grep.cli.repo_map import build_repo_map, build_repo_map_incremental


def _repo(tmp_path: Path, files: int = 12) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for i in range(files):
        (root / f"mod{i}.py").write_text(
            f"def fn{i}():\n    return {i}\n\n\nclass C{i}:\n    pass\n", encoding="utf-8"
        )
    return root


# --------------------------------------------------------------------------------------------
# The INCREMENTAL branch -- the one that had no deadline parameter at all
# --------------------------------------------------------------------------------------------


def test_incremental_rebuild_honours_an_exhausted_deadline(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    previous = build_repo_map(root)
    changeset: dict[str, Any] = {
        "added": [],
        "modified": [str(root / f"mod{i}.py") for i in range(12)],
        "removed": [],
    }

    # An ALREADY-PAST deadline: the parse loop must break on its first check. Using a past value
    # rather than a tiny future one keeps this free of wall-clock racing -- there is no duration
    # to measure and nothing for a loaded runner to perturb.
    result = build_repo_map_incremental(previous, changeset, deadline_monotonic=monotonic() - 1.0)

    assert result.get("partial") is True, (
        "an incremental rebuild that ran out of budget must say so; without this the warm-session "
        "path returns a silently-stale map at exit 0"
    )
    assert result["deadline_limit"]["deadline_exceeded"] is True
    # Same field names the FULL builder emits -- a consumer cannot tell which builder produced a
    # payload, so a deadline must look identical from either.
    assert set(result["deadline_limit"]) == {
        "deadline_exceeded",
        "files_scanned",
        "files_total",
    }


def test_incremental_rebuild_with_an_ample_deadline_is_not_marked_partial(
    tmp_path: Path,
) -> None:
    """CONTROL. Without it, a builder that hardcoded `partial=True` passes the test above."""
    root = _repo(tmp_path)
    previous = build_repo_map(root)
    changeset: dict[str, Any] = {
        "added": [],
        "modified": [str(root / "mod0.py")],
        "removed": [],
    }

    result = build_repo_map_incremental(previous, changeset, deadline_monotonic=monotonic() + 300.0)

    assert "partial" not in result, f"a completed rebuild must not claim partial: {result.keys()}"
    assert "deadline_limit" not in result


def test_incremental_rebuild_without_a_deadline_is_byte_identical_to_before(
    tmp_path: Path,
) -> None:
    """The backward-compatibility arm: `deadline_monotonic=None` must change nothing.

    This is what makes the parameter safe to add to a load-bearing path -- every existing caller
    passes nothing and must be unaffected. Compared as whole payloads rather than spot-checked
    keys, so a stray new field would fail here too.
    """
    root = _repo(tmp_path)
    previous = build_repo_map(root)
    changeset: dict[str, Any] = {
        "added": [],
        "modified": [str(root / "mod0.py")],
        "removed": [],
    }

    without = build_repo_map_incremental(previous, changeset)
    explicit_none = build_repo_map_incremental(previous, changeset, deadline_monotonic=None)
    assert without == explicit_none
    assert "partial" not in without


# --------------------------------------------------------------------------------------------
# The FULL branch, through the session layer -- proves the thread actually connects
# --------------------------------------------------------------------------------------------


def test_open_session_threads_its_deadline_into_the_repo_map(tmp_path: Path) -> None:
    """Seam test: the parameter has to REACH `build_repo_map`, not just exist on the signature.

    Asserted through observable output rather than by inspecting the call, because a signature
    that accepts `deadline_monotonic` and quietly drops it would satisfy any argument-shape check
    while leaving the defect exactly where it was.
    """
    from tensor_grep.cli import session_store

    root = _repo(tmp_path)
    result = session_store.open_session(str(root), deadline_monotonic=monotonic() - 1.0)
    repo_map = result.repo_map if hasattr(result, "repo_map") else None
    if repo_map is None:  # payload-shaped result
        import json

        payload = json.loads(
            session_store._session_payload_path(root, result.session_id).read_text(encoding="utf-8")
        )
        repo_map = payload["repo_map"]
    assert repo_map.get("partial") is True, (
        "open_session's deadline did not reach build_repo_map -- the parameter exists but is "
        "dropped, which is the defect wearing a fix's clothes"
    )


def test_open_session_without_a_deadline_still_builds_a_complete_map(tmp_path: Path) -> None:
    """CONTROL for the seam test: the default path must be unchanged and complete."""
    from tensor_grep.cli import session_store

    root = _repo(tmp_path)
    result = session_store.open_session(str(root))
    import json

    payload = json.loads(
        session_store._session_payload_path(root, result.session_id).read_text(encoding="utf-8")
    )
    assert "partial" not in payload["repo_map"]
