"""A command that exits ``2`` must have SAID something on stdout.

Thirteen of the fourteen ``_scan_incomplete`` gates in ``cli/main.py`` raised ``typer.Exit(2)``
over text output that read exactly like a complete result -- ``codemap`` was the only one that
disclosed. An agent branching on the exit code was fine; every human, and every agent reading the
text, was told a truncated answer was the whole answer.

Two tests, doing different jobs:

* the RATCHET reads the source and pins that every gate's text branch emits the banner, keyed on
  the SAME payload variable the gate reads -- a static property that no behavioural test would
  cover for commands whose fixtures are expensive to build;
* the behavioural tests drive real commands, so the ratchet cannot pass over a helper that emits
  nothing.

The ratchet's variable check is not hypothetical. The first cut of this change was applied by a
script that inserted ``_emit_scan_incompleteness_banner(payload)`` into every text branch, and
three daemon fast paths gate on ``daemon_payload`` -- so those three sites disclosed the state of
the wrong object. A sweep is a hypothesis until each site is read; this test is that reading, made
permanent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import typer

from tensor_grep.cli import main as main_mod
from tensor_grep.cli.main import _emit_scan_incompleteness_banner

_MAIN = Path(main_mod.__file__)
_CALL = "_emit_scan_incompleteness_banner"


def _truncated() -> dict[str, Any]:
    return {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
    }


# ------------------------------------------------------------------------------- the ratchet


def test_every_exit_two_gate_has_a_disclosure_on_its_text_branch() -> None:
    lines = _MAIN.read_text(encoding="utf-8").splitlines()
    gates = []
    for i, line in enumerate(lines):
        if not line.strip().startswith("if "):
            continue
        m = re.search(r"_scan_incomplete\((\w+)\)", line)
        if not m:
            continue
        # Skip the helper's OWN guard -- it is the disclosure, not a gate needing one. Found by
        # this test flagging line ~11308 on its first run, which is the ratchet catching itself.
        enclosing = next((lines[j] for j in range(i, 0, -1) if lines[j].startswith("def ")), "")
        if _CALL in enclosing:
            continue
        gates.append((i, m.group(1)))
    # PREMISE: the gates still exist and are plural. If a refactor renamed them this test would
    # otherwise pass over an empty list -- a ratchet that quietly covers nothing still reads green.
    assert len(gates) >= 10, f"expected the exit-2 gate family, found {len(gates)}"

    undisclosed = []
    for idx, var in gates:
        # Scoped to the ENCLOSING FUNCTION, not a fixed line window. A 40-line window flagged
        # `prepare`, whose banner is correctly placed but sits ~50 lines above its gate with the
        # capsule-writing block in between. An arbitrary window makes the ratchet's verdict depend
        # on unrelated code length -- a false positive whose obvious cure is to widen the window
        # until it stops complaining, which is how a ratchet quietly stops ratcheting.
        start = next((j for j in range(idx, 0, -1) if lines[j].startswith("def ")), 0)
        window = "\n".join(lines[start:idx])
        if f"{_CALL}({var})" in window:
            continue
        # `codemap` discloses through its own older `PARTIAL:` line; it is not silent, and
        # double-disclosing would be worse than either shape. Its POSITION (trailing) is a
        # separate, tracked defect and deliberately not changed here.
        if "PARTIAL:" in window:
            continue
        undisclosed.append((idx + 1, var))
    assert not undisclosed, (
        f"these exit-2 gates have no disclosure on their text branch: {undisclosed}. "
        f"Add {_CALL}(<the same var the gate reads>) at the TOP of the `else` of the "
        "`if json_output` fork -- never beside the json.dumps, which would break json.loads."
    )


def test_the_banner_is_never_emitted_on_a_json_branch() -> None:
    # The one way this change could break a machine consumer: a stray line on stdout ahead of a
    # JSON document. Pinned structurally -- the call must not appear between `if json_output:` and
    # the `else:` that closes it.
    lines = _MAIN.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if _CALL not in line or line.strip().startswith(("#", "*", '"')):
            continue
        if "def " in line:
            continue
        indent = len(line) - len(line.lstrip())
        for j in range(i - 1, max(0, i - 25), -1):
            stripped = lines[j].strip()
            cur = len(lines[j]) - len(lines[j].lstrip())
            if cur < indent and stripped.endswith(":"):
                if stripped.startswith("if json_output"):
                    offenders.append(i + 1)
                break
    assert not offenders, f"banner emitted on a --json branch at lines {offenders}"


# -------------------------------------------------------------------------- behaviour (unit)


def test_the_helper_emits_a_leading_warning_for_a_truncated_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emitted = _emit_scan_incompleteness_banner({"scan_limit": _truncated()})
    out = capsys.readouterr().out
    assert emitted is True
    assert out.startswith("warning: INCOMPLETE RESULT:")


def test_the_helper_is_silent_and_returns_false_for_a_complete_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # CONTROL ARM: without it, a helper that printed unconditionally would satisfy every other
    # assertion in this file and change output on every complete run.
    emitted = _emit_scan_incompleteness_banner({"files": ["a.py"]})
    assert emitted is False
    assert capsys.readouterr().out == ""


def test_map_discloses_a_truncated_scan_before_its_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tensor_grep.cli import repo_map

    payload = {
        "path": str(tmp_path),
        "files": ["a.py"],
        "tests": [],
        "symbols": [],
        "imports": [],
        "scan_limit": _truncated(),
    }
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *a, **k: dict(payload), raising=True)
    with pytest.raises(typer.Exit) as exc:
        # EVERY parameter passed explicitly: an unpassed typer option arrives as an `OptionInfo`
        # sentinel, not its default, and the command dies on `int(OptionInfo)` -- an error, not a
        # red arm, and one that says nothing about the disclosure under test.
        main_mod.map(
            path=str(tmp_path),
            max_files=None,
            max_repo_files=512,
            deadline=None,
            no_deadline=False,
            json_output=False,
        )
    assert exc.value.exit_code == 2  # premise: this really is the exit-2 path
    out = capsys.readouterr().out
    assert "Repository map for" in out  # premise: the counts block rendered
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")
    assert out.index("warning:") < out.index("Repository map for")
