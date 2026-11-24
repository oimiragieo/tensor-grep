"""Tests for content-hashed incremental symbol cache and Python package-relative import resolution."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import repo_map
from tensor_grep.cli.incremental_symbol_cache import (
    IncrementalSymbolCache,
    resolve_python_import_target,
)


def test_content_hash_cache_hit_and_invalidation_on_edit(tmp_path: Path) -> None:
    target = tmp_path / "mod.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")

    cache_file = tmp_path / ".cache" / "symbols.json"
    cache = IncrementalSymbolCache(cache_path=cache_file)

    entry1, hit1 = cache.get_file_entry(target)
    assert hit1 is False
    assert cache.misses == 1
    assert [s["name"] for s in entry1.symbols] == ["alpha"]

    # Unchanged content -> cache hit
    entry2, hit2 = cache.get_file_entry(target)
    assert hit2 is True
    assert cache.hits == 1
    assert entry2.sha256 == entry1.sha256

    # Persist to disk and reload in a fresh cache instance -> still a hit
    cache.save()
    reloaded = IncrementalSymbolCache(cache_path=cache_file)
    entry3, hit3 = reloaded.get_file_entry(target)
    assert hit3 is True
    assert entry3.sha256 == entry1.sha256

    # Edit file content -> SHA-256 changes -> cache invalidates and recomputes
    target.write_text("def beta():\n    return 2\n", encoding="utf-8")
    entry4, hit4 = reloaded.get_file_entry(target)
    assert hit4 is False
    assert entry4.sha256 != entry1.sha256
    assert [s["name"] for s in entry4.symbols] == ["beta"]


def test_resolve_python_import_target_intra_package_and_relative(tmp_path: Path) -> None:
    pkg_cli = tmp_path / "src" / "tensor_grep" / "cli"
    pkg_cli.mkdir(parents=True)
    (tmp_path / "src" / "tensor_grep" / "__init__.py").write_text("", encoding="utf-8")
    (pkg_cli / "__init__.py").write_text("", encoding="utf-8")
    detail_tier = pkg_cli / "detail_tier.py"
    detail_tier.write_text("def apply_detail_tier():\n    pass\n", encoding="utf-8")
    caller = pkg_cli / "consumer.py"
    caller.write_text(
        "from tensor_grep.cli import detail_tier\nfrom . import detail_tier\n",
        encoding="utf-8",
    )

    # Intra-package `from tensor_grep.cli import detail_tier`
    abs_target = resolve_python_import_target(
        caller,
        "tensor_grep.cli",
        tmp_path,
        level=0,
        imported_names=["detail_tier"],
    )
    assert abs_target == detail_tier.resolve()

    # Relative `from . import detail_tier`
    rel_target = resolve_python_import_target(
        caller,
        "detail_tier",
        tmp_path,
        level=1,
    )
    assert rel_target == detail_tier.resolve()

    # Verify repo_map._resolve_raw_import_entry resolves cleanly without external/unresolved gaps
    resolved_abs = repo_map._resolve_raw_import_entry(
        caller,
        {"module": "tensor_grep.cli", "line": 1, "level": 0, "names": ["detail_tier"]},
        tmp_path,
        "python",
    )
    assert resolved_abs["resolved"] == str(detail_tier.resolve())
    assert resolved_abs["external"] is False

    resolved_rel = repo_map._resolve_raw_import_entry(
        caller,
        {"module": "detail_tier", "line": 2, "level": 1},
        tmp_path,
        "python",
    )
    assert resolved_rel["resolved"] == str(detail_tier.resolve())
    assert resolved_rel["external"] is False

    # Verify build_file_imports produces zero resolution_gaps for the relative import
    imports_payload = repo_map.build_file_imports(caller)
    assert imports_payload["unresolved"] == []
    assert imports_payload.get("resolution_gaps", []) == []
