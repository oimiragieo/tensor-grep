"""Tests for the persisted chunk-BM25 semantic index (build/save/load + staleness fingerprint)."""

import json
import os
from pathlib import Path

import pytest

from tensor_grep.core.semantic_index import (
    _META_NAME,
    INDEX_VERSION,
    build_and_save,
    compute_fingerprint,
    default_index_dir,
    load_or_warn,
)


def test_default_index_dir_respects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_SEMANTIC_INDEX_DIR", str(tmp_path / "custom"))
    assert default_index_dir(str(tmp_path)) == tmp_path / "custom"


def test_default_index_dir_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_SEMANTIC_INDEX_DIR", raising=False)
    assert default_index_dir(str(tmp_path)) == tmp_path / ".tg_semantic_index"


def test_build_save_load_query_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_SEMANTIC_INDEX_DIR", raising=False)
    f1 = tmp_path / "a.py"
    f1.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("def render_html():\n    return node\n", encoding="utf-8")
    idx_dir = tmp_path / ".tg_semantic_index"

    built = build_and_save([str(f1), str(f2)], idx_dir)
    assert built.query("parse invoice")  # in-memory index works

    loaded = load_or_warn(idx_dir)
    assert loaded is not None
    results = loaded.query("parse invoice", top_k=2)
    assert results
    top_chunk = loaded.chunks[results[0][0]]
    assert top_chunk.file_path == str(f1)


def test_compute_fingerprint_changes_with_mtime(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    os.utime(f, (1000, 1000))
    fp1 = compute_fingerprint([str(f)])
    os.utime(f, (2000, 2000))
    fp2 = compute_fingerprint([str(f)])
    assert fp1 != fp2


def test_load_or_warn_missing_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert load_or_warn(tmp_path / "nope") is None
    assert "semantic index" in capsys.readouterr().err.lower()


def test_load_or_warn_stale_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    os.utime(f, (1000, 1000))
    idx_dir = tmp_path / ".tg_semantic_index"
    build_and_save([str(f)], idx_dir)

    os.utime(f, (9999, 9999))  # change after indexing -> stale
    assert load_or_warn(idx_dir) is None
    assert "stale" in capsys.readouterr().err.lower()


def test_meta_records_chunker_mode_and_current_version(tmp_path: Path) -> None:
    """PR-S1 index hygiene: the persisted meta records both the schema version and which chunker
    mode built the index -- see semantic_index.INDEX_VERSION's docstring for the bug class this
    closes (a structural-chunked index silently reused as if it were fixed-window, or vice versa)."""
    f = tmp_path / "a.py"
    f.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    idx_dir = tmp_path / ".tg_semantic_index"
    build_and_save([str(f)], idx_dir)

    meta = json.loads((idx_dir / _META_NAME).read_text(encoding="utf-8"))
    assert meta["version"] == INDEX_VERSION
    assert meta["chunker_mode"] == "fixed-window"


def test_load_or_warn_rejects_index_built_under_a_different_chunker_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An index built with TG_CHUNKER unset (fixed-window) must never be silently reused once
    TG_CHUNKER=structural is active (or vice versa) -- chunk boundaries differ between the two
    modes, so blending them would corrupt the BM25 corpus's line-containment invariants."""
    monkeypatch.delenv("TG_CHUNKER", raising=False)
    f = tmp_path / "a.py"
    f.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    idx_dir = tmp_path / ".tg_semantic_index"
    build_and_save([str(f)], idx_dir)

    monkeypatch.setenv("TG_CHUNKER", "structural")
    assert load_or_warn(idx_dir) is None
    err = capsys.readouterr().err.lower()
    assert "chunker mode" in err


def test_load_or_warn_rejects_stale_schema_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-v2 index (no ``chunker_mode`` field at all) must be treated as stale rather than
    loaded and silently misread -- the version bump alone is enough to force a rebuild."""
    f = tmp_path / "a.py"
    f.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    idx_dir = tmp_path / ".tg_semantic_index"
    build_and_save([str(f)], idx_dir)

    meta_path = idx_dir / _META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["version"] = 1  # simulate a pre-cAST (v1) index written before chunker_mode existed
    del meta["chunker_mode"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert load_or_warn(idx_dir) is None
    err = capsys.readouterr().err.lower()
    assert "schema version" in err
