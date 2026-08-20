"""Path-keyed caching primitives shared by `repo_map` and its extracted sibling modules.

WHY THIS IS ITS OWN MODULE
--------------------------
`_mtime_aware_cache` is applied as a DECORATOR, i.e. it is evaluated while the module that
uses it is being imported. The split of `repo_map.py` (docs/design/2026-08-19-split-floor-
escape.md) moves decorated functions into sibling modules that `repo_map` imports at its top,
so those siblings cannot reach back into `repo_map` for the decorator -- `repo_map` is only
part-way through its own body at that moment and the name does not exist yet. Hoisting the
decorator into a leaf module both `repo_map` and its siblings import breaks that cycle without
any import-order subtlety.

`_mtime_key` deliberately stays in `repo_map`: the test suite monkeypatches it there, so the
wrapper reads it late through `_self` (see the Route A note below) rather than binding it at
import time.
"""

from __future__ import annotations

import importlib
import sys
import threading
from collections.abc import Callable
from functools import lru_cache, wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

# Route A late binding, as in repo_map.py -- but pointed at `repo_map` rather than at this
# module, because that is where the test suite patches. A plain import would be circular
# (repo_map imports this module), so the runtime branch resolves through sys.modules on every
# attribute read; the TYPE_CHECKING branch never executes and exists only so mypy keeps the
# real signatures instead of collapsing everything to Any.
if TYPE_CHECKING:
    from tensor_grep.cli import repo_map as _self
else:

    class _RepoMapProxy:
        """Late-binding view of `tensor_grep.cli.repo_map`."""

        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            module = sys.modules.get("tensor_grep.cli.repo_map")
            if module is None:
                module = importlib.import_module("tensor_grep.cli.repo_map")
            return getattr(module, name)

    _self = _RepoMapProxy()

_CacheR = TypeVar("_CacheR")

# Entry cap for the shared mtime-aware source-read cache. It lives here rather than in
# repo_map because it is used as a DECORATOR ARGUMENT by extracted sibling modules, i.e. it
# is read while repo_map is still part-way through its own body and could not yet supply it.
_SOURCE_READ_CACHE_MAXSIZE = 4096


# Fix A / Guard 3: every _mtime_aware_cache-decorated function registers its cache_clear
# here so a warm daemon can sweep ALL of them in one call when a session is refreshed/detected
# stale. Without this, a same-(mtime_ns,size) edit landing between two daemon calls could keep
# serving a stale cached parse/read forever (the mtime key alone can't tell them apart).
_MTIME_CACHE_CLEAR_REGISTRY: list[Callable[[], None]] = []


# Fix B: the JS/TS import-resolution path (_js_ts_module_candidates / _js_ts_candidate_files /
# _js_ts_resolve_exported_symbol / _js_ts_import_match_details / _normalized_repo_root) calls
# Path.resolve() on the SAME handful of path strings thousands of times per caller_scan --
# profiled at ~27,669 resolve() calls / ~83,114 nt._getfinalpathname syscalls on a real repo
# (~18s of ~22s wall time), because caller_scan re-derives candidate module paths for every
# (candidate file, definition file) pair even when the underlying importer path / repo root /
# module name repeats across pairs. Path.resolve() is a pure function of the path string for the
# lifetime of a single resolution (no dependency on the target FILE's mtime -- it's a syscall
# that walks the filesystem to canonicalize the string), so memoize it directly by string.
#
# Guard 3 (daemon safety): this is a PLAIN lru_cache, not _mtime_aware_cache -- there's no single
# file whose mtime this could key off (the input is a path STRING, not a file whose bytes we're
# reading), and a moved file / retargeted symlink mid-session could change what a given string
# resolves to. Register its cache_clear in the same _MTIME_CACHE_CLEAR_REGISTRY sweep the parse
# cache uses so a daemon session refresh/detected-staleness flushes it too.
@lru_cache(maxsize=8192)
def _resolved_path_str(path_str: str) -> str:
    return str(Path(path_str).resolve())


_MTIME_CACHE_CLEAR_REGISTRY.append(_resolved_path_str.cache_clear)


def _mtime_aware_cache(
    maxsize: int = 256,
) -> Callable[[Callable[..., _CacheR]], Callable[..., _CacheR]]:
    """Decorator: like @lru_cache but includes file mtime+size in the key.

    The decorated function must take the file path (str) as its first
    positional argument.  All remaining arguments must be hashable. Generic over the
    return type so decorated functions keep their precise signature for type-checking.
    """

    def decorator(fn: Callable[..., _CacheR]) -> Callable[..., _CacheR]:
        cache: dict[tuple[Any, ...], _CacheR] = {}
        lock = threading.Lock()

        @wraps(fn)
        def wrapper(path_str: str, /, *args: Any, **kwargs: Any) -> _CacheR:
            mtime_key = _self._mtime_key(path_str)
            cache_key = (path_str, mtime_key, args, tuple(sorted(kwargs.items())))
            with lock:
                if cache_key in cache:
                    return cache[cache_key]
            result = fn(path_str, *args, **kwargs)
            with lock:
                # Evict oldest entry when the cache is full.
                if len(cache) >= maxsize:
                    try:
                        oldest = next(iter(cache))
                        del cache[oldest]
                    except StopIteration:
                        pass
                cache[cache_key] = result
            return result

        def cache_clear() -> None:
            with lock:
                cache.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        _MTIME_CACHE_CLEAR_REGISTRY.append(cache_clear)
        return cast("Callable[..., _CacheR]", wrapper)

    return decorator
