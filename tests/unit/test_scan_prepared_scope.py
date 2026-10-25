import json
from pathlib import Path

from tensor_grep.cli.ast_scan import _run_ast_scan_payload
from tensor_grep.cli.main import _collect_candidate_files
from tensor_grep.cli.sarif import scan_payload_to_sarif
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult
from tensor_grep.io.directory_scanner import DirectoryScanner

_RUST_AWS_RULE = {
    "id": "rust-aws-access-key-id",
    "engine": "regex",
    "pattern": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "language": "rust",
    "severity": "high",
    "message": "Avoid hardcoding AWS access key IDs.",
}


def _prepared_fixture(root: Path):
    scanner = DirectoryScanner(SearchConfig(ast=True, ast_prefer_native=True, lang=None))
    candidates, _seen = _collect_candidate_files(scanner, [str(root)])
    return scanner, candidates


def test_Run_PreparedScope_OverridesRootsAndGlobsAndProducesSuccessfulSarif(tmp_path: Path):
    selected = tmp_path / "selected.rs"
    excluded = tmp_path / "excluded.rs"
    selected.write_text(
        'let key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    excluded.write_text(
        'let key = "ASIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    scanner, candidates = _prepared_fixture(tmp_path)

    payload = _run_ast_scan_payload(
        {
            "config_path": "builtin:secrets-basic",
            "root_dir": tmp_path,
            "language": "rust",
        },
        [dict(_RUST_AWS_RULE)],
        routing_reason="builtin-ruleset-scan",
        ruleset_name="secrets-basic",
        scan_paths=[str(excluded)],
        scan_globs=["*.py"],
        allow_broad_generated_scan=True,
        prepared_scope=([str(selected.resolve())], scanner),
    )
    sarif = scan_payload_to_sarif(payload, tool_version="test", base_path=str(tmp_path))

    assert len(candidates) == 2
    assert str(selected) in candidates
    assert str(excluded) in candidates
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["files"] == [str(selected.resolve())]
    assert sarif["runs"][0]["invocations"][0]["executionSuccessful"] is True
    assert sarif["runs"][0]["results"]


def test_Run_PreparedRefusal_PreservesScannerEvidenceAndSkipsBaselineWrites(tmp_path: Path):
    input_file = tmp_path / "main.rs"
    input_file.write_text("let clean = true;\n", encoding="utf-8")
    scanner, candidates = _prepared_fixture(tmp_path)
    scanner.scan_truncated = True
    scanner.scan_truncation_cause = "max-scan-entries"
    scanner.unreadable_path_count = 1
    scanner.unreadable_path_sample = [str(input_file)]
    baseline_out = tmp_path / "baseline.json"
    suppressions_out = tmp_path / "suppressions.json"
    baseline_out.write_bytes(b"baseline-sentinel")
    suppressions_out.write_bytes(b"suppressions-sentinel")

    payload = _run_ast_scan_payload(
        {
            "config_path": "builtin:subprocess-safe",
            "root_dir": tmp_path,
            "language": "python",
            "public_language": "python",
            "languages": [],
            "language_selection": "explicit",
            "rule_count": 8,
            "applicable_rule_count": 0,
            "refusal_reason": "explicit_language_mismatch",
            "scope": {
                "paths": [str(tmp_path)],
                "files_considered": len(candidates),
                "files_applicable": 0,
                "detected_languages": ["rust"],
            },
        },
        [],
        routing_reason="builtin-ruleset-scan",
        ruleset_name="subprocess-safe",
        baseline_path=str(tmp_path / "missing-baseline.json"),
        suppressions_path=str(tmp_path / "missing-suppressions.json"),
        write_baseline_path=str(baseline_out),
        write_suppressions_path=str(suppressions_out),
        suppression_justification="Refusal must not write.",
        allow_broad_generated_scan=True,
        prepared_scope=(candidates, scanner),
    )
    sarif = scan_payload_to_sarif(payload, tool_version="test", base_path=str(tmp_path))

    assert payload["error"]["reason"] == "explicit_language_mismatch"
    assert payload["partial"] is True
    assert payload["partial_reason"] == "incomplete_discovery"
    assert payload["scan_truncation_cause"] == "max-scan-entries"
    assert payload["unreadable_paths"]["count"] == 1
    assert "OBSERVED scope" in payload["remediation"]
    assert baseline_out.read_bytes() == b"baseline-sentinel"
    assert suppressions_out.read_bytes() == b"suppressions-sentinel"
    assert sarif["runs"][0]["invocations"][0]["executionSuccessful"] is False
    assert sarif["runs"][0]["invocations"][0]["toolExecutionNotifications"][0]["descriptor"][
        "id"
    ] == ("tensor-grep/no-applicable-rules")


class AstGrepWrapperBackend:
    def __init__(self, rust_path: str, calls: list[tuple[str, tuple[str, ...]]]):
        self.rust_path = rust_path
        self.calls = calls
        self.project_calls: list[tuple[str, str]] = []

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        self.project_calls.append((root_path, config_path))
        return {}

    def search_many(self, file_paths, pattern, config=None) -> SearchResult:
        paths = tuple(file_paths)
        self.calls.append((pattern, paths))
        return SearchResult(
            matches=[MatchLine(1, "wrapper hit", self.rust_path)],
            matched_file_paths=[self.rust_path],
            match_counts_by_file={self.rust_path: 1},
            total_files=1,
            total_matches=1,
            routing_backend="AstGrepWrapperBackend",
        )


class _NativeBackend:
    def __init__(self, python_path: str, calls: list[str]):
        self.python_path = python_path
        self.calls = calls

    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        self.calls.append(file_path)
        return SearchResult(
            matches=[MatchLine(1, "native hit", self.python_path)],
            matched_file_paths=[self.python_path],
            match_counts_by_file={self.python_path: 1},
            total_files=1,
            total_matches=1,
            routing_backend="NativeTestBoundary",
        )


def _raise_for_prepared_fault(canary: str, pristine):
    def read_text(path: Path, *args, **kwargs):
        if path == Path(canary):
            raise PermissionError(13, "Permission denied", canary)
        return pristine(path, *args, **kwargs)

    return read_text


def test_Run_PreparedScope_RestrictsWrapperAndNativeRulesWithoutProjectFastPath(
    tmp_path: Path, monkeypatch
):
    rust_path = tmp_path / "selected.rs"
    python_path = tmp_path / "selected.py"
    rust_path.write_text("wrapper source\n", encoding="utf-8")
    python_path.write_text("native source\n", encoding="utf-8")
    scanner, candidates = _prepared_fixture(tmp_path)
    wrapper_calls: list[tuple[str, tuple[str, ...]]] = []
    native_calls: list[str] = []
    wrapper = AstGrepWrapperBackend(str(rust_path.resolve()), wrapper_calls)
    native = _NativeBackend(str(python_path.resolve()), native_calls)
    by_rule = {"rust-wrapper": wrapper, "python-native": native}

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_rule",
        lambda cfg, rule, cache: by_rule[rule["id"]],
    )
    payload = _run_ast_scan_payload(
        {"config_path": "builtin:test", "root_dir": tmp_path, "language": "rust"},
        [
            {"id": "rust-wrapper", "language": "rust", "pattern": "wrapper"},
            {"id": "python-native", "language": "python", "pattern": "native"},
        ],
        routing_reason="prepared-scope-test",
        allow_broad_generated_scan=True,
        project_scan_fast_path=True,
        prepared_scope=(candidates, scanner),
    )

    assert len(candidates) == 2
    assert wrapper.project_calls == []
    assert wrapper_calls == [("wrapper", (str(rust_path.resolve()),))]
    assert native_calls == [str(python_path.resolve())]
    assert payload["total_matches"] == 2
    assert {finding["rule_id"]: finding["files"] for finding in payload["findings"]} == {
        "rust-wrapper": [str(rust_path.resolve())],
        "python-native": [str(python_path.resolve())],
    }


def test_Run_PreparedRegex_MergesScannerAndReadFailures(tmp_path: Path, monkeypatch):
    source = tmp_path / "secret.rs"
    readable = tmp_path / "readable.rs"
    source.write_text(
        'let key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    readable.write_text(
        'let key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    scanner, candidates = _prepared_fixture(tmp_path)
    scanner.unreadable_path_count = 2
    scanner.unreadable_path_sample = ["discovery-denied.rs"]
    monkeypatch.setattr(
        Path, "read_text", _raise_for_prepared_fault(str(source.resolve()), Path.read_text)
    )

    payload = _run_ast_scan_payload(
        {"config_path": "builtin:secrets-basic", "root_dir": tmp_path, "language": "rust"},
        [dict(_RUST_AWS_RULE)],
        routing_reason="prepared-scope-test",
        allow_broad_generated_scan=True,
        prepared_scope=(candidates, scanner),
    )

    assert payload["total_matches"] == 1
    assert payload["findings"][0]["files"] == [str(readable.resolve())]
    assert payload["unreadable_paths"]["count"] == 3
    assert payload["unreadable_paths"]["sample"] == ["discovery-denied.rs", str(source.resolve())]
    assert payload["partial"] is True
    assert payload["partial_reason"] == "unreadable_path"


def test_Run_PreparedRegex_WritesBaselineForRealFinding(tmp_path: Path):
    source = tmp_path / "secret.rs"
    source.write_text(
        'let key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    scanner, candidates = _prepared_fixture(tmp_path)
    baseline = tmp_path / "baseline.json"

    payload = _run_ast_scan_payload(
        {"config_path": "builtin:secrets-basic", "root_dir": tmp_path, "language": "rust"},
        [dict(_RUST_AWS_RULE)],
        routing_reason="prepared-scope-test",
        allow_broad_generated_scan=True,
        prepared_scope=(candidates, scanner),
        ruleset_name="secrets-basic",
        write_baseline_path=str(baseline),
    )

    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["total_matches"] == 1
    assert payload["baseline_written"]["count"] == 1
    assert written["version"] == 1
    assert written["schema_version"] == 1
    assert written["kind"] == "ruleset-scan-baseline"
    assert written["ruleset"] == "secrets-basic"
    assert written["language"] == "rust"
    assert len(written["fingerprints"]) == 1


def test_Run_PreparedRegex_KeepsGitignoreTruncationSticky(tmp_path: Path):
    source = tmp_path / "secret.rs"
    source.write_text(
        'let key = "AKIAIOSFODNN7EXAMPLE";\n',  # test fixture
        encoding="utf-8",
    )
    scanner, candidates = _prepared_fixture(tmp_path)
    scanner.gitignore_truncated = True

    payload = _run_ast_scan_payload(
        {"config_path": "builtin:secrets-basic", "root_dir": tmp_path, "language": "rust"},
        [dict(_RUST_AWS_RULE)],
        routing_reason="prepared-scope-test",
        allow_broad_generated_scan=True,
        prepared_scope=(candidates, scanner),
    )

    assert payload["total_matches"] == 1
    assert payload["gitignore_truncated"] is True
    assert payload["partial"] is True
    assert payload["partial_reason"] == "incomplete_discovery"


def test_Run_EmptyPreparedRefusal_MarksSarifUnsuccessful(tmp_path: Path):
    scanner, candidates = _prepared_fixture(tmp_path)

    payload = _run_ast_scan_payload(
        {
            "config_path": "builtin:secrets-basic",
            "root_dir": tmp_path,
            "language": "python",
            "refusal_reason": "empty_scope",
        },
        [],
        routing_reason="builtin-ruleset-scan",
        allow_broad_generated_scan=True,
        prepared_scope=(candidates, scanner),
    )
    sarif = scan_payload_to_sarif(payload, tool_version="test", base_path=str(tmp_path))

    assert payload["error"]["reason"] == "empty_scope"
    assert "partial" not in payload
    assert sarif["runs"][0]["invocations"][0]["executionSuccessful"] is False
