"""Measure the SPLIT FLOOR of a module: how many lines are welded to it by test patches.

WHY
---
Wave 3 discovered that a file can be structurally UNABLE to reach a line limit by
splitting. Python resolves bare names through the DEFINING module's globals, so any
function that calls a monkeypatched name by bare identifier must stay physically
co-located with wherever the test's `setattr` lands -- the module the tests import.

For `benchmarks/run_gpu_native_benchmarks.py` that closure measured 1,752 lines across
17 functions, which alone exceeds the 1,500-line limit. Splitting could never have
worked, and discovering that AFTER dispatching an agent cost a full wave.

This script answers the question BEFORE a wave is scoped, for the two giants that the
campaign's remaining work depends on.

METHOD
------
1. Collect every symbol that tests patch on the target module (all three shapes:
   `setattr("dotted.path.X", ...)`, `setattr(mod, "X", ...)`, and `mod.X = ...`).
2. Walk the module's AST. A top-level function is IN THE CLOSURE if it references any
   patched symbol as a BARE NAME (`ast.Name`) -- an attribute access (`mod.X()`) is late
   binding and is free to move.
3. Transitively add any function that bare-calls a function already in the closure:
   moving a caller away from its callee breaks the same way.
4. Sum the physical line spans.

WHAT THIS DOES NOT CLAIM
------------------------
It is a LOWER BOUND on what must stay, not an upper bound on what can move. It does not
model class methods, closures, or `global` rebinding, and it cannot see modules loaded
via `spec_from_file_location`. A number here that already exceeds the limit is decisive
(the split cannot work); a number under the limit is encouraging, not a guarantee.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

LIMIT = 1500


def _repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent,
    ).stdout.strip()
    return Path(out)


ROOT = _repo_root()


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


def patched_symbols(dotted: str) -> set[str]:
    """Every attribute tests patch on `dotted`, across all three patch shapes."""
    found: set[str] = set()
    for path in tracked("tests/**/*.py"):
        tree = parse(path)
        if tree is None:
            continue
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    aliases[a.asname or a.name] = f"{node.module}.{a.name}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    aliases[a.asname or a.name] = a.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "setattr" or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    mod, _, attr = first.value.rpartition(".")
                    if mod == dotted:
                        found.add(attr)
                elif len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    attr = node.args[1].value
                    if isinstance(first, ast.Name) and aliases.get(first.id) == dotted:
                        if isinstance(attr, str):
                            found.add(attr)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                        if aliases.get(tgt.value.id) == dotted:
                            found.add(tgt.attr)
    return found


def measure(rel_path: str, dotted: str) -> None:
    path = ROOT / rel_path
    total_lines = sum(1 for _ in path.open("rb"))
    tree = parse(path)
    assert tree is not None, f"could not parse {rel_path}"

    patched = patched_symbols(dotted)

    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            funcs[node.name] = node

    bare_refs: dict[str, set[str]] = defaultdict(set)
    for name, fn in funcs.items():
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Name):
                bare_refs[name].add(sub.id)

    closure = {n for n, refs in bare_refs.items() if refs & patched}
    # Transitive: a function that bare-calls a locked function is locked too.
    changed = True
    while changed:
        changed = False
        for name, refs in bare_refs.items():
            if name not in closure and refs & closure:
                closure.add(name)
                changed = True

    locked = 0
    for name in closure:
        fn = funcs[name]
        end = getattr(fn, "end_lineno", fn.lineno)
        locked += end - fn.lineno + 1

    verdict = "SPLIT CANNOT REACH THE LIMIT" if locked > LIMIT else "split is viable on this metric"
    print(f"\n{rel_path}")
    print(f"  total lines            {total_lines:>7}")
    print(f"  top-level functions    {len(funcs):>7}")
    print(f"  symbols tests patch    {len(patched):>7}")
    print(f"  functions LOCKED       {len(closure):>7}")
    print(f"  lines LOCKED to facade {locked:>7}   (limit {LIMIT})")
    print(f"  -> {verdict}")


if __name__ == "__main__":
    targets = [
        ("src/tensor_grep/cli/main.py", "tensor_grep.cli.main"),
        ("src/tensor_grep/cli/repo_map.py", "tensor_grep.cli.repo_map"),
        ("src/tensor_grep/cli/mcp_server.py", "tensor_grep.cli.mcp_server"),
        ("src/tensor_grep/cli/agent_capsule.py", "tensor_grep.cli.agent_capsule"),
    ]
    print("SPLIT-FLOOR MEASUREMENT (lower bound on what must stay in the facade)")
    for rel, dotted in targets:
        measure(rel, dotted)
    sys.exit(0)
