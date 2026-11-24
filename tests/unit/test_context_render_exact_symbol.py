from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.repo_map import build_context_render
from tensor_grep.cli.repo_map_ranking import promote_exact_declaration_files


def test_promote_exact_declaration_files_orders_by_best_exact_score() -> None:
    scored_files = [
        (500, "hub_a.py"),
        (400, "hub_b.py"),
        (120, "def_low.py"),
        (100, "def_high.py"),
    ]
    scored_symbols = [
        {"file": "hub_a.py", "name": "helper", "score": 300, "exact_query_match": False},
        {"file": "def_low.py", "name": "target", "score": 110, "exact_query_match": True},
        {"file": "def_high.py", "name": "target", "score": 250, "exact_query_match": True},
    ]
    file_reasons: dict[str, list[str]] = {}

    promote_exact_declaration_files(scored_files, scored_symbols, file_reasons)

    assert [path for _, path in scored_files] == [
        "def_high.py",
        "def_low.py",
        "hub_a.py",
        "hub_b.py",
    ]
    assert "exact-declaration" in file_reasons["def_high.py"]
    assert "exact-declaration" in file_reasons["def_low.py"]
    assert "exact-declaration" not in file_reasons.get("hub_a.py", [])


def test_promote_exact_declaration_files_noop_without_exact_matches() -> None:
    scored_files = [(200, "a.py"), (100, "b.py")]
    scored_symbols = [{"file": "b.py", "name": "other", "score": 90, "exact_query_match": False}]
    file_reasons: dict[str, list[str]] = {}

    promote_exact_declaration_files(scored_files, scored_symbols, file_reasons)

    assert scored_files == [(200, "a.py"), (100, "b.py")]
    assert file_reasons == {}


def test_build_context_render_includes_defining_file_over_high_indegree_hubs(
    tmp_path: Path,
) -> None:
    (tmp_path / "target_def.py").write_text(
        "def exact_target_symbol(value: int) -> int:\n    return value + 1\n",
        encoding="utf-8",
    )
    for hub_name in ("hub_a", "hub_b", "hub_c"):
        (tmp_path / f"{hub_name}.py").write_text(
            f"# mentions exact_target_symbol in {hub_name}\n"
            "def dispatch_hub(x: int) -> int:\n"
            "    return x\n",
            encoding="utf-8",
        )
    for idx in range(8):
        (tmp_path / f"sat_{idx}.py").write_text(
            "import hub_a\nimport hub_b\nimport hub_c\n"
            f"def sat_{idx}_fn() -> int:\n"
            "    return hub_a.dispatch_hub(1) + hub_b.dispatch_hub(2) + hub_c.dispatch_hub(3)\n",
            encoding="utf-8",
        )

    rendered = build_context_render("exact_target_symbol", tmp_path, max_files=2)
    top_files = [Path(p).name for p in rendered["files"][:2]]

    assert "target_def.py" in top_files
    assert any(sym.get("name") == "exact_target_symbol" for sym in rendered["symbols"])
