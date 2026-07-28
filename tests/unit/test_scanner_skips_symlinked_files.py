"""`DirectoryScanner.walk()` must never yield a symlinked FILE.

`os.walk(followlinks=False)` — the default this scanner relies on — stops the walk DESCENDING into
a symlinked directory. It does not stop a symlink-to-a-file appearing in ``files``. Every consumer
then does a plain ``open(path, "rb")``, which follows the link to its real target regardless.

So a git-tracked symlink (an ordinary mode-120000 blob — nothing exotic) pointing at
``~/.ssh/id_rsa``, a sibling project's ``.env``, or ``/etc/passwd`` disclosed that file's contents
into search output, the on-disk symbol cache, and — because ``DirectoryScanner`` is the walker
behind ``tg agent`` / ``tg orient`` / the MCP tools — an LLM agent's context window.

Proven on a temp fixture before the fix::

    repo/leak.txt -> ../outside/secret.txt   (contains SECRET_MARKER_998877)
    DirectoryScanner.walk(repo) -> ['leak.txt', 'normal.py']
    open('.../leak.txt').read() -> 'SECRET_MARKER_998877'

`checkpoint_store.py` already carried this guard. AGENTS.md names symlink-follow as a repo-wide
SWEEP target, not a one-file patch — this is the core walker the sweep missed, and it matters more
than the site it landed on: checkpoint create/restore is occasional, this is the default read path
for ordinary search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner

_MARKER = "SECRET_MARKER_998877"


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    """A scanned root containing a symlink that escapes it.

    Returns (repo_root, link_path). Skips on hosts that cannot create symlinks (unprivileged
    Windows) — mirroring the repo's existing follow-fixture pattern rather than silently passing.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(f"{_MARKER}\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "normal.py").write_text("x = 1\n", encoding="utf-8")

    link = repo / "leak.txt"
    try:
        link.symlink_to(secret)
    except OSError as exc:  # pragma: no cover - platform/privilege dependent
        pytest.skip(f"cannot create a symlink on this host: {exc}")

    # PREMISE: the fixture really is the dangerous shape. Without this, a host that silently
    # created a regular file instead of a link would make every assertion below vacuous.
    assert link.is_symlink(), "fixture did not produce a symlink"
    assert link.resolve() == secret.resolve(), "symlink does not point out of the scanned root"
    return repo, link


def _walk(repo: Path) -> list[str]:
    return [Path(p).name for p in DirectoryScanner(SearchConfig()).walk(str(repo))]


def test_a_symlinked_file_is_not_yielded(tmp_path: Path) -> None:
    """THE DEFECT: `leak.txt` was yielded, and opening it returned out-of-root content."""
    repo, _ = _fixture(tmp_path)
    names = _walk(repo)
    # PREMISE: the walk really ran and found the ordinary file, so the absence below is a skip
    # and not an empty walk.
    assert "normal.py" in names, f"walk returned nothing usable: {names}"
    assert "leak.txt" not in names, (
        "DirectoryScanner yielded a symlinked file; every consumer open()s it and follows the "
        "link out of the scanned root"
    )


def test_the_out_of_root_content_is_never_reachable_through_the_walk(tmp_path: Path) -> None:
    """End-to-end: no path the walk yields may read back the marker.

    Stronger than the name check above — it survives a fix that renames or relocates the link
    rather than skipping it.
    """
    repo, _ = _fixture(tmp_path)
    for path in DirectoryScanner(SearchConfig()).walk(str(repo)):
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        assert _MARKER not in content, f"{path} discloses out-of-root content"


def test_ordinary_files_are_still_walked(tmp_path: Path) -> None:
    """CONTROL ARM: without it, a scanner that yielded NOTHING would pass both tests above."""
    repo, _ = _fixture(tmp_path)
    (repo / "second.py").write_text("y = 2\n", encoding="utf-8")
    names = _walk(repo)
    assert "normal.py" in names
    assert "second.py" in names


def test_a_symlink_whose_target_is_inside_the_root_is_also_skipped(tmp_path: Path) -> None:
    """Skipped, not resolved-and-bounded — and deliberately so.

    A link pointing INSIDE the root loses no content by being skipped: the real file is walked by
    its own path. Resolving-then-comparing would have to get root containment exactly right on
    every platform (case-insensitive FS, UNC paths, junctions) to avoid reintroducing the escape,
    for zero gain. This test pins the simpler contract so a later "improvement" to resolve-and-
    allow has to argue with it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    real = repo / "real.py"
    real.write_text("z = 3\n", encoding="utf-8")
    inner = repo / "alias.py"
    try:
        inner.symlink_to(real)
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"cannot create a symlink on this host: {exc}")
    assert inner.is_symlink()

    names = _walk(repo)
    assert "real.py" in names, "the real file must still be walked"
    assert "alias.py" not in names, "an in-root symlink is skipped; its target is walked directly"
