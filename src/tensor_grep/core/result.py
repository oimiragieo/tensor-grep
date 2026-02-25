from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatchLine:
    line_number: int
    text: str
    file: str


@dataclass
class SearchResult:
    matches: list[MatchLine] = field(default_factory=list)
    total_files: int = 0
    total_matches: int = 0

    @property
    def is_empty(self) -> bool:
        return self.total_matches == 0
