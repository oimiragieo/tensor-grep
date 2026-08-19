"""Ratchet the bare calls that weld the three giant modules to their own file.

WHY THIS EXISTS
---------------
`cli/main.py`, `cli/repo_map.py` and `cli/mcp_server.py` cannot reach the 1,500-line limit by
moving code. Python resolves a bare name through the DEFINING module's globals, so a function
that calls a monkeypatched name as a bare identifier must stay physically co-located with the
module the tests patch. Measured, that locked closure is 4-7x the limit on its own
(`scripts/measure_split_floor.py`).

`docs/design/2026-08-19-split-floor-escape.md` picks Route A: convert those bare calls to late
attribute reads (`_self.X()`), after which the function is free to move. This script is that
design's **step 1** -- the gate that has to exist BEFORE any conversion, so a half-finished
conversion cannot be believed complete, and so a future edit cannot quietly reintroduce a bare
call into a module that has been cleaned.

WHAT IT ASSERTS
---------------
For each target module: the number of `ast.Call` nodes whose `func` is an `ast.Name` matching a
symbol the test suite patches on that module, pinned in `bare_call_pins.json`.

It is a RATCHET, and like `scripts/file_size_budget.py` it is fail-closed in BOTH directions:

    * a module ABOVE its pin          -> FAIL (a new bare call was introduced)
    * a module BELOW its pin          -> FAIL (progress must be banked by lowering the pin)
    * a module at 0 still pinned      -> FAIL (retire it; the conversion is done)
    * a pinned module that is gone    -> FAIL (stale entry)

Going DOWN failing is the unusual half and it is deliberate. A conversion that lowers the count
without lowering the pin leaves the gate accepting a range, and a range is exactly how a later
regression hides -- it re-introduces a bare call and the gate stays green because the number is
still under the old pin.

WHAT IT DOES NOT CLAIM
----------------------
Zero here does NOT mean the module is free to split. It means no BARE CALL to a patched name is
left. The locked set can still contain functions welded by other mechanisms this AST query does
not model -- class methods, closures, `global` rebinding, and modules loaded via
`spec_from_file_location`. Re-run `scripts/measure_split_floor.py` after a module reaches zero;
do not infer the floor from this number.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cost_split_floor_routes import (
    ROOT,
    TARGETS,
    bare_call_sites,
    patch_sites,
)

PINS_PATH = Path(__file__).resolve().parent / "bare_call_pins.json"


def load_pins(path: Path | None = None) -> dict[str, int]:
    p = path or PINS_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in raw.get("bare_calls", {}).items()}


def measure(targets: list[tuple[str, str]] | None = None) -> dict[str, int]:
    """Bare calls to patched symbols, per module. The measurement, with no policy."""
    counts: dict[str, int] = {}
    for rel, dotted in targets if targets is not None else TARGETS:
        path = ROOT / rel
        if not path.exists():
            continue
        symbols, _sites, _files = patch_sites(dotted)
        total, _per_symbol = bare_call_sites(path, symbols)
        counts[rel] = total
    return counts


def evaluate(counts: dict[str, int], pins: dict[str, int]) -> list[str]:
    """Return failure messages. Empty list means the ratchet holds."""
    failures: list[str] = []

    for rel, pinned in sorted(pins.items()):
        if rel not in counts:
            failures.append(
                f"STALE PIN: {rel} is pinned at {pinned} bare calls but the module is not in the "
                f"target set (deleted or renamed?). Remove the entry, or re-add the module to "
                f"TARGETS in scripts/cost_split_floor_routes.py."
            )
            continue
        actual = counts[rel]
        if actual > pinned:
            failures.append(
                f"RATCHET REGRESSION: {rel} has {actual} bare calls to monkeypatched symbols, "
                f"above its pin of {pinned}. A bare call to a patched name welds its function to "
                f"this file -- moving it elsewhere leaves the test passing while production runs "
                f"the unpatched original. Use `_self.NAME(...)` (see "
                f"docs/design/2026-08-19-split-floor-escape.md) instead of `NAME(...)`."
            )
        elif actual < pinned and actual > 0:
            failures.append(
                f"BANK THE PROGRESS: {rel} is down to {actual} bare calls from a pin of {pinned}. "
                f"Lower the pin in {PINS_PATH.name} to {actual}. A pin left above the real count "
                f"accepts a RANGE, and a later regression back up to {pinned} would not be caught."
            )
        elif actual == 0:
            failures.append(
                f"RETIRE THE ENTRY: {rel} has no bare calls to patched symbols left -- the Route A "
                f"conversion for this module is complete. Remove it from {PINS_PATH.name}; a "
                f"module at zero must not stay on a list of known offenders."
            )

    for rel, actual in sorted(counts.items()):
        if rel not in pins and actual > 0:
            failures.append(
                f"UNPINNED OFFENDER: {rel} has {actual} bare calls to patched symbols and no pin. "
                f"Add it to {PINS_PATH.name}, or convert it."
            )

    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report",
        action="store_true",
        help="print the per-module counts alongside the verdict",
    )
    args = ap.parse_args()

    counts = measure()
    pins = load_pins()
    failures = evaluate(counts, pins)

    if args.report:
        print(f"{'module':<40}{'bare calls':>12}{'pinned':>10}")
        print("-" * 62)
        for rel in sorted(set(counts) | set(pins)):
            print(f"{rel:<40}{counts.get(rel, '-'):>12}{pins.get(rel, '-'):>10}")
        print("-" * 62)

    if failures:
        print("BARE-CALL RATCHET FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    total = sum(counts.values())
    print(f"bare-call ratchet OK: {len(counts)} modules, {total} bare calls, 0 regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
