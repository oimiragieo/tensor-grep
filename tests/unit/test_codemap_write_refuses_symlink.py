"""`tg codemap` must not write through a symlinked destination.

Audit finding DD-858, and the THIRD instance of one class:

  #847  DirectoryScanner yielded symlinked FILES        (read side, disclosure)
  #852  backend_ast direct_write_file followed symlinks (write side, --apply)
  DD-858 codemap._atomic_write_text                     (write side, doc generation)

Each earlier fix closed one site. `codemap.py` hand-rolls `write_text(tmp)` + `replace_with_retry`
with no `is_symlink` precheck, while `_index_lock.atomic_write_bytes` -- which exists in this repo
precisely to refuse a symlinked destination, with `O_CREAT|O_EXCL|O_NOFOLLOW` and mode-at-create --
was called ZERO times from this module.

Severity is LOW, deliberately stated: `replace()` swaps the LINK ENTRY, leaving the target's
content intact, so this is an integrity/doc-generation surface rather than the content-overwrite
that #852 was. It is fixed anyway because a write surface that bypasses the shared guarded writer
is how the next instance gets written.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli.codemap import _atomic_write_text

_MARKER = "OUTSIDE_TARGET_MARKER_5150\n"


def _linked(tmp_path: Path) -> tuple[Path, Path]:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "real.md"
    target.write_text(_MARKER, encoding="utf-8")

    out = tmp_path / "out"
    out.mkdir()
    link = out / "index.md"
    try:
        link.symlink_to(target)
    except OSError as exc:  # pragma: no cover - unprivileged Windows
        pytest.skip(f"cannot create a symlink on this host: {exc}")

    # PREMISE: the fixture really is the dangerous shape, or every assertion below is vacuous.
    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    return link, target


def test_a_symlinked_destination_is_refused(tmp_path: Path) -> None:
    """THE DEFECT: the hand-rolled writer replaced the link entry with no precheck."""
    link, _ = _linked(tmp_path)

    with pytest.raises(OSError):
        _atomic_write_text(link, "# generated\n")


def test_the_symlink_target_is_never_modified(tmp_path: Path) -> None:
    """Stronger than the raise: assert the TARGET is byte-identical afterwards.

    Checking only for an exception would pass an implementation that wrote first and raised after.
    """
    link, target = _linked(tmp_path)

    with pytest.raises(OSError):
        _atomic_write_text(link, "# generated\n")

    assert target.read_text(encoding="utf-8") == _MARKER, "the symlink target was modified"


def test_an_ordinary_destination_is_still_written(tmp_path: Path) -> None:
    """CONTROL ARM: without it, 'refuse every write' passes both tests above and breaks codemap."""
    out = tmp_path / "out"
    out.mkdir()
    plain = out / "index.md"

    _atomic_write_text(plain, "# first\n")
    assert plain.read_text(encoding="utf-8") == "# first\n"

    # Overwrite must work too -- codemap regenerates in place on every run.
    _atomic_write_text(plain, "# second\n")
    assert plain.read_text(encoding="utf-8") == "# second\n"


def test_no_temp_file_is_left_behind(tmp_path: Path) -> None:
    """CONTROL ARM: a refusal must not litter the output directory with .tmp files.

    The pre-fix writer created the temp BEFORE any check, so a naive 'raise early' fix could leave
    orphans in a directory codemap regenerates repeatedly.
    """
    link, _ = _linked(tmp_path)
    out = link.parent

    with pytest.raises(OSError):
        _atomic_write_text(link, "# generated\n")

    leftovers = [p.name for p in out.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"refusal left temp files behind: {leftovers}"
