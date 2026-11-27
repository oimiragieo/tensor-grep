from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import citations as _citations
from tensor_grep.cli._main_binding import _self

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.
# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
from tensor_grep.cli.error_handling import _emit_search_error_json
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import _deadline_option, _safe_stdout_line
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.core import retrieval_symbols
from tensor_grep.core.reranker import route_labels, select_deduplicated_find_matches
from tensor_grep.core.retrieval_chunker import MAX_CHUNKS
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

if TYPE_CHECKING:
    from tensor_grep.core.result import SearchResult
    from tensor_grep.core.retrieval_chunker import Chunk

# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.

# --- from cli/ast_scan.py ----------------------------------------------------

# --- from cli/doctor_report.py -----------------------------------------------

# --- from cli/doctor_payload.py ----------------------------------------------

# --- from cli/native_frontdoor.py --------------------------------------------

# --- from cli/windows_launcher.py --------------------------------------------

# backlog #1 : kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 3
_DOCTOR_LSP_SCHEMA_VERSION = 2
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)

if TYPE_CHECKING:
    from tensor_grep.core.result import SearchResult
    from tensor_grep.core.retrieval_chunker import Chunk

_SEMANTIC_CORPUS_CHUNK_CAP = MAX_CHUNKS


def _apply_semantic_rerank(all_results: SearchResult, pattern: str) -> SearchResult:
    """Apply the `--semantic` hybrid (BM25 + dense RRF [+ late MaxSim]) rerank, fail-closed to
    BM25-only.

    The dense leg is best-effort: when the `semantic` extra is absent, the model has not been
    fetched, the model produces a malformed/mismatched embedding, or a query-time dense fault
    occurs (F1: e.g. a dim mismatch raised from inside `rerank_hybrid`'s call to
    `DenseIndex.query`), this degrades VISIBLY to a BM25-only rerank (stderr warning +
    ``rank_fallback_reason`` set) -- it never silently returns unranked output and never mislabels
    BM25-only output as "semantic". A genuine backend fault (e.g. a corrupt model directory)
    raises ``BackendExecutionError`` instead of degrading, per the Backend Fail-Closed Contract --
    that is NOT caught here; the caller (the `search` command) must catch it and exit cleanly
    (F4).

    T5/T6 (design doc "The seam" + "Fail-closed contract"): when ``TG_LATE_RERANK=1`` is set, a
    late-interaction (MaxSim) reranker is built here (``late_available()`` probe, then
    ``load_late_reranker()``) and passed into the PRIMARY ``rerank_hybrid`` call only -- never
    into any of the BM25-only degrade retries below, since those already mean the whole hybrid
    stage bypassed the late stage too (each appends "; late rerank skipped" to its
    ``rank_fallback_reason`` when late rerank was requested). A RECOVERABLE late-leg failure
    (extra absent, model not fetched) degrades here exactly like the dense leg; an UNRECOVERABLE
    ``BackendExecutionError`` (e.g. a corrupt model directory) deliberately propagates, same as
    the dense leg's.
    """
    from tensor_grep.core.reranker import rerank_hybrid
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import chunk_file
    from tensor_grep.core.retrieval_dense import (
        DenseIndex,
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )

    late_rerank_requested = os.environ.get("TG_LATE_RERANK") == "1"

    def _maybe_append_late_skip(reason: str) -> str:
        """The 3 upstream degrade paths below all BYPASS the late stage entirely (it is only ever
        wired into the PRIMARY rerank_hybrid call further down) -- when late rerank was
        requested, say so explicitly rather than leaving the envelope silently ambiguous about
        why no late reorder happened (T6)."""
        return f"{reason}; late rerank skipped" if late_rerank_requested else reason

    dense_index = None
    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")

    # F3 : build the chunk corpus ONCE and share it between the BM25 and dense
    # legs. Previously the dense leg's corpus was built here while the BM25 leg rebuilt its own
    # corpus from scratch inside `rerank_hybrid` (bm25_index=None) -- a second full file-I/O pass,
    # and a silent RRF-misalignment risk if the two passes' chunk_size/overlap defaults ever
    # diverge.
    chunks: list[Chunk] = []
    for path in all_results.matched_file_paths:
        try:
            file_chunks = chunk_file(path)
        except RuntimeError as exc:
            # F5: retrieval_chunker's own MAX_CHUNKS guard is PER FILE; a single pathological file
            # can still trip it on its own even before the corpus-wide cap below is reached. Either
            # way, degrade to BM25-only (using whatever we already have -- discard the corpus, let
            # rerank_hybrid rebuild its own BM25-only chunks) rather than crash the whole search.
            all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
            sys.stderr.write(f"tg: {exc}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )
        chunks.extend(file_chunks)
        if len(chunks) > _self._SEMANTIC_CORPUS_CHUNK_CAP:
            reason = (
                "semantic ranking unavailable: corpus chunk cap "
                f"({_self._SEMANTIC_CORPUS_CHUNK_CAP}) exceeded across the matched file set -- narrow "
                "the search to fewer files for a semantic rerank"
            )
            all_results.rank_fallback_reason = _maybe_append_late_skip(reason)
            sys.stderr.write(f"tg: {reason}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )

    bm25_index = Bm25Index(chunks)

    if available:
        try:
            model = load_dense_model(default_model_dir())
            dense_index = DenseIndex(chunks, model)
        except DenseUnavailableError as exc:
            # v1.92.1 dogfood item 3: rewrite the raw module-CLI fetch hint into the friendly
            # `tg install-dense` one-shot -- mirrors `tg find`'s identical treatment below
            # (`_friendly_dense_unavailable_message`); previously only `tg find` got this, so
            # `tg search --semantic`'s "model not fetched" degrade still showed the raw
            # `python -m tensor_grep.core.retrieval_dense --fetch` command. A no-op for any
            # DenseUnavailableError that doesn't carry the raw hint (e.g. a malformed-shape
            # message never mentions fetch).
            message = _friendly_dense_unavailable_message(exc)
            all_results.rank_fallback_reason = message
            sys.stderr.write(f"tg: {message}\n")
        # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: that is
        # an unrecoverable fault the CLI boundary must catch and exit on (F4), not degrade here.

    late_reranker = None
    if late_rerank_requested:
        from tensor_grep.core.retrieval_late import (
            LateRerankUnavailableError,
            late_available,
            load_late_reranker,
        )

        late_ok, late_reason = late_available()
        if not late_ok:
            _note_late_rerank_degraded(all_results, late_reason or "late rerank unavailable")
        else:
            try:
                late_reranker = load_late_reranker()
            except LateRerankUnavailableError as exc:
                _note_late_rerank_degraded(all_results, str(exc))
            # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: an
            # unrecoverable fault the CLI boundary must catch and exit on, not degrade here --
            # mirrors the dense leg immediately above.

    try:
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=dense_index,
            late_reranker=late_reranker,
        )
    except DenseUnavailableError as exc:
        # F1: a query-time dense fault (e.g. a dim mismatch) is raised from INSIDE rerank_hybrid's
        # call to `DenseIndex.query`, outside the try/except above (which only guards index
        # construction). Degrade to BM25-only here too -- reuse the SAME bm25_index (no second
        # chunk pass) rather than let it traceback. This also BYPASSES the late stage (it is only
        # ever wired into the primary call above, never into this retry).
        all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
        sys.stderr.write(f"tg: {exc}\n")
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=None,
        )


_FIND_CORPUS_CHUNK_CAP = MAX_CHUNKS


_DENSE_FETCH_RAW_HINT = "python -m tensor_grep.core.retrieval_dense --fetch"


_DENSE_FETCH_FRIENDLY_HINT = "tg install-dense"


def _friendly_dense_unavailable_message(exc: BaseException) -> str:
    """CLI-boundary rewrite of the dense leg's raw fetch-command hint into the one-shot
    `tg install-dense` command  a real `tg find` user should run instead.

    Deliberately a string substitution at the CALL SITE, not an edit to the library exception
    message itself (`retrieval_dense.py`'s `DenseUnavailableError` "not fetched" message is pinned
    verbatim -- including the raw `python -m tensor_grep.core.retrieval_dense --fetch` command --
    by `test_retrieval_dense_fetch.py::test_not_fetched_error_names_the_new_fetch_command`, which
    documents that message as the module-CLI-only front door). A no-op when the raw hint is absent
    (e.g. a dim-mismatch or malformed-shape `DenseUnavailableError`, which never mentions fetch).
    """
    return str(exc).replace(_DENSE_FETCH_RAW_HINT, _DENSE_FETCH_FRIENDLY_HINT)


_FIND_DENSE_WEIGHT_ENV = "TG_FIND_DENSE_WEIGHT"


_FIND_DENSE_WEIGHT_DEFAULT = 1.0


_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT = 5.0


def _find_dense_weight(query: str) -> float:
    """Query-adaptive `dense_weight` for `tg find`'s `rank_chunks` calls ONLY (#189, flipped ON by
    #191): with `TG_FIND_DENSE_WEIGHT` unset -- or set to something malformed/unparseable/
    non-finite (see flip-prep NIT 1 below) -- a genuinely multi-word NL query now gets the
    ledger-validated `_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT` (5.0) instead of the old inert 1.0
    no-op. A single whitespace-free token ALWAYS stays at the protected
    `_FIND_DENSE_WEIGHT_DEFAULT` (1.0), regardless of the env var's state -- the whitespace gate
    below applies uniformly to the adaptive default, a malformed/non-finite fallback, AND an
    explicit override alike.

    When the env var IS set to a valid FINITE float, that EXPLICIT value replaces the adaptive
    default for a multi-word query -- `TG_FIND_DENSE_WEIGHT=1.0` is the explicit opt-out back to
    the old equal-weight fusion; any other finite value (e.g. `=3.0`) is honored verbatim. A
    single whitespace-free token (a bare identifier, a function/class/symbol name, a grep-style
    literal search -- `tg find`'s own "literal" and "identifier3" golden slices,
    `benchmarks/datasets/literal_golden.jsonl` + `identifier3_golden.jsonl`) instead routes to 1.0
    (the un-boosted 1:1 fusion) NO MATTER what the env var says: the sweep's canary case proved
    BM25 is the stronger leg for a lexical query, so boosting dense would risk regressing it --
    this is the guard that keeps the knob scoped to where it was actually measured to help.

    Whitespace, NOT morphemes (dogfood finding, #191): the classifier gates on whitespace-separated
    word count, never `split_terms(query)` morpheme count. `split_terms` splits snake_case/camelCase
    into morphemes, so a descriptive single-token identifier like `mint_access_token` /
    `getUserName` / `_confine_mcp_path` splits into 3+ morphemes and the old `> 2` floor
    misclassified it as NL -- exactly backwards for a literal-identifier lookup where BM25 is the
    strong leg. A real-repo dogfood on tensor-grep's own src caught the leak (5 of 6 literal
    identifier queries wrongly boosted, only the 2-morpheme `rank_chunks` protected); the
    whitespace gate is the dogfood's own recommended fix.

    KNOWN SCOPE : the whitespace gate is purely structural -- a
    2-word LEXICAL phrase (e.g. `"return None"`, `"TODO fixme"`) is indistinguishable from a
    2-word NL phrase and ALSO receives the adaptive boost. The 1:5 sweep is 100% NL queries and
    the literal/identifier3 golden slices are single-token by construction, so this exact shape is
    UNMEASURED. This is a deliberate non-goal, not an oversight: building a lexical-vs-NL content
    classifier here would repeat the exact mistake the morpheme classifier made (a content-based
    heuristic that silently misclassifies real queries) -- see
    `test_find_dense_weight_default_boosts_two_word_lexical_canary` for the regression-catching
    canary this scope decision needs. `TG_FIND_DENSE_WEIGHT=1.0` remains the escape hatch for a
    precise 2-word literal search; `potion-code-16M` is a CODE-domain embedding model, which makes
    boosting a 2-word code fragment defensible too, not just prose NL.

    Flip-prep NIT 1 (tg_find_review_ledger.md FLIP-PREP): `float("nan")` / `float("inf")` /
    `float("-inf")` all PARSE successfully -- `ValueError` alone never catches them, and `nan` in
    particular compares unequal to everything (including itself), so a downstream `!= 1.0` check
    would treat it as "non-default" too. `math.isfinite` rejects `nan` and both infinities the same
    way the `except ValueError` branch below rejects outright garbage -- both now fall through to
    the SAME adaptive-default resolution as an unset env var (#191 must-fix 2: a typo must not
    silently opt an operator OUT of the improved default), rather than being clamped to the old
    1.0. Either way, a non-finite value never reaches `rank_chunks(dense_weight=...)`:
    `weights=[1.0, nan]` would otherwise build a degenerate list for `reciprocal_rank_fusion`'s
    sort.
    """
    raw = os.environ.get(_FIND_DENSE_WEIGHT_ENV)
    weight = _FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT
    if raw:
        try:
            explicit = float(raw)
        except ValueError:
            explicit = None
        if explicit is not None and math.isfinite(explicit):
            weight = explicit
        # else: malformed or non-finite -- treated exactly like an unset env var (falls through to
        # the adaptive default above), per #191 must-fix 2.

    # A single whitespace-free token (or an empty/whitespace-only query, whose `split()` -> []) is a
    # literal identifier -> stay at the protected 1.0, regardless of the env var's state. Only a
    # genuinely multi-word (space-separated) query is NL and gets the resolved `weight` above.
    # Whitespace, not `split_terms` morphemes -- see the module comment above `_FIND_DENSE_WEIGHT_ENV`
    # for the dogfood finding (#191) this fixes.
    if _find_is_single_token_query(query):
        return _FIND_DENSE_WEIGHT_DEFAULT
    return weight


def _find_is_single_token_query(query: str) -> bool:
    """Shared literal/identifier-query predicate for `tg find`'s two query-adaptive knobs --
    `_find_dense_weight`'s dense_weight (above) AND `_find_combine_mode`'s RRF combine mode
    (below). A single whitespace-free token (or an empty/whitespace-only query, whose ``split()``
    -> ``[]``) is a literal identifier lookup; anything with 2+ whitespace-separated words is NL.
    Whitespace, not `split_terms` morphemes -- see the module comment above
    `_FIND_DENSE_WEIGHT_ENV` for the #191 dogfood finding this rule fixes. Factored out to a single
    canonical definition so the two knobs can never silently disagree on what counts as literal.
    """
    return len(query.split()) <= 1


def _find_combine_mode(query: str) -> Literal["sum", "max"]:
    """Query-adaptive RRF `combine` mode for `tg find`'s `rank_chunks` calls (accuracy-leg
    max-fusion regression fix, review finding on PR #717): `combine="max"` (`rank_chunks`'s own
    default, see its docstring) lifts genuinely multi-word/NL queries (ndcg@10 +62.6% on the frozen
    40-query golden set) but REGRESSES single-token literal/identifier lookups -- measured on
    `benchmarks/datasets/literal_golden.jsonl` (10 queries, `dense_weight=1.0` as
    `_find_dense_weight` already protects them at): sum scores a perfect 1.0 ndcg@10 (as it always
    has); max drops to 0.9631, a real -0.0369 regression. Mechanism: a literal query's true answer
    is frequently ranked #1 by BOTH the bm25 leg and the dense leg independently -- `"sum"`'s
    per-leg-agreement bonus is exactly the confirming signal `"max"` discards, letting a
    single-leg-only competitor tie the true answer's now-undiscounted best-single-term score.

    Routes on the EXACT SAME whitespace predicate `_find_dense_weight` already uses
    (`_find_is_single_token_query`) so the two adaptive knobs can never disagree on what counts as
    literal: a single whitespace-free token -> `"sum"` (byte-identical to the pre-max-flip
    behavior, recovering the regression); a genuinely multi-word query -> `"max"` (keeps the NL
    win, including composed with the adaptive `dense_weight=5.0`, which reaches the dense-alone
    ndcg@10 ceiling exactly). This is scoped to `tg find` only -- `rerank_hybrid` (the `tg search
    --semantic` path) never passes `combine`, so it is unaffected and stays on the plain "max"
    default.
    """
    return "sum" if _find_is_single_token_query(query) else "max"


def _find_representative_line(chunk: Chunk, query_terms: set[str]) -> tuple[int, str]:
    """Pick ONE line within `chunk` to surface as the synthesized `MatchLine` (D2): the line with
    the most `split_terms` overlap with the query, ties broken by the FIRST such line (the `>`
    comparison below never replaces an already-found best on an equal score) -- deterministic
    regardless of repeated content within the chunk.

    Falls back to the chunk's first line (or an empty string for a pathologically empty chunk)
    when the query has no terms at all or the chunk somehow has no lines -- `chunk_file` never
    returns an empty-text chunk in practice, but this stays total rather than raising on it.
    """
    from tensor_grep.core.retrieval_lexical import split_terms

    lines = chunk.text.splitlines()
    if not lines:
        return chunk.start_line, ""

    best_index = 0
    best_overlap = -1
    for index, line_text in enumerate(lines):
        overlap = len(query_terms & set(split_terms(line_text))) if query_terms else 0
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index

    return chunk.start_line + best_index, lines[best_index]


def _execute_find(
    query: str,
    path: str,
    *,
    limit: int,
    max_repo_files: int,
    max_tokens: int,
    deadline: float | None,
    why_ranked: bool = False,
    grounding: str = "local",
    rerank: str = "off",
    hybrid: bool = False,
    verbose: bool = False,
) -> SearchResult:
    """The `tg find` pipeline: whole-repo walk -> chunk -> BM25 [+ dense] [+ late MaxSim] rank via
    the shared `rank_chunks` core (`core/reranker.py`) -> `--limit` -> token-budget fit -> a
    synthesized `SearchResult` (one representative `MatchLine` per selected chunk, D2).

    Fail-closed matrix (D3, fix-approach council must-fixes C1-C3):
    - dense/late-leg extra absent, model not fetched, or a recoverable degrade -> visible BM25-only
      (or pre-late-stage) fallback: `rank_fallback_reason` set + a `tg:`-prefixed stderr line, exit
      0. Mirrors `_apply_semantic_rerank`'s dense/late scaffold (same `dense_available` /
      `late_available` probes, same construction-time degrades) INCLUDING its query-time F1 catch:
      a `DenseUnavailableError` raised from INSIDE `rank_chunks`'s `DenseIndex.query` (a dim/shape
      mismatch, distinct from the construction path) is caught around the `rank_chunks` call and
      degrades to a BM25-only re-run, so it stays a visible exit-0 degrade rather than escaping as a
      raw traceback (`DenseUnavailableError` subclasses `RuntimeError`, so neither the construction
      guard nor the command boundary would otherwise catch it).
    - a genuine unrecoverable backend fault (`BackendExecutionError` from a corrupt model directory
      or an encode-time crash) is NOT caught here -- it propagates to the command boundary (C1),
      which must catch it and exit 2 with a clean `tg:` message, never a raw traceback.
    - a repo walk capped by `--max-repo-files`, a `--deadline` cutoff (walk or chunk phase), a
      per-file chunk() `RuntimeError`, or the corpus-wide chunk cap all mean the ranked corpus was
      PARTIAL (no regex prefilter narrowed it first, unlike `--semantic`) -- each sets
      `result_incomplete=True` + appends a human-readable `incomplete_reason` (C2); the caller
      exits 2 for these, never the exit-0 BM25-only degrade `--semantic`'s own corpus cap uses.
    - `--limit` and `--max-tokens` are OUTPUT caps on an otherwise-complete ranking (mirrors
      `_scan_incomplete`'s output-cap-stays-0 carve-out, repo_map.py) -- they truncate the
      lowest-ranked matches first and never set `result_incomplete`; at least one match survives
      the token budget even if it alone exceeds `max_tokens` (the "confident false zero" floor
      `_apply_context_token_budget` documents at repo_map.py:8221).
    """
    from tensor_grep.cli.repo_map import (
        _deadline_monotonic_from_seconds,
        _DeadlineBreakFlag,
        _iter_repo_files,
        _UnreadablePathFlag,
    )
    from tensor_grep.cli.repo_map import _estimate_tokens as _repo_map_estimate_tokens
    from tensor_grep.core.reranker import rank_chunks
    from tensor_grep.core.result import MatchLine, SearchResult
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import chunk_file
    from tensor_grep.core.retrieval_dense import (
        DenseIndex,
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )
    from tensor_grep.core.retrieval_lexical import split_terms

    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    from tensor_grep.cli.investigation_options import finish_find, validate_investigation_options

    validate_investigation_options(grounding, rerank)
    result = SearchResult()
    incomplete_reasons: list[str] = []
    # Task #276 slice 1 (+ #284): `tg find` can accumulate MULTIPLE heterogeneous incomplete
    # causes -- walk deadline, --max-repo-files cap, an UNREADABLE subtree, chunking deadline,
    # chunk-count cap, and a per-file parse/read error that doesn't map onto the closed
    # vocabulary at all -- into one `incomplete_reasons` list. `incomplete_reason_class` is a
    # single field, so first-cause-wins; it stays `None` if only an unclassifiable error occurred.
    #
    # This list is NOT a completeness guarantee. Read it as "the causes wired so far", never as
    # "the causes that exist" -- #284 was exactly this defect: the unreadable-path cause was
    # missing from BOTH the code and this comment, so a reader who saw the other causes handled
    # correctly would reasonably infer coverage that did not exist. If you add a cause, add it
    # here too; if you find one that is NOT here, it is unwired, not impossible.
    incomplete_reason_class: str | None = None

    deadline_monotonic = _deadline_monotonic_from_seconds(deadline)
    walk_deadline_hit = _DeadlineBreakFlag()
    walk_unreadable_hit = _UnreadablePathFlag()
    all_files = _iter_repo_files(
        root,
        max_files=max_repo_files + 1,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=walk_deadline_hit,
        unreadable_hit=walk_unreadable_hit,
    )
    file_limit_hit = len(all_files) > max_repo_files
    del all_files[max_repo_files:]

    if walk_deadline_hit.hit:
        reason = (
            f"repo walk exceeded the {deadline:g}s deadline after {len(all_files)} files -- "
            "ranking covers a partial corpus"
        )
        incomplete_reasons.append(reason)
        incomplete_reason_class = "deadline"
        sys.stderr.write(f"tg: {reason}\n")
    elif file_limit_hit:
        reason = (
            f"repo walk capped at --max-repo-files={max_repo_files}; the repo may hold more "
            "files -- raise --max-repo-files to widen coverage"
        )
        incomplete_reasons.append(reason)
        incomplete_reason_class = "scan_limit"
        sys.stderr.write(f"tg: {reason}\n")

    if walk_unreadable_hit.hit:
        # Task #284: an unreadable subtree is an INDEPENDENT cause, not an alternative to the two
        # above -- it can co-occur with either, so this is a separate `if`, never an `elif`. It is
        # also the one cause in this block that is NOT budget-remediable, which is exactly why it
        # must be reported even when a budget cause already claimed
        # `incomplete_reason_class`: otherwise the reader gets WRONG-KNOB advice ("raise
        # --max-repo-files") with no hint that a bigger budget cannot make those paths readable.
        # Same reasoning, and the same shape, as the entry-cap clarifier in `search_command`.
        sample = ", ".join(walk_unreadable_hit.sample) or "an unreadable path"
        reason = (
            f"repo walk skipped {walk_unreadable_hit.count} unreadable path(s) (e.g. {sample}) -- "
            "ranking covers a partial corpus. More budget will not fix this: the path(s) need to "
            "become readable, or scope the search away from them"
        )
        incomplete_reasons.append(reason)
        # First-cause-wins, matching this function's documented convention above: only claim the
        # single `incomplete_reason_class` field if a budget cause has not already taken it. The
        # human-readable `incomplete_reasons` list carries the full picture either way.
        if incomplete_reason_class is None:
            incomplete_reason_class = "unreadable_path"
        sys.stderr.write(f"tg: {reason}\n")

    # C2: chunk with a per-file RuntimeError guard + a corpus-wide cap, mirroring
    # `_apply_semantic_rerank`'s chunk-building loop (main.py:3886-3927) in SHAPE only -- the
    # ACTION on trip deliberately differs (see the docstring above): note partial coverage and
    # keep going / stop, never force a bm25-only retry the way the regex-prefiltered `--semantic`
    # path does.
    chunks: list[Chunk] = []
    chunked_file_count = 0
    for file_path in all_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            reason = (
                f"chunking exceeded the {deadline:g}s deadline after {chunked_file_count} of "
                f"{len(all_files)} files -- ranking covers a partial corpus"
            )
            incomplete_reasons.append(reason)
            if incomplete_reason_class is None:
                incomplete_reason_class = "deadline"
            sys.stderr.write(f"tg: {reason}\n")
            break
        try:
            file_chunks = chunk_file(str(file_path))
        except RuntimeError as exc:
            # A per-file chunk/parse failure doesn't cleanly map onto the closed vocabulary
            # (it could be a read error, a decode error, or a genuine parser bug) -- leave
            # `incomplete_reason_class` unset (not overwritten) if this is the only cause, so
            # the field stays absent rather than guessed.
            reason = f"skipped {file_path}: {exc}"
            incomplete_reasons.append(reason)
            sys.stderr.write(f"tg: {reason}\n")
            continue
        chunks.extend(file_chunks)
        chunked_file_count += 1
        if len(chunks) > _self._FIND_CORPUS_CHUNK_CAP:
            reason = (
                f"find corpus chunk cap ({_self._FIND_CORPUS_CHUNK_CAP}) reached over "
                f"{chunked_file_count} of {len(all_files)} files -- ranking covers a partial corpus"
            )
            incomplete_reasons.append(reason)
            if incomplete_reason_class is None:
                incomplete_reason_class = "scan_limit"
            sys.stderr.write(f"tg: {reason}\n")
            break

    if incomplete_reasons:
        result.result_incomplete = True
        result.incomplete_reason = "; ".join(incomplete_reasons)
        result.incomplete_reason_class = incomplete_reason_class

    if not chunks:
        return finish_find(
            result,
            query,
            path,
            grounding,
            rerank,
            deadline_monotonic=deadline_monotonic,
            max_tokens=max_tokens,
        )

    bm25_index = Bm25Index(chunks)

    dense_index = None
    available, unavailable_reason = dense_available()
    if not available:
        result.rank_fallback_reason = unavailable_reason
        if verbose:
            sys.stderr.write(f"tg: {unavailable_reason}\n")
    else:
        try:
            model = load_dense_model(default_model_dir())
            dense_index = DenseIndex(chunks, model)
        except DenseUnavailableError as exc:
            message = _friendly_dense_unavailable_message(exc)
            result.rank_fallback_reason = message
            if verbose:
                sys.stderr.write(f"tg: {message}\n")
        # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates -- the
        # command boundary (C1) must catch it and exit 2, never degrade here.

    late_reranker = None
    if os.environ.get("TG_LATE_RERANK") == "1":
        from tensor_grep.core.retrieval_late import (
            LateRerankUnavailableError,
            late_available,
            load_late_reranker,
        )

        late_ok, late_reason = late_available()
        if not late_ok:
            _note_late_rerank_degraded(result, late_reason or "late rerank unavailable")
        else:
            try:
                late_reranker = load_late_reranker()
            except LateRerankUnavailableError as exc:
                _note_late_rerank_degraded(result, str(exc))
            # BackendExecutionError deliberately propagates -- mirrors the dense leg immediately
            # above and `_apply_semantic_rerank`'s identical contract.

    try:
        fused_order, late_fallback_reason = rank_chunks(
            query,
            chunks,
            bm25_index=bm25_index,
            dense_index=dense_index,
            late_reranker=late_reranker,
            dense_weight=_find_dense_weight(query),
            combine=_find_combine_mode(query),
            hybrid=hybrid,
            cache_root=root if hybrid else None,
            **retrieval_symbols.evidence_options(result),
        )
    except DenseUnavailableError as exc:
        # F1 (review blocker; mirrors `_apply_semantic_rerank`'s own query-time catch,
        # main.py:3970-3984): a dense fault raised at QUERY time from INSIDE `rank_chunks`'s call to
        # `DenseIndex.query` (e.g. a dim/shape mismatch) is NOT the DenseIndex CONSTRUCTION path
        # guarded above. `DenseUnavailableError` subclasses `RuntimeError`, so without this it would
        # escape both here AND the command boundary (which catches only FileNotFoundError /
        # BackendExecutionError) as a raw traceback + exit 1 -- a Backend Fail-Closed Contract
        # violation. Degrade VISIBLY to BM25-only (reuse the SAME bm25_index -- no second chunk
        # pass) and re-run; this also bypasses the late stage, which is only ever wired into the
        # primary call above and fed off the (now-dropped) dense fusion.
        degrade_reason = str(exc)
        result.rank_fallback_reason = (
            f"{result.rank_fallback_reason}; {degrade_reason}"
            if result.rank_fallback_reason
            else degrade_reason
        )
        sys.stderr.write(f"tg: {exc}\n")
        dense_index = None
        fused_order, late_fallback_reason = rank_chunks(
            query,
            chunks,
            bm25_index=bm25_index,
            dense_index=None,
            late_reranker=None,
            dense_weight=_find_dense_weight(query),
            combine=_find_combine_mode(query),
            hybrid=hybrid,
            cache_root=root if hybrid else None,
            **retrieval_symbols.evidence_options(result),
        )
    result.routing_backend, result.routing_reason, result.install_state = route_labels(
        dense_index, result.rank_fusion, hybrid=hybrid
    )
    if late_fallback_reason:
        result.rank_fallback_reason = (
            f"{result.rank_fallback_reason}; {late_fallback_reason}"
            if result.rank_fallback_reason
            else late_fallback_reason
        )

    matches, selected = select_deduplicated_find_matches(
        chunks,
        fused_order,
        set(split_terms(query)),
        limit=limit,
        why_ranked=why_ranked,
        find_representative_line=_find_representative_line,
    )

    # Output-only budget fit (never touches result_incomplete -- see docstring): truncate the
    # LOWEST-ranked matches first (the tail of the already best-first `matches` list), floored at 1
    # survivor so a real hit is never trimmed down to a "confident false zero".
    if max_tokens > 0 and matches:
        budgeted: list[MatchLine] = []
        running_tokens = 0
        for match in matches:
            match_tokens = _repo_map_estimate_tokens(match.text)
            if budgeted and running_tokens + match_tokens > max_tokens:
                break
            budgeted.append(match)
            running_tokens += match_tokens
        matches = budgeted or matches[:1]

    result.matches = matches
    if rerank != "off":
        result.rerank_texts = [chunks[index].text for index in selected[: len(matches)]]
    result.total_matches = len(matches)
    matched_paths = sorted({match.file for match in matches})
    result.matched_file_paths = matched_paths
    result.total_files = len(matched_paths)
    for match in matches:
        result.match_counts_by_file[match.file] = result.match_counts_by_file.get(match.file, 0) + 1

    return finish_find(
        result,
        query,
        path,
        grounding,
        rerank,
        deadline_monotonic=deadline_monotonic,
        max_tokens=max_tokens,
    )


def find(
    query: str = typer.Argument(..., help="Natural-language or keyword query to search for."),
    path: str = typer.Argument(".", help="Root directory (or single file) to search."),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum ranked chunks to return."),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before ranking.",
    ),
    max_tokens: int = typer.Option(
        4000,
        "--max-tokens",
        min=0,
        help=(
            "Bound the result set to ~N tokens, dropping the lowest-ranked matches first "
            "(0 = unbounded)."
        ),
    ),
    deadline: float | None = _deadline_option(
        "Stop the repo walk/chunk phase after N seconds and return ranked results over the partial corpus scanned so far (result_incomplete=true, exit 2) instead of running unbounded."
    ),
    why_ranked: bool = typer.Option(False, "--why-ranked", help="Explain why matches were ranked."),
    grounding: str = typer.Option("local", "--grounding", help="Evidence: off, local, registry."),
    rerank: str = typer.Option("off", "--rerank", help="Native rerank: off, auto, cross-encoder."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ndjson: bool = typer.Option(False, "--ndjson", help="Emit newline-delimited JSON per match."),
    citations: bool = typer.Option(False, "--citations", help="Emit citation ranges."),
    hybrid: bool = typer.Option(False, "--hybrid", help="Use FTS5 + adaptive IDF-weighted RRF."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show verbose fallback and diagnostic notices."
    ),
) -> None:
    """EXPERIMENTAL: whole-repo hybrid semantic search (BM25 + local CPU dense-embedding
    relevance, RRF-fused), ranked file:line results.

    Opt-in `--rerank` scores the existing candidate head using installed native CPU assets.
    `--grounding local` reads dependency metadata; `registry` explicitly requests bounded network
    metadata. Neither registry versions nor static API evidence prove API compatibility.

    Unlike `tg search --rank`/`--semantic` (which re-rank an EXISTING regex match set), `tg find`
    walks and ranks the WHOLE repo -- no pattern pre-filter, so it can surface content a
    vocabulary-mismatched regex would miss. No API key, no GPU. The dense leg requires the
    `semantic` extra and a fetched model; falls back to BM25-only (visibly, never silently) when
    either is missing -- a BM25-only `tg find` is still a fully supported mode. Bounded by default
    (`--max-repo-files`, `--deadline`, an internal corpus-wide chunk cap): a truncated scan is
    marked `result_incomplete` and exits 2 rather than silently reporting a partial repo as
    complete. Does not offer `--format rg` -- this is not a grep-parity surface.
    """
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.core.pipeline import ConfigurationError

    _citations.validate_find_citation_modes(citations, ndjson)
    try:
        result = _self._execute_find(
            query,
            path,
            limit=limit,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
            deadline=deadline,
            why_ranked=why_ranked,
            grounding=grounding,
            rerank=rerank,
            hybrid=hybrid,
            verbose=verbose,
        )
    except (FileNotFoundError, ConfigurationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except BackendExecutionError as exc:
        # C1: mirror `search`'s command-boundary catch (main.py ~7528-7537) -- `_execute_find`
        # deliberately does not catch this (see its docstring); a corrupt model / encode-time
        # fault must exit cleanly here, never a raw traceback.
        if json_output:
            _emit_search_error_json("find_backend_error", str(exc))
        else:
            typer.echo(f"tg: {exc}", err=True)
        sys.exit(2)

    _citations.exit_if_find_citations(result, path, citations, json_output, ndjson)

    if result.is_empty:
        if json_output:
            from tensor_grep.cli.formatters.json_fmt import JsonFormatter

            _safe_stdout_line(JsonFormatter().format(result))
        # C3: replicate search's hand-written exit block (main.py ~7643-7679) -- the SearchResult
        # envelope buys the JSON fields, not the exit codes.
        sys.exit(2 if result.result_incomplete else 1)

    formatter: OutputFormatter
    if ndjson:
        from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter

        formatter = NdjsonFormatter()
    elif json_output:
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        formatter = JsonFormatter()
    else:
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
        from tensor_grep.core.config import SearchConfig

        # `with_filename=True` unconditionally: unlike `search` (whose matches are usually already
        # scoped to a query'd area), `find` ranks across the whole repo, so the file is always
        # relevant context even when every top match happens to land in one file.
        formatter = RipgrepFormatter(config=SearchConfig(with_filename=True))

    _safe_stdout_line(formatter.format(result))
    if result.result_incomplete:
        sys.exit(2)


def register(app: typer.Typer) -> None:
    app.command()(find)
