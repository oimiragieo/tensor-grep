import subprocess

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
        import shutil
        if shutil.which("rg"):
            return "rg"
        if shutil.which("rg.exe"):
            return "rg.exe"
        
        # Check standard ripgrep windows paths if in dev env
        import os
        dev_path = os.path.join(os.getcwd(), "benchmarks", "ripgrep-14.1.0-x86_64-pc-windows-msvc", "rg.exe")
        if os.path.exists(dev_path):
            return dev_path
            
        return None

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError("RipgrepBackend requires the 'rg' binary to be installed.")

        cmd = [self._get_binary_name(), "--json"]

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

            if config.context is not None:
                cmd.extend(["-C", str(config.context)])
            elif config.before_context is not None:
                cmd.extend(["-B", str(config.before_context)])
            elif config.after_context is not None:
                cmd.extend(["-A", str(config.after_context)])

            if config.max_count is not None:
                cmd.extend(["-m", str(config.max_count)])

        # The pattern
        cmd.append(pattern)
        cmd.append(file_path)

        try:
            # We use check=False because rg exits with 1 if no matches are found
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, encoding="utf-8"
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

                        # Note: Ripgrep JSON also outputs absolute offsets, but MatchLine requires line_num/text
                        matches.append(
                            MatchLine(line_number=line_number, text=text, file=file_path)
                        )
                except json.JSONDecodeError:
                    pass

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
            )

        except Exception:
            # If ripgrep completely fails, we fall back to returning 0 matches gracefully
            return SearchResult(matches=[], total_files=0, total_matches=0)
