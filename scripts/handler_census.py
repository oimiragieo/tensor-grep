"""Reproducible census of every broad ``except Exception:`` / bare ``except:`` handler under
``src/tensor_grep``, for the W1 excluded-handler audit (docs/plans/2026-08-20-worldclass-closeout-plan.md, W1.1).

WHY THIS SCRIPT EXISTS
-----------------------
The plan's W1.1 numbers were first produced by a throwaway one-off script, which makes them
*attested*, not *reproducible* -- a claim only its author can re-derive is not a receipt. This
script is that receipt, committed. It reuses the SAME classifier functions as the shipped gate
(``tests/unit/test_silent_failure_hardening.py``'s ``_is_broad_handler``,
``_body_records_reason``, ``_body_reraises``) by importing that module directly, so the audit
instrument and the enforcement gate cannot silently disagree about what counts as "broad" or
"disclosed".

POSITIVE CONTROL
-----------------
Run without ``--include-excluded``: this must print the EXACT population the shipped gate
computes over -- currently pinned at ``TOTAL_BROAD_HANDLERS_CEILING`` in that same test file. A
census that cannot reproduce the known, already-pinned number is not trusted to count the
unknown (still-excluded) one.

    python scripts/handler_census.py
    python scripts/handler_census.py --include-excluded --by-slice

Flags
-----
--include-excluded   Count every module under src/tensor_grep, ignoring
                      ``_EXCLUDED_MODULES`` from the gate file. Without this flag the script
                      reproduces exactly what the gate itself counts (the positive control).
--by-slice            Group the (excluded-only, i.e. audit-target) population by the W1
                      slice table in the plan (W1.2). Modules not owned by any slice --
                      because they are either already in-census, or excluded by a module this
                      campaign does not touch this wave -- are reported under "unslotted".
--json                Emit machine-readable JSON instead of the human-readable report.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_FILE = REPO_ROOT / "tests" / "unit" / "test_silent_failure_hardening.py"

# The W1.2 slice table (docs/plans/2026-08-20-worldclass-closeout-plan.md). Modules a slice
# does not own are reported as "unslotted" rather than silently dropped -- see AGENTS.md's "no
# zero without a control" rule; an empty slice bucket must be visibly empty, not absent.
SLICE_OWNERSHIP: dict[str, tuple[str, ...]] = {
    "W1-d": (
        "cli/repo_map_lang_js.py",
        "cli/repo_map_lang_rust.py",
        "cli/_main_binding.py",
        "cli/doctor_payload.py",
        "cli/repo_map.py",
        "cli/repo_map_cache.py",
        "cli/repo_map_lang_java.py",
        "cli/repo_map_lang_python.py",
        "cli/repo_map_output_budget.py",
        "cli/repo_map_regex_fallback.py",
    ),
    "W1-a": (
        "cli/mcp_server.py",
        "cli/mcp_symbol_tools.py",
        "cli/mcp_audit_tools.py",
        "cli/mcp_rewrite_tools.py",
    ),
    "W1-b": (
        "cli/doctor_report.py",
        "cli/native_frontdoor.py",
        "cli/windows_launcher.py",
        "cli/ast_scan.py",
    ),
    "W1-c": ("cli/main.py",),
}


def _load_gate_module() -> ModuleType:
    """Import the shipped gate test module by file path so this script's classification can
    never drift from what the gate actually enforces (they call the SAME functions)."""

    spec = importlib.util.spec_from_file_location("_handler_census_gate", GATE_FILE)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive, gate file always exists
        raise RuntimeError(f"could not load gate module from {GATE_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _census(gate: ModuleType, *, include_excluded: bool) -> dict[str, list[tuple[int, bool]]]:
    """Returns {relative_module_path: [(lineno, is_disclosed), ...]}."""

    per_module: dict[str, list[tuple[int, bool]]] = {}
    for path in sorted(gate.PY_SRC.rglob("*.py")):
        relative = path.relative_to(gate.PY_SRC).as_posix()
        if not include_excluded and relative in gate._EXCLUDED_MODULES:
            continue
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        handlers: list[tuple[int, bool]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if not gate._is_broad_handler(handler):
                    continue
                disclosed = gate._body_records_reason(handler) or gate._body_reraises(handler)
                handlers.append((handler.lineno, disclosed))
        if handlers or include_excluded:
            per_module[relative] = handlers
    return per_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--by-slice", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    gate = _load_gate_module()
    per_module = _census(gate, include_excluded=args.include_excluded)

    total = sum(len(v) for v in per_module.values())
    not_disclosed = sum(1 for v in per_module.values() for _, disclosed in v if not disclosed)

    if args.json:
        payload: dict[str, object] = {
            "total": total,
            "not_provably_disclosing": not_disclosed,
            "per_module": {
                mod: {
                    "handlers": len(handlers),
                    "not_provably_disclosing": sum(1 for _, d in handlers if not d),
                }
                for mod, handlers in sorted(per_module.items())
            },
        }
        if args.by_slice:
            payload["by_slice"] = _slice_breakdown(per_module)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for mod, handlers in sorted(per_module.items()):
        nd = sum(1 for _, d in handlers if not d)
        print(f"{len(handlers):4d}  {mod:32s} not-provably-disclosing: {nd}")
    print()
    print(f"TOTAL handlers: {total}")
    print(f"TOTAL not-provably-disclosing: {not_disclosed}")

    if args.by_slice:
        print()
        print("By slice (W1.2):")
        for slice_id, breakdown in _slice_breakdown(per_module).items():
            print(
                f"  {slice_id}: handlers={breakdown['handlers']} "
                f"not-provably-disclosing={breakdown['not_provably_disclosing']} "
                f"modules={breakdown['modules']}"
            )

    return 0


def _slice_breakdown(per_module: dict[str, list[tuple[int, bool]]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    slotted: set[str] = set()
    for slice_id, modules in SLICE_OWNERSHIP.items():
        handlers = 0
        not_disclosed = 0
        present_modules = []
        for mod in modules:
            slotted.add(mod)
            entries = per_module.get(mod, [])
            handlers += len(entries)
            not_disclosed += sum(1 for _, d in entries if not d)
            present_modules.append(mod)
        result[slice_id] = {
            "handlers": handlers,
            "not_provably_disclosing": not_disclosed,
            "modules": present_modules,
        }
    unslotted_modules = sorted(set(per_module) - slotted)
    if unslotted_modules:
        handlers = sum(len(per_module[m]) for m in unslotted_modules)
        not_disclosed = sum(1 for m in unslotted_modules for _, d in per_module[m] if not d)
        result["unslotted"] = {
            "handlers": handlers,
            "not_provably_disclosing": not_disclosed,
            "modules": unslotted_modules,
        }
    return result


if __name__ == "__main__":
    sys.exit(main())
