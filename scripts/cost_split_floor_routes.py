"""Cost the two ways out of the split floor, for the three files that cannot be split.

The split-floor measurement says `cli/main.py`, `cli/repo_map.py` and `cli/mcp_server.py`
cannot reach 1,500 lines by moving code, because 9,453 / 11,025 / 5,554 lines are welded
to the module tests patch. Two routes exist. Neither has been costed. This costs them.

ROUTE A -- convert bare-name calls to late attribute lookup.
    Inside the module, rewrite `helper(x)` as `_self.helper(x)` (or move the call behind a
    module-attribute read) so the binding is resolved at CALL time, not import time. Then
    the function is free to move: a test patching `module.helper` still wins.
    COST DRIVER: the number of bare CALL SITES inside the module that must change.

ROUTE B -- repoint the tests.
    Leave the code shape alone; move the function and update every test that patches it to
    patch the NEW module path.
    COST DRIVER: the number of TEST patch sites that must change.

Route A is one file per module and mechanically checkable; Route B is spread across the
test tree and each edit is a chance to silently point at the wrong module. This prints
both numbers so the choice is made on evidence rather than on which sounds tidier.
"""

from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from pathlib import Path

# Resolve the root from THIS FILE, never from an absolute path. A hardcoded cwd made
# every run measure one particular checkout no matter where it was invoked -- so running
# it inside a worktree silently reported the other tree's numbers, and on any other
# machine it would not run at all. `scripts/measure_split_floor.py` already did this
# correctly; the two siblings disagreed, and the wrong one produced the figures that
# reached the design doc.
ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
)

TARGETS = [
    ("src/tensor_grep/cli/main.py", "tensor_grep.cli.main"),
    ("src/tensor_grep/cli/repo_map.py", "tensor_grep.cli.repo_map"),
    ("src/tensor_grep/cli/mcp_server.py", "tensor_grep.cli.mcp_server"),
]


def tracked(pattern: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", pattern],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / p for p in out.split("\0") if p]


def parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def patch_sites(dotted: str) -> tuple[set[str], int, set[str]]:
    """(patched symbols, count of test patch sites, test files involved)."""
    symbols: set[str] = set()
    sites = 0
    files: set[str] = set()
    for path in tracked("tests/**/*.py"):
        tree = parse(path)
        if tree is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    aliases[a.asname or a.name] = a.name
        for node in ast.walk(tree):
            hit: str | None = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "setattr" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        mod, _, attr = first.value.rpartition(".")
                        if mod == dotted:
                            hit = attr
                    elif len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        if isinstance(first, ast.Name) and aliases.get(first.id) == dotted:
                            val = node.args[1].value
                            if isinstance(val, str):
                                hit = val
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                        if aliases.get(tgt.value.id) == dotted:
                            hit = tgt.attr
            if hit:
                symbols.add(hit)
                sites += 1
                files.add(rel)
    return symbols, sites, files


def bare_call_sites(path: Path, symbols: set[str]) -> tuple[int, dict[str, int]]:
    """How many BARE calls to patched symbols exist inside the module itself."""
    tree = parse(path)
    assert tree is not None
    per_symbol: dict[str, int] = defaultdict(int)
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in symbols:
                per_symbol[node.func.id] += 1
                total += 1
    return total, dict(per_symbol)


def main() -> int:
    print("COSTING THE TWO ROUTES OUT OF THE SPLIT FLOOR\n")
    print(f"{'module':<34}{'ROUTE A':>10}{'ROUTE B':>10}{'B files':>9}")
    print(f"{'':34}{'call sites':>10}{'patch sites':>10}{'':>9}")
    print("-" * 63)
    grand_a = grand_b = 0
    detail: list[tuple[str, dict[str, int]]] = []
    for rel, dotted in TARGETS:
        symbols, sites, files = patch_sites(dotted)
        a_total, per_symbol = bare_call_sites(ROOT / rel, symbols)
        grand_a += a_total
        grand_b += sites
        print(f"{rel.split('/')[-1]:<34}{a_total:>10}{sites:>10}{len(files):>9}")
        detail.append((rel, per_symbol))
    print("-" * 63)
    print(f"{'TOTAL':<34}{grand_a:>10}{grand_b:>10}")
    print(
        "\nROUTE A edits live in 3 source files and are mechanically verifiable "
        "(a bare call to a patched name is an AST query).\nROUTE B edits are spread "
        "across the test tree; each one can silently point at the wrong module."
    )
    print("\nHEAVIEST SYMBOLS PER MODULE (Route A work concentrates here):")
    for rel, per_symbol in detail:
        top = sorted(per_symbol.items(), key=lambda kv: -kv[1])[:5]
        if top:
            joined = ", ".join(f"{k} x{v}" for k, v in top)
            print(f"  {rel.split('/')[-1]}: {joined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
