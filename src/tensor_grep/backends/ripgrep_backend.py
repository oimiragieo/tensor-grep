import base64
import binascii
import subprocess
import sys
from pathlib import Path

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds, run_subprocess
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


def _decode_rg_field(field: dict[str, object] | None) -> str:
    """Decode an rg ``--json`` text-or-bytes field object.

    rg emits ``.text`` (already-UTF-8) normally, but ``.bytes`` (base64) for non-UTF-8
    content or paths. The old parser read only ``.text`` and silently defaulted to "",
    producing a phantom match with empty ``MatchLine.text`` on any non-UTF-8 file. NEVER
    raises: a malformed/undecodable payload degrades to "" so one bad record can't abort
    the whole search (the per-record ``except json.JSONDecodeError`` does not catch
    ``binascii.Error``/``ValueError``).
    """
    if not field:
        return ""
    text = field.get("text")
    if isinstance(text, str):
        return text  # valid-UTF-8 path: byte-identical to today, zero extra work
    b64 = field.get("bytes")
    if not isinstance(b64, str):
        return ""
    try:
        return base64.b64decode(b64).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


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
            # Check help output or version info for PCRE2 support
            result = run_subprocess(
                [binary, "--help"],
                capture_output=True,
                text=True,
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            if "--pcre2" in result.stdout or "PCRE2" in result.stdout:
                # Also do a quick smoke test to be sure
                test_proc = run_subprocess(
                    [binary, "-P", "a(?=b)", "-V"],
                    capture_output=True,
                    text=True,
                    timeout_seconds=configured_ripgrep_timeout_seconds(),
                )
                return test_proc.returncode == 0
            return False
        except Exception:
            return False

    def search(
        self, file_path: str | list[str], pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        # Audit MED: an explicitly-empty file_path list means "no candidate files" and must
        # yield no matches. Without this guard, _append_search_paths no-ops on the empty list,
        # leaving rg with zero path args -> rg defaults to a full recursive CWD scan (a
        # scope-widening footgun + misleading --stats), instead of an empty search.
        if isinstance(file_path, list) and not file_path:
            return SearchResult(
                matches=[],
                matched_file_paths=[],
                match_counts_by_file={},
                total_files=0,
                total_matches=0,
                routing_backend="RipgrepBackend",
                routing_reason="rg_empty_paths",
            )
        if config and (config.count or config.count_matches):
            return self._search_counts(file_path=file_path, pattern=pattern, config=config)
        if config and config.files_with_matches:
            return self._search_files_with_matches(
                file_path=file_path, pattern=pattern, config=config
            )

        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=True)
        try:
            # We use check=False because rg exits with 1 if no matches are found
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            import json

            matches = []
            matched_file_paths: list[str] = []
            match_counts_by_file: dict[str, int] = {}
            total_matches = 0

            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "match":
                        data_match = data["data"]
                        line_number = data_match.get("line_number", 0)
                        # Decode text-or-bytes: non-UTF-8 files arrive as lines.bytes (base64),
                        # not lines.text — reading only .text produced a phantom empty match.
                        text = _decode_rg_field(data_match.get("lines")).rstrip("\n\r")

                        _path_obj = data_match.get("path", {})
                        path_str = _decode_rg_field(_path_obj)
                        # Preserve the single-file fallback: if rg gave no path.text, prefer the
                        # real caller-supplied path over a lossy U+FFFD decode (keeps match.file
                        # openable for _resolve_match_path). Non-UTF-8 filenames in a directory
                        # scan may still yield a lossy path; correct raw-bytes path is out of scope.
                        if "text" not in _path_obj and isinstance(file_path, str):
                            path_str = file_path

                        # Stash rg's per-occurrence byte offsets (submatches[]) for --vimgrep/
                        # --column output shaping. Counting stays one-per-matching-line (below) so
                        # total_matches / parity with the other backends is unchanged.
                        _subs = data_match.get("submatches") or None
                        matches.append(
                            MatchLine(
                                line_number=line_number,
                                text=text,
                                file=path_str,
                                submatches=tuple(_subs) if _subs else None,
                            )
                        )
                        total_matches += 1
                        if path_str:
                            match_counts_by_file[path_str] = (
                                match_counts_by_file.get(path_str, 0) + 1
                            )
                            if path_str not in matched_file_paths:
                                matched_file_paths.append(path_str)
                    elif data.get("type") == "context":
                        data_match = data["data"]
                        line_number = data_match.get("line_number", 0)
                        text = _decode_rg_field(data_match.get("lines")).rstrip("\n\r")
                        _path_obj = data_match.get("path", {})
                        path_str = _decode_rg_field(_path_obj)
                        if "text" not in _path_obj and isinstance(file_path, str):
                            path_str = file_path
                        matches.append(MatchLine(line_number=line_number, text=text, file=path_str))
                except json.JSONDecodeError:
                    pass

            # Parse-first, THEN branch on the exit code. rg exit 2 = a SOFT per-file error (e.g.
            # one unreadable/missing path among many); if it still emitted matches for the readable
            # files, KEEP them + flag incomplete so we exit 2 like rg AND surface a "suppression !=
            # absence" marker. Only a genuine total failure (exit >2, or exit 2 with nothing parsed)
            # stays fail-closed with the byte-identical RuntimeError message.
            partial = result.returncode == 2 and total_matches > 0
            if result.returncode > 1 and not partial:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"rg failed with exit code {result.returncode}: {stderr or 'no stderr output'}"
                )
            search_result = SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                match_counts_by_file=match_counts_by_file,
                total_files=len(matched_file_paths),
                total_matches=total_matches,
                routing_backend="RipgrepBackend",
                routing_reason="rg_json",
                routing_distributed=False,
                routing_worker_count=1,
            )
            if partial:
                reason = result.stderr.strip() or "rg exit 2 (partial results)"
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
            return search_result

        except Exception as e:
            raise RuntimeError(f"Ripgrep backend failed: {e}") from e

    def _search_files_with_matches(
        self, file_path: str | list[str], pattern: str, config: SearchConfig
    ) -> SearchResult:
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        try:
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            path_parts = result.stdout.split("\0") if config.null else result.stdout.splitlines()
            matched_file_paths = [path for path in path_parts if path]
            # rg exit 2 with some matched files = soft partial error: keep them, flag incomplete.
            partial = result.returncode == 2 and bool(matched_file_paths)
            if result.returncode > 1 and not partial:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"rg failed with exit code {result.returncode}: {stderr or 'no stderr output'}"
                )
            search_result = SearchResult(
                matches=[],
                matched_file_paths=matched_file_paths,
                match_counts_by_file=dict.fromkeys(matched_file_paths, 1),
                total_files=len(matched_file_paths),
                total_matches=len(matched_file_paths),
                routing_backend="RipgrepBackend",
                routing_reason="rg_files_with_matches",
                routing_distributed=False,
                routing_worker_count=1,
            )
            if partial:
                reason = result.stderr.strip() or "rg exit 2 (partial results)"
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
            return search_result
        except Exception as e:
            raise RuntimeError(f"Ripgrep backend failed: {e}") from e

    def _search_counts(
        self, file_path: str | list[str], pattern: str, config: SearchConfig
    ) -> SearchResult:
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        try:
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                timeout_seconds=configured_ripgrep_timeout_seconds(),
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
            # Audit HIGH: rg emits `path:count` whenever it prints the filename, which is
            # driven by -H/--no-filename (config.with_filename/no_filename), NOT only by the
            # multi-file heuristic. A single-file `--count -H` yielded `path:count`, hit the
            # bare-count branch, `int()` raised, and the line was silently dropped -> false 0.
            path_prefixed = (multi_file or config.with_filename) and not config.no_filename
            for line in lines:
                matched_path: str | None = None
                if config.null and "\0" in line:
                    matched_path, count_text = line.rsplit("\0", 1)
                elif path_prefixed and ":" in line:
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

            partial = result.returncode == 2 and total_matches > 0
            if result.returncode > 1 and not partial:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"rg failed with exit code {result.returncode}: {stderr or 'no stderr output'}"
                )
            routing_reason = "rg_count_matches" if config.count_matches else "rg_count"
            search_result = SearchResult(
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
            if partial:
                reason = result.stderr.strip() or "rg exit 2 (partial results)"
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
            return search_result
        except Exception as e:
            raise RuntimeError(f"Ripgrep backend failed: {e}") from e

    def search_passthrough(
        self, file_path: str | list[str], pattern: str, config: SearchConfig | None = None
    ) -> int:
        """
        Execute ripgrep directly and stream output to stdout/stderr without JSON re-parsing.
        Returns rg's native exit code.
        """
        cmd = self._build_cmd(
            file_path=file_path,
            pattern=pattern,
            config=config,
            json_mode=bool(config and config.json_mode),
        )
        try:
            result = run_subprocess(
                cmd,
                check=False,
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
        except subprocess.TimeoutExpired:
            # ripgrep never self-terminates a search; this timeout is tg-imposed. Exit
            # with the coreutils `timeout` convention instead of letting an uncaught
            # TimeoutExpired traceback abort the stream (audit B5/#10).
            sys.stderr.write(
                "tensor-grep: search exceeded the "
                f"{configured_ripgrep_timeout_seconds():g}s timeout and was stopped. For a large "
                "repo, scope the search to a path (e.g. `tg search PATTERN src/`), or raise "
                "TG_RG_TIMEOUT_SECONDS.\n"
            )
            return 124
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
            if config.no_invert_match:
                cmd.append("--no-invert-match")
            if config.word_regexp:
                cmd.append("-w")
            if config.line_regexp:
                cmd.append("-x")
            if config.fixed_strings:
                cmd.append("-F")
            if config.no_fixed_strings:
                cmd.append("--no-fixed-strings")
            if config.crlf:
                cmd.append("--crlf")
            if config.no_crlf:
                cmd.append("--no-crlf")
            if config.encoding != "auto":
                cmd.extend(["--encoding", config.encoding])
            if config.no_encoding:
                cmd.append("--no-encoding")
            if not config.mmap:
                cmd.append("--no-mmap")
            if config.no_mmap:
                cmd.append("--no-mmap")
            if config.multiline:
                cmd.append("--multiline")
            if config.no_multiline:
                cmd.append("--no-multiline")
            if config.multiline_dotall:
                cmd.append("--multiline-dotall")
            if config.no_multiline_dotall:
                cmd.append("--no-multiline-dotall")
            if config.auto_hybrid_regex:
                cmd.append("--auto-hybrid-regex")
            if config.no_auto_hybrid_regex:
                cmd.append("--no-auto-hybrid-regex")
            if config.unicode:
                cmd.append("--unicode")
            if config.pcre2_unicode:
                cmd.append("--pcre2-unicode")
            if config.no_pcre2_unicode:
                cmd.append("--no-pcre2-unicode")
            if config.no_unicode:
                cmd.append("--no-unicode")
            if config.ignore:
                cmd.append("--ignore")
            if config.no_ignore:
                cmd.append("--no-ignore")
            if config.ignore_dot:
                cmd.append("--ignore-dot")
            if config.no_ignore_dot:
                cmd.append("--no-ignore-dot")
            if config.ignore_exclude:
                cmd.append("--ignore-exclude")
            if config.no_ignore_exclude:
                cmd.append("--no-ignore-exclude")
            if config.ignore_files:
                cmd.append("--ignore-files")
            if config.no_ignore_files:
                cmd.append("--no-ignore-files")
            if config.ignore_global:
                cmd.append("--ignore-global")
            if config.no_ignore_global:
                cmd.append("--no-ignore-global")
            if config.ignore_parent:
                cmd.append("--ignore-parent")
            if config.no_ignore_parent:
                cmd.append("--no-ignore-parent")
            if config.ignore_vcs:
                cmd.append("--ignore-vcs")
            if config.no_ignore_vcs:
                cmd.append("--no-ignore-vcs")
            if config.no_require_git:
                cmd.append("--no-require-git")
            if config.require_git:
                cmd.append("--require-git")
            if config.one_file_system:
                cmd.append("--one-file-system")
            if config.no_one_file_system:
                cmd.append("--no-one-file-system")
            if config.ignore_file:
                for ignore_path in config.ignore_file:
                    cmd.extend(["--ignore-file", ignore_path])
            if config.ignore_file_case_insensitive:
                cmd.append("--ignore-file-case-insensitive")
            if config.no_ignore_file_case_insensitive:
                cmd.append("--no-ignore-file-case-insensitive")
            if config.max_depth is not None:
                cmd.extend(["--max-depth", str(config.max_depth)])
            if config.no_config:
                cmd.append("--no-config")
            if config.only_matching:
                cmd.append("-o")
            if config.text:
                cmd.append("-a")
            if config.no_text:
                cmd.append("--no-text")
            if config.binary and not config.text:
                cmd.append("--binary")
            if config.no_binary:
                cmd.append("--no-binary")
            if config.hidden:
                cmd.append("--hidden")
            if config.no_hidden:
                cmd.append("--no-hidden")
            if config.unrestricted:
                # Forward the raw -u/-uu/-uuu token; rg's own parser owns the
                # -u->--no-ignore / -uu->+--hidden / -uuu->+--binary expansion. Was a
                # silent no-op — parsed into SearchConfig but never handed to rg.
                cmd.append("-" + "u" * config.unrestricted)
            if config.follow:
                cmd.append("--follow")
            if config.no_follow:
                cmd.append("--no-follow")
            if config.line_number is not None and not json_mode:
                if config.line_number:
                    cmd.append("-n")
                else:
                    cmd.append("--no-line-number")
            if config.column and not json_mode:
                cmd.append("--column")
            if config.no_column and not json_mode:
                cmd.append("--no-column")
            if config.path_separator is not None and not json_mode:
                cmd.extend(["--path-separator", config.path_separator])
            if config.vimgrep and not json_mode:
                cmd.append("--vimgrep")
            if config.null and not json_mode:
                cmd.append("-0")
            if config.color:
                cmd.extend(["--color", config.color])
            if config.glob_case_insensitive:
                cmd.append("--glob-case-insensitive")
            if config.no_glob_case_insensitive:
                cmd.append("--no-glob-case-insensitive")
            if config.glob:
                for glob in config.glob:
                    cmd.extend(["-g", glob])
            if config.iglob:
                for glob in config.iglob:
                    cmd.extend(["--iglob", glob])
            if config.file_type:
                for file_type in config.file_type:
                    cmd.extend(["-t", file_type])
            if config.type_not:
                for file_type in config.type_not:
                    cmd.extend(["-T", file_type])
            if config.type_add:
                for type_spec in config.type_add:
                    cmd.extend(["--type-add", type_spec])
            if config.type_clear:
                cmd.extend(["--type-clear", config.type_clear])

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
            if config.files_with_matches and not (config.count or config.count_matches):
                cmd.append("--files-with-matches")
            if config.files_without_match:
                cmd.append("--files-without-match")
            if config.replace_str is not None and not json_mode:
                cmd.extend(["--replace", config.replace_str])
            if config.passthru and not json_mode:
                cmd.append("--passthru")
            if config.block_buffered and not json_mode:
                cmd.append("--block-buffered")
            if config.no_block_buffered and not json_mode:
                cmd.append("--no-block-buffered")
            if config.byte_offset and not json_mode:
                cmd.append("-b")
            if config.no_byte_offset and not json_mode:
                cmd.append("--no-byte-offset")
            if config.colors and not json_mode:
                for color_spec in config.colors:
                    cmd.extend(["--colors", color_spec])
            if config.context_separator != "--" and not json_mode:
                cmd.extend(["--context-separator", config.context_separator])
            if config.no_context_separator and not json_mode:
                cmd.append("--no-context-separator")
            if config.field_context_separator != "-" and not json_mode:
                cmd.extend(["--field-context-separator", config.field_context_separator])
            if config.field_match_separator != ":" and not json_mode:
                cmd.extend(["--field-match-separator", config.field_match_separator])
            if not config.heading and not json_mode:
                cmd.append("--no-heading")
            if config.include_zero and not json_mode:
                cmd.append("--include-zero")
            if config.no_include_zero and not json_mode:
                cmd.append("--no-include-zero")
            if config.line_buffered and not json_mode:
                cmd.append("--line-buffered")
            if config.no_line_buffered and not json_mode:
                cmd.append("--no-line-buffered")
            if config.max_columns is not None and not json_mode:
                cmd.extend(["--max-columns", str(config.max_columns)])
            if config.max_columns_preview and not json_mode:
                cmd.append("--max-columns-preview")
            if config.no_max_columns_preview and not json_mode:
                cmd.append("--no-max-columns-preview")
            # Audit MED: --sort/--sortr/--sort-files change the RESULT ORDER (unlike
            # --max-columns/--trim, which only affect text rendering and are legitimately
            # json-gated), and rg honors them with --json. Forward unconditionally so the
            # default search() (always json_mode) and --json callers get the requested
            # ordering instead of silently dropping it.
            if config.sort_by != "none":
                cmd.extend(["--sort", config.sort_by])
            if config.sort_files:
                cmd.append("--sort-files")
            if config.sort_by_reverse != "none":
                cmd.extend(["--sortr", config.sort_by_reverse])
            if config.trim and not json_mode:
                cmd.append("--trim")
            if config.no_trim and not json_mode:
                cmd.append("--no-trim")
            if config.with_filename and not json_mode:
                cmd.append("--with-filename")
            if config.no_filename and not json_mode:
                cmd.append("--no-filename")
            if config.debug:
                cmd.append("--debug")
            if config.trace:
                cmd.append("--trace")
            if config.stats:
                cmd.append("--stats")
            if config.no_stats:
                cmd.append("--no-stats")
            if config.ignore_messages:
                cmd.append("--ignore-messages")
            if config.no_ignore_messages:
                cmd.append("--no-ignore-messages")
            if config.no_messages:
                cmd.append("--no-messages")
            if config.messages:
                cmd.append("--messages")
            if config.pcre2:
                cmd.append("-P")
            if config.no_pcre2:
                cmd.append("--no-pcre2")
            if config.pre:
                cmd.extend(["--pre", config.pre])
            if config.no_pre:
                cmd.append("--no-pre")
            if config.pre_glob:
                for glob in config.pre_glob:
                    cmd.extend(["--pre-glob", glob])
            if config.search_zip:
                cmd.append("--search-zip")
            if config.no_search_zip:
                cmd.append("--no-search-zip")
            if config.no_json:
                cmd.append("--no-json")
            if config.max_filesize:
                cmd.extend(["--max-filesize", config.max_filesize])
            if config.threads > 0:
                cmd.extend(["-j", str(config.threads)])
            if config.list_files:
                cmd.append("--files")
                self._append_search_paths(cmd, file_path)
                return cmd

        pattern_files = list(config.file_patterns or []) if config else []
        for pattern_file in pattern_files:
            cmd.extend(["--file", pattern_file])
        patterns = list(config.regexp or []) if config and config.regexp else []
        if not pattern_files:
            patterns = patterns or [pattern]
        for current_pattern in patterns:
            if len(patterns) > 1 or current_pattern.startswith("-"):
                cmd.extend(["-e", current_pattern])
            else:
                cmd.append(current_pattern)
        self._append_search_paths(cmd, file_path)
        return cmd

    @staticmethod
    def _append_search_paths(cmd: list[str], file_path: str | list[str]) -> None:
        """Append positional paths after a literal ``--`` end-of-options separator.

        ripgrep itself disambiguates trailing positionals with ``--``; without it a path
        beginning with ``-`` (e.g. ``-foo.txt``, ``--no-ignore``, ``--type-add=x:*``)
        supplied as a search target is parsed as a FLAG, silently searching the wrong
        files or altering ignore/type behavior (audit B4/#8). The separator is a no-op
        for normal paths and universally supported by rg.
        """
        paths = file_path if isinstance(file_path, list) else [file_path]
        if not paths:
            return
        cmd.append("--")
        cmd.extend(paths)
