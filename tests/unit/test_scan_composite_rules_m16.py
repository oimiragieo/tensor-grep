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
from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner


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
    in production (ast-grep 0.42.1 `range.byteOffset.start/end`). This lets
    the iterative-path union accounting be pinned at node granularity without a
    subprocess; the REAL subprocess arm is covered by
    `test_wrapper_real_astgrep_spans_distinct_for_same_line_matches`."""

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


class _SpanFakeWrapperBackend(_SpanFakeAstBackend):
    """F3: named `AstGrepWrapperBackend` so composite routing treats it as the
    ast-grep wrapper; per-member `search_many` carries the span-bearing
    MatchLines through the real wrapper branch of the scan loops. Directory
    paths are expanded exactly like the real wrapper's (rglob over files)."""

    def is_available(self) -> bool:
        return True

    def search_many(self, file_paths, pattern, config=None) -> SearchResult:
        total = 0
        all_matches: list[MatchLine] = []
        matched_files: list[str] = []
        expanded_paths: list[str] = []
        for file_path in file_paths:
            candidate = Path(file_path)
            if candidate.is_dir():
                expanded_paths.extend(
                    str(path) for path in sorted(candidate.rglob("*")) if path.is_file()
                )
            else:
                expanded_paths.append(file_path)
        for file_path in expanded_paths:
            result = self.search(file_path, pattern, config=config)
            total += result.total_matches
            all_matches.extend(result.matches)
            if result.total_matches > 0:
                matched_files.append(file_path)
        return SearchResult(
            matches=all_matches,
            matched_file_paths=matched_files,
            total_files=len(matched_files),
            total_matches=total,
        )

    def search_project(self, root_path, config_path):
        return {}


_SpanFakeWrapperBackend.__name__ = "AstGrepWrapperBackend"


def test_wrapper_real_astgrep_spans_distinct_for_same_line_matches(
    tmp_path: Path, monkeypatch
) -> None:
    """F1 (b) REAL production-parsing arm: drive the ACTUAL ast-grep 0.42.1
    subprocess (the same code path `search_many` uses) and assert the byte
    spans are populated AND distinct for two same-line matches. RED (round-2
    code): `range.start.index` does not exist on the real wire
    (`range.byteOffset.start/end` does), so both matches were spanless and
    collapsed to one identity."""
    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

    backend = AstGrepWrapperBackend()
    if not backend.is_available():
        pytest.skip("ast-grep binary not available in this environment")

    file_path = tmp_path / "sample.py"
    file_path.write_text("alpha(1); alpha(2)\n", encoding="utf-8")

    result = backend.search(str(file_path), "alpha")
    spans = {(m.start_byte, m.end_byte) for m in result.matches if m.start_byte is not None}
    assert spans == {(0, 5), (10, 15)}, f"real ast-grep spans: {spans}"


def test_native_ast_backend_counts_distinct_same_line_nodes_by_span(
    tmp_path: Path,
) -> None:
    """F2 (b) verification: the native tree-sitter path counts DISTINCT
    same-line nodes. Pre-fix it indexed/collapsed by LINE, so the two `call`
    nodes in `alpha(1); alpha(2)` on one line counted ONE; post-fix the
    node-type index and the query-capture path both dedupe by NODE SPAN and
    report (0,8) and (10,18)."""
    from tensor_grep.backends.ast_backend import AstBackend

    backend = AstBackend()
    if not backend.is_available():
        pytest.skip("tree-sitter grammars not available in this environment")

    file_path = tmp_path / "sample.py"
    file_path.write_text("alpha(1); alpha(2)\n", encoding="utf-8")

    # Node-type index path (pattern = node TYPE).
    result = backend.search(str(file_path), "call")
    spans = sorted((m.start_byte, m.end_byte) for m in result.matches)
    assert spans == [(0, 8), (10, 18)], f"node-type path spans: {spans}"

    # Query-capture path (s-expression) — same-line suppression removed.
    result = backend.search(str(file_path), "(call)")
    spans = sorted((m.start_byte, m.end_byte) for m in result.matches)
    assert spans == [(0, 8), (10, 18)], f"capture path spans: {spans}"


def test_legacy_result_cache_without_format_is_treated_as_miss(
    tmp_path: Path,
) -> None:
    """F2 (a): a legacy result-cache payload (no `format` discriminator, line
    numbers only) must be treated as a MISS — a cache hit would undercount
    distinct same-line nodes for composite-rule span accounting."""
    import json as _json

    from tensor_grep.backends.ast_backend import AstBackend

    backend = AstBackend()
    file_path = tmp_path / "sample.py"
    file_path.write_text("alpha(1); alpha(2)\n", encoding="utf-8")
    cache_path = backend._get_result_cache_path(str(file_path), "python", "call")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        _json.dumps({
            "file_signature": list(backend._build_file_signature(str(file_path))),
            "total_files": 1,
            "total_matches": 1,
            "matches": [{"line_number": 1, "text": "alpha(1); alpha(2)", "file": str(file_path)}],
        }),
        encoding="utf-8",
    )
    assert backend._load_persistent_cached_result(str(file_path), "python", "call") is None, (
        "legacy spanless result cache must be rejected"
    )


def test_result_cache_round_trips_spans(tmp_path: Path) -> None:
    """F2 (a): freshly-written result caches carry the node spans AND the
    format discriminator, so a disk cache hit preserves node identity."""
    from tensor_grep.backends.ast_backend import AstBackend

    if not AstBackend().is_available():
        pytest.skip("tree-sitter grammars not available in this environment")

    file_path = tmp_path / "sample.py"
    file_path.write_text("alpha(1); alpha(2)\n", encoding="utf-8")

    backend = AstBackend()
    AstBackend._clear_shared_caches()
    first = backend.search(str(file_path), "call")
    spans = sorted((m.start_byte, m.end_byte) for m in first.matches)
    assert spans == [(0, 8), (10, 18)]

    payload = json.loads(
        backend._get_result_cache_path(str(file_path), "python", "call").read_text(encoding="utf-8")
    )
    assert payload.get("format") == 2
    assert payload["matches"][0]["start_byte"] == 0
    assert payload["matches"][0]["end_byte"] == 8

    # Fresh instance, in-memory caches cleared: the disk cache (format-2) is
    # served and keeps the spans.
    AstBackend._clear_shared_caches()
    second = AstBackend().search(str(file_path), "call")
    assert sorted((m.start_byte, m.end_byte) for m in second.matches) == [(0, 8), (10, 18)]


def test_rule_backend_selection_is_member_aware() -> None:
    """F3 unit RED (pre-fix): selection used only `rule["pattern"]` (the FIRST
    member), so a mixed composite (bare `alpha` + DSL `alpha(1)`) was hinted
    native and misrouted. GREEN (post-fix): any non-native member forces the
    ast-grep wrapper; all-native composites and single rules keep the legacy
    decisions."""
    select = ast_workflows._select_ast_backend_name_for_rule

    assert select({"pattern": "alpha", "patterns": ["alpha", "alpha(1)"]}, "python") == (
        "AstGrepWrapperBackend"
    )
    assert select({"pattern": "alpha", "patterns": ["alpha", "beta"]}, "python") == "AstBackend"
    assert select({"pattern": "alpha", "patterns": ["alpha(1)", "beta"]}, "python") == (
        "AstGrepWrapperBackend"
    )
    assert select({"pattern": "alpha(1)"}, "python") == "AstGrepWrapperBackend"
    assert select({"pattern": "alpha"}, "python") == "AstBackend"
    assert select({"pattern": "(call)"}, "python") == "AstBackend"


def test_orchestration_hint_for_mixed_composite_is_wrapper() -> None:
    """F3: the orchestration hints (consumed by the default `tg scan` path)
    must name the wrapper for a mixed-shape composite, never a backend that
    serves only the first member."""
    hints = ast_workflows._precompute_orchestration_hints(
        {"language": "python"},
        [
            {
                "id": "mixed",
                "pattern": "alpha",
                "patterns": ["alpha", "alpha(1)"],
                "language": "python",
            }
        ],
        [],
    )
    assert hints["backend_hints"]["mixed"] == "AstGrepWrapperBackend"


def test_scan_mixed_shape_composite_routes_to_wrapper_and_counts_3(monkeypatch) -> None:
    """F3 CLI-level: a default `tg scan` of a mixed-shape composite (bare
    `alpha` + DSL `alpha(1)`) must route to the AST-GREP path (both members
    scanned) and count the three distinct nodes — the ast-grep `any` count.
    RED (pre-fix): the first member's native hint would have routed this to
    tree-sitter, which cannot serve `alpha(1)`'s DSL."""

    records: list[str] = []
    original_search_many = _SpanFakeWrapperBackend.search_many

    def recording_search_many(self, file_paths, pattern, config=None):
        records.append(pattern)
        return original_search_many(self, file_paths, pattern, config=config)

    _SpanFakeWrapperBackend.search_many = recording_search_many
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend", _SpanFakeWrapperBackend
    )
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    try:
        runner = CliRunner()
        with runner.isolated_filesystem():
            Path("a.py").write_text("alpha(1); alpha(2)\n", encoding="utf-8")
            Path("b.py").write_text("ok\n", encoding="utf-8")
            inline = (
                "id: mixed\n"
                "language: python\n"
                "rule:\n"
                "  any:\n"
                "    - pattern: alpha\n"
                "    - pattern: 'alpha(1)'\n"
            )
            result = runner.invoke(app, ["scan", "--inline-rules", inline, "--path", "."])
    finally:
        _SpanFakeWrapperBackend.search_many = original_search_many

    assert result.exit_code == 0
    assert records == ["alpha", "alpha(1)"], f"members scanned via wrapper: {records}"
    assert "[scan] rule=mixed lang=python matches=3 files=1" in result.output
    assert "backends=AstGrepWrapperBackend" in result.output


def test_scan_project_composite_any_rule_counts_span_union(monkeypatch) -> None:
    """F1 three-arm parity (Python iterative arm, node-granular): the fixture
    `alpha(1); alpha(2)` with members `alpha` + `alpha(1)` contains THREE
    distinct AST nodes (two identifier nodes, one call node) on ONE line.
    Whole-config ast-grep counts 3; the iterative path (scan_paths given, so
    project fast path is off) must too. The mixed composite is routed to the
    ast-grep wrapper (F3), driven here by the span-bearing wrapper fake; the
    REAL production-parsing arms are `test_wrapper_real_astgrep_spans...`
    (wrapper subprocess) and the Rust scan-core test (CI). Round-1 RED pinned 1
    (line-identity); round-3 GREEN pins 3 (span-identity on the real wire)."""

    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend", _SpanFakeWrapperBackend
    )
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
        # Scan root is a src/ subdir so the rules/config YAML (which contain
        # the literal member strings) are NOT scanned by the wrapper fake.
        Path("src").mkdir()
        Path("src/a.py").write_text("alpha(1); alpha(2)\n", encoding="utf-8")
        Path("src/b.py").write_text("ok\n", encoding="utf-8")

        # Positional scan path turns the project fast path OFF so the iterative
        # wrapper member loop (the code under test) runs.
        result = runner.invoke(app, ["scan", "--config", "sgconfig.yml", "src"])

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
