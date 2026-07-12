"""The content-addressed AST parse cache (`_cached_ast_parse`) collapses the 2-3x duplicate Python
file parses `build_agent_capsule` does across phases -- the map-build imports/symbols pass and the
caller/blast-radius consumer scan both `ast.parse` the SAME source. Profile 2026-07-11: ~40% of
`tg agent` wall was `ast.parse` + `ast.walk` over those duplicate parses (1512 parses / ~783 files).
Keying on source TEXT (not path) keeps it staleness-free under the reused session-daemon process.

The cache is bounded by TOTAL CACHED SOURCE BYTES (a byte budget), not entry count -- an
entry-count cap (the original `@lru_cache(maxsize=2048)`) does not bound resident memory, since
2048 large-file ASTs is unbounded growth in a long-lived daemon (external-audit finding: an
OOM/DoS vector). The tests below the first block cover that memory-bounded redesign: content-key
dedup across distinct paths, staleness-safety on a same-path edit, LRU eviction once the budget is
exceeded, the per-entry-ceiling bypass (parse-but-don't-store, never a skip), and thread safety
under concurrent daemon-style access.
"""

import ast
import threading
from pathlib import Path

import pytest

from tensor_grep.cli.repo_map import _cached_ast_parse, _python_imports_and_symbols


def test_identical_source_is_parsed_once_and_shared() -> None:
    _cached_ast_parse.cache_clear()
    src = "import os\nfrom a.b import c\n\n\ndef f():\n    return c\n"
    first = _cached_ast_parse(src)
    second = _cached_ast_parse(src)
    assert first is second  # same tree object -> parsed once, shared read-only
    info = _cached_ast_parse.cache_info()
    assert info.misses == 1 and info.hits == 1


def test_cached_parse_is_byte_identical_to_ast_parse() -> None:
    _cached_ast_parse.cache_clear()
    src = "import os\nfrom a.b import c as d\n\n\nclass K:\n    def m(self):\n        return 1\n"
    assert ast.dump(_cached_ast_parse(src)) == ast.dump(ast.parse(src))


def test_distinct_sources_get_distinct_trees() -> None:
    _cached_ast_parse.cache_clear()
    assert _cached_ast_parse("import os\n") is not _cached_ast_parse("import sys\n")


def test_reparsing_the_same_unchanged_file_hits_the_cache(tmp_path: Path) -> None:
    # The whole point: the map-build pass and the caller/blast scan both parse the same file. A
    # second extraction of identical content must be a cache HIT (no new miss), not a re-parse.
    _cached_ast_parse.cache_clear()
    module = tmp_path / "m.py"
    module.write_text(
        "import os\nfrom pkg import thing\n\n\ndef go():\n    return thing\n", encoding="utf-8"
    )
    _python_imports_and_symbols(module)
    misses_after_first = _cached_ast_parse.cache_info().misses
    _python_imports_and_symbols(module)  # same source read again
    info = _cached_ast_parse.cache_info()
    assert info.misses == misses_after_first  # NOT re-parsed
    assert info.hits >= 1


# ---------------------------------------------------------------------------
# Memory-bounded (byte-budget) cache redesign -- content-key dedup, staleness-safety,
# byte-budget eviction, per-entry-ceiling bypass, and concurrency.
# ---------------------------------------------------------------------------


def test_two_paths_identical_source_dedupe_to_one_entry(tmp_path: Path) -> None:
    """Content-key dedup: two DIFFERENT paths with identical source content collapse to a
    single cache entry / a single parse -- the cache key is the source text, never the path."""
    _cached_ast_parse.cache_clear()
    src = "import os\n\n\ndef shared():\n    return os\n"
    path_a = tmp_path / "a.py"
    path_b = tmp_path / "b.py"
    path_a.write_text(src, encoding="utf-8")
    path_b.write_text(src, encoding="utf-8")

    _python_imports_and_symbols(path_a)
    misses_after_a = _cached_ast_parse.cache_info().misses
    _python_imports_and_symbols(path_b)
    info = _cached_ast_parse.cache_info()

    assert info.misses == misses_after_a, "identical content from a second path was re-parsed"
    assert info.hits >= 1
    assert info.current_entries == 1


def test_same_path_changed_source_is_not_stale(tmp_path: Path) -> None:
    """Staleness-safety: the SAME path, edited to different content, must re-parse -- content
    (not path) is the key, so an edit always produces a different key. This is the #535
    daemon-staleness regression this cache exists to never reintroduce."""
    _cached_ast_parse.cache_clear()
    p = tmp_path / "evolving.py"
    p.write_text("def alpha():\n    pass\n", encoding="utf-8")
    _, symbols_v1 = _python_imports_and_symbols(p)
    assert any(s["name"] == "alpha" for s in symbols_v1)

    p.write_text("def beta():\n    pass\n", encoding="utf-8")
    _, symbols_v2 = _python_imports_and_symbols(p)

    assert any(s["name"] == "beta" for s in symbols_v2)
    assert not any(s["name"] == "alpha" for s in symbols_v2), (
        "stale AST served after a same-path content edit"
    )
    info = _cached_ast_parse.cache_info()
    assert info.misses == 2  # two distinct contents -> two distinct parses
    assert info.current_entries == 2


def test_byte_budget_evicts_oldest_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Byte-budget eviction: once the configured budget is exceeded, the least-recently-used
    entry is evicted first, and total resident bytes never exceed the budget."""
    _cached_ast_parse.cache_clear()
    sources = [f"x_{i} = {i}\n" * 3 for i in range(6)]
    entry_size = len(sources[0].encode("utf-8"))
    budget = entry_size * 3  # fits about half of the 6 sources -> forces eviction
    monkeypatch.setenv("TENSOR_GREP_AST_CACHE_BYTES", str(budget))

    for src in sources:
        _cached_ast_parse(src)

    info = _cached_ast_parse.cache_info()
    assert info.current_bytes <= budget
    assert info.evictions > 0

    # The oldest (first-inserted) source was never touched again -> evicted -> fresh MISS.
    misses_before = _cached_ast_parse.cache_info().misses
    _cached_ast_parse(sources[0])
    assert _cached_ast_parse.cache_info().misses == misses_before + 1

    # The most-recently-inserted source is still resident -> HIT, no new miss.
    misses_before = _cached_ast_parse.cache_info().misses
    hits_before = _cached_ast_parse.cache_info().hits
    _cached_ast_parse(sources[-1])
    info_after = _cached_ast_parse.cache_info()
    assert info_after.misses == misses_before
    assert info_after.hits == hits_before + 1


def test_oversized_source_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-entry ceiling: a source over the configured cap must BYPASS the cache (never stored)
    but still return a real, correctly-parsed ast.Module -- bypass must never degrade into a
    skip, since several call sites have no size guard of their own and would silently lose
    symbols/imports/callers if this returned anything less than a fully parsed tree."""
    _cached_ast_parse.cache_clear()
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "10")  # tiny cap -> everything is "huge"

    src = "def f():\n    return 42\n"
    tree = _cached_ast_parse(src)

    assert isinstance(tree, ast.Module)
    assert ast.dump(tree) == ast.dump(ast.parse(src))
    assert _cached_ast_parse.cache_info().current_entries == 0  # never stored

    # Calling again re-parses (a second MISS, not a HIT) -- proof it truly bypassed storage.
    misses_before = _cached_ast_parse.cache_info().misses
    _cached_ast_parse(src)
    assert _cached_ast_parse.cache_info().misses == misses_before + 1


def test_concurrent_parses_are_thread_safe() -> None:
    """Concurrency smoke: many threads hammering the cache with a mix of shared and per-thread
    unique sources must not crash or corrupt state, and every call must land as exactly one hit
    or one miss (no double-counting from the tolerated benign double-parse race)."""
    _cached_ast_parse.cache_clear()
    shared_sources = [f"def shared_{i}():\n    return {i}\n" for i in range(4)]
    calls_per_thread = 20
    thread_count = 16
    errors: list[Exception] = []

    def worker(thread_id: int) -> None:
        try:
            for i in range(calls_per_thread):
                # Alternate a small shared pool (drives hits after warmup) with a thread-unique
                # source (drives misses), exercising both cache paths concurrently.
                if i % 2 == 0:
                    src = shared_sources[i % len(shared_sources)]
                else:
                    src = f"def unique_{thread_id}_{i}():\n    return {thread_id}\n"
                tree = _cached_ast_parse(src)
                assert isinstance(tree, ast.Module)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker thread hung"
    assert not errors, f"worker thread(s) raised: {errors}"

    info = _cached_ast_parse.cache_info()
    assert info.hits + info.misses == thread_count * calls_per_thread
    assert info.evictions == 0  # tiny sources, default 64 MiB budget -> nothing evicted
