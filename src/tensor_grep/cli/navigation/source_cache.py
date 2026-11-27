from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Any, NamedTuple

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli.repo_map_cache import _mtime_aware_cache


def _mtime_key(path_str: str) -> tuple[int, int]:
    """Return (st_mtime_ns, st_size) for *path_str*, or (-1, -1) on error."""
    try:
        st = _repo_map.Path(path_str).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (-1, -1)


def _clear_all_source_caches() -> None:
    """Sweep every _mtime_aware_cache-decorated cache (Fix A / Guard 3)."""
    for clear in _repo_map._MTIME_CACHE_CLEAR_REGISTRY:
        clear()
    _repo_map._JS_TS_REPO_CONTEXTS.clear()
    _repo_map._RUST_REPO_CONTEXTS.clear()
    _repo_map.lang_go.clear_go_repo_context_cache()


def _read_source_cached(path_str: str) -> bytes:
    """Read raw file bytes, cached by (mtime_ns, size); see Fix A note above."""
    try:
        size = _repo_map.Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return _repo_map.read_content_bytes(path_str)
    return _repo_map._read_source_cached_bounded(path_str)


@_mtime_aware_cache(maxsize=_repo_map._SOURCE_READ_CACHE_MAXSIZE)
def _read_source_cached_bounded(path_str: str) -> bytes:
    return _repo_map.read_content_bytes(path_str)


def _read_source_text_cached(path_str: str) -> str:
    """Read file text (universal-newline decoded), cached by (mtime_ns, size).

    Mirrors _read_source_cached's outer/inner split: oversize files bypass the cache entirely
    (Guard 4) and are read directly, uncached, every call.
    """
    try:
        size = _repo_map.Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return _repo_map.lang_registry.read_source_text(_repo_map.Path(path_str))
    return _repo_map._read_source_text_cached_bounded(path_str)


@_mtime_aware_cache(maxsize=_repo_map._SOURCE_READ_CACHE_MAXSIZE)
def _read_source_text_cached_bounded(path_str: str) -> str:
    return _repo_map.lang_registry.read_source_text(_repo_map.Path(path_str))


def _parser_for_source_suffix(suffix: str) -> Any | None:
    """Select the tree-sitter parser the same way each consumer picked one inline before this
    fix -- TS suffixes (incl. tsx) get the typescript parser, the rest of the JS/TS suffix set
    gets the javascript parser, Rust gets the rust parser. Returns None for any other suffix (a
    caller-side suffix pre-check, e.g. `path.suffix.lower() not in _JS_TS_SUFFIXES`, is expected to have
    already gated the call, matching the pre-fix per-site behavior)."""
    if suffix in _repo_map._TS_SUFFIXES:
        return _repo_map._typescript_parser(tsx=suffix == ".tsx")
    if suffix in _repo_map._JS_TS_SUFFIXES:
        return _repo_map._javascript_parser()
    if suffix in _repo_map._RUST_SUFFIXES:
        return _repo_map._rust_parser()
    if suffix in _repo_map._JAVA_SUFFIXES:
        return _repo_map._java_parser()
    return None


def _parse_source_uncached(path_str: str) -> tuple[str, bytes, Any] | None:
    parser = _repo_map._parser_for_source_suffix(_repo_map.Path(path_str).suffix.lower())
    if parser is None:
        return None
    try:
        source = _repo_map._read_source_text_cached(path_str)
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
        size = _repo_map.Path(path_str).stat().st_size
    except OSError:
        size = -1
    if size < 0 or size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return _repo_map._parse_source_uncached(path_str)
    return _repo_map._parsed_source_and_tree_bounded(path_str)


@_mtime_aware_cache(maxsize=_repo_map._PARSE_PRODUCT_CACHE_MAXSIZE)
def _parsed_source_and_tree_bounded(path_str: str) -> tuple[str, bytes, Any] | None:
    return _repo_map._parse_source_uncached(path_str)


def _file_may_contain_literal_symbol(path: _repo_map.Path, symbol: str) -> bool:
    normalized_symbol = (symbol or "").strip()
    if not normalized_symbol:
        return True
    try:
        size = _repo_map._file_size_with_receipt(path)
    except OSError:
        return True
    if size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return True
    try:
        data = _repo_map._read_source_cached(str(path))
    except OSError:
        return True
    if b"\0" in data[:8192]:
        return False
    return normalized_symbol.encode("utf-8") in data


def _file_may_import_symbol_definition(path: _repo_map.Path, definition_files: list[str]) -> bool:
    spec = _repo_map.lang_registry.spec_for_path(path)
    if spec is None:
        return False
    try:
        size = _repo_map._file_size_with_receipt(path)
    except OSError:
        return True
    if size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return True
    try:
        data = _repo_map._read_source_cached(str(path))
    except OSError:
        return True
    if b"\0" in data[:8192]:
        return False
    lowered = data.lower()
    if not any(marker in lowered for marker in spec.import_markers):
        return False
    if spec.language_id in ("javascript", "typescript"):
        # PERF increment 1 / Section C : a naive mirror of the branch below --
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
        return _repo_map._js_ts_has_import_bindings(str(path))
    for definition_file in definition_files:
        for alias in _repo_map._module_aliases_for_path(definition_file):
            if alias and alias.encode("utf-8") in lowered:
                return True
    return False


def _literal_symbol_seed_files(
    root: _repo_map.Path,
    symbol: str | None,
    *,
    existing_files: list[_repo_map.Path],
) -> list[_repo_map.Path]:
    normalized_symbol = (symbol or "").strip()
    if not normalized_symbol or not _repo_map._is_dir_with_receipt(root):
        return []

    normalized_root = _repo_map._resolved_input_path(root)
    existing = {str(_repo_map._safe_resolve(path)) for path in existing_files}
    seed_files: list[_repo_map.Path] = []
    scanned = 0
    for current in _repo_map._iter_repo_files(
        normalized_root, max_files=_repo_map._SYMBOL_LITERAL_SEED_SCAN_LIMIT
    ):
        if scanned >= _repo_map._SYMBOL_LITERAL_SEED_SCAN_LIMIT:
            break
        scanned += 1
        resolved = _repo_map._safe_resolve(current)
        if str(resolved) in existing or not _repo_map._literal_symbol_candidate(resolved):
            continue
        if not _repo_map._file_contains_literal_symbol(resolved, normalized_symbol):
            continue
        seed_files.append(resolved)
        if len(seed_files) >= _repo_map._SYMBOL_LITERAL_SEED_MAX_FILES:
            break
    return seed_files


def _repo_map_root_dir(repo_map: dict[str, Any]) -> _repo_map.Path:
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
    ``NotADirectoryError`` (WinError 267 on Windows, ENOTDIR on POSIX) -- the dogfood-reported
    crash on `tg defs <file> <symbol> --provider lsp/hybrid` (native is unaffected; it never reaches
    this code). Mirrors the pre-existing ``root if root.is_dir() else root.parent`` pattern already
    used by ``build_repo_map`` (``context_root``)."""
    root = _repo_map._resolved_input_path(str(repo_map["path"]))
    return root if _repo_map._is_dir_with_receipt(root) else root.parent


def _repo_map_file_and_test_universe(
    repo_map: dict[str, Any],
) -> tuple[list[_repo_map.Path], list[_repo_map.Path]]:
    """Same normalization + cross-key dedupe as ``_repo_map_file_universe`` below, but returned
    as SEPARATE (files, tests) lists so a caller can distinguish membership (F1 fix: the
    caller-scan ceiling ordering needs to know which universe members are tests) without
    re-deriving it. ``_repo_map_file_universe`` is a thin wrapper over this and its
    source-first-then-tests concatenation order is UNCHANGED -- existing global-order
    consumers (build_context_pack_from_map, edit-plan seeding) are untouched."""
    base = _repo_map._repo_map_root_dir(repo_map)
    seen: set[str] = set()
    files: list[_repo_map.Path] = []
    tests: list[_repo_map.Path] = []
    for key, bucket in (("files", files), ("tests", tests)):
        for raw_path in repo_map.get(key, []) or []:
            current = _repo_map.Path(str(raw_path)).expanduser()
            if not current.is_absolute():
                current = base / current
            normalized = _repo_map.os.path.abspath(str(current))
            if normalized in seen:
                continue
            seen.add(normalized)
            bucket.append(_repo_map.Path(normalized))
    return files, tests


def _repo_map_file_universe(repo_map: dict[str, Any]) -> list[_repo_map.Path]:
    files, tests = _repo_map._repo_map_file_and_test_universe(repo_map)
    return [*files, *tests]


def _interleave_proportionally(
    sources: list[_repo_map.Path], tests: list[_repo_map.Path]
) -> list[_repo_map.Path]:
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
    merged: list[_repo_map.Path] = []
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
    source_files: list[_repo_map.Path],
    test_files: list[_repo_map.Path],
    *,
    symbol: str | None,
    deadline_monotonic: float | None = None,
) -> list[_repo_map.Path]:
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

    def _is_literal_hit(path: _repo_map.Path) -> bool:
        return _repo_map._literal_symbol_candidate(
            path
        ) and _repo_map._file_may_contain_literal_symbol(path, normalized_symbol)

    universe = [*source_files, *test_files]
    literal_hit_ids: set[str] = set()
    for index, current in enumerate(universe):
        if index >= _repo_map.CALLER_SCAN_ORDER_PROBE_CEILING:
            break
        if (
            deadline_monotonic is not None
            and index % _repo_map._CALLER_SCAN_ORDER_PROBE_DEADLINE_STRIDE == 0
            and _repo_map.time.monotonic() >= deadline_monotonic
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
    return _repo_map._interleave_proportionally(ordered_sources, ordered_tests)


def _cap_caller_scan_files(
    files: list[_repo_map.Path],
    *,
    symbol: str | None = None,
    test_files: list[_repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[_repo_map.Path], bool]:
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

    parity fix: below the ceiling, this used to return ``files`` UNORDERED whenever a
    ``--deadline`` was supplied -- unlike ``tg importers``' reverse-candidate ordering
    (``_tier_reverse_importer_candidates``, #221), which ALWAYS orders regardless of ceiling. A
    slow-to-parse repo under the 2000-file ceiling can still blow a tight ``--deadline`` in the
    main caller-scan loop below, so a caller/blast-radius scan got NO likely-first protection at
    all in that regime and could strand a late-sorting/test-file caller past the cut -- while an
    equally-truncated ceiling-exceeded scan (the branch below) always orders first. Order here
    too whenever a deadline is actually in play and there are tests to interleave; nothing is
    DROPPED by this branch (``ceiling_exceeded`` stays ``False``), so it is a pure candidate-order
    change, invisible to a scan that completes."""
    if len(files) <= _repo_map.CALLER_SCAN_FILE_CEILING:
        if deadline_monotonic is not None and test_files:
            test_id_set = {str(current) for current in test_files}
            source_only = [current for current in files if str(current) not in test_id_set]
            ordered = _repo_map._order_caller_scan_candidates(
                source_only, test_files, symbol=symbol, deadline_monotonic=deadline_monotonic
            )
            return ordered, False
        return files, False
    if test_files:
        test_id_set = {str(current) for current in test_files}
        source_only = [current for current in files if str(current) not in test_id_set]
        ordered = _repo_map._order_caller_scan_candidates(
            source_only, test_files, symbol=symbol, deadline_monotonic=deadline_monotonic
        )
    else:
        ordered = files
    return ordered[: _repo_map.CALLER_SCAN_FILE_CEILING], True


def _is_python_dynamic_import_call(node: _repo_map.ast.Call) -> bool:
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
    if isinstance(func, _repo_map.ast.Name):
        return func.id in {"__import__", "import_module"}
    return (
        isinstance(func, _repo_map.ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, _repo_map.ast.Name)
        and func.value.id == "importlib"
    )


class _AstCacheInfo(NamedTuple):
    """Observability snapshot for `_cached_ast_parse`'s cache. `hits`/`misses` match
    `functools.lru_cache.cache_info()`'s field names (this was a plain lru_cache before);
    `evictions`/`current_bytes`/`current_entries` are new for the byte-budgeted design."""

    hits: int
    misses: int
    evictions: int
    current_bytes: int
    current_entries: int


def _cached_ast_parse(source: str) -> _repo_map.ast.Module:
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

    with _repo_map._ast_cache_lock:
        cached = _repo_map._ast_cache.get(key)
        if cached is not None:
            _repo_map._ast_cache.move_to_end(key)
            _repo_map._ast_cache_stats["hits"] += 1
            return cached[0]

    # Miss: parse OUTSIDE the lock (see docstring -- never serialize concurrent parsing).
    tree = _repo_map.parse_python(source)
    size = len(source.encode("utf-8"))
    budget = _repo_map._ast_cache_byte_budget()

    if size > _repo_map._max_parse_bytes() or size > budget:
        # Per-entry ceiling: too large to cache (or larger than the whole budget). Bypass means
        # "parse, don't store" -- the caller still gets a real, correct ast.Module.
        with _repo_map._ast_cache_lock:
            _repo_map._ast_cache_stats["misses"] += 1
        return tree

    with _repo_map._ast_cache_lock:
        raced = _repo_map._ast_cache.get(key)
        if raced is not None:
            # Another thread inserted this exact key while we were parsing above.
            _repo_map._ast_cache.move_to_end(key)
            _repo_map._ast_cache_stats["hits"] += 1
            return raced[0]

        _repo_map._ast_cache_stats["misses"] += 1
        while _repo_map._ast_cache and _repo_map._ast_cache_stats["current_bytes"] + size > budget:
            _, (_, evicted_size) = _repo_map._ast_cache.popitem(last=False)
            _repo_map._ast_cache_stats["current_bytes"] -= evicted_size
            _repo_map._ast_cache_stats["evictions"] += 1
        _repo_map._ast_cache[key] = (tree, size)
        _repo_map._ast_cache_stats["current_bytes"] += size

    return tree


def _ast_cache_info() -> _repo_map._AstCacheInfo:
    """Return a snapshot of the AST parse cache's counters (see `_AstCacheInfo`)."""
    with _repo_map._ast_cache_lock:
        return _repo_map._AstCacheInfo(
            hits=_repo_map._ast_cache_stats["hits"],
            misses=_repo_map._ast_cache_stats["misses"],
            evictions=_repo_map._ast_cache_stats["evictions"],
            current_bytes=_repo_map._ast_cache_stats["current_bytes"],
            current_entries=len(_repo_map._ast_cache),
        )


def _ast_cache_clear() -> None:
    """Clear the AST parse cache and reset its counters (mirrors `lru_cache.cache_clear()`)."""
    with _repo_map._ast_cache_lock:
        _repo_map._ast_cache.clear()
        _repo_map._ast_cache_stats["hits"] = 0
        _repo_map._ast_cache_stats["misses"] = 0
        _repo_map._ast_cache_stats["evictions"] = 0
        _repo_map._ast_cache_stats["current_bytes"] = 0


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
    spec = _repo_map.lang_registry.spec_for_path(path)
    if spec is None:
        return "heuristic"
    if spec.parser_for_path is None:
        return spec.provenance_when_parsed
    return (
        spec.provenance_when_parsed
        if spec.parser_for_path(_repo_map.Path(path)) is not None
        else spec.provenance_when_missing
    )


_cached_ast_parse.cache_info = _ast_cache_info  # type: ignore[attr-defined]
_cached_ast_parse.cache_clear = _ast_cache_clear  # type: ignore[attr-defined]
