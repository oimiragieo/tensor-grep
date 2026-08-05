"""The native front-door download must not reopen its destination BY NAME after claiming it.

`tests/unit/test_native_download_exclusive_temp.py` covers the FIRST gap the #859 ratchet found:
a symlink planted at the temp path BEFORE `_download_native_frontdoor_asset` is even called gets
refused by the O_EXCL claim. That guard closes name-collision-at-claim-time, but it does not by
itself close the gap this module tests: the (former) implementation's `os.open(...); os.close(fd)`
claim RELEASED the fd, and `urllib.request.urlretrieve(url, destination, ...)` then reopened
`destination` BY NAME (via the builtin `open(filename, 'wb')`, internally), which follows a
symlink. Between the claim's `close(fd)` and that reopen, an attacker who wins the race can
replace `destination` with a symlink and have urlretrieve write the downloaded payload -- a native
EXECUTABLE the front door later runs -- straight through it.

frontdoor-download-held-fd task: fixed by claiming the fd and streaming the WHOLE transfer through
that SAME held fd (`os.fdopen(fd, "wb")`), never closing and reopening by name. This module proves
that specific property.
"""

from __future__ import annotations

import builtins
import os
from email.message import Message
from pathlib import Path
from typing import Any

import pytest


class _FakeUrlopenResponse:
    """A response object compatible with BOTH consumers this test must run against unmodified:
    the fixed implementation's own chunked `.read(n)` loop, AND (for the perturbation-proof RED
    arm, where this same test file is run against the pre-fix source) the real
    `urllib.request.urlretrieve`, which additionally calls `.info()` and `.close()` internally.
    Providing both keeps the red arm's failure the ASSERTION below, not an unrelated
    AttributeError from an incomplete fake -- an incidental crash would still technically be
    \"red\", but it would not be evidence of the specific property this test checks."""

    def __init__(self, payload: bytes) -> None:
        self._payload: bytes | None = payload

    def read(self, _size: int = -1) -> bytes:
        if self._payload is None:
            return b""
        payload, self._payload = self._payload, None
        return payload

    def info(self) -> Message:
        return Message()

    def close(self) -> None:
        pass

    def __enter__(self) -> _FakeUrlopenResponse:
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False


def test_download_never_reopens_destination_by_name_after_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural discriminator for the close-then-reopen TOCTOU gap: the download must open
    `destination` BY NAME exactly ONCE (the O_EXCL `os.open` claim) and never look the path up by
    name again -- via `os.open` OR the builtin `open()` -- for the rest of the transfer.

    WHY A CALL-COUNT CHECK AND NOT A LIVE SYMLINK-SWAP RACE: an earlier draft of this test tried to
    swap `destination` for a symlink from inside the fake network call, at the moment control
    returns to attacker-observable code after the claim succeeds (the same idea `test_native_
    download_exclusive_temp.py` uses for the pre-claim case). That is NOT portable to this
    platform: Windows opens files without `FILE_SHARE_DELETE` by default, so unlinking/renaming a
    path while a fd the fix itself keeps open for the whole transfer is still live raises
    `PermissionError: [WinError 32] ... used by another process` -- confirmed by running that
    draft on this box. A call-count assertion needs no OS-level rename semantics at all and is
    deterministic on every platform.

    WHY BOTH `os.open` AND THE BUILTIN `open()`: the pre-fix implementation's reopen goes through
    `urllib.request.urlretrieve`, which internally calls the builtin `open(filename, 'wb')`, not
    `os.open` -- a check that only counted `os.open` calls would show exactly one hit under BOTH
    implementations (the O_EXCL claim) and never discriminate at all, which was this test's own
    first draft mistake, caught by actually running the perturbation proof rather than assuming
    the check would fail red.

    REJECTED ALTERNATIVE: asserting `"urlretrieve" not in inspect.getsource(...)` (or grepping the
    source for the symbol). That proves the FUNCTION NAME is absent, not that bytes cannot land
    through a reopened path -- a rewrite that still closes-and-reopens via a differently-named API
    (`Path.open`, a second `os.open` call, `shutil.copyfileobj` into a fresh handle) would pass a
    source-text guard while carrying the identical defect. AGENTS.md's own dated-instrument laws
    make the same point about substring/name checks: "a source census is satisfied by a comment."
    Counting real by-name opens against the real destination path, made by the real function
    during a real invocation, pins the actual mechanism rather than how the fix happens to be
    spelled.
    """
    from tensor_grep.cli import main as cli_main

    destination = tmp_path / "asset.tmp"
    destination_str = str(destination)
    payload = b"REAL-ASSET-BYTES"

    real_os_open = os.open
    real_builtin_open = builtins.open
    open_calls: list[tuple[str, str]] = []

    def _tracking_os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if isinstance(path, (str, os.PathLike)) and str(path) == destination_str:
            open_calls.append(("os.open", str(path)))
        return real_os_open(path, *args, **kwargs)

    def _tracking_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        # Only path-based opens matter here (a NAME lookup) -- os.fdopen(fd, ...) routes through
        # the builtin open() too, but with an INT fd, never a path, so it is correctly excluded.
        if isinstance(file, (str, os.PathLike)) and str(file) == destination_str:
            open_calls.append(("open", str(file)))
        return real_builtin_open(file, *args, **kwargs)

    def _fake_urlopen(url: str, timeout: Any = None, *args: Any, **kwargs: Any) -> Any:
        return _FakeUrlopenResponse(payload)

    monkeypatch.setattr(os, "open", _tracking_os_open)
    monkeypatch.setattr(builtins, "open", _tracking_builtin_open)
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    cli_main._download_native_frontdoor_asset("https://example.invalid/tg", destination)

    assert open_calls == [("os.open", destination_str)], (
        "expected exactly ONE by-name open of destination (the O_EXCL os.open claim); got "
        f"{open_calls} -- any entry beyond the first is exactly the close-then-reopen-by-name "
        "shape that lets a symlink swapped in between the claim and the reopen get followed"
    )
    assert destination.read_bytes() == payload
