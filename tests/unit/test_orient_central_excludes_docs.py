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
    # the `import config` resolved to config.py (code), NOT config.md. Composite score =
    # min(fan_in=2, cap) + fan_out=0 + symbol_density=1 (the Config class) = 3.0.
    cfg = next(c for c in central if c["file"] == "src/config.py")
    assert cfg["graph_score"] == 3.0


def test_hub_outranks_leaf_constant() -> None:
    # A data SINK (constants.py: imported by 20, imports nothing, 1 symbol) must NOT outrank a real
    # HUB (hub.py: imported by 3, imports 6 modules, 20 symbols). Pure import in-degree ranked the
    # sink first (20 > 3); the composite (capped fan-in + fan-out + symbol density) fixes it.
    files = ["src/constants.py", "src/hub.py"]
    files += [f"src/u{i}.py" for i in range(6)]  # hub's import targets
    files += [f"src/imp_c{i}.py" for i in range(20)]  # constants' importers (huge fan-in)
    files += [f"src/imp_h{i}.py" for i in range(3)]  # hub's importers
    imports = [{"file": f"src/imp_c{i}.py", "imports": ["constants"]} for i in range(20)]
    imports += [{"file": f"src/imp_h{i}.py", "imports": ["hub"]} for i in range(3)]
    imports.append({"file": "src/hub.py", "imports": [f"u{i}" for i in range(6)]})
    symbols = [{"name": "C", "kind": "class", "file": "src/constants.py"}]
    symbols += [{"name": f"h{i}", "kind": "function", "file": "src/hub.py"} for i in range(20)]
    central = _central_files_from_map(
        {"files": files, "imports": imports, "symbols": symbols}, max_central_files=10
    )
    order = [c["file"] for c in central]
    assert "src/hub.py" in order and "src/constants.py" in order
    assert order.index("src/hub.py") < order.index("src/constants.py")


def test_pure_docs_repo_falls_back_not_empty() -> None:
    # A repo that is ONLY docs must still return orientation context (fallback to all files),
    # not an empty capsule.
    rm = {"files": ["a.md", "b.md"], "imports": [], "symbols": []}
    central = _central_files_from_map(rm, max_central_files=10)
    assert len(central) >= 1
