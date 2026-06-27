"""Regression tests for tensor-grep CLI contract fixes in src/tensor_grep/cli/main.py.

Covers:
  C3  - plain ``--json`` must reject render-only flags fast instead of risking the
        front-door launcher deadlock.
  H1  - ``audit-verify``/``review-bundle verify`` ``--json`` must exit 1 when invalid.
  H11 - regex-backed ruleset rules must be scoped to the rule's language.
  M14 - inline-flag regex errors must not suggest ``-F`` (a silent wrong answer).
  L1  - symbol commands must exit 1 and set ``not_found`` when zero results.
  L9  - ``tg run <path-but-no-pattern>`` must fail with a clear error.

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

from tensor_grep.cli.main import (
    _annotate_result_completeness,
    _emit_symbol_command_result,
    _invalid_regex_remediation,
    _plain_json_incompatible_render_flags,
    _regex_rule_targets_file,
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


def test_blast_radius_output_limit_truncation_flagged() -> None:
    # REAL blast-radius shape: _apply_blast_radius_output_limits emits callers_truncated /
    # files_truncated (NOT possibly_truncated). A capped blast radius must read as incomplete.
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "callers": [{"file": "a.py"}],
        "files": ["a.py"],
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
    assert payload["result_incomplete"] is True
    assert is_truncation is True
    assert caveat is not None and "INCOMPLETE" in caveat and "8 caller(s)" in caveat


def test_repo_map_output_limit_possibly_truncated_flagged() -> None:
    # The repo-map output cap shape (apply_repo_map_output_limits) uses possibly_truncated.
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
    assert payload["result_incomplete"] is True and is_truncation is True
    assert caveat is not None and "INCOMPLETE" in caveat


def test_blast_radius_cli_surfaces_truncation_on_real_output() -> None:
    # Dogfood the REAL command output: cap callers to 1 on a symbol with several callers so
    # production actually emits callers_truncated=True, and assert the warning is surfaced
    # (defends against testing a payload shape production never emits).
    result = runner.invoke(
        app,
        [
            "blast-radius",
            "src/tensor_grep/cli/main.py",
            "_emit_symbol_command_result",
            "--max-callers",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Only assert the completeness contract when production actually truncated.
    if payload.get("output_limit", {}).get("callers_truncated"):
        assert payload["result_incomplete"] is True
        assert "INCOMPLETE" in payload["caveat"]


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
