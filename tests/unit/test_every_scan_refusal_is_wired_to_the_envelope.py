"""Every broad-scan refusal must go through `_emit_broad_scan_refusal`, not a bare `typer.echo`.

#843 introduced the machine-readable refusal envelope and wired it at THREE emitters
(generated-scan, workspace-scan, vendored-root). Two more existed and were missed: both call sites
of `_format_unbounded_large_root_scan_error`, which is the refusal a bare `tg search PAT --json`
on a large single-project root actually hits -- i.e. the exact command in the external dogfood
reports for v1.101.7 and v1.101.9 ("bare `tg search P --json` (no PATH) -- exit 1/2, empty, no
machine-readable refusal"). The headline fix shipped while the reported command stayed broken.

Measured on the shipped v1.101.10 binary, from a large repo root::

    tg search ZZZ_NOMATCH --json  ->  exit 2, stdout 0 bytes, stderr 636 bytes

A `--json` consumer got `JSONDecodeError` and had to parse English off stderr to learn why -- the
precise inference this surface exists to prevent, and indistinguishable at the call site from
"no matches".

Pinned as a CLASS rather than as two more cases: the defect is not that these two sites were
wrong, it is that a new refusal emitter can be added without the envelope and nothing notices.
Matched over the AST so a docstring or changelog entry quoting the pattern cannot satisfy or break
it.

Verified to bite: run against `origin/main` this flags 2 sites.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli" / "main.py"


def _refusal_formatters(tree: ast.Module) -> set[str]:
    """Every `_format_*_scan_error` helper defined in the module."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_format_")
        and node.name.endswith("_scan_error")
    }


def _first_arg_callee(call: ast.Call) -> str | None:
    if not call.args:
        return None
    inner = call.args[0]
    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
        return inner.func.id
    return None


def test_no_refusal_is_emitted_through_a_bare_typer_echo() -> None:
    """THE DEFECT: two sites printed the refusal and exited 2 with nothing on stdout."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    formatters = _refusal_formatters(tree)

    # PREMISE: the matcher really found the refusal formatters. Without this, a rename would make
    # the assertion below vacuously true -- it would scan for calls that no longer exist.
    assert len(formatters) >= 4, f"expected the refusal formatter family, found {formatters}"

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_typer_echo = (
            isinstance(func, ast.Attribute)
            and func.attr == "echo"
            and isinstance(func.value, ast.Name)
            and func.value.id == "typer"
        )
        if not is_typer_echo:
            continue
        callee = _first_arg_callee(node)
        if callee in formatters:
            offenders.append((node.lineno, callee))

    assert not offenders, (
        "a broad-scan refusal is emitted with a bare typer.echo, so `--json` callers get zero "
        f"bytes on stdout and a JSONDecodeError: {offenders}. Route it through "
        "_emit_broad_scan_refusal(message, json_output=json, path=...) instead."
    )


def test_every_refusal_formatter_reaches_the_envelope() -> None:
    """The other direction: a formatter that is never routed to the envelope is dead or unwired.

    Without this, deleting the two call sites entirely would satisfy the test above -- an
    emitter that prints nothing at all also prints no bare typer.echo.
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    formatters = _refusal_formatters(tree)

    wired: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_emit_broad_scan_refusal"
        ):
            callee = _first_arg_callee(node)
            if callee:
                wired.add(callee)

    unwired = formatters - wired
    assert not unwired, (
        f"these refusal formatters never reach the machine-readable envelope: {sorted(unwired)}. "
        "Either wire them through _emit_broad_scan_refusal or delete them."
    )


def test_the_envelope_helper_still_exists_under_this_name() -> None:
    """PREMISE for both tests above: they key on the helper by NAME. A rename would silently turn
    the first test green (nothing routes through a name that no longer exists) while the second
    fails loudly -- this makes the cause obvious rather than leaving a confusing half-failure.
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_emit_broad_scan_refusal" in names, (
        "_emit_broad_scan_refusal was renamed; update this file's matchers with it"
    )
