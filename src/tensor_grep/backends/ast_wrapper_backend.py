import json
import subprocess
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tensor_grep.backends.ast_backend import normalize_ast_language
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
    ) -> tuple[list[str], AbstractContextManager[object]]:
        lang = normalize_ast_language(config.lang) if config and config.lang else None
        selector = config.ast_selector if config else None
        strictness = config.ast_strictness if config else None
        globs = list(config.glob or []) if config else []
        stdin_enabled = bool(config.ast_stdin) if config else False
        if ("\n" in pattern or "\r" in pattern) and (
            selector or strictness or globs or stdin_enabled
        ):
            raise RuntimeError(
                "ast-grep semantic run options are not supported for multiline patterns yet"
            )
        if "\n" not in pattern and "\r" not in pattern:
            cmd = [self._get_binary_name(), "run", "--json", "-p", pattern]
            if lang:
                cmd.extend(["--lang", lang])
            if selector:
                cmd.extend(["--selector", selector])
            if strictness:
                cmd.extend(["--strictness", strictness])
            for glob in globs:
                cmd.extend(["--globs", glob])
            if stdin_enabled:
                cmd.append("--stdin")
            else:
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

    def _raise_for_nonzero(self, result: subprocess.CompletedProcess[str]) -> None:
        raw_returncode = getattr(result, "returncode", 0)
        returncode = raw_returncode if isinstance(raw_returncode, int) else 0
        if returncode == 0:
            return

        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if not stderr and stdout.startswith("["):
            return

        detail = stderr or stdout or "no error output"
        detail = detail.splitlines()[0]
        raise RuntimeError(f"ast-grep failed with exit code {returncode}: {detail}")

    def _parse_result(self, stdout: str, fallback_file: str | None = None) -> SearchResult:
        matches: list[MatchLine] = []
        matched_files: list[str] = []
        seen_files: set[str] = set()

        try:
            data_list = json.loads(stdout)
            for item in data_list:
                file_path = str(item.get("file") or fallback_file or "")
                text = item.get("text", "")
                match_range = item.get("range")
                if not isinstance(match_range, dict):
                    match_range = None
                meta_variables = item.get("metaVariables")
                if not isinstance(meta_variables, dict):
                    meta_variables = None
                start = match_range.get("start", {}) if match_range is not None else {}
                if not isinstance(start, dict):
                    start = {}
                line_num = int(start.get("line", 0)) + 1  # 0-indexed to 1-indexed

                matches.append(
                    MatchLine(
                        line_number=line_num,
                        text=text,
                        file=file_path,
                        range=match_range,
                        meta_variables=meta_variables,
                    )
                )
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

        self._raise_for_nonzero(result)
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
                    input=config.ast_stdin_input if config and config.ast_stdin else None,
                )
                self._raise_for_nonzero(result)
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
                    input=config.ast_stdin_input if config and config.ast_stdin else None,
                )
                self._raise_for_nonzero(result)
                return self._parse_result(result.stdout, fallback_file=file_path)

        except Exception as e:
            raise RuntimeError(f"AstGrepWrapperBackend failed: {e}") from e
