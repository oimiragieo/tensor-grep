"""TDD for audit #96: answer-first payloads + universal ``--max-tokens`` on defs/refs/callers.

Root cause (design ``docs/plans/design-tensor-grep-96-answer-first-payloads-2026-07-09.md``):
``defs``/``refs``/``source`` did not compute "related" tests at all -- they shallow-copied the
WHOLE-REPO test manifest verbatim into ``payload["tests"]`` (and again into ``related_paths``),
regardless of whether any given test file had anything to do with the requested symbol.
``callers``/``impact`` DID compute relevance (via ``_relevant_tests_for_symbol``) but never
count-capped the result. ``blast-radius`` was the one command already fixed
(``_apply_blast_radius_output_limits``) -- this suite proves defs/refs/callers/impact now match
that bar: relevance-filtered, count-capped (``--max-tests``), token-budgeted (``--max-tokens``),
with an additive agent-facing ``omissions`` envelope, and WITHOUT ever tripping the
scan-truncation exit-2 contract (an output cap is a COMPLETE analysis capped for display, not an
incomplete scan).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import app

runner = CliRunner()


def _write_module_with_symbol(project: Path, symbol: str) -> Path:
    src_dir = project / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        f"def {symbol}(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    return module_path


def _write_relevant_test(tests_dir: Path, name: str, symbol: str) -> Path:
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_path = tests_dir / name
    # Import-only, deliberately NOT calling `symbol`: relevance detection
    # (_file_imports_symbol_from_definition) is import-based, so this is enough to land in
    # `tests`, while keeping `tests` and `callers` counts orthogonal in fixtures that combine
    # both (a test file that also CALLS the symbol shows up in `callers` too, which would make
    # caller/test counts interact in confusing ways for the --max-tokens fanout fixtures below).
    test_path.write_text(f"from src.payments import {symbol}\n", encoding="utf-8")
    return test_path


def _write_unrelated_test(tests_dir: Path, name: str, index: int) -> Path:
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_path = tests_dir / name
    test_path.write_text(
        f"def test_unrelated_{index}():\n    assert {index} == {index}\n",
        encoding="utf-8",
    )
    return test_path


# --------------------------------------------------------------------------------------- item 1
# relevance-filter regression: defs must stop dumping the whole-repo test manifest.


def test_defs_tests_field_is_relevance_filtered_not_whole_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    relevant_test = _write_relevant_test(tests_dir, "test_payments.py", "create_invoice")
    for index in range(5):
        _write_unrelated_test(tests_dir, f"test_unrelated_{index}.py", index)

    payload = repo_map.build_symbol_defs("create_invoice", str(project))

    # Sanity: the whole-repo manifest really does contain all 6 test files (proves the fixture
    # would have failed under the OLD raw-copy behavior, not just an empty-tests coincidence).
    repo_payload = repo_map.build_repo_map(project)
    assert len(repo_payload.get("tests", [])) == 6

    assert payload["tests"] == [str(relevant_test.resolve())]


def test_refs_and_source_inherit_the_defs_relevance_filter(tmp_path: Path) -> None:
    # refs/source pull `tests` straight from build_symbol_defs_from_map's payload -- fixing defs
    # must fix them too, with no separate relevance-filter code of their own.
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    relevant_test = _write_relevant_test(tests_dir, "test_payments.py", "create_invoice")
    for index in range(5):
        _write_unrelated_test(tests_dir, f"test_unrelated_{index}.py", index)

    refs_payload = repo_map.build_symbol_refs("create_invoice", str(project))
    source_payload = repo_map.build_symbol_source("create_invoice", str(project))

    assert refs_payload["tests"] == [str(relevant_test.resolve())]
    assert source_payload["tests"] == [str(relevant_test.resolve())]


def test_callers_and_impact_relevance_filter_unchanged_by_the_defs_fix(tmp_path: Path) -> None:
    # callers/impact already relevance-filtered via _relevant_tests_for_symbol before this change
    # -- confirm the root-cause fix to defs did not regress their (already correct) behavior.
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    relevant_test = _write_relevant_test(tests_dir, "test_payments.py", "create_invoice")
    for index in range(5):
        _write_unrelated_test(tests_dir, f"test_unrelated_{index}.py", index)

    callers_payload = repo_map.build_symbol_callers("create_invoice", str(project))
    impact_payload = repo_map.build_symbol_impact("create_invoice", str(project))

    assert callers_payload["tests"] == [str(relevant_test.resolve())]
    assert impact_payload["tests"] == [str(relevant_test.resolve())]


# --------------------------------------------------------------------------------------- item 2
# shared output-limit helper + dedicated --max-tests count-cap.


def _write_many_relevant_tests(tests_dir: Path, symbol: str, count: int) -> list[Path]:
    return [
        _write_relevant_test(tests_dir, f"test_relevant_{index:02d}.py", symbol)
        for index in range(count)
    ]


def test_defs_max_tests_caps_count_and_stamps_output_limit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    test_paths = _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    payload = repo_map.build_symbol_defs("create_invoice", str(project), max_tests=3)

    assert len(payload["tests"]) == 3
    output_limit = payload["output_limit"]
    assert output_limit["max_tests"] == 3
    assert output_limit["tests_truncated"] is True
    assert output_limit["total_tests"] == 8
    assert output_limit["returned_tests"] == 3
    assert output_limit["omitted_tests"] == 5

    # every retained test path is a real fixture path (not a fabricated/duplicated entry)
    all_test_paths = {str(path.resolve()) for path in test_paths}
    assert set(payload["tests"]) <= all_test_paths


def test_builder_level_max_tests_none_is_unbounded_no_output_limit_key(tmp_path: Path) -> None:
    # Library/MCP callers that do not opt in must see byte-identical output to before this cap
    # existed (mirrors _apply_context_token_budget's "None is a no-op" contract).
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    payload = repo_map.build_symbol_defs("create_invoice", str(project))

    assert len(payload["tests"]) == 8
    assert "output_limit" not in payload


def test_callers_and_impact_get_their_own_dedicated_max_tests(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    callers_payload = repo_map.build_symbol_callers("create_invoice", str(project), max_tests=2)
    impact_payload = repo_map.build_symbol_impact("create_invoice", str(project), max_tests=4)

    assert len(callers_payload["tests"]) == 2
    assert callers_payload["output_limit"]["omitted_tests"] == 6
    assert len(impact_payload["tests"]) == 4
    assert impact_payload["output_limit"]["omitted_tests"] == 4


def test_refs_gets_its_own_dedicated_max_tests_independent_of_defs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    refs_payload = repo_map.build_symbol_refs("create_invoice", str(project), max_tests=1)

    assert len(refs_payload["tests"]) == 1
    assert refs_payload["output_limit"]["omitted_tests"] == 7


# --------------------------------------------------------------------------------------- item 3
# related_paths must not leak the capped-out (or filtered-out) tests back in via the 2nd field.


def test_capped_tests_are_absent_from_related_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    test_paths = _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    payload = repo_map.build_symbol_defs("create_invoice", str(project), max_tests=3)

    all_test_paths = {str(path.resolve()) for path in test_paths}
    omitted_test_paths = all_test_paths - set(payload["tests"])
    assert omitted_test_paths  # sanity: something really was omitted
    assert not (omitted_test_paths & set(payload["related_paths"]))
    # the retained tests DO still show up in related_paths
    assert set(payload["tests"]) <= set(payload["related_paths"])


def test_filtered_out_unrelated_tests_are_absent_from_related_paths(tmp_path: Path) -> None:
    # The root-cause (relevance-filter) leak, not just the count-cap leak: an unrelated test that
    # never belonged in `tests` must not sneak into `related_paths` either.
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_relevant_test(tests_dir, "test_payments.py", "create_invoice")
    unrelated_paths = [
        _write_unrelated_test(tests_dir, f"test_unrelated_{index}.py", index) for index in range(5)
    ]

    payload = repo_map.build_symbol_defs("create_invoice", str(project))

    unrelated_str = {str(path.resolve()) for path in unrelated_paths}
    assert not (unrelated_str & set(payload["related_paths"]))


# --------------------------------------------------------------------------------------- item 4
# universal --max-tokens: secondary fields trimmed before the primary answer array.


def _write_fanout_project(tmp_path: Path, *, callers: int, tests: int) -> Path:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    for index in range(callers):
        (src_dir / f"caller_{index:03d}.py").write_text(
            "from src.payments import create_invoice\n\n"
            f"def use_invoice_{index}(total, tax):\n"
            "    return create_invoice(total, tax)\n",
            encoding="utf-8",
        )
    _write_many_relevant_tests(tests_dir, "create_invoice", tests)
    return project


def test_max_tokens_trims_secondary_fields_before_primary_callers_array(tmp_path: Path) -> None:
    project = _write_fanout_project(tmp_path, callers=10, tests=10)

    # First: measure the REAL (untrimmed) and secondary-only-trimmed payload sizes so the budgets
    # below are chosen relative to real numbers, not a guess (this payload carries a large
    # irreducible floor from `symbols`/`imports`/`provider_status` that dwarfs a small guess).
    baseline = runner.invoke(
        app, ["callers", str(project), "create_invoice", "--json", "--max-tests", "50"]
    )
    assert baseline.exit_code == 0, baseline.output
    baseline_payload = json.loads(baseline.stdout)
    caller_count = len(baseline_payload["callers"])
    assert caller_count == 10
    assert len(baseline_payload["tests"]) == 10
    full_size = repo_map._estimate_payload_tokens(baseline_payload)
    secondary_cleared = dict(baseline_payload)
    secondary_cleared["tests"] = []
    secondary_cleared["related_paths"] = []
    secondary_only_size = repo_map._estimate_payload_tokens(secondary_cleared)
    assert secondary_only_size < full_size  # sanity: clearing secondary really shrinks it

    # A budget between the secondary-only-cleared size and the full size: dropping `tests` +
    # `related_paths` alone must suffice -- the caller primary array must survive intact.
    budget = (secondary_only_size + full_size) // 2
    result = runner.invoke(
        app,
        [
            "callers",
            str(project),
            "create_invoice",
            "--json",
            "--max-tests",
            "50",
            "--max-tokens",
            str(budget),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    token_budget = payload["token_budget"]
    assert token_budget["truncated"] is True
    assert "tests" in token_budget["secondary_fields_trimmed"]
    assert token_budget["primary_truncated"] is False
    assert len(payload["callers"]) == caller_count  # the answer itself was never touched
    assert payload["tests"] == []


def test_max_tokens_trims_primary_when_still_over_budget_after_secondary(tmp_path: Path) -> None:
    project = _write_fanout_project(tmp_path, callers=10, tests=10)

    result = runner.invoke(
        app,
        [
            "callers",
            str(project),
            "create_invoice",
            "--json",
            "--max-tests",
            "50",
            "--max-tokens",
            "60",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    token_budget = payload["token_budget"]
    assert token_budget["truncated"] is True
    assert token_budget["primary_truncated"] is True
    assert token_budget["primary_omitted"] > 0
    assert len(payload["callers"]) < 10
    # Floor at 1, never 0 (design #96 fix): a budget trim must never look like "no callers found".
    assert len(payload["callers"]) >= 1
    assert payload["not_found"] is False


def test_max_tokens_zero_is_unbounded_opt_out(tmp_path: Path) -> None:
    project = _write_fanout_project(tmp_path, callers=10, tests=10)

    result = runner.invoke(
        app,
        ["callers", str(project), "create_invoice", "--json", "--max-tokens", "0"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "token_budget" not in payload
    assert len(payload["callers"]) == 10


# --------------------------------------------------------------------------------------- item 5
# contract safety: an output-cap trim must NEVER trip the scan-truncation exit-2 signal.


def test_max_tests_and_max_tokens_trim_never_sets_result_incomplete_or_partial(
    tmp_path: Path,
) -> None:
    project = _write_fanout_project(tmp_path, callers=10, tests=10)

    result = runner.invoke(
        app,
        [
            "callers",
            str(project),
            "create_invoice",
            "--json",
            "--max-tests",
            "1",
            "--max-tokens",
            "60",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # Sanity: this really did trim something on both axes -- a no-op trim proves nothing.
    assert payload["output_limit"]["tests_truncated"] is True
    assert payload["token_budget"]["primary_truncated"] is True
    assert payload.get("result_incomplete") is not True
    assert "partial" not in payload
    caller_scan_limit = payload.get("caller_scan_limit")
    if isinstance(caller_scan_limit, dict):
        assert caller_scan_limit.get("possibly_truncated") is not True
    assert "caveat" not in payload
    assert payload["not_found"] is False


def test_defs_max_tests_trim_never_sets_result_incomplete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_many_relevant_tests(tests_dir, "create_invoice", 8)

    result = runner.invoke(
        app,
        ["defs", str(project), "create_invoice", "--json", "--max-tests", "1"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["output_limit"]["tests_truncated"] is True
    assert payload.get("result_incomplete") is not True


# --------------------------------------------------------------------------------------- item 3b
# the answer-first `omissions` envelope, incl. its own self-referential follow-up pointer test.


def test_omissions_envelope_always_present_and_empty_when_nothing_omitted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_relevant_test(tests_dir, "test_payments.py", "create_invoice")

    result = runner.invoke(app, ["defs", str(project), "create_invoice", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["omissions"]["omitted_section_count"] == 0
    assert payload["omissions"]["omitted_sections"] == []
    assert payload["omissions"]["follow_up_reads"] == []


def test_omissions_self_referential_follow_up_read_suggests_bigger_budget(tmp_path: Path) -> None:
    project = _write_fanout_project(tmp_path, callers=10, tests=10)

    result = runner.invoke(
        app,
        [
            "callers",
            str(project),
            "create_invoice",
            "--json",
            "--max-tests",
            "1",
            "--max-tokens",
            "60",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    omissions = payload["omissions"]
    assert omissions["omitted_section_count"] >= 2  # tests AND callers were both cut
    section_names = {entry["section"] for entry in omissions["omitted_sections"]}
    assert "tests" in section_names
    assert "callers" in section_names
    assert len(omissions["follow_up_reads"]) >= 1
    follow_up = omissions["follow_up_reads"][0]
    assert follow_up["symbol"] == "create_invoice"
    assert "callers" in follow_up["argv"]
    # self-referential: it points back at THIS command, not a different one (contrast with the
    # capsule's cross-command follow-up reads)
    assert "--max-tests" in follow_up["argv"] or "--max-tokens" in follow_up["argv"]


# --------------------------------------------------------------------------------------- item extra
# schema-shape pins: tests stays a flat list of strings; JSON_OUTPUT_VERSION stays 1.


def test_tests_field_stays_a_flat_list_of_strings_not_restructured(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    _write_module_with_symbol(project, "create_invoice")
    _write_many_relevant_tests(tests_dir, "create_invoice", 3)

    payload = repo_map.build_symbol_defs("create_invoice", str(project), max_tests=2)

    assert isinstance(payload["tests"], list)
    assert all(isinstance(entry, str) for entry in payload["tests"])


def test_json_output_version_not_bumped() -> None:
    assert repo_map.JSON_OUTPUT_VERSION == 1
