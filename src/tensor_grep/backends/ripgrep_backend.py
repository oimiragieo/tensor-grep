import subprocess
from pathlib import Path

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class RipgrepBackend(ComputeBackend):
    """
    A backend that seamlessly delegates to the native `rg` (ripgrep) binary
    when installed on the system. Used for optimal single-threaded small-file
    searching and full parity with complex regex features.
    """

    def is_available(self) -> bool:
        return self._get_binary_name() is not None

    def _get_binary_name(self) -> str | None:
        from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

        path = resolve_ripgrep_binary()
        return str(path) if path else None

    def supports_pcre2(self) -> bool:
        """Check if the ripgrep binary was compiled with PCRE2 support."""
        binary = self._get_binary_name()
        if not binary:
            return False
        try:
            result = subprocess.run([binary, "--pcre2-version"], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False

    def search(
        self, file_path: str | list[str], pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if config and (config.count or config.count_matches):
            return self._search_counts(file_path=file_path, pattern=pattern, config=config)

        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=True)
        try:
            # We use check=False because rg exits with 1 if no matches are found
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, encoding="utf-8"
            )
            if result.returncode > 1:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"rg failed with exit code {result.returncode}: {stderr or 'no stderr output'}"
                )

            import json

            matches = []

            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        data_match = data["data"]
                        line_number = data_match.get("line_number", 0)
                        # We extract the pure text matched line
                        text = data_match.get("lines", {}).get("text", "").rstrip("\n\r")

                        path_str = data_match.get("path", {}).get("text", "")
                        if not path_str and isinstance(file_path, str):
                            path_str = file_path

                        # Note: Ripgrep JSON also outputs absolute offsets, but MatchLine requires line_num/text
                        matches.append(MatchLine(line_number=line_number, text=text, file=path_str))
                    elif data.get("type") == "context":
                        data_match = data["data"]
                        line_number = data_match.get("line_number", 0)
                        text = data_match.get("lines", {}).get("text", "").rstrip("\n\r")
                        path_str = data_match.get("path", {}).get("text", "")
                        if not path_str and isinstance(file_path, str):
                            path_str = file_path
                        matches.append(MatchLine(line_number=line_number, text=text, file=path_str))
                except json.JSONDecodeError:
                    pass

            files_set = {m.file for m in matches}

            return SearchResult(
                matches=matches,
                total_files=len(files_set),
                total_matches=len(matches),
                routing_backend="RipgrepBackend",
                routing_reason="rg_json",
                routing_distributed=False,
                routing_worker_count=1,
            )

        except Exception as e:
            raise RuntimeError(f"Ripgrep backend failed: {e}") from e

    def _search_counts(
        self, file_path: str | list[str], pattern: str, config: SearchConfig
    ) -> SearchResult:
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, encoding="utf-8"
            )
            if result.returncode > 1:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"rg failed with exit code {result.returncode}: {stderr or 'no stderr output'}"
                )

            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            total_matches = 0
            total_files = 0
            matched_file_paths: list[str] = []
            match_counts_by_file: dict[str, int] = {}

            multi_file = (
                (isinstance(file_path, (list, tuple)) and len(file_path) > 1)
                or (isinstance(file_path, str) and Path(file_path).is_dir())
                or (
                    isinstance(file_path, (list, tuple))
                    and len(file_path) == 1
                    and Path(file_path[0]).is_dir()
                )
            )
            for line in lines:
                matched_path: str | None = None
                if multi_file and ":" in line:
                    matched_path, count_text = line.rsplit(":", 1)
                else:
                    count_text = line
                try:
                    count_value = int(count_text.strip())
                except ValueError:
                    continue
                total_matches += count_value
                if count_value > 0:
                    total_files += 1
                    if matched_path:
                        matched_file_paths.append(matched_path)
                        match_counts_by_file[matched_path] = count_value
                    elif isinstance(file_path, str):
                        matched_file_paths.append(file_path)
                        match_counts_by_file[file_path] = count_value

            routing_reason = "rg_count_matches" if config.count_matches else "rg_count"
            return SearchResult(
                matches=[],
                matched_file_paths=matched_file_paths,
                match_counts_by_file=match_counts_by_file,
                total_files=total_files,
                total_matches=total_matches,
                routing_backend="RipgrepBackend",
                routing_reason=routing_reason,
                routing_distributed=False,
                routing_worker_count=1,
            )
        except Exception as e:
            raise RuntimeError(f"Ripgrep backend failed: {e}") from e

    def search_passthrough(
        self, file_path: str | list[str], pattern: str, config: SearchConfig | None = None
    ) -> int:
        """
        Execute ripgrep directly and stream output to stdout/stderr without JSON re-parsing.
        Returns rg's native exit code.
        """
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        result = subprocess.run(cmd, check=False)
        return int(result.returncode)

    def _build_cmd(
        self,
        file_path: str | list[str],
        pattern: str,
        config: SearchConfig | None,
        *,
        json_mode: bool,
    ) -> list[str]:
        binary_name = self._get_binary_name()
        if binary_name is None:
            raise RuntimeError("RipgrepBackend requires the 'rg' binary to be installed.")

        cmd: list[str] = [binary_name]
        if json_mode:
            cmd.append("--json")

        # We enforce JSON output so we can seamlessly parse it back into our SearchResult dataclasses
        if config:
            if config.ignore_case:
                cmd.append("-i")
            if config.case_sensitive:
                cmd.append("-s")
            if config.invert_match:
                cmd.append("-v")
            if config.word_regexp:
                cmd.append("-w")
            if config.line_regexp:
                cmd.append("-x")
            if config.fixed_strings:
                cmd.append("-F")
            if config.no_ignore:
                cmd.append("--no-ignore")
            if config.only_matching:
                cmd.append("-o")
            if config.text:
                cmd.append("-a")
            if config.line_number is not None and not json_mode:
                if config.line_number:
                    cmd.append("-n")
                else:
                    cmd.append("--no-line-number")
            if config.color:
                cmd.extend(["--color", config.color])
            if config.glob_case_insensitive:
                cmd.append("--glob-case-insensitive")
            if config.glob:
                for glob in config.glob:
                    cmd.extend(["-g", glob])
            if config.iglob:
                for glob in config.iglob:
                    cmd.extend(["--iglob", glob])

            if config.context is not None:
                cmd.extend(["-C", str(config.context)])
            else:
                if config.before_context is not None:
                    cmd.extend(["-B", str(config.before_context)])
                if config.after_context is not None:
                    cmd.extend(["-A", str(config.after_context)])

            if config.max_count is not None:
                cmd.extend(["-m", str(config.max_count)])
            if config.count:
                cmd.append("-c")
            if config.count_matches:
                cmd.append("--count-matches")
            if config.debug:
                cmd.append("--debug")
            if config.trace:
                cmd.append("--trace")
            if config.stats:
                cmd.append("--stats")
            if config.pcre2:
                cmd.append("-P")
            if config.max_filesize:
                cmd.extend(["--max-filesize", config.max_filesize])
            if config.threads > 0:
                cmd.extend(["-j", str(config.threads)])

        # The pattern
        cmd.append(pattern)
        if isinstance(file_path, list):
            cmd.extend(file_path)
        else:
            cmd.append(file_path)
        return cmd
