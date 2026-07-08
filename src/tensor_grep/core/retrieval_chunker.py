"""Split source files into newline-aligned, overlapping chunks for BM25/semantic ranking.

Per-chunk (not per-line, not per-file) is the granularity the design council settled on: it keeps
the vector/posting count bounded while preserving intra-file locality. :func:`chunk_file` is the
default, always-on path -- plain line windows, no AST required. A loud MAX_CHUNKS guard prevents a
pathological repo from OOM-ing.

:func:`chunk_file_structural` adds an OPT-IN alternative: cAST (arxiv 2506.15655) structural AST
chunking via tree-sitter split-then-merge -- parse the file, recursively split any AST node whose
own non-whitespace-char span exceeds the budget (module -> class/def -> block -> statement),
then greedily merge adjacent small sibling spans back up to the budget so chunk boundaries land on
syntactic units instead of arbitrary line counts. It is wired in transparently: :func:`chunk_file`
itself checks the ``TG_CHUNKER`` environment variable and dispatches to the structural path when
(and only when) it is set to ``"structural"`` -- every existing caller (``reranker.py``,
``semantic_index.py``, ``main.py``) already calls ``chunk_file()`` with no signature change, so
setting the env var is the entire opt-in surface. Unset (or any other value) is BYTE-IDENTICAL to
the pre-cAST behavior -- this PR does not flip the default; that is a separate, evidence-gated
change after a golden-set retrieval-quality measurement (chunk boundaries are ranking-fragile).

FAIL-OPEN CONTRACT: :func:`chunk_file_structural` never raises. Any condition that would prevent a
faithful structural chunking -- no tree-sitter grammar registered for the file's suffix, an
unreadable/undecodable file, a syntax-error parse (``tree.root_node.has_error``), or a budget
pathology that would itself blow past ``MAX_CHUNKS`` -- falls open to today's line-window chunker
(:func:`chunk_file`'s defaults) instead. The one loud exception in this module remains the
line-window path's own ``MAX_CHUNKS`` guard (unchanged), which the fail-open fallback can still
trip on a genuinely pathological file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_CHUNKS: int = 100_000

# The env var that opts a caller into cAST structural chunking. Anything other than this exact
# value (including unset) keeps today's line-window behavior byte-identical.
CHUNKER_MODE_ENV_VAR = "TG_CHUNKER"
_STRUCTURAL_MODE_VALUE = "structural"
STRUCTURAL_CHUNKER_MODE = "structural"
FIXED_WINDOW_CHUNKER_MODE = "fixed-window"

# Heuristic mapping from chunk_file's line-based ``chunk_size``/``overlap`` contract onto the
# paper's non-whitespace-char budget, used only by chunk_file()'s TG_CHUNKER=structural dispatch.
# Not a precision claim -- just a reasonable default so the opt-in path targets roughly the same
# chunk granularity as the line-window default (30 lines) it stands beside.
_STRUCTURAL_CHARS_PER_LINE = 40
DEFAULT_STRUCTURAL_BUDGET = 30 * _STRUCTURAL_CHARS_PER_LINE  # 1200

_STRUCTURAL_JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs"}


@dataclass(frozen=True)
class Chunk:
    """A contiguous window of a file. Line numbers are 1-based and inclusive."""

    file_path: str
    start_line: int
    end_line: int
    text: str


def current_chunker_mode() -> str:
    """The chunker mode :func:`chunk_file` will use right now, per ``TG_CHUNKER``.

    Exposed so ``semantic_index.py`` can fold the active mode into its persisted index's
    schema/version key -- a structural-chunked index and a fixed-window index must never silently
    fuse (the silent-mixed-chunker bug class), so the persisted meta records which mode built it
    and the loader refuses to reuse an index built under a different mode.
    """
    if os.environ.get(CHUNKER_MODE_ENV_VAR) == _STRUCTURAL_MODE_VALUE:
        return STRUCTURAL_CHUNKER_MODE
    return FIXED_WINDOW_CHUNKER_MODE


def chunk_file(
    file_path: str,
    *,
    chunk_size: int = 30,
    overlap: int = 5,
) -> list[Chunk]:
    """Split ``file_path`` into ``chunk_size``-ish windows, line-window by default.

    Returns an empty list for unreadable or empty files. Raises ``RuntimeError`` if the file would
    produce more than :data:`MAX_CHUNKS` chunks (a loud failure beats a silent OOM).

    When ``TG_CHUNKER=structural`` is set in the environment, dispatches to
    :func:`chunk_file_structural` instead (mapping ``chunk_size``/``overlap`` onto its
    char-budget/overlap-context contract) -- see the module docstring. Unset (or any other value)
    is byte-identical to the pre-cAST implementation.
    """
    if current_chunker_mode() == STRUCTURAL_CHUNKER_MODE:
        budget = max(1, chunk_size) * _STRUCTURAL_CHARS_PER_LINE
        try:
            return chunk_file_structural(file_path, budget=budget, overlap_context=max(0, overlap))
        except Exception:
            # Last-resort net: chunk_file_structural already fails open internally for every
            # EXPECTED condition (missing grammar, parse error, budget pathology). This catches
            # only a genuinely unexpected internal bug, so the opt-in flag stays zero-risk even
            # if the structural path has a defect -- it degrades to the line-window chunker below
            # rather than ever raising or returning nothing.
            pass

    return _chunk_file_line_windows(file_path, chunk_size=chunk_size, overlap=overlap)


def _chunk_file_line_windows(
    file_path: str,
    *,
    chunk_size: int = 30,
    overlap: int = 5,
) -> list[Chunk]:
    """The original (pre-cAST) line-window chunker. Kept as a private helper so both
    :func:`chunk_file`'s default path and :func:`chunk_file_structural`'s fail-open fallback share
    one implementation."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError:
        return []

    lines = raw.splitlines(keepends=True)
    total = len(lines)
    if total == 0:
        return []

    step = max(1, chunk_size - overlap)
    chunks: list[Chunk] = []
    start = 0  # 0-based index into ``lines``
    while start < total:
        end = min(start + chunk_size, total)  # exclusive
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=start + 1,
                end_line=end,
                text="".join(lines[start:end]),
            )
        )
        if len(chunks) > MAX_CHUNKS:
            raise RuntimeError(
                f"MAX_CHUNKS ({MAX_CHUNKS}) exceeded while chunking {file_path!r}. "
                "Use a larger chunk_size or scope the search to fewer files."
            )
        if end == total:
            break
        start += step

    return chunks


# ---------------------------------------------------------------------------
# cAST structural chunking (arxiv 2506.15655): tree-sitter split-then-merge.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _structural_python_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_python
    except ImportError:
        return None
    language = tree_sitter.Language(tree_sitter_python.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=1)
def _structural_javascript_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None
    language = tree_sitter.Language(tree_sitter_javascript.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=2)
def _structural_typescript_parser(*, tsx: bool) -> Any | None:
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
def _structural_rust_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_rust
    except ImportError:
        return None
    language = tree_sitter.Language(tree_sitter_rust.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=1)
def _structural_go_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_go
    except ImportError:
        return None
    language = tree_sitter.Language(tree_sitter_go.language())
    return tree_sitter.Parser(language)


def _structural_parser_for_path(file_path: str) -> Any | None:
    """Best-effort tree-sitter parser for ``file_path``'s suffix, or ``None`` when the language is
    unregistered here or its grammar package is not installed -- either way the caller falls open
    to line windows. Mirrors ``repo_map.py``'s per-language ``_xxx_parser()`` factories (same
    lru_cache-per-language shape) rather than importing them: ``repo_map.py`` (cli layer) already
    imports from ``core``, so importing back would cycle, and its module is far heavier than this
    self-contained lookup needs."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".py":
        return _structural_python_parser()
    if suffix in _STRUCTURAL_JS_SUFFIXES:
        return _structural_javascript_parser()
    if suffix == ".ts":
        return _structural_typescript_parser(tsx=False)
    if suffix == ".tsx":
        return _structural_typescript_parser(tsx=True)
    if suffix == ".rs":
        return _structural_rust_parser()
    if suffix == ".go":
        return _structural_go_parser()
    return None


def _non_ws_len(text: str) -> int:
    """Non-whitespace character count -- the paper's chunk-size unit (not raw char/line count, so
    indentation and blank-line padding don't inflate a chunk's measured size)."""
    return sum(1 for ch in text if not ch.isspace())


def _leaf_units(node: Any, budget: int) -> list[tuple[int, int]]:
    """Ordered ``(start_line, end_line)`` (1-based, inclusive) spans covering *node*'s full
    extent: *node* itself if its non-whitespace span already fits ``budget`` (or it has no
    children left to descend into -- an atomic token that still exceeds budget has nothing smaller
    to split it into and is returned as-is), otherwise the concatenation of its children's own
    leaf units, recursively. This is the SPLIT half of split-then-merge."""
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    children = node.children
    if not children or _non_ws_len(node.text.decode("utf-8", errors="replace")) <= budget:
        return [(start_line, end_line)]
    units: list[tuple[int, int]] = []
    for child in children:
        units.extend(_leaf_units(child, budget))
    return units


def _collapse_same_line_units(units: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Coalesce consecutive leaf units that land on the same (or an overlapping) source line.

    ``Chunk`` is line-granularity, but ``_leaf_units`` walks the AST token-by-token -- several
    sibling tokens (e.g. a ``def`` keyword, a function name, its ``()`` parameters, and the
    trailing ``:``) routinely share a single source line. Left un-collapsed, each of those tokens
    would materialize as its own line-identical duplicate unit, and (since the *combined* text of
    two same-line units never shrinks below either one's own already-over-budget length) the merge
    step below could never fold them back together. Collapsing same-line runs BEFORE merging
    restores one unit per distinguishable line span."""
    if not units:
        return []
    collapsed: list[tuple[int, int]] = [units[0]]
    for start, end in units[1:]:
        last_start, last_end = collapsed[-1]
        if start <= last_end:
            collapsed[-1] = (last_start, max(last_end, end))
        else:
            collapsed.append((start, end))
    return collapsed


def _merge_units(
    units: list[tuple[int, int]],
    lines: list[str],
    budget: int,
) -> list[tuple[int, int]]:
    """Greedily coalesce adjacent spans (in document order) while their combined non-whitespace
    text still fits ``budget`` -- the MERGE half of split-then-merge, healing the small fragments
    ``_leaf_units`` produces (e.g. a ``def`` keyword token split from its own body) back into
    denser chunks instead of leaving tiny leftover slivers."""
    if not units:
        return []
    merged: list[tuple[int, int]] = []
    cur_start, cur_end = units[0]
    for start, end in units[1:]:
        candidate_start = min(cur_start, start)
        candidate_end = max(cur_end, end)
        candidate_text = "".join(lines[candidate_start - 1 : candidate_end])
        if _non_ws_len(candidate_text) <= budget:
            cur_start, cur_end = candidate_start, candidate_end
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    merged.append((cur_start, cur_end))
    return merged


def _materialize_structural_chunks(
    file_path: str,
    spans: list[tuple[int, int]],
    lines: list[str],
    overlap_context: int,
) -> list[Chunk]:
    total = len(lines)
    chunks: list[Chunk] = []
    for start, end in spans:
        ctx_start = max(1, start - overlap_context)
        ctx_end = min(total, end + overlap_context)
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=ctx_start,
                end_line=ctx_end,
                text="".join(lines[ctx_start - 1 : ctx_end]),
            )
        )
    return chunks


def chunk_file_structural(
    file_path: str,
    *,
    budget: int = DEFAULT_STRUCTURAL_BUDGET,
    overlap_context: int = 0,
) -> list[Chunk]:
    """cAST (arxiv 2506.15655) structural AST chunking: parse ``file_path``, recursively split any
    AST node whose non-whitespace span exceeds ``budget`` (module -> class/def -> block ->
    statement) into its children, then greedily merge adjacent small sibling spans back up to
    ``budget``. ``overlap_context`` optionally pads each resulting chunk by that many extra lines
    of surrounding context on each side (0 = chunks are exactly the merged structural spans, no
    overlap).

    FAIL-OPEN (never raises): falls back to :func:`chunk_file`'s plain line-window defaults
    (``chunk_size=30, overlap=5``) whenever the file's language has no tree-sitter grammar
    registered/installed, the file can't be read or UTF-8-decoded, the parse contains a syntax
    error (``tree.root_node.has_error``), the structural pass raises for any other reason, or the
    resulting chunk count would itself exceed :data:`MAX_CHUNKS` (a budget pathology -- the
    fallback's own loud MAX_CHUNKS guard still applies to a genuinely oversized file).
    """
    parser = _structural_parser_for_path(file_path)
    if parser is None:
        return _chunk_file_line_windows(file_path)

    try:
        with open(file_path, "rb") as handle:
            source_bytes = handle.read()
    except OSError:
        return _chunk_file_line_windows(file_path)

    if not source_bytes:
        return []

    try:
        tree = parser.parse(source_bytes)
        if tree.root_node.has_error:
            return _chunk_file_line_windows(file_path)

        # Re-read in text mode (universal newlines) for the line list used to materialize chunk
        # text -- matches `_chunk_file_line_windows`'s own read exactly (CRLF -> LF), so a chunk's
        # `.text` is never byte-for-byte divergent from what the line-window chunker would have
        # produced for the same lines. Row/column numbers from `tree`, parsed against the raw
        # bytes above, still line up: both `str.splitlines()` and tree-sitter's own row counting
        # treat a CRLF pair as a single line break.
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        lines = text.splitlines(keepends=True)
        if not lines:
            return []

        raw_units = _leaf_units(tree.root_node, budget)
        if not raw_units:
            return _chunk_file_line_windows(file_path)

        line_units = _collapse_same_line_units(raw_units)
        merged_spans = _merge_units(line_units, lines, budget)
        chunks = _materialize_structural_chunks(file_path, merged_spans, lines, overlap_context)
    except Exception:
        return _chunk_file_line_windows(file_path)

    if len(chunks) > MAX_CHUNKS:
        return _chunk_file_line_windows(file_path)

    return chunks
