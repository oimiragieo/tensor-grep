"""tg orient must rank CODE architecture as "central files", never documentation.

Dogfood 2026-07-03 (v1.19.3, a doc-heavy repo with 36 CLAUDE.md files): orient's top central
files were all docs (graph_score 10.0), burying main.tsx / the real code. Docs are excluded from
the centrality graph so they neither rank as central nor shadow a code module via a stem collision
(config.md absorbing an `import config` that meant config.py)."""

from tensor_grep.cli.orient_capsule import _central_files_from_map


def test_docs_are_never_central_and_code_wins_stem_collision() -> None:
    rm = {
        "files": ["src/config.py", "docs/config.md", "src/a.py", "src/b.py", "README.md"],
        "imports": [
            {"file": "src/a.py", "imports": ["config"]},
            {"file": "src/b.py", "imports": ["config"]},
        ],
        "symbols": [{"name": "Config", "kind": "class", "file": "src/config.py"}],
    }
    central = _central_files_from_map(rm, max_central_files=10)
    files = [c["file"] for c in central]
    assert not any(f.endswith((".md", ".rst", ".txt", ".adoc")) for f in files)  # no docs
    assert "src/config.py" in files
    # the `import config` resolved to config.py (code), NOT config.md — in-degree 2 on the code file.
    cfg = next(c for c in central if c["file"] == "src/config.py")
    assert cfg["graph_score"] == 2.0


def test_pure_docs_repo_falls_back_not_empty() -> None:
    # A repo that is ONLY docs must still return orientation context (fallback to all files),
    # not an empty capsule.
    rm = {"files": ["a.md", "b.md"], "imports": [], "symbols": []}
    central = _central_files_from_map(rm, max_central_files=10)
    assert len(central) >= 1
