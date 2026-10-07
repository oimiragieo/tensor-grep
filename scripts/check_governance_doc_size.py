"""Governance document size budget ratchet.

Prevents silent unbounded expansion of central governance files.
Sizes are measured in UTF-8 bytes and line count.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Public contributor documentation size limits.
PINNED_BUDGETS: dict[str, dict[str, int]] = {
    "AGENTS.md": {"max_bytes": 420_000, "max_lines": 4_100},
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
