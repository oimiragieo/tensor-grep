"""The premise the task 307 reframe rests on: rg has no machine-readable incompleteness channel.

The trust benchmark reports tg, ripgrep and GNU grep tied 2 admits / 2 partial / 0 silent. The
proposed reading is that the tie is a MEASUREMENT artifact -- rg *structurally cannot* signal an
incomplete scan inside its JSON stream, so a benchmark that only reads stderr and the exit code
is looking at the one channel all three share.

That reading is worth nothing unless the premise is executable. If ripgrep ever grows a
machine-readable incompleteness signal, this test fails and the whole reframe is void -- which is
exactly what a premise test is for. It is a statement about rg's DESIGN, not a complaint:
`rg --json` emits begin/end/match/context/summary, and the terminal `summary` message carries
timing and stats and no incompleteness field (ripgrep `crates/printer/src/json.rs`; rg(1)
EXIT STATUS: *"2 exit status occurs when an error occurred ... for soft errors (e.g., unable to
read a file)"*). Both halves are asserted here: nothing in the JSON, and the signal present on
the exit code.

THE SETUP IS WHAT BREAKS THIS TEST, NOT THE ASSERTION. `os.chmod(d, 0o000)` is a NO-OP on
Windows -- the directory stays fully listable -- and it is also a no-op for root. Either way the
scan completes, rg exits 0, and a naive version of this test would pass while proving nothing.
So the unreadable condition is VERIFIED before anything is asserted, and the test skips with the
observed state when it cannot be established. Measured on the dev box while writing this:
chmod 0o000 then os.listdir() succeeded, i.e. LISTABLE anyway.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

# rg's five message types, per the JSON Lines contract. `summary` is the terminal one.
_TERMINAL_MESSAGE_TYPE = "summary"

# Any of these appearing in rg's summary would mean rg CAN disclose incompleteness in-band, and
# the task 307 reframe is dead. Deliberately generous -- a near-miss name should still trip it.
_INCOMPLETENESS_MARKERS = (
    "incomplete",
    "partial",
    "truncated",
    "unreadable",
    "error",
    "skipped",
    "failed",
)


def _keys_recursive(obj: object) -> set[str]:
    """Every key anywhere in a nested JSON value."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(key)
            found |= _keys_recursive(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _keys_recursive(item)
    return found


def _make_unreadable_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """A root holding one readable hit and one directory the process genuinely cannot read.

    Skips -- never returns a half-built tree -- when the OS will not honour the permission
    change, because a scan that can read everything cannot exercise the behaviour under test.
    """
    root = tmp_path / "root"
    (root / "readable").mkdir(parents=True)
    (root / "readable" / "hit.txt").write_text("NEEDLE\n", encoding="utf-8")

    locked = root / "locked"
    locked.mkdir()
    (locked / "hidden.txt").write_text("NEEDLE\n", encoding="utf-8")

    try:
        os.chmod(locked, 0o000)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"cannot chmod a directory on this platform: {exc!r}")

    # PREMISE CHECK. This is the assertion that keeps the test honest on Windows and as root.
    try:
        os.listdir(locked)
    except PermissionError:
        return root
    else:
        os.chmod(locked, 0o700)
        pytest.skip(
            "chmod 0o000 did not make the directory unreadable to this process "
            f"(listdir succeeded; mode is now {oct(locked.stat().st_mode & 0o777)}). "
            "Expected on Windows and when running as root -- the premise cannot be probed here, "
            "so passing would prove nothing."
        )
        raise AssertionError("unreachable")  # pragma: no cover


def _rg() -> str:
    binary = shutil.which("rg")
    if binary is None:
        pytest.skip("ripgrep is not installed; this test is a statement about rg's contract")
    return binary


def test_rg_json_stream_carries_no_incompleteness_signal(tmp_path: pathlib.Path) -> None:
    """ARM 1: rg's JSON stream stays silent about the directory it could not read."""
    root = _make_unreadable_tree(tmp_path)
    try:
        proc = subprocess.run(
            [_rg(), "--json", "NEEDLE", str(root)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.chmod(root / "locked", 0o700)

    messages = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert messages, f"rg emitted no JSON at all (exit {proc.returncode}): {proc.stderr[:400]}"

    # PREMISE: the scan must have actually found the readable hit, or we are asserting the
    # absence of a signal in a stream that never searched anything.
    assert any(m.get("type") == "match" for m in messages), (
        "rg found no matches -- the readable half of the tree was not searched, so this run "
        f"cannot show anything about incompleteness reporting. stderr: {proc.stderr[:400]}"
    )

    summaries = [m for m in messages if m.get("type") == _TERMINAL_MESSAGE_TYPE]
    assert len(summaries) == 1, f"expected exactly one summary message, got {len(summaries)}"

    keys = {k.lower() for k in _keys_recursive(summaries[0])}
    leaked = sorted(k for k in keys if any(marker in k for marker in _INCOMPLETENESS_MARKERS))
    assert not leaked, (
        "ripgrep's JSON summary now carries what looks like an incompleteness signal "
        f"({leaked}). If that is real, the task 307 premise is DEAD: rg can disclose an "
        "incomplete scan in-band, tg no longer leads on that channel, and the plan in "
        "docs/plans/2026-07-26-307-trust-benchmark-lead.md must be withdrawn."
    )


def test_rg_reports_the_unreadable_directory_on_its_only_available_channel(
    tmp_path: pathlib.Path,
) -> None:
    """ARM 2 -- THE CONTROL, and the half that makes ARM 1 a design claim rather than a smear.

    Without this, ARM 1 is satisfied by an rg that noticed nothing at all, and the test would
    read as "rg is careless". It is not: rg DOES report the unreadable directory, loudly, on
    stderr and via exit status 2. The point of task 307 is only that those two channels are not
    machine-readable output -- a consumer parsing the JSON stream alone cannot see them.
    """
    root = _make_unreadable_tree(tmp_path)
    try:
        proc = subprocess.run(
            [_rg(), "--json", "NEEDLE", str(root)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        os.chmod(root / "locked", 0o700)

    assert proc.returncode == 2, (
        "rg should exit 2 on a soft error (unreadable path) per rg(1) EXIT STATUS; got "
        f"{proc.returncode}. If this changed, the benchmark's exit-code column is measuring "
        "something different than it was written for."
    )
    assert proc.stderr.strip(), "rg exited 2 but said nothing on stderr"
