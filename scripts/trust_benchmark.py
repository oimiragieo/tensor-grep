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
        # NOTE (2026-07-26, #307): plain `tg search` is NOT tg's own engine. bootstrap.py:1497
        # forwards it to real rg via `_run_rg_passthrough` whenever no compiled native binary
        # resolves (bootstrap.py:1264-1272) -- the literal `rg: ` prefix in this row's stderr
        # appears nowhere in our source, while tg's own walk error is `eprintln!("tg: {err}")`
        # (native_search.rs:1253). This row is kept because it is what a plain user runs, but a
        # tie between it and the `rg` row below is DEFINITIONAL, not a result. The `tg --json`
        # rows are the ones that measure tg.
        ToolSpec("tg (text; forwards to rg)", ["tg", "search", SENTINEL, "."]),
        ToolSpec("tg --json", ["tg", "search", "--json", SENTINEL, "."]),
        ToolSpec("tg --ndjson", ["tg", "search", "--ndjson", SENTINEL, "."]),
        ToolSpec("rg", ["rg", SENTINEL, "."]),
        ToolSpec("rg --json", ["rg", "--json", SENTINEL, "."]),
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


# The machine-readable incompleteness vocabulary a PAYLOAD consumer can branch on. Mirrors
# tensor_grep.cli.incompleteness.INCOMPLETENESS_MARKERS; duplicated on purpose because this
# harness must score COMPETING tools by the same rule, and importing the tool under test would
# make tg's row depend on tg's own module.
# EXACTLY the closed incompleteness vocabulary, and nothing looser. The first cut also listed
# `"errors"` and `paths`, and both were FALSE POSITIVES that flattered the tools:
#   - `paths` matched `matched_file_paths`, a key present in every COMPLETE `tg --json` directory
#     envelope, so tg scored ADMITS on a channel where it is in fact silent.
#   - `"errors"` is always present in semgrep's JSON, so semgrep scored ADMITS unconditionally.
# A marker that matches complete output cannot distinguish complete from incomplete, which is the
# only thing this column exists to do. The control arm below now makes that failure self-evident
# instead of leaving it to be noticed by someone who happens to distrust a good-looking number.
_PAYLOAD_MARKERS = ("result_incomplete", "incomplete_reason_class")


def _score(rc: int, out: str, err: str, unreadable_name: str) -> tuple[int, str]:
    """Score one run from the PROCESS channel: exit code + stderr.

    This is what a shell or CI consumer sees. It is deliberately NOT the whole story -- see
    `_score_payload`, which scores what an agent piping stdout into `jq` sees. Keeping them
    separate is the point: this scorer alone cannot distinguish "told me on stderr" from "told
    me in the payload", and that distinction is the entire enterprise thesis (#276).
    """
    named = unreadable_name and unreadable_name in err
    if rc not in (0, 1):
        return (ADMITS, f"exit {rc}" + (" + names path" if named else ""))
    if named:
        return (ADMITS, f"exit {rc}, names path on stderr")
    if err.strip():
        return (PARTIAL, f"exit {rc}, unattributed stderr: {err.strip()[:60]!r}")
    return (SILENT, f"exit {rc}, no stderr")


def _score_payload(_rc: int, out: str, _err: str, unreadable_name: str) -> tuple[int, str]:
    """Score STDOUT ALONE -- no exit code, no stderr. The agent's-eye view.

    An agent piping `--json` into `jq` never sees an exit code and never sees stderr. If the
    payload cannot say "this answer is incomplete", that consumer cannot distinguish "no matches"
    from "I could not finish looking".

    This column DISCRIMINATES ON DAY ONE and it currently scores AGAINST us: `rg --json` admits
    via exit 2 on the process channel but is SILENT here, and `tg --json` is SILENT on BOTH --
    i.e. tg is behind, not tied. That is the honest baseline #276 has to move, and recording the
    deficit before the fix is what will make the eventual win credible rather than post-hoc.
    """
    if any(marker in out for marker in _PAYLOAD_MARKERS):
        return (ADMITS, "stdout carries a machine-readable incompleteness marker")
    if unreadable_name and unreadable_name in out:
        return (PARTIAL, "stdout names the path but carries no structured field")
    return (SILENT, "stdout indistinguishable from a complete result")


def _make_tree(root: Path) -> None:
    """The sentinel is written as an IDENTIFIER, never inside a string literal.

    The first version wrote ``x = 'SENTINEL'``. The text tools found it; ast-grep and semgrep did
    not, and both FAILED THE CONTROL -- an AST pattern matches an identifier NODE, not the bytes
    inside a string. That was a defect in the FIXTURE presenting as a defect in two tools, which
    is the most misleading thing a comparative benchmark can do. As a bare identifier all six can
    find it: the text tools match the characters, the AST tools match the node.
    """
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "visible.py").write_text(f"{SENTINEL} = 1\n", encoding="utf-8")
    (root / "pkg" / "other.py").write_text("unrelated = 2\n", encoding="utf-8")


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
        # `(OI)(CI)` are OBJECT/CONTAINER INHERIT flags -- they describe what a DIRECTORY hands
        # down to its children and are meaningless on a file. Applying them to a file left it
        # fully readable, so the unreadable-file condition reported BROKEN FIXTURE for every
        # tool and measured nothing. Plain `(R)` is what denies a single file.
        spec = f"{user}:(OI)(CI)(R)" if path.is_dir() else f"{user}:(R)"
        rc, _, _ = _run(["icacls", str(path), "/deny", spec], path.parent)
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
                    # THE PAYLOAD CONTROL. Score the payload scorer against a COMPLETE search too.
                    # A complete result must look complete: anything but SILENT here means a marker
                    # matches ordinary output, so the incomplete column is measuring nothing. This
                    # arm is what turns "I noticed a suspicious 2" into an automatic failure --
                    # the first version of this file had no control and shipped two false-positive
                    # markers because of it.
                    pay_score, pay_detail = _score_payload(rc, out, err, "")
                    control_cell = result.cells.setdefault(f"{tool.name}  [stdout-only]", {})
                    control_cell[name] = Cell(
                        pay_score,
                        "ok" if pay_score == SILENT else "control-failed",
                        pay_detail
                        if pay_score == SILENT
                        else f"BROKEN MARKER: complete output scored {pay_score} -- {pay_detail}",
                    )
                    continue
                # TWO channels, deliberately. `_score` is what a shell/CI consumer sees (exit
                # code + stderr); `_score_payload` is what an agent piping stdout into `jq` sees.
                # Reporting only the first is what let "tg ties rg" stand for a whole session --
                # both tools exit 2 and both write stderr, so that channel alone cannot see the
                # difference #276 is being built to create.
                score, detail = _score(rc, out, err, hidden_name or "")
                cell[name] = Cell(score, "ok", detail)
                pay_score, pay_detail = _score_payload(rc, out, err, hidden_name or "")
                payload_cell = result.cells.setdefault(f"{tool.name}  [stdout-only]", {})
                payload_cell[name] = Cell(pay_score, "ok", pay_detail)
        finally:
            cleanup()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _cond_unreadable_dir(root: Path):
    secret = root / "pkg" / "locked_dir"
    secret.mkdir()
    (secret / "hidden.py").write_text(f"{SENTINEL} = 3\n", encoding="utf-8")
    if not _deny_read(secret) or not _premise_unreadable(secret):
        _undeny_read(secret)
        return (None, lambda: None)
    return (secret.name, lambda: _undeny_read(secret))


def _cond_unreadable_file(root: Path):
    target = root / "pkg" / "locked_file.py"
    target.write_text(f"{SENTINEL} = 4\n", encoding="utf-8")
    if not _deny_read(target) or not _premise_unreadable(target):
        _undeny_read(target)
        return (None, lambda: None)
    return (target.name, lambda: _undeny_read(target))


# REMOVED 2026-07-26 (#302): the `vanished-file` condition. Kept as a comment so nobody re-adds it
# without reading this.
#
# It created `pkg/ghost.py` and `unlink()`ed it BEFORE any tool ran. Every tool then walked a tree
# where the file simply did not exist, correctly reported nothing about it -- and was scored 0
# ("SILENT: exit 0, nothing on stderr, smaller result set"). So a tool doing exactly the right
# thing earned the worst score, and all six tied at the floor on both platforms.
#
# That is two defects, not one. It DISCRIMINATED NOTHING (a column where every arm ties separates
# no tools -- yet six zeros read to a human as "they are all bad at this", which the data does not
# support), and it MISLABELLED correct behaviour as dishonesty. A tied-at-floor column is worse
# than no column, because it looks like a finding. This is verification-oracle Form 7 (AGENTS.md)
# turned on our own scorecard: *what would this column show if a tool were GOOD at it?* -- still 0.
#
# Could it be fixed instead of dropped? Only by making the file vanish DURING each tool's walk,
# between the directory listing and the open. Every tool here is a separate subprocess with its own
# walk order and timing, so that window cannot be opened deterministically for six of them at once
# -- the same reason AGENTS.md A27 forbids wall-clock-overlap assertions in the concurrency tests.
# A flaky column is not an improvement on a meaningless one.
#
# If someone genuinely needs TOCTOU coverage, it belongs in a single-tool test with an injected
# filesystem hook, not in a cross-tool scorecard.

CONDITIONS = [
    ("unreadable-dir", _cond_unreadable_dir),
    ("unreadable-file", _cond_unreadable_file),
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
    # Render EVERY row that was scored, not just the ToolSpec names. The `[stdout-only]` payload
    # rows are keyed `f"{tool.name}  [stdout-only]"`, which is not a ToolSpec name -- iterating
    # `tools` alone computed them, stored them, and printed none of them. A score nobody can see
    # cannot fail, which is precisely the defect class this benchmark exists to measure (#276).
    # The JSON output already carried them; only the human-readable table, the thing anyone
    # actually reads, was blind.
    row_names = [name for tool in tools for name in (tool.name, f"{tool.name}  [stdout-only]")]
    row_names = [n for n in row_names if n in result.cells]
    width = max(len(n) for n in row_names) + 2
    print(f"\nTrust benchmark -- {result.platform}")
    print("2=ADMITS 1=PARTIAL 0=SILENT -=N/A  (honest == 2)")
    print("`[stdout-only]` rows score STDOUT ALONE -- the agent's-eye view, no exit code,")
    print("no stderr. That is the channel #276 is about.\n")
    print("tool".ljust(width) + "".join(c.ljust(18) for c in cond_names))
    for row_name in row_names:
        row = result.cells.get(row_name, {})
        line = row_name.ljust(width)
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
    for row_name in row_names:
        for c, cell in result.cells.get(row_name, {}).items():
            if c != "control" and cell.detail:
                print(f"  {row_name:>26} / {c:<16} {cell.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
