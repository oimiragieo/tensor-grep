"""Unit tests for CodeNib-style unified cache manifest and LARGER lazy graph expansion."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import prepare_service, repo_map
from tensor_grep.cli.unified_cache import (
    CACHE_VIEWS,
    MANIFEST_REL_PATH,
    build_source_address_contract,
    check_view_freshness,
    compute_generation_merkle_root,
    compute_view_artifact_fingerprint,
    is_manifest_fresh,
    load_unified_manifest,
    save_unified_manifest,
    update_view_states,
)


def _make_repo(root: Path) -> tuple[Path, Path, Path, Path]:
    a_py = root / "billing.py"
    b_py = root / "tax.py"
    c_py = root / "ledger.py"
    t_py = root / "test_billing.py"
    a_py.write_text(
        "from tax import compute_tax\n\ndef compute_invoice_total(amount):\n    return amount + compute_tax(amount)\n",
        encoding="utf-8",
    )
    b_py.write_text(
        "from ledger import record_entry\n\ndef compute_tax(amount):\n    record_entry(amount)\n    return amount * 0.1\n",
        encoding="utf-8",
    )
    c_py.write_text("def record_entry(val):\n    return val\n", encoding="utf-8")
    t_py.write_text(
        "from billing import compute_invoice_total\n\ndef test_total():\n    assert compute_invoice_total(10) == 11\n",
        encoding="utf-8",
    )
    return a_py, b_py, c_py, t_py


def test_unified_manifest_creation_and_cross_view_freshness(tmp_path: Path) -> None:
    files = list(_make_repo(tmp_path))
    merkle_root, source_address = build_source_address_contract(tmp_path, files)
    assert len(merkle_root) == 64
    assert source_address["file_count"] == 4

    hybrid_path = tmp_path / ".tg_cache" / "hybrid_v1" / "index.sqlite3"
    hybrid_path.parent.mkdir(parents=True, exist_ok=True)
    hybrid_path.write_bytes(b"valid-hybrid-v1")
    symbols_path = tmp_path / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_path.write_bytes(b"valid-symbols-v1")
    graph_path = tmp_path / ".tg_cache" / "graph_v1" / "incremental_edges.sqlite3"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_bytes(b"valid-graph-v1")
    hybrid_fp = compute_view_artifact_fingerprint(tmp_path, "hybrid_v1")
    assert hybrid_fp is not None

    assert update_view_states(
        tmp_path,
        merkle_root,
        {
            "symbols_v1": (True, 1),
            "graph_v1": (True, 1),
            "hybrid_v1": (True, 2),
            "index_v1": (False, 1),
        },
        source_address=source_address,
        artifact_fingerprints={"hybrid_v1": hybrid_fp},
    )
    manifest_path = tmp_path / MANIFEST_REL_PATH
    assert manifest_path.is_file()

    loaded = load_unified_manifest(tmp_path)
    assert loaded is not None
    assert loaded["merkle_root"] == merkle_root
    freshness = check_view_freshness(loaded, merkle_root, CACHE_VIEWS)
    assert freshness == {
        "symbols_v1": True,
        "graph_v1": True,
        "hybrid_v1": True,
        "index_v1": False,
    }
    assert is_manifest_fresh(loaded, merkle_root, ("symbols_v1", "graph_v1"))
    assert not is_manifest_fresh(loaded, merkle_root, ("index_v1",))


def test_unified_manifest_merkle_invalidation_on_file_change(tmp_path: Path) -> None:
    a_py, b_py, c_py, t_py = _make_repo(tmp_path)
    files = [a_py, b_py, c_py, t_py]
    m1, addr1 = build_source_address_contract(tmp_path, files)
    assert save_unified_manifest(
        tmp_path,
        m1,
        source_address=addr1,
        views={"symbols_v1": {"name": "symbols_v1", "merkle_root": m1, "ready": True}},
    )

    b_py.write_text(
        "def compute_tax(amount):\n    return amount * 0.25\n",
        encoding="utf-8",
    )
    st = b_py.stat()
    os.utime(b_py, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    m2 = compute_generation_merkle_root(tmp_path, files)
    assert m2 != m1
    loaded = load_unified_manifest(tmp_path)
    assert not is_manifest_fresh(loaded, m2, ("symbols_v1",))
    assert check_view_freshness(loaded, m2, ("symbols_v1",)) == {"symbols_v1": False}


def test_unified_manifest_rejects_tampered_and_unsigned_checkout_manifests(
    tmp_path: Path,
) -> None:
    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)
    assert save_unified_manifest(tmp_path, m1, source_address=addr1)

    manifest_path = tmp_path / MANIFEST_REL_PATH
    envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
    envelope["mac"] = "0" * 64
    manifest_path.write_text(json.dumps(envelope), encoding="utf-8")
    assert load_unified_manifest(tmp_path) is None

    # Unsigned plain JSON payload without mac envelope also fails closed.
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "merkle_root": m1, "source_address": addr1, "views": {}}),
        encoding="utf-8",
    )
    assert load_unified_manifest(tmp_path) is None


def test_unified_manifest_refuses_symlink_or_junction_cache_dir(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
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
        m1, addr1 = build_source_address_contract(root, [root / "a.py"])
        assert save_unified_manifest(root, m1, source_address=addr1) is False
        assert load_unified_manifest(root) is None
    finally:
        if os.name == "nt" and tg_cache.exists() and not tg_cache.is_symlink():
            subprocess.run(["cmd", "/c", "rmdir", str(tg_cache)], check=False)


def test_warm_prepare_reuses_unified_manifest_and_expands_larger_neighborhood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)

    build_calls = 0
    orig_build = repo_map.build_repo_map

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)

    cold = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    assert build_calls >= 1
    manifest_path = tmp_path / MANIFEST_REL_PATH
    assert manifest_path.is_file()
    manifest = load_unified_manifest(tmp_path)
    assert manifest is not None
    assert manifest["views"]["symbols_v1"]["ready"] is True
    assert manifest["views"]["graph_v1"]["ready"] is True
    cold_updated_at = manifest["views"]["graph_v1"]["updated_at"]

    # 2-hop LARGER expansion should surface test_billing.py or tax.py in ego_nodes.
    ego_nodes = cold["blast_radius_floor"]["ego_nodes"]
    assert any("billing.py" in n for n in ego_nodes)
    assert any("test_billing.py" in n or "tax.py" in n for n in ego_nodes)

    # Warm run reuses warm cache + unified manifest with 0 build_repo_map calls.
    build_calls = 0
    warm = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    assert build_calls == 0
    assert warm["primary_target"] == cold["primary_target"]
    warm_manifest = load_unified_manifest(tmp_path)
    assert warm_manifest is not None
    assert warm_manifest["views"]["graph_v1"]["updated_at"] == cold_updated_at


def test_prepare_forces_graph_refresh_when_manifest_stale_or_invalid_and_tracks_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)

    # Create hybrid_v1 and index_v1 artifacts on disk so view inspection records them.
    hybrid_path = tmp_path / ".tg_cache" / "hybrid_v1" / "index.sqlite3"
    hybrid_path.parent.mkdir(parents=True, exist_ok=True)
    hybrid_path.write_bytes(b"stale-hybrid")
    index_path = tmp_path / ".tg_cache" / "index_v1" / "a1b2c3d4e5f6.tg_index"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(b"stale-index")

    # Mark graph_v1 ready=False in manifest_v1.json -> prepare must force refresh and repair it.
    assert update_view_states(
        tmp_path,
        m1,
        {"symbols_v1": (True, 1), "graph_v1": (False, 1)},
        source_address=addr1,
    )

    seen_force_refresh: list[bool] = []
    from tensor_grep.cli import symbol_graph_index

    orig_sync = symbol_graph_index.sync_incremental_ego_subgraph_from_paths

    def spy_sync(*args, **kwargs):
        seen_force_refresh.append(bool(kwargs.get("force_refresh", False)))
        return orig_sync(*args, **kwargs)

    monkeypatch.setattr(symbol_graph_index, "sync_incremental_ego_subgraph_from_paths", spy_sync)

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    assert seen_force_refresh == [True]
    repaired = load_unified_manifest(tmp_path)
    assert repaired is not None
    assert repaired["views"]["graph_v1"]["ready"] is True
    assert repaired["views"]["symbols_v1"]["ready"] is True
    assert repaired["views"]["hybrid_v1"]["ready"] is False
    assert repaired["views"]["index_v1"]["ready"] is False


def test_prepare_context_digest_changes_across_queries_and_marks_partial_symbols_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    seen_digests: list[str] = []
    from tensor_grep.cli import symbol_graph_index

    orig_sync = symbol_graph_index.sync_incremental_ego_subgraph_from_paths

    def spy_sync(*args, **kwargs):
        seen_digests.append(str(kwargs.get("context_digest") or ""))
        return orig_sync(*args, **kwargs)

    monkeypatch.setattr(symbol_graph_index, "sync_incremental_ego_subgraph_from_paths", spy_sync)

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute tax rate",
        claim=False,
    )
    assert len(seen_digests) == 2
    assert seen_digests[0] != ""
    assert seen_digests[0] != seen_digests[1]

    # Partial repo_map must NOT mark symbols_v1 ready even when graph sync completes.
    orig_build = repo_map.build_repo_map

    def partial_build(*args, **kwargs):
        rm = orig_build(*args, **kwargs)
        rm["partial"] = True
        return rm

    monkeypatch.setattr(repo_map, "build_repo_map", partial_build)
    partial_dir = tmp_path / "partial_repo"
    partial_dir.mkdir()
    _make_repo(partial_dir)
    prepare_service._build_prepare_payload(
        path=str(partial_dir),
        query="invoice total calculation",
        claim=False,
    )
    partial_manifest = load_unified_manifest(partial_dir)
    assert partial_manifest is not None
    assert partial_manifest["views"]["graph_v1"]["ready"] is True
    assert partial_manifest["views"]["symbols_v1"]["ready"] is False


def test_prepare_repairs_fresh_manifest_to_not_ready_on_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path)
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    fresh = load_unified_manifest(tmp_path)
    assert fresh is not None
    assert fresh["views"]["symbols_v1"]["ready"] is True
    assert fresh["views"]["graph_v1"]["ready"] is True

    # Simulate a subsequent degraded run on the same source generation where both
    # repo_map is partial and graph sync is incomplete.
    orig_build = repo_map.build_repo_map

    def partial_build(*args, **kwargs):
        rm = orig_build(*args, **kwargs)
        rm["partial"] = True
        return rm

    from tensor_grep.cli import symbol_graph_index

    def incomplete_sync(*args, **kwargs):
        return {"nodes": [], "edges": [], "sync": {"complete": 0, "updated": 0, "deleted": 0}}

    monkeypatch.setattr(
        prepare_service,
        "_load_warm_prepare_cache",
        lambda *_args, **_kwargs: (None, {}, None, False),
    )
    monkeypatch.setattr(repo_map, "build_repo_map", partial_build)
    monkeypatch.setattr(
        symbol_graph_index, "sync_incremental_ego_subgraph_from_paths", incomplete_sync
    )

    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    degraded = load_unified_manifest(tmp_path)
    assert degraded is not None
    assert degraded["views"]["symbols_v1"]["ready"] is False
    assert degraded["views"]["graph_v1"]["ready"] is False


def test_unified_manifest_rejects_deeply_nested_json_recursion_error(tmp_path: Path) -> None:
    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)
    assert save_unified_manifest(tmp_path, m1, source_address=addr1)

    manifest_path = tmp_path / MANIFEST_REL_PATH
    depth = 2000
    deep_json = ("[" * depth) + "0" + ("]" * depth)
    manifest_path.write_text(deep_json, encoding="utf-8")
    assert load_unified_manifest(tmp_path) is None


def test_unified_manifest_refuses_hardlinked_manifest(tmp_path: Path) -> None:
    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)
    assert save_unified_manifest(tmp_path, m1, source_address=addr1)

    manifest_path = tmp_path / MANIFEST_REL_PATH
    hardlink_path = tmp_path / "manifest_hardlink.json"
    try:
        os.link(manifest_path, hardlink_path)
    except OSError:
        pytest.skip("hardlink creation unavailable")

    assert load_unified_manifest(tmp_path) is None


def test_prepare_resets_stale_ready_views_when_artifacts_removed_or_replaced(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.unified_cache import compute_view_artifact_fingerprint

    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)

    hybrid_path = tmp_path / ".tg_cache" / "hybrid_v1" / "index.sqlite3"
    hybrid_path.parent.mkdir(parents=True, exist_ok=True)
    hybrid_path.write_bytes(b"valid-hybrid-v1")

    index_path = tmp_path / ".tg_cache" / "index_v1" / "a1b2c3d4e5f6.tg_index"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(b"valid-index-v1")

    hybrid_fp = compute_view_artifact_fingerprint(tmp_path, "hybrid_v1")
    index_fp = compute_view_artifact_fingerprint(tmp_path, "index_v1")
    assert hybrid_fp is not None
    assert index_fp is not None

    assert update_view_states(
        tmp_path,
        m1,
        {"hybrid_v1": (True, 1), "index_v1": (True, 1)},
        source_address=addr1,
        artifact_fingerprints={"hybrid_v1": hybrid_fp, "index_v1": index_fp},
    )

    # Remove hybrid_v1 artifact and replace index_v1 artifact with different content.
    hybrid_path.unlink()
    index_path.write_bytes(b"replaced-index-v1-different-bytes")

    # save_unified_manifest must not preserve stale ready=True for missing/replaced artifacts.
    assert update_view_states(tmp_path, m1, {"symbols_v1": (True, 1)}, source_address=addr1)
    after_save = load_unified_manifest(tmp_path)
    assert after_save is not None
    assert after_save["views"]["hybrid_v1"]["ready"] is False
    assert after_save["views"]["index_v1"]["ready"] is False

    # Re-seed ready=True with old fingerprints, then run prepare_service -> must reset to False.
    assert update_view_states(
        tmp_path,
        m1,
        {"hybrid_v1": (True, 1), "index_v1": (True, 1)},
        source_address=addr1,
        artifact_fingerprints={"hybrid_v1": hybrid_fp, "index_v1": index_fp},
    )
    prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="invoice total calculation",
        claim=False,
    )
    after_prepare = load_unified_manifest(tmp_path)
    assert after_prepare is not None
    assert after_prepare["views"]["hybrid_v1"]["ready"] is False
    assert after_prepare["views"]["index_v1"]["ready"] is False


def test_save_and_check_view_freshness_fail_closed_on_missing_or_mismatched_artifacts(
    tmp_path: Path,
) -> None:
    files = list(_make_repo(tmp_path))
    m1, addr1 = build_source_address_contract(tmp_path, files)

    # Newly supplied hybrid_v1/index_v1 without on-disk artifacts must be forced ready=False.
    assert update_view_states(
        tmp_path,
        m1,
        {"hybrid_v1": (True, 1), "index_v1": (True, 1)},
        source_address=addr1,
    )
    loaded = load_unified_manifest(tmp_path)
    assert loaded is not None
    assert loaded["views"]["hybrid_v1"]["ready"] is False
    assert loaded["views"]["index_v1"]["ready"] is False
    assert check_view_freshness(loaded, m1, ("hybrid_v1", "index_v1")) == {
        "hybrid_v1": False,
        "index_v1": False,
    }

    # Create real hybrid_v1 artifact, save with matching fingerprint -> fresh=True.
    hybrid_path = tmp_path / ".tg_cache" / "hybrid_v1" / "index.sqlite3"
    hybrid_path.parent.mkdir(parents=True, exist_ok=True)
    hybrid_path.write_bytes(b"valid-hybrid-v1")
    symbols_path = tmp_path / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    symbols_path.write_bytes(b"valid-symbols-v1")
    graph_path = tmp_path / ".tg_cache" / "graph_v1" / "incremental_edges.sqlite3"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_bytes(b"valid-graph-v1")
    hybrid_fp = compute_view_artifact_fingerprint(tmp_path, "hybrid_v1")
    assert hybrid_fp is not None
    assert update_view_states(
        tmp_path,
        m1,
        {"hybrid_v1": (True, 1)},
        source_address=addr1,
        artifact_fingerprints={"hybrid_v1": hybrid_fp},
    )
    loaded_ok = load_unified_manifest(tmp_path)
    assert loaded_ok is not None
    assert check_view_freshness(loaded_ok, m1, ("hybrid_v1",)) == {"hybrid_v1": True}

    # Mutate on-disk artifact -> check_view_freshness must immediately return False.
    hybrid_path.write_bytes(b"mutated-hybrid-v1-bytes")
    assert check_view_freshness(loaded_ok, m1, ("hybrid_v1",)) == {"hybrid_v1": False}

    # Saving newly supplied ready=True with stale fingerprint must force ready=False.
    assert update_view_states(
        tmp_path,
        m1,
        {"hybrid_v1": (True, 1)},
        source_address=addr1,
        artifact_fingerprints={"hybrid_v1": hybrid_fp},
    )
    loaded_stale = load_unified_manifest(tmp_path)
    assert loaded_stale is not None
    assert loaded_stale["views"]["hybrid_v1"]["ready"] is False
