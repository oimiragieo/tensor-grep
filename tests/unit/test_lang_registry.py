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

from tensor_grep.cli import lang_c, lang_csharp, lang_go, lang_registry, repo_map

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
        "foo.go": "go",
        "foo.java": "java",
        "foo.php": "php",
        "foo.cs": "csharp",
        "foo.c": "c",
    }
    for name, expected_language in expectations.items():
        spec = lang_registry.spec_for_path(Path(name))
        assert spec is not None, f"expected a LanguageSpec for {name}"
        assert spec.language_id == expected_language


def test_spec_for_path_unknown_suffix_returns_none() -> None:
    # PATH A Stage 1/2: .go, .java, and .php are now all REGISTERED languages (see
    # test_spec_for_path_resolves_every_registered_suffix), so they moved out of this "still
    # unsupported" list -- .kt/.rb stand in as still-unsupported examples for the
    # resolution_gaps tests below instead (same substitution each stage made for its own
    # newly-registered suffix).
    for name in ("foo.kt", "foo.rb", "foo.txt", "foo", "foo.md"):
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
        ".go",
        ".java",
        ".php",
        ".cs",
        ".c",
    })


def test_language_registry_has_exactly_the_stage2_languages() -> None:
    assert set(lang_registry.LANGUAGE_REGISTRY.keys()) == {
        "python",
        "javascript",
        "typescript",
        "rust",
        "go",
        "java",
        "php",
        "csharp",
        "c",
    }


def test_target_and_provider_language_agree_with_registry() -> None:
    """F22 (audit #63 LOW tail): `_target_language_for_path` (repo_map.py:6475-6494) and
    `_provider_language_for_path` (repo_map.py:13018+) each still carry their OWN hardcoded
    suffix dispatch instead of routing through `lang_registry` -- the "MOST-FORGOTTEN seam"
    the code comments itself warn about (repo_map.py:6489-6493: miss the teach on either
    function and the agent capsule's query-language-vs-target-language 0.55 confidence cap
    silently misfires on the new language). `test_graph_suffixes_matches_the_historical_
    hardcoded_union` above pins only the suffix UNION; it says nothing about whether each
    suffix maps to the SAME language id in all three places.

    Parametrize DYNAMICALLY over `lang_registry.LANGUAGE_REGISTRY` (never a hardcoded suffix
    list, which would itself be exactly the kind of thing that drifts) so this is a ratchet
    against the NEXT language expansion (the #62 CEO fork), not just a snapshot of today's
    five languages: it fails loudly the moment a new `LanguageSpec` is registered without
    teaching both dispatch functions, instead of the new language silently reading as "no
    target language" / "no provider language".
    """
    assert lang_registry.LANGUAGE_REGISTRY, (
        "registry must be non-empty for this test to mean anything"
    )
    for spec in lang_registry.LANGUAGE_REGISTRY.values():
        assert spec.suffixes, f"{spec.language_id!r} has no suffixes to check"
        for suffix in spec.suffixes:
            path = f"example{suffix}"
            assert repo_map._target_language_for_path(path) == spec.language_id, (
                f"_target_language_for_path disagrees with lang_registry for suffix {suffix!r}: "
                f"registry says {spec.language_id!r}"
            )
            assert repo_map._provider_language_for_path(path) == spec.language_id, (
                f"_provider_language_for_path disagrees with lang_registry for suffix {suffix!r}: "
                f"registry says {spec.language_id!r}"
            )


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


def test_go_provenance_is_tree_sitter_when_grammar_present() -> None:
    assert repo_map._symbol_navigation_provenance_for_path("foo.go") == "tree-sitter"


def test_csharp_provenance_is_tree_sitter_when_grammar_present() -> None:
    assert repo_map._symbol_navigation_provenance_for_path("foo.cs") == "tree-sitter"


def test_c_provenance_is_tree_sitter_when_grammar_present() -> None:
    assert repo_map._symbol_navigation_provenance_for_path("foo.c") == "tree-sitter"


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


def test_grammar_absent_monkeypatch_go_provenance_flips_to_grammar_missing(monkeypatch) -> None:
    """Go has NO regex fallback (Stage 1 fail-closed trap): a grammar-absent .go file's
    provenance label must flip to "grammar-missing" (not "regex-heuristic" -- Go never
    silently degrades to a text heuristic the way JS/TS/Rust do)."""
    monkeypatch.setattr(lang_go, "_go_parser", lambda: None)

    provenance = repo_map._symbol_navigation_provenance_for_path("foo.go")

    assert provenance == "grammar-missing"
    assert provenance != ""


def test_grammar_absent_monkeypatch_csharp_provenance_flips_to_grammar_missing(monkeypatch) -> None:
    """C# has NO regex fallback (Stage 1 fail-closed trap, same as Go): a grammar-absent .cs
    file's provenance label must flip to "grammar-missing" (not "regex-heuristic")."""
    monkeypatch.setattr(lang_csharp, "_csharp_parser", lambda: None)

    provenance = repo_map._symbol_navigation_provenance_for_path("foo.cs")

    assert provenance == "grammar-missing"
    assert provenance != ""


def test_grammar_absent_monkeypatch_c_provenance_flips_to_grammar_missing(monkeypatch) -> None:
    """C has NO regex fallback (Stage 1 fail-closed trap, same as Go/PHP/C#): a grammar-absent
    .c file's provenance label must flip to "grammar-missing" (not "regex-heuristic")."""
    monkeypatch.setattr(lang_c, "_c_parser", lambda: None)

    provenance = repo_map._symbol_navigation_provenance_for_path("foo.c")

    assert provenance == "grammar-missing"
    assert provenance != ""


# ---------------------------------------------------------------------------
# resolution_gaps honesty floor
# ---------------------------------------------------------------------------


def _write_python_symbol_plus_unsupported_language_file(tmp_path: Path) -> tuple[Path, Path]:
    # PATH A Stage 1: .go moved from "unsupported" to "registered" (it has its own LanguageSpec
    # now), so .java stood in here instead. PATH A Stage 2: .java ALSO moved to "registered"
    # (foundational tier: symbols + imports, see tests/unit/test_lang_java.py) -- .kt stands in
    # now, still genuinely unregistered, AND (like .go and .java before it) already a member of
    # _SOURCE_FIRST_SUFFIXES, so it actually enters the refs/callers scan universe (a suffix
    # outside that set, e.g. .rb, would be invisible to the scan and never produce a gap at
    # all). This fixture is about the resolution_gaps floor for a language tensor-grep does NOT
    # yet cover at all (contrast with Java's own PARTIAL-capability gap, covered separately in
    # test_lang_java.py's test_refs_and_callers_never_crash_and_flag_java_as_import_resolution_
    # gap).
    py_path = tmp_path / "target.py"
    py_path.write_text(
        "def Target():\n    return 1\n\n\ndef caller():\n    return Target()\n",
        encoding="utf-8",
    )
    kt_path = tmp_path / "Helper.kt"
    kt_path.write_text(
        "class Helper {\n  fun helper() { Target() }\n}\n",
        encoding="utf-8",
    )
    return py_path, kt_path


def test_refs_emits_resolution_gaps_for_unsupported_language_file(tmp_path: Path) -> None:
    _write_python_symbol_plus_unsupported_language_file(tmp_path)

    payload = repo_map.build_symbol_refs("Target", tmp_path)

    assert not payload.get("no_match")
    assert "resolution_gaps" in payload
    gaps = payload["resolution_gaps"]
    assert any(gap["language"] == "kotlin" for gap in gaps)
    kt_gap = next(gap for gap in gaps if gap["language"] == "kotlin")
    assert kt_gap["files_affected"] >= 1
    assert kt_gap["reason"]
    assert kt_gap["remediation"]
    # Additive-only: existing fields must still be present and shaped as before.
    assert "coverage_summary" in payload
    assert "references" in payload


def test_callers_emits_resolution_gaps_for_unsupported_language_file(tmp_path: Path) -> None:
    _write_python_symbol_plus_unsupported_language_file(tmp_path)

    payload = repo_map.build_symbol_callers("Target", tmp_path)

    assert not payload.get("no_match")
    assert "resolution_gaps" in payload
    gaps = payload["resolution_gaps"]
    assert any(gap["language"] == "kotlin" for gap in gaps)
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
    _write_python_symbol_plus_unsupported_language_file(tmp_path)

    payload = repo_map.build_symbol_blast_radius("Target", tmp_path)

    assert "resolution_gaps" in payload
    if payload["resolution_gaps"]:
        assert payload["graph_trust_summary"].get("resolution_gaps_present") is True
