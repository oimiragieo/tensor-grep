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

        if shutil.which("ast-grep"):
            return "ast-grep"
        if shutil.which("ast-grep.exe"):
            return "ast-grep.exe"
        if shutil.which("sg"):
            return "sg"
        return "ast-grep"

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        binary = self._get_binary_name()

        # ast-grep --json output
        cmd = [binary, "run", "--json", "-p", pattern]

        if config and config.lang:
            cmd.extend(["--lang", config.lang])

        cmd.append(file_path)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, encoding="utf-8"
            )

            import json

            matches = []

            # ast-grep json mode outputs an array of objects
            try:
                data_list = json.loads(result.stdout)
                for item in data_list:
                    # Item contains 'text', 'file', 'range'
                    text = item.get("text", "")
                    line_num = (
                        item.get("range", {}).get("start", {}).get("line", 0) + 1
                    )  # 0-indexed to 1-indexed

                    matches.append(MatchLine(line_number=line_num, text=text, file=file_path))
            except json.JSONDecodeError:
                pass

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
            )

        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e
