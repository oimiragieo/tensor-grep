import json
import os
import re
import subprocess
import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tensor_grep.backends.ast_backend import normalize_ast_language
from tensor_grep.backends.base import BackendExecutionError, ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

# Per-path I/O problems ast-grep reports while still scanning the rest of the
# tree (a permission-denied system directory, a locked or vanished file, etc.).
# These are non-fatal warnings, NOT a failed run; ripgrep logs them and keeps
# going with a successful exit, and tensor-grep must do the same so one
# unreadable path cannot abort an otherwise-complete scan.
_PATH_ACCESS_WARNING_PATTERN = re.compile(
    r"access is denied"
    r"|permission denied"
    r"|os error (?:5|13)"
    r"|no such file or directory"
    r"|cannot (?:open|read|access)"
    r"|the system cannot find",
    re.IGNORECASE,
)


def _stderr_is_only_path_access_warnings(stderr: str) -> bool:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return False
    return all(_PATH_ACCESS_WARNING_PATTERN.search(line) for line in lines)


def _stdout_is_json_payload(stdout: str) -> bool:
    if not stdout:
        return False
    try:
        return isinstance(json.loads(stdout), list)
    except json.JSONDecodeError:
        return False


_DEFAULT_AST_GREP_COMMAND_TIMEOUT_SECONDS = 60.0
_AST_GREP_COMMAND_TIMEOUT_ENV = "TG_AST_GREP_TIMEOUT_SECONDS"


def _is_ast_grep_sg_binary(binary: str) -> bool:
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    version_text = f"{result.stdout}\n{result.stderr}".lower()
    return "ast-grep" in version_text


def _ast_grep_command_timeout_seconds() -> float:
    raw_timeout = os.environ.get(_AST_GREP_COMMAND_TIMEOUT_ENV)
    if raw_timeout:
        try:
            parsed_timeout = float(raw_timeout)
        except ValueError:
            parsed_timeout = 0.0
        if parsed_timeout > 0:
            return parsed_timeout
    return _DEFAULT_AST_GREP_COMMAND_TIMEOUT_SECONDS


class AstGrepWrapperBackend(ComputeBackend):
    """
    A backend that seamlessly delegates to the native `ast-grep` (sg) binary
    when installed on the system, specifically for one-off CLI AST queries.
    This bypasses the heavy PyTorch Geometric setup for simple, fast structural searches.
    """

    def __init__(self) -> None:
        self._resolved_binary_name: str | None = None

    def is_available(self) -> bool:
        return self._get_binary_name() != "ast-grep"

    def _get_binary_name(self) -> str:
        import shutil

        if self._resolved_binary_name is not None:
            return self._resolved_binary_name

        # #130(b): every which()-resolved candidate must PROBE-RUN before being
        # trusted, not just the sg/sg.exe aliases. A broken npm shim literally
        # named `ast-grep`/`ast-grep.exe` (e.g. a Windows shim invoked under
        # WSL/Linux, exiting 127) previously resolved via shutil.which() alone
        # on the first two branches with no probe -- making is_available() (and
        # therefore `tg doctor`) report available:true for a binary that cannot
        # actually run. Gate all four branches on the same probe used below.
        if ast_grep_path := shutil.which("ast-grep"):
            if _is_ast_grep_sg_binary(ast_grep_path):
                self._resolved_binary_name = ast_grep_path
                return self._resolved_binary_name
        if ast_grep_exe_path := shutil.which("ast-grep.exe"):
            if _is_ast_grep_sg_binary(ast_grep_exe_path):
                self._resolved_binary_name = ast_grep_exe_path
                return self._resolved_binary_name
        if sg_exe_path := shutil.which("sg.exe"):
            if _is_ast_grep_sg_binary(sg_exe_path):
                self._resolved_binary_name = sg_exe_path
                return self._resolved_binary_name
        if sg_path := shutil.which("sg"):
            if _is_ast_grep_sg_binary(sg_path):
                self._resolved_binary_name = sg_path
                return self._resolved_binary_name
        self._resolved_binary_name = "ast-grep"
        return self._resolved_binary_name

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
            raise BackendExecutionError(
                "ast-grep semantic run options (--selector, --strictness, --stdin, --globs) "
                "are not supported for multiline patterns"
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
                # CWE-88 / native-argv flag injection: a user-supplied path that
                # looks like a flag (e.g. "-U" / "--update-all") would otherwise
                # be parsed by ast-grep's clap CLI as its auto-fix flag, turning
                # a read-only scan into a file rewrite. The "--" sentinel forces
                # everything after it to be treated as a positional path.
                cmd.append("--")
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
        # CWE-88: "--" sentinel prevents a user path like "-U" / "--update-all"
        # from being interpreted as ast-grep's auto-fix flag (see comment above).
        cmd = [self._get_binary_name(), "scan", "--json", "--rule", str(rule_file), "--", *paths]
        return cmd, context

    def _raise_for_nonzero(self, result: subprocess.CompletedProcess[str]) -> None:
        raw_returncode = getattr(result, "returncode", 0)
        returncode = raw_returncode if isinstance(raw_returncode, int) else 0
        if returncode == 0:
            return

        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        # Audit HIGH: `stdout.startswith("[")` waived ANY nonzero exit whose stdout merely
        # began with `[`, so a killed/OOM'd sg subprocess emitting TRUNCATED JSON was masked
        # as a clean 0-match scan (the later json.loads then raised and was swallowed
        # downstream). Require a COMPLETE, parseable JSON list before waiving — a truncated
        # payload fails the parse and falls through to raise BackendExecutionError.
        if not stderr and _stdout_is_json_payload(stdout):
            return

        # ast-grep exits nonzero when it cannot read an individual path (a
        # permission-denied directory, a locked/vanished file) even though it
        # successfully scanned everything else and emitted findings on stdout.
        # Treat that as a non-fatal partial scan: keep the results and forward
        # the warning to stderr instead of aborting. A genuine failure (bad
        # config/rule, invalid language) does not match the access-warning
        # shape, so it still raises below.
        if _stdout_is_json_payload(stdout) and _stderr_is_only_path_access_warnings(stderr):
            print(
                f"tg: warning: skipped unreadable paths during ast scan: {stderr.splitlines()[0]}",
                file=sys.stderr,
            )
            return

        detail = stderr or stdout or "no error output"
        detail = detail.splitlines()[0]
        # Provide a cleaner hint when ast-grep rejects a --selector/--strictness
        # combination (exit 8) so callers can surface a structured error instead
        # of a raw traceback.
        if returncode == 8:
            raise BackendExecutionError(
                f"ast-grep rejected the query (exit 8): {detail}. "
                "This often means --selector does not match the pattern's AST node kind, "
                "or the pattern is not valid for the specified language."
            )
        raise BackendExecutionError(f"ast-grep failed with exit code {returncode}: {detail}")

    def _parse_result(self, stdout: str, fallback_file: str | None = None) -> SearchResult:
        matches: list[MatchLine] = []
        matched_files: list[str] = []
        seen_files: set[str] = set()

        try:
            data_list = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BackendExecutionError(
                f"ast-grep returned malformed JSON on stdout (exit 0): {exc}"
            ) from exc
        if not isinstance(data_list, list):
            raise BackendExecutionError(
                "ast-grep returned an unexpected JSON shape on stdout (exit 0): "
                f"expected a list, got {type(data_list).__name__}"
            )

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

    @staticmethod
    def _cap_to_max_count(result: SearchResult, config: SearchConfig | None) -> SearchResult:
        """H6: cap `result` to `config.max_count`, matching cpu_backend/rust's
        per-file (per-call) cap semantics instead of returning every structural
        match ast-grep found."""
        max_count = config.max_count if config else None
        if not max_count or len(result.matches) <= max_count:
            return result
        result.matches = result.matches[:max_count]
        result.total_matches = len(result.matches)
        matched_files_capped: list[str] = []
        seen_files_capped: set[str] = set()
        for match in result.matches:
            if match.file and match.file not in seen_files_capped:
                seen_files_capped.add(match.file)
                matched_files_capped.append(match.file)
        result.matched_file_paths = matched_files_capped
        result.total_files = len(matched_files_capped)
        return result

    def _parse_json_items(self, stdout: str) -> list[dict[str, Any]]:
        try:
            loaded = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BackendExecutionError(
                f"ast-grep returned malformed JSON on stdout (exit 0): {exc}"
            ) from exc
        if not isinstance(loaded, list):
            raise BackendExecutionError(
                "ast-grep returned an unexpected JSON shape on stdout (exit 0): "
                f"expected a list, got {type(loaded).__name__}"
            )
        return [item for item in loaded if isinstance(item, dict)]

    def _run_ast_grep_command(
        self,
        cmd: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        timeout_seconds = _ast_grep_command_timeout_seconds()
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                input=input_text,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendExecutionError(
                "ast-grep command timed out after "
                f"{timeout_seconds:g}s; set {_AST_GREP_COMMAND_TIMEOUT_ENV} to adjust."
            ) from exc

    def search_project(self, root_path: str, config_path: str) -> dict[str, SearchResult]:
        if not self.is_available():
            raise BackendExecutionError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            result = self._run_ast_grep_command([
                self._get_binary_name(),
                "scan",
                "--json",
                "--config",
                config_path,
                # CWE-88: "--" sentinel prevents a user-supplied root_path like
                # "-U" / "--update-all" from being interpreted as ast-grep's
                # auto-fix flag (see comment on the run-command path above).
                "--",
                root_path,
            ])
        except BackendExecutionError:
            raise
        except Exception as e:
            raise BackendExecutionError(f"AstGrepWrapperBackend failed: {e}") from e

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
            raise BackendExecutionError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            cmd, context = self._build_command(pattern, file_paths, config=config)
            with context:
                result = self._run_ast_grep_command(
                    cmd,
                    input_text=config.ast_stdin_input if config and config.ast_stdin else None,
                )
                self._raise_for_nonzero(result)
                return self._cap_to_max_count(self._parse_result(result.stdout), config)
        except BackendExecutionError:
            raise
        except Exception as e:
            raise BackendExecutionError(f"AstGrepWrapperBackend failed: {e}") from e

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise BackendExecutionError(
                "AstGrepWrapperBackend requires the 'ast-grep' binary to be installed."
            )

        try:
            cmd, context = self._build_command(pattern, [file_path], config=config)
            with context:
                result = self._run_ast_grep_command(
                    cmd,
                    input_text=config.ast_stdin_input if config and config.ast_stdin else None,
                )
                self._raise_for_nonzero(result)
                return self._cap_to_max_count(
                    self._parse_result(result.stdout, fallback_file=file_path), config
                )
        except BackendExecutionError:
            raise
        except Exception as e:
            raise BackendExecutionError(f"AstGrepWrapperBackend failed: {e}") from e
