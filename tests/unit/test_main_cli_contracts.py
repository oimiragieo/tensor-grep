"""Regression tests for tensor-grep CLI contract fixes in src/tensor_grep/cli/main.py.

Covers:
  C3  - plain ``--json`` must reject render-only flags fast instead of risking the
        front-door launcher deadlock.
  H1  - ``audit-verify``/``review-bundle verify`` ``--json`` must exit 1 when invalid.
  H11 - regex-backed ruleset rules must be scoped to the rule's language.
  M14 - inline-flag regex errors must not suggest ``-F`` (a silent wrong answer).
  L1  - symbol commands must exit 1 and set ``not_found`` when zero results.
  L9  - ``tg run <path-but-no-pattern>`` must fail with a clear error.
  1D  - ``tg agent`` must honor the exit-2-on-scan-truncation contract like every other
        code-intelligence command (PR-1).

These import only light helpers / the Typer app and never touch the compiled
extension, so they run without a built ``.so``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule
from tensor_grep.cli.main import (
    _annotate_result_completeness,
    _emit_symbol_command_result,
    _invalid_regex_remediation,
    _plain_json_incompatible_render_flags,
    _regex_rule_targets_file,
    _scan_incomplete,
    _symbol_payload_has_no_results,
    app,
)

runner = CliRunner()


# --------------------------------------------------------------------------- C3
@pytest.mark.parametrize(
    "argv,expected",
    [
        (["search", "--json", "-b", "foo", "x.py"], ["-b"]),
        (["search", "--json", "--passthru", "foo", "x.py"], ["--passthru"]),
        (["--json", "--heading", "foo", "x.py"], ["--heading"]),
        (["--json", "--trim", "foo"], ["--trim"]),
        (["--json", "-p", "foo"], ["-p"]),
        (["--json", "--max-columns", "10", "foo"], ["-M"]),
        (["--json", "--context-separator", "##", "foo"], ["--context-separator"]),
        (["--json", "--field-match-separator", "|", "foo"], ["--field-match-separator"]),
        # No render flags -> nothing flagged.
        (["--json", "foo", "x.py"], []),
        (["search", "foo", "x.py"], []),
        # A literal flag-looking *pattern* after `--` must not be misread as a flag.
        (["--json", "--", "--passthru"], []),
    ],
)
def test_plain_json_incompatible_render_flags(argv: list[str], expected: list[str]) -> None:
    assert _plain_json_incompatible_render_flags(argv) == expected


def test_c3_plain_json_render_flag_exits_two_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "file.py"
    fixture.write_text("foo bar\nbaz foo\n", encoding="utf-8")

    # The render-flag guard is argv-based (mirroring _explicit_rg_format_requested), so
    # replicate how main_entry() lays out sys.argv before dispatching `search`.
    argv = ["tg", "search", "--json", "-b", "foo", str(fixture)]
    monkeypatch.setattr("sys.argv", argv)

    result = runner.invoke(app, argv[1:])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "unsupported_flag"
    assert "--format rg --json" in payload["detail"]
    assert "-b" in payload["detail"]


# -------------------------------------------------------------------------- M14
def test_m14_inline_flag_error_does_not_suggest_fixed_strings() -> None:
    hint = _invalid_regex_remediation(
        "error parsing regex: global flags not at the start of the expression at position 1"
    )
    assert "-P" in hint
    assert "(?s)" in hint
    # The harmful -F suggestion must be gone for the inline-flag case.
    assert "-F" not in hint
    assert "fixed-strings" not in hint


def test_m14_general_regex_error_frames_fixed_strings_as_intentional_only() -> None:
    hint = _invalid_regex_remediation("missing ), unterminated subpattern at position 3")
    # -P stays the primary suggestion; -F is only offered behind an explicit intent gate.
    assert "-P" in hint
    assert "only if you intended" in hint


# -------------------------------------------------------------------------- H11
@pytest.mark.parametrize(
    "rule_language,filename,expected",
    [
        ("python", "leak.ts", False),
        ("python", "leak.js", False),
        ("python", "leak.rs", False),
        ("python", "leak.py", True),
        ("typescript", "leak.ts", True),
        ("typescript", "leak.py", False),
        # Undetectable languages are not silently dropped.
        ("python", "config.yaml", True),
        ("python", "Makefile", True),
    ],
)
def test_h11_regex_rule_targets_file(rule_language: str, filename: str, expected: bool) -> None:
    assert _regex_rule_targets_file(rule_language, filename) is expected


# --------------------------------------------------------------------------- L1
@pytest.mark.parametrize(
    "payload,result_key,expected",
    [
        ({"definitions": []}, "definitions", True),
        ({"definitions": [{"file": "a.py"}]}, "definitions", False),
        ({"no_match": True, "definitions": [{"file": "a.py"}]}, "definitions", True),
        ({"callers": []}, "callers", True),
        ({"files": ["a.py"]}, "files", False),
    ],
)
def test_l1_symbol_payload_has_no_results(
    payload: dict[str, Any], result_key: str, expected: bool
) -> None:
    assert _symbol_payload_has_no_results(payload, result_key) is expected


def test_l1_emit_sets_not_found_and_exits_one_when_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {"definitions": [], "symbol": "x", "path": "."}
    with pytest.raises(typer.Exit) as exc:
        _emit_symbol_command_result(
            payload,
            result_key="definitions",
            json_output=True,
            emit_text=lambda _p: None,
        )
    assert exc.value.exit_code == 1
    assert payload["not_found"] is True
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["not_found"] is True


def test_l1_emit_keeps_exit_zero_when_results_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {"definitions": [{"file": "a.py"}], "symbol": "x", "path": "."}
    # No raise => exit 0 path.
    _emit_symbol_command_result(
        payload,
        result_key="definitions",
        json_output=True,
        emit_text=lambda _p: None,
    )
    assert payload["not_found"] is False
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["not_found"] is False


def test_upstream_incomplete_empty_symbol_result_never_claims_not_found(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "definitions": [],
        "symbol": "x",
        "path": ".",
        "result_incomplete": True,
        "incomplete_reason": "upstream analysis stopped early",
    }
    with pytest.raises(typer.Exit) as exc:
        _emit_symbol_command_result(
            payload,
            result_key="definitions",
            json_output=True,
            emit_text=lambda _p: None,
        )
    assert exc.value.exit_code == 2
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["result_incomplete"] is True
    assert emitted["not_found"] is False
    assert "upstream analysis stopped early" in emitted["caveat"]


# ----------------------------------------------------------------- P7 zero-callers caveat
# "zero callers != dead code": a symbol that RESOLVED but has no callers in the static graph
# is the P7 trap (validated twice on real codebases: registration symbols + spec_to_env_fragment,
# which `tg callers` reported as 0 callers while it was live-called from a script + two tests).
# The tool must surface the caveat at the result so an agent without the audit skill can't delete
# load-bearing code.
def test_callers_zero_results_emits_dead_code_caveat_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [],
        "files": [],
        "symbol": "spec_to_env_fragment",
        "path": ".",
    }
    with pytest.raises(typer.Exit) as exc:
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=True, emit_text=lambda _p: None
        )
    assert exc.value.exit_code == 1
    emitted = json.loads(capsys.readouterr().out)
    assert "caveat" in emitted
    assert "dead code" in emitted["caveat"].lower()


def test_callers_zero_results_emits_caveat_in_text_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {"callers": [], "files": [], "symbol": "x", "path": "."}
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=False, emit_text=lambda _p: None
        )
    out = capsys.readouterr().out
    assert "note:" in out
    assert "dead code" in out.lower()


def test_callers_with_results_has_no_caveat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [{"file": "a.py"}],
        "files": ["a.py"],
        "symbol": "x",
        "path": ".",
    }
    _emit_symbol_command_result(
        payload, result_key="callers", json_output=True, emit_text=lambda _p: None
    )
    emitted = json.loads(capsys.readouterr().out)
    assert "caveat" not in emitted


def test_zero_definitions_does_not_get_callers_caveat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The caveat is callers-specific; a zero-result `defs`/`refs` must NOT inherit it.
    payload: dict[str, Any] = {"definitions": [], "symbol": "x", "path": "."}
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="definitions", json_output=True, emit_text=lambda _p: None
        )
    emitted = json.loads(capsys.readouterr().out)
    assert "caveat" not in emitted


def test_unresolved_symbol_no_match_does_not_get_caveat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Symbol did not resolve (no_match) -> "zero callers != dead" would mislead; suppress it.
    payload: dict[str, Any] = {"callers": [], "no_match": True, "symbol": "typo", "path": "."}
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=True, emit_text=lambda _p: None
        )
    emitted = json.loads(capsys.readouterr().out)
    assert "caveat" not in emitted


# --------------------------------------------------------------- P0 truncated-scan silent zero
# A scan that hit its file cap and dropped project files can return a confident-looking zero
# that renders identically to a real zero — "the green light to delete live code". The payload
# already knows (scan_limit.possibly_truncated); the default output must shout it.
def test_truncated_scan_marks_result_incomplete_and_warns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [],
        "files": [],
        "symbol": "spec_to_env_fragment",
        "path": ".",
        "scan_limit": {
            "max_repo_files": 512,
            "scanned_files": 512,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=True, emit_text=lambda _p: None
        )
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["result_incomplete"] is True
    assert "INCOMPLETE" in emitted["caveat"]
    assert "512" in emitted["caveat"]


def test_truncation_warning_supersedes_dead_code_caveat_in_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [],
        "files": [],
        "symbol": "x",
        "path": ".",
        "scan_limit": {
            "max_repo_files": 512,
            "scanned_files": 512,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=False, emit_text=lambda _p: None
        )
    out = capsys.readouterr().out
    assert "warning:" in out
    assert "INCOMPLETE" in out
    assert "dead code" not in out.lower()  # truncation is the real story, not the generic caveat


def test_blast_radius_output_only_cap_is_advisory_not_incomplete() -> None:
    # REAL blast-radius shape: _apply_blast_radius_output_limits emits callers_truncated /
    # files_truncated (NOT possibly_truncated). An output cap is a COMPLETE analysis whose DISPLAY
    # was paginated: result_incomplete stays False, is_truncation stays False (exit 0), and the
    # caveat carries the OUTPUT LIMITED advisory with the exact omitted count -- never
    # "INCOMPLETE RESULT", which would misclassify pagination as a failed scan.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [{"file": "a.py"}],
        "files": ["a.py"],
        "output_limit": {
            "max_callers": 1,
            "max_files": 1,
            "callers_truncated": True,
            "files_truncated": True,
            "total_callers": 9,
            "returned_callers": 1,
            "omitted_callers": 8,
            "total_files": 4,
            "returned_files": 1,
            "omitted_files": 3,
        },
    }
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    assert payload["result_incomplete"] is False
    assert is_truncation is False
    assert caveat is not None and "OUTPUT LIMITED" in caveat and "8 caller(s)" in caveat
    assert "3 file(s)" in caveat
    assert "--max-callers" in caveat and "--max-files" in caveat
    assert "INCOMPLETE RESULT" not in caveat


def test_repo_map_output_limit_is_advisory_not_incomplete() -> None:
    # The repo-map output cap shape (apply_repo_map_output_limits) uses possibly_truncated with
    # original/emitted counts. Pagination must stay exit-0-complete: result_incomplete False and
    # an OUTPUT LIMITED advisory naming the exact omitted count and the --max-files knob.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "output_limit": {
            "max_files": 25,
            "emitted_files": 25,
            "original_files": 400,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    caveat, is_truncation = _annotate_result_completeness(payload)
    assert payload["result_incomplete"] is False and is_truncation is False
    assert caveat is not None and "OUTPUT LIMITED" in caveat
    assert "375 file(s)" in caveat
    assert "--max-files" in caveat
    assert "INCOMPLETE RESULT" not in caveat


def test_apply_blast_radius_output_limits_omissions_reach_the_advisory() -> None:
    # Real budget helper on a concrete payload: every omission field (callers, files, tests, AND
    # import consumers) must fire, and the annotation must surface every exact omitted count as
    # an advisory without ever calling the analysis incomplete.
    from tensor_grep.cli.repo_map import _apply_blast_radius_output_limits

    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [
            {"file": "a.py", "line": 1},
            {"file": "b.py", "line": 2},
            {"file": "c.py", "line": 3},
        ],
        "caller_tree": [],
        "files": ["a.py", "b.py", "c.py"],
        "tests": ["test_a.py", "test_b.py", "test_c.py"],
        "import_graph_consumers": [{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}],
    }
    limited = _apply_blast_radius_output_limits(payload, max_callers=1, max_files=1)
    # PREMISE: the caps really fired on all three omissions -- a cap that did not apply would
    # make every assertion below vacuous.
    output_limit = limited["output_limit"]
    assert output_limit["callers_truncated"] is True
    assert output_limit["files_truncated"] is True
    assert output_limit["tests_truncated"] is True
    assert output_limit["import_consumers_truncated"] is True
    assert output_limit["omitted_callers"] == 2
    assert output_limit["omitted_files"] == 2
    assert output_limit["omitted_tests"] == 2
    assert output_limit["omitted_import_consumers"] == 2
    caveat, is_truncation = _annotate_result_completeness(limited, result_key="callers")
    assert limited["result_incomplete"] is False and is_truncation is False
    assert caveat is not None and "OUTPUT LIMITED" in caveat
    assert "2 caller(s)" in caveat
    assert "2 file(s)" in caveat
    assert "2 test file(s)" in caveat
    assert "2 import consumer(s)" in caveat
    assert "INCOMPLETE RESULT" not in caveat


def test_test_omission_advisory_names_its_producer_knob() -> None:
    blast_payload = {
        "output_limit": {
            "max_files": 2,
            "tests_truncated": True,
            "total_tests": 5,
            "returned_tests": 2,
            "omitted_tests": 3,
        }
    }
    symbol_payload = {
        "output_limit": {
            "max_tests": 2,
            "tests_truncated": True,
            "total_tests": 5,
            "returned_tests": 2,
            "omitted_tests": 3,
        }
    }
    blast_note = _annotate_result_completeness(blast_payload)[0]
    symbol_note = _annotate_result_completeness(symbol_payload)[0]
    assert (
        blast_note is not None and "--max-files" in blast_note and "--max-tests" not in blast_note
    )
    assert (
        symbol_note is not None
        and "--max-tests" in symbol_note
        and "--max-files" not in symbol_note
    )


def test_scan_only_truncation_stays_an_incomplete_result() -> None:
    # Scan-only payload: the old classification that must survive this slice unchanged.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [],
        "scan_limit": {
            "max_repo_files": 512,
            "scanned_files": 512,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    assert payload["result_incomplete"] is True and is_truncation is True
    assert caveat is not None and "INCOMPLETE RESULT" in caveat
    assert "OUTPUT LIMITED" not in caveat


def test_deadline_only_truncation_names_the_deadline_remedy() -> None:
    # A --deadline cutoff is a SCAN truncation (its own remedy), never an output advisory.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [],
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True, "files_scanned": 37, "files_total": 900},
    }
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    assert payload["result_incomplete"] is True and is_truncation is True
    assert caveat is not None and "INCOMPLETE RESULT" in caveat and "--deadline" in caveat
    assert "OUTPUT LIMITED" not in caveat


def test_mixed_deadline_plus_output_discloses_both_facts() -> None:
    # A display cap and a failed scan are independent facts: the deadline warning leads (exit 2)
    # AND the output omissions stay disclosed -- a deadline must not be masked by an output cap.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [{"file": "a.py"}],
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True, "files_scanned": 37, "files_total": 900},
        "output_limit": {
            "max_callers": 1,
            "max_files": 1,
            "callers_truncated": True,
            "files_truncated": False,
            "total_callers": 9,
            "returned_callers": 1,
            "omitted_callers": 8,
        },
    }
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    assert payload["result_incomplete"] is True and is_truncation is True
    assert caveat is not None
    assert "--deadline" in caveat
    assert "OUTPUT LIMITED" in caveat and "8 caller(s)" in caveat


def test_preexisting_incomplete_gets_a_fail_closed_warning() -> None:
    # An independently stamped upstream analysis-incomplete reason with no scan shape of its own
    # must still warn (fail-closed) and classify as truncation (exit 2 downstream).
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [{"file": "a.py"}],
        "result_incomplete": True,
        "incomplete_reason": "upstream analysis stopped early",
    }
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    assert payload["result_incomplete"] is True and is_truncation is True
    assert caveat is not None
    assert "INCOMPLETE RESULT" in caveat and "upstream analysis stopped early" in caveat


def test_blast_radius_upstream_incomplete_empty_result_never_claims_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import main as main_mod
    from tensor_grep.cli import repo_map

    payload: dict[str, Any] = {
        "symbol": "x",
        "path": str(tmp_path),
        "definitions": [],
        "callers": [],
        "files": [],
        "tests": [],
        "result_incomplete": True,
        "incomplete_reason": "upstream analysis stopped early",
    }
    monkeypatch.setattr(main_mod, "_maybe_symbol_command_via_running_daemon", lambda **_k: None)
    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", lambda *_a, **_k: dict(payload))

    result = runner.invoke(app, ["blast-radius", str(tmp_path), "x", "--json"])
    assert result.exit_code == 2, result.output
    emitted = json.loads(result.stdout)
    assert emitted["result_incomplete"] is True
    assert emitted["not_found"] is False
    assert "upstream analysis stopped early" in emitted["caveat"]


def test_map_route_preexisting_incomplete_warns_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `tg map` must gate its exit on the SHARED annotation's truncation classification, so an
    # upstream-only incomplete stamp (no scan_limit of its own) warns and exits 2.
    from tensor_grep.cli import repo_map

    map_payload: dict[str, Any] = {
        "path": str(tmp_path),
        "files": ["a.py"],
        "tests": [],
        "symbols": [],
        "imports": [],
        "result_incomplete": True,
        "incomplete_reason": "upstream analysis stopped early",
    }
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *a, **k: dict(map_payload))

    json_result = runner.invoke(app, ["map", str(tmp_path), "--json"])
    assert json_result.exit_code == 2, json_result.output
    json_payload = json.loads(json_result.stdout)
    assert json_payload["result_incomplete"] is True
    assert "INCOMPLETE RESULT" in json_payload["caveat"]

    text_result = runner.invoke(app, ["map", str(tmp_path)])
    assert text_result.exit_code == 2, text_result.output
    # LEADING disclosure: the warning precedes the data it qualifies.
    assert text_result.output.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")


def test_map_cli_output_cap_only_exits_zero_with_output_limited_note(tmp_path: Path) -> None:
    # REAL map route (previously a raw dump with no advisory): a two-file fixture capped to one
    # file must keep exit 0, keep JSON result_incomplete false, disclose the exact omission in
    # JSON and text, and never claim the capped subset is the whole answer.
    project = tmp_path / "map_cap_project"
    project.mkdir()
    for index in range(2):
        (project / f"module_{index}.py").write_text(
            f"def helper_{index}():\n    return {index}\n", encoding="utf-8"
        )
    json_result = runner.invoke(app, ["map", str(project), "--max-files", "1", "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.stdout)
    # PREMISE: the cap really fired -- a cap that did not apply would make the assertions below
    # vacuous.
    assert payload["output_limit"]["original_files"] > payload["output_limit"]["emitted_files"]
    omitted = payload["output_limit"]["original_files"] - payload["output_limit"]["emitted_files"]
    assert payload["result_incomplete"] is False
    assert "OUTPUT LIMITED" in payload["caveat"]
    assert f"{omitted} file(s)" in payload["caveat"]
    assert "--max-files" in payload["caveat"]
    assert "INCOMPLETE RESULT" not in payload["caveat"]

    text_result = runner.invoke(app, ["map", str(project), "--max-files", "1"])
    assert text_result.exit_code == 0, text_result.output
    assert "OUTPUT LIMITED" in text_result.output
    assert f"{omitted} file(s)" in text_result.output
    assert "INCOMPLETE RESULT" not in text_result.output
    # An output note is commentary on a complete analysis, so it TRAILS the result rather than
    # leading it (the inverse of the scan-warning position contract).
    assert text_result.output.index("Repository map for") < text_result.output.index(
        "OUTPUT LIMITED"
    )


def test_map_cli_tests_only_output_cap_is_disclosed(tmp_path: Path) -> None:
    project = tmp_path / "map_test_cap_project"
    project.mkdir()
    (project / "module.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    for index in range(4):
        (project / f"test_module_{index}.py").write_text(
            f"def test_helper_{index}():\n    assert {index} == {index}\n", encoding="utf-8"
        )

    json_result = runner.invoke(app, ["map", str(project), "--max-files", "1", "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.stdout)
    limit = payload["output_limit"]
    assert limit["omitted_files"] == 0
    assert limit["total_tests"] > limit["returned_tests"]
    assert limit["omitted_tests"] == 3
    assert payload["result_incomplete"] is False
    assert "OUTPUT LIMITED" in payload["caveat"]
    assert "3 test file(s)" in payload["caveat"]
    assert "INCOMPLETE RESULT" not in payload["caveat"]

    text_result = runner.invoke(app, ["map", str(project), "--max-files", "1"])
    assert text_result.exit_code == 0, text_result.output
    assert "3 test file(s)" in text_result.output
    assert "INCOMPLETE RESULT" not in text_result.output


def test_blast_radius_cli_surfaces_output_cap_on_real_output(tmp_path: Path) -> None:
    # Dogfood the REAL command output: a fixture with at least two known callers capped to one.
    # Production MUST emit callers_truncated=True (asserted as a premise, so a cap that does not
    # fire fails the test instead of silently passing), and the payload must stay exit 0 /
    # result_incomplete False with an exact OUTPUT LIMITED omitted count.
    project = tmp_path / "blast_cap_project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "target.py").write_text(
        "def target_fn(value):\n    return value\n", encoding="utf-8"
    )
    for index in range(2):
        (src_dir / f"caller_{index}.py").write_text(
            "from src.target import target_fn\n\n"
            f"def caller_{index}(value):\n"
            "    return target_fn(value)\n",
            encoding="utf-8",
        )
    result = runner.invoke(
        app,
        ["blast-radius", str(project), "target_fn", "--max-callers", "1", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # PREMISE: pagination really fired (total > returned, truncated flag set).
    assert payload["output_limit"]["callers_truncated"] is True
    assert payload["output_limit"]["total_callers"] > payload["output_limit"]["returned_callers"]
    omitted = payload["output_limit"]["omitted_callers"]
    assert omitted >= 1
    assert payload["result_incomplete"] is False
    assert "OUTPUT LIMITED" in payload["caveat"]
    assert f"{omitted} caller(s)" in payload["caveat"]
    assert "INCOMPLETE RESULT" not in payload["caveat"]


@pytest.mark.parametrize(
    "warm,json_output", [(False, False), (False, True), (True, False), (True, True)]
)
def test_blast_radius_tests_only_cap_discloses_on_warm_and_cold_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    warm: bool,
    json_output: bool,
) -> None:
    from tensor_grep.cli import main as main_mod
    from tensor_grep.cli import repo_map

    base: dict[str, Any] = {
        "symbol": "target",
        "path": str(tmp_path),
        "definitions": [{"file": "src.py", "line": 1}],
        "callers": [{"file": "src.py", "line": 2}],
        "caller_tree": [],
        "files": ["src.py"],
        "tests": ["test_a.py", "test_b.py", "test_c.py"],
        "import_graph_consumers": [],
    }
    monkeypatch.setattr(
        main_mod,
        "_maybe_symbol_command_via_running_daemon",
        lambda **_kwargs: dict(base) if warm else None,
    )

    def _cold(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        return repo_map._apply_blast_radius_output_limits(
            dict(base), max_callers=kwargs.get("max_callers"), max_files=kwargs.get("max_files")
        )

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _cold)
    main_mod.blast_radius(
        path=str(tmp_path),
        symbol_arg="target",
        symbol=None,
        provider="native",
        max_depth=3,
        max_repo_files=512,
        max_callers=None,
        max_files=1,
        deadline=None,
        json_output=json_output,
        mermaid_output=False,
    )
    out = capsys.readouterr().out
    assert "2 test file(s)" in out
    assert "INCOMPLETE RESULT" not in out
    if json_output:
        payload = json.loads(out)
        assert payload["output_limit"]["tests_truncated"] is True
        assert payload["output_limit"]["omitted_tests"] == 2
        assert payload["result_incomplete"] is False


def test_blast_radius_cli_mixed_truncation_warns_leads_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Mixed deadline+output through the REAL blast-radius text route: exit 2, LEADING deadline
    # warning, and the output omissions still disclosed (a deadline cannot be masked by a cap).
    from tensor_grep.cli import main as main_mod
    from tensor_grep.cli import repo_map

    mixed: dict[str, Any] = {
        "symbol": "x",
        "path": str(tmp_path),
        "definitions": [],
        "callers": [{"file": "a.py", "line": 1}],
        "files": ["a.py"],
        "tests": [],
        "partial": True,
        "deadline_limit": {"deadline_exceeded": True, "files_scanned": 37, "files_total": 900},
        "output_limit": {
            "max_callers": 1,
            "max_files": 1,
            "callers_truncated": True,
            "files_truncated": False,
            "total_callers": 9,
            "returned_callers": 1,
            "omitted_callers": 8,
        },
    }
    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", lambda *a, **k: dict(mixed))
    monkeypatch.setattr(main_mod, "_maybe_symbol_command_via_running_daemon", lambda **k: None)

    with pytest.raises(typer.Exit) as exc:
        main_mod.blast_radius(
            path=str(tmp_path),
            symbol_arg="x",
            symbol=None,
            provider="native",
            max_depth=3,
            max_repo_files=512,
            max_callers=None,
            max_files=None,
            deadline=None,
            json_output=False,
            mermaid_output=False,
        )
    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")
    assert "--deadline" in out
    assert "OUTPUT LIMITED" in out and "8 caller(s)" in out


def test_complete_scan_sets_result_incomplete_false(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [{"file": "a.py"}],
        "files": ["a.py"],
        "symbol": "x",
        "path": ".",
        "scan_limit": {
            "max_repo_files": 512,
            "scanned_files": 40,
            "possibly_truncated": False,
            "truncation_cause": None,
        },
    }
    _emit_symbol_command_result(
        payload, result_key="callers", json_output=True, emit_text=lambda _p: None
    )
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["result_incomplete"] is False
    assert "caveat" not in emitted


# ------------------------------------------------------------- 1D `tg agent` scan-truncation gate
# `tg agent` was the ONLY command in the code-intelligence family that never gated on
# `_scan_incomplete` and dropped `scan_limit`/`partial`/`result_incomplete` from its payload -- a
# `tg agent . "query" --max-repo-files N` on a repo with >N files produced a CONFIDENT capsule at
# exit 0 over a PARTIAL scan. PR-1 (1D) fixes this.
def _write_agent_scan_cap_project(tmp_path: Path) -> Path:
    project = tmp_path / "agent_scan_cap_project"
    project.mkdir()
    for index in range(8):
        (project / f"module_{index}.py").write_text(
            f"def helper_{index}(value):\n    return value + {index}\n",
            encoding="utf-8",
        )
    return project


def test_agent_cli_json_exits_two_on_scan_truncation(tmp_path: Path) -> None:
    project = _write_agent_scan_cap_project(tmp_path)

    result = runner.invoke(
        app,
        ["agent", str(project), "helper_0", "--max-repo-files", "1", "--json"],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["result_incomplete"] is True
    assert payload["ask_user_before_editing"]["required"] is True
    assert any(
        "scan was truncated" in reason for reason in payload["ask_user_before_editing"]["reasons"]
    )


def test_agent_cli_text_exits_two_on_scan_truncation(tmp_path: Path) -> None:
    project = _write_agent_scan_cap_project(tmp_path)

    result = runner.invoke(
        app,
        ["agent", str(project), "helper_0", "--max-repo-files", "1"],
    )

    assert result.exit_code == 2, result.output
    # Output-before-exit: the full text summary must still print, never swallowed by the exit.
    assert "Agent capsule for" in result.output
    assert "ask_required=True" in result.output


def test_agent_cli_output_cap_only_stays_exit_zero(tmp_path: Path) -> None:
    project = _write_agent_scan_cap_project(tmp_path)

    result = runner.invoke(
        app,
        ["agent", str(project), "helper_0", "--max-tokens", "1", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # The repo scan itself was NOT capped (the 8-file project is far under the default
    # `--max-repo-files`), so `scan_limit` may be present (every code-intelligence command that
    # accepts `--max-repo-files` stamps it once a limit is configured) but must read complete --
    # only a tight `--max-tokens` OUTPUT budget was hit, which must stay exit 0.
    scan_limit = payload.get("scan_limit")
    if scan_limit is not None:
        assert scan_limit["possibly_truncated"] is False
    assert "result_incomplete" not in payload


def test_capsule_scan_incomplete_matches_main_scan_incomplete() -> None:
    """PR-1 (1D): `agent_capsule._capsule_scan_incomplete` is a module-local twin of
    `main._scan_incomplete` (not imported -- importing it back would be circular). Pin the two
    functions to agree on every scan-side shape, while an output-only cap (`result_incomplete`
    alone, no scan-side key) must stay False for BOTH -- that's the output-cap-stays-0 contract.
    """
    cases: list[dict[str, Any]] = [
        {},
        {"scan_limit": {"possibly_truncated": True}},
        {"scan_limit": {"possibly_truncated": False}},
        {"caller_scan_limit": {"possibly_truncated": True}},
        {"partial": True},
        {"caller_scan_truncated": True},
        {"result_incomplete": True},  # output-only cap signal; must NOT count as scan-truncated
        {"scan_limit": {"possibly_truncated": True}, "partial": True},
    ]
    for payload in cases:
        assert _scan_incomplete(payload) == agent_capsule._capsule_scan_incomplete(payload), payload


# --------------------------------------------------------------------------- H1
def _write_audit_manifest(directory: Path, *, valid: bool) -> Path:
    from tensor_grep.cli import audit_manifest as am

    body = {"kind": "rewrite-audit", "path": str(directory), "entries": []}
    digest = am._sha256_hex(am._canonical_manifest_bytes(body))
    manifest = dict(body)
    manifest["manifest_sha256"] = digest if valid else "0" * 64
    target = directory / ("clean.json" if valid else "tampered.json")
    target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def test_h1_audit_verify_json_exits_one_on_tampered(tmp_path: Path) -> None:
    manifest = _write_audit_manifest(tmp_path, valid=False)
    result = runner.invoke(app, ["audit-verify", str(manifest), "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is False


def test_h1_audit_verify_json_exits_zero_on_valid(tmp_path: Path) -> None:
    manifest = _write_audit_manifest(tmp_path, valid=True)
    result = runner.invoke(app, ["audit-verify", str(manifest), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is True


def test_h1_review_bundle_verify_json_exits_one_on_tampered(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"bundle_sha256": "0" * 64, "checksums": {}}), encoding="utf-8")
    result = runner.invoke(app, ["review-bundle", "verify", str(bundle), "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["valid"] is False


# --------------------------------------------------------------------------- L9
def test_l9_run_with_path_but_no_pattern_errors(tmp_path: Path) -> None:
    fixture = tmp_path / "m.py"
    fixture.write_text("def foo():\n    pass\n", encoding="utf-8")

    result = runner.invoke(app, ["run", str(fixture)])

    assert result.exit_code == 2, result.output
    assert "requires a PATTERN" in result.output


def test_l9_run_with_directory_but_no_pattern_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "requires a PATTERN" in result.output
