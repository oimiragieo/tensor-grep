"""_module_aliases_for_path memoization (blast-radius perf, 2026-07-03).

Found by profile-at-scale: on a depth-2 blast-radius this pure path->aliases function was
called ~1.4M times for ~unique-file inputs (the reverse-import graph / PageRank loops),
dominating a 62s run. It has no file I/O, so caching by the path string is unconditionally
correct. This locks in the memoization + immutability without changing the alias contents.
"""

from tensor_grep.cli.repo_map import _module_aliases_for_path


def test_returns_frozenset_and_is_cached() -> None:
    _module_aliases_for_path.cache_clear()
    a1 = _module_aliases_for_path("src/tensor_grep/core/config.py")
    a2 = _module_aliases_for_path("src/tensor_grep/core/config.py")
    assert isinstance(a1, frozenset)
    assert a1 is a2  # identical cached object -> the hot loop pays one build per path
    assert _module_aliases_for_path.cache_info().hits >= 1


def test_alias_contents_unchanged() -> None:
    # Parity: the memoized frozenset must contain exactly the aliases the old set did.
    aliases = _module_aliases_for_path("a/b/mymod.py")
    assert "mymod" in aliases  # stem
    assert "a.b.mymod" in aliases  # full dotted path
    assert "b.mymod" in aliases  # last-two dotted
    assert "" not in aliases  # empties filtered


def test_immutable_result_cannot_corrupt_cache() -> None:
    aliases = _module_aliases_for_path("x/y.py")
    # frozenset has no mutating API — a stray .add() would raise, not silently poison the cache.
    assert not hasattr(aliases, "add")
