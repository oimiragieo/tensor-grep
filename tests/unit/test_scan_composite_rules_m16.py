"""M16 (audit) regression tests: Rust `tg scan` + its Python twin dropped
composite rules (multi-pattern `any`-of / `pattern:` lists) and custom
severity/message. This module pins the PYTHON side of the fix so the loader
(`_extract_rule_member_patterns` / `_load_rule_specs_and_meta` /
`_load_inline_rule_specs`) carries the SAME member semantics as the Rust scan
core (`AstWorkflowOrchestrator::extract_rule_member_patterns`), and the scan
loops count the union by AST NODE SPAN (file, start_byte, end_byte) — the
same node matched by several members counts once, but two distinct nodes on
one line each count (F1), matching whole-config ast-grep's per-node `any`
semantics.

Rust-side coverage lives in `rust_core/src/backend_ast_workflow.rs` (CI
compiles/tests it); these tests run locally and mirror it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import ast_workflows
from tensor_grep.cli.main import _load_inline_rule_specs, app
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner


def test_extract_rule_member_patterns_supported_shapes() -> None:
    extract = ast_workflows._extract_rule_member_patterns

    assert extract({"pattern": "alpha(x)"}) == ["alpha(x)"]
    assert extract({"pattern": ["alpha(x)", "beta(x)"]}) == ["alpha(x)", "beta(x)"]
    assert extract({"rule": {"pattern": "gamma(x)"}}) == ["gamma(x)"]
    assert extract({"rule": {"any": [{"pattern": "alpha"}, {"pattern": "beta"}]}}) == [
        "alpha",
        "beta",
    ]
    assert extract({"rule": {"any": [{"pattern": "alpha"}, {"rule": {"pattern": "beta(x)"}}]}}) == [
        "alpha",
        "beta(x)",
    ]


def test_extract_rule_member_patterns_fails_closed() -> None:
    extract = ast_workflows._extract_rule_member_patterns

    # all:/not: composite bodies need same-node semantics; dropped (None).
    assert extract({"rule": {"all": [{"pattern": "a(x)"}, {"pattern": "b(x)"}]}}) is None
    assert extract({"rule": {"not": {"pattern": "a(x)"}}}) is None
    # A pattern list with a bad member fails the whole rule closed.
    assert extract({"pattern": ["alpha(x)", 5]}) is None
    assert extract({"pattern": []}) is None
    assert extract({"rule": {"any": [{"pattern": "alpha"}, {"any": [{"pattern": "x"}]}]}}) is None
    assert extract({}) is None
    assert extract({"other": 1}) is None


def test_load_rule_specs_and_meta_carries_composite_members_and_metadata(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "composite.yml").write_text(
        "id: composite-rule\n"
        "language: python\n"
        "severity: high\n"
        "message: avoid both\n"
        "rule:\n"
        "  any:\n"
        "    - pattern: alpha\n"
        "    - pattern: beta\n",
        encoding="utf-8",
    )
    (rules_dir / "list.yml").write_text(
        "id: list-rule\nlanguage: python\npattern:\n  - gamma(x)\n  - delta(x)\n",
        encoding="utf-8",
    )
    (rules_dir / "dropped.yml").write_text(
        "id: all-rule\nlanguage: python\nrule:\n  all:\n    - pattern: a(x)\n    - pattern: b(x)\n",
        encoding="utf-8",
    )

    project_cfg = {"root_dir": str(tmp_path), "rule_dirs": ["rules"], "language": "python"}
    specs, _meta = ast_workflows._load_rule_specs_and_meta(project_cfg)  # type: ignore[arg-type]

    by_id = {spec["id"]: spec for spec in specs}
    assert set(by_id) == {"composite-rule", "list-rule"}  # all:-rule dropped (fail-closed)

    composite = by_id["composite-rule"]
    assert composite["pattern"] == "alpha"
    assert composite["patterns"] == ["alpha", "beta"]
    assert composite["severity"] == "high"
    assert composite["message"] == "avoid both"

    list_rule = by_id["list-rule"]
    assert list_rule["pattern"] == "gamma(x)"
    assert list_rule["patterns"] == ["gamma(x)", "delta(x)"]
    assert list_rule["severity"] == "warning"
    assert list_rule["message"] == ""


def test_load_inline_rule_specs_carries_composite_members() -> None:
    """F2 RED (pre-fix): `_load_inline_rule_specs` used the flat-string
    extractor, so a two-member `rule.any` returned NO pattern and the rule was
    silently dropped for `--rule` / `--inline-rules`. GREEN (post-fix): the
    same member extraction the project-config path uses carries BOTH members."""
    inline = (
        "id: comp\nlanguage: python\nrule:\n  any:\n    - pattern: alpha\n    - pattern: beta\n"
    )
    specs = _load_inline_rule_specs(inline)
    assert len(specs) == 1
    assert specs[0]["pattern"] == "alpha"
    assert specs[0].get("patterns") == ["alpha", "beta"]

    list_form = "id: list-rule\nlanguage: python\npattern:\n  - gamma(x)\n  - delta(x)\n"
    specs = _load_inline_rule_specs(list_form)
    assert len(specs) == 1
    assert specs[0]["pattern"] == "gamma(x)"
    assert specs[0].get("patterns") == ["gamma(x)", "delta(x)"]


def test_match_node_identity_uses_span_or_line_fallback() -> None:
    identity = ast_workflows._match_node_identity

    spanned = MatchLine(line_number=1, text="x", file="f.py", start_byte=3, end_byte=7)
    assert identity(spanned) == ("f.py", 3, 7)

    # Fake/line-only backends fall back to (file, -1, line).
    no_span = MatchLine(line_number=2, text="x", file="f.py")
    assert identity(no_span) == ("f.py", -1, 2)

    # Per-file searches may leave file empty; the caller supplies the fallback.
    empty_file = MatchLine(line_number=5, text="x", file="", start_byte=0, end_byte=2)
    assert identity(empty_file, fallback_file="a.py") == ("a.py", 0, 2)


def _write_project(root: Path, rule_yaml: str) -> None:
    (root / "sgconfig.yml").write_text("ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8")
    (root / "rules").mkdir(exist_ok=True)
    (root / "rules" / "r1.yml").write_text(rule_yaml, encoding="utf-8")
    (root / "a.py").write_text("alpha(1); alpha(2)\n", encoding="utf-8")


_RULE_WITH_SEVERITY = (
    "id: r1\nlanguage: python\nseverity: high\nmessage: fresh\npattern: danger(x)\n"
)


def _write_cache_payload(root: Path, extra: dict[str, object]) -> None:
    cache_dir = root / ".tg_cache" / "ast"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_cfg": {
            "config_path": str(root / "sgconfig.yml"),
            "root_dir": str(root),
            "rule_dirs": [],
            "test_dirs": [],
            "language": "python",
        },
        "rule_specs": [
            {
                "id": "r1",
                "pattern": "danger(x)",
                "language": "python",
                "severity": "stale",
                "message": "stale",
            }
        ],
        "candidate_files": [],
        "test_data": [],
        "orchestration_hints": {},
        "validation_metadata": {},
    }
    payload.update(extra)
    (cache_dir / "project_data_v6.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "extra, expected_severity",
    [
        # Legacy schema (no cache_schema_version): REBUILT from source, so the
        # fresh severity wins over the stale cache value. RED (pre-fix): the
        # mtime-fresh legacy cache was served with "stale".
        ({}, "high"),
        # Current schema: served (mtime-fresh, version matches), so "stale" wins.
        ({"cache_schema_version": 2}, "stale"),
    ],
)
def test_load_ast_project_data_schema_gate(
    tmp_path: Path, extra: dict[str, object], expected_severity: str
) -> None:
    _write_project(tmp_path, _RULE_WITH_SEVERITY)
    _write_cache_payload(tmp_path, extra)

    _project_cfg, rule_specs, _files, _test_data, _hints = ast_workflows._load_ast_project_data(
        str(tmp_path / "sgconfig.yml")
    )
    assert rule_specs[0]["severity"] == expected_severity


def test_load_ast_project_data_rebuild_produces_composite_members(tmp_path: Path) -> None:
    _write_project(
        tmp_path,
        "id: r1\nlanguage: python\nrule:\n  any:\n    - pattern: alpha\n    - pattern: beta\n",
    )
    _write_cache_payload(tmp_path, {})

    _project_cfg, rule_specs, _files, _test_data, _hints = ast_workflows._load_ast_project_data(
        str(tmp_path / "sgconfig.yml")
    )
    assert rule_specs[0]["pattern"] == "alpha"
    assert rule_specs[0].get("patterns") == ["alpha", "beta"]


class _SpanFakeAstBackend:
    """Mimics the ast-grep wrapper's PER-NODE MatchLines with byte spans: one
    MatchLine per text occurrence of the member pattern, with the occurrence's
    byte span -- the shape the wrapper's `search_many`/`search_project` produce
    in production. This lets the iterative-path union accounting be pinned
    locally at node granularity."""

    def search(self, file_path: str, pattern: str, config=None) -> SearchResult:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except OSError:
            content = ""
        matches: list[MatchLine] = []
        start = 0
        while True:
            idx = content.find(pattern, start)
            if idx < 0:
                break
            matches.append(
                MatchLine(
                    line_number=1,
                    text=content,
                    file=file_path,
                    start_byte=idx,
                    end_byte=idx + len(pattern),
                )
            )
            start = idx + len(pattern)
        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
        )


class _SpanFakeAstPipeline:
    def __init__(self, force_cpu=False, config=None):
        _ = force_cpu
        _ = config
        self._backend = _SpanFakeAstBackend()

    def get_backend(self):
        return self._backend


def test_scan_project_composite_any_rule_counts_span_union(monkeypatch) -> None:
    """F1 three-arm parity (Python iterative arm, node-granular): the fixture
    `alpha(1); alpha(2)` with members `alpha` + `alpha(1)` contains THREE
    distinct AST nodes (two identifier nodes, one call node) on ONE line.
    Whole-config ast-grep counts 3; the iterative path (scan_paths given, so
    project fast path is off) must too. Round-1 RED pinned 1 (line-identity);
    round-2 GREEN pins 3 (span-identity)."""
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _SpanFakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/composite.yml").write_text(
            "id: composite-rule\n"
            "language: python\n"
            "rule:\n"
            "  any:\n"
            "    - pattern: alpha\n"
            "    - pattern: 'alpha(1)'\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("alpha(1); alpha(2)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")

        # Positional scan path turns the project fast path OFF so the iterative
        # native/wrapper member loop (the code under test) runs.
        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml", "."])

    assert result.exit_code == 0
    assert "[scan] rule=composite-rule lang=python matches=3 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=3" in result.output


def test_scan_inline_rules_composite_members_are_scanned(monkeypatch) -> None:
    """F2 loop-level RED (pre-fix): `--inline-rules` dropped the composite rule
    at load (no spec -> no rules -> exit 1). GREEN (post-fix): both members are
    loaded AND scanned; two distinct nodes on one line each count."""
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _SpanFakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("a.py").write_text("alpha(1); beta(2)\n", encoding="utf-8")
        Path("b.py").write_text("ok\n", encoding="utf-8")
        inline = (
            "id: comp\nlanguage: python\nrule:\n  any:\n    - pattern: alpha\n    - pattern: beta\n"
        )

        result = runner.invoke(app, ["scan", "--inline-rules", inline, "--path", "."])

    assert result.exit_code == 0
    assert "[scan] rule=comp lang=python matches=2 files=1" in result.output
    assert "Scan completed. rules=1 matched_rules=1 total_matches=2" in result.output


def test_scan_project_composite_any_rule_json_carries_severity_and_message(monkeypatch) -> None:
    """F1 loop-level RED: pre-fix the composite rule was dropped (no finding at
    all); post-fix the finding carries the rule's custom severity/message. Uses
    the plain fake (no spans) to also pin the (file, -1, line) fallback."""
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)

    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("sgconfig.yml").write_text(
            "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
        )
        Path("rules").mkdir()
        Path("rules/composite.yml").write_text(
            "id: composite-rule\n"
            "language: python\n"
            "severity: high\n"
            "message: avoid both\n"
            "rule:\n"
            "  any:\n"
            "    - pattern: alpha\n"
            "    - pattern: beta\n",
            encoding="utf-8",
        )
        Path("a.py").write_text("alpha(1)\n", encoding="utf-8")

        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total_matches"] == 1
    finding = payload["findings"][0]
    assert finding["rule_id"] == "composite-rule"
    assert finding["severity"] == "high"
    assert finding["message"] == "avoid both"
    assert finding["files"] == ["a.py"]
