"""Tests for B7 (mtime-aware cache) and O2 (byte-cap) fixes in repo_map.

These tests import only light modules (pathlib, time, tempfile) and the
repo_map module itself.  They do NOT require the compiled rust_core extension
because they exercise pure-Python helpers only.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.repo_map import (
    _max_parse_bytes,
    _mtime_aware_cache,
    _mtime_key,
    _python_test_function_candidates,
)

# ---------------------------------------------------------------------------
# B7: _mtime_aware_cache returns fresh content after a file's mtime changes
# ---------------------------------------------------------------------------


def test_mtime_key_returns_size_and_ns(tmp_path: Path) -> None:
    """_mtime_key returns a (mtime_ns, size) tuple for an existing file."""
    p = tmp_path / "f.txt"
    p.write_text("hello")
    key = _mtime_key(str(p))
    assert isinstance(key, tuple)
    assert len(key) == 2
    mtime_ns, size = key
    assert mtime_ns > 0
    assert size == 5


def test_mtime_key_missing_file_returns_sentinel() -> None:
    """-1, -1 is returned for a file that does not exist."""
    key = _mtime_key("/nonexistent/file/does_not_exist.txt")
    assert key == (-1, -1)


def test_mtime_aware_cache_returns_cached_result_for_unchanged_file(tmp_path: Path) -> None:
    """Consecutive calls for the same path+mtime hit the cache (no re-read)."""
    call_count = 0

    @_mtime_aware_cache(maxsize=8)
    def _read_upper(path_str: str) -> str:
        nonlocal call_count
        call_count += 1
        return Path(path_str).read_text()

    p = tmp_path / "sample.txt"
    p.write_text("initial")

    result1 = _read_upper(str(p))
    result2 = _read_upper(str(p))
    assert result1 == result2 == "initial"
    # Second call must hit the cache
    assert call_count == 1


def test_mtime_aware_cache_sees_new_content_after_file_edit(tmp_path: Path) -> None:
    """The core B7 regression: cache must NOT return stale content after a write.

    This test writes a file, calls a cached reader, rewrites the file with
    different content, and asserts the reader returns the new content.
    """
    call_count = 0

    @_mtime_aware_cache(maxsize=8)
    def _read_file(path_str: str) -> str:
        nonlocal call_count
        call_count += 1
        return Path(path_str).read_text()

    p = tmp_path / "evolving.txt"
    p.write_text("version-1")

    first = _read_file(str(p))
    assert first == "version-1"
    assert call_count == 1

    # Ensure mtime advances (some filesystems have 1-second granularity).
    # We use os.utime to force-bump the timestamp rather than sleeping.
    import os

    old_stat = p.stat()
    p.write_text("version-2")
    # Force mtime ahead by 2 seconds to guarantee a different mtime_ns value
    # even on filesystems with coarse-grained timestamps.
    new_mtime = old_stat.st_mtime + 2.0
    os.utime(str(p), (new_mtime, new_mtime))

    second = _read_file(str(p))
    assert second == "version-2", "Cache returned stale content after file edit — B7 regression."
    assert call_count == 2, "Expected a fresh read after mtime changed."


def test_mtime_aware_cache_evicts_oldest_when_full(tmp_path: Path) -> None:
    """When the cache reaches maxsize it evicts the oldest entry."""
    maxsize = 3

    @_mtime_aware_cache(maxsize=maxsize)
    def _read_name(path_str: str) -> str:
        return Path(path_str).read_text()

    paths = []
    for i in range(maxsize + 1):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"content-{i}")
        paths.append(p)

    # Fill and overflow the cache
    for p in paths:
        _read_name(str(p))

    # All reads should have returned the correct value regardless of eviction
    for p in paths:
        assert _read_name(str(p)) == p.read_text()


def test_mtime_aware_cache_with_extra_args(tmp_path: Path) -> None:
    """Extra args beyond the path are included in the cache key."""
    call_count = 0

    @_mtime_aware_cache(maxsize=8)
    def _prefixed(path_str: str, prefix: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"{prefix}:{Path(path_str).read_text()}"

    p = tmp_path / "data.txt"
    p.write_text("x")

    r1 = _prefixed(str(p), "A")
    r2 = _prefixed(str(p), "B")
    r3 = _prefixed(str(p), "A")  # cache hit

    assert r1 == "A:x"
    assert r2 == "B:x"
    assert r3 == "A:x"
    assert call_count == 2  # "A" + "B"; second "A" is a cache hit


def test_python_test_function_candidates_refreshes_after_edit(tmp_path: Path) -> None:
    """_python_test_function_candidates (a real B7-patched function) sees new names."""
    import os

    p = tmp_path / "test_example.py"
    p.write_text("def test_alpha(): pass\n")

    first = _python_test_function_candidates(str(p))
    assert "test_alpha" in first

    old_stat = p.stat()
    p.write_text("def test_beta(): pass\n")
    new_mtime = old_stat.st_mtime + 2.0
    os.utime(str(p), (new_mtime, new_mtime))

    second = _python_test_function_candidates(str(p))
    assert "test_beta" in second, "Stale cache — B7 regression in _python_test_function_candidates"
    assert "test_alpha" not in second


# ---------------------------------------------------------------------------
# O2: byte-cap skips oversized files in _imports_and_symbols_for_path
# ---------------------------------------------------------------------------


def test_max_parse_bytes_default_is_positive() -> None:
    """_max_parse_bytes() returns a positive integer when no env var is set."""
    cap = _max_parse_bytes()
    assert isinstance(cap, int)
    assert cap > 0


def test_max_parse_bytes_env_override(monkeypatch: object) -> None:
    """TENSOR_GREP_MAX_PARSE_BYTES env var overrides the default cap."""

    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "1234567")  # type: ignore[attr-defined]
    # Re-import to pick up env — _max_parse_bytes() reads os.environ at call time
    from tensor_grep.cli.repo_map import _max_parse_bytes as _mbp

    assert _mbp() == 1_234_567


def test_imports_and_symbols_skips_oversized_file(tmp_path: Path, monkeypatch: object) -> None:
    """O2: _imports_and_symbols_for_path returns empty lists for files exceeding the cap."""

    from tensor_grep.cli.repo_map import _imports_and_symbols_for_path

    # Set cap to 10 bytes so the real test file is always "oversized"
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "10")  # type: ignore[attr-defined]

    p = tmp_path / "huge.py"
    # Write 100 bytes — well above the 10-byte cap
    p.write_text("import os\n" * 10)

    imports, symbols = _imports_and_symbols_for_path(p)
    assert imports == [], "O2: oversized file should return empty imports"
    assert symbols == [], "O2: oversized file should return empty symbols"


def test_imports_and_symbols_parses_small_file(tmp_path: Path, monkeypatch: object) -> None:
    """O2: files under the byte cap are parsed normally."""
    from tensor_grep.cli.repo_map import _imports_and_symbols_for_path

    # Use the default cap (2 MB) — a tiny file is always below it
    p = tmp_path / "small.py"
    p.write_text("import os\n\ndef my_func(): pass\n")

    imports, symbols = _imports_and_symbols_for_path(p)
    assert "os" in imports
    assert any(s["name"] == "my_func" for s in symbols)
