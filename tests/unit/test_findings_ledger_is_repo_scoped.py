"""Slice 2 of the ledger must resolve to the same physical index from any subtree of a repo.

Slice 1 (claims) was fixed for the "PATH-scope footgun" in v1.92.1: `claim core/hooks` and
`list .` each resolved to a DIFFERENT physical directory, so a claim filed from one subtree was
invisible from another within the SAME repository, with no error and no signal.

Slice 2 (`record_finding` / `find_findings`) was left on the literal
`session_store._resolve_root`, and the module docstring said so explicitly -- "per the same
footgun it has not (yet) been reported for". It has now been reported, twice, in the live external
dogfoods of v1.101.7 and v1.101.9 ("Slice 2 ledger still more path-literal than Slice 1").

The consequence is worse here than for claims. A findings miss is SILENT and self-justifying: the
caller asked "has anyone computed this?", got "no", and recomputed -- which is exactly what it
would do if the answer were legitimately no. "Nothing recorded" and "recorded under a different
root" are indistinguishable at the call site, so the coordination pillar degrades to zero reuse
without ever reporting a fault.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli.ledger_store import find_findings, record_finding


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with a nested subtree. Returns (repo_root, subtree)."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "core" / "hooks"
    sub.mkdir(parents=True)
    (root / "a.py").write_text("def target():\n    pass\n", encoding="utf-8")

    # PREMISE: the two paths really are distinct directories, so a literal-root implementation
    # genuinely would resolve them differently. Without this the test could pass on a fluke of
    # tmp_path layout.
    assert root.resolve() != sub.resolve()
    return root, sub


def _receipt(tmp_path: Path, name: str = "receipt.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"summary": "precomputed", "items": [1, 2, 3]}), encoding="utf-8")
    return path


def _record(where: Path, receipt: Path, symbol: str = "target") -> dict:
    return record_finding(
        str(where),
        symbol=symbol,
        artifact_kind="blast-radius",
        receipt_path=str(receipt),
    )


def test_a_finding_recorded_at_the_root_is_found_from_a_subtree(tmp_path: Path) -> None:
    """THE DEFECT: the subtree lookup resolved to its own index and returned nothing."""
    root, sub = _repo(tmp_path)
    _record(root, _receipt(tmp_path))

    result = find_findings(str(sub), symbol="target")

    assert result["count"] == 1, (
        "a finding recorded at the repo root is invisible from a subtree of the same repo -- "
        "the sibling agent recomputes an artifact that is already on disk"
    )


def test_a_finding_recorded_in_a_subtree_is_found_from_the_root(tmp_path: Path) -> None:
    """The other direction. Both must unify, or the fix is half a fix."""
    root, sub = _repo(tmp_path)
    _record(sub, _receipt(tmp_path))

    result = find_findings(str(root), symbol="target")

    assert result["count"] == 1, "a finding recorded in a subtree is invisible from the repo root"


def test_two_different_repos_still_do_not_share_findings(tmp_path: Path) -> None:
    """CONTROL ARM: without it, "resolve everything to one global index" would satisfy both tests
    above while destroying isolation between unrelated repositories.

    This is the assertion that makes the two above mean something -- it pins that the unification
    is bounded by the `.git` boundary rather than being unconditional.
    """
    root_a, _ = _repo(tmp_path / "one")
    root_b, _ = _repo(tmp_path / "two")
    _record(root_a, _receipt(tmp_path))

    assert find_findings(str(root_a), symbol="target")["count"] == 1, "premise: A really recorded"
    assert find_findings(str(root_b), symbol="target")["count"] == 0, (
        "a finding from an unrelated repository leaked across the .git boundary"
    )


def test_a_non_git_directory_keeps_literal_path_behaviour(tmp_path: Path) -> None:
    """The documented fallback: with no `.git` ancestor there is no repo boundary to canonicalize
    to, so resolution stays literal. Pinned so a later "always walk up" change has to argue with
    it -- walking past a non-git directory would start merging unrelated working directories.
    """
    plain = tmp_path / "plain"
    nested = plain / "nested"
    nested.mkdir(parents=True)

    _record(plain, _receipt(tmp_path))

    assert find_findings(str(plain), symbol="target")["count"] == 1, "premise: it recorded"
    assert find_findings(str(nested), symbol="target")["count"] == 0, (
        "a non-git directory must keep literal-path behaviour; unifying here would merge "
        "unrelated working directories that happen to share a parent"
    )


@pytest.mark.parametrize("start", ["root", "sub"])
def test_the_index_lives_at_the_repo_root_regardless_of_where_record_ran(
    tmp_path: Path, start: str
) -> None:
    """Storage location, not just lookup: exactly one physical index, at the repo root.

    Asserting only on `find` would pass for an implementation that wrote two indices and then
    read both -- which reintroduces the two-root ambiguity instead of removing it.
    """
    root, sub = _repo(tmp_path)
    _record(root if start == "root" else sub, _receipt(tmp_path))

    indices = sorted(p.relative_to(root).as_posix() for p in root.rglob(".tensor-grep"))
    assert indices == [".tensor-grep"], (
        f"expected exactly one ledger directory, at the repo root; found {indices}"
    )
