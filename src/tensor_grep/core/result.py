from dataclasses import dataclass, field

#: Wire-schema version stamped into every ``--json`` / ``--ndjson`` envelope as both
#: ``version`` and ``schema_version``.
#:
#: This is a LITERAL on purpose (DC-001, 2026-08-19). It was previously derived at
#: runtime by regex-scraping ``const JSON_OUTPUT_VERSION`` out of
#: ``rust_core/src/main.rs`` through ``Path(__file__).resolve().parents[3]``. That
#: works in a dev checkout, where parents[3] is the repo root -- and can never work in
#: a wheel, where it resolves to the directory above ``site-packages`` and ``rust_core/``
#: is simply absent (pyproject's ``[tool.maturin] include`` does not ship it). The
#: scrape caught ``OSError`` and defaulted to 1, so every published install would have
#: gone on reporting a stale schema version, silently, from the first bump onward.
#:
#: Keep this in lockstep with ``JSON_OUTPUT_VERSION`` in ``rust_core/src/main.rs``.
#: ``tests/unit/test_json_output_version_pin.py`` cross-pins the two and fails CI if
#: they diverge; it runs only from a dev checkout, which is the one place both sources
#: are visible at once.
JSON_OUTPUT_VERSION = 1


def strip_line_terminator(text: str) -> str:
    r"""Strip AT MOST one trailing ``\n`` from a raw line's text -- never a trailing ``\r``.

    ``MatchLine.text`` must hold a line's content with its own terminating newline removed,
    but with everything else -- including a genuine trailing ``\r`` from a CRLF-terminated
    source line -- left byte-for-byte intact, matching how real ``rg`` reports a CRLF line's
    content (verified directly against ``rg.exe``: its plain-text AND ``--json`` output both
    keep the file's own ``\r``).

    Every backend used to call ``text.rstrip("\n\r")`` / ``text.rstrip("\r\n")`` here, which
    strips ANY trailing run of ``\r``/``\n`` in ANY order -- e.g. for a CRLF line whose
    content itself legitimately ends in ``\r`` (Rust's line-splitter, and a raw ``rg --json``
    "lines" field, both include that ``\r``), this silently ate it too. On Windows that
    divergence used to be masked (or, once the rg-parity test suites started comparing raw
    bytes instead of `text=True`-decoded strings, exposed) by the SEPARATE stdout
    universal-newlines bug this fix is paired with (task #262) -- but even with stdout fixed,
    every one of these `rstrip` call sites would still corrupt a CRLF file's OWN `\r` on the
    way in, independent of anything happening at the stdout layer. ``removesuffix`` only ever
    removes the single trailing ``\n`` that every engine here is known to append; a real
    trailing ``\r`` survives.
    """
    return text.removesuffix("\n")


def split_source_lines(text: str) -> list[str]:
    r"""Split a whole file's decoded text into per-line strings for line-oriented matching,
    keeping any trailing ``\r`` from a CRLF line intact.

    Never ``str.splitlines()`` (and, for a StringZilla ``Str``, never its own
    ``.splitlines()`` either): both treat ``\r\n``, a bare ``\r``, and a bare ``\n`` as
    equivalent line breaks, and BOTH strip the terminator characters entirely -- silently
    eating a CRLF line's genuine trailing ``\r`` before a single match is even evaluated,
    independent of anything fixed in ``strip_line_terminator`` or the stdout-writing layer
    (task #262). Splitting on a bare ``\n`` only preserves that ``\r`` as part of the
    line's own content, matching how ``rg`` and the Rust engine both treat a CRLF file.

    Mirrors ``str.splitlines()``'s line COUNT for well-formed trailing-newline input by
    dropping the one spurious empty element a final ``\n`` produces via a plain
    ``str.split("\n")`` (``"a\nb\n".split("\n")`` has a trailing ``""`` that
    ``"a\nb\n".splitlines()`` does not) -- so a caller that assumed that invariant does not
    silently gain an extra blank line; only a genuine embedded/trailing ``\r`` now survives.
    """
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@dataclass(frozen=True)
class MatchLine:
    line_number: int
    text: str
    file: str
    # M16 F1: byte span of the matched AST node (start_byte inclusive,
    # end_byte exclusive). Populated by the AST backends; used by composite-
    # rule scan accounting to deduplicate by NODE SPAN (two distinct nodes on
    # one line are distinct matches). None when the backend only has the line.
    start_byte: int | None = None
    end_byte: int | None = None
    range: dict[str, object] | None = None
    meta_variables: dict[str, object] | None = None
    # rg's authoritative per-occurrence byte offsets for a multi-match line (each entry is an rg
    # submatch: {"match": {...}, "start": int, "end": int}). Populated by RipgrepBackend; consumed
    # by --vimgrep/--column output shaping. compare=False keeps this frozen dataclass HASHABLE — a
    # tuple of dicts is not hashable, so including it would break hash(MatchLine(...)) once
    # populated. Excluding it from == is correct: these offsets are a pure function of text+line,
    # so two matches equal on those fields are equal here too.
    submatches: tuple[dict[str, object], ...] | None = field(default=None, compare=False)
    container: dict[str, object] | None = field(default=None, compare=False)
    why_ranked: list[str] | None = field(default=None, compare=False)


@dataclass
class SearchResult:
    matches: list[MatchLine] = field(default_factory=list)
    matched_file_paths: list[str] = field(default_factory=list)
    match_counts_by_file: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_matches: int = 0
    sidecar_used: bool = False
    routing_backend: str | None = None
    routing_reason: str | None = None
    requested_gpu_device_ids: list[int] = field(default_factory=list)
    # Advisory, additive, and deliberately NOT part of the incompleteness family. A search whose
    # PATH defaulted to the cwd RAN TO COMPLETION -- it answered a narrower question than the
    # caller may have meant. Setting `result_incomplete` here would be a lie AND would flip the
    # exit code to 2, breaking the closed 0/1/2 contract. Live dogfood (v1.101.22) asked for the
    # stderr scope note to reach the JSON body: "agents that ignore stderr can miss it".
    path_was_defaulted: bool = False
    scope_note: str | None = None
    ast_enrichment_truncated: bool = False
    routing_gpu_device_ids: list[int] = field(default_factory=list)
    routing_gpu_chunk_plan_mb: list[tuple[int, int]] = field(default_factory=list)
    routing_distributed: bool = False
    routing_worker_count: int = 0
    # M9: the DISTINCT backends actually used across a heterogeneous per-file scan. `routing_backend`
    # above is last-write-wins (a per-file merge overwrites it each call), so on a scan where some
    # files ran on Torch and others fell back to CPU, `routing_backend` alone reports only whichever
    # file was processed last — silently hiding that matches came from more than one engine. These
    # two additive fields carry the truth: `routing_backends_seen` is the accumulated set (insertion
    # order), `is_mixed_routing` is True once >1 distinct backend contributed.
    routing_backends_seen: list[str] = field(default_factory=list)
    is_mixed_routing: bool = False
    # GPU execution telemetry — optional; None when not measured or not applicable.
    # Populated by GPU backends that instrument their kernel and transfer timing.
    kernel_time_ms: float | None = None
    transfer_time_ms: float | None = None
    staging_bytes: int | None = None
    fallback_reason: str | None = None
    # Partial-results signal: the backend produced SOME output but a soft per-item error
    # suppressed the rest (e.g. rg exit 2 with matches for the readable files). Distinct from
    # fallback_reason (which means "the execution engine was swapped") — conflating them would
    # emit a false "we fell back" signal to doctor/JSON. Drives the rg-parity exit code 2 and a
    # machine-visible "suppression != absence" marker on the JSON/MCP envelopes.
    result_incomplete: bool = False
    incomplete_reason: str | None = None
    # Task #276 slice 1: a machine-branchable CLASS for `incomplete_reason`, so an agent doesn't
    # have to string-sniff a human-readable message to decide whether retrying with a bigger
    # budget could help. One of "unreadable_path" / "scan_limit" / "deadline" / "timeout" --
    # closed vocabulary. NOT universally set on every `result_incomplete=True` producer in this
    # codebase: it currently covers `RipgrepBackend`'s exit-2/timeout branches, the CPU/native
    # search route's own directory-scan-truncation consumption (both in `cli/main.py`'s
    # `search_command`), and `tg find`'s deadline/`--max-repo-files`/chunk-cap causes (also
    # `cli/main.py`) -- see each call site for the exact mapping. It is deliberately left unset
    # (never `False`-defaulted to a guessed value) when a cause doesn't cleanly map onto the
    # closed vocabulary (e.g. `tg find`'s per-file chunk/parse error) or hasn't been classified
    # yet for a given route -- `repo_map.py`'s SEPARATE `_mark_result_incomplete` mechanism
    # (the symbol commands: `defs`/`refs`/`callers`/`blast-radius`/`map`/`context`/`agent`/
    # `edit-plan`) and the MCP tool envelopes (`mcp_server.py`'s own `result_incomplete` sites)
    # do NOT set this field at all. None when `result_incomplete` is False (never emitted -- see
    # json_fmt's omit-when-complete rule) OR when a route sets `result_incomplete` without
    # classifying its cause (also never emitted -- `json_fmt._routing_envelope` only adds the
    # key when this is non-None).
    incomplete_reason_class: str | None = None
    # Set ONLY when `--semantic` was requested but the dense leg could not run (extra absent,
    # model not fetched, or a shape/dim-mismatch degrade) -- distinct from `fallback_reason`
    # (reserved for a full engine swap) and from `incomplete_reason` (partial results). Emitted
    # to stderr + this field so a BM25-only result is never mislabeled "semantic" output.
    rank_fallback_reason: str | None = None
    install_state: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.total_matches == 0


def merge_runtime_routing(aggregate: SearchResult, result: SearchResult) -> None:
    """Merge a backend's runtime routing metadata into an aggregate result.

    Runtime routing is authoritative when a backend internally falls back (for example
    Torch -> CPU for unsupported regex paths), so an aggregate seeded from the *selected*
    backend must adopt the runtime values rather than keep reporting the planned route.
    Shared by the CLI, MCP, and GPU-sidecar paths so the merge semantics cannot drift.
    """
    # M9: accumulate the distinct backends BEFORE the last-write-wins overwrite below, so a
    # heterogeneous per-file scan (e.g. Torch for some files, CPU-fallback for others) surfaces
    # every engine that contributed rather than only the last-merged one.
    for _seen_backend in (aggregate.routing_backend, result.routing_backend):
        if _seen_backend and _seen_backend not in aggregate.routing_backends_seen:
            aggregate.routing_backends_seen.append(_seen_backend)
    aggregate.is_mixed_routing = len(aggregate.routing_backends_seen) > 1
    if result.routing_backend:
        aggregate.routing_backend = result.routing_backend
        aggregate.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
        aggregate.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
    elif result.routing_gpu_device_ids or result.routing_gpu_chunk_plan_mb:
        aggregate.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
        aggregate.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
    if result.routing_reason:
        aggregate.routing_reason = result.routing_reason
    aggregate.routing_distributed = aggregate.routing_distributed or result.routing_distributed
    aggregate.routing_worker_count = max(
        aggregate.routing_worker_count, result.routing_worker_count
    )
    # Backlog #22: `sidecar_used` is monotonic like `result_incomplete` below -- once ANY
    # per-file result reports sidecar routing, the aggregate must keep reporting it, never
    # reset to False by a later file that happened to run natively. Previously unmerged: every
    # existing caller either sets `aggregate.sidecar_used` once, up front, before the per-file
    # loop starts (`sidecar.py`, always True) or never sets it at all (`cli/main.py`,
    # `mcp_server.py`, whose `core.pipeline.Pipeline` backends never set `sidecar_used=True` on
    # a per-file `SearchResult` today) -- so this OR-merge is a no-op for every current caller
    # and only starts mattering the day a backend on either of those paths legitimately reports
    # sidecar routing on a per-file result. Read by both the `--json` envelope's `sidecar_used`
    # field and `gpu_request_unhonoured()` (json_fmt.py), which the `tg search` exit-code
    # decision delegates to -- an unmerged sidecar signal there would silently misreport an
    # unhonoured explicit GPU request as honoured.
    aggregate.sidecar_used = aggregate.sidecar_used or result.sidecar_used
    # Partial-results incompleteness is monotonic: any incomplete sub-result taints the aggregate,
    # so ALL consumers (CLI, MCP, sidecar) inherit the rg-parity exit-2 + envelope marker uniformly.
    aggregate.result_incomplete = aggregate.result_incomplete or result.result_incomplete
    if result.incomplete_reason and not aggregate.incomplete_reason:
        aggregate.incomplete_reason = result.incomplete_reason
    if result.incomplete_reason_class and not aggregate.incomplete_reason_class:
        aggregate.incomplete_reason_class = result.incomplete_reason_class
