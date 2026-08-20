"""Schema checker for the W3-a beyond-Route-A costing document.

WHY THIS EXISTS
---------------
The plan's r1 acceptance command for W3-a was `test -f <doc>` plus a one-token grep.
A document containing no costing at all passes that. This checker fails on a missing
row, an empty field, a `derivation_command` that is not a runnable command string, a
missing counter-argument, or zero/multiple RECOMMENDATION lines.

It is a SHAPE check, not a truth check: it cannot tell you a number is right, only that
the number is present, attributed to a command, and that no (module, option) cell of the
3x3 grid is silently missing. Stating which way it errs: it is PERMISSIVE -- a fabricated
number behind a syntactically-runnable command passes.

FORMAT
------
Nine rows, each:

    #### ROW n: <module> / <option>
    - module: ...
    - option: ...
    ... (11 fields, in any order, each non-empty)

Plus exactly one line beginning `RECOMMENDATION:` and one beginning `COUNTER-ARGUMENT:`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FIELDS = [
    "module",
    "option",
    "cone_lines",
    "candidate_seams",
    "affected_tests",
    "affected_callers",
    "estimated_edits",
    "estimated_ci_rounds",
    "risk",
    "expected_residual_floor",
    "derivation_command",
]
MODULES = {
    "src/tensor_grep/cli/main.py",
    "src/tensor_grep/cli/repo_map.py",
    "src/tensor_grep/cli/mcp_server.py",
}
OPTIONS = {"shrink-patched-set", "dependency-injection", "accept-the-pin"}
RUNNABLE = ("python ", "git ", "tg ")

ROW_RE = re.compile(r"^#### ROW (\d+): (.+?) / (.+?)\s*$")
FIELD_RE = re.compile(r"^- ([a-z_]+):\s*(.*)$")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/check_costing_doc.py <doc.md>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"FAIL: no such document: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        m = ROW_RE.match(line)
        if m:
            current = {}
            rows.append(current)
            continue
        if current is None:
            continue
        f = FIELD_RE.match(line)
        if f:
            current[f.group(1)] = f.group(2).strip()
        elif line.startswith("#"):
            current = None

    errors: list[str] = []
    if len(rows) != 9:
        errors.append(f"expected 9 rows (3 modules x 3 options), found {len(rows)}")
    seen: set[tuple[str, str]] = set()
    commands = 0
    for i, row in enumerate(rows, 1):
        for field in FIELDS:
            value = row.get(field, "")
            if not value:
                errors.append(f"row {i}: field '{field}' missing or empty")
        mod, opt = row.get("module", ""), row.get("option", "")
        if mod and mod not in MODULES:
            errors.append(f"row {i}: unknown module {mod!r}")
        if opt and opt not in OPTIONS:
            errors.append(f"row {i}: unknown option {opt!r}")
        if mod and opt:
            if (mod, opt) in seen:
                errors.append(f"row {i}: duplicate cell ({mod}, {opt})")
            seen.add((mod, opt))
        cmd = row.get("derivation_command", "")
        if cmd:
            if cmd.startswith("`") and cmd.endswith("`"):
                cmd = cmd[1:-1].strip()
            if not cmd.startswith(RUNNABLE) or len(cmd) < 12:
                errors.append(
                    f"row {i}: derivation_command is not a runnable command: {cmd[:60]!r}"
                )
            else:
                commands += 1
    missing = sorted({(m, o) for m in MODULES for o in OPTIONS} - seen)
    for mod, opt in missing:
        errors.append(f"missing cell: ({mod}, {opt})")

    recs = [ln for ln in lines if ln.startswith("RECOMMENDATION:")]
    counters = [ln for ln in lines if ln.startswith("COUNTER-ARGUMENT:")]
    if len(recs) != 1:
        errors.append(f"expected exactly 1 RECOMMENDATION line, found {len(recs)}")
    if len(counters) != 1:
        errors.append(f"expected exactly 1 COUNTER-ARGUMENT line, found {len(counters)}")

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(
        f"{len(rows)} rows (3 modules x 3 options), {len(FIELDS)}/{len(FIELDS)} fields present, "
        f"{commands} derivation commands, {len(recs)} RECOMMENDATION, {len(counters)} COUNTER-ARGUMENT"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
