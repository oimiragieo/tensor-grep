import subprocess

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class AstGrepWrapperBackend(ComputeBackend):
    """
    A backend that seamlessly delegates to the native `ast-grep` (sg) binary
    when installed on the system, specifically for one-off CLI AST queries.
    This bypasses the heavy PyTorch Geometric setup for simple, fast structural searches.
    """

    def is_available(self) -> bool:
        import shutil

        return (
            shutil.which("ast-grep") is not None
            or shutil.which("ast-grep.exe") is not None
            or shutil.which("sg") is not None
        )

    def _get_binary_name(self) -> str:
        import shutil

        if ast_grep_path := shutil.which("ast-grep"):
            return ast_grep_path
        if ast_grep_exe_path := shutil.which("ast-grep.exe"):
            return ast_grep_exe_path
        if sg_path := shutil.which("sg"):
            return sg_path
        return "ast-grep"

    def _build_command(
        self, pattern: str, paths: list[str], config: SearchConfig | None = None
    ) -> list[str]:
        cmd = [self._get_binary_name(), "run", "--json", "-p", pattern]

        if config and config.lang:
            cmd.extend(["--lang", config.lang])

        cmd.extend(paths)
        return cmd

    def _parse_result(self, stdout: str, fallback_file: str | None = None) -> SearchResult:
        import json

        matches: list[MatchLine] = []
        matched_files: list[str] = []
        seen_files: set[str] = set()

        try:
            data_list = json.loads(stdout)
            for item in data_list:
                file_path = str(item.get("file") or fallback_file or "")
                text = item.get("text", "")
                line_num = (
                    item.get("range", {}).get("start", {}).get("line", 0) + 1
                )  # 0-indexed to 1-indexed

                matches.append(MatchLine(line_number=line_num, text=text, file=file_path))
                if file_path and file_path not in seen_files:
                    seen_files.add(file_path)
                    matched_files.append(file_path)
        except json.JSONDecodeError:
            pass

        return SearchResult(
            matches=matches,
            matched_file_paths=matched_files,
            total_files=len(matched_files),
            total_matches=len(matches),
            routing_backend="AstGrepWrapperBackend",
            routing_reason="ast_grep_json",
            routing_distributed=False,
            routing_worker_count=1,
        )

    def search_many(
        self, file_paths: list[str], pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            result = subprocess.run(
                self._build_command(pattern, file_paths, config=config),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
            return self._parse_result(result.stdout)
        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            result = subprocess.run(
                self._build_command(pattern, [file_path], config=config),
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
            return self._parse_result(result.stdout, fallback_file=file_path)

        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e
