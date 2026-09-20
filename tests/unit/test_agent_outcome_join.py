from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

#: Private absence marker. sys.modules.get(name) cannot distinguish "no entry" from an
#: entry whose value is literally None (a legal "this import is known to fail" marker),
#: and conflating them makes the restore DELETE a caller's None entry.
_MISSING = object()


def _load_join_module() -> Any:
    """Load benchmarks/agent_outcome_join.py by path, registered during execution.

    module_from_spec() does NOT put the module in sys.modules. The module resolves its
    own dataclass annotations at import time under `from __future__ import annotations`,
    and typing.get_type_hints reads globals from sys.modules[__name__] -- so the entry
    must exist WHILE exec_module runs.

    The finally restores the EXACT pre-call state: absence pops, and any prior value --
    including None -- is re-assigned unchanged. No test leaks an entry to the next one,
    and no test has an entry of its own taken away.
    """
    spec = importlib.util.spec_from_file_location(
        "agent_outcome_join", ROOT / "benchmarks" / "agent_outcome_join.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name, _MISSING)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is _MISSING:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    return module


def _prediction(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "system_id": "tensor-grep",
        "instance_id": "click-format-filename-shorten",
        "repo_commit": "a" * 40,
        "tool_version": "1.113.3",
        "model_id": "gpt-5.6-sol",
        "budget_id": "tokens=16000",
        "predicted_validation_commands": ["python -m pytest tests/test_utils.py -q"],
        "predicted_primary_file": "src/click/utils.py",
    }
    record.update(overrides)
    return record


def _outcome(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "system_id": "tensor-grep",
        "instance_id": "click-format-filename-shorten",
        "repo_commit": "a" * 40,
        "tool_version": "1.113.3",
        "model_id": "gpt-5.6-sol",
        "budget_id": "tokens=16000",
        "execution_observed": True,
        "validation_passed": True,
        "patch_applied": True,
        "tokens_in": 1200,
        "tokens_out": 340,
        "elapsed_s": 4.5,
    }
    record.update(overrides)
    return record


def test_loader_registers_the_module_during_execution_and_leaves_no_entry() -> None:
    """The module resolves its own annotations at import; without a sys.modules entry
    that resolution raises NameError and this call never returns a module. With no prior
    entry, the loader must leave no entry behind."""
    assert "agent_outcome_join" not in sys.modules

    module = _load_join_module()

    assert module.IDENTITY_FIELDS == module.ANNOTATED_IDENTITY_FIELDS
    assert "agent_outcome_join" not in sys.modules


def test_loader_restores_a_preexisting_sentinel_object() -> None:
    """A DIFFERENT object already occupying the name must survive the call identically."""
    sentinel = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("agent_outcome_join", loader=None)
    )
    sys.modules["agent_outcome_join"] = sentinel
    try:
        _load_join_module()

        assert sys.modules["agent_outcome_join"] is sentinel
    finally:
        del sys.modules["agent_outcome_join"]


def test_loader_restores_a_preexisting_none_entry() -> None:
    """A sys.modules entry whose value is literally None is legal and load-bearing: it
    is the "this import is known to fail" marker. A `previous is not None` restore would
    POP it, silently turning a caller's guaranteed ImportError back into a live import.
    The entry must still be PRESENT and still be None afterwards -- membership, not
    truthiness, is the contract."""
    sys.modules["agent_outcome_join"] = None
    try:
        _load_join_module()

        assert "agent_outcome_join" in sys.modules
        assert sys.modules["agent_outcome_join"] is None
    finally:
        del sys.modules["agent_outcome_join"]


def test_identity_requires_all_six_fields() -> None:
    module = _load_join_module()

    assert module.IDENTITY_FIELDS == (
        "system_id",
        "instance_id",
        "repo_commit",
        "tool_version",
        "model_id",
        "budget_id",
    )


def test_extract_identity_returns_missing_fields_instead_of_guessing() -> None:
    module = _load_join_module()
    record = _prediction()
    record.pop("model_id")
    record["tool_version"] = "   "

    identity, missing = module.extract_identity(record)

    assert identity is None
    assert missing == ["model_id", "tool_version"]


def test_join_does_not_cross_join_rows_differing_in_one_identity_field() -> None:
    """MAP.md Answer 1: same instance_id, different model_id must never join."""
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[_prediction(model_id="gpt-5.6-sol")],
        outcomes=[_outcome(model_id="claude-opus-5", validation_passed=True)],
    )

    assert report["joined"] == []
    assert report["summary"]["unmatched_prediction_count"] == 1
    assert report["summary"]["unmatched_outcome_count"] == 1
    assert report["summary"]["verified_task_success_cases"] == 0


def test_duplicate_full_identities_fail_closed_and_are_not_joined() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[_prediction(), _prediction()],
        outcomes=[_outcome()],
    )

    assert report["joined"] == []
    assert report["summary"]["duplicate_identity_count"] == 1
    assert report["duplicate_identities"][0]["side"] == "prediction"
    assert report["duplicate_identities"][0]["count"] == 2


def test_legacy_row_missing_identity_stays_unavailable_not_guessed() -> None:
    module = _load_join_module()
    legacy = module.adapt_legacy_bakeoff_row({
        "instance_id": "click-format-filename-shorten",
        "system": "tensor-grep",
        "patch_applied": True,
        "validation_passed": True,
    })

    report = module.build_outcome_join_report(
        predictions=[_prediction()],
        outcomes=[legacy],
    )

    assert legacy["system_id"] == "tensor-grep"
    assert report["joined"] == []
    assert report["summary"]["unidentified_outcome_count"] == 1
    assert report["unidentified_outcomes"][0]["missing_fields"] == [
        "budget_id",
        "model_id",
        "repo_commit",
        "tool_version",
    ]
    assert report["summary"]["verified_task_success_cases"] == 0


def test_matching_full_identity_joins_and_counts_verified_success() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(predictions=[_prediction()], outcomes=[_outcome()])

    assert len(report["joined"]) == 1
    row = report["joined"][0]
    assert row["outcome_state"] == "passed"
    assert row["verified_task_success"] is True
    assert report["summary"]["complete_cases"] == 1
    assert report["summary"]["incomplete_cases"] == 0
    assert report["summary"]["verified_task_success_rate"] == 1.0


def test_joined_rows_are_ordered_deterministically_by_identity() -> None:
    """The report is written to a JSON artifact; a nondeterministic order makes two
    runs of identical inputs diff. The key must be a plain tuple of the six strings."""
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[
            _prediction(instance_id="gamma"),
            _prediction(instance_id="beta"),
        ],
        outcomes=[_outcome(instance_id="gamma"), _outcome(instance_id="beta")],
    )

    assert [row["identity"]["instance_id"] for row in report["joined"]] == ["beta", "gamma"]


def test_cargo_command_fit_with_failing_patch_gets_fit_but_zero_verified_success() -> None:
    """MAP.md Answer 2: a plausible cargo command is not evidence the task was solved."""
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[
            _prediction(
                instance_id="clap-lex-parse",
                predicted_validation_commands=["cargo test -p clap_lex"],
                predicted_primary_file="src/lib.rs",
            )
        ],
        outcomes=[
            _outcome(
                instance_id="clap-lex-parse",
                execution_observed=True,
                validation_passed=False,
                patch_applied=True,
            )
        ],
    )

    row = report["joined"][0]
    assert row["command_fit"] is True
    assert row["verified_task_success"] is False
    assert row["outcome_state"] == "failed"
    assert report["summary"]["command_fit_cases"] == 1
    assert report["summary"]["complete_cases"] == 1
    assert report["summary"]["verified_task_success_cases"] == 0
    assert report["summary"]["verified_task_success_rate"] == 0.0


def test_absent_execution_is_unavailable_never_passing() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[
            _prediction(
                instance_id="clap-lex-parse",
                predicted_validation_commands=["cargo test -p clap_lex"],
            )
        ],
        outcomes=[
            _outcome(
                instance_id="clap-lex-parse",
                execution_observed=False,
                validation_passed=True,  # stale/asserted value must be IGNORED
            )
        ],
    )

    row = report["joined"][0]
    assert row["outcome_state"] == "unavailable"
    assert row["verified_task_success"] is False
    assert row["command_fit"] is True
    # The unavailable case is excluded from the readiness denominator entirely.
    assert report["summary"]["complete_cases"] == 0
    assert report["summary"]["incomplete_cases"] == 1
    assert report["summary"]["verified_task_success_rate"] is None


def test_tokens_and_elapsed_include_failed_attempts() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[_prediction(), _prediction(instance_id="second")],
        outcomes=[
            _outcome(validation_passed=False, tokens_in=100, tokens_out=20, elapsed_s=1.5),
            _outcome(instance_id="second", tokens_in=50, tokens_out=10, elapsed_s=0.5),
        ],
    )

    assert report["summary"]["tokens_in_total"] == 150
    assert report["summary"]["tokens_out_total"] == 30
    assert report["summary"]["elapsed_s_total"] == 2.0


def test_missing_cost_is_null_not_zero() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(
        predictions=[_prediction(), _prediction(instance_id="second")],
        outcomes=[
            _outcome(tokens_in=100, tokens_out=20, elapsed_s=1.5),
            _outcome(instance_id="second", tokens_in=None, tokens_out=None, elapsed_s=None),
        ],
    )

    assert report["summary"]["tokens_in_total"] is None
    assert report["summary"]["tokens_out_total"] is None
    assert report["summary"]["elapsed_s_total"] is None


def test_empty_inputs_report_no_evidence_rather_than_perfect_success() -> None:
    module = _load_join_module()

    report = module.build_outcome_join_report(predictions=[], outcomes=[])

    assert report["joined"] == []
    assert report["summary"]["complete_cases"] == 0
    assert report["summary"]["verified_task_success_rate"] is None
    assert report["summary"]["command_fit_rate"] is None
