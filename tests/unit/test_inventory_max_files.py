"""TDD regression: ``tg inventory`` must thread ``--max-repo-files`` INTO the walk.

Bug (round-5 [q4]): ``build_inventory`` called ``_iter_repo_files(root, max_files=None)``
-- walking the WHOLE tree -- then sliced the already-fully-walked list down to
``max_files`` afterward. On a huge repo that is unbounded work despite the cap.

The fix threads the real cap into the iterator (``_iter_repo_files`` supports a real
bucketed early-stop, see ``repo_map.py::_iter_repo_files``) so the walk itself stops
once it has enough files, while still honoring the "truncation is never silent"
contract (``scan_limit.possibly_truncated`` / ``truncation_cause``).
"""

import os

import tensor_grep.cli.inventory as inventory_module
from tensor_grep.cli.inventory import build_inventory


def _write(root, rel, content=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


class TestWalkThreadsMaxFiles:
    def test_iterator_receives_bounded_max_files_not_none(self, tmp_path, monkeypatch):
        """The walker must be told the cap up front, not walked unbounded then sliced."""
        for i in range(20):
            _write(tmp_path, f"f{i:03d}.py", "x=1\n")

        real_iter_repo_files = inventory_module._iter_repo_files
        seen_max_files: list[int | None] = []

        def spy_iter_repo_files(root, *, max_files=None, **kwargs):
            seen_max_files.append(max_files)
            return real_iter_repo_files(root, max_files=max_files, **kwargs)

        monkeypatch.setattr(inventory_module, "_iter_repo_files", spy_iter_repo_files)

        inv = build_inventory(str(tmp_path), max_files=5)

        assert seen_max_files, "the walker was never called"
        # The old bug called _iter_repo_files(root, max_files=None) -- an unbounded
        # walk -- and truncated the already-fully-walked list afterward. The fix must
        # pass a real, finite bound tied to the requested cap.
        assert seen_max_files[0] is not None
        assert seen_max_files[0] <= 5 + 1

        assert inv["totals"]["files"] == 5
        assert inv["scan_limit"]["possibly_truncated"] is True
        assert inv["scan_limit"]["truncation_cause"] == "project-files"

    def test_walk_stops_early_does_not_scandir_every_top_level_dir(self, tmp_path, monkeypatch):
        """With N top-level dirs and max_files=k<N, only ~k dirs should ever be scanned.

        A fully-walk-then-slice implementation must call os.scandir on every one of
        the N top-level directories before it can slice the result down to k. A real
        early-stop walk only advances as many bucket generators as needed to reach
        the cap, so most of the N directories are never touched.
        """
        n_dirs = 40
        for i in range(n_dirs):
            _write(tmp_path, f"d{i:03d}/only.py", "x=1\n")

        scanned_dirs: set[str] = set()
        real_scandir = os.scandir

        def counting_scandir(path="."):
            scanned_dirs.add(os.fspath(path))
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", counting_scandir)

        inv = build_inventory(str(tmp_path), max_files=3)

        assert inv["totals"]["files"] == 3
        assert inv["scan_limit"]["possibly_truncated"] is True

        # +1 accounts for the root directory's own scandir call. A walk-everything
        # implementation would scan all n_dirs subdirectories (plus root); the
        # early-stop walk should touch only a handful near the cap.
        assert len(scanned_dirs) < n_dirs, (
            f"expected an early-stopped walk to skip most of {n_dirs} top-level dirs, "
            f"but {len(scanned_dirs)} were scanned"
        )
