from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from tensor_grep.cli import lang_registry, symbols_cache
from tensor_grep.cli.detail_tier import VALID_DETAIL_TIERS, apply_detail_tier
from tensor_grep.cli.main import app


def _sample_project(root: Path) -> Path:
    project = root / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def target_fn():\n    return 1\n\ndef caller_fn():\n    return target_fn()\n",
        encoding="utf-8",
    )
    return project


def test_apply_detail_tier_compact_omits_telemetry_and_preserves_contracts() -> None:
    payload = {
        "version": 1,
        "schema_version": 1,
        "result_incomplete": True,
        "incomplete_reason": "scan cap reached",
        "incomplete_reason_class": "scan_limit",
        "resolution_gaps": [{"file": "a.py", "reason": "unparsed"}],
        "remediation": "raise --max-repo-files",
        "partial": True,
        "partial_reason": "deadline",
        "deadline_limit": {"deadline_exceeded": True},
        "definitions": [{"name": "target_fn", "line": 1}],
        "references": [{"line": 5}],
        "callers": [{"name": "caller_fn", "line": 4}],
        "caller_tree": {"depth": 1},
        "affected_files": ["a.py"],
        "primary_target": {"file": "a.py", "symbol": "target_fn"},
        "alternative_targets": [],
        "validation_plan": [{"command": "pytest"}],
        "validation_commands": ["pytest"],
        "blast_radius_floor": {"floor": 1},
        "coverage_summary": {"total": 1},
        "provider_status": {"mode": "native"},
        "symbol_cache": {"hits": 1},
        "daemon_response_cache": {"hit": False},
        "serve_cache": {"status": "bypass"},
        "rendered_caller_tree": "target_fn\n  caller_fn",
        "rendered_context": "context text",
        "graph_trust_summary": {"trust": "high"},
    }
    original = dict(payload)

    compact = apply_detail_tier(payload, "compact")

    assert payload == original
    for omitted in (
        "coverage_summary",
        "provider_status",
        "symbol_cache",
        "daemon_response_cache",
        "serve_cache",
        "rendered_caller_tree",
        "rendered_context",
        "graph_trust_summary",
    ):
        assert omitted not in compact

    for preserved in (
        "version",
        "schema_version",
        "result_incomplete",
        "incomplete_reason",
        "incomplete_reason_class",
        "resolution_gaps",
        "remediation",
        "partial",
        "partial_reason",
        "deadline_limit",
        "definitions",
        "references",
        "callers",
        "caller_tree",
        "affected_files",
        "primary_target",
        "alternative_targets",
        "validation_plan",
        "validation_commands",
        "blast_radius_floor",
    ):
        assert compact[preserved] == payload[preserved]

    assert apply_detail_tier(payload, "standard") is payload
    assert apply_detail_tier(payload, "full") is payload
    assert VALID_DETAIL_TIERS == ("compact", "standard", "full")

    with pytest.raises(typer.BadParameter):
        apply_detail_tier(payload, "invalid")


@pytest.mark.parametrize(
    "args",
    [
        ["agent", ".", "target_fn", "--detail", "invalid", "--json"],
        ["defs", ".", "target_fn", "--detail", "invalid", "--json"],
        ["impact", ".", "target_fn", "--detail", "invalid", "--json"],
        ["refs", ".", "target_fn", "--detail", "invalid", "--json"],
        ["callers", ".", "target_fn", "--detail", "invalid", "--json"],
        ["blast-radius", ".", "target_fn", "--detail", "invalid", "--json"],
    ],
)
def test_invalid_detail_flag_fails_fast_with_nonzero_exit(args: list[str]) -> None:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "Invalid --detail" in result.output


def test_cli_compact_json_reduces_defs_and_callers_by_over_half(tmp_path: Path) -> None:
    project = _sample_project(tmp_path)
    runner = CliRunner()

    for command, primary_key in (("defs", "definitions"), ("callers", "callers")):
        standard_res = runner.invoke(
            app, [command, str(project), "target_fn", "--detail", "standard", "--json"]
        )
        compact_res = runner.invoke(
            app, [command, str(project), "target_fn", "--detail", "compact", "--json"]
        )
        assert standard_res.exit_code == 0, standard_res.output
        assert compact_res.exit_code == 0, compact_res.output

        standard_bytes = len(standard_res.stdout.encode("utf-8"))
        compact_bytes = len(compact_res.stdout.encode("utf-8"))
        assert compact_bytes < standard_bytes * 0.5, (
            f"{command}: compact={compact_bytes} >= 50% of standard={standard_bytes}"
        )

        compact_payload = json.loads(compact_res.stdout)
        assert compact_payload["version"] == 1
        assert compact_payload["schema_version"] == 1
        assert compact_payload["result_incomplete"] is False
        assert compact_payload[primary_key]
        for omitted in ("coverage_summary", "provider_status", "symbol_cache"):
            assert omitted not in compact_payload


def test_cli_detail_wired_on_remaining_commands(tmp_path: Path) -> None:
    project = _sample_project(tmp_path)
    runner = CliRunner()
    for args in (
        ["agent", str(project), "target_fn", "--detail", "compact", "--json"],
        ["impact", str(project), "target_fn", "--detail", "compact", "--json"],
        ["refs", str(project), "target_fn", "--detail", "compact", "--json"],
        ["blast-radius", str(project), "target_fn", "--detail", "compact", "--json"],
    ):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output
        payload = json.loads(res.stdout)
        assert "symbol_cache" not in payload
        assert "provider_status" not in payload
        assert "coverage_summary" not in payload
        assert "rendered_caller_tree" not in payload
        assert "graph_trust_summary" not in payload


def test_symbol_generation_options_memoization(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (pkg / ".gitignore").write_text("*.log\n", encoding="utf-8")
    file_a = pkg / "a.py"
    file_b = pkg / "b.py"
    file_c = pkg / "c.js"
    file_a.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    file_b.write_text("def beta():\n    return 2\n", encoding="utf-8")
    file_c.write_text("function gamma() { return 3; }\n", encoding="utf-8")

    read_calls: list[Path] = []
    real_read_confined = symbols_cache.read_confined

    def _counting_read_confined(root: Path, path: Path, limit: int) -> bytes:
        read_calls.append(path)
        return real_read_confined(root, path, limit)

    monkeypatch.setattr(symbols_cache, "read_confined", _counting_read_confined)

    parser_calls = 0
    real_spec_for_path = lang_registry.spec_for_path

    def _counting_spec_for_path(path: Path):
        spec = real_spec_for_path(path)
        if spec is None:
            return None
        real_parser_for_path = spec.parser_for_path

        def _counting_parser(target: Path):
            nonlocal parser_calls
            parser_calls += 1
            return real_parser_for_path(target) if real_parser_for_path is not None else None

        return lang_registry.LanguageSpec(
            language_id=spec.language_id,
            suffixes=spec.suffixes,
            grammar_modules=spec.grammar_modules,
            parser_for_path=_counting_parser,
        )

    monkeypatch.setattr(lang_registry, "spec_for_path", _counting_spec_for_path)

    gen = symbols_cache.SymbolGeneration(
        tmp_path, scope=tmp_path, max_bytes=1024 * 1024, ignore_policy=[], deadline=None
    )
    opts_a = gen._options(file_a)
    opts_b = gen._options(file_b)
    opts_c = gen._options(file_c)

    assert opts_a == opts_b
    assert opts_a is opts_b
    assert opts_a["suffix"] == ".py"
    assert opts_c["suffix"] == ".js"
    assert opts_a["ignore_configuration"] == opts_c["ignore_configuration"]
    # Two .gitignore files (pkg/.gitignore + root/.gitignore) read at most once each across a.py, b.py, c.js.
    assert len(read_calls) == 2
    # parser_for_path evaluated once for .py and once for .js, not twice for .py.
    assert parser_calls == 2

    # Cache keys produced by product() match for identical content across files in the same dir/suffix.
    keys: list[str] = []
    for path in (file_a, file_b):
        file_b.write_text(file_a.read_text(encoding="utf-8"), encoding="utf-8")
        gen.product(path, lambda: ([], []))
        keys.append(gen.files[str(path)][1])
    assert keys[0] == keys[1]
