"""The ast-grep-unavailable error must tell the user how to fix it.

WHY THIS EXISTS. `tg rulesets` advertises six built-in security rulesets with rule counts and no
availability caveat, but on a stock `pip install tensor-grep` every `tg scan --ruleset <name>` exits
1 with "ast-grep wrapper backend ... is not available" and NO remediation. `ast_grep_py` is in no
dependency and no extra, and the wheel bundles no native binary, so a user following the documented
install path has an advertised security scanner that cannot scan and no stated way to obtain it.

The house standard is one command over: with its optional backend absent, `tg find` degrades
visibly and names the fix -- "run `tg install-dense` (or pip install 'tensor-grep[semantic]')".
This module holds the scan-side errors to the same standard.

THE REMEDIATION IS VERIFIED, NOT PLAUSIBLE. Measured 2026-08-21 in a clean container running the
PUBLISHED v1.111.1 wheel:

    tg scan /probe --ruleset subprocess-safe   -> rc=1            (control: fails)
    pip install ast-grep-cli                   -> /usr/local/bin/ast-grep
    tg scan /probe --ruleset subprocess-safe   -> rc=0, matched_rules=1, total_matches=1

`AstGrepWrapperBackend.is_available()` probes for an `ast-grep`/`sg` BINARY on PATH (with a
probe-run that rejects broken shims), which is why the package `ast-grep-cli` -- not `ast_grep_py`
-- is the correct thing to name.
"""

from __future__ import annotations

import pytest

from tensor_grep.core.pipeline import ConfigurationError

# The exact remediation token users must be able to act on. Kept as one constant so a future edit
# cannot silently change the guidance in one error site and not the other.
REMEDIATION_PACKAGE = "ast-grep-cli"


def _wrapper_unavailable_errors() -> list[str]:
    """Every message raised when the ast-grep wrapper is required but missing.

    Derived by CALLING the real selection functions with the wrapper forced unavailable, rather
    than by grepping for the message text -- a grep would also match a docstring that merely
    QUOTES the error, and would miss a site whose wording drifted.
    """
    from dataclasses import replace

    from tensor_grep.cli import ast_workflows
    from tensor_grep.core.config import SearchConfig

    messages: list[str] = []
    original = ast_workflows._check_backend_available

    def _force_wrapper_missing(name: str) -> bool:
        if name == "AstGrepWrapperBackend":
            return False
        return original(name)

    ast_workflows._check_backend_available = _force_wrapper_missing  # type: ignore[assignment]
    try:
        cfg = replace(SearchConfig(query_pattern=""), lang="python")

        # Site 1: a single wrapper-shaped pattern.
        with pytest.raises(ConfigurationError) as single:
            ast_workflows._select_ast_backend_for_pattern(cfg, "def $F($$$ARGS): return $EXPR")
        messages.append(str(single.value))

        # Site 2: a composite rule whose members need the wrapper.
        # A rule's members live in `patterns` (a list) or a single `pattern` -- see
        # `ast_workflow_rules._rule_member_patterns`. A nested `all: [{pattern: ...}]` shape
        # raises KeyError before reaching the guard, which is what the control arm caught.
        composite = {
            "language": "python",
            "pattern": "def $F($$$ARGS): return $EXPR",
            "patterns": ["def $F($$$ARGS): return $EXPR", "$X + $Y"],
        }
        with pytest.raises(ConfigurationError) as rule:
            ast_workflows._select_ast_backend_for_rule(cfg, composite)
        messages.append(str(rule.value))
    finally:
        ast_workflows._check_backend_available = original  # type: ignore[assignment]

    return messages


def test_control_both_error_sites_are_reachable():
    """Without this, an empty message list would make every assertion below vacuously true."""
    messages = _wrapper_unavailable_errors()
    assert len(messages) == 2, (
        "expected both wrapper-unavailable raise sites to fire; if a site was removed or its "
        f"guard changed, fix this control before trusting the assertions below. got={messages}"
    )
    for message in messages:
        assert "not available" in message, message


@pytest.mark.parametrize("index", [0, 1])
def test_every_wrapper_unavailable_error_names_the_remediation(index: int):
    """EVERY reachable path must disclose the fix, not just the one someone happened to hit.

    A remediation present on one path and absent on another is the LOGGED-DEGRADE failure this
    repo has already paid for: the user meets whichever path their input takes.
    """
    message = _wrapper_unavailable_errors()[index]
    assert REMEDIATION_PACKAGE in message, (
        "the error must name how to obtain the backend. A stock `pip install tensor-grep` cannot "
        "run any advertised ruleset, and this message is the only place the user finds out how. "
        f"got={message!r}"
    )
    assert "pip install" in message, message


def test_remediation_names_the_cli_package_not_the_python_bindings():
    """`AstGrepWrapperBackend.is_available()` probes for a BINARY on PATH.

    Naming `ast-grep-py` (the Python bindings) would be advice that does not fix the problem --
    the user installs it, the probe still fails, and they conclude the tool is broken.
    """
    for message in _wrapper_unavailable_errors():
        assert "ast-grep-py" not in message, (
            "ast-grep-py provides Python bindings, not the `ast-grep` binary the availability "
            f"probe looks for on PATH. got={message!r}"
        )
