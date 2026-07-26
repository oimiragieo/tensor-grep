#!/usr/bin/env python3
"""Comparative trust benchmark: does a search tool ADMIT what it could not read?

THE QUESTION. Not "is it fast" and not "is it correct on a clean tree" -- every tool here is
correct on a clean tree. The question is what happens when part of the tree is unreadable. A tool
that returns a smaller result set with a success exit code has told its caller a falsehood, and
the caller -- usually an agent -- acts on it: "no matches, safe to delete."

SCORING. Per (tool, condition) cell:
    2  ADMITS   -- a non-zero exit code OR a message naming the unreadable path
    1  PARTIAL  -- some signal, but not one a machine can key on (e.g. text on stderr with exit 0)
    0  SILENT   -- exit 0, nothing on stderr, smaller result set
    -  N/A      -- the condition cannot be constructed for this tool on this platform

A tool is HONEST on a condition at 2. Anything less is a caller who cannot tell truncation from
absence.

WHY THE PREMISE CHECKS MATTER MORE THAN THE SCORES. A hostile fixture that fails to actually
make a path unreadable turns every cell into a free 0 -- the tools all "pass" a test that was
never administered. So each condition asserts its own premise BEFORE any tool runs, and a
fixture that does not bite is reported as BROKEN-FIXTURE, never as a score. This is the failure
mode that silently invalidated an earlier run of this benchmark; it is why the control below is
not optional.

THE CONTROL. Every tool is first run against a fully readable tree. It MUST find the sentinel and
exit 0. A tool that "admits" on the control is not being honest, it is broken -- and its hostile
scores mean nothing. Cells whose control failed are reported as CONTROL-FAILED.

Run:  python scripts/trust_benchmark.py            # table to stdout
      python scripts/trust_benchmark.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SENTINEL = "TRUSTBENCH_SENTINEL_TOKEN"
TIMEOUT_S = 60

ADMITS, PARTIAL, SILENT = 2, 1, 0


@dataclass
class ToolSpec:
    name: str
    argv: list[str]
    #: Tools that only search tracked files need a git repo built around the fixture.
    needs_git: bool = False
    #: semgrep/ast-grep want a rule or pattern rather than a bare regex.
    note: str = ""


@dataclass
class Cell:
    score: int | None = None
    status: str = "ok"
    detail: str = ""


@dataclass
class Result:
    platform: str
    cells: dict[str, dict[str, Cell]] = field(default_factory=dict)
    broken_fixtures: list[str] = field(default_factory=list)


def _tools() -> list[ToolSpec]:
    return [
        ToolSpec("tg", ["tg", "search", SENTINEL, "."]),
        ToolSpec("rg", ["rg", SENTINEL, "."]),
        ToolSpec("GNU grep", ["grep", "-r", SENTINEL, "."]),
        ToolSpec("git grep", ["git", "grep", SENTINEL], needs_git=True),
        ToolSpec("ast-grep", ["ast-grep", "run", "-p", SENTINEL, "-l", "python", "."]),
        ToolSpec(
            "semgrep",
            ["semgrep", "--pattern", SENTINEL, "--lang", "python", "--quiet", "--json", "."],
            note="pattern mode; --quiet keeps the banner off stderr so a real error is visible",
        ),
    ]


def _run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except FileNotFoundError:
        return (127, "", "tool not found")
    except subprocess.TimeoutExpired:
        return (124, "", "timeout")
    except OSError as exc:  # pragma: no cover - platform-specific spawn failures
        return (125, "", f"spawn failed: {exc}")
    return (proc.returncode, proc.stdout, proc.stderr)


def _score(rc: int, _out: str, err: str, unreadable_name: str) -> tuple[int, str]:
    """Score one run. A non-zero exit OR a message naming the path is an admission."""
    named = unreadable_name and unreadable_name in err
    if rc not in (0, 1):
        return (ADMITS, f"exit {rc}" + (" + names path" if named else ""))
    if named:
        return (ADMITS, f"exit {rc}, names path on stderr")
    if err.strip():
        return (PARTIAL, f"exit {rc}, unattributed stderr: {err.strip()[:60]!r}")
    return (SILENT, f"exit {rc}, no stderr")


def _make_tree(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "visible.py").write_text(f"x = '{SENTINEL}'\n", encoding="utf-8")
    (root / "pkg" / "other.py").write_text("y = 1\n", encoding="utf-8")


def _init_git(root: Path) -> bool:
    for argv in (
        ["git", "init", "-q", "-b", "main", "."],
        ["git", "config", "user.email", "bench@example.com"],
        ["git", "config", "user.name", "bench"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "fixture"],
    ):
        rc, _, _ = _run(argv, root)
        if rc != 0:
            return False
    return True


def _deny_read(path: Path) -> bool:
    """Make ``path`` unreadable. Returns False when the platform refuses to cooperate."""
    if platform.system() == "Windows":
        user = os.environ.get("USERNAME", "")
        if not user:
            return False
        rc, _, _ = _run(["icacls", str(path), "/deny", f"{user}:(OI)(CI)(R)"], path.parent)
        return rc == 0
    try:
        path.chmod(0o000)
    except OSError:
        return False
    return True


def _undeny_read(path: Path) -> bool:
    """Restore access. Returns whether the path is readable again -- checked, not assumed.

    Two earlier versions of this harness lost their results in `rmtree`: the deny ACE survived
    `/remove:d` and then `/reset /T` (which needs to enumerate the very directory it is unlocking).
    Removing the deny AND granting full control, in that order, is what actually works -- and the
    post-condition below is why I know rather than hope.
    """
    if platform.system() == "Windows":
        user = os.environ.get("USERNAME", "")
        if user:
            _run(["icacls", str(path), "/remove:d", user], path.parent)
            _run(["icacls", str(path), "/grant", f"{user}:(OI)(CI)F"], path.parent)
    else:
        try:
            path.chmod(stat.S_IRWXU)
        except OSError:
            return False
    return not _premise_unreadable(path)


def _premise_unreadable(path: Path) -> bool:
    """THE fixture-bites check: can this process still read it? If yes, the fixture is inert."""
    try:
        if path.is_dir():
            list(path.iterdir())
        else:
            path.read_bytes()
    except OSError:
        return True
    return False


def run_condition(
    name: str, build, tools: list[ToolSpec], result: Result, *, control: bool = False
) -> None:
    # NOT TemporaryDirectory. This harness deliberately creates paths the OS will refuse to
    # delete, and `TemporaryDirectory.__exit__` still raises through `ignore_cleanup_errors`
    # because its `onexc` hook calls `chmod` on the very path that is denied. Two runs died
    # there with every result already computed and never printed -- the harness losing the
    # measurement it exists to produce. mkdtemp + `rmtree(ignore_errors=True)` cannot raise.
    tmp = tempfile.mkdtemp(prefix="trustbench-")
    try:
        root = Path(tmp) / "repo"
        root.mkdir()
        _make_tree(root)
        git_ok = _init_git(root)

        hidden_name, cleanup = build(root)
        try:
            if not control and hidden_name is None:
                result.broken_fixtures.append(f"{name}: could not construct on this platform")
                for tool in tools:
                    result.cells.setdefault(tool.name, {})[name] = Cell(
                        None, "n/a", "fixture unavailable"
                    )
                return

            for tool in tools:
                cell = result.cells.setdefault(tool.name, {})
                if tool.needs_git and not git_ok:
                    cell[name] = Cell(None, "n/a", "git fixture unavailable")
                    continue
                rc, out, err = _run(tool.argv, root)
                if rc == 127:
                    cell[name] = Cell(None, "n/a", "tool not installed")
                    continue
                if control:
                    found = SENTINEL in out
                    cell[name] = Cell(
                        None,
                        "ok" if (found and rc == 0) else "control-failed",
                        "found sentinel, exit 0" if found and rc == 0 else f"rc={rc} found={found}",
                    )
                    continue
                score, detail = _score(rc, out, err, hidden_name or "")
                cell[name] = Cell(score, "ok", detail)
        finally:
            cleanup()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _cond_unreadable_dir(root: Path):
    secret = root / "pkg" / "locked_dir"
    secret.mkdir()
    (secret / "hidden.py").write_text(f"z = '{SENTINEL}'\n", encoding="utf-8")
    if not _deny_read(secret) or not _premise_unreadable(secret):
        _undeny_read(secret)
        return (None, lambda: None)
    return (secret.name, lambda: _undeny_read(secret))


def _cond_unreadable_file(root: Path):
    target = root / "pkg" / "locked_file.py"
    target.write_text(f"z = '{SENTINEL}'\n", encoding="utf-8")
    if not _deny_read(target) or not _premise_unreadable(target):
        _undeny_read(target)
        return (None, lambda: None)
    return (target.name, lambda: _undeny_read(target))


def _cond_vanishing_file(root: Path):
    """TOCTOU: a file present at walk time and gone at read time. Approximated by deleting it
    after the tree is built -- the tools re-walk, so this mainly proves they do not report a
    phantom. Kept because a tool that CRASHES here is worse than one that skips."""
    target = root / "pkg" / "ghost.py"
    target.write_text(f"z = '{SENTINEL}'\n", encoding="utf-8")
    target.unlink()
    return (target.name, lambda: None)


CONDITIONS = [
    ("unreadable-dir", _cond_unreadable_dir),
    ("unreadable-file", _cond_unreadable_file),
    ("vanished-file", _cond_vanishing_file),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    tools = _tools()
    result = Result(platform=f"{platform.system()} {platform.release()}")

    # CONTROL FIRST. Hostile scores from a tool that cannot even find the sentinel on a clean
    # tree are meaningless, and reporting them would be the exact dishonesty this measures.
    run_condition("control", lambda r: (None, lambda: None), tools, result, control=True)
    for name, build in CONDITIONS:
        run_condition(name, build, tools, result)

    if args.json:
        payload = {
            "platform": result.platform,
            "broken_fixtures": result.broken_fixtures,
            "cells": {
                t: {
                    c: {"score": x.score, "status": x.status, "detail": x.detail}
                    for c, x in row.items()
                }
                for t, row in result.cells.items()
            },
        }
        print(json.dumps(payload, indent=2))
        return 0

    cond_names = ["control"] + [n for n, _ in CONDITIONS]
    width = max(len(t.name) for t in tools) + 2
    print(f"\nTrust benchmark -- {result.platform}")
    print("2=ADMITS 1=PARTIAL 0=SILENT -=N/A  (honest == 2)\n")
    print("tool".ljust(width) + "".join(c.ljust(18) for c in cond_names))
    for tool in tools:
        row = result.cells.get(tool.name, {})
        line = tool.name.ljust(width)
        for c in cond_names:
            cell = row.get(c)
            if cell is None:
                line += "-".ljust(18)
            elif cell.status == "control-failed":
                line += "CONTROL-FAILED".ljust(18)
            elif cell.status == "n/a":
                line += "-".ljust(18)
            elif c == "control":
                line += "ok".ljust(18)
            else:
                line += str(cell.score).ljust(18)
        print(line)

    if result.broken_fixtures:
        print("\nBROKEN FIXTURES (scored NOTHING -- the condition was never administered):")
        for b in result.broken_fixtures:
            print(f"  - {b}")
    print("\nDetail:")
    for tool in tools:
        for c, cell in result.cells.get(tool.name, {}).items():
            if c != "control" and cell.detail:
                print(f"  {tool.name:>10} / {c:<16} {cell.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
