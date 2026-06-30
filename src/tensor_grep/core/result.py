from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatchLine:
    line_number: int
    text: str
    file: str
    range: dict[str, object] | None = None
    meta_variables: dict[str, object] | None = None


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
    # GPU execution telemetry — optional; None when not measured or not applicable.
    # Populated by GPU backends that instrument their kernel and transfer timing.
    kernel_time_ms: float | None = None
    transfer_time_ms: float | None = None
    staging_bytes: int | None = None
    fallback_reason: str | None = None

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
