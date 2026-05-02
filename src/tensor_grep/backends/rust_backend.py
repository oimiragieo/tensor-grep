import re
from pathlib import Path

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

    @staticmethod
    def _parse_max_filesize_bytes(value: str) -> int | None:
        match = re.fullmatch(r"\s*(\d+)\s*([kmgt]?b?)?\s*", value, flags=re.IGNORECASE)
        if not match:
            return None
        amount = int(match.group(1))
        suffix = (match.group(2) or "").lower()
        multiplier_by_suffix = {
            "": 1,
            "b": 1,
            "k": 1024,
            "kb": 1024,
            "m": 1024**2,
            "mb": 1024**2,
            "g": 1024**3,
            "gb": 1024**3,
            "t": 1024**4,
            "tb": 1024**4,
        }
        multiplier = multiplier_by_suffix.get(suffix)
        if multiplier is None:
            return None
        return amount * multiplier

    def _file_exceeds_max_filesize(self, file_path: str, max_filesize: str) -> bool:
        limit_bytes = self._parse_max_filesize_bytes(max_filesize)
        if limit_bytes is None:
            return False
        path = Path(file_path)
        if not path.is_file():
            return False
        try:
            return path.stat().st_size > limit_bytes
        except OSError:
            return False

    @staticmethod
    def _should_search_binary_as_text(config: SearchConfig | None) -> bool:
        return bool(config and (config.text or config.binary))

    @staticmethod
    def _is_binary_file(file_path: str) -> bool:
        path = Path(file_path)
        if not path.is_file():
            return False
        try:
            with path.open("rb") as handle:
                return b"\0" in handle.read(8192)
        except OSError:
            return False

    @staticmethod
    def _binary_notice_text(file_path: str) -> str:
        try:
            offset = Path(file_path).read_bytes().find(b"\0")
        except OSError:
            offset = -1
        if offset < 0:
            offset = 0
        return f'binary file matches (found "/0" byte around offset {offset})'

    @staticmethod
    def _binary_file_matches_pattern(
        file_path: str, pattern: str, config: SearchConfig | None
    ) -> bool:
        try:
            haystack = Path(file_path).read_bytes()
        except OSError:
            return False

        ignore_case = bool(
            config and (config.ignore_case or (config.smart_case and pattern.islower()))
        )
        pattern_bytes = pattern.encode("utf-8", errors="surrogateescape")
        if config and config.fixed_strings:
            if ignore_case:
                return pattern_bytes.lower() in haystack.lower()
            return pattern_bytes in haystack

        flags = re.IGNORECASE if ignore_case else 0
        try:
            return re.search(pattern_bytes, haystack, flags=flags) is not None
        except re.error:
            escaped = re.escape(pattern_bytes)
            return re.search(escaped, haystack, flags=flags) is not None

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

        if max_filesize and self._file_exceeds_max_filesize(str(file_path), max_filesize):
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="RustCoreBackend",
                routing_reason="rust_max_filesize_skipped",
                routing_distributed=False,
                routing_worker_count=1,
            )

        if not self._should_search_binary_as_text(config) and self._is_binary_file(str(file_path)):
            matches = []
            if self._binary_file_matches_pattern(str(file_path), pattern, config):
                matches.append(
                    MatchLine(
                        line_number=1,
                        text=self._binary_notice_text(str(file_path)),
                        file=str(file_path),
                        meta_variables={"binary_notice": True},
                    )
                )
            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="RustCoreBackend",
                routing_reason="rust_binary_notice" if matches else "rust_binary_skipped",
                routing_distributed=False,
                routing_worker_count=1,
            )

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
