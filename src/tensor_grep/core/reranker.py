"""Re-rank an existing SearchResult by BM25 chunk relevance (and, for the hybrid variant, an
RRF fusion of BM25 + dense embedding [+ opt-in path/filename] relevance).

This is the lightweight post-processing seam for ``tg search --rank`` / ``tg search --semantic``:
the normal backend produces matches in grep order, and this re-orders them by the relevance score
of the chunk that contains each match. Matches whose chunk does not score (or whose file is not in
the corpus) sink to the end. The sort is stable, so equal-score matches keep their original grep
order.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import threading
from collections import defaultdict

from tensor_grep.core.result import SearchResult
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import MAX_CHUNKS, Chunk, chunk_file
from tensor_grep.core.retrieval_dense import DenseIndex
from tensor_grep.core.retrieval_fusion import DEFAULT_K, reciprocal_rank_fusion
from tensor_grep.core.retrieval_late import LateReranker, LateRerankUnavailableError
from tensor_grep.core.retrieval_lexical import split_terms

# PR-S2 (channelized RRF, sverklo steal-list #2): a third, opt-in fusion leg that ranks chunks by
# filename-token overlap with the query -- a precision signal (a query mentioning "invoice" should
# surface invoice_parser.py's chunks first). DEFAULT-OFF (gated by `_RRF_CHANNELS_ENV`) so this is
# a zero-risk additive change pending a golden-set default-flip in a separate PR. A symbol-name
# channel is DEFERRED to a later phase (it would need a def-scan source and couple this free-file
# module to repo_map).
_RRF_CHANNELS_ENV: str = "TG_RRF_CHANNELS"
PATH_CHANNEL_WEIGHT: float = 1.5


def _rrf_channels_enabled() -> bool:
    return os.environ.get(_RRF_CHANNELS_ENV) == "1"


# T5/T6 (design doc "The seam" + "Fail-closed contract",
# docs/plans/design-tensor-grep-late-rerank-2026-07-09.md): a 4th, opt-in, ORDER-ONLY stage that
# MaxSim-reranks the head of the RRF-fused pool via an injected `LateReranker`
# (core/retrieval_late.py, T0-T4, not modified here). The caller (`_apply_semantic_rerank` in
# cli/main.py) owns the `TG_LATE_RERANK=1` gate, late-leg availability, and model load; this
# module only needs the pool size and the latency budget, both read directly from the
# environment right where they are used (mirrors `_RRF_CHANNELS_ENV` above) so `rerank_hybrid`'s
# signature gains a single new `late_reranker` kwarg and nothing else has to be threaded through.
_RERANK_POOL_K_ENV: str = "TG_RERANK_POOL_K"
_RERANK_BUDGET_MS_ENV: str = "TG_RERANK_BUDGET_MS"
_DEFAULT_RERANK_POOL_K: int = 50
_MAX_RERANK_POOL_K: int = 100
_DEFAULT_RERANK_BUDGET_MS: int = 2000

# #128d (backlog cluster-1 P0-CORRECTNESS, MED-1): retrieval_chunker.MAX_CHUNKS bounds a single
# chunk_file() call (per FILE). A matched-file set of many small files can still blow past a sane
# CORPUS-wide total even though no single file trips the per-file guard -- plain `tg search --rank`
# (CLI cli/main.py:7222-7225, MCP cli/mcp_server.py:4258-4263, both funnel through the
# `index is None` / `bm25_index is None` branches below) had NO total cap at all, unlike the
# `--semantic` path's `_SEMANTIC_CORPUS_CHUNK_CAP` (cli/main.py, shipped by #527/A2). This mirrors
# that cap at the shared reranker.py chokepoint instead, so CLI, MCP, and any future caller are
# covered with ZERO call-site edits. Env-tunable (unlike the semantic path's plain constant)
# because an operator may want to raise/lower it without a code change; the default equals
# MAX_CHUNKS, the same threshold the per-file guard and the semantic cap already share, so there is
# still exactly one number to tune in the common case.
_RANK_CORPUS_CHUNK_CAP_ENV: str = "TG_RANK_CORPUS_CHUNK_CAP"


def _int_env(name: str, default: int) -> int:
    """Parse a numeric ``TG_*`` env var, falling back to ``default`` on any missing or
    non-numeric value -- a malformed override must degrade gracefully, never crash the whole
    search (the same fail-closed spirit as the rest of the late-rerank contract)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _path_channel_ranking(chunks: list[Chunk], query: str) -> list[int]:
    """Rank chunk indices by query-token overlap with their file's stem (basename minus
    extension), best-first. Reuses ``retrieval_lexical.split_terms`` -- the same tokenizer the
    BM25 leg uses -- so "parse_invoice" query terms match an "invoice_parser.py" filename despite
    the different word order. Only chunks with at least one overlapping token are included
    (mirrors ``Bm25Index.query`` excluding zero-score docs, so a non-matching filename contributes
    0 to this leg rather than an arbitrary tie-broken rank). Ties break by ascending chunk index
    for full determinism.
    """
    query_terms = set(split_terms(query))
    if not query_terms:
        return []

    scored: list[tuple[int, int]] = []
    for i, chunk in enumerate(chunks):
        stem = os.path.splitext(os.path.basename(chunk.file_path))[0]
        overlap = len(query_terms & set(split_terms(stem)))
        if overlap > 0:
            scored.append((i, overlap))

    ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
    return [chunk_index for chunk_index, _overlap in ranked]


def _rank_corpus_chunk_cap() -> int:
    """The corpus-wide chunk cap for the ``index is None`` / ``bm25_index is None`` build loops
    below -- ``TG_RANK_CORPUS_CHUNK_CAP`` if set to a valid positive int, else :data:`MAX_CHUNKS`.
    A non-positive or malformed override falls back to the default rather than pathologically
    capping at (near) zero -- the same "a malformed override must degrade gracefully" spirit as
    :func:`_int_env`."""
    cap = _int_env(_RANK_CORPUS_CHUNK_CAP_ENV, MAX_CHUNKS)
    return cap if cap > 0 else MAX_CHUNKS


def _chunk_corpus_with_total_cap(
    file_paths: list[str],
    *,
    chunk_size: int,
    overlap: int,
) -> tuple[list[Chunk], str | None]:
    """Chunk every file in ``file_paths`` in order, STOPPING before the accumulated chunk count
    would exceed :func:`_rank_corpus_chunk_cap` -- the chokepoint fix for #128d (MED-1): plain
    `tg search --rank` previously chunked the ENTIRE matched-file set with no total bound (only a
    per-FILE bound existed, ``retrieval_chunker.MAX_CHUNKS``), so a broad query on a large repo
    could rechunk thousands of files before ranking even started -- unbounded CPU/memory, reachable
    from both the CLI and the MCP ``rank``/``tg_search`` tool.

    Returns ``(chunks, fallback_reason)``. ``fallback_reason`` is ``None`` when every file was
    chunked (the common case -- byte-identical to the pre-cap behavior). When the cap trips it is a
    human-readable string the caller MUST surface on the returned ``SearchResult`` (append, never
    clobber -- the same convention every other rank/semantic degrade in this codebase follows, see
    ``rerank_hybrid``'s late-rerank combination below and ``cli/main.py``'s semantic-cap degrade) --
    silently truncating would be indistinguishable from "the corpus was simply small", exactly the
    suppression-reads-as-absence failure the Backend Fail-Closed Contract forbids for a full engine
    swap and this project's partial-results contract forbids for a soft per-item suppression.

    Matches are NEVER dropped by this cap -- the rerank contract is order-only (see each caller's
    docstring). Files left unchunked past the trip point simply have no scored chunk, so their
    matches sink to the end via the existing zero-score path -- identical to how an unmatched file
    behaves today.
    """
    cap = _rank_corpus_chunk_cap()
    chunks: list[Chunk] = []
    fallback_reason: str | None = None
    chunked_file_count = 0
    for path in file_paths:
        chunks.extend(chunk_file(path, chunk_size=chunk_size, overlap=overlap))
        chunked_file_count += 1
        if len(chunks) > cap:
            fallback_reason = (
                f"bm25 rank corpus cap reached ({cap} chunks over {chunked_file_count} of "
                f"{len(file_paths)} matched files); ranking covers the first "
                f"{chunked_file_count} files, remaining matches keep grep order"
            )
            sys.stderr.write(f"tg: {fallback_reason}\n")
            break
    return chunks, fallback_reason


def rerank_by_bm25(
    result: SearchResult,
    query: str,
    file_paths: list[str],
    *,
    chunk_size: int = 30,
    overlap: int = 5,
    index: Bm25Index | None = None,
) -> SearchResult:
    """Return a copy of ``result`` with matches re-sorted by best BM25 chunk score (desc).

    When ``index`` is not supplied, the corpus built from ``file_paths`` is bounded by
    :func:`_chunk_corpus_with_total_cap` (#128d) -- see its docstring for the chokepoint fix.
    Passing a prebuilt ``index`` (e.g. a caller's own deliberately-capped corpus, as the
    ``--semantic`` degrade path does) bypasses this bound entirely, same as before.
    """
    if not result.matches:
        return dataclasses.replace(result, matches=list(result.matches))

    corpus_cap_reason: str | None = None
    if index is None:
        chunks, corpus_cap_reason = _chunk_corpus_with_total_cap(
            file_paths, chunk_size=chunk_size, overlap=overlap
        )
        index = Bm25Index(chunks)

    # Best score per chunk index for this query.
    chunk_scores: dict[int, float] = dict(index.query(query, top_k=max(1, len(index.chunks))))

    # file -> [(start_line, end_line, chunk_index)] for line-containment lookup.
    by_file: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for i, chunk in enumerate(index.chunks):
        by_file[chunk.file_path].append((chunk.start_line, chunk.end_line, i))

    def match_score(match) -> float:  # type: ignore[no-untyped-def]
        best = 0.0
        for start, end, i in by_file.get(match.file, ()):
            if start <= match.line_number <= end:
                best = max(best, chunk_scores.get(i, 0.0))
        return best

    # Stable sort by descending score (Python's sort is stable -> ties keep grep order).
    reranked = sorted(result.matches, key=match_score, reverse=True)
    if corpus_cap_reason is not None:
        # Append rather than overwrite: a pre-existing reason on the input result (should a future
        # caller ever pass one in) must survive alongside the corpus-cap reason -- mirrors
        # rerank_hybrid's late-rerank combination below and cli/main.py's semantic-cap degrade.
        combined_reason = (
            f"{result.rank_fallback_reason}; {corpus_cap_reason}"
            if result.rank_fallback_reason
            else corpus_cap_reason
        )
        return dataclasses.replace(result, matches=reranked, rank_fallback_reason=combined_reason)
    return dataclasses.replace(result, matches=reranked)


def rerank_hybrid(
    result: SearchResult,
    query: str,
    file_paths: list[str],
    *,
    chunk_size: int = 30,
    overlap: int = 5,
    k: int = DEFAULT_K,
    bm25_index: Bm25Index | None = None,
    dense_index: DenseIndex | None = None,
    late_reranker: LateReranker | None = None,
) -> SearchResult:
    """Return a copy of ``result`` re-sorted by best RRF-fused (BM25 + dense) chunk score (desc).

    Mirrors :func:`rerank_by_bm25`: SAME matches as the input, only the order changes. The BM25
    leg always runs; the dense leg is optional -- the caller owns dense-leg availability and the
    fail-closed BM25-only degrade (see ``core/retrieval_dense.py``), so ``dense_index=None`` here
    simply means "fuse with the BM25 leg alone" (still routed through RRF).

    A third, opt-in PATH channel (see :func:`_path_channel_ranking`) ranks chunks by
    filename-token overlap with the query at ``PATH_CHANNEL_WEIGHT`` (1.5x) vs the BM25/dense
    legs' implicit 1.0x. It is gated behind the ``TG_RRF_CHANNELS=1`` environment variable and is
    DEFAULT-OFF: with the flag unset (the default), fusion runs BM25 [+ dense] with
    ``weights=None`` exactly as before -- a byte-identical no-op (see
    :func:`~tensor_grep.core.retrieval_fusion.reciprocal_rank_fusion`) -- so this is a zero-risk
    additive change pending a golden-set default-flip in a separate PR.

    ``late_reranker`` (optional, T5, design doc "The seam"): a 4th, ORDER-ONLY stage layered on
    top of the fused ranking above -- it MaxSim-reranks the head of ``fused_order`` (size
    ``TG_RERANK_POOL_K``, default 50, hard-capped at 100) and leaves the tail untouched in its RRF
    order. Same matches, same membership, same JSON shape -- only a permutation of the head.
    ``late_reranker=None`` (the default) skips this stage entirely: a byte-identical no-op,
    mirroring ``dense_index=None`` and the ``TG_RRF_CHANNELS`` pattern above. The caller
    (``_apply_semantic_rerank`` in ``cli/main.py``) owns late-leg availability and model load; a
    RECOVERABLE failure at rerank time (a malformed embedding shape, or the
    ``TG_RERANK_BUDGET_MS`` latency budget -- default 2000ms -- exceeded) degrades in-place here
    to the plain RRF order for that pool and is reported via the returned result's
    ``rank_fallback_reason`` (appended, never clobbering an existing reason); an UNRECOVERABLE
    ``BackendExecutionError`` from the injected encoder is NOT caught here and propagates to the
    CLI boundary, per the Backend Fail-Closed Contract.

    NOTE (F15): a BM25-only RRF degrade is NOT byte-identical to :func:`rerank_by_bm25` on a BM25
    SCORE TIE -- RRF breaks ties by ascending chunk index, whereas ``rerank_by_bm25``'s stable sort
    preserves grep order. Both are valid orderings; when ``--semantic`` is requested the fused path
    is authoritative, so this is a benign ordering divergence, not a correctness gap.
    """
    if not result.matches:
        return dataclasses.replace(result, matches=list(result.matches))

    corpus_cap_reason: str | None = None
    if bm25_index is None:
        chunks, corpus_cap_reason = _chunk_corpus_with_total_cap(
            file_paths, chunk_size=chunk_size, overlap=overlap
        )
        bm25_index = Bm25Index(chunks)
    chunks = bm25_index.chunks

    total = max(1, len(chunks))
    bm25_ranking = [chunk_idx for chunk_idx, _ in bm25_index.query(query, top_k=total)]
    rankings: list[list[int]] = [bm25_ranking]
    if dense_index is not None:
        dense_ranking = [chunk_idx for chunk_idx, _ in dense_index.query(query, top_k=total)]
        rankings.append(dense_ranking)

    weights: list[float] | None = None
    if _rrf_channels_enabled():
        weights = [1.0] * len(rankings)
        path_ranking = _path_channel_ranking(chunks, query)
        if path_ranking:
            rankings.append(path_ranking)
            weights.append(PATH_CHANNEL_WEIGHT)

    fused_order = reciprocal_rank_fusion(rankings, k=k, weights=weights)

    # T5/T6: the late-interaction splice. Order-only over `fused_order`'s chunk indices -- same
    # matches, same membership, same JSON shape (design doc "The seam"). `late_reranker=None`
    # (the default) skips this block entirely: byte-identical to the pre-T5 fused order.
    late_rank_fallback_reason: str | None = None
    if late_reranker is not None:
        pool_k = max(
            0, min(_int_env(_RERANK_POOL_K_ENV, _DEFAULT_RERANK_POOL_K), _MAX_RERANK_POOL_K)
        )
        budget_ms = _int_env(_RERANK_BUDGET_MS_ENV, _DEFAULT_RERANK_BUDGET_MS)
        head = fused_order[:pool_k]
        # Real wall-clock deadline (A3, external audit 2026-07-11): the previous post-hoc
        # `elapsed > budget` check could only DISCARD a rerank that had already RETURNED -- a HUNG
        # encoder (a wedged model, an infinite loop in the injected reranker) never returns, so
        # `late_reranker.rerank` blocked `tg search --rank` indefinitely with no bound. Run it on a
        # daemon thread and `join` on the budget: if it overruns, abandon the thread (it dies with
        # this short-lived CLI process) and degrade. The Backend Fail-Closed Contract is PRESERVED
        # across the thread boundary -- a LateRerankUnavailableError (recoverable) degrades to the
        # plain RRF order; ANYTHING ELSE (a genuine BackendExecutionError encode fault, or a
        # KeyboardInterrupt/SystemExit user-abort) is re-raised on this thread so it still
        # propagates to the CLI boundary (cli/main.py ~:6804-6813), exactly as the synchronous
        # version did. Capturing BaseException (not just Exception) is deliberate: it keeps a
        # user-abort from being silently swallowed into an RRF degrade AND guarantees exactly one
        # of the two result holders is populated when the worker finishes (so the `else` splice
        # below can never IndexError on an empty result).
        rerank_result: list[list[int]] = []
        rerank_error: list[BaseException] = []

        def _run_late_rerank() -> None:
            try:
                rerank_result.append(
                    late_reranker.rerank(query, [chunks[i].text for i in head], head)
                )
            except (
                BaseException
            ) as exc:  # classified + re-raised (if non-recoverable) by the caller
                rerank_error.append(exc)

        worker = threading.Thread(target=_run_late_rerank, name="tg-late-rerank", daemon=True)
        worker.start()
        worker.join(timeout=budget_ms / 1000.0)

        if worker.is_alive():
            # Still running at the deadline (hung or merely too slow): do NOT wait -- abandon the
            # daemon thread and degrade. This is the only branch that bounds a genuinely HUNG
            # encoder; the old post-hoc check could never run for one.
            late_rank_fallback_reason = (
                f"late rerank unavailable: budget exceeded (>{budget_ms}ms at pool_k={pool_k})"
            )
            sys.stderr.write(f"tg: {late_rank_fallback_reason}\n")
        elif rerank_error:
            exc = rerank_error[0]
            if isinstance(exc, LateRerankUnavailableError):
                # RECOVERABLE (e.g. a malformed embedding shape): degrade, never crash.
                late_rank_fallback_reason = str(exc)
                sys.stderr.write(f"tg: {late_rank_fallback_reason}\n")
            else:
                # A genuine encode-time fault -> propagate (Backend Fail-Closed Contract): never
                # silently degrade a real error into a plausible-but-wrong ranking.
                raise exc
        else:
            fused_order = rerank_result[0] + fused_order[pool_k:]

    # Position in the fused order is a monotonic proxy for the underlying RRF score: RRF ties are
    # already broken by ascending chunk index before this list is built, so using position
    # preserves the exact fused ordering while giving `match_score` below a single comparable
    # per-chunk number (mirrors `chunk_scores` in `rerank_by_bm25`).
    fused_score: dict[int, float] = {
        chunk_idx: 1.0 / (1 + position) for position, chunk_idx in enumerate(fused_order)
    }

    # file -> [(start_line, end_line, chunk_index)] for line-containment lookup.
    by_file: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for i, chunk in enumerate(chunks):
        by_file[chunk.file_path].append((chunk.start_line, chunk.end_line, i))

    def match_score(match) -> float:  # type: ignore[no-untyped-def]
        best = 0.0
        for start, end, i in by_file.get(match.file, ()):
            if start <= match.line_number <= end:
                best = max(best, fused_score.get(i, 0.0))
        return best

    # Stable sort by descending fused score (Python's sort is stable -> ties keep grep order).
    reranked = sorted(result.matches, key=match_score, reverse=True)
    # Append rather than overwrite, folding in EVERY reason source in the order it was produced:
    # the caller's own pre-existing reason (e.g. a dense-leg degrade set before this call), then
    # the corpus-cap reason (#128d, set while building the BM25 index above), then the late-rerank
    # reason (set last, during the late-interaction stage above) -- see T6's bidirectional
    # invariant (exactly one of "order provably changed, reason untouched" / "reason non-None"
    # holds; this is the "reason non-None" side, generalized from two sources to three).
    combined_parts = [
        part
        for part in (result.rank_fallback_reason, corpus_cap_reason, late_rank_fallback_reason)
        if part
    ]
    if combined_parts:
        return dataclasses.replace(
            result, matches=reranked, rank_fallback_reason="; ".join(combined_parts)
        )
    return dataclasses.replace(result, matches=reranked)
