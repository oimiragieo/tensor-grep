from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import prepare_service
from tensor_grep.cli.incremental_graph_sync import GRAPH_SYNC_REL_PATH
from tensor_grep.cli.symbol_graph_index import (
    GRAPH_CACHE_REL_PATH,
    SymbolGraphIndex,
    sync_incremental_ego_subgraph_from_paths,
)
from tensor_grep.cli.tdad_kg_ast import (
    TDAD_CACHE_REL_PATH,
    ensure_tdad_dependency_map,
    query_tdad_tests,
    record_tdad_edges,
)
from tensor_grep.cli.unified_cache import (
    MANIFEST_REL_PATH,
    build_source_address_contract,
    load_unified_manifest,
    update_view_states,
)


def _make_git_project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "repo"
    src = repo / "src"
    tests = repo / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (repo / ".git").mkdir()
    mod_py = src / "mod.py"
    mod_py.write_text("def compute(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    test_py = tests / "test_mod.py"
    test_py.write_text(
        "from src.mod import compute\n\ndef test_compute() -> None:\n    assert compute(1) == 2\n",
        encoding="utf-8",
    )
    return repo, src, mod_py, test_py


def test_unified_manifest_anchors_to_project_root_from_subdirectory(tmp_path: Path) -> None:
    repo, src, mod_py, _ = _make_git_project(tmp_path)

    merkle_root, source_address = build_source_address_contract(src, [mod_py])
    assert source_address["root"] == str(repo.resolve())
    assert update_view_states(
        src,
        merkle_root,
        {"symbols_v1": (True, 1), "graph_v1": (True, 1)},
        source_address=source_address,
    )

    assert (repo / MANIFEST_REL_PATH).is_file()
    assert not (src / ".tg_cache").exists()
    loaded = load_unified_manifest(src)
    assert loaded is not None
    assert loaded["merkle_root"] == merkle_root


def test_symbol_graph_index_and_incremental_sync_anchor_to_project_root(tmp_path: Path) -> None:
    repo, src, mod_py, _ = _make_git_project(tmp_path)

    with SymbolGraphIndex(merkle_root="m-sub") as idx:
        idx.insert_edges([("src/mod.py", "compute", "src/mod.py", "helper", "call", 2, "test")])
        saved_path = idx.save_to_cache(src)

    assert saved_path == (repo / GRAPH_CACHE_REL_PATH).resolve()
    assert saved_path.is_file()
    assert not (src / ".tg_cache").exists()

    loaded = SymbolGraphIndex.load_from_cache(src, expected_merkle_root="m-sub")
    assert loaded is not None
    loaded.close()

    subgraph = sync_incremental_ego_subgraph_from_paths(
        src,
        [mod_py],
        lambda _p, _c: [],
        str(mod_py),
    )
    assert subgraph["sync"]["complete"] == 1
    assert (repo / GRAPH_SYNC_REL_PATH).is_file()
    assert not (src / ".tg_cache").exists()


def test_tdad_cache_anchors_to_project_root_from_subdirectory(tmp_path: Path) -> None:
    repo, src, mod_py, test_py = _make_git_project(tmp_path)

    count = record_tdad_edges(
        src,
        [(str(mod_py), "compute", str(test_py), "import", 0.9)],
        replace=True,
    )
    assert count == 1
    assert (repo / TDAD_CACHE_REL_PATH).is_file()
    assert not (src / ".tg_cache").exists()

    ensured = ensure_tdad_dependency_map(
        src,
        repo_map_payload={"files": [str(mod_py)], "tests": [str(test_py)]},
    )
    assert ensured["cache_path"].endswith(".tg_cache/graph_v1/tdad_tests.sqlite3")
    assert "/src/.tg_cache/" not in ensured["cache_path"]
    assert not (src / ".tg_cache").exists()

    hits = query_tdad_tests(src, ["src/mod.py"])
    assert any("test_mod.py" in item["test_file"] for item in hits)
    assert not (src / ".tg_cache").exists()


def test_prepare_subdirectory_never_creates_nested_tg_cache(tmp_path: Path) -> None:
    repo, src, _, _ = _make_git_project(tmp_path)

    payload = prepare_service._build_prepare_payload(
        path=str(src),
        query="compute",
        claim=False,
    )
    assert payload["primary_target"] is not None
    assert (repo / MANIFEST_REL_PATH).is_file()
    assert not (src / ".tg_cache").exists()
