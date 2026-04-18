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
    routing_gpu_device_ids: list[int] = field(default_factory=list)
    routing_gpu_chunk_plan_mb: list[tuple[int, int]] = field(default_factory=list)
    routing_distributed: bool = False
    routing_worker_count: int = 0

    @property
    def is_empty(self) -> bool:
        return self.total_matches == 0
