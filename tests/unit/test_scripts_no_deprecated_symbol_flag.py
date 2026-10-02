"""Maintenance scripts must call the symbol commands with positional SYMBOL, not `--symbol`.

`test_docs_no_deprecated_symbol_flag.py` guards the LIVE DOCS only. The scripts under `scripts/` that
shell out to `tg` were never scanned, so `stress_test_gauntlet.py` kept running
`tg blast-radius-render <dir> --symbol hello_3_1` -- which still works but prints a deprecation
warning on every run (found by the v1.123.10 dogfood; verified: exit 0 plus
"--symbol is deprecated for tg blast-radius-render"). A script that teaches/uses the deprecated idiom
is copied just like a doc is.

`tg ledger ... --symbol` is a different, real, non-deprecated flag and is exempt.

The scan is AST-based, not textual: it looks at every list literal of string constants (the shape a
`subprocess` argv takes) that contains `--symbol` AND a deprecated-form command name AND not `ledger`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
_DEPRECATED_FORM_COMMAND = re.compile(
    r"^(defs|source|refs|callers|impact|blast-radius[a-z-]*)$", re.IGNORECASE
)


def _violations_in(source: str, filename: str) -> list[tuple[str, int]]:
    tree = ast.parse(source, filename=filename)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        items = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        if "--symbol" not in items or "ledger" in items:
            continue
        if any(_DEPRECATED_FORM_COMMAND.match(item) for item in items):
            found.append((filename, node.lineno))
    return found


def _scan_scripts() -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for path in sorted(_SCRIPTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.extend(
            _violations_in(
                path.read_text(encoding="utf-8"), path.relative_to(_REPO_ROOT).as_posix()
            )
        )
    return found


def test_no_script_passes_the_deprecated_symbol_flag_to_a_symbol_command() -> None:
    assert not _scan_scripts(), (
        "scripts/ must pass SYMBOL positionally (`tg <cmd> [PATH] SYMBOL`), not `--symbol`: "
        f"{_scan_scripts()}"
    )


# --- perturbation arms: the scan must be able to fail --------------------------------------------


def test_arm_a_deprecated_command_with_the_flag_is_flagged() -> None:
    src = 'cmd = ["uv", "run", "tg", "blast-radius-render", "d", "--symbol", "x"]\n'
    assert _violations_in(src, "s.py") == [("s.py", 1)]


def test_arm_every_deprecated_form_command_is_covered() -> None:
    for command in ("defs", "source", "refs", "callers", "impact", "blast-radius"):
        src = f'cmd = ["tg", "{command}", "--symbol", "x"]\n'
        assert _violations_in(src, "s.py"), command


def test_arm_ledger_symbol_is_exempt() -> None:
    # CONTROL: `tg ledger claim ... --symbol` is a real non-deprecated flag.
    src = 'cmd = ["tg", "ledger", "claim", "p", "--symbol", "x", "--json"]\n'
    assert _violations_in(src, "s.py") == []


def test_arm_the_positional_form_is_not_flagged() -> None:
    # CONTROL: the correct form must stay quiet.
    src = 'cmd = ["uv", "run", "tg", "blast-radius-render", "d", "hello"]\n'
    assert _violations_in(src, "s.py") == []


def test_arm_an_unrelated_command_with_symbol_is_not_flagged() -> None:
    # CONTROL: only the deprecated-form commands are in scope.
    src = 'cmd = ["tool", "build", "--symbol", "x"]\n'
    assert _violations_in(src, "s.py") == []
