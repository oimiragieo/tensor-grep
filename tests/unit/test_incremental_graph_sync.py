from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli.incremental_graph_sync import (
    GRAPH_SYNC_REL_PATH,
    extract_ego_subgraph,
    sync_graph_edges,
)
from tensor_grep.cli.symbol_graph_index import (
    GRAPH_CACHE_REL_PATH,
    SymbolGraphIndex,
    sync_incremental_ego_subgraph,
)


def _extract_imports(path: str, content: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for line in content.splitlines():
        if line.startswith("import "):
            target = line.removeprefix("import ").strip()
            edges.append((target, "import"))
    return edges


def test_sync_graph_edges_skips_unchanged_and_updates_dirty(tmp_path: Path) -> None:
    calls: list[str] = []

    def tracked_extract(path: str, content: str) -> list[tuple[str, str]]:
        calls.append(path)
        return _extract_imports(path, content)

    files_v1: dict[str, bytes | str] = {
        "a.py": "import b.py\n",
        "b.py": b"import c.py\n",
    }
    res1 = sync_graph_edges(tmp_path, files_v1, tracked_extract)
    assert res1 == {"skipped": 0, "updated": 2, "deleted": 0}
    assert sorted(calls) == ["a.py", "b.py"]

    db_path = tmp_path / GRAPH_SYNC_REL_PATH
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()
        assert check == ("ok",)
    finally:
        conn.close()

    # Second run with identical contents must skip all files in O(1) without calling extractor.
    calls.clear()
    res2 = sync_graph_edges(tmp_path, files_v1, tracked_extract)
    assert res2 == {"skipped": 2, "updated": 0, "deleted": 0}
    assert calls == []

    # Third run modifies only a.py; b.py is skipped and a.py's old edges are replaced.
    files_v2: dict[str, bytes | str] = {
        "a.py": "import d.py\n",
        "b.py": b"import c.py\n",
    }
    res3 = sync_graph_edges(tmp_path, files_v2, tracked_extract)
    assert res3 == {"skipped": 1, "updated": 1, "deleted": 0}
    assert calls == ["a.py"]

    sub_a = extract_ego_subgraph(tmp_path, "a.py", max_hops=1, max_nodes=16)
    assert sub_a["nodes"] == ["a.py", "d.py"]
    assert sub_a["edges"] == [{"src": "a.py", "dst": "d.py", "kind": "import"}]


def test_sync_graph_edges_prunes_deleted_files(tmp_path: Path) -> None:
    files_v1: dict[str, bytes | str] = {
        "a.py": "import b.py\n",
        "b.py": "import c.py\n",
    }
    sync_graph_edges(tmp_path, files_v1, _extract_imports)

    files_v2: dict[str, bytes | str] = {"a.py": "import b.py\n"}
    res = sync_graph_edges(tmp_path, files_v2, _extract_imports)
    assert res == {"skipped": 1, "updated": 0, "deleted": 1}

    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        file_rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        edge_rows = conn.execute("SELECT src, dst FROM edges ORDER BY src, dst").fetchall()
        assert file_rows == [("a.py",)]
        assert edge_rows == [("a.py", "b.py")]
    finally:
        conn.close()


def test_extract_ego_subgraph_bounded_hops_and_nodes(tmp_path: Path) -> None:
    empty_sub = extract_ego_subgraph(tmp_path, "missing.py")
    assert empty_sub == {
        "seed": "missing.py",
        "nodes": ["missing.py"],
        "edges": [],
        "truncated": False,
    }

    with pytest.raises(ValueError, match="max_hops must be non-negative"):
        extract_ego_subgraph(tmp_path, "a.py", max_hops=-1)

    files: dict[str, bytes | str] = {
        "a.py": "import b.py\nimport e.py\n",
        "b.py": "import c.py\n",
        "c.py": "import d.py\n",
        "e.py": "import f.py\n",
    }
    sync_graph_edges(tmp_path, files, _extract_imports)

    hop1 = extract_ego_subgraph(tmp_path, "a.py", max_hops=1, max_nodes=32)
    assert set(hop1["nodes"]) == {"a.py", "b.py", "e.py"}
    assert hop1["truncated"] is False

    hop2 = extract_ego_subgraph(tmp_path, "a.py", max_hops=2, max_nodes=32)
    assert set(hop2["nodes"]) == {"a.py", "b.py", "e.py", "c.py", "f.py"}
    assert "d.py" not in hop2["nodes"]
    assert hop2["truncated"] is False

    capped = extract_ego_subgraph(tmp_path, "a.py", max_hops=3, max_nodes=2)
    assert len(capped["nodes"]) == 2
    assert capped["truncated"] is True


def test_incremental_graph_does_not_collide_with_symbol_graph_cache(tmp_path: Path) -> None:
    assert GRAPH_SYNC_REL_PATH != GRAPH_CACHE_REL_PATH
    with SymbolGraphIndex(merkle_root="m1") as sym_idx:
        sym_idx.save_to_cache(tmp_path)

    result = sync_incremental_ego_subgraph(
        tmp_path,
        {"a.py": "import b.py\n"},
        _extract_imports,
        "a.py",
        max_hops=1,
        max_nodes=8,
    )
    assert result["nodes"] == ["a.py", "b.py"]
    assert result["sync"] == {"skipped": 0, "updated": 1, "deleted": 0}
    assert (tmp_path / GRAPH_CACHE_REL_PATH).exists()
    assert (tmp_path / GRAPH_SYNC_REL_PATH).exists()

    loaded = SymbolGraphIndex.load_from_cache(tmp_path, expected_merkle_root="m1")
    assert loaded is not None
    loaded.close()


def test_incremental_graph_sync_rejects_symlink_or_junction_cache_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    tg_cache = root / ".tg_cache"

    try:
        os.symlink(outside, tg_cache, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(tg_cache), str(outside)],
                check=True,
                capture_output=True,
            )
        else:
            pytest.skip("symlink creation unavailable")

    try:
        with pytest.raises(OSError):
            sync_graph_edges(root, {"a.py": "import b.py\n"}, _extract_imports)
        with pytest.raises(OSError):
            extract_ego_subgraph(root, "a.py")
    finally:
        if os.name == "nt" and tg_cache.exists() and not tg_cache.is_symlink():
            subprocess.run(["cmd", "/c", "rmdir", str(tg_cache)], check=False)


def test_extract_ego_subgraph_handles_multiple_edge_kinds_per_dst(tmp_path: Path) -> None:
    def _multi_edges(path: str, _content: str) -> list[tuple[str, str]]:
        if path == "a.py":
            return [
                ("b.py", "import"),
                ("b.py", "call"),
                ("b.py", "type"),
                ("b.py", "ref"),
                ("c.py", "import"),
            ]
        return []

    sync_graph_edges(tmp_path, {"a.py": "1", "b.py": "2", "c.py": "3"}, _multi_edges)
    sub = extract_ego_subgraph(tmp_path, "a.py", max_hops=1, max_nodes=3)
    assert sub["nodes"] == ["a.py", "b.py", "c.py"]
    assert sub["truncated"] is False
