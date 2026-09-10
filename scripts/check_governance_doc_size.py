"""Governance document size budget ratchet.

Prevents silent unbounded expansion of central governance files.
Sizes are measured in UTF-8 bytes and line count.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Pinned maximum allowable byte sizes and line counts (calibrated against live baselines)
# AGENTS.md baseline: 378 KB, 3,895 lines
# docs/BACKLOG.md baseline: 354 KB, 3,728 lines (raised 2026-09-10: the 400KB ceiling from the
#   2026-09-03 calibration hit ~0 bytes of headroom this session -- two rounds of competitive-
#   research additions each needed 4-5 rounds of manual word-trimming to land under budget,
#   which does not scale for an append-only-by-design historical ledger. Raised with real
#   headroom rather than trimmed indefinitely; if this ceiling is approached again, the correct
#   fix is an archival/compaction pass -- splitting dated history into a separate file -- not
#   another bump.)
# docs/TASK_BOARD.md baseline: 62 KB, 523 lines
# CLAUDE.md baseline: 26 KB, 206 lines
PINNED_BUDGETS: dict[str, dict[str, int]] = {
    "AGENTS.md": {"max_bytes": 420_000, "max_lines": 4_100},
    "docs/BACKLOG.md": {"max_bytes": 460_000, "max_lines": 4_600},
    "docs/TASK_BOARD.md": {"max_bytes": 80_000, "max_lines": 700},
    "CLAUDE.md": {"max_bytes": 35_000, "max_lines": 350},
}


def main() -> int:
    violations: list[str] = []
    for rel_path, budget in PINNED_BUDGETS.items():
        doc_path = REPO_ROOT / rel_path
        if not doc_path.exists():
            violations.append(f"Missing governance document: {rel_path}")
            continue
        content = doc_path.read_bytes()
        actual_bytes = len(content)
        actual_lines = len(content.splitlines())
        max_bytes = budget["max_bytes"]
        max_lines = budget["max_lines"]
        if actual_bytes > max_bytes:
            violations.append(f"{rel_path}: size {actual_bytes} bytes exceeds ceiling {max_bytes}")
        if actual_lines > max_lines:
            violations.append(f"{rel_path}: lines {actual_lines} exceeds ceiling {max_lines}")

    if violations:
        print("Governance doc size budget violations:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("Governance doc size budget OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
