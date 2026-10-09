"""Whole-file AST reuse must never mistake metadata or an incomplete changeset for content."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from tensor_grep.cli import lang_registry, repo_map, symbols_cache
from tensor_grep.cli.symbols_cache_io import cache_lock, read_confined


def _source(root: Path, name: str = "a.py", symbol: str = "alpha") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"def {symbol}():\n    return 1\n", encoding="utf-8")
    return path


def _names(payload: dict) -> set[str]:
    return {symbol["name"] for symbol in payload["symbols"]}


def test_whole_file_cache_survives_restarts_without_parsing_again(
    tmp_path: Path, monkeypatch
) -> None:
    _source(tmp_path)
    cold = repo_map.build_repo_map(tmp_path)
    assert cold["symbol_cache"]["misses"] == 1
    repo_map._clear_all_source_caches()
    monkeypatch.setattr(
        repo_map, "_imports_and_symbols_for_path", lambda *a, **k: pytest.fail("reparse")
    )
    warm = repo_map.build_repo_map(tmp_path)
    assert _names(warm) == {"alpha"}
    assert warm["symbol_cache"]["hits"] == 1
    assert warm["symbol_cache"]["merkle_root"] == cold["symbol_cache"]["merkle_root"]


def test_same_size_mtime_edit_is_reconciled_even_with_empty_changeset(tmp_path: Path) -> None:
    path = _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    original = path.stat()
    path.write_text(path.read_text().replace("alpha", "bravo"))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    second = repo_map.build_repo_map_incremental(
        first, {"added": [], "modified": [], "removed": []}
    )
    assert _names(second) == {"bravo"}
    assert second["symbol_cache"]["misses"] == 1
    assert second["symbol_cache"]["freshness_route"] == "content-reconciliation"
    assert second["symbol_cache"]["trusted_notifications"] is False


def test_non_git_add_remove_and_rename_reuse_content_with_correct_paths(tmp_path: Path) -> None:
    path = _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    moved = tmp_path / "nested" / "moved.py"
    moved.parent.mkdir()
    path.rename(moved)
    _source(tmp_path, "untracked.py", "other")
    second = repo_map.build_repo_map_incremental(first, {"overflow": True})
    assert _names(second) == {"alpha", "other"}
    assert second["symbol_cache"]["hits"] == 1
    assert second["symbol_cache"]["misses"] == 1
    assert all(s["file"] != str(path) for s in second["symbols"])
    assert next(s["file"] for s in second["symbols"] if s["name"] == "alpha") == str(moved)
    moved.unlink()
    third = repo_map.build_repo_map(tmp_path)
    assert _names(third) == {"other"}


def test_modified_ignore_configuration_invalidates_inventory_and_keys(tmp_path: Path) -> None:
    _source(tmp_path)
    _source(tmp_path, "skip.py", "hidden")
    ignore = tmp_path / ".gitignore"
    ignore.write_text("skip.py\n")
    first = repo_map.build_repo_map(tmp_path)
    assert _names(first) == {"alpha"}
    ignore.write_text("a.py\n")
    second = repo_map.build_repo_map(tmp_path)
    assert _names(second) == {"hidden"}
    assert second["symbol_cache"]["misses"] >= 1


def test_corrupt_database_rebuilds_and_discloses_recovery(tmp_path: Path) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    database = Path(first["symbol_cache"]["path"])
    database.write_bytes(b"corrupt metadata")
    rebuilt = repo_map.build_repo_map(tmp_path)
    assert _names(rebuilt) == {"alpha"}
    assert rebuilt["symbol_cache"]["status"].startswith("rebuild-required")
    assert repo_map.build_repo_map(tmp_path)["symbol_cache"]["hits"] == 1


def test_corrupt_product_receipt_rebuilds_instead_of_returning_wrong_symbols(
    tmp_path: Path,
) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    with closing(sqlite3.connect(first["symbol_cache"]["path"])) as connection:
        connection.execute("UPDATE entries SET payload='{}'")
        connection.commit()
    rebuilt = repo_map.build_repo_map(tmp_path)
    assert _names(rebuilt) == {"alpha"}
    assert rebuilt["symbol_cache"]["status"] == "corrupt-entry-rebuilt"


def test_source_cap_reports_omission_with_positive_control(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    _source(tmp_path, "b.py", "bravo")
    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 8)
    refused = repo_map.build_repo_map(tmp_path)
    assert refused["partial"] is True
    assert refused["symbol_cache_coverage"]["omitted_files"] == 2
    assert "exceeds 8 bytes" in refused["symbol_cache_coverage"]["sample"][0]["reason"]
    monkeypatch.setattr(repo_map, "_max_parse_bytes", lambda: 4096)
    assert _names(repo_map.build_repo_map(tmp_path)) == {"alpha", "bravo"}


def test_racing_edit_is_not_published_as_a_complete_snapshot(tmp_path: Path, monkeypatch) -> None:
    path = _source(tmp_path)
    extract = repo_map._imports_and_symbols_for_path

    def change_after_parse(current: Path):
        result = extract(current)
        current.write_text("def bravo(): pass\n")
        return result

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", change_after_parse)
    result = repo_map.build_repo_map(tmp_path)
    assert not result["symbols"]
    assert result["partial"] is True
    assert result["symbol_cache"]["complete"] is False
    assert (
        "changed during symbol extraction" in result["symbol_cache_coverage"]["sample"][0]["reason"]
    )
    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", extract)
    assert _names(repo_map.build_repo_map(tmp_path)) == {"bravo"}
    assert path.exists()


def test_snapshot_reader_and_cache_key_preserve_same_mtime_and_crlf(tmp_path: Path) -> None:
    path = _source(tmp_path)
    with lang_registry.source_snapshot(path, b"def bravo():\r\n    return 2\r\n", "receipt"):
        assert lang_registry.read_source_text(path) == "def bravo():\n    return 2\n"
        assert {s["name"] for s in repo_map._python_imports_and_symbols(path)[1]} == {"bravo"}
    assert lang_registry.source_snapshot_digest(str(path)) is None


def test_exhausted_deadline_does_not_read_or_persist_cache(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    monkeypatch.setattr(
        symbols_cache, "_database", lambda *a, **k: pytest.fail("cache read after deadline")
    )
    result = repo_map.build_repo_map(tmp_path, deadline_monotonic=time.monotonic() - 1)
    assert result["partial"] is True
    assert result["symbol_cache"]["complete"] is False
    assert not (tmp_path / ".tg_cache").exists()


def test_linked_cache_parent_cannot_escape_checkout(tmp_path: Path) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _source(root)
    try:
        (root / ".tg_cache").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    result = repo_map.build_repo_map(root)
    assert _names(result) == {"alpha"}
    assert result["symbol_cache"]["status"].startswith("not-persisted")
    assert not list(outside.iterdir())


def test_leaf_source_link_refused_and_regular_source_allowed(tmp_path: Path) -> None:
    original = _source(tmp_path)
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(OSError, match="non-regular"):
        read_confined(tmp_path, link, 4096)
    assert read_confined(tmp_path, original, 4096).startswith(b"def alpha")


def test_transactions_publish_generation_merkle_and_current_membership(tmp_path: Path) -> None:
    path = _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    path.unlink()
    second = repo_map.build_repo_map(tmp_path)
    with closing(sqlite3.connect(first["symbol_cache"]["path"])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone() == (0,)
        merkle, complete = connection.execute(
            "SELECT merkle,complete FROM generations ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert merkle == second["symbol_cache"]["merkle_root"]
    assert complete == 1


def test_lock_budget_is_bounded_and_query_still_returns_fresh_results(tmp_path: Path) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    database = Path(first["symbol_cache"]["path"])
    with cache_lock(tmp_path, database, None):
        result = repo_map.build_repo_map(tmp_path)
    assert _names(result) == {"alpha"}
    assert "lock deadline exceeded" in result["symbol_cache"]["status"]
    assert "partial" not in result


def test_unexpected_sqlite_schema_is_rebuilt_without_executing_trigger(tmp_path: Path) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    with closing(sqlite3.connect(first["symbol_cache"]["path"])) as connection:
        connection.execute(
            "CREATE TRIGGER poison BEFORE INSERT ON entries BEGIN SELECT RAISE(ABORT,'poison'); END"
        )
        connection.commit()
    second = repo_map.build_repo_map(tmp_path)
    assert _names(second) == {"alpha"}
    assert "unexpected schema objects" in second["symbol_cache"]["status"]
    assert repo_map.build_repo_map(tmp_path)["symbol_cache"]["hits"] == 1


def test_cancelled_metadata_write_releases_lock(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    publish = symbols_cache.publish_confined

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(symbols_cache, "publish_confined", cancel)
    with pytest.raises(KeyboardInterrupt):
        repo_map.build_repo_map(tmp_path)
    monkeypatch.setattr(symbols_cache, "publish_confined", publish)
    assert _names(repo_map.build_repo_map(tmp_path)) == {"alpha"}
    with cache_lock(tmp_path, Path(first["symbol_cache"]["path"]), None):
        pass


def test_file_selection_places_cache_at_discovered_project_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="fixture"\n')
    path = _source(tmp_path, "src/pkg/a.py")
    result = repo_map.build_repo_map(path)
    assert _names(result) == {"alpha"}
    assert Path(result["symbol_cache"]["path"]).parent.parent.parent == tmp_path
    assert not (path.parent / ".tg_cache").exists()


def test_cache_entry_cap_preserves_fresh_uncached_output(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    monkeypatch.setattr(symbols_cache, "_ENTRY_LIMIT", 32)
    result = repo_map.build_repo_map(tmp_path)
    assert _names(result) == {"alpha"}
    assert "partial" not in result
    assert result["symbol_cache"]["status"] == "entry-too-large-not-persisted"


def test_machine_authentication_rejects_forged_parser_records_with_recomputed_checksum(
    tmp_path: Path,
) -> None:
    _source(tmp_path)
    first = repo_map.build_repo_map(tmp_path)
    with closing(sqlite3.connect(first["symbol_cache"]["path"])) as connection:
        key, raw = connection.execute("SELECT key,payload FROM entries").fetchone()
        product = json.loads(raw)
        product["symbols"][0]["name"] = "forged_target"
        forged = json.dumps(product, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "UPDATE entries SET payload=?,receipt=? WHERE key=?",
            (
                forged,
                hashlib.sha256(forged.encode()).hexdigest(),
                key,
            ),
        )
        connection.commit()
    rebuilt = repo_map.build_repo_map(tmp_path)
    assert _names(rebuilt) == {"alpha"}
    assert rebuilt["symbol_cache"]["hits"] == 0
    assert rebuilt["symbol_cache"]["status"] == "corrupt-entry-rebuilt"
    valid = repo_map.build_repo_map(tmp_path)
    assert valid["symbol_cache"]["hits"] == 1
    assert valid["symbol_cache"]["entry_authentication"] == "machine-hmac-sha256"


def test_unavailable_machine_key_never_reuses_unsigned_checkout_products(
    tmp_path: Path, monkeypatch
) -> None:
    _source(tmp_path)
    repo_map.build_repo_map(tmp_path)
    monkeypatch.setattr(symbols_cache, "load_machine_key", lambda root: None)
    uncached = repo_map.build_repo_map(tmp_path)
    assert _names(uncached) == {"alpha"}
    assert uncached["symbol_cache"]["hits"] == 0
    assert uncached["symbol_cache"]["entry_authentication"] == "unavailable"
    assert "machine signing key unavailable" in uncached["symbol_cache"]["status"]


@pytest.mark.parametrize("limit", [None, 2000])
def test_generated_symbol_cache_does_not_consume_repository_scan_quota(
    tmp_path: Path, limit: int | None
) -> None:
    _source(tmp_path)
    (tmp_path / "README.md").write_text("source fixture", encoding="utf-8")
    before = repo_map._iter_repo_files(tmp_path, max_files=limit)
    assert len(before) == 2
    cold = repo_map.build_repo_map(tmp_path, max_repo_files=limit)
    assert (tmp_path / ".tg_cache" / "symbols_v1" / "metadata.sqlite3").is_file()
    assert repo_map._iter_repo_files(tmp_path, max_files=limit) == before
    _source(tmp_path, "nested/.tg_cache/fake.py", "generated_cache_symbol")
    _source(tmp_path, "nested/untracked.py", "untracked_source")
    warm = repo_map.build_repo_map(tmp_path, max_repo_files=limit)
    assert _names(warm) == {"alpha", "untracked_source"}
    assert len(repo_map._iter_repo_files(tmp_path, max_files=limit)) == 3
    if limit is not None:
        assert cold["scan_limit"]["scanned_files"] == 2
        assert warm["scan_limit"]["scanned_files"] == 3
