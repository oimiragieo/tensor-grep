import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import (
    app,
)
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_cli_json_output_should_prefer_runtime_backend_metadata_over_pipeline_selection(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "ERROR -> eventually ERROR", ".", "--ltl", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "CPUBackend"
    assert payload["routing_reason"] == "torch_regex_cpu_fallback"
    assert payload["routing_gpu_device_ids"] == []
    assert payload["routing_gpu_chunk_plan_mb"] == []
    assert payload["routing_distributed"] is False
    assert payload["routing_worker_count"] == 1


def test_cli_debug_should_print_runtime_routing_when_backend_falls_back(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert "[debug] routing.backend=FakeBackend reason=unit_test_fake_pipeline" in result.output
    assert (
        "[debug] routing.runtime backend=CPUBackend reason=torch_regex_cpu_fallback"
        in result.output
    )


def test_cli_stats_should_prefer_runtime_backend_metadata_when_backend_falls_back(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CPUBackend",
                routing_reason="torch_regex_cpu_fallback",
                routing_gpu_device_ids=[],
                routing_gpu_chunk_plan_mb=[],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] backend=CPUBackend reason=torch_regex_cpu_fallback" in result.output
    assert "[stats] gpu_device_ids=" not in result.output


def test_cli_debug_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPlanOnlyPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert (
        "[debug] routing.gpu_device_ids=[] routing.gpu_chunk_plan_mb=[(7, 256), (3, 512)]"
        in result.output
    )


def test_cli_stats_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPlanOnlyPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--stats"])

    assert result.exit_code == 0
    assert (
        "[stats] backend=RipgrepBackend reason=gpu_explicit_ids_no_gpu_backend_fallback"
        in result.output
    )
    assert (
        "[stats] gpu_device_ids=[] gpu_chunk_plan_mb=[(7, 256), (3, 512)] distributed=True workers=2"
        in result.output
    )


def test_cli_json_output_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "ERROR -> eventually ERROR", ".", "--ltl", "--format", "json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "CuDFBackend"
    assert payload["routing_reason"] == "cudf_chunked_single_worker_plan"
    assert payload["routing_gpu_device_ids"] == [3]
    assert payload["routing_gpu_chunk_plan_mb"] == [{"device_id": 3, "chunk_mb": 1}]
    assert payload["routing_distributed"] is False
    assert payload["routing_worker_count"] == 1


def test_cli_debug_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--debug", "--ltl"])

    assert result.exit_code == 0
    assert (
        "[debug] routing.runtime backend=CuDFBackend reason=cudf_chunked_single_worker_plan"
        in result.output
    )
    assert (
        "[debug] routing.runtime.gpu_device_ids=[3] routing.runtime.gpu_chunk_plan_mb=[(3, 1)] distributed=False workers=1"
        in result.output
    )


def test_cli_stats_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan(
    monkeypatch,
):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
                routing_backend="CuDFBackend",
                routing_reason="cudf_chunked_single_worker_plan",
                routing_gpu_device_ids=[3],
                routing_gpu_chunk_plan_mb=[(3, 1)],
                routing_distributed=False,
                routing_worker_count=1,
            )
        }
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeGpuPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 0
    assert "[stats] backend=CuDFBackend reason=cudf_chunked_single_worker_plan" in result.output
    assert (
        "[stats] gpu_device_ids=[3] gpu_chunk_plan_mb=[(3, 1)] distributed=False workers=1"
        in result.output
    )


def test_cli_stats_prints_summary_when_no_matches(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR -> eventually ERROR", ".", "--stats", "--ltl"])

    assert result.exit_code == 1
    assert "[stats] scanned_files=1 matched_files=0 total_matches=0" in result.output
    assert "[stats] backend=FakeBackend reason=unit_test_fake_pipeline" in result.output


def test_scan_executes_rules_from_sgconfig(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "[scan] rule=error-rule lang=python matches=1 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_rulesets_json_lists_builtin_rule_packs():
    runner = CliRunner()

    result = runner.invoke(app, ["rulesets", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    rulesets = {ruleset["name"]: ruleset for ruleset in payload["rulesets"]}
    assert set(rulesets) == {
        "auth-safe",
        "crypto-safe",
        "deserialization-safe",
        "secrets-basic",
        "subprocess-safe",
        "tls-safe",
    }
    assert rulesets["auth-safe"]["category"] == "security"
    assert "python" in rulesets["auth-safe"]["languages"]
    assert rulesets["auth-safe"]["rule_count"] >= 1


def test_scan_executes_builtin_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "crypto-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset crypto-safe (python)" in result.output
    assert "[scan] rule=python-hashlib-md5 lang=python matches=1 files=1" in result.output
    assert "[scan] rule=python-hashlib-sha1 lang=python matches=0 files=0" in result.output
    assert "Scan completed. rules=2 matched_rules=1 total_matches=1" in result.output


def test_scan_ruleset_refuses_direct_temp_root_before_walking(monkeypatch, tmp_path: Path):
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    (temp_root / "a.py").write_text("API_KEY = 'secret'\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _ExplodingAstScanner)

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "secrets-basic",
            "--language",
            "python",
            "--path",
            str(temp_root),
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "broad AST scan refused" in result.output
    assert "safety guard, not a zero-match result" in result.output
    assert "Temp" in result.output
    assert "--max-depth" in result.output
    assert "--allow-broad-generated-scan" in result.output


def test_scan_ruleset_allows_depth_bounded_temp_root(monkeypatch, tmp_path: Path):
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    monkeypatch.chdir(temp_root)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    Path("a.py").write_text('password = "$SECRET"\n', encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--ruleset",
            "secrets-basic",
            "--language",
            "python",
            "--path",
            str(temp_root),
            "--max-depth",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "secrets-basic"
    assert payload["total_matches"] >= 1


def test_scan_builtin_ruleset_can_emit_json(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == payload["version"]
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "crypto-safe"
    assert payload["rule_count"] == 2
    assert payload["matched_rules"] == 1
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["rule_id"] == "python-hashlib-md5"
    assert payload["findings"][0]["severity"] == "high"
    assert "hashlib.md5" in payload["findings"][0]["message"]
    assert (
        payload["findings"][0]["fingerprint"]
        == hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert payload["findings"][0]["files"] == ["a.py"]
    assert payload["findings"][0]["evidence"] == [{"file": "a.py", "match_count": 1}]


def test_scan_builtin_ruleset_can_emit_evidence_snippets(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--include-evidence-snippets",
                "--max-evidence-snippets-per-file",
                "1",
                "--max-evidence-snippet-chars",
                "12",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["evidence"][0]["snippets"] == [
        {"text": "hashlib.md5(", "truncated": True}
    ]


def test_scan_supports_inline_rules_text(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "rule:",
        "  pattern: print($A)",
    ])
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert "[scan] rule=no-print lang=python matches=1 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_scan_supports_single_rule_file_and_positional_path(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    rule_file = tmp_path / "no_print.yml"
    rule_file.write_text(
        "\n".join([
            "id: no-print",
            "language: python",
            "rule:",
            "  pattern: print($A)",
        ]),
        encoding="utf-8",
    )
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["scan", "--rule", str(rule_file), str(source_root), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "ast-single-rule-scan"
    assert payload["findings"][0]["rule_id"] == "no-print"
    assert payload["findings"][0]["matches"] == 1


def test_scan_filter_limits_project_rules(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = config
            total = 1 if pattern == "print($A)" else 0
            return SearchResult(
                matches=[],
                matched_file_paths=["app.py"] if total else [],
                total_files=1 if total else 0,
                total_matches=total,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "no_print.yml").write_text(
        "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)\n",
        encoding="utf-8",
    )
    (tmp_path / "rules" / "no_eval.yml").write_text(
        "id: no-eval\nlanguage: python\nrule:\n  pattern: eval($A)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["scan", "--config", str(tmp_path / "sgconfig.yml"), "--filter", "print", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rule_count"] == 1
    assert [finding["rule_id"] for finding in payload["findings"]] == ["no-print"]


def test_scan_project_filter_respects_positional_scan_paths(monkeypatch, tmp_path: Path) -> None:
    class CountingAstBackend:
        def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
            _ = config
            try:
                lines = Path(file_path).read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            matches = [
                MatchLine(line_number=line_number, text=line, file=file_path)
                for line_number, line in enumerate(lines, start=1)
                if pattern in line
            ]
            total_matches = sum(line.count(pattern) for line in lines)
            return SearchResult(
                matches=matches,
                matched_file_paths=[file_path] if total_matches else [],
                total_files=1 if total_matches else 0,
                total_matches=total_matches,
                routing_backend="AstBackend",
                routing_reason="ast_native",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    src_dir = tmp_path / "src"
    rules_dir = tmp_path / "rules"
    src_dir.mkdir()
    rules_dir.mkdir()
    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    (rules_dir / "no-pass.yml").write_text(
        "\n".join([
            "id: no-pass",
            "language: python",
            "message: avoid pass",
            "rule:",
            "  pattern: pass",
        ]),
        encoding="utf-8",
    )
    (src_dir / "sample.py").write_text("def f():\n    pass\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "scan",
            "--config",
            str(tmp_path / "sgconfig.yml"),
            "--filter",
            "no-pass",
            str(src_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["scan_paths"] == [str(src_dir.resolve())]
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["files"] == [str((src_dir / "sample.py").resolve())]
    assert all(
        "rules" not in Path(file_path).parts for file_path in payload["findings"][0]["files"]
    )


def test_scan_inline_rules_json_preserves_rule_metadata(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = config
            matches: list[MatchLine] = []
            matched_file_paths: list[str] = []

            for file_path in file_paths:
                candidate = Path(file_path)
                expanded_paths = (
                    sorted(str(path) for path in candidate.rglob("*") if path.is_file())
                    if candidate.is_dir()
                    else [file_path]
                )
                for expanded_path in expanded_paths:
                    content = Path(expanded_path).read_text(encoding="utf-8")
                    if pattern == "print($A)" and "print(" in content:
                        matched_file_paths.append(expanded_path)
                        matches.append(
                            MatchLine(line_number=1, text="print('hello')", file=expanded_path)
                        )

            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                total_files=len(matched_file_paths),
                total_matches=len(matches),
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "severity: warning",
        "message: Avoid print in library code.",
        "rule:",
        "  pattern: print($A)",
    ])
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    finding = payload["findings"][0]
    assert finding["rule_id"] == "no-print"
    assert finding["severity"] == "warning"
    assert finding["message"] == "Avoid print in library code."


@pytest.mark.parametrize(
    ("ast_grep_language", "normalized_language"),
    [
        ("Python", "python"),
        ("JavaScript", "javascript"),
        ("TypeScript", "typescript"),
        ("Tsx", "tsx"),
        ("Go", "go"),
        ("Rust", "rust"),
    ],
)
def test_scan_inline_rules_normalizes_ast_grep_language_names(
    monkeypatch,
    tmp_path: Path,
    ast_grep_language: str,
    normalized_language: str,
) -> None:
    seen_config_languages: list[str | None] = []

    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            seen_config_languages.append(config.lang if config is not None else None)
            return SearchResult(
                matches=[],
                matched_file_paths=[],
                total_files=0,
                total_matches=0,
                routing_backend="AstGrepWrapperBackend",
                routing_reason="ast_grep_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    inline_rules = "\n".join([
        "id: normalized-language",
        f"language: {ast_grep_language}",
        "rule:",
        "  pattern: ERROR",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["language"] == normalized_language
    assert seen_config_languages == [normalized_language]


def test_scan_inline_rules_rejects_unsupported_language(tmp_path: Path) -> None:
    inline_rules = "\n".join([
        "id: unsupported-language",
        "language: Dart",
        "rule:",
        "  pattern: print($A)",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error: Unsupported AST language Dart" in result.output
    assert "Traceback" not in result.output


def test_scan_inline_rules_reports_invalid_yaml_without_traceback(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", "id: broken\nrule: [", "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "YAML" in result.output
    assert "Traceback" not in result.output


def test_scan_rule_file_reports_invalid_yaml_without_traceback(tmp_path: Path) -> None:
    rule_file = tmp_path / "broken.yml"
    rule_file.write_text("id: broken\nrule: [", encoding="utf-8")

    result = CliRunner().invoke(app, ["scan", "--rule", str(rule_file), str(tmp_path)])

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "YAML" in result.output
    assert "Traceback" not in result.output


def test_scan_wrapper_runtime_errors_do_not_show_traceback(monkeypatch, tmp_path: Path) -> None:
    class AstGrepWrapperBackend:
        def search_many(self, file_paths: list[str], pattern: str, config=None) -> SearchResult:
            _ = file_paths
            _ = pattern
            _ = config
            raise RuntimeError("ast-grep failed with exit code 8: invalid language")

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )
    inline_rules = "\n".join([
        "id: wrapper-error",
        "language: Python",
        "rule:",
        "  pattern: print($A)",
    ])

    result = CliRunner().invoke(
        app,
        ["scan", "--inline-rules", inline_rules, "--path", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "Error: ast-grep failed with exit code 8: invalid language" in result.output
    assert "Traceback" not in result.output


def test_scan_builtin_ruleset_can_compare_and_write_baseline(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        Path("old-baseline.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "ruleset-scan-baseline",
                    "ruleset": "crypto-safe",
                    "language": "python",
                    "fingerprints": [
                        hashlib.sha256(
                            json.dumps(
                                {
                                    "rule_id": "python-hashlib-md5",
                                    "language": "python",
                                    "files": ["a.py"],
                                },
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest(),
                        "resolved-fingerprint",
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--baseline",
                "old-baseline.json",
                "--write-baseline",
                "new-baseline.json",
            ],
        )

        written = json.loads(Path("new-baseline.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["status"] == "existing"
    assert payload["findings"][1]["status"] == "clear"
    assert payload["baseline"]["new_findings"] == 0
    assert payload["baseline"]["existing_findings"] == 1
    assert payload["baseline"]["resolved_findings"] == 1
    assert payload["baseline"]["resolved_fingerprints"] == ["resolved-fingerprint"]
    assert payload["baseline_written"]["count"] == 1
    assert written["kind"] == "ruleset-scan-baseline"
    assert written["fingerprints"] == [payload["findings"][0]["fingerprint"]]


def test_scan_builtin_ruleset_can_apply_suppressions(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        Path("suppressions.json").write_text(
            json.dumps(
                {"version": 1, "kind": "ruleset-scan-suppressions", "fingerprints": [fingerprint]},
                indent=2,
            ),
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--suppressions",
                "suppressions.json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["findings"][1]["status"] == "clear"
    assert payload["suppressions"]["suppressed_findings"] == 1


def test_scan_builtin_ruleset_can_write_suppressions(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--write-suppressions",
                "written-suppressions.json",
                "--justification",
                "Approved suppression for fixture coverage.",
            ],
        )

        written = json.loads(Path("written-suppressions.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["suppressions_written"]["count"] == 1
    assert written["kind"] == "ruleset-scan-suppressions"
    assert written["entries"][0]["fingerprint"] == payload["findings"][0]["fingerprint"]
    assert written["entries"][0]["justification"] == "Approved suppression for fixture coverage."
    assert written["entries"][0]["created_at"].endswith("Z")


def test_scan_builtin_ruleset_write_suppressions_requires_justification(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "crypto-safe",
                "--language",
                "python",
                "--path",
                ".",
                "--json",
                "--write-suppressions",
                "written-suppressions.json",
            ],
        )

    assert result.exit_code == 1
    assert "justification" in result.output


def test_scan_executes_secrets_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('password = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset secrets-basic (python)" in result.output
    assert "[scan] rule=python-hardcoded-password lang=python matches=1 files=1" in result.output
    assert "[scan] rule=python-hardcoded-api-key lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-hardcoded-api-key-uppercase lang=python matches=0 files=0"
        in result.output
    )
    assert "[scan] rule=python-hardcoded-token lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-hardcoded-provider-token lang=python matches=0 files=0" in result.output
    )
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=0 files=0" in result.output
    )
    assert "Scan completed. rules=6 matched_rules=1 total_matches=1" in result.output


def test_scan_ruleset_respects_filter(monkeypatch):
    # audit #22: --filter was applied only on the sgconfig project-scan path (and explicitly
    # rejected for --rule) -- silently ignored for --ruleset, so a --ruleset run always scanned
    # every rule in the pack regardless of --filter.
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('password = "$SECRET"\n', encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--ruleset",
                "secrets-basic",
                "--language",
                "python",
                "--path",
                ".",
                "--filter",
                "password",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "[scan] rule=python-hardcoded-password" in result.output
    assert "python-hardcoded-api-key " not in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_scan_inline_rules_respects_filter(monkeypatch):
    # audit #22: same uniform application as --ruleset, for --inline-rules.
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    # `eval($A)` here is an ast-grep RULE PATTERN (a literal YAML string matched structurally
    # against scanned source, mirroring test_scan_inline_rules_json_preserves_rule_metadata above)
    # -- it is never passed to Python's eval() or executed.
    inline_rules = (
        "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)\n"
        "---\n"
        "id: no-eval\nlanguage: python\nrule:\n  pattern: eval($A)\n"
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        # `_FakeAstBackend.search` (via the patched Pipeline) matches on literal substring, so the
        # fixture content matches the rule pattern text exactly.
        Path("a.py").write_text("print($A)\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "scan",
                "--inline-rules",
                inline_rules,
                "--path",
                ".",
                "--filter",
                "no-print",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "[scan] rule=no-print" in result.output
    assert "no-eval" not in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=1" in result.output


def test_scan_executes_secrets_ruleset_uppercase_api_key(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('API_KEY = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-api-key-uppercase lang=python matches=1 files=1"
        in result.output
    )


def test_scan_executes_secrets_ruleset_api_key_pattern(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('const apiKey = "$SECRET"\n', encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "javascript", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=javascript-hardcoded-api-key lang=javascript matches=1 files=1"
        in result.output
    )


def test_scan_executes_secrets_ruleset_generic_provider_token_regex(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('stripe_secret = "sk_live_1234567890abcdef"\n', encoding="utf-8")
        Path("b.py").write_text("# leaked token sk_live_abcdef1234567890\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-provider-token lang=python matches=2 files=2" in result.output
    )


def test_scan_executes_secrets_ruleset_prefixed_api_key_regex(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text(
            'OPENAI_API_KEY = "fake_test_key_123456"\nHEADER_NAME = "not-a-secret-value"\n',
            encoding="utf-8",
        )

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=1 files=1" in result.output
    )
    assert "HEADER_NAME" not in result.output


def test_scan_executes_secrets_ruleset_fake_api_key_snake_case(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text('fake_api_key = "fake_test_key_123456"\n', encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "secrets-basic", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-hardcoded-named-api-key lang=python matches=1 files=1" in result.output
    )


def test_scan_executes_tls_ruleset(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ssl._create_unverified_context()\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "tls-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert "Scanning project using built-in ruleset tls-safe (python)" in result.output
    assert (
        "[scan] rule=python-unverified-ssl-context lang=python matches=1 files=1" in result.output
    )
    assert "[scan] rule=python-requests-verify-false lang=python matches=0 files=0" in result.output
    assert (
        "[scan] rule=python-requests-post-verify-false lang=python matches=0 files=0"
        in result.output
    )
    assert "Scan completed. rules=3 matched_rules=1 total_matches=1" in result.output


def test_scan_executes_tls_ruleset_requests_post_pattern(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("requests.post($URL, verify=False)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["scan", "--ruleset", "tls-safe", "--language", "python", "--path", "."],
        )

    assert result.exit_code == 0
    assert (
        "[scan] rule=python-requests-post-verify-false lang=python matches=1 files=1"
        in result.output
    )


def test_scan_should_not_claim_gnns_when_ast_wrapper_backend_selected(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    AstGrepWrapperBackend.search_project_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "GPU-Accelerated GNNs" not in result.output
    assert AstGrepWrapperBackend.search_project_calls == 1
    assert AstGrepWrapperBackend.search_many_calls == 0
    assert _FakeAstScanner.walk_calls == 1


def test_scan_json_should_use_wrapper_project_fast_path(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    AstGrepWrapperBackend.search_project_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml", "--json"])

    assert result.exit_code == 0
    assert AstGrepWrapperBackend.search_project_calls == 1
    assert AstGrepWrapperBackend.search_many_calls == 0
    assert _FakeAstScanner.walk_calls == 1


def test_scan_should_count_files_from_count_only_ast_results(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeCountOnlyAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "[scan] rule=error-rule lang=python matches=1 files=1" in result.output


def test_run_should_not_warn_when_ast_wrapper_backend_selected(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    AstGrepWrapperBackend.search_many_calls = 0
    _FakeAstScanner.walk_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Warning:" not in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1
    assert _FakeAstScanner.walk_calls == 0


def test_run_should_report_ast_wrapper_backend_mode(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Executing ast-grep structural matching run..." in result.output
    assert "GPU-Accelerated AST-Grep Run" not in result.output


def test_run_should_use_native_first_ast_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert _CapturingAstPipeline.last_config.query_pattern == "ERROR"


def test_run_should_report_native_ast_backend_mode_without_gnns(monkeypatch):
    _patch_direct_native_execution(monkeypatch)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["run", "ERROR", "."])

    assert result.exit_code == 0
    assert "Executing native AST matching run..." in result.output
    assert "GPU-Accelerated GNNs" not in result.output


def test_run_should_emit_rewrite_plan_without_apply(monkeypatch):
    from tensor_grep.cli import ast_workflows

    seen: dict[str, str] = {}

    def _fake_execute_rewrite_plan_json(
        *,
        pattern: str,
        replacement: str,
        lang: str,
        path: str,
    ) -> tuple[str, int]:
        seen.update({
            "pattern": pattern,
            "replacement": replacement,
            "lang": lang,
            "path": path,
        })
        return '{"total_edits": 1, "edits": []}', 0

    monkeypatch.setattr(ast_workflows, "execute_rewrite_plan_json", _fake_execute_rewrite_plan_json)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("a.py").write_text("def add(x, y): return x + y\n", encoding="utf-8")
        result = runner.invoke(
            app,
            [
                "run",
                "--lang",
                "python",
                "--rewrite",
                "lambda $$$ARGS: $EXPR",
                "def $F($$$ARGS): return $EXPR",
                "a.py",
            ],
        )

    assert result.exit_code == 0
    assert '"total_edits": 1' in result.output
    assert "Executing " not in result.output
    assert seen == {
        "pattern": "def $F($$$ARGS): return $EXPR",
        "replacement": "lambda $$$ARGS: $EXPR",
        "lang": "python",
        "path": "a.py",
    }


def test_ast_rust_language_support_matrix(monkeypatch):
    from tensor_grep.cli.ast_workflows import _select_ast_backend_name_for_pattern

    monkeypatch.setattr("tensor_grep.cli.ast_workflows._check_backend_available", lambda name: True)

    # Native S-expression for Rust (supported by PyO3/tree-sitter)
    backend_native = _select_ast_backend_name_for_pattern("(function_item) @match", "rust")
    assert backend_native == "AstBackend"

    # Ast-grep specific string query with variadic params (supported by ast-grep CLI wrapper)
    backend_wrapper = _select_ast_backend_name_for_pattern("fn $F($$$ARGS)", "rust")
    assert backend_wrapper == "AstGrepWrapperBackend"


def test_test_command_should_report_ast_wrapper_backend_mode(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "Testing AST rules using ast-grep structural matching" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_test_command_should_report_native_ast_backend_mode_without_gnns(monkeypatch):
    _patch_direct_native_execution(monkeypatch)

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "Testing AST rules using native AST matching" in result.output
    assert "GPU-Accelerated GNNs" not in result.output


def test_test_command_should_batch_wrapper_backend_once_per_case(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\n  - all good\ninvalid:\n  - ERROR in file\n  - another ERROR\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=4" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_test_command_should_batch_wrapper_backend_across_cases_for_same_rule(monkeypatch):
    _patch_direct_wrapper_selection(monkeypatch)
    AstGrepWrapperBackend.search_many_calls = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "tests:\n"
            "  - id: error-test-1\n"
            "    ruleId: error-rule\n"
            "    valid:\n"
            "      - ok\n"
            "    invalid:\n"
            "      - ERROR in file\n"
            "  - id: error-test-2\n"
            "    ruleId: error-rule\n"
            "    valid:\n"
            "      - still ok\n"
            "    invalid:\n"
            "      - another ERROR\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert "All tests passed. cases=4" in result.output
    assert AstGrepWrapperBackend.search_many_calls == 1


def test_scan_should_prefer_native_ast_backend_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("ERROR in file\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert any(cfg and cfg.query_pattern == "ERROR" for cfg in _CapturingAstPipeline.seen_configs)


def test_test_command_should_prefer_native_ast_backend_policy(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/error.yml").write_text(
            "id: error-rule\nlanguage: python\nrule:\n  pattern: ERROR\n",
            encoding="utf-8",
        )
        Path("tests/error.yml").write_text(
            "id: error-test\nruleId: error-rule\nvalid:\n  - ok\ninvalid:\n  - ERROR in file\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.last_config is not None
    assert _CapturingAstPipeline.last_config.ast_prefer_native is True
    assert any(cfg and cfg.query_pattern == "ERROR" for cfg in _CapturingAstPipeline.seen_configs)


def test_scan_should_reuse_native_ast_backend_selection_for_multiple_native_patterns(monkeypatch):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/rule_a.yml").write_text(
            "id: rule-a\nlanguage: python\nrule:\n  pattern: function_definition\n",
            encoding="utf-8",
        )
        Path("rules/rule_b.yml").write_text(
            "id: rule-b\nlanguage: python\nrule:\n  pattern: class_definition\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("function_definition\nclass_definition\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.init_count == 1


def test_test_command_should_reuse_wrapper_backend_selection_for_multiple_ast_grep_patterns(
    monkeypatch,
):
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _CapturingAstPipeline)
    _CapturingAstPipeline.seen_configs = []
    _CapturingAstPipeline.init_count = 0

    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
            encoding="utf-8",
        )
        Path("rules").mkdir()
        Path("tests").mkdir()
        Path("rules/a.yml").write_text(
            "id: rule-a\nlanguage: python\nrule:\n  pattern: 'def $FUNC():'\n",
            encoding="utf-8",
        )
        Path("rules/b.yml").write_text(
            "id: rule-b\nlanguage: python\nrule:\n  pattern: 'class $NAME:'\n",
            encoding="utf-8",
        )
        Path("tests/a.yml").write_text(
            "id: test-a\nruleId: rule-a\nvalid:\n  - ok\ninvalid:\n  - 'def $FUNC():'\n",
            encoding="utf-8",
        )
        Path("tests/b.yml").write_text(
            "id: test-b\nruleId: rule-b\nvalid:\n  - ok\ninvalid:\n  - 'class $NAME:'\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["test", "--config", "sgconfig.yml"])

    assert result.exit_code == 0
    assert _CapturingAstPipeline.init_count == 1
