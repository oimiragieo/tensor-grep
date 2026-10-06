import base64
import binascii
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tensor_grep.backends.base import BackendExecutionError, ComputeBackend
from tensor_grep.backends.rg_json_render import (
    effective_multiline,
    render_json_record,
    strip_record_terminator,
)
from tensor_grep.backends.rg_json_render import (
    field_bytes as _field_bytes,
)
from tensor_grep.cli.rg_root_ignore import root_ignore_file_args
from tensor_grep.cli.subprocess_policy import (
    configured_ripgrep_timeout_seconds as configured_ripgrep_timeout_seconds,
)
from tensor_grep.cli.subprocess_policy import decode_diagnostic_output, decode_protocol_output
from tensor_grep.cli.subprocess_policy import run_subprocess as run_subprocess
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


def _decode_rg_path_field(field: dict[str, object] | None) -> str | None:
    """Decode rg path bytes with the filesystem codec, preserving path identity."""
    if not field:
        return None
    text = field.get("text")
    if isinstance(text, str):
        return text or None
    raw = _field_bytes(field)
    if raw is None or not raw:
        return None
    return os.fsdecode(raw)


def _lossy_record_text(field: dict[str, object] | None, delim: bytes) -> str:
    """The record text for ``MatchLine.text``: terminator stripped, undecodable bytes -> U+FFFD."""
    raw = _field_bytes(field)
    if raw is None:
        return ""
    return strip_record_terminator(raw, delim).decode("utf-8", errors="replace")


def _pattern_semantics_flags(config: SearchConfig | None) -> list[str]:
    """Every rg flag that decides WHETHER A LINE MATCHES (pattern semantics + input decoding).

    Single source of truth shared by ``RipgrepBackend._build_cmd`` and the binary-file match
    check in ``rust_backend`` so the two can never drift. Order encodes rg's last-flag-wins
    precedence (``-i`` then ``-s`` means case-sensitive; ``--auto-hybrid-regex`` precedes
    ``-P``/``--no-pcre2``). Raises ``BackendExecutionError`` for an unsupported ``--engine``.

    CENSUS of the ``SearchConfig`` fields ``_build_cmd`` reads (asserted by
    tests/unit/test_ripgrep_backend_field_coverage.py):

    * MATCHING SEMANTICS -- forwarded here: invert_match, no_invert_match, stop_on_nonmatch,
      null_data, dfa_size_limit, regex_size_limit, crlf, no_crlf, encoding, no_encoding,
      multiline, no_multiline, multiline_dotall, no_multiline_dotall, auto_hybrid_regex,
      no_auto_hybrid_regex, unicode, no_unicode, pcre2_unicode, no_pcre2_unicode, ignore_case,
      case_sensitive, smart_case, engine, word_regexp, line_regexp, fixed_strings,
      no_fixed_strings, pcre2, no_pcre2.
    * OUTPUT-ONLY / TRAVERSAL -- stay in ``_build_cmd``: every ignore*/hidden/follow/glob/type/
      sort/threads/max_depth/max_filesize/one_file_system flag (which FILES are searched),
      count/count_matches/files_*/only_matching/replace/passthru/context/max_count/max_columns/
      color/heading/line_number/column/vimgrep/byte_offset/with_filename/trim/stats/debug/...
      (how results are PRINTED or how many), text/binary/no_text/no_binary (the binary check
      always passes ``-a``), json/no_json, mmap, list_files.
    * KNOWN GAPS of the binary check (input is not the pattern string): ``pre``/``pre_glob``/
      ``search_zip`` (preprocessors would execute on a file we only probe) and the extra
      pattern sources ``regexp``/``file_patterns`` (the check receives ``pattern`` directly).
    """
    flags: list[str] = []
    if not config:
        return flags
    if config.invert_match:
        flags.append("-v")
    if config.no_invert_match:
        flags.append("--no-invert-match")
    if config.stop_on_nonmatch:
        flags.append("--stop-on-nonmatch")
    if config.null_data:
        flags.append("--null-data")
    if config.dfa_size_limit:
        flags.extend(["--dfa-size-limit", str(config.dfa_size_limit)])
    if config.regex_size_limit:
        flags.extend(["--regex-size-limit", str(config.regex_size_limit)])
    if config.crlf:
        flags.append("--crlf")
    if config.no_crlf:
        flags.append("--no-crlf")
    if config.encoding != "auto":
        flags.extend(["--encoding", config.encoding])
    if config.no_encoding:
        flags.append("--no-encoding")
    if config.multiline:
        flags.append("--multiline")
    if config.no_multiline:
        flags.append("--no-multiline")
    if config.multiline_dotall:
        flags.append("--multiline-dotall")
    if config.no_multiline_dotall:
        flags.append("--no-multiline-dotall")
    if config.auto_hybrid_regex:
        flags.append("--auto-hybrid-regex")
    if config.no_auto_hybrid_regex:
        flags.append("--no-auto-hybrid-regex")
    if config.unicode:
        flags.append("--unicode")
    if config.pcre2_unicode:
        flags.append("--pcre2-unicode")
    if config.no_pcre2_unicode:
        flags.append("--no-pcre2-unicode")
    if config.no_unicode:
        flags.append("--no-unicode")
    if config.ignore_case:
        flags.append("-i")
    if config.case_sensitive:
        flags.append("-s")
    if config.smart_case and not (config.ignore_case or config.case_sensitive):
        flags.append("-S")  # explicit -i/-s win; rg is last-flag-wins
    engine = str(config.engine or "default").lower()
    if engine in {"pcre2", "auto"}:
        flags.extend(["--engine", engine])
    elif engine != "default":
        # ascii(): CLI diagnostics stay ASCII even when the user's value is not
        raise BackendExecutionError(f"unsupported --engine value: {config.engine!a}")
    if config.word_regexp:
        flags.append("-w")
    if config.line_regexp:
        flags.append("-x")
    if config.fixed_strings:
        flags.append("-F")
    if config.no_fixed_strings:
        flags.append("--no-fixed-strings")
    if config.pcre2:
        flags.append("-P")
    if config.no_pcre2:
        flags.append("--no-pcre2")
    return flags


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
                encoding="utf-8",
                errors="replace",
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            if "--pcre2" in result.stdout or "PCRE2" in result.stdout:
                # Also do a quick smoke test to be sure
                test_proc = run_subprocess(
                    [binary, "-P", "a(?=b)", "-V"],
                    capture_output=True,
                    text=False,
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
        # -o / -r: entries are laid out by the rg-compatible printer in rg_json_render.py from
        # rg's own --json data (unforgeable framing); there is no plain-text route
        render_cfg: SearchConfig | None = None
        if config and (config.only_matching or config.replace_str is not None):
            if not (config.files_with_matches or config.files_without_match or config.list_files):
                render_cfg = config

        probe = (
            self._multiline_strategy_probe(pattern, render_cfg)
            if render_cfg is not None and effective_multiline(render_cfg)
            else None
        )
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=True)
        try:
            # We use check=False because rg exits with 1 if no matches are found
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=False,
                check=False,
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            stdout = decode_protocol_output(result.stdout)
            matches, matched_file_paths, match_counts_by_file, total_matches = (
                self._parse_ndjson_matches(
                    stdout,
                    file_path,
                    delim=b"\0" if config is not None and config.null_data else b"\n",
                    render=render_cfg,
                    probe=probe,
                )
            )

            # Parse-first, THEN branch on the exit code. rg exit 2 = a SOFT per-file error (e.g.
            # one unreadable/missing path among many); if it still emitted matches for the readable
            # files, KEEP them + flag incomplete so we exit 2 like rg AND surface a "suppression !=
            # absence" marker. Only a genuine total failure (exit >2, or exit 2 with nothing parsed)
            # stays fail-closed with the byte-identical BackendExecutionError message.
            partial = result.returncode == 2 and total_matches > 0
            if result.returncode > 1 and not partial:
                stderr = decode_diagnostic_output(result.stderr).strip()
                raise BackendExecutionError(
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
                # rg exited 0 = it matched; a rendered (-o/-r) request may still print nothing
                # (e.g. `-U -o '\\n'`), which is success with zero entries
                rg_exit_zero=render_cfg is not None and result.returncode == 0,
            )
            if partial:
                reason = (
                    decode_diagnostic_output(result.stderr).strip() or "rg exit 2 (partial results)"
                )
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
                # Task #276 slice 1: rg's own exit-2-with-partial-output soft error is always a
                # per-path access problem (missing/permission-denied/etc among the searched
                # paths) -- rg has no notion of "scan_limit"/"deadline" in its own exit code, so
                # "unreadable_path" is the correct class for every occurrence of this branch.
                search_result.incomplete_reason_class = "unreadable_path"
            return search_result

        except subprocess.TimeoutExpired as e:
            # Audit H2 (Fable review of #400): the aggregate JSON path previously had NO
            # handler for a timed-out rg subprocess, so this fell into the broad `except
            # Exception` below, got wrapped as a RuntimeError, and propagated as an
            # UNCAUGHT traceback all the way through main.py's search command (exit 1, no
            # JSON envelope, all partial results silently lost) -- violating the Backend
            # Fail-Closed Contract (AGENTS.md). That gave agents THREE different "timed
            # out" signals depending on route: exit 124 from the rg passthrough
            # (search_passthrough, below -- kept as-is: 124 is the coreutils `timeout`
            # convention and is load-bearing rg-parity for the streaming/interactive path,
            # so it is NOT changed here), exit 2 + result_incomplete from the native walk
            # deadline (main.py, added in #400), and an uncaught traceback + exit 1 from
            # here. Fix: treat it like rg's own soft partial-failure (exit 2) above --
            # `subprocess.run(capture_output=True)` attaches whatever stdout rg had
            # already flushed before being killed to the TimeoutExpired exception, so
            # best-effort re-use the SAME NDJSON parser to recover any complete match
            # records instead of discarding them; fall back to an empty-but-well-formed
            # envelope if nothing parses. Either way: result_incomplete=True + a reason,
            # NEVER a crash, NEVER a silent empty. main.py's existing
            # `sys.exit(2 if all_results.result_incomplete else ...)` then exits 2 for
            # this path too, consistent with the native-walk-deadline signal.
            timeout_seconds = (
                e.timeout if e.timeout is not None else configured_ripgrep_timeout_seconds()
            )
            try:
                partial_stdout_raw = e.stdout or b""
                if isinstance(partial_stdout_raw, bytes):
                    # NDJSON records are independently framed by LF. Discard one torn
                    # trailing record before strict decoding; retain complete records.
                    last_complete = partial_stdout_raw.rfind(b"\n")
                    partial_stdout = decode_protocol_output(partial_stdout_raw[: last_complete + 1])
                else:
                    partial_stdout = partial_stdout_raw
            except UnicodeDecodeError:
                partial_stdout = ""
            matches, matched_file_paths, match_counts_by_file, total_matches = (
                self._parse_ndjson_matches(
                    partial_stdout,
                    file_path,
                    delim=b"\0" if config is not None and config.null_data else b"\n",
                    render=render_cfg,
                    probe=probe,
                )
            )
            reason = (
                f"rg aggregate search exceeded the {timeout_seconds:g}s timeout and was "
                "stopped; returning partial results. Scope the search to a smaller path, "
                "or raise TG_RG_TIMEOUT_SECONDS."
            )
            sys.stderr.write(
                f"tg: rg aggregate search timed out, keeping partial results: {reason}\n"
            )
            return SearchResult(
                matches=matches,
                matched_file_paths=matched_file_paths,
                match_counts_by_file=match_counts_by_file,
                total_files=len(matched_file_paths),
                total_matches=total_matches,
                routing_backend="RipgrepBackend",
                routing_reason="rg_json",
                routing_distributed=False,
                routing_worker_count=1,
                result_incomplete=True,
                incomplete_reason=reason,
                incomplete_reason_class="timeout",
            )
        except Exception as e:
            raise BackendExecutionError(f"Ripgrep backend failed: {e}") from e

    def _multiline_strategy_probe(
        self, pattern: str, config: SearchConfig
    ) -> Callable[[bytes], bool | None]:
        """Does rg's searcher use its MULTI-LINE strategy for this pattern under `-U`?

        rg does not report it, but it decides how the printer lays out `-o`/`-r` ("lines mode").
        The observable: a haystack of two ADJACENT copies of a block that matches yields ONE
        merged record under the multi-line strategy and two records under the line strategy
        (verified with rg 15.1: `ab` -> 2, `(a)\\n?`, `$`, `^`, `ab$` -> 1). Memoised per request;
        returns None when the probe cannot tell (no match / rg failure).
        """
        import dataclasses
        import json
        import os
        import tempfile

        cache: list[bool | None] = []
        probe_cfg = dataclasses.replace(
            config,
            only_matching=False,
            replace_str=None,
            context=None,
            before_context=None,
            after_context=None,
            invert_match=False,
            no_invert_match=False,
            max_count=None,
            count=False,
            count_matches=False,
            files_with_matches=False,
            files_without_match=False,
        )

        def probe(block: bytes) -> bool | None:
            if cache:
                return cache[0]
            delim = b"\0" if config.null_data else b"\n"
            unit = block if block.endswith(delim) else block + delim
            handle, tmp_path = tempfile.mkstemp(prefix="tg-strategy-probe-")
            result: bool | None = None
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(unit + unit)
                cmd = self._build_cmd(
                    file_path=tmp_path, pattern=pattern, config=probe_cfg, json_mode=True
                )
                proc = run_subprocess(
                    cmd,
                    capture_output=True,
                    text=False,
                    check=False,
                    timeout_seconds=configured_ripgrep_timeout_seconds(),
                )
                stdout = decode_protocol_output(proc.stdout)
                records = 0
                for line in stdout.split("\n"):
                    if line.strip() and json.loads(line).get("type") == "match":
                        records += 1
                result = records == 1 if records else None
            except (OSError, ValueError, subprocess.SubprocessError):
                result = None
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            cache.append(result)
            return result

        return probe

    @staticmethod
    def _parse_ndjson_matches(
        stdout: str,
        file_path: str | list[str],
        delim: bytes = b"\n",
        render: SearchConfig | None = None,
        probe: Callable[[bytes], bool | None] | None = None,
    ) -> tuple[list[MatchLine], list[str], dict[str, int], int]:
        """Parse rg ``--json`` NDJSON output into match/context records.

        Shared by the success path and the timeout-recovery path above (a
        ``subprocess.TimeoutExpired`` carries whatever stdout rg had already flushed before
        being killed via ``e.stdout``) so a timed-out aggregate search can still surface any
        complete match records instead of discarding them.
        """
        import json

        matches: list[MatchLine] = []
        matched_file_paths: list[str] = []
        match_counts_by_file: dict[str, int] = {}
        total_matches = 0

        # split on rg's NDJSON record delimiter (\n) — NOT str.splitlines(), which also
        # breaks on U+2028/U+2029/U+0085 that rg emits UNESCAPED inside a match's line text,
        # fracturing the JSON record so it fails json.loads and is silently dropped.
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "match":
                    data_match = data["data"]
                    line_number = data_match.get("line_number", 0)
                    # Decode text-or-bytes: non-UTF-8 files arrive as lines.bytes (base64),
                    # not lines.text — reading only .text produced a phantom empty match.
                    # strip_record_terminator (one configured delimiter, never .rstrip("\n\r")):
                    # rg's own "lines" field
                    # includes the source line's real trailing `\r` for a CRLF file (verified
                    # directly against `rg.exe --json`) -- `.rstrip("\n\r")` ate that `\r` too,
                    # a genuine tg-vs-rg `--json` divergence task #262 uncovered.
                    text = _lossy_record_text(data_match.get("lines"), delim)

                    _path_obj = data_match.get("path", {})
                    decoded_path = _decode_rg_path_field(_path_obj)
                    path_str = decoded_path or (file_path if isinstance(file_path, str) else "")

                    # Stash rg's per-occurrence byte offsets (submatches[]) for --vimgrep/
                    # --column output shaping. Counting stays one-per-matching-line (below) so
                    # total_matches / parity with the other backends is unchanged.
                    _subs = data_match.get("submatches") or None
                    if render is not None:  # -o/-r: entries come from rg's own data fields
                        entries = render_json_record(
                            data_match, "match", render, path_str, delim, probe
                        )
                    else:
                        entries = [
                            MatchLine(
                                line_number=line_number,
                                text=text,
                                file=path_str,
                                submatches=tuple(_subs) if _subs else None,
                                rg_kind="match",
                            )
                        ]
                    matches.extend(entries)
                    total_matches += len(entries)
                    if path_str and entries:
                        # O(1) first-seen detection via the counts dict — the previous
                        # `path_str not in matched_file_paths` was an O(n) scan per match,
                        # degrading a common-token search on a large repo to O(matches x files).
                        new_count = match_counts_by_file.get(path_str, 0) + len(entries)
                        match_counts_by_file[path_str] = new_count
                        if new_count == len(entries):
                            matched_file_paths.append(path_str)
                elif data.get("type") == "context":
                    data_match = data["data"]
                    line_number = data_match.get("line_number", 0)
                    text = _lossy_record_text(data_match.get("lines"), delim)
                    _path_obj = data_match.get("path", {})
                    decoded_path = _decode_rg_path_field(_path_obj)
                    path_str = decoded_path or (file_path if isinstance(file_path, str) else "")
                    if render is not None:
                        matches.extend(
                            render_json_record(
                                data_match, "context", render, path_str, delim, probe
                            )
                        )
                        continue
                    matches.append(
                        MatchLine(
                            line_number=line_number,
                            text=text,
                            file=path_str,
                            rg_kind="context",
                        )
                    )
            except json.JSONDecodeError:
                pass

        return matches, matched_file_paths, match_counts_by_file, total_matches

    def _search_files_with_matches(
        self, file_path: str | list[str], pattern: str, config: SearchConfig
    ) -> SearchResult:
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        try:
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=False,
                check=False,
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            separator = b"\0" if config.null else b"\n"
            stdout = result.stdout
            if isinstance(stdout, bytes):
                matched_file_paths = [os.fsdecode(path) for path in stdout.split(separator) if path]
            else:
                # Keep compatibility with existing patched-run tests. The real child stays in
                # bytes mode so path records retain filesystem identity and CR/LF bytes.
                matched_file_paths = [
                    path for path in stdout.split(separator.decode("ascii")) if path
                ]
            # rg exit 2 with some matched files = soft partial error: keep them, flag incomplete.
            partial = result.returncode == 2 and bool(matched_file_paths)
            if result.returncode > 1 and not partial:
                stderr = decode_diagnostic_output(result.stderr).strip()
                raise BackendExecutionError(
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
                reason = (
                    decode_diagnostic_output(result.stderr).strip() or "rg exit 2 (partial results)"
                )
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
                search_result.incomplete_reason_class = "unreadable_path"
            return search_result
        except subprocess.TimeoutExpired as e:
            # L7: rg timed out mid-scan -> recover the file list it already flushed instead of
            # hard-erroring, mirroring search()'s TimeoutExpired handling. Fail-graceful, not
            # fail-crash: a `tg search -l` on a huge tree returns partial + result_incomplete.
            partial_stdout = e.stdout or b""
            separator = b"\0" if config.null else b"\n"
            if isinstance(partial_stdout, bytes):
                # Only complete delimiter-terminated names are usable after timeout. The
                # trailing fragment may be a truncated path, not a complete rg record.
                complete = partial_stdout.split(separator)
                if not partial_stdout.endswith(separator):
                    complete = complete[:-1]
                matched_file_paths = [os.fsdecode(path) for path in complete if path]
            else:
                complete = partial_stdout.split(separator.decode("ascii"))
                if not partial_stdout.endswith(separator.decode("ascii")):
                    complete = complete[:-1]
                matched_file_paths = [path for path in complete if path]
            reason = (
                "rg files-with-matches search timed out; returning the partial file list. Scope "
                "the search to a smaller path (e.g. `tg search PATTERN src/`) or raise "
                "TG_RG_TIMEOUT_SECONDS."
            )
            sys.stderr.write(f"tg: {reason}\n")
            return SearchResult(
                matches=[],
                matched_file_paths=matched_file_paths,
                match_counts_by_file=dict.fromkeys(matched_file_paths, 1),
                total_files=len(matched_file_paths),
                total_matches=len(matched_file_paths),
                routing_backend="RipgrepBackend",
                routing_reason="rg_files_with_matches",
                routing_distributed=False,
                routing_worker_count=1,
                result_incomplete=True,
                incomplete_reason=reason,
                incomplete_reason_class="timeout",
            )
        except Exception as e:
            raise BackendExecutionError(f"Ripgrep backend failed: {e}") from e

    def _parse_count_stdout(
        self,
        stdout: bytes | str,
        config: SearchConfig,
        file_path: str | list[str],
        *,
        complete_records_only: bool = False,
    ) -> tuple[int, int, list[str], dict[str, int]]:
        """Parse rg ``--count`` stdout into (total_matches, total_files, matched_file_paths,
        match_counts_by_file). Shared by the success path and the L7 TimeoutExpired partial-recovery
        path so a timed-out count still tallies whatever rg had flushed."""
        raw_stdout = os.fsencode(stdout)
        newline, nul, colon = b"\n", b"\0", b":"
        multi_file = (
            (isinstance(file_path, (list, tuple)) and len(file_path) > 1)
            or (isinstance(file_path, str) and Path(file_path).is_dir())
            or (
                isinstance(file_path, (list, tuple))
                and len(file_path) == 1
                and Path(file_path[0]).is_dir()
            )
        )
        path_prefixed = (multi_file or config.with_filename) and not config.no_filename
        if config.null and path_prefixed:
            # In --null mode rg emits `path NUL count LF`; filenames may contain
            # whitespace and CR/LF, so only the count field may be stripped/split.
            lines: list[bytes] = []
            remaining = raw_stdout
            while remaining:
                separator = remaining.find(nul)
                if separator < 0:
                    break
                path_record = remaining[:separator]
                count_start = separator + 1
                record_end = remaining.find(newline, count_start)
                if record_end < 0:
                    if complete_records_only:
                        break
                    count_record = remaining[count_start:]
                    remaining = b""
                else:
                    count_record = remaining[count_start:record_end]
                    remaining = remaining[record_end + 1 :]
                if count_record.strip():
                    lines.append(path_record + nul + count_record.strip())
        else:
            # Without --null, newline is rg's path/count framing and a filename
            # containing LF cannot be represented unambiguously by this format.
            if complete_records_only:
                final_delimiter = raw_stdout.rfind(newline)
                raw_stdout = raw_stdout[:final_delimiter] if final_delimiter >= 0 else b""
            lines = [line for line in raw_stdout.split(newline) if line.strip()]
        total_matches = 0
        total_files = 0
        matched_file_paths: list[str] = []
        match_counts_by_file: dict[str, int] = {}
        # Audit HIGH: rg emits `path:count` whenever it prints the filename, which is driven by
        # -H/--no-filename (config.with_filename/no_filename), NOT only by the multi-file heuristic.
        for line in lines:
            matched_path: str | None = None
            if config.null and nul in line:
                matched_path_raw, count_text = line.rsplit(nul, 1)
                matched_path = os.fsdecode(matched_path_raw)
            elif path_prefixed and colon in line:
                matched_path_raw, count_text = line.rsplit(colon, 1)
                matched_path = os.fsdecode(matched_path_raw)
            else:
                count_text = line
            try:
                count_value = int(count_text.strip().decode("ascii"))
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
        return total_matches, total_files, matched_file_paths, match_counts_by_file

    def _search_counts(
        self, file_path: str | list[str], pattern: str, config: SearchConfig
    ) -> SearchResult:
        cmd = self._build_cmd(file_path=file_path, pattern=pattern, config=config, json_mode=False)
        try:
            result = run_subprocess(
                cmd,
                capture_output=True,
                text=False,
                check=False,
                timeout_seconds=configured_ripgrep_timeout_seconds(),
            )
            total_matches, total_files, matched_file_paths, match_counts_by_file = (
                self._parse_count_stdout(result.stdout, config, file_path)
            )

            partial = result.returncode == 2 and total_matches > 0
            if result.returncode > 1 and not partial:
                stderr = decode_diagnostic_output(result.stderr).strip()
                raise BackendExecutionError(
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
                reason = (
                    decode_diagnostic_output(result.stderr).strip() or "rg exit 2 (partial results)"
                )
                sys.stderr.write(f"tg: rg exited 2, keeping partial results: {reason}\n")
                search_result.result_incomplete = True
                search_result.incomplete_reason = reason
                search_result.incomplete_reason_class = "unreadable_path"
            return search_result
        except subprocess.TimeoutExpired as e:
            # L7: recover the partial tally rg had flushed before the timeout instead of
            # hard-erroring, mirroring search()'s TimeoutExpired handling.
            partial_stdout = e.stdout or b""
            total_matches, total_files, matched_file_paths, match_counts_by_file = (
                self._parse_count_stdout(
                    partial_stdout, config, file_path, complete_records_only=True
                )
            )
            routing_reason = "rg_count_matches" if config.count_matches else "rg_count"
            reason = (
                "rg count search timed out; returning the partial tally. Scope the search to a "
                "smaller path or raise TG_RG_TIMEOUT_SECONDS."
            )
            sys.stderr.write(f"tg: {reason}\n")
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
                result_incomplete=True,
                incomplete_reason=reason,
                incomplete_reason_class="timeout",
            )
        except Exception as e:
            raise BackendExecutionError(f"Ripgrep backend failed: {e}") from e

    def search_passthrough(
        self, file_path: str | list[str], pattern: str, config: SearchConfig | None = None
    ) -> int:
        """
        Execute ripgrep directly and stream output to stdout/stderr without JSON re-parsing.
        Returns rg's native exit code.
        """
        try:
            cmd = self._build_cmd(
                file_path=file_path,
                pattern=pattern,
                config=config,
                json_mode=bool(config and config.json_mode),
            )
        except BackendExecutionError as exc:
            # e.g. an unsupported --engine value: exit 2 (error), never a traceback and never 1
            # ("no match") -- the streaming route has no JSON envelope, so it is a stderr line
            sys.stderr.write(f"Error: {exc}\n")
            return 2
        if config and config.quiet:
            # `-q` LIVES HERE AND NOWHERE ELSE, and the reason is a regression I shipped.
            #
            # It was first added inside `_build_cmd`, which looks like the natural home -- that is
            # where the other ~30 flags are forwarded. But `_build_cmd` has FOUR consumers and only
            # THIS one streams rg's output straight to tg's stdout. The other three PARSE it:
            #   search()                      :104   parses --json
            #   _search_files_with_matches()  :290   parses -l output
            #   _search_counts()              :409   parses --count output
            #
            # `-q` makes rg print NOTHING and exit 0 on the first match. Measured on the real rg:
            #     rg --count-matches needle f.txt      -> "2"        rg --count-matches -q ... -> ""
            #     rg -l needle f.txt                   -> "f.txt"    rg -l -q ...              -> ""
            #     rg --json needle f.txt               -> 5 lines    rg --json -q ...          -> 1
            # So a parsing consumer saw an empty stream and reported total_matches=0 -- a FALSE
            # NO-MATCH on a file that matches, and an exit-code contract violation (1 instead of 0).
            # Suppressing OUTPUT and suppressing the ANSWER are not the same thing.
            #
            # `test_quiet_survives_rg_passthrough.py` guards the placement now, and its control arm
            # asserts the three parsing consumers do NOT receive `-q`.
            cmd.append("-q")
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
            raise BackendExecutionError("RipgrepBackend requires the 'rg' binary to be installed.")

        cmd: list[str] = [binary_name]
        if json_mode:
            cmd.append("--json")

        # We enforce JSON output so we can seamlessly parse it back into our SearchResult dataclasses
        # Task #269 non-blocking note (independent gate, Part B): every flag in this block --
        # including the root-ignore-file injection below -- is gated on `config` being truthy.
        # `search_passthrough(file_path, pattern)` (no third arg) defaults `config` to `None`,
        # so a caller using that form gets ZERO of these flags, not just no ignore-file
        # injection. Not a regression (this is pre-existing behavior for every other flag here
        # too, and both real call sites, `cli/main.py:7768`/`:7843`, always pass a real
        # `config`), but worth flagging so `config=None` is never mistaken for "injection
        # covered" -- it means "no SearchConfig-derived flags at all were forwarded".
        if config:
            if not config.mmap:
                cmd.append("--no-mmap")
            if config.no_mmap:
                cmd.append("--no-mmap")
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
            # Task #269: this is the second of two Python-only real-`rg` forwarding paths
            # (the other is `bootstrap.py::_run_rg_passthrough`), reachable via every entry
            # point built on `_build_cmd` (`search`, `search_passthrough`, `_search_counts`,
            # `_search_files_with_matches`) whenever no compiled native `tg` binary is
            # discoverable. Real rg's own `.gitignore` auto-discovery requires
            # `require_git=true` by default, so a root `.gitignore` is silently a no-op outside
            # a git repo -- the same #264 defect already fixed for the compiled native binary's
            # rg-passthrough (`rust_core/src/rg_passthrough.rs::root_ignore_file_args`).
            # `file_path` here is the same root(s) real rg is about to walk (mirrors that
            # function's own `args.paths`); a pre-enumerated FILE list (the rare
            # `--files-without-match` branch in `cli/main.py`) safely no-ops via the helper's
            # own `Path.is_file()` existence check rather than emitting a wrong flag.
            # `config.unrestricted` (rg's `-u`/`-uu`/`-uuu`) is a documented ALIAS for
            # `--no-ignore` (+`--hidden`/+`--binary`) that rg's own parser expands -- NOT
            # observed by `config.no_ignore` -- so it must gate this injection too (independent
            # gate finding, task #269): without it `-u` came out STRICTER than no flag at all,
            # since the `--ignore-file` operands emitted here survive `--no-ignore` by design
            # (see this function's own `cmd.append("-" + "u" * config.unrestricted)` further
            # below, which forwards the raw token to rg -- referenced by SHAPE, not a line
            # number, per the NB-2 lesson from this task's independent gate: a raw line-number
            # citation drifted stale within the SAME commit that added it) and would otherwise
            # silently resurrect the very rules `-u` asked to disable.
            cmd.extend(
                root_ignore_file_args(
                    file_path if isinstance(file_path, list) else [file_path],
                    no_ignore=config.no_ignore,
                    no_ignore_files=config.no_ignore_files,
                    no_ignore_vcs=config.no_ignore_vcs,
                    no_ignore_dot=config.no_ignore_dot,
                    unrestricted=config.unrestricted,
                )
            )
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
            if config.replace_str is not None:
                # also in json mode: rg then adds a per-submatch `replacement` field
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
            # After --auto-hybrid-regex/--pcre2-unicode above: engine flags are last-wins in rg.
            cmd.extend(_pattern_semantics_flags(config))
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
