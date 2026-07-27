"""The provider-alias call producers must not report a DECLARATION as a call (task 326, REOPENED).

Task 326 was "fixed" once in `_regex_references_and_calls`, shipped in v1.99.1 with green unit
tests, and the defect survived. The patched function was correct and never ran: the Rust chain is

    _rust_references_and_calls   -> ([], []) with no tree-sitter grammar (any wheel install)
    _rust_provider_alias_calls   -> RETURNS ROWS, so the chain stops here
    _regex_references_and_calls  -> never reached  <- the one that was patched

so the guard lived in a shadowed arm. This file pins the arm that actually answers.

WHY IT USES THE REPO'S OWN SOURCE. Four synthetic fixtures were built and each failed to
reproduce, for a different reason every time (measured, not guessed):
  1. one declaration -> the symbol becomes THE definition and never enters `references`
  2. the dev venv HAS the tree-sitter grammar, so the AST arm answers and this arm is skipped --
     a synthetic end-to-end test PASSES ON A BROKEN BUILD unless the grammar is stubbed out
  3. `alias_names` is populated only from `use` statements that RESOLVE to the symbol, so files
     without a resolving `use` make the producer emit nothing at all
  4. resolution needs a plausible crate layout
Rather than keep approximating, this calls the producer directly on the two real files that are
PROVEN to reproduce it. The coupling is deliberate and the premise assertions below make a
content drift fail loudly as a SKIP-worthy premise failure, not as a silent pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli.repo_map import _rust_provider_alias_calls

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYMBOL = "collect_walked_files"
# Both files DECLARE `collect_walked_files`; one becomes the definition and the other's
# declaration leaks into `references`, where this producer labels it a call.
_SOURCES = (
    _REPO_ROOT / "rust_core" / "src" / "gpu_native.rs",
    _REPO_ROOT / "rust_core" / "src" / "native_search.rs",
)


def _declaration_lines(path: Path) -> set[int]:
    """Line numbers whose text is a `fn <SYMBOL>(` declaration."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {
        number
        for number, text in enumerate(lines, start=1)
        if text.lstrip().startswith(("fn " + _SYMBOL, "pub fn " + _SYMBOL))
        and _SYMBOL + "(" in text
    }


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_alias_producer_does_not_report_declarations_as_calls(source: Path) -> None:
    if not source.exists():  # pragma: no cover - repo layout guard
        pytest.skip(f"{source} absent; this test is coupled to the repo's own Rust sources")

    declarations = _declaration_lines(source)
    calls = _rust_provider_alias_calls(source, _SYMBOL, str(_REPO_ROOT))
    call_lines = {int(row["line"]) for row in calls}

    # PREMISE 1 -- the file must still contain a declaration, or the assertion below is vacuous.
    assert declarations, (
        f"{source.name} no longer declares `{_SYMBOL}`; this test is coupled to repo content "
        "and must be re-pointed rather than silently passing"
    )
    # PREMISE 2 -- the producer must still emit SOMETHING here. It emits only when a `use`
    # binding resolves to the symbol; if that stops holding, an empty result would satisfy the
    # negative assertion while proving nothing.
    assert calls, (
        f"{source.name}: `_rust_provider_alias_calls` returned NO rows, so this test cannot "
        "discriminate. Re-check the `use`-binding precondition before trusting a pass."
    )

    leaked = sorted(declarations & call_lines)
    assert not leaked, (
        f"{source.name}: declaration line(s) {leaked} reported as calls. "
        f"declarations={sorted(declarations)} call_lines={sorted(call_lines)}"
    )


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_alias_producer_still_reports_genuine_call_sites(source: Path) -> None:
    """CONTROL ARM. A guard that suppressed every row would satisfy the test above while
    destroying the feature, so a real call site must survive."""
    if not source.exists():  # pragma: no cover - repo layout guard
        pytest.skip(f"{source} absent; this test is coupled to the repo's own Rust sources")

    declarations = _declaration_lines(source)
    calls = _rust_provider_alias_calls(source, _SYMBOL, str(_REPO_ROOT))
    call_lines = {int(row["line"]) for row in calls}

    genuine = call_lines - declarations
    assert genuine, (
        f"{source.name}: NO non-declaration call site survived -- the guard over-fired and "
        f"suppressed real calls. call_lines={sorted(call_lines)} declarations={sorted(declarations)}"
    )
