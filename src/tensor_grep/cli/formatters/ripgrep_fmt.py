from collections import defaultdict
from pathlib import Path

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import SearchResult


class RipgrepFormatter(OutputFormatter):
    def __init__(self, config: SearchConfig | None = None):
        self.config = config or SearchConfig()

    @staticmethod
    def _binary_notice(file_path: str) -> str:
        try:
            offset = Path(file_path).read_bytes().find(b"\0")
        except OSError:
            offset = -1
        if offset < 0:
            offset = 0
        return f'binary file matches (found "/0" byte around offset {offset})'

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
                        lines.append(f"{file_path}:{count}")
                    else:
                        lines.append(f"{count}")
            return "\n".join(lines)

        if not self.config.text and not self.config.binary:
            binary_files = {match.file for match in result.matches if "\0" in str(match.text)}
            if binary_files:
                for file_path in sorted(binary_files):
                    message = self._binary_notice(file_path)
                    if self.config.with_filename or (
                        self.config.file_patterns is None
                        and not self.config.no_filename
                        and result.total_files > 1
                    ):
                        lines.append(f"{file_path}:{message}")
                    else:
                        lines.append(message)

                non_binary_matches = [
                    match for match in result.matches if match.file not in binary_files
                ]
            else:
                non_binary_matches = result.matches
        else:
            non_binary_matches = result.matches

        for match in non_binary_matches:
            parts = []
            if self.config.with_filename or (
                self.config.file_patterns is None
                and not self.config.no_filename
                and result.total_files > 1
            ):
                parts.append(str(match.file))

            if self.config.line_number:
                parts.append(str(match.line_number))

            parts.append(str(match.text))
            lines.append(":".join(parts))
        return "\n".join(lines)
