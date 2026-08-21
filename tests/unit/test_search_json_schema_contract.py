"""The search `--json` envelope must actually be VALIDATED against its own schema.

`tests/schemas/tg_output.schema.json` has existed for a long time and describes the primary
`tg search --json` contract, but until this module NOTHING validated against it: the only
references anywhere in the tree were `tests/unit/test_file_size_budget.py` (which merely counts
its lines as a "contract" category) and `docs/code-map/tests_schemas.md` (which just lists the
path). A schema no test loads is decorative -- it cannot fail, so it cannot be evidence, and the
gap it left is exactly the one these tests close: the completeness triple
(`result_incomplete` / `incomplete_reason` / `incomplete_reason_class`) that `docs/CONTRACTS.md`
makes load-bearing for agents was absent from the schema entirely.

Every test here carries a BIDIRECTIONAL control. Validating a good payload proves nothing on its
own -- with `additionalProperties: true` and few typed fields, a permissive schema accepts almost
anything -- so each positive case is paired with a mutation that MUST be rejected. If a mutation
ever starts passing, the schema has stopped constraining that field and these tests fail loudly
rather than going quietly green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "tg_output.schema.json"


def _schema() -> dict:
    with _SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _complete_payload() -> dict:
    """A minimal COMPLETE search envelope: every required field, no truncation signals.

    Mirrors the omit-when-complete convention in `docs/CONTRACTS.md`: a complete result carries
    none of the completeness triple, so this payload deliberately omits all three.
    """
    return {
        "version": 1,
        "routing_backend": "NativeCpuBackend",
        "routing_reason": "json_output",
        "sidecar_used": False,
        "requested_gpu_device_ids": [],
        "routing_gpu_device_ids": [],
        "total_matches": 1,
        "matches": [{"file": "src/example.py", "line": 12, "text": "def example():"}],
    }


def _truncated_payload() -> dict:
    """A DISCLOSED-incomplete envelope carrying the full completeness triple."""
    payload = _complete_payload()
    payload["result_incomplete"] = True
    payload["incomplete_reason"] = "directory scan hit an unreadable path"
    payload["incomplete_reason_class"] = "unreadable_path"
    return payload


def test_schema_file_is_parseable_and_is_the_search_contract():
    schema = _schema()
    # Control: prove we loaded the intended document, not an empty/renamed file. A test that
    # silently validated against `{}` would pass everything below for the wrong reason.
    assert schema.get("title") == "tensor-grep search JSON output"
    assert "matches" in schema["properties"]
    jsonschema.Draft202012Validator.check_schema(schema)


def test_complete_search_payload_validates():
    jsonschema.validate(instance=_complete_payload(), schema=_schema())


def test_truncated_payload_carrying_the_completeness_triple_validates():
    """`result_incomplete` and friends must be ACCEPTED, not merely tolerated as unknown keys."""
    jsonschema.validate(instance=_truncated_payload(), schema=_schema())


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        # The field an agent branches on to avoid treating a truncated scan as complete. A
        # string "false" is truthy in most languages, so a mistyped emitter here is the exact
        # bug that makes an incomplete result read as complete.
        ("result_incomplete", "false"),
        ("incomplete_reason", 42),
        ("incomplete_reason_class", ["unreadable_path"]),
    ],
)
def test_completeness_triple_rejects_wrong_types(field, bad_value):
    """RED CONTROL for the whole point of this module.

    Before the schema declared these three fields they fell through `additionalProperties: true`
    and ANY value validated -- so this test failed for all three parameters. If the declarations
    are ever removed, it fails again instead of silently passing.
    """
    payload = _truncated_payload()
    payload[field] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=_schema())


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda p: p.pop("matches"), "matches is required"),
        (lambda p: p.update(total_matches=-1), "total_matches has minimum 0"),
        (lambda p: p.update(routing_backend=""), "routing_backend has minLength 1"),
        (lambda p: p.update(sidecar_used="no"), "sidecar_used is boolean"),
        (lambda p: p.update(matches=[{"file": "a.py"}]), "a match requires text"),
        (
            lambda p: p.update(matches=[{"file": "a.py", "text": "x"}]),
            "a match requires line or line_number",
        ),
    ],
)
def test_schema_actually_rejects_malformed_envelopes(mutate, why):
    """Proves the schema CONSTRAINS. Without these, 'the payload validated' means nothing."""
    payload = _complete_payload()
    mutate(payload)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=_schema())
    assert why, "each case carries its reason so a failure names which constraint lapsed"


def test_real_cli_search_json_conforms_to_the_schema(tmp_path):
    """Dogfood: validate a payload the PRODUCT actually emitted, not a hand-written fixture.

    A hand-written fixture only proves the schema matches what the test author believed. This
    arm runs the real search path and validates its envelope, so producer drift is caught.
    """
    from tensor_grep.cli import main as cli_main

    source = tmp_path / "sample.py"
    source.write_text("def findme():\n    return 1\n", encoding="utf-8")

    emit = getattr(cli_main, "_execute_search_json", None)
    if emit is None:
        pytest.skip(
            "no in-process search-JSON entry point exposed; the CLI-level arm is covered by "
            "the integration suite against the real binary"
        )

    raw = emit(query="findme", path=str(tmp_path))
    payload = json.loads(raw) if isinstance(raw, str) else raw

    # Control: a search that found nothing would validate trivially and prove little, so assert
    # the arm actually exercised a populated envelope before trusting the validation.
    assert payload.get("total_matches", 0) >= 1, (
        "search returned no matches; this arm cannot discriminate. "
        f"payload={json.dumps(payload)[:400]}"
    )
    jsonschema.validate(instance=payload, schema=_schema())


def test_schema_declares_the_completeness_triple_by_name():
    """Structural guard: the triple must be DECLARED, not merely permitted.

    `additionalProperties: true` means an undeclared field is silently accepted, so the
    type-rejection tests above are the only thing standing between the contract and a
    mistyped emitter. This asserts the declarations exist so a future edit that deletes them
    fails here with a clear reason rather than quietly weakening every test above.
    """
    props = _schema()["properties"]
    for field in ("result_incomplete", "incomplete_reason", "incomplete_reason_class"):
        assert field in props, (
            f"{field} is absent from tg_output.schema.json. docs/CONTRACTS.md makes it "
            "load-bearing for agent retry decisions; declaring it is what gives the "
            "wrong-type tests something to catch."
        )
    assert props["result_incomplete"]["type"] == "boolean"
    assert props["incomplete_reason"]["type"] == "string"
    assert props["incomplete_reason_class"]["type"] == "string"
    # Deliberately NOT an enum. docs/CONTRACTS.md records the vocabulary as "the set wired so
    # far" and warns against reading it as closed; pinning an enum here would make the schema
    # REJECT a valid payload the first time a new cause is wired -- a worse failure than the
    # gap this module closes.
    assert "enum" not in props["incomplete_reason_class"]
