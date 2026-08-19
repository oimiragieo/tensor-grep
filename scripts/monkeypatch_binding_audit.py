"""Inventory the monkeypatch surface, and flag the early-binding hazard a split creates.

WHY THIS EXISTS
---------------
The file-size campaign has to decompose modules that tests reach into by attribute
path. `monkeypatch.setattr("tensor_grep.cli.repo_map.X", ...)` does not patch a
function; it rebinds an attribute **on a module object**. So the property a split
must preserve is not "the symbol still imports" -- it is:

    the binding the TEST patches and the binding PRODUCTION reads are the same
    object attribute.

An alias shim (`sys.modules[old] = new`) preserves imports and preserves that
property *only while intra-package calls go through late attribute lookup*. The
moment a freshly-split submodule does::

    from .helpers import X        # early binding: X is now a local reference
    ...
    X()                           # reads the local, NOT helpers.X

...a test patching `repo_map.X` silently patches a module attribute nobody reads.
The test passes. Production runs the original function. That is a false green of
the worst kind: it appears *after* the refactor, in a test that was correct
before it, and no assertion anywhere fails.

This script inventories the exposed surface so a wave can be scoped, and reports
the early-binding hazards inside a module that a split would convert from
harmless into load-bearing.

USAGE
-----
    python scripts/monkeypatch_binding_audit.py                 # whole surface
    python scripts/monkeypatch_binding_audit.py --module repo_map --verbose

A wave's red arm is: run with --module <target> BEFORE the split and record the
patched-symbol set; run again AFTER; the two sets must be identical and every
symbol must still resolve to the same object. A symbol that changes identity is
the defect this file exists to catch.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_PREFIX = "tensor_grep"


@dataclass
class PatchSite:
    """One `monkeypatch.setattr` call found in the test tree."""

    test_file: str
    lineno: int
    target_module: str
    attribute: str
    # "string" -> setattr("a.b.C", v) | "object" -> setattr(mod, "C", v)
    # "assign" -> mod.C = v : a plain rebinding, not a monkeypatch call, but it
    #             mutates the same module attribute and a split breaks it the same
    #             way. See collect_patch_sites Form C for why it is counted.
    style: str


@dataclass
class ModuleReport:
    module: str
    patched_attributes: set[str] = field(default_factory=set)
    sites: list[PatchSite] = field(default_factory=list)
    early_bound_imports: dict[str, list[str]] = field(default_factory=dict)


def _tracked(pattern: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p]


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError:
        return None


def _is_setattr_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "setattr"


def collect_patch_sites() -> list[PatchSite]:
    """Every monkeypatch.setattr site in the test tree, by AST -- never by regex.

    A regex over `monkeypatch.setattr` would also match the string inside a
    docstring explaining the pattern, and this repo has a documented history of a
    grep hit being the fix's own documentation.
    """
    sites: list[PatchSite] = []
    for path in _tracked("tests/**/*.py"):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()

        # Map local alias -> dotted module, so setattr(rm, "X", v) resolves when the
        # test did `from tensor_grep.cli import repo_map as rm`.
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if full.startswith(PKG_PREFIX):
                        aliases[alias.asname or alias.name] = full
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(PKG_PREFIX):
                        aliases[alias.asname or alias.name] = alias.name

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_setattr_call(node)):
                continue
            args = node.args
            if not args:
                continue

            # Form A: monkeypatch.setattr("tensor_grep.cli.repo_map.symbol", value)
            first = args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                dotted = first.value
                if not dotted.startswith(PKG_PREFIX) or "." not in dotted:
                    continue
                mod, _, attr = dotted.rpartition(".")
                sites.append(PatchSite(rel, node.lineno, mod, attr, "string"))
                continue

            # Form B: monkeypatch.setattr(module_obj, "symbol", value)
            if len(args) >= 2 and isinstance(args[1], ast.Constant):
                attr = args[1].value
                if not isinstance(attr, str):
                    continue
                target = first
                dotted = None
                if isinstance(target, ast.Name):
                    dotted = aliases.get(target.id)
                elif isinstance(target, ast.Attribute):
                    parts: list[str] = []
                    cur: ast.expr = target
                    while isinstance(cur, ast.Attribute):
                        parts.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        parts.append(cur.id)
                        head = ".".join(reversed(parts))
                        root, _, rest = head.partition(".")
                        base = aliases.get(root)
                        dotted = f"{base}.{rest}" if base and rest else (base or None)
                if dotted:
                    sites.append(PatchSite(rel, node.lineno, dotted, attr, "object"))

        # Form C: plain rebinding -- `repo_map.build_thing = fake`.
        #
        # Not a monkeypatch call at all, and therefore invisible to the two forms
        # above, but it mutates a module attribute in exactly the same way and a
        # split breaks it in exactly the same way. Found the hard way during wave 2:
        # this collector reported ZERO exposure for a module whose tests patch it
        # this way throughout, and the split brief was written on that false zero.
        # Had the split trusted it, the patched primitives would have moved to a
        # submodule and the tests would have gone green against dead attributes.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                base = target.value
                if not isinstance(base, ast.Name):
                    continue
                dotted = aliases.get(base.id)
                if dotted:
                    sites.append(PatchSite(rel, node.lineno, dotted, target.attr, "assign"))

    return sites


def find_early_bound_imports(module_file: Path, symbols: set[str]) -> dict[str, list[str]]:
    """Which of `symbols` this module imports BY VALUE from a sibling module.

    `from .helpers import X` binds X locally. After a split, a test patching
    `<facade>.X` no longer affects a call to that local name -- the exact false
    green described in the module docstring. Before a split these are usually
    harmless (there is no facade yet); the split is what makes them load-bearing,
    which is why they are reported as hazards rather than defects.
    """
    tree = _parse(module_file)
    if tree is None:
        return {}
    hazards: dict[str, list[str]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            origin = ("." * node.level) + (node.module or "")
            for alias in node.names:
                local = alias.asname or alias.name
                if local in symbols:
                    hazards[local].append(f"{origin} (line {node.lineno})")
    return dict(hazards)


def module_to_path(dotted: str) -> Path | None:
    rel = Path("src") / Path(*dotted.split("."))
    for candidate in (rel.with_suffix(".py"), rel / "__init__.py"):
        if (REPO_ROOT / candidate).is_file():
            return REPO_ROOT / candidate
    return None


def build_reports(sites: list[PatchSite]) -> dict[str, ModuleReport]:
    reports: dict[str, ModuleReport] = {}
    for site in sites:
        report = reports.setdefault(site.target_module, ModuleReport(site.target_module))
        report.patched_attributes.add(site.attribute)
        report.sites.append(site)
    for report in reports.values():
        path = module_to_path(report.module)
        if path is not None:
            report.early_bound_imports = find_early_bound_imports(path, report.patched_attributes)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the monkeypatch binding surface.")
    parser.add_argument("--module", help="Filter to modules whose dotted name contains this.")
    parser.add_argument("--verbose", action="store_true", help="List every patch site.")
    parser.add_argument(
        "--fail-on-hazard",
        action="store_true",
        help="Exit 1 if any patched symbol is imported by value (post-split gate).",
    )
    args = parser.parse_args(argv)

    sites = collect_patch_sites()
    if not sites:
        print(
            "ERROR: zero monkeypatch.setattr sites found. This repo is known to have "
            "hundreds, so an empty result means the collector is broken, not that the "
            "surface is clean.",
            file=sys.stderr,
        )
        return 2

    reports = build_reports(sites)
    if args.module:
        reports = {k: v for k, v in reports.items() if args.module in k}
        if not reports:
            print(f"no patched module matches {args.module!r}", file=sys.stderr)
            return 2

    ranked = sorted(reports.values(), key=lambda r: -len(r.sites))
    total_hazards = 0

    print(f"monkeypatch surface: {len(sites)} sites across {len(reports)} modules\n")
    print(f"{'sites':>6} {'symbols':>8} {'hazards':>8}  module")
    print("-" * 78)
    for report in ranked:
        hazards = len(report.early_bound_imports)
        total_hazards += hazards
        print(
            f"{len(report.sites):>6} {len(report.patched_attributes):>8} "
            f"{hazards:>8}  {report.module}"
        )

    if total_hazards:
        print("\nEARLY-BINDING HAZARDS")
        print(
            "These symbols are patched by tests AND imported by value. Harmless today;\n"
            "after a split they become the silent false green described in this file's\n"
            "docstring. Convert each to late attribute lookup before splitting.\n"
        )
        for report in ranked:
            for symbol, origins in sorted(report.early_bound_imports.items()):
                print(f"  {report.module}.{symbol}")
                for origin in origins:
                    print(f"      imported by value from {origin}")

    if args.verbose:
        print("\nPATCH SITES")
        for report in ranked:
            print(f"\n  {report.module}")
            for site in sorted(report.sites, key=lambda s: (s.test_file, s.lineno)):
                print(f"    {site.attribute:<45} {site.test_file}:{site.lineno} [{site.style}]")

    if args.fail_on_hazard and total_hazards:
        print(f"\nFAIL: {total_hazards} early-binding hazard(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
