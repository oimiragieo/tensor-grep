"""Fix A: mtime-keyed source/parse cache for the caller_scan hot path.

Profiled on a real repo: build_symbol_callers spends ~90% of wall time in caller_scan, which
re-reads (and, for _file_imports_symbol_from_definition, re-parses) the SAME candidate file
multiple times per call -- once in _file_may_contain_literal_symbol, again in
_file_may_import_symbol_definition, and once per (file, definition) pair in
_file_imports_symbol_from_definition's any() loop over definition_files.

This test suite exercises the 4 guards from the fix spec:
  - Guard 1: _file_imports_symbol_from_definition takes a str first-positional arg and is
    decorated with @_mtime_aware_cache.
  - Guard 2: _file_may_import_symbol_definition is NOT decorated directly (its definition_files
    arg is an unhashable list) -- only the underlying byte-read is shared via
    _read_source_cached.
  - Guard 3: a warm daemon must not serve a stale cached parse/read after a session refresh --
    _clear_all_source_caches() sweeps every _mtime_aware_cache-decorated cache, and
    session_store.refresh_session invokes it.
  - Guard 4: the shared byte-read cache is bounded (maxsize + a byte cap mirroring
    _SYMBOL_LITERAL_SEED_MAX_BYTES) so one giant file cannot dominate cache memory.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import repo_map, session_store


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _build_repo(root: Path) -> dict[str, Path]:
    """A tiny repo with one symbol definition and several importers/non-importers."""
    core = _write(
        root / "src" / "core.py",
        "def create_invoice(total):\n    return total + 1\n",
    )
    caller_a = _write(
        root / "src" / "caller_a.py",
        "from src.core import create_invoice\n\n"
        "def use_a(total):\n"
        "    return create_invoice(total)\n",
    )
    caller_b = _write(
        root / "src" / "caller_b.py",
        "from src.core import create_invoice\n\n"
        "def use_b(total):\n"
        "    return create_invoice(total)\n",
    )
    non_caller = _write(
        root / "src" / "unrelated.py",
        "def format_label(item_id):\n    return f'item-{item_id}'\n",
    )
    return {
        "root": root,
        "core": core,
        "caller_a": caller_a,
        "caller_b": caller_b,
        "unrelated": non_caller,
    }


def _caller_files(result: dict[str, object]) -> set[str]:
    callers = result.get("callers")
    assert isinstance(callers, list)
    return {str(entry["file"]).replace("\\", "/") for entry in callers}  # type: ignore[index]


# ---------------------------------------------------------------------------
# Speed/behavior: repeat calls are identical, and the shared read cache is actually hit.
# ---------------------------------------------------------------------------


def test_repeated_build_symbol_callers_returns_identical_results(tmp_path: Path) -> None:
    paths = _build_repo(tmp_path)
    root = paths["root"]

    first = repo_map.build_symbol_callers("create_invoice", str(root))
    second = repo_map.build_symbol_callers("create_invoice", str(root))

    assert first["callers"] == second["callers"]
    caller_files = _caller_files(first)
    assert any(name.endswith("src/caller_a.py") for name in caller_files)
    assert any(name.endswith("src/caller_b.py") for name in caller_files)
    assert not any(name.endswith("src/unrelated.py") for name in caller_files)


def test_caller_scan_reads_each_unchanged_candidate_file_at_most_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Before the fix: _file_may_contain_literal_symbol + _file_may_import_symbol_definition
    each called path.read_bytes() independently -- 2 reads per candidate PER call. With the
    shared _read_source_cached helper, an unchanged file is read once total, no matter how many
    helpers ask for its bytes or how many times build_symbol_callers is called."""
    paths = _build_repo(tmp_path)
    root = paths["root"]

    original_read_bytes = Path.read_bytes
    read_counts: dict[str, int] = {}

    def counting_read_bytes(self: Path) -> bytes:
        key = str(self).replace("\\", "/")
        read_counts[key] = read_counts.get(key, 0) + 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    repo_map.build_symbol_callers("create_invoice", str(root))
    repo_map.build_symbol_callers("create_invoice", str(root))

    caller_a_reads = sum(v for k, v in read_counts.items() if k.endswith("src/caller_a.py"))
    caller_b_reads = sum(v for k, v in read_counts.items() if k.endswith("src/caller_b.py"))
    # Exactly one physical read_bytes() across BOTH candidate helpers and BOTH top-level calls.
    assert caller_a_reads <= 1, f"expected <=1 read_bytes() for caller_a.py, saw {caller_a_reads}"
    assert caller_b_reads <= 1, f"expected <=1 read_bytes() for caller_b.py, saw {caller_b_reads}"


def test_file_imports_symbol_from_definition_is_cached(tmp_path: Path, monkeypatch) -> None:
    paths = _build_repo(tmp_path)
    assert hasattr(repo_map._file_imports_symbol_from_definition, "cache_clear")

    original_read_text = Path.read_text
    read_calls = {"n": 0}

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if str(self).replace("\\", "/").endswith("src/caller_a.py"):
            read_calls["n"] += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    root = str(paths["root"])
    core = str(paths["core"])
    args = (str(paths["caller_a"]), "create_invoice", core, root)

    first = repo_map._file_imports_symbol_from_definition(*args)
    second = repo_map._file_imports_symbol_from_definition(*args)

    assert first is True
    assert first == second
    assert read_calls["n"] == 1, "second call must hit the cache, not re-read+re-parse the file"


# ---------------------------------------------------------------------------
# Staleness/correctness
# ---------------------------------------------------------------------------


def test_build_symbol_callers_reflects_edit_that_changes_file_size(tmp_path: Path) -> None:
    """Normal case: editing caller_b.py to drop the import changes its byte size, which changes
    the mtime-aware cache key automatically -- no explicit cache_clear() needed."""
    paths = _build_repo(tmp_path)
    root = paths["root"]

    before = repo_map.build_symbol_callers("create_invoice", str(root))
    before_callers = _caller_files(before)
    assert any(name.endswith("src/caller_b.py") for name in before_callers)

    # Drop the import entirely -- different byte length, so (mtime_ns, size) changes too.
    paths["caller_b"].write_text(
        "def use_b(total):\n    return total\n",
        encoding="utf-8",
    )

    after = repo_map.build_symbol_callers("create_invoice", str(root))
    after_callers = _caller_files(after)
    assert not any(name.endswith("src/caller_b.py") for name in after_callers), (
        "stale cache served pre-edit content: caller_b.py no longer imports create_invoice"
    )
    assert any(name.endswith("src/caller_a.py") for name in after_callers)


def test_clear_all_source_caches_sweeps_a_frozen_mtime_key(tmp_path: Path, monkeypatch) -> None:
    """Guard 3, mechanism-level: freeze the mtime key (simulating the pathological
    same-(mtime_ns,size) edit a warm daemon can hit in practice) and prove that ONLY
    _clear_all_source_caches() recovers fresh content -- the mtime key by itself cannot."""
    target = tmp_path / "mod.py"
    target.write_bytes(b"import os\n")  # write_bytes: no newline translation on Windows

    frozen_key = (123456789, 999)
    monkeypatch.setattr(repo_map, "_mtime_key", lambda path_str: frozen_key)

    primed = repo_map._read_source_cached(str(target))
    assert primed == b"import os\n"

    # Same byte length, frozen mtime key: the pathological case.
    target.write_bytes(b"import sys\n")
    still_stale = repo_map._read_source_cached(str(target))
    assert still_stale == primed, "sanity check: the frozen key must serve the stale cached bytes"

    repo_map._clear_all_source_caches()
    fresh = repo_map._read_source_cached(str(target))
    assert fresh == b"import sys\n", "_clear_all_source_caches() must sweep the stale entry"


def test_refresh_session_invokes_clear_all_source_caches(tmp_path: Path, monkeypatch) -> None:
    """Guard 3, wiring-level: session_store.refresh_session (the single choke point behind the
    explicit `tg session refresh` command AND the daemon's refresh_on_stale recovery path) must
    call _clear_all_source_caches() so a warm daemon never serves pre-refresh cached content."""
    project = tmp_path / "project"
    _write(project / "src" / "a.py", "def a():\n    return 1\n")
    session_id = session_store.open_session(str(project)).session_id

    calls = {"n": 0}
    original = session_store._clear_all_source_caches

    def counting_clear() -> None:
        calls["n"] += 1
        original()

    monkeypatch.setattr(session_store, "_clear_all_source_caches", counting_clear)

    session_store.refresh_session(session_id, str(project))

    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Guard-2 regression: _file_may_import_symbol_definition is NOT decorated (unhashable list arg).
# ---------------------------------------------------------------------------


def test_file_may_import_symbol_definition_not_decorated_accepts_list_arg(tmp_path: Path) -> None:
    paths = _build_repo(tmp_path)
    assert not hasattr(repo_map._file_may_import_symbol_definition, "cache_clear"), (
        "Guard 2: this function's 2nd arg is an unhashable list[str] -- it must NOT be "
        "decorated with @_mtime_aware_cache, or every call would raise TypeError."
    )
    # A plain list argument must work without raising.
    result = repo_map._file_may_import_symbol_definition(paths["caller_a"], [str(paths["core"])])
    assert isinstance(result, bool)
    assert result is True

    no_match = repo_map._file_may_import_symbol_definition(paths["unrelated"], [str(paths["core"])])
    assert isinstance(no_match, bool)


# ---------------------------------------------------------------------------
# Guard 4: byte cap bypasses the cache for oversized files, and stays correct regardless.
# ---------------------------------------------------------------------------


def test_read_source_cached_bypasses_cache_above_byte_cap(tmp_path: Path, monkeypatch) -> None:
    big = tmp_path / "big.py"
    content = b"x" * 4096
    big.write_bytes(content)
    # Force the "too large to cache" branch without needing an actual multi-MB fixture.
    monkeypatch.setattr(repo_map, "_SYMBOL_LITERAL_SEED_MAX_BYTES", 100)

    original_read_bytes = Path.read_bytes
    calls = {"n": 0}

    def counting_read_bytes(self: Path) -> bytes:
        if self == big:
            calls["n"] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    first = repo_map._read_source_cached(str(big))
    second = repo_map._read_source_cached(str(big))

    assert first == content
    assert second == content
    # Bypassed the cache both times -- a giant file must never sit in the cache.
    assert calls["n"] == 2


def test_read_source_cached_under_byte_cap_is_cached(tmp_path: Path, monkeypatch) -> None:
    small = tmp_path / "small.py"
    content = b"y" * 32
    small.write_bytes(content)

    original_read_bytes = Path.read_bytes
    calls = {"n": 0}

    def counting_read_bytes(self: Path) -> bytes:
        if self == small:
            calls["n"] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    first = repo_map._read_source_cached(str(small))
    second = repo_map._read_source_cached(str(small))

    assert first == content
    assert second == content
    assert calls["n"] == 1, "an unchanged, under-cap file must be read once and served from cache"
