"""Path-containment regression test for audit-manifest root resolution (audit HIGH).

``_resolve_manifest_root`` honored the manifest's self-reported ``path`` field verbatim
whenever it pointed at any *existing* directory, with no containment check. A tampered
manifest (attacker-controlled JSON) could therefore redirect the filesystem root used for
audit-history writes and checkpoint reads to any directory on disk. The root must instead
be honored only when the manifest file actually lives under the declared root, else be
derived from the manifest file's own location.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli.audit_manifest import (
    _AUDIT_SUBDIR,
    _TG_DIRNAME,
    _diff_manifest_values,
    _resolve_manifest_root,
)


def _deep_manifest(leaf: str, depth: int = 100) -> dict:
    root: dict = {}
    node = root
    for _ in range(depth):
        node["k"] = {}
        node = node["k"]
    node["v"] = leaf
    return root


def test_diff_manifest_values_bounds_recursion_depth() -> None:
    """Audit LOW (DoS): a maliciously deep manifest must raise a clean bounded ValueError,
    not crash `tg audit diff` with an uncaught RecursionError."""
    with pytest.raises(ValueError):
        _diff_manifest_values(
            _deep_manifest("a"),
            _deep_manifest("b"),
            path="",
            added={},
            removed={},
            changed={},
        )


def test_resolve_manifest_root_ignores_tampered_out_of_tree_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    audit_dir = repo / _TG_DIRNAME / _AUDIT_SUBDIR
    audit_dir.mkdir(parents=True)
    manifest_path = audit_dir / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    # An attacker points the manifest's `path` at an unrelated existing directory.
    victim = tmp_path / "victim"
    victim.mkdir()

    root = _resolve_manifest_root(manifest_path, {"path": str(victim)})

    # The tampered path must NOT be honored; the root must derive from the manifest location.
    assert root == repo.resolve()
    assert root != victim.resolve()


def test_resolve_manifest_root_honors_legit_containing_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    audit_dir = repo / _TG_DIRNAME / _AUDIT_SUBDIR
    audit_dir.mkdir(parents=True)
    manifest_path = audit_dir / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    # A legit manifest declares the root it actually lives under.
    root = _resolve_manifest_root(manifest_path, {"path": str(repo)})
    assert root == repo.resolve()
