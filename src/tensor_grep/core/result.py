from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatchLine:
    line_number: int
    text: str
    file: str
    range: dict[str, object] | None = None
    meta_variables: dict[str, object] | None = None
    # rg's authoritative per-occurrence byte offsets for a multi-match line (each entry is an rg
    # submatch: {"match": {...}, "start": int, "end": int}). Populated by RipgrepBackend; consumed
    # by --vimgrep/--column output shaping. compare=False keeps this frozen dataclass HASHABLE — a
    # tuple of dicts is not hashable, so including it would break hash(MatchLine(...)) once
    # populated. Excluding it from == is correct: these offsets are a pure function of text+line,
    # so two matches equal on those fields are equal here too.
    submatches: tuple[dict[str, object], ...] | None = field(default=None, compare=False)


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
    # Set ONLY when `--semantic` was requested but the dense leg could not run (extra absent,
    # model not fetched, or a shape/dim-mismatch degrade) -- distinct from `fallback_reason`
    # (reserved for a full engine swap) and from `incomplete_reason` (partial results). Emitted
    # to stderr + this field so a BM25-only result is never mislabeled "semantic" output.
    rank_fallback_reason: str | None = None

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
    # Partial-results incompleteness is monotonic: any incomplete sub-result taints the aggregate,
    # so ALL consumers (CLI, MCP, sidecar) inherit the rg-parity exit-2 + envelope marker uniformly.
    aggregate.result_incomplete = aggregate.result_incomplete or result.result_incomplete
    if result.incomplete_reason and not aggregate.incomplete_reason:
        aggregate.incomplete_reason = result.incomplete_reason
