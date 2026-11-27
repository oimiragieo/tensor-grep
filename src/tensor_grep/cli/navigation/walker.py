from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any, Literal, NamedTuple

from tensor_grep.cli import repo_map as _repo_map


def _repo_context_cache_max_roots() -> int:
    return _repo_map._configured_positive_int(
        _repo_map._REPO_CONTEXT_CACHE_MAX_ROOTS_ENV,
        _repo_map._DEFAULT_REPO_CONTEXT_CACHE_MAX_ROOTS,
    )


def _max_parse_bytes() -> int:
    """Return the per-file byte cap for AST parsing (O2)."""
    return _repo_map._configured_positive_int(
        _repo_map._MAX_PARSE_BYTES_ENV, _repo_map._DEFAULT_MAX_PARSE_BYTES
    )


def _ast_cache_byte_budget() -> int:
    """Return the total-resident-bytes budget for the AST parse cache."""
    return _repo_map._configured_positive_int(
        _repo_map._AST_CACHE_BYTES_ENV, _repo_map._DEFAULT_AST_CACHE_BYTES
    )


def _remember_repo_context(
    cache: OrderedDict[str, dict[str, Any]],
    key: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not _repo_map.content_receipts_active():
        cache.pop(key, None)
        cache[key] = context
        while len(cache) > _repo_map._repo_context_cache_max_roots():
            cache.popitem(last=False)
    return context


def _get_repo_context_cache_entry(
    cache: OrderedDict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any] | None:
    cached = None if _repo_map.content_receipts_active() else cache.pop(key, None)
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
    def __init__(self, collector: _repo_map._ProfileCollector, name: str) -> None:
        self._collector = collector
        self._name = name
        self._start = 0.0

    def __enter__(self) -> None:
        self._collector._push(self._name)
        self._start = _repo_map.time.perf_counter()
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        try:
            elapsed = max(0.0, _repo_map.time.perf_counter() - self._start)
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

    def phase(self, name: str) -> _repo_map._ProfilePhase | nullcontext[None]:
        if not self.enabled:
            return nullcontext()
        return _repo_map._ProfilePhase(self, name)

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
    collector: _repo_map._ProfileCollector | None,
    name: str,
) -> _repo_map._ProfilePhase | nullcontext[None]:
    if collector is None:
        return nullcontext()
    return collector.phase(name)


def _resolve_profiling_collector(
    *,
    profile: bool,
    collector: _repo_map._ProfileCollector | None,
) -> _repo_map._ProfileCollector | None:
    if collector is not None:
        return collector
    if profile:
        return _repo_map._ProfileCollector()
    return None


def _attach_profiling(
    payload: dict[str, Any],
    collector: _repo_map._ProfileCollector | None,
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
    return "-".join(sorted(_repo_map.lang_registry.LANGUAGE_REGISTRY))


def _symbol_navigation_descriptor() -> str:
    """Dynamically derive the honest ``coverage.symbol_navigation`` value, split into the two
    real navigation tiers instead of one flat language list (same honesty-bug fix as
    ``_language_scope_descriptor`` above -- see that function's docstring for the incident).

    - ``parser-backed-refs-callers``: languages whose ``LanguageSpec.references_and_calls`` is
      wired up get AST/tree-sitter-VERIFIED ``tg refs``/``tg callers``/``tg blast-radius``
      (resolved through ``_references_and_calls_for_path``, which reads
      ``spec.references_and_calls`` rather than re-deriving membership by
      ``language_id``). Verified live: this tier currently also includes go, which several
      PR-comment summaries lump in with the "foundational-only" languages below -- that
      undercounts it. Go's own dedicated ``lang_go.go_references_and_calls`` is a full
      tree-sitter extractor (package-alias resolution, node-type-based ref_kind), not a
      regex fallback, so it belongs here.
    - ``foundational-defs-imports-only``: languages with ``references_and_calls is None``
      still get parser-backed defs/imports (see ``_imports_and_symbols_for_path``'s
      fail-closed per-language branches -- NO regex fallback, an unparseable file becomes an
      honest ``resolution_gaps`` entry), but ``tg refs``/``tg callers``/``tg blast-radius``
      fall through to the generic ``_regex_references_and_calls`` text heuristic instead of an
      AST-verified match -- these languages' own registration comments in this module
      self-label "FOUNDATIONAL-TIER" for exactly this reason. As of Task 10E (C++, the final
      wave of the top-10 language-support campaign) this tier is EMPTY -- every registered
      language carries a real ``references_and_calls`` extractor (see
      ``test_every_registered_language_is_parser_backed_after_the_final_wave``). The segment is
      still ALWAYS emitted (never omitted when empty, unlike an earlier version of this function)
      so a consumer can rely on the descriptor always containing exactly one ``"+"`` and both
      prefixes -- ``test_cpp_moves_into_the_parser_backed_tier_descriptor`` partitions on that
      literal shape.

    Both groups are derived live from ``LANGUAGE_REGISTRY`` and sorted, so a newly onboarded
    language lands in the correct bucket automatically the moment its ``LanguageSpec`` is
    registered, without a repeat of the drift ``_language_scope_descriptor`` fixes.
    """
    parser_backed = sorted(
        language_id
        for language_id, spec in _repo_map.lang_registry.LANGUAGE_REGISTRY.items()
        if spec.references_and_calls is not None
    )
    foundational = sorted(
        language_id
        for language_id, spec in _repo_map.lang_registry.LANGUAGE_REGISTRY.items()
        if spec.references_and_calls is None
    )
    parts = [
        f"parser-backed-refs-callers:{'-'.join(parser_backed)}",
        f"foundational-defs-imports-only:{'-'.join(foundational)}",
    ]
    return "+".join(parts)


def _envelope(path: _repo_map.Path) -> dict[str, Any]:
    return {
        "version": _repo_map.JSON_OUTPUT_VERSION,
        "schema_version": _repo_map.JSON_OUTPUT_VERSION,
        "routing_backend": _repo_map.ROUTING_BACKEND,
        "routing_reason": _repo_map.ROUTING_REASON,
        "sidecar_used": False,
        "coverage": {
            "language_scope": _repo_map._language_scope_descriptor(),
            "symbol_navigation": _repo_map._symbol_navigation_descriptor(),
            "test_matching": "filename+import+graph-heuristic",
        },
        "path": str(path),
    }


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


def _stamp_partial_status(
    payload: dict[str, Any],
    *,
    deadline_exceeded: bool = False,
    emit_reason: bool = True,
) -> None:
    if not payload.get("partial") and not deadline_exceeded:
        return
    payload["partial"] = True
    limit = dict(payload.get("deadline_limit") or {})
    source_coverage = bool(payload.get("symbol_cache_coverage"))
    actual_deadline = (
        deadline_exceeded
        or bool(limit.get("deadline_exceeded"))
        or (not source_coverage and "deadline_exceeded" not in limit)
    )
    limit["deadline_exceeded"] = actual_deadline
    payload["deadline_limit"] = limit
    if emit_reason or "partial_reason" in payload:
        payload["partial_reason"] = "deadline" if actual_deadline else "source_coverage"


def _copy_partial_signal(payload: dict[str, Any], source: dict[str, Any]) -> None:
    """capability P0-6 step 2: carry the deadline PARTIAL signal forward when a symbol builder repackages a
    build_repo_map / build_symbol_defs result into its own payload. Without this, a deadline-truncated
    map silently loses partial:true + deadline_limit the moment it is wrapped, so the agent sees a
    small result with no signal it was cut short. Only propagates when the source was actually
    partial (a complete result carries neither key -- parity). Kept separate from _copy_scan_limit:
    scan_limit is the file-cap fact, partial is the time-budget outcome."""
    coverage = source.get("symbol_cache_coverage")
    parse_cap_only = isinstance(coverage, dict) and coverage.get("parse_cap_only") is True
    symbol_answer = str(payload.get("routing_reason", "")).startswith("symbol-")
    actual_deadline = any(
        isinstance(item.get("deadline_limit"), dict)
        and item["deadline_limit"].get("deadline_exceeded")
        for item in (payload, source)
    )
    scan_truncated = any(
        isinstance(item.get("scan_limit"), dict) and item["scan_limit"].get("possibly_truncated")
        for item in (payload, source)
    )
    if symbol_answer and parse_cap_only and not actual_deadline and not scan_truncated:
        # Empty-answer coverage gates judge parse-cap gaps for symbol answers. Raw maps
        # and context/capsule source coverage retain their partial signal.
        payload.pop("partial", None)
        payload.pop("partial_reason", None)
        payload.pop("deadline_limit", None)
    elif source.get("partial"):
        payload["partial"] = True
        deadline_limit = source.get("deadline_limit")
        if isinstance(deadline_limit, dict):
            prior_limit = dict(payload.get("deadline_limit") or {})
            payload["deadline_limit"] = {**prior_limit, **deadline_limit}
            if prior_limit.get("deadline_exceeded"):
                payload["deadline_limit"]["deadline_exceeded"] = True
    if isinstance(source.get("symbol_cache"), dict):
        payload["symbol_cache"] = {
            key: value
            for key, value in source["symbol_cache"].items()
            if key
            in {
                "schema_version",
                "freshness_route",
                "trusted_notifications",
                "entry_authentication",
                "snapshot_consistency",
            }
        }
    if isinstance(source.get("symbol_cache_coverage"), dict):
        payload["symbol_cache_coverage"] = dict(source["symbol_cache_coverage"])
        _repo_map._stamp_partial_status(payload)


def _scan_did_not_finish(payload: dict[str, Any]) -> bool:
    """Whether this payload's SCAN stopped early -- by EITHER truncation cause (task 332).

    The file-count cap (``scan_limit.possibly_truncated``) and the ``--deadline`` cutoff
    (``partial`` / ``deadline_limit.deadline_exceeded``) are two causes of ONE fact: we stopped
    looking before we were done, so an empty or small result proves nothing.

    Every gate in this module that asked "was this truncated?" was written when the file cap was
    the only cause, and none was widened when ``--deadline`` arrived. A mechanical sweep found
    3 of 3 ``possibly_truncated`` readers here answering the question count-only, so a
    deadline-truncated scan read as COMPLETE at all three -- an external dogfood of the published
    1.99.4 wheel caught `tg callers . <symbol> --deadline 0.1 --json` reporting
    ``files_scanned: 0`` beside a flat "No exact definition found" for a symbol that demonstrably
    exists.

    This deliberately MIRRORS ``main._scan_incomplete``, which is where the same two-cause
    contract is already defined for the exit-code gate -- it is not a fourth independent notion.
    Re-deriving truncation narrowly is the entire defect class; adding another private definition
    would guarantee the next drift.

    An OUTPUT cap is NOT a scan truncation (a complete analysis capped for display stays
    complete), so no ``output_limit`` key is consulted here -- same boundary ``_scan_incomplete``
    draws, and the reason its docstring gives for drawing it.
    """
    scan_limit = payload.get("scan_limit")
    if isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated"):
        return True
    deadline_limit = payload.get("deadline_limit")
    if isinstance(deadline_limit, dict) and deadline_limit.get("deadline_exceeded"):
        return True
    return bool(payload.get("partial"))


def _deadline_monotonic_from_seconds(deadline_seconds: float | None) -> float | None:
    """Convert a relative --deadline (seconds-from-now) to an ABSOLUTE time.monotonic() timestamp,
    computed ONCE at the top of a builder so that multiple internal build_repo_map calls (e.g. the
    blast-radius literal-seed retry) SHARE one budget instead of each getting a fresh N seconds."""
    if deadline_seconds is None:
        return None
    return _repo_map.time.monotonic() + deadline_seconds


class _TestScanCounts:
    """opt10 #3: how much of the test universe ``_context_tests`` actually scanned.

    DELIBERATELY NOT a field on ``_DeadlineBreakFlag``. That object is this module's
    general-purpose mutable out-signal and ``tg callers`` reports 23 consumers across six modules
    (agent_capsule, codemap, docs_coverage, inventory, main, repo_map), so widening it for one
    caller's benefit would be a 23-consumer change serving a 3-consumer need. This object is passed
    only where the counts are wanted; its blast radius is one.

    WHY THE COUNTS EXIST: ``partial`` / ``deadline_limit.deadline_exceeded`` are SHARED booleans
    that any of this module's 24 ``time.monotonic`` readers can set, so a test asserting them
    cannot say WHICH stage ran out of budget -- which is how three tests named for the
    ``_context_tests`` deadline passed on a baseline where ``_context_tests`` accepted no deadline
    at all. Nothing else scans test candidates, so this pair attributes the stop.
    """

    __slots__ = ("scanned", "total")

    def __init__(self) -> None:
        self.scanned = 0
        self.total = 0


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


class _UnreadablePathFlag:
    """Task #276 slice 1: mutable out-signal for `_iter_repo_files`/`_iter_repo_bucket_files`.

    Both walkers wrap their `os.scandir()` calls in a bare ``except OSError: return`` /
    ``continue`` -- the default-safe choice for a walk that must not crash on one bad
    directory, but it ALSO means a permission-denied (or since-deleted) subtree simply
    disappears from the file list with no signal at all: `tg orient`'s central-file/entry-point
    ranking (and every other `build_repo_map` consumer) could silently miss a whole subtree.
    Mirrors `_DeadlineBreakFlag`'s mutable-out-param pattern so existing callers that don't pass
    one (the majority of `_iter_repo_files`'s ~12 call sites) are unaffected -- `None` is a
    complete no-op, byte-identical to the walk's pre-fix behavior.
    """

    __slots__ = ("count", "hit", "sample")

    def __init__(self) -> None:
        self.hit = False
        self.count = 0
        self.sample: list[str] = []

    def record(self, exc: OSError) -> None:
        self.hit = True
        self.count += 1
        if len(self.sample) < _repo_map._MAX_REPO_WALK_UNREADABLE_PATH_SAMPLE:
            offending_path = getattr(exc, "filename", None) or str(exc)
            self.sample.append(str(offending_path))


def _repo_walk_dir_sort_key(name: str) -> tuple[int, str]:
    normalized = name.lower()
    if normalized in _repo_map._SOURCE_FIRST_DIR_NAMES:
        return (0, normalized)
    if normalized in _repo_map._TEST_DIR_NAMES:
        return (1, normalized)
    return (2, normalized)


def _repo_walk_file_sort_key(name: str) -> tuple[int, str]:
    suffix = _repo_map.Path(name).suffix.lower()
    if suffix in _repo_map._SOURCE_FIRST_SUFFIXES:
        return (0, name.lower())
    return (1, name.lower())


def _repo_walk_path_sort_key(
    path: _repo_map.Path, root: _repo_map.Path
) -> tuple[int, int, int, str]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    try:
        group_parts = relative.parts if path.is_dir() else relative.parts[:-1]
    except OSError:
        group_parts = relative.parts[:-1]
    normalized_group_parts = {part.lower() for part in group_parts}
    if normalized_group_parts & _repo_map._SOURCE_FIRST_DIR_NAMES:
        group = 0
    elif normalized_group_parts & _repo_map._TEST_DIR_NAMES:
        group = 1
    elif len(relative.parts) == 1 and not path.is_dir():
        group = 2
    else:
        group = 3
    file_group, file_name = _repo_map._repo_walk_file_sort_key(path.name)
    return (group, file_group, len(relative.parts), relative.as_posix().lower() or file_name)


def _repo_walk_bucket_sort_key(path: _repo_map.Path, root: _repo_map.Path) -> tuple[int, str]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    path_group = _repo_map._repo_walk_path_sort_key(path, root)[0]
    top_level = relative.parts[0].lower() if relative.parts else path.name.lower()
    return (path_group, top_level)


def _should_skip_repo_dir(path: _repo_map.Path) -> bool:
    name = path.name.lower()
    if name in _repo_map._SKIP_DIR_NAMES:
        return True
    if name == "context" and path.parent.name.lower() == ".claude":
        return True
    return name.startswith((
        ".tmp_",
        ".tmp-",  # covers .tmp-ci, .tmp-ci-123, etc.
        "tg-agent-gpu-probe",
        "tg-doctor-gpu-probe",
    ))


def _path_has_vendor_component(path: _repo_map.Path, root: _repo_map.Path) -> bool:
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
        if part_lower in _repo_map._VENDOR_CACHE_DIR_COMPONENTS:
            return True
        if part_lower.startswith((".tmp_", ".tmp-")):
            return True
    return False


def _scan_limit_cause(
    files: list[_repo_map.Path],
    root: _repo_map.Path,
    capped_file_count: int,
    max_files: int,
) -> str:
    """Classify why the file-scan cap was reached.

    Returns ``'project-files'`` when the cap was reached because there are
    more non-vendored project files than the limit, or ``'vendor-cache'`` when
    every file at or over the cap boundary belongs to a vendor/cache subtree.

    The heuristic: if ALL files in the returned list are vendor-path files,
    the entire quota was consumed by vendor dirs â€” a real truncation warning
    would be misleading.  If any file is a project file, we conservatively
    report ``'project-files'``.
    """
    if capped_file_count < max_files:
        # Cap was not actually reached â€” caller shouldn't be calling this, but
        # return a safe value anyway.
        return "project-files"
    project_file_found = any(not _repo_map._path_has_vendor_component(f, root) for f in files)
    return "project-files" if project_file_found else "vendor-cache"


def _stack_ignored(
    path: _repo_map.Path, *, is_dir: bool, stack: tuple[_repo_map._GitignoreMatcher, ...]
) -> bool:
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
    root: _repo_map.Path,
    ancestor_stack: tuple[_repo_map._GitignoreMatcher, ...] = (),
    *,
    unreadable_hit: _repo_map._UnreadablePathFlag | None = None,
) -> Iterator[_repo_map.Path]:
    # Load this directory's matcher so nested .gitignore rules override ancestors.
    own = _repo_map._load_gitignore_matcher(str(root))
    stack = (*ancestor_stack, own) if own.has_rules else ancestor_stack
    try:
        entries = list(_repo_map.os.scandir(root))
    except OSError as exc:
        if unreadable_hit is not None:
            unreadable_hit.record(exc)
        return

    dir_paths: list[_repo_map.Path] = []
    file_paths: list[_repo_map.Path] = []
    for entry in entries:
        try:
            is_reparse_point = _repo_map.os.name == "nt" and bool(
                getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
            )
            if not is_reparse_point and entry.is_dir(follow_symlinks=False):
                directory = _repo_map.Path(entry.path)
                if _repo_map._should_skip_repo_dir(directory):
                    continue
                if _repo_map._stack_ignored(directory, is_dir=True, stack=stack):
                    continue
                dir_paths.append(directory)
            elif not is_reparse_point and entry.is_file(follow_symlinks=False):
                file_path = _repo_map.Path(entry.path)
                if _repo_map._stack_ignored(file_path, is_dir=False, stack=stack):
                    continue
                file_paths.append(file_path)
        except OSError as exc:
            if unreadable_hit is not None:
                unreadable_hit.record(exc)
            continue

    dir_paths.sort(key=lambda path: _repo_map._repo_walk_dir_sort_key(path.name))
    file_paths.sort(key=lambda path: _repo_map._repo_walk_file_sort_key(path.name))
    source_dirs = [
        path for path in dir_paths if _repo_map._repo_walk_dir_sort_key(path.name)[0] <= 1
    ]
    other_dirs = [path for path in dir_paths if _repo_map._repo_walk_dir_sort_key(path.name)[0] > 1]

    for directory in source_dirs:
        yield from _repo_map._iter_repo_bucket_files(
            directory, stack, unreadable_hit=unreadable_hit
        )
    yield from file_paths
    for directory in other_dirs:
        yield from _repo_map._iter_repo_bucket_files(
            directory, stack, unreadable_hit=unreadable_hit
        )


def _iter_repo_files(
    root: _repo_map.Path,
    *,
    max_files: int | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
    unreadable_hit: _repo_map._UnreadablePathFlag | None = None,
    exclude_files: frozenset[_repo_map.Path] = frozenset(),
) -> list[_repo_map.Path]:
    """Walk with count/deadline bounds, excluding exact paths before the cap.

    Mutable signals preserve incomplete reasons; paths are normalized once, never per entry.
    """
    with _repo_map._profiling_phase(_profiling_collector, "file_walk"):
        authenticated_files = _repo_map._authenticated_scan_paths_under(root, exclude_files)
        if authenticated_files is not None:
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return []
            return (
                authenticated_files[: max(1, max_files)]
                if max_files is not None
                else authenticated_files
            )
        if root.is_file():
            current = root.resolve()
            return [] if current in exclude_files else [current]

        files: list[_repo_map.Path] = []
        normalized_root = root.resolve()
        gitignore = _repo_map._load_gitignore_matcher(str(normalized_root))
        if max_files is not None:
            try:
                entries = list(_repo_map.os.scandir(normalized_root))
            except OSError as exc:
                if unreadable_hit is not None:
                    unreadable_hit.record(exc)
                return []

            buckets: list[tuple[tuple[int, str], Iterator[_repo_map.Path]]] = []
            for entry in entries:
                try:
                    is_reparse_point = _repo_map.os.name == "nt" and bool(
                        getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
                    )
                    if not is_reparse_point and entry.is_dir(follow_symlinks=False):
                        path = _repo_map.Path(entry.path)
                        if _repo_map._should_skip_repo_dir(path):
                            continue
                        if gitignore.is_ignored(path, is_dir=True):
                            continue
                        buckets.append((
                            _repo_map._repo_walk_bucket_sort_key(path, normalized_root),
                            _repo_map._iter_repo_bucket_files(
                                path, (gitignore,), unreadable_hit=unreadable_hit
                            ),
                        ))
                    elif not is_reparse_point and entry.is_file(follow_symlinks=False):
                        path = _repo_map.Path(entry.path)
                        if gitignore.is_ignored(path, is_dir=False):
                            continue
                        buckets.append((
                            _repo_map._repo_walk_bucket_sort_key(path, normalized_root),
                            iter([path]),
                        ))
                except OSError as exc:
                    if unreadable_hit is not None:
                        unreadable_hit.record(exc)
                    continue

            selected: list[_repo_map.Path] = []
            limit = max(1, max_files)
            bucket_groups: dict[int, list[tuple[tuple[int, str], Iterator[_repo_map.Path]]]] = {}
            for key, iterator in buckets:
                bucket_groups.setdefault(key[0], []).append((key, iterator))
            walk_deadline_exceeded = False
            for group in sorted(bucket_groups):
                active_buckets = sorted(bucket_groups[group], key=lambda item: item[0])
                while active_buckets and len(selected) < limit:
                    next_buckets: list[tuple[tuple[int, str], Iterator[_repo_map.Path]]] = []
                    for key, iterator in active_buckets:
                        # Consume exclusions within this turn to preserve bucket fairness.
                        # Check every advance so an excluded prefix cannot evade the deadline.
                        while True:
                            if (
                                deadline_monotonic is not None
                                and _repo_map.time.monotonic() >= deadline_monotonic
                            ):
                                walk_deadline_exceeded = True
                                break
                            try:
                                current = next(iterator)
                            except StopIteration:
                                break
                            if current in exclude_files:
                                continue
                            selected.append(current)
                            if len(selected) < limit:
                                next_buckets.append((key, iterator))
                            break
                        if walk_deadline_exceeded or len(selected) >= limit:
                            break
                    if walk_deadline_exceeded:
                        break
                    active_buckets = next_buckets
                if len(selected) >= limit or walk_deadline_exceeded:
                    break
            if walk_deadline_exceeded and deadline_hit is not None:
                deadline_hit.hit = True
            return selected

        walk_deadline_exceeded = False
        for current in _repo_map._iter_repo_bucket_files(
            normalized_root, (), unreadable_hit=unreadable_hit
        ):
            if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
                walk_deadline_exceeded = True
                break
            if current not in exclude_files:
                files.append(current)
        if walk_deadline_exceeded and deadline_hit is not None:
            deadline_hit.hit = True
        files.sort(key=lambda path: _repo_map._repo_walk_path_sort_key(path, normalized_root))
        return files


def _safe_resolve(path: _repo_map.Path) -> _repo_map.Path:
    try:
        return _repo_map._resolved_input_path(path)
    except OSError:
        return path


def _looks_like_binary_file(path: _repo_map.Path) -> bool:
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


def _is_hidden_non_code_file(path: _repo_map.Path, root: _repo_map.Path) -> bool:
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
    return path.suffix.lower() not in _repo_map._CODE_CONTEXT_SUFFIXES


def _is_repo_context_file(path: _repo_map.Path, root: _repo_map.Path) -> bool:
    suffix = path.suffix.lower()
    if suffix not in _repo_map._CONTEXT_FILE_SUFFIXES:
        return False
    if suffix in _repo_map._NON_CODE_HIDDEN_SUFFIXES:
        return False
    if _repo_map._is_hidden_non_code_file(path, root):
        return False
    return True


def _path_is_relative_to(path: _repo_map.Path, parent: _repo_map.Path) -> bool:
    """Check containment lexically for signed inputs and fail closed on live resolution errors."""
    try:
        _repo_map._resolved_input_path(path).relative_to(_repo_map._resolved_input_path(parent))
    except (ValueError, OSError):
        return False
    return True


def _precomputed_validation_files_for_root(
    root: _repo_map.Path,
    file_paths: list[str | _repo_map.Path] | None,
    *,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
    unreadable_hit: _repo_map._UnreadablePathFlag | None = None,
) -> list[_repo_map.Path] | None:
    """#639 review nit 1 (dogfood #1 RESIDUAL): this loop does one filesystem ``Path.resolve()``
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
    normalized_root = _repo_map._resolved_input_path(root)
    selected: list[_repo_map.Path] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        if deadline_monotonic is not None and _repo_map.time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        current = _repo_map.Path(str(raw_path)).expanduser()
        if not current.is_absolute():
            current = normalized_root / current
        resolve_error: OSError | None = None
        try:
            resolved = _repo_map._resolved_input_path(current)
        except OSError as exc:
            # The `absolute()` fallback keeps this loop from crashing, but MEASURED: it does not
            # save the entry. `_path_is_relative_to` below re-resolves the path, hits the same
            # OSError, and fails closed -- so a resolve failure here ALWAYS costs the file.
            #
            # An earlier draft of this comment claimed the fallback was usually harmless and that
            # only a boundary-crossing symlink lost anything. The control-arm test falsified that
            # in one run. Do not restore that reasoning without re-running it.
            resolved = current.absolute()
            resolve_error = exc
        if not _repo_map._path_is_relative_to(resolved, normalized_root):
            if resolve_error is not None and unreadable_hit is not None:
                # Task #291. THIS is the only branch where the resolve failure actually costs a
                # file. `absolute()` skipped symlink resolution, so a link crossing the root
                # boundary is judged on its unresolved form and dropped here -- silently, from a
                # list that feeds the agent's "run these to validate your edit" plan. An agent
                # then runs a SHORTER suite and believes it validated.
                #
                # Recorded HERE rather than in the except arm on purpose: recording at the except
                # would flag every benign resolve failure as data loss and drown the real signal.
                # The distinction is what the control arm in the test pins.
                unreadable_hit.record(resolve_error)
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
    validation_root: _repo_map.Path | None = None,
) -> list[str | _repo_map.Path] | None:
    if validation_root is not None:
        try:
            map_root = _repo_map._resolved_input_path(str(repo_map.get("path", ".")))
            resolved_validation_root = _repo_map._resolved_input_path(validation_root)
        except (OSError, RuntimeError):
            return None
        if not _repo_map._is_dir_with_receipt(map_root):
            map_root = map_root.parent
        if map_root != resolved_validation_root and not _repo_map._path_is_relative_to(
            resolved_validation_root,
            map_root,
        ):
            return None
    raw_paths = list(repo_map.get("related_paths", [])) or [
        *list(repo_map.get("files", [])),
        *list(repo_map.get("tests", [])),
    ]
    return [_repo_map.Path(str(current)) for current in raw_paths if str(current or "")]


def _literal_symbol_candidate(path: _repo_map.Path) -> bool:
    return path.suffix.lower() in _repo_map._SOURCE_FIRST_SUFFIXES


def _file_contains_literal_symbol(path: _repo_map.Path, symbol: str) -> bool:
    try:
        size = _repo_map._file_size_with_receipt(path)
    except (FileNotFoundError, NotADirectoryError):
        _repo_map.record_content_absence(path)
        return False
    except OSError:
        _repo_map.record_content_receipt_digest(path, "")
        return False
    if size > _repo_map._SYMBOL_LITERAL_SEED_MAX_BYTES:
        return False
    try:
        data = _repo_map._read_source_cached(str(path))
    except OSError:
        return False
    if b"\0" in data[:8192]:
        return False
    return symbol.encode("utf-8") in data
