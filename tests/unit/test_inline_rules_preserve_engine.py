"""An inline rule's `engine:` declaration must survive parsing.

THE DEFECT. `_load_inline_rule_specs` built its spec dict without copying `engine`, so a rule
declared `engine: regex` came out as `{'id', 'pattern', 'language', 'severity', 'message'}` --
measured directly. The regex fast path in the per-rule loop
(`if rule.get("engine") == "regex": continue`, `cli/ast_scan.py`) therefore could never fire for
`--inline-rules` input, and EVERY inline rule was routed through AST backend selection regardless
of what it asked for. On a machine with no `ast-grep`/`sg` binary that means a rule which
explicitly requested regex fails with an AST dependency error.

The silence is what makes it serious: the declaration is accepted without complaint and then
disregarded. No warning, no `engine_ignored` field -- the only symptom is a dependency error
mentioning AST, on a rule the author said was not AST.

This is not a design choice being reversed. The built-in packs in `cli/rule_packs.py` already set
`"engine": "regex"` on their entries and `ast_scan` already honours it for them; inline rules
simply dropped the key on the floor. Supporting it makes the two paths agree.
"""

from __future__ import annotations

import pytest

from tensor_grep.cli.ast_scan import _load_inline_rule_specs

_RULE_YAML = "\n".join([
    "id: probe",
    "engine: regex",
    "pattern: SENTINEL_TOKEN",
    "language: python",
    "severity: high",
    "message: sentinel",
])

_MULTI_RULE_YAML = "\n".join([
    "language: python",
    "rules:",
    "  - id: probe-a",
    "    engine: regex",
    "    pattern: SENTINEL_A",
    "    severity: high",
    "    message: a",
    "  - id: probe-b",
    "    pattern: SENTINEL_B",
    "    severity: low",
    "    message: b",
])


def test_control_the_parser_returns_a_spec_at_all():
    """Without this, an empty spec list would make every assertion below vacuously true."""
    specs = _load_inline_rule_specs(_RULE_YAML)
    assert len(specs) == 1, specs
    assert specs[0]["pattern"] == "SENTINEL_TOKEN", specs[0]


def test_single_document_rule_preserves_engine():
    spec = _load_inline_rule_specs(_RULE_YAML)[0]
    assert spec.get("engine") == "regex", (
        "the rule declared `engine: regex`; dropping it silently routes the rule through AST "
        f"backend selection, which fails with no ast-grep binary present. spec={spec}"
    )


def test_rules_list_member_preserves_its_own_engine():
    """The per-member construction site is separate from the per-document one; both must copy it."""
    specs = _load_inline_rule_specs(_MULTI_RULE_YAML)
    by_id = {spec["id"]: spec for spec in specs}
    assert set(by_id) == {"probe-a", "probe-b"}, specs
    assert by_id["probe-a"].get("engine") == "regex", by_id["probe-a"]


def test_absent_engine_stays_absent():
    """The OTHER arm. A fix that stamped a default `engine` on every rule would pass the arms
    above while changing the routing of every rule that never asked for it."""
    specs = _load_inline_rule_specs(_MULTI_RULE_YAML)
    by_id = {spec["id"]: spec for spec in specs}
    assert "engine" not in by_id["probe-b"], (
        "probe-b declared no engine; inventing one would silently re-route it. "
        f"spec={by_id['probe-b']}"
    )


@pytest.mark.parametrize("declared", ["regex", "ast-grep"])
def test_engine_value_is_carried_verbatim(declared: str):
    """Whatever the author wrote is what the router must see -- not a normalised guess."""
    yaml_text = _RULE_YAML.replace("engine: regex", f"engine: {declared}")
    spec = _load_inline_rule_specs(yaml_text)[0]
    assert spec.get("engine") == declared, spec


def test_regex_engine_rule_runs_with_no_ast_grep_available(tmp_path):
    """THE BEHAVIOURAL ARM: the key must change ROUTING, not merely survive parsing.

    The assertions above prove `engine` reaches the spec dict. This proves it is acted on -- with
    the ast-grep wrapper forced unavailable (the stock-install / CI shape, A85), a rule declared
    `engine: regex` must be served by the regex backend instead of failing with an AST dependency
    error. Before the fix this arm raised:

        Explicit AST search requires AST dependencies: ast-grep wrapper backend ... not available
    """
    import json
    from unittest.mock import patch

    from typer.testing import CliRunner

    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
    from tensor_grep.cli.main import app

    (tmp_path / "a.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")

    with patch.object(AstGrepWrapperBackend, "is_available", lambda self: False):
        result = CliRunner().invoke(
            app, ["scan", str(tmp_path), "--inline-rules", _RULE_YAML, "--json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["matched_rules"] == 1, payload
    assert payload["total_matches"] == 1, payload
    assert "AstGrepWrapperBackend" not in payload.get("backends", []), (
        f"the rule asked for regex; it must not be routed to the ast-grep wrapper. {payload}"
    )
