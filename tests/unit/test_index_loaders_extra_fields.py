"""F-12: index loaders must tolerate unknown fields instead of crashing permanently."""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli import checkpoint_store, session_store


def test_session_load_index_ignores_unknown_fields(tmp_path: Path) -> None:
    entry = {
        "version": 1,
        "session_id": "s1",
        "root": str(tmp_path),
        "created_at": "t",
        "file_count": 1,
        "symbol_count": 2,
    }
    index_path = session_store._index_path(tmp_path)
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps([entry]), encoding="utf-8")
    # CONTROL: the exact-shape entry loads.
    assert [r.session_id for r in session_store._load_index(tmp_path)] == ["s1"]

    index_path.write_text(json.dumps([{**entry, "added_by_a_newer_tg": True}]), encoding="utf-8")
    assert [r.session_id for r in session_store._load_index(tmp_path)] == ["s1"]


def test_checkpoint_load_index_ignores_unknown_fields(tmp_path: Path) -> None:
    entry = {
        "version": 1,
        "checkpoint_id": "c1",
        "mode": "filesystem-snapshot",
        "root": str(tmp_path),
        "created_at": "t",
        "file_count": 1,
    }
    index_path = checkpoint_store._index_path(tmp_path)
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps([entry]), encoding="utf-8")
    assert [r.checkpoint_id for r in checkpoint_store._load_index(tmp_path)] == ["c1"]

    index_path.write_text(json.dumps([{**entry, "added_by_a_newer_tg": True}]), encoding="utf-8")
    assert [r.checkpoint_id for r in checkpoint_store._load_index(tmp_path)] == ["c1"]
