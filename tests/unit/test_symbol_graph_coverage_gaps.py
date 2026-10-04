"""Wave-2a Part G1: symbol-graph files must be handled or DISCLOSED, never silently dropped."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import lang_registry, repo_map


def _names(payload: dict[str, Any]) -> list[str]:
    return [d["name"] for d in payload["definitions"]]


# --------------------------------------------------------------------------------------------
# G1.1 shared source reader (BOM, non-UTF-8, form feed)
# --------------------------------------------------------------------------------------------


def test_python_bom_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_bytes(b"\xef\xbb\xbfdef bom_target():\n    return 1\n")
    assert _names(repo_map.build_symbol_defs("bom_target", tmp_path)) == ["bom_target"]


@pytest.mark.requires_grammar
def test_non_utf8_go_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.go").write_bytes(b"package m\n// caf\xe9\nfunc LatinTarget() {}\n")
    assert _names(repo_map.build_symbol_defs("LatinTarget", tmp_path)) == ["LatinTarget"]


@pytest.mark.requires_grammar
def test_cp1252_c_file_keeps_its_definitions(tmp_path: Path) -> None:
    (tmp_path / "m.c").write_bytes(b"/* caf\xe9 */\nint latin_c_target(void) { return 1; }\n")
    assert _names(repo_map.build_symbol_defs("latin_c_target", tmp_path)) == ["latin_c_target"]


def test_python_call_text_survives_form_feed_lines(tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        "def target():\n    return 1\n\n\x0c\ndef caller():\n    return target()\n",
        encoding="utf-8",
        newline="",
    )
    _refs, calls = repo_map._python_references_and_calls(p, "target")
    assert [c["text"] for c in calls] == ["    return target()"]


def test_read_source_text_strips_bom_and_replaces_bad_bytes(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_bytes(b"\xef\xbb\xbfa\xe9b")
    assert lang_registry.read_source_text(p) == "a\ufffdb"


def test_split_source_lines_only_splits_on_newline() -> None:
    assert lang_registry.split_source_lines("a\n\x0cb\u2028c\n") == ["a", "\x0cb\u2028c"]


# --------------------------------------------------------------------------------------------
# G1.2 coverage-gap incompleteness (over-cap, syntax error, grammar missing, lossy decode)
# --------------------------------------------------------------------------------------------

from tensor_grep.cli import lang_go  # noqa: E402
from tensor_grep.cli import main as cli_main  # noqa: E402


def _big(tmp_path: Path, name: str = "big.py", symbol: str = "big_target") -> Path:
    p = tmp_path / name
    p.write_text(f"def {symbol}():\n    return 1\n# " + "x" * 4000 + "\n", encoding="utf-8")
    return p


def test_oversize_file_with_empty_answer_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    payload = repo_map.build_symbol_defs("big_target", tmp_path)
    assert payload["no_match"] is True
    assert payload["result_incomplete"] is True
    assert "TENSOR_GREP_MAX_PARSE_BYTES" in payload["incomplete_reason"]
    assert payload["incomplete_reason_class"] == "coverage_gap"
    gap = next(g for g in payload["resolution_gaps"] if g["reason"].startswith("file(s) over"))
    assert gap["files_affected"] == 1 and gap["affects_completeness"] == "when_empty"
    assert cli_main._annotate_result_completeness(payload)[1] is True  # the exit-2 gate input


def test_oversize_gap_is_disclosed_but_not_blocking_when_answer_found(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    (tmp_path / "ok.py").write_text("def ok_target():\n    return 1\n", encoding="utf-8")
    payload = repo_map.build_symbol_defs("ok_target", tmp_path)
    assert _names(payload) == ["ok_target"]
    assert not payload.get("result_incomplete")
    assert any(g["files_affected"] == 1 for g in payload.get("resolution_gaps", []))


def test_python_syntax_error_file_empty_answer_is_incomplete(tmp_path):
    (tmp_path / "bad.py").write_text("def broken_target(:\n    pass\n", encoding="utf-8")
    payload = repo_map.build_symbol_defs("broken_target", tmp_path)
    assert payload["result_incomplete"] is True
    assert any("syntax" in g["reason"] for g in payload["resolution_gaps"])


def test_syntax_error_file_is_disclosed_on_a_found_answer(tmp_path):
    (tmp_path / "ok.py").write_text("def found_target():\n    return 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    payload = repo_map.build_symbol_defs("found_target", tmp_path)
    assert _names(payload) == ["found_target"]
    assert not payload.get("result_incomplete")
    assert any("syntax" in g["reason"] for g in payload["resolution_gaps"])


@pytest.mark.requires_grammar
def test_grammar_missing_go_with_empty_answer_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)
    (tmp_path / "m.go").write_text("package m\nfunc GoOnly() {}\n", encoding="utf-8")
    payload = repo_map.build_symbol_defs("GoOnly", tmp_path)
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert any(
        g["language"] == "go" and g["affects_completeness"] == "when_empty"
        for g in payload["resolution_gaps"]
    )


def test_php_lossy_decode_empty_answer_is_incomplete_and_found_answer_discloses(tmp_path):
    (tmp_path / "bad.php").write_bytes(b"<?php\nfunction bad\xffname() { return 1; }\n")
    payload = repo_map.build_symbol_defs("badname", tmp_path)
    assert payload["result_incomplete"] is True
    assert any("not valid UTF-8" in g["reason"] for g in payload["resolution_gaps"])
    (tmp_path / "ok.php").write_text("<?php\nfunction okname() { return 1; }\n", encoding="utf-8")
    found = repo_map.build_symbol_defs("okname", tmp_path)
    assert not found.get("result_incomplete")
    assert any("not valid UTF-8" in g["reason"] for g in found["resolution_gaps"])


def test_valid_utf8_files_have_no_lossy_gap_even_with_literal_replacement_char(tmp_path):
    (tmp_path / "ok.py").write_text("X = '\ufffd'\ndef fine():\n    return 1\n", encoding="utf-8")
    payload = repo_map.build_symbol_defs("nothing_here", tmp_path)
    assert not any("not valid UTF-8" in g["reason"] for g in payload.get("resolution_gaps", []))
    assert not payload.get("result_incomplete")


def test_scan_limit_cause_keeps_precedence_over_coverage_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    for index in range(4):
        (tmp_path / f"f{index}.py").write_text(f"def f{index}():\n    pass\n", encoding="utf-8")
    capped = repo_map.build_symbol_defs("absent_symbol", tmp_path, max_repo_files=2)
    assert capped["result_incomplete"] is True
    assert capped.get("incomplete_reason_class") != "coverage_gap"
    fresh = repo_map.build_symbol_defs("absent_symbol", tmp_path)
    assert fresh["incomplete_reason_class"] == "coverage_gap"


def test_mixed_repo_unreadable_gap_helper_is_not_raised_for_missing_file(tmp_path):
    from tensor_grep.cli import repo_map_coverage_gaps

    gaps = repo_map_coverage_gaps.source_coverage_gaps([tmp_path / "gone.py"], tmp_path)
    assert gaps and gaps[0]["reason"].startswith("file(s) could not be read")


# --------------------------------------------------------------------------------------------
# G1.2 propagation: refs/callers/blast-radius/source/impact keep the coverage cause
# --------------------------------------------------------------------------------------------

import json  # noqa: E402

from typer.testing import CliRunner  # noqa: E402


def _cli(args: list[str]):
    return CliRunner().invoke(cli_main.app, args)


def _json_of(result) -> dict[str, Any]:
    text = result.output
    return json.loads(text[text.index("{") :])


def _capped_repo(tmp_path: Path, monkeypatch, *, with_caller: bool = False) -> None:
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    (tmp_path / "defs.py").write_text("def real_target():\n    return 1\n", encoding="utf-8")
    if with_caller:
        (tmp_path / "use.py").write_text(
            "from defs import real_target\n\n\ndef run():\n    return real_target()\n",
            encoding="utf-8",
        )
    _big(tmp_path)


@pytest.mark.parametrize("command", ["refs", "callers", "blast-radius"])
def test_only_definition_in_oversize_file_keeps_gap_and_exits_2(tmp_path, monkeypatch, command):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    builder = {
        "refs": repo_map.build_symbol_refs,
        "callers": repo_map.build_symbol_callers,
        "blast-radius": repo_map.build_symbol_blast_radius,
    }[command]
    payload = builder("big_target", tmp_path)
    assert payload["result_incomplete"] is True
    gaps = payload["resolution_gaps"]
    assert gaps and gaps[0]["files_sample"] == ["big.py"]
    result = _cli([command, str(tmp_path), "big_target"])
    assert result.exit_code == 2, result.output


def test_blast_radius_found_definition_zero_callers_and_oversize_file_exits_2(
    tmp_path, monkeypatch
):
    _capped_repo(tmp_path, monkeypatch)
    payload = repo_map.build_symbol_blast_radius("real_target", tmp_path)
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert payload["resolution_gaps"][0]["files_sample"] == ["big.py"]
    assert not payload.get("caller_scan_truncated")
    assert _cli(["blast-radius", str(tmp_path), "real_target"]).exit_code == 2


@pytest.mark.parametrize("command", ["blast-radius-render", "blast-radius-plan"])
def test_render_and_plan_exit_2_with_the_parse_cap_remedy(tmp_path, monkeypatch, command):
    _capped_repo(tmp_path, monkeypatch)
    result = _cli([command, str(tmp_path), "real_target"])
    assert result.exit_code == 2, result.output
    assert "TENSOR_GREP_MAX_PARSE_BYTES" in result.output
    assert "caller_scan_truncated" not in result.output or '"caller_scan_truncated": false' in (
        result.output
    )


def test_render_syntax_error_variant_names_the_syntax_remedy(tmp_path):
    (tmp_path / "defs.py").write_text("def real_target():\n    return 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = _cli(["blast-radius-render", str(tmp_path), "real_target"])
    assert result.exit_code == 2, result.output
    assert "syntax" in result.output.lower()


@pytest.mark.parametrize("command", ["blast-radius", "blast-radius-render", "blast-radius-plan"])
def test_found_answer_with_callers_and_oversize_file_stays_exit_0(tmp_path, monkeypatch, command):
    _capped_repo(tmp_path, monkeypatch, with_caller=True)
    result = _cli([command, str(tmp_path), "real_target"])
    assert result.exit_code == 0, result.output
    assert "coverage_gap_limit" not in result.output


def test_source_mixed_repo_keeps_exit_0_and_discloses_the_gap(tmp_path, monkeypatch):
    _capped_repo(tmp_path, monkeypatch)
    result = _cli(["source", str(tmp_path), "real_target", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json_of(result)
    assert payload["sources"]
    assert payload["resolution_gaps"][0]["files_sample"] == ["big.py"]
    assert not payload.get("result_incomplete")


def test_source_empty_answer_in_oversize_file_exits_2_with_class(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    result = _cli(["source", str(tmp_path), "big_target", "--json"])
    assert result.exit_code == 2, result.output
    payload = _json_of(result)
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert payload["resolution_gaps"][0]["files_sample"] == ["big.py"]


def test_source_complete_repo_control_has_no_gap_fields(tmp_path):
    (tmp_path / "defs.py").write_text("def real_target():\n    return 1\n", encoding="utf-8")
    result = _cli(["source", str(tmp_path), "real_target", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json_of(result)
    assert not payload.get("resolution_gaps")
    assert not payload.get("result_incomplete")


def test_impact_mixed_repo_keeps_exit_0_and_discloses_the_gap(tmp_path, monkeypatch):
    _capped_repo(tmp_path, monkeypatch)
    result = _cli(["impact", str(tmp_path), "real_target", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json_of(result)
    assert payload["resolution_gaps"][0]["files_sample"] == ["big.py"]


def test_impact_empty_answer_in_oversize_file_exits_2_with_class(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1024")
    _big(tmp_path)
    result = _cli(["impact", str(tmp_path), "big_target", "--json"])
    assert result.exit_code == 2, result.output
    assert _json_of(result)["incomplete_reason_class"] == "coverage_gap"


# --------------------------------------------------------------------------------------------
# G1.2 (r26): every Python parse failure is a SyntaxError at one choke point
# --------------------------------------------------------------------------------------------

_DEEP_EXPR = "x = " + "1+" * 10000 + "1\n"  # ~20 KB: under the parse cap, overflows the parser


def test_cached_ast_parse_converts_recursion_error_and_does_not_cache_it() -> None:
    for _ in range(2):  # second call proves the failure was not cached as a success
        with pytest.raises(SyntaxError) as excinfo:
            repo_map._cached_ast_parse(_DEEP_EXPR)
        assert isinstance(excinfo.value.__cause__, RecursionError)


def test_nul_byte_source_is_a_syntax_error_at_the_choke_point() -> None:
    with pytest.raises(SyntaxError):
        repo_map._cached_ast_parse("x = 1\n\x00\n")


def test_defs_on_unparseable_deep_file_only_candidate_exits_2_naming_the_cause(tmp_path):
    (tmp_path / "deep.py").write_text(_DEEP_EXPR + "def deep_target():\n    return 1\n")
    result = _cli(["defs", "--json", str(tmp_path), "deep_target"])
    assert result.exit_code == 2, result.output
    payload = _json_of(result)
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert "RecursionError" in json.dumps(payload["resolution_gaps"])


def test_defs_on_unparseable_deep_file_mixed_repo_exits_0_with_disclosure(tmp_path):
    (tmp_path / "deep.py").write_text(_DEEP_EXPR)
    (tmp_path / "ok.py").write_text("def ok_target():\n    return 1\n")
    result = _cli(["defs", "--json", str(tmp_path), "ok_target"])
    assert result.exit_code == 0, result.output
    assert "RecursionError" in json.dumps(_json_of(result)["resolution_gaps"])


def test_imports_on_unparseable_deep_file_discloses_instead_of_crashing(tmp_path):
    p = tmp_path / "deep.py"
    p.write_text("import os\n" + _DEEP_EXPR)
    result = _cli(["imports", "--json", str(p)])
    assert result.exit_code == 2, result.output
    assert _json_of(result)["incomplete_reason_class"] == "coverage_gap"


# --------------------------------------------------------------------------------------------
# G1.2 direct extractor consumers: tg imports / tg importers
# --------------------------------------------------------------------------------------------


def test_imports_syntax_error_file_is_exit_2_with_a_syntax_gap(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("import os\ndef broken(:\n", encoding="utf-8")
    payload = repo_map.build_file_imports(p)
    assert payload["result_incomplete"] is True
    assert any("syntax" in g["reason"] for g in payload["resolution_gaps"])
    result = _cli(["imports", "--json", str(p)])
    assert result.exit_code == 2, result.output
    assert _json_of(result)["incomplete_reason_class"] == "coverage_gap"


def test_imports_valid_file_with_no_imports_stays_complete_and_exits_1(tmp_path):
    p = tmp_path / "plain.py"
    p.write_text("x = 1\n", encoding="utf-8")
    result = _cli(["imports", "--json", str(p)])
    assert result.exit_code == 1  # genuine "none" on a complete scan, result.output
    assert not _json_of(result).get("result_incomplete")


def test_imports_valid_file_keeps_its_import(tmp_path):
    p = tmp_path / "ok.py"
    p.write_text("import os\n", encoding="utf-8")
    result = _cli(["imports", "--json", str(p)])
    assert result.exit_code == 0, result.output
    assert _json_of(result)["imports"]


def test_importers_only_importer_with_syntax_error_is_exit_2_with_the_path(tmp_path):
    (tmp_path / "b.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("import b\ndef broken(:\n", encoding="utf-8")
    payload = repo_map.build_file_importers(tmp_path / "b.py", tmp_path)
    assert payload["importer_count"] == 0
    assert payload["result_incomplete"] is True
    assert "a.py" in json.dumps(payload["resolution_gaps"])


def test_importers_parseable_importer_control_stays_exit_0(tmp_path):
    (tmp_path / "b.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    payload = repo_map.build_file_importers(tmp_path / "b.py", tmp_path)
    assert payload["importer_count"] == 1
    assert not payload.get("result_incomplete")


# --------------------------------------------------------------------------------------------
# G1.2 diff-impact: a changed file whose symbols could not be extracted is never silent
# --------------------------------------------------------------------------------------------

import subprocess  # noqa: E402


def _diff_repo(tmp_path: Path, name: str, before: bytes, after: bytes) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    (repo / name).write_bytes(before)
    git("add", "--all")
    git("commit", "-qm", "i")
    (repo / name).write_bytes(after)
    return repo


def test_diff_impact_syntax_error_file_is_listed_as_unparsed(tmp_path, monkeypatch):
    repo = _diff_repo(
        tmp_path,
        "app.py",
        b"def changed():\n    return 1\n",
        b"def changed():\n    return 2\ndef broken(:\n",
    )
    monkeypatch.chdir(repo)
    result = _cli(["diff-impact", "--json"])
    assert result.exit_code == 2, result.output
    data = _json_of(result)
    assert data["unparsed_changed_files"] == ["app.py"]


def test_diff_impact_valid_file_has_no_unparsed_files(tmp_path, monkeypatch):
    repo = _diff_repo(
        tmp_path, "app.py", b"def changed():\n    return 1\n", b"def changed():\n    return 2\n"
    )
    monkeypatch.chdir(repo)
    result = _cli(["diff-impact", "--json"])
    assert result.exit_code == 0, result.output
    assert _json_of(result)["unparsed_changed_files"] == []


def test_diff_impact_non_utf8_python_file_is_a_coverage_gap(tmp_path, monkeypatch):
    repo = _diff_repo(
        tmp_path,
        "app.py",
        b"def changed():\n    return 1\n",
        b"def changed():\n    return 2\n# bad byte \xff\n",
    )
    monkeypatch.chdir(repo)
    result = _cli(["diff-impact", "--json"])
    assert result.exit_code == 2, result.output
    data = _json_of(result)
    assert data["unparsed_changed_files"] == ["app.py"]
    assert data["incomplete_reason"] == "coverage_gap"
    assert data["incomplete_reason_class"] == "coverage_gap"


@pytest.mark.requires_grammar
def test_diff_impact_grammar_missing_changed_file_is_unparsed(tmp_path, monkeypatch):
    repo = _diff_repo(
        tmp_path,
        "m.go",
        b"package m\n\nfunc Changed() int { return 1 }\n",
        b"package m\n\nfunc Changed() int { return 2 }\n",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)
    result = _cli(["diff-impact", "--json"])
    assert result.exit_code == 2, result.output
    data = _json_of(result)
    assert data["unparsed_changed_files"] == ["m.go"]
    assert data["incomplete_reason_class"] == "coverage_gap"


# --------------------------------------------------------------------------------------------
# G1.2 tg file-api goes through the same coverage-gap gate
# --------------------------------------------------------------------------------------------


def test_file_api_oversize_python_file_exits_2_with_a_disclosed_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "10")
    p = tmp_path / "big.py"
    p.write_text("def api_target():\n    return 1\n", encoding="utf-8")
    result = _cli(["file-api", "--json", str(p)])
    assert result.exit_code == 2, result.output
    payload = _json_of(result)
    assert "TENSOR_GREP_MAX_PARSE_BYTES" in json.dumps(payload["resolution_gaps"])
    assert payload["incomplete_reason_class"] == "coverage_gap"


def test_file_api_syntax_error_file_exits_2_with_a_disclosed_gap(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = _cli(["file-api", "--json", str(p)])
    assert result.exit_code == 2, result.output
    assert any("syntax" in g["reason"] for g in _json_of(result)["resolution_gaps"])


@pytest.mark.requires_grammar
def test_file_api_missing_grammar_target_exits_2_with_a_disclosed_gap(tmp_path, monkeypatch):
    p = tmp_path / "m.go"
    p.write_text("package m\nfunc A() {}\n", encoding="utf-8")
    spec = lang_registry.spec_for_path(p)
    assert spec is not None and spec.parser_for_path is not None  # premise: the probe is _go_parser
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)
    assert spec.parser_for_path(p) is None  # the patched loader IS the one the gap builder calls
    result = _cli(["file-api", "--json", str(p)])
    assert result.exit_code == 2, result.output
    assert any(g["language"] == "go" for g in _json_of(result)["resolution_gaps"])


def test_file_api_empty_parseable_file_stays_exit_0_and_one_def_is_complete(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("", encoding="utf-8")
    result = _cli(["file-api", "--json", str(empty)])
    assert result.exit_code == 0, result.output
    assert not _json_of(result).get("resolution_gaps")
    one = tmp_path / "one.py"
    one.write_text("def solo():\n    return 1\n", encoding="utf-8")
    result = _cli(["file-api", "--json", str(one)])
    assert result.exit_code == 0, result.output
    assert _json_of(result)["symbol_count"] == 1


# --------------------------------------------------------------------------------------------
# G1.2 remaining extractor consumers: capsule primary file, --enrich-ast
# --------------------------------------------------------------------------------------------


def test_enrichment_marks_a_match_in_an_unparseable_file(tmp_path):
    from tensor_grep.cli import ast_enrichment

    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    needle = 1\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("def fine():\n    needle = 1\n", encoding="utf-8")
    items = [
        {"path": str(bad), "line_number": 2},
        {"path": str(good), "line_number": 2},
    ]
    enriched, _diag = ast_enrichment.enrich_search_items_with_containers([bad, good], items)
    assert enriched[0]["enclosing_symbol_status"] == "unparsed"
    assert "enclosing_symbol_status" not in enriched[1]
    assert enriched[1]["container"]["name"] == "fine"


def test_capsule_on_a_syntax_error_primary_file_discloses_it(tmp_path):
    from tensor_grep.cli import agent_capsule

    (tmp_path / "bad.py").write_text("def capsule_target(:\n    return 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def unrelated():\n    return 2\n", encoding="utf-8")
    payload = agent_capsule.build_agent_capsule("capsule_target", tmp_path)
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert any("syntax" in g["reason"] for g in payload["resolution_gaps"])


def test_capsule_on_a_valid_primary_file_is_unchanged(tmp_path):
    from tensor_grep.cli import agent_capsule

    (tmp_path / "ok.py").write_text("def capsule_target():\n    return 1\n", encoding="utf-8")
    payload = agent_capsule.build_agent_capsule("capsule_target", tmp_path)
    assert not payload.get("result_incomplete")
    assert "resolution_gaps" not in payload


# --------------------------------------------------------------------------------------------
# G1.2 (r30): JS/Rust source readers join the shared reader; `tg source` judges its own answer
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "body", "symbol"),
    [
        (
            "box.js",
            b"// caf\xe9\nclass Box {\n  method(x) { return x; }\n}\n",
            "method",
        ),
        (
            "lib.rs",
            b"// caf\xe9\nstruct S;\nimpl S {\n    fn method(&self) -> i32 { 1 }\n}\n",
            "method",
        ),
    ],
)
def test_source_for_a_method_in_a_file_with_an_invalid_byte_returns_its_source(
    tmp_path, name, body, symbol
):
    (tmp_path / name).write_bytes(body)
    result = _cli(["source", "--json", str(tmp_path), symbol])
    assert result.exit_code == 0, result.output
    assert _json_of(result)["sources"]


def test_source_valid_utf8_js_control(tmp_path):
    (tmp_path / "box.js").write_text(
        "class Box {\n  method(x) { return x; }\n}\n", encoding="utf-8"
    )
    result = _cli(["source", "--json", str(tmp_path), "method"])
    assert result.exit_code == 0, result.output
    assert _json_of(result)["sources"]


def test_source_definition_found_but_extraction_empty_with_blocking_gap_exits_2(tmp_path):
    # A stale symbol row (the definition exists in the map but the extractor finds no source for
    # it) in a file that also carries a blocking lossy-decode gap: the EMPTY source answer must
    # be incomplete, not a quiet exit 1.
    bad = tmp_path / "bad.py"
    bad.write_bytes(b"def other():\n    return 1\n# caf\xe9\n")
    rmap = repo_map.build_repo_map(tmp_path)
    rmap["symbols"] = [
        *rmap["symbols"],
        {"name": "ghost", "kind": "function", "file": str(bad), "line": 1},
    ]
    payload = repo_map.build_symbol_source_from_map(rmap, "ghost")
    assert payload["definitions"] and not payload["sources"]
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason_class"] == "coverage_gap"
    assert "bad.py" in json.dumps(payload["resolution_gaps"])
    assert cli_main._annotate_result_completeness(payload)[1] is True


# --------------------------------------------------------------------------------------------
# G1.3 (r22): every site that indexes by AST / tree-sitter line splits on newline only
# --------------------------------------------------------------------------------------------

_FF = chr(0x0C)
_LS = chr(0x2028)


def test_python_symbol_source_survives_a_form_feed_line(tmp_path):
    (tmp_path / "m.py").write_text(
        "x = 1\n" + _FF + "\ndef alpha():\n    return 1\n", encoding="utf-8", newline=""
    )
    sources = repo_map._python_symbol_sources(tmp_path / "m.py", "alpha")
    assert [s["source"].strip() for s in sources] == ["def alpha():\n    return 1"]


def test_python_alias_call_keeps_its_line_after_a_form_feed(tmp_path):
    (tmp_path / "m.py").write_text(
        "def target():\n    return 1\n"
        + _FF
        + "\nalias = target\ndef run():\n    return alias()\n",
        encoding="utf-8",
        newline="",
    )
    calls = repo_map._python_provider_alias_calls(tmp_path / "m.py", "target")
    assert [c["text"] for c in calls] == ["    return alias()"]


def test_python_symbol_source_and_alias_controls_without_separators(tmp_path):
    (tmp_path / "m.py").write_text(
        "x = 1\ndef alpha():\n    return 1\n" + "s = 'a" + _LS + "b'\n", encoding="utf-8"
    )
    sources = repo_map._python_symbol_sources(tmp_path / "m.py", "alpha")
    assert [s["source"].strip() for s in sources] == ["def alpha():\n    return 1"]


# --------------------------------------------------------------------------------------------
# G1.4 .mts/.cts register as TypeScript end to end; resolver-less importers are UNKNOWN
# --------------------------------------------------------------------------------------------

_GENERIC_METHOD = "class Box { method<T>(x: T): T { return x; } }\n"


@pytest.mark.parametrize("suffix", [".ts", ".mts", ".cts"])
def test_typescript_family_suffixes_give_defs_and_complete_source(tmp_path, suffix):
    (tmp_path / f"box{suffix}").write_text(_GENERIC_METHOD, encoding="utf-8")
    defs = _cli(["defs", "--json", str(tmp_path), "method"])
    assert defs.exit_code == 0, defs.output
    assert _json_of(defs)["definitions"]
    source = _cli(["source", "--json", str(tmp_path), "method"])
    assert source.exit_code == 0, source.output
    sources = _json_of(source)["sources"]
    assert sources and "return x" in json.dumps(sources)


def test_mjs_class_method_still_uses_the_javascript_grammar(tmp_path):
    (tmp_path / "box.mjs").write_text(
        "class Box {\n  method(x) { return x; }\n}\n", encoding="utf-8"
    )
    result = _cli(["source", "--json", str(tmp_path), "method"])
    assert result.exit_code == 0, result.output
    assert "return x" in json.dumps(_json_of(result)["sources"])


@pytest.mark.parametrize("suffix", [".mts", ".cts", ".ts"])
def test_inventory_classifies_ts_family_as_typescript_code(tmp_path, suffix):
    from tensor_grep.cli import inventory

    assert inventory._LANGUAGE_BY_SUFFIX[suffix] == "typescript"
    assert suffix in inventory._CODE_SUFFIXES


# --------------------------------------------------------------------------------------------
# G1 audit fix: "empty answer" is decided from ALL the answer's evidence
# --------------------------------------------------------------------------------------------


def _import_only_repo(tmp_path: Path, *, with_bad: bool = True) -> None:
    (tmp_path / "defs.py").write_text("def target():\n    pass\n", encoding="utf-8")
    (tmp_path / "use.py").write_text("from defs import target\n", encoding="utf-8")
    if with_bad:
        (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")


@pytest.mark.parametrize("command", ["callers", "blast-radius"])
def test_import_only_consumer_answer_is_not_empty_and_never_exit_2(tmp_path, command):
    _import_only_repo(tmp_path)
    result = _cli([command, "--json", str(tmp_path), "target"])
    # Zero DIRECT callers exits 1 on main with or without the bad file (the CLI's own
    # not-found semantics for the callers list); the point is that the gap never makes it 2.
    (tmp_path / "bad.py").unlink()
    control = _cli([command, "--json", str(tmp_path), "target"])
    assert result.exit_code == control.exit_code != 2, result.output
    payload = _json_of(result)
    assert payload["import_graph_consumer_count"] == 1
    assert not payload.get("result_incomplete")
    assert any("syntax" in g["reason"] for g in payload["resolution_gaps"])


@pytest.mark.parametrize("command", ["callers", "blast-radius"])
def test_direct_call_answer_with_a_gap_stays_exit_0(tmp_path, command):
    (tmp_path / "defs.py").write_text("def target():\n    pass\n", encoding="utf-8")
    (tmp_path / "use.py").write_text(
        "from defs import target\n\n\ndef run():\n    return target()\n", encoding="utf-8"
    )
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    result = _cli([command, "--json", str(tmp_path), "target"])
    assert result.exit_code == 0, result.output
    assert not _json_of(result).get("result_incomplete")


@pytest.mark.parametrize("command", ["callers", "blast-radius"])
def test_truly_empty_callers_answer_with_a_gap_exits_2(tmp_path, command):
    _import_only_repo(tmp_path)
    (tmp_path / "use.py").unlink()
    result = _cli([command, "--json", str(tmp_path), "target"])
    assert result.exit_code == 2, result.output


def test_answer_empty_helper_counts_every_evidence_key():
    from tensor_grep.cli import repo_map_coverage_gaps as cg

    assert cg.answer_empty({"callers": [], "import_graph_consumers": [1]}, "callers") is False
    assert cg.answer_empty({"callers": [], "import_graph_consumers": []}, "callers") is True
    assert cg.answer_empty({"references": [], "string_refs": [1]}, "refs") is False
    assert cg.answer_empty({"references": [1]}, "refs") is False
    assert cg.answer_empty({"references": [], "string_refs": []}, "refs") is True
