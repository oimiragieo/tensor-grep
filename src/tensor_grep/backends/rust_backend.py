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

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.inner:
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="RustCoreBackend",
                routing_reason="rust_unavailable",
                routing_distributed=False,
                routing_worker_count=1,
            )

        ignore_case = False
        fixed_strings = False
        invert_match = False
        count_only = False
        pcre2 = False
        max_filesize = None
        no_ignore_vcs = False

        if config:
            if config.ignore_case:
                ignore_case = True
            if config.fixed_strings:
                fixed_strings = True
            if config.invert_match:
                invert_match = True
            if config.count:
                count_only = True
            if config.pcre2:
                pcre2 = True
            if config.max_filesize:
                max_filesize = config.max_filesize
            if config.no_ignore_vcs:
                no_ignore_vcs = True

        # PCRE2 or advanced limits always route to ripgrep passthrough via Rust
        if pcre2 or max_filesize or no_ignore_vcs:
            try:
                exit_code = self.inner.execute_ripgrep(
                    [pattern],
                    str(file_path),
                    ignore_case,
                    fixed_strings,
                    invert_match,
                    count_only,
                    False,  # count_matches
                    config.line_number if config else True,
                    config.column if config else False,
                    config.only_matching if config else False,
                    config.context if config else None,
                    config.before_context if config else None,
                    config.after_context if config else None,
                    config.max_count if config else None,
                    config.word_regexp if config else False,
                    config.smart_case if config else False,
                    config.glob if config else [],
                    config.no_ignore if config else False,
                    no_ignore_vcs,
                    config.hidden if config else False,
                    config.follow if config else False,
                    config.text if config else False,
                    False,  # files_with_matches
                    False,  # files_without_match
                    config.file_type if config else [],
                    config.color if config else None,
                    config.replace_str if config else None,
                    pcre2,
                    max_filesize,
                )
                # Results are emitted directly to stdout by ripgrep binary
                return SearchResult(
                    matches=[],
                    total_files=1 if exit_code == 0 else 0,
                    total_matches=0,  # Unknown without parsing
                    routing_backend="RustCoreBackend",
                    routing_reason="rust_pcre2_passthrough" if pcre2 else "rust_limit_passthrough",
                    routing_distributed=False,
                    routing_worker_count=1,
                )
            except (AttributeError, Exception):
                # Fallback to standard Python-regex path if bridge fails
                pass

        try:
            if count_only and not invert_match:
                # Use highly-optimized Rayon parallel count fast-path
                total_count = self.inner.count_matches(
                    pattern, str(file_path), ignore_case, fixed_strings
                )
                return SearchResult(
                    matches=[],  # No lines needed for count
                    match_counts_by_file={str(file_path): total_count} if total_count > 0 else {},
                    total_files=1 if total_count > 0 else 0,
                    total_matches=total_count,
                    routing_backend="RustCoreBackend",
                    routing_reason="rust_count",
                    routing_distributed=False,
                    routing_worker_count=1,
                )

            # Support older signature and new signature smoothly
            try:
                results = self.inner.search(
                    pattern, str(file_path), ignore_case, fixed_strings, invert_match
                )
            except TypeError:
                results = self.inner.search(pattern, str(file_path), ignore_case, fixed_strings)
        except Exception:
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="RustCoreBackend",
                routing_reason="rust_exception",
                routing_distributed=False,
                routing_worker_count=1,
            )

        if config and config.max_count is not None:
            results = results[: config.max_count]

        matches = []
        for line_num, text in results:
            clean_text = text.rstrip("\r\n")
            matches.append(MatchLine(line_number=line_num, text=clean_text, file=str(file_path)))

        total_matches = len(matches)

        return SearchResult(
            matches=matches,
            total_files=1 if total_matches > 0 else 0,
            total_matches=total_matches,
            routing_backend="RustCoreBackend",
            routing_reason="rust_regex",
            routing_distributed=False,
            routing_worker_count=1,
        )
