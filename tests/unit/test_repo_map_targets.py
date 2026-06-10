"""Regression tests for repo_map target selection and walk fixes.

Covers:
- C4: Rust doc-comment mis-parsed as a ``use`` path crashing ``with_suffix``.
- H4: ``build_context_pack`` applying the 512 ``max_repo_files`` default.
- H6: ``string_refs[]`` surfacing string-literal / decorator-arg occurrences.
- H7: exactly-named symbols winning primary-target selection.
- L6: ``omitted_sections[].file`` emitting JSON null (not the string "None").
- L8: the repo walk honoring ``.gitignore``.

These import only the light ``repo_map`` module so they run without the
compiled Rust extension.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import repo_map

# --- C4: Rust doc-comment mis-parse must not crash symbol lookup -------------


def test_rust_doc_comment_not_parsed_as_use_binding() -> None:
    source = (
        "/// We only use the trigram index when the regex parser can prove a finite set\n"
        "/// of bytes;\n"
        "use crate::foo::Bar;\n"
        "pub use std::collections::HashMap as Map;\n"
        "use crate::prelude::*;\n"
    )
    bindings = repo_map._rust_use_bindings(source)
    paths = {str(binding.get("path") or binding.get("module")) for binding in bindings}
    # The doc-comment text must not survive as a binding.
    assert not any("trigram" in path for path in paths)
    assert "crate::foo::Bar" in paths
    assert any(
        binding.get("wildcard") and binding.get("module") == "crate::prelude"
        for binding in bindings
    )


def test_rust_module_candidates_does_not_crash_on_garbage(tmp_path: Path) -> None:
    importer = tmp_path / "lib.rs"
    importer.write_text("fn main() {}\n", encoding="utf-8")
    # Previously raised ValueError: "<path> has an empty name" from with_suffix.
    candidates = repo_map._rust_module_candidates(
        importer,
        "/// End-user description: blah blah",
        str(tmp_path),
    )
    # No crash; any returned candidate must be well-formed.
    assert isinstance(candidates, list)


def test_is_valid_rust_use_path() -> None:
    assert repo_map._is_valid_rust_use_path("crate::foo::Bar")
    assert repo_map._is_valid_rust_use_path("std::collections::HashMap")
    assert not repo_map._is_valid_rust_use_path("the trigram index when the regex")
    assert not repo_map._is_valid_rust_use_path("/// End-user description")


# --- H4: context default max_repo_files ------------------------------------


def test_build_context_pack_defaults_max_repo_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    captured: dict[str, object] = {}
    real_build_repo_map = repo_map.build_repo_map

    def _spy(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["max_repo_files"] = kwargs.get("max_repo_files")
        return real_build_repo_map(path, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _spy)
    repo_map.build_context_pack("alpha", tmp_path)
    assert captured["max_repo_files"] == repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT


# --- H6: string_refs[] -------------------------------------------------------


def test_string_literal_references_classifies_occurrences(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text(
        "\n".join([
            "from unittest.mock import patch",
            "",
            '__all__ = ["Widget"]',
            "",
            "",
            '@patch("pkg.mod.Widget")',
            "def test_widget():",
            '    backend = "Widget"',
            "    return backend",
        ])
        + "\n",
        encoding="utf-8",
    )
    refs = repo_map._string_literal_references(target, "Widget")
    occurrences = {ref["occurrence"] for ref in refs}
    assert "decorator-arg" in occurrences
    assert "string-literal" in occurrences
    # The dotted @patch path tail must be matched.
    assert any('@patch("pkg.mod.Widget")' in ref["text"] for ref in refs)
    # Word boundary prevents matching WidgetExtra-style names.
    assert all(ref["name"] == "Widget" for ref in refs)


def test_string_literal_references_word_boundary(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text('x = "WidgetExtra"\ny = "Widget"\n', encoding="utf-8")
    refs = repo_map._string_literal_references(target, "Widget")
    lines = {ref["line"] for ref in refs}
    assert lines == {2}


# --- H7: exact-symbol primary target ----------------------------------------


def test_distinctive_identifier() -> None:
    assert repo_map._is_distinctive_identifier("tg_rewrite_plan")
    assert repo_map._is_distinctive_identifier("RustCoreBackend")
    assert repo_map._is_distinctive_identifier("assignFiles")
    assert repo_map._is_distinctive_identifier("Name2")
    # Bare English words / acronyms must not be treated as named symbols.
    assert not repo_map._is_distinctive_identifier("device")
    assert not repo_map._is_distinctive_identifier("file")
    assert not repo_map._is_distinctive_identifier("HTTP")
    assert not repo_map._is_distinctive_identifier("x")


def test_exact_query_match_prefers_ranked_flag() -> None:
    ranked = [
        {"name": "centrality_hub", "file": "a.py", "score": 9},
        {"name": "tg_rewrite_plan", "file": "b.py", "score": 3, "exact_query_match": True},
    ]
    chosen = repo_map._exact_query_match_primary_symbol(ranked)
    assert chosen is not None
    assert chosen["name"] == "tg_rewrite_plan"


def test_exact_query_match_falls_back_to_repo_map_inventory() -> None:
    # ranked_symbols pre-filtered to the top file omits the named target.
    ranked = [{"name": "centrality_hub", "file": "a.py", "score": 9}]
    repo_map_payload = {
        "path": ".",
        "symbols": [
            {"name": "tg_rewrite_plan", "kind": "function", "file": "b.py", "line": 10},
        ],
    }
    chosen = repo_map._exact_query_match_primary_symbol(
        ranked,
        repo_map=repo_map_payload,
        query="change tg_rewrite_plan to add validation",
    )
    assert chosen is not None
    assert chosen["name"] == "tg_rewrite_plan"
    assert chosen["exact_query_match"] is True


def test_exact_query_match_ignores_common_word_symbols() -> None:
    ranked = [{"name": "centrality_hub", "file": "a.py", "score": 9}]
    repo_map_payload = {
        "path": ".",
        "symbols": [{"name": "device", "kind": "function", "file": "b.py", "line": 1}],
    }
    chosen = repo_map._exact_query_match_primary_symbol(
        ranked,
        repo_map=repo_map_payload,
        query="improve the GPU file device assignment routing",
    )
    assert chosen is None


# --- L6: omitted_sections file null -----------------------------------------


def test_primary_omitted_section_emits_null_not_string_none() -> None:
    payload = {
        "edit_plan_seed": {"primary_file": "src/widget.py"},
        "navigation_pack": {"primary_target": {"file": "src/widget.py"}},
        "files": [],
        "sources": [],
        "sections": [],
        "query": "q",
    }
    out = repo_map._apply_context_consistency_invariants(payload)
    for section in out.get("omitted_sections", []):
        assert section.get("file") != "None"

    # When no primary file resolves, the consistency primary_file is "" (falsy),
    # never the string "None".
    none_payload = {
        "edit_plan_seed": {"primary_file": None},
        "navigation_pack": {"primary_target": {"file": ""}},
        "files": [],
        "sources": [],
        "sections": [],
        "query": "q",
    }
    out_none = repo_map._apply_context_consistency_invariants(none_payload)
    assert out_none["context_consistency"]["primary_file"] is None
    for section in out_none.get("omitted_sections", []):
        assert section.get("file") != "None"


# --- L8: gitignore-aware walk -----------------------------------------------


def test_gitignore_matcher_common_patterns(tmp_path: Path) -> None:
    matcher = repo_map._GitignoreMatcher(
        tmp_path,
        [
            "# comment",
            "__pycache__/",
            "*.pyd",
            "/*.log",
            "dist/",
            "rust_core/target/",
            "!tests/golden/",
        ],
    )
    assert matcher.is_ignored(tmp_path / "src" / "ext.pyd", is_dir=False)
    assert matcher.is_ignored(tmp_path / "dist" / "wheel.whl", is_dir=False)
    assert matcher.is_ignored(tmp_path / "rust_core" / "target" / "x.rs", is_dir=False)
    assert matcher.is_ignored(tmp_path / "build.log", is_dir=False)
    assert not matcher.is_ignored(tmp_path / "sub" / "build.log", is_dir=False)
    assert matcher.is_ignored(tmp_path / "src" / "__pycache__" / "x.pyc", is_dir=False)
    assert not matcher.is_ignored(tmp_path / "src" / "main.py", is_dir=False)


def test_repo_walk_honors_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n*.tmp\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "scratch.tmp").write_text("junk\n", encoding="utf-8")
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    (ignored_dir / "vendored.py").write_text("y = 2\n", encoding="utf-8")

    repo_map._load_gitignore_matcher.cache_clear()
    walked = {p.name for p in repo_map._iter_repo_files(tmp_path)}
    assert "keep.py" in walked
    assert "scratch.tmp" not in walked
    assert "vendored.py" not in walked
