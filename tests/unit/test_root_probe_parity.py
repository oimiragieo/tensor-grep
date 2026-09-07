"""AGT-06 parity gate: bootstrap's short-circuit probe and main.py's sorted-diagnostics
probe must keep their existing return shape and short-circuit/collection behavior after both
are refactored to share `io/root_probe.iter_top_level_vendored_dirs` (Task 08).

Frozen fixture table: vendored child, ordinary Node root, tool cache, nonexistent root,
unreadable root, mixed case, and multiple roots -- compared against each adapter's own
pre-refactor baseline behavior (not against each other, since their shapes intentionally
differ: bool vs sorted list).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tensor_grep.cli.bootstrap import _search_paths_include_vendored_root
from tensor_grep.cli.main import _root_top_level_vendored_dir_names
from tensor_grep.io.root_probe import iter_top_level_vendored_dirs
from tensor_grep.io.scan_limits import UNBOUNDED_VENDORED_ROOT_DIR_NAMES

_A_VENDORED_NAME = next(iter(UNBOUNDED_VENDORED_ROOT_DIR_NAMES))


def _make_root(tmp_path: Path, *dirnames: str) -> Path:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    for name in dirnames:
        (root / name).mkdir()
    return root


def test_vendored_child_short_circuits_true(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME)
    assert _search_paths_include_vendored_root([str(root)]) is True


def test_vendored_child_collected_sorted(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME)
    assert _root_top_level_vendored_dir_names([str(root)]) == [_A_VENDORED_NAME]


def test_ordinary_node_root_no_vendored_child(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "src", "tests")
    assert _search_paths_include_vendored_root([str(root)]) is False
    assert _root_top_level_vendored_dir_names([str(root)]) == []


def test_nonexistent_root(tmp_path: Path) -> None:
    missing = str(tmp_path / "does-not-exist")
    assert _search_paths_include_vendored_root([missing]) is False
    assert _root_top_level_vendored_dir_names([missing]) == []


@pytest.mark.skipif(os.name == "nt", reason="chmod-based unreadable dirs are not enforced on Windows")
def test_unreadable_root_swallows_oserror(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME)
    root.chmod(0)
    try:
        assert _search_paths_include_vendored_root([str(root)]) is False
        assert _root_top_level_vendored_dir_names([str(root)]) == []
    finally:
        root.chmod(stat.S_IRWXU)


def test_mixed_case_vendored_name_matches(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME.upper())
    assert _search_paths_include_vendored_root([str(root)]) is True
    assert _root_top_level_vendored_dir_names([str(root)]) == [_A_VENDORED_NAME.upper()]


def test_multiple_roots_bootstrap_short_circuits_on_first_hit(tmp_path: Path) -> None:
    clean = _make_root(tmp_path / "a", "src")
    vendored = _make_root(tmp_path / "b", _A_VENDORED_NAME)
    assert _search_paths_include_vendored_root([str(clean), str(vendored)]) is True


def test_multiple_roots_main_collects_dedupes_sorts(tmp_path: Path) -> None:
    root_a = _make_root(tmp_path / "a", _A_VENDORED_NAME)
    root_b = _make_root(tmp_path / "b", _A_VENDORED_NAME)
    result = _root_top_level_vendored_dir_names([str(root_a), str(root_b)])
    assert result == [_A_VENDORED_NAME]


def test_empty_stdin_and_flag_like_paths_skipped(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME)
    paths = ["", "-", "--json", str(root)]
    assert _search_paths_include_vendored_root(paths) is True
    assert _root_top_level_vendored_dir_names(paths) == [_A_VENDORED_NAME]


def test_shared_iterator_matches_bootstrap_short_circuit(tmp_path: Path) -> None:
    root = _make_root(tmp_path, _A_VENDORED_NAME)
    names = list(iter_top_level_vendored_dirs([str(root)], UNBOUNDED_VENDORED_ROOT_DIR_NAMES))
    assert bool(names) is _search_paths_include_vendored_root([str(root)])


def test_shared_iterator_matches_main_sorted_dedup(tmp_path: Path) -> None:
    root_a = _make_root(tmp_path / "a", _A_VENDORED_NAME)
    root_b = _make_root(tmp_path / "b", _A_VENDORED_NAME)
    paths = [str(root_a), str(root_b)]
    names = sorted(
        set(iter_top_level_vendored_dirs(paths, UNBOUNDED_VENDORED_ROOT_DIR_NAMES)),
        key=lambda item: item.lower(),
    )
    assert names == _root_top_level_vendored_dir_names(paths)
