"""PATH A STAGE 0 -- language-extractor registry (lang_registry.py) tests.

Stage 0 is a PURE PARITY REFACTOR: `lang_registry.py` replaces the scattered
`path.suffix in _JS_TS_SUFFIXES` / `_RUST_SUFFIXES` / `== ".py"` dispatch across repo_map.py
with `spec_for_path(path)` lookups, with ZERO behavior change for the 4 currently-supported
languages (python, javascript, typescript, rust). It also adds one additive honesty field:
`resolution_gaps` on `build_symbol_refs_from_map` / `build_symbol_callers_from_map`, which
labels a file in the refs/callers scan universe that has no registered `LanguageSpec` instead
of silently degrading it to a plain regex scan with no explanation.

Covered here:
- `spec_for_path` resolves every registered suffix to the right LanguageSpec, and returns
  `None` for an unregistered suffix (e.g. `.go`).
- `graph_suffixes()` matches the historical hardcoded suffix union exactly.
- The provenance labeler (`_symbol_navigation_provenance_for_path`) honors each LanguageSpec's
  `provenance_when_parsed` / `provenance_when_missing`, including the grammar-absent case
  (tree-sitter uninstalled) for JS/TS and rust -- it must flip to "regex-heuristic", never
  return an empty string.
- The `resolution_gaps` floor: a repo containing a `.go` file alongside a resolvable python
  symbol surfaces an additive `resolution_gaps` entry tagged `language: "go"` on both
  `build_symbol_refs` and `build_symbol_callers`, without changing the exit-code contract or
  the existing `coverage_summary` shape.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import lang_registry, repo_map

# ---------------------------------------------------------------------------
# spec_for_path / graph_suffixes
# ---------------------------------------------------------------------------


def test_spec_for_path_resolves_every_registered_suffix() -> None:
    expectations = {
        "foo.py": "python",
        "foo.js": "javascript",
        "foo.jsx": "javascript",
        "foo.mjs": "javascript",
        "foo.cjs": "javascript",
        "foo.ts": "typescript",
        "foo.tsx": "typescript",
        "foo.rs": "rust",
    }
    for name, expected_language in expectations.items():
        spec = lang_registry.spec_for_path(Path(name))
        assert spec is not None, f"expected a LanguageSpec for {name}"
        assert spec.language_id == expected_language


def test_spec_for_path_unknown_suffix_returns_none() -> None:
    for name in ("foo.go", "foo.java", "foo.rb", "foo.txt", "foo", "foo.md"):
        assert lang_registry.spec_for_path(Path(name)) is None


def test_graph_suffixes_matches_the_historical_hardcoded_union() -> None:
    assert lang_registry.graph_suffixes() == frozenset({
        ".py",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".rs",
    })


def test_language_registry_has_exactly_the_four_stage0_languages() -> None:
    assert set(lang_registry.LANGUAGE_REGISTRY.keys()) == {
        "python",
        "javascript",
        "typescript",
        "rust",
    }


# ---------------------------------------------------------------------------
# Provenance labeling (parsed vs missing-grammar)
# ---------------------------------------------------------------------------


def test_python_provenance_is_always_python_ast_no_parser_gate() -> None:
    spec = lang_registry.LANGUAGE_REGISTRY["python"]
    assert spec.parser_for_path is None
    assert repo_map._symbol_navigation_provenance_for_path("foo.py") == "python-ast"


def test_js_ts_and_rust_provenance_is_tree_sitter_when_grammar_present() -> None:
    # In this dev/test environment the tree-sitter grammar packages ARE installed (ast extra),
    # so the happy path should report "tree-sitter", not fall back.
    assert repo_map._symbol_navigation_provenance_for_path("foo.js") == "tree-sitter"
    assert repo_map._symbol_navigation_provenance_for_path("foo.ts") == "tree-sitter"
    assert repo_map._symbol_navigation_provenance_for_path("foo.rs") == "tree-sitter"


def test_grammar_absent_monkeypatch_js_ts_provenance_flips_to_regex_heuristic(monkeypatch) -> None:
    """Simulate tree-sitter-javascript/typescript being uninstalled (ImportError inside the
    parser factory returns None, per repo_map._javascript_parser/_typescript_parser). The
    provenance label must fail OPEN to "regex-heuristic" -- never empty, never a crash."""
    monkeypatch.setattr(repo_map, "_javascript_parser", lambda: None)
    monkeypatch.setattr(repo_map, "_typescript_parser", lambda *, tsx: None)

    js_provenance = repo_map._symbol_navigation_provenance_for_path("foo.js")
    ts_provenance = repo_map._symbol_navigation_provenance_for_path("foo.tsx")

    assert js_provenance == "regex-heuristic"
    assert ts_provenance == "regex-heuristic"
    assert js_provenance != ""
    assert ts_provenance != ""


def test_grammar_absent_monkeypatch_rust_provenance_flips_to_regex_heuristic(monkeypatch) -> None:
    monkeypatch.setattr(repo_map, "_rust_parser", lambda: None)

    provenance = repo_map._symbol_navigation_provenance_for_path("foo.rs")

    assert provenance == "regex-heuristic"
    assert provenance != ""


# ---------------------------------------------------------------------------
# resolution_gaps honesty floor
# ---------------------------------------------------------------------------


def _write_python_symbol_plus_go_file(tmp_path: Path) -> tuple[Path, Path]:
    py_path = tmp_path / "target.py"
    py_path.write_text(
        "def Target():\n    return 1\n\n\ndef caller():\n    return Target()\n",
        encoding="utf-8",
    )
    go_path = tmp_path / "helper.go"
    go_path.write_text(
        "package main\n\nfunc Helper() int {\n\treturn Target()\n}\n",
        encoding="utf-8",
    )
    return py_path, go_path


def test_refs_emits_resolution_gaps_for_unsupported_language_file(tmp_path: Path) -> None:
    _write_python_symbol_plus_go_file(tmp_path)

    payload = repo_map.build_symbol_refs("Target", tmp_path)

    assert not payload.get("no_match")
    assert "resolution_gaps" in payload
    gaps = payload["resolution_gaps"]
    assert any(gap["language"] == "go" for gap in gaps)
    go_gap = next(gap for gap in gaps if gap["language"] == "go")
    assert go_gap["files_affected"] >= 1
    assert go_gap["reason"]
    assert go_gap["remediation"]
    # Additive-only: existing fields must still be present and shaped as before.
    assert "coverage_summary" in payload
    assert "references" in payload


def test_callers_emits_resolution_gaps_for_unsupported_language_file(tmp_path: Path) -> None:
    _write_python_symbol_plus_go_file(tmp_path)

    payload = repo_map.build_symbol_callers("Target", tmp_path)

    assert not payload.get("no_match")
    assert "resolution_gaps" in payload
    gaps = payload["resolution_gaps"]
    assert any(gap["language"] == "go" for gap in gaps)
    assert "coverage_summary" in payload
    assert "callers" in payload


def test_resolution_gaps_empty_for_pure_python_repo(tmp_path: Path) -> None:
    py_path = tmp_path / "target.py"
    py_path.write_text(
        "def Target():\n    return 1\n\n\ndef caller():\n    return Target()\n",
        encoding="utf-8",
    )

    refs_payload = repo_map.build_symbol_refs("Target", tmp_path)
    callers_payload = repo_map.build_symbol_callers("Target", tmp_path)

    assert refs_payload["resolution_gaps"] == []
    assert callers_payload["resolution_gaps"] == []


def test_blast_radius_downgrades_graph_trust_summary_when_gaps_present(tmp_path: Path) -> None:
    _write_python_symbol_plus_go_file(tmp_path)

    payload = repo_map.build_symbol_blast_radius("Target", tmp_path)

    assert "resolution_gaps" in payload
    if payload["resolution_gaps"]:
        assert payload["graph_trust_summary"].get("resolution_gaps_present") is True
