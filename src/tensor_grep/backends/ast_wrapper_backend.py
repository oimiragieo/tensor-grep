import json
import subprocess
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class AstGrepWrapperBackend(ComputeBackend):
    """
    A backend that seamlessly delegates to the native `ast-grep` (sg) binary
    when installed on the system, specifically for one-off CLI AST queries.
    This bypasses the heavy PyTorch Geometric setup for simple, fast structural searches.
    """

    _cached_binary_name: str | None = None
    _binary_name_resolved = False

    def is_available(self) -> bool:
        return self._get_binary_name() != "ast-grep"

    def _get_binary_name(self) -> str:
        import shutil

        if type(self)._binary_name_resolved:
            return type(self)._cached_binary_name or "ast-grep"

        if ast_grep_path := shutil.which("ast-grep"):
            binary_name = ast_grep_path
        elif ast_grep_exe_path := shutil.which("ast-grep.exe"):
            binary_name = ast_grep_exe_path
        elif sg_path := shutil.which("sg"):
            binary_name = sg_path
        else:
            binary_name = "ast-grep"
        type(self)._cached_binary_name = binary_name
        type(self)._binary_name_resolved = True
        return binary_name

    def _build_command(
        self, pattern: str, paths: list[str], config: SearchConfig | None = None
    ) -> tuple[list[str], AbstractContextManager[object]]:
        lang = config.lang if config and config.lang else None
        if "\n" not in pattern and "\r" not in pattern:
            cmd = [self._get_binary_name(), "run", "--json", "-p", pattern]
            if lang:
                cmd.extend(["--lang", lang])
            cmd.extend(paths)
            return cmd, nullcontext()

        context = TemporaryDirectory(prefix="tg_ast_wrapper_rule_")
        temp_dir = Path(context.name)
        rule_file = temp_dir / "inline_rule.yml"
        lang_value = lang or "python"
        rule_file.write_text(
            "\n".join([
                "id: inline-rule",
                f"language: {lang_value}",
                "rule:",
                "  pattern: |",
                *[f"    {line}" for line in pattern.splitlines()],
                "",
            ]),
            encoding="utf-8",
        )
        cmd = [self._get_binary_name(), "scan", "--json", "--rule", str(rule_file), *paths]
        return cmd, context

    def _parse_result(self, stdout: str, fallback_file: str | None = None) -> SearchResult:
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

    def _parse_json_items(self, stdout: str) -> list[dict[str, Any]]:
        try:
            loaded = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        if not self.is_available():
            raise RuntimeError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            result = subprocess.run(
                [
                    self._get_binary_name(),
                    "scan",
                    "--json",
                    "--config",
                    config_path,
                    root_path,
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e

        grouped_matches: dict[str, list[dict[str, Any]]] = {}
        for item in self._parse_json_items(result.stdout):
            rule_id = item.get("ruleId") or item.get("rule_id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                continue
            grouped_matches.setdefault(rule_id, []).append(item)

        grouped_results: dict[str, SearchResult] = {}
        for rule_id, items in grouped_matches.items():
            grouped_results[rule_id] = self._parse_result(json.dumps(items))
        return grouped_results

    def search_many(
        self, file_paths: list[str], pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            cmd, context = self._build_command(pattern, file_paths, config=config)
            with context:
                result = subprocess.run(
                    cmd,
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
            cmd, context = self._build_command(pattern, [file_path], config=config)
            with context:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    encoding="utf-8",
                )
                return self._parse_result(result.stdout, fallback_file=file_path)

        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e
