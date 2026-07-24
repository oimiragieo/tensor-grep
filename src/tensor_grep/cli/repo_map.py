from __future__ import annotations

import ast
import atexit
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import tomllib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypeVar, cast
from urllib.parse import unquote, urlparse

from tensor_grep.cli import lang_c, lang_cpp, lang_csharp, lang_go, lang_php, lang_registry
from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager, LSPTransportError
from tensor_grep.core.retrieval_lexical import score_term_overlap, split_terms

_CacheR = TypeVar("_CacheR")

# ---------------------------------------------------------------------------
# B7: mtime-aware cache decorator for path-keyed file-content helpers.
#
# Plain @lru_cache(key=path_str) returns stale results in the long-lived
# daemon after a file is edited.  This decorator folds (st_mtime_ns, st_size)
# into the key so callers automatically see fresh content after a write
# without needing a manual cache_clear().
#
# Convention: the decorated function must accept the file path as its FIRST
# positional argument (a str).  Additional arguments form the rest of the key.
# ---------------------------------------------------------------------------


def _mtime_key(path_str: str) -> tuple[int, int]:
    """Return (st_mtime_ns, st_size) for *path_str*, or (-1, -1) on error."""
    try:
        st = Path(path_str).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (-1, -1)


# Fix A / Guard 3: every _mtime_aware_cache-decorated function registers its cache_clear
# here so a warm daemon can sweep ALL of them in one call when a session is refreshed/detected
# stale. Without this, a same-(mtime_ns,size) edit landing between two daemon calls could keep
# serving a stale cached parse/read forever (the mtime key alone can't tell them apart).
_MTIME_CACHE_CLEAR_REGISTRY: list[Callable[[], None]] = []


def _clear_all_source_caches() -> None:
    """Sweep every _mtime_aware_cache-decorated cache (Fix A / Guard 3).

    Must be invoked whenever the daemon re-reads/refreshes a session (see
    session_store.refresh_session), since these caches are process-global and outlive any
    single session payload.
    """
    for clear in _MTIME_CACHE_CLEAR_REGISTRY:
        clear()
    # Fable final-review (advisory B): the JS/TS + Rust per-repo contexts hold parsed tsconfig +
    # re_export_cache keyed by root; they are NOT _mtime_aware_cache wrappers, so without this they
    # survive a refresh and a warm daemon could serve a stale re-export / tsconfig-alias resolution
    # after an edit. Clear them too so the sweep is actually complete (they rebuild on demand).
    _JS_TS_REPO_CONTEXTS.clear()
    _RUST_REPO_CONTEXTS.clear()
    lang_go.clear_go_repo_context_cache()


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
            mtime_key = _mtime_key(path_str)
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


JSON_OUTPUT_VERSION = 1
ROUTING_BACKEND = "RepoMap"
ROUTING_REASON = "repo-map"
# backlog #1 (Fable+thinktank plan, 2026-07-06): raised 512 -> 2000 so ROUTING commands
# (edit-plan/agent/context-render/defs/orient/session-open/MCP fallbacks) stop misrouting on
# repos >512 files -- a file past the old cap never entered the map at all, so the right file
# could not be found (dogfood-proven: edit-plan "API retry" -> wrong file at 512, correct at
# 2000). Measured cold repo-map-build cost: 512=1.48s, 2000=3.51s (OK), 4000=10.35s
# (superlinear -- do NOT raise past 2000 without re-measuring). This constant is ALSO the
# default for `main.py`'s shared `_DEFAULT_AGENT_REPO_SCAN_LIMIT` CLI option default (kept as a
# separate literal there deliberately -- see the comment at its definition) which feeds BOTH
# routing commands AND the caller-scan commands (callers/refs/blast-radius/impact); raising it
# is safe for caller-scan latency ONLY because CALLER_SCAN_FILE_CEILING below bounds their
# actual per-file work independently of how large the map is.
DEFAULT_AGENT_REPO_MAP_LIMIT = 2000
# backlog #1 chokepoint (the thinktank's winning insight over "repoint every option default",
# which leaks -- e.g. session_store.py's stored-session blast-radius calls
# build_symbol_blast_radius_from_map directly on a full session repo_map with NO max_repo_files
# passthrough, so a per-command option default cannot reach it). The caller-scan functions
# (build_symbol_callers_from_map, build_symbol_blast_radius_from_map via its callers-scan,
# build_symbol_refs_from_map) do a slow per-file prefilter + re-parse whose wall-clock scales
# with the file universe size (task #52: ~100s on a 1941-file repo at the old 512 cap). This
# ceiling bounds that ONE hot loop at a single internal chokepoint, so it stays fast regardless
# of DEFAULT_AGENT_REPO_MAP_LIMIT / an explicit --max-repo-files / a stored session map's size.
# backlog #57 (2026-07-09): raised 512 -> 2000, matching DEFAULT_AGENT_REPO_MAP_LIMIT above, now
# that #478 threaded a --deadline hard-bound through every caller-scan loop and closed the #52
# unbounded-hang risk that originally kept this ceiling frozen below the map default. 2000 is a
# deliberate knee (see the map-build cost note above), not a removal of the cap: the ceiling
# stays a hard backstop for a --max-repo-files-raised mega-repo and for the flag-less
# (--deadline omitted) default path, which still has no other wall-clock bound. Raising past
# 2000 needs fresh cost data plus confirming _PARSE_PRODUCT_CACHE_MAXSIZE (below) still exceeds
# it -- otherwise the shared parse-cache guarantee silently thrashes via FIFO eviction. When the
# scan is capped below the map's true file count, the payload is marked result_incomplete (see
# _CALLER_SCAN_CEILING_REMEDIATION) so the exit-2 truncation-honesty contract still fires.
CALLER_SCAN_FILE_CEILING = 2000
# F1-review HIGH fix (task#52 shape, 2026-07-06): _order_caller_scan_candidates probes
# _file_may_contain_literal_symbol (a stat + cached read_bytes) across the caller-scan file
# UNIVERSE to decide ordering, BEFORE _cap_caller_scan_files slices to CALLER_SCAN_FILE_CEILING.
# Left unbounded, that probe pays O(map-size) I/O regardless of the 512 ceiling or --deadline --
# exactly the pathology CALLER_SCAN_FILE_CEILING exists to prevent, just one step earlier in the
# pipeline. This ceiling bounds the PROBE itself (belt); the deadline check threaded into the
# same loop is the suspenders. 4x the scan ceiling so a normal (<=2000-file) map's ordering pass
# is never truncated in practice -- it only bites when --max-repo-files is raised well past the
# default, which is exactly the case this fix targets.
CALLER_SCAN_ORDER_PROBE_CEILING = 4 * CALLER_SCAN_FILE_CEILING
# How often (in probed files) the ordering pass re-checks the deadline. Checking every file would
# add a time.monotonic() call per file; checking too rarely risks overrunning a tight --deadline
# by a wide margin. 64 is a compromise consistent with other bounded-scan loops in this module.
_CALLER_SCAN_ORDER_PROBE_DEADLINE_STRIDE = 64
_DEFAULT_LSP_OPERATION_BUDGET_SECONDS = 2.0
_LSP_OPERATION_BUDGET_ENV_VAR = "TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS"
_SKIP_DIR_NAMES = {
    ".tensor-grep",
    # tg-owned index/reference trees — never product source, and can be large enough to
    # hang an unscoped walk (critical unscoped-search-hang audit). Kept in sync with
    # docs_coverage.py's _EXCLUDED_DIR_PARTS, which already excludes these.
    "_tg_refs",
    ".tg_semantic_index",
    "external_repos",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".mypy_cache",
    ".next",
    ".nyc_output",
    ".parcel-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv_cuda",
    ".tox",
    ".turbo",
    ".vite",
    ".vitest",
    "__pycache__",
    "artifacts",
    "bench_data",
    "build",
    "coverage",
    "dist",
    "gpu_bench_data",
    "group2_many_files",
    "htmlcov",
    "many_files",
    "node_modules",
    "out",
    "site",
    "target",
    "temp",
    "tmp",
    "tmp_agent_probe",
    "venv",
    ".cache",
    ".nox",
    # additional vendor/cache dirs that can slip through walk ordering
    "site-packages",
    "vendor",
    "pods",
    "gems",
    ".bundle",
    ".gradle",
    ".cargo",
    "bower_components",
}
# Path-component names considered vendor/cache for truncation_cause classification.
# These are checked against every component of a returned file's relative path so
# that files nested inside a vendor dir that wasn't excluded at the walk level (e.g.
# because the vendor dir sits deeper than the root scan entry points) are still
# correctly identified as non-project files.
_VENDOR_CACHE_DIR_COMPONENTS: frozenset[str] = frozenset(
    _SKIP_DIR_NAMES
    | {
        # extra names that appear as sub-directory components but may not be
        # top-level walk roots (so _should_skip_repo_dir never sees them directly).
        # NB: do NOT add bare "lib" here — it is a common SOURCE directory; the
        # vendored case is lib/.../site-packages, already covered by "site-packages".
        # Misclassifying lib/ project files as vendor makes possibly_truncated False,
        # which silently disables blast-radius literal-symbol seeding.
        "site_packages",  # older virtualenv layout
    }
)
_JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_TS_SUFFIXES = {".ts", ".tsx"}
_RUST_SUFFIXES = {".rs"}
_JAVA_SUFFIXES = {".java"}
# Top-10 language campaign (Phase 2, C++): matches lang_cpp.py's LanguageSpec.suffixes AND
# _provider_language_for_path's pre-existing "cpp" assignment exactly -- ".h" is claimed by C++
# (not C), see lang_cpp.py's module docstring for the header-ambiguity rationale.
_CPP_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
_SOURCE_FIRST_DIR_NAMES = {
    ".claude",
    "app",
    "apps",
    "bin",
    "cmd",
    "crates",
    "lib",
    "packages",
    "rust_core",
    "scripts",
    "src",
    "tools",
}
_TEST_DIR_NAMES = {"__tests__", "spec", "specs", "test", "tests"}
_SOURCE_FIRST_SUFFIXES = {
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".go",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".php",
    ".py",
    ".rs",
    ".swift",
    ".tsx",
    ".ts",
}
_RENDER_PROFILES = {"full", "compact", "llm"}
_JS_RUNNER_ORDER = ("jest", "vitest", "mocha")
_QUERY_TERM_SYNONYMS = {
    "resolved": ("resolve",),
    "resolving": ("resolve",),
    "resolution": ("resolve",),
}
_DEFAULT_EDIT_PLAN_MAX_DEPTH = 3
_VALIDATION_RUNNER_SCAN_LIMIT = 512
_SOURCE_FALLBACK_SCAN_LIMIT = 8
_DIRECT_VALIDATION_SYMBOL_SCAN_LIMIT = 64
_DIRECT_VALIDATION_TEST_SCAN_LIMIT = 256
_GRAPH_PAGERANK_SEED_FILE_LIMIT = 64
_EDIT_PLAN_BLAST_RADIUS_FILE_MULTIPLIER = 4
_FRAMEWORK_TEST_PATTERN_SMALL_TEST_LIMIT = 128
_SYMBOL_LITERAL_SEED_SCAN_LIMIT = 4096
_SYMBOL_LITERAL_SEED_MAX_FILES = 16
_SYMBOL_LITERAL_SEED_MAX_BYTES = 2_000_000
_BLAST_RADIUS_LIMITED_SYMBOLS_PER_FILE = 3
_REPO_CONTEXT_CACHE_MAX_ROOTS_ENV = "TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS"
_DEFAULT_REPO_CONTEXT_CACHE_MAX_ROOTS = 32
# O2: per-file byte cap for AST parsing in build_repo_map.  Files larger than
# this are skipped (imports/symbols returned empty) to bound RSS spikes from
# multi-MB bundled/generated files.  Override with env var.
_MAX_PARSE_BYTES_ENV = "TENSOR_GREP_MAX_PARSE_BYTES"
_DEFAULT_MAX_PARSE_BYTES = 2_000_000
# Total-resident-bytes budget for the content-addressed AST parse cache (see
# _cached_ast_parse below).  Bounds DAEMON MEMORY -- independent of _MAX_PARSE_BYTES_ENV
# above, which only bounds a single file's eligibility to be parsed/cached at all.
# Override with env var.
_AST_CACHE_BYTES_ENV = "TENSOR_GREP_AST_CACHE_BYTES"
_DEFAULT_AST_CACHE_BYTES = 64 * 1024 * 1024  # 64 MiB
_RUST_TEST_FN_PATTERN = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
_JS_TS_REPO_CONTEXTS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_RUST_REPO_CONTEXTS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_EXTERNAL_LSP_PROVIDER_MANAGER = ExternalLSPProviderManager()
atexit.register(_EXTERNAL_LSP_PROVIDER_MANAGER.stop_all)


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _repo_context_cache_max_roots() -> int:
    return _configured_positive_int(
        _REPO_CONTEXT_CACHE_MAX_ROOTS_ENV,
        _DEFAULT_REPO_CONTEXT_CACHE_MAX_ROOTS,
    )


def _max_parse_bytes() -> int:
    """Return the per-file byte cap for AST parsing (O2)."""
    return _configured_positive_int(_MAX_PARSE_BYTES_ENV, _DEFAULT_MAX_PARSE_BYTES)


def _ast_cache_byte_budget() -> int:
    """Return the total-resident-bytes budget for the AST parse cache."""
    return _configured_positive_int(_AST_CACHE_BYTES_ENV, _DEFAULT_AST_CACHE_BYTES)


def _remember_repo_context(
    cache: OrderedDict[str, dict[str, Any]],
    key: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    cache.pop(key, None)
    cache[key] = context
    while len(cache) > _repo_context_cache_max_roots():
        cache.popitem(last=False)
    return context


def _get_repo_context_cache_entry(
    cache: OrderedDict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any] | None:
    cached = cache.pop(key, None)
    if cached is None:
        return None
    cache[key] = cached
    return cached


class _ValidationRunnerInfo(NamedTuple):
    has_python: bool
    has_rust: bool
    has_javascript: bool
    python_detection: str
    has_package_json: bool
    js_runners: tuple[str, ...]
    ts_runners: tuple[str, ...]
    js_script_command: str | None
    js_fallback_command: str | None
    js_test_script: str | None


class _ProfilePhase:
    def __init__(self, collector: _ProfileCollector, name: str) -> None:
        self._collector = collector
        self._name = name
        self._start = 0.0

    def __enter__(self) -> None:
        self._collector._push(self._name)
        self._start = time.perf_counter()
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        try:
            elapsed = max(0.0, time.perf_counter() - self._start)
            self._collector._record(self._name, elapsed)
        finally:
            self._collector._pop()
        return False


class _ProfileCollector:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._local = threading.local()
        self._lock = threading.Lock()
        self._phase_totals: dict[str, float] = {}
        self._phase_calls: dict[str, int] = {}
        self._phase_order: list[str] = []

    def _stack(self) -> list[str]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _push(self, name: str) -> None:
        self._stack().append(name)

    def _pop(self) -> None:
        stack = self._stack()
        if stack:
            stack.pop()

    def _record(self, name: str, elapsed: float) -> None:
        with self._lock:
            if name not in self._phase_totals:
                self._phase_totals[name] = 0.0
                self._phase_calls[name] = 0
                self._phase_order.append(name)
            self._phase_totals[name] += elapsed
            self._phase_calls[name] += 1

    def phase(self, name: str) -> _ProfilePhase | nullcontext[None]:
        if not self.enabled:
            return nullcontext()
        return _ProfilePhase(self, name)

    def result(self) -> dict[str, Any]:
        with self._lock:
            phases: list[dict[str, Any]] = [
                {
                    "name": name,
                    "elapsed_s": float(self._phase_totals[name]),
                    "calls": int(self._phase_calls[name]),
                }
                for name in self._phase_order
            ]
            total_elapsed = float(sum(self._phase_totals[name] for name in self._phase_order))
        breakdown_pct = (
            {
                str(phase["name"]): (float(phase["elapsed_s"]) / total_elapsed) * 100.0
                for phase in phases
            }
            if total_elapsed > 0.0
            else {}
        )
        return {
            "phases": phases,
            "total_elapsed_s": total_elapsed,
            "breakdown_pct": breakdown_pct,
        }


def _profiling_phase(
    collector: _ProfileCollector | None,
    name: str,
) -> _ProfilePhase | nullcontext[None]:
    if collector is None:
        return nullcontext()
    return collector.phase(name)


def _resolve_profiling_collector(
    *,
    profile: bool,
    collector: _ProfileCollector | None,
) -> _ProfileCollector | None:
    if collector is not None:
        return collector
    if profile:
        return _ProfileCollector()
    return None


def _attach_profiling(
    payload: dict[str, Any],
    collector: _ProfileCollector | None,
) -> dict[str, Any]:
    if collector is not None and collector.enabled:
        profile_result = collector.result()
        payload["_profiling"] = profile_result
        payload["profile"] = {"enabled": True, **profile_result}
    else:
        payload.pop("_profiling", None)
        payload.pop("profile", None)
    return payload


def _language_scope_descriptor() -> str:
    """Dynamically derive the honest ``coverage.language_scope`` value from
    ``lang_registry.LANGUAGE_REGISTRY`` -- the single source of truth for which languages have
    a registered symbol-graph ``LanguageSpec`` (see lang_registry.py's module docstring) --
    instead of a hand-maintained literal.

    Dogfood-found honesty bug fix: this used to be the hardcoded 4-language literal
    ``"python-js-ts-rust"``, which silently under-reported coverage to agent/MCP consumers
    once go/java/php/csharp/c/cpp were onboarded (the language-support campaign registered 6
    more languages in ``lang_registry`` without anyone updating this string -- a classic
    N-site-registration miss, since nothing wired the envelope to the registry). Deriving it
    live means the NEXT language onboarded (see AGENTS.md's "Adding a Language" section) can
    never cause the same drift: as soon as its ``LanguageSpec`` is registered, it appears here
    automatically. Sorted for a deterministic value independent of registration order.
    """
    return "-".join(sorted(lang_registry.LANGUAGE_REGISTRY))


def _symbol_navigation_descriptor() -> str:
    """Dynamically derive the honest ``coverage.symbol_navigation`` value, split into the two
    real navigation tiers instead of one flat language list (same honesty-bug fix as
    ``_language_scope_descriptor`` above -- see that function's docstring for the incident).

    - ``parser-backed-refs-callers``: languages whose ``LanguageSpec.references_and_calls`` is
      wired up get AST/tree-sitter-VERIFIED ``tg refs``/``tg callers``/``tg blast-radius``
      (see the explicit per-``language_id`` dispatch branches in
      ``build_symbol_refs_from_map`` / ``build_symbol_callers_from_map``, e.g. around the
      ``current_spec.language_id == "go"`` branch). Verified live: this tier currently also
      includes go, which several PR-comment summaries lump in with the "foundational-only"
      languages below -- that undercounts it. Go's own dedicated
      ``lang_go.go_references_and_calls`` is a full tree-sitter extractor (package-alias
      resolution, node-type-based ref_kind), not a regex fallback, so it belongs here.
    - ``foundational-defs-imports-only``: languages with ``references_and_calls is None``
      still get parser-backed defs/imports (see ``_imports_and_symbols_for_path``'s
      fail-closed per-language branches -- NO regex fallback, an unparseable file becomes an
      honest ``resolution_gaps`` entry), but ``tg refs``/``tg callers``/``tg blast-radius``
      fall through to the generic ``_regex_references_and_calls`` text heuristic instead of an
      AST-verified match -- these languages' own registration comments in this module
      self-label "FOUNDATIONAL-TIER" for exactly this reason.

    Both groups are derived live from ``LANGUAGE_REGISTRY`` and sorted, so a newly onboarded
    language lands in the correct bucket automatically the moment its ``LanguageSpec`` is
    registered, without a repeat of the drift ``_language_scope_descriptor`` fixes.
    """
    parser_backed = sorted(
        language_id
        for language_id, spec in lang_registry.LANGUAGE_REGISTRY.items()
        if spec.references_and_calls is not None
    )
    foundational = sorted(
        language_id
        for language_id, spec in lang_registry.LANGUAGE_REGISTRY.items()
        if spec.references_and_calls is None
    )
    parts = [f"parser-backed-refs-callers:{'-'.join(parser_backed)}"]
    if foundational:
        parts.append(f"foundational-defs-imports-only:{'-'.join(foundational)}")
    return "+".join(parts)


def _envelope(path: Path) -> dict[str, Any]:
    return {
        "version": JSON_OUTPUT_VERSION,
        "schema_version": JSON_OUTPUT_VERSION,
        "routing_backend": ROUTING_BACKEND,
        "routing_reason": ROUTING_REASON,
        "sidecar_used": False,
        "coverage": {
            "language_scope": _language_scope_descriptor(),
            "symbol_navigation": _symbol_navigation_descriptor(),
            "test_matching": "filename+import+graph-heuristic",
        },
        "path": str(path),
    }


# dogfood 1.28.3 feature #3: a machine-readable remediation carried as a top-level `scan_remediation`
# sibling of `scan_limit`, so a JSON-consuming agent gets the actionable next step without parsing the
# stderr warning (a truncated scan with a zero/small count otherwise reads as a real "not found"
# answer -- a silent-truncation trap). Kept OUT of the scan_limit dict on purpose: scan_limit is a
# stable exact-shape contract (facts), scan_remediation is the advice. Non-null only when the scan
# actually dropped project files.
_SCAN_LIMIT_TRUNCATED_REMEDIATION = (
    "A truncated scan dropped project files, so a zero or small count is NOT trustworthy. "
    "Re-run scoped to a subdirectory PATH, raise --max-repo-files, or warm the index with "
    "`tg session daemon start`."
)

# backlog #1 chokepoint: distinct from _SCAN_LIMIT_TRUNCATED_REMEDIATION above (which fires when
# the repo-MAP itself was truncated before this symbol resolved). This fires when the map was
# complete but the caller-scan's OWN internal ceiling (CALLER_SCAN_FILE_CEILING) bounded how many
# of the map's files were actually walked for callers/references -- a resolved symbol may still
# be missing callers that live past the ceiling.
_CALLER_SCAN_CEILING_REMEDIATION = (
    f"The caller-scan was bounded to the first {CALLER_SCAN_FILE_CEILING} repo-map files for "
    "latency, even though the map covers more; callers/references in files past that window "
    "may be missing. Narrow PATH to a subdirectory containing the symbol for full coverage."
)


def _mark_result_incomplete(
    payload: dict[str, Any],
    *,
    remediation: str,
    caller_scan_limit: dict[str, Any] | None = None,
) -> None:
    """Payload-level honesty signal (round-6 council): set result_incomplete + remediation at ASSEMBLY
    time so non-CLI consumers (MCP tools, *_json) get the same truncation signal the CLI emitter adds.
    Additive; callers gate it on possibly_truncated so complete results never grow the key.

    backlog #1 fix: a plain ``setdefault`` was a no-op whenever the payload already carried a
    ``scan_remediation`` key with value ``None`` (every repo_map/defs payload does -- build_repo_map
    always stamps ``scan_remediation`` to either a message or ``None``, never omits the key). That
    silently swallowed the CALLER_SCAN_FILE_CEILING remediation on a repo_map that was itself
    complete (dogfood-caught: `tg callers` on a real >512-file repo set result_incomplete:true but
    scan_remediation:null). Only skip when an existing message is genuinely present (truthy), so a
    MORE SPECIFIC remediation set earlier is still preserved (test:
    test_mark_result_incomplete_helper_does_not_clobber_existing_remediation).

    F1 fix: ``caller_scan_limit`` is the structured sibling of the CALLER_SCAN_CEILING
    remediation string above -- a machine-checkable ``{"possibly_truncated": True, "ceiling":
    ..., "files_total": ...}`` dict (mirroring the existing ``scan_limit`` shape) so a JSON
    consumer doesn't have to regex the prose remediation to learn the ceiling/total. Only
    callers that actually hit the caller-scan ceiling pass this; every other
    ``_mark_result_incomplete`` call site (the generic repo-map scan truncation) omits it, so a
    complete-map/ceiling-truncated result never grows the key."""
    payload["result_incomplete"] = True
    if not payload.get("scan_remediation"):
        payload["scan_remediation"] = remediation
    if caller_scan_limit is not None:
        payload["caller_scan_limit"] = caller_scan_limit


def _copy_scan_limit(payload: dict[str, Any], source: dict[str, Any]) -> None:
    scan_limit = source.get("scan_limit")
    if isinstance(scan_limit, dict):
        payload["scan_limit"] = dict(scan_limit)
        # Propagate the advice sibling alongside the facts (only when the source carried it).
        if "scan_remediation" in source:
            payload["scan_remediation"] = source["scan_remediation"]
    # Carry the payload-level honesty flag too (codex review, round-6): builders that rebuild a FRESH
    # envelope via this helper (e.g. build_symbol_source_from_map) would otherwise drop
    # result_incomplete on a truncated no_match while keeping scan_remediation -> a non-CLI consumer
    # (MCP tg_symbol_source, *_json) sees the advice but not the machine-checkable flag. Parity: only
    # when the source set it True (a complete result never carries it).
    if source.get("result_incomplete"):
        payload["result_incomplete"] = True


def _copy_partial_signal(payload: dict[str, Any], source: dict[str, Any]) -> None:
    """Moat P0-6 step 2: carry the deadline PARTIAL signal forward when a symbol builder repackages a
    build_repo_map / build_symbol_defs result into its own payload. Without this, a deadline-truncated
    map silently loses partial:true + deadline_limit the moment it is wrapped, so the agent sees a
    small result with no signal it was cut short. Only propagates when the source was actually
    partial (a complete result carries neither key -- parity). Kept separate from _copy_scan_limit:
    scan_limit is the file-cap fact, partial is the time-budget outcome."""
    if source.get("partial"):
        payload["partial"] = True
        deadline_limit = source.get("deadline_limit")
        if isinstance(deadline_limit, dict):
            payload["deadline_limit"] = dict(deadline_limit)


def _deadline_monotonic_from_seconds(deadline_seconds: float | None) -> float | None:
    """Convert a relative --deadline (seconds-from-now) to an ABSOLUTE time.monotonic() timestamp,
    computed ONCE at the top of a builder so that multiple internal build_repo_map calls (e.g. the
    blast-radius literal-seed retry) SHARE one budget instead of each getting a fresh N seconds."""
    if deadline_seconds is None:
        return None
    return time.monotonic() + deadline_seconds


class _DeadlineBreakFlag:
    """Task #61: mutable out-signal for whether a deadline-scoped sibling loop broke early.

    ``_build_import_graph_consumers_from_map`` and ``_preferred_definition_files`` are also called
    from non-deadline-aware seams (agent_capsule's edit-plan primary-file resolution,
    ``build_symbol_impact_from_map``) that pass no ``deadline_monotonic`` and expect the existing
    plain ``list[...]`` return value -- widening the return type to a tuple would ripple into every
    call site. This tiny mutable object lets the deadline-aware callers/blast-radius seams read back
    "did this loop break on --deadline" *after* the call, so the caller-scan's existing
    partial/incomplete stamp (``caller_scan_deadline_hit`` in ``build_symbol_callers_from_map``) can
    fold sibling-loop truncation into the same honesty gate instead of silently completing a
    partially-scanned result.
    """

    __slots__ = ("hit",)

    def __init__(self) -> None:
        self.hit = False


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.js")
        or "tests" in path.parts
        or "__tests__" in path.parts
    )


def _gitignore_pattern_to_regex(pattern: str) -> str:
    """Translate a single gitignore glob body to an anchored regex fragment.

    The fragment matches against a repo-relative POSIX path (no leading slash).
    Handles ``*`` (segment-local), ``**`` (cross-segment), ``?`` and literal
    characters; anchoring/negation/dir-only handling lives in the caller.
    """

    regex_parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                # ``**`` matches any number of path segments (including none).
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                    regex_parts.append("(?:.*/)?")
                else:
                    regex_parts.append(".*")
            else:
                regex_parts.append("[^/]*")
                index += 1
        elif char == "?":
            regex_parts.append("[^/]")
            index += 1
        else:
            regex_parts.append(re.escape(char))
            index += 1
    return "".join(regex_parts)


class _GitignoreMatcher:
    """Pragmatic ``.gitignore`` matcher covering the common pattern forms.

    Supports directory-only patterns (trailing ``/``), anchored patterns
    (leading or embedded ``/``), unanchored basename patterns, ``*``/``**``/``?``
    globs, and ``!`` negation. Paths are matched relative to ``root`` using
    POSIX separators. This intentionally mirrors ripgrep's gitignore handling
    closely enough that context/repo-map walks stop drowning in vendored and
    cache files; it is not a byte-perfect reimplementation of git's spec.
    """

    def __init__(self, root: Path, lines: list[str]) -> None:
        self._root = root.resolve()
        self._rules: list[tuple[re.Pattern[str], bool, bool]] = []
        for raw_line in lines:
            line = raw_line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            # A literal leading ``#`` or ``!`` is escaped with a backslash.
            negated = False
            if line.startswith("!"):
                negated = True
                line = line[1:]
            elif line.startswith(("\\#", "\\!")):
                line = line[1:]
            line = line.rstrip()
            if not line:
                continue
            dir_only = line.endswith("/")
            body = line[:-1] if dir_only else line
            anchored = body.startswith("/") or ("/" in body.rstrip("/"))
            body = body.lstrip("/")
            if not body:
                continue
            fragment = _gitignore_pattern_to_regex(body)
            if anchored:
                regex = rf"^{fragment}(?:/.*)?$"
            else:
                # Unanchored patterns match at any depth.
                regex = rf"(?:^|.*/){fragment}(?:/.*)?$"
            try:
                compiled = re.compile(regex)
            except re.error:
                continue
            self._rules.append((compiled, negated, dir_only))

    @property
    def has_rules(self) -> bool:
        return bool(self._rules)

    def check(self, path: Path, *, is_dir: bool) -> bool | None:
        # Tri-state result so nested .gitignore specs can be stacked with correct git
        # precedence: True = matched an ignore, False = matched a negated re-include, None =
        # no rule matched (this spec has no opinion). Match the path AS WALKED — never
        # ``resolve()`` here. ``self._root`` is resolved once in __init__ and the walk descends
        # from it, so every entry is already absolute and under the root. Calling
        # ``path.resolve()`` per entry would add a stat/symlink syscall for every file in the
        # tree — an O(files) regression on large roots (~384k files) and would follow symlinks,
        # which is not how gitignore matches paths.
        candidate = path if path.is_absolute() else (self._root / path)
        try:
            relative = candidate.relative_to(self._root)
        except ValueError:
            return None
        rel_posix = relative.as_posix()
        if not rel_posix or rel_posix == ".":
            return None
        # A dir-only pattern (``dist/``) ignores the directory *and everything
        # inside it*. Test the path itself plus each ancestor segment-prefix so
        # files under an ignored directory are recognised even when checked in
        # isolation (the walk also prunes such directories during traversal).
        segments = rel_posix.split("/")
        ancestor_prefixes = ["/".join(segments[: index + 1]) for index in range(len(segments) - 1)]
        decision: bool | None = None
        for compiled, negated, dir_only in self._rules:
            if dir_only:
                matched = any(compiled.match(prefix) for prefix in ancestor_prefixes)
                if is_dir:
                    matched = matched or bool(compiled.match(rel_posix))
            else:
                matched = bool(compiled.match(rel_posix))
                if not matched:
                    # An unanchored/file pattern still ignores descendants of a
                    # directory it matched (e.g. ``build`` matching ``build/x``).
                    matched = any(compiled.match(prefix) for prefix in ancestor_prefixes)
            if matched:
                decision = not negated
        return decision

    def is_ignored(self, path: Path, *, is_dir: bool) -> bool:
        return bool(self.check(path, is_dir=is_dir))


@lru_cache(maxsize=64)
def _load_gitignore_matcher(root_key: str) -> _GitignoreMatcher:
    root = Path(root_key)
    gitignore_path = root / ".gitignore"
    lines: list[str] = []
    try:
        if gitignore_path.is_file():
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    return _GitignoreMatcher(root, lines)


def _repo_walk_dir_sort_key(name: str) -> tuple[int, str]:
    normalized = name.lower()
    if normalized in _SOURCE_FIRST_DIR_NAMES:
        return (0, normalized)
    if normalized in _TEST_DIR_NAMES:
        return (1, normalized)
    return (2, normalized)


def _repo_walk_file_sort_key(name: str) -> tuple[int, str]:
    suffix = Path(name).suffix.lower()
    if suffix in _SOURCE_FIRST_SUFFIXES:
        return (0, name.lower())
    return (1, name.lower())


def _repo_walk_path_sort_key(path: Path, root: Path) -> tuple[int, int, int, str]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    try:
        group_parts = relative.parts if path.is_dir() else relative.parts[:-1]
    except OSError:
        group_parts = relative.parts[:-1]
    normalized_group_parts = {part.lower() for part in group_parts}
    if normalized_group_parts & _SOURCE_FIRST_DIR_NAMES:
        group = 0
    elif normalized_group_parts & _TEST_DIR_NAMES:
        group = 1
    elif len(relative.parts) == 1 and not path.is_dir():
        group = 2
    else:
        group = 3
    file_group, file_name = _repo_walk_file_sort_key(path.name)
    return (group, file_group, len(relative.parts), relative.as_posix().lower() or file_name)


def _repo_walk_bucket_sort_key(path: Path, root: Path) -> tuple[int, str]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    path_group = _repo_walk_path_sort_key(path, root)[0]
    top_level = relative.parts[0].lower() if relative.parts else path.name.lower()
    return (path_group, top_level)


def _should_skip_repo_dir(path: Path) -> bool:
    name = path.name.lower()
    if name in _SKIP_DIR_NAMES:
        return True
    if name == "context" and path.parent.name.lower() == ".claude":
        return True
    return name.startswith((
        ".tmp_",
        ".tmp-",  # covers .tmp-ci, .tmp-ci-123, etc.
        "tg-agent-gpu-probe",
        "tg-doctor-gpu-probe",
    ))


def _path_has_vendor_component(path: Path, root: Path) -> bool:
    """Return True if *path* passes through a vendor/cache directory component.

    Used to classify capped files as 'vendor-cache' vs 'project-files' so that
    ``possibly_truncated`` / ``truncation_cause`` reflects whether RELEVANT
    (non-vendored) files were actually dropped by the scan cap.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    for part in relative.parts[:-1]:  # skip the final filename
        part_lower = part.lower()
        if part_lower in _VENDOR_CACHE_DIR_COMPONENTS:
            return True
        if part_lower.startswith((".tmp_", ".tmp-")):
            return True
    return False


def _scan_limit_cause(
    files: list[Path],
    root: Path,
    capped_file_count: int,
    max_files: int,
) -> str:
    """Classify why the file-scan cap was reached.

    Returns ``'project-files'`` when the cap was reached because there are
    more non-vendored project files than the limit, or ``'vendor-cache'`` when
    every file at or over the cap boundary belongs to a vendor/cache subtree.

    The heuristic: if ALL files in the returned list are vendor-path files,
    the entire quota was consumed by vendor dirs — a real truncation warning
    would be misleading.  If any file is a project file, we conservatively
    report ``'project-files'``.
    """
    if capped_file_count < max_files:
        # Cap was not actually reached — caller shouldn't be calling this, but
        # return a safe value anyway.
        return "project-files"
    project_file_found = any(not _path_has_vendor_component(f, root) for f in files)
    return "project-files" if project_file_found else "vendor-cache"


def _stack_ignored(path: Path, *, is_dir: bool, stack: tuple[_GitignoreMatcher, ...]) -> bool:
    # Apply the ancestor .gitignore matchers shallowest-first; the DEEPEST matcher with an opinion
    # wins (git precedence: a nested .gitignore overrides a parent's). check()'s tri-state
    # (True ignore / False negated-reinclude / None no-opinion) lets a deeper `!re-include`
    # correctly override a parent's ignore.
    decision: bool | None = None
    for matcher in stack:
        result = matcher.check(path, is_dir=is_dir)
        if result is not None:
            decision = result
    return bool(decision)


def _iter_repo_bucket_files(
    root: Path,
    ancestor_stack: tuple[_GitignoreMatcher, ...] = (),
) -> Iterator[Path]:
    # Nested-.gitignore support (MED-5 part B): load THIS directory's own matcher and push it onto
    # the ancestor stack, so a nested subdir/.gitignore is honored -- not just the repo root's.
    # _load_gitignore_matcher is lru_cached per directory, so re-walks stay cheap.
    own = _load_gitignore_matcher(str(root))
    stack = (*ancestor_stack, own) if own.has_rules else ancestor_stack
    try:
        entries = list(os.scandir(root))
    except OSError:
        return

    dir_paths: list[Path] = []
    file_paths: list[Path] = []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                directory = Path(entry.path)
                if _should_skip_repo_dir(directory):
                    continue
                if _stack_ignored(directory, is_dir=True, stack=stack):
                    continue
                dir_paths.append(directory)
            elif entry.is_file(follow_symlinks=False):
                file_path = Path(entry.path)
                if _stack_ignored(file_path, is_dir=False, stack=stack):
                    continue
                file_paths.append(file_path)
        except OSError:
            continue

    dir_paths.sort(key=lambda path: _repo_walk_dir_sort_key(path.name))
    file_paths.sort(key=lambda path: _repo_walk_file_sort_key(path.name))
    source_dirs = [path for path in dir_paths if _repo_walk_dir_sort_key(path.name)[0] <= 1]
    other_dirs = [path for path in dir_paths if _repo_walk_dir_sort_key(path.name)[0] > 1]

    for directory in source_dirs:
        yield from _iter_repo_bucket_files(directory, stack)
    yield from file_paths
    for directory in other_dirs:
        yield from _iter_repo_bucket_files(directory, stack)


def _iter_repo_files(
    root: Path,
    *,
    max_files: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> list[Path]:
    """Walk *root* for repo files, optionally bounded by a file-count cap AND/OR a wall-clock
    ``deadline_monotonic`` (task #52, loop A). ``deadline_hit`` is the mutable out-signal (mirrors
    ``_DeadlineBreakFlag`` sibling seams in this module) so a caller whose own local is declared
    AFTER this call can still learn "did the walk itself break early on --deadline" and fold that
    into its partial/deadline_limit stamp. Both new params default to None -> byte-identical to the
    pre-fix walk for every one of the ~12 existing call sites that do not pass them."""
    with _profiling_phase(_profiling_collector, "file_walk"):
        if root.is_file():
            return [root.resolve()]

        files: list[Path] = []
        normalized_root = root.resolve()
        gitignore = _load_gitignore_matcher(str(normalized_root))
        if max_files is not None:
            try:
                entries = list(os.scandir(normalized_root))
            except OSError:
                return []

            buckets: list[tuple[tuple[int, str], Iterator[Path]]] = []
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        path = Path(entry.path)
                        if _should_skip_repo_dir(path):
                            continue
                        if gitignore.is_ignored(path, is_dir=True):
                            continue
                        buckets.append((
                            _repo_walk_bucket_sort_key(path, normalized_root),
                            _iter_repo_bucket_files(path, (gitignore,)),
                        ))
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        if gitignore.is_ignored(path, is_dir=False):
                            continue
                        buckets.append((
                            _repo_walk_bucket_sort_key(path, normalized_root),
                            iter([path]),
                        ))
                except OSError:
                    continue

            selected: list[Path] = []
            limit = max(1, max_files)
            bucket_groups: dict[int, list[tuple[tuple[int, str], Iterator[Path]]]] = {}
            for key, iterator in buckets:
                bucket_groups.setdefault(key[0], []).append((key, iterator))
            walk_deadline_exceeded = False
            for group in sorted(bucket_groups):
                active_buckets = sorted(bucket_groups[group], key=lambda item: item[0])
                while active_buckets and len(selected) < limit:
                    next_buckets: list[tuple[tuple[int, str], Iterator[Path]]] = []
                    for key, iterator in active_buckets:
                        # task #52 loop A: one deadline check per next() = the file-granularity
                        # this walk otherwise has no time bound at all (only the max_files COUNT
                        # bound above), so a huge bucket could burn the whole --deadline budget.
                        if (
                            deadline_monotonic is not None
                            and time.monotonic() >= deadline_monotonic
                        ):
                            walk_deadline_exceeded = True
                            break
                        try:
                            selected.append(next(iterator))
                        except StopIteration:
                            continue
                        if len(selected) >= limit:
                            break
                        next_buckets.append((key, iterator))
                    if walk_deadline_exceeded:
                        break
                    active_buckets = next_buckets
                if len(selected) >= limit or walk_deadline_exceeded:
                    break
            if walk_deadline_exceeded and deadline_hit is not None:
                deadline_hit.hit = True
            return selected

        walk_deadline_exceeded = False
        for current in _iter_repo_bucket_files(normalized_root, ()):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                walk_deadline_exceeded = True
                break
            files.append(current)
        if walk_deadline_exceeded and deadline_hit is not None:
            deadline_hit.hit = True
        files.sort(key=lambda path: _repo_walk_path_sort_key(path, normalized_root))
        return files


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _looks_like_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            data = handle.read(8192)
    except OSError:
        return False
    # A UTF-16/UTF-32 BOM means TEXT whose encoding legitimately contains NUL bytes (round-8 audit):
    # UTF-16 interleaves a NUL after every ASCII char, so the NUL heuristic below would otherwise
    # misclassify all UTF-16/32 text as binary and make it invisible to every tg command (a real
    # Windows-relevant loss -- PowerShell/redirected output, some editors default to UTF-16).
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data.startswith(b"\x00\x00\xfe\xff"):
        return False
    return b"\0" in data


def _is_hidden_non_code_file(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    hidden_parts = [
        part
        for part in relative.parts[:-1]
        if part.startswith(".") and part.lower() not in {".github"}
    ]
    if not hidden_parts and path.name.startswith("."):
        hidden_parts.append(path.name)
    if not hidden_parts:
        return False
    return path.suffix.lower() not in _CODE_CONTEXT_SUFFIXES


def _is_repo_context_file(path: Path, root: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in _CONTEXT_FILE_SUFFIXES:
        return False
    if suffix in _NON_CODE_HIDDEN_SUFFIXES:
        return False
    if _is_hidden_non_code_file(path, root):
        return False
    return True


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _precomputed_validation_files_for_root(
    root: Path,
    file_paths: list[str | Path] | None,
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[Path] | None:
    """#639 Opus-gate nit 1 (dogfood #1 RESIDUAL): this loop does one filesystem ``Path.resolve()``
    syscall per entry in ``file_paths`` -- on a large repo map's ``related_paths``/``files``+
    ``tests`` list (up to ``DEFAULT_AGENT_REPO_MAP_LIMIT`` entries) this was the dominant,
    entirely UNBOUNDED cost behind ``tg agent ROOT Q --deadline 8`` running ~20s despite the scan
    itself finishing in budget (Windows ``nt._getfinalpathname`` is comparatively expensive per
    call; see ``tests/integration/test_agent_codemap_deadline_scale.py``'s documented finding).
    ``deadline_monotonic``/``deadline_hit`` are optional (default ``None``, fully backward
    compatible with the other call sites of this function that pass no deadline at all) and follow
    the same ``_DeadlineBreakFlag`` readback contract as every other deadline-scoped sibling loop
    in this module: on expiry, stop resolving further entries and return whatever was already
    collected instead of walking the rest of the list regardless.
    """
    if file_paths is None:
        return None
    normalized_root = root.expanduser().resolve()
    selected: list[Path] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        current = Path(str(raw_path)).expanduser()
        if not current.is_absolute():
            current = normalized_root / current
        try:
            resolved = current.resolve()
        except OSError:
            resolved = current.absolute()
        if not _path_is_relative_to(resolved, normalized_root):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        selected.append(resolved)
    selected.sort(key=lambda path: str(path))
    return selected


def _repo_map_validation_file_paths(
    repo_map: dict[str, Any],
    *,
    validation_root: Path | None = None,
) -> list[str | Path] | None:
    if validation_root is not None:
        try:
            map_root = Path(str(repo_map.get("path", "."))).expanduser().resolve()
            resolved_validation_root = validation_root.expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        if map_root.is_file():
            map_root = map_root.parent
        if map_root != resolved_validation_root and not _path_is_relative_to(
            resolved_validation_root,
            map_root,
        ):
            return None
    raw_paths = list(repo_map.get("related_paths", [])) or [
        *list(repo_map.get("files", [])),
        *list(repo_map.get("tests", [])),
    ]
    return [Path(str(current)) for current in raw_paths if str(current or "")]


def _literal_symbol_candidate(path: Path) -> bool:
    return path.suffix.lower() in _SOURCE_FIRST_SUFFIXES


def _file_contains_literal_symbol(path: Path, symbol: str) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if b"\0" in data[:8192]:
        return False
    return symbol.encode("utf-8") in data


# Fix A: caller_scan (build_symbol_callers_from_map) calls _file_may_contain_literal_symbol
# and _file_may_import_symbol_definition on every candidate file, each doing its OWN
# path.read_bytes() -- a doubled full-file read per candidate, profiled at ~90% of wall time on
# a real repo (see AGENTS.md / dogfood notes). Route both through one mtime-aware cached read so
# the bytes are read once per (path, mtime, size) no matter how many callers need them.
#
# Guard 2: _file_may_import_symbol_definition itself is NOT decorated with @_mtime_aware_cache
# (its 2nd arg, definition_files: list[str], is unhashable -- decorating it directly would raise
# TypeError on every call). Only the byte-read is cached, via this dedicated helper.
#
# Guard 4: bound both the entry count (maxsize) and the per-entry size (byte cap) so this
# cache cannot be dominated by one giant generated file sitting in memory. Files above the cap
# bypass the cache entirely and are read directly, mirroring _SYMBOL_LITERAL_SEED_MAX_BYTES.
_SOURCE_READ_CACHE_MAXSIZE = 4096


def _read_source_cached(path_str: str) -> bytes:
    """Read raw file bytes, cached by (mtime_ns, size); see Fix A note above."""
    try:
        size = Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        # Either stat failed (let read_bytes() raise/propagate the real error below) or the
        # file is too large to be worth caching -- read it directly, uncached (Guard 4).
        return Path(path_str).read_bytes()
    return _read_source_cached_bounded(path_str)


@_mtime_aware_cache(maxsize=_SOURCE_READ_CACHE_MAXSIZE)
def _read_source_cached_bounded(path_str: str) -> bytes:
    return Path(path_str).read_bytes()


# PERF increment 1 (parse-product cache, Fable-designed): every JS/TS/Rust symbol/ref/caller
# extractor independently did its own `path.read_text(...)` + `parser.parse(...)` on the SAME
# file -- caller_scan and edit-plan seeding can re-parse one file up to 3x per symbol lookup
# (_js_ts_parser_symbols during repo-map build, _js_ts_references_and_calls during caller_scan,
# _js_ts_import_update_target during edit-plan seeding -- the last one alone profiled at ~26% of
# edit_plan wall time). These two functions share ONE read + ONE parse per (path, mtime, size)
# across all of them via the same @_mtime_aware_cache seam Fix A/B7 already use.
#
# CRITICAL PARITY: _read_source_text_cached uses Path.read_text (universal-newline \r\n -> \n
# translation) -- NOT a decode() of the raw-bytes cache above -- because that translated text is
# exactly what every tree-sitter consumer .encode()'s and parses today. A raw-bytes .decode()
# would leave \r\n intact and shift every node's byte offset on a CRLF file (silent wrong output
# on Windows-authored sources). Do not "simplify" this into a decode of _read_source_cached.
def _read_source_text_cached(path_str: str) -> str:
    """Read file text (universal-newline decoded), cached by (mtime_ns, size).

    Mirrors _read_source_cached's outer/inner split: oversize files bypass the cache entirely
    (Guard 4) and are read directly, uncached, every call.
    """
    try:
        size = Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        return Path(path_str).read_text(encoding="utf-8")
    return _read_source_text_cached_bounded(path_str)


@_mtime_aware_cache(maxsize=_SOURCE_READ_CACHE_MAXSIZE)
def _read_source_text_cached_bounded(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8")


# backlog #57 companion fix (2026-07-09): must stay >= CALLER_SCAN_FILE_CEILING (2000). This
# cache is FIFO (insertion-order eviction, not LRU -- see _mtime_aware_cache above), so if it were
# smaller than the caller-scan ceiling, a single callers/refs/blast-radius pass over a
# >maxsize-eligible-file repo would silently thrash: files parsed early in the scan get evicted
# before later consumers (refs after callers, or a second symbol lookup) can reuse them, quietly
# defeating the "one parse per file, shared across every symbol/ref/caller extractor" guarantee
# this cache exists to provide. 2048 keeps headroom above the 2000 ceiling.
_PARSE_PRODUCT_CACHE_MAXSIZE = 2048


def _parser_for_source_suffix(suffix: str) -> Any | None:
    """Select the tree-sitter parser the same way each consumer picked one inline before this
    fix -- TS suffixes (incl. tsx) get the typescript parser, the rest of the JS/TS suffix set
    gets the javascript parser, Rust gets the rust parser. Returns None for any other suffix (a
    caller-side suffix pre-check, e.g. `path.suffix not in _JS_TS_SUFFIXES`, is expected to have
    already gated the call, matching the pre-fix per-site behavior)."""
    if suffix in _TS_SUFFIXES:
        return _typescript_parser(tsx=suffix == ".tsx")
    if suffix in _JS_TS_SUFFIXES:
        return _javascript_parser()
    if suffix in _RUST_SUFFIXES:
        return _rust_parser()
    if suffix in _JAVA_SUFFIXES:
        return _java_parser()
    return None


def _parse_source_uncached(path_str: str) -> tuple[str, bytes, Any] | None:
    parser = _parser_for_source_suffix(Path(path_str).suffix)
    if parser is None:
        return None
    try:
        source = _read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return None
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    return source, source_bytes, tree


def _parsed_source_and_tree(path_str: str) -> tuple[str, bytes, Any] | None:
    """One tree-sitter parse per (path, mtime, size), shared across every symbol/ref/caller/
    import-update-target extractor that used to parse this file independently.

    Guard-4 mirror (:1134-1150 note above _read_source_cached): oversize files bypass the
    parse-product cache too -- stat-fail or size over _SYMBOL_LITERAL_SEED_MAX_BYTES reads +
    parses UNCACHED every call, via the same outer/inner split as _read_source_cached/_bounded.
    """
    try:
        size = Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        return _parse_source_uncached(path_str)
    return _parsed_source_and_tree_bounded(path_str)


@_mtime_aware_cache(maxsize=_PARSE_PRODUCT_CACHE_MAXSIZE)
def _parsed_source_and_tree_bounded(path_str: str) -> tuple[str, bytes, Any] | None:
    return _parse_source_uncached(path_str)


def _file_may_contain_literal_symbol(path: Path, symbol: str) -> bool:
    normalized_symbol = (symbol or "").strip()
    if not normalized_symbol:
        return True
    try:
        stat = path.stat()
    except OSError:
        return True
    if stat.st_size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        return True
    try:
        data = _read_source_cached(str(path))
    except OSError:
        return True
    if b"\0" in data[:8192]:
        return False
    return normalized_symbol.encode("utf-8") in data


def _file_may_import_symbol_definition(path: Path, definition_files: list[str]) -> bool:
    spec = lang_registry.spec_for_path(path)
    if spec is None:
        return False
    try:
        stat = path.stat()
    except OSError:
        return True
    if stat.st_size > _SYMBOL_LITERAL_SEED_MAX_BYTES:
        return True
    try:
        data = _read_source_cached(str(path))
    except OSError:
        return True
    if b"\0" in data[:8192]:
        return False
    lowered = data.lower()
    if not any(marker in lowered for marker in spec.import_markers):
        return False
    if spec.language_id in ("javascript", "typescript"):
        # PERF increment 1 / Section C (Fable-designed): a naive mirror of the branch below --
        # matching a definition-file alias literally in this file's text -- changes results and
        # drops a pinned caller: _module_aliases_for_path yields the DEFINITION file's own
        # stem/parts, but a barrel re-export consumer (`import { x as y } from "./barrel"`) has
        # neither the target symbol's name nor the definition file's alias anywhere in its text
        # -- see test_js_ts_advanced_resolution.py::
        # test_aliased_re_export_chain_resolves_to_original_definition. The SOUND gate instead
        # checks for >=1 import BINDING from the exact set the expensive checks
        # (_js_ts_file_imports_symbol_from_definition, _js_ts_import_update_target) can ever
        # match through -- named/default/namespace imports -- which a barrel consumer always
        # has (its own `import {...} from "./barrel"` statement), independent of what the
        # definition file is named. This only gates out files with zero real import bindings
        # (an "import"/"from"/"require(" marker hit only inside a comment or string, or an
        # export-only barrel leaf with no import statement of its own).
        return _js_ts_has_import_bindings(str(path))
    for definition_file in definition_files:
        for alias in _module_aliases_for_path(definition_file):
            if alias and alias.encode("utf-8") in lowered:
                return True
    return False


@_mtime_aware_cache(maxsize=_SOURCE_READ_CACHE_MAXSIZE)
def _js_ts_has_import_bindings(path_str: str) -> bool:
    """True iff *path_str* has >=1 real JS/TS import binding (named/default/namespace).

    Backs the sound gate in _file_may_import_symbol_definition above. Fails OPEN (True) on a
    read error, matching the fail-open stat/read-error arms just above it (:1269-70, :1275-76)
    -- an unreadable file must never be silently excluded from the caller/import-graph scan.
    """
    try:
        source = _read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return True
    if any(
        str(binding.get("statement_kind", "import")) == "import"
        for binding in _js_ts_named_import_bindings(source)
    ):
        return True
    if _js_ts_default_import_bindings(source):
        return True
    return bool(_js_ts_namespace_import_bindings(source))


def _literal_symbol_seed_files(
    root: Path,
    symbol: str | None,
    *,
    existing_files: list[Path],
) -> list[Path]:
    normalized_symbol = (symbol or "").strip()
    if not normalized_symbol or root.is_file():
        return []

    normalized_root = root.resolve()
    existing = {str(_safe_resolve(path)) for path in existing_files}
    seed_files: list[Path] = []
    scanned = 0
    for current in _iter_repo_files(normalized_root, max_files=_SYMBOL_LITERAL_SEED_SCAN_LIMIT):
        if scanned >= _SYMBOL_LITERAL_SEED_SCAN_LIMIT:
            break
        scanned += 1
        resolved = _safe_resolve(current)
        if str(resolved) in existing or not _literal_symbol_candidate(resolved):
            continue
        if not _file_contains_literal_symbol(resolved, normalized_symbol):
            continue
        seed_files.append(resolved)
        if len(seed_files) >= _SYMBOL_LITERAL_SEED_MAX_FILES:
            break
    return seed_files


def _repo_map_root_dir(repo_map: dict[str, Any]) -> Path:
    """Directory-safe root derived from ``repo_map['path']``.

    A repo map's own ``path`` field can be a FILE, not a directory -- ``tg defs/source/refs/
    callers/impact/blast-radius <file> <symbol>`` scopes the whole map to that one file (see
    ``build_repo_map``'s ``_envelope(root)`` call, which deliberately stamps the raw, possibly-
    file ``root`` so the OUTPUT accurately echoes back what path was targeted). But any INTERNAL
    consumer that needs a directory to root external tooling in -- an LSP `workspace_root` (which
    ultimately reaches ``subprocess.Popen(cwd=...)`` in ``lsp_external_provider.py``), or a Cargo/
    go.mod discovery walk -- must never receive that file itself: unlike ``Path.is_file()``/
    ``.exists()`` (which pathlib silently degrades to ``False`` for a through-a-file path),
    ``subprocess.Popen(cwd=<file>)`` is a raw OS call with no such guard and crashes with
    ``NotADirectoryError`` (WinError 267 on Windows, ENOTDIR on POSIX) -- the CEO-dogfood-reported
    crash on `tg defs <file> <symbol> --provider lsp/hybrid` (native is unaffected; it never reaches
    this code). Mirrors the pre-existing ``root if root.is_dir() else root.parent`` pattern already
    used by ``build_repo_map`` (``context_root``)."""
    root = Path(str(repo_map["path"])).expanduser().resolve()
    return root if root.is_dir() else root.parent


def _repo_map_file_and_test_universe(repo_map: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    """Same normalization + cross-key dedupe as ``_repo_map_file_universe`` below, but returned
    as SEPARATE (files, tests) lists so a caller can distinguish membership (F1 fix: the
    caller-scan ceiling ordering needs to know which universe members are tests) without
    re-deriving it. ``_repo_map_file_universe`` is a thin wrapper over this and its
    source-first-then-tests concatenation order is UNCHANGED -- existing global-order
    consumers (build_context_pack_from_map, edit-plan seeding) are untouched."""
    base = _repo_map_root_dir(repo_map)
    seen: set[str] = set()
    files: list[Path] = []
    tests: list[Path] = []
    for key, bucket in (("files", files), ("tests", tests)):
        for raw_path in repo_map.get(key, []) or []:
            current = Path(str(raw_path)).expanduser()
            if not current.is_absolute():
                current = base / current
            normalized = os.path.abspath(str(current))
            if normalized in seen:
                continue
            seen.add(normalized)
            bucket.append(Path(normalized))
    return files, tests


def _repo_map_file_universe(repo_map: dict[str, Any]) -> list[Path]:
    files, tests = _repo_map_file_and_test_universe(repo_map)
    return [*files, *tests]


def _interleave_proportionally(sources: list[Path], tests: list[Path]) -> list[Path]:
    """Merge two ordered lists so that ANY prefix of the result carries roughly its
    proportional share of ``tests`` (a Bresenham-style proportional interleave). Used so a
    ceiling slice taken from the FRONT of the merged list never strands 100% of the test
    files behind a long run of source files, no matter how large ``sources`` is relative to
    ``tests``."""
    if not tests:
        return list(sources)
    if not sources:
        return list(tests)
    total = len(sources) + len(tests)
    merged: list[Path] = []
    source_idx = 0
    test_idx = 0
    for position in range(1, total + 1):
        expected_tests_by_now = math.ceil(position * len(tests) / total)
        if test_idx < expected_tests_by_now and test_idx < len(tests):
            merged.append(tests[test_idx])
            test_idx += 1
        elif source_idx < len(sources):
            merged.append(sources[source_idx])
            source_idx += 1
        else:
            merged.append(tests[test_idx])
            test_idx += 1
    return merged


def _order_caller_scan_candidates(
    source_files: list[Path],
    test_files: list[Path],
    *,
    symbol: str | None,
    deadline_monotonic: float | None = None,
) -> list[Path]:
    """F1 fix (dogfood v1.42.0, 24->14 refs regression): ORDER the caller-scan candidate
    universe before ``_cap_caller_scan_files`` applies its ``CALLER_SCAN_FILE_CEILING`` slice.

    ``_repo_map_file_universe`` deliberately keeps a source-first-then-tests order (other
    consumers depend on it -- do not change it globally); left as-is, a >CEILING-source repo
    consumes the ENTIRE ceiling budget on source files and stone-cold strands every test file
    past the window. This function only changes what the CEILING SLICE sees: literal
    symbol-hit files (probed via the already-cached ``_file_may_contain_literal_symbol``, which
    warms the same cache the per-file caller/ref scan reads right after) sort first WITHIN their
    own category (source/test), then ``_interleave_proportionally`` merges the two categories so
    EVERY prefix of the result -- including the eventual ceiling slice -- carries a proportional
    share of test files. Interleaving globally (rather than only after a literal-hits block) is
    the F1-review LOW-MED fix: a source-heavy run of literal hits can no longer consume the
    entire ceiling budget and strand 100% of the test files, hit or not, past the window.

    TRAP (do not regress): literal-contains is an ORDERING signal only, never a FILTER. A
    caller/ref can resolve with NO literal symbol byte match in the referencing file itself --
    e.g. an aliased re-export resolved via ``_js_ts_provider_alias_calls`` -- so every file
    stays eligible for the ceiling slice; only its position changes.

    F1-review HIGH fix (task#52 shape): the literal-hit probe itself must not scale with the
    full file universe -- bounded both by count (``CALLER_SCAN_ORDER_PROBE_CEILING``) and, when
    a deadline is in play, by wall-clock (checked every
    ``_CALLER_SCAN_ORDER_PROBE_DEADLINE_STRIDE`` files). Files past whichever bound is hit first
    are simply never probed (treated as non-hits for ordering purposes) -- they are NEVER
    dropped from the candidate list, only left unprobed, so they still land in the
    source/test-first remainder that ``_interleave_proportionally`` merges below.
    """
    normalized_symbol = (symbol or "").strip()
    if not normalized_symbol:
        return [*source_files, *test_files]

    def _is_literal_hit(path: Path) -> bool:
        return _literal_symbol_candidate(path) and _file_may_contain_literal_symbol(
            path, normalized_symbol
        )

    universe = [*source_files, *test_files]
    literal_hit_ids: set[str] = set()
    for index, current in enumerate(universe):
        if index >= CALLER_SCAN_ORDER_PROBE_CEILING:
            break
        if (
            deadline_monotonic is not None
            and index % _CALLER_SCAN_ORDER_PROBE_DEADLINE_STRIDE == 0
            and time.monotonic() >= deadline_monotonic
        ):
            break
        if _is_literal_hit(current):
            literal_hit_ids.add(str(current))

    ordered_sources = [current for current in source_files if str(current) in literal_hit_ids] + [
        current for current in source_files if str(current) not in literal_hit_ids
    ]
    ordered_tests = [current for current in test_files if str(current) in literal_hit_ids] + [
        current for current in test_files if str(current) not in literal_hit_ids
    ]
    return _interleave_proportionally(ordered_sources, ordered_tests)


def _cap_caller_scan_files(
    files: list[Path],
    *,
    symbol: str | None = None,
    test_files: list[Path] | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[Path], bool]:
    """backlog #1 chokepoint: bound the caller-scan file universe to
    CALLER_SCAN_FILE_CEILING regardless of how large the passed-in repo_map is (the map default
    was raised for routing accuracy; caller-scan latency must not scale with it). Returns
    (capped_files, ceiling_exceeded) so the caller can mark the payload result_incomplete only
    when the ceiling actually dropped files.

    F1 fix: when a slice is actually needed AND the caller passes ``test_files`` (the subset of
    ``files`` classified as tests), ORDER the candidates first via
    ``_order_caller_scan_candidates`` so literal symbol-hit files and a proportional share of
    tests survive the slice instead of being stranded behind a long source-file run (see that
    function's docstring for the full rationale + trap). The ceiling itself (
    ``CALLER_SCAN_FILE_CEILING``, see the module note above) is a hard backstop, not a fixed
    historical relic: backlog #57 (2026-07-09) raised it 512 -> 2000 now that #478's
    --deadline hard-bound closed task #52's ~100s TS-regex hang risk for the interruptible path,
    but a flag-less (--deadline omitted) invocation and a --max-repo-files-raised mega-repo still
    have no other wall-clock bound, so the ceiling remains in force at the new, higher value.
    When no ``test_files`` are passed (or none exist), behavior is unchanged: a plain prefix
    slice of ``files`` in whatever order the caller already supplied.

    F1-review HIGH fix: ``deadline_monotonic`` is threaded through to
    ``_order_caller_scan_candidates`` so the ordering PROBE (not just the later per-file scan
    loop) honors --deadline on a repo raised via --max-repo-files -- see that function's
    docstring.

    CEO #4 parity fix: below the ceiling, this used to return ``files`` UNORDERED whenever a
    ``--deadline`` was supplied -- unlike ``tg importers``' reverse-candidate ordering
    (``_tier_reverse_importer_candidates``, #221), which ALWAYS orders regardless of ceiling. A
    slow-to-parse repo under the 2000-file ceiling can still blow a tight ``--deadline`` in the
    main caller-scan loop below, so a caller/blast-radius scan got NO likely-first protection at
    all in that regime and could strand a late-sorting/test-file caller past the cut -- while an
    equally-truncated ceiling-exceeded scan (the branch below) always orders first. Order here
    too whenever a deadline is actually in play and there are tests to interleave; nothing is
    DROPPED by this branch (``ceiling_exceeded`` stays ``False``), so it is a pure candidate-order
    change, invisible to a scan that completes."""
    if len(files) <= CALLER_SCAN_FILE_CEILING:
        if deadline_monotonic is not None and test_files:
            test_id_set = {str(current) for current in test_files}
            source_only = [current for current in files if str(current) not in test_id_set]
            ordered = _order_caller_scan_candidates(
                source_only, test_files, symbol=symbol, deadline_monotonic=deadline_monotonic
            )
            return ordered, False
        return files, False
    if test_files:
        test_id_set = {str(current) for current in test_files}
        source_only = [current for current in files if str(current) not in test_id_set]
        ordered = _order_caller_scan_candidates(
            source_only, test_files, symbol=symbol, deadline_monotonic=deadline_monotonic
        )
    else:
        ordered = files
    return ordered[:CALLER_SCAN_FILE_CEILING], True


def _is_python_dynamic_import_call(node: ast.Call) -> bool:
    """True when ``node`` is one of the 3 dynamic-import call shapes that a top-level-only
    ``ast.Import``/``ast.ImportFrom`` scan can never see: ``__import__(...)``, bare
    ``import_module(...)`` (from ``from importlib import import_module``), or
    ``importlib.import_module(...)``.  These are ``ast.Call`` EXPRESSIONS that can appear
    anywhere -- inside a function body, a conditional, a try/except -- which is exactly why they
    need a whole-tree ``ast.walk`` (see ``_python_dynamic_import_entry_for_call``, the per-node
    detector built on top of this predicate) instead of the ``tree.body``-only scan below
    (#93 SUB-1 audit finding).
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"__import__", "import_module"}
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    )


def _python_dynamic_import_call_is_relative(node: ast.Call) -> bool:
    """True when a ``__import__(...)`` call is unambiguously or possibly RELATIVE via its
    ``level`` argument (5th positional, or the ``level=`` keyword) -- ``__import__``'s own
    relative-import marker is this integer, separate from ``import_module``'s leading-dot
    module-string convention (the caller checks that with a plain ``.startswith(".")`` on the
    literal module name instead, since ``import_module`` has no ``level`` parameter at all --
    this always returns ``False`` for an ``import_module``/bare-``import_module`` call, harmlessly).

    A non-literal ``level`` value (a variable, an expression) can't be proven to be the safe
    default of ``0``, so it is conservatively treated as relative too -- the same "can't prove
    it's safe" fail-closed posture as every other honesty check in this module (e.g. the
    non-literal-argument case just above, or the #152 sys.path-hack idiom matcher).
    """
    level_arg: ast.expr | None = None
    if len(node.args) >= 5:
        level_arg = node.args[4]
    else:
        for keyword in node.keywords:
            if keyword.arg == "level":
                level_arg = keyword.value
                break
    if level_arg is None:
        return False
    if isinstance(level_arg, ast.Constant) and isinstance(level_arg.value, int):
        return level_arg.value != 0
    return True  # non-literal level -- can't prove it's 0, fail closed (treat as relative)


def _python_dynamic_import_entry_for_call(node: ast.AST) -> dict[str, Any] | None:
    """Given a single AST node, return its dynamic-import entry dict if `node` is one of the 3
    dynamic-import call shapes -- ``__import__(...)``, bare ``import_module(...)``, or
    ``importlib.import_module(...)`` (see `_is_python_dynamic_import_call`) -- else `None`. The
    returned dict is shaped like the static entries `_python_imports_with_lines` emits (`module`,
    `line`, `level`) plus two #93 SUB-1 markers: `dynamic` (always `True` here) and
    `dynamic_unresolved` (`True` when there is no static-string-literal target to resolve at all
    -- the first argument isn't a literal, e.g. a variable or an f-string -- OR when the literal
    names a RELATIVE import this slice deliberately does not attempt to resolve, see below).

    Both `_python_imports_with_lines` (opt10 F4.2) and `_python_imports_and_symbols` (opt10
    lever-1) fold this per-node check into their own single `ast.walk(tree)` pass instead of
    paying for a second whole-tree walk. This used to be the loop body of a separate whole-tree
    helper, `_python_dynamic_import_entries` -- pulled out unchanged (same literal-extraction,
    same relative-literal fail-closed check, same entry shape) so `_python_imports_with_lines`
    could fold it into its existing walk (opt10 F4.2) while `_python_imports_and_symbols` kept
    calling `_python_dynamic_import_entries` wholesale for its own separate walk. Once opt10
    lever-1 migrated that last remaining caller to call this per-node function directly too,
    `_python_dynamic_import_entries` had zero callers left and was removed as dead code -- there
    is no longer a standalone whole-tree dynamic-import walk anywhere in this module.

    Fails CLOSED on the non-literal-argument case: `module` is `""` rather than a guessed name --
    asserting a fabricated edge for an import whose target we can't actually read would be a
    precision regression in a moat feature (see `_resolve_raw_import_entry` /
    `_confirm_import_edges`, which both skip resolution entirely when `dynamic_unresolved` is
    set).

    Also fails CLOSED on a RELATIVE literal -- a leading-dot `import_module(".sibling",
    package=...)` module string, or an `__import__(name, ..., level=N)` call with a nonzero (or
    non-literal, unprovable) `level` (`_python_dynamic_import_call_is_relative` above -- scope
    slice #6, the tractable dynamic-import LITERAL slice). Both forms carry a real literal name,
    kept here (unlike the non-literal case, `module` is NOT blanked to `""` -- nothing is
    fabricated, the literal text is exactly what the source says), but the downstream absolute
    resolver (`_python_module_candidates`) must never see it: its `_python_module_parts` splitter
    drops a leading empty component from `".sibling".split(".")`, so an unguarded relative
    literal would silently be searched for as if it were the ABSOLUTE module "sibling" -- a
    PROVEN false-edge risk (a same-named-but-unrelated top-level file can exist anywhere in the
    search roots) not merely a theoretical one. Resolving the relative form correctly needs a
    second, chained lookup (resolve the `package`/enclosing-package argument to a directory
    FIRST, only then walk it by the relative level) that this slice does not build -- out of
    scope per the "no false edges, missing is fine" contract; a future slice can add it.
    `package` itself is never read here (a non-literal `package` -- the overwhelmingly common
    real-world shape, `package=__name__`/`package=__package__` -- couldn't be resolved statically
    anyway, and even a literal `package` string is left for that future slice), so this is a pure
    detect-and-refuse guard on the `module`/`level` shape alone.
    """
    if not isinstance(node, ast.Call) or not _is_python_dynamic_import_call(node):
        return None
    literal_module: str | None = None
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        literal_module = node.args[0].value
    dynamic_unresolved = literal_module is None
    if literal_module is not None and (
        literal_module.startswith(".") or _python_dynamic_import_call_is_relative(node)
    ):
        dynamic_unresolved = True
    return {
        "module": literal_module or "",
        "line": int(node.lineno),
        "level": 0,
        "dynamic": True,
        "dynamic_unresolved": dynamic_unresolved,
    }


# ---------------------------------------------------------------------------
# Content-addressed, memory-bounded AST parse cache backing _cached_ast_parse below.
#
# Was `@lru_cache(maxsize=2048)` -- an ENTRY-COUNT bound, not a memory bound. 2048 cached ASTs
# of large files is unbounded resident-memory growth in the long-lived warm daemon (external-
# audit finding: an OOM/DoS vector). Replaced with a hand-rolled LRU bounded by TOTAL CACHED
# SOURCE BYTES (`_ast_cache_byte_budget()`, env-configurable, default 64 MiB), evicting
# least-recently-used entries on insert -- same eviction shape as
# `_remember_repo_context`/`_get_repo_context_cache_entry` above, just keyed by cumulative bytes
# instead of entry count.
# ---------------------------------------------------------------------------


class _AstCacheInfo(NamedTuple):
    """Observability snapshot for `_cached_ast_parse`'s cache. `hits`/`misses` match
    `functools.lru_cache.cache_info()`'s field names (this was a plain lru_cache before);
    `evictions`/`current_bytes`/`current_entries` are new for the byte-budgeted design."""

    hits: int
    misses: int
    evictions: int
    current_bytes: int
    current_entries: int


_ast_cache: OrderedDict[str, tuple[ast.Module, int]] = OrderedDict()
_ast_cache_lock = threading.Lock()
_ast_cache_stats: dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0, "current_bytes": 0}


def _cached_ast_parse(source: str) -> ast.Module:
    """Content-addressed AST parse cache. `build_agent_capsule` parses each Python file 2-3x
    across phases -- once in the map-build imports/symbols pass and again in the caller/blast-radius
    consumer scan (and the `tg imports`/`importers` extractors) -- all on the SAME source read the
    same way (`path.read_text(encoding="utf-8")`). Profiling `tg agent` on an 872-file repo (2026-
    07-11) showed ~40% of the wall in `ast.parse` + `ast.walk` over those duplicate parses (1512
    parses for ~783 files).

    Bounded by TOTAL CACHED SOURCE BYTES (`_ast_cache_byte_budget()`, default 64 MiB), not entry
    count -- see the module comment above. A source larger than `_max_parse_bytes()` (or larger
    than the byte budget itself) BYPASSES the cache: it is still fully parsed and a real
    `ast.Module` is returned, it is just never stored. Bypass must never degrade into a skip --
    several of this function's ~9 call sites have no size guard of their own and would silently
    lose symbols/imports/callers if this returned anything less than a fully parsed tree.

    Keying on the source TEXT (not the path) is staleness-free: an edited file has different
    content -> a fresh sha256 key -> a fresh parse, so this stays correct even under the reused
    session-daemon process (no mtime races, and no path-keyed staleness risk -- see the #535
    daemon-staleness regression this design must never reintroduce). Callers only ever READ the
    tree (`tree.body` / `ast.walk` + `isinstance`, no mutation -- audited), so sharing one parsed
    tree across callers is safe. Raises on invalid syntax exactly like `ast.parse` (a syntax-error
    source is never cached -- it simply re-raises + re-parses each time; callers' try/except is
    unchanged).

    Thread safety: the daemon calls this concurrently. The lock is held ONLY around the
    dict/counter mutations, never around `ast.parse()` itself -- holding it there would serialize
    all parsing across every concurrent caller. Two threads racing on the same brand-new source
    may both parse outside the lock; the loser's redundant tree is discarded under the lock and
    counted as a hit (a benign, tolerated double-parse race)."""
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()

    with _ast_cache_lock:
        cached = _ast_cache.get(key)
        if cached is not None:
            _ast_cache.move_to_end(key)
            _ast_cache_stats["hits"] += 1
            return cached[0]

    # Miss: parse OUTSIDE the lock (see docstring -- never serialize concurrent parsing).
    tree = ast.parse(source)
    size = len(source.encode("utf-8"))
    budget = _ast_cache_byte_budget()

    if size > _max_parse_bytes() or size > budget:
        # Per-entry ceiling: too large to cache (or larger than the whole budget). Bypass means
        # "parse, don't store" -- the caller still gets a real, correct ast.Module.
        with _ast_cache_lock:
            _ast_cache_stats["misses"] += 1
        return tree

    with _ast_cache_lock:
        raced = _ast_cache.get(key)
        if raced is not None:
            # Another thread inserted this exact key while we were parsing above.
            _ast_cache.move_to_end(key)
            _ast_cache_stats["hits"] += 1
            return raced[0]

        _ast_cache_stats["misses"] += 1
        while _ast_cache and _ast_cache_stats["current_bytes"] + size > budget:
            _, (_, evicted_size) = _ast_cache.popitem(last=False)
            _ast_cache_stats["current_bytes"] -= evicted_size
            _ast_cache_stats["evictions"] += 1
        _ast_cache[key] = (tree, size)
        _ast_cache_stats["current_bytes"] += size

    return tree


def _ast_cache_info() -> _AstCacheInfo:
    """Return a snapshot of the AST parse cache's counters (see `_AstCacheInfo`)."""
    with _ast_cache_lock:
        return _AstCacheInfo(
            hits=_ast_cache_stats["hits"],
            misses=_ast_cache_stats["misses"],
            evictions=_ast_cache_stats["evictions"],
            current_bytes=_ast_cache_stats["current_bytes"],
            current_entries=len(_ast_cache),
        )


def _ast_cache_clear() -> None:
    """Clear the AST parse cache and reset its counters (mirrors `lru_cache.cache_clear()`)."""
    with _ast_cache_lock:
        _ast_cache.clear()
        _ast_cache_stats["hits"] = 0
        _ast_cache_stats["misses"] = 0
        _ast_cache_stats["evictions"] = 0
        _ast_cache_stats["current_bytes"] = 0


_cached_ast_parse.cache_info = _ast_cache_info  # type: ignore[attr-defined]
_cached_ast_parse.cache_clear = _ast_cache_clear  # type: ignore[attr-defined]


def _python_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix != ".py":
        return [], []

    try:
        tree = _cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    # opt10/lever-1 speed fix: merge the imports / symbols / dynamic-import scans into a SINGLE
    # `ast.walk(tree)` pass instead of three separate whole-tree walks (one for imports, one for
    # symbols, and a third buried inside `_python_dynamic_import_entries`) -- the same
    # single-walk-plus-helper-reuse pattern #716 already shipped for the sibling
    # `_python_imports_with_lines` (see that function's own comment, and its F4.2 test, in
    # tests/unit/test_file_deps.py). `ast.Import`, `ast.ImportFrom`, `ast.ClassDef`,
    # `ast.FunctionDef`/`ast.AsyncFunctionDef`, and `ast.Call` are mutually-exclusive node
    # subclasses, so each node dispatches to at most one branch below -- identical in effect to
    # filtering three separate walks for three disjoint predicates and concatenating the
    # results. The trailing `sorted(dict.fromkeys(imports))` + `symbols.sort(...)` below make
    # append ORDER irrelevant, so interleaving all three kinds of appends into one walk is
    # byte-identical to the old three-walk output. See
    # test_python_imports_and_symbols_merges_all_three_walks_into_one (walk-count + output-
    # identity proof).
    #
    # Nested-scope recall fix (companion to the same change in `_python_imports_with_lines`):
    # `ast.walk` (not `tree.body`) so a plain `import`/`from ... import` STATEMENT nested inside a
    # function body, an `if`/`try` block, or an `if TYPE_CHECKING:` guard feeds this alias-graph
    # list too. This list becomes `repo_map["imports"]` (`build_repo_map`'s per-file entries),
    # which is the ONLY source `_reverse_importers`'s alias PREFILTER reads (see
    # `build_file_importers_from_map`, `build_symbol_callers_from_map`,
    # `build_symbol_blast_radius_from_map`, `build_context_render`'s agent-capsule scoring) --
    # a candidate file whose ONLY import of a target is scope-nested was previously invisible to
    # the prefilter, so it never even reached the reverse `tg importers` CONFIRM step
    # (`_confirm_import_edges`) regardless of that step's own recall. Verified low-risk: this is a
    # strict superset (`ast.walk` visits everything `tree.body` did, plus more), it only ADDS
    # entries (recall-only, never removes/reorders an existing one), and the full relevant test
    # suite (agent/blast-radius/callers/refs/orient/importers, 500+ tests) is green across this
    # change with zero new failures.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                for alias in node.names:
                    imports.append(f"{node.module}.{alias.name}")
            elif node.level:
                # `from . import x` / `from .. import x` -- no dotted module text, only
                # relative dots plus the imported names (which may themselves be sibling
                # submodules, e.g. `from . import helpers` importing `helpers.py`). Recording
                # the bare alias name keeps this import in the reverse-import alias graph
                # (`_reverse_importers`/`_module_aliases_for_path`) so `tg importers` can even
                # PREFILTER a sibling `from . import X` importer -- omitting it here (unlike
                # `_python_imports_with_lines`, which already records it for the forward
                # `tg imports` primitive) was a genuine recall gap, not an intentional
                # exclusion (#74 review fix). The precise per-candidate CONFIRM step
                # (`_python_module_matches_definition`) still disambiguates which file it
                # actually resolves to -- this only widens the prefilter's candidate set.
                for alias in node.names:
                    imports.append(alias.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                _symbol_record(
                    name=node.name,
                    kind="class",
                    file=path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                _symbol_record(
                    name=node.name,
                    kind="function",
                    file=path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        elif isinstance(node, ast.Call):
            # #93 SUB-1: fold in dynamic-import call targets (only the STATICALLY resolvable
            # ones -- an unresolved dynamic import has no literal name to add to this
            # alias-graph prefilter list; see `_python_dynamic_import_entry_for_call`) so a file
            # that ONLY reaches a target dynamically is still discoverable as a candidate by the
            # reverse `tg importers` prefilter (`_reverse_importers`), not just by the forward
            # `tg imports` primitive. Reuses `_python_dynamic_import_entry_for_call` -- the
            # ALREADY-TESTED per-node helper `_python_imports_with_lines` folds into its own
            # single walk too (see that function) -- instead of calling the whole-tree
            # `_python_dynamic_import_entries(tree)` this call site used to call, so the
            # dynamic-import check rides the SAME walk as the import/symbol checks above instead
            # of paying for a separate (third) whole-tree `ast.walk`. This was
            # `_python_dynamic_import_entries`'s last remaining caller -- with it migrated to the
            # per-node helper too, that whole-tree function had zero callers left and was removed
            # as dead code (opt10 lever-1).
            #
            # #703 gate NIT-1 fix: a plain `entry["module"]` truthiness check is NOT actually
            # equivalent to "statically resolvable" -- `_python_dynamic_import_entry_for_call`
            # marks a RELATIVE literal (leading-dot `import_module(".sibling", package=...)`) or
            # an explicit-nonzero-`level` `__import__(...)` as `dynamic_unresolved` too, and
            # unlike the non-literal-argument case, those keep their real literal text in
            # `module` (nothing is fabricated/blanked, see that helper's docstring) rather than
            # blanking it to `""`. So an unresolved-but-non-blank literal like `".sibling"` must
            # not slip into `imports`, which becomes `repo_map["imports"]` -- the alias graph
            # `tg blast-radius`'s reverse SCORING prefilter
            # (`_reverse_import_distances`/`_reverse_importers`) reads. A
            # same-named-but-unrelated top-level file (`_import_alias_candidates` + the
            # substring test in `_import_graph_bonus`) could then fuzzy-match that unresolved
            # literal and be pulled into `affected_files`/`dependent_files` -- even though the
            # precise `tg importers` edge (`_resolve_raw_import_entry` / `_confirm_import_edges`)
            # already excludes it correctly, since THAT path has always skipped resolution
            # whenever `dynamic_unresolved` is set. Requiring `not entry["dynamic_unresolved"]`
            # here makes this prefilter honor the exact same "no false edges, missing is fine"
            # contract the precise resolvers already enforce. Pinned by
            # test_blast_radius_excludes_unresolved_dynamic_literal_fuzzy_match
            # (regression-lock) and test_blast_radius_legitimate_dependent_ranking_pin (proves
            # the legitimate reverse-scoring ranking is unaffected) in
            # tests/unit/test_file_deps.py.
            entry = _python_dynamic_import_entry_for_call(node)
            if entry is not None and entry["module"] and not entry["dynamic_unresolved"]:
                imports.append(str(entry["module"]))

    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


@lru_cache(maxsize=1)
def _javascript_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_javascript.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=2)
def _typescript_parser(*, tsx: bool) -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        return None

    raw_language = (
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(raw_language)
    return tree_sitter.Parser(language)


@lru_cache(maxsize=1)
def _rust_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_rust
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_rust.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=1)
def _java_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_java.language())
    return tree_sitter.Parser(language)


def _symbol_navigation_provenance_for_path(path: str) -> str:
    spec = lang_registry.spec_for_path(path)
    if spec is None:
        return "heuristic"
    if spec.parser_for_path is None:
        return spec.provenance_when_parsed
    return (
        spec.provenance_when_parsed
        if spec.parser_for_path(Path(path)) is not None
        else spec.provenance_when_missing
    )


_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_FALLBACK_SOURCE_SUFFIXES = {
    ".adoc",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".markdown",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_CODE_CONTEXT_SUFFIXES = _SOURCE_FIRST_SUFFIXES | _JS_TS_SUFFIXES | _RUST_SUFFIXES | {".py"}
_CONTEXT_FILE_SUFFIXES = _CODE_CONTEXT_SUFFIXES | _FALLBACK_SOURCE_SUFFIXES
_NON_CODE_HIDDEN_SUFFIXES = {
    ".bak",
    ".bin",
    ".cache",
    ".db",
    ".log",
    ".pid",
    ".sqlite",
    ".tmp",
}


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _symbol_record(
    *,
    name: str,
    kind: str,
    file: Path,
    start_line: int,
    end_line: int | None = None,
) -> dict[str, Any]:
    normalized_end_line = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": kind,
        "file": str(file),
        "line": start_line,
        "start_line": start_line,
        "end_line": normalized_end_line,
    }


def _node_has_ancestor_type(node: Any, ancestor_types: set[str]) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type in ancestor_types:
            return True
        current = getattr(current, "parent", None)
    return False


def _line_span_from_offsets(source: str, start_offset: int, end_offset: int) -> tuple[int, int]:
    start_line = source.count("\n", 0, max(0, start_offset)) + 1
    normalized_end = max(0, end_offset - 1)
    end_line = source.count("\n", 0, normalized_end) + 1
    return start_line, max(start_line, end_line)


def _js_ts_named_import_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<statement_kind>import|export)\s+(?:type\s+)?\{(?P<specifiers>[^}]+)\}\s*from\s*[\"'](?P<module>[^\"']+)[\"']",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
        module_name = match.group("module").strip()
        specifiers = match.group("specifiers")
        statement_kind = match.group("statement_kind").strip()
        for raw_specifier in specifiers.split(","):
            specifier = raw_specifier.strip()
            if not specifier:
                continue
            if " as " in specifier:
                imported, local = (part.strip() for part in specifier.split(" as ", 1))
            else:
                imported = specifier
                local = specifier
            if imported and local:
                bindings.append({
                    "module": module_name,
                    "imported": imported,
                    "local": local,
                    "statement_kind": statement_kind,
                    "start_line": start_line,
                    "end_line": end_line,
                })
    return bindings


def _js_ts_namespace_import_bindings(source: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    pattern = re.compile(
        r"""(?x)
        import\s+\*\s+as\s+(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s+from\s*["'](?P<module>[^"']+)["']
        """
    )
    for match in pattern.finditer(source):
        bindings.append({
            "module": match.group("module").strip(),
            "local": match.group("local").strip(),
        })
    return bindings


def _js_ts_default_import_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"""(?x)
        import
        \s+
        (?!type\b)
        (?P<local>[A-Za-z_][A-Za-z0-9_]*)
        \s*
        (?:,\s*\{[^}]*\})?
        \s+from\s*["'](?P<module>[^"']+)["']
        """,
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
        bindings.append({
            "module": match.group("module").strip(),
            "local": match.group("local").strip(),
            "start_line": start_line,
            "end_line": end_line,
        })
    return bindings


def _normalized_repo_root(repo_root: Path | str | None) -> Path | None:
    if repo_root is None:
        return None
    # Fix B: repo_root is constant across an entire caller_scan, but this is called on every
    # _js_ts_repo_context / _js_ts_resolve_exported_symbol invocation -- route through the
    # process-wide resolve() cache instead of re-walking the filesystem each time.
    return Path(_resolved_path_str(str(Path(str(repo_root)).expanduser())))


def _dedupe_labels(labels: list[str]) -> list[str]:
    ordered: list[str] = []
    for label in labels:
        normalized = str(label).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _parse_js_ts_tsconfig(root: Path) -> dict[str, Any]:
    tsconfig_path = root / "tsconfig.json"
    payload: dict[str, Any] = {
        "exists": False,
        "base_url": None,
        "paths": [],
    }
    if not tsconfig_path.exists():
        return payload

    try:
        parsed = json.loads(tsconfig_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return payload

    compiler_options = parsed.get("compilerOptions", {})
    if not isinstance(compiler_options, dict):
        return payload

    payload["exists"] = True
    base_url = compiler_options.get("baseUrl")
    if isinstance(base_url, str) and base_url.strip():
        payload["base_url"] = str((root / base_url).resolve())

    raw_paths = compiler_options.get("paths", {})
    if isinstance(raw_paths, dict):
        normalized_paths: list[dict[str, Any]] = []
        for pattern, targets in raw_paths.items():
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            if isinstance(targets, str):
                target_list = [targets]
            elif isinstance(targets, list):
                target_list = [str(target) for target in targets if isinstance(target, str)]
            else:
                continue
            if not target_list:
                continue
            normalized_paths.append({"pattern": pattern, "targets": target_list})
        payload["paths"] = normalized_paths
    return payload


def _prime_js_ts_repo_context(root: Path) -> dict[str, Any]:
    normalized_root = root.expanduser().resolve()
    key = str(normalized_root)
    context = {
        "root": key,
        "tsconfig": _parse_js_ts_tsconfig(normalized_root),
        "re_export_cache": {},
    }
    return _remember_repo_context(_JS_TS_REPO_CONTEXTS, key, context)


def _js_ts_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return {
            "root": None,
            "tsconfig": {
                "exists": False,
                "base_url": None,
                "paths": [],
            },
            "re_export_cache": {},
        }
    cached = _get_repo_context_cache_entry(_JS_TS_REPO_CONTEXTS, str(normalized_root))
    if cached is not None:
        return cached
    return _prime_js_ts_repo_context(normalized_root)


def _js_ts_candidate_files(base: Path) -> list[Path]:
    # Fix B: called once per (importer, module) candidate lookup, and the same `base` string
    # recurs across many definition_file iterations in caller_scan -- route every resolve()
    # through the cached helper.
    normalized_base = Path(_resolved_path_str(str(base)))
    candidates: list[Path] = []
    if normalized_base.suffix in _JS_TS_SUFFIXES:
        candidates.append(normalized_base)
    else:
        candidates.extend(
            Path(_resolved_path_str(str(normalized_base.with_suffix(suffix))))
            for suffix in sorted(_JS_TS_SUFFIXES)
        )
        candidates.extend(
            Path(_resolved_path_str(str((normalized_base / "index").with_suffix(suffix))))
            for suffix in sorted(_JS_TS_SUFFIXES)
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        current = str(candidate)
        if current not in seen:
            deduped.append(candidate)
            seen.add(current)
    return deduped


def _expand_js_ts_tsconfig_target(
    module_name: str,
    pattern: str,
    target: str,
) -> str | None:
    if "*" not in pattern:
        return target if module_name == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not module_name.startswith(prefix):
        return None
    if suffix and not module_name.endswith(suffix):
        return None
    token_end = len(module_name) - len(suffix) if suffix else len(module_name)
    token = module_name[len(prefix) : token_end]
    return target.replace("*", token, 1)


def _js_ts_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    if module_name.startswith("."):
        # Fix B: same (importer_path, module_name) pair recurs across many definition_file
        # iterations of caller_scan -- cache the resolve() by string.
        base = Path(_resolved_path_str(str(importer_path.parent / module_name)))
        return {
            "paths": _js_ts_candidate_files(base),
            "provenance": [],
            "confidence": 1.0,
        }

    context = _js_ts_repo_context(repo_root)
    tsconfig = context.get("tsconfig", {})
    base_dir_str = str(
        tsconfig.get("base_url")
        or context.get("root")
        or _resolved_path_str(str(importer_path.parent))
    )
    base_dir = Path(_resolved_path_str(base_dir_str))

    for current in tsconfig.get("paths", []):
        pattern = str(current.get("pattern", ""))
        targets = [str(target) for target in current.get("targets", []) if target]
        for target in targets:
            expanded = _expand_js_ts_tsconfig_target(module_name, pattern, target)
            if expanded is None:
                continue
            return {
                "paths": _js_ts_candidate_files(Path(_resolved_path_str(str(base_dir / expanded)))),
                "provenance": ["tsconfig-path-alias"],
                "confidence": 0.88,
            }

    if tsconfig.get("base_url"):
        return {
            "paths": _js_ts_candidate_files(Path(_resolved_path_str(str(base_dir / module_name)))),
            "provenance": ["tsconfig-base-url"],
            "confidence": 0.76,
        }

    return {"paths": [], "provenance": [], "confidence": 0.0}


def _js_ts_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    candidate_info = _js_ts_module_candidates(importer_path, module_name, repo_root)
    # Fix B: definition_path is constant for the entire outer symbol scan across every
    # candidate/importer pair -- avoid re-resolving it on every call.
    resolved_definition = _resolved_path_str(definition_path)
    if any(str(candidate) == resolved_definition for candidate in candidate_info["paths"]):
        return {
            "matched": True,
            "provenance": list(candidate_info["provenance"]),
            "confidence": float(candidate_info["confidence"] or 1.0),
        }

    if not module_name.startswith(".") and _module_path_matches_definition(
        module_name, definition_path
    ):
        return {
            "matched": True,
            "provenance": ["partial-resolution"],
            "confidence": 0.2,
        }

    return {"matched": False, "provenance": [], "confidence": 0.0}


def _js_ts_symbol_names(path: Path) -> set[str]:
    symbols = _js_ts_parser_symbols(path)
    if not symbols:
        _, symbols = _regex_imports_and_symbols(path)
    return {str(symbol.get("name", "")) for symbol in symbols if symbol.get("name")}


def _js_ts_default_export_name(source: str, path: Path) -> str | None:
    direct_patterns = [
        re.compile(
            r"export\s+default\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        ),
        re.compile(r"export\s+default\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        re.compile(r"export\s+default\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.MULTILINE),
    ]
    for pattern in direct_patterns:
        match = pattern.search(source)
        if not match:
            continue
        candidate = match.group(1).strip()
        if candidate in _js_ts_symbol_names(path):
            return candidate
    return None


def _js_ts_resolve_exported_symbol(
    module_path: Path,
    exported_name: str,
    repo_root: Path | str | None = None,
    *,
    _depth: int = 0,
    _visited: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    normalized_root = _normalized_repo_root(repo_root)
    # Fix B: this resolve() runs BEFORE the re_export_cache lookup below, so an uncached
    # resolve() here defeats that cache's purpose on repeat calls for the same module path.
    normalized_module = Path(_resolved_path_str(str(module_path.expanduser())))
    if normalized_module.suffix not in _JS_TS_SUFFIXES:
        return None

    context = _js_ts_repo_context(normalized_root)
    cache_key = (str(normalized_module), exported_name)
    cached = context["re_export_cache"].get(cache_key)
    if cached is not None:
        return dict(cached) if isinstance(cached, dict) else None

    visited = set() if _visited is None else set(_visited)
    if _depth >= 5 or cache_key in visited:
        context["re_export_cache"][cache_key] = None
        return None
    visited.add(cache_key)

    try:
        source = normalized_module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        context["re_export_cache"][cache_key] = None
        return None

    if exported_name == "default":
        direct_default = _js_ts_default_export_name(source, normalized_module)
        if direct_default:
            result = {
                "symbol": direct_default,
                "definition_file": str(normalized_module),
                "provenance": ["default-import"],
                "confidence": 0.95,
            }
            context["re_export_cache"][cache_key] = dict(result)
            return result
    elif exported_name in _js_ts_symbol_names(normalized_module):
        result = {
            "symbol": exported_name,
            "definition_file": str(normalized_module),
            "provenance": [],
            "confidence": 0.95,
        }
        context["re_export_cache"][cache_key] = dict(result)
        return result

    for binding in _js_ts_named_import_bindings(source):
        if (
            str(binding.get("statement_kind", "")) != "export"
            or str(binding.get("local", "")) != exported_name
        ):
            continue
        candidate_info = _js_ts_module_candidates(
            normalized_module,
            str(binding.get("module", "")),
            normalized_root,
        )
        for candidate in candidate_info["paths"]:
            nested = _js_ts_resolve_exported_symbol(
                candidate,
                str(binding.get("imported", "")),
                normalized_root,
                _depth=_depth + 1,
                _visited=visited,
            )
            if nested is None:
                continue
            provenance = _dedupe_labels([
                *list(candidate_info.get("provenance", [])),
                *list(nested.get("provenance", [])),
                "re-export-chain",
            ])
            confidence = float(nested.get("confidence", 0.2))
            if float(candidate_info.get("confidence", 0.0)) > 0.0:
                confidence = min(confidence, float(candidate_info["confidence"]))
            result = {
                "symbol": str(nested.get("symbol", exported_name)),
                "definition_file": str(nested.get("definition_file", normalized_module)),
                "provenance": provenance,
                "confidence": round(confidence, 3),
            }
            context["re_export_cache"][cache_key] = dict(result)
            return result

    context["re_export_cache"][cache_key] = None
    return None


def _js_ts_resolve_imported_symbol(
    importer_path: Path,
    module_name: str,
    imported_name: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    candidate_info = _js_ts_module_candidates(importer_path, module_name, repo_root)
    for candidate in candidate_info["paths"]:
        resolved = _js_ts_resolve_exported_symbol(candidate, imported_name, repo_root)
        if resolved is None:
            continue
        provenance = _dedupe_labels([
            *list(candidate_info.get("provenance", [])),
            *list(resolved.get("provenance", [])),
        ])
        confidence = float(resolved.get("confidence", 0.2))
        if float(candidate_info.get("confidence", 0.0)) > 0.0:
            confidence = min(confidence, float(candidate_info["confidence"]))
        return {
            "symbol": str(resolved.get("symbol", imported_name)),
            "definition_file": str(resolved.get("definition_file", candidate)),
            "provenance": provenance,
            "confidence": round(confidence, 3),
        }
    return None


def _js_ts_import_match_details(
    importer_path: Path,
    *,
    module_name: str,
    imported_name: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    is_default: bool = False,
) -> dict[str, Any] | None:
    # Fix B: definition_path is constant across every binding/candidate iteration of the outer
    # caller_scan any()-loop -- avoid re-resolving it per call.
    resolved_definition = _resolved_path_str(definition_path)
    resolved = _js_ts_resolve_imported_symbol(
        importer_path,
        module_name,
        "default" if is_default else imported_name,
        repo_root,
    )
    if resolved is not None:
        if (
            str(resolved.get("definition_file")) == resolved_definition
            and str(resolved.get("symbol")) == symbol
        ):
            return {
                "provenance": list(resolved.get("provenance", [])),
                "confidence": float(resolved.get("confidence", 0.95)),
            }
        return None

    if is_default:
        return None

    details = _js_ts_module_match_details(importer_path, module_name, definition_path, repo_root)
    if details["matched"] and imported_name == symbol:
        return {
            "provenance": list(details.get("provenance", [])),
            "confidence": float(details.get("confidence", 0.95)),
        }
    return None


def _split_top_level_list(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    brace_depth = 0
    for char in text:
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        if char == "," and brace_depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


_RUST_USE_SEGMENT_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|\*)$")


def _is_valid_rust_use_path(path: str) -> bool:
    """Return ``True`` when ``path`` is a syntactically valid Rust ``use`` path.

    The ``use ... ;`` regex used to collect bindings is intentionally permissive
    (``DOTALL``) and can latch onto the word ``use`` inside a doc comment such as
    ``/// We only use the trigram index ... ;``. Those false positives produce
    module names containing whitespace/newlines that later blow up
    ``Path.with_suffix`` with ``empty name`` ``ValueError``s. A genuine use path is
    a ``::``-separated list of Rust identifiers (plus ``*`` for globs), so reject
    anything else here.
    """

    candidate = path.strip()
    if not candidate:
        return False
    segments = candidate.split("::")
    return all(_RUST_USE_SEGMENT_RE.match(segment.strip()) for segment in segments)


def _flatten_rust_use_items(expression: str, prefix: str = "") -> list[str]:
    normalized = expression.strip()
    if not normalized:
        return []

    brace_index = normalized.find("{")
    if brace_index < 0:
        return [f"{prefix}::{normalized}".strip(":") if prefix else normalized]

    prefix_part = normalized[:brace_index].rstrip(":").strip()
    combined_prefix = prefix
    if prefix_part:
        combined_prefix = f"{prefix}::{prefix_part}".strip(":") if prefix else prefix_part

    closing_index = normalized.rfind("}")
    if closing_index < 0:
        return [combined_prefix] if combined_prefix else []
    inner = normalized[brace_index + 1 : closing_index]

    flattened: list[str] = []
    for item in _split_top_level_list(inner):
        if item == "self":
            if combined_prefix:
                flattened.append(combined_prefix)
            continue
        if "{" in item:
            flattened.extend(_flatten_rust_use_items(item, combined_prefix))
            continue
        flattened.append(f"{combined_prefix}::{item}".strip(":") if combined_prefix else item)
    return flattened


def _rust_use_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:pub\s+)?use\s+([^;]+);", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(source):
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
        for item in _flatten_rust_use_items(match.group(1)):
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.endswith("::*"):
                module_glob = normalized[:-3].strip()
                if not _is_valid_rust_use_path(module_glob):
                    continue
                bindings.append({
                    "module": module_glob,
                    "wildcard": True,
                    "start_line": start_line,
                    "end_line": end_line,
                })
                continue

            if " as " in normalized:
                imported_path, local_name = (part.strip() for part in normalized.rsplit(" as ", 1))
            else:
                imported_path = normalized
                local_name = normalized.rsplit("::", 1)[-1].strip()

            # Reject false-positive matches (e.g. the word ``use`` inside a doc
            # comment) so downstream path resolution never receives whitespace.
            if not _is_valid_rust_use_path(imported_path):
                continue

            if "::" in imported_path:
                module_name, imported_name = imported_path.rsplit("::", 1)
            else:
                module_name = ""
                imported_name = imported_path

            bindings.append({
                "module": module_name.strip(),
                "imported": imported_name.strip(),
                "local": local_name.strip(),
                "path": imported_path.strip(),
                "wildcard": False,
                "start_line": start_line,
                "end_line": end_line,
            })
    return bindings


def _rust_mod_declarations(source: str) -> list[str]:
    pattern = re.compile(
        r"^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$",
        re.MULTILINE,
    )
    return [match.group(1).strip() for match in pattern.finditer(source)]


def _rust_module_base_dir(module_file: Path) -> Path:
    if module_file.name in {"lib.rs", "main.rs", "mod.rs"}:
        return module_file.parent.resolve()
    return (module_file.parent / module_file.stem).resolve()


def _rust_module_file_for_declaration(module_file: Path, module_name: str) -> Path | None:
    base_dir = _rust_module_base_dir(module_file)
    candidates = [
        (base_dir / f"{module_name}.rs").resolve(),
        (base_dir / module_name / "mod.rs").resolve(),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _build_rust_module_tree(
    entry_path: Path,
    *,
    _prefix: tuple[str, ...] = (),
    _visited: set[str] | None = None,
) -> dict[str, str]:
    normalized_entry = entry_path.expanduser().resolve()
    visited = set() if _visited is None else set(_visited)
    current_key = str(normalized_entry)
    if current_key in visited:
        return {}
    visited.add(current_key)

    try:
        source = normalized_entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    module_tree: dict[str, str] = {}
    for module_name in _rust_mod_declarations(source):
        module_file = _rust_module_file_for_declaration(normalized_entry, module_name)
        if module_file is None:
            continue
        module_parts = (*_prefix, module_name)
        module_tree["::".join(module_parts)] = str(module_file)
        module_tree.update(
            _build_rust_module_tree(
                module_file,
                _prefix=module_parts,
                _visited=visited,
            )
        )
    return module_tree


def _normalize_rust_crate_name(name: str) -> str:
    return str(name).strip().replace("-", "_")


def _parse_rust_workspace_members(root: Path) -> dict[str, Any]:
    cargo_toml = root / "Cargo.toml"
    payload: dict[str, Any] = {
        "exists": False,
        "members": {},
    }
    if not cargo_toml.is_file():
        return payload

    try:
        parsed = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return payload

    workspace = parsed.get("workspace", {})
    if not isinstance(workspace, dict):
        return payload

    raw_members = workspace.get("members", [])
    member_patterns = [raw_members] if isinstance(raw_members, str) else raw_members
    if not isinstance(member_patterns, list):
        return payload

    members: dict[str, str] = {}
    for member_pattern in member_patterns:
        if not isinstance(member_pattern, str) or not member_pattern.strip():
            continue
        has_glob = any(token in member_pattern for token in {"*", "?", "["})
        member_dirs = (
            [candidate for candidate in root.glob(member_pattern) if candidate.is_dir()]
            if has_glob
            else [root / member_pattern]
        )
        for member_dir in member_dirs:
            normalized_dir = member_dir.expanduser().resolve()
            member_cargo = normalized_dir / "Cargo.toml"
            if not member_cargo.is_file():
                continue
            crate_name = _normalize_rust_crate_name(normalized_dir.name)
            try:
                member_parsed = tomllib.loads(member_cargo.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                member_parsed = {}
            package = member_parsed.get("package", {})
            if isinstance(package, dict):
                package_name = package.get("name")
                if isinstance(package_name, str) and package_name.strip():
                    crate_name = _normalize_rust_crate_name(package_name)
            entry_path = (normalized_dir / "src" / "lib.rs").resolve()
            if not entry_path.is_file():
                fallback_entry = (normalized_dir / "src" / "main.rs").resolve()
                if fallback_entry.is_file():
                    entry_path = fallback_entry
                else:
                    continue
            members[crate_name] = str(entry_path)
    if members:
        payload["exists"] = True
        payload["members"] = members
    return payload


def _prime_rust_repo_context(root: Path) -> dict[str, Any]:
    normalized_root = root.expanduser().resolve()
    key = str(normalized_root)
    context = {
        "root": key,
        "workspace": _parse_rust_workspace_members(normalized_root),
        "mod_tree_cache": {},
    }
    return _remember_repo_context(_RUST_REPO_CONTEXTS, key, context)


def _rust_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return {
            "root": None,
            "workspace": {
                "exists": False,
                "members": {},
            },
            "mod_tree_cache": {},
        }
    cached = _get_repo_context_cache_entry(_RUST_REPO_CONTEXTS, str(normalized_root))
    if cached is not None:
        return cached
    return _prime_rust_repo_context(normalized_root)


def _rust_crate_entry_for_path(path: Path) -> Path | None:
    normalized_path = path.expanduser().resolve()
    candidates = [normalized_path.parent, *normalized_path.parents]
    src_root = next((parent for parent in candidates if parent.name == "src"), None)
    if src_root is None:
        return None
    lib_path = (src_root / "lib.rs").resolve()
    if lib_path.is_file():
        return lib_path
    main_path = (src_root / "main.rs").resolve()
    if main_path.is_file():
        return main_path
    if normalized_path.name in {"lib.rs", "main.rs"} and normalized_path.parent == src_root:
        return normalized_path
    return None


def _rust_module_tree_for_entry(
    entry_path: Path,
    repo_root: Path | str | None = None,
) -> dict[str, str]:
    normalized_entry = entry_path.expanduser().resolve()
    context = _rust_repo_context(repo_root if repo_root is not None else normalized_entry.parent)
    cache_key = str(normalized_entry)
    cached = context["mod_tree_cache"].get(cache_key)
    if cached is not None:
        return dict(cached)
    module_tree = _build_rust_module_tree(normalized_entry)
    context["mod_tree_cache"][cache_key] = dict(module_tree)
    return module_tree


def _rust_workspace_entry_for_crate(
    crate_name: str,
    repo_root: Path | str | None = None,
) -> Path | None:
    context = _rust_repo_context(repo_root)
    members = context.get("workspace", {}).get("members", {})
    member_path = members.get(_normalize_rust_crate_name(crate_name))
    return Path(str(member_path)).expanduser().resolve() if member_path else None


def _rust_module_tree_lookup(
    entry_path: Path,
    module_parts: list[str],
    repo_root: Path | str | None = None,
) -> Path | None:
    if not module_parts:
        return entry_path.expanduser().resolve()
    module_tree = _rust_module_tree_for_entry(entry_path, repo_root)
    resolved = module_tree.get("::".join(module_parts))
    return Path(resolved).expanduser().resolve() if resolved else None


def _rust_partial_candidate_paths(
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return []

    normalized_definition = Path(definition_path).expanduser().resolve()
    inferred_candidates: list[dict[str, Any]] = []
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not module_parts or module_parts[0] in {"crate", "self", "super"}:
        return inferred_candidates

    external_entry = (normalized_root / module_parts[0] / "src" / "lib.rs").resolve()
    if not external_entry.is_file():
        fallback_entry = (normalized_root / module_parts[0] / "src" / "main.rs").resolve()
        if fallback_entry.is_file():
            external_entry = fallback_entry
        else:
            return inferred_candidates

    candidate_path = _rust_module_tree_lookup(external_entry, module_parts[1:], normalized_root)
    if candidate_path is None and not module_parts[1:]:
        candidate_path = external_entry
    if candidate_path is not None and candidate_path == normalized_definition:
        provenance = ["partial-resolution"]
        if module_parts[1:]:
            provenance.append("mod-declaration")
        inferred_candidates.append({
            "path": str(candidate_path),
            "provenance": provenance,
            "confidence": 0.2,
        })
    return inferred_candidates


def _rust_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not parts:
        return []

    normalized_root = _normalized_repo_root(repo_root)
    normalized_importer = importer_path.expanduser().resolve()
    candidates: list[dict[str, Any]] = []

    def _add_candidate(path: Path | None, provenance: list[str], confidence: float) -> None:
        if path is None:
            return
        resolved_path = str(path.expanduser().resolve())
        if any(str(current["path"]) == resolved_path for current in candidates):
            return
        candidates.append({
            "path": resolved_path,
            "provenance": list(provenance),
            "confidence": float(confidence),
        })

    crate_entry = _rust_crate_entry_for_path(normalized_importer)
    if parts[0] == "crate" and crate_entry is not None:
        _add_candidate(
            _rust_module_tree_lookup(crate_entry, parts[1:], normalized_root),
            ["mod-declaration"] if parts[1:] else [],
            0.95,
        )
    elif normalized_root is not None:
        workspace_entry = _rust_workspace_entry_for_crate(parts[0], normalized_root)
        if workspace_entry is not None:
            _add_candidate(
                _rust_module_tree_lookup(workspace_entry, parts[1:], normalized_root),
                ["workspace-crate", *(["mod-declaration"] if parts[1:] else [])],
                0.92,
            )

    start = normalized_importer.parent
    while start.name == "":
        start = start.parent

    heuristic_parts = list(parts)
    if heuristic_parts[0] == "crate":
        crate_root = next(
            (
                parent
                for parent in [normalized_importer.parent, *normalized_importer.parents]
                if parent.name == "src"
            ),
            None,
        )
        if crate_root is not None:
            start = crate_root.resolve()
        heuristic_parts = heuristic_parts[1:]
    else:
        while heuristic_parts and heuristic_parts[0] == "super":
            start = start.parent
            heuristic_parts = heuristic_parts[1:]
        if heuristic_parts and heuristic_parts[0] == "self":
            heuristic_parts = heuristic_parts[1:]

    if heuristic_parts:
        base = start.joinpath(*heuristic_parts).resolve()
        # Defensive guard: a malformed module name (e.g. a mis-parsed doc
        # comment) can yield a base path whose final component has an empty
        # name, which makes ``with_suffix`` raise ``ValueError``. Skip the
        # ``.rs`` sibling in that case rather than crashing symbol lookup.
        try:
            rust_sibling = base.with_suffix(".rs")
        except ValueError:
            rust_sibling = None
        _add_candidate(rust_sibling, [], 1.0)
        _add_candidate(base / "mod.rs", [], 1.0)

    return candidates


def _rust_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    resolved_definition = str(Path(definition_path).expanduser().resolve())
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 1.0)),
            }

    for candidate in _rust_partial_candidate_paths(module_name, definition_path, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 0.2)),
            }

    if module_parts and module_parts[0] in {"crate", "self", "super"}:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if module_parts and _rust_workspace_entry_for_crate(module_parts[0], repo_root) is not None:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if _module_path_matches_definition(module_name, definition_path):
        return {
            "matched": True,
            "provenance": ["partial-resolution"],
            "confidence": 0.2,
        }
    return {"matched": False, "provenance": [], "confidence": 0.0}


def _rust_use_binding_match_details(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    definition_stem = Path(definition_path).with_suffix("").name.lower()
    if not (
        bool(binding.get("wildcard"))
        or imported_name.lower() == symbol.lower()
        or local_name.lower() == symbol.lower()
        or imported_name.lower() == definition_stem
    ):
        return None

    for module_name in [str(binding.get("module", "")), str(binding.get("path", ""))]:
        if not module_name:
            continue
        details = _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )
        if details["matched"]:
            return {
                "provenance": list(details.get("provenance", [])),
                "confidence": float(details.get("confidence", 1.0)),
            }
    return None


def _extract_rust_impl_block(source: str, start_index: int) -> str:
    depth = 0
    block_start = -1
    for index in range(start_index, len(source)):
        current = source[index]
        if current == "{":
            if depth == 0:
                block_start = index + 1
            depth += 1
        elif current == "}":
            if depth == 0:
                break
            depth -= 1
            if depth == 0 and block_start >= 0:
                return source[block_start:index]
    return ""


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_impl_method_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    path = Path(definition_path)
    if path.suffix not in _RUST_SUFFIXES:
        return ()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()

    impl_pattern = re.compile(
        rf"\bimpl(?:\s*<[^{{>]*>)?\s+{re.escape(symbol)}(?:\s*<[^{{>]*>)?\s*\{{",
        re.MULTILINE,
    )
    method_pattern = re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)")
    candidates: list[str] = []
    for match in impl_pattern.finditer(source):
        block = _extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        candidates.extend(method_match.group(1) for method_match in method_pattern.finditer(block))
    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_impl_owner_type(definition_path: str, line_number: int) -> str | None:
    path = Path(definition_path)
    if path.suffix not in _RUST_SUFFIXES:
        return None
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    impl_pattern = re.compile(
        r"\bimpl(?:\s*<[^{}>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^{}>]*>)?\s*\{",
        re.MULTILINE,
    )
    for match in impl_pattern.finditer(source):
        start_line = source.count("\n", 0, match.start()) + 1
        block = _extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        end_index = match.end() - 1 + len(block) + 1
        end_line = source.count("\n", 0, end_index) + 1
        if start_line <= line_number <= end_line:
            return match.group(1)
    return None


@_mtime_aware_cache(maxsize=512)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_symbol_reference_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    candidates = [symbol]
    candidates.extend(_rust_impl_method_candidates(definition_path, symbol))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _rust_file_references_symbol_from_definition(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> bool:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    reference_candidates = _rust_symbol_reference_candidates(definition_path, symbol)
    if not reference_candidates:
        return False

    for candidate in reference_candidates:
        if re.search(rf"\b{re.escape(candidate)}\b", source):
            return True
        if re.search(rf"(?:\.|::){re.escape(candidate)}\s*\(", source):
            return True
    return False


def _rust_resolve_use_binding(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    wildcard = bool(binding.get("wildcard"))

    module_names = [str(binding.get("module", "")), str(binding.get("path", ""))]
    for module_name in module_names:
        if not module_name:
            continue
        for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
            if not str(candidate.get("path")):
                continue
            candidate_path = Path(str(candidate["path"]))
            if (
                wildcard
                or imported_name.lower() == symbol.lower()
                or local_name.lower() == symbol.lower()
            ):
                return {
                    "symbol": symbol,
                    "definition_file": str(candidate_path),
                    "provenance": list(candidate.get("provenance", [])),
                    "confidence": float(candidate.get("confidence", 1.0)),
                }
            try:
                candidate_source = candidate_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for nested_binding in _rust_use_bindings(candidate_source):
                nested_imported = str(nested_binding.get("imported", ""))
                nested_local = str(nested_binding.get("local", ""))
                if imported_name.lower() not in {nested_imported.lower(), nested_local.lower()}:
                    continue
                nested_resolved = _rust_resolve_use_binding(
                    candidate_path, nested_binding, symbol, repo_root
                )
                if nested_resolved is None:
                    continue
                return {
                    "symbol": symbol,
                    "definition_file": str(nested_resolved.get("definition_file", candidate_path)),
                    "provenance": list(candidate.get("provenance", []))
                    + list(nested_resolved.get("provenance", [])),
                    "confidence": min(
                        float(candidate.get("confidence", 1.0)),
                        float(nested_resolved.get("confidence", 1.0)),
                    ),
                }
    return None


def _definition_module_parts(path: str) -> list[str]:
    # audit #81 #11: case-folding every path segment made `Foo.py` match `foo.py` on a
    # case-SENSITIVE filesystem (Linux CI/prod) -> wrong-file attribution in reverse-import
    # edges. Gate the fold on Windows only, mirroring `_definition_file_dedupe_key` (below),
    # which already conditions its case-handling on `os.name == "nt"` for the same reason.
    raw_parts = [part for part in Path(path).with_suffix("").parts if part]
    if os.name == "nt":
        raw_parts = [part.lower() for part in raw_parts]
    parts = [part for part in raw_parts if part not in {".", ".."} and not part.endswith(":\\")]
    if not parts:
        return []
    # The __init__/index/mod magic-name check stays case-insensitive on every platform: these
    # are real on-disk filenames that are always already lowercase by language convention
    # (CPython only ever treats a literal `__init__.py` as a package initializer), so comparing
    # case-insensitively here does not reintroduce the false-edge risk the fold above removed.
    if parts[-1].lower() in {"__init__", "index", "mod"} and len(parts) > 1:
        parts = parts[:-1]
    return parts


def _normalized_module_parts(module_name: str) -> list[str]:
    # audit #81 #11: see _definition_module_parts above -- same Windows-only case-fold gate.
    raw_parts = [part for part in re.split(r"[^A-Za-z0-9_]+", module_name) if part]
    parts = [part.lower() for part in raw_parts] if os.name == "nt" else raw_parts
    while parts and parts[0] in {"crate", "self", "super"}:
        parts = parts[1:]
    return parts


def _module_path_matches_definition(module_name: str, definition_path: str) -> bool:
    module_parts = _normalized_module_parts(module_name)
    definition_parts = _definition_module_parts(definition_path)
    if not module_parts or not definition_parts:
        return False
    return definition_parts[-len(module_parts) :] == module_parts


def _js_ts_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    return bool(
        _js_ts_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )["matched"]
    )


def _rust_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    return bool(
        _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )["matched"]
    )


# Fix A / Guard 1: this scans+parses the importer file (ast.parse for Python, regex/AST for
# JS/TS/Rust) once PER (file, definition) PAIR, and caller_scan calls it in an any() loop over
# every definition_file for every candidate file -- N definitions means N re-reads/re-parses of
# the same file. @_mtime_aware_cache requires a str first-positional arg (its wrapper keys the
# cache on path_str directly), so the parameter is `path_str: str` here, not `Path` -- callers
# that still pass a Path work at runtime (Path(path_str) below accepts either), but the one hot
# call site (build_symbol_callers_from_map's _should_scan_for_symbol_callers) is updated to pass
# str(current) explicitly so the cache key is a plain, consistently-typed string.
#
# Guard 4: bound the cache's entry count -- this key includes symbol+definition_path+repo_root,
# so it can grow faster than a plain per-file cache; keep it generous but finite.
def _python_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    try:
        tree = _cached_ast_parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                _module_path_matches_definition(alias.name, definition_path) for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if not _module_path_matches_definition(node.module, definition_path):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return True
            elif node.level:
                # `from . import helpers` / `from .. import helpers` -- no dotted `node.module`
                # text, only relative dots plus the imported name(s). This bare form BINDS THE
                # SUBMODULE ITSELF (like `ast.Import`, not a `from X import symbol` name
                # binding), so match on module path alone -- mirrors #460's fix for the forward
                # `_python_imports_and_symbols`/`_python_imports_with_lines` extractors (the `tg
                # imports`/`tg importers` primitive), applied here to the callers/blast-radius
                # consumer path, which was still silently dropping this shape (audit #81 #3): the
                # old `if not node.module: continue` guard skipped every bare relative import, so
                # a sibling `from . import helpers` consumer was invisible to `tg callers`/`tg
                # blast-radius` even though `tg importers` already found it.
                if any(
                    _module_path_matches_definition(alias.name, definition_path)
                    for alias in node.names
                ):
                    return True
    return False


def _js_ts_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    bindings = _js_ts_named_import_bindings(source)
    default_bindings = _js_ts_default_import_bindings(source)
    namespace_bindings = _js_ts_namespace_import_bindings(source)
    return (
        any(
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding["module"]),
                imported_name=str(binding["imported"]),
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
            )
            is not None
            for binding in bindings
            if str(binding.get("statement_kind", "import")) == "import"
        )
        or any(
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding["module"]),
                imported_name="default",
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
                is_default=True,
            )
            is not None
            for binding in default_bindings
        )
        or any(
            _js_ts_module_matches_definition(
                file_path,
                binding["module"],
                definition_path,
                repo_root,
            )
            for binding in namespace_bindings
        )
    )


def _rust_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    bindings = _rust_use_bindings(source)
    if any(
        _rust_use_binding_match_details(
            file_path,
            binding,
            symbol,
            definition_path,
            repo_root,
        )
        is not None
        for binding in bindings
    ):
        return True
    if _is_test_file(file_path):
        return _rust_file_references_symbol_from_definition(file_path, symbol, definition_path)
    return False


@_mtime_aware_cache(maxsize=2048)  # Fix A: mtime+size in key; replaces per-call read+parse
def _file_imports_symbol_from_definition(
    path_str: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    file_path = Path(path_str)
    try:
        source = _read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return False
    spec = lang_registry.spec_for_path(file_path)
    if spec is None or spec.file_imports_symbol_from_definition is None:
        return False
    return spec.file_imports_symbol_from_definition(
        file_path, source, symbol, definition_path, repo_root
    )


def _import_names_reference_symbol_definition(
    import_names: list[str],
    *,
    importer_path: Path,
    symbol: str,
    definition_path: str,
) -> bool:
    normalized_symbol = symbol.strip()
    if not normalized_symbol:
        return False

    for raw_import_name in import_names:
        import_name = str(raw_import_name).strip()
        if not import_name:
            continue
        variants = [import_name]
        rust_like = import_name.replace("::", ".")
        if rust_like != import_name:
            variants.append(rust_like)

        for candidate in variants:
            if candidate.endswith(f".{normalized_symbol}") or candidate.endswith(".*"):
                module_name = candidate.rsplit(".", 1)[0]
                if _module_path_matches_definition(module_name, definition_path):
                    return True
            if importer_path.suffix == ".py" and _module_path_matches_definition(
                candidate,
                definition_path,
            ):
                return True
    return False


def _direct_validation_import_count_from_repo_map(
    *,
    tests: list[str],
    imports_by_file: dict[str, list[str]],
    symbol: str,
    definition_path: str,
) -> int:
    count = 0
    for test_path in tests[:_DIRECT_VALIDATION_TEST_SCAN_LIMIT]:
        test_file = Path(str(test_path))
        if _import_names_reference_symbol_definition(
            imports_by_file.get(str(test_path), []),
            importer_path=test_file,
            symbol=symbol,
            definition_path=definition_path,
        ):
            count += 1
    return count


def _python_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = _cached_ast_parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_path_matches_definition(alias.name, definition_path):
                    return {
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "module": alias.name,
                        "provenance": "parser-backed",
                    }
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if not _module_path_matches_definition(node.module, definition_path):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return {
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "module": node.module,
                        "provenance": "parser-backed",
                    }
            elif node.level:
                # Mirror the sibling fix in _python_file_imports_symbol_from_definition above
                # (audit #81 #3): `from . import helpers` has no dotted `node.module`, only
                # relative dots + the imported name(s), which bind the SUBMODULE itself.
                for alias in node.names:
                    if _module_path_matches_definition(alias.name, definition_path):
                        return {
                            "start_line": int(node.lineno),
                            "end_line": int(getattr(node, "end_lineno", node.lineno)),
                            "module": alias.name,
                            "provenance": "parser-backed",
                        }
    return None


def _js_ts_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = _read_source_text_cached(str(file_path))
    except (OSError, UnicodeDecodeError):
        return None

    # PERF increment 1 / read site 5 (Fable-designed, the "surprise 5th" site): this used to
    # re-read + re-parse the file on every (file, symbol, definition) pair -- edit-plan seeding
    # and _build_import_graph_consumers_from_map call it once per definition_file, profiled at
    # ~26% of edit_plan wall time. Share the parse product with every other JS/TS extractor via
    # the same (path, mtime, size)-keyed cache instead of parsing locally.
    parsed = _parsed_source_and_tree(str(file_path))
    if parsed is not None:
        _parsed_source, _source_bytes, tree = parsed
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "import_statement":
                statement = source[node.start_byte : node.end_byte]
                for binding in _js_ts_default_import_bindings(statement):
                    if (
                        _js_ts_import_match_details(
                            file_path,
                            module_name=str(binding.get("module", "")),
                            imported_name="default",
                            symbol=symbol,
                            definition_path=definition_path,
                            repo_root=repo_root,
                            is_default=True,
                        )
                        is not None
                    ):
                        return {
                            "start_line": int(node.start_point[0] + 1),
                            "end_line": int(node.end_point[0] + 1),
                            "module": str(binding.get("module", "")),
                            "provenance": "parser-backed",
                        }
                for binding in _js_ts_named_import_bindings(statement):
                    if (
                        str(binding.get("statement_kind", "import")) == "import"
                        and _js_ts_import_match_details(
                            file_path,
                            module_name=str(binding.get("module", "")),
                            imported_name=str(binding.get("imported", "")),
                            symbol=symbol,
                            definition_path=definition_path,
                            repo_root=repo_root,
                        )
                        is not None
                    ):
                        return {
                            "start_line": int(node.start_point[0] + 1),
                            "end_line": int(node.end_point[0] + 1),
                            "module": str(binding.get("module", "")),
                            "provenance": "parser-backed",
                        }
            stack.extend(reversed(node.children))

    for binding in _js_ts_default_import_bindings(source):
        if (
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding.get("module", "")),
                imported_name="default",
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
                is_default=True,
            )
            is not None
        ):
            return {
                "start_line": int(binding.get("start_line", 0)),
                "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
                "module": str(binding.get("module", "")),
                "provenance": "heuristic",
            }

    for binding in _js_ts_named_import_bindings(source):
        if (
            str(binding.get("statement_kind", "import")) == "import"
            and _js_ts_import_match_details(
                file_path,
                module_name=str(binding.get("module", "")),
                imported_name=str(binding.get("imported", "")),
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
            )
            is not None
        ):
            return {
                "start_line": int(binding.get("start_line", 0)),
                "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
                "module": str(binding.get("module", "")),
                "provenance": "heuristic",
            }
    return None


def _rust_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    for binding in _rust_use_bindings(source):
        if (
            _rust_use_binding_match_details(
                file_path,
                binding,
                symbol,
                definition_path,
                repo_root,
            )
            is None
        ):
            continue
        return {
            "start_line": int(binding.get("start_line", 0)),
            "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
            "module": str(binding.get("module", "")) or str(binding.get("path", "")),
            "provenance": "heuristic",
        }
    return None


def _import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    if str(file_path.resolve()) == str(Path(definition_path).resolve()):
        return None
    spec = lang_registry.spec_for_path(file_path)
    if spec is None or spec.import_update_target is None:
        return None
    return spec.import_update_target(file_path, symbol, definition_path, repo_root)


def _source_line_text(path: Path, line_number: int) -> str:
    if line_number <= 0:
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    return lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""


def _import_graph_resolution_confidence(provenance: str) -> float:
    if provenance == "parser-backed":
        return 0.95
    if provenance in {"heuristic", "regex-heuristic"}:
        return 0.65
    return 0.75


def _build_import_graph_consumers_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    definition_files: list[str],
    *,
    bounded_files: list[Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> list[dict[str, Any]]:
    if not definition_files:
        return []
    repo_root = _repo_map_root_dir(repo_map)
    definition_file_set = {str(current) for current in definition_files}
    files = bounded_files if bounded_files is not None else _repo_map_file_universe(repo_map)
    consumers: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    with _profiling_phase(_profiling_collector, "import_graph_consumers"):
        for current in files:
            # task #61: this loop re-walks the same up-to-CEILING file set the caller-scan main
            # loop just bounded, but ran AFTER it with no deadline check of its own -- for a
            # central symbol the main loop finished inside budget while THIS sibling loop pushed
            # wall-clock well past --deadline (profiled: `callers ... --deadline 10` -> ~25s).
            # Mirror the main loop's check (repo_map.py caller-scan, build_symbol_callers_from_map)
            # exactly, using the SAME shared deadline value.
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            current_file = str(current)
            if current_file in definition_file_set:
                continue
            if lang_registry.spec_for_path(current) is None:
                continue
            if not _file_may_import_symbol_definition(current, definition_files):
                continue
            for definition_file in definition_files:
                target = _import_update_target(current, symbol, definition_file, repo_root)
                if target is None:
                    continue
                line = int(target.get("start_line", 0) or 0)
                end_line = int(target.get("end_line", line) or line)
                module = str(target.get("module", "") or "")
                provenance = str(target.get("provenance", "heuristic") or "heuristic")
                key = (current_file, line, end_line, definition_file, module)
                if key in seen:
                    continue
                seen.add(key)
                consumers.append({
                    "file": current_file,
                    "line": line,
                    "end_line": end_line,
                    "text": _source_line_text(current, line),
                    "kind": "import-consumer",
                    "edge_kind": "reverse-import",
                    "definition_file": definition_file,
                    "module": module,
                    "provenance": provenance,
                    "resolution_confidence": _import_graph_resolution_confidence(provenance),
                })
    consumers.sort(
        key=lambda item: (
            str(item["file"]),
            int(item.get("line", 0) or 0),
            str(item.get("module", "")),
            str(item.get("definition_file", "")),
        )
    )
    return consumers


def _preferred_definition_files(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[str]:
    repo_root = _repo_map_root_dir(repo_map)
    definitions = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("name")) == symbol
    ]
    definition_files = list(dict.fromkeys(str(current["file"]) for current in definitions))
    if len(definition_files) <= 1:
        return definition_files

    scores = dict.fromkeys(definition_files, 0)
    for current in _repo_map_file_universe(repo_map):
        # task #61: this loop iterates the FULL repo-map universe (NOT the bounded caller-scan
        # file set) with no deadline check -- unbounded even when the caller-scan main loop this
        # function feeds into is correctly bounded. Mirror the same deadline check.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        current_path = str(current)
        if current_path in scores:
            continue
        for definition_file in definition_files:
            if _file_imports_symbol_from_definition(
                current,
                symbol,
                definition_file,
                repo_root,
            ):
                scores[definition_file] += 2 if _is_test_file(current) else 1

    preferred = [current for current, score in scores.items() if score > 0]
    return preferred or definition_files


def _relevant_tests_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definition_files: list[str],
    *,
    caller_files: list[str] | None = None,
    fallback_tests: list[str] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> list[str]:
    repo_root = _repo_map_root_dir(repo_map)
    tests = [str(current) for current in repo_map.get("tests", [])]
    caller_set = set(caller_files or [])
    symbol_candidates = [symbol]
    for definition_file in definition_files:
        definition_symbol = next(
            (
                current
                for current in repo_map.get("symbols", [])
                if str(current.get("file")) == str(definition_file)
                and str(current.get("name")) == symbol
            ),
            None,
        )
        if not definition_symbol:
            continue
        if str(definition_file).endswith(".rs"):
            owner_type = _rust_impl_owner_type(
                str(definition_file),
                int(definition_symbol.get("line", definition_symbol.get("start_line", 0)) or 0),
            )
            if owner_type and owner_type not in symbol_candidates:
                symbol_candidates.append(owner_type)
    if caller_files:
        source_files = list(
            dict.fromkeys([*(str(current) for current in caller_files or []), *definition_files])
        )
        all_files = [str(current) for current in repo_map.get("files", [])]
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in repo_map.get("imports", [])
        }
        # #691 gate NIT-1 (#222 residual, still-reachable cold path): this function already
        # declares `deadline_monotonic`/`deadline_hit` (used by the direct_definition_tests loop
        # below) but left THESE three whole-repo graph calls un-gated -- the identical ~n^2.2 BFS
        # `_reverse_import_distances` bounded elsewhere in this module. `build_symbol_callers_
        # from_map` reaches this exact block with BOTH `caller_files=` (non-None, so this branch
        # runs) AND a real `deadline_monotonic` (the `tg callers --deadline SYMBOL` budget), so a
        # high-fan-in symbol could still blow the whole-repo BFS/reverse-index cost this PR exists
        # to bound. Thread the two kwargs already in scope, same per-item-inside-the-expensive-
        # loop shape as every other call site this PR fixed.
        reverse_importers = _reverse_importers(
            all_files,
            imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        file_distances = _reverse_import_distances(
            source_files,
            all_files,
            imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        graph_scores = _personalized_reverse_import_pagerank(
            source_files,
            all_files,
            reverse_importers,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        file_scores: dict[str, int] = {}
        for current in source_files:
            file_scores[current] = max(file_scores.get(current, 0), 5)
        for current, depth in file_distances.items():
            file_scores[current] = max(file_scores.get(current, 0), max(1, 5 - int(depth)))

        test_matches = _context_tests(
            source_files,
            tests,
            _query_terms(symbol),
            imports_by_file,
            file_distances,
            graph_scores,
            file_scores,
            raw_query=symbol,
        )
        direct_definition_tests = []
        for current in tests:
            # #52 fix (loop B): this loop re-walks the FULL tests list with no deadline check --
            # unbounded even when the caller-scan/impact seams that feed it are correctly bounded
            # (dominant cause of the 23x --deadline overrun on a high-fan-out symbol like "main").
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            path = Path(current)
            if any(
                _file_imports_symbol_from_definition(
                    path,
                    candidate_symbol,
                    definition_file,
                    repo_root,
                )
                for candidate_symbol in symbol_candidates
                for definition_file in definition_files
            ):
                direct_definition_tests.append(current)

        ranked = [str(current["path"]) for current in test_matches]
        ordered: list[str] = []
        for current in [*direct_definition_tests, *ranked]:
            if current in caller_set and current not in ordered:
                ordered.append(current)
            elif current not in ordered:
                ordered.append(current)
        if ordered:
            return ordered
    related: list[str] = []
    for current in tests:
        # #52 fix (loop B): same unbounded-full-tests-list hazard as the direct_definition_tests
        # loop above (this branch runs whenever `caller_files` is falsy, or the ranked/direct
        # branch above found nothing) -- guard it identically.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        if current in caller_set:
            related.append(current)
            continue
        path = Path(current)
        if any(
            _file_imports_symbol_from_definition(
                path,
                candidate_symbol,
                definition_file,
                repo_root,
            )
            for candidate_symbol in symbol_candidates
            for definition_file in definition_files
        ):
            related.append(current)
    if related:
        return related
    if fallback_tests:
        return list(dict.fromkeys(str(current) for current in fallback_tests))
    return []


def _related_test_stem(stem: str) -> str:
    normalized = stem.lower()
    for suffix in (".test", ".spec"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.removeprefix("test_")


def _discover_validation_tests_for_primary_file(
    scoped_root: str | Path,
    primary_file: str | None,
    *,
    primary_symbol_name: str | None,
    query: str,
    limit: int,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[str]:
    if not primary_file:
        return []
    source_path = Path(primary_file).expanduser().resolve()
    validation_root = _validation_repo_root(source_path.parent)
    scoped_path = Path(scoped_root).expanduser().resolve()
    if scoped_path.is_dir() and validation_root == scoped_path:
        return []

    terms: list[str] = []
    for term in [
        *_candidate_terms(primary_symbol_name),
        *_candidate_terms(query),
        *_source_tokens([str(source_path)]),
    ]:
        if term and term not in terms:
            terms.append(term)

    source_stem = source_path.stem.lower()
    source_related_stem = _related_test_stem(source_stem)
    scored_tests: list[tuple[int, str]] = []
    candidate_files = _precomputed_validation_files_for_root(
        validation_root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if candidate_files is None:
        # #222 residual fix: this fallback (only reached when the caller has no `rm["files"]`
        # list to reuse -- e.g. a bare `validation_root` outside the current repo map) did a
        # FRESH, un-deadlined filesystem walk even though this function already has `deadline_
        # monotonic`/`deadline_hit` in scope and threads them into the `_precomputed_validation_
        # files_for_root` branch just above. `_iter_repo_files` already supports both kwargs
        # (the main `build_repo_map` scan uses them) -- this was simply not wired here. Profiled
        # contributing multiple seconds to `tg agent`'s post-deadline tail (via `_build_edit_
        # plan_seed`'s validation-plan machinery) on a 6,000-file synthetic repo.
        candidate_files = _iter_repo_files(
            validation_root,
            max_files=_VALIDATION_RUNNER_SCAN_LIMIT,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    for current in candidate_files:
        # #639 Opus-gate nit 1: the resolve loop above can be bounded and still hand back a
        # partial `candidate_files` list -- also bound THIS scoring loop directly (test-file
        # detection + node:test probe + path scoring below are each their own, non-trivial cost)
        # so a large candidate set can't itself run the shared budget past zero.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        if not _is_test_file(current):
            continue
        resolved = current.resolve()
        if resolved == source_path:
            continue
        if current.suffix.lower() in _JS_TS_SUFFIXES and not _javascript_test_file_uses_node_test(
            str(resolved)
        ):
            continue
        # F84 Fix B: discovery used to be JS/TS-only -- every non-JS/TS candidate (including a
        # `.py` test file that already passed the `_is_test_file` gate above) was silently
        # dropped here, so a scoped python primary never got a targeted per-file validation
        # step even once Fix A let the walk reach the real root. `.py` test files now fall
        # through to the language-neutral scoring below; the JS/TS node:test gate above is
        # unchanged, and every other language is still excluded.
        if current.suffix.lower() not in _JS_TS_SUFFIXES and current.suffix.lower() != ".py":
            continue
        current_path = str(resolved)
        score = _score_file_path(current_path, terms)
        test_related_stem = _related_test_stem(current.stem)
        if test_related_stem == source_related_stem:
            score += 8
        elif source_related_stem and source_related_stem in test_related_stem:
            score += 4
        score += _framework_test_pattern_bonus(current_path, terms, raw_query=query)
        if score <= 0:
            continue
        scored_tests.append((score, current_path))

    scored_tests.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in scored_tests[: max(1, limit)]]


def _dedupe_symbol_records(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for current in symbols:
        line = int(current.get("line", current.get("start_line", 0)) or 0)
        end_line = int(current.get("end_line", line) or line)
        key = (
            str(current.get("file", "")),
            line,
            end_line,
            str(current.get("kind", "")),
            str(current.get("name", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(current))
    deduped.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return deduped


def _js_ts_dynamic_import_hit(line: str) -> tuple[str, bool] | None:
    """Detect a dynamic ``import(...)`` call or a ``require(...)`` call NOT already covered by
    the assignment-anchored static regexes above (bare ``require("x");`` with no assignment,
    ``require(...)``/``import(...)`` used as a sub-expression, or either call form given a
    non-literal argument).

    Returns ``(module, dynamic_unresolved)`` -- ``module`` is ``""`` when the argument isn't a
    static string literal (a variable, template literal, or expression), and
    ``dynamic_unresolved`` is ``True`` in that case -- or ``None`` when neither call form is
    present on this line.

    #93 SUB-1 recall fix: this is ADDITIVE to the static regexes, never a replacement -- callers
    only consult this after their own assignment-anchored match comes back empty, so a plain
    ``const x = require("y")`` line is still reported exactly once (via the static path), not
    twice. Known limitation (accepted, same "precision over guessing" posture as the rest of
    this file's regex heuristics): a fully INDIRECT alias -- `const req = require; req("y");` --
    is not traced; there is no literal `require(`/`import(` call shape on the second line for
    this to match.
    """
    literal_match = re.search(r'\b(?:import|require)\s*\(\s*["\']([^"\']+)["\']\s*\)', line)
    if literal_match:
        return literal_match.group(1), False
    if re.search(r"\b(?:import|require)\s*\(", line):
        return "", True
    return None


def _regex_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return [], []

    try:
        lines = _read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    for line_number, line in enumerate(lines, start=1):
        if path.suffix in _JS_TS_SUFFIXES:
            import_match = re.match(r'^\s*import\s+.*?from\s+["\']([^"\']+)["\']', line)
            export_from_match = re.match(r'^\s*export\s+.*?from\s+["\']([^"\']+)["\']', line)
            require_match = re.match(
                r"^\s*(?:const|let|var)\s+(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)"
                r'\s*=\s*require\(["\']([^"\']+)["\']\)',
                line,
            )
            class_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            function_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            variable_function_match = re.match(
                r"^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            commonjs_export_function_match = re.match(
                r"^\s*(?:module\.)?exports\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            object_export_function_match = re.match(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            if import_match:
                imports.append(import_match.group(1))
            if export_from_match:
                imports.append(export_from_match.group(1))
            if require_match:
                imports.append(require_match.group(1))
            else:
                # #93 SUB-1: `import("x")` call-form and a require(...) not shaped like the
                # assignment-anchored regex above. Only the statically-resolvable literal is
                # useful to this alias-graph prefilter list -- an unresolved (non-literal) hit
                # has no name to add.
                dynamic_hit = _js_ts_dynamic_import_hit(line)
                if dynamic_hit is not None and dynamic_hit[0]:
                    imports.append(dynamic_hit[0])
            if class_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=class_match.group(1),
                        kind="class",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if function_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=function_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            for current_match in (
                variable_function_match,
                commonjs_export_function_match,
                object_export_function_match,
            ):
                if current_match is None:
                    continue
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=current_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
        elif path.suffix in _RUST_SUFFIXES:
            use_match = re.match(r"^\s*use\s+([^;]+);", line)
            fn_match = re.match(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            struct_match = re.match(
                r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            enum_match = re.match(
                r"^\s*(?:pub\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            trait_match = re.match(
                r"^\s*(?:pub\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            if use_match:
                imports.append(use_match.group(1).strip())
            if fn_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=fn_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if struct_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=struct_match.group(1),
                        kind="struct",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if enum_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=enum_match.group(1),
                        kind="enum",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if trait_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=trait_match.group(1),
                        kind="trait",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )

    imports = sorted(dict.fromkeys(imports))
    return imports, _dedupe_symbol_records(symbols)


def _js_ts_symbol_name_node(node: Any) -> Any | None:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return name_node
    for child in node.children:
        if child.type in {"identifier", "property_identifier", "private_property_identifier"}:
            return child
    return None


def _js_ts_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    kind_by_node_type = {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    }

    def _walk(node: Any) -> None:
        if node.type in kind_by_node_type:
            name_node = _js_ts_symbol_name_node(node)
            if name_node is not None:
                name = _node_text(name_node)
                if _is_clean_symbol_name(name):
                    symbols.append(
                        _symbol_record(
                            name=name,
                            kind=kind_by_node_type[node.type],
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return symbols


def _rust_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        kind_map = {
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
        }
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                name = _node_text(name_node)
                if _is_clean_symbol_name(name):
                    symbols.append(
                        _symbol_record(
                            name=name,
                            kind=kind_map[node.type],
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return symbols


# PATH A Stage 2: Java's kind map (shared between the symbol extractor below and
# `_java_parser_symbol_sources`, mirroring the two Rust functions above that each inline their
# own copy). classes/interfaces/enums/records -> "class"; methods/constructors -> "function",
# matching the Python convention (`_python_imports_and_symbols` above: ClassDef -> "class",
# every FunctionDef/AsyncFunctionDef -> "function") rather than Go's separate "method" kind.
_JAVA_SYMBOL_KIND_MAP: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "record_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
}

# Verified against the real tree-sitter-java grammar (0.23.5): an `import_declaration` node has
# NO `name` field (unlike class/method/etc.) -- its children are the `import` keyword, an
# optional `static` keyword, a `scoped_identifier`/`identifier` (optionally followed by a
# trailing `.` + `asterisk` for a wildcard import), and the closing `;`. Reconstructing the
# dotted name via node-text + strip is therefore simpler and more robust than chasing child
# field names, and naturally preserves a trailing `.*` for a wildcard import.
_JAVA_IMPORT_STRIP_RE = re.compile(r"^import\s+(?:static\s+)?(.*?)\s*;?\s*$", re.DOTALL)


def _java_import_declaration_text(node: Any, source_bytes: bytes) -> str:
    text = _tree_sitter_node_text(source_bytes, node).strip()
    match = _JAVA_IMPORT_STRIP_RE.match(text)
    return match.group(1).strip() if match else text


def _java_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Foundational-tier Java extractor: classes/interfaces/enums/records/methods/constructors
    plus raw import declarations, in ONE tree walk (mirrors `_python_imports_and_symbols`'s
    combined shape, since Java -- like Python -- has no separate regex-heuristic extractor to
    split imports from symbols the way the JS/TS/Rust split does).

    Fail-closed like Go (mirrors `_python_imports_and_symbols`'s guard): parser-None or a
    read/parse error returns ``([], [])``, never a partial regex degrade -- Java has no regex
    fallback (see the `.java` branch in `_imports_and_symbols_for_path` below).
    """
    if path.suffix not in _JAVA_SUFFIXES:
        return [], []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    _source, source_bytes, tree = parsed

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type == "import_declaration":
            imports.append(_java_import_declaration_text(node, source_bytes))
        elif node.type in _JAVA_SYMBOL_KIND_MAP:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                name = _node_text(name_node)
                if _is_clean_symbol_name(name):
                    symbols.append(
                        _symbol_record(
                            name=name,
                            kind=_JAVA_SYMBOL_KIND_MAP[node.type],
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def _java_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """`tg source` extractor for Java -- exact source block for a named class/interface/enum/
    record/method/constructor. Mirrors `_rust_parser_symbol_sources` exactly, reusing the shared
    cached parse product (`_parsed_source_and_tree`) instead of re-parsing directly."""
    if path.suffix not in _JAVA_SUFFIXES:
        return []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type in _JAVA_SYMBOL_KIND_MAP:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": _JAVA_SYMBOL_KIND_MAP[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _java_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    """`tg imports` extractor for Java -- one row per `import_declaration` STATEMENT with its
    1-based line number (mirrors `_rust_imports_with_lines`'s shape/role exactly, but tree-sitter
    -backed rather than regex-backed since Java has no regex fallback)."""
    if path.suffix not in _JAVA_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return []
    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed

    entries: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if node.type == "import_declaration":
            entries.append({
                "module": _java_import_declaration_text(node, source_bytes),
                "line": node.start_point[0] + 1,
            })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return entries


def _python_classify_ref_kind(node: ast.AST, parent: ast.AST | None, *, in_annotation: bool) -> str:
    """Classify an already-matched Python Name/Attribute reference node (T1 additive).

    Only called for nodes the existing matcher already emits a row for (moat P0-T1: classify
    EXISTING rows, never widen the match set -- that would change row counts). Precedence: a
    node that IS the callee of its parent ``ast.Call`` is "call" even inside an annotation
    subtree (unlikely but keeps the check order simple); otherwise annotation subtrees are
    "type"; a bare ``ast.Attribute`` is "field"; anything else is "value".
    """
    if isinstance(parent, ast.Call) and parent.func is node:
        return "call"
    if in_annotation:
        return "type"
    if isinstance(node, ast.Attribute):
        return "field"
    return "value"


def _python_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix != ".py":
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
        tree = _cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    lines = source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    # Manual recursive walk (equivalent to ast.NodeVisitor's generic_visit dispatch, which the
    # prior Visitor class relied on) so we can thread `parent` + `in_annotation` context down to
    # the match sites for ref_kind classification -- ast.NodeVisitor gives no parent access.
    def _walk(node: ast.AST, parent: ast.AST | None, in_annotation: bool) -> None:
        if isinstance(node, ast.Name) and node.id == symbol:
            references.append({
                "name": symbol,
                "kind": "reference",
                "ref_kind": _python_classify_ref_kind(node, parent, in_annotation=in_annotation),
                "file": str(path),
                "line": node.lineno,
                "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            })
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            references.append({
                "name": symbol,
                "kind": "reference",
                "ref_kind": _python_classify_ref_kind(node, parent, in_annotation=in_annotation),
                "file": str(path),
                "line": node.lineno,
                "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            })
        elif isinstance(node, ast.Call):
            matched = False
            if isinstance(node.func, ast.Name) and node.func.id == symbol:
                matched = True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                matched = True
            if matched:
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.lineno,
                    "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                })

        for field_name, value in ast.iter_fields(node):
            if isinstance(node, ast.Call) and field_name in ("args", "keywords"):
                # F19 fix (audit #63): a Call's arguments are runtime VALUES, not type syntax,
                # even when the call itself sits inside a type-annotation subtree (e.g.
                # `Annotated[int, validate(LIMIT)]`) -- only the callee should keep the
                # enclosing annotation context, and that's moot anyway since the parent-is-Call
                # precedence above always wins for the callee regardless of in_annotation.
                child_in_annotation = False
            else:
                child_in_annotation = in_annotation or field_name in ("annotation", "returns")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        _walk(item, node, child_in_annotation)
            elif isinstance(value, ast.AST):
                _walk(value, node, child_in_annotation)

    _walk(tree, None, False)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _python_provider_alias_calls(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = _cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    alias_names = {symbol}

    def _binding_name(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
        return None

    def _assignment_targets(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for current in target.elts:
                names.extend(_assignment_targets(current))
            return names
        return []

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Import):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Assign):
                binding_name = _binding_name(node.value)
                if binding_name not in alias_names:
                    continue
                for target_name in (
                    _assignment_targets(node.targets[0])
                    if len(node.targets) == 1
                    else [name for target in node.targets for name in _assignment_targets(target)]
                ):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True
            elif isinstance(node, ast.AnnAssign):
                binding_name = _binding_name(node.value) if node.value is not None else None
                if binding_name not in alias_names:
                    continue
                for target_name in _assignment_targets(node.target):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True

    calls: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        alias_name = _binding_name(node.func)
        if alias_name not in alias_names:
            continue
        calls.append({
            "name": symbol,
            "kind": "call",
            "file": str(path),
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            "alias": alias_name,
        })

    calls.sort(key=lambda item: (item["file"], item["line"], item.get("alias", ""), item["text"]))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for call_entry in calls:
        key = (
            str(call_entry["file"]),
            int(call_entry["line"]),
            int(call_entry.get("end_line", call_entry["line"])),
            str(call_entry.get("alias", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_entry)
    return deduped


def _regex_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return [], []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    symbol_pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    call_pattern = re.compile(rf"(?:\b|\.|::){re.escape(symbol)}\s*\(")

    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _strip_line_string_and_comment_noise(line: str, *, supports_template_strings: bool) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
        in_template = False
        escaped = False

        for index, char in enumerate(line):
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if in_single:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_single = False
                cleaned.append(" ")
                continue
            if in_double:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_double = False
                cleaned.append(" ")
                continue
            if in_template:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "`":
                    in_template = False
                cleaned.append(" ")
                continue
            if char == "/" and next_char == "/":
                break
            if char == "'":
                in_single = True
                cleaned.append(" ")
                continue
            if char == '"':
                in_double = True
                cleaned.append(" ")
                continue
            if supports_template_strings and char == "`":
                in_template = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    for line_number, line in enumerate(lines, start=1):
        if symbol_pattern.search(line):
            references.append({
                "name": symbol,
                "kind": "reference",
                "file": str(path),
                "line": line_number,
                "text": line,
            })
        supports_template_strings = path.suffix in _JS_TS_SUFFIXES
        sanitized_line = _strip_line_string_and_comment_noise(
            line, supports_template_strings=supports_template_strings
        )
        if call_pattern.search(sanitized_line):
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "text": line,
            })

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


_JS_TS_TYPE_ANCESTOR_TYPES: set[str] = {
    "type_annotation",
    "extends_clause",
    "implements_clause",
    "generic_type",
}

# F7 fix: `as_expression`/`satisfies_expression` are handled by dedicated positional logic below
# (only the TYPE-side child is "type") -- they must NOT sit in the generic ancestor-walk set above,
# which would (and used to) mislabel the runtime VALUE operand (`Widget as unknown` -> `Widget`)
# as "type" merely for having one of these as an ancestor.
_JS_TS_AS_SATISFIES_TYPES: set[str] = {"as_expression", "satisfies_expression"}

# F17 fix: construction/render call sites -- `new Widget()` and JSX (`<Widget/>`,
# `<Widget>...</Widget>`) -- are call-like uses of the symbol, not bare "value" mentions.
_JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES: set[str] = {
    "jsx_self_closing_element",
    "jsx_opening_element",
    "jsx_closing_element",
}


def _js_ts_classify_ref_kind(node: Any) -> str:
    """Classify an already-matched JS/TS identifier/property_identifier reference (T1).

    Only called for nodes the existing walker already emits a row for -- ``type_identifier``
    (the node type TS uses for most type-position symbols, e.g. ``x: Symbol``) is never matched
    by the walker today, so widening to it is T2 (would add new rows, not just labels).
    """
    parent = getattr(node, "parent", None)
    # NOTE: tree-sitter's Python binding hands back a NEW wrapper object on every
    # child_by_field_name()/`.parent` access, so `is` identity comparison silently never matches
    # -- `==` (and `.id`) compare the underlying node and are what must be used here.
    if node.type == "identifier" and parent is not None and parent.type == "call_expression":
        function_node = parent.child_by_field_name("function")
        if function_node is not None and function_node == node:
            return "call"
    if node.type == "identifier" and parent is not None and parent.type == "new_expression":
        constructor_node = parent.child_by_field_name("constructor")
        if constructor_node is not None and constructor_node == node:
            return "call"
    if (
        node.type == "identifier"
        and parent is not None
        and parent.type in _JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES
    ):
        name_node = parent.child_by_field_name("name")
        if name_node is not None and name_node == node:
            return "call"
    if (
        node.type == "property_identifier"
        and parent is not None
        and parent.type == "member_expression"
    ):
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            function_node = grandparent.child_by_field_name("function")
            if function_node is not None and function_node == parent:
                return "call"
        return "field"
    if parent is not None and parent.type in _JS_TS_AS_SATISFIES_TYPES:
        # Neither `as_expression` nor `satisfies_expression` exposes named grammar fields for its
        # two children, so the type side is identified positionally: it is always the LAST named
        # child (`operand as/satisfies TYPE`). Only that child is "type"; the operand falls
        # through to keep whatever kind it would have received anyway (call/field/value).
        type_side = (
            parent.named_child(parent.named_child_count - 1) if parent.named_child_count else None
        )
        if type_side is not None and type_side == node:
            return "type"
    if _node_has_ancestor_type(node, _JS_TS_TYPE_ANCESTOR_TYPES):
        return "type"
    return "value"


def _js_ts_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return [], []

    try:
        source = _read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return [], []

    # PERF increment 1 / Section B (Fable-designed): binding resolution only needs the source
    # TEXT (not a parse tree), so it now runs BEFORE the parse -- letting a symbol-absent file
    # skip tree-sitter parsing entirely below (the refs loop that follows has no prefilter,
    # unlike the caller-scan literal check, so this is the biggest single payoff in this file).
    # TRAP (do not reorder): this pass must run first so a renamed re-export
    # (`export {x as y} from "./mod"`) still triggers a parse even though the literal target
    # symbol name never appears in THIS file's text -- alias_names ends up non-empty and the
    # early-exit below correctly falls through to parsing. See test_js_ts_advanced_resolution.py
    # ::test_aliased_re_export_chain_resolves_to_original_definition.
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in _js_ts_named_import_bindings(source):
        if str(binding.get("statement_kind", "import")) != "import":
            continue
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            str(binding.get("imported", "")),
            repo_root,
        )
        if resolved_import is not None:
            if str(resolved_import.get("symbol")) != symbol:
                continue
            alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
            continue
        if str(binding.get("imported", "")) == symbol:
            alias_resolution_by_name[str(binding.get("local", ""))] = {
                "provenance": [],
                "confidence": 0.95,
            }
    for binding in _js_ts_default_import_bindings(source):
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            "default",
            repo_root,
        )
        if resolved_import is None or str(resolved_import.get("symbol")) != symbol:
            continue
        alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
    alias_names = {name for name in alias_resolution_by_name if name}

    if symbol not in source and not alias_names:
        return [], []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    parsed_source, source_bytes, tree = parsed
    # `lines` MUST come from the SAME read as `source_bytes`/`tree` (all three from the single
    # `_parsed_source_and_tree` product), NOT from the earlier `source` text read above: the two are
    # independent (path, mtime, size)-keyed cache lookups, so a file edited between them would leave
    # tree node line-indices (from the parse) indexing into stale `lines` -> wrong reported line
    # content / IndexError. The pre-parse text read keeps using `source` (a cheap heuristic gate).
    lines = parsed_source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if _node_has_ancestor_type(node, {"import_statement"}):
            return True
        if parent.type in {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "generator_function_declaration",
        }:
            return True
        return bool(parent.type == "import_specifier")

    def _walk(node: Any) -> None:
        node_type = node.type
        node_text = _node_text(node) if node_type in {"identifier", "property_identifier"} else ""
        matched_identifier = node_text == symbol or (
            node_type == "identifier" and node_text in alias_names
        )
        if matched_identifier:
            if not _is_definition_identifier(node):
                alias_reference_resolution = (
                    alias_resolution_by_name.get(node_text) if node_type == "identifier" else None
                )
                try:
                    ref_kind = _js_ts_classify_ref_kind(node)
                except Exception:
                    # F20: a classifier bug must only default THIS row to "value", never drop
                    # every reference in the file -- classify-only, so a failure here can never
                    # be allowed to look like a fail-closed backend error.
                    ref_kind = "value"
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": ref_kind,
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(
                                alias_reference_resolution.get("provenance", [])
                            ),
                            "resolution_confidence": float(
                                alias_reference_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_reference_resolution
                        else {}
                    ),
                })
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            alias_resolution: dict[str, Any] | None = None
            if function_node is not None:
                if function_node.type in {"identifier", "property_identifier"}:
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or (
                        function_node.type == "identifier" and function_name in alias_names
                    )
                    if function_node.type == "identifier":
                        alias_resolution = alias_resolution_by_name.get(function_name)
                elif function_node.type == "member_expression":
                    property_node = function_node.child_by_field_name("property")
                    matched = bool(
                        property_node is not None and _node_text(property_node) == symbol
                    )
            if matched:
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(alias_resolution.get("provenance", [])),
                            "resolution_confidence": float(
                                alias_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_resolution
                        else {}
                    ),
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _js_ts_provider_alias_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    include_assignment_wrappers: bool = False,
) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    try:
        source = _read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in _js_ts_named_import_bindings(source):
        if str(binding.get("statement_kind", "import")) != "import":
            continue
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            str(binding.get("imported", "")),
            repo_root,
        )
        if resolved_import is not None:
            if str(resolved_import.get("symbol")) != symbol:
                continue
            alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
            continue
        if str(binding.get("imported", "")) == symbol:
            alias_resolution_by_name[str(binding.get("local", ""))] = {
                "provenance": [],
                "confidence": 0.95,
            }
    for binding in _js_ts_default_import_bindings(source):
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            "default",
            repo_root,
        )
        if resolved_import is None or str(resolved_import.get("symbol")) != symbol:
            continue
        alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
    alias_names = {name for name in alias_resolution_by_name if name}

    def _strip_js_ts_string_and_comment_noise(line: str) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
        in_template = False
        escaped = False

        for index, char in enumerate(line):
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if in_single:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_single = False
                cleaned.append(" ")
                continue
            if in_double:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_double = False
                cleaned.append(" ")
                continue
            if in_template:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "`":
                    in_template = False
                cleaned.append(" ")
                continue
            if char == "/" and next_char == "/":
                break
            if char == "'":
                in_single = True
                cleaned.append(" ")
                continue
            if char == '"':
                in_double = True
                cleaned.append(" ")
                continue
            if char == "`":
                in_template = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    if include_assignment_wrappers:
        assignment_pattern = re.compile(
            r"\b(?:const|let|var)\s+(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_]*)\b"
        )
        changed = True
        while changed:
            changed = False
            for line in lines:
                match = assignment_pattern.search(line)
                if match is None:
                    continue
                value_name = match.group("value")
                local_name = match.group("local")
                if value_name not in alias_names or local_name in alias_names:
                    continue
                alias_names.add(local_name)
                alias_resolution_by_name[local_name] = dict(
                    alias_resolution_by_name.get(value_name, {})
                )
                changed = True

    calls: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        sanitized_line = _strip_js_ts_string_and_comment_noise(line)
        for alias_name in sorted(alias_names):
            if not re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "end_line": line_number,
                "text": line,
                "alias": alias_name,
                "resolution_provenance": list(alias_resolution.get("provenance", [])),
                "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
            })
    calls.sort(
        key=lambda item: (item["file"], item["line"], str(item.get("alias", "")), item["text"])
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for call_entry in calls:
        key = (
            str(call_entry["file"]),
            int(call_entry["line"]),
            int(call_entry.get("end_line", call_entry["line"])),
            str(call_entry.get("alias", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_entry)
    return deduped


def _rust_classify_ref_kind(node: Any) -> str:
    """Classify an already-matched Rust ``identifier`` reference node (T1 additive).

    Only called for nodes the existing walker already emits a row for -- Rust's grammar splits
    type positions into ``type_identifier`` and field access into ``field_identifier`` (both
    distinct from plain ``identifier``), so those positions are never matched by the walker
    today; widening to them is T2 (would add new rows, not just labels).

    F6 fix: only the macro NAME position (e.g. ``println`` in ``println!(...)``) is "call" --
    ``macro_invocation``'s ``arguments`` token-tree can contain arbitrary identifiers
    (``println!("{:?}", Symbol)``, ``vec![Symbol]``) that are plain data/argument mentions, not
    calls, and must fall through to "value" like any other bare identifier. Checking ONLY the
    immediate parent (rather than any ancestor) is what keeps macro *arguments* out of this branch.

    F18 fix: a turbofish call (``foo::<T>()``) puts the function identifier under a
    ``generic_function`` node (itself the ``call_expression``'s ``function`` field), one layer
    deeper than a plain call -- both the direct-identifier and ``scoped_identifier`` (path-
    qualified) forms are handled below so ``foo::<T>()`` and ``bar::baz::<T>()`` both classify
    "call" instead of falling through to "value".
    """
    parent = getattr(node, "parent", None)
    # NOTE: see _js_ts_classify_ref_kind -- tree-sitter node accessors return fresh wrapper
    # objects, so identity must be checked with `==`, not `is`.
    if parent is not None and parent.type == "call_expression":
        function_node = parent.child_by_field_name("function")
        if function_node is not None and function_node == node:
            return "call"
    if parent is not None and parent.type == "generic_function":
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            call_function_node = grandparent.child_by_field_name("function")
            if call_function_node is not None and call_function_node == parent:
                generic_function_name = parent.child_by_field_name("function")
                if generic_function_name is not None and generic_function_name == node:
                    return "call"
    if parent is not None and parent.type == "scoped_identifier":
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            function_node = grandparent.child_by_field_name("function")
            if function_node is not None and function_node == parent:
                return "call"
        if grandparent is not None and grandparent.type == "generic_function":
            great_grandparent = getattr(grandparent, "parent", None)
            if great_grandparent is not None and great_grandparent.type == "call_expression":
                call_function_node = great_grandparent.child_by_field_name("function")
                if call_function_node is not None and call_function_node == grandparent:
                    generic_function_name = grandparent.child_by_field_name("function")
                    if generic_function_name is not None and generic_function_name == parent:
                        return "call"
    if parent is not None and parent.type == "macro_invocation":
        macro_name_node = parent.child_by_field_name("macro")
        if macro_name_node is not None and macro_name_node == node:
            return "call"
    return "value"


def _rust_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _RUST_SUFFIXES:
        return [], []

    try:
        source = _read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return [], []

    # PERF increment 1 / Section B mirror (Fable-designed): same alias-aware early exit as
    # _js_ts_references_and_calls above -- bindings only need the source TEXT, so they're
    # resolved before the parse, and a symbol-absent file with no matching `use` binding skips
    # tree-sitter parsing entirely.
    bindings = _rust_use_bindings(source)
    local_name_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        local_name_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            local_name_resolution_by_name.setdefault(symbol, dict(resolved_import))
    local_names = {name for name in local_name_resolution_by_name if name}

    if symbol not in source and not local_names:
        return [], []

    parsed = _parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    parsed_source, source_bytes, tree = parsed
    # `lines` MUST come from the SAME read as `source_bytes`/`tree` (all three from the single
    # `_parsed_source_and_tree` product), NOT from the earlier `source` text read above: the two are
    # independent (path, mtime, size)-keyed cache lookups, so a file edited between them would leave
    # tree node line-indices (from the parse) indexing into stale `lines` -> wrong reported line
    # content / IndexError. The pre-parse text read keeps using `source` (a cheap heuristic gate).
    lines = parsed_source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        return bool(parent.type in {"function_item", "struct_item", "enum_item", "trait_item"})

    def _walk(node: Any) -> None:
        node_type = node.type
        if node_type == "identifier":
            node_text = _node_text(node)
            alias_resolution = local_name_resolution_by_name.get(node_text)
            if (
                (node_text == symbol or node_text in local_names)
                and not _is_definition_identifier(node)
                and not _node_has_ancestor_type(node, {"use_declaration"})
            ):
                try:
                    ref_kind = _rust_classify_ref_kind(node)
                except Exception:
                    # F20: a classifier bug must only default THIS row to "value", never drop
                    # every reference in the file.
                    ref_kind = "value"
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": ref_kind,
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(alias_resolution.get("provenance", [])),
                            "resolution_confidence": float(
                                alias_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_resolution
                        else {}
                    ),
                })
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            call_resolution: dict[str, Any] | None = None
            if function_node is not None:
                if function_node.type == "identifier":
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or function_name in local_names
                    call_resolution = local_name_resolution_by_name.get(function_name)
                elif function_node.type == "field_expression":
                    field_node = function_node.child_by_field_name("field")
                    matched = bool(field_node is not None and _node_text(field_node) == symbol)
                elif function_node.type == "scoped_identifier":
                    name_node = function_node.child_by_field_name("name")
                    matched = bool(name_node is not None and _node_text(name_node) == symbol)
            if matched:
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(call_resolution.get("provenance", [])),
                            "resolution_confidence": float(call_resolution.get("confidence", 0.95)),
                        }
                        if call_resolution
                        else {}
                    ),
                })
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(call_resolution.get("provenance", [])),
                            "resolution_confidence": float(call_resolution.get("confidence", 0.95)),
                        }
                        if call_resolution
                        else {}
                    ),
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _rust_provider_alias_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    include_assignment_wrappers: bool = False,
) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    bindings = _rust_use_bindings(source)
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        alias_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            alias_resolution_by_name.setdefault(symbol, dict(resolved_import))
    alias_names = {name for name in alias_resolution_by_name if name}

    def _strip_rust_string_and_comment_noise(line: str) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
        escaped = False

        for index, char in enumerate(line):
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if in_single:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_single = False
                cleaned.append(" ")
                continue
            if in_double:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_double = False
                cleaned.append(" ")
                continue
            if char == "/" and next_char == "/":
                break
            if char == "'":
                in_single = True
                cleaned.append(" ")
                continue
            if char == '"':
                in_double = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    if include_assignment_wrappers:
        assignment_pattern = re.compile(
            r"\blet\s+(?:mut\s+)?(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_:]*)\b"
        )
        changed = True
        while changed:
            changed = False
            for line in lines:
                match = assignment_pattern.search(line)
                if match is None:
                    continue
                value_name = match.group("value").split("::")[-1]
                local_name = match.group("local")
                if value_name not in alias_names or local_name in alias_names:
                    continue
                alias_names.add(local_name)
                alias_resolution_by_name[local_name] = dict(
                    alias_resolution_by_name.get(value_name, {})
                )
                changed = True

    calls: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        sanitized_line = _strip_rust_string_and_comment_noise(line)
        for alias_name in sorted(alias_names):
            if not re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "end_line": line_number,
                "text": line,
                "alias": alias_name,
                "resolution_provenance": list(alias_resolution.get("provenance", [])),
                "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
            })
    calls.sort(
        key=lambda item: (item["file"], item["line"], str(item.get("alias", "")), item["text"])
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for call_entry in calls:
        key = (
            str(call_entry["file"]),
            int(call_entry["line"]),
            int(call_entry.get("end_line", call_entry["line"])),
            str(call_entry.get("alias", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_entry)
    return deduped


def _python_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = _cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    sources: list[dict[str, Any]] = []

    symbol_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == symbol
    ]
    symbol_nodes.sort(
        key=lambda current: (current.lineno, getattr(current, "end_lineno", current.lineno))
    )
    for node in symbol_nodes:
        end_lineno = getattr(node, "end_lineno", node.lineno)
        block = "\n".join(lines[node.lineno - 1 : end_lineno])
        if block:
            block = f"{block}\n"
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        sources.append({
            "name": symbol,
            "kind": kind,
            "file": str(path),
            "start_line": node.lineno,
            "end_line": end_lineno,
            "source": block,
        })

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _js_ts_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    if path.suffix in {".ts", ".tsx"}:
        parser = _typescript_parser(tsx=path.suffix == ".tsx")
    else:
        parser = _javascript_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    kind_by_node_type = {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    }

    def _walk(node: Any) -> None:
        if node.type in kind_by_node_type:
            name_node = _js_ts_symbol_name_node(node)
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": kind_by_node_type[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _rust_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    parser = _rust_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []
    kind_map = {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
    }

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": kind_map[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _extract_braced_block(lines: list[str], start_index: int) -> tuple[int, str]:
    start_line = lines[start_index]
    start_line_num = start_index + 1
    brace_balance = start_line.count("{") - start_line.count("}")
    if brace_balance <= 0:
        return start_line_num, f"{start_line}\n"

    block_lines = [start_line]
    end_index = start_index
    for current_index in range(start_index + 1, len(lines)):
        current_line = lines[current_index]
        block_lines.append(current_line)
        brace_balance += current_line.count("{") - current_line.count("}")
        end_index = current_index
        if brace_balance <= 0:
            break
    return end_index + 1, "\n".join(block_lines) + "\n"


def _regex_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    if path.suffix in _JS_TS_SUFFIXES:
        escaped_symbol = re.escape(symbol)
        patterns = [
            (
                "class",
                re.compile(rf"^\s*(?:export\s+)?class\s+({escaped_symbol})\b"),
            ),
            (
                "function",
                re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?function\s+({escaped_symbol})\b"),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*(?:const|let|var)\s+({escaped_symbol})\s*=\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*(?:module\.)?exports\.({escaped_symbol})\s*=\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*({escaped_symbol})\s*:\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
        ]
    else:
        patterns = [
            (
                "function",
                re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+({re.escape(symbol)})\b"),
            ),
            (
                "struct",
                re.compile(rf"^\s*(?:pub\s+)?struct\s+({re.escape(symbol)})\b"),
            ),
            (
                "enum",
                re.compile(rf"^\s*(?:pub\s+)?enum\s+({re.escape(symbol)})\b"),
            ),
            (
                "trait",
                re.compile(rf"^\s*(?:pub\s+)?trait\s+({re.escape(symbol)})\b"),
            ),
        ]

    sources: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        matched_kind = None
        for kind, pattern in patterns:
            if pattern.match(line):
                matched_kind = kind
                break
        if matched_kind is None:
            continue

        end_line, block = _extract_braced_block(lines, line_number - 1)
        sources.append({
            "name": symbol,
            "kind": matched_kind,
            "file": str(path),
            "start_line": line_number,
            "end_line": end_line,
            "source": block,
        })

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


# ---------------------------------------------------------------------------
# PATH A Stage 0: language-extractor registry registration.
#
# Registers the CURRENT four languages (python, javascript, typescript, rust) with
# lang_registry by WRAPPING the functions defined above UNCHANGED. This is a pure-parity
# refactor: every dispatch seam that used to test `path.suffix in _JS_TS_SUFFIXES` /
# `_RUST_SUFFIXES` / `== ".py"` directly now asks `lang_registry.spec_for_path(path)` instead,
# but the underlying per-language logic those specs point to is untouched.
#
# javascript and typescript are two separate LanguageSpec entries (distinct language_id,
# distinct suffixes) but both wrap the SAME `_js_ts_*` functions -- those functions already
# branch on the .ts/.tsx suffix internally (parser/tsx selection), so registering the pair
# twice changes nothing observable; only which suffix routes to which spec entry differs,
# mirroring how `_target_language_for_path`/`_provider_language_for_path` already distinguish
# "javascript" from "typescript" today.
# ---------------------------------------------------------------------------


def _python_references_and_calls_for_registry(
    path: Path, symbol: str, repo_root: Path | str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _python_references_and_calls(path, symbol)


def _python_provider_alias_calls_for_registry(
    path: Path, symbol: str, repo_root: Path | str | None = None
) -> list[dict[str, Any]]:
    return _python_provider_alias_calls(path, symbol)


def _python_import_update_target_for_registry(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    return _python_import_update_target(file_path, symbol, definition_path)


lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="python",
        suffixes=frozenset({".py"}),
        grammar_modules=(),
        parser_for_path=None,
        provenance_when_parsed="python-ast",
        provenance_when_missing="python-ast",
        import_markers=(b"import ", b"from "),
        def_node_kinds=("ClassDef", "FunctionDef", "AsyncFunctionDef"),
        extract_imports_and_symbols=_python_imports_and_symbols,
        references_and_calls=_python_references_and_calls_for_registry,
        provider_alias_calls=_python_provider_alias_calls_for_registry,
        file_imports_symbol_from_definition=_python_file_imports_symbol_from_definition,
        import_update_target=_python_import_update_target_for_registry,
        prime_repo_context=None,
        classify_ref_kind=_python_classify_ref_kind,
    )
)

_JS_TS_REGISTRY_SHARED_KWARGS: dict[str, Any] = {
    "grammar_modules": ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"),
    "provenance_when_parsed": "tree-sitter",
    "provenance_when_missing": "regex-heuristic",
    "import_markers": (b"import ", b"from ", b"require("),
    "def_node_kinds": ("function_declaration", "class_declaration", "method_definition"),
    "extract_imports_and_symbols": None,
    "references_and_calls": _js_ts_references_and_calls,
    "provider_alias_calls": _js_ts_provider_alias_calls,
    "file_imports_symbol_from_definition": _js_ts_file_imports_symbol_from_definition,
    "import_update_target": _js_ts_import_update_target,
    "prime_repo_context": _prime_js_ts_repo_context,
    "classify_ref_kind": _js_ts_classify_ref_kind,
}

lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="javascript",
        suffixes=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
        parser_for_path=lambda path: _javascript_parser(),
        **_JS_TS_REGISTRY_SHARED_KWARGS,
    )
)

lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="typescript",
        suffixes=frozenset({".ts", ".tsx"}),
        parser_for_path=lambda path: _typescript_parser(tsx=path.suffix == ".tsx"),
        **_JS_TS_REGISTRY_SHARED_KWARGS,
    )
)

lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="rust",
        suffixes=frozenset({".rs"}),
        grammar_modules=("tree_sitter", "tree_sitter_rust"),
        parser_for_path=lambda path: _rust_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="regex-heuristic",
        import_markers=(b"use ",),
        def_node_kinds=("function_item", "struct_item", "enum_item", "trait_item"),
        extract_imports_and_symbols=None,
        references_and_calls=_rust_references_and_calls,
        provider_alias_calls=_rust_provider_alias_calls,
        file_imports_symbol_from_definition=_rust_file_imports_symbol_from_definition,
        import_update_target=_rust_import_update_target,
        prime_repo_context=_prime_rust_repo_context,
        classify_ref_kind=_rust_classify_ref_kind,
    )
)

# PATH A Stage 1: first language expansion beyond the original four. Go's grammar/import model
# is simpler than Rust's (package == directory, no mod-tree to walk), so its extractor lives in
# its own module (lang_go.py) rather than inline here -- see that module's docstring for the
# no-import-cycle rationale. provenance_when_missing="grammar-missing" (NOT "regex-heuristic")
# is what makes a grammar-absent Go file a genuine `resolution_gaps` entry instead of a silent
# empty result (Go has no regex fallback, unlike JS/TS/Rust).
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="go",
        suffixes=frozenset({".go"}),
        grammar_modules=("tree_sitter", "tree_sitter_go"),
        parser_for_path=lambda path: lang_go._go_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"import ",),
        def_node_kinds=(
            "function_declaration",
            "method_declaration",
            "type_spec",
            "const_spec",
            "var_spec",
        ),
        extract_imports_and_symbols=None,
        references_and_calls=lang_go.go_references_and_calls,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=lang_go.go_file_imports_symbol_from_definition,
        import_update_target=None,
        prime_repo_context=lang_go.prime_go_repo_context,
        classify_ref_kind=None,
    )
)

# PATH A Stage 2: Java joins the symbol graph as a FOUNDATIONAL-TIER language -- symbols
# (classes/interfaces/enums/records/methods/constructors) and raw import declarations flow into
# build_repo_map / `tg defs` / `tg source` / `tg imports` / `tg agent` via
# _java_imports_and_symbols / _java_parser_symbol_sources / _java_imports_with_lines. The deep
# caller-graph tier (references_and_calls, provider_alias_calls,
# file_imports_symbol_from_definition, import_update_target, prime_repo_context,
# classify_ref_kind -- cross-file method-call resolution powering `tg callers`/
# `tg blast-radius`) is intentionally left None here, deferred to a follow-up PR (see the
# feat/java-symbol-intelligence PR body). provenance_when_missing="grammar-missing" (NOT
# "regex-heuristic"): Java has no regex fallback, mirroring Go's fail-closed contract.
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="java",
        suffixes=frozenset({".java"}),
        grammar_modules=("tree_sitter", "tree_sitter_java"),
        parser_for_path=lambda path: _java_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"import ",),
        def_node_kinds=(
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
            "method_declaration",
            "constructor_declaration",
        ),
        extract_imports_and_symbols=_java_imports_and_symbols,
        references_and_calls=None,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=None,
        import_update_target=None,
        prime_repo_context=None,
        classify_ref_kind=None,
    )
)

# PATH A Stage 1 (second language expansion beyond Go): PHP's own module (lang_php.py), same
# no-import-cycle rationale as lang_go.py. NARROWER than Go's landing -- this registration ships
# defs + imports only; the cross-file caller-graph (references_and_calls and the other three
# callables below) is explicitly deferred to a follow-up, so all four are None here. That is a
# strict subset of an already-handled shape: _language_coverage_gaps_for_universe already treats
# import_update_target=None as an honest resolution_gaps entry (see the "audit #81 #4" comment on
# that function, and lang_go.py's own import_update_target=None precedent), so `tg callers`/
# `tg blast-radius` stay honest about PHP instead of silently reading as a proven zero.
# provenance_when_missing="grammar-missing" (NOT "regex-heuristic") for the same fail-closed
# reason as Go: PHP has no regex-heuristic fallback.
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="php",
        suffixes=frozenset({".php"}),
        grammar_modules=("tree_sitter", "tree_sitter_php"),
        parser_for_path=lambda path: lang_php._php_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"use ",),
        def_node_kinds=(
            "class_declaration",
            "interface_declaration",
            "trait_declaration",
            "enum_declaration",
            "function_definition",
            "method_declaration",
        ),
        extract_imports_and_symbols=None,
        references_and_calls=None,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=None,
        import_update_target=None,
        prime_repo_context=None,
        classify_ref_kind=None,
    )
)

# PATH A Stage 1 (second expansion, alongside Go): C# gets its own module (lang_csharp.py) for
# the same no-import-cycle reason as Go. FOUNDATIONAL scope only -- defs/source/imports/agent
# via `extract_imports_and_symbols`-shaped extraction, wired at the `_imports_and_symbols_for_path`
# / `build_symbol_source_from_map` dispatch sites below. The cross-file caller-graph
# (references_and_calls / file_imports_symbol_from_definition / import_update_target / repo-root
# context priming for a future .csproj/namespace resolver) is DEFERRED to a follow-up -- all four
# stay None here, same shape as Go's own `import_update_target=None` gap, so `tg refs`/`tg
# callers`/`tg blast-radius` on a C# symbol fall through to the generic
# `_regex_references_and_calls` text-heuristic path instead of crashing or fabricating an
# AST-verified match. provenance_when_missing="grammar-missing" (NOT "regex-heuristic") is what
# makes a grammar-absent C# file a genuine `resolution_gaps` entry instead of a silent empty
# result (C# has no regex fallback, unlike JS/TS/Rust).
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="csharp",
        suffixes=frozenset({".cs"}),
        grammar_modules=("tree_sitter", "tree_sitter_c_sharp"),
        parser_for_path=lambda path: lang_csharp._csharp_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"using ",),
        def_node_kinds=(
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
            "record_declaration",
            "method_declaration",
            "constructor_declaration",
        ),
        extract_imports_and_symbols=None,
        references_and_calls=None,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=None,
        import_update_target=None,
        prime_repo_context=None,
        classify_ref_kind=None,
    )
)

# PATH A Stage 3: C joins the symbol graph as a FOUNDATIONAL-TIER language (top-10 language
# campaign, Phase 1 of C/C++ -- C++ is a SEPARATE follow-up, not built here). Own module
# (lang_c.py) for the same no-import-cycle reason as Go/PHP/C#. FOUNDATIONAL scope only --
# defs/source/imports/agent via `extract_imports_and_symbols`-shaped extraction, wired at the
# `_imports_and_symbols_for_path` / `build_symbol_source_from_map` dispatch sites below. The
# cross-file caller-graph (references_and_calls / file_imports_symbol_from_definition /
# import_update_target / repo-root context priming for a future `#include`-path resolver) is
# DEFERRED to a follow-up -- all four stay None here, same shape as Go/PHP/C#'s own
# `import_update_target=None` gap, so `tg refs`/`tg callers`/`tg blast-radius` on a C symbol fall
# through to the generic `_regex_references_and_calls` text-heuristic path instead of crashing or
# fabricating an AST-verified match. provenance_when_missing="grammar-missing" (NOT
# "regex-heuristic") is what makes a grammar-absent C file a genuine `resolution_gaps` entry
# instead of a silent empty result (C has no regex fallback, unlike JS/TS/Rust).
#
# ".h" is deliberately NOT in suffixes -- `_provider_language_for_path` (below) already assigns
# every C/C++ header suffix to "cpp" (tree-sitter-cpp is a strict grammar superset of C), so a
# future lang_cpp.py is the natural owner of ".h"; claiming it here would disagree with that
# pre-existing assignment and fail test_target_and_provider_language_agree_with_registry. See
# lang_c.py's module docstring for the full header-ambiguity rationale.
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="c",
        suffixes=frozenset({".c"}),
        grammar_modules=("tree_sitter", "tree_sitter_c"),
        parser_for_path=lambda path: lang_c._c_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"#include",),
        def_node_kinds=(
            "function_definition",
            "declaration",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "type_definition",
        ),
        extract_imports_and_symbols=None,
        references_and_calls=None,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=None,
        import_update_target=None,
        prime_repo_context=None,
        classify_ref_kind=None,
    )
)

# PATH A Stage 3 (Phase 2 of C/C++): C++ joins the symbol graph as a FOUNDATIONAL-TIER language,
# closing the top-10 language-support campaign to 10/10. Own module (lang_cpp.py) for the same
# no-import-cycle reason as C/Go/PHP/C# -- a SEPARATE LanguageSpec + grammar package
# (tree-sitter-cpp) from C's, mirroring the shipped JS/TS "two specs, not one mode flag"
# precedent (``_SPEC_BY_SUFFIX`` is a flat suffix->ONE-spec dict, so ".h" cannot belong to both).
# FOUNDATIONAL scope only -- defs/source/imports/agent via `extract_imports_and_symbols`-shaped
# extraction, wired at the `_imports_and_symbols_for_path` / `build_symbol_source_from_map`
# dispatch sites below. The cross-file caller-graph (references_and_calls /
# file_imports_symbol_from_definition / import_update_target / repo-root context priming for a
# future `#include`-path resolver) is DEFERRED to a follow-up -- all four stay None here, same
# shape as C/Go/PHP/C#'s own `import_update_target=None` gap, so `tg refs`/`tg callers`/`tg
# blast-radius` on a C++ symbol fall through to the generic `_regex_references_and_calls`
# text-heuristic path instead of crashing or fabricating an AST-verified match.
# provenance_when_missing="grammar-missing" (NOT "regex-heuristic") is what makes a
# grammar-absent C++ file a genuine `resolution_gaps` entry instead of a silent empty result
# (C++ has no regex fallback, unlike JS/TS/Rust).
#
# ".h" IS in suffixes here (unlike lang_c.py, which deliberately excludes it) --
# `_provider_language_for_path` (below) ALREADY assigns every C/C++ header suffix to "cpp"
# (tree-sitter-cpp is a strict grammar superset of C), so this module is the pre-existing,
# forced owner of ".h"/".hh"/".hpp"/".hxx" as well as ".cc"/".cpp"/".cxx". See lang_cpp.py's
# module docstring for the full header-ambiguity rationale.
lang_registry.register_language(
    lang_registry.LanguageSpec(
        language_id="cpp",
        suffixes=frozenset(_CPP_SUFFIXES),
        grammar_modules=("tree_sitter", "tree_sitter_cpp"),
        parser_for_path=lambda path: lang_cpp._cpp_parser(),
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(b"#include",),
        def_node_kinds=(
            "function_definition",
            "declaration",
            "field_declaration",
            "class_specifier",
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "namespace_definition",
            "template_declaration",
            "type_definition",
            "alias_declaration",
        ),
        extract_imports_and_symbols=None,
        references_and_calls=None,
        provider_alias_calls=None,
        file_imports_symbol_from_definition=None,
        import_update_target=None,
        prime_repo_context=None,
        classify_ref_kind=None,
    )
)


def _prime_all_language_repo_contexts(context_root: Path) -> None:
    """Prime every registered language's per-repo-root context exactly once.

    Registry-driven replacement for the previous hardcoded
    ``_prime_js_ts_repo_context(...); _prime_rust_repo_context(...)`` pair. javascript and
    typescript are separate LanguageSpec entries that both point at the SAME
    ``_prime_js_ts_repo_context`` callable -- dedupe by callable identity so a repo with both
    suffixes present still primes the shared JS/TS context exactly once, matching prior
    behavior (which called it a single time regardless of how many JS/TS suffixes appeared).
    """
    primed: set[int] = set()
    for spec in lang_registry.LANGUAGE_REGISTRY.values():
        if spec.prime_repo_context is None:
            continue
        if id(spec.prime_repo_context) in primed:
            continue
        primed.add(id(spec.prime_repo_context))
        spec.prime_repo_context(context_root)


def _imports_and_symbols_for_path(
    path: Path,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    # O2: skip files that exceed the per-file byte cap to bound RSS spikes from
    # multi-MB bundled/generated files.  The cap is configurable via the
    # TENSOR_GREP_MAX_PARSE_BYTES env var (default 2 MB).
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return [], []
    with _profiling_phase(_profiling_collector, "file_parse"):
        current_imports, current_symbols = _python_imports_and_symbols(path)
        spec = lang_registry.spec_for_path(path)
        if spec is not None and spec.language_id in ("javascript", "typescript"):
            current_imports, regex_symbols = _regex_imports_and_symbols(path)
            current_symbols = _dedupe_symbol_records([
                *_js_ts_parser_symbols(path),
                *regex_symbols,
            ])
        elif spec is not None and spec.language_id == "rust":
            current_imports, _ = _regex_imports_and_symbols(path)
            current_symbols = _rust_parser_symbols(path)
            if not current_symbols:
                _, current_symbols = _regex_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "go":
            # Fail-closed (Stage 1 trap): NO regex fallback for Go. A grammar-missing Go file
            # returns ([], []) here -- surfaced honestly via `resolution_gaps`, never silently
            # degraded to a text heuristic the way JS/TS/Rust are.
            current_imports, current_symbols = lang_go.go_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "java":
            # Fail-closed (same Stage 1 trap as Go): NO regex fallback for Java either.
            current_imports, current_symbols = _java_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "php":
            # Fail-closed (Stage 1 trap, same as Go): NO regex fallback for PHP either -- see
            # lang_php.py's module docstring.
            current_imports, current_symbols = lang_php.php_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "csharp":
            # Fail-closed (Stage 1 trap, same as Go): NO regex fallback for C#. A grammar-missing
            # .cs file returns ([], []) here -- surfaced honestly via `resolution_gaps`.
            current_imports, current_symbols = lang_csharp.csharp_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "c":
            # Fail-closed (Stage 1 trap, same as Go): NO regex fallback for C. A grammar-missing
            # .c file returns ([], []) here -- surfaced honestly via `resolution_gaps`.
            current_imports, current_symbols = lang_c.c_imports_and_symbols(path)
        elif spec is not None and spec.language_id == "cpp":
            # Fail-closed (Stage 1 trap, same as Go/C): NO regex fallback for C++ either. A
            # grammar-missing C++ file returns ([], []) here -- surfaced honestly via
            # `resolution_gaps`.
            current_imports, current_symbols = lang_cpp.cpp_imports_and_symbols(path)
        elif not current_imports and not current_symbols:
            current_imports, current_symbols = _regex_imports_and_symbols(path)
        return current_imports, current_symbols


# #74 moat: `tg imports`/`tg importers` -- the scoped file-dependency primitive. Companion to
# `_imports_and_symbols_for_path` above, which collapses imports to a deduped, line-less
# `list[str]` (fine for the reverse-import alias graph, useless for a command that must report
# *where* each import statement lives). Mirrors that function's per-language extraction sources
# exactly (same AST node types / same regexes as `_python_imports_and_symbols` and
# `_regex_imports_and_symbols`) so raw recall stays identical -- this only adds the line number
# `tg imports` needs and keeps one row per import STATEMENT (not one row per imported symbol),
# which is the right unit for a file-dependency primitive.
def _python_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return []
    try:
        tree = _cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    dynamic_entries: list[dict[str, Any]] = []
    # Nested-scope recall fix: `ast.walk` (not `tree.body`) so a plain `import`/`from ... import`
    # STATEMENT nested inside a function body, an `if`/`try` block, or an `if TYPE_CHECKING:`
    # guard is collected too -- `tree.body` only ever visited module-top-level statements,
    # silently missing anything scope-nested (a `tg imports`/`tg importers` recall gap;
    # `result_incomplete` stayed False, so the omission was invisible).
    #
    # opt10 F4.2 speed fix: this single walk ALSO picks up `__import__`/`import_module`/
    # `importlib.import_module` CALLS -- the #93 SUB-1 dynamic-import shape -- via
    # `_python_dynamic_import_entry_for_call` (originally the extracted per-node half of a
    # whole-tree helper, `_python_dynamic_import_entries`), instead of a SEPARATE second
    # `ast.walk(tree)` over the same tree the way this used to call that function wholesale.
    # `ast.Import`/`ast.ImportFrom` and `ast.Call` are disjoint node types, so folding both checks
    # into one walk and accumulating into two separate lists (`entries` for static,
    # `dynamic_entries` for dynamic) produces the IDENTICAL two per-kind orderings `ast.walk`
    # would produce run separately -- `ast.walk` is a deterministic traversal of a fixed tree, so
    # filtering it once for two disjoint predicates and concatenating the two result lists
    # (`entries + dynamic_entries`, same order as the old
    # `entries.extend(_python_dynamic_import_entries(tree))`) is exactly equivalent to filtering
    # it twice. See test_python_imports_with_lines_merges_dynamic_walk_into_single_ast_walk_pass
    # (walk-count + order-identity proof). `_python_dynamic_import_entries` itself -- the
    # whole-tree helper this per-node check was extracted from -- kept its own separate `ast.walk`
    # alive at the time (opt10 F4.2) purely for its OTHER remaining caller,
    # `_python_imports_and_symbols`; opt10 lever-1 later migrated that caller to this same
    # per-node helper too, leaving `_python_dynamic_import_entries` with zero callers, so it was
    # removed as dead code.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                entries.append({"module": alias.name, "line": int(node.lineno), "level": 0})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                entries.append({
                    "module": node.module,
                    "line": int(node.lineno),
                    "level": int(node.level or 0),
                })
            elif node.level:
                # `from . import x` / `from .. import x` -- no dotted module text, only
                # relative dots plus the imported names, which may themselves be
                # submodules (e.g. `from . import utils` importing sibling `utils.py`).
                for alias in node.names:
                    entries.append({
                        "module": alias.name,
                        "line": int(node.lineno),
                        "level": int(node.level),
                    })
        elif isinstance(node, ast.Call):
            dynamic_entry = _python_dynamic_import_entry_for_call(node)
            if dynamic_entry is not None:
                dynamic_entries.append(dynamic_entry)
    entries.extend(dynamic_entries)
    return entries


def _js_ts_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return []
    try:
        lines = _read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        import_match = re.match(r'^\s*import\s+.*?from\s+["\']([^"\']+)["\']', line)
        export_from_match = re.match(r'^\s*export\s+.*?from\s+["\']([^"\']+)["\']', line)
        require_match = re.match(
            r"^\s*(?:const|let|var)\s+(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)"
            r'\s*=\s*require\(["\']([^"\']+)["\']\)',
            line,
        )
        if import_match:
            entries.append({"module": import_match.group(1), "line": line_number})
        if export_from_match:
            entries.append({"module": export_from_match.group(1), "line": line_number})
        if require_match:
            entries.append({"module": require_match.group(1), "line": line_number})
        else:
            # #93 SUB-1: `import("x")` call-form and a require(...) not shaped like the
            # assignment-anchored regex above (bare, chained, or a sub-expression argument).
            dynamic_hit = _js_ts_dynamic_import_hit(line)
            if dynamic_hit is not None:
                module, dynamic_unresolved = dynamic_hit
                entries.append({
                    "module": module,
                    "line": line_number,
                    "dynamic": True,
                    "dynamic_unresolved": dynamic_unresolved,
                })
    return entries


def _rust_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return []
    try:
        lines = _read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        # Same single-line `use ... ;` regex as `_regex_imports_and_symbols` -- a brace-group
        # `use` spanning multiple lines is a pre-existing extraction gap there too, not a new
        # one introduced here (recall gaps must stay honest, not silently "fixed" here only).
        use_match = re.match(r"^\s*use\s+([^;]+);", line)
        if use_match:
            entries.append({"module": use_match.group(1).strip(), "line": line_number})
    return entries


def _imports_with_lines_for_path(path: Path) -> list[dict[str, Any]]:
    """Raw per-statement imports with 1-based line numbers for the 10 supported languages.

    Returns ``[]`` for an unsupported language (e.g. Kotlin) or an over-cap file -- callers that
    need to distinguish "genuinely no imports" from "not scanned" must check those conditions
    themselves (see ``build_file_imports``'s ``result_incomplete`` handling).
    """
    spec = lang_registry.spec_for_path(path)
    if spec is None:
        return []
    if spec.language_id == "python":
        return _python_imports_with_lines(path)
    if spec.language_id in ("javascript", "typescript"):
        return _js_ts_imports_with_lines(path)
    if spec.language_id == "rust":
        return _rust_imports_with_lines(path)
    if spec.language_id == "java":
        return _java_imports_with_lines(path)
    if spec.language_id in ("go", "php", "csharp", "c", "cpp"):
        # go/php/csharp/c/cpp's own extractors (lang_go.py/lang_php.py/lang_csharp.py/lang_c.py/
        # lang_cpp.py) mirror their `_X_imports_and_symbols` siblings, which get this SAME cap
        # check for free from THEIR caller (`_imports_and_symbols_for_path`, above) rather than
        # self-guarding -- applied here once, at this dispatcher, for the identical reason.
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > _max_parse_bytes():
            return []
        if spec.language_id == "go":
            return lang_go.go_imports_with_lines(path)
        if spec.language_id == "php":
            return lang_php.php_imports_with_lines(path)
        if spec.language_id == "csharp":
            return lang_csharp.csharp_imports_with_lines(path)
        if spec.language_id == "c":
            return lang_c.c_imports_with_lines(path)
        return lang_cpp.cpp_imports_with_lines(path)
    return []


def _python_module_parts(module_name: str) -> list[str]:
    return [part for part in module_name.split(".") if part]


def _python_relative_base_dir(importer_path: Path, level: int) -> Path:
    # PEP 328: level=1 ("from . import x") resolves relative to the importer's OWN package
    # dir (its parent); level=2 ("from .. import x") goes one dir further up, etc.
    current = importer_path.parent
    for _ in range(max(0, level - 1)):
        current = current.parent
    return current


# #152 fix (CEO v1.69.3 dogfood, 2 HIGH): a Python file that path-hacks its own module
# resolution via `sys.path.insert(...)`/`sys.path.append(...)` -- a common same-repo vendoring
# idiom, e.g.:
#
#     import sys, os
#     sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
#     from ultrathink_routing import route   # lib/ultrathink_routing.py
#
# -- used to be invisible to `_python_candidate_roots` below (whose docstring said so outright:
# "no `sys.path` to consult") and, transitively, to both its forward (`tg imports`) and reverse
# (`tg importers`) consumers, which BOTH funnel through it via `_python_module_candidates` --
# fixing that one chokepoint fixes both directions instead of duplicating the logic twice.
#
# Deliberately narrow: only a handful of common, STATICALLY-resolvable directory-argument idioms
# are recognized --
#   * a bare string literal:              sys.path.insert(0, "lib")
#   * os.path.join(DIRNAME_EXPR, "SUB"[, "SUB2", ...])
#   * DIRNAME_EXPR alone (os.path.dirname(__file__) / os.path.dirname(os.path.abspath(__file__)))
#   * Path(__file__).parent / "SUB" (chained; optionally str(...)-wrapped)
#   * os.path.join(HERE, "SUB") where HERE = os.path.dirname(__file__) earlier in the module
# -- anything with a dynamic/computed component (a variable holding an unknown value, an
# f-string, an environment lookup, any non-literal expression) is left alone: the module stays
# `external`/`resolved=None`, honest, the same fail-closed posture as every other resolver in
# this file. A resolved directory is also required to EXIST and stay INSIDE the scanned repo
# root (`_path_is_relative_to`, the same containment guard used elsewhere in this module) -- a
# `..`-escape or an absolute path outside the root is silently ignored, never followed.
def _python_sys_path_dunder_file(node: ast.AST) -> bool:
    """True for the bare `__file__` name expression."""
    return isinstance(node, ast.Name) and node.id == "__file__"


def _python_sys_path_os_path_call_args(node: ast.AST, attr: str) -> list[ast.expr] | None:
    """If `node` is exactly `os.path.<attr>(...)` (the literal dotted chain -- an aliased
    `import os.path as op` or `from os.path import dirname` is left alone), return its call
    arguments; else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == attr
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "os"
    ):
        return None
    return node.args


def _python_sys_path_file_dirname_expr(node: ast.AST) -> bool:
    """True for `os.path.dirname(__file__)` or `os.path.dirname(os.path.abspath(__file__))` --
    both mean "this file's own directory"."""
    args = _python_sys_path_os_path_call_args(node, "dirname")
    if args is None or len(args) != 1:
        return False
    arg = args[0]
    if _python_sys_path_dunder_file(arg):
        return True
    abspath_args = _python_sys_path_os_path_call_args(arg, "abspath")
    return (
        abspath_args is not None
        and len(abspath_args) == 1
        and _python_sys_path_dunder_file(abspath_args[0])
    )


def _python_sys_path_file_parent_expr(node: ast.AST) -> bool:
    """True for `Path(__file__).parent` (bare `Path` or a dotted `pathlib.Path`) -- the pathlib
    equivalent of `_python_sys_path_file_dirname_expr`."""
    if not (isinstance(node, ast.Attribute) and node.attr == "parent"):
        return False
    call = node.value
    if not (isinstance(call, ast.Call) and len(call.args) == 1):
        return False
    if not _python_sys_path_dunder_file(call.args[0]):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "Path"
    return isinstance(func, ast.Attribute) and func.attr == "Path"


def _python_sys_path_file_dir_expr(node: ast.AST) -> bool:
    """True for any expression meaning "this file's own directory" (os.path or pathlib style)."""
    return _python_sys_path_file_dirname_expr(node) or _python_sys_path_file_parent_expr(node)


def _python_sys_path_static_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _python_sys_path_join_suffix(node: ast.AST, here_names: frozenset[str]) -> str | None:
    """`os.path.join(FILE_DIR_EXPR, "SUB"[, "SUB2", ...])` -> `"SUB/SUB2"` (a single "/"-joined
    suffix to append to the file's own directory), or None if `node` isn't this shape or any
    "SUB" component isn't a plain string literal. `here_names` lets a `HERE = os.path.dirname(
    __file__)`-style module-level alias (see `_python_sys_path_here_aliases`) stand in for the
    literal FILE_DIR_EXPR as the join's first argument."""
    args = _python_sys_path_os_path_call_args(node, "join")
    if not args:
        return None
    first, *rest = args
    is_file_dir = _python_sys_path_file_dir_expr(first) or (
        isinstance(first, ast.Name) and first.id in here_names
    )
    if not is_file_dir or not rest:
        return None
    parts: list[str] = []
    for arg in rest:
        literal = _python_sys_path_static_str(arg)
        if literal is None:
            return None
        parts.append(literal)
    return "/".join(parts)


def _python_sys_path_truediv_suffix(node: ast.AST) -> str | None:
    """`Path(__file__).parent / "SUB"` (chained divisions allowed, optionally `str(...)`-wrapped)
    -> `"SUB"`, or None if `node` isn't this shape or any segment isn't a plain string literal."""
    current: ast.AST = node
    if (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Name)
        and current.func.id == "str"
        and len(current.args) == 1
    ):
        current = current.args[0]
    parts: list[str] = []
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        literal = _python_sys_path_static_str(current.right)
        if literal is None:
            return None
        parts.append(literal)
        current = current.left
    if not parts or not _python_sys_path_file_parent_expr(current):
        return None
    parts.reverse()
    return "/".join(parts)


def _python_sys_path_here_aliases(tree: ast.Module) -> frozenset[str]:
    """Module-level `HERE = os.path.dirname(__file__)`-style aliases (the optional bullet #5 of
    the #152 idiom list above) -- lets `os.path.join(HERE, "SUB")` resolve the same as spelling
    the dirname expression out inline. Deliberately broad-recall (`ast.walk`, not just
    `tree.body`), matching this module's established nested-scope extraction posture -- a name
    later reassigned to something else is a rare, low-risk over-recognition: it only ever WIDENS
    which directories get tried, it never resolves to a wrong FILE (the final candidate still
    has to exist on disk, inside the repo root)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and _python_sys_path_file_dir_expr(node.value):
            names.add(target.id)
    return frozenset(names)


def _python_sys_path_insert_or_append_arg(node: ast.Call) -> ast.expr | None:
    """`sys.path.insert(idx, ARG)` / `sys.path.append(ARG)` -> `ARG`, else None. Only the plain,
    unaliased `sys.path` attribute chain is recognized (`import sys` then `sys.path....`) -- an
    aliased `sys` import (`import sys as _sys`) is left alone, the same fail-closed posture as
    every other idiom this fix does not try to statically resolve."""
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    ):
        return None
    if func.attr == "insert" and len(node.args) >= 2:
        return node.args[1]
    if func.attr == "append" and len(node.args) >= 1:
        return node.args[0]
    return None


def _python_sys_path_arg_to_dir(
    arg: ast.expr, filedir: Path, here_names: frozenset[str]
) -> Path | None:
    """Resolve one `sys.path.insert`/`.append` directory ARGUMENT expression to an absolute
    `Path` relative to `filedir` (the importing file's own directory) -- or None if `arg` isn't
    one of the recognized static idioms."""
    if _python_sys_path_file_dir_expr(arg):
        return filedir
    join_suffix = _python_sys_path_join_suffix(arg, here_names)
    if join_suffix is not None:
        return filedir / join_suffix
    truediv_suffix = _python_sys_path_truediv_suffix(arg)
    if truediv_suffix is not None:
        return filedir / truediv_suffix
    literal = _python_sys_path_static_str(arg)
    if literal is not None:
        return filedir / literal
    return None


@_mtime_aware_cache(maxsize=1024)  # #152 fix: mtime+size in key; one AST walk per file, shared
def _python_sys_path_hack_dirs(path_str: str) -> tuple[str, ...]:
    """Statically-resolvable absolute directories this Python file adds to `sys.path` via
    `sys.path.insert`/`sys.path.append` (see the idiom list in the block comment above). Returns
    `()` for a file with no such calls, or where every call's directory argument is a
    non-literal/dynamic expression.

    Cached by (path, mtime, size) -- a pure function of the file's own source text -- so a file
    with N raw import entries (`_python_candidate_roots` runs once PER entry) parses and walks
    its own AST for this exactly once, not N times.

    Deliberately returns raw, un-containment-checked strings: existence + "stays inside the
    scanned repo root" is enforced by the caller (`_python_sys_path_hack_roots`), which has the
    `repo_root` this function does not need in its cache key -- the same file's sys.path hacks
    resolve to the same absolute dirs regardless of which root the caller is scanning from.
    """
    try:
        file_size = Path(path_str).stat().st_size
    except OSError:
        file_size = 0
    if file_size > _max_parse_bytes():
        return ()
    try:
        source = _read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return ()
    if "sys.path" not in source:
        # Fast-reject the overwhelming common case (no sys.path manipulation at all) without
        # paying for a full `ast.walk` -- both recognized calls (`sys.path.insert`/`.append`)
        # always contain this literal substring, so this can never skip a real hit.
        return ()
    try:
        tree = _cached_ast_parse(source)
    except SyntaxError:
        return ()

    filedir = Path(path_str).parent
    here_names = _python_sys_path_here_aliases(tree)
    dirs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arg = _python_sys_path_insert_or_append_arg(node)
        if arg is None:
            continue
        resolved_dir = _python_sys_path_arg_to_dir(arg, filedir, here_names)
        if resolved_dir is not None:
            dirs.append(str(resolved_dir))
    return tuple(dict.fromkeys(dirs))


def _python_sys_path_hack_roots(
    importer_path: Path, repo_root: Path | str | None
) -> tuple[Path, ...]:
    """Existing, containment-checked sys.path-hacked directories for `importer_path` (raw
    extraction: `_python_sys_path_hack_dirs`). Shared by `_python_candidate_roots` (folds these
    into the general search-root list, tried FIRST) and `_python_module_candidates` (tags the
    winning candidate's provenance as "sys-path-insert") so the existence/containment check
    itself lives in exactly one place. Returns `()` when `repo_root` is unknown (`None`) -- no
    root means no containment boundary to enforce, so this resolves nothing rather than guess.
    """
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return ()
    validated: list[Path] = []
    for hacked_dir in _python_sys_path_hack_dirs(str(importer_path)):
        candidate_dir = Path(hacked_dir)
        if candidate_dir.is_dir() and _path_is_relative_to(candidate_dir, normalized_root):
            validated.append(candidate_dir)
    return tuple(validated)


def _python_candidate_roots(importer_path: Path, repo_root: Path | str | None) -> list[Path]:
    """Plausible absolute-import search roots for a Python file.

    Unlike JS/TS (tsconfig baseUrl/paths) or Rust (Cargo.toml workspace members), tensor-grep
    has no primed "project context" for Python module resolution -- this is the net-new
    resolution seam the #74 design flagged as the highest-risk part of `tg imports`. Tries, in
    order: any directory the file itself adds via a statically-resolvable
    `sys.path.insert`/`.append` call (#152 fix -- see `_python_sys_path_hack_roots`), the repo
    root, a `src/` layout root, the importer's own directory, and each ancestor directory up to
    the repo root (covers same-package absolute imports without a full `sys.path` simulation). A
    bare specifier that is a local workspace package NOT reachable via one of these roots is
    honestly misclassified as external -- see the module docstring risk note; recall gaps here
    are disclosed via ``external``/``unresolved``, never silently hidden.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            roots.append(candidate)

    for hacked_root in _python_sys_path_hack_roots(importer_path, repo_root):
        _add(hacked_root)
    normalized_root = _normalized_repo_root(repo_root)
    _add(normalized_root)
    if normalized_root is not None:
        _add(normalized_root / "src")
    current = importer_path.parent
    _add(current)
    if normalized_root is not None:
        try:
            current.relative_to(normalized_root)
            within_root = True
        except ValueError:
            within_root = False
        if within_root:
            while current != normalized_root:
                current = current.parent
                _add(current)
    # Walk up past every `__init__.py`-marked package directory: the first ancestor WITHOUT
    # one is the natural Python "import root" for an absolute dotted import (e.g. `pkg.helpers`
    # written inside `pkg/main.py` resolves relative to pkg's PARENT, not pkg itself). This
    # covers the common case where no project-root marker file exists at all.
    package_top = importer_path.parent
    while (package_top / "__init__.py").exists():
        parent = package_top.parent
        if parent == package_top:
            break
        package_top = parent
    _add(package_top)
    return roots


def _python_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> dict[str, Any]:
    parts = _python_module_parts(module_name)
    if not parts:
        return {"paths": [], "provenance": [], "confidence": 0.0, "path_provenance": {}}

    # opt10 F4.3 speed fast-path: skip the multi-root candidate-path construction below (2
    # `Path` builds per root, each pushed through the `_resolved_path_str` resolve-and-dedupe
    # machinery -- ~10-12 `Path.resolve()` calls for a typical root count, PLUS the caller's own
    # `.is_file()` probe of every returned candidate) for a bare top-level stdlib import
    # (`import os` / `import sys` / `import json`) -- the dominant import shape, 59-100% of
    # imports in sampled real files per the opt10 speed campaign.
    #
    # SHADOW-SAFETY (the whole correctness risk of this fast-path): `parts[0] in
    # sys.stdlib_module_names` alone is NOT sufficient -- a repo can ship a same-named top-level
    # module (e.g. a local `json.py` at its root) that MUST still resolve to that local file, the
    # same way it would via the general path below (see
    # test_build_file_imports_stdlib_shadowed_by_local_module_resolves_to_local_file). So this
    # only returns the fast-path shape after confirming NEITHER shape the general path's
    # level==0 branch would also probe (`<root>/<name>.py`, `<root>/<name>/__init__.py`) exists
    # as a real file at ANY of `_python_candidate_roots`' roots -- the exact same roots (repo
    # root, src/ layout, sys-path-hacked dirs, importer's own dir and ancestors, package-top)
    # the general path already computes, just probed with a cheap `.is_file()`/`.is_dir()` stat
    # instead of building+resolving+deduping the full candidate list. Any doubt (an `OSError`
    # probing a candidate, or `parts[0]` existing locally at all) falls CLOSED to the unchanged
    # general path below, never guesses.
    #
    # Narrowed to `len(parts) == 1` (a bare `import json`, not a dotted `import os.path`):
    # a dotted stdlib access still needs `root/parts[0]` to be an existing local DIRECTORY for
    # any local shadow to be possible at all, so the `is_dir()` probe below already catches that
    # case too and correctly falls through -- but the deeper submodule candidates the general
    # path would build (`root/parts[0]/parts[1]/...`) are not worth fast-pathing separately here,
    # so leave every dotted access on the general path unconditionally.
    #
    # Returns EXACTLY the shape the general (non-relative) branch below always sets for
    # `provenance`/`confidence` -- unconditionally, before any candidate is even probed for
    # existence -- so `_resolve_raw_import_entry` / `_python_module_match_details` read the
    # identical values off this dict as they would off the general path's result for a module
    # that genuinely has zero real candidates (see the opt10 PR body's captured baseline: an
    # empty `paths: []` here is observationally identical to the general path's non-empty-but-
    # entirely-nonexistent candidate list -- both make `resolved`/`matched` come out the same on
    # the calling side, since neither contains a real file).
    if level == 0 and len(parts) == 1 and parts[0] in sys.stdlib_module_names:
        name = parts[0]
        shadowed = False
        for root in _python_candidate_roots(importer_path, repo_root):
            try:
                if (root / f"{name}.py").is_file() or (root / name).is_dir():
                    shadowed = True
                    break
            except OSError:
                shadowed = True  # can't prove no local shadow -- fail closed to the slow path
                break
        if not shadowed:
            return {
                "paths": [],
                "provenance": ["python-path-heuristic"],
                "confidence": 0.7,
                "path_provenance": {},
            }

    candidates: list[Path] = []
    # #152 fix: per-candidate provenance override, keyed by the candidate's OWN resolved path
    # string -- lets a candidate reached ONLY via a sys.path-hacked root report its specific
    # "sys-path-insert" provenance instead of the generic "python-path-heuristic" every other
    # absolute-import candidate gets, without changing `provenance`'s existing list-of-str shape.
    path_provenance: dict[str, str] = {}
    if level > 0:
        base_dir = _python_relative_base_dir(importer_path, level)
        target = base_dir.joinpath(*parts)
        candidates.append(target.with_suffix(".py"))
        candidates.append(target / "__init__.py")
        provenance = ["relative"]
        confidence = 1.0
    else:
        hacked_roots = {
            str(current) for current in _python_sys_path_hack_roots(importer_path, repo_root)
        }
        for root in _python_candidate_roots(importer_path, repo_root):
            module_file = root.joinpath(*parts).with_suffix(".py")
            package_init = root.joinpath(*parts, "__init__.py")
            candidates.append(module_file)
            candidates.append(package_init)
            if str(root) in hacked_roots:
                for hacked_candidate in (module_file, package_init):
                    try:
                        path_provenance[_resolved_path_str(str(hacked_candidate))] = (
                            "sys-path-insert"
                        )
                    except OSError:
                        continue
        provenance = ["python-path-heuristic"]
        confidence = 0.7

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = _resolved_path_str(str(candidate))
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(Path(key))
    return {
        "paths": deduped,
        "provenance": provenance,
        "confidence": confidence,
        "path_provenance": path_provenance,
    }


def _python_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> dict[str, Any]:
    """Resolve-then-compare Python reverse-import confirm.

    Mirrors `_js_ts_module_match_details` / `_rust_module_match_details`: reuses the SAME
    precise resolver the forward `tg imports` uses (`_python_module_candidates`) instead of a
    bare path-SUFFIX match, so two files sharing a basename (`app/config.py` vs
    `tools/config.py`) no longer produce a phantom reverse edge just because an importer's
    `import config` textually ends with "config" (#74 review fix -- see
    `_module_path_matches_definition`, which is exactly that suffix match and is what this
    function replaces for the Python confirm step).

    Deliberately has NO suffix-match fallback (unlike JS/TS's bare-specifier partial-resolution
    or Rust's non-workspace-crate partial-resolution) -- that fallback IS the bug this closes,
    so it must not be reintroduced here.
    """
    candidate_info = _python_module_candidates(importer_path, module_name, repo_root, level=level)
    resolved_definition = _resolved_path_str(definition_path)
    if any(str(candidate) == resolved_definition for candidate in candidate_info["paths"]):
        provenance = list(candidate_info["provenance"])
        tagged_provenance = candidate_info.get("path_provenance", {}).get(resolved_definition)
        if tagged_provenance is not None:
            provenance = [tagged_provenance]
        return {
            "matched": True,
            "provenance": provenance,
            "confidence": float(candidate_info["confidence"] or 1.0),
        }
    return {"matched": False, "provenance": [], "confidence": 0.0}


def _python_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> tuple[bool, list[str]]:
    """Return `(matched, provenance)`.

    Unlike the bool-only `_js_ts_module_matches_definition` / `_rust_module_matches_definition`
    siblings, this also threads through `_python_module_match_details`'s `provenance` (notably
    the "sys-path-insert" tag) -- #155 fix: that tag was computed but provably unreachable
    (this was the only caller, and it discarded everything but the bool) before this change.
    The sole caller, `_confirm_import_edges`, uses it to report the tag honestly on `tg
    importers` reverse edges instead of silently collapsing it into a generic label.
    """
    details = _python_module_match_details(
        importer_path, module_name, definition_path, repo_root, level=level
    )
    return bool(details["matched"]), list(details["provenance"])


def _group_symbols_by_file(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = grouped.setdefault(current_path, [])
        current_symbols.append(dict(symbol))
    for current_symbols in grouped.values():
        current_symbols.sort(
            key=lambda item: (item["file"], item["line"], item["kind"], item["name"])
        )
    return grouped


def _normalized_changeset_paths(
    root: Path,
    changeset: dict[str, Any],
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key in ("added", "modified", "removed"):
        entries: list[str] = []
        for raw_path in changeset.get(key, []) or []:
            current_path = Path(str(raw_path)).expanduser()
            if not current_path.is_absolute():
                current_path = root / current_path
            entries.append(str(current_path.resolve()))
        normalized[key] = sorted(dict.fromkeys(entries))
    return normalized


def build_repo_map(
    path: str | Path = ".",
    *,
    max_repo_files: int | None = None,
    extra_files: list[Path] | None = None,
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    normalized_max_repo_files = max(1, int(max_repo_files)) if max_repo_files is not None else None

    with _profiling_phase(_profiling_collector, "repo_map_build"):
        context_root = root if root.is_dir() else root.parent
        _prime_all_language_repo_contexts(context_root)
        payload = _envelope(root)
        # #52 fix (loop A): the file WALK itself had no time bound (only max_repo_files COUNT),
        # so a huge/slow tree could burn the whole --deadline budget before the parse loop below
        # ever got a chance to run. Share the same absolute deadline + fold the walk's own
        # early-break signal into the parse loop's `deadline_hit` local just below.
        repo_walk_deadline_hit = _DeadlineBreakFlag()
        if _profiling_collector is None:
            all_files = _iter_repo_files(
                root,
                max_files=normalized_max_repo_files,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=repo_walk_deadline_hit,
            )
        else:
            all_files = _iter_repo_files(
                root,
                max_files=normalized_max_repo_files,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=repo_walk_deadline_hit,
                _profiling_collector=_profiling_collector,
            )
        capped_file_count = len(all_files)
        if extra_files:
            seen_files = {str(_safe_resolve(current)) for current in all_files}
            for extra_file in extra_files:
                normalized_extra_file = _safe_resolve(extra_file)
                if str(normalized_extra_file) not in seen_files:
                    all_files.append(normalized_extra_file)
                    seen_files.add(str(normalized_extra_file))
        context_files = [
            current for current in all_files if _is_repo_context_file(current, context_root)
        ]
        tests = [str(current) for current in context_files if _is_test_file(current)]
        non_test_source_files = [
            str(current) for current in context_files if not _is_test_file(current)
        ]
        source_files = non_test_source_files or tests

        imports: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        # moat P0-6: a supplied ABSOLUTE monotonic deadline stops the CPU-bound per-file parse loop
        # early and returns partial results (partial:true + deadline_limit) instead of running
        # unbounded, so a huge repo degrades gracefully instead of the caller's hard timeout
        # discarding all work. The file LIST above is already walked cheaply; only symbol/import
        # PARSING is bounded here. Break + keep what we have -- never raise, never zero the results.
        # #52 fix (loop A): seed from the walk's OWN early-break flag -- a deadline-truncated file
        # LIST is itself a reason this whole result is partial, even if the parse loop that follows
        # never gets to run (or completes trivially over a truncated list without tripping its own
        # check).
        deadline_hit = repo_walk_deadline_hit.hit
        files_scanned = 0
        for current in context_files:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                deadline_hit = True
                break
            if _profiling_collector is None:
                current_imports, current_symbols = _imports_and_symbols_for_path(current)
            else:
                current_imports, current_symbols = _imports_and_symbols_for_path(
                    current,
                    _profiling_collector=_profiling_collector,
                )
            if current_imports:
                imports.append({
                    "file": str(current),
                    "imports": current_imports,
                    "provenance": _symbol_navigation_provenance_for_path(str(current)),
                })
            symbols.extend(current_symbols)
            files_scanned += 1

        payload["files"] = source_files
        payload["symbols"] = symbols
        payload["imports"] = imports
        payload["tests"] = tests
        payload["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
        if normalized_max_repo_files is not None:
            _capped = capped_file_count >= normalized_max_repo_files
            _cause = (
                _scan_limit_cause(
                    all_files, context_root, capped_file_count, normalized_max_repo_files
                )
                if _capped
                else "project-files"
            )
            _truncated = _capped and _cause == "project-files"
            payload["scan_limit"] = {
                "max_repo_files": normalized_max_repo_files,
                "scanned_files": capped_file_count,
                # possibly_truncated is True only when project (non-vendor) files
                # were dropped; kept for back-compat but see truncation_cause for
                # the full picture.
                "possibly_truncated": _truncated,
                "truncation_cause": _cause if _capped else None,
            }
            payload["scan_remediation"] = _SCAN_LIMIT_TRUNCATED_REMEDIATION if _truncated else None
        # moat P0-6: signal a deadline-truncated parse as a top-level `partial` flag (the one field
        # an agent's parser checks) plus a `deadline_limit` sibling. Kept SEPARATE from scan_limit
        # on purpose: scan_limit means "the FILE LIST was capped by max_repo_files" (remedy: raise
        # --max-repo-files), a deadline means "PARSING ran out of time" (remedy: raise --deadline /
        # scope the query) -- conflating the two causes gives wrong-knob advice.
        if deadline_hit:
            payload["partial"] = True
            payload["deadline_limit"] = {
                "deadline_exceeded": True,
                "files_scanned": files_scanned,
                "files_total": len(context_files),
            }
    return _attach_profiling(payload, _profiling_collector)


def build_repo_map_incremental(
    previous_map: dict[str, Any],
    changeset: dict[str, Any],
    *,
    max_repo_files: int | None = None,
) -> dict[str, Any]:
    root = Path(str(previous_map.get("path", "."))).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    context_root = root if root.is_dir() else root.parent
    _prime_all_language_repo_contexts(context_root)
    normalized_changeset = _normalized_changeset_paths(root, changeset)
    changed_files = set(normalized_changeset["added"]) | set(normalized_changeset["modified"])
    # D2: normalized_changeset["removed"] is computed by _normalized_changeset_paths
    # but currently unused — explicit pruning of removed paths is handled
    # implicitly because _iter_repo_files only returns files that still exist.
    # Future work: use normalized_changeset["removed"] to proactively clean
    # previous_paths/previous_symbols/previous_imports rather than waiting for
    # the next full build_repo_map() call.
    previous_paths = {
        str(Path(str(current)).expanduser().resolve())
        for current in (
            list(previous_map.get("related_paths", []))
            or [*previous_map.get("files", []), *previous_map.get("tests", [])]
        )
    }
    previous_imports_by_file = {
        str(Path(str(entry["file"])).expanduser().resolve()): [
            str(item) for item in entry["imports"]
        ]
        for entry in previous_map.get("imports", [])
    }
    previous_symbols_by_file = _group_symbols_by_file([
        dict(symbol) for symbol in previous_map.get("symbols", [])
    ])

    normalized_max_repo_files = max(1, int(max_repo_files)) if max_repo_files is not None else None
    all_files = [
        current
        for current in _iter_repo_files(root, max_files=normalized_max_repo_files)
        if _is_repo_context_file(current, context_root)
    ]
    capped_file_count = len(all_files)
    current_files_by_path = {str(current): current for current in all_files}
    parsed_imports_by_file: dict[str, list[str]] = {}
    parsed_symbols_by_file: dict[str, list[dict[str, Any]]] = {}

    for current_path in sorted(changed_files | (set(current_files_by_path) - previous_paths)):
        path_obj = current_files_by_path.get(current_path)
        if path_obj is None:
            continue
        current_imports, current_symbols = _imports_and_symbols_for_path(path_obj)
        parsed_imports_by_file[current_path] = current_imports
        parsed_symbols_by_file[current_path] = current_symbols

    payload = _envelope(root)
    tests = [str(current) for current in all_files if _is_test_file(current)]
    source_files = [str(current) for current in all_files if not _is_test_file(current)]
    imports: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    for current in all_files:
        current_path = str(current)
        current_imports = (
            parsed_imports_by_file[current_path]
            if current_path in parsed_imports_by_file
            else previous_imports_by_file.get(current_path, [])
        )
        current_symbols = (
            parsed_symbols_by_file[current_path]
            if current_path in parsed_symbols_by_file
            else previous_symbols_by_file.get(current_path, [])
        )
        if current_imports:
            imports.append({
                "file": current_path,
                "imports": current_imports,
                "provenance": _symbol_navigation_provenance_for_path(current_path),
            })
        symbols.extend(current_symbols)

    payload["files"] = source_files
    payload["symbols"] = symbols
    payload["imports"] = imports
    payload["tests"] = tests
    payload["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
    if normalized_max_repo_files is not None:
        _capped = capped_file_count >= normalized_max_repo_files
        _cause = (
            _scan_limit_cause(all_files, context_root, capped_file_count, normalized_max_repo_files)
            if _capped
            else "project-files"
        )
        _truncated = _capped and _cause == "project-files"
        payload["scan_limit"] = {
            "max_repo_files": normalized_max_repo_files,
            "scanned_files": capped_file_count,
            "possibly_truncated": _truncated,
            "truncation_cause": _cause if _capped else None,
        }
        payload["scan_remediation"] = _SCAN_LIMIT_TRUNCATED_REMEDIATION if _truncated else None
    return payload


def apply_repo_map_output_limits(
    payload: dict[str, Any],
    *,
    max_files: int | None = None,
) -> dict[str, Any]:
    if max_files is None:
        return payload

    normalized_max_files = max(1, int(max_files))
    limited = dict(payload)
    original_files = [str(current) for current in payload.get("files", [])]
    selected_files = original_files[:normalized_max_files]
    selected_file_set = set(selected_files)
    original_tests = [str(current) for current in payload.get("tests", [])]
    selected_tests = original_tests[:normalized_max_files]
    selected_test_set = set(selected_tests)

    limited["files"] = selected_files
    limited["tests"] = selected_tests
    limited["symbols"] = [
        dict(symbol)
        for symbol in payload.get("symbols", [])
        if str(symbol.get("file", "")) in selected_file_set
    ]
    limited["imports"] = [
        dict(entry)
        for entry in payload.get("imports", [])
        if str(entry.get("file", "")) in selected_file_set
    ]
    for key in ("file_matches", "file_summaries", "sources"):
        if key in payload:
            limited[key] = [
                dict(entry)
                for entry in payload.get(key, [])
                if str(entry.get("path", entry.get("file", ""))) in selected_file_set
            ]
    if "test_matches" in payload:
        limited["test_matches"] = [
            dict(entry)
            for entry in payload.get("test_matches", [])
            if str(entry.get("path", entry.get("file", ""))) in selected_test_set
        ]
    if "related_paths" in payload:
        allowed_related_paths = selected_file_set | selected_test_set
        limited["related_paths"] = [
            str(path)
            for path in payload.get("related_paths", [])
            if str(path) in allowed_related_paths
        ]
    _output_capped = len(original_files) > normalized_max_files
    limited["output_limit"] = {
        "max_files": normalized_max_files,
        "emitted_files": len(selected_files),
        "original_files": len(original_files),
        # output_limit operates on files already filtered by the repo-map walk,
        # so these are always project files; possibly_truncated is accurate here.
        "possibly_truncated": _output_capped,
        "truncation_cause": "project-files" if _output_capped else None,
    }
    return limited


def build_repo_map_json(
    path: str | Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
) -> str:
    payload = build_repo_map(path, max_repo_files=max_repo_files)
    return json.dumps(apply_repo_map_output_limits(payload, max_files=max_files), indent=2)


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        normalized = raw_token.lower()
        for candidate in [normalized, *split_terms(raw_token)]:
            if candidate and candidate not in terms:
                terms.append(candidate)
            for synonym in _QUERY_TERM_SYNONYMS.get(candidate, ()):
                if synonym not in terms:
                    terms.append(synonym)
    return terms


def _symbol_query_terms(query: str) -> list[str]:
    expanded_terms: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        normalized = raw_token.lower()
        if normalized and normalized not in expanded_terms:
            expanded_terms.append(normalized)
        for synonym in _QUERY_TERM_SYNONYMS.get(normalized, ()):
            if synonym not in expanded_terms:
                expanded_terms.append(synonym)
        if "_" in raw_token:
            continue
        for term in split_terms(raw_token):
            if term not in expanded_terms:
                expanded_terms.append(term)
            for synonym in _QUERY_TERM_SYNONYMS.get(term, ()):
                if synonym not in expanded_terms:
                    expanded_terms.append(synonym)
    return expanded_terms


_QUERY_LANGUAGE_ALIASES = {
    "python": "python",
    "py": "python",
    "typescript": "typescript",
    "ts": "typescript",
    "javascript": "javascript",
    "js": "javascript",
    "rust": "rust",
    "rs": "rust",
}


def _query_language_hints(query: str) -> list[str]:
    hints: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        language = _QUERY_LANGUAGE_ALIASES.get(raw_token.lower())
        if language is not None and language not in hints:
            hints.append(language)
    return hints


def _target_language_for_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    suffix = Path(str(path)).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in _TS_SUFFIXES:
        return "typescript"
    if suffix in _JS_TS_SUFFIXES:
        return "javascript"
    if suffix in _RUST_SUFFIXES:
        return "rust"
    if suffix == ".go":
        # MOST-FORGOTTEN seam (PATH A Stage 1 design note): without this, the capsule's
        # query-language-vs-target-language 0.55 confidence cap (agent_capsule.py) never even
        # sees "go" as a candidate target language, so it can silently misfire on Go targets --
        # e.g. treating a Go primary file as having "no target language" instead of correctly
        # reporting primary_target_language == "go".
        return "go"
    if suffix in _JAVA_SUFFIXES:
        # Same MOST-FORGOTTEN seam, Stage 2: without this, `tg agent`'s capsule never reports
        # primary_target_language == "java" for a Java target.
        return "java"
    if suffix == ".php":
        # MOST-FORGOTTEN seam (see the ".go" branch above) -- same fix, same reason, for PHP's
        # Stage 1 registration.
        return "php"
    if suffix == ".cs":
        # Same MOST-FORGOTTEN seam, now for C# (PATH A Stage 1, second expansion).
        return "csharp"
    if suffix == ".c":
        # Same MOST-FORGOTTEN seam, now for C (PATH A Stage 3, top-10 language campaign). Note
        # ".h" is deliberately absent here -- lang_cpp.py (below) is the registered owner of
        # every C/C++ header suffix (see lang_c.py's module docstring's header-ambiguity note);
        # this branch must match ONLY the suffixes lang_c.py's LanguageSpec actually registers.
        return "c"
    if suffix in _CPP_SUFFIXES:
        # Same MOST-FORGOTTEN seam, now for C++ (PATH A Stage 3, Phase 2 -- closes the top-10
        # language campaign to 10/10). Must match lang_cpp.py's LanguageSpec.suffixes AND
        # _provider_language_for_path's pre-existing "cpp" assignment exactly, or
        # test_target_and_provider_language_agree_with_registry fails.
        return "cpp"
    return None


def _path_matches_query_language_hints(path: str | Path, hints: list[str]) -> bool:
    language = _target_language_for_path(path)
    return bool(language is not None and language in hints)


def _query_file_name_hint_score(path: str | Path, query: str) -> int:
    query_lower = query.lower()
    path_obj = Path(str(path))
    name = path_obj.name.lower()
    stem = path_obj.stem.lower()
    if name and name in query_lower:
        return 24
    if stem and re.search(rf"\b{re.escape(stem)}\b", query_lower):
        return 12
    return 0


def _score_text_terms(text: str, terms: list[str]) -> int:
    haystack = text.lower()
    haystack_terms = set(split_terms(text))
    score = 0
    for term in terms:
        normalized = term.lower()
        if len(normalized) <= 3:
            if normalized in haystack_terms:
                score += 1
        elif normalized in haystack or normalized in haystack_terms:
            score += 1
    return score


def _symbol_span_length(symbol: dict[str, Any]) -> int:
    line = int(symbol.get("line", symbol.get("start_line", 0)) or 0)
    start_line = int(symbol.get("start_line", line) or line)
    end_line = int(symbol.get("end_line", start_line) or start_line)
    return max(1, end_line - start_line + 1)


def _is_cli_command_module_path(file_path: str) -> bool:
    """No-I/O, path-only check: does ``file_path`` live under a ``cli/`` package? Necessary but
    NOT sufficient for "thin CLI dispatcher" (task #250) -- see
    ``_thin_cli_dispatcher_call_targets``, which additionally requires the specific symbol to
    carry a Typer/Click ``.command(...)`` registration decorator AND a provable call-through
    before a ``cli/`` symbol is ever treated as a pass-through wrapper rather than genuine logic
    (e.g. ``tg search``'s own flag-parsing in ``cli/main.py`` IS the real implementation for "add
    a --flag to tg search" and must never be demoted by this check)."""
    if not file_path.lower().endswith(".py"):
        return False
    try:
        parts = {part.lower() for part in Path(file_path).parts}
    except (OSError, ValueError):
        return False
    return "cli" in parts


# Task #250 gate NIT-1: a REAL thinness gate, not just "decorated as a command + calls
# something" -- an independent Opus review on #693 found that shape alone is an EMERGENT ranking
# property, not a structural guarantee: `search_command` (cli/main.py, the real ~1500-line
# implementation of `tg search` itself) is ALSO a `.command`-decorated function that calls dozens
# of other names, and would be treated identically to a genuine one-line passthrough if any one of
# its many callees ever happened to surface as a top-4 alternative for some query. The swap must
# only fire on a function that is STRUCTURALLY small, not merely "decorated + calls the right
# name". Calibrated against the two known genuine dispatcher shapes in this repo -- `ledger_claim`
# (10 top-level body statements, 14 distinct callee names, docstring excluded from both counts) and
# `ledger_release` (6 / 10) -- versus the one known fat command, `search_command` (89 / 104). Both
# real dispatchers sit at or under 15 on each axis; `search_command` sits 6-10x over. 20 leaves
# roughly 2x headroom above the largest observed genuine dispatcher on each axis while staying far
# below any real implementation function measured in this repo.
_THIN_DISPATCHER_MAX_BODY_STATEMENTS = 20
_THIN_DISPATCHER_MAX_CALL_TARGETS = 20


def _thin_cli_dispatcher_call_targets(
    file_path: str, symbol_name: str, *, expected_line: int | None = None
) -> set[str] | None:
    """Task #250: does ``symbol_name`` (defined in ``file_path``) look like a GENUINELY THIN
    Typer/Click command dispatcher -- decorated with a ``.command(...)`` registration AND small
    enough on both axes below to be structurally a passthrough, not a real implementation -- and
    if so, what names does its body call?

    Returns ``None`` when the file can't be parsed, the symbol isn't a function/method, it is NOT
    decorated as a command, OR it exceeds ``_THIN_DISPATCHER_MAX_BODY_STATEMENTS`` top-level body
    statements (docstring excluded) or ``_THIN_DISPATCHER_MAX_CALL_TARGETS`` distinct callee
    names -- i.e. not provably a THIN dispatcher, so the caller must leave the primary target
    alone. This is what protects a genuine ``cli/main.py`` target, e.g. ``search_command`` (see
    the constants' docstring above) or "add a --flag to tg search", from ever being demoted: a
    big command is rejected here regardless of whether one of its many callees happens to match an
    alternative candidate's name. Returns the -- possibly empty -- set of names the (small) body
    calls otherwise; the caller intersects this against alternative-candidate symbol names to find
    the SPECIFIC implementation a dispatcher hands off to (e.g. ``ledger_claim``'s body calling
    ``ledger_store.submit_claim(...)``).

    Reuses ``_read_source_text_cached``/``_cached_ast_parse`` (both content-addressed -- a cache
    HIT, not a re-read/re-parse, for a file already scanned earlier in this same process) and
    bounds the walk to the ONE matched function's own subtree: a structural re-check of a symbol
    already selected as the primary-target candidate, not a new repo-wide scan.
    """
    if not _is_cli_command_module_path(file_path):
        return None
    try:
        source = _read_source_text_cached(file_path)
        tree = _cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError, RecursionError):
        return None

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name
    ]
    if not matches:
        return None
    if len(matches) > 1 and expected_line is not None:
        matches.sort(key=lambda node: abs(node.lineno - expected_line))
    match = matches[0]

    decorator_names: set[str] = set()
    for decorator in match.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            decorator_names.add(target.attr)
        elif isinstance(target, ast.Name):
            decorator_names.add(target.id)
    if "command" not in decorator_names:
        return None

    # Structural thinness gate (NIT-1): count top-level body statements, excluding a leading
    # docstring (boilerplate that scales with how well-documented a command is, not with its
    # implementation size) so a thoroughly-documented thin dispatcher isn't unfairly penalized.
    body = match.body
    if body and ast.get_docstring(match, clean=False) is not None:
        body = body[1:]
    if len(body) > _THIN_DISPATCHER_MAX_BODY_STATEMENTS:
        return None

    called_names: set[str] = set()
    for call_node in ast.walk(match):
        if not isinstance(call_node, ast.Call):
            continue
        func = call_node.func
        if isinstance(func, ast.Name):
            called_names.add(func.id)
        elif isinstance(func, ast.Attribute):
            called_names.add(func.attr)
    if len(called_names) > _THIN_DISPATCHER_MAX_CALL_TARGETS:
        return None
    return called_names


def _symbol_rank_key(symbol: dict[str, Any]) -> tuple[int, int, int, int, str, int, str]:
    if bool(symbol.get("exact_query_match")):
        query_match_rank = 0
    elif bool(symbol.get("bridge_query_match")):
        query_match_rank = 1
    elif bool(symbol.get("covered_query_match")):
        query_match_rank = 2
    else:
        query_match_rank = 3
    return (
        query_match_rank,
        -int(symbol.get("score", 0)),
        0 if str(symbol.get("kind")) == "function" else 1,
        -_symbol_span_length(symbol),
        str(symbol.get("file")),
        int(symbol.get("line", 0)),
        str(symbol.get("name")),
    )


def _symbol_lookup_key(text: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9]+", text)).lower()


def _symbol_name_matches_query_exactly(symbol_name: str, query: str) -> bool:
    normalized_name = symbol_name.strip()
    normalized_query = query.strip()
    if not normalized_name:
        return False
    if normalized_name == normalized_query:
        return True
    return any(
        normalized_name == token and _is_distinctive_identifier(token)
        for token in re.findall(r"[A-Za-z0-9_]+", query)
    )


def _symbol_name_matches_query_bridge(symbol_name: str, query: str) -> bool:
    symbol_key = _symbol_lookup_key(symbol_name)
    if not symbol_key:
        return False
    if symbol_key == _symbol_lookup_key(query):
        return not _symbol_name_matches_query_exactly(symbol_name, query)
    return any(
        symbol_key == _symbol_lookup_key(token)
        for token in _query_terms(query)
        if symbol_name != token
    )


def _symbol_name_terms_cover_query(symbol_name: str, terms: list[str]) -> bool:
    symbol_terms = set(split_terms(symbol_name))
    meaningful_query_terms = {term for term in terms if len(term) > 2}
    return bool(len(symbol_terms) >= 2 and symbol_terms <= meaningful_query_terms)


def _score_file_path(path: str, terms: list[str]) -> int:
    path_obj = Path(path)
    path_parts = [
        part
        for part in path_obj.parts
        if part and part not in {path_obj.anchor, path_obj.drive, os.sep, "/"}
    ]
    score_root_markers = {
        "src",
        "tests",
        "test",
        "rust_core",
        "crates",
        "packages",
        "apps",
        "docs",
        "scripts",
        "benchmarks",
    }
    start_index = next(
        (index for index, part in enumerate(path_parts) if part.lower() in score_root_markers),
        max(0, len(path_parts) - 4),
    )
    repo_like_tail = " ".join(path_parts[start_index:])
    return _score_text_terms(path_obj.name, terms) + _score_text_terms(repo_like_tail, terms)


# Task #254 (Blackbird-style ranking heuristics -- the CEO deep-research #251 steal). Two small,
# additive signals layered onto the flat, no-IDF `_score_symbol` scorer (the known-weak point
# named in the tensor-grep-architecture-contract skill): a soft test-file demotion (heuristic 2)
# and an exact word-boundary bonus (heuristic 3). A third candidate signal from the same research
# pass -- "weight definitions above references" -- was investigated and found to have NO live
# insertion point here: `payload["symbols"]` (this scorer's entire input population, traced
# end-to-end through `_python_imports_and_symbols`/`_js_ts_parser_symbols`/`_rust_parser_symbols`/
# `_regex_imports_and_symbols`) is ALREADY exclusively AST/regex-matched DEFINITIONS (kind in
# {class, function, method, struct, enum, trait}). References/call-sites are produced by a
# structurally separate pipeline (`_python_references_and_calls` and its JS/TS/Rust/regex
# siblings) that feeds `tg refs`/`tg callers`/blast-radius and never reaches `payload["symbols"]`
# or this scorer -- a "definitions above references" bonus here would be permanently-inert dead
# code (it could never fire on real data), not a real signal, so it was deliberately not added.
_TEST_SHADOW_PENALTY = 2


def _non_test_definition_names(symbols: list[dict[str, Any]]) -> frozenset[str]:
    """Task #254 heuristic 2 support: names with at least one definition OUTSIDE a test file.

    Query-independent (a structural fact about the scanned repo, not the search terms), so a
    caller computes this ONCE per scoring pass -- cheaply, no I/O, no re-parsing -- and reuses it
    for every `_score_symbol` call in that pass rather than re-deriving it per symbol.
    """
    return frozenset(
        str(symbol["name"])
        for symbol in symbols
        if symbol.get("name")
        and symbol.get("file")
        and not _is_test_file(Path(str(symbol["file"])))
    )


def _symbol_name_exact_boundary_bonus(symbol_name: str, terms: list[str]) -> int:
    """Task #254 heuristic 3: +1 when a query term matches ``symbol_name`` as a clean,
    word-boundary-respecting token (a member of ``split_terms(symbol_name)``) rather than only
    through ``_score_text_terms``'s looser RAW-SUBSTRING fallback (a long term merely embedded
    inside a longer, differently-tokenized identifier -- e.g. term "underscore" inside a name
    like "my_underscored_value"). Standard IR signal: an exact token match is stronger evidence of
    relevance than mere containment. Restricted to ``len(term) > 3`` to mirror
    ``_score_text_terms``'s own threshold -- a term of length <= 3 is ALREADY boundary-only there
    (it only ever checks ``haystack_terms``), so there is no substring laxity to correct for short
    terms. Deliberately additive and capped at +1 total (not per matching term) to keep the delta
    small relative to the existing scoring scale -- this refines ORDER among candidates that
    already match; it never changes WHICH symbols match (the base credit from
    ``_score_text_terms`` already covers both the clean-token and substring cases).
    """
    name_terms = set(split_terms(symbol_name))
    return 1 if any(len(term) > 3 and term.lower() in name_terms for term in terms) else 0


def _score_symbol(
    symbol: dict[str, Any],
    terms: list[str],
    *,
    non_test_definition_names: frozenset[str] | None = None,
) -> int:
    symbol_name = str(symbol["name"])
    score = (
        _score_text_terms(symbol_name, terms) * 3
        + _score_text_terms(str(symbol["kind"]), terms)
        + _score_file_path(str(symbol["file"]), terms)
    )
    if _symbol_name_terms_cover_query(symbol_name, terms):
        score += 2
    score += _symbol_name_exact_boundary_bonus(symbol_name, terms)
    if (
        non_test_definition_names is not None
        and symbol_name in non_test_definition_names
        and _is_test_file(Path(str(symbol["file"])))
    ):
        # Task #254 heuristic 2: a test-file hit sinks below a same-named non-test
        # implementation instead of competing with it on equal footing -- floored at 0 so a
        # borderline match is never pushed to a misleading negative score.
        score = max(0, score - _TEST_SHADOW_PENALTY)
    return score


def _score_import_entry(entry: dict[str, Any], terms: list[str]) -> int:
    imports_joined = " ".join(str(item) for item in entry["imports"])
    return (
        _score_file_path(str(entry["file"]), terms) + _score_text_terms(imports_joined, terms) * 2
    )


def _score_file_source_terms(path: str, terms: list[str]) -> int:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return score_term_overlap(terms, source)


def _append_reason(reason_map: dict[str, list[str]], path: str, reason: str) -> None:
    current = reason_map.setdefault(path, [])
    if reason not in current:
        current.append(reason)


def _provenance_from_reasons(reasons: list[str]) -> list[str]:
    provenance: list[str] = []
    for reason in reasons:
        if reason in {"definition", "symbol", "import", "caller"}:
            label = "parser-backed"
        elif reason in {"import-graph", "graph-depth", "graph-centrality", "test-graph"}:
            label = "graph-derived"
        elif reason == "framework-pattern":
            label = "framework-pattern"
        elif reason in {"filename", "path"}:
            label = "filename-convention"
        else:
            label = "heuristic"
        if label not in provenance:
            provenance.append(label)
    return provenance or ["heuristic"]


def _span_rationale(symbol_name: str, reasons: list[str], depth: int) -> str:
    reason_text = ", ".join(reasons) if reasons else "ranked relevance"
    if "caller" in reasons:
        return f"Selected {symbol_name} because it directly calls the target symbol and sits at depth {depth}."
    if "definition" in reasons:
        return f"Selected {symbol_name} because it contains the defining behavior for the ranked edit target."
    return f"Selected {symbol_name} because it is connected by {reason_text} at depth {depth}."


def _ranking_quality(
    file_matches: list[dict[str, Any]],
    test_matches: list[dict[str, Any]],
) -> str:
    candidates = [
        int(current.get("score", 0))
        for current in [*file_matches[:2], *test_matches[:1]]
        if current
    ]
    if not candidates:
        return "weak"
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else 0
    if top >= 10 and (top - runner_up) >= 3:
        return "strong"
    if top >= 5:
        return "moderate"
    return "weak"


def _reference_kind_counts(references: list[Any]) -> dict[str, int]:
    """Additive T1 aggregate: counts every ``references`` row by its ``ref_kind`` label.

    Always sums to ``len(references)`` -- ref_kind is additive-only (moat P0-T1), so this must
    never drift from a straight tally of what is already in the list. F21 fix: a non-dict row
    (defensive-only today -- every real producer emits dicts) used to hit a bare ``continue`` and
    silently vanish from the tally, breaking the sum-equals-``len`` invariant the docstring
    promises; it is now counted too (under its label-less default, "value"), same as a dict row
    missing the ``ref_kind`` key.
    """
    counts: dict[str, int] = {"call": 0, "import": 0, "type": 0, "field": 0, "value": 0}
    for item in references:
        if not isinstance(item, dict):
            counts["value"] += 1
            continue
        ref_kind = str(item.get("ref_kind", "value"))
        counts[ref_kind] = counts.get(ref_kind, 0) + 1
    return counts


def _coverage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = dict(payload.get("coverage", {}))
    language_scope = str(coverage.get("language_scope", ""))
    evidence_counts = {
        "parser_backed": 0,
        "graph_derived": 0,
        "heuristic": 0,
    }

    def add_label(label: str) -> None:
        normalized = label.strip().lower()
        if normalized in {"python-ast", "tree-sitter", "parser-backed"}:
            evidence_counts["parser_backed"] += 1
        elif normalized == "graph-derived":
            evidence_counts["graph_derived"] += 1
        elif normalized in {
            "regex-heuristic",
            "heuristic",
            "filename-convention",
            "framework-pattern",
        }:
            evidence_counts["heuristic"] += 1

    def add_from_value(value: Any) -> None:
        if isinstance(value, str):
            add_label(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add_label(item)
        elif isinstance(value, dict):
            provenance = value.get("provenance")
            if provenance is not None:
                add_from_value(provenance)

    for group_name in (
        "file_matches",
        "test_matches",
        "imports",
        "definitions",
        "references",
        "callers",
    ):
        for item in payload.get(group_name, []):
            if isinstance(item, dict):
                add_from_value(item.get("provenance"))
                if group_name == "test_matches":
                    add_from_value(item.get("association"))
    for item in payload.get("caller_tree", []):
        if isinstance(item, dict):
            add_from_value(item.get("provenance"))
    total_evidence = sum(evidence_counts.values())
    if total_evidence > 0:
        evidence_ratios = {
            key: round(value / total_evidence, 6) for key, value in evidence_counts.items()
        }
    else:
        evidence_ratios = {
            "parser_backed": 0.0,
            "graph_derived": 0.0,
            "heuristic": 0.0,
        }
    return {
        "language_scope": language_scope,
        "parser_backed_fields": [
            "defs",
            "source",
            "refs",
            "callers",
        ],
        "heuristic_fields": [
            "test-matching",
            "graph-ranking",
        ],
        "graph_completeness": str(payload.get("graph_completeness", "moderate")),
        "evidence_counts": evidence_counts,
        "evidence_ratios": evidence_ratios,
        "reference_kind_counts": _reference_kind_counts(payload.get("references", [])),
    }


def _graph_trust_summary(
    caller_tree: list[dict[str, Any]],
    *,
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parser_backed = 0
    heuristic = 0
    provenance: list[str] = []
    max_confidence_rank = 0
    confidence_order = {"weak": 1, "moderate": 2, "strong": 3}
    for level in caller_tree:
        edge_summary = level.get("edge_summary", {})
        if not isinstance(edge_summary, dict):
            continue
        counts = edge_summary.get("evidence_counts", {})
        if isinstance(counts, dict):
            parser_backed += int(counts.get("parser_backed", 0))
            heuristic += int(counts.get("heuristic", 0))
        for label in edge_summary.get("provenance", []):
            if isinstance(label, str) and label not in provenance:
                provenance.append(label)
        confidence = str(edge_summary.get("confidence", "weak"))
        max_confidence_rank = max(max_confidence_rank, confidence_order.get(confidence, 1))
    rank_to_confidence = {value: key for key, value in confidence_order.items()}
    # Additive T1 moat closer: by_ref_kind lets a consumer see whether the blast-radius callers
    # are parser-backed CALL sites (strong evidence) vs type/field/value-only mentions
    # (moderate/weak) -- reuses the same ref_kind labels stamped on `calls` (payload["callers"]).
    by_ref_kind: dict[str, int] = {}
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        ref_kind = str(call.get("ref_kind", "call"))
        by_ref_kind[ref_kind] = by_ref_kind.get(ref_kind, 0) + 1
    return {
        "edge_kind": "reverse-import",
        "confidence": rank_to_confidence.get(max_confidence_rank, "weak"),
        "provenance": provenance or ["graph-derived"],
        "depth_count": len(caller_tree),
        "evidence_counts": {
            "parser_backed": parser_backed,
            "heuristic": heuristic,
            "by_ref_kind": by_ref_kind,
        },
    }


def _language_coverage_gap_remediation(
    language: str, *, fail_closed: bool = False, import_resolution_only: bool = False
) -> str:
    """F12 fix: the remediation text must match what ACTUALLY happens for this gap.

    An unregistered-language file (``fail_closed=False``, no ``LanguageSpec`` at all) really does
    fall back to plain literal-text/regex matching -- see the generic ``else`` branch in the
    refs/callers scan loops. A registered-but-grammar-missing language with no regex fallback
    (``fail_closed=True``, e.g. Go when ``tree_sitter_go`` is not installed) produces ZERO rows
    for its files instead -- claiming a regex fallback there was simply false.

    ``import_resolution_only`` (audit #81 #4): a registered language whose grammar IS installed
    but whose ``LanguageSpec.import_update_target`` is ``None`` (Go today) -- defs/refs/callers
    all work normally, but the reverse-import-graph edge (``import_graph_consumers``) can never
    be computed for this language, so a zero count there must read as UNKNOWN, not proven-zero.
    """
    if fail_closed:
        return (
            f"tg has a '{language}' extractor registered but its required parser/grammar is not "
            f"installed -- refs/callers on a symbol whose definition or usage lives in a "
            f"{language} file currently produce NO rows for those files ('{language}' has no "
            "plain-text/regex fallback, unlike python/javascript/typescript/rust). Install the "
            f"missing '{language}' tree-sitter grammar package to restore coverage."
        )
    if import_resolution_only:
        return (
            f"tg has a '{language}' extractor registered and its parser/grammar is installed, "
            f"but no reverse-import resolver is wired for '{language}' yet -- `tg callers`/`tg "
            f"blast-radius` cannot discover a {language} file that consumes a symbol purely via "
            "an import statement (`import_graph_consumers` is always empty for this language). "
            "Direct-reference/call matches inside scanned files are unaffected. Treat a zero "
            f"import-graph-consumer count for a {language} definition as UNKNOWN, not "
            "proven-zero, until native reverse-import resolution ships."
        )
    return (
        f"tg has no parser-backed extractor registered for '{language}' files yet -- refs/"
        f"callers on a symbol whose definition or usage lives in a {language} file fall back to "
        "plain literal-text/regex matching (no import-graph resolution, no AST-verified call "
        "sites). Treat matches in these files as lower-confidence until native support ships."
    )


def _language_coverage_gaps_for_universe(bounded_files: list[Path]) -> list[dict[str, Any]]:
    """PATH A Stage 0 honesty floor (additive): label files in the refs/callers scan universe
    that have no registered ``LanguageSpec`` (or, for a registered language whose grammar is
    fail-closed with no regex fallback, no usable parser) instead of silently degrading them.
    Zero behavior change for python/javascript/typescript/rust today -- every file with one of
    those suffixes always resolves a spec, so this only ever fires for a language tensor-grep's
    symbol graph does not yet cover (e.g. .kt, .swift) that happens to sit in the scan universe.

    Also covers a NARROWER partial-capability gap (audit #81 #4): a language can be fully
    registered with a working parser (defs/refs/callers all resolve normally) yet still have
    ``LanguageSpec.import_update_target is None`` -- Go today (PATH A Stage 2: Java hits this
    same branch too, but for Java refs/callers are not just import-resolution-incomplete, they
    are UNIMPLEMENTED entirely -- foundational tier only, see the Java LanguageSpec registration
    above; `tg refs`/`tg callers` simply find nothing in a Java file rather than resolving
    normally). Before this fix, that combination fell through the two branches above straight to
    ``continue``, so a Go-only repo with the grammar installed reported an EMPTY
    ``resolution_gaps`` even though
    ``_build_import_graph_consumers_from_map`` can never produce a single reverse-import edge for
    it -- indistinguishable from "genuinely has zero import-graph consumers". The third branch
    below flags that combination explicitly so `tg callers`/`tg blast-radius` stay honest about
    it instead of reading as a proven-zero.
    """
    gaps_by_language: dict[str, dict[str, Any]] = {}
    for current in bounded_files:
        spec = lang_registry.spec_for_path(current)
        fail_closed = False
        import_resolution_only = False
        if spec is None:
            language = _provider_language_for_path(current) or (
                current.suffix.lstrip(".").lower() or "unknown"
            )
            reason = "no registered language extractor for this file suffix"
        elif spec.parser_for_path is not None and spec.parser_for_path(current) is None:
            # Only a genuine gap for a language that FAILS CLOSED with no fallback when its
            # grammar is missing (Stage 1+ languages like Go). None of the 4 current languages
            # set this: JS/TS/Rust always fall back to regex-heuristic extraction, so this
            # branch is unreachable for them today and only matters for future registrations.
            if spec.provenance_when_missing not in {"regex-heuristic", "heuristic"}:
                language = spec.language_id
                reason = (
                    "required parser/grammar is not installed for this language and it has no "
                    "regex fallback (fail-closed)"
                )
                fail_closed = True
            else:
                continue
        elif spec.import_update_target is None:
            language = spec.language_id
            reason = (
                "registered language extractor has no reverse-import resolver -- "
                "import_graph_consumers can never be computed for this language"
            )
            import_resolution_only = True
        else:
            continue
        entry = gaps_by_language.setdefault(
            language,
            {
                "language": language,
                "reason": reason,
                "files_affected": 0,
                "remediation": _language_coverage_gap_remediation(
                    language,
                    fail_closed=fail_closed,
                    import_resolution_only=import_resolution_only,
                ),
            },
        )
        entry["files_affected"] += 1
    return sorted(
        gaps_by_language.values(),
        key=lambda item: (-int(item["files_affected"]), str(item["language"])),
    )


def _downgrade_graph_trust_summary_for_coverage_gaps(
    summary: dict[str, Any],
    resolution_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Additive downgrade: a non-empty ``resolution_gaps`` means part of the caller/reference
    universe was scanned with no parser-backed extractor, so the blast-radius trust summary
    should not read as more confident than that. Downgrades ``confidence`` by exactly one rung
    and stamps ``resolution_gaps_present`` -- never touches anything when gaps are empty, so
    the 4 currently-supported languages see byte-identical output."""
    if not resolution_gaps:
        return summary
    confidence_order = {"weak": 1, "moderate": 2, "strong": 3}
    rank_to_confidence = {value: key for key, value in confidence_order.items()}
    current_rank = confidence_order.get(str(summary.get("confidence", "weak")), 1)
    downgraded = dict(summary)
    downgraded["confidence"] = rank_to_confidence[max(1, current_rank - 1)]
    downgraded["resolution_gaps_present"] = True
    return downgraded


def _is_parser_backed_provenance(label: str) -> bool:
    return label.strip().lower() in {"python-ast", "tree-sitter", "parser-backed"}


def _is_heuristic_provenance(label: str) -> bool:
    return label.strip().lower() in {"regex-heuristic", "heuristic", "filename-convention"}


def _dependency_trust(
    repo_map: dict[str, Any],
    dependent_files: list[str],
) -> dict[str, Any]:
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get("provenance", _symbol_navigation_provenance_for_path(str(current["file"])))
        )
        for current in repo_map.get("imports", [])
        if current.get("file")
    }
    parser_backed_count = 0
    heuristic_count = 0
    for current in dict.fromkeys(str(path) for path in dependent_files if path):
        provenance = import_provenance_by_file.get(current, "")
        if _is_parser_backed_provenance(provenance):
            parser_backed_count += 1
        elif _is_heuristic_provenance(provenance):
            heuristic_count += 1
    if parser_backed_count > 0 and heuristic_count == 0:
        import_resolution_quality = "strong"
    elif parser_backed_count > 0:
        import_resolution_quality = "moderate"
    else:
        import_resolution_quality = "weak"
    return {
        "import_resolution_quality": import_resolution_quality,
        "parser_backed_count": parser_backed_count,
        "heuristic_count": heuristic_count,
    }


def _plan_trust_summary(
    ranking_quality: str,
    coverage_summary: dict[str, Any],
    dependency_trust: dict[str, Any],
) -> str:
    evidence_counts = dict(coverage_summary.get("evidence_counts", {}))
    return (
        f"Ranking {ranking_quality}; coverage {coverage_summary.get('graph_completeness', 'moderate')} "
        f"(parser_backed={int(evidence_counts.get('parser_backed', 0))}, "
        f"graph_derived={int(evidence_counts.get('graph_derived', 0))}, "
        f"heuristic={int(evidence_counts.get('heuristic', 0))}); dependency trust "
        f"{dependency_trust.get('import_resolution_quality', 'weak')} "
        f"(parser_backed={int(dependency_trust.get('parser_backed_count', 0))}, "
        f"heuristic={int(dependency_trust.get('heuristic_count', 0))})."
    )


def _match_record(
    path: str,
    score: int,
    reasons: list[str],
    graph_score: float | None = None,
) -> dict[str, Any]:
    payload = {
        "path": path,
        "score": score,
        "reasons": list(reasons),
        "provenance": _provenance_from_reasons(reasons),
    }
    if graph_score is not None:
        payload["graph_score"] = round(graph_score, 6)
    return payload


def _file_summaries(symbols: list[dict[str, Any]], ranked_files: list[str]) -> list[dict[str, Any]]:
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = symbols_by_file.setdefault(current_path, [])
        current_symbols.append({
            "name": str(symbol["name"]),
            "kind": str(symbol["kind"]),
            "line": int(symbol["line"]),
        })
    for current_symbols in symbols_by_file.values():
        current_symbols.sort(
            key=lambda item: (int(item["line"]), str(item["kind"]), str(item["name"]))
        )

    summaries: list[dict[str, Any]] = []
    for current in ranked_files:
        file_symbols = symbols_by_file.get(str(current), [])
        if not file_symbols:
            continue
        summaries.append({"path": str(current), "symbols": file_symbols})
    return summaries


def _source_tokens(source_files: list[str]) -> set[str]:
    tokens: set[str] = set()
    for current in source_files:
        path = Path(current)
        stem = path.stem.lower()
        tokens.add(stem)
        for part in re.split(r"[^A-Za-z0-9_]+", stem):
            if part:
                tokens.add(part)
    return tokens


# Directory-index reverse-importers fix (tg importers correctness gap, express@4.21.1 dogfood):
# the "magic" directory-index/package-init stems `_definition_module_parts` (:3200) already
# strips for BARE/absolute specifier matching. Rust `mod.rs` is deliberately EXCLUDED for two
# reasons: SCOPE (the reported gap is JS/TS `index` + the symmetric Python `__init__`) AND
# PRECISION -- adding "mod" would extend the accepted bare-specifier false-positive surface
# documented on `_reverse_importer_extra_aliases` (below) to Rust as well.
_DIRECTORY_INDEX_STEMS = frozenset({"index", "__init__"})


@lru_cache(maxsize=16384)
def _module_aliases_for_path(path: str) -> frozenset[str]:
    # Pure function of the path STRING (no file I/O) — safe to cache unconditionally, no mtime
    # key needed. The reverse-import graph / PageRank calls this in tight loops (~1.4M calls for
    # ~unique-file inputs on a depth-2 blast-radius), so memoization collapses it to one build
    # per distinct path. frozenset return keeps the cached value immutable (all callers iterate
    # it or .update() FROM it; none mutate it).
    #
    # DELIBERATELY does NOT emit a directory-index file's PARENT-DIR alias -- that lives in the
    # reverse-importers-ONLY `_reverse_importer_extra_aliases` (below). This helper is SHARED by
    # substring/exact RANKING (`_import_graph_bonus`, `_test_import_bonus`, `_test_graph_score`)
    # and by BLAST-RADIUS scope expansion (set-intersection ~:11542) + the test-coverage gate
    # (substring match ~:16732). Giving a top-level `pkg/__init__.py` a bare "pkg" alias HERE
    # would make editing that (often empty) init pull most of the repo into `tg blast-radius`
    # and mark nearly every test covered (adversarial-review finding, 2026-07-16). Keep it
    # byte-stable for those consumers; widen only the reverse-importers prefilter.
    current = Path(path)
    aliases = {current.stem.lower()}
    parts = [part.lower() for part in current.with_suffix("").parts]
    if parts:
        aliases.add(".".join(parts))
    if len(parts) > 1:
        aliases.add(".".join(parts[-2:]))
    return frozenset(alias for alias in aliases if alias)


@lru_cache(maxsize=16384)
def _reverse_importer_extra_aliases(path: str) -> frozenset[str]:
    """Extra alias a DIRECTORY-INDEX file earns ONLY for the reverse-importers candidate
    prefilter (`_reverse_importers`) -- deliberately NOT folded into the SHARED
    `_module_aliases_for_path` (see its note for why that helper must stay byte-stable).

    A bare relative specifier that names a DIRECTORY -- `require('./router')` /
    `import ... from './router'` (JS/TS) or `from . import router` where `router` is a subpackage
    (Python) -- resolves, by Node's/Python's own directory-index convention, to
    `router/index.{js,ts,mjs,cjs}` or `router/__init__.py`. That specifier normalizes to the
    PARENT DIRECTORY name ("router"), which `_module_aliases_for_path` never emits (every alias
    it builds is anchored on the file's OWN stem -- "index"/"__init__" -- never its parent dir).
    Without this alias the directory-index importer never enters the coarse prefilter as a
    candidate, so it never reaches the precise per-candidate CONFIRM step
    (`_js_ts_module_matches_definition` / `_python_module_matches_definition`).

    PRECISION -- this alias WIDENS the prefilter; it is NOT a proof of an edge (the CONFIRM step
    still gates every candidate, and every `_reverse_importers` consumer either confirms edges
    (`tg importers`) or feeds PageRank SCORING, never raw membership). For a RELATIVE specifier
    the confirm resolves the exact path, so `./router` -> a real `router/index.js` is an exact
    edge and `./routerX` is rejected. For a BARE (non-relative) specifier the JS/TS confirm falls
    through to `_module_path_matches_definition` (:3214) -- a path-SUFFIX compare that itself
    strips the index/__init__ magic name -- so a bare npm-style `import X from 'react'` WILL
    match a local `src/react/index.ts` at the pre-existing 0.2 "partial-resolution" confidence.
    That is INTENTIONAL (correct in pnpm/yarn workspace monorepos where `react` is a real local
    package dir; a deliberate false-positive only for the rare npm-package-name-vs-local-dir
    collision) and is exactly the existing bare-suffix heuristic this alias feeds -- NOT a new
    exactness claim. Pinned by
    test_build_file_importers_bare_specifier_matches_local_directory_index_package.
    """
    current = Path(path)
    if current.stem.lower() not in _DIRECTORY_INDEX_STEMS:
        return frozenset()
    parts = [part.lower() for part in current.with_suffix("").parts]
    if len(parts) < 2:
        return frozenset()
    parent = parts[-2]
    return frozenset({parent}) if parent else frozenset()


def _import_alias_candidates(import_name: str) -> set[str]:
    lowered = import_name.lower().strip()
    if not lowered:
        return set()
    normalized = re.sub(r"[^a-z0-9_]+", ".", lowered).strip(".")
    parts = [part for part in normalized.split(".") if part]
    candidates = {lowered, normalized}
    candidates.update(parts)
    for start in range(len(parts)):
        for end in range(start + 2, len(parts) + 1):
            candidates.add(".".join(parts[start:end]))
    return {candidate for candidate in candidates if candidate}


def _import_graph_bonus(
    file_path: str,
    dependency_aliases: dict[str, frozenset[str]],
    imports_by_file: dict[str, list[str]],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(file_path, []):
        lowered = import_name.lower()
        for aliases in dependency_aliases.values():
            if any(alias and alias in lowered for alias in aliases):
                bonus += 4
                break
    return bonus


def _reverse_import_distances(
    seed_files: list[str],
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, int]:
    """#222 residual fix (cold-path assembly-tail SLA, real-workspace-scale continuation of
    #669/#671): this BFS's inner loop re-scans ALL of ``all_files`` at EACH of up to 3 depth
    levels, and for every candidate does O(``len(frontier)``) work inside ``_import_graph_bonus``
    -- so cost is O(depth x len(all_files) x len(frontier)), and ``frontier`` itself grows with
    each wave once the import graph has any real fan-in (a popular shared module -- exactly the
    common case). Pre-fix, this function itself had NO deadline check anywhere, even though every
    sibling stage of its DOMINANT caller, ``_build_context_pack_from_map`` (the symbol-scoring
    loop, ``_personalized_reverse_import_pagerank``, both ``_detect_vendored_subtrees`` calls),
    already honored the shared budget -- by far the most expensive post-deadline tail consumer on
    the `tg agent` path. A SECOND, independently-reachable call site had the identical gap:
    ``_relevant_tests_for_symbol``'s ``if caller_files:`` block already declared ``deadline_
    monotonic``/``deadline_hit`` in its own signature (used by its direct_definition_tests loop)
    but never forwarded them here -- and ``build_symbol_callers_from_map`` reaches that block with
    BOTH a non-None ``caller_files`` and a real ``deadline_monotonic``, so ``tg callers --deadline
    SYMBOL`` on a high-fan-in symbol (and the agent capsule's caller-evidence path, which calls
    into the same builder) could still run this whole-repo BFS unbounded (#691 gate NIT-1). Both
    call sites are now gated identically.

    Measured (direct, non-subprocess probe, a hub-fan-in-shaped synthetic tree, 5-seed BFS):
    0.99s at 2,000 files -> 13.6s at 6,000 (13.8x for 3x files) -> 60.3s at 12,000 (61x for 6x
    files, ~4.4x for the last 2x step alone) -- a ~n^2.2 curve, clearly super-linear, and the
    dominant cost of ``_build_context_pack_from_map`` at scale (60.3s of that call's 71.5s total
    at 12,000 files = 84%).

    Fix shape mirrors every other sibling stage in this module (``_personalized_reverse_import_
    pagerank``, the symbol-scoring loop above, ``_precomputed_validation_files_for_root``,
    ``_detect_vendored_subtrees``): a per-ITEM check inside the expensive inner loop, not just a
    per-depth (outer-loop) check -- the outer loop only ever runs 3 times regardless of repo size,
    so an outer-only check would not bound anything (the #669/#671 lesson: bound the loop whose
    OWN per-iteration cost is what scales). On expiry this returns the PARTIAL ``distances``
    already accumulated -- never discarded -- exactly like every caller already treats a missing
    entry (``file_distances.get(x)`` / ``current_path in file_distances``) as "no graph-distance
    signal for this file," the same honest degrade ``_personalized_reverse_import_pagerank``'s own
    docstring documents for its ``{}`` abandon. Optional, default ``None`` -- every pre-existing
    call site (this function has 4) is a byte-identical no-op.
    """
    with _profiling_phase(_profiling_collector, "graph_bfs"):
        distances: dict[str, int] = {}
        frontier = list(seed_files)
        seen = set(seed_files)

        for depth in range(1, 4):
            dependency_aliases = {
                current: _module_aliases_for_path(current) for current in frontier
            }
            next_frontier: list[str] = []
            for current in all_files:
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    if deadline_hit is not None:
                        deadline_hit.hit = True
                    return distances
                if current in seen:
                    continue
                bonus = _import_graph_bonus(current, dependency_aliases, imports_by_file)
                if bonus <= 0:
                    continue
                distances[current] = depth
                seen.add(current)
                next_frontier.append(current)
            if not next_frontier:
                break
            frontier = next_frontier
        return distances


def _reverse_importers(
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
    *,
    include_directory_index_aliases: bool = False,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, set[str]]:
    # `include_directory_index_aliases` is opt-in and defaults OFF so this function stays
    # byte-behaviour-identical to origin/main for its RANKING consumers -- the blast-radius
    # (:16587) and context/agent-capsule (:3836) callers feed its output into
    # `_personalized_reverse_import_pagerank` (a SCORING signal), and even a widened reverse edge
    # there measurably reorders pinned `dependent_files` output
    # (test_python_termui_symbols_prefer_depth_one_dependents). ONLY the `tg importers`
    # reverse-resolution (`build_file_importers_from_map`) passes True, because ONLY it runs the
    # per-candidate CONFIRM step (`_confirm_import_edges`) that turns the widened prefilter back
    # into exact edges. See `_reverse_importer_extra_aliases` for the alias rationale.
    #
    # #222 residual fix: this function's own direct cost is comparatively cheap and scales close
    # to linearly (measured ~0.02s/0.08s/0.15s at 2k/6k/12k files on the same synthetic tree that
    # exposed ``_reverse_import_distances``' quadratic cost above) -- NOT the dominant residual --
    # but it sits in the exact same unconditional, un-gated call block in `_build_context_pack_
    # from_map` immediately after that function. Without a check here, a deadline tripped INSIDE
    # `_reverse_import_distances` would still let this whole second whole-repo pass run
    # unbounded afterward. Same per-item-in-the-expensive-loop shape as every sibling; optional,
    # default `None`, byte-identical no-op for the 3 other pre-existing call sites.
    with _profiling_phase(_profiling_collector, "graph_construction"):
        alias_to_files: dict[str, set[str]] = {}
        for current in all_files:
            for alias in _module_aliases_for_path(current):
                alias_to_files.setdefault(alias, set()).add(current)
            if include_directory_index_aliases:
                # Directory-index recall (express@4.21.1 dogfood): also nominate a directory's
                # `index.*`/`__init__.py` under its PARENT-DIR alias, so a bare relative specifier
                # (`require('./router')` / `from . import router`) reaches the CONFIRM step.
                for alias in _reverse_importer_extra_aliases(current):
                    alias_to_files.setdefault(alias, set()).add(current)
        reverse: dict[str, set[str]] = {current: set() for current in all_files}
        for importer in all_files:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return reverse
            for import_name in imports_by_file.get(importer, []):
                for alias in _import_alias_candidates(import_name):
                    for current in alias_to_files.get(alias, set()):
                        if current == importer:
                            continue
                        reverse[current].add(importer)
        return reverse


def _personalized_reverse_import_pagerank(
    seed_files: list[str],
    all_files: list[str],
    reverse_importers: dict[str, set[str]],
    *,
    alpha: float = 0.85,
    iterations: int = 12,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, float]:
    """dogfood finding 1: this 12-iteration loop ran fully UNBOUNDED even when a caller already
    had a deadline_monotonic in scope (a real ``tg agent``/``tg codemap`` whole-repo call could
    run well past --deadline here alone). ``deadline_monotonic``/``deadline_hit`` follow the same
    ``_DeadlineBreakFlag`` readback contract every other deadline-scoped sibling loop in this
    module uses -- checked at the ITERATION boundary (not mid-iteration) so a hit is always a
    clean iteration count, never a half-applied update.

    On expiry this ABANDONS to ``{}`` rather than returning the last-completed iteration's
    partial ranks: every existing caller already treats a missing/zero graph score as "no
    centrality signal" via ``.get(x, 0.0)`` (see ``_build_context_pack_from_map`` and
    ``_relevant_tests_for_symbol``), so ``{}`` is a deterministic, already-handled degrade --
    never a silently-incomplete ranking presented as complete.

    The per-node ``sorted(reverse_importers.get(current))`` is hoisted OUT of the iteration loop
    below: ``reverse_importers`` never changes across iterations, so the pre-fix code recomputed
    the same sort up to 12x per node for nothing. Free, additive speedup, independent of the
    deadline fix above -- proven numerically identical to the pre-hoist shape by
    ``test_pagerank_hoisted_sort_matches_reference_computation``.
    """
    with _profiling_phase(_profiling_collector, "graph_pagerank"):
        if not seed_files:
            return {}

        all_file_set = set(all_files)
        seen_seeds: set[str] = set()
        unique_seeds: list[str] = []
        for current in seed_files:
            if current not in all_file_set or current in seen_seeds:
                continue
            seen_seeds.add(current)
            unique_seeds.append(current)
            if len(unique_seeds) >= _GRAPH_PAGERANK_SEED_FILE_LIMIT:
                break
        if not unique_seeds:
            return {}

        seed_set = set(unique_seeds)
        seed_weight = 1.0 / len(unique_seeds)
        personalization = {
            current: (seed_weight if current in seed_set else 0.0) for current in all_files
        }
        ranks = dict(personalization)
        sorted_outgoing = {
            current: sorted(reverse_importers.get(current, set())) for current in all_files
        }
        for _ in range(iterations):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return {}
            updated = {current: (1.0 - alpha) * personalization[current] for current in all_files}
            for current in all_files:
                outgoing = sorted_outgoing[current]
                if outgoing:
                    share = alpha * ranks[current] / len(outgoing)
                    for importer in outgoing:
                        updated[importer] = updated.get(importer, 0.0) + share
                    continue
                spill = alpha * ranks[current] / len(unique_seeds)
                for seed in unique_seeds:
                    updated[seed] = updated.get(seed, 0.0) + spill
            ranks = updated
        return {current: rank for current, rank in ranks.items() if rank > 0.0}


def _test_import_bonus(
    test_path: str,
    source_tokens: set[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        if any(token and token in lowered for token in source_tokens):
            bonus += 3
        for file_path, depth in file_distances.items():
            aliases = _module_aliases_for_path(file_path)
            if any(alias and alias in lowered for alias in aliases):
                bonus += max(1, 4 - depth)
                break
    return bonus


def _test_graph_score(
    test_path: str,
    source_files: list[str],
    imports_by_file: dict[str, list[str]],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
) -> float:
    aliases_by_file = {current: _module_aliases_for_path(current) for current in source_files}
    score = 0.0
    matched_files: set[str] = set()
    max_file_score = max(file_scores.values(), default=0)
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        for current, aliases in aliases_by_file.items():
            if current in matched_files:
                continue
            if any(alias and alias in lowered for alias in aliases):
                matched_files.add(current)
                score += graph_scores.get(current, 0.0)
                if max_file_score > 0:
                    score += file_scores.get(current, 0) / max_file_score
    return score


def _context_tests(
    source_files: list[str],
    tests: list[str],
    terms: list[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
    *,
    raw_query: str | None = None,
) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    allow_unrelated_framework_scan = len(tests) <= _FRAMEWORK_TEST_PATTERN_SMALL_TEST_LIMIT
    source_stems = {Path(current).stem.lower() for current in source_files}
    source_tokens = _source_tokens(source_files)
    for current in tests:
        score = _score_file_path(current, terms)
        reasons: list[str] = []
        if score > 0:
            reasons.append("path")
        stem = Path(current).stem.lower().removeprefix("test_")
        if stem in source_stems:
            score += 2
            reasons.append("filename")
        import_bonus = _test_import_bonus(current, source_tokens, imports_by_file, file_distances)
        score += import_bonus
        if import_bonus > 0:
            reasons.append("test-graph")
        graph_score = _test_graph_score(
            current,
            source_files,
            imports_by_file,
            graph_scores,
            file_scores,
        )
        framework_bonus = (
            _framework_test_pattern_bonus(current, terms, raw_query=raw_query)
            if score > 0 or graph_score > 0.0 or allow_unrelated_framework_scan
            else 0
        )
        score += framework_bonus
        if framework_bonus > 0:
            reasons.append("framework-pattern")
        if graph_score > 0.0:
            reasons.append("graph-centrality")
            score += max(1, round(graph_score * 10))
        if score > 0:
            if import_bonus > 0 and stem in source_stems:
                edge_kind = "hybrid"
            elif import_bonus > 0:
                edge_kind = "import-graph"
            elif framework_bonus > 0:
                edge_kind = "framework-pattern"
            elif stem in source_stems:
                edge_kind = "filename"
            else:
                edge_kind = "path"
            if import_bonus > 0 or graph_score > 0.0:
                confidence = "strong" if import_bonus > 0 and graph_score > 0.0 else "moderate"
            elif framework_bonus > 0:
                confidence = "moderate" if framework_bonus >= 3 else "weak"
            else:
                confidence = "weak"
            match = _match_record(
                current, score, reasons, graph_score if graph_score > 0.0 else None
            )
            match["association"] = {
                "edge_kind": edge_kind,
                "confidence": confidence,
                "provenance": _provenance_from_reasons(reasons),
            }
            related.append(match)
    related.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return related


def _build_context_pack_from_map(
    payload: dict[str, Any],
    query: str,
    *,
    auto_deweight: bool = True,
    _test_source_limit: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "context_scoring"):
        terms = _query_terms(query)
        symbol_terms = _symbol_query_terms(query)
        query_language_hints = _query_language_hints(query)
        all_symbols = [dict(symbol) for symbol in payload["symbols"]]
        # Task #254 heuristic 2: computed ONCE per scoring pass (query-independent), then reused
        # for every `_score_symbol` call below instead of re-deriving it per symbol.
        non_test_definition_names = _non_test_definition_names(payload["symbols"])
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in payload["imports"]
        }
        file_scores = {
            str(current): _score_file_path(str(current), terms) for current in payload["files"]
        }
        file_reasons: dict[str, list[str]] = {}
        for current, score in file_scores.items():
            if score > 0:
                _append_reason(file_reasons, current, "path")
            file_name_bonus = _query_file_name_hint_score(current, query)
            if file_name_bonus > 0:
                file_scores[current] = file_scores.get(current, 0) + file_name_bonus
                _append_reason(file_reasons, current, "filename")

        scored_symbols: list[dict[str, Any]] = []
        for symbol in payload["symbols"]:
            # task #103: this loop iterates every symbol in the scanned repo -- the single
            # largest repo-size-proportional loop in context-pack construction (profiled at
            # ~13% of a `tg callers`/`tg impact` call) -- and ran fully unbounded regardless of
            # --deadline before this fix. Mirror the same pre-iteration deadline check
            # _preferred_definition_files/_relevant_tests_for_symbol already use.
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            score = _score_symbol(
                symbol, symbol_terms, non_test_definition_names=non_test_definition_names
            )
            if score <= 0:
                continue
            scored_symbol = dict(symbol)
            scored_symbol["score"] = score
            current_path = str(scored_symbol["file"])
            if _is_test_file(Path(current_path)):
                continue
            symbol_name_score = _score_text_terms(str(scored_symbol["name"]), symbol_terms)
            symbol_name = str(scored_symbol["name"])
            exact_query_match = _symbol_name_matches_query_exactly(symbol_name, query)
            bridge_query_match = _symbol_name_matches_query_bridge(symbol_name, query)
            covered_query_match = _symbol_name_terms_cover_query(symbol_name, symbol_terms)
            if symbol_name_score > 0 and (
                exact_query_match or bridge_query_match or covered_query_match
            ):
                if exact_query_match:
                    scored_symbol["exact_query_match"] = True
                elif bridge_query_match:
                    scored_symbol["bridge_query_match"] = True
                elif covered_query_match:
                    scored_symbol["covered_query_match"] = True
                _append_reason(file_reasons, current_path, "definition")
                _append_reason(file_reasons, current_path, "symbol")
            scored_symbols.append(scored_symbol)
        scored_symbols.sort(key=_symbol_rank_key)
        for symbol in scored_symbols:
            current = str(symbol["file"])
            exact_symbol_bonus = 36 if bool(symbol.get("exact_query_match")) else 0
            bridge_symbol_bonus = 8 if bool(symbol.get("bridge_query_match")) else 0
            covered_symbol_bonus = 24 if bool(symbol.get("covered_query_match")) else 0
            file_scores[current] = (
                file_scores.get(current, 0)
                + int(symbol["score"]) * 3
                + exact_symbol_bonus
                + bridge_symbol_bonus
                + covered_symbol_bonus
            )

        scored_imports: list[dict[str, Any]] = []
        for entry in payload["imports"]:
            if _is_test_file(Path(str(entry["file"]))):
                continue
            score = _score_import_entry(entry, terms)
            if score <= 0:
                continue
            scored_entry = dict(entry)
            scored_entry["provenance"] = str(
                entry.get("provenance", _symbol_navigation_provenance_for_path(str(entry["file"])))
            )
            scored_entry["score"] = score
            scored_imports.append(scored_entry)
        scored_imports.sort(key=lambda item: (-int(item["score"]), str(item["file"])))
        for entry in scored_imports:
            current = str(entry["file"])
            file_scores[current] = file_scores.get(current, 0) + int(entry["score"]) * 2
            _append_reason(file_reasons, current, "import")

        source_candidates: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in source_candidates:
                source_candidates.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in source_candidates:
                source_candidates.append(current)
        for current, score in sorted(
            file_scores.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        ):
            if score > 0 and current not in source_candidates:
                source_candidates.append(current)
        if not source_candidates:
            source_candidates = [str(current) for current in payload["files"]]
        source_candidates = source_candidates[:_SOURCE_FALLBACK_SCAN_LIMIT]
        for current_path in source_candidates:
            source_score = _score_file_source_terms(current_path, terms)
            if source_score <= 0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + source_score * 8
            _append_reason(file_reasons, current_path, "source")

        dependency_seed_files: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in dependency_seed_files:
                dependency_seed_files.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in dependency_seed_files:
                dependency_seed_files.append(current)
        if not dependency_seed_files:
            dependency_seed_files = [
                path for path in payload["files"] if _score_file_path(str(path), terms) > 0
            ]

        all_files = [str(current) for current in payload["files"]]
        dependency_aliases = {
            current: _module_aliases_for_path(current) for current in dependency_seed_files
        }
        # #222 (real-workspace-scale residual of #220/#669/#671): these two whole-repo graph
        # passes were the ONE un-gated post-deadline tail consumer left in this function -- every
        # other sibling stage here (the symbol-scoring loop above, pagerank below, both
        # `_detect_vendored_subtrees` calls) already honors `deadline_monotonic`. Reusing the SAME
        # `deadline_hit` flag this function already threads into pagerank keeps `context_pack_
        # assembly` in the caller's `assembly_stages_skipped` list honestly attributed to "some
        # stage in this pack build," not a new, over-precise label this signal alone can't support.
        file_distances = _reverse_import_distances(
            dependency_seed_files,
            all_files,
            imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        reverse_importers = _reverse_importers(
            all_files,
            imports_by_file,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        for current in payload["files"]:
            # #222 residual fix (found via OLD-vs-NEW re-profile of the fix above): a THIRD
            # unconditional whole-repo loop calling `_import_graph_bonus` directly -- cost is
            # O(len(payload["files"]) x len(dependency_aliases)), and `dependency_aliases` is NOT
            # always small: a query term that fuzzy-matches many files' import strings (e.g. a
            # short/common token) can pull hundreds-to-thousands of files into `dependency_seed_
            # files`. Measured dominating a post-fix (deadline-honoring `_reverse_import_
            # distances`/`_reverse_importers`) re-profile at 12,000 files: 93s of a 102s total,
            # 208M `_import_graph_bonus` genexpr evaluations across only 10,006 outer calls. Same
            # per-item check, same shared `deadline_hit` flag as its two siblings above.
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                break
            current_path = str(current)
            if current_path in dependency_seed_files:
                continue
            import_graph_bonus = _import_graph_bonus(
                current_path,
                dependency_aliases,
                imports_by_file,
            )
            file_scores[current_path] = file_scores.get(current_path, 0) + import_graph_bonus
            if import_graph_bonus > 0:
                _append_reason(file_reasons, current_path, "import-graph")
            if current_path in file_distances:
                file_scores[current_path] = file_scores.get(current_path, 0) + max(
                    1, 5 - file_distances[current_path]
                )
                _append_reason(file_reasons, current_path, "import-graph")
        graph_seed_files: list[str] = []
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in graph_seed_files:
                graph_seed_files.append(current)
        if not graph_seed_files:
            graph_seed_files = list(dependency_seed_files)

        # dogfood finding 1: deadline_monotonic/deadline_hit were already in scope in this
        # function (threaded into the symbol-scoring loop above) but never forwarded to pagerank
        # -- this 12-iteration whole-repo-file loop ran fully unbounded even past --deadline.
        graph_scores = _personalized_reverse_import_pagerank(
            graph_seed_files,
            all_files,
            reverse_importers,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        for current_path in set(dependency_seed_files) | set(file_distances):
            graph_score = graph_scores.get(current_path, 0.0)
            if graph_score <= 0.0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + max(
                1, round(graph_score * 10)
            )
            _append_reason(file_reasons, current_path, "graph-centrality")

        if query_language_hints:
            for current_path, score in list(file_scores.items()):
                if score <= 0:
                    continue
                if _path_matches_query_language_hints(current_path, query_language_hints):
                    file_scores[current_path] = score + 20
                    _append_reason(file_reasons, current_path, "language")

        explicit_symbol_intent = any(
            bool(symbol.get("exact_query_match") or symbol.get("bridge_query_match"))
            for symbol in scored_symbols
        )
        if not explicit_symbol_intent:
            with _profiling_phase(_profiling_collector, "direct_validation_scoring"):
                for symbol in scored_symbols[:_DIRECT_VALIDATION_SYMBOL_SCAN_LIMIT]:
                    current_path = str(symbol["file"])
                    if _is_test_file(Path(current_path)):
                        continue
                    direct_test_count = _direct_validation_import_count_from_repo_map(
                        tests=[str(test_path) for test_path in payload["tests"]],
                        imports_by_file=imports_by_file,
                        symbol=str(symbol["name"]),
                        definition_path=current_path,
                    )
                    if direct_test_count <= 0:
                        continue
                    file_scores[current_path] = file_scores.get(current_path, 0) + min(
                        64,
                        40 + direct_test_count * 8,
                    )
                    _append_reason(file_reasons, current_path, "validation-direct-definition")

        # Auto de-weight (never hard-exclude) auto-detected vendor/skill/generated CODE subtrees
        # (#55 PR6) -- same import-island heuristic `tg orient` applies, reused here so `tg agent`'s
        # primary-target ranking benefits too. Local import avoids a module-level circular import
        # (orient_capsule imports this module), mirroring the existing `_apply_ignore_globs` reuse
        # a few lines up in `build_context_render`.
        deweighted_trees: dict[str, dict[str, Any]] = {}
        if auto_deweight:
            from tensor_grep.cli.orient_capsule import _DEWEIGHT_FACTOR, _detect_vendored_subtrees

            deweighted_trees = _detect_vendored_subtrees(
                payload, deadline_monotonic=deadline_monotonic, deadline_hit=deadline_hit
            )
            if deweighted_trees:
                tree_roots = list(deweighted_trees.keys())
                for current_path, score in list(file_scores.items()):
                    if score <= 0:
                        continue
                    candidate = Path(current_path)
                    for tree_root in tree_roots:
                        try:
                            candidate.relative_to(tree_root)
                        except ValueError:
                            continue
                        file_scores[current_path] = max(0, round(score * _DEWEIGHT_FACTOR))
                        _append_reason(file_reasons, current_path, "vendored-subtree-deweighted")
                        break

        scored_files = [(score, path) for path, score in file_scores.items() if score > 0]
        scored_files.sort(key=lambda item: (-item[0], item[1]))
        ranked_files = [path for _, path in scored_files]
        file_matches = [
            _match_record(path, score, file_reasons.get(path, []), graph_scores.get(path))
            for score, path in scored_files
        ]
        if not ranked_files:
            for symbol in scored_symbols:
                current = str(symbol["file"])
                if current not in ranked_files:
                    ranked_files.append(current)
            for entry in scored_imports:
                current = str(entry["file"])
                if current not in ranked_files:
                    ranked_files.append(current)
        test_source_files = (
            ranked_files[: max(1, _test_source_limit)]
            if _test_source_limit is not None
            else ranked_files
        )
        test_matches = _context_tests(
            test_source_files,
            payload["tests"],
            terms,
            imports_by_file,
            file_distances,
            graph_scores,
            file_scores,
            raw_query=query,
        )
        ranked_tests = [str(item["path"]) for item in test_matches]

        related_paths = []
        for current in ranked_files:
            related_paths.append(current)
        for current in ranked_tests:
            if current not in related_paths:
                related_paths.append(current)

    payload["routing_reason"] = "context-pack"
    payload["query"] = query
    payload["files"] = ranked_files
    payload["file_matches"] = file_matches
    payload["file_summaries"] = _file_summaries(all_symbols, ranked_files)
    payload["symbols"] = scored_symbols
    payload["imports"] = scored_imports
    payload["tests"] = ranked_tests
    payload["test_matches"] = test_matches
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _ranking_quality(file_matches, test_matches)
    payload["coverage_summary"] = _coverage_summary(payload)
    # Only emit when non-empty. An empty `deweighted_trees` still costs ~20 bytes in every
    # context-pack payload, and (since compaction records dropped keys in omitted_keys) omitting it
    # saves nothing -- enough to tip the macOS llm-compact token-budget test, whose long temp paths
    # leave a ~5-byte margin under the < 9000 ceiling, over the edge (#525 CI). Consumers treat a
    # missing key as "nothing de-weighted".
    if deweighted_trees:
        payload["deweighted_trees"] = [
            {"path": tree_path, "reasons": list(info["reasons"])}
            for tree_path, info in sorted(deweighted_trees.items())
        ]
    return payload


# Default output-token budget for the `tg context` CLI (dogfood v1.19.9: an UNBOUNDED pack ballooned
# to >1MB — "blows any context window"). The pack is for prompt injection, so bound it by default at
# the CLI layer only; build_context_pack itself defaults to unbounded so library callers
# (session/edit-plan/mcp) are unchanged unless they opt in.
_DEFAULT_CONTEXT_MAX_TOKENS = 16000


def _estimate_payload_tokens(payload: dict[str, Any]) -> int:
    return _estimate_tokens(json.dumps(payload, ensure_ascii=False))


def _apply_context_token_budget(payload: dict[str, Any], max_tokens: int | None) -> dict[str, Any]:
    """Bound the serialized context pack to ~``max_tokens`` so it stays prompt-injection-ready.

    FILE-DRIVEN + coherent: reduces the ranked-file count via ``apply_repo_map_output_limits`` (which
    keeps each retained file WITH its symbols/imports/matches consistently), so the bounded pack is a
    smaller top-ranked slice, never a file list gutted of its symbols. Adapts to file size -- a repo
    of huge files fits fewer, a repo of small files fits more. ``max_tokens`` of ``None`` / ``<= 0``
    is a no-op (unbounded opt-out). Records ``token_budget`` honestly.
    """
    if max_tokens is None or max_tokens <= 0:
        return payload
    estimated = _estimate_payload_tokens(payload)
    if estimated <= max_tokens:
        capped = dict(payload)
        capped["token_budget"] = {
            "max_tokens": max_tokens,
            "estimated_tokens": estimated,
            "truncated": False,
        }
        return capped
    file_count = len(payload.get("files", []))
    capped = payload
    while file_count > 1 and estimated > max_tokens:
        # Proportional first guess, then strictly shrink so we always make progress.
        guess = max(1, min(file_count - 1, file_count * max_tokens // max(estimated, 1)))
        capped = apply_repo_map_output_limits(payload, max_files=guess)
        estimated = _estimate_payload_tokens(capped)
        file_count = guess
    if capped is payload:  # over budget even before shrinking (single-file pack); take the top file
        capped = apply_repo_map_output_limits(payload, max_files=1)
        estimated = _estimate_payload_tokens(capped)
    capped = dict(capped)
    capped["token_budget"] = {
        "max_tokens": max_tokens,
        "estimated_tokens": estimated,
        "truncated": True,
    }
    return capped


# Secondary (supporting-context) fields trimmed BEFORE the primary answer array when a
# defs/refs/callers/impact payload exceeds --max-tokens (design #96, answer-first shrink order).
_SYMBOL_TOKEN_BUDGET_SECONDARY_FIELDS: tuple[str, ...] = ("tests", "related_paths")


def _apply_symbol_token_budget(
    payload: dict[str, Any],
    max_tokens: int | None,
    *,
    primary_field: str,
    companion_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Bound a defs/refs/callers/impact payload to ~``max_tokens`` (design #96 item 4).

    Modeled on ``_apply_context_token_budget``'s serialize-then-measure approach, but with an
    ANSWER-FIRST shrink order: SECONDARY fields (``tests``, ``related_paths`` -- whichever are
    present; each field's ``{field}_matches`` companion, e.g. impact's ``test_matches``, is
    cleared alongside it so the real bloat source is not left untouched) are cleared FIRST since
    they are supporting context, not the answer itself. Only if the payload is STILL over budget
    after zeroing every secondary field is the PRIMARY answer array (``primary_field`` --
    ``definitions``/``references``/``callers``/``files``) trimmed, and that is flagged distinctly
    (``token_budget.primary_truncated``/``primary_omitted``) so an agent trusting "here are all N
    callers" can tell N was cut for space, not because there were only N. ``companion_fields``
    (e.g. impact's ``file_matches``, which shares ``files``'s exact order/length by construction)
    are sliced to the same length as the trimmed primary array so the two never disagree.

    ``max_tokens`` of None/<=0 is a no-op (unbounded opt-out), matching
    ``_apply_context_token_budget``. This is an OUTPUT-cap, never a scan-truncation signal: it
    must never set ``result_incomplete``/``partial``/``caller_scan_limit`` (design #96 contract
    safety section) -- achieved simply by never touching those keys.
    """
    if max_tokens is None or max_tokens <= 0:
        return payload
    estimated = _estimate_payload_tokens(payload)
    if estimated <= max_tokens:
        capped = dict(payload)
        capped["token_budget"] = {
            "max_tokens": max_tokens,
            "estimated_tokens": estimated,
            "truncated": False,
            "primary_truncated": False,
        }
        return capped

    capped = dict(payload)
    secondary_trimmed: list[str] = []
    for field_name in _SYMBOL_TOKEN_BUDGET_SECONDARY_FIELDS:
        if estimated <= max_tokens:
            break
        current_value = capped.get(field_name)
        if isinstance(current_value, list) and current_value:
            capped[field_name] = []
            companion = f"{field_name}_matches"
            if isinstance(capped.get(companion), list):
                capped[companion] = []
            secondary_trimmed.append(field_name)
            estimated = _estimate_payload_tokens(capped)

    primary_truncated = False
    primary_omitted = 0
    if estimated > max_tokens:
        primary_list = list(capped.get(primary_field) or [])
        original_primary_count = len(primary_list)
        count = original_primary_count
        # Floor at 1, never 0 (mirrors _apply_context_token_budget's file-shrink floor): trimming
        # the primary answer array all the way to an EMPTY list is indistinguishable from a
        # genuine "not found" (the exact "confident false zero" this codebase's own
        # _scan_truncation_warning docstring calls "the single most dangerous output for a
        # refactor-safety tool") -- _emit_symbol_command_result reads an empty primary field as
        # not_found and exits 1, which would silently relabel a budget trim as an absence.
        while count > 1 and estimated > max_tokens:
            # Proportional first guess, then strictly shrink so we always make progress (mirrors
            # _apply_context_token_budget's file-shrink loop).
            guess = max(1, min(count - 1, count * max_tokens // max(estimated, 1)))
            capped[primary_field] = primary_list[:guess]
            estimated = _estimate_payload_tokens(capped)
            count = guess
        # count/original_primary_count already <=1 (0 or 1 entries): nothing left to trim without
        # zeroing the answer out, so best-effort stop here even if still over budget -- keeping a
        # truthful non-empty answer outranks strictly honoring the token cap.
        new_primary_len = len(capped.get(primary_field) or [])
        primary_omitted = max(0, original_primary_count - new_primary_len)
        primary_truncated = primary_omitted > 0
        if primary_truncated:
            surviving_primary = capped.get(primary_field) or []
            # Filter by PATH MEMBERSHIP (not index/length slicing): a companion like impact's
            # `file_matches` is not guaranteed to stay index-aligned with `files` once the CLI
            # layer has post-processed the primary field (e.g. impact's own caller-merge step
            # appends extra file paths to `files` with no matching `file_matches` entry) -- a
            # length-slice would silently keep the WRONG entries in that case.
            if surviving_primary and all(isinstance(item, str) for item in surviving_primary):
                surviving_set = set(surviving_primary)
                for companion in companion_fields:
                    companion_value = capped.get(companion)
                    if isinstance(companion_value, list):
                        capped[companion] = [
                            entry
                            for entry in companion_value
                            if not (isinstance(entry, dict) and "path" in entry)
                            or str(entry["path"]) in surviving_set
                        ]
            else:
                for companion in companion_fields:
                    companion_value = capped.get(companion)
                    if isinstance(companion_value, list):
                        capped[companion] = companion_value[:new_primary_len]

    capped["token_budget"] = {
        "max_tokens": max_tokens,
        "estimated_tokens": estimated,
        "truncated": True,
        "secondary_fields_trimmed": secondary_trimmed,
        "primary_truncated": primary_truncated,
        "primary_omitted": primary_omitted,
    }
    return capped


def build_context_pack(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
    max_tokens: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # Match map/agent/blast-radius: when the caller does not bound the scan,
    # default to DEFAULT_AGENT_REPO_MAP_LIMIT so context never walks an entire
    # workspace (which previously made `tg context` hang indefinitely on large
    # trees that lack a --max-repo-files default at the CLI layer).
    if max_repo_files is None:
        max_repo_files = DEFAULT_AGENT_REPO_MAP_LIMIT
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg context`
    # (Click "No such option" exit-2). Converted ONCE to an absolute monotonic budget (mirrors
    # build_symbol_impact's moat P0-6 step-3 pattern) and shared across both the repo-map build AND
    # the symbol-scoring loop below, so a slow ranking pass on a huge repo cannot itself blow past
    # the requested budget after the walk/parse phase already finished inside it.
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    payload = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    context_payload = build_context_pack_from_map(
        payload,
        query,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    limited = apply_repo_map_output_limits(context_payload, max_files=max_files)
    result = _apply_context_token_budget(limited, max_tokens)
    # #642 gate nit-1 fast-follow: mirrors build_context_render_from_map's own return-time
    # catch-all (added by this same PR) verbatim. `tg context` has no edit-plan-seed tail, but
    # still calls apply_repo_map_output_limits/_apply_context_token_budget AFTER build_context_
    # pack_from_map's own own_deadline_hit fold already ran, so it shares the same missing
    # "regardless of stage" recheck at its true final return point.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )
    if result.get("partial") or deadline_exceeded_at_return:
        result["partial"] = True
        result["partial_reason"] = "deadline"
        existing_deadline_limit = result.get("deadline_limit")
        result["deadline_limit"] = (
            dict(existing_deadline_limit)
            if isinstance(existing_deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
    return result


def build_context_pack_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int | None = None,
    max_repo_files: int | None = None,
    max_tokens: int | None = None,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        build_context_pack(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )


def _render_context_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"kind": "query", "text": f"Query: {payload['query']}"}]
    file_matches_by_path = {str(match["path"]): match for match in payload.get("file_matches", [])}
    test_matches_by_path = {str(match["path"]): match for match in payload.get("test_matches", [])}
    symbol_scores_by_key = {
        (str(symbol["file"]), str(symbol["name"])): int(symbol.get("score", 0))
        for symbol in payload.get("symbols", [])
    }
    tests = [str(current) for current in payload.get("tests", [])]
    if tests:
        test_lines = ["Tests:", *[f"- {current}" for current in tests[:3]]]
        parts.append({
            "kind": "tests",
            "text": "\n".join(test_lines),
            "paths": tests[:3],
            "provenance": {
                "matches": [
                    {
                        "path": current,
                        "score": int(test_matches_by_path.get(current, {}).get("score", 0)),
                        "graph_score": test_matches_by_path.get(current, {}).get("graph_score"),
                        "reasons": list(test_matches_by_path.get(current, {}).get("reasons", [])),
                    }
                    for current in tests[:3]
                ]
            },
        })

    sources_by_file: dict[str, list[dict[str, Any]]] = {}
    for source in payload.get("sources", []):
        current = str(source["file"])
        current_sources = sources_by_file.setdefault(current, [])
        current_sources.append(source)

    max_files = int(payload.get("max_files", 3))
    summaries = list(payload.get("file_summaries", []))[:max_files]
    summarized_paths = {str(summary["path"]) for summary in summaries}
    for current in [str(path) for path in payload.get("files", [])[:max_files]]:
        if current in summarized_paths or current not in sources_by_file:
            continue
        summaries.append({"path": current, "symbols": []})
        summarized_paths.add(current)

    for summary in summaries:
        current_path = str(summary["path"])
        summary_lines = [f"File: {current_path}", "Summary:"]
        for symbol in summary.get("symbols", [])[: int(payload.get("max_symbols_per_file", 6))]:
            summary_lines.append(f"- {symbol['kind']} {symbol['name']} @ line {symbol['line']}")
        file_match = file_matches_by_path.get(current_path, {})
        parts.append({
            "kind": "summary",
            "path": current_path,
            "text": "\n".join(summary_lines),
            "provenance": {
                "path": current_path,
                "score": int(file_match.get("score", 0)),
                "graph_score": file_match.get("graph_score"),
                "reasons": list(file_match.get("reasons", [])),
            },
        })
        for source in sources_by_file.get(current_path, [])[:2]:
            file_match = file_matches_by_path.get(current_path, {})
            symbol_name = str(source["name"])
            parts.append({
                "kind": "source",
                "path": current_path,
                "symbol": symbol_name,
                "provenance": {
                    "path": current_path,
                    "symbol": symbol_name,
                    "score": int(file_match.get("score", 0)),
                    "graph_score": file_match.get("graph_score"),
                    "reasons": list(file_match.get("reasons", [])),
                    "symbol_score": symbol_scores_by_key.get((current_path, symbol_name), 0),
                },
                "text": (
                    "Source:\n```text\n"
                    f"{str(source.get('rendered_source', source['source'])).rstrip()}\n```"
                ),
            })
    return parts


def _estimate_tokens_for_len(length: int) -> int:
    """The SINGLE source of the chars->tokens estimate. `_estimate_tokens` (the text-taking
    wrapper) and `_truncate_source_text_to_budget`'s running-length fast path MUST both go
    through here -- a duplicated formula would let the truncation loop silently desync from
    the budget check if the estimate ever changes."""
    if length <= 0:
        return 0
    return max(1, math.ceil(length / 3.5))


def _estimate_tokens(
    text: str,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> int:
    with _profiling_phase(_profiling_collector, "token_estimation"):
        return _estimate_tokens_for_len(len(text))


def _line_map_for_budgeted_lines(
    line_map: list[dict[str, int]],
    selected_line_numbers: list[int],
) -> list[dict[str, int]]:
    rendered_to_original: dict[int, int] = {}
    for segment in line_map:
        rendered_start = int(segment.get("rendered_start_line", 0))
        rendered_end = int(segment.get("rendered_end_line", 0))
        original_start = int(segment.get("original_start_line", 0))
        if rendered_start <= 0 or rendered_end < rendered_start or original_start <= 0:
            continue
        for rendered_line in range(rendered_start, rendered_end + 1):
            rendered_to_original[rendered_line] = original_start + rendered_line - rendered_start

    budgeted: list[dict[str, int]] = []
    for new_rendered_line, previous_rendered_line in enumerate(selected_line_numbers, start=1):
        original_line = rendered_to_original.get(previous_rendered_line)
        if original_line is None:
            continue
        if budgeted and original_line == budgeted[-1]["original_end_line"] + 1:
            budgeted[-1]["rendered_end_line"] = new_rendered_line
            budgeted[-1]["original_end_line"] = original_line
            continue
        budgeted.append({
            "rendered_start_line": new_rendered_line,
            "rendered_end_line": new_rendered_line,
            "original_start_line": original_line,
            "original_end_line": original_line,
        })
    return budgeted


def _source_text_within_budget(
    text: str,
    *,
    max_tokens: int | None,
    max_chars: int | None,
    _profiling_collector: _ProfileCollector | None = None,
) -> bool:
    if max_chars is not None and len(text) > max_chars:
        return False
    if (
        max_tokens is not None
        and _estimate_tokens(
            text,
            _profiling_collector=_profiling_collector,
        )
        > max_tokens
    ):
        return False
    return True


def _truncate_source_text_to_budget(
    text: str,
    *,
    max_tokens: int | None,
    max_chars: int | None,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[str, list[int], bool]:
    if _source_text_within_budget(
        text,
        max_tokens=max_tokens,
        max_chars=max_chars,
        _profiling_collector=_profiling_collector,
    ):
        line_count = len(text.splitlines())
        return text, list(range(1, line_count + 1)), False

    # perf (O(k^2) -> O(k)): `_source_text_within_budget` (and the `_estimate_tokens` it calls) is
    # a PURE function of `len(text)` -- `_estimate_tokens` is `max(1, ceil(len(text)/3.5))` (0 for
    # an empty string) and never reads text content, only `len(text)`, `max_tokens`, `max_chars`
    # (see the two functions right above this one). The two loops below used to rebuild the FULL
    # candidate string on every iteration just to hand it to that length-only check
    # (`"".join([*selected_lines, line])`, and the same shape rebuilding `candidate_lines` in the
    # tail-rescue loop) -- each rebuild is O(current total length), making the loops O(k^2) on a
    # k-line file. `_within_budget_for_len` reproduces the identical pass/fail decision from a
    # running integer length instead, so `selected_lines`/`selected_indexes`/the final joined text
    # come out byte-identical while both loops become O(k) (see
    # tests/unit/test_token_budget.py::test_truncate_*_is_byte_identical for the pinned-output
    # regression coverage, captured from this function before the rewrite).
    def _within_budget_for_len(candidate_len: int) -> bool:
        if max_chars is not None and candidate_len > max_chars:
            return False
        if max_tokens is not None and _estimate_tokens_for_len(candidate_len) > max_tokens:
            return False
        return True

    lines = text.splitlines(keepends=True)
    selected_lines: list[str] = []
    selected_indexes: list[int] = []
    running_len = 0

    for index, line in enumerate(lines, start=1):
        candidate_len = running_len + len(line)
        if not _within_budget_for_len(candidate_len):
            break
        selected_lines.append(line)
        selected_indexes.append(index)
        running_len = candidate_len

    tail_line: tuple[int, str] | None = None
    for index in range(len(lines), 0, -1):
        line = lines[index - 1]
        stripped = line.strip()
        if stripped.startswith(("return ", "raise ", "yield ", "assert ")):
            tail_line = (index, line)
            break

    if tail_line is not None and tail_line[0] not in selected_indexes:
        tail_text = tail_line[1]
        tail_len = len(tail_text)
        last_selected = max(selected_indexes, default=0)
        omitted_between = max(0, tail_line[0] - last_selected - 1)
        marker = f"# ... {omitted_between} lines omitted by source budget ...\n"
        candidate_len = running_len + len(marker) + tail_len
        while selected_lines and not _within_budget_for_len(candidate_len):
            popped = selected_lines.pop()
            selected_indexes.pop()
            running_len -= len(popped)
            last_selected = max(selected_indexes, default=0)
            omitted_between = max(0, tail_line[0] - last_selected - 1)
            marker = f"# ... {omitted_between} lines omitted by source budget ...\n"
            candidate_len = running_len + len(marker) + tail_len
        if _within_budget_for_len(candidate_len):
            selected_lines = [*selected_lines, marker, tail_text]
            selected_indexes = [*selected_indexes, 0, tail_line[0]]

    if not selected_lines and lines:
        chunk = lines[0]
        if max_chars is not None:
            chunk = chunk[:max_chars]
        while (
            max_tokens is not None
            and _estimate_tokens(
                chunk,
                _profiling_collector=_profiling_collector,
            )
            > max_tokens
            and len(chunk) > 1
        ):
            chunk = chunk[: max(1, int(len(chunk) * 0.75))]
        selected_lines = [chunk]
        selected_indexes = [1]

    return "".join(selected_lines).rstrip("\n"), selected_indexes, True


def _apply_source_output_budget(
    sources: list[dict[str, Any]],
    *,
    max_tokens: int | None,
    max_render_chars: int | None,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    normalized_max_chars = (
        max_render_chars if max_render_chars is not None and max_render_chars > 0 else None
    )
    if normalized_max_tokens is None and normalized_max_chars is None:
        return sources, None, []

    budgeted_sources: list[dict[str, Any]] = []
    omitted_sections: list[dict[str, Any]] = []
    remaining_tokens = normalized_max_tokens
    remaining_chars = normalized_max_chars
    original_token_total = 0
    emitted_token_total = 0
    original_char_total = 0
    emitted_char_total = 0
    truncated_sources = 0
    omitted_sources = 0
    omitted_line_count = 0

    for source in sources:
        rendered_source = str(source.get("rendered_source", source.get("source", "")))
        original_tokens = _estimate_tokens(
            rendered_source,
            _profiling_collector=_profiling_collector,
        )
        original_token_total += original_tokens
        original_char_total += len(rendered_source)
        original_line_count = len(rendered_source.splitlines())
        if (remaining_tokens is not None and remaining_tokens <= 0) or (
            remaining_chars is not None and remaining_chars <= 0
        ):
            omitted_sources += 1
            omitted_line_count += original_line_count
            omitted_sections.append({
                "kind": "source_payload",
                "file": str(source.get("file", "")),
                "symbol": source.get("name"),
                "score": 0,
                "reason": "source_budget_exhausted",
                "omitted_line_count": original_line_count,
                "token_estimate": original_tokens,
            })
            continue

        truncated_source, selected_lines, truncated = _truncate_source_text_to_budget(
            rendered_source,
            max_tokens=remaining_tokens,
            max_chars=remaining_chars,
            _profiling_collector=_profiling_collector,
        )
        emitted_tokens = _estimate_tokens(
            truncated_source,
            _profiling_collector=_profiling_collector,
        )
        emitted_token_total += emitted_tokens
        emitted_char_total += len(truncated_source)
        if remaining_tokens is not None:
            remaining_tokens = max(0, remaining_tokens - emitted_tokens)
        if remaining_chars is not None:
            remaining_chars = max(0, remaining_chars - len(truncated_source))

        budgeted = dict(source)
        budgeted["rendered_source"] = truncated_source
        if "source" in budgeted:
            budgeted["source"] = truncated_source
        if truncated:
            truncated_sources += 1
            omitted_lines = max(0, original_line_count - len(selected_lines))
            omitted_line_count += omitted_lines
            budgeted["line_map"] = _line_map_for_budgeted_lines(
                _list_of_dicts(source.get("line_map")),
                selected_lines,
            )
            diagnostics = dict(budgeted.get("render_diagnostics", {}))
            diagnostics["budget_removed_line_count"] = omitted_lines
            diagnostics["rendered_line_count"] = len(selected_lines)
            budgeted["render_diagnostics"] = diagnostics
            omitted_sections.append({
                "kind": "source_payload",
                "file": str(source.get("file", "")),
                "symbol": source.get("name"),
                "score": 0,
                "reason": "source_budget",
                "omitted_line_count": omitted_lines,
                "token_estimate": original_tokens,
                "emitted_token_estimate": emitted_tokens,
            })
        budgeted["source_budget"] = {
            "max_tokens": normalized_max_tokens,
            "max_render_chars": normalized_max_chars,
            "original_token_estimate": original_tokens,
            "emitted_token_estimate": emitted_tokens,
            "original_char_count": len(rendered_source),
            "emitted_char_count": len(truncated_source),
            "truncated": truncated,
        }
        budgeted_sources.append(budgeted)

    summary = {
        "max_tokens": normalized_max_tokens,
        "max_render_chars": normalized_max_chars,
        "original_token_estimate": original_token_total,
        "emitted_token_estimate": emitted_token_total,
        "original_char_count": original_char_total,
        "emitted_char_count": emitted_char_total,
        "truncated_sources": truncated_sources,
        "omitted_sources": omitted_sources,
        "omitted_line_count": omitted_line_count,
        "possibly_truncated": bool(truncated_sources or omitted_sources),
    }
    return budgeted_sources, summary, omitted_sections


def _render_part_score(part: dict[str, Any]) -> int:
    provenance = part.get("provenance", {})
    if not isinstance(provenance, dict):
        return 0
    if "score" in provenance:
        return int(provenance.get("score", 0))
    matches = provenance.get("matches", [])
    if not isinstance(matches, list):
        return 0
    return max(
        (int(match.get("score", 0)) for match in matches if isinstance(match, dict)),
        default=0,
    )


def _render_part_path(part: dict[str, Any]) -> str | None:
    current_path = part.get("path")
    if current_path:
        return str(current_path)
    paths = part.get("paths", [])
    if isinstance(paths, list) and paths:
        return str(paths[0])
    return None


def _render_part_sort_key(
    part: dict[str, Any],
    *,
    primary_file: str | None,
    original_index: int,
) -> tuple[int, int, int, str, str, int]:
    kind = str(part.get("kind", ""))
    path = _render_part_path(part) or ""
    is_primary = primary_file is not None and path == primary_file
    kind_priority = (
        {
            "source": 0,
            "summary": 1,
            "tests": 2,
        }
        if is_primary
        else {
            "summary": 0,
            "source": 1,
            "tests": 2,
        }
    ).get(kind, 3)
    return (
        0 if is_primary else 1,
        -_render_part_score(part),
        kind_priority,
        path,
        str(part.get("symbol", "")),
        original_index,
    )


def _prepare_render_part(
    part: dict[str, Any],
    *,
    has_prior_content: bool,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any] | None:
    text = str(part.get("text", "")).strip()
    if not text:
        return None
    prefix = "" if not has_prior_content else "\n\n"
    chunk = f"{prefix}{text}"
    prepared = {key: value for key, value in part.items() if key != "text"}
    prepared["text"] = text
    prepared["chunk"] = chunk
    prepared["score"] = _render_part_score(part)
    prepared["token_estimate"] = _estimate_tokens(
        chunk,
        _profiling_collector=_profiling_collector,
    )
    return prepared


def _omitted_section_record(part: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(part.get("kind", "")),
        "file": _render_part_path(part),
        "symbol": part.get("symbol"),
        "score": int(part.get("score", 0)),
        "token_estimate": int(part.get("token_estimate", 0)),
    }


def _render_context_string_and_sections(
    payload: dict[str, Any],
    *,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[str, list[dict[str, Any]], bool, int, list[dict[str, Any]]]:
    with _profiling_phase(_profiling_collector, "render_packing"):
        max_render_chars = (
            max_render_chars if max_render_chars is not None and max_render_chars > 0 else None
        )
        resolved_max_tokens = max_tokens if max_tokens is not None else payload.get("max_tokens")
        max_tokens = (
            int(resolved_max_tokens)
            if resolved_max_tokens is not None and int(resolved_max_tokens) > 0
            else None
        )
        _ = model if model is not None else payload.get("model")
        primary_file = None
        edit_plan_seed = payload.get("edit_plan_seed")
        if isinstance(edit_plan_seed, dict):
            current_primary_file = edit_plan_seed.get("primary_file")
            if current_primary_file:
                primary_file = str(current_primary_file)
        if primary_file is None:
            files = payload.get("files", [])
            if isinstance(files, list) and files:
                primary_file = str(files[0])

        raw_parts = _render_context_parts(payload)
        query_parts: list[dict[str, Any]] = []
        ranked_parts: list[tuple[dict[str, Any], int]] = []
        for index, part in enumerate(raw_parts):
            if str(part.get("kind", "")) == "query":
                query_parts.append(part)
                continue
            ranked_parts.append((part, index))
        ranked_parts.sort(
            key=lambda item: _render_part_sort_key(
                item[0],
                primary_file=primary_file,
                original_index=item[1],
            )
        )
        parts = [*query_parts, *[part for part, _ in ranked_parts]]

        selected_parts: list[dict[str, Any]] = []
        omitted_parts: list[dict[str, Any]] = []
        non_query_selected = 0
        estimated_total_tokens = 0
        budget_exhausted = False

        for part in parts:
            prepared = _prepare_render_part(
                part,
                has_prior_content=bool(selected_parts),
                _profiling_collector=_profiling_collector,
            )
            if prepared is None:
                continue
            if str(prepared.get("kind", "")) == "query":
                selected_parts.append(prepared)
                estimated_total_tokens += int(prepared["token_estimate"])
                continue
            if budget_exhausted:
                omitted_parts.append(prepared)
                continue
            current_token_estimate = estimated_total_tokens + int(prepared["token_estimate"])
            if max_tokens is None or current_token_estimate <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                continue
            if non_query_selected == 0 and estimated_total_tokens <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                budget_exhausted = True
                continue
            omitted_parts.append(prepared)

        sections: list[dict[str, Any]] = []
        rendered_parts: list[str] = []
        offset = 0
        total_token_estimate = 0
        truncated = bool(omitted_parts)
        char_omitted_parts: list[dict[str, Any]] = []
        for index, part in enumerate(selected_parts):
            chunk = str(part["chunk"])
            if max_render_chars is not None and offset + len(chunk) > max_render_chars:
                remaining = max_render_chars - offset
                partially_rendered_current = False
                if offset == 0 and remaining > 0:
                    chunk = chunk[:remaining]
                    section_token_estimate = _estimate_tokens(
                        chunk,
                        _profiling_collector=_profiling_collector,
                    )
                    rendered_parts.append(chunk)
                    sections.append({
                        "kind": str(part["kind"]),
                        "start": offset,
                        "end": offset + len(chunk),
                        "token_estimate": section_token_estimate,
                        **{
                            key: value
                            for key, value in part.items()
                            if key not in {"text", "chunk", "score", "token_estimate"}
                        },
                    })
                    offset += len(chunk)
                    total_token_estimate += section_token_estimate
                    partially_rendered_current = True
                char_omitted_parts = selected_parts[
                    index + (1 if partially_rendered_current else 0) :
                ]
                truncated = True
                break
            rendered_parts.append(chunk)
            section_token_estimate = int(part["token_estimate"])
            sections.append({
                "kind": str(part["kind"]),
                "start": offset,
                "end": offset + len(chunk),
                "token_estimate": section_token_estimate,
                **{
                    key: value
                    for key, value in part.items()
                    if key not in {"text", "chunk", "score", "token_estimate"}
                },
            })
            offset += len(chunk)
            total_token_estimate += section_token_estimate
        omitted_sections = [
            _omitted_section_record(part) for part in [*char_omitted_parts, *omitted_parts]
        ]
        return (
            "".join(rendered_parts).rstrip(),
            sections,
            truncated,
            total_token_estimate,
            omitted_sections,
        )


def _normalize_render_profile(render_profile: str, optimize_context: bool) -> str:
    profile = render_profile.strip().lower() or "full"
    if profile not in _RENDER_PROFILES:
        raise ValueError(f"Unsupported render profile: {render_profile}")
    if optimize_context and profile == "full":
        return "compact"
    return profile


def _is_comment_line(path: Path, line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if path.suffix == ".py":
        return stripped.startswith("#")
    if path.suffix in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return (
            stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.startswith("*/")
        )
    return False


def _python_ast_omitted_relative_lines(
    block: str, profile: str = "compact", strip_docstrings: bool = True
) -> tuple[set[int], set[int]]:
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return set(), set()

    docstring_lines: set[int] = set()
    boilerplate_lines: set[int] = set()

    def _walk_and_strip(nodes: list[ast.stmt], parent: ast.AST | None = None) -> None:
        if not nodes:
            return

        # Check if the first node in this body is a docstring
        first = nodes[0]
        first_value = getattr(first, "value", None)
        is_docstring = (
            isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and isinstance(first, ast.Expr)
            and isinstance(first_value, ast.Constant)
            and isinstance(first_value.value, str)
        )

        if is_docstring and (profile == "compact" or strip_docstrings):
            end_lineno = getattr(first, "end_lineno", first.lineno)
            docstring_lines.update(range(first.lineno, end_lineno + 1))

        if profile in {"compact", "llm"}:
            # Strip 'pass' if it's the only node or only node after docstring
            if len(nodes) == 1 or (len(nodes) == 2 and is_docstring):
                last = nodes[-1]
                if isinstance(last, ast.Pass):
                    end_lineno = getattr(last, "end_lineno", last.lineno)
                    boilerplate_lines.update(range(last.lineno, end_lineno + 1))

        # Recurse into all nodes to find nested functions/classes
        for node in nodes:
            node_body = getattr(node, "body", None)
            if node_body and isinstance(node_body, list):
                _walk_and_strip(node_body, parent=node)

    _walk_and_strip(tree.body)
    return docstring_lines, boilerplate_lines


def _js_ast_omitted_relative_lines(block: str) -> set[int]:
    jsdoc_lines: set[int] = set()
    in_jsdoc = False

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not in_jsdoc:
            if not stripped.startswith("/**"):
                continue
            in_jsdoc = True

        if in_jsdoc:
            jsdoc_lines.add(line_number)
            if "*/" in stripped:
                in_jsdoc = False

    return jsdoc_lines


def _ts_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    jsdoc_lines = _js_ast_omitted_relative_lines(block)
    type_import_lines: set[int] = set()
    in_type_import = False

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not in_type_import:
            if not stripped.startswith("import type"):
                continue
            in_type_import = True

        type_import_lines.add(line_number)
        if ";" in stripped:
            in_type_import = False

    return jsdoc_lines, type_import_lines


def _rust_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    doc_comment_lines: set[int] = set()
    attribute_lines: set[int] = set()
    in_attribute = False
    attribute_bracket_balance = 0

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("///") or stripped.startswith("//!"):
            doc_comment_lines.add(line_number)

        if not in_attribute and re.match(r"^#\[\s*(derive|cfg|allow)\b", stripped):
            in_attribute = True
            attribute_bracket_balance = 0

        if in_attribute:
            attribute_lines.add(line_number)
            attribute_bracket_balance += line.count("[") - line.count("]")
            if attribute_bracket_balance <= 0:
                in_attribute = False

    return doc_comment_lines, attribute_lines


def _render_source_block(
    source: dict[str, Any],
    *,
    render_profile: str,
    optimize_context: bool,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "source_rendering"):
        block = str(source.get("source", ""))
        path = Path(str(source["file"]))
        normalized_profile = _normalize_render_profile(render_profile, optimize_context)
        diagnostics = {
            "original_line_count": 0,
            "rendered_line_count": 0,
            "removed_line_count": 0,
            "removed_comment_lines": 0,
            "removed_blank_lines": 0,
            "removed_docstring_lines": 0,
            "removed_boilerplate_lines": 0,
            "js_jsdoc_removed": 0,
            "ts_type_imports_removed": 0,
            "rust_doc_comments_removed": 0,
            "rust_attributes_removed": 0,
        }
        line_map: list[dict[str, int]] = []

        original_lines = block.splitlines()
        diagnostics["original_line_count"] = len(original_lines)
        if normalized_profile == "full":
            rendered_source = block
            if original_lines:
                line_map.append({
                    "rendered_start_line": 1,
                    "rendered_end_line": len(original_lines),
                    "original_start_line": int(source["start_line"]),
                    "original_end_line": int(source["end_line"]),
                })
            diagnostics["rendered_line_count"] = len(original_lines)
        else:
            kept_lines: list[str] = []
            current_segment: dict[str, int] | None = None
            rendered_line_number = 1
            original_start = int(source["start_line"])
            omitted_docstring_lines: set[int] = set()
            omitted_boilerplate_lines: set[int] = set()
            omitted_jsdoc_lines: set[int] = set()
            omitted_ts_type_import_lines: set[int] = set()
            omitted_rust_doc_comment_lines: set[int] = set()
            omitted_rust_attribute_lines: set[int] = set()
            if path.suffix == ".py":
                omitted_docstring_lines, omitted_boilerplate_lines = (
                    _python_ast_omitted_relative_lines(
                        block, normalized_profile, strip_docstrings=optimize_context
                    )
                )
            elif path.suffix in _TS_SUFFIXES:
                omitted_jsdoc_lines, omitted_ts_type_import_lines = _ts_ast_omitted_relative_lines(
                    block
                )
            elif path.suffix in _JS_TS_SUFFIXES:
                omitted_jsdoc_lines = _js_ast_omitted_relative_lines(block)
            elif path.suffix in _RUST_SUFFIXES:
                omitted_rust_doc_comment_lines, omitted_rust_attribute_lines = (
                    _rust_ast_omitted_relative_lines(block)
                )
            for index, line in enumerate(original_lines):
                original_line_number = original_start + index
                relative_line_number = index + 1
                if not line.strip():
                    diagnostics["removed_blank_lines"] += 1
                    continue
                if relative_line_number in omitted_jsdoc_lines:
                    diagnostics["removed_comment_lines"] += 1
                    diagnostics["js_jsdoc_removed"] += 1
                    continue
                if relative_line_number in omitted_ts_type_import_lines:
                    diagnostics["ts_type_imports_removed"] += 1
                    continue
                if relative_line_number in omitted_rust_doc_comment_lines:
                    diagnostics["removed_comment_lines"] += 1
                    diagnostics["rust_doc_comments_removed"] += 1
                    continue
                if relative_line_number in omitted_rust_attribute_lines:
                    diagnostics["removed_boilerplate_lines"] += 1
                    diagnostics["rust_attributes_removed"] += 1
                    continue
                if _is_comment_line(path, line):
                    diagnostics["removed_comment_lines"] += 1
                    continue
                if relative_line_number in omitted_docstring_lines:
                    diagnostics["removed_docstring_lines"] += 1
                    continue
                if relative_line_number in omitted_boilerplate_lines:
                    diagnostics["removed_boilerplate_lines"] += 1
                    continue

                kept_lines.append(line)
                if (
                    current_segment is None
                    or original_line_number != current_segment["original_end_line"] + 1
                ):
                    current_segment = {
                        "rendered_start_line": rendered_line_number,
                        "rendered_end_line": rendered_line_number,
                        "original_start_line": original_line_number,
                        "original_end_line": original_line_number,
                    }
                    line_map.append(current_segment)
                else:
                    current_segment["rendered_end_line"] = rendered_line_number
                    current_segment["original_end_line"] = original_line_number
                rendered_line_number += 1

            rendered_source = "\n".join(kept_lines)
            if kept_lines and block.endswith("\n"):
                rendered_source += "\n"
            diagnostics["rendered_line_count"] = len(kept_lines)
            diagnostics["removed_line_count"] = (
                diagnostics["original_line_count"] - diagnostics["rendered_line_count"]
            )

    rendered = dict(source)
    rendered["render_profile"] = normalized_profile
    rendered["optimize_context"] = optimize_context
    rendered["rendered_source"] = rendered_source
    rendered["line_map"] = line_map
    rendered["render_diagnostics"] = diagnostics
    return rendered


def _confidence_from_score(score: int) -> float:
    if score <= 0:
        return 0.0
    return round(min(1.0, 0.35 + (score / 20.0)), 3)


def _primary_span_for_symbol(symbol: dict[str, Any] | None) -> dict[str, int] | None:
    if symbol is None:
        return None
    start_line = symbol.get("start_line", symbol.get("line"))
    end_line = symbol.get("end_line", start_line)
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return None
    return {
        "start_line": start_line,
        "end_line": end_line,
    }


def _validation_repo_root(repo_root: str | Path) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if root.is_file():
        root = root.parent
    markers = (
        "package.json",
        "package-lock.json",
        "pnpm-workspace.yaml",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Cargo.toml",
        "tsconfig.json",
    )
    boundary_markers = (
        "README.md",
        ".gitignore",
        "LICENSE",
        "AGENTS.md",
    )
    boundary_candidate: Path | None = None
    current = root
    temp_root = Path(tempfile.gettempdir()).resolve()
    while True:
        if current == temp_root and root != temp_root:
            break
        if any((current / marker).exists() for marker in markers):
            return current
        # F84 Fix A: a directory is only a STRONG boundary -- and thus allowed to trap the
        # walk-up before its parent is examined -- when it has `.git` (dir or file, the
        # definitive repo-root signal every other tool uses) OR carries >=2 distinct boundary
        # markers. A LONE README.md/.gitignore/LICENSE/AGENTS.md living in a scoped
        # subdirectory used to trap the walk one level too early (the boundary-README trap),
        # so a single marker is no longer sufficient on its own.
        matched_boundary_markers = sum(
            1 for marker in boundary_markers if (current / marker).exists()
        )
        if (current / ".git").exists() or matched_boundary_markers >= 2:
            boundary_candidate = current
        if current.parent == current:
            break
        next_current = current.parent
        if boundary_candidate is not None and next_current == boundary_candidate.parent:
            break
        current = next_current
    return boundary_candidate or root


def _read_package_json(root: Path) -> dict[str, Any]:
    """O(1) fixed-path read of ``<root>/package.json`` -- no directory scan. Returns ``{}``
    when the file is missing, unreadable, or not a JSON object."""
    package_json_path = root / "package.json"
    if not package_json_path.is_file():
        return {}
    try:
        loaded = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _infer_js_package_manager(root: Path, package_json: dict[str, Any]) -> str:
    package_manager = package_json.get("packageManager")
    if isinstance(package_manager, str):
        normalized = package_manager.split("@", 1)[0].strip().lower()
        if normalized in {"npm", "pnpm", "yarn", "bun"}:
            return normalized
    if (root / "pnpm-lock.yaml").is_file() or (root / "pnpm-workspace.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file():
        return "bun"
    return "npm"


def _javascript_repo_fallback_command(package_manager: str) -> str:
    if package_manager == "pnpm":
        return "pnpm test"
    if package_manager == "yarn":
        return "yarn test"
    if package_manager == "bun":
        return "bun test"
    return "npm test"


def _package_test_script_command(root: Path, package_json: dict[str, Any]) -> str | None:
    test_script = _package_test_script(package_json)
    if test_script is None:
        return None
    return _javascript_repo_fallback_command(_infer_js_package_manager(root, package_json))


def _package_test_script(package_json: dict[str, Any]) -> str | None:
    scripts = package_json.get("scripts")
    if not isinstance(scripts, dict):
        return None
    test_script = scripts.get("test")
    if not isinstance(test_script, str) or not test_script.strip():
        return None
    if "no test specified" in test_script.lower():
        return None
    return test_script.strip()


def _package_json_dependency_names(package_json: dict[str, Any]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        current = package_json.get(key)
        if not isinstance(current, dict):
            continue
        dependency_names.update(str(name) for name in current.keys())
    return dependency_names


def _ts_jest_configured(
    repo_root: Path,
    package_json: dict[str, Any],
    package_text: str,
    dependency_names: set[str],
) -> bool:
    if "ts-jest" in dependency_names:
        return True
    if "ts-jest" in package_text:
        return True
    jest_config = package_json.get("jest")
    if jest_config is not None and "ts-jest" in json.dumps(jest_config, sort_keys=True):
        return True
    for config_name in (
        "jest.config.js",
        "jest.config.cjs",
        "jest.config.mjs",
        "jest.config.ts",
        "jest.config.json",
    ):
        config_path = repo_root / config_name
        if not config_path.is_file():
            continue
        try:
            if "ts-jest" in config_path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _detect_validation_runners_from_root(
    root: Path,
    *,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> _ValidationRunnerInfo:
    """#642 gate nit-1 fast-follow: ``deadline_monotonic``/``deadline_hit`` are optional (default
    ``None``, fully backward compatible with every other call site) and thread straight into
    ``_precomputed_validation_files_for_root``'s own per-entry ``Path.resolve()`` loop -- the
    SECOND validation-plan chain the #642 Opus gate named as still-unbounded for
    ``tg context-render``/``tg edit-plan``/``tg context`` (repo_map.py ~11987, reached via
    ``_build_edit_plan_seed``). Mirrors the SAME optional-kwarg contract that function already
    documents for its other callers.
    """
    if not root.exists():
        return _ValidationRunnerInfo(
            False, False, False, "generic", False, (), (), None, None, None
        )

    all_files = _precomputed_validation_files_for_root(
        root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if all_files is None:
        # #222 residual fix -- same gap and shape as `_discover_validation_tests_for_primary_
        # file`'s identical fallback above: thread the deadline this function already accepts
        # into the un-deadlined walk it falls back to.
        all_files = _iter_repo_files(
            root,
            max_files=_VALIDATION_RUNNER_SCAN_LIMIT,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    has_python = any(current.suffix == ".py" for current in all_files)
    has_python_tests = any(
        current.suffix == ".py" and _is_test_file(current) for current in all_files
    )
    has_python_project_marker = any(
        (root / marker).is_file()
        for marker in (
            "pyproject.toml",
            "pytest.ini",
            "setup.py",
            "setup.cfg",
            "tox.ini",
        )
    )
    if has_python_project_marker or has_python_tests:
        python_detection = "detected"
    elif has_python:
        python_detection = "heuristic"
    else:
        python_detection = "generic"
    has_rust = (root / "Cargo.toml").is_file() or any(
        current.name == "Cargo.toml" for current in all_files
    )
    has_javascript = any(current.suffix.lower() in _JS_TS_SUFFIXES for current in all_files)

    package_json: dict[str, Any] = {}
    package_text = ""
    package_json_path = root / "package.json"
    has_package_json = package_json_path.is_file()
    if package_json_path.is_file():
        try:
            package_text = package_json_path.read_text(encoding="utf-8")
            loaded = json.loads(package_text)
            if isinstance(loaded, dict):
                package_json = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            package_json = {}
            package_text = ""

    dependency_names = _package_json_dependency_names(package_json)
    js_runners = tuple(runner for runner in _JS_RUNNER_ORDER if runner in dependency_names)

    ts_runners: list[str] = []
    if "vitest" in dependency_names:
        ts_runners.append("vitest")
    if "jest" in dependency_names and _ts_jest_configured(
        root, package_json, package_text, dependency_names
    ):
        ts_runners.append("jest")
    if "mocha" in dependency_names and ("ts-node" in dependency_names or "tsx" in dependency_names):
        ts_runners.append("mocha")
    js_fallback_command = (
        _javascript_repo_fallback_command(_infer_js_package_manager(root, package_json))
        if has_javascript and has_package_json
        else None
    )
    js_test_script = (
        _package_test_script(package_json) if has_javascript and has_package_json else None
    )
    js_script_command = (
        _package_test_script_command(root, package_json)
        if has_javascript and has_package_json
        else None
    )

    return _ValidationRunnerInfo(
        has_python=has_python,
        has_rust=has_rust,
        has_javascript=has_javascript,
        python_detection=python_detection,
        has_package_json=has_package_json,
        js_runners=js_runners,
        ts_runners=tuple(ts_runners),
        js_script_command=js_script_command,
        js_fallback_command=js_fallback_command,
        js_test_script=js_test_script,
    )


@lru_cache(maxsize=64)
def _detect_validation_runners(repo_root: str) -> _ValidationRunnerInfo:
    root = _validation_repo_root(repo_root)
    return _detect_validation_runners_from_root(root)


def _relative_validation_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def _shell_safe_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value) or '"' in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _candidate_terms(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return _query_terms(normalized.replace("_", " "))


def _python_decorator_qualname(node: ast.AST) -> str | None:
    current = node
    if isinstance(current, ast.Call):
        current = current.func
    if isinstance(current, ast.Name):
        return current.id
    if isinstance(current, ast.Attribute):
        parent = _python_decorator_qualname(current.value)
        return f"{parent}.{current.attr}" if parent else current.attr
    return None


def _best_test_function_candidate(
    candidates: list[str],
    *,
    primary_symbol_name: str | None,
    query: str | None,
) -> str | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    symbol_terms = _candidate_terms(primary_symbol_name)
    query_terms = _candidate_terms(query)
    best_name: str | None = None
    best_score = 0
    for candidate in candidates:
        haystack = candidate.lower()
        score = 0
        if symbol_terms:
            if all(term in haystack for term in symbol_terms):
                score += 6
            score += sum(2 for term in symbol_terms if term in haystack)
        if query_terms:
            score += sum(1 for term in query_terms if term in haystack)
        if score > 0 and candidate.startswith("test_"):
            score += 1
        if score > best_score or (
            score == best_score
            and score > 0
            and best_name is not None
            and len(candidate) < len(best_name)
        ):
            best_name = candidate
            best_score = score
    if best_score <= 0:
        return None
    return best_name


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _python_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = _cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            candidates.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test"):
                    candidates.append(member.name)
    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _python_parametrized_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = _cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []

    def visit_body(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                decorator_names = {
                    name
                    for decorator in node.decorator_list
                    if (name := _python_decorator_qualname(decorator))
                }
                if {
                    "pytest.mark.parametrize",
                    "mark.parametrize",
                } & decorator_names:
                    candidates.append(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit_body(list(node.body))

    visit_body(list(tree.body))
    return tuple(dict.fromkeys(candidates))


def _rust_test_attribute_kind(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    after_hash = stripped[1:].lstrip()
    if not after_hash.startswith("[") or not after_hash.endswith("]"):
        return None
    inner = after_hash[1:-1].strip()
    attribute_name = inner.split("(", 1)[0].strip()
    if attribute_name in {"test", "tokio::test"}:
        return attribute_name
    return None


def _rust_test_function_candidates_from_source(
    source: str,
    *,
    tokio_only: bool,
) -> tuple[str, ...]:
    candidates: list[str] = []
    pending_test_attribute = False
    for line in source.splitlines():
        attribute_kind = _rust_test_attribute_kind(line)
        if not pending_test_attribute:
            if attribute_kind == "tokio::test" or (not tokio_only and attribute_kind == "test"):
                pending_test_attribute = True
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if attribute_kind is not None:
            pending_test_attribute = attribute_kind == "tokio::test" or (
                not tokio_only and attribute_kind == "test"
            )
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = _RUST_TEST_FN_PATTERN.match(line)
        if match:
            candidates.append(match.group(1))
        pending_test_attribute = False

    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=False)


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_tokio_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=True)


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _javascript_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    describe_pattern = re.compile(r"""\bdescribe(?:\.(?:only|skip))?\s*\(\s*["']([^"']+)["']""")
    test_pattern = re.compile(
        r"""(?x)
        (?:
            \b(?:test|it)(?:\.(?:only|skip|todo|concurrent))?\s*\(\s*["']([^"']+)["']
            |
            \b(?:test|it)\.each\s*\([^)]*\)\s*\(\s*["']([^"']+)["']
            |
            \bDeno\.test\s*\(\s*["']([^"']+)["']
        )
        """
    )
    describe_candidates = [
        (match.start(), match.group(1)) for match in describe_pattern.finditer(source)
    ]
    candidates: list[str] = []
    for match in test_pattern.finditer(source):
        target_name = next((group for group in match.groups() if group), None)
        if not target_name:
            continue
        suite_name = None
        for position, candidate in describe_candidates:
            if position >= match.start():
                break
            suite_name = candidate
        if suite_name:
            candidates.append(f"{suite_name} {target_name}".strip())
        candidates.append(target_name)
    return tuple(dict.fromkeys(candidates))


def _framework_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_parametrized_test_function_candidates(test_path)
    if suffix in _JS_TS_SUFFIXES:
        return _javascript_test_function_candidates(test_path)
    if suffix in _RUST_SUFFIXES:
        return _rust_test_function_candidates(test_path)
    return ()


def _framework_test_pattern_bonus(
    test_path: str,
    terms: list[str],
    *,
    raw_query: str | None = None,
) -> int:
    expanded_terms: list[str] = list(terms)
    for candidate_term in _candidate_terms(raw_query):
        if candidate_term not in expanded_terms:
            expanded_terms.append(candidate_term)
    if not expanded_terms:
        return 0
    # perf(edit-plan): textual pre-check -- `_framework_test_function_candidates` triggers an
    # expensive per-file AST parse for every `.py` candidate (`_python_parametrized_test_function_
    # candidates` -> `_cached_ast_parse`), profiled at 46% of `tg context-render`'s wall / 23.9% of
    # `tg prepare`'s, even though most candidates contribute a 0 bonus. Read the file's raw text
    # ONCE and skip straight to the identical `return 0` outcome the expensive parse would have
    # produced when nothing in `expanded_terms` could possibly score.
    #
    # BYTE-IDENTICAL: every string `_score_text_terms` is ever asked to score against is a literal
    # substring of the raw file text -- EXCEPT the JS/TS `describe`+`test` name synthesized in
    # `_javascript_test_function_candidates` (``f"{suite_name} {target_name}"``), which joins two
    # literal-but-non-adjacent quoted-string substrings with an artificial single space. A term can
    # only score against that synthesized string by being a literal substring of `suite_name` or of
    # `target_name` alone (both real file substrings), OR by straddling exactly that one synthetic
    # join space -- and in the straddle case each half is still individually a literal file
    # substring. Checking each whitespace-split WORD of a term (not just the term as one contiguous
    # run) against the raw file text closes that seam: it is a strictly safer over-approximation (a
    # substring of a term that would have scored is still checked), so this short-circuit never
    # produces a different answer than the un-short-circuited computation below.
    try:
        file_text = _read_source_text_cached(test_path).lower()
    except (OSError, UnicodeDecodeError):
        file_text = None  # can't prove absence -- fall through and let the real path handle it
    if file_text is not None:
        atoms = {word for term in expanded_terms for word in term.lower().split()}
        if not any(atom in file_text for atom in atoms):
            return 0
    candidates = _framework_test_function_candidates(test_path)
    if not candidates:
        return 0
    return (
        max((_score_text_terms(candidate, expanded_terms) for candidate in candidates), default=0)
        * 3
    )


def _javascript_runner_file_command(runner: str, relative_path: str) -> str:
    if runner == "vitest":
        return f"npx vitest run {relative_path}"
    if runner == "mocha":
        return f"npx mocha {relative_path}"
    return f"npx jest {relative_path}"


def _javascript_runner_specific_command(runner: str, relative_path: str, test_filter: str) -> str:
    quoted_filter = _shell_safe_arg(test_filter)
    if runner == "vitest":
        return f"npx vitest run {relative_path} -t {quoted_filter}"
    if runner == "mocha":
        return f"npx mocha {relative_path} --grep {quoted_filter}"
    return f"npx jest {relative_path} --testNamePattern {quoted_filter}"


def _javascript_runner_fallback_command(runner: str) -> str:
    if runner == "vitest":
        return "npx vitest run"
    if runner == "mocha":
        return "npx mocha"
    return "npx jest"


def _javascript_node_test_file_command(relative_path: str) -> str:
    return f"node --test {relative_path}"


def _javascript_test_script_uses_node_test(test_script: str | None) -> bool:
    normalized = (test_script or "").strip().lower()
    return bool(normalized) and "node" in normalized and "--test" in normalized


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _javascript_test_file_uses_node_test(test_path: str) -> bool:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return bool(
        re.search(
            r"""\b(?:import\s+test\s+from\s+["']node:test["']|require\(["']node:test["']\))""",
            source,
        )
        or re.search(r"""\bnode:test\b""", source)
    )


def _rust_file_level_command(test_path: Path, repo_root: Path) -> str | None:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return None
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return None
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    target_parts = parts[tests_index + 1 :]
    if not target_parts:
        return None
    if len(target_parts) == 1:
        target = Path(target_parts[0]).stem
    else:
        target = Path(target_parts[0]).stem
    if not target:
        return None
    return f"cargo test --test {target}"


def _rust_uses_nested_test_target(test_path: Path, repo_root: Path) -> bool:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return False
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return False
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    return len(parts[tests_index + 1 :]) > 1


def _nearest_cargo_manifest_for_path(path: str | Path, repo_root: Path) -> Path | None:
    try:
        current = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if current.is_file() or current.suffix:
        current = current.parent
    try:
        boundary = repo_root.expanduser().resolve()
    except (OSError, RuntimeError):
        boundary = repo_root
    while True:
        manifest = current / "Cargo.toml"
        if manifest.is_file():
            return manifest
        if current == boundary or current.parent == current:
            break
        current = current.parent
    return None


def _cargo_test_command_for_primary_file(
    primary_file: str | Path | None,
    repo_root: Path,
) -> str | None:
    if primary_file is None or _target_language_for_path(primary_file) != "rust":
        return None
    manifest = _nearest_cargo_manifest_for_path(primary_file, repo_root)
    if manifest is None:
        return None
    try:
        if manifest.parent.resolve() == repo_root.resolve():
            return "cargo test"
    except (OSError, RuntimeError):
        pass
    relative_manifest = _relative_validation_path(manifest, repo_root)
    return f"cargo test --manifest-path {relative_manifest}"


def _primary_language_fallback_validation_steps(
    *,
    repo_root: str | Path,
    primary_file: str | Path | None,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    if primary_file is None:
        return []
    root = _validation_repo_root(repo_root)
    primary_language = _target_language_for_path(primary_file)
    steps: list[dict[str, Any]] = []
    if primary_language == "rust":
        command = _cargo_test_command_for_primary_file(primary_file, root)
        if command is not None:
            steps.append({
                "command": command,
                "scope": "repo",
                "runner": "cargo",
                "confidence": 0.5,
                "detection": "detected",
            })
    elif primary_language == "python" and _has_python_validation_fallback_evidence(
        root,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    ):
        detected = _detect_validation_runners_from_root(
            root,
            precomputed_file_paths=precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
        if detected.python_detection == "detected":
            steps.append({
                "command": "uv run pytest -q",
                "scope": "repo",
                "runner": "pytest",
                "confidence": 0.55,
                "detection": "detected",
            })
    elif primary_language in ("javascript", "typescript") and (root / "package.json").is_file():
        # dogfood F3: a TS/JS primary target used to get NO fallback step at all here (this
        # branch only had rust/python) -- on a repo where the raw per-test validation plan
        # comes back empty (e.g. a scan-ceiling-capped root on a large monorepo), that meant an
        # EMPTY validation_plan even though the repo obviously has a package.json test runner.
        # Fixed-path `.is_file()` probe only -- no directory scan (see AGENTS.md no-repo-wide-
        # scan rule).
        package_json = _read_package_json(root)
        command = _javascript_repo_fallback_command(_infer_js_package_manager(root, package_json))
        steps.append({
            "command": command,
            "scope": "repo",
            "runner": "javascript",
            "confidence": 0.5,
            "detection": "detected",
        })
    return steps


def _has_python_validation_fallback_evidence(
    root: Path,
    *,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> bool:
    if any(
        (root / marker).is_file()
        for marker in (
            "pyproject.toml",
            "pytest.ini",
            "setup.py",
            "setup.cfg",
            "tox.ini",
        )
    ):
        return True
    candidate_files = _precomputed_validation_files_for_root(
        root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if candidate_files is None:
        # #222 residual fix -- same gap and shape as the two identical fallbacks above.
        candidate_files = _iter_repo_files(
            root,
            max_files=_VALIDATION_RUNNER_SCAN_LIMIT,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    return any(current.suffix == ".py" and _is_test_file(current) for current in candidate_files)


_ROOT_TEST_DIR_NAMES = ("test", "tests", "__tests__")


def _suggested_validation_command_candidates(
    source_path: Path,
    *,
    repo_root: Path | None = None,
) -> list[Path]:
    """Pure-filename test-neighbor CANDIDATES for one source file — no execution, no repo
    scan, no manifest lookup. Distinct from `_has_python_validation_fallback_evidence` (the
    strict runner-evidence gate); this only feeds the additive, unverified suggestion field.

    `repo_root`, when given and distinct from the primary file's own directory, ALSO probes a
    fixed set of repo-root test-tree paths (`<root>/tests/test_<stem>.py`,
    `<root>/test|tests|__tests__/<stem>.test<suffix>`) so a test living in a root-level test
    tree (not next to the source file) is still discoverable. Every probe is a single
    `is_file()` check on a fully-determined path — never a directory walk/glob (dogfood F3: a
    root-tree GLOB scan here would violate the no-repo-wide-scan promise)."""
    suffix = source_path.suffix.lower()
    stem = source_path.stem
    parent = source_path.parent
    root = repo_root if repo_root is not None and repo_root != parent else None
    if suffix == ".py":
        candidates = [
            parent / "tests" / f"test_{stem}.py",
            parent / f"{stem}_test.py",
            parent / f"test_{stem}.py",
        ]
        if root is not None:
            candidates.append(root / "tests" / f"test_{stem}.py")
        return candidates
    if suffix in _JS_TS_SUFFIXES:
        candidates = [
            parent / "__tests__" / f"{stem}{suffix}",
            parent / "__tests__" / f"{stem}.test{suffix}",
            parent / f"{stem}.test{suffix}",
            parent / f"{stem}.spec{suffix}",
        ]
        if root is not None:
            candidates.extend(
                root / dir_name / f"{stem}.test{suffix}" for dir_name in _ROOT_TEST_DIR_NAMES
            )
        return candidates
    return []


def _suggested_validation_command_for_primary_file(
    primary_file: str | Path | None,
    repo_root: str | Path,
) -> dict[str, Any] | None:
    """Build the ADDITIVE `suggested_validation_commands` entry (verified: false) from a pure
    filename probe of the primary target's directory. NEVER feeds the strict, evidence-gated
    `validation_commands` list — no subprocess, no manifest read, no repo-wide scan."""
    if not primary_file:
        return None
    try:
        source_path = Path(primary_file).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if not source_path.is_file():
        return None
    try:
        root = _validation_repo_root(repo_root)
    except (OSError, RuntimeError):
        root = source_path.parent
    candidates = _suggested_validation_command_candidates(source_path, repo_root=root)
    if not candidates:
        return None
    test_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if test_path is None:
        return None
    relative_test = _relative_validation_path(test_path, root)
    suffix = source_path.suffix.lower()
    if suffix == ".py":
        command = f"pytest {relative_test}"
    elif suffix in _TS_SUFFIXES:
        command = f"vitest run {relative_test}"
    else:
        command = f"jest {relative_test}"

    return {
        "command": command,
        "target_test": relative_test,
        "basis": "test-neighbor-heuristic",
        "verified": False,
    }


def _without_heuristic_repo_cargo_fallback(
    validation_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(step)
        for step in validation_plan
        if not (
            str(step.get("runner") or "") == "cargo"
            and str(step.get("command") or "") == "cargo test"
            and str(step.get("scope") or "") == "repo"
            and str(step.get("detection") or "") == "heuristic"
        )
    ]


def _ensure_primary_language_validation_fallback(
    validation_plan: list[dict[str, Any]],
    *,
    repo_root: str | Path,
    primary_file: str | Path | None,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    primary_language = _target_language_for_path(primary_file)
    if primary_language is None:
        return validation_plan
    fallback_steps = _primary_language_fallback_validation_steps(
        repo_root=repo_root,
        primary_file=primary_file,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if any(
        _validation_step_matches_primary_language(step, primary_language)
        for step in validation_plan
    ):
        fallback_commands = {str(step.get("command") or "") for step in fallback_steps}
        if (
            primary_language == "rust"
            and fallback_commands
            and not any(
                str(step.get("command") or "") in fallback_commands for step in validation_plan
            )
            and any(
                str(step.get("runner") or "") == "cargo"
                and str(step.get("command") or "") == "cargo test"
                and str(step.get("detection") or "") == "heuristic"
                for step in validation_plan
            )
        ):
            augmented = [
                dict(step)
                for step in validation_plan
                if not (
                    str(step.get("runner") or "") == "cargo"
                    and str(step.get("command") or "") == "cargo test"
                    and str(step.get("detection") or "") == "heuristic"
                )
            ]
            augmented.extend(dict(step) for step in fallback_steps)
            return augmented
        if primary_language == "python" and fallback_commands:
            return _without_heuristic_repo_cargo_fallback(validation_plan)
        return validation_plan
    if not fallback_steps:
        return validation_plan
    base_plan = (
        _without_heuristic_repo_cargo_fallback(validation_plan)
        if primary_language == "python"
        else validation_plan
    )
    seen = {str(step.get("command") or "") for step in base_plan}
    augmented = [dict(step) for step in base_plan]
    for step in fallback_steps:
        command = str(step.get("command") or "")
        if command and command not in seen:
            seen.add(command)
            augmented.append(dict(step))
    return augmented


def _validation_commands_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | Path] | None = None,
) -> list[str]:
    return [
        str(step["command"])
        for step in _validation_plan_for_tests(
            tests,
            repo_root=repo_root,
            primary_test=primary_test,
            primary_symbol=primary_symbol,
            query=query,
            precomputed_file_paths=precomputed_file_paths,
        )
    ]


def _raw_validation_plan_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    explicit_root = Path(repo_root).expanduser().resolve()
    if explicit_root.is_file():
        explicit_root = explicit_root.parent
    resolved_tests = [Path(current).expanduser().resolve() for current in tests]
    tests_under_explicit_root = bool(resolved_tests) and all(
        _path_is_relative_to(current, explicit_root) for current in resolved_tests
    )
    root = explicit_root if tests_under_explicit_root else _validation_repo_root(explicit_root)
    if tests:
        detected = _detect_validation_runners_from_root(
            root,
            precomputed_file_paths=precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    else:
        local_files = _precomputed_validation_files_for_root(
            explicit_root,
            precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
        if local_files is None:
            # #222 residual fix -- same gap and shape as `_detect_validation_runners_from_root`'s
            # identical fallback (the "has tests" branch above): thread the deadline this
            # function already accepts into the un-deadlined walk it falls back to.
            local_files = _iter_repo_files(
                explicit_root,
                max_files=_VALIDATION_RUNNER_SCAN_LIMIT,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        local_has_python = any(current.suffix == ".py" for current in local_files)
        local_has_rust = any(current.suffix in _RUST_SUFFIXES for current in local_files)
        local_has_javascript = any(
            current.suffix.lower() in _JS_TS_SUFFIXES for current in local_files
        )
        if local_has_python and not local_has_javascript and not local_has_rust:
            detected = _detect_validation_runners_from_root(
                explicit_root,
                precomputed_file_paths=precomputed_file_paths,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        elif precomputed_file_paths is not None:
            detected = _detect_validation_runners_from_root(
                root,
                precomputed_file_paths=precomputed_file_paths,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        else:
            detected = _detect_validation_runners(str(root))
    has_javascript_validation = bool(
        detected.js_runners
        or detected.ts_runners
        or detected.js_script_command
        or detected.js_fallback_command
    )
    has_python_validation = detected.python_detection == "detected"
    primary_symbol_name = (
        str(primary_symbol.get("name"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("name")
        else None
    )
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_javascript_runners: list[str] = []
    include_python_fallback = False
    include_rust_fallback = False

    def remember_runner(runner: str) -> None:
        if runner not in requested_javascript_runners:
            requested_javascript_runners.append(runner)

    def add_step(
        command: str,
        *,
        scope: str,
        runner: str,
        target: str | None = None,
        confidence: float,
        detection: str,
    ) -> None:
        if command in seen:
            return
        seen.add(command)
        step: dict[str, Any] = {
            "command": command,
            "scope": scope,
            "runner": runner,
            "confidence": round(min(1.0, max(0.0, confidence)), 3),
            "detection": detection,
        }
        if target:
            step["target"] = target
        plan.append(step)

    for current in tests:
        path = Path(current)
        suffix = path.suffix.lower()
        absolute_path = str(path.resolve())
        relative_path = _relative_validation_path(path, root)
        is_primary_test = primary_test is not None and absolute_path == str(
            Path(primary_test).resolve()
        )

        if suffix == ".py":
            include_python_fallback = True
            if is_primary_test:
                test_filter = _best_test_function_candidate(
                    list(_python_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    add_step(
                        f"uv run pytest {relative_path} -k {test_filter} -q",
                        scope="symbol",
                        runner="pytest",
                        target=relative_path,
                        confidence=0.95,
                        detection="detected",
                    )
            add_step(
                f"uv run pytest {relative_path} -q",
                scope="file",
                runner="pytest",
                target=relative_path,
                confidence=0.82,
                detection="detected",
            )
            continue

        if suffix in _TS_SUFFIXES:
            test_candidates = (
                list(_javascript_test_function_candidates(absolute_path)) if is_primary_test else []
            )
            test_filter = _best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            for runner in detected.ts_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _javascript_runner_specific_command(runner, relative_path, test_filter),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                        detection="detected",
                    )
                add_step(
                    _javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                    detection="detected",
                )
            continue

        if suffix in _JS_TS_SUFFIXES:
            test_candidates = (
                list(_javascript_test_function_candidates(absolute_path)) if is_primary_test else []
            )
            test_filter = _best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            script_uses_node_test = _javascript_test_script_uses_node_test(detected.js_test_script)
            primary_file_uses_node_test = (
                is_primary_test
                and not script_uses_node_test
                and _javascript_test_file_uses_node_test(absolute_path)
            )
            if script_uses_node_test or primary_file_uses_node_test:
                add_step(
                    _javascript_node_test_file_command(relative_path),
                    scope="file",
                    runner="node:test",
                    target=relative_path,
                    confidence=0.84,
                    detection="detected",
                )
            for runner in detected.js_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _javascript_runner_specific_command(runner, relative_path, test_filter),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                        detection="detected",
                    )
                add_step(
                    _javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                    detection="detected",
                )
            continue

        if suffix in _RUST_SUFFIXES:
            include_rust_fallback = True
            file_level_command = _rust_file_level_command(path, root)
            if is_primary_test:
                test_filter = _best_test_function_candidate(
                    list(_rust_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    targeted_command = (
                        f"{file_level_command} {test_filter}"
                        if file_level_command and _rust_uses_nested_test_target(path, root)
                        else f"cargo test {test_filter}"
                    )
                    add_step(
                        targeted_command,
                        scope="symbol",
                        runner="cargo",
                        target=relative_path,
                        confidence=0.88,
                        detection="detected",
                    )
            if file_level_command:
                add_step(
                    file_level_command,
                    scope="file",
                    runner="cargo",
                    target=relative_path,
                    confidence=0.8,
                    detection="detected",
                )
            continue

    if not tests:
        if has_javascript_validation:
            include_python_fallback = include_python_fallback
        else:
            include_python_fallback = include_python_fallback or has_python_validation
        include_rust_fallback = include_rust_fallback or detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if (
        not include_python_fallback
        and not include_rust_fallback
        and not requested_javascript_runners
    ):
        include_python_fallback = not has_javascript_validation and (
            has_python_validation and not detected.has_rust
        )
        include_rust_fallback = detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if include_python_fallback:
        add_step(
            "uv run pytest -q",
            scope="repo",
            runner="pytest",
            confidence=0.55,
            detection=detected.python_detection,
        )
    if detected.js_script_command and (requested_javascript_runners or detected.has_javascript):
        add_step(
            detected.js_script_command,
            scope="repo",
            runner="javascript",
            confidence=0.65,
            detection="detected",
        )
    else:
        for runner in requested_javascript_runners:
            add_step(
                _javascript_runner_fallback_command(runner),
                scope="repo",
                runner=runner,
                confidence=0.5,
                detection="heuristic",
            )
        if (
            detected.has_javascript
            and not requested_javascript_runners
            and detected.js_fallback_command
        ):
            add_step(
                detected.js_fallback_command,
                scope="repo",
                runner="javascript",
                confidence=0.45,
                detection="heuristic",
            )
    if include_rust_fallback:
        add_step(
            "cargo test",
            scope="repo",
            runner="cargo",
            confidence=0.55,
            detection="detected" if (root / "Cargo.toml").is_file() else "heuristic",
        )

    return plan


def _validation_runner_languages(step: dict[str, Any]) -> set[str]:
    runner = str(step.get("runner", "") or "").strip().lower()
    command = str(step.get("command", "") or "").strip().lower()
    if runner in {"pytest", "python"} or re.search(r"(^|\s)pytest(\s|$)", command):
        return {"python"}
    if runner == "cargo" or command.startswith("cargo "):
        return {"rust"}
    if runner in {"vitest", "jest", "mocha", "node:test", "javascript"}:
        return {"javascript", "typescript"}
    if any(token in command for token in ("vitest", "jest", "mocha", "node --test")):
        return {"javascript", "typescript"}
    if command.startswith(("npm ", "pnpm ", "yarn ", "npx ")):
        return {"javascript", "typescript"}
    return set()


def _validation_step_matches_primary_language(
    step: dict[str, Any],
    primary_language: str | None,
) -> bool:
    if primary_language is None:
        return True
    languages = _validation_runner_languages(step)
    if not languages:
        return True
    return primary_language in languages


def _align_validation_plan_for_primary_language(
    validation_plan: list[dict[str, Any]],
    primary_file: str | Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_language = _target_language_for_path(primary_file)
    aligned: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    issues: list[str] = []
    for raw_step in validation_plan:
        step = dict(raw_step)
        if _validation_step_matches_primary_language(step, primary_language):
            aligned.append(step)
            continue
        filtered.append(step)
        runner = str(step.get("runner", "") or "unknown")
        command = str(step.get("command", "") or "")
        issues.append(
            f"filtered {runner} validation for {primary_language} primary target: {command}"
        )

    if not validation_plan:
        status = "no-validation"
    elif filtered:
        status = "mismatch-filtered"
    else:
        status = "aligned"
    alignment = {
        "status": status,
        "primary_target_language": primary_language,
        "kept_count": len(aligned),
        "filtered_count": len(filtered),
        "issues": issues,
    }
    return aligned, alignment


def _validation_plan_and_alignment_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    primary_file: str | Path | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """#642 gate nit-1 fast-follow: this is the SECOND validation-plan chain the gate named as
    still-unbounded for ``tg context-render``/``tg edit-plan``/``tg context`` (repo_map.py ~11987,
    reached via ``_build_edit_plan_seed``) -- ``deadline_monotonic``/``deadline_hit`` are optional
    (default ``None``, backward compatible with every existing caller) and thread straight through
    ``_raw_validation_plan_for_tests`` to bound the same per-entry ``Path.resolve()`` cost
    ``_precomputed_validation_files_for_root`` already bounds for the FIRST (validation-test
    discovery) chain.
    """
    raw_plan = _raw_validation_plan_for_tests(
        tests,
        repo_root=repo_root,
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        query=query,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    resolved_primary_file = (
        str(primary_symbol.get("file"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("file")
        else (str(primary_file) if primary_file is not None and str(primary_file) else None)
    )
    raw_plan = _ensure_primary_language_validation_fallback(
        raw_plan,
        repo_root=repo_root,
        primary_file=resolved_primary_file,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    return _align_validation_plan_for_primary_language(raw_plan, resolved_primary_file)


def _validation_plan_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    primary_file: str | Path | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | Path] | None = None,
) -> list[dict[str, Any]]:
    plan, _alignment = _validation_plan_and_alignment_for_tests(
        tests,
        repo_root=repo_root,
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        primary_file=primary_file,
        query=query,
        precomputed_file_paths=precomputed_file_paths,
    )
    return plan


def _symbol_sort_key(symbol: dict[str, Any]) -> tuple[int, int, str, str]:
    start_line = int(symbol.get("start_line", symbol.get("line", 0)))
    end_line = int(symbol.get("end_line", start_line))
    return (
        start_line,
        end_line,
        str(symbol.get("kind", "")),
        str(symbol.get("name", "")),
    )


def _symbols_for_file(repo_map: dict[str, Any], file_path: str) -> list[dict[str, Any]]:
    symbols = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("file")) == file_path
    ]
    symbols.sort(key=_symbol_sort_key)
    return symbols


def _enclosing_symbol_for_line(
    repo_map: dict[str, Any],
    file_path: str,
    line_number: int,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for symbol in _symbols_for_file(repo_map, file_path):
        start_line = symbol.get("start_line", symbol.get("line"))
        end_line = symbol.get("end_line", start_line)
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        if start_line <= line_number <= end_line:
            candidates.append(symbol)
    if not candidates:
        return None
    candidates.sort(
        key=lambda symbol: (
            int(symbol.get("end_line", symbol.get("line", 0)))
            - int(symbol.get("start_line", symbol.get("line", 0))),
            *_symbol_sort_key(symbol),
        )
    )
    return candidates[0]


def _related_span_record(
    symbol: dict[str, Any],
    *,
    depth: int,
    score: int,
    reasons: list[str],
) -> dict[str, Any] | None:
    span = _primary_span_for_symbol(symbol)
    file_path = symbol.get("file")
    symbol_name = symbol.get("name")
    if span is None or not file_path or not symbol_name:
        return None
    return {
        "file": str(file_path),
        "symbol": str(symbol_name),
        "start_line": int(span["start_line"]),
        "end_line": int(span["end_line"]),
        "depth": int(depth),
        "score": int(score),
        "reasons": list(reasons),
        "provenance": _provenance_from_reasons(reasons),
        "rationale": _span_rationale(str(symbol_name), list(reasons), int(depth)),
    }


def _ordered_dependent_file_matches(
    radius_payload: dict[str, Any],
    *,
    primary_file: str | None,
    max_depth: int,
) -> list[dict[str, Any]]:
    definition_files = {
        str(current.get("file"))
        for current in radius_payload.get("definitions", [])
        if current.get("file")
    }
    if primary_file:
        definition_files.add(str(primary_file))

    matches: list[dict[str, Any]] = []
    for current in radius_payload.get("file_matches", []):
        current_path = str(current.get("path", ""))
        if not current_path or current_path in definition_files:
            continue
        if _is_test_file(Path(current_path)):
            continue
        depth = int(current.get("depth", max_depth + 1))
        if depth > max_depth:
            continue
        matches.append({
            "path": current_path,
            "depth": depth,
            "score": int(current.get("score", 0)),
            "reasons": list(current.get("reasons", [])),
            "graph_score": float(current.get("graph_score", 0.0)),
        })

    matches = _narrow_python_depth_two_dependency_matches(matches, primary_file=primary_file)

    matches.sort(
        key=lambda current: (
            int(current["depth"]),
            0 if "caller" in current["reasons"] else 1,
            -int(current["score"]),
            -float(current["graph_score"]),
            str(current["path"]),
        )
    )
    return matches


def _narrow_python_depth_two_dependency_matches(
    matches: list[dict[str, Any]],
    *,
    primary_file: str | None,
) -> list[dict[str, Any]]:
    if not primary_file:
        return matches
    primary_name = Path(primary_file).name
    if primary_name not in {"utils.py", "exceptions.py", "termui.py", "_compat.py", "core.py"}:
        return matches
    if primary_name in {"utils.py", "termui.py", "core.py"}:
        has_depth_one = any(int(current.get("depth", 0)) <= 1 for current in matches)
        if not has_depth_one:
            return matches
        return [current for current in matches if int(current.get("depth", 0)) <= 1]
    caller_backed = any("caller" in list(current.get("reasons", [])) for current in matches)
    if not caller_backed:
        return matches
    filtered: list[dict[str, Any]] = []
    for current in matches:
        reasons = list(current.get("reasons", []))
        depth = int(current.get("depth", 0))
        if "caller" not in reasons and depth > 1:
            continue
        filtered.append(current)
    return filtered


def _related_spans_from_blast_radius(
    repo_map: dict[str, Any],
    radius_payload: dict[str, Any],
    *,
    primary_symbol: dict[str, Any] | None,
    dependent_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if primary_symbol is None:
        return [], 0

    primary_key = (
        str(primary_symbol.get("file", "")),
        str(primary_symbol.get("name", "")),
    )
    seen: set[tuple[str, str]] = {primary_key}
    related_spans: list[dict[str, Any]] = []
    caller_keys: set[tuple[str, str]] = set()
    match_by_path = {str(match["path"]): match for match in dependent_matches}

    def _add_symbol(
        symbol: dict[str, Any] | None,
        *,
        is_caller: bool = False,
        depth: int = 0,
        score: int = 0,
        reasons: list[str] | None = None,
    ) -> None:
        if symbol is None:
            return
        key = (str(symbol.get("file", "")), str(symbol.get("name", "")))
        if not key[0] or not key[1] or key in seen:
            return
        record = _related_span_record(
            symbol,
            depth=depth,
            score=score,
            reasons=list(reasons or []),
        )
        if record is None:
            return
        seen.add(key)
        related_spans.append(record)
        if is_caller:
            caller_keys.add(key)

    direct_callers = sorted(
        (
            dict(current)
            for current in radius_payload.get("callers", [])
            if current.get("file")
            and isinstance(current.get("line"), int)
            and not _is_test_file(Path(str(current.get("file"))))
        ),
        key=lambda current: (
            str(current.get("file", "")),
            int(current.get("line", 0)),
            str(current.get("text", "")),
        ),
    )
    for caller in direct_callers:
        caller_path = str(caller["file"])
        caller_match = match_by_path.get(
            caller_path,
            {
                "depth": 1,
                "score": 0,
                "reasons": ["caller"],
            },
        )
        _add_symbol(
            _enclosing_symbol_for_line(
                repo_map,
                caller_path,
                int(caller["line"]),
            ),
            is_caller=True,
            depth=int(caller_match.get("depth", 1)),
            score=int(caller_match.get("score", 0)),
            reasons=list(caller_match.get("reasons", ["caller"])),
        )

    for match in dependent_matches:
        for symbol in _symbols_for_file(repo_map, str(match["path"])):
            _add_symbol(
                symbol,
                depth=int(match.get("depth", 0)),
                score=int(match.get("score", 0)),
                reasons=list(match.get("reasons", [])),
            )
            break

    return related_spans, len(caller_keys)


def _candidate_edit_spans(
    *,
    primary_symbol: dict[str, Any] | None,
    primary_file_match: dict[str, Any],
    related_spans: list[dict[str, Any]],
    max_spans: int,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if primary_symbol is not None:
        span = _primary_span_for_symbol(primary_symbol)
        if span is not None and primary_symbol.get("file") and primary_symbol.get("name"):
            spans.append({
                "file": str(primary_symbol["file"]),
                "symbol": str(primary_symbol["name"]),
                "start_line": int(span["start_line"]),
                "end_line": int(span["end_line"]),
                "depth": 0,
                "score": int(primary_file_match.get("score", 0))
                + int(primary_symbol.get("score", 0)),
                "reasons": list(primary_file_match.get("reasons", [])) or ["primary"],
                "provenance": _provenance_from_reasons(
                    list(primary_file_match.get("reasons", [])) or ["primary"]
                ),
                "rationale": _span_rationale(
                    str(primary_symbol["name"]),
                    list(primary_file_match.get("reasons", [])) or ["primary"],
                    0,
                ),
            })
    spans.extend(dict(current) for current in related_spans)
    spans.sort(
        key=lambda current: (
            int(current.get("depth", 0)),
            -int(current.get("score", 0)),
            str(current.get("file", "")),
            int(current.get("start_line", 0)),
            str(current.get("symbol", "")),
        )
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for current in spans:
        key = (
            str(current.get("file", "")),
            str(current.get("symbol", "")),
            int(current.get("start_line", 0)),
            int(current.get("end_line", 0)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
        if len(deduped) >= max(1, max_spans):
            break
    return deduped


def _mention_ref(file_path: str, start_line: int, end_line: int) -> str:
    normalized_end = max(start_line, end_line)
    if normalized_end <= start_line:
        return f"{file_path}#L{start_line}"
    return f"{file_path}#L{start_line}-L{normalized_end}"


def _navigation_pack(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    max_reads: int,
) -> dict[str, Any]:
    seed = dict(payload.get("edit_plan_seed", {}))
    primary_file = str(seed.get("primary_file", "") or "")
    raw_primary_symbol = seed.get("primary_symbol")
    primary_symbol = dict(raw_primary_symbol) if isinstance(raw_primary_symbol, dict) else {}
    primary_symbol_name = str(primary_symbol.get("name", "") or "")
    raw_primary_span = seed.get("primary_span")
    primary_span = dict(raw_primary_span) if isinstance(raw_primary_span, dict) else {}
    primary_start = int(primary_span.get("start_line", 0) or 0)
    primary_end = int(primary_span.get("end_line", primary_start) or primary_start)
    primary_target = {
        "file": primary_file,
        "symbol": primary_symbol_name,
        "start_line": primary_start,
        "end_line": primary_end,
        "mention_ref": _mention_ref(primary_file, primary_start, primary_end)
        if primary_file and primary_start > 0
        else "",
        "reasons": list(seed.get("reasons", [])),
        "confidence": dict(seed.get("confidence", {})),
    }
    for key in (
        "semantic_provider",
        "provenance",
        "lsp_provider_response",
        "lsp_proof",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in primary_symbol:
            primary_target[key] = primary_symbol[key]

    follow_up_reads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()

    def add_read(entry: dict[str, Any], *, role: str) -> None:
        file_path = str(entry.get("file", "") or "")
        symbol_name = str(entry.get("symbol", "") or "")
        start_line = int(entry.get("start_line", 0) or 0)
        end_line = int(entry.get("end_line", start_line) or start_line)
        if not file_path or start_line <= 0 or end_line < start_line:
            return
        key = (file_path, symbol_name, start_line, end_line)
        if key in seen:
            return
        seen.add(key)
        follow_up_reads.append({
            "file": file_path,
            "symbol": symbol_name,
            "start_line": start_line,
            "end_line": end_line,
            "mention_ref": _mention_ref(file_path, start_line, end_line),
            "role": role,
            "rationale": str(entry.get("rationale", "") or ""),
            "reasons": list(entry.get("reasons", [])),
            "provenance": list(entry.get("provenance", [])),
        })

    if primary_file and primary_symbol_name and primary_start > 0:
        add_read(
            {
                "file": primary_file,
                "symbol": primary_symbol_name,
                "start_line": primary_start,
                "end_line": primary_end,
                "rationale": "Primary edit target selected from ranked symbols and file matches.",
                "reasons": list(seed.get("reasons", [])),
            },
            role="primary",
        )

    for span in list(payload.get("candidate_edit_targets", {}).get("spans", [])):
        span_file = str(span.get("file", "") or "")
        span_symbol = str(span.get("symbol", "") or "")
        role = (
            "primary"
            if span_file == primary_file
            and span_symbol == primary_symbol_name
            and int(span.get("start_line", 0) or 0) == primary_start
            else "related"
        )
        add_read(dict(span), role=role)
        if len(follow_up_reads) >= max(1, max_reads):
            break

    if len(follow_up_reads) < max(1, max_reads):
        validation_tests = [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ]
        for test_path in validation_tests:
            symbols = _symbols_for_file(repo_map, test_path)
            preferred = next(
                (current for current in symbols if str(current.get("name", "")).startswith("test")),
                symbols[0] if symbols else None,
            )
            if preferred is None:
                continue
            span = _primary_span_for_symbol(preferred)
            if span is None:
                continue
            add_read(
                {
                    "file": test_path,
                    "symbol": str(preferred.get("name", "") or ""),
                    "start_line": int(span["start_line"]),
                    "end_line": int(span["end_line"]),
                    "rationale": "Validation target likely to confirm the primary edit outcome.",
                    "reasons": ["validation-test"],
                    "provenance": ["filename-heuristic"],
                },
                role="test",
            )
            if len(follow_up_reads) >= max(1, max_reads):
                break

    grouped_reads: dict[str, list[dict[str, Any]]] = {"related": [], "test": []}
    for current in follow_up_reads:
        role = str(current.get("role", "") or "")
        if role == "related":
            grouped_reads["related"].append(current)
        elif role == "test":
            grouped_reads["test"].append(current)

    parallel_read_groups: list[dict[str, Any]] = []
    prefetched_reads: list[dict[str, Any]] = []
    if primary_file:
        try:
            primary_parent = Path(primary_file).resolve().parent
        except (OSError, ValueError):
            primary_parent = None
        if primary_parent is not None:
            for role in ("related", "test"):
                remaining_reads: list[dict[str, Any]] = []
                for current in grouped_reads[role]:
                    current_file = str(current.get("file", "") or "")
                    try:
                        current_parent = Path(current_file).resolve().parent
                    except (OSError, ValueError):
                        current_parent = None
                    if current_parent is not None and current_parent == primary_parent:
                        prefetched_reads.append(current)
                    else:
                        remaining_reads.append(current)
                grouped_reads[role] = remaining_reads
    if primary_target["mention_ref"]:
        primary_mentions = [str(primary_target["mention_ref"])]
        primary_files = [primary_file] if primary_file else []
        primary_roles = ["primary"]
        for current in prefetched_reads:
            mention = str(current.get("mention_ref", "") or "")
            file_path = str(current.get("file", "") or "")
            role = str(current.get("role", "") or "")
            if mention:
                primary_mentions.append(mention)
            if file_path:
                primary_files.append(file_path)
            if role:
                primary_roles.append(role)
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "primary",
            "can_parallelize": False,
            "mentions": primary_mentions,
            "files": primary_files,
            "roles": primary_roles,
        })
    if grouped_reads["related"]:
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "related",
            "can_parallelize": True,
            "mentions": [
                str(current.get("mention_ref", "") or "") for current in grouped_reads["related"]
            ],
            "files": [str(current.get("file", "") or "") for current in grouped_reads["related"]],
            "roles": ["related"],
        })
    if grouped_reads["test"]:
        parallel_read_groups.append({
            "phase": len(parallel_read_groups),
            "label": "test",
            "can_parallelize": True,
            "mentions": [
                str(current.get("mention_ref", "") or "") for current in grouped_reads["test"]
            ],
            "files": [str(current.get("file", "") or "") for current in grouped_reads["test"]],
            "roles": ["test"],
        })

    return {
        "primary_target": primary_target,
        "follow_up_reads": follow_up_reads,
        "parallel_read_groups": parallel_read_groups,
        "related_tests": [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ],
        "validation_commands": [
            str(current) for current in seed.get("validation_commands", []) if str(current)
        ],
        "suggested_validation_commands": [
            dict(current)
            for current in seed.get("suggested_validation_commands", []) or []
            if isinstance(current, dict)
        ],
        "validation_alignment": dict(seed.get("validation_alignment", {}))
        if isinstance(seed.get("validation_alignment"), dict)
        else {},
        "edit_ordering": [
            str(current) for current in seed.get("edit_ordering", []) if str(current)
        ],
        "rollback_risk": float(seed.get("rollback_risk", 0.0) or 0.0),
    }


def _deterministic_edit_ordering(
    primary_file: str | None,
    dependent_files: list[str],
    tests: list[str],
) -> list[str]:
    ordering: list[str] = []
    for current in [primary_file, *dependent_files, *tests]:
        if not current or current in ordering:
            continue
        ordering.append(str(current))
    return ordering


def _suggested_edit_confidence(score: int, provenance: str | None = None) -> float:
    if score <= 0:
        return 0.0
    normalized_provenance = str(provenance or "").strip().lower()
    adjusted_score = score
    if normalized_provenance in {"heuristic", "regex-heuristic", "filename-convention"}:
        adjusted_score = max(1, score - 3)
    elif normalized_provenance == "graph-derived":
        adjusted_score = max(1, score - 2)
    return _confidence_from_score(adjusted_score)


def _import_update_rationale(symbol: str, module_name: str) -> str:
    return (
        f"this file imports {symbol} from {module_name}; "
        f"if {symbol} changes, this import must be updated"
    )


def _caller_update_rationale(symbol: str, line_number: int) -> str:
    return (
        f"calls {symbol}() on line {line_number}; "
        f"if {symbol}'s signature changes, this call site must be updated"
    )


def _suggested_edit_base_entry(current: dict[str, Any]) -> dict[str, Any]:
    file_path = str(current.get("file", ""))
    reasons = list(current.get("reasons", []))
    edit_kind = "caller-update" if "caller" in reasons else "dependency-update"
    provenance = _symbol_navigation_provenance_for_path(file_path) if file_path else "heuristic"
    return {
        "file": file_path,
        "symbol": str(current.get("symbol", "")),
        "start_line": int(current.get("start_line", 0)),
        "end_line": int(current.get("end_line", 0)),
        "edit_kind": edit_kind,
        "rationale": str(
            current.get(
                "rationale",
                _span_rationale(
                    str(current.get("symbol", "")),
                    reasons,
                    int(current.get("depth", 0)),
                ),
            )
        ),
        "provenance": provenance,
        "confidence": _suggested_edit_confidence(int(current.get("score", 0)), provenance),
    }


def _caller_update_targets_for_span(
    current: dict[str, Any],
    callers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_file = str(current.get("file", ""))
    start_line = int(current.get("start_line", 0))
    end_line = int(current.get("end_line", start_line))
    if not current_file or start_line <= 0 or end_line < start_line:
        return []

    targets_by_line: dict[int, dict[str, Any]] = {}
    for caller in callers:
        if str(caller.get("file", "")) != current_file:
            continue
        line_number = int(caller.get("line", 0))
        if line_number < start_line or line_number > end_line or line_number <= 0:
            continue
        provenance = str(
            caller.get("provenance", _symbol_navigation_provenance_for_path(current_file))
        )
        candidate = {
            "start_line": line_number,
            "end_line": line_number,
            "provenance": provenance,
        }
        existing = targets_by_line.get(line_number)
        if existing is None or float(_suggested_edit_confidence(10, provenance)) > float(
            _suggested_edit_confidence(10, str(existing.get("provenance", "")))
        ):
            targets_by_line[line_number] = candidate

    return [targets_by_line[line] for line in sorted(targets_by_line)]


def _ambiguous_suggested_edit_alternatives(
    primary_symbol: dict[str, Any] | None,
    definitions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if primary_symbol is None:
        return []
    primary_name = str(primary_symbol.get("name", ""))
    primary_file = str(primary_symbol.get("file", ""))
    alternatives: dict[tuple[str, str], dict[str, str]] = {}
    for current in definitions:
        current_file = str(current.get("file", ""))
        current_name = str(current.get("name", ""))
        if not current_file or not current_name:
            continue
        if current_name != primary_name:
            continue
        if current_file == primary_file:
            continue
        key = (current_file, current_name)
        alternatives[key] = {"file": current_file, "symbol": current_name}
    return [alternatives[key] for key in sorted(alternatives)]


def _suggested_edit_priority(entry: dict[str, Any]) -> tuple[int, float, int, int, str, str]:
    edit_kind = str(entry.get("edit_kind", "dependency-update"))
    kind_priority = {
        "caller-update": 0,
        "import-update": 1,
        "dependency-update": 2,
    }.get(edit_kind, 3)
    start_line = int(entry.get("start_line", 0))
    end_line = int(entry.get("end_line", start_line))
    return (
        kind_priority,
        -float(entry.get("confidence", 0.0)),
        max(0, end_line - start_line),
        0 if bool(entry.get("ambiguous")) else 1,
        str(entry.get("symbol", "")),
        str(entry.get("rationale", "")),
    )


def _deduplicate_suggested_edits(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_keys: list[tuple[str, int]] = []
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in suggestions:
        file_path = str(entry.get("file", ""))
        start_line = int(entry.get("start_line", 0))
        if not file_path or start_line <= 0:
            continue
        key = (file_path, start_line)
        existing = deduped.get(key)
        if existing is None:
            ordered_keys.append(key)
            deduped[key] = dict(entry)
            continue
        if _suggested_edit_priority(entry) < _suggested_edit_priority(existing):
            deduped[key] = dict(entry)
    return [deduped[key] for key in ordered_keys]


def _capped_suggested_edits(
    entries: list[dict[str, Any]],
    max_edits: int | None,
) -> list[dict[str, Any]]:
    """Bound ``entries`` to ``max_edits`` items; ``None`` means unbounded. This is the SOLE
    enforcement point for ``_suggested_edits_from_related_spans``'s ``max_edits`` contract (audit
    B9/A18): the parameter used to be accepted but silently ignored, so ``tg edit-plan --json
    --max-files N`` grew ``suggested_edits`` unbounded despite the flag's documented meaning.

    ``max_edits`` is threaded in from ``_build_edit_plan_seed``'s ``suggested_edits_max`` parameter,
    which defaults to ``None`` and is OPT-IN per caller (see that parameter's docstring).
    ``tg edit-plan``'s own builder (``build_context_edit_plan_from_map``, audit B9/A18) was the
    first to pass a real value. Audit #212 (a follow-up to B9/#661) found the identical flag-lie in
    three sibling callers and closed it the same way: ``tg context-render`` (own separate,
    pre-existing downstream cap via ``_compact_edit_plan_seed``, but only for the compact/llm render
    profiles -- the default "full" profile had no cap at all) and ``tg blast-radius-plan``/``tg
    blast-radius-render`` (no cap at all, in any profile) now ALSO opt in, passing their own
    ``--max-files`` value through ``build_context_render_from_map``/``build_symbol_blast_radius_
    plan_from_map``/``build_symbol_blast_radius_render_from_map`` respectively. See
    ``tests/unit/test_edit_plan_max_files_bounds_suggested_edits.py`` and
    ``tests/unit/test_context_render_and_blast_radius_max_files_bounds_suggested_edits.py`` for the
    pins proving so. Any FUTURE caller of ``_build_edit_plan_seed``/``_attach_edit_plan_metadata``
    that does not explicitly pass ``suggested_edits_max`` still gets the unbounded, pre-fix
    behavior -- the low-level default stays ``None`` on purpose so a not-yet-written caller is never
    silently capped."""
    if max_edits is None:
        return entries
    return entries[:max_edits]


def _suggested_edits_from_related_spans(
    related_spans: list[dict[str, Any]],
    *,
    primary_symbol: dict[str, Any] | None = None,
    definitions: list[dict[str, Any]] | None = None,
    callers: list[dict[str, Any]] | None = None,
    repo_root: Path | str | None = None,
    max_edits: int | None = None,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    processed_spans: list[dict[str, Any]] = []
    resolved_callers = [dict(current) for current in callers or []]
    resolved_definitions = [dict(current) for current in definitions or []]
    for current in related_spans:
        reasons = list(current.get("reasons", []))
        if "caller" not in reasons:
            suggestions.append(_suggested_edit_base_entry(current))
            processed_spans.append(current)
            continue

        current_file = str(current.get("file", ""))
        score = int(current.get("score", 0))
        call_targets = _caller_update_targets_for_span(current, resolved_callers)
        ambiguous_alternatives = _ambiguous_suggested_edit_alternatives(
            primary_symbol,
            resolved_definitions,
        )
        if call_targets and primary_symbol is not None:
            primary_name = str(primary_symbol.get("name", ""))
            for target in call_targets:
                entry: dict[str, Any] = {
                    "file": current_file,
                    "symbol": str(current.get("symbol", "")),
                    "start_line": int(target["start_line"]),
                    "end_line": int(target["end_line"]),
                    "edit_kind": "caller-update",
                    "rationale": _caller_update_rationale(
                        primary_name,
                        int(target["start_line"]),
                    ),
                    "provenance": str(target.get("provenance", "heuristic")),
                    "confidence": _suggested_edit_confidence(
                        score,
                        str(target.get("provenance", "heuristic")),
                    ),
                }
                if ambiguous_alternatives:
                    entry["ambiguous"] = True
                    entry["alternatives"] = ambiguous_alternatives
                suggestions.append(entry)
        else:
            fallback_entry = _suggested_edit_base_entry(current)
            if ambiguous_alternatives:
                fallback_entry["ambiguous"] = True
                fallback_entry["alternatives"] = ambiguous_alternatives
            suggestions.append(fallback_entry)
        processed_spans.append(current)

    if primary_symbol is None:
        return _capped_suggested_edits(_deduplicate_suggested_edits(suggestions), max_edits)

    primary_name = str(primary_symbol.get("name", ""))
    definition_path = str(primary_symbol.get("file", ""))
    if not primary_name or not definition_path:
        return _capped_suggested_edits(_deduplicate_suggested_edits(suggestions), max_edits)

    import_updates_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for current in processed_spans:
        current_file = str(current.get("file", ""))
        if not current_file:
            continue
        import_target = _import_update_target(
            Path(current_file),
            primary_name,
            definition_path,
            repo_root,
        )
        if import_target is None:
            continue
        start_line = int(import_target.get("start_line", 0))
        end_line = int(import_target.get("end_line", start_line))
        provenance = str(import_target.get("provenance", "heuristic"))
        module_name = str(import_target.get("module", Path(definition_path).stem))
        import_entry: dict[str, Any] = {
            "file": current_file,
            "symbol": primary_name,
            "start_line": start_line,
            "end_line": end_line,
            "edit_kind": "import-update",
            "rationale": _import_update_rationale(primary_name, module_name),
            "provenance": provenance,
            "confidence": _suggested_edit_confidence(int(current.get("score", 0)), provenance),
        }
        key = (current_file, start_line, end_line)
        existing = import_updates_by_key.get(key)
        if existing is None or float(import_entry["confidence"]) > float(existing["confidence"]):
            import_updates_by_key[key] = import_entry

    suggestions.extend(import_updates_by_key.values())
    return _capped_suggested_edits(_deduplicate_suggested_edits(suggestions), max_edits)


def _preferred_edit_anchor_symbol(
    primary_symbol: dict[str, Any] | None,
    ranked_symbols: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if primary_symbol is not None:
        primary_file = primary_symbol.get("file")
        if primary_file and not _is_test_file(Path(str(primary_file))):
            return primary_symbol
    for symbol in ranked_symbols:
        file_path = symbol.get("file")
        if file_path and not _is_test_file(Path(str(file_path))):
            return symbol
    return primary_symbol


def _symbol_is_unrequested_marker_helper(
    symbol: dict[str, Any] | None,
    query: str,
) -> bool:
    if symbol is None:
        return False
    symbol_name = str(symbol.get("name", "") or "")
    if not symbol_name:
        return False
    query_terms = set(_query_terms(query))
    symbol_terms = set(split_terms(symbol_name))
    return "marker" in symbol_terms and "marker" not in query_terms


def _symbol_name_mentions_marker(symbol: dict[str, Any] | None) -> bool:
    if symbol is None:
        return False
    symbol_name = str(symbol.get("name", "") or "")
    return "marker" in set(split_terms(symbol_name))


def _non_marker_implementation_candidate(
    primary_symbol: dict[str, Any],
    ranked_symbols: list[dict[str, Any]],
    *,
    selected_files: set[str],
    query_language_hints: list[str],
) -> dict[str, Any] | None:
    primary_file = str(primary_symbol.get("file", "") or "")
    primary_score = int(primary_symbol.get("score", 0) or 0)
    primary_span = _symbol_span_length(primary_symbol)
    primary_language_matches_hint = bool(
        query_language_hints
        and _path_matches_query_language_hints(primary_file, query_language_hints)
    )
    for candidate in ranked_symbols:
        candidate_file = str(candidate.get("file", "") or "")
        if not candidate_file or candidate_file not in selected_files:
            continue
        if candidate_file == primary_file:
            continue
        if _is_test_file(Path(candidate_file)):
            continue
        if _symbol_name_mentions_marker(candidate):
            continue
        if primary_language_matches_hint and not _path_matches_query_language_hints(
            candidate_file,
            query_language_hints,
        ):
            continue
        candidate_score = int(candidate.get("score", 0) or 0)
        candidate_span = _symbol_span_length(candidate)
        if candidate_score > primary_score or (
            candidate_score == primary_score and candidate_span >= primary_span
        ):
            return candidate
    return None


def _promote_substantive_symbol_for_edit_seed(
    primary_symbol: dict[str, Any] | None,
    ranked_symbols: list[dict[str, Any]],
    *,
    query: str,
    selected_files: set[str],
    query_language_hints: list[str],
    primary_file_reasons: set[str],
) -> dict[str, Any] | None:
    if primary_symbol is None:
        return None
    primary_file = str(primary_symbol.get("file", "") or "")
    if not primary_file:
        return primary_symbol
    if _symbol_is_unrequested_marker_helper(primary_symbol, query):
        implementation_candidate = _non_marker_implementation_candidate(
            primary_symbol,
            ranked_symbols,
            selected_files=selected_files,
            query_language_hints=query_language_hints,
        )
        if implementation_candidate is not None:
            return implementation_candidate
    # An exactly-named primary symbol (H7) must not be demoted by a higher-scored
    # but merely graph-central candidate.
    if bool(primary_symbol.get("exact_query_match")):
        return primary_symbol
    if "validation-direct-definition" in primary_file_reasons:
        return primary_symbol
    primary_score = int(primary_symbol.get("score", 0) or 0)
    primary_span = _symbol_span_length(primary_symbol)
    primary_language_matches_hint = bool(
        query_language_hints
        and _path_matches_query_language_hints(primary_file, query_language_hints)
    )
    for candidate in ranked_symbols:
        candidate_file = str(candidate.get("file", "") or "")
        if not candidate_file or candidate_file not in selected_files:
            continue
        if _is_test_file(Path(candidate_file)):
            continue
        if primary_language_matches_hint and not _path_matches_query_language_hints(
            candidate_file, query_language_hints
        ):
            continue
        if candidate_file == primary_file:
            return primary_symbol
        candidate_score = int(candidate.get("score", 0) or 0)
        candidate_span = _symbol_span_length(candidate)
        if candidate_score > primary_score or (
            candidate_score == primary_score and candidate_span >= primary_span + 2
        ):
            return candidate
        return primary_symbol
    return primary_symbol


def _should_build_edit_plan_blast_radius(
    symbol: dict[str, Any] | None,
    file_match: dict[str, Any] | None,
) -> bool:
    if symbol is None:
        return False
    if bool(symbol.get("exact_query_match") or symbol.get("bridge_query_match")):
        return True

    reasons = {str(reason) for reason in (file_match or {}).get("reasons", [])}
    symbol_score = int(symbol.get("score", 0) or 0)
    if "validation-direct-definition" in reasons:
        return symbol_score >= 6
    return symbol_score >= 8 and bool(reasons & {"definition", "source", "import"})


def _with_lsp_primary_symbol_evidence(
    repo_map: dict[str, Any],
    primary_symbol: dict[str, Any] | None,
    *,
    semantic_provider: str,
) -> dict[str, Any] | None:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native" or primary_symbol is None:
        return primary_symbol
    symbol_name = str(primary_symbol.get("name", "") or "")
    if not symbol_name:
        return primary_symbol
    try:
        defs_payload = build_symbol_defs_from_map(
            repo_map,
            symbol_name,
            semantic_provider=normalized_provider,
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, LSPTransportError, ValueError):
        return primary_symbol
    proof_definitions = [
        dict(current)
        for current in defs_payload.get("definitions", [])
        if isinstance(current, dict) and _is_lsp_proof_row(current)
    ]
    if not proof_definitions:
        return primary_symbol
    primary_file = str(primary_symbol.get("file", "") or "")
    selected_definition = next(
        (
            current
            for current in proof_definitions
            if primary_file and str(current.get("file", "")) == primary_file
        ),
        proof_definitions[0],
    )
    enriched = dict(primary_symbol)
    for key in (
        "file",
        "kind",
        "line",
        "start_line",
        "end_line",
        "provenance",
        "lsp_provider_response",
        "lsp_operation",
        "lsp_resolution_basis",
    ):
        if key in selected_definition:
            enriched[key] = selected_definition[key]
    if "start_line" not in enriched and "line" in enriched:
        enriched["start_line"] = enriched["line"]
    if "end_line" not in enriched:
        enriched["end_line"] = enriched.get("start_line", enriched.get("line", 1))
    enriched["lsp_proof"] = True
    enriched["semantic_provider"] = normalized_provider
    try:
        enriched["score"] = max(
            int(enriched.get("score", 0) or 0),
            int(selected_definition.get("score", 0) or 0),
        )
    except (TypeError, ValueError):
        pass
    return enriched


def _scoped_repo_map_for_edit_plan_blast_radius(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    symbol: str,
    *,
    max_files: int,
) -> dict[str, Any]:
    limit = max(1, int(max_files)) * _EDIT_PLAN_BLAST_RADIUS_FILE_MULTIPLIER
    all_files = [str(current) for current in repo_map.get("files", [])]
    all_file_set = set(all_files)
    selected_files: list[str] = []
    selected_file_set: set[str] = set()

    def _add_file(path_value: object) -> None:
        if len(selected_files) >= limit:
            return
        path = str(path_value or "")
        if not path or path in selected_file_set or path not in all_file_set:
            return
        selected_files.append(path)
        selected_file_set.add(path)

    for current in repo_map.get("symbols", []):
        if not isinstance(current, dict) or str(current.get("name", "")) != symbol:
            continue
        _add_file(current.get("file"))
    for current in payload.get("files", []):
        _add_file(current)
    for current in payload.get("tests", []):
        _add_file(current)

    if len(selected_files) < limit:
        definition_aliases: set[str] = set()
        for current in selected_files:
            definition_aliases.update(_module_aliases_for_path(current))
        for entry in repo_map.get("imports", []):
            if not isinstance(entry, dict):
                continue
            importer = str(entry.get("file", "") or "")
            if importer in selected_file_set:
                continue
            imported_aliases: set[str] = set()
            for import_name in entry.get("imports", []):
                imported_aliases.update(_import_alias_candidates(str(import_name)))
            if definition_aliases & imported_aliases:
                _add_file(importer)
            if len(selected_files) >= limit:
                break

    selected_file_set = set(selected_files)
    scoped = dict(repo_map)
    scoped["files"] = selected_files
    scoped["symbols"] = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if isinstance(current, dict) and str(current.get("file", "")) in selected_file_set
    ]
    scoped["imports"] = [
        dict(current)
        for current in repo_map.get("imports", [])
        if isinstance(current, dict) and str(current.get("file", "")) in selected_file_set
    ]
    scoped["tests"] = [
        str(current) for current in repo_map.get("tests", []) if str(current) in selected_file_set
    ]
    scoped["edit_plan_blast_radius_scope"] = {
        "max_files": limit,
        "source": "selected-context",
        "original_file_count": len(all_files),
        "scoped_file_count": len(selected_files),
    }
    return scoped


def _rollback_risk_from_blast_radius(
    *,
    dependent_matches: list[dict[str, Any]],
    caller_symbol_count: int,
    test_count: int,
    max_depth: int,
) -> float:
    if not dependent_matches and caller_symbol_count <= 0:
        return 0.0

    normalized_max_depth = max(1, int(max_depth))
    observed_depth = max((int(current["depth"]) for current in dependent_matches), default=0)
    dependent_count = len(dependent_matches)
    depth_factor = min(1.0, observed_depth / normalized_max_depth)
    caller_factor = min(1.0, caller_symbol_count / 5.0)
    dependent_factor = min(1.0, dependent_count / 6.0)
    coverage_factor = min(1.0, test_count / max(1, dependent_count + 1))
    risk = (
        0.1
        + (0.3 * depth_factor)
        + (0.25 * caller_factor)
        + (0.15 * dependent_factor)
        - (0.25 * coverage_factor)
    )
    return round(min(1.0, max(0.0, risk)), 3)


def _is_distinctive_identifier(token: str) -> bool:
    """Return ``True`` when ``token`` looks like a deliberate code identifier.

    Distinctive identifiers carry a structural signal that a bare English word
    does not: an underscore, a digit, or internal case mixing (``camelCase`` /
    ``PascalCase``). This keeps the exact-symbol fallback from latching onto a
    common word (``device``, ``file``) that merely happens to also be a symbol.
    """

    if len(token) < 3:
        return False
    if "_" in token or any(char.isdigit() for char in token):
        return True
    lowered = token.lower()
    return token not in {lowered, token.upper()}


def _exact_query_match_primary_symbol(
    ranked_symbols: list[dict[str, Any]],
    *,
    repo_map: dict[str, Any] | None = None,
    query: str = "",
) -> dict[str, Any] | None:
    """Return the best symbol whose name exactly matches a query token.

    When the query names a symbol that resolves exactly (``exact_query_match``
    was set during scoring), that definition should win primary-target selection
    over a higher-ranked graph-centrality candidate that merely happens to live
    in the top-ranked file. ``ranked_symbols`` is already score-sorted, so the
    first exact match with a resolvable file is the highest-scored exact match.

    Some pipelines (notably context-render) pre-filter ``ranked_symbols`` down to
    symbols defined in the top-ranked files, which can drop the exactly-named
    target before it reaches here. In that case fall back to scanning the full
    ``repo_map`` symbol inventory for a definition matching a query token and
    synthesize a candidate carrying ``exact_query_match`` so it is treated as the
    resolved primary target.
    """

    ranked = next(
        (
            current
            for current in ranked_symbols
            if bool(current.get("exact_query_match")) and current.get("file")
        ),
        None,
    )
    if ranked is not None:
        return ranked
    if repo_map is None or not query:
        return None

    query_tokens = set(re.findall(r"[A-Za-z0-9_]+", query))
    if not query_tokens:
        return None
    # Only fall back for distinctive identifiers the query *names* on purpose
    # (``tg_rewrite_plan``, ``camelCase``, ``Name2``) -- never a bare lowercase
    # English word such as ``device`` that merely happens to also be a symbol,
    # which would hijack descriptive queries away from graph-centrality ranking.
    distinctive_tokens = {token for token in query_tokens if _is_distinctive_identifier(token)}
    if not distinctive_tokens:
        return None
    for definition in repo_map.get("symbols", []):
        if not isinstance(definition, dict):
            continue
        name = str(definition.get("name", "") or "")
        if name not in distinctive_tokens or not definition.get("file"):
            continue
        candidate = dict(definition)
        candidate["exact_query_match"] = True
        return candidate
    return None


def _build_edit_plan_seed(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    ranked_symbols: list[dict[str, Any]],
    query: str,
    max_files: int,
    max_depth: int = _DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    suggested_edits_max: int | None = None,
) -> dict[str, Any]:
    primary_file = next(iter(payload.get("files", [])), None)
    primary_symbol = next(
        (
            current
            for current in ranked_symbols
            if primary_file is not None and str(current.get("file")) == str(primary_file)
        ),
        next(iter(ranked_symbols), None),
    )
    # H7: an exactly-named, resolvable symbol must outrank a graph-centrality
    # candidate that merely lives in the top-ranked file. Promote it here so the
    # capsule's primary_symbol stays coupled to the highest-scored candidate.
    exact_primary = _exact_query_match_primary_symbol(
        ranked_symbols,
        repo_map=repo_map,
        query=query,
    )
    if exact_primary is not None and exact_primary is not primary_symbol:
        primary_symbol = exact_primary
        primary_file = str(exact_primary["file"])
    if primary_symbol is not None and primary_file is not None:
        primary_symbol_file = str(primary_symbol.get("file", "") or "")
        primary_file_has_ranked_symbol = any(
            str(current.get("file", "") or "") == str(primary_file) for current in ranked_symbols
        )
        if primary_symbol_file and primary_symbol_file != str(primary_file):
            if not primary_file_has_ranked_symbol:
                primary_file = primary_symbol_file
    if primary_symbol is not None and primary_file is None:
        preferred_files = _preferred_definition_files(repo_map, str(primary_symbol.get("name", "")))
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    selected_files = {
        str(current) for current in list(payload.get("files", []))[: max(1, int(max_files))]
    }
    promoted_symbol = _promote_substantive_symbol_for_edit_seed(
        primary_symbol,
        ranked_symbols,
        query=query,
        selected_files=selected_files,
        query_language_hints=_query_language_hints(query),
        primary_file_reasons={str(reason) for reason in primary_file_match.get("reasons", [])},
    )
    if promoted_symbol is not None and promoted_symbol is not primary_symbol:
        primary_symbol = promoted_symbol
        if primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
            primary_file_match = next(
                (
                    match
                    for match in payload.get("file_matches", [])
                    if str(match.get("path")) == str(primary_file)
                ),
                payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
            )
    lsp_enriched_symbol = _with_lsp_primary_symbol_evidence(
        repo_map,
        primary_symbol,
        semantic_provider=semantic_provider,
    )
    if lsp_enriched_symbol is not None:
        primary_symbol = lsp_enriched_symbol
        if primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
            primary_file_match = next(
                (
                    match
                    for match in payload.get("file_matches", [])
                    if str(match.get("path")) == str(primary_file)
                ),
                payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
            )
    validation_tests = list(payload.get("tests", []))[: max(1, min(max_files, 3))]
    primary_symbol_name = (
        str(primary_symbol.get("name"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("name")
        else None
    )
    validation_root = (
        _validation_repo_root(Path(str(primary_file)).expanduser().resolve().parent)
        if primary_file is not None
        else None
    )
    validation_file_paths = _repo_map_validation_file_paths(
        repo_map,
        validation_root=validation_root,
    )
    if len(validation_tests) < max(1, min(max_files, 3)):
        for current in _discover_validation_tests_for_primary_file(
            payload.get("path", "."),
            str(primary_file) if primary_file is not None else None,
            primary_symbol_name=primary_symbol_name,
            query=query,
            limit=max(1, min(max_files, 3)) - len(validation_tests),
            precomputed_file_paths=validation_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        ):
            if current not in validation_tests:
                validation_tests.append(current)
    primary_test = next(iter(validation_tests), None)
    primary_test_match: dict[str, Any] = next(
        (
            match
            for match in payload.get("test_matches", [])
            if str(match.get("path")) == str(primary_test)
        ),
        {"score": 1, "reasons": ["validation-discovery"]} if primary_test else {},
    )

    dependent_files: list[str] = []
    related_spans: list[dict[str, Any]] = []
    caller_symbol_count = 0
    rollback_risk = 0.0
    edit_anchor_symbol = _preferred_edit_anchor_symbol(primary_symbol, ranked_symbols)
    edit_anchor_file = (
        str(edit_anchor_symbol.get("file"))
        if edit_anchor_symbol is not None and edit_anchor_symbol.get("file")
        else None
    )
    radius_payload = blast_radius_payload
    if (
        edit_anchor_symbol is not None
        and radius_payload is None
        and _should_build_edit_plan_blast_radius(edit_anchor_symbol, primary_file_match)
    ):
        edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
        if edit_symbol_name:
            radius_repo_map = _scoped_repo_map_for_edit_plan_blast_radius(
                repo_map,
                payload,
                edit_symbol_name,
                max_files=max_files,
            )
            radius_payload = build_symbol_blast_radius_from_map(
                radius_repo_map,
                edit_symbol_name,
                max_depth=max_depth,
            )
            scope = radius_repo_map.get("edit_plan_blast_radius_scope")
            if isinstance(scope, dict):
                radius_payload["edit_plan_blast_radius_scope"] = dict(scope)
    if edit_anchor_symbol is not None and radius_payload is not None:
        dependent_matches = _ordered_dependent_file_matches(
            radius_payload,
            primary_file=edit_anchor_file,
            max_depth=max_depth,
        )
        dependent_files = [str(current["path"]) for current in dependent_matches]
        related_spans, caller_symbol_count = _related_spans_from_blast_radius(
            repo_map,
            radius_payload,
            primary_symbol=edit_anchor_symbol,
            dependent_matches=dependent_matches,
        )
        rollback_risk = _rollback_risk_from_blast_radius(
            dependent_matches=dependent_matches,
            caller_symbol_count=caller_symbol_count,
            test_count=len(radius_payload.get("tests", payload.get("tests", []))),
            max_depth=max_depth,
        )
    dependency_trust = _dependency_trust(repo_map, dependent_files)
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _coverage_summary(payload)))
    suggested_edit_primary_symbol = edit_anchor_symbol or primary_symbol
    suggested_edit_definitions = (
        list(radius_payload.get("definitions", []))
        if radius_payload is not None
        else ([suggested_edit_primary_symbol] if suggested_edit_primary_symbol is not None else [])
    )
    # #642 gate nit-1 fast-follow: this IS the SECOND validation-plan chain the gate named (the
    # FIRST -- _discover_validation_tests_for_primary_file above -- already threads deadline_
    # monotonic/deadline_hit). Reuse this function's own already-threaded `deadline_hit` flag (not
    # a fresh one) so an overrun HERE folds into the SAME `edit_plan_seed_deadline_hit` union
    # `_attach_edit_plan_metadata` already checks -- no new fold-in site needed.
    validation_plan, validation_alignment = _validation_plan_and_alignment_for_tests(
        validation_tests,
        repo_root=payload.get("path", "."),
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        primary_file=str(primary_file) if primary_file is not None else None,
        query=query,
        precomputed_file_paths=validation_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    validation_commands = [str(step["command"]) for step in validation_plan]
    # Additive, unverified suggestion (test-neighbor filename probe) — NEVER merged into the
    # strict, evidence-gated `validation_commands`/`validation_plan` above. See
    # `_suggested_validation_command_for_primary_file`.
    suggested_validation_command = (
        _suggested_validation_command_for_primary_file(
            primary_file,
            validation_root if validation_root is not None else payload.get("path", "."),
        )
        if primary_file is not None
        else None
    )
    suggested_validation_commands = (
        [suggested_validation_command] if suggested_validation_command is not None else []
    )
    confidence = {
        "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
        "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
        if primary_symbol is not None
        else 0.0,
        "test": _confidence_from_score(int(primary_test_match.get("score", 0))),
    }
    if int(validation_alignment.get("filtered_count", 0) or 0) > 0:
        confidence = {key: round(min(float(value), 0.65), 3) for key, value in confidence.items()}

    return {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": primary_test,
        "validation_tests": validation_tests,
        "validation_commands": validation_commands,
        "validation_plan": validation_plan,
        "validation_alignment": validation_alignment,
        "suggested_validation_commands": suggested_validation_commands,
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": confidence,
        "related_spans": related_spans,
        "suggested_edits": _suggested_edits_from_related_spans(
            related_spans,
            primary_symbol=suggested_edit_primary_symbol,
            definitions=suggested_edit_definitions,
            callers=list(radius_payload.get("callers", [])) if radius_payload is not None else [],
            repo_root=Path(str(repo_map["path"])).resolve(),
            # Opt-in only (audit B9/A18 fix scope, broadened by #212): `suggested_edits_max` defaults
            # to `None` (unbounded, byte-identical to every UNNAMED caller's pre-fix behavior) unless
            # the caller of `_build_edit_plan_seed` explicitly requests a bound. `tg edit-plan`'s own
            # builder (`build_context_edit_plan_from_map`) opted in first (B9/A18); `tg context-render`
            # (which also has its own separate, pre-existing downstream cap for the compact/llm
            # profiles only -- see `_compact_edit_plan_seed` -- but none for the default "full"
            # profile) and `tg blast-radius-plan`/`tg blast-radius-render` (which had no cap at all,
            # in any profile) now ALSO opt in (#212, a follow-up to B9/#661), each passing its own
            # `--max-files` value through `_attach_edit_plan_metadata`. Any future caller that omits
            # `suggested_edits_max` still gets the unbounded pre-fix shape.
            max_edits=suggested_edits_max,
        ),
        "dependency_trust": dependency_trust,
        "plan_trust_summary": _plan_trust_summary(
            ranking_quality,
            coverage_summary,
            dependency_trust,
        ),
        "dependent_files": dependent_files,
        "edit_ordering": _deterministic_edit_ordering(
            edit_anchor_file or (str(primary_file) if primary_file is not None else None),
            dependent_files,
            [str(current) for current in payload.get("tests", [])],
        ),
        "rollback_risk": rollback_risk,
        "blast_radius_scope": dict(radius_payload.get("edit_plan_blast_radius_scope", {}))
        if isinstance(radius_payload, dict)
        else {},
    }


def _sorted_ranked_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(symbols, key=_symbol_rank_key)


def _attach_edit_plan_headline_aliases(payload: dict[str, Any]) -> None:
    navigation_pack = payload.get("navigation_pack")
    edit_plan_seed = payload.get("edit_plan_seed")
    if not isinstance(navigation_pack, dict) or not isinstance(edit_plan_seed, dict):
        return
    primary_target = navigation_pack.get("primary_target")
    if isinstance(primary_target, dict) and primary_target:
        payload["primary_target"] = dict(primary_target)
    edit_order = edit_plan_seed.get("edit_ordering") or navigation_pack.get("edit_ordering")
    if isinstance(edit_order, list):
        payload["edit_order"] = [str(item) for item in edit_order]
    payload["plan"] = {
        "query": str(payload.get("query", "")),
        "primary_file": edit_plan_seed.get("primary_file"),
        "primary_symbol": dict(edit_plan_seed.get("primary_symbol", {}))
        if isinstance(edit_plan_seed.get("primary_symbol"), dict)
        else edit_plan_seed.get("primary_symbol"),
        "primary_span": dict(edit_plan_seed.get("primary_span", {}))
        if isinstance(edit_plan_seed.get("primary_span"), dict)
        else edit_plan_seed.get("primary_span"),
        "edit_order": list(payload.get("edit_order", [])),
        "validation_commands": list(edit_plan_seed.get("validation_commands", [])),
        "suggested_validation_commands": [
            dict(current)
            for current in edit_plan_seed.get("suggested_validation_commands", []) or []
            if isinstance(current, dict)
        ],
        "rollback_risk": edit_plan_seed.get("rollback_risk"),
        "ranking_quality": str(payload.get("ranking_quality", "")),
    }


def _attach_edit_plan_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
    max_depth: int = _DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
    suggested_edits_max: int | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "edit_plan_assembly"):

        def _ordered_candidate_files(
            files: list[str],
            primary_file: str | None,
            *,
            limit: int,
        ) -> list[str]:
            if primary_file is None:
                return files[:limit]
            ordered = [current for current in files if current != primary_file]
            return [primary_file, *ordered][:limit]

        ranked_symbols = _sorted_ranked_symbols(list(payload.get("symbols", [])))
        primary_payload_file = next(iter(payload.get("files", [])), None)
        payload_file_symbol = next(
            (
                current
                for current in ranked_symbols
                if primary_payload_file is not None
                and str(current.get("file")) == str(primary_payload_file)
            ),
            None,
        )
        primary_payload_file_match: dict[str, Any] = next(
            (
                match
                for match in payload.get("file_matches", [])
                if primary_payload_file is not None
                and str(match.get("path")) == str(primary_payload_file)
            ),
            {},
        )
        edit_anchor_symbol = _preferred_edit_anchor_symbol(payload_file_symbol, ranked_symbols)
        resolved_blast_radius_payload = blast_radius_payload
        if (
            edit_anchor_symbol is not None
            and resolved_blast_radius_payload is None
            and _should_build_edit_plan_blast_radius(
                edit_anchor_symbol,
                primary_payload_file_match,
            )
        ):
            edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
            if edit_symbol_name:
                with _profiling_phase(_profiling_collector, "edit_plan_blast_radius"):
                    radius_repo_map = _scoped_repo_map_for_edit_plan_blast_radius(
                        repo_map,
                        payload,
                        edit_symbol_name,
                        max_files=max_files,
                    )
                    resolved_blast_radius_payload = build_symbol_blast_radius_from_map(
                        radius_repo_map,
                        edit_symbol_name,
                        max_depth=max_depth,
                        _profiling_collector=_profiling_collector,
                    )
                    scope = radius_repo_map.get("edit_plan_blast_radius_scope")
                    if isinstance(scope, dict):
                        resolved_blast_radius_payload["edit_plan_blast_radius_scope"] = dict(scope)
        with _profiling_phase(_profiling_collector, "edit_plan_seed"):
            # #639 Opus-gate nit 1 (dogfood #1 RESIDUAL): the validation-file discovery this seed
            # runs (_discover_validation_tests_for_primary_file -> _precomputed_validation_files_
            # for_root) does a per-file Path.resolve() pass over the repo map's file list -- a
            # pre-existing UNBOUNDED cost (see that function's own docstring) that let a `tg agent
            # --deadline` request overrun in this tail even after the checkpointed scan + pack
            # stage finished in budget. Own a flag here (mirrors build_context_pack_from_map's own
            # `own_deadline_hit` pattern) and fold an early break into `payload["partial"]` below,
            # same "any one sibling stage" fold-in shape every other deadline-scoped seam uses.
            edit_plan_seed_deadline_hit = _DeadlineBreakFlag()
            payload["edit_plan_seed"] = _build_edit_plan_seed(
                repo_map,
                payload,
                ranked_symbols=ranked_symbols,
                query=query,
                max_files=max_files,
                max_depth=max_depth,
                blast_radius_payload=resolved_blast_radius_payload,
                semantic_provider=semantic_provider,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=edit_plan_seed_deadline_hit,
                suggested_edits_max=suggested_edits_max,
            )
            if edit_plan_seed_deadline_hit.hit:
                payload["partial"] = True
                # setdefault, not overwrite: never clobber a richer deadline_limit already present
                # from the SCAN stage or build_context_pack_from_map's own self-stamp.
                payload.setdefault("deadline_limit", {"deadline_exceeded": True})
        payload["graph_trust_summary"] = (
            dict(resolved_blast_radius_payload.get("graph_trust_summary", {}))
            if resolved_blast_radius_payload is not None
            else _graph_trust_summary([])
        )
        primary_file = str(payload["edit_plan_seed"].get("primary_file") or "") or None
        payload["candidate_edit_targets"] = {
            "files": _ordered_candidate_files(
                list(payload.get("files", [])),
                primary_file,
                limit=max_files,
            ),
            # Widen the candidate-edit symbol POOL beyond the render cap so a query-relevant
            # implementation symbol cannot be crowded entirely out of `candidate_edit_targets`
            # by same-tier symbols from a large file (Task #4 / agent-capsule moat). The
            # token-bearing `payload["symbols"]` stays at `max_symbols`; only the alternative
            # pool widens, and the capsule's marker-helper swap can then promote the impl.
            "symbols": ranked_symbols[: max(max_symbols, 8)],
            "tests": list(payload.get("tests", []))[:max_files],
            "ranking_quality": str(
                payload.get(
                    "ranking_quality",
                    _ranking_quality(
                        list(payload.get("file_matches", [])),
                        list(payload.get("test_matches", [])),
                    ),
                )
            ),
            "coverage_summary": dict(payload.get("coverage_summary", _coverage_summary(payload))),
            "spans": _candidate_edit_spans(
                primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
                primary_file_match={},
                related_spans=[],
                max_spans=max(max_files, max_symbols),
            ),
        }
        primary_file_match = next(
            (
                match
                for match in payload.get("file_matches", [])
                if str(match.get("path")) == str(primary_file)
            ),
            payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
        )
        payload["candidate_edit_targets"]["spans"] = _candidate_edit_spans(
            primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
            primary_file_match=primary_file_match,
            related_spans=list(payload["edit_plan_seed"].get("related_spans", [])),
            max_spans=max(max_files, max_symbols),
        )
        with _profiling_phase(_profiling_collector, "edit_plan_navigation_pack"):
            payload["navigation_pack"] = _navigation_pack(
                repo_map,
                payload,
                max_reads=max(max_files, max_symbols),
            )
        _attach_edit_plan_headline_aliases(payload)
    return payload


def _attach_lightweight_navigation_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
) -> dict[str, Any]:
    def _ordered_candidate_files(
        files: list[str],
        primary_file: str | None,
        *,
        limit: int,
    ) -> list[str]:
        if primary_file is None:
            return files[:limit]
        ordered = [current for current in files if current != primary_file]
        return [primary_file, *ordered][:limit]

    ranked_symbols = _sorted_ranked_symbols(list(payload.get("symbols", [])))
    primary_symbol = next(iter(ranked_symbols), None)
    primary_file = next(iter(payload.get("files", [])), None)
    if primary_symbol is not None:
        preferred_files = _preferred_definition_files(repo_map, str(primary_symbol.get("name", "")))
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)

    payload["edit_plan_seed"] = {}
    payload["edit_plan_seed_skipped"] = True
    payload["graph_trust_summary"] = _graph_trust_summary([])
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _coverage_summary(payload)))
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    validation_plan, validation_alignment = _validation_plan_and_alignment_for_tests(
        [],
        repo_root=payload.get("path", repo_map.get("path", ".")),
        primary_test=None,
        primary_symbol=primary_symbol,
        primary_file=primary_file,
        query=query,
        precomputed_file_paths=_repo_map_validation_file_paths(
            repo_map,
            validation_root=(
                _validation_repo_root(Path(str(primary_file)).expanduser().resolve().parent)
                if primary_file
                else None
            ),
        ),
        # #642 gate nit-1 fast-follow (Opus-gate N1): this is the render's `include_edit_plan_seed=
        # False` lightweight-navigation sibling of _build_edit_plan_seed's own validation-plan call
        # above -- no LIVE caller currently sets include_edit_plan_seed=False, so this path is
        # unreachable today (the return-time backstop below would already catch it either way),
        # but thread deadline through it too so it stays actually bounded rather than merely
        # backstopped if a future caller ever does take this branch.
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    lightweight_suggested_validation_command = (
        _suggested_validation_command_for_primary_file(primary_file, payload.get("path", "."))
        if primary_file
        else None
    )
    lightweight_seed = {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": None,
        "validation_tests": [],
        "validation_commands": [str(step["command"]) for step in validation_plan],
        "validation_plan": validation_plan,
        "validation_alignment": validation_alignment,
        "suggested_validation_commands": (
            [lightweight_suggested_validation_command]
            if lightweight_suggested_validation_command is not None
            else []
        ),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": {
            "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
            "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
            if primary_symbol is not None
            else 0.0,
            "test": 0.0,
        },
        "related_spans": [],
        "suggested_edits": [],
        "dependency_trust": _dependency_trust(repo_map, []),
        "plan_trust_summary": _plan_trust_summary(
            ranking_quality,
            coverage_summary,
            _dependency_trust(repo_map, []),
        ),
        "dependent_files": [],
        "edit_ordering": _deterministic_edit_ordering(
            str(primary_file) if primary_file is not None else None,
            [],
            [],
        ),
        "rollback_risk": 0.0,
    }
    lightweight_primary_symbol = dict(primary_symbol) if isinstance(primary_symbol, dict) else None
    payload["candidate_edit_targets"] = {
        "files": _ordered_candidate_files(
            list(payload.get("files", [])),
            str(primary_file) if primary_file is not None else None,
            limit=max_files,
        ),
        # Widen the candidate-edit symbol pool beyond the render cap (Task #4 / agent-capsule
        # moat) — same rationale as build_context_render: don't let a large file's same-tier
        # symbols crowd a query-relevant implementation out of the candidate pool.
        "symbols": ranked_symbols[: max(max_symbols, 8)],
        "tests": list(payload.get("tests", []))[:max_files],
        "ranking_quality": ranking_quality,
        "coverage_summary": coverage_summary,
        "spans": _candidate_edit_spans(
            primary_symbol=lightweight_primary_symbol,
            primary_file_match={},
            related_spans=[],
            max_spans=max(max_files, max_symbols),
        ),
    }
    payload["candidate_edit_targets"]["spans"] = _candidate_edit_spans(
        primary_symbol=lightweight_primary_symbol,
        primary_file_match=primary_file_match,
        related_spans=[],
        max_spans=max(max_files, max_symbols),
    )
    navigation_payload = dict(payload)
    navigation_payload["edit_plan_seed"] = lightweight_seed
    payload["navigation_pack"] = _navigation_pack(
        repo_map,
        navigation_payload,
        max_reads=max(max_files, max_symbols),
    )
    return payload


def build_context_edit_plan(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    """``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline, used AS-IS instead of being recomputed from
    ``deadline_seconds`` when supplied. The CLI cold path (``main.edit_plan``) anchors it at
    command entry, before path resolution and the daemon gate, so front-door time is budgeted the
    same way scan time already is. Existing ``deadline_seconds``-only callers are unaffected: the
    fallback computation below is byte-identical to the prior behavior."""
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg edit-plan`
    # (Click "No such option" exit-2). Converted ONCE (moat P0-6 step-3 pattern) and shared across
    # the repo-map build AND edit-plan's own symbol-scoring pass in `_from_map` below.
    if deadline_monotonic is None:
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    return build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_symbols=max_symbols,
        max_sources=max_sources,
        max_tokens=max_tokens,
        semantic_provider=semantic_provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )


def build_context_edit_plan_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    payload = build_context_pack_from_map(
        repo_map,
        query,
        _test_source_limit=max_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    normalized_max_sources = (
        max(1, max_sources) if max_sources is not None else normalized_max_symbols
    )
    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    payload["routing_reason"] = "context-edit-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = [
        {
            **dict(summary),
            "symbols": list(dict(summary).get("symbols", []))[:normalized_max_symbols],
        }
        for summary in list(payload.get("file_summaries", []))[:normalized_max_files]
        if isinstance(summary, dict)
    ]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    selected_file_set = {str(current) for current in payload["files"]}
    selected_test_set = {str(current) for current in payload["tests"]}
    payload["imports"] = [
        {
            **dict(entry),
            "imports": list(dict(entry).get("imports", []))[:normalized_max_symbols],
        }
        for entry in payload.get("imports", [])
        if isinstance(entry, dict) and str(entry.get("file", "")) in selected_file_set
    ][:normalized_max_files]
    payload["related_paths"] = [
        str(path)
        for path in payload.get("related_paths", [])
        if str(path) in selected_file_set | selected_test_set
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload["max_sources"] = normalized_max_sources
    payload["max_tokens"] = normalized_max_tokens
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=query,
        max_files=normalized_max_files,
        max_symbols=min(normalized_max_symbols, normalized_max_sources),
        semantic_provider=semantic_provider,
        # #642 gate nit-1 fast-follow: this call dropped deadline_monotonic entirely (distinct from
        # the SECOND-validation-plan-chain gap the #642 gate itself flagged) -- `tg edit-plan` never
        # threaded a deadline into the edit-plan-seed's validation-test discovery or validation-plan
        # chain at all, independent of the return-time backstop below. build_context_render_from_
        # map's own call to this same helper already passes deadline_monotonic; mirror it here.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
        # audit B9/A18: `tg edit-plan --json --max-files N` wired `max_edits=max_files` into
        # `_suggested_edits_from_related_spans` but the callee never read it, so `suggested_edits`
        # grew unbounded regardless of `--max-files`. This is the ONE opt-in call site for the new
        # `suggested_edits_max` bound -- edit-plan's own top-level builder, never shared with
        # context-render or blast-radius-plan/render (see `_build_edit_plan_seed`'s call comment).
        suggested_edits_max=normalized_max_files,
    )
    payload["validation_commands"] = _top_level_validation_commands(payload)
    payload["suggested_validation_commands"] = _top_level_suggested_validation_commands(payload)
    # Parity fix (v1.71.1 dogfood): `tg agent --json` already surfaces a top-level structured
    # `validation_plan`; edit-plan only had the flat `validation_commands` above even though the
    # structured steps already exist at `edit_plan_seed.validation_plan`. Purely additive -- does
    # not change `validation_commands`/`suggested_validation_commands` above.
    payload["validation_plan"] = _top_level_validation_plan(payload)
    # Parity fix (CEO v1.72.1 dogfood): `tg agent --json` already surfaces a top-level `confidence`
    # (float-bearing object) and `ask_user_before_editing` gate; edit-plan had neither (`confidence`
    # read as `null`, `ask_user_before_editing` was absent). Purely additive -- see
    # `_edit_plan_confidence_and_ask`'s docstring for exactly what is/isn't reused from agent.
    payload["confidence"], payload["ask_user_before_editing"] = _edit_plan_confidence_and_ask(
        payload,
        query=query,
    )
    # #642 gate nit-1 fast-follow: mirrors build_context_render_from_map's own return-time
    # catch-all (added by this same PR) verbatim -- this function is `tg edit-plan`'s OWN builder
    # (build_context_edit_plan -> build_context_edit_plan_from_map), a sibling of build_context_
    # render_from_map, not a caller of it, so it needs its own independent recheck.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )
    if payload.get("partial") or deadline_exceeded_at_return:
        payload["partial"] = True
        payload["partial_reason"] = "deadline"
        existing_deadline_limit = payload.get("deadline_limit")
        payload["deadline_limit"] = (
            dict(existing_deadline_limit)
            if isinstance(existing_deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
    return _attach_profiling(payload, collector)


def build_context_edit_plan_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_symbols: int = 5,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        build_context_edit_plan(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_symbols=max_symbols,
            max_sources=max_sources,
            max_tokens=max_tokens,
            semantic_provider=semantic_provider,
            profile=profile,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )


def build_context_render(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    include_edit_plan_seed: bool = True,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    semantic_provider: str = "native",
    profile: bool = False,
    _profiling_collector: _ProfileCollector | None = None,
    ignore: tuple[str, ...] = (),
    include_suggested_scope: bool = False,
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline, used AS-IS instead of being recomputed from
    ``deadline_seconds`` when supplied. The CLI cold path (``main.context_render``) anchors it at
    command entry, before path resolution and the daemon gate, so front-door time is budgeted the
    same way scan time already is. Existing ``deadline_seconds``-only callers are unaffected: the
    fallback computation below is byte-identical to the prior behavior."""
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on
    # `tg context-render`/`tg agent` (Click "No such option" exit-2). Converted ONCE (moat P0-6
    # step-3 pattern) and shared across the repo-map build AND the render's own symbol-scoring pass.
    if deadline_monotonic is None:
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    if ignore:
        # Local import avoids a module-level circular import (orient_capsule imports this module);
        # reuses orient's tested glob filter (`tg orient --ignore`, PR #392) so `tg agent --ignore`
        # drops the same vendor/skill CODE trees from ranking before centrality/context-pack scoring.
        from tensor_grep.cli.orient_capsule import _apply_ignore_globs

        repo_map = _apply_ignore_globs(repo_map, ignore)
    render = build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        include_edit_plan_seed=include_edit_plan_seed,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        semantic_provider=semantic_provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    if include_suggested_scope:
        # suggested_scope (audit #93 SUB-2 / #133 dogfood): the SAME centrality-weighted directory
        # rollup `tg orient` emits, computed from the raw map we ALREADY built above -- no second
        # scan. Gated on the map's OWN `scan_limit.possibly_truncated` (a complete scan has nothing
        # left to narrow), mirroring orient_capsule's gate. Reuses orient's tested helper; the local
        # import avoids the module-level cycle (orient_capsule imports this module), exactly like the
        # `_apply_ignore_globs` reuse above. Additive + conditional: absent unless the scan was
        # truncated AND a clear winner exists, so a non-truncated render stays byte-identical.
        #
        # #179: also thread in the SAME auto-detected vendor/skill/tool-config tree set `tg orient`
        # (#168/#606) and `tg agent` (agent_capsule.py's own #179 fix) exclude from their own
        # suggested_scope rollup -- otherwise this wrapper (behind `tg context-render`, and any other
        # `include_suggested_scope=True` caller) could point an agent at a tree the sibling commands
        # already know to avoid, on the exact same repo map.
        scan_limit = repo_map.get("scan_limit")
        if isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated"):
            from tensor_grep.cli.orient_capsule import (
                _detect_vendored_subtrees,
                _suggested_scope_from_map,
            )

            deweighted_trees = _detect_vendored_subtrees(
                repo_map, deadline_monotonic=deadline_monotonic
            )
            suggested_scope = _suggested_scope_from_map(
                repo_map,
                deweighted_trees=deweighted_trees,
                deadline_monotonic=deadline_monotonic,
            )
            if suggested_scope is not None:
                render["suggested_scope"] = suggested_scope
    return render


def _fallback_file_source(
    path: Path, *, max_lines: int = 120, max_chars: int = 6_000
) -> dict[str, Any] | None:
    source_suffixes = _FALLBACK_SOURCE_SUFFIXES | _JS_TS_SUFFIXES | _RUST_SUFFIXES | {".py"}
    if path.suffix.lower() not in source_suffixes:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    if not lines:
        return None
    snippet_lines: list[str] = []
    rendered_chars = 0
    for line in lines[:max_lines]:
        rendered_chars += len(line) + 1
        if rendered_chars > max_chars:
            break
        snippet_lines.append(line)
    if not snippet_lines:
        return None
    source = "\n".join(snippet_lines)
    if not source.endswith("\n"):
        source = f"{source}\n"
    return {
        "name": path.name,
        "kind": "file",
        "file": str(path),
        "start_line": 1,
        "end_line": len(snippet_lines),
        "source": source,
    }


_COMPACT_CONTEXT_RENDER_PROFILES = {"compact", "llm"}
_COMPACT_CONTEXT_RENDER_OMITTED_KEYS = ("symbols", "imports", "related_paths")
_LLM_CONTEXT_RENDER_OMITTED_KEYS = (
    "candidate_edit_targets",
    "coverage",
    "coverage_summary",
    "file_matches",
    "file_summaries",
    "graph_trust_summary",
    "edit_order",
    "plan",
    "primary_target",
    "test_matches",
)


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(current) for current in value if isinstance(current, dict)]


def _list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    strings: list[str] = []
    for current in value:
        if current is None:
            continue
        text = str(current)
        if text:
            strings.append(text)
    return strings


def _compact_symbol_record(symbol: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(symbol, dict):
        return None
    keys = (
        "name",
        "kind",
        "file",
        "line",
        "start_line",
        "end_line",
        "score",
        "provenance",
    )
    return {key: symbol[key] for key in keys if key in symbol}


def _compact_span_record(span: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "file",
        "symbol",
        "start_line",
        "end_line",
        "depth",
        "score",
        "provenance",
        "rationale",
    )
    return {key: span[key] for key in keys if key in span}


def _compact_suggested_edit(edit: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "file",
        "symbol",
        "start_line",
        "end_line",
        "edit_kind",
        "confidence",
        "provenance",
        "rationale",
    )
    return {key: edit[key] for key in keys if key in edit}


def _compact_edit_plan_seed(seed: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(seed, dict):
        return {}
    compact = dict(seed)
    ordering_limit = max(1, max_files) + 1
    validation_limit = max(1, max_files) + 2
    compact["primary_symbol"] = _compact_symbol_record(
        seed.get("primary_symbol") if isinstance(seed.get("primary_symbol"), dict) else None
    )
    compact["validation_tests"] = _list_of_strings(seed.get("validation_tests"))[:max_files]
    compact["validation_commands"] = _list_of_strings(seed.get("validation_commands"))[
        :validation_limit
    ]
    compact["dependent_files"] = _list_of_strings(seed.get("dependent_files"))[:max_files]
    compact["edit_ordering"] = _list_of_strings(seed.get("edit_ordering"))[:ordering_limit]
    compact["related_spans"] = [
        _compact_span_record(current) for current in _list_of_dicts(seed.get("related_spans"))
    ][:max_files]
    compact["suggested_edits"] = [
        _compact_suggested_edit(current) for current in _list_of_dicts(seed.get("suggested_edits"))
    ][:max_files]
    compact["validation_plan"] = _list_of_dicts(seed.get("validation_plan"))[
        : max(1, max_files) + 2
    ]
    return compact


def _compact_navigation_group(group: dict[str, Any], *, max_files: int) -> dict[str, Any]:
    compact = dict(group)
    compact["mentions"] = _list_of_strings(group.get("mentions"))[:max_files]
    compact["files"] = _list_of_strings(group.get("files"))[:max_files]
    compact["roles"] = _list_of_strings(group.get("roles"))[:max_files]
    return compact


def _compact_navigation_pack(pack: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(pack, dict):
        return {}
    compact = dict(pack)
    ordering_limit = max(1, max_files) + 1
    validation_limit = max(1, max_files) + 2
    compact["follow_up_reads"] = _list_of_dicts(pack.get("follow_up_reads"))[:max_files]
    compact["parallel_read_groups"] = [
        _compact_navigation_group(current, max_files=max_files)
        for current in _list_of_dicts(pack.get("parallel_read_groups"))
    ][: max(1, max_files) + 1]
    compact["related_tests"] = _list_of_strings(pack.get("related_tests"))[:max_files]
    compact["validation_commands"] = _list_of_strings(pack.get("validation_commands"))[
        :validation_limit
    ]
    compact["edit_ordering"] = _list_of_strings(pack.get("edit_ordering"))[:ordering_limit]
    return compact


def _compact_candidate_edit_targets(targets: object, *, max_files: int) -> dict[str, Any]:
    if not isinstance(targets, dict):
        return {}
    compact = dict(targets)
    compact["files"] = _list_of_strings(targets.get("files"))[:max_files]
    compact["symbols"] = [
        current
        for current in (
            _compact_symbol_record(symbol) for symbol in _list_of_dicts(targets.get("symbols"))
        )
        if current is not None
    ][:max_files]
    compact["tests"] = _list_of_strings(targets.get("tests"))[:max_files]
    compact["spans"] = [
        _compact_span_record(current) for current in _list_of_dicts(targets.get("spans"))
    ][:max_files]
    return compact


def _compact_context_sources(sources: object, *, max_sources: int) -> list[dict[str, Any]]:
    compact_sources: list[dict[str, Any]] = []
    for source in _list_of_dicts(sources)[:max_sources]:
        compact_sources.append({key: value for key, value in source.items() if key != "source"})
    return compact_sources


def _compact_context_sections(sections: object) -> list[dict[str, Any]]:
    compact_sections: list[dict[str, Any]] = []
    for section in _list_of_dicts(sections):
        keys = ("kind", "start", "end", "token_estimate", "path", "symbol")
        compact_sections.append({key: section[key] for key in keys if key in section})
    return compact_sections


def _top_level_validation_commands(payload: dict[str, Any]) -> list[str]:
    navigation_pack = payload.get("navigation_pack")
    navigation_commands = (
        navigation_pack.get("validation_commands", []) if isinstance(navigation_pack, dict) else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_commands = (
        edit_plan_seed.get("validation_commands", []) if isinstance(edit_plan_seed, dict) else []
    )
    return _list_of_strings(navigation_commands or seed_commands)


def _top_level_suggested_validation_commands(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Additive counterpart to `_top_level_validation_commands` — surfaces the unverified
    test-neighbor-heuristic suggestion. NEVER read by trust/confidence/tie logic."""
    navigation_pack = payload.get("navigation_pack")
    navigation_suggested = (
        navigation_pack.get("suggested_validation_commands", [])
        if isinstance(navigation_pack, dict)
        else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_suggested = (
        edit_plan_seed.get("suggested_validation_commands", [])
        if isinstance(edit_plan_seed, dict)
        else []
    )
    source = navigation_suggested or seed_suggested
    return [dict(current) for current in source if isinstance(current, dict)]


def _top_level_validation_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Additive parity counterpart to `_top_level_validation_commands` -- surfaces the existing
    STRUCTURED `validation_plan` (list of step dicts with `command`/`confidence`/`detection`/
    `runner`/`scope`/optional `target`) at the payload TOP LEVEL, matching how `tg agent --json`
    already exposes a top-level `validation_plan` (audit: v1.71.1 dogfood gap). Same dual-source
    precedence as `_top_level_validation_commands` (navigation_pack first, edit_plan_seed
    fallback); each step dict is copied so a caller mutating the returned list never mutates
    `edit_plan_seed`/`navigation_pack` in place. Never removes or mutates `validation_commands`."""
    navigation_pack = payload.get("navigation_pack")
    navigation_plan = (
        navigation_pack.get("validation_plan", []) if isinstance(navigation_pack, dict) else []
    )
    edit_plan_seed = payload.get("edit_plan_seed")
    seed_plan = (
        edit_plan_seed.get("validation_plan", []) if isinstance(edit_plan_seed, dict) else []
    )
    source = navigation_plan or seed_plan
    return [dict(current) for current in source if isinstance(current, dict)]


def _edit_plan_confidence_and_ask(
    payload: dict[str, Any],
    *,
    query: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parity fix (CEO v1.72.1 dogfood): `tg edit-plan --json` had no top-level `confidence`/
    `ask_user_before_editing` even though `tg agent --json` already surfaces both (the same class
    of gap `_top_level_validation_plan` above closed for `validation_plan`). Delegates to
    `agent_capsule._capsule_confidence_and_ask_without_render`, which reproduces agent's confidence
    + ambiguity ladder (INCLUDING the tie/marker-helper ask-gate -- Opus-gate safety MUST-FIX on
    c63f509) against this exact edit-plan payload, using agent's own helpers. A DEFERRED import,
    since `agent_capsule` imports this module at load time (`from tensor_grep.cli import repo_map`)
    and a module-level import here would cycle; same precedent as this module's existing local
    `orient_capsule` imports a few hundred lines up. See that helper's docstring for exactly which
    genuinely snippet-/call-site-/LSP-evidence-gated enrichments stay agent-only (and why)."""
    from tensor_grep.cli import agent_capsule

    return agent_capsule._capsule_confidence_and_ask_without_render(payload, query=query)


def _compact_context_render_payload(
    payload: dict[str, Any],
    *,
    render_profile: str,
    max_files: int,
    max_sources: int,
) -> dict[str, Any]:
    if render_profile not in _COMPACT_CONTEXT_RENDER_PROFILES:
        return payload

    compact = dict(payload)
    omitted_keys: list[str] = []
    for key in _COMPACT_CONTEXT_RENDER_OMITTED_KEYS:
        if key in compact:
            compact.pop(key)
            omitted_keys.append(key)
    if render_profile == "llm":
        for key in _LLM_CONTEXT_RENDER_OMITTED_KEYS:
            if key in compact:
                compact.pop(key)
                omitted_keys.append(key)

    compact["tests"] = list(compact.get("tests", []))[:max_files]
    compact["sources"] = _compact_context_sources(
        compact.get("sources"),
        max_sources=max_sources,
    )
    compact["sections"] = _compact_context_sections(compact.get("sections"))
    compact["edit_plan_seed"] = _compact_edit_plan_seed(
        compact.get("edit_plan_seed"),
        max_files=max_files,
    )
    compact["navigation_pack"] = _compact_navigation_pack(
        compact.get("navigation_pack"),
        max_files=max_files,
    )
    if "candidate_edit_targets" in compact:
        compact["candidate_edit_targets"] = _compact_candidate_edit_targets(
            compact.get("candidate_edit_targets"),
            max_files=max_files,
        )
    compact["validation_commands"] = _top_level_validation_commands(compact)
    compact["suggested_validation_commands"] = _top_level_suggested_validation_commands(compact)
    compact["context_payload_profile"] = f"{render_profile}-compact"
    compact["payload_compaction"] = {
        "omitted_keys": omitted_keys,
        "raw_source_omitted": True,
        "source_diagnostics_omitted": False,
        "agent_metadata_compacted": True,
        "max_files": max_files,
        "max_sources": max_sources,
    }
    return compact


def _downgrade_ranking_quality(value: str) -> str:
    if value == "strong":
        return "moderate"
    if value == "moderate":
        return "weak"
    return "weak"


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _span_bounds(span: object) -> tuple[int, int] | None:
    if not isinstance(span, dict):
        return None
    start = _int_or_none(span.get("start_line") or span.get("line"))
    end = _int_or_none(span.get("end_line") or start)
    if start is None:
        return None
    if end is None:
        end = start
    return start, max(start, end)


def _line_map_overlaps_span(line_map: object, span: object) -> bool:
    bounds = _span_bounds(span)
    if bounds is None:
        return False
    span_start, span_end = bounds
    for row in _list_of_dicts(line_map):
        row_start = _int_or_none(
            row.get("original_start_line")
            or row.get("original_line")
            or row.get("line")
            or row.get("start_line")
        )
        if row_start is None:
            continue
        row_end = _int_or_none(row.get("original_end_line") or row.get("end_line")) or row_start
        row_end = max(row_start, row_end)
        if row_start <= span_end and row_end >= span_start:
            return True
    return False


def _line_map_covers_span(line_map: object, span: object) -> bool:
    bounds = _span_bounds(span)
    if bounds is None:
        return False
    span_start, span_end = bounds
    next_required = span_start
    rows: list[tuple[int, int]] = []
    for row in _list_of_dicts(line_map):
        row_start = _int_or_none(
            row.get("original_start_line")
            or row.get("original_line")
            or row.get("line")
            or row.get("start_line")
        )
        if row_start is None:
            continue
        row_end = _int_or_none(row.get("original_end_line") or row.get("end_line")) or row_start
        rows.append((row_start, max(row_start, row_end)))
    for row_start, row_end in sorted(rows):
        if row_end < next_required:
            continue
        if row_start > next_required:
            return False
        if row_end >= span_end:
            return True
        next_required = row_end + 1
    return False


def _source_includes_primary_symbol(
    source: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(source.get("file", "") or "") != primary_file:
        return False
    source_symbol = str(source.get("name") or source.get("symbol") or "")
    if primary_symbol_name and source_symbol == primary_symbol_name:
        return True
    return _line_map_overlaps_span(source.get("line_map"), primary_span)


def _source_truncates_primary_symbol(
    source: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(source.get("file", "") or "") != primary_file:
        return False
    source_budget = source.get("source_budget")
    if not isinstance(source_budget, dict) or not bool(source_budget.get("truncated")):
        return False
    source_symbol = str(source.get("name") or source.get("symbol") or "")
    overlaps_primary_span = _line_map_overlaps_span(source.get("line_map"), primary_span)
    if primary_symbol_name and source_symbol != primary_symbol_name and not overlaps_primary_span:
        return False
    if _span_bounds(primary_span) is None:
        return True
    return not _line_map_covers_span(source.get("line_map"), primary_span)


def _section_includes_primary_symbol(
    section: dict[str, Any],
    *,
    primary_file: str,
    primary_symbol_name: str,
    primary_span: object,
) -> bool:
    if str(section.get("kind", "")) != "source":
        return False
    if str(section.get("path", "") or "") != primary_file:
        return False
    section_symbol = str(section.get("symbol") or "")
    if primary_symbol_name and section_symbol == primary_symbol_name:
        return True
    bounds = _span_bounds(primary_span)
    section_start = _int_or_none(section.get("original_start_line") or section.get("start_line"))
    section_end = _int_or_none(section.get("original_end_line") or section.get("end_line"))
    if bounds is None or section_start is None:
        return False
    if section_end is None:
        section_end = section_start
    span_start, span_end = bounds
    return section_start <= span_end and max(section_start, section_end) >= span_start


def _ensure_primary_source_in_sources(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    max_sources: int,
    render_profile: str,
    optimize_context: bool,
    _profiling_collector: _ProfileCollector | None = None,
) -> list[dict[str, Any]]:
    edit_plan_seed = payload.get("edit_plan_seed")
    if not isinstance(edit_plan_seed, dict):
        return sources
    primary_file = str(edit_plan_seed.get("primary_file") or "")
    primary_symbol = edit_plan_seed.get("primary_symbol")
    primary_symbol_name = (
        str(primary_symbol.get("name") or "") if isinstance(primary_symbol, dict) else ""
    )
    if not primary_file or not primary_symbol_name:
        return sources
    primary_span = edit_plan_seed.get("primary_span") or primary_symbol
    if any(
        _source_includes_primary_symbol(
            source,
            primary_file=primary_file,
            primary_symbol_name=primary_symbol_name,
            primary_span=primary_span,
        )
        for source in sources
    ):
        return sources

    primary_source: dict[str, Any] | None = None
    primary_source_payload = build_symbol_source_from_map(
        repo_map,
        primary_symbol_name,
        _profiling_collector=_profiling_collector,
    )
    for source in _list_of_dicts(primary_source_payload.get("sources")):
        if str(source.get("file", "") or "") != primary_file:
            continue
        primary_source = _render_source_block(
            source,
            render_profile=render_profile,
            optimize_context=optimize_context,
            _profiling_collector=_profiling_collector,
        )
        break
    if primary_source is None:
        return sources

    filtered_sources = [
        source for source in sources if str(source.get("file", "") or "") != primary_file
    ]
    return [primary_source, *filtered_sources][:max_sources]


def _apply_context_consistency_invariants(payload: dict[str, Any]) -> dict[str, Any]:
    edit_plan_seed = payload.get("edit_plan_seed")
    navigation_pack = payload.get("navigation_pack")
    primary_file = (
        str(edit_plan_seed.get("primary_file") or "") if isinstance(edit_plan_seed, dict) else ""
    )
    raw_primary_symbol = (
        edit_plan_seed.get("primary_symbol") if isinstance(edit_plan_seed, dict) else None
    )
    primary_symbol = dict(raw_primary_symbol) if isinstance(raw_primary_symbol, dict) else {}
    primary_symbol_name = str(primary_symbol.get("name") or "")
    primary_span = edit_plan_seed.get("primary_span") if isinstance(edit_plan_seed, dict) else {}
    raw_primary_target = (
        navigation_pack.get("primary_target") if isinstance(navigation_pack, dict) else None
    )
    primary_target = dict(raw_primary_target) if isinstance(raw_primary_target, dict) else {}
    navigation_primary_file = str(primary_target.get("file", "") or "")
    files = {str(current) for current in payload.get("files", []) if str(current)}
    source_files = {
        str(source.get("file", ""))
        for source in _list_of_dicts(payload.get("sources"))
        if str(source.get("file", ""))
    }
    follow_up_files = set()
    if isinstance(navigation_pack, dict):
        follow_up_files = {
            str(read.get("file", ""))
            for read in _list_of_dicts(navigation_pack.get("follow_up_reads"))
            if str(read.get("file", ""))
        }
    rendered_files = {
        str(section.get("path", ""))
        for section in _list_of_dicts(payload.get("sections"))
        if str(section.get("kind", "")) in {"summary", "source"} and str(section.get("path", ""))
    }
    primary_symbol_included = bool(
        not primary_file
        or not primary_symbol_name
        or any(
            _source_includes_primary_symbol(
                source,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for source in _list_of_dicts(payload.get("sources"))
        )
    )
    rendered_includes_primary_symbol = bool(
        not primary_file
        or not primary_symbol_name
        or any(
            _section_includes_primary_symbol(
                section,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for section in _list_of_dicts(payload.get("sections"))
        )
    )
    primary_symbol_truncated = bool(
        primary_file
        and primary_symbol_name
        and any(
            _source_truncates_primary_symbol(
                source,
                primary_file=primary_file,
                primary_symbol_name=primary_symbol_name,
                primary_span=primary_span,
            )
            for source in _list_of_dicts(payload.get("sources"))
        )
    )

    included = bool(primary_file and primary_file in (files | source_files | follow_up_files))
    render_matches_primary_target = bool(
        not primary_file or not navigation_primary_file or primary_file == navigation_primary_file
    )
    rendered_includes_primary = bool(not primary_file or primary_file in rendered_files)
    omitted_reason = None
    if primary_file and not included:
        omitted_reason = "primary file was outside the selected file/source/read budgets"
    elif primary_file and not rendered_includes_primary:
        omitted_reason = (
            "primary file metadata was selected but omitted from rendered_context budget"
        )
    elif primary_file and primary_symbol_truncated:
        omitted_reason = "primary_symbol_truncated_by_source_budget"
    elif primary_file and not rendered_includes_primary_symbol:
        omitted_reason = "primary_symbol_omitted_from_rendered_context"

    confidence_downgraded = False
    if primary_file and (
        not render_matches_primary_target
        or not rendered_includes_primary
        or not rendered_includes_primary_symbol
        or primary_symbol_truncated
    ):
        ranking_quality = str(payload.get("ranking_quality", "weak"))
        downgraded_quality = _downgrade_ranking_quality(ranking_quality)
        if downgraded_quality != ranking_quality:
            payload["ranking_quality"] = downgraded_quality
        confidence_downgraded = True
        if isinstance(edit_plan_seed, dict):
            confidence = dict(edit_plan_seed.get("confidence", {}))
            for key, value in list(confidence.items()):
                try:
                    confidence[key] = round(float(value) * 0.75, 3)
                except (TypeError, ValueError):
                    continue
            edit_plan_seed["confidence"] = confidence

    validation_alignment = (
        dict(edit_plan_seed.get("validation_alignment", {}))
        if isinstance(edit_plan_seed, dict)
        and isinstance(edit_plan_seed.get("validation_alignment"), dict)
        else {}
    )
    validation_filtered_count = int(validation_alignment.get("filtered_count", 0) or 0)
    validation_kept_count = int(validation_alignment.get("kept_count", 0) or 0)
    if validation_filtered_count > 0 and validation_kept_count == 0:
        confidence_downgraded = True
    primary_target_language = _target_language_for_path(primary_file)

    payload["context_consistency"] = {
        "primary_file": primary_file or None,
        "navigation_primary_file": navigation_primary_file or None,
        "query_language_hints": _query_language_hints(str(payload.get("query", "") or "")),
        "primary_target_language": primary_target_language,
        "validation_alignment": validation_alignment,
        "validation_filtered_count": validation_filtered_count,
        "primary_file_included": included,
        "primary_symbol": primary_symbol_name or None,
        "primary_symbol_included": primary_symbol_included,
        "primary_symbol_truncated": primary_symbol_truncated,
        "render_matches_primary_target": render_matches_primary_target,
        "rendered_context_includes_primary": rendered_includes_primary,
        "rendered_context_includes_primary_symbol": rendered_includes_primary_symbol,
        "confidence_downgraded": confidence_downgraded,
        "omitted_primary_reason": omitted_reason,
    }
    if omitted_reason:
        omitted_sections = _list_of_dicts(payload.get("omitted_sections"))
        omitted_sections.append({
            "kind": "primary",
            # Emit JSON null (not the string "None") when no primary file
            # resolved, matching follow_up_reads[].file semantics.
            "file": primary_file or None,
            "reason": omitted_reason,
        })
        payload["omitted_sections"] = omitted_sections
    return payload


def build_context_render_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    include_edit_plan_seed: bool = True,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # #222 (call-2 enumeration-gap fix): `deadline_hit` is a NEW, additive, default-None
    # passthrough to `build_context_pack_from_map` -- every pre-existing caller (repo_map's own
    # `build_context_render`, session_store's warm-path renders) passes none and is byte-
    # identical to before. It lets a caller that already owns a `_DeadlineBreakFlag` (agent_
    # capsule's `build_agent_capsule_from_map`) observe "did ANY sibling stage INSIDE the pack
    # build (symbol-scoring, pagerank, or this render's own auto_deweight `_detect_vendored_
    # subtrees` call) cut short" -- previously unobservable here, so a truncation inside this
    # call only ever surfaced as the generic `payload["partial"]`, with nothing naming WHICH
    # assembly stage actually consumed the budget.
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    context_payload = build_context_pack_from_map(
        repo_map,
        query,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
        _profiling_collector=collector,
    )
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)
    top_files = {str(current) for current in context_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    seen_source_files: set[str] = set()
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in context_payload.get("symbols", []):
        symbols_by_file.setdefault(str(symbol["file"]), []).append(dict(symbol))
    ordered_symbols: list[dict[str, Any]] = []
    for current_file in context_payload.get("files", [])[:max_files]:
        ordered_symbols.extend(symbols_by_file.get(str(current_file), []))
    for symbol in ordered_symbols:
        current_file = str(symbol["file"])
        if current_file not in top_files:
            continue
        if current_file in seen_source_files:
            continue
        symbol_key = (current_file, str(symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = build_symbol_source_from_map(
            repo_map,
            str(symbol["name"]),
            _profiling_collector=collector,
        ).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            seen_source_files.add(current_file)
            break
        if len(sources) >= max_sources:
            break

    if len(sources) < max_sources:
        for current_file in context_payload.get("files", [])[:max_files]:
            current_file = str(current_file)
            if current_file in seen_source_files:
                continue
            fallback_source = _fallback_file_source(Path(current_file))
            if fallback_source is None:
                continue
            sources.append(
                _render_source_block(
                    fallback_source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            seen_source_files.add(current_file)
            if len(sources) >= max_sources:
                break

    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    source_budget: dict[str, Any] | None = None
    source_omitted_sections: list[dict[str, Any]] = []
    payload = dict(context_payload)
    payload["routing_reason"] = "context-render"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    selected_file_set = {str(current) for current in payload["files"]}
    payload["tests"] = list(payload.get("tests", []))[:max_files]
    selected_test_set = {str(current) for current in payload["tests"]}
    payload["test_matches"] = list(payload.get("test_matches", []))[:max_files]
    payload["symbols"] = [
        dict(symbol)
        for symbol in payload.get("symbols", [])
        if str(symbol.get("file", "")) in selected_file_set
    ][: max_files * max_symbols_per_file]
    payload["imports"] = [
        dict(entry)
        for entry in payload.get("imports", [])
        if str(entry.get("file", "")) in selected_file_set
    ][:max_files]
    payload["related_paths"] = [
        str(path)
        for path in payload.get("related_paths", [])
        if str(path) in selected_file_set | selected_test_set
    ]
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["max_tokens"] = normalized_max_tokens
    payload["model"] = model
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    if include_edit_plan_seed:
        payload = _attach_edit_plan_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
            max_depth=_DEFAULT_EDIT_PLAN_MAX_DEPTH,
            semantic_provider=semantic_provider,
            deadline_monotonic=deadline_monotonic,
            _profiling_collector=collector,
            # #212 (broader B9/#661 flag-lie): the "full" render profile (the default for text
            # output, and explicitly selectable for --json) has no downstream cap on suggested_edits
            # at all -- _compact_context_render_payload's _compact_edit_plan_seed truncation only
            # runs for render_profile in {"compact", "llm"} (see that function's own guard). Opting
            # into the SAME suggested_edits_max mechanism build_context_edit_plan_from_map already
            # uses closes the gap for "full" while being a provable no-op for "compact"/"llm" --
            # _compact_edit_plan_seed's OWN [:max_files] truncation downstream already reduces those
            # profiles to <=max_files, so bounding at the source produces the identical final list.
            suggested_edits_max=max_files,
        )
    else:
        payload = _attach_lightweight_navigation_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
            deadline_monotonic=deadline_monotonic,
        )
    payload["validation_commands"] = _top_level_validation_commands(payload)
    payload["suggested_validation_commands"] = _top_level_suggested_validation_commands(payload)
    sources = _ensure_primary_source_in_sources(
        repo_map,
        payload,
        sources,
        max_sources=max_sources,
        render_profile=normalized_profile,
        optimize_context=optimize_context,
        _profiling_collector=collector,
    )
    sources, source_budget, source_omitted_sections = _apply_source_output_budget(
        sources,
        max_tokens=normalized_max_tokens,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["sources"] = sources
    if source_budget is not None:
        payload["source_budget"] = source_budget
    (
        rendered_context,
        sections,
        truncated,
        token_estimate,
        omitted_sections,
    ) = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated or bool(source_omitted_sections)
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = [*source_omitted_sections, *omitted_sections]
    payload = _apply_context_consistency_invariants(payload)
    payload = _compact_context_render_payload(
        payload,
        render_profile=normalized_profile,
        max_files=max_files,
        max_sources=max_sources,
    )
    # #642 gate nit-1 fast-follow: #642 added this SAME final wall-clock catch-all to
    # build_agent_capsule_from_map (agent_capsule.py) ONLY -- a caller reaching THIS function
    # directly (the shared render core behind `tg context-render`, both the cold build_context_
    # render path and the warm session-daemon route in session_store.py) never got the return-time
    # recheck. `payload.get("partial")` already folds in every stage this function's own callees
    # thread a deadline flag through (build_context_pack_from_map's own_deadline_hit above, and --
    # as of this same fix -- the edit-plan-seed's validation-plan chain via
    # _attach_edit_plan_metadata); OR that with one absolute wall-clock recheck here so ANY sibling
    # stage this function calls -- instrumented or not -- can never silently return exit 0 once the
    # caller's budget is gone. Mirrors agent_capsule.py's `deadline_exceeded_at_return` verbatim.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )
    if payload.get("partial") or deadline_exceeded_at_return:
        payload["partial"] = True
        payload["partial_reason"] = "deadline"
        existing_deadline_limit = payload.get("deadline_limit")
        payload["deadline_limit"] = (
            dict(existing_deadline_limit)
            if isinstance(existing_deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
    return _attach_profiling(payload, collector)


def build_context_render_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    semantic_provider: str = "native",
    profile: bool = False,
    deadline_seconds: float | None = None,
) -> str:
    payload = build_context_render(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        semantic_provider=semantic_provider,
        deadline_seconds=deadline_seconds,
        profile=profile,
    )
    if payload.get("render_profile") == "llm":
        return json.dumps(payload, separators=(",", ":"))
    return json.dumps(payload, indent=2)


def build_context_pack_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    auto_deweight: bool = True,
    _test_source_limit: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _DeadlineBreakFlag | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(symbol) for symbol in repo_map.get("symbols", [])]
    payload["imports"] = [dict(entry) for entry in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    payload.pop("_profiling", None)
    # dogfood finding 1 (council must-fix #2, stamp the partial boolean DIRECTLY): every current
    # caller of this function (agent/context/edit-plan's own render/pack builders) passes NO
    # deadline_hit at all, so a sibling loop breaking early INSIDE _build_context_pack_from_map
    # (symbol-scoring, pagerank) was silently unstamped -- `_copy_partial_signal` can only
    # PROPAGATE an existing dict's `partial` key forward, it cannot originate one from a bare
    # _DeadlineBreakFlag. Always own a flag here (reuse the caller's if one WAS supplied, so a
    # caller folding this into its own wider N-way union -- mirroring the callers/impact/
    # blast-radius fold-in pattern -- still observes `.hit`).
    own_deadline_hit = deadline_hit if deadline_hit is not None else _DeadlineBreakFlag()
    payload = _build_context_pack_from_map(
        payload,
        query,
        auto_deweight=auto_deweight,
        _test_source_limit=_test_source_limit,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=own_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    if own_deadline_hit.hit:
        payload["partial"] = True
        # setdefault, not overwrite: a repo_map already partial from the SCAN stage (build_repo_
        # map's own --deadline cutoff, copied onto `payload` via `dict(repo_map)` above) carries a
        # richer deadline_limit (files_scanned/files_total) -- never clobber that with the generic
        # shape below just because a post-map sibling loop also happened to cross the same budget.
        payload.setdefault("deadline_limit", {"deadline_exceeded": True})
    return _attach_profiling(payload, _profiling_collector)


def _normalize_semantic_provider(provider: str) -> str:
    normalized = str(provider).strip().lower() or "native"
    if normalized not in {"native", "lsp", "hybrid"}:
        return "native"
    return normalized


def _language_for_path(path: str | Path) -> str:
    # PATH A Stage 0 honesty fix: this used to default unconditionally to "python" for ANY
    # suffix it didn't recognize (e.g. an lsp-provenance fallback label for a .go/.rb/.txt
    # file would be silently stamped "lsp-python") -- dishonest, and only reachable today via
    # the `_provider_language_for_path(...) or _language_for_path(...)` fallback chain for
    # suffixes _provider_language_for_path *also* doesn't recognize. Route through the
    # registry and fall back to "unknown" instead. javascript/typescript are intentionally
    # collapsed to the single "javascript" label here (unchanged from before) since callers of
    # this function have never distinguished the two.
    spec = lang_registry.spec_for_path(path)
    if spec is None:
        return "unknown"
    if spec.language_id in ("javascript", "typescript"):
        return "javascript"
    return spec.language_id


def _provider_language_for_path(path: str | Path) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript"
    if suffix in _TS_SUFFIXES:
        return "typescript"
    if suffix in _RUST_SUFFIXES:
        return "rust"
    if suffix == ".go":
        return "go"
    if suffix == ".java":
        return "java"
    if suffix == ".c":
        return "c"
    if suffix in {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
        return "cpp"
    if suffix == ".cs":
        return "csharp"
    if suffix == ".php":
        return "php"
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    if suffix == ".swift":
        return "swift"
    if suffix == ".lua":
        return "lua"
    return None


def _path_from_lsp_file_uri(uri: str) -> Path | None:
    if not uri.startswith("file://"):
        return None
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path).expanduser().resolve()


def _lsp_symbol_kind_name(value: object) -> str:
    mapping = {
        5: "class",
        12: "function",
        6: "method",
        2: "module",
        3: "namespace",
        13: "variable",
        14: "constant",
        10: "enum",
        23: "struct",
        11: "interface",
    }
    if isinstance(value, int):
        return mapping.get(value, "symbol")
    return "symbol"


def _symbol_character_in_file(path: Path, line_number: int, symbol: str) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return 0
    if line_number <= 0 or line_number > len(lines):
        return 0
    line = lines[line_number - 1]
    return max(0, line.find(symbol))


def _provider_languages_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definitions: list[dict[str, Any]] | None = None,
) -> list[str]:
    languages: set[str] = set()
    for current in definitions or []:
        language = _provider_language_for_path(str(current.get("file", "")))
        if language:
            languages.add(language)
    if languages:
        return sorted(languages)
    for current in repo_map.get("symbols", []):
        if str(current.get("name", "")) == symbol:
            language = _provider_language_for_path(str(current.get("file", "")))
            if language:
                languages.add(language)
    if languages:
        return sorted(languages)
    for current in repo_map.get("files", []):
        language = _provider_language_for_path(str(current))
        if language:
            languages.add(language)
    return sorted(languages)


def _provider_status_snapshot(
    repo_root: Path,
    *,
    semantic_provider: str,
    languages: list[str],
    fallback_used: bool = False,
) -> dict[str, Any]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    providers = [
        _EXTERNAL_LSP_PROVIDER_MANAGER.provider_status(language=language, workspace_root=repo_root)
        for language in languages
    ]
    return {
        "mode": normalized_provider,
        "fallback_used": bool(fallback_used),
        "providers": providers,
    }


def _configured_lsp_operation_budget_seconds() -> float:
    raw_value = os.environ.get(_LSP_OPERATION_BUDGET_ENV_VAR)
    if raw_value is None:
        return _DEFAULT_LSP_OPERATION_BUDGET_SECONDS
    try:
        return max(float(raw_value), 0.0)
    except (TypeError, ValueError):
        return _DEFAULT_LSP_OPERATION_BUDGET_SECONDS


def _wait_for_lsp_readiness(
    client: Any,
    deadline_monotonic: float,
    *,
    probe: Any = None,
    no_progress_grace_seconds: float = 1.0,
) -> None:
    """Best-effort P0-2 readiness gate. Tolerates clients without wait_until_ready (duck-typed
    fakes/third-party stubs) — the gate is a pre-step that improves first-query completeness;
    it must never be a new failure mode for the query itself."""
    waiter = getattr(client, "wait_until_ready", None)
    if waiter is None:
        return
    waiter(
        deadline_monotonic,
        probe=probe,
        no_progress_grace_seconds=no_progress_grace_seconds,
    )


def _lsp_operation_deadline() -> float:
    return time.monotonic() + _configured_lsp_operation_budget_seconds()


def _remaining_lsp_budget_seconds(deadline_monotonic: float) -> float:
    return max(0.0, deadline_monotonic - time.monotonic())


def _run_lsp_with_operation_budget(
    client: Any,
    deadline_monotonic: float,
    operation: Callable[[], Any],
) -> Any:
    remaining = _remaining_lsp_budget_seconds(deadline_monotonic)
    if remaining <= 0:
        raise LSPTransportError("LSP operation budget exhausted")
    original_request_timeout = getattr(client, "request_timeout_seconds", None)
    original_initialize_timeout = getattr(client, "initialize_timeout_seconds", None)
    try:
        if original_request_timeout is not None:
            client.request_timeout_seconds = min(float(original_request_timeout), remaining)
        if original_initialize_timeout is not None:
            client.initialize_timeout_seconds = min(float(original_initialize_timeout), remaining)
        return operation()
    finally:
        if original_request_timeout is not None:
            client.request_timeout_seconds = original_request_timeout
        if original_initialize_timeout is not None:
            client.initialize_timeout_seconds = original_initialize_timeout


def _attach_lsp_evidence_status(
    payload: dict[str, Any],
    *,
    semantic_provider: str,
    lsp_count: int,
    fallback_used: bool,
) -> None:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native":
        payload["lsp_evidence_status"] = "not_requested"
        payload["lsp_proof"] = False
        payload["not_lsp_proof_reason"] = "Semantic provider mode is native."
        return
    if lsp_count > 0:
        payload["lsp_evidence_status"] = "lsp_proof"
        payload["lsp_proof"] = True
        payload.pop("not_lsp_proof_reason", None)
        return
    if fallback_used:
        payload["lsp_evidence_status"] = "fallback_native"
        payload["lsp_proof"] = False
        payload["not_lsp_proof_reason"] = (
            "External LSP provider returned no usable evidence; native fallback was used."
        )
        return
    payload["lsp_evidence_status"] = "no_lsp_evidence"
    payload["lsp_proof"] = False
    payload["not_lsp_proof_reason"] = "External LSP provider returned no usable evidence."


def _is_lsp_proof_row(row: dict[str, Any]) -> bool:
    provenance = str(row.get("provenance", ""))
    return (
        row.get("lsp_provider_response") is True
        and provenance.startswith("lsp-")
        and "-fallback" not in provenance
    )


def _merge_navigation_duplicate(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    existing_is_lsp = _is_lsp_proof_row(existing)
    candidate_is_lsp = _is_lsp_proof_row(candidate)
    if candidate_is_lsp and not existing_is_lsp:
        merged = {**existing, **candidate}
    elif existing_is_lsp and not candidate_is_lsp:
        merged = {**candidate, **existing}
    else:
        merged = {**existing, **candidate}
    if _is_lsp_proof_row(merged):
        merged["lsp_proof"] = True
    return dict(merged)


def _lsp_proof_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _is_lsp_proof_row(row))


def _copy_lsp_evidence_status(payload: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("lsp_evidence_status", "lsp_proof", "not_lsp_proof_reason"):
        if key in source:
            payload[key] = source[key]


def _merge_agreement_status(
    *,
    semantic_provider: str,
    native_count: int,
    lsp_count: int,
    merged_count: int,
    fallback_used: bool,
) -> dict[str, Any]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native":
        agreement_status = "native-only"
    elif fallback_used and lsp_count == 0:
        agreement_status = "fallback-native"
    elif normalized_provider == "lsp":
        if lsp_count == 0:
            agreement_status = "native-fallback"
        elif native_count > lsp_count:
            # native found strictly MORE refs than the LSP index proved -> the LSP result is
            # incomplete. Report the divergence honestly instead of a clean lsp-only proof (P0-1:
            # dogfood's 2-of-14 partial was masked as authoritative lsp-only). "diverged" was
            # structurally unreachable in lsp mode before this. When native<=lsp (they agree, or LSP
            # proved everything native saw) it stays lsp-only.
            agreement_status = "diverged"
        else:
            agreement_status = "lsp-only"
    elif native_count > 0 and lsp_count > 0 and merged_count == native_count == lsp_count:
        agreement_status = "agreed"
    elif native_count > 0 and lsp_count > 0:
        agreement_status = "diverged"
    elif lsp_count > 0:
        agreement_status = "lsp-only"
    else:
        agreement_status = "native-only"
    return {
        "mode": normalized_provider,
        "agreement_status": agreement_status,
        "native_count": native_count,
        "lsp_count": lsp_count,
        "merged_count": merged_count,
        "fallback_used": fallback_used,
    }


def _default_provider_metadata(
    repo_root: Path,
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str,
    definitions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    return (
        _merge_agreement_status(
            semantic_provider=normalized_provider,
            native_count=len(definitions or []),
            lsp_count=0,
            merged_count=len(definitions or []),
            fallback_used=normalized_provider == "lsp",
        ),
        _provider_status_snapshot(
            repo_root,
            semantic_provider=normalized_provider,
            languages=_provider_languages_for_symbol(repo_map, symbol, definitions),
            fallback_used=normalized_provider == "lsp",
        ),
    )


def _external_workspace_symbols(
    repo_root: Path,
    symbol: str,
    *,
    repo_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    deadline_monotonic = _lsp_operation_deadline()
    current_repo_map = repo_map or build_repo_map(repo_root)
    languages = _provider_languages_for_symbol(current_repo_map, symbol)
    for language in sorted(languages):
        if _remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        try:
            client = _EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )

            # P0-2 (zero-grace variant): wait out an advertised indexing round; silent servers
            # proceed instantly.
            _wait_for_lsp_readiness(client, deadline_monotonic, no_progress_grace_seconds=0.0)

            def _request_symbols(
                *,
                current_client: Any = client,
                query: str = symbol,
            ) -> Any:
                return current_client.request("workspace/symbol", {"query": query})

            result = _run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_symbols,
            )
        except (FileNotFoundError, LSPTransportError, ValueError):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict) or str(current.get("name", "")) != symbol:
                continue
            location = current.get("location")
            if not isinstance(location, dict):
                continue
            payload_range = location.get("range")
            if not isinstance(payload_range, dict):
                continue
            payload_start = payload_range.get("start")
            payload_end = payload_range.get("end")
            if not isinstance(payload_start, dict) or not isinstance(payload_end, dict):
                continue
            uri = str(location.get("uri", ""))
            resolved_path = _path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            matches.append({
                "name": symbol,
                "kind": _lsp_symbol_kind_name(current.get("kind")),
                "file": str(resolved_path),
                "line": int(payload_start.get("line") or 0) + 1,
                "end_line": int(payload_end.get("line") or payload_start.get("line") or 0) + 1,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_operation": "workspace/symbol",
            })
            client.lsp_provider_response = True
    matches.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["kind"])))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for current in matches:
        key = (
            str(current["file"]),
            int(current["line"]),
            int(current["end_line"]),
            str(current["kind"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
    return deduped


def _iter_lsp_definition_locations(result: object) -> list[tuple[str, dict[str, Any]]]:
    raw_items: list[object]
    if isinstance(result, list):
        raw_items = result
    elif isinstance(result, dict):
        raw_items = [result]
    else:
        return []

    locations: list[tuple[str, dict[str, Any]]] = []
    for current in raw_items:
        if not isinstance(current, dict):
            continue
        if isinstance(current.get("targetUri"), str):
            uri = str(current["targetUri"])
            payload_range = current.get("targetSelectionRange") or current.get("targetRange")
        else:
            uri = str(current.get("uri", ""))
            payload_range = current.get("range")
        if not uri or not isinstance(payload_range, dict):
            continue
        start = payload_range.get("start")
        end = payload_range.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        locations.append((uri, payload_range))
    return locations


def _dedupe_lsp_definition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _dedupe_definition_rows(rows)


def _definition_file_display_path(file_value: Any) -> str:
    file_raw = str(file_value or "")
    if file_raw.startswith("file:"):
        parsed = urlparse(file_raw)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
            if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = f"//{parsed.netloc}{path}"
            file_raw = path

    try:
        return str(Path(file_raw).resolve())
    except (OSError, ValueError):
        return file_raw


def _definition_file_dedupe_key(file_value: Any) -> str:
    file_key = _definition_file_display_path(file_value)

    if os.name == "nt":
        file_key = file_key.lower().replace("/", "\\")
    return file_key


def _definition_dedupe_key(row: dict[str, Any]) -> tuple[str, int, str]:
    # LSP backends may emit file:// URIs or slash-normalized paths while the
    # native backend emits OS-native paths. Normalize before merging hybrid rows.
    file_key = _definition_file_dedupe_key(row.get("file"))
    line = int(row.get("line", row.get("start_line", 0)) or 0)
    return (file_key, line, str(row.get("name", "")))


def _merge_definition_duplicate(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    existing_is_lsp = _is_lsp_proof_row(existing)
    candidate_is_lsp = _is_lsp_proof_row(candidate)
    if candidate_is_lsp and not existing_is_lsp:
        merged = dict(existing)
        merged.update(candidate)
        for key in ("text", "source", "source_line"):
            if key in existing and key not in merged:
                merged[key] = existing[key]
        return merged
    if existing_is_lsp and not candidate_is_lsp:
        merged = dict(candidate)
        merged.update(existing)
        for key in ("text", "source", "source_line"):
            if key in candidate and key not in merged:
                merged[key] = candidate[key]
        return merged
    return dict(existing)


def _dedupe_definition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: dict[tuple[str, int, str], dict[str, Any]] = {}
    for current in rows:
        current = dict(current)
        current["file"] = _definition_file_display_path(current.get("file"))
        key = _definition_dedupe_key(current)
        if key in seen:
            seen[key] = _merge_definition_duplicate(seen[key], current)
            continue
        seen[key] = dict(current)
    deduped = list(seen.values())
    deduped.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["kind"])))
    return deduped


def _external_definitions(
    repo_root: Path,
    symbol: str,
    native_definitions: list[dict[str, Any]],
    *,
    repo_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    workspace_matches = _external_workspace_symbols(repo_root, symbol, repo_map=repo_map)
    if workspace_matches or not native_definitions:
        return workspace_matches

    definitions: list[dict[str, Any]] = []
    deadline_monotonic = _lsp_operation_deadline()
    for native_definition in native_definitions:
        if _remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        current_path = Path(str(native_definition.get("file", ""))).expanduser().resolve()
        if not current_path.exists():
            continue
        language = _provider_language_for_path(current_path) or _language_for_path(current_path)
        try:
            client = _EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            document_uri = current_path.as_uri()
            document_text = current_path.read_text(encoding="utf-8")
            definition_line = int(
                native_definition.get(
                    "line",
                    native_definition.get("start_line", 1),
                )
                or 1
            )
            definition_character = _symbol_character_in_file(
                current_path,
                definition_line,
                symbol,
            )

            def _ensure_document(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                text: str = document_text,
                language_id: str = language,
            ) -> None:
                current_client.ensure_document(uri=uri, text=text, language_id=language_id)

            _run_lsp_with_operation_budget(client, deadline_monotonic, _ensure_document)

            # P0-2 (zero-grace variant): progress-advertising servers wait for their indexing
            # round to end; silent servers proceed instantly (no latency tax on the 2s budget).
            _wait_for_lsp_readiness(client, deadline_monotonic, no_progress_grace_seconds=0.0)

            def _request_definition(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                line: int = definition_line,
                character: int = definition_character,
            ) -> Any:
                return current_client.request(
                    "textDocument/definition",
                    {
                        "textDocument": {"uri": uri},
                        "position": {
                            "line": line - 1,
                            "character": character,
                        },
                    },
                )

            result = _run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_definition,
            )
        except (FileNotFoundError, OSError, UnicodeDecodeError, LSPTransportError, ValueError):
            continue

        for uri, payload_range in _iter_lsp_definition_locations(result):
            resolved_path = _path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            start = dict(payload_range.get("start", {}))
            end = dict(payload_range.get("end", {}))
            line = int(start.get("line") or 0) + 1
            definitions.append({
                "name": symbol,
                "kind": str(native_definition.get("kind", "symbol")),
                "file": str(resolved_path),
                "line": line,
                "start_line": line,
                "end_line": int(end.get("line") or start.get("line") or 0) + 1,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_proof": True,
                "lsp_operation": "textDocument/definition",
                "lsp_resolution_basis": "native-definition-anchor",
            })
            client.lsp_provider_response = True

    return _dedupe_lsp_definition_rows(definitions) or workspace_matches


def _external_references(
    repo_root: Path, symbol: str, definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    deadline_monotonic = _lsp_operation_deadline()
    for definition in definitions:
        if _remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        current_path = Path(str(definition["file"])).resolve()
        language = _provider_language_for_path(current_path) or _language_for_path(current_path)
        try:
            client = _EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            document_uri = current_path.as_uri()
            document_text = current_path.read_text(encoding="utf-8")
            definition_line = int(definition.get("line", 1))
            definition_character = _symbol_character_in_file(current_path, definition_line, symbol)

            def _ensure_document(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                text: str = document_text,
                language_id: str = language,
            ) -> None:
                current_client.ensure_document(uri=uri, text=text, language_id=language_id)

            _run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _ensure_document,
            )

            # P0-2 readiness gate: wait for the server's workspace index to settle before the
            # references query — firing immediately answers from a half-built index (the 2-of-14
            # under-return). Probe = workspace/symbol hit-count stability for servers that never
            # emit workDoneProgress. A timeout here is honest-partial territory (the P0-1 union +
            # diverged stamps make the result truthful); it must NOT read as a provider failure.
            def _symbol_count_probe(
                *, current_client: Any = client, query: str = symbol
            ) -> int | None:
                hits = current_client.request("workspace/symbol", {"query": query})
                return len(hits) if isinstance(hits, list) else 0

            def _bounded_probe(
                *,
                current_client: Any = client,
                deadline: float = deadline_monotonic,
                probe_fn: Any = _symbol_count_probe,
            ) -> int | None:
                probed = _run_lsp_with_operation_budget(current_client, deadline, probe_fn)
                return probed if isinstance(probed, int) else None

            _wait_for_lsp_readiness(client, deadline_monotonic, probe=_bounded_probe)

            def _request_references(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                line: int = definition_line,
                character: int = definition_character,
            ) -> Any:
                return current_client.request(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": uri},
                        "position": {
                            "line": line - 1,
                            "character": character,
                        },
                        "context": {"includeDeclaration": True},
                    },
                )

            result = _run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_references,
            )
        except (FileNotFoundError, OSError, UnicodeDecodeError, LSPTransportError, ValueError):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict):
                continue
            location_range = current.get("range")
            if not isinstance(location_range, dict):
                continue
            start = location_range.get("start")
            end = location_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            uri = str(current.get("uri", ""))
            resolved_path = _path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            try:
                lines = resolved_path.read_text(encoding="utf-8").splitlines()
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                lines = []
            line_number = int(start.get("line", 0)) + 1
            text = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else symbol
            references.append({
                "name": symbol,
                "kind": "reference",
                "file": str(resolved_path),
                "line": line_number,
                "end_line": int(end.get("line") or start.get("line") or 0) + 1,
                "text": text,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_proof": True,
                "lsp_operation": "textDocument/references",
            })
            client.lsp_provider_response = True
    references.sort(key=lambda item: (str(item["file"]), int(item["line"])))
    deduped: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int, int]] = set()
    for current in references:
        key = (str(current["file"]), int(current["line"]), int(current["end_line"]))
        if key in seen_refs:
            continue
        seen_refs.add(key)
        deduped.append(current)
    return deduped


def _enclosing_class_for_definition(
    definition: dict[str, Any],
    all_symbols: list[dict[str, Any]],
) -> str | None:
    """Return the name of the innermost class that contains *definition*, or None.

    Looks through *all_symbols* for class-kind entries in the same file whose
    ``start_line``/``end_line`` span contains the definition's line.  When
    multiple classes nest (rare but possible), the one with the largest
    ``start_line`` (i.e. innermost) is returned.
    """
    def_file = str(definition.get("file", ""))
    def_line = int(definition.get("line", 0) or 0)
    best: dict[str, Any] | None = None
    for sym in all_symbols:
        if str(sym.get("kind", "")) != "class":
            continue
        if str(sym.get("file", "")) != def_file:
            continue
        sym_start = int(sym.get("start_line", sym.get("line", 0)) or 0)
        sym_end = int(sym.get("end_line", sym_start) or sym_start)
        if sym_start <= def_line <= sym_end:
            if best is None or sym_start > int(best.get("start_line", best.get("line", 0)) or 0):
                best = sym
    return str(best["name"]) if best is not None else None


def _definition_confidence_score(definition: dict[str, Any], symbol: str) -> float:
    """Return a [0.0, 1.0] confidence score for a definition entry.

    All definitions returned by ``build_symbol_defs_from_map`` already pass an
    exact-name filter, so this function starts at 1.0 and applies small
    downward adjustments for signals that indicate lower fidelity:

    * LSP-proof entries get a slight boost (capped at 1.0) — they have
      cross-validated provenance.
    * Heuristic / regex-backed provenance gets a small penalty.
    * The definition is in a test file (path contains "test") — mild penalty,
      as a matching symbol in test code is less likely to be the canonical def.
    """
    score = 1.0
    provenance = str(definition.get("provenance", "")).lower()
    if "lsp" in provenance and "fallback" not in provenance:
        score = min(1.0, score + 0.05)
    if "heuristic" in provenance or provenance == "regex-heuristic":
        score -= 0.1
    file_str = str(definition.get("file", "")).lower().replace("\\", "/")
    if "/test" in file_str or file_str.startswith("test"):
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 3)


def _apply_symbol_field_output_limit(
    payload: dict[str, Any],
    *,
    field_name: str,
    max_count: int | None,
) -> dict[str, Any]:
    """Cap ``payload[field_name]`` (a flat list) to ``max_count`` entries, stamping ``output_limit``.

    Generalizes ``_apply_blast_radius_output_limits``'s tests-cap + ``output_limit`` stamping
    (design #96 item 2) to any flat-list field -- giving defs/refs/callers/impact a DEDICATED
    ``--max-tests`` instead of blast-radius's conflated ``--max-files``, and leaving the helper
    ``field_name``-generic so a follow-up can reuse it for ``import_graph_consumers``.

    Deliberately field-NAME-scoped output_limit keys (``{field_name}_truncated``, e.g.
    ``tests_truncated`` -- never blast-radius's own ``callers_truncated``/``files_truncated``
    names, which ``main._scan_truncation_warning`` DOES recognize as a SCAN truncation). An
    output cap here is a COMPLETE analysis capped for display and must stay exit-0 (design #96
    contract-safety section; see ``main._scan_incomplete``'s docstring for the scan-vs-output-cap
    split this deliberately avoids colliding with).

    ``max_count=None`` is a no-op: the field and ``output_limit`` are left untouched, so an
    uncapped library/MCP caller sees byte-identical output to before this cap existed (mirrors
    ``_apply_context_token_budget``'s ``None``-is-unbounded contract).
    """
    if max_count is None:
        return payload
    normalized_max = max(0, int(max_count))
    original = list(payload.get(field_name) or [])
    capped_list = original[:normalized_max]
    payload[field_name] = capped_list
    output_limit = dict(payload.get("output_limit") or {})
    output_limit[f"max_{field_name}"] = normalized_max
    output_limit[f"{field_name}_truncated"] = len(capped_list) < len(original)
    output_limit[f"total_{field_name}"] = len(original)
    output_limit[f"returned_{field_name}"] = len(capped_list)
    output_limit[f"omitted_{field_name}"] = max(0, len(original) - len(capped_list))
    payload["output_limit"] = output_limit
    return payload


def build_symbol_defs(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_tests: int | None = None,
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg defs`
    # (Click "No such option" exit-2), unlike its true siblings build_symbol_refs/_callers/_impact.
    # build_repo_map already accepts deadline_monotonic, so this is the same thin thread-through
    # those wrappers use -- `_copy_partial_signal` below guarantees the signal reaches the top-level
    # output even though `build_symbol_defs_from_map`'s `dict(repo_map)` copy already carries it.
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    payload = build_repo_map(
        path, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic
    )
    # audit C1/C2: this call used to be BARE, dropping the deadline entirely --
    # build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol scan (repo_map.py:3812)
    # already accepts deadline_monotonic (#203), it just never received one from here, so a `tg
    # defs --deadline N` request ran that stage-1 scan fully unbounded (dogfood: `--deadline 40`
    # -> 113.5s, exit 0, partial:null). main.py's daemon gate skips the warm fast path whenever
    # --deadline is set, so THIS cold wrapper is the only path an explicit-deadline caller exercises.
    result = build_symbol_defs_from_map(
        payload,
        symbol,
        semantic_provider=semantic_provider,
        max_tests=max_tests,
        deadline_monotonic=deadline_monotonic,
    )
    _copy_partial_signal(result, payload)
    # C1 defense-in-depth: mirrors build_context_pack's #642-style return-time backstop
    # (repo_map.py:8380-8392) -- a final absolute wall-clock recheck so ANY stage inside
    # build_symbol_defs_from_map (instrumented or not) can never silently return exit 0 once the
    # caller's --deadline budget is gone, regardless of which internal loop actually consumed the
    # time. Does NOT set `partial_reason` -- that field is reserved for the
    # agent/context/context-render/edit-plan family (docs/CONTRACTS.md); the symbol commands'
    # existing convention (impact/refs/callers/blast-radius fold-ins above) is partial +
    # deadline_limit only.
    deadline_exceeded_at_return = (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )
    if result.get("partial") or deadline_exceeded_at_return:
        result["partial"] = True
        existing_deadline_limit = result.get("deadline_limit")
        result["deadline_limit"] = (
            dict(existing_deadline_limit)
            if isinstance(existing_deadline_limit, dict)
            else {"deadline_exceeded": True}
        )
    return result


def build_symbol_defs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    max_tests: int | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(current) for current in repo_map.get("symbols", [])]
    payload["imports"] = [dict(current) for current in repo_map.get("imports", [])]
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    native_definitions = [
        {
            **dict(current),
            "provenance": _symbol_navigation_provenance_for_path(str(current["file"])),
        }
        for current in payload["symbols"]
        if str(current["name"]) == symbol
    ]
    native_definitions.sort(
        key=lambda item: (
            str(item["file"]),
            int(item["line"]),
            str(item["kind"]),
            str(item["name"]),
        )
    )
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    definitions = [dict(current) for current in native_definitions]
    external_definitions: list[dict[str, Any]] = []
    fallback_used = False
    if normalized_provider != "native":
        external_definitions = _external_definitions(
            _repo_map_root_dir(repo_map),
            symbol,
            native_definitions,
            repo_map=repo_map,
        )
        if normalized_provider == "lsp":
            proof_definitions = [
                dict(current) for current in external_definitions if _is_lsp_proof_row(current)
            ]
            fallback_used = not bool(proof_definitions)
            definitions = proof_definitions or definitions
        else:
            definitions = _dedupe_definition_rows([*external_definitions, *definitions])

    # L3: Enrich each definition with `class` (enclosing class name or null)
    # and `score` (confidence signal).  These are additive fields — existing
    # keys are not renamed or removed.
    all_symbols_for_class_lookup = list(repo_map.get("symbols", []))
    for definition in definitions:
        if "class" not in definition:
            definition["class"] = _enclosing_class_for_definition(
                definition, all_symbols_for_class_lookup
            )
        if "score" not in definition:
            definition["score"] = _definition_confidence_score(definition, symbol)

    definition_files = [str(current["file"]) for current in definitions]
    # Root-cause fix (audit #96): defs used to shallow-copy the WHOLE-REPO test manifest into
    # `tests` (every _is_test_file up to the repo-map scan cap), regardless of relevance -- the
    # "69KB for a 1-symbol answer" bug. Route through the SAME relevance filter callers/impact
    # already use so defs stops dumping the manifest, then cap it BEFORE `related_paths` derives
    # below (a leak-back through the second field is the same bug one layer down).
    # task #203: thread the (now-optional) deadline into this sibling test-relevance scan --
    # _relevant_tests_for_symbol already supports deadline_monotonic/deadline_hit (used by
    # impact/callers' own fold-in), but defs never passed either, so a warm-daemon `defs` request
    # on a repo with many tests ran this loop fully unbounded even when a caller supplied a
    # deadline. Mirrors build_context_pack_from_map's own_deadline_hit pattern (repo_map.py:13744).
    defs_related_tests_deadline_hit = _DeadlineBreakFlag()
    payload["tests"] = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=defs_related_tests_deadline_hit,
    )
    _apply_symbol_field_output_limit(payload, field_name="tests", max_count=max_tests)
    if defs_related_tests_deadline_hit.hit:
        payload["partial"] = True
        payload.setdefault("deadline_limit", {"deadline_exceeded": True})
    related_paths = []
    for current in [*definition_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-defs"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["files"] = sorted(dict.fromkeys(definition_files))
    definition_file_set = set(payload["files"])
    compact_symbols = [
        current
        for current in payload["symbols"]
        if str(current.get("name", "")) == symbol
        and (not definition_file_set or str(current.get("file", "")) in definition_file_set)
    ]
    if not compact_symbols and definitions:
        compact_symbols = [dict(current) for current in definitions]
    payload["symbols"] = _dedupe_symbol_records(compact_symbols)
    payload["imports"] = [
        current
        for current in payload["imports"]
        if str(current.get("file", "")) in definition_file_set
    ]
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "strong"
    payload["semantic_provider"] = normalized_provider
    for definition in definitions:
        if _is_lsp_proof_row(definition):
            definition["lsp_proof"] = True
    lsp_proof_count = _lsp_proof_row_count(definitions)
    if normalized_provider != "native" and lsp_proof_count == 0 and native_definitions:
        fallback_used = True
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=len(native_definitions),
        lsp_count=lsp_proof_count,
        merged_count=len(definitions),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        _repo_map_root_dir(repo_map),
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, definitions),
        fallback_used=fallback_used,
    )
    _attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    if not definitions:
        payload["no_match"] = True
        payload["message"] = f"No exact definition found for symbol {symbol!r}."
        scan_limit = payload.get("scan_limit")
        if isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated"):
            payload["message"] += (
                f" The broad scan stopped after {scan_limit.get('scanned_files')} files; "
                "narrow PATH or raise --max-repo-files."
            )
            _mark_result_incomplete(payload, remediation=_SCAN_LIMIT_TRUNCATED_REMEDIATION)
        # F13 fix: unlike refs/callers (which compute `resolution_gaps` over the files they
        # actually scan), defs' own no_match path used to return bare -- indistinguishable from
        # "the symbol genuinely does not exist" even when the real cause is a grammar-missing
        # fail-closed language (e.g. a Go-only symbol with tree_sitter_go not installed). Attach
        # the same honesty-floor gap here so a defs-only caller gets the hint too.
        gap_files, gap_tests = _repo_map_file_and_test_universe(repo_map)
        resolution_gaps = _language_coverage_gaps_for_universe([*gap_files, *gap_tests])
        payload["resolution_gaps"] = resolution_gaps
        if resolution_gaps:
            gap_hint = "; ".join(
                f"{int(gap['files_affected'])} {gap['language']} file(s): {gap['remediation']}"
                for gap in resolution_gaps
            )
            payload["message"] += f" Coverage gap detected: {gap_hint}"
        payload["files"] = []
        payload["symbols"] = []
        payload["imports"] = []
        payload["tests"] = []
        payload["related_paths"] = []
        payload["graph_completeness"] = "empty"
    return payload


def build_symbol_defs_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
) -> str:
    return json.dumps(
        build_symbol_defs(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline_seconds,
        ),
        indent=2,
    )


def build_symbol_source(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # CEO v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg source` (Click "No such
    # option" exit-2) even though its true sibling build_symbol_defs already had it -- same thin,
    # additive thread-through build_repo_map already accepts (mirrors #581's build_symbol_defs).
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # audit C1/C2: thread the same deadline into the _from_map core -- previously
    # dropped here even though build_symbol_source already computed it (dogfood: `tg source
    # --deadline` overran by 47.0s). build_symbol_source_from_map gains deadline_monotonic below
    # (site 6); this cold wrapper is site 7.
    result = build_symbol_source_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    _copy_partial_signal(result, repo_map)
    return result


def build_symbol_source_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: `deadline_monotonic` is a NEW parameter here (site 6) -- this
    # function previously had no deadline awareness at all, so its bare build_symbol_defs_from_map
    # call below silently ran the stage-1 related-tests scan unbounded regardless of any caller's
    # --deadline. Defaults to None -> byte-identical for every pre-existing call site that does not
    # pass it (mirrors the documented _iter_repo_files convention, repo_map.py:993-998).
    # `_copy_partial_signal(payload, defs_payload)` below already folds defs_payload's partial
    # signal into this function's own fresh envelope, so threading the deadline is the whole fix.
    defs_payload = build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    default_agreement, default_status = _default_provider_metadata(
        _repo_map_root_dir(repo_map),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    sources: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    with _profiling_phase(_profiling_collector, "source_extraction"):
        for definition in defs_payload["definitions"]:
            current_path = Path(str(definition["file"]))
            if str(current_path) in seen_files:
                continue
            seen_files.add(str(current_path))
            current_sources = _python_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix in _JS_TS_SUFFIXES:
                current_sources = _js_ts_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix in _RUST_SUFFIXES:
                current_sources = _rust_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix == ".go":
                current_sources = lang_go.go_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix in _JAVA_SUFFIXES:
                current_sources = _java_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix == ".php":
                current_sources = lang_php.php_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix == ".cs":
                current_sources = lang_csharp.csharp_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix == ".c":
                current_sources = lang_c.c_parser_symbol_sources(current_path, symbol)
            if not current_sources and current_path.suffix in _CPP_SUFFIXES:
                current_sources = lang_cpp.cpp_parser_symbol_sources(current_path, symbol)
            if not current_sources:
                current_sources = _regex_symbol_sources(current_path, symbol)
            sources.extend(current_sources)

    related_paths: list[str] = []
    for current in [*defs_payload["files"], *defs_payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-source"
    payload["symbol"] = symbol
    payload["definitions"] = defs_payload["definitions"]
    payload["files"] = defs_payload["files"]
    payload["symbols"] = defs_payload["symbols"]
    payload["imports"] = defs_payload["imports"]
    payload["tests"] = defs_payload["tests"]
    payload["related_paths"] = related_paths
    payload["sources"] = sources
    if defs_payload.get("no_match"):
        payload["no_match"] = True
        payload["message"] = str(defs_payload.get("message", "No exact definition found."))
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    _copy_lsp_evidence_status(payload, defs_payload)
    _copy_scan_limit(payload, defs_payload)
    _copy_partial_signal(payload, defs_payload)
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_source_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_source(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def build_symbol_impact(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # moat P0-6 step 3: convert the relative --deadline once to an ABSOLUTE monotonic timestamp so
    # the underlying repo scan can bound itself and return partial results.
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    payload = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = build_symbol_impact_from_map(
        payload,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
        _profiling_collector=_profiling_collector,
    )
    _copy_partial_signal(
        result, payload
    )  # guarantee the deadline signal reaches the top-level output
    return result


def build_symbol_impact_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg impact
    # --deadline 1` -> 85.4s). `_copy_partial_signal(payload, defs_payload)` below already folds
    # defs_payload's partial signal into this function's own return, so threading the deadline
    # here is the whole fix at this site.
    defs_payload = build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    default_agreement, default_status = _default_provider_metadata(
        _repo_map_root_dir(repo_map),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    if defs_payload.get("no_match"):
        payload = dict(defs_payload)
        payload["routing_reason"] = "symbol-impact"
        payload["preferred_command"] = "blast-radius"
        payload["preferred_command_reason"] = (
            "impact is a fast file-level planning signal; "
            "blast-radius adds caller_tree, blast_radius_score, and call-graph depth "
            "for precise change-impact analysis — use blast-radius when you need "
            "caller attribution or a scored propagation graph"
        )
        payload["trust_level"] = "planning-signal"
        payload["file_matches"] = []
        payload["file_summaries"] = []
        payload["test_matches"] = []
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _coverage_summary(payload)
        payload["provider_agreement"] = dict(default_agreement)
        payload["provider_status"] = dict(default_status)
        _copy_lsp_evidence_status(payload, defs_payload)
        return _attach_profiling(payload, _profiling_collector)
    # task #103 Fix 2: thread the shared deadline into context-pack's own symbol-scoring loop too
    # -- previously unbounded even when this same deadline_monotonic already gated the sibling
    # scans just below (the #52 fix (loop C) comment).
    context_pack_deadline_hit = _DeadlineBreakFlag()
    context_payload = build_context_pack_from_map(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop C): build_symbol_impact_from_map previously threaded NO deadline at all into
    # either of its two sibling scans below, even when a caller (build_symbol_impact,
    # build_symbol_blast_radius_from_map) already had one in scope -- both loops mirror the
    # equivalent callers-scan seams (task #61) which already guard the same helpers.
    preferred_definition_deadline_hit = _DeadlineBreakFlag()
    preferred_definition_files = _preferred_definition_files(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=preferred_definition_deadline_hit,
    )
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    unselected_definition_files = {
        str(current)
        for current in defs_payload["files"]
        if str(current) not in set(definition_files)
    }

    impacted_files: list[str] = []
    import_files = [str(entry["file"]) for entry in context_payload["imports"]]
    for current in [*definition_files, *context_payload["files"], *import_files]:
        if current in unselected_definition_files:
            continue
        if current not in impacted_files:
            impacted_files.append(current)

    related_tests_deadline_hit = _DeadlineBreakFlag()
    related_tests = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        fallback_tests=list(context_payload.get("tests", [])),
        deadline_monotonic=deadline_monotonic,
        deadline_hit=related_tests_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    # impact's own DEDICATED --max-tests (design #96 item 2): cap BEFORE related_paths/test_matches
    # derive below (both are indexed off `related_tests`), or the omitted tests leak back in via
    # the second field. `payload` does not exist yet at this point, so cap through a scratch dict.
    impact_tests_limit_scratch: dict[str, Any] = {"tests": related_tests}
    _apply_symbol_field_output_limit(
        impact_tests_limit_scratch, field_name="tests", max_count=max_tests
    )
    related_tests = cast(list[str], impact_tests_limit_scratch["tests"])
    impact_tests_output_limit = impact_tests_limit_scratch.get("output_limit")

    definition_file_set = set(definition_files)
    file_matches_by_path: dict[str, dict[str, Any]] = {}
    for item in context_payload.get("file_matches", []):
        item_path = str(item["path"])
        item_reasons = list(item["reasons"])
        if item_path not in definition_file_set:
            item_reasons = [
                reason for reason in item_reasons if reason not in {"definition", "symbol"}
            ]
        file_matches_by_path[item_path] = {
            "path": item_path,
            "score": int(item["score"]),
            "reasons": item_reasons,
            "provenance": _provenance_from_reasons(item_reasons),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
        }
    for current in definition_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "definition" not in entry["reasons"]:
            entry["reasons"].append("definition")
    for current in import_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "import" not in entry["reasons"]:
            entry["reasons"].append("import")

    test_matches_by_path: dict[str, dict[str, Any]] = {
        str(item["path"]): {
            "path": str(item["path"]),
            "score": int(item["score"]),
            "reasons": list(item["reasons"]),
            "provenance": list(item.get("provenance", [])),
            **({"association": dict(item["association"])} if "association" in item else {}),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
        }
        for item in context_payload.get("test_matches", [])
    }
    for current in related_tests:
        test_matches_by_path.setdefault(
            str(current),
            {
                "path": str(current),
                "score": 1,
                "reasons": ["test-graph"],
                "provenance": _provenance_from_reasons(["test-graph"]),
                "association": {
                    "edge_kind": "import-graph",
                    "confidence": "moderate",
                    "provenance": _provenance_from_reasons(["test-graph"]),
                },
            },
        )

    related_paths: list[str] = []
    for current in [*impacted_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-impact"
    payload["symbol"] = symbol
    payload["preferred_command"] = "blast-radius"
    payload["preferred_command_reason"] = (
        "impact is a fast file-level planning signal; "
        "blast-radius adds caller_tree, blast_radius_score, and call-graph depth "
        "for precise change-impact analysis — use blast-radius when you need "
        "caller attribution or a scored propagation graph"
    )
    payload["trust_level"] = "planning-signal"
    payload["definitions"] = definitions
    payload["files"] = impacted_files
    payload["file_matches"] = [file_matches_by_path[str(current)] for current in impacted_files]
    payload["file_summaries"] = _file_summaries(repo_map.get("symbols", []), impacted_files)
    payload["tests"] = related_tests
    payload["test_matches"] = [test_matches_by_path[str(current)] for current in related_tests]
    if impact_tests_output_limit is not None:
        payload["output_limit"] = impact_tests_output_limit
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _ranking_quality(payload["file_matches"], payload["test_matches"])
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    _copy_lsp_evidence_status(payload, defs_payload)
    # #52 fix (loop C): fold THIS function's own sibling-loop deadline signals (preferred-
    # definition scoring + related-test matching, both unbounded before this fix) into partial --
    # mirrors the callers/blast-radius fold-in pattern (task #61) so a --deadline-truncated impact
    # result is never silently reported as complete.
    # task #103 Fix 2: context_pack_deadline_hit joins the same fold-in -- the context-pack
    # symbol-scoring loop is a THIRD sibling scan sharing this deadline_monotonic.
    if (
        preferred_definition_deadline_hit.hit
        or related_tests_deadline_hit.hit
        or context_pack_deadline_hit.hit
    ):
        payload["partial"] = True
        payload["deadline_limit"] = {"deadline_exceeded": True}
    _copy_scan_limit(payload, defs_payload)
    _copy_partial_signal(payload, defs_payload)
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_impact_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_impact(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def _dedupe_symbol_references(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for current in references:
        line = int(current.get("line", 0) or 0)
        end_line = int(current.get("end_line", line) or line)
        key = (
            str(current.get("file", "")),
            line,
            end_line,
            str(current.get("name", "")),
            str(current.get("text", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(current))
    deduped.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", ""))))
    return deduped


def _classify_string_reference(line_text: str, match_start: int) -> str:
    """Classify a quoted-string occurrence of a symbol.

    Returns one of ``"decorator-arg"`` (inside a ``@deco(...)`` call on the same
    line), ``"fstring"`` (the literal is an f-string), or ``"string-literal"``
    (any other quoted occurrence, e.g. ``routing_backend="Foo"`` or an
    ``__all__`` entry). These are *not* AST references; they exist so that
    rename-aware agents can find ``@patch("module.Symbol")`` targets and string
    assignments that the precise AST pass intentionally excludes.
    """

    prefix = line_text[:match_start]
    stripped_prefix = prefix.lstrip()
    if stripped_prefix.startswith("@") and "(" in prefix:
        return "decorator-arg"
    quote_lead = prefix.rstrip()
    if quote_lead.endswith(("f'", 'f"', "rf'", 'rf"', "fr'", 'fr"', "f'''", 'f"""')):
        return "fstring"
    return "string-literal"


def _string_literal_references(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Find occurrences of ``symbol`` as a quoted string literal.

    The precise AST reference pass deliberately excludes string occurrences such
    as ``@patch("pkg.mod.Symbol")`` decorator arguments,
    ``routing_backend="Symbol"`` assignments, and ``__all__`` entries. Those are
    surfaced here so they are not silently dropped from rename planning.

    #52 fix (loop D): read via ``_read_source_text_cached`` instead of a raw uncached
    ``path.read_text`` -- bundles the SAME ``_SYMBOL_LITERAL_SEED_MAX_BYTES`` size guard its
    siblings (``_file_may_contain_literal_symbol`` et al.) already use: normal files are read
    once per (path, mtime, size) and shared across repeated calls in a session; oversize files
    still get read directly, uncached, exactly like before (no behavior change for them).
    """

    if not symbol:
        return []
    try:
        source = _read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return []

    # Match the symbol when it sits inside a single- or double-quoted string,
    # either standalone (``"Symbol"``) or as a dotted-path tail
    # (``"pkg.mod.Symbol"``). ``\b`` keeps it from matching ``SymbolExtra``.
    pattern = re.compile(
        rf"""(?P<quote>['"])(?:[A-Za-z0-9_.]*\.)?{re.escape(symbol)}\b(?:['"])""",
    )
    occurrences: list[dict[str, Any]] = []
    for match in pattern.finditer(source):
        line_no = source.count("\n", 0, match.start()) + 1
        line_start = source.rfind("\n", 0, match.start()) + 1
        line_end = source.find("\n", match.start())
        if line_end < 0:
            line_end = len(source)
        line_text = source[line_start:line_end]
        occurrence_kind = _classify_string_reference(line_text, match.start() - line_start)
        occurrences.append({
            "name": symbol,
            "kind": "string-reference",
            "occurrence": occurrence_kind,
            "file": str(path),
            "line": line_no,
            "text": line_text.strip(),
        })
    return occurrences


def build_symbol_refs(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
) -> dict[str, Any]:
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic
    )
    result = build_symbol_refs_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
    )
    _copy_partial_signal(result, repo_map)
    return result


def build_symbol_refs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    max_tests: int | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg refs
    # --deadline` overran by 47.4s). `payload` here IS the defs return value, mutated in place
    # for the rest of this function, so threading the deadline is the whole fix at this site --
    # defs_payload's own partial/deadline_limit (if set) survive untouched through every mutation
    # below since nothing resets those keys before the final return.
    payload = build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    if payload.get("no_match"):
        payload["routing_reason"] = "symbol-refs"
        payload["references"] = []
        payload["string_refs"] = []
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _coverage_summary(payload)
        payload["resolution_gaps"] = []
        return payload
    # #205: bound context-pack's own symbol-scoring + pagerank loop with the SAME warm-daemon
    # deadline. Previously called BARE here while the sibling handlers callers/impact threaded it
    # (repo_map.py:16431 / 15011); on a very large session repo this in-memory stage could still
    # overrun the 60s budget. Fold its early-break into the refs partial signal below.
    context_pack_deadline_hit = _DeadlineBreakFlag()
    context_payload = build_context_pack_from_map(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
    )
    repo_root = _repo_map_root_dir(repo_map)
    refs_universe_files, refs_universe_tests = _repo_map_file_and_test_universe(repo_map)
    bounded_files, refs_ceiling_hit = _cap_caller_scan_files(
        [*refs_universe_files, *refs_universe_tests],
        symbol=symbol,
        test_files=refs_universe_tests,
        deadline_monotonic=deadline_monotonic,
    )
    bounded_file_set = {str(current) for current in bounded_files}
    # F25 fix (Go alias-resolution confidence): the symbol's own known definition directories,
    # so a package-qualified Go call only earns high-confidence "go-import-resolution" when it
    # resolves to a package that actually OWNS this symbol -- not merely to a package alias that
    # happens to resolve to SOME directory (which used to give two unrelated same-named exports
    # identical fabricated confidence). Computed once, outside the per-file loop.
    go_definition_dirs = frozenset(
        str(Path(str(current_definition["file"])).resolve().parent)
        for current_definition in payload.get("definitions", [])
    )
    references: list[dict[str, Any]] = []
    refs_scan_deadline_hit = False
    refs_files_scanned = 0
    for current in bounded_files:
        # moat P0-6 step 6: bound the reference-scan traversal so a CENTRAL symbol honors --deadline
        # instead of hanging past it (1.35.0 dogfood: `refs QueryEngine --deadline 15` -> 45s timeout,
        # no partial). Same per-file-scan hot loop as callers.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            refs_scan_deadline_hit = True
            break
        refs_files_scanned += 1
        current_provenance = _symbol_navigation_provenance_for_path(str(current))
        current_spec = lang_registry.spec_for_path(current)
        if current_spec is not None and current_spec.language_id == "python":
            current_refs, _ = _python_references_and_calls(current, symbol)
        elif current_spec is not None and current_spec.language_id in ("javascript", "typescript"):
            current_refs, current_calls = _js_ts_references_and_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_calls = _js_ts_provider_alias_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_refs, current_calls = _regex_references_and_calls(current, symbol)
            js_ts_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "ref_kind": str(call.get("ref_kind", "call")),
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                    "provenance": current_provenance,
                    **(
                        {
                            "resolution_provenance": list(call.get("resolution_provenance", [])),
                            "resolution_confidence": float(call.get("resolution_confidence", 0.95)),
                        }
                        if "resolution_provenance" in call
                        else {}
                    ),
                }
                for call in current_calls
            ]
            current_refs.extend(js_ts_call_refs)
        elif current_spec is not None and current_spec.language_id == "rust":
            current_refs, current_calls = _rust_references_and_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_calls = _rust_provider_alias_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_refs, current_calls = _regex_references_and_calls(current, symbol)
            rust_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "ref_kind": str(call.get("ref_kind", "call")),
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                    "provenance": current_provenance,
                    **(
                        {
                            "resolution_provenance": list(call.get("resolution_provenance", [])),
                            "resolution_confidence": float(call.get("resolution_confidence", 0.95)),
                        }
                        if "resolution_provenance" in call
                        else {}
                    ),
                }
                for call in current_calls
            ]
            current_refs.extend(rust_call_refs)
        elif current_spec is not None and current_spec.language_id == "go":
            # Fail-closed (Stage 1 trap): no regex/provider-alias fallback for Go -- a
            # grammar-missing file yields ([], []) here and is surfaced honestly via
            # `resolution_gaps` further down, never a silently-degraded text match.
            current_refs, current_calls = lang_go.go_references_and_calls(
                current, symbol, repo_root, definition_dirs=go_definition_dirs
            )
            go_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "ref_kind": str(call.get("ref_kind", "call")),
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                    "provenance": current_provenance,
                    **(
                        {
                            "resolution_provenance": list(call.get("resolution_provenance", [])),
                            "resolution_confidence": float(call.get("resolution_confidence", 0.95)),
                        }
                        if "resolution_provenance" in call
                        else {}
                    ),
                }
                for call in current_calls
            ]
            current_refs.extend(go_call_refs)
        else:
            current_refs, _ = _regex_references_and_calls(current, symbol)
        references.extend(
            {
                **dict(current_ref),
                "provenance": str(current_ref.get("provenance", current_provenance)),
            }
            for current_ref in current_refs
        )

    normalized_provider = _normalize_semantic_provider(semantic_provider)
    external_refs: list[dict[str, Any]] = []
    fallback_used = False
    # Native ref count captured BEFORE any lsp/hybrid merge -> the divergence signal for
    # provider_agreement (a partial LSP result must surface as diverged, not a clean lsp-only proof).
    native_reference_count = len(references)
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _external_references(
                repo_root, symbol, [dict(current) for current in payload["definitions"]]
            )
            if str(Path(str(current.get("file", ""))).expanduser().resolve()) in bounded_file_set
        ]
        # Merge (union) native + external refs for BOTH lsp and hybrid. A partial / under-indexed
        # LSP result must NEVER discard the correct native answer: the old `references = proof_refs
        # or references` REPLACED the native rows with the LSP rows, then recomputed native_count
        # from the replaced list -> always 0 -> falsely reported lsp-only / lsp_proof:True (dogfood
        # v1.20.0: `tg refs --provider lsp` returned 2 of 14, marked authoritative -- a silent
        # wrong-output / fail-closed-contract violation). Union keeps the native truth; the
        # provider_agreement + lsp_proof below are then stamped honestly (native>lsp -> diverged).
        proof_refs = [dict(current) for current in external_refs if _is_lsp_proof_row(current)]
        merged_refs: dict[tuple[str, int, int], dict[str, Any]] = {}
        for current_ref in [*external_refs, *references]:
            key = (
                str(current_ref["file"]),
                int(current_ref["line"]),
                int(current_ref.get("end_line", current_ref["line"])),
            )
            if key in merged_refs:
                merged_refs[key] = _merge_navigation_duplicate(merged_refs[key], dict(current_ref))
            else:
                merged_refs[key] = dict(current_ref)
        references = list(merged_refs.values())
        references.sort(
            key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
        )
        if normalized_provider == "lsp" and len(proof_refs) < native_reference_count:
            # LSP proved fewer refs than the native scan -> its index is incomplete for this symbol;
            # flag a fallback so the partial is not mis-reported as a clean lsp-only proof.
            fallback_used = True

    references = _dedupe_symbol_references(references)
    for reference in references:
        if _is_lsp_proof_row(reference):
            reference["lsp_proof"] = True
        # Additive T1 safety net: every code path above (native AST match, JS/TS-or-Rust call
        # flatten, regex/alias fallback, external/LSP merge) must leave a ref_kind on the row;
        # "value" is the least-specific label for paths (LSP/regex) that carry no AST context.
        reference.setdefault("ref_kind", "value")

    # Collect string-literal occurrences (decorator args, ``routing_backend=``
    # assignments, ``__all__`` entries, f-strings). These are reported alongside
    # the precise AST ``references`` so rename-aware agents do not miss them.
    string_refs: list[dict[str, Any]] = []
    for current in bounded_files:
        # #52 fix (loop D): this second pass over bounded_files ran AFTER the deadline-checked
        # main scan above with no bound of its own -- fold into the SAME refs_scan_deadline_hit
        # local the main loop already declares and the payload assembly below already reads.
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            refs_scan_deadline_hit = True
            break
        string_refs.extend(_string_literal_references(current, symbol))
    string_refs.sort(
        key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
    )

    referenced_files = sorted(dict.fromkeys(str(current["file"]) for current in references))
    # refs' own DEDICATED --max-tests (design #96 item 2), independent of defs' -- the nested
    # build_symbol_defs_from_map call above passes no max_tests, so `payload["tests"]` here is
    # still the full relevance-filtered (uncapped) list; cap it now, BEFORE related_paths derives
    # below, or the omitted tests leak back in via the second field.
    _apply_symbol_field_output_limit(payload, field_name="tests", max_count=max_tests)
    related_paths: list[str] = []
    for current in [*payload["files"], *referenced_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-refs"
    payload["references"] = references
    payload["string_refs"] = string_refs
    payload["files"] = referenced_files
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "moderate"
    payload["ranking_quality"] = _ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["resolution_gaps"] = _language_coverage_gaps_for_universe(bounded_files)
    payload["semantic_provider"] = normalized_provider
    lsp_proof_count = _lsp_proof_row_count(references)
    if normalized_provider != "native" and lsp_proof_count == 0 and references:
        fallback_used = True
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=native_reference_count,
        lsp_count=lsp_proof_count,
        merged_count=len(references),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, payload["definitions"]),
        fallback_used=fallback_used,
    )
    _attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    if refs_scan_deadline_hit or context_pack_deadline_hit.hit:
        payload["partial"] = True
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "reference_files_scanned": refs_files_scanned,
            "reference_files_total": len(bounded_files),
        }
    if refs_ceiling_hit:
        # backlog #1 chokepoint: the caller-scan ceiling dropped files the map otherwise covers
        # -> the reference set is not exhaustive, so mark it honestly incomplete (exit-2 contract).
        _mark_result_incomplete(
            payload,
            remediation=_CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": CALLER_SCAN_FILE_CEILING,
                "files_total": len(refs_universe_files) + len(refs_universe_tests),
            },
        )
    return payload


def build_symbol_refs_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_refs(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


# #74 moat: `tg imports FILE` / `tg importers FILE [ROOT]` -- the scoped file-dependency
# primitive. The benchmark that motivated this (docs/benchmarks.md P4) showed `tg` losing to
# plain grep ~10x on file-dependency lookups because the only existing primitive was the
# whole-repo `tg map` (~53K tokens on a mid-size repo); these two commands answer "what does
# this ONE file import" (O(1) parse, no scan) and "who imports this ONE file" (bounded reverse
# lookup) directly, at ~1-2K tokens.
_PROJECT_ROOT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "tsconfig.json")


def _infer_project_root(file_path: Path) -> Path:
    """Walk upward from a file looking for a project-root marker; falls back to its own dir.

    `tg imports FILE` takes no ROOT argument (by design -- it is meant to be a true O(1)
    single-file operation), so bare-specifier / tsconfig-alias / Rust-workspace resolution needs
    a plausible root inferred from the file's own location rather than passed explicitly.
    """
    current = file_path.parent
    for candidate in [current, *current.parents]:
        try:
            if any((candidate / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
                return candidate
        except OSError:
            continue
    return current


_SUPPORTED_FILE_DEPENDENCY_LANGUAGES = frozenset({
    "python",
    "javascript",
    "typescript",
    "rust",
    "java",
    # #74-follow-up (2026-07-23 completeness audit): go/php/csharp join at the SAME
    # foundational tier java landed at -- raw import statements + line numbers via
    # lang_go.go_imports_with_lines / lang_php.php_imports_with_lines /
    # lang_csharp.csharp_imports_with_lines, `_resolve_raw_import_entry` reporting them honestly
    # unresolved (never a fabricated `resolved` path or a fabricated `external=True`). TRUE
    # import-string -> target-file resolution (and the `tg importers` reverse-edge CONFIRM step,
    # gated separately by `_confirm_import_edges`'s own language tuple below) stays deferred for
    # all three -- see docs/BACKLOG.md and this PR's body for the per-language resolver scope
    # that is still missing.
    "go",
    "php",
    "csharp",
    # Top-10 language campaign (Phase 1, C; Phase 2, C++ -- closes the campaign to 10/10): raw
    # `#include` directives + line numbers via lang_c.c_imports_with_lines /
    # lang_cpp.cpp_imports_with_lines, `_resolve_raw_import_entry` reporting them honestly
    # unresolved. TRUE `#include` -> file resolution is deferred and harder than go/php/csharp's
    # own deferred resolvers -- C/C++ have no standardized manifest (no
    # go.mod/composer.json/.csproj equivalent) to resolve against; see docs/BACKLOG.md.
    "c",
    "cpp",
})


def _resolve_raw_import_entry(
    importer_path: Path,
    entry: dict[str, Any],
    repo_root: Path | str | None,
    language_id: str,
) -> dict[str, Any]:
    module = str(entry.get("module", ""))
    line = int(entry.get("line", 0) or 0)
    dynamic = bool(entry.get("dynamic", False))
    dynamic_unresolved = bool(entry.get("dynamic_unresolved", False))

    if dynamic_unresolved:
        # #93 SUB-1: the module argument isn't a static string literal (e.g.
        # `import_module(pkg_var)` / `import(path)`) -- there is no name to resolve against.
        # Fail closed: never guess a target file for an import whose identity we don't actually
        # know (over-reporting here would be a precision regression in a moat feature).
        resolved, external, provenance, confidence = None, False, [], 0.0
    elif language_id in ("javascript", "typescript"):
        candidate_info = _js_ts_module_candidates(importer_path, module, repo_root)
        is_relative = module.startswith(".")
        has_candidates = bool(candidate_info["paths"])
        resolved = next(
            (str(current) for current in candidate_info["paths"] if Path(current).is_file()),
            None,
        )
        external = (not is_relative) and not has_candidates
        provenance = list(candidate_info["provenance"]) or (["relative"] if is_relative else [])
        confidence = float(candidate_info["confidence"]) if resolved is not None else 0.0
    elif language_id == "rust":
        candidates = _rust_module_candidates(importer_path, module, repo_root)
        resolved_candidate = next(
            (current for current in candidates if Path(str(current["path"])).is_file()),
            None,
        )
        parts = [part.strip() for part in module.split("::") if part.strip()]
        first = parts[0] if parts else ""
        is_workspace_crate = first not in {"crate", "self", "super"} and (
            _rust_workspace_entry_for_crate(first, repo_root) is not None
        )
        is_local_syntax = first in {"crate", "self", "super"} or is_workspace_crate
        resolved = str(resolved_candidate["path"]) if resolved_candidate else None
        external = resolved is None and not is_local_syntax
        provenance = list(resolved_candidate["provenance"]) if resolved_candidate else []
        confidence = float(resolved_candidate["confidence"]) if resolved_candidate else 0.0
    elif language_id == "python":
        level = int(entry.get("level", 0) or 0)
        candidate_info = _python_module_candidates(importer_path, module, repo_root, level=level)
        resolved = next(
            (str(current) for current in candidate_info["paths"] if Path(current).is_file()),
            None,
        )
        external = resolved is None
        provenance = list(candidate_info["provenance"])
        if resolved is not None:
            # #152 fix: report the specific "sys-path-insert" provenance when this candidate was
            # reached only via a sys.path-hacked root, instead of the generic heuristic tag.
            tagged_provenance = candidate_info.get("path_provenance", {}).get(resolved)
            if tagged_provenance is not None:
                provenance = [tagged_provenance]
        confidence = float(candidate_info["confidence"]) if resolved is not None else 0.0
    elif language_id == "java":
        # Foundational tier (symbols+imports only): raw import statements are extracted and
        # reported with their line numbers (see _java_imports_with_lines), but resolving WHICH
        # file/module an import points to is cross-file resolution machinery (mirrors Rust/JS/
        # Python's own `_*_module_candidates` helpers) deferred to a follow-up PR. Report as
        # unresolved-but-not-presumed-external -- the same conservative tuple the
        # dynamic_unresolved branch above uses -- rather than guessing (never fabricate
        # resolution precision this extractor doesn't actually have).
        resolved, external, provenance, confidence = None, False, [], 0.0
    elif language_id in ("go", "php", "csharp", "c", "cpp"):
        # Foundational tier (mirrors the "java" branch above): raw import statements are
        # extracted with their line numbers (see lang_go.go_imports_with_lines /
        # lang_php.php_imports_with_lines / lang_csharp.csharp_imports_with_lines /
        # lang_c.c_imports_with_lines / lang_cpp.cpp_imports_with_lines), but resolving WHICH
        # file/module an import points to is deferred -- each of these five needs DIFFERENT
        # missing machinery: go's existing `_go_import_path_to_dir` resolves to a PACKAGE
        # DIRECTORY, not a file (no 1:1 import-to-file mapping to wire); php has no
        # PSR-4/composer.json autoload-map reader; csharp has no `.csproj`/namespace-to-file map;
        # c/cpp have no standardized manifest at all (no go.mod/composer.json/.csproj
        # equivalent) -- a bare `#include "foo.h"` needs the including file's own directory plus
        # the compiler's `-iquote`/`-I` search order, and a bare `#include <foo.h>` is ENTIRELY
        # build-system/toolchain defined, not in the source at all (see lang_c.py's and
        # lang_cpp.py's module docstrings). None of that resolver machinery exists yet for any of
        # the five. Report as unresolved-but-not-presumed-external -- the same conservative tuple
        # every other "resolution not yet built" branch in this function uses -- rather than
        # guessing (never fabricate resolution precision these extractors don't actually have).
        resolved, external, provenance, confidence = None, False, [], 0.0
    else:
        resolved, external, provenance, confidence = None, True, [], 0.0

    result: dict[str, Any] = {
        "module": module,
        "line": line,
        "resolved": resolved,
        "provenance": provenance,
        "resolution_confidence": confidence,
        "external": external,
    }
    if dynamic:
        # Payload-bloat fix (#93 SUB-1 follow-up): only stamp the dynamic markers on an entry
        # that is ACTUALLY dynamic. Stamping "dynamic": false / "dynamic_unresolved": false on
        # every static entry (the overwhelming majority) conveys nothing and tipped
        # `tg importers`' payload past the <10%-of-`tg map` token-economy guard (a MOAT
        # invariant -- see test_importers_payload_is_far_smaller_than_map). Presence of
        # "dynamic" now itself MEANS "this is a dynamic import" -- callers must use
        # `.get("dynamic", False)`, not a hard subscript.
        result["dynamic"] = dynamic
        result["dynamic_unresolved"] = dynamic_unresolved
    return result


def build_file_imports(file_path: str | Path) -> dict[str, Any]:
    """Return what a single FILE imports, resolved to target files where possible.

    O(1): parses exactly one file (plus lazily-primed, cached repo context for tsconfig/Cargo
    workspace lookups) -- no repo scan, no scan cap, no ``--deadline``. See ``build_file_importers``
    for the reverse (who imports this file) primitive, which does need a bounded repo scan.
    """
    resolved_file = Path(file_path).expanduser().resolve()
    if not resolved_file.exists():
        raise FileNotFoundError(f"File not found: {resolved_file}")
    if resolved_file.is_dir():
        raise ValueError(f"tg imports expects a FILE, not a directory: {resolved_file}")

    payload = _envelope(resolved_file)
    payload["routing_reason"] = "file-imports"
    payload["file"] = str(resolved_file)

    spec = lang_registry.spec_for_path(resolved_file)
    language_id = spec.language_id if spec is not None else None
    supported = language_id in _SUPPORTED_FILE_DEPENDENCY_LANGUAGES

    try:
        file_size = resolved_file.stat().st_size
    except OSError:
        file_size = 0
    max_parse_bytes = _max_parse_bytes()
    over_cap = file_size > max_parse_bytes

    imports: list[dict[str, Any]] = []
    result_incomplete = False
    incomplete_reason: str | None = None

    if over_cap:
        # Fix-A-lineage honesty rule (#74 design): the underlying per-file byte cap
        # (`_imports_and_symbols_for_path`, `_imports_with_lines_for_path`) returns an empty
        # result for an over-cap file -- that must NEVER read as "this file genuinely has zero
        # imports". Surface it as an incomplete scan instead of a clean empty list.
        result_incomplete = True
        incomplete_reason = (
            f"file exceeds the {max_parse_bytes}-byte parse cap (size={file_size}); "
            "imports were not scanned"
        )
    elif not supported:
        result_incomplete = True
        incomplete_reason = (
            "no import extractor registered for this file suffix"
            if spec is None
            else f"'{language_id}' has no import-resolution support in `tg imports` yet"
        )
    else:
        repo_root = _infer_project_root(resolved_file)
        for raw_entry in _imports_with_lines_for_path(resolved_file):
            imports.append(
                _resolve_raw_import_entry(resolved_file, raw_entry, repo_root, str(language_id))
            )

    payload["imports"] = imports
    payload["resolved_files"] = sorted(
        dict.fromkeys(str(current["resolved"]) for current in imports if current.get("resolved"))
    )
    payload["external_modules"] = sorted(
        dict.fromkeys(str(current["module"]) for current in imports if current.get("external"))
    )
    payload["unresolved"] = sorted(
        dict.fromkeys(
            str(current["module"])
            for current in imports
            # #93 SUB-1: `current.get("module")` guards out dynamic_unresolved entries (module
            # ""), which are surfaced via their own `dynamic_unresolved` marker on the raw
            # entry, not as a fabricated blank name in this flat unresolved-module-names summary.
            if current.get("module") and not current.get("external") and not current.get("resolved")
        )
    )
    payload["result_incomplete"] = result_incomplete
    if incomplete_reason is not None:
        payload["incomplete_reason"] = incomplete_reason
    return payload


def _confirm_import_edges(
    candidate_importer: Path,
    target_file: str,
    repo_root: Path | str | None,
) -> list[dict[str, Any]]:
    """Re-parse ONE prefiltered candidate file and confirm which (if any) of its raw imports
    actually resolve to ``target_file``.

    Precision step for `tg importers`: the alias-substring prefilter (`_reverse_importers`)
    deliberately over-counts (it is the same mechanism behind the Case-4 false-edge bug) --
    this is what turns "maybe imports it" into "confirmed imports it, on this exact line".
    """
    spec = lang_registry.spec_for_path(candidate_importer)
    language_id = spec.language_id if spec is not None else None
    if language_id not in ("javascript", "typescript", "rust", "python"):
        return []

    edges: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for raw_entry in _imports_with_lines_for_path(candidate_importer):
        module = str(raw_entry.get("module", ""))
        line = int(raw_entry.get("line", 0) or 0)
        dynamic = bool(raw_entry.get("dynamic", False))
        dynamic_unresolved = bool(raw_entry.get("dynamic_unresolved", False))
        if dynamic_unresolved:
            # #93 SUB-1: no literal module name to compare against target_file -- never assert
            # a confirmed edge for an import whose target we can't actually read (precision
            # matters more than recall past this point).
            continue
        python_provenance: list[str] = []
        if language_id in ("javascript", "typescript"):
            matched = _js_ts_module_matches_definition(
                candidate_importer, module, target_file, repo_root
            )
        elif language_id == "rust":
            matched = _rust_module_matches_definition(
                candidate_importer, module, target_file, repo_root
            )
        else:
            # Python: resolve-then-compare via the SAME precise resolver the forward
            # `tg imports` uses, symmetric with the JS/TS/Rust branches above (#74 review
            # fix). `level` (0 for absolute, >=1 for `from .`/`from ..`) is carried on the raw
            # entry by `_python_imports_with_lines` -- threading it through here is what lets
            # `_python_module_candidates` resolve a relative import correctly instead of
            # falling back to a bare path-suffix match that ignores directory context.
            level = int(raw_entry.get("level", 0) or 0)
            matched, python_provenance = _python_module_matches_definition(
                candidate_importer, module, target_file, repo_root, level=level
            )
        if not matched or line in seen_lines:
            continue
        seen_lines.add(line)
        provenance = "parser-backed" if language_id == "python" else "heuristic"
        edge: dict[str, Any] = {
            "file": str(candidate_importer),
            "line": line,
            "text": _source_line_text(candidate_importer, line),
            "kind": "import-consumer",
            "edge_kind": "reverse-import",
            "module": module,
            "provenance": provenance,
            "resolution_confidence": _import_graph_resolution_confidence(provenance),
        }
        if python_provenance == ["sys-path-insert"]:
            # #155 fix: report the sys.path-hack tag honestly on the reverse edge too, mirroring
            # the forward `tg imports` path (`_resolve_raw_import_entry`). A SEPARATE field, not
            # an overwrite of `provenance` above -- that string drives
            # `_import_graph_resolution_confidence`'s enum ("parser-backed"/"heuristic"/
            # "regex-heuristic", pinned by test_import_span_targeting.py) and this edge is still
            # exactly as parser-confirmed (exact AST-parsed, exact resolved-path match) as any
            # other Python edge, just resolved via a sys.path-hacked root instead of a standard
            # one.
            edge["path_provenance"] = "sys-path-insert"
        if dynamic:
            # Payload-bloat fix (#93 SUB-1 follow-up, same rationale as
            # _resolve_raw_import_entry): preserve the dynamic markers on an edge that IS
            # dynamic -- a `tg importers` consumer needs to know this edge came from a dynamic
            # call, not a static import statement -- but a static edge (the majority) gains
            # nothing from two always-False keys, and that bloat tipped the importers payload
            # past the <10%-of-map guard.
            edge["dynamic"] = dynamic
            edge["dynamic_unresolved"] = dynamic_unresolved
        edges.append(edge)
    return edges


_REVERSE_IMPORTER_TIER_SAME_ANCESTRY = 0
_REVERSE_IMPORTER_TIER_SAME_PROJECT = 1
_REVERSE_IMPORTER_TIER_SAME_LANGUAGE = 2
_REVERSE_IMPORTER_TIER_OTHER = 3


def _tier_reverse_importer_candidates(
    candidates: set[str],
    target_file: str,
) -> list[str]:
    """Proximity-tiered reverse-import candidate ordering (dogfood flap: v1.81.15 PASS ->
    v1.81.17 INCOMPLETE "0 importers @ 330/1035 files scanned" on a 50k-file WSL multi-repo
    workspace). Orders the reverse-import PREFILTER candidates by proximity to TARGET before
    ``build_file_importers_from_map`` applies
    its ``CALLER_SCAN_FILE_CEILING``/``--deadline`` slice, instead of the plain lexicographic
    path sort this replaces.

    ``_reverse_importers``'s alias prefilter is DELIBERATELY broad -- ``_module_aliases_for_path``
    keys purely on a file's basename/near-basename (e.g. every ``utils.py`` in a 40-repo
    workspace collides on the "utils" alias), relying on the per-candidate
    ``_confirm_import_edges`` CONFIRM step to separate real edges from same-named-but-unrelated
    noise. On a huge multi-repo ROOT this prefilter can legitimately balloon to 1000+ candidates
    for one target file, the overwhelming majority from OTHER repos. A plain
    ``sorted(reverse_map.get(target_file, set()))`` then buckets that list by absolute-path
    string, i.e. effectively by REPO NAME alphabetically -- nothing about it favors TARGET's own
    repo. When the ceiling/deadline slice below cuts the list short (a large ROOT + a
    --deadline, or the default CLI --deadline-less path racing wall-clock variance on a slow
    WSL /mnt/c mount), a target file whose own repo happens to sort late is entirely capable of
    having ALL of its real (same-repo) importers stranded past the cut line, reporting "0
    importers" while the scan is still honestly stamped incomplete -- exactly the reported flap.

    A real importer of FILE is overwhelmingly within FILE's own repo/package, so reordering the
    SAME candidate set (never dropping or adding members) into four proximity tiers, closest
    first, makes a partial scan cover the highest-yield subset first:

      0. same directory as FILE, or one of FILE's ancestor directories up to (and including) its
         inferred project root (``_infer_project_root`` -- the nearest ``.git``/``pyproject.toml``/
         ``package.json``/``Cargo.toml``/``tsconfig.json`` marker) -- the files most likely to hold
         a real same-package edge (a parent ``__init__.py``, a sibling module).
      1. elsewhere inside the same project root.
      2. outside the project root, but the same language (file suffix) as FILE.
      3. everything else.

    Within a tier, candidates sort by path string for full determinism, independent of the
    upstream set/dict iteration order. This is a pure REORDERING of the same candidate
    membership -- an unbounded/complete scan (no ceiling or deadline hit) still confirms every
    candidate exactly as before and returns the identical found-set;
    ``build_file_importers_from_map`` re-sorts ``edges`` by ``(file, line)`` before returning, so
    the tiering here never changes output order, only which candidates survive a partial cut.
    """
    target_path = Path(target_file)
    target_dir = target_path.parent
    project_root = _infer_project_root(target_path)
    ancestor_dirs: set[Path] = set()
    current = target_dir
    while True:
        try:
            ancestor_dirs.add(current.resolve())
        except OSError:
            ancestor_dirs.add(current)
        if current == project_root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    target_suffix = target_path.suffix.lower()
    # Opus-gate nit (PR #670, same class as the #639 precedent at :1149): _tier runs once per
    # candidate via sorted() below, and for every non-same-ancestry candidate (the majority on a
    # large multi-repo ROOT) used to pay ~3 filesystem syscalls -- its own
    # candidate_path.resolve(), a SECOND resolve of that identical path inside
    # _path_is_relative_to, and a resolve of project_root inside _path_is_relative_to repeated on
    # EVERY call despite being invariant across the whole sort. This runs over the FULL
    # prefiltered set (not ceiling-bounded) BEFORE the deadline-gated confirm loop, so on a slow
    # filesystem a short --deadline could be consumed by tiering itself. Resolve project_root
    # exactly ONCE here; _tier below reuses the already-resolved resolved_candidate directly via
    # relative_to instead of calling _path_is_relative_to (which would re-resolve both sides
    # again) -- net one resolve per candidate, zero per-candidate project-root resolves.
    try:
        project_root_resolved = project_root.resolve()
    except OSError:
        project_root_resolved = project_root

    def _tier(candidate: str) -> int:
        candidate_path = Path(candidate)
        try:
            resolved_candidate = candidate_path.resolve()
        except OSError:
            resolved_candidate = candidate_path
        if resolved_candidate.parent in ancestor_dirs:
            return _REVERSE_IMPORTER_TIER_SAME_ANCESTRY
        try:
            resolved_candidate.relative_to(project_root_resolved)
            return _REVERSE_IMPORTER_TIER_SAME_PROJECT
        except ValueError:
            pass
        if candidate_path.suffix.lower() == target_suffix:
            return _REVERSE_IMPORTER_TIER_SAME_LANGUAGE
        return _REVERSE_IMPORTER_TIER_OTHER

    return sorted(candidates, key=lambda candidate: (_tier(candidate), candidate))


def build_file_importers_from_map(
    repo_map: dict[str, Any],
    file_path: str | Path,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    # A relative `file_path` here is resolved against `repo_map`'s own root, NOT process cwd --
    # deliberate for this function's OTHER callers: `session_file_importers` and the raw
    # `file_importers` daemon-socket command (session_store.py) pass a `file` straight from a
    # remote/persistent-daemon request, where the daemon process's own cwd is meaningless and
    # "relative to the session root" is the only sane interpretation (round-7 security audit
    # #81 deliberately anchors the MCP confinement check the same way; see
    # `tg_session_file_importers` in mcp_server.py). `build_file_importers` (the CLI/cold-tier
    # entry point, dogfood #104) instead pre-resolves FILE to an absolute cwd-anchored path
    # BEFORE calling here, so this join is a no-op for that caller -- do not "fix" this function
    # to resolve against cwd instead, that would silently break the session/daemon contract.
    repo_root = Path(str(repo_map["path"])).resolve()
    resolved_file = Path(file_path).expanduser()
    if not resolved_file.is_absolute():
        resolved_file = repo_root / resolved_file
    resolved_file = resolved_file.resolve()
    if not resolved_file.exists():
        raise FileNotFoundError(f"File not found: {resolved_file}")
    target_file = str(resolved_file)
    # Honesty fix (dogfood, published v1.69.2 wheel): a purely lexical containment check --
    # both paths are already `.resolve()`d above, so this touches no filesystem state beyond
    # what the code above already did. A relative FILE (the daemon/session convention, joined
    # onto repo_root by the `if not resolved_file.is_absolute()` branch above) stays a
    # descendant of repo_root for the normal in-repo convention, so it does not trip this; a
    # net-escaping `..` relative path (e.g. `../other/mod.py`) correctly DOES. The usual
    # outside-root case is an already-absolute FILE (the CLI cold-tier caller, pre-resolved
    # against cwd by `build_file_importers` before this function ever sees it). `relative_to` raises ValueError both for a genuinely
    # unrelated path and for a different Windows drive -- both mean "outside root" here. Without
    # this signal, an outside-root FILE silently reported `importer_count: 0`, indistinguishable
    # from a genuine "unimported inside ROOT" answer, when the real problem was ROOT defaulting
    # to the wrong scan boundary (e.g. CWD instead of the repo that actually contains FILE).
    try:
        resolved_file.relative_to(repo_root)
        file_outside_root = False
    except ValueError:
        file_outside_root = True

    payload = _envelope(repo_root)
    payload["routing_reason"] = "file-importers"
    payload["file"] = target_file
    payload["file_outside_root"] = file_outside_root

    all_files = [str(current) for current in repo_map.get("files", [])]
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(str(name) for name in current.get("imports", []) if name)
        )
        for current in repo_map.get("imports", [])
    }
    # `include_directory_index_aliases=True`: `tg importers` is the ONE reverse consumer that
    # runs the per-candidate CONFIRM step below (`_confirm_import_edges`), so it is the only one
    # that may safely widen the prefilter with directory-index parent-dir aliases (a bare
    # `require('./router')` importer of `router/index.js`). The blast-radius / context callers of
    # `_reverse_importers` leave this OFF -- they feed its output into PageRank scoring with no
    # confirm step, and widening it there reorders pinned output (see the note on that function).
    # #691 gate NIT-2 (#222 residual symmetry): `_reverse_importers` is ~linear -- not the
    # super-linear culprit `_reverse_import_distances` was -- so this is lower severity than
    # NIT-1, but this function's OWN confirm-edges loop just below already honors `deadline_
    # monotonic`; leaving this call un-threaded left one un-gated whole-repo pass in an otherwise
    # deadline-aware pipeline. Fold its own trip into the SAME `deadline_hit` local the
    # confirm-edges loop already sets, so `tg importers --deadline` reports one honest signal
    # regardless of which stage actually consumed the budget.
    reverse_importers_deadline_hit = _DeadlineBreakFlag()
    reverse_map = _reverse_importers(
        all_files,
        imports_by_file,
        include_directory_index_aliases=True,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_importers_deadline_hit,
    )
    # Proximity-tiered, not a plain lexicographic path sort (dogfood flap fix) -- see
    # _tier_reverse_importer_candidates for why a bare alphabetical order strands a target
    # repo's real importers on a large multi-repo ROOT once the ceiling/deadline slice below
    # cuts the candidate list short.
    prefiltered = _tier_reverse_importer_candidates(
        reverse_map.get(target_file, set()), target_file
    )

    ceiling_hit = len(prefiltered) > CALLER_SCAN_FILE_CEILING
    bounded_candidates = prefiltered[:CALLER_SCAN_FILE_CEILING]

    edges: list[dict[str, Any]] = []
    deadline_hit = reverse_importers_deadline_hit.hit
    scanned_count = 0
    for candidate in bounded_candidates:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            deadline_hit = True
            break
        scanned_count += 1
        edges.extend(_confirm_import_edges(Path(candidate), target_file, repo_root))
    edges.sort(key=lambda item: (str(item["file"]), int(item.get("line", 0) or 0)))

    payload["importers"] = edges
    payload["importer_files"] = sorted(dict.fromkeys(str(item["file"]) for item in edges))
    payload["importer_count"] = len(edges)
    # backlog #57 companion fix: copy the (unrelated) repo-map-level scan_limit/scan_remediation
    # BEFORE stamping the caller-scan ceiling's own remediation below -- build_symbol_callers_from_map
    # orders it the same way (_copy_scan_limit, then its ceiling-hit _mark_result_incomplete).
    # Reversing this order was a real bug caught while adding coverage for this branch: a COMPLETE
    # repo-map scan always stamps `scan_remediation: None` on its own payload (see
    # _mark_result_incomplete's docstring), and _copy_scan_limit unconditionally copies that key
    # over when `source["scan_limit"]` is a dict -- clobbering the ceiling-hit remediation this
    # function sets below if _copy_scan_limit ran AFTER it instead of before.
    _copy_scan_limit(payload, repo_map)
    if ceiling_hit:
        # task #61 lesson: bound this sibling loop with the SAME ceiling the caller-scan main
        # loop uses, and mark it via the shared `caller_scan_limit` shape so the CLI's existing
        # `_scan_truncation_warning`/exit-2 contract picks it up with no bespoke wiring.
        # backlog #57 companion fix: route through _mark_result_incomplete (as
        # build_symbol_callers_from_map/build_symbol_refs_from_map already do at their own
        # ceiling-hit sites) instead of setting `caller_scan_limit` alone -- the CLI's exit-2 gate
        # already read `caller_scan_limit` directly so behavior there was unaffected, but a
        # non-CLI consumer (MCP tools, a direct build_file_importers*/session_file_importers call)
        # used to see the ceiling-truncation fact without the `result_incomplete`/`scan_remediation`
        # honesty signal its callers/refs siblings always set.
        _mark_result_incomplete(
            payload,
            remediation=_CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": CALLER_SCAN_FILE_CEILING,
                "files_total": len(prefiltered),
            },
        )
    if deadline_hit:
        payload["partial"] = True
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "importer_candidates_scanned": scanned_count,
            "importer_candidates_total": len(bounded_candidates),
        }
    # Stamped LAST (after _copy_scan_limit and the ceiling/deadline blocks above), only when no
    # more specific remediation already claimed the slot: _copy_scan_limit unconditionally copies
    # the repo-map's own `scan_remediation` (often `None` on a complete ROOT scan) whenever the
    # repo-map carried a `scan_limit` dict, and the ceiling-hit branch above stamps its own
    # narrower message first via `_mark_result_incomplete` -- either would silently clobber this
    # one if it ran earlier (backlog #57 taught the same lesson for the ceiling-hit remediation
    # above). `file_outside_root` is additive-only: it never flips `result_incomplete`/`partial`
    # or changes exit code, since a truly outside-root FILE always finds zero prefiltered
    # candidates (never ceiling- or deadline-bound).
    if file_outside_root and not payload.get("scan_remediation"):
        payload["scan_remediation"] = (
            f"FILE {resolved_file} is outside the scanned ROOT {repo_root}; the importers scan "
            "only covers ROOT, so 0 importers here does NOT mean the file is unused. Pass the "
            "repo containing FILE as ROOT (tg importers FILE <its-repo>) or run from inside it."
        )
    payload["resolution_gaps"] = list(repo_map.get("resolution_gaps", []))
    return payload


def build_file_importers(
    file_path: str | Path,
    root: str | Path = ".",
    *,
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    """Return which files import a single FILE (the reverse #74 file-dependency primitive).

    Cold tier: builds (or reuses a cached) repo map over ROOT, then confirms candidate importer
    edges precisely. See ``session_file_importers`` in session_store.py for the zero-reparse
    session tier, which calls ``build_file_importers_from_map`` directly on a cached map.

    Dogfood #104 fix: FILE is resolved to an absolute path independently, against cwd -- the
    SAME rule ``build_file_imports`` (``tg imports``, which takes no ROOT arg at all) already
    uses -- before ``build_file_importers_from_map`` ever sees it. ROOT is only the scan
    boundary. Without this, a cwd-relative FILE arg (the normal shell convention; from a parent
    directory it is naturally prefixed with ROOT's own directory name, e.g.
    ``myrepo/src/util.py`` when ROOT is ``myrepo``) got silently re-joined onto ROOT a second
    time inside ``build_file_importers_from_map`` (``repo_root / resolved_file``), doubling the
    path (``myrepo/myrepo/src/util.py``) and raising a spurious "not found". An already-absolute
    FILE is unaffected (``.resolve()`` on an absolute path just normalizes it), so this is a
    no-op for every caller that already passes an absolute FILE (both MCP tools always do, via
    ``_confine_read_path``) -- only the previously-buggy cwd-relative case changes.
    """
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    resolved_file_path = Path(file_path).expanduser().resolve()
    repo_map = build_repo_map(
        root,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = build_file_importers_from_map(
        repo_map,
        resolved_file_path,
        deadline_monotonic=deadline_monotonic,
    )
    _copy_partial_signal(result, repo_map)
    return result


def build_symbol_callers(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        max_tests=max_tests,
        _profiling_collector=_profiling_collector,
    )
    _copy_partial_signal(result, repo_map)
    return result


def build_symbol_callers_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    max_tests: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline (dogfood: `tg callers
    # --deadline` overran by 20.2s). `_copy_partial_signal(payload, defs_payload)` below already
    # folds defs_payload's partial signal into this function's own return, so threading the
    # deadline here is the whole fix at this site.
    defs_payload = build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    if defs_payload.get("no_match"):
        payload = dict(defs_payload)
        payload["routing_reason"] = "symbol-callers"
        payload["callers"] = []
        payload["import_graph_consumers"] = []
        payload["import_graph_consumer_files"] = []
        payload["import_graph_consumer_count"] = 0
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _coverage_summary(payload)
        payload["resolution_gaps"] = []
        return _attach_profiling(payload, _profiling_collector)
    repo_root = _repo_map_root_dir(repo_map)
    callers_universe_files, callers_universe_tests = _repo_map_file_and_test_universe(repo_map)
    bounded_files, callers_ceiling_hit = _cap_caller_scan_files(
        [*callers_universe_files, *callers_universe_tests],
        symbol=symbol,
        test_files=callers_universe_tests,
        deadline_monotonic=deadline_monotonic,
    )
    bounded_file_set = {str(current) for current in bounded_files}
    # task #61: this sibling loop used to be unbounded even though it feeds the deadline-aware
    # caller scan below -- share the SAME deadline_monotonic and fold its early-break signal into
    # caller_scan_deadline_hit (set once the scan variable exists, below) for exit-2 honesty.
    preferred_definition_deadline_hit = _DeadlineBreakFlag()
    preferred_definition_files = _preferred_definition_files(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=preferred_definition_deadline_hit,
    )
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    # F25 fix: see the matching comment in build_symbol_refs_from_map -- only trust a Go
    # package-alias resolution as high-confidence when it resolves to a directory that actually
    # owns this symbol.
    go_definition_dirs = frozenset(
        str(Path(current_file).resolve().parent) for current_file in definition_files
    )
    calls: list[dict[str, Any]] = []
    python_files: set[str] = set()

    def _should_scan_for_symbol_callers(current: Path) -> bool:
        if _file_may_contain_literal_symbol(current, symbol):
            return True
        if lang_registry.spec_for_path(current) is None:
            return False
        if not _file_may_import_symbol_definition(current, definition_files):
            return False
        return any(
            _file_imports_symbol_from_definition(
                str(current),
                symbol,
                definition_file,
                repo_root,
            )
            for definition_file in definition_files
        )

    caller_scan_deadline_hit = False
    caller_files_scanned = 0
    with _profiling_phase(_profiling_collector, "caller_scan"):
        for current in bounded_files:
            # moat P0-6 step 6: bound the CALLER-SCAN traversal, not just the repo-map parse. A
            # CENTRAL symbol's cost is scanning many files for references here (leaf symbols were
            # bounded by the parse-loop deadline; central ones hung past it because this loop was
            # unbounded -- dogfood 1.35.0). Break + return partial callers instead of overrunning.
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                caller_scan_deadline_hit = True
                break
            caller_files_scanned += 1
            if not _should_scan_for_symbol_callers(current):
                continue
            current_spec = lang_registry.spec_for_path(current)
            if current_spec is not None and current_spec.language_id == "python":
                python_files.add(str(current))
                _, current_calls = _python_references_and_calls(current, symbol)
            elif current_spec is not None and current_spec.language_id in (
                "javascript",
                "typescript",
            ):
                _, current_calls = _js_ts_references_and_calls(current, symbol, repo_root)
                if not current_calls:
                    current_calls = _js_ts_provider_alias_calls(current, symbol, repo_root)
                if not current_calls:
                    _, current_calls = _regex_references_and_calls(current, symbol)
            elif current_spec is not None and current_spec.language_id == "rust":
                _, current_calls = _rust_references_and_calls(current, symbol, repo_root)
                if not current_calls:
                    current_calls = _rust_provider_alias_calls(current, symbol, repo_root)
                if not current_calls:
                    _, current_calls = _regex_references_and_calls(current, symbol)
            elif current_spec is not None and current_spec.language_id == "go":
                # Fail-closed (Stage 1 trap): no provider-alias/regex fallback for Go -- a
                # grammar-missing file simply contributes no calls (surfaced via
                # `resolution_gaps` for the refs/blast-radius payloads that compute it).
                _, current_calls = lang_go.go_references_and_calls(
                    current, symbol, repo_root, definition_dirs=go_definition_dirs
                )
            else:
                _, current_calls = _regex_references_and_calls(current, symbol)
            for current_call in current_calls:
                call_payload = dict(current_call)
                call_payload["provenance"] = _symbol_navigation_provenance_for_path(
                    str(current_call["file"])
                )
                call_payload.setdefault("ref_kind", "call")
                calls.append(call_payload)

    normalized_provider = _normalize_semantic_provider(semantic_provider)
    external_calls: list[dict[str, Any]] = []
    fallback_used = False
    # Native call count captured BEFORE any lsp/hybrid merge -> the divergence signal for
    # provider_agreement (a partial LSP result must surface as diverged, not a clean lsp-only
    # proof). Mirrors build_symbol_refs_from_map's native_reference_count (H1 fix).
    native_call_count = len(calls)
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _external_references(
                repo_root, symbol, [dict(current) for current in defs_payload["definitions"]]
            )
            if str(Path(str(current.get("file", ""))).expanduser().resolve()) in bounded_file_set
        ]
        python_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _JS_TS_SUFFIXES
        }
        rust_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _RUST_SUFFIXES
        }
        python_external_files = {
            str(current["file"])
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_files = {
            str(current["file"])
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _JS_TS_SUFFIXES
        }
        rust_external_files = {
            str(current["file"])
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _RUST_SUFFIXES
        }
        for external_ref in external_refs:
            text = str(external_ref.get("text", ""))
            if f"{symbol}(" in text or f"{symbol}!" in text or symbol in text:
                external_calls.append({
                    **dict(external_ref),
                    "kind": "call",
                })
        for python_file in sorted(python_external_files):
            alias_calls = _python_provider_alias_calls(Path(python_file), symbol)
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": python_external_provenance.get(
                        python_file,
                        f"lsp-{_language_for_path(Path(python_file))}",
                    ),
                })
        for js_ts_file in sorted(js_ts_external_files):
            alias_calls = _js_ts_provider_alias_calls(
                Path(js_ts_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": js_ts_external_provenance.get(
                        js_ts_file,
                        f"lsp-{_language_for_path(Path(js_ts_file))}",
                    ),
                })
        for rust_file in sorted(rust_external_files):
            alias_calls = _rust_provider_alias_calls(
                Path(rust_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append({
                    **dict(alias_call),
                    "provenance": rust_external_provenance.get(
                        rust_file,
                        f"lsp-{_language_for_path(Path(rust_file))}",
                    ),
                })
        if not external_calls:
            fallback_used = True
            for python_file in sorted(python_files):
                alias_calls = _python_provider_alias_calls(Path(python_file), symbol)
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_language_for_path(Path(python_file))}-fallback",
                    })
            js_ts_files = sorted(
                str(current)
                for current in bounded_files
                if Path(str(current)).suffix in _JS_TS_SUFFIXES
            )
            for js_ts_file in js_ts_files:
                alias_calls = _js_ts_provider_alias_calls(
                    Path(js_ts_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_language_for_path(Path(js_ts_file))}-fallback",
                    })
            rust_files = sorted(
                str(current)
                for current in bounded_files
                if Path(str(current)).suffix in _RUST_SUFFIXES
            )
            for rust_file in rust_files:
                alias_calls = _rust_provider_alias_calls(
                    Path(rust_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append({
                        **dict(alias_call),
                        "provenance": f"lsp-{_language_for_path(Path(rust_file))}-fallback",
                    })
        # Merge (union) native + external calls for BOTH lsp and hybrid modes. A partial /
        # under-indexed LSP result must NEVER discard the correct native answer: the old
        # `calls = external_calls or calls` REPLACED the native call list with the (possibly
        # partial) LSP list in lsp mode, then native_count was recomputed from the REPLACED list
        # (=0) -> _merge_agreement_status always returned "lsp-only" + stamped lsp_proof: True,
        # silently dropping native-found call sites beyond a partial LSP index (H1: native finds
        # create_invoice() in a.py + b.py; a partial LSP index proves only a.py -> b.py is
        # silently dropped and the result stamped authoritative). Union keeps the native truth;
        # the provider_agreement + lsp_proof below are then stamped honestly (native>lsp ->
        # diverged). Mirrors build_symbol_refs_from_map's ref merge exactly.
        proof_calls = [current for current in external_calls if _is_lsp_proof_row(current)]
        merged_calls: dict[tuple[str, int, int], dict[str, Any]] = {}
        for current_call_entry in [*external_calls, *calls]:
            key = (
                str(current_call_entry["file"]),
                int(current_call_entry["line"]),
                int(current_call_entry.get("end_line", current_call_entry["line"])),
            )
            if key in merged_calls:
                merged_calls[key] = _merge_navigation_duplicate(
                    merged_calls[key],
                    dict(current_call_entry),
                )
            else:
                merged_calls[key] = dict(current_call_entry)
        calls = list(merged_calls.values())
        calls.sort(
            key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
        )
        calls = _dedupe_symbol_references(calls)
        if normalized_provider == "lsp" and len(proof_calls) < native_call_count:
            # LSP proved fewer calls than the native scan -> its index is incomplete for this
            # symbol; flag a fallback so the partial is not mis-reported as a clean lsp-only proof.
            fallback_used = True

    definition_locations = {
        (
            str(current["file"]),
            int(current.get("line", current.get("start_line", 0)) or 0),
        )
        for current in definitions
    }
    calls = [
        dict(current)
        for current in calls
        if (str(current["file"]), int(current.get("line", 0) or 0)) not in definition_locations
    ]
    for call in calls:
        if _is_lsp_proof_row(call):
            call["lsp_proof"] = True
        # Additive T1 safety net (mirrors build_symbol_refs_from_map): every caller row is a
        # call by construction, so default a missing ref_kind (external/LSP/alias/regex paths
        # that predate this field) to "call" rather than leaving it absent.
        call.setdefault("ref_kind", "call")
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in calls))
    # task #61: this sibling loop used to be unbounded even though it re-walks the same
    # bounded_files set the deadline-checked caller-scan main loop above just finished -- share the
    # SAME deadline_monotonic and fold its early-break signal into caller_scan_deadline_hit below.
    import_graph_consumers_deadline_hit = _DeadlineBreakFlag()
    import_graph_consumers = _build_import_graph_consumers_from_map(
        repo_map,
        symbol,
        definition_files,
        bounded_files=bounded_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=import_graph_consumers_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    import_graph_consumer_files = sorted(
        dict.fromkeys(str(current["file"]) for current in import_graph_consumers)
    )
    # task #103 Fix 2: thread the shared deadline into context-pack's own symbol-scoring loop too
    # -- previously unbounded even though it feeds the same 4-way partial fold-in just below.
    context_pack_deadline_hit = _DeadlineBreakFlag()
    context_payload = build_context_pack_from_map(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=context_pack_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop B): this sibling loop used to be unbounded even though it feeds the
    # deadline-aware caller scan above -- share the SAME deadline_monotonic and fold its
    # early-break signal into the 4-way partial fold-in below (dominant cause of the 23x overrun
    # on a high-fan-out symbol: _preferred_definition_files falls back to the FULL unfiltered
    # definition_files when all import-scores are 0, flooding this loop unbounded).
    related_tests_deadline_hit = _DeadlineBreakFlag()
    related_tests = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        caller_files=caller_files,
        fallback_tests=list(context_payload.get("tests", [])),
        deadline_monotonic=deadline_monotonic,
        deadline_hit=related_tests_deadline_hit,
        _profiling_collector=_profiling_collector,
    )
    # callers' own DEDICATED --max-tests (design #96 item 2): cap BEFORE related_paths derives
    # below, or the omitted tests leak back in via the second field. `payload` does not exist yet
    # at this point (built via _envelope below), so cap through a scratch dict and read the
    # (possibly capped) list + stamped output_limit back out.
    tests_limit_scratch: dict[str, Any] = {"tests": related_tests}
    _apply_symbol_field_output_limit(tests_limit_scratch, field_name="tests", max_count=max_tests)
    related_tests = cast(list[str], tests_limit_scratch["tests"])
    tests_output_limit = tests_limit_scratch.get("output_limit")

    related_paths: list[str] = []
    for related_path in [
        *definition_files,
        *caller_files,
        *import_graph_consumer_files,
        *related_tests,
    ]:
        if related_path not in related_paths:
            related_paths.append(related_path)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-callers"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["callers"] = calls
    payload["import_graph_consumers"] = import_graph_consumers
    payload["import_graph_consumer_files"] = import_graph_consumer_files
    payload["import_graph_consumer_count"] = len(import_graph_consumers)
    payload["files"] = caller_files
    payload["tests"] = related_tests
    if tests_output_limit is not None:
        payload["output_limit"] = tests_output_limit
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "moderate"
    # task #61 / #52 fix (loop B): fold ALL sibling loops' early-break signal in alongside the main
    # caller-scan loop's own flag -- a central symbol can finish the bounded main scan inside budget
    # while any sibling loop (import-graph-consumers, preferred-definition-file scoring,
    # related-test matching, context-pack symbol scoring) pushes wall-clock well past --deadline.
    # Any one of the five breaking early makes this result partial.
    # task #103 Fix 2: context_pack_deadline_hit joins the same fold-in.
    if (
        caller_scan_deadline_hit
        or import_graph_consumers_deadline_hit.hit
        or preferred_definition_deadline_hit.hit
        or related_tests_deadline_hit.hit
        or context_pack_deadline_hit.hit
    ):
        # moat P0-6 step 6: the caller-scan was cut short by --deadline -> partial (the callers list
        # holds what was found before the budget). graph_completeness downgrades so an agent does not
        # trust a small/zero caller count on a deadline-truncated central-symbol scan.
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        payload["deadline_limit"] = {
            "deadline_exceeded": True,
            "caller_files_scanned": caller_files_scanned,
            "caller_files_total": len(bounded_files),
        }
    payload["ranking_quality"] = _ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["resolution_gaps"] = _language_coverage_gaps_for_universe(bounded_files)
    payload["semantic_provider"] = normalized_provider
    lsp_proof_count = _lsp_proof_row_count(calls)
    if normalized_provider != "native" and lsp_proof_count == 0 and calls:
        fallback_used = True
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=native_call_count,
        lsp_count=lsp_proof_count,
        merged_count=len(calls),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, defs_payload["definitions"]),
        fallback_used=fallback_used,
    )
    _attach_lsp_evidence_status(
        payload,
        semantic_provider=normalized_provider,
        lsp_count=lsp_proof_count,
        fallback_used=fallback_used,
    )
    _copy_scan_limit(payload, defs_payload)
    _copy_partial_signal(payload, defs_payload)
    if callers_ceiling_hit:
        # backlog #1 chokepoint: the caller-scan ceiling dropped files the map otherwise covers
        # -> the caller set is not exhaustive, so mark it honestly incomplete (exit-2 contract).
        _mark_result_incomplete(
            payload,
            remediation=_CALLER_SCAN_CEILING_REMEDIATION,
            caller_scan_limit={
                "possibly_truncated": True,
                "ceiling": CALLER_SCAN_FILE_CEILING,
                "files_total": len(callers_universe_files) + len(callers_universe_tests),
            },
        )
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_callers_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_callers(
            symbol,
            path,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def _blast_radius_no_match_is_possibly_truncated(payload: dict[str, Any]) -> bool:
    """True iff a blast-radius-shaped payload is a ``no_match`` produced from a
    possibly-truncated repo map -- the one case where a map-based lookup that skipped the
    literal-seed rescue below cannot be trusted (the symbol may simply sit outside the scan
    window, not genuinely be absent from the repo).

    ONE shared definition for three call sites that must agree verbatim on exactly when a
    no_match is trustworthy, instead of three independently-drifting inline copies:
    (1) this function's own caller below, which retries via ``_literal_symbol_seed_files``;
    (2) the Tier-2 daemon agent-capsule's call-site-evidence collector
    (``agent_capsule._collect_capsule_call_site_evidence_from_map``), which has no rescue
    available on the daemon's cached map and must instead signal its caller to fall back to the
    cold path (task #108, the TRAP A class); and (3) ``main._daemon_blast_radius_no_match_is_
    unreliable`` (the Tier-1 ``blast-radius`` command's own daemon-fallback gate, audit #107).

    Deliberately narrow: only fires on ``no_match`` AND ``possibly_truncated`` together. A
    no_match on a COMPLETE map is a real miss -- treating that as untrustworthy too would
    defeat retries/daemon-fallback for every genuine no-match, not just the truncated ones.
    """
    if not payload.get("no_match"):
        return False
    scan_limit = payload.get("scan_limit")
    return isinstance(scan_limit, dict) and bool(scan_limit.get("possibly_truncated"))


def build_symbol_blast_radius(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_callers: int | None = None,
    max_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # moat P0-6 step 3: ONE absolute deadline shared across BOTH the initial scan and the
    # literal-seed retry below -- a per-call re-derivation would double the wall-clock for exactly
    # the already-truncated huge repos that need a deadline most.
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    payload = build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    if _blast_radius_no_match_is_possibly_truncated(payload):
        seed_files = _literal_symbol_seed_files(
            Path(path).expanduser().resolve(),
            symbol,
            existing_files=_repo_map_file_universe(repo_map),
        )
        if seed_files:
            repo_map = build_repo_map(
                path,
                max_repo_files=max_repo_files,
                extra_files=seed_files,
                deadline_monotonic=deadline_monotonic,
                _profiling_collector=_profiling_collector,
            )
            payload = build_symbol_blast_radius_from_map(
                repo_map,
                symbol,
                max_depth=max_depth,
                semantic_provider=semantic_provider,
                deadline_monotonic=deadline_monotonic,
                _profiling_collector=_profiling_collector,
            )
            payload_scan_limit = payload.get("scan_limit")
            if isinstance(payload_scan_limit, dict):
                payload_scan_limit["literal_seed_files"] = [str(current) for current in seed_files]
    result = _apply_blast_radius_output_limits(
        payload,
        max_callers=max_callers,
        max_files=max_files,
    )
    _copy_partial_signal(result, repo_map)  # deadline signal from the (possibly retried) scan
    return result


def _apply_blast_radius_output_limits(
    payload: dict[str, Any],
    *,
    max_callers: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    normalized_max_callers = max(1, int(max_callers)) if max_callers is not None else None
    normalized_max_files = max(1, int(max_files)) if max_files is not None else None
    if normalized_max_callers is None and normalized_max_files is None:
        return payload

    limited = dict(payload)
    original_callers = _list_of_dicts(payload.get("callers"))
    original_files = _list_of_strings(payload.get("files"))
    original_import_consumers = _list_of_dicts(payload.get("import_graph_consumers"))

    if normalized_max_callers is not None:
        limited["callers"] = original_callers[:normalized_max_callers]
        limited["caller_tree"] = _list_of_dicts(payload.get("caller_tree"))[:normalized_max_callers]

    if normalized_max_files is not None:
        selected_files = original_files[:normalized_max_files]
        selected_file_set = set(selected_files)
        limited["files"] = selected_files
        limited["affected_files"] = list(selected_files)
        limited["file_matches"] = [
            current
            for current in _list_of_dicts(payload.get("file_matches"))
            if str(current.get("path")) in selected_file_set
        ][:normalized_max_files]
        limited["file_summaries"] = [
            {
                **current,
                "symbols": [
                    compact_symbol
                    for compact_symbol in (
                        _compact_symbol_record(symbol)
                        for symbol in _list_of_dicts(current.get("symbols"))
                    )
                    if compact_symbol is not None
                ][:_BLAST_RADIUS_LIMITED_SYMBOLS_PER_FILE],
            }
            for current in _list_of_dicts(payload.get("file_summaries"))
            if str(current.get("path")) in selected_file_set
        ][:normalized_max_files]
        limited["tests"] = _list_of_strings(payload.get("tests"))[:normalized_max_files]
        selected_test_set = set(limited["tests"])
        limited["test_matches"] = [
            current
            for current in _list_of_dicts(payload.get("test_matches"))
            if str(current.get("path")) in selected_test_set
        ][:normalized_max_files]
        limited["related_paths"] = _list_of_strings(payload.get("related_paths"))[
            :normalized_max_files
        ]
        limited["symbols"] = [
            current
            for current in _list_of_dicts(payload.get("symbols"))
            if str(current.get("file")) in selected_file_set
        ]
        limited["imports"] = [
            current
            for current in _list_of_dicts(payload.get("imports"))
            if str(current.get("file")) in selected_file_set
        ]
        limited["import_graph_consumers"] = [
            current
            for current in original_import_consumers
            if str(current.get("file")) in selected_file_set
        ]
        limited["import_graph_consumer_files"] = sorted(
            dict.fromkeys(str(current["file"]) for current in limited["import_graph_consumers"])
        )
        limited["import_graph_consumer_count"] = len(limited["import_graph_consumers"])
        limited_caller_tree: list[dict[str, Any]] = []
        for current in _list_of_dicts(limited.get("caller_tree", payload.get("caller_tree"))):
            depth_files = [
                path for path in _list_of_strings(current.get("files")) if path in selected_file_set
            ][:normalized_max_files]
            if not depth_files:
                continue
            compact_level = dict(current)
            compact_level["files"] = depth_files
            limited_caller_tree.append(compact_level)
        limited["caller_tree"] = limited_caller_tree
        rendered_lines = [f"Blast radius for {payload.get('symbol', '')}:"]
        for current in limited_caller_tree:
            rendered_lines.append(f"Depth {current.get('depth')}:")
            rendered_lines.extend(f"- {path}" for path in _list_of_strings(current.get("files")))
        limited["rendered_caller_tree"] = "\n".join(rendered_lines)
    elif "files" in limited:
        limited["affected_files"] = _list_of_strings(limited.get("files"))

    returned_import_consumers = _list_of_dicts(
        limited.get("import_graph_consumers", original_import_consumers)
    )
    limited["output_limit"] = {
        "max_callers": normalized_max_callers,
        "max_files": normalized_max_files,
        "callers_truncated": (
            normalized_max_callers is not None and len(original_callers) > normalized_max_callers
        ),
        "files_truncated": (
            normalized_max_files is not None and len(original_files) > normalized_max_files
        ),
        "import_consumers_truncated": (
            normalized_max_files is not None
            and len(returned_import_consumers) < len(original_import_consumers)
        ),
        "total_callers": len(original_callers),
        "returned_callers": len(_list_of_dicts(limited.get("callers"))),
        "omitted_callers": max(
            0, len(original_callers) - len(_list_of_dicts(limited.get("callers")))
        ),
        "total_files": len(original_files),
        "returned_files": len(_list_of_strings(limited.get("files"))),
        "omitted_files": max(0, len(original_files) - len(_list_of_strings(limited.get("files")))),
        "total_import_consumers": len(original_import_consumers),
        "returned_import_consumers": len(returned_import_consumers),
        "omitted_import_consumers": max(
            0, len(original_import_consumers) - len(returned_import_consumers)
        ),
    }
    return limited


def build_symbol_blast_radius_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # audit C1/C2: this call used to be BARE, dropping the deadline this function
    # itself received -- build_symbol_defs_from_map's OWN internal _relevant_tests_for_symbol
    # scan (repo_map.py:3812) ran unbounded regardless of --deadline. This is blast-radius' OWN
    # DIRECT defs call (distinct from the deadline-aware callers_payload/impact_payload sub-calls
    # below, which already threaded deadline_monotonic through their own now-fixed sibling defs
    # calls) -- fixing only the sub-calls would leave THIS call blocking before either of them
    # ever runs. Folded into this function's own partial stamp below (defs_payload.get("partial")).
    defs_payload = build_symbol_defs_from_map(
        repo_map, symbol, semantic_provider=semantic_provider, deadline_monotonic=deadline_monotonic
    )
    default_agreement, default_status = _default_provider_metadata(
        _repo_map_root_dir(repo_map),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    if defs_payload.get("no_match"):
        payload = dict(defs_payload)
        payload["routing_reason"] = "symbol-blast-radius"
        payload["max_depth"] = max(0, int(max_depth))
        payload["callers"] = []
        payload["import_graph_consumers"] = []
        payload["import_graph_consumer_files"] = []
        payload["import_graph_consumer_count"] = 0
        payload["affected_files"] = []
        payload["blast_radius_score"] = 0.0
        payload["file_matches"] = []
        payload["file_summaries"] = []
        payload["test_matches"] = []
        payload["caller_tree"] = []
        payload["rendered_caller_tree"] = str(
            defs_payload.get("message", "No exact definition found.")
        )
        payload["graph_trust_summary"] = {
            "graph_completeness": "empty",
            "edge_confidence": "none",
            "evidence_counts": {"parser_backed": 0, "heuristic": 0},
        }
        payload["resolution_gaps"] = []
        payload["ranking_quality"] = "empty"
        payload["coverage_summary"] = _coverage_summary(payload)
        payload["provider_agreement"] = dict(default_agreement)
        payload["provider_status"] = dict(default_status)
        _copy_lsp_evidence_status(payload, defs_payload)
        if "scan_limit" in repo_map:
            payload["scan_limit"] = dict(repo_map["scan_limit"])
        return _attach_profiling(payload, _profiling_collector)
    callers_payload = build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # #52 fix (loop C): forward the shared deadline into the impact call too -- previously
    # unbounded (build_symbol_impact_from_map had no deadline param at all), so blast-radius
    # inherited an unbounded internal preferred-definition/related-tests scan via this call even
    # though every other seam in this function is deadline-aware.
    impact_payload = build_symbol_impact_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    # task #61: this blast-radius-local call feeds the same sibling loop as the callers seam above
    # -- share the SAME deadline_monotonic and fold its early-break signal into this payload's own
    # partial marking below (build_symbol_callers_from_map already folds its OWN internal
    # preferred-definition-files/import-graph-consumers calls into callers_payload["partial"]).
    preferred_definition_deadline_hit_blast = _DeadlineBreakFlag()
    preferred_definition_files = _preferred_definition_files(
        repo_map,
        symbol,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=preferred_definition_deadline_hit_blast,
    )
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        {
            **dict(current),
            "provenance": str(
                current.get(
                    "provenance", _symbol_navigation_provenance_for_path(str(current["file"]))
                )
            ),
        }
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [
        {
            **dict(current),
            "provenance": str(
                current.get(
                    "provenance", _symbol_navigation_provenance_for_path(str(current["file"]))
                )
            ),
        }
        for current in defs_payload["definitions"]
    ]

    normalized_depth = max(0, int(max_depth))
    all_files = [str(current) for current in repo_map.get("files", [])]
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get("provenance", _symbol_navigation_provenance_for_path(str(current["file"])))
        )
        for current in repo_map.get("imports", [])
    }
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(
                str(import_name) for import_name in current.get("imports", []) if import_name
            )
        )
        for current in repo_map.get("imports", [])
    }
    # #222 (real-workspace-scale residual of #220/#669/#671): this is blast-radius' OWN direct
    # reverse-import-graph derivation (distinct from callers_payload/impact_payload's already-
    # deadline-aware sub-scans read above) -- it fed the same un-gated `_reverse_import_distances`/
    # `_reverse_importers` this PR bounds in `_build_context_pack_from_map`, reached from `tg
    # agent`'s cold path via `_collect_capsule_call_site_evidence`'s blast-radius call whenever a
    # symbol was explicitly requested at high seed confidence. Dedicated flag (not reusing
    # `preferred_definition_deadline_hit_blast`, which names a DIFFERENT stage) folded into the
    # SAME partial/deadline_limit union below.
    reverse_import_graph_deadline_hit_blast = _DeadlineBreakFlag()
    reverse_importers = _reverse_importers(
        all_files,
        imports_by_file,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_import_graph_deadline_hit_blast,
        _profiling_collector=_profiling_collector,
    )
    definition_files = [str(current["file"]) for current in definitions]
    dependency_distances = _reverse_import_distances(
        definition_files,
        all_files,
        imports_by_file,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_import_graph_deadline_hit_blast,
        _profiling_collector=_profiling_collector,
    )
    reverse_graph_scores = _personalized_reverse_import_pagerank(
        definition_files,
        all_files,
        reverse_importers,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=reverse_import_graph_deadline_hit_blast,
        _profiling_collector=_profiling_collector,
    )

    direct_callers = [dict(current) for current in callers_payload.get("callers", [])]
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in direct_callers))
    import_graph_consumers = [
        dict(current) for current in _list_of_dicts(callers_payload.get("import_graph_consumers"))
    ]
    import_graph_consumer_files = sorted(
        dict.fromkeys(str(current["file"]) for current in import_graph_consumers)
    )
    import_graph_consumer_file_set = set(import_graph_consumer_files)

    file_matches_by_path: dict[str, dict[str, Any]] = {}
    file_depths: dict[str, int] = {}

    def _merge_file_match(
        current_path: str,
        *,
        depth: int,
        score: int,
        reasons: list[str],
        graph_score: float | None = None,
    ) -> None:
        entry = file_matches_by_path.setdefault(
            current_path,
            {
                "path": current_path,
                "depth": depth,
                "score": 0,
                "reasons": [],
            },
        )
        entry["depth"] = min(int(entry.get("depth", depth)), depth)
        entry["score"] = max(int(entry.get("score", 0)), score)
        for reason in reasons:
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        if graph_score is not None and graph_score > float(entry.get("graph_score", 0.0)):
            entry["graph_score"] = round(graph_score, 6)
        file_depths[current_path] = min(file_depths.get(current_path, depth), depth)

    for current in definition_files:
        current_match: dict[str, Any] = next(
            (
                item
                for item in impact_payload.get("file_matches", [])
                if str(item.get("path")) == current
            ),
            {},
        )
        _merge_file_match(
            current,
            depth=0,
            score=max(1, int(current_match.get("score", 0))),
            reasons=["definition", *list(current_match.get("reasons", []))],
            graph_score=(
                float(current_match["graph_score"])
                if "graph_score" in current_match
                else reverse_graph_scores.get(current)
            ),
        )

    for current in caller_files:
        _merge_file_match(
            current,
            depth=1 if current not in definition_files else 0,
            score=max(
                1,
                int(
                    next(
                        (
                            item.get("score", 0)
                            for item in impact_payload.get("file_matches", [])
                            if str(item.get("path")) == current
                        ),
                        0,
                    )
                ),
            ),
            reasons=["caller"],
            graph_score=reverse_graph_scores.get(current),
        )

    for current, depth in dependency_distances.items():
        if depth > normalized_depth:
            continue
        reasons = ["graph-depth"]
        if current in caller_files and "caller" not in reasons:
            reasons.append("caller")
        if current in import_graph_consumer_file_set:
            reasons.append("import-consumer")
        if current in definition_files and "definition" not in reasons:
            reasons.append("definition")
        graph_score = reverse_graph_scores.get(current, 0.0)
        score = max(1, round(graph_score * 10))
        _merge_file_match(
            current,
            depth=depth,
            score=score,
            reasons=reasons,
            graph_score=graph_score if graph_score > 0.0 else None,
        )

    ranked_files = sorted(
        file_matches_by_path.values(),
        key=lambda item: (
            int(item.get("depth", normalized_depth + 1)),
            -int(item.get("score", 0)),
            -float(item.get("graph_score", 0.0)),
            str(item.get("path", "")),
        ),
    )
    radius_files = [str(item["path"]) for item in ranked_files]

    test_match_lookup = {
        str(item["path"]): dict(item) for item in impact_payload.get("test_matches", [])
    }
    related_tests: list[str] = []
    for current in impact_payload.get("tests", []):
        test_entry = test_match_lookup.get(str(current), {})
        reasons = list(test_entry.get("reasons", []))
        if "blast-radius" not in reasons:
            reasons.append("blast-radius")
        graph_score = float(test_entry.get("graph_score", 0.0))
        score = int(test_entry.get("score", 0))
        coverage_hits = 0
        current_imports = imports_by_file.get(str(current), [])
        for source_file in radius_files:
            aliases = _module_aliases_for_path(source_file)
            if any(
                alias and alias in import_name.lower()
                for alias in aliases
                for import_name in current_imports
            ):
                coverage_hits += 1
        if coverage_hits <= 0 and not any(
            reason
            in {"import-graph", "graph-centrality", "framework-pattern", "test-graph", "filename"}
            for reason in reasons
        ):
            continue
        score += coverage_hits
        related_tests.append(str(current))
        updated_entry = dict(test_entry)
        updated_entry["path"] = str(current)
        updated_entry["score"] = score
        updated_entry["reasons"] = reasons
        if graph_score > 0.0:
            updated_entry["graph_score"] = graph_score
        test_match_lookup[str(current)] = updated_entry

    caller_tree: list[dict[str, Any]] = []
    rendered_lines = [f"Blast radius for {symbol}:"]
    for depth in range(0, normalized_depth + 1):
        depth_files = [
            str(item["path"])
            for item in ranked_files
            if int(item.get("depth", normalized_depth + 1)) == depth
        ]
        if not depth_files:
            continue
        parser_backed_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"python-ast", "tree-sitter"}
        )
        heuristic_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"regex-heuristic", "heuristic"}
        )
        edge_provenance = ["graph-derived"]
        if parser_backed_edges > 0:
            edge_provenance.append("parser-backed")
        if heuristic_edges > 0:
            edge_provenance.append("heuristic")
        if parser_backed_edges > 0 and heuristic_edges == 0:
            edge_confidence = "strong"
        elif parser_backed_edges > 0:
            edge_confidence = "moderate"
        else:
            edge_confidence = "weak"
        caller_tree.append({
            "depth": depth,
            "files": depth_files,
            "provenance": edge_provenance,
            "graph_completeness": "moderate",
            "edge_summary": {
                "edge_kind": "reverse-import",
                "confidence": edge_confidence,
                "provenance": edge_provenance,
                "evidence_counts": {
                    "parser_backed": parser_backed_edges,
                    "heuristic": heuristic_edges,
                },
            },
        })
        rendered_lines.append(f"Depth {depth}:")
        rendered_lines.extend(f"- {current}" for current in depth_files)

    related_paths: list[str] = []
    for current in [*radius_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-blast-radius"
    payload["symbol"] = symbol
    payload["max_depth"] = normalized_depth
    payload["definitions"] = definitions
    payload["callers"] = direct_callers
    payload["import_graph_consumers"] = import_graph_consumers
    payload["import_graph_consumer_files"] = import_graph_consumer_files
    payload["import_graph_consumer_count"] = len(import_graph_consumers)
    payload["files"] = radius_files
    payload["affected_files"] = list(radius_files)
    evidence_score = sum(min(10, max(0, int(item.get("score", 0)))) for item in ranked_files)
    evidence_score += len(direct_callers) + len(related_tests)
    evidence_denominator = max(
        1, (10 * len(ranked_files)) + len(direct_callers) + len(related_tests)
    )
    payload["blast_radius_score"] = round(min(1.0, evidence_score / evidence_denominator), 3)
    payload["file_matches"] = ranked_files
    payload["file_summaries"] = _file_summaries(repo_map.get("symbols", []), radius_files)
    payload["tests"] = related_tests
    payload["test_matches"] = [test_match_lookup[str(current)] for current in related_tests]
    payload["caller_tree"] = caller_tree
    payload["rendered_caller_tree"] = "\n".join(rendered_lines)
    payload["resolution_gaps"] = list(callers_payload.get("resolution_gaps", []))
    payload["graph_trust_summary"] = _downgrade_graph_trust_summary_for_coverage_gaps(
        _graph_trust_summary(caller_tree, calls=direct_callers),
        payload["resolution_gaps"],
    )
    payload["imports"] = impact_payload["imports"]
    payload["symbols"] = impact_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _ranking_quality(payload["file_matches"], payload["test_matches"])
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(
        callers_payload.get("provider_agreement", default_agreement)
    )
    payload["provider_status"] = dict(callers_payload.get("provider_status", default_status))
    _copy_lsp_evidence_status(payload, callers_payload)
    if "scan_limit" in repo_map:
        payload["scan_limit"] = dict(repo_map["scan_limit"])
    # moat P0-6 step 6: the direct-caller scan may have been cut by --deadline for a CENTRAL symbol
    # -> carry its partial + deadline_limit onto the blast radius so a caller does not trust a
    # truncated caller_tree / blast_radius_score as complete.
    # task #61: OR in this function's OWN preferred-definition-files call (a second, redundant call
    # separate from the one build_symbol_callers_from_map already made internally) -- it can still
    # push wall-clock past the shared deadline even when callers_payload came back complete.
    # #52 fix (loop C): ALSO OR in impact_payload's own partial signal -- this function reads
    # impact_payload["file_matches"]/["tests"]/["imports"]/["symbols"] directly (below), so a
    # deadline-truncated impact scan makes THIS result incomplete too, even when callers_payload
    # and this function's own direct preferred-definition-files call both finished inside budget.
    # audit C1/C2: ALSO OR in defs_payload's own partial signal -- defs_payload is
    # this function's OWN direct build_symbol_defs_from_map call above (distinct from the nested
    # defs calls inside callers_payload/impact_payload, which already fold into THEIR OWN partial
    # fields read above); a deadline blown during THIS call's stage-1 scan must not go unreported
    # just because it isn't otherwise read by name past this point.
    # #222: ALSO OR in this function's OWN reverse-import-graph derivation (`reverse_importers`/
    # `dependency_distances`/`reverse_graph_scores` above) -- previously un-gated and unreported.
    if (
        callers_payload.get("partial")
        or preferred_definition_deadline_hit_blast.hit
        or impact_payload.get("partial")
        or defs_payload.get("partial")
        or reverse_import_graph_deadline_hit_blast.hit
    ):
        payload["partial"] = True
        payload["graph_completeness"] = "partial"
        caller_deadline_limit = callers_payload.get("deadline_limit")
        if isinstance(caller_deadline_limit, dict):
            payload["deadline_limit"] = dict(caller_deadline_limit)
        elif preferred_definition_deadline_hit_blast.hit:
            payload["deadline_limit"] = {"deadline_exceeded": True}
        elif isinstance(impact_payload.get("deadline_limit"), dict):
            payload["deadline_limit"] = dict(impact_payload["deadline_limit"])
        elif isinstance(defs_payload.get("deadline_limit"), dict):
            payload["deadline_limit"] = dict(defs_payload["deadline_limit"])
        elif reverse_import_graph_deadline_hit_blast.hit:
            payload["deadline_limit"] = {"deadline_exceeded": True}
    if callers_payload.get("result_incomplete"):
        # backlog #1 chokepoint: the direct-caller scan's internal ceiling (CALLER_SCAN_FILE_CEILING)
        # dropped files the map covers -> the blast radius built on top of it is not exhaustive
        # either (session_blast_radius calls this function directly on a full, unbounded session
        # repo_map -- this is the leak fix, since a per-command option default can't reach that path).
        # `caller_scan_truncated` is a DISTINCT scan-incompleteness signal so the blast-radius CLI gate
        # can exit 2 on it WITHOUT catching a mere --max-callers/--max-files OUTPUT cap (which also sets
        # result_incomplete but is a complete analysis capped only for display -> stays exit 0).
        payload["caller_scan_truncated"] = True
        callers_caller_scan_limit = callers_payload.get("caller_scan_limit")
        _mark_result_incomplete(
            payload,
            remediation=str(
                callers_payload.get("scan_remediation", _CALLER_SCAN_CEILING_REMEDIATION)
            ),
            caller_scan_limit=(
                dict(callers_caller_scan_limit)
                if isinstance(callers_caller_scan_limit, dict)
                else None
            ),
        )
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    max_callers: int | None = None,
    max_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_blast_radius(
            symbol,
            path,
            max_depth=max_depth,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
            max_callers=max_callers,
            max_files=max_files,
        ),
        indent=2,
    )


def build_symbol_blast_radius_plan(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    deadline_seconds: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    # CEO v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg blast-radius-plan` (Click
    # "No such option" exit-2) even though its true sibling build_symbol_blast_radius already had
    # it -- same thin, additive thread-through (mirrors #581's build_symbol_defs / this file's own
    # build_symbol_blast_radius above), threaded into BOTH the initial repo_map build and the
    # _from_map call below so the caller-scan pass inside build_symbol_blast_radius_from_map also
    # honors the same shared absolute budget, not just the file walk/parse.
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    repo_map = build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    result = build_symbol_blast_radius_plan_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    _copy_partial_signal(result, repo_map)
    return result


def build_symbol_blast_radius_plan_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    payload["routing_reason"] = "symbol-blast-radius-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = list(payload.get("file_summaries", []))[:normalized_max_files]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=normalized_max_files,
        max_symbols=normalized_max_symbols,
        max_depth=max_depth,
        blast_radius_payload=payload,
        # task #203: this call dropped deadline_monotonic entirely even though it is already in
        # scope as this function's own parameter -- the identical #642 gate nit-1 gap already fixed
        # on build_context_edit_plan_from_map's matching call (repo_map.py:12617-12622), unfixed
        # here. Without this, the edit-plan-seed's validation-test discovery inside
        # _attach_edit_plan_metadata ran unbounded even when a caller supplied a deadline that DID
        # bound the build_symbol_blast_radius_from_map call above.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=_profiling_collector,
        # #212 (broader B9/#661 flag-lie): `tg blast-radius-plan` has NO downstream compaction step
        # at all (unlike context-render), so before this, edit_plan_seed.suggested_edits was
        # unconditionally unbounded regardless of --max-files -- dogfooded on tensor-grep's own repo
        # (SearchConfig, 80 files): --max-files 1 returned files=[1] but suggested_edits spanning 8
        # distinct files. Opt into the same mechanism build_context_edit_plan_from_map already uses.
        suggested_edits_max=normalized_max_files,
    )
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_plan_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_blast_radius_plan(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )


def build_symbol_blast_radius_render(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    repo_map = build_repo_map(path, max_repo_files=max_repo_files, _profiling_collector=collector)
    return build_symbol_blast_radius_render_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
        semantic_provider=semantic_provider,
        _profiling_collector=collector,
    )


def build_symbol_blast_radius_render_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    semantic_provider: str = "native",
    deadline_monotonic: float | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    radius_payload = build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
    )
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)

    top_files = {str(current) for current in radius_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    ranked_symbols = _sorted_ranked_symbols(list(radius_payload.get("symbols", [])))
    # Perf guard (TG-4): a high-fan-in symbol yields thousands of candidate symbols in the top
    # files, and each candidate triggers an expensive build_symbol_source_from_map lookup. With
    # only the max_sources accumulation as a bound, a symbol whose candidates rarely yield a
    # matching source scanned them ALL — ~3.5 min on a large repo vs ~3s for the JSON graph.
    # Cap the expensive per-candidate lookups. ranked_symbols is relevance-sorted, so the best
    # sources are examined first and we degrade gracefully to fewer rendered blocks.
    max_source_candidates = max(max_sources * 8, 24)
    examined_candidates = 0
    # task #203: bound this per-candidate source-lookup loop with the SAME deadline_monotonic
    # pattern callers/refs/file_importers already use (checked first, unconditionally, before any
    # per-candidate filtering) -- the TG-4 comment above already documents this loop running
    # "~3.5 min on a large repo" with only the COUNT-based max_source_candidates cap and no
    # wall-clock bound at all.
    source_loop_deadline_hit = False
    for current_symbol in ranked_symbols:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            source_loop_deadline_hit = True
            break
        current_file = str(current_symbol["file"])
        if current_file not in top_files:
            continue
        symbol_key = (current_file, str(current_symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        examined_candidates += 1
        if examined_candidates > max_source_candidates:
            break
        symbol_sources = build_symbol_source_from_map(
            repo_map,
            str(current_symbol["name"]),
            _profiling_collector=collector,
        ).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            break
        if len(sources) >= max_sources:
            break

    payload = dict(radius_payload)
    payload["routing_reason"] = "symbol-blast-radius-render"
    payload["query"] = f"blast radius: {symbol}"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    (
        rendered_context,
        sections,
        truncated,
        token_estimate,
        omitted_sections,
    ) = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = omitted_sections
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=max_files,
        max_symbols=max_sources,
        max_depth=max_depth,
        blast_radius_payload=radius_payload,
        # task #203: thread the deadline into the edit-plan-seed assembly too, mirroring the
        # identical fix on build_context_edit_plan_from_map (repo_map.py:12617-12622) and
        # build_symbol_blast_radius_plan_from_map (this file, just above) -- this call previously
        # dropped deadline_monotonic entirely.
        deadline_monotonic=deadline_monotonic,
        _profiling_collector=collector,
        # #212 (broader B9/#661 flag-lie): `tg blast-radius-render` has NO downstream compaction
        # step either, in ANY render_profile, so before this, edit_plan_seed.suggested_edits was
        # unconditionally unbounded regardless of --max-files -- dogfooded on tensor-grep's own repo
        # (SearchConfig, 80 files): --max-files 1 returned files=[1] but suggested_edits spanning 40
        # distinct files (identical 73-entry count as --max-files 50 -- zero bounding effect at all).
        # Opt into the same mechanism build_context_edit_plan_from_map already uses.
        suggested_edits_max=max_files,
    )
    # task #203: fold this function's OWN source-lookup loop deadline signal into partial --
    # `dict(radius_payload)` above already copied forward any partial/deadline_limit that
    # build_symbol_blast_radius_from_map (or _attach_edit_plan_metadata's own edit_plan_seed fold-in
    # just above) already stamped, so `setdefault` here never clobbers a richer upstream signal;
    # this only adds the flag when THIS loop was the one that broke early.
    if source_loop_deadline_hit:
        payload["partial"] = True
        payload.setdefault(
            "deadline_limit",
            {
                "deadline_exceeded": True,
                "source_candidates_examined": examined_candidates,
                "source_candidates_total": len(ranked_symbols),
            },
        )
    return _attach_profiling(payload, collector)


def build_symbol_blast_radius_render_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    semantic_provider: str = "native",
    max_repo_files: int | None = None,
) -> str:
    return json.dumps(
        build_symbol_blast_radius_render(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=optimize_context,
            render_profile=render_profile,
            profile=profile,
            semantic_provider=semantic_provider,
            max_repo_files=max_repo_files,
        ),
        indent=2,
    )
