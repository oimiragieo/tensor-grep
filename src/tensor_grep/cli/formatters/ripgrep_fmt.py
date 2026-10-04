import sys
from collections import defaultdict

from tensor_grep.backends.rust_backend import _first_nul_offset
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.formatters.json_fmt import _REGEX_META, _literal_column_index
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class RipgrepFormatter(OutputFormatter):
    def __init__(self, config: SearchConfig | None = None):
        self.config = config or SearchConfig()
        self._column_notice_emitted = False

    @staticmethod
    def _binary_notice(file_path: str) -> str:
        try:
            offset = _first_nul_offset(file_path)
        except OSError:
            offset = -1
        if offset < 0:
            offset = 0
        # ripgrep prints the NUL escape as "\0"; emit the same so byte-for-byte parity
        # assertions against rg's notice hold (audit B19).
        return f'binary file matches (found "\\0" byte around offset {offset})'

    @staticmethod
    def _is_binary_notice_match(match: MatchLine) -> bool:
        if match.meta_variables and match.meta_variables.get("binary_notice") is True:
            return True
        return "\0" in str(match.text)

    def _binary_notice_for_match(self, match: MatchLine) -> str:
        if match.meta_variables and match.meta_variables.get("binary_notice") is True:
            return str(match.text)
        return self._binary_notice(match.file)

    def _format_path(self, file_path: str) -> str:
        if self.config.path_separator is None:
            return file_path
        separator = self.config.path_separator
        return file_path.replace("\\", separator).replace("/", separator)

    def _column_for_match(self, match: MatchLine) -> int:
        if match.range is not None:
            start = match.range.get("start")
            if isinstance(start, dict):
                column = start.get("column")
                if isinstance(column, int):
                    return column + 1

        pattern = self.config.query_pattern or ""
        if not pattern and self.config.regexp:
            pattern = self.config.regexp[0]
        is_literal = bool(self.config.fixed_strings) or not (_REGEX_META & set(pattern))
        if not pattern:
            return 1  # an empty pattern matches at the start of the line: column 1 is exact
        if not is_literal:
            # KNOWN APPROXIMATION: --column/--vimgrep mandate a column field (rg never omits it).
            # A regex line with no authoritative offset prints 1 (never a guess from re-running
            # the user's regex) and says so on stderr. The pipeline routes column requests to rg
            # whenever rg exists, so this is only reached with rg absent.
            return self._approximate_column()
        if self.config.word_regexp or self.config.line_regexp:
            return self._approximate_column()  # find() ignores -w/-x boundaries -- never guess
        # explicit -s (case_sensitive) overrides smart case, as in RipgrepBackend._build_cmd
        ignore_case = bool(
            self.config.ignore_case
            or (self.config.smart_case and not self.config.case_sensitive and pattern.islower())
        )
        index = _literal_column_index(match.text, pattern, ignore_case=ignore_case)
        if index < 0:
            return self._approximate_column()
        # ripgrep/--vimgrep columns are BYTE offsets, not character indices: advance
        # by the UTF-8 width of the text before the match (audit MED parity).
        return len(match.text[:index].encode("utf-8")) + 1

    def _approximate_column(self) -> int:
        if not self._column_notice_emitted:
            self._column_notice_emitted = True
            sys.stderr.write("tg: column approximated (1): rg not available for exact offsets\n")
        return 1

    def _submatch_columns(self, match: MatchLine) -> list[int] | None:
        """rg's authoritative 1-based byte columns for every occurrence on this line.

        rg submatch ``start`` is a 0-based BYTE offset into the line, so ``start + 1`` is rg's
        1-based byte column directly — no Python regex, no first-occurrence-only guess. Returns
        None for non-rg backends / context lines (no submatches) so those fall through to the
        existing single-column path unchanged.
        """
        subs = match.submatches
        if not subs:
            return None
        columns: list[int] = []
        for sub in subs:
            if isinstance(sub, dict):
                start = sub.get("start")
                if isinstance(start, int):
                    columns.append(start + 1)
        return columns or None

    def format(self, result: SearchResult) -> str:
        lines = []

        if self.config.count or self.config.count_matches:
            if result.total_matches > 0 or self.config.include_zero:
                # Group counts by file to match ripgrep output
                counts_by_file: dict[str, int] = defaultdict(int)
                if result.match_counts_by_file:
                    counts_by_file.update(result.match_counts_by_file)
                else:
                    for match in result.matches:
                        counts_by_file[match.file] += 1

                if not counts_by_file and result.total_matches > 0:
                    # Fallback if result matches aren't populated but total is
                    lines.append(f"{result.total_matches}")
                    return "\n".join(lines)

                for file_path, count in counts_by_file.items():
                    if self.config.with_filename or (
                        self.config.file_patterns is None
                        and not self.config.no_filename
                        and result.total_files > 1
                    ):
                        lines.append(f"{self._format_path(str(file_path))}:{count}")
                    else:
                        lines.append(f"{count}")
            return "\n".join(lines)

        if not self.config.text and not self.config.binary:
            binary_notice_matches = [
                match for match in result.matches if self._is_binary_notice_match(match)
            ]
            if binary_notice_matches:
                for match in sorted(binary_notice_matches, key=lambda current: current.file):
                    message = self._binary_notice_for_match(match)
                    file_path = self._format_path(str(match.file))
                    if self.config.with_filename or (
                        self.config.file_patterns is None
                        and not self.config.no_filename
                        and result.total_files > 1
                    ):
                        lines.append(f"{file_path}:{message}")
                    else:
                        lines.append(message)

                non_binary_matches = [
                    match for match in result.matches if not self._is_binary_notice_match(match)
                ]
            else:
                non_binary_matches = result.matches
        else:
            non_binary_matches = result.matches

        for match in non_binary_matches:
            # rg supplies per-occurrence byte offsets; --vimgrep/--column emit ONE row per
            # occurrence (matching real `rg --vimgrep`), not just the first. Non-rg backends /
            # context lines have no submatches -> single row via _column_for_match, unchanged.
            submatch_columns = self._submatch_columns(match)
            if self.config.vimgrep:
                columns = submatch_columns or [self._column_for_match(match)]
                for column in columns:
                    lines.append(
                        ":".join([
                            self._format_path(str(match.file)),
                            str(match.line_number),
                            str(column),
                            str(match.text),
                        ])
                    )
                continue

            prefix_parts = []
            if self.config.with_filename or (
                self.config.file_patterns is None
                and not self.config.no_filename
                and result.total_files > 1
            ):
                prefix_parts.append(self._format_path(str(match.file)))

            if self.config.line_number:
                prefix_parts.append(str(match.line_number))

            if self.config.column:
                columns = submatch_columns or [self._column_for_match(match)]
                for column in columns:
                    lines.append(":".join([*prefix_parts, str(column), str(match.text)]))
            else:
                lines.append(":".join([*prefix_parts, str(match.text)]))
        return "\n".join(lines)
