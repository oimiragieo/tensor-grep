"""Unit tests for DF-04 warm `tg prepare` cache acceleration."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from tensor_grep.cli import prepare_service, repo_map
from tensor_grep.cli.incremental_graph_sync import (
    GRAPH_SYNC_REL_PATH,
    sync_graph_edges,
    sync_graph_edges_from_paths,
)


def _make_sample_repo(root: Path) -> tuple[Path, Path, Path]:
    a_py = root / "alpha.py"
    b_py = root / "beta.py"
    c_py = root / "gamma.py"
    a_py.write_text(
        "from beta import helper_beta\n\n"
        "def compute_invoice_total(items):\n"
        "    return sum(helper_beta(x) for x in items)\n",
        encoding="utf-8",
    )
    b_py.write_text(
        "def helper_beta(val):\n    return val * 2\n",
        encoding="utf-8",
    )
    c_py.write_text(
        "def unrelated_gamma():\n    return 42\n",
        encoding="utf-8",
    )
    return a_py, b_py, c_py


def test_warm_prepare_reuses_cached_map_and_matches_cold_primary_target(
    tmp_path: Path, monkeypatch
) -> None:
    """Warm prepare makes 0 build_repo_map / full-parse calls and matches cold primary_target."""
    _make_sample_repo(tmp_path)

    build_calls = 0
    parse_calls: list[str] = []
    orig_build = repo_map.build_repo_map
    orig_parse = repo_map._imports_and_symbols_for_path

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    def spy_parse(path: Path, *args, **kwargs):
        parse_calls.append(str(path))
        return orig_parse(path, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)
    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", spy_parse)

    # Cold run
    cold = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    assert build_calls >= 1
    assert len(parse_calls) >= 3

    # Warm run: 0 build_repo_map calls, 0 file re-parses
    build_calls = 0
    parse_calls.clear()
    warm = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    assert build_calls == 0
    assert parse_calls == []
    assert warm["primary_target"] == cold["primary_target"]
    assert "ego_nodes" in warm["blast_radius_floor"]
    assert "ego_edges" in warm["blast_radius_floor"]


def test_warm_prepare_invalidates_only_modified_file(tmp_path: Path, monkeypatch) -> None:
    """Modifying one file invalidates and re-parses only that file, updating primary_target."""
    a_py, _b_py, c_py = _make_sample_repo(tmp_path)

    # Cold run to populate signed cache
    cold = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert cold["primary_target"]["symbol"] == "compute_invoice_total"

    build_calls = 0
    parse_calls: list[str] = []
    orig_build = repo_map.build_repo_map
    orig_parse = repo_map._imports_and_symbols_for_path

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    def spy_parse(path: Path, *args, **kwargs):
        parse_calls.append(str(path))
        return orig_parse(path, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)
    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", spy_parse)

    # Modify only gamma.py and bump mtime_ns explicitly to avoid coarse FS timestamp ties
    c_py.write_text(
        "def compute_invoice_total_v2(items):\n    return len(items)\n",
        encoding="utf-8",
    )
    st = c_py.stat()
    os.utime(c_py, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    warm_rm, _floors, _meta, cache_dirty = prepare_service._load_warm_prepare_cache(
        str(tmp_path), repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT, None
    )
    assert warm_rm is not None
    assert cache_dirty is True
    assert build_calls == 0
    assert len(parse_calls) == 1
    assert Path(parse_calls[0]).name == "gamma.py"

    parse_calls.clear()
    updated = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total_v2",
        claim=False,
    )
    assert updated["primary_target"]["symbol"] == "compute_invoice_total_v2"
    assert Path(updated["primary_target"]["file"]).name == "gamma.py"

    # Subsequent warm cache load with no further edits parses 0 files
    build_calls = 0
    parse_calls.clear()
    warm_rm2, _floors2, _meta2, cache_dirty2 = prepare_service._load_warm_prepare_cache(
        str(tmp_path), repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT, None
    )
    assert warm_rm2 is not None
    assert cache_dirty2 is False
    assert build_calls == 0
    assert parse_calls == []
    assert a_py.exists()


def test_warm_prepare_rejects_tampered_cache(tmp_path: Path, monkeypatch) -> None:
    """Tampering with the signed warm cache falls back cleanly to cold build_repo_map."""
    _make_sample_repo(tmp_path)

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    cache_file = tmp_path / prepare_service._PREPARE_CACHE_REL_PATH
    assert cache_file.is_file()

    envelope = json.loads(cache_file.read_text(encoding="utf-8"))
    envelope["mac"] = "0" * 64
    cache_file.write_text(json.dumps(envelope), encoding="utf-8")

    build_calls = 0
    orig_build = repo_map.build_repo_map

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)

    rebuilt = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert build_calls >= 1
    assert rebuilt["primary_target"]["symbol"] == "compute_invoice_total"


def test_sync_graph_edges_from_paths_skips_unchanged_without_reading(tmp_path: Path) -> None:
    """Stat+hash reconciliation skips reading unchanged files on warm runs."""
    f1 = tmp_path / "m1.py"
    f2 = tmp_path / "m2.py"
    f1.write_text("import m2\n", encoding="utf-8")
    f2.write_text("x = 1\n", encoding="utf-8")

    extracted: list[str] = []

    def extract_fn(path_str: str, _content: str) -> list[tuple[str, str]]:
        extracted.append(Path(path_str).name)
        return [("m2", "import")] if path_str.endswith("m1.py") else []

    first = sync_graph_edges_from_paths(tmp_path, [f1, f2], extract_fn)
    assert first == {
        "skipped": 0,
        "updated": 2,
        "deleted": 0,
        "incomplete_oversized": 0,
        "incomplete_unreadable": 0,
        "complete": 1,
    }
    assert sorted(extracted) == ["m1.py", "m2.py"]

    extracted.clear()
    second = sync_graph_edges_from_paths(tmp_path, [f1, f2], extract_fn)
    assert second == {
        "skipped": 2,
        "updated": 0,
        "deleted": 0,
        "incomplete_oversized": 0,
        "incomplete_unreadable": 0,
        "complete": 1,
    }
    assert extracted == []

    # Modify only f2
    f2.write_text("x = 222\n", encoding="utf-8")
    st = f2.stat()
    os.utime(f2, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    third = sync_graph_edges_from_paths(tmp_path, [f1, f2], extract_fn)
    assert third == {
        "skipped": 1,
        "updated": 1,
        "deleted": 0,
        "incomplete_oversized": 0,
        "incomplete_unreadable": 0,
        "complete": 1,
    }
    assert extracted == ["m2.py"]


def test_same_size_edit_with_restored_mtime_detected_via_ctime(tmp_path: Path, monkeypatch) -> None:
    """Same-size edit with restored mtime_ns is still detected via ctime_ns in prepare and graph sync."""
    a_py, _b_py, c_py = _make_sample_repo(tmp_path)
    orig_st = c_py.stat()
    orig_text = c_py.read_text(encoding="utf-8")
    replacement_text = "def compute_invoice_total_v9():\n    return 9\n"
    # Pad replacement to exact same byte length as original
    if len(replacement_text) < len(orig_text):
        replacement_text = replacement_text + (" " * (len(orig_text) - len(replacement_text)))
    else:
        orig_text = orig_text + (" " * (len(replacement_text) - len(orig_text)))
        c_py.write_text(orig_text, encoding="utf-8")
        os.utime(c_py, ns=(orig_st.st_atime_ns, orig_st.st_mtime_ns))
        orig_st = c_py.stat()

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )

    c_py.write_text(replacement_text, encoding="utf-8")
    os.utime(c_py, ns=(orig_st.st_atime_ns, orig_st.st_mtime_ns))
    new_st = c_py.stat()
    if new_st.st_ctime_ns == orig_st.st_ctime_ns:
        # On filesystems where ctime_ns doesn't advance within same tick, simulate ctime_ns bump
        orig_lstat = prepare_service._lstat_regular_file
        monkeypatch.setattr(
            prepare_service,
            "_lstat_regular_file",
            lambda p: (
                (orig_st.st_mtime_ns, orig_st.st_ctime_ns + 1_000_000, orig_st.st_size)
                if Path(p) == c_py
                else orig_lstat(p)
            ),
        )

    updated = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total_v9",
        claim=False,
    )
    assert updated["primary_target"]["symbol"] == "compute_invoice_total_v9"
    assert a_py.exists()


def test_oversized_files_skipped_cleanly_without_unbounded_read(
    tmp_path: Path, monkeypatch
) -> None:
    """Oversized files above _max_parse_bytes are skipped cleanly in prepare and graph sync."""
    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    _make_sample_repo(repo1)
    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 64)

    res = prepare_service._build_prepare_payload(
        path=str(repo1),
        query="helper_beta",
        claim=False,
    )
    # beta.py is <64 bytes, alpha.py is >64 bytes
    assert res["primary_target"]["symbol"] == "helper_beta"

    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    a_py, b_py, _c_py = _make_sample_repo(repo2)
    extracted: list[str] = []
    sync_res = sync_graph_edges_from_paths(
        repo2,
        [a_py, b_py],
        lambda p, _c: extracted.append(Path(p).name) or [],
    )
    assert sync_res == {
        "skipped": 0,
        "updated": 1,
        "deleted": 0,
        "incomplete_oversized": 1,
        "incomplete_unreadable": 0,
        "complete": 0,
    }
    assert extracted == ["beta.py"]


def test_project_config_change_invalidates_warm_prepare_cache(tmp_path: Path, monkeypatch) -> None:
    """Modifying a project config file (pyproject.toml) invalidates the warm prepare cache."""
    _make_sample_repo(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'sample'\n", encoding="utf-8")

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )

    build_calls = 0
    orig_build = repo_map.build_repo_map

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)

    pyproject.write_text("[project]\nname = 'sample-v2'\n", encoding="utf-8")
    st = pyproject.stat()
    os.utime(pyproject, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert build_calls >= 1


def test_sync_graph_edges_maintains_file_stats_with_ctime(tmp_path: Path) -> None:
    """sync_graph_edges() populates file_stats including ctime_ns for disk-backed files."""
    f1 = tmp_path / "x.py"
    f1.write_text("import y\n", encoding="utf-8")

    sync_graph_edges(tmp_path, {str(f1): f1.read_bytes()}, lambda _p, _c: [("y", "import")])
    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        rows = conn.execute("SELECT path, mtime_ns, ctime_ns, size FROM file_stats").fetchall()
        assert len(rows) == 1
        st = f1.lstat()
        assert rows[0] == (str(f1), int(st.st_mtime_ns), int(st.st_ctime_ns), int(st.st_size))
    finally:
        conn.close()


def test_oversized_existing_file_prunes_stale_graph_rows_and_marks_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    """Sol #1/#4: existing file growing past max_bytes deletes stale rows and sets complete=0."""
    f1 = tmp_path / "m1.py"
    f1.write_text("import m2\n", encoding="utf-8")
    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 64)

    res1 = sync_graph_edges_from_paths(tmp_path, [f1], lambda _p, _c: [("m2", "import")])
    assert res1["complete"] == 1
    assert res1["incomplete_oversized"] == 0

    f1.write_text("import m2\n" + ("# pad\n" * 20), encoding="utf-8")
    res2 = sync_graph_edges_from_paths(tmp_path, [f1], lambda _p, _c: [("m2", "import")])
    assert res2 == {
        "skipped": 0,
        "updated": 0,
        "deleted": 0,
        "incomplete_oversized": 1,
        "incomplete_unreadable": 0,
        "complete": 0,
    }
    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        assert conn.execute("SELECT * FROM edges").fetchall() == []
        assert conn.execute("SELECT * FROM files").fetchall() == []
        assert conn.execute("SELECT * FROM file_stats").fetchall() == []
    finally:
        conn.close()


def test_cold_save_requires_pre_build_stats_and_detects_max_parse_bytes_change(
    tmp_path: Path, monkeypatch
) -> None:
    """Daybreak #1 + Sol #2/#6/#7: cold save requires pre_build_stats; max_parse_bytes invalidates."""
    _make_sample_repo(tmp_path)
    rm = repo_map.build_repo_map(
        str(tmp_path), max_repo_files=repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    prepare_service._save_warm_prepare_cache(
        str(tmp_path),
        repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT,
        rm,
        {},
        None,
        None,
        pre_build_stats=None,
    )
    assert not (tmp_path / prepare_service._PREPARE_CACHE_REL_PATH).exists()

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert (tmp_path / prepare_service._PREPARE_CACHE_REL_PATH).exists()

    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 123456)
    loaded, _, _, _ = prepare_service._load_warm_prepare_cache(
        str(tmp_path), repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT, None
    )
    assert loaded is None


def test_graph_sync_refreshes_on_context_digest_and_rejects_incomplete_floor_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    """Sol #3/#4/#5: context_digest refreshes edges; incomplete sync skips floor reuse; scan meta."""
    a_py, b_py, _c_py = _make_sample_repo(tmp_path)
    r1 = sync_graph_edges_from_paths(
        tmp_path, [a_py, b_py], lambda _p, _c: [("old", "import")], context_digest="d1"
    )
    assert r1["updated"] == 2
    r2 = sync_graph_edges_from_paths(
        tmp_path, [a_py, b_py], lambda _p, _c: [("new", "import")], context_digest="d1"
    )
    assert r2["skipped"] == 2 and r2["updated"] == 0
    r3 = sync_graph_edges_from_paths(
        tmp_path, [a_py, b_py], lambda _p, _c: [("new", "import")], context_digest="d2"
    )
    assert r3["updated"] == 2 and r3["complete"] == 1

    # Sol #5: mismatching scanned_files in cached scan_limit bypasses fast return
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    warm_rm, _floors, _meta, cache_dirty = prepare_service._load_warm_prepare_cache(
        str(tmp_path), repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT, None
    )
    assert warm_rm is not None
    assert cache_dirty is True

    # Sol #4: complete=0 prevents reusing cached blast_radius_floor
    floor_calls = 0
    orig_floor = prepare_service._build_prepare_blast_radius_floor

    def spy_floor(**kwargs):
        nonlocal floor_calls
        floor_calls += 1
        return orig_floor(**kwargs)

    monkeypatch.setattr(prepare_service, "_build_prepare_blast_radius_floor", spy_floor)
    monkeypatch.setattr(
        "tensor_grep.cli.symbol_graph_index.sync_incremental_ego_subgraph_from_paths",
        lambda *a, **kw: {
            "nodes": [],
            "edges": [],
            "sync": {"updated": 0, "deleted": 0, "complete": 0},
        },
    )
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert floor_calls == 1


def test_interrupted_context_digest_refresh_not_committed(tmp_path: Path) -> None:
    """Sol #18: deadline-interrupted context_digest refresh does not commit new digest."""
    a_py, b_py, _c_py = _make_sample_repo(tmp_path)
    r1 = sync_graph_edges_from_paths(
        tmp_path, [a_py, b_py], lambda _p, _c: [("old", "import")], context_digest="d1"
    )
    assert r1["complete"] == 1

    r_interrupted = sync_graph_edges_from_paths(
        tmp_path,
        [a_py, b_py],
        lambda _p, _c: [("new", "import")],
        deadline_monotonic=0.0,
        context_digest="d2",
    )
    assert r_interrupted["complete"] == 0
    assert r_interrupted["updated"] == 0

    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'context_digest'").fetchone()
        assert row == ("d1",)
    finally:
        conn.close()

    r_resumed = sync_graph_edges_from_paths(
        tmp_path, [a_py, b_py], lambda _p, _c: [("new", "import")], context_digest="d2"
    )
    assert r_resumed["updated"] == 2
    assert r_resumed["complete"] == 1
    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'context_digest'").fetchone()
        assert row == ("d2",)
    finally:
        conn.close()


def test_oserror_read_invalidates_stale_graph_rows_and_sets_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    """Sol #19: OSError reading a dirty file tracks incomplete_unreadable and prunes stale rows."""
    f1 = tmp_path / "m1.py"
    f1.write_text("import m2\n", encoding="utf-8")

    res1 = sync_graph_edges_from_paths(tmp_path, [f1], lambda _p, _c: [("m2", "import")])
    assert res1["complete"] == 1
    assert res1["incomplete_unreadable"] == 0

    f1.write_text("import m3\n", encoding="utf-8")
    st = f1.stat()
    os.utime(f1, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    from tensor_grep.cli import incremental_graph_sync as igs

    orig_read_confined = igs.read_confined

    def _raise_oserror(root, target, limit):
        if Path(target) == f1:
            raise OSError("simulated unreadable file")
        return orig_read_confined(root, target, limit)

    monkeypatch.setattr(igs, "read_confined", _raise_oserror)
    res2 = sync_graph_edges_from_paths(tmp_path, [f1], lambda _p, _c: [("m3", "import")])
    assert res2 == {
        "skipped": 0,
        "updated": 0,
        "deleted": 0,
        "incomplete_oversized": 0,
        "incomplete_unreadable": 1,
        "complete": 0,
    }
    conn = sqlite3.connect(str(tmp_path / GRAPH_SYNC_REL_PATH))
    try:
        assert conn.execute("SELECT * FROM edges").fetchall() == []
        assert conn.execute("SELECT * FROM files").fetchall() == []
        assert conn.execute("SELECT * FROM file_stats").fetchall() == []
    finally:
        conn.close()


def test_possibly_truncated_map_disables_cached_blast_radius_floor_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    """Sol #20: rm.possibly_truncated=True disables cached blast_radius_floor reuse."""
    _make_sample_repo(tmp_path)
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )

    orig_load = prepare_service._load_warm_prepare_cache

    def _load_truncated(*args, **kwargs):
        warm_rm, floors, meta, dirty = orig_load(*args, **kwargs)
        assert warm_rm is not None
        warm_rm = dict(warm_rm)
        warm_rm["scan_limit"] = {
            **dict(warm_rm.get("scan_limit") or {}),
            "possibly_truncated": True,
        }
        return warm_rm, floors, meta, dirty

    floor_calls = 0
    orig_floor = prepare_service._build_prepare_blast_radius_floor

    def spy_floor(**kwargs):
        nonlocal floor_calls
        floor_calls += 1
        return orig_floor(**kwargs)

    monkeypatch.setattr(prepare_service, "_load_warm_prepare_cache", _load_truncated)
    monkeypatch.setattr(prepare_service, "_build_prepare_blast_radius_floor", spy_floor)
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert floor_calls == 1


def test_cold_prepare_persists_complete_rm_even_if_floor_hits_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    """Complete rm is saved to .tg_cache even when blast_radius_floor expires the deadline."""
    _make_sample_repo(tmp_path)

    def _expired_floor(**_kwargs):
        return (
            {
                "symbol": "compute_invoice_total",
                "callers_count": 0,
                "top_callers": [],
                "source": "supplementary_blast_radius",
                "graph_trust_summary": {},
                "resolution_gaps": [],
                "possibly_incomplete": True,
            },
            True,
        )

    monkeypatch.setattr(prepare_service, "_build_prepare_blast_radius_floor", _expired_floor)
    payload = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert payload.get("partial") is True
    assert (tmp_path / prepare_service._PREPARE_CACHE_REL_PATH).exists()
