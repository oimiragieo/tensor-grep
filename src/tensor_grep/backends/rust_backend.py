from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

try:
    from tensor_grep.rust_core import RustBackend as NativeRustBackend

    HAVE_RUST = True
except ImportError:
    HAVE_RUST = False


class RustCoreBackend(ComputeBackend):
    """Python wrapper implementing the ComputeBackend interface around the PyO3 Rust extension."""

    def __init__(self) -> None:
        if HAVE_RUST:
            self.inner = NativeRustBackend()
        else:
            self.inner = None

    def is_available(self) -> bool:
        return HAVE_RUST

    def search(self, file_path: str, pattern: str, config: SearchConfig | None = None) -> SearchResult:
        if not self.inner:
            return SearchResult(matches=[], total_files=0, total_matches=0)

        ignore_case = False
        count_only = False
        fixed_strings = False
        if config:
            if config.ignore_case:
                ignore_case = True
            if config.count:
                count_only = True
            if config.fixed_strings:
                fixed_strings = True

        try:
            if count_only:
                # Use highly-optimized Rayon parallel count fast-path
                total_count = self.inner.count_matches(
                    pattern, str(file_path), ignore_case, fixed_strings
                )
                return SearchResult(
                    matches=[],  # No lines needed for count
                    total_files=1 if total_count > 0 else 0,
                    total_matches=total_count,
                )

            results = self.inner.search(pattern, str(file_path), ignore_case, fixed_strings)
        except Exception:
            return SearchResult(matches=[], total_files=0, total_matches=0)

        matches = []
        for line_num, text in results:
            clean_text = text.rstrip("\r\n")
            matches.append(MatchLine(line_number=line_num, text=clean_text, file=str(file_path)))

        total_matches = len(matches)

        return SearchResult(
            matches=matches, total_files=1 if total_matches > 0 else 0, total_matches=total_matches
        )
