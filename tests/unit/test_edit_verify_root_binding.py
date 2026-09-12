"""`verify_edit_ticket` must be about the tree the TICKET describes, and must fail closed
when its own verify-time walk was truncated.

Two holes, both of which let a FAIL-shaped situation reach a PASS-shaped verdict:

1. The ticket records the `repo_root` it was built from, but `verify_edit_ticket` took the
   root as an INDEPENDENT argument and never compared the two -- so a ticket minted against
   tree A could be verified against tree B and every fingerprint comparison would silently be
   cross-tree.
2. The verify-time walk result was discarded (`current_fps, _current_population = ...`). The
   ticket-side population gate cannot speak for THAT walk: if it was cut off by a budget,
   files it never reached have no current fingerprint, so drift in them is undetectable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tensor_grep.cli import edit_ticket_service
from tensor_grep.cli.edit_ticket_service import build_edit_ready_ticket, verify_edit_ticket


def _ticket_for(root: Path):
    target = root / "allowed.py"
    target.write_text("a = 1\n", encoding="utf-8")
    return build_edit_ready_ticket(
        repo_root=str(root),
        target_path=str(target),
        query="a = 1",
        allowed_files=["allowed.py"],
    )


def test_verifying_against_a_different_repo_root_fails_closed(tmp_path: Path) -> None:
    tree_a = tmp_path / "a"
    tree_b = tmp_path / "b"
    tree_a.mkdir()
    tree_b.mkdir()
    (tree_b / "allowed.py").write_text("a = 1\n", encoding="utf-8")

    ticket = _ticket_for(tree_a)

    result = verify_edit_ticket(
        repo_root=str(tree_b),
        ticket=ticket,
        modified_files=[],
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "repo_root_mismatch"


def test_verify_time_incomplete_population_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = _ticket_for(tmp_path)

    real_walk = edit_ticket_service._walk_tracked_files_bounded
    calls: list[int] = []

    def _truncated_walk(root):
        files, population = real_walk(root)
        calls.append(1)
        # The ticket was built by an earlier call with a COMPLETE population; only the
        # verify-time walk is truncated here, which is exactly the case the ticket-side gate
        # cannot see.
        return files, {**population, "status": "incomplete"}

    monkeypatch.setattr(edit_ticket_service, "_walk_tracked_files_bounded", _truncated_walk)

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=[],
    )

    assert calls, "the verify-time walk must actually have run"
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


def test_matching_root_and_complete_population_still_passes(tmp_path: Path) -> None:
    """MUTATION CONTROL.

    Both gates above are FAIL-returning. A fix that returned FAIL unconditionally, or a
    root comparison strict enough to reject a legitimate spelling of the same directory,
    would satisfy both tests while breaking every real verification. An untouched tree
    verified against its own root must still PASS.
    """
    ticket = _ticket_for(tmp_path)

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=[],
    )

    assert result["verdict"] == "PASS", result


def test_same_root_spelled_differently_still_passes(tmp_path: Path) -> None:
    """A trailing separator is not a different tree -- path spelling is not identity."""
    ticket = _ticket_for(tmp_path)

    result = verify_edit_ticket(
        repo_root=str(tmp_path) + "/",
        ticket=ticket,
        modified_files=[],
    )

    assert result["verdict"] == "PASS", result


def test_root_identity_is_case_folded_only_where_the_filesystem_is() -> None:
    """REGRESSION (validator seat, 2026-09-12). `_normalized_root` lowercased every resolved
    root unconditionally (shipped v1.119.8), so on a CASE-SENSITIVE filesystem `/tmp/Repo`
    and `/tmp/repo` -- two genuinely different trees -- compared equal and a cross-tree
    verify could slip past `repo_root_mismatch` when contents happened to match.

    Asserted against the platform's real rule rather than hardcoding one, so the test states
    the invariant on both populations instead of passing vacuously on the author's Windows
    box (A125: a maintainer's machine is the wrong population).
    """
    from tensor_grep.cli.edit_ticket_service import _normalized_root

    upper = _normalized_root("/tmp/Repo/project")
    lower = _normalized_root("/tmp/repo/project")

    filesystem_is_case_insensitive = os.path.normcase("A") == "a"
    if filesystem_is_case_insensitive:
        assert upper == lower, "Windows/macOS-style FS: the two spellings ARE one tree"
    else:
        assert upper != lower, (
            "case-sensitive FS: /tmp/Repo and /tmp/repo are DIFFERENT trees and must not "
            "compare equal -- collapsing them bypasses repo_root_mismatch"
        )


def test_spelling_differences_that_are_never_identity_still_normalize() -> None:
    """Control for the test above: the normalizations that ARE always safe -- separator
    style and a trailing slash -- must still collapse on every platform, so the fix did not
    simply disable normalization.
    """
    from tensor_grep.cli.edit_ticket_service import _normalized_root

    assert _normalized_root("/tmp/repo/project/") == _normalized_root("/tmp/repo/project")
    assert _normalized_root("\\tmp\\repo") == _normalized_root("/tmp/repo")
