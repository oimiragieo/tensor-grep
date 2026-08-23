"""Enterprise file-size budget: census, classification, and ratchet enforcement.

The standard (CEO, 2026-08-19):

    data contracts / schemas / interface files   <=  500 lines
    core engine / business-logic modules         <= 1500 lines
    unit-test files                              <= 2000 lines
    mock / stub / fixture / factory files        <= 2000 lines

WHY THIS FILE EXISTS
--------------------
Before it, the standard had *no enforcing mechanism* anywhere in CI or the test
suite (verified 2026-08-19 by grep, with a positive control proving the grep
could find the repo's other governance gates). A standard with no mechanism is
not a standard; it is a wish. House doctrine is to convert a repeatedly violated
rule into a mechanism rather than restating it in prose.

THE RATCHET
-----------
Twenty-plus files already exceed their budget. A hard gate would red `main`
instantly and be reverted within the hour, so the pre-existing violations are
pinned in ``file_size_allowlist.json`` at their measured line count. From that
baseline the gate is *fail-closed in both directions*:

  * a NON-allowlisted file over its limit           -> FAIL (no new violations)
  * an allowlisted file ABOVE its pinned baseline   -> FAIL (cannot get worse)
  * an allowlisted path no longer over its limit    -> FAIL (retire the entry)
  * an allowlisted path that no longer exists       -> FAIL (stale exception)

The third and fourth rules are what stop the allowlist from becoming a dumping
ground: an entry cannot outlive the violation it documents. Shrinking a file is
therefore a two-part change -- the code split AND the allowlist update -- which
makes every refactor wave show up as a provable decrease in a pinned number.

Counting is over ``git ls-files`` output, never a directory walk: this repo
carries 54 worktrees plus untracked scratch that would otherwise pollute the
census.

Physical lines are counted, including comments and blanks, per the standard.
Compressed formatting cannot buy headroom here -- ruff enforces line-length 100
repo-wide, and the giants measure 37-43 chars/line average, so there is no
line-count arbitrage available.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = Path(__file__).resolve().parent / "file_size_allowlist.json"

CONTRACT_LIMIT = 500
CORE_LIMIT = 1500
TEST_LIMIT = 2000
FIXTURE_LIMIT = 2000

LIMITS = {
    "contract": CONTRACT_LIMIT,
    "core": CORE_LIMIT,
    "test": TEST_LIMIT,
    "fixture": FIXTURE_LIMIT,
}

# Files whose PURPOSE is to define a data contract / wire schema / interface.
# Explicit, because "contract" is a role, not a path pattern -- a heuristic here
# would silently reclassify business logic into the strictest tier.
_CONTRACT_PATHS = frozenset({
    "src/tensor_grep/core/result.py",
    "src/tensor_grep/cli/rg_contract.py",
})

_SOURCE_SUFFIXES = frozenset({".py", ".rs"})

# Governance docs (AGENTS.md, CLAUDE.md, docs/BACKLOG.md, docs/TASK_BOARD.md) are
# REPORT-ONLY, never gated -- see governance_doc_census() below for why.
GOVERNANCE_DOC_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/BACKLOG.md",
    "docs/TASK_BOARD.md",
)


@dataclass(frozen=True)
class FileRecord:
    path: str
    category: str
    lines: int

    @property
    def limit(self) -> int:
        return LIMITS[self.category]

    @property
    def over(self) -> bool:
        return self.lines > self.limit


def classify(path: str) -> str | None:
    """Return the budget category for ``path``, or None if it is out of scope.

    Order matters: fixture before test (a fixture living under tests/ is a
    fixture), and contract before core (a contract living under src/ is a
    contract).
    """
    if path in _CONTRACT_PATHS or path.endswith(".schema.json"):
        return "contract"
    if path.startswith("tests/fixtures/") or path.endswith("/conftest.py"):
        return "fixture"
    if path.startswith("tests/") or path.startswith("rust_core/tests/"):
        return "test"
    if (
        path.startswith("src/")
        or path.startswith("rust_core/src/")
        or path.startswith("scripts/")
        or path.startswith("benchmarks/")
    ):
        return "core"
    return None


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def governance_doc_census(files: list[str] | None = None) -> list[tuple[str, int, int]]:
    """(path, lines, bytes) for each GOVERNANCE_DOC_PATHS entry that is git-tracked.

    WHY REPORT-ONLY, NOT GATED
    ---------------------------
    AGENTS.md and docs/BACKLOG.md are APPEND-ONLY BY DESIGN: AGENTS.md's dated
    instrument laws (A1, A2, ...) and BACKLOG.md's council receipts accumulate
    forever as a permanent, citable record -- that is the whole point of them.
    The ratchet above exists to make files SHRINK; pointing it at a doc whose
    house rule is "never edit history, only append" would forbid the exact
    growth the doc is required to do, and the first honest append would FAIL
    the gate ("allowlisted file above its pinned baseline"). Adding these paths
    to file_size_allowlist.json would not fix that -- it would just make the
    ratchet lie about ever holding, since every future append is expected to
    grow past whatever baseline got pinned.

    So this function exists purely to make the SIZE VISIBLE (a `--docs-report`
    flag, never wired into the pass/fail exit code) so a human -- not this
    script -- can decide when a doc has grown enough to need trimming/splitting.
    That threshold is a CEO decision, not something this ratchet should infer.

    Only git-tracked docs are counted, reusing _tracked_files()/_count_lines()
    exactly as census() does, so an untracked worktree artifact sharing one of
    these filenames can never be reported as if it were the real governance doc.
    """
    tracked = set(files if files is not None else _tracked_files())
    results: list[tuple[str, int, int]] = []
    for rel in GOVERNANCE_DOC_PATHS:
        if rel not in tracked:
            continue
        absolute = REPO_ROOT / rel
        if not absolute.is_file():
            continue
        results.append((rel, _count_lines(absolute), absolute.stat().st_size))
    return results


def _render_governance_doc_report(rows: list[tuple[str, int, int]]) -> str:
    if not rows:
        return (
            "NO TRACKED DOCS MATCHED -- probe found nothing (not the same as 'the docs are small')."
        )
    lines = [
        "governance doc size (REPORT-ONLY, never gates CI -- see governance_doc_census docstring):",
        "",
    ]
    for path, doc_lines, doc_bytes in rows:
        lines.append(f"  {doc_lines:>6} lines  {doc_bytes:>8} bytes  {path}")
    return "\n".join(lines)


def census(files: list[str] | None = None) -> list[FileRecord]:
    """Classified line-count census over git-tracked, in-scope source files."""
    records: list[FileRecord] = []
    for rel in files if files is not None else _tracked_files():
        if Path(rel).suffix not in _SOURCE_SUFFIXES and not rel.endswith(".schema.json"):
            continue
        category = classify(rel)
        if category is None:
            continue
        absolute = REPO_ROOT / rel
        if not absolute.is_file():
            continue
        records.append(FileRecord(rel, category, _count_lines(absolute)))
    return records


def violations(records: list[FileRecord]) -> dict[str, FileRecord]:
    return {r.path: r for r in records if r.over}


def load_allowlist() -> dict[str, int]:
    if not ALLOWLIST_PATH.exists():
        return {}
    raw = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in raw.get("grandfathered", {}).items()}


def evaluate(records: list[FileRecord], allowlist: dict[str, int]) -> list[str]:
    """Return failure messages. Empty list means the ratchet holds.

    Fail-closed in BOTH directions -- see the module docstring.
    """
    failures: list[str] = []
    live = violations(records)
    by_path = {r.path: r for r in records}

    for path, record in sorted(live.items()):
        if path not in allowlist:
            failures.append(
                f"NEW VIOLATION: {path} is {record.lines} lines, over the "
                f"{record.category} limit of {record.limit}. Split it, or if this "
                f"is genuinely unavoidable, add it to {ALLOWLIST_PATH.name} with a "
                f"written justification -- which requires review."
            )
            continue
        pinned = allowlist[path]
        if record.lines > pinned:
            failures.append(
                f"RATCHET REGRESSION: {path} grew to {record.lines} lines, above "
                f"its pinned baseline of {pinned}. An allowlisted file may shrink, "
                f"never grow. Reduce it, or split it."
            )

    for path, pinned in sorted(allowlist.items()):
        if path not in by_path:
            failures.append(
                f"STALE EXCEPTION: {path} is allowlisted at {pinned} lines but is "
                f"no longer a tracked in-scope file. Remove the entry."
            )
        elif path not in live:
            record = by_path[path]
            failures.append(
                f"RETIRE EXCEPTION: {path} is now {record.lines} lines, within its "
                f"{record.category} limit of {record.limit}. Remove it from "
                f"{ALLOWLIST_PATH.name} -- an exception may not outlive the "
                f"violation it documents."
            )

    return failures


def _render_report(records: list[FileRecord], allowlist: dict[str, int]) -> str:
    live = violations(records)
    lines = [
        f"scanned {len(records)} in-scope tracked files",
        f"violations: {len(live)}   grandfathered: {len(allowlist)}",
        "",
    ]
    for path, record in sorted(live.items(), key=lambda kv: -kv[1].lines):
        pinned = allowlist.get(path)
        state = f"pinned={pinned}" if pinned is not None else "UNPINNED"
        lines.append(
            f"  {record.lines:>6}  limit={record.limit:<5} {record.category:<9} {state:<12} {path}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Enterprise file-size budget gate.")
    parser.add_argument(
        "--write-allowlist",
        action="store_true",
        help="Regenerate the allowlist from the live census. Review the diff.",
    )
    parser.add_argument("--report", action="store_true", help="Print the census.")
    parser.add_argument(
        "--docs-report",
        action="store_true",
        help="Print governance-doc sizes (report-only; never affects the exit code).",
    )
    args = parser.parse_args(argv)

    if args.docs_report:
        print(_render_governance_doc_report(governance_doc_census()))

    records = census()

    if args.write_allowlist:
        live = violations(records)
        payload = {
            "_comment": (
                "Grandfathered file-size violations, pinned at their measured line "
                "count. Fail-closed both ways: an entry may only shrink, and must be "
                "REMOVED once the file is within its limit. Regenerate with "
                "`python scripts/file_size_budget.py --write-allowlist`, and review "
                "the diff -- a growing number here is a governance failure."
            ),
            "limits": LIMITS,
            "grandfathered": {p: r.lines for p, r in sorted(live.items())},
        }
        ALLOWLIST_PATH.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {ALLOWLIST_PATH} with {len(live)} grandfathered entries")
        return 0

    allowlist = load_allowlist()
    if args.report:
        print(_render_report(records, allowlist))

    failures = evaluate(records, allowlist)
    if failures:
        print("\nFILE-SIZE BUDGET FAILURES:\n")
        for failure in failures:
            print(f"  - {failure}\n")
        return 1
    print(
        f"file-size budget OK: {len(records)} files scanned, "
        f"{len(allowlist)} grandfathered, 0 regressions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
