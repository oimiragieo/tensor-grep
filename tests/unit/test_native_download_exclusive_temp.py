"""The native front-door download must not write through a symlink at its temp path.

`urllib.request.urlretrieve` opens its target with a plain `'wb'`, which FOLLOWS a symlink. The
downloaded payload here is a native EXECUTABLE that the front door later runs, so a followed
symlink writes attacker-influenced bytes to an attacker-chosen path.

HONEST SCOPE -- this is defence-in-depth, not a wide-open hole. The surrounding install path is
already well defended and none of that is changed here:

  * `CHECKSUMS.txt` is fetched BEFORE the download and the install REFUSES outright if it cannot
    be fetched (fail-closed, matching scripts/install.sh / install.ps1 / npm/install.js),
  * the temp target is `{name}.{uuid4().hex}.tmp` -- an UNPREDICTABLE name, so an attacker cannot
    pre-plant a symlink at a path they can name in advance,
  * the checksum is verified on the temp file BEFORE `os.replace` publishes it, with a version
    smoke test and rollback after.

What remains is a race: on a world-writable parent an attacker watching for creation (inotify or
equivalent) could still land a symlink between name selection and open. `O_EXCL` closes it by
atomically claiming the name as a regular file -- if anything already exists there, including a
symlink, the open fails instead of following it.

Found by the #859 atomic-writer ratchet (`test_cli_atomic_writer_ratchet.py`), which flagged
`urlretrieve` as an unguarded raw writer. The ratchet was right about the writer; reading the call
site is what right-sized the severity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _symlink_or_skip(link_path: Path, target: Path) -> None:
    try:
        link_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported / not privileged on this platform")


def test_download_refuses_to_write_through_a_symlinked_temp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE DEFECT: urlretrieve followed a symlink planted at the temp path.

    The fake urlretrieve below writes through whatever path it is handed, exactly as the real one
    does. If the download claims its temp name exclusively first, it never gets that far.
    """
    from tensor_grep.cli import main as cli_main

    protected = tmp_path / "protected.bin"
    protected.write_bytes(b"ORIGINAL-DO-NOT-OVERWRITE")

    temp_path = tmp_path / "asset.tmp"
    _symlink_or_skip(temp_path, protected)

    def _fake_urlretrieve(url: str, filename: Any, reporthook: Any = None) -> Any:
        # Mirrors urlretrieve's real behaviour: a plain binary open, which follows symlinks.
        with open(filename, "wb") as handle:
            handle.write(b"ATTACKER-CONTROLLED-PAYLOAD")
        return (str(filename), None)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlretrieve", _fake_urlretrieve)

    with pytest.raises((FileExistsError, OSError)):
        cli_main._download_native_frontdoor_asset("https://example.invalid/tg", temp_path)

    assert protected.read_bytes() == b"ORIGINAL-DO-NOT-OVERWRITE", (
        "the download wrote THROUGH the symlink and clobbered the protected target"
    )


def test_download_still_succeeds_on_an_ordinary_fresh_temp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONTROL: the guard must not break the ordinary path.

    Without this arm the fix could be "always raise", which would pass the test above for entirely
    the wrong reason -- the classic check that cannot fail.

    frontdoor-download-held-fd task: the download no longer calls `urlretrieve` (that reopen-by-
    name was the remaining TOCTOU gap this module's own docstring flags -- see the module
    docstring's "HONEST SCOPE" note, now closed by a follow-up); it streams through `urlopen` and
    the SAME held fd the O_EXCL claim below opens, so the fake swaps to `urlopen`.
    """
    from tensor_grep.cli import main as cli_main

    temp_path = tmp_path / "fresh.tmp"

    class _FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._chunks = [payload]

        def read(self, _size: int = -1) -> bytes:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *_exc_info: Any) -> bool:
            return False

    def _fake_urlopen(url: str, timeout: Any = None) -> Any:
        return _FakeResponse(b"REAL-ASSET-BYTES")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    cli_main._download_native_frontdoor_asset("https://example.invalid/tg", temp_path)

    assert temp_path.read_bytes() == b"REAL-ASSET-BYTES"
    assert not temp_path.is_symlink()


def test_download_refuses_when_a_regular_file_already_occupies_the_temp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O_EXCL means EXCLUSIVE: a pre-existing REGULAR file must also refuse, not be truncated.

    A uuid4 temp name should never collide, so a collision means something unexpected is writing
    into this directory -- refusing is the honest response and keeps the guard from degrading into
    "symlinks only", which an attacker could sidestep with a hard link.
    """
    from tensor_grep.cli import main as cli_main

    temp_path = tmp_path / "occupied.tmp"
    temp_path.write_bytes(b"SOMEONE-ELSE-WAS-HERE")

    def _fake_urlretrieve(url: str, filename: Any, reporthook: Any = None) -> Any:
        with open(filename, "wb") as handle:
            handle.write(b"NEW-BYTES")
        return (str(filename), None)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlretrieve", _fake_urlretrieve)

    with pytest.raises((FileExistsError, OSError)):
        cli_main._download_native_frontdoor_asset("https://example.invalid/tg", temp_path)

    assert temp_path.read_bytes() == b"SOMEONE-ELSE-WAS-HERE"
