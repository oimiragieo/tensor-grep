import io
import json
import re
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import (
    _safe_stdout_line,
    _write_path_list,
    app,
)
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_cli_modes_shared import *  # noqa: F403

# ruff: noqa: F405  -- names come from the shared wildcard import above (W4-d split)


def test_native_search_walk_stops_at_wall_clock_deadline_and_returns_partial_results(
    monkeypatch,
):
    """Critical unscoped-search-hang fix B: the native (non-Ripgrep) per-file search loop
    must honor a wall-clock deadline -- reused from the SAME resolver rg's own subprocess
    timeout uses -- and stop after the CURRENT file rather than hang forever. Results found
    before expiry must come back as `result_incomplete: true` (never silently empty, never a
    crash), and the CLI must exit 2 (rg-parity partial-results convention)."""
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.py", "b.py", "c.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            name: SearchResult(
                matches=[MatchLine(line_number=1, text="needle", file=name)],
                matched_file_paths=[name],
                match_counts_by_file={name: 1},
                total_files=1,
                total_matches=1,
            )
            for name in ("a.py", "b.py", "c.py")
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setenv("TG_RG_TIMEOUT_SECONDS", "10")

    # First monotonic() call computes the deadline (t=0 -> deadline=10). Per-file checks:
    # before a.py (t=1, still under budget, proceed); before b.py (t=20, expired, stop).
    # So only a.py is ever searched -- b.py/c.py must never be touched.
    clock_values = iter([0.0, 1.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0])
    monkeypatch.setattr(
        "tensor_grep.backends.cpu_backend.time.monotonic",
        lambda: next(clock_values),
    )

    result = CliRunner().invoke(app, ["search", "needle", ".", "--cpu", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is True
    assert "timeout" in payload["incomplete_reason"]
    assert payload["total_matches"] == 1
    assert [m["file"] for m in payload["matches"]] == ["a.py"]


def test_cli_json_no_match_emits_valid_empty_payload(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[],
                matched_file_paths=[],
                match_counts_by_file={},
                total_files=0,
                total_matches=0,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "__missing__", ".", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0
    assert payload["total_files"] == 0
    assert payload["matches"] == []


def test_cli_invalid_regex_reports_diagnostic_and_error_exit(monkeypatch):
    from tensor_grep.backends.cpu_backend import InvalidRegexError

    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _InvalidRegexBackend:
        def search(self, file_path, pattern, config=None):
            raise InvalidRegexError("invalid regex pattern: missing ), unterminated subpattern")

    class _InvalidRegexPipeline(_FakePipeline):
        def __init__(self, force_cpu=False, config=None):
            super().__init__(force_cpu=force_cpu, config=config)
            self.backend = _InvalidRegexBackend()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _InvalidRegexPipeline)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr


def test_cli_invalid_regex_is_rejected_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "(", "."])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr
    assert "cmd" not in seen


def test_cli_invalid_regex_reports_json_error_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "(", ".", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_regex"
    assert "invalid regex" in payload["detail"].lower()
    assert "cmd" not in seen


def test_cli_invalid_regex_is_rejected_before_scanning(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _UnexpectedScanner:
        def __init__(self, config=None):
            raise AssertionError("invalid regex should fail before walking broad roots")

    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _UnexpectedScanner)

    result = CliRunner().invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()


def test_cli_later_invalid_regexp_is_rejected_before_native_delegation(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(app, ["search", "-e", "safe", "-e", "(", "."])

    assert result.exit_code == 2
    assert "invalid regex" in result.stderr.lower()
    assert "cmd" not in seen


def test_cli_broad_claude_json_uses_python_guardrails_before_native(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".claude": [".claude/lib/utils.cjs"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            ".claude/lib/utils.cjs": SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="safeParseJSON(value)",
                        file=".claude/lib/utils.cjs",
                    )
                ],
                matched_file_paths=[".claude/lib/utils.cjs"],
                match_counts_by_file={".claude/lib/utils.cjs": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        raise AssertionError("broad .claude JSON search needs Python scanner guardrails")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    result = CliRunner().invoke(
        app,
        ["search", "safeParseJSON", ".claude", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 1
    assert payload["matches"][0]["file"] == ".claude/lib/utils.cjs"


def test_cli_broad_claude_ripgrep_backend_adds_guard_excludes(monkeypatch):
    seen: dict[str, object] = {}
    global _FAKE_WALK
    _FAKE_WALK = {".claude": [".claude/lib/utils.cjs"]}

    class RipgrepBackend:
        def is_available(self):
            return True

        def search_passthrough(self, paths, pattern, config=None):
            raise AssertionError("broad .claude should not use rg passthrough")

        def search(self, paths, pattern, config=None):
            seen["paths"] = list(paths)
            seen["glob"] = list(config.glob or [])
            return SearchResult(
                matches=[
                    MatchLine(
                        line_number=1,
                        text="safeParseJSON(value)",
                        file=".claude/lib/utils.cjs",
                    )
                ],
                matched_file_paths=[".claude/lib/utils.cjs"],
                match_counts_by_file={".claude/lib/utils.cjs": 1},
                total_files=1,
                total_matches=1,
                routing_backend="RipgrepBackend",
                routing_reason="rg_json",
            )

    class _RipgrepPipeline:
        def __init__(self, force_cpu=False, config=None):
            self.backend = RipgrepBackend()
            self.selected_backend_name = "RipgrepBackend"
            self.selected_backend_reason = "rg_json"
            self.selected_gpu_device_ids = []
            self.selected_gpu_chunk_plan_mb = []

        def get_backend(self):
            return self.backend

    monkeypatch.setattr("tensor_grep.backends.ripgrep_backend.RipgrepBackend", RipgrepBackend)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _RipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    result = CliRunner().invoke(
        app,
        ["search", "safeParseJSON", ".claude", "--json"],
    )

    assert result.exit_code == 0
    assert seen["paths"] == [".claude"]
    assert "!context/**" in seen["glob"]
    assert "!**/context/**" in seen["glob"]


def test_cli_rg_aggregate_json_timeout_emits_incomplete_envelope_exit2(monkeypatch):
    """Fable review of #400, finding H2: when `tg search PATTERN --json` routes to the
    ripgrep AGGREGATE backend and the rg subprocess times out, the old code let
    ``subprocess.TimeoutExpired`` fall into RipgrepBackend.search()'s broad `except
    Exception`, wrap it as a RuntimeError, and re-raise -- main.py's search command had no
    handler for that either, so it became an UNCAUGHT traceback: exit 1, no JSON envelope,
    all partial results lost. This drives the REAL RipgrepBackend (not a fake) through the
    full CLI so the fix in ripgrep_backend.py is exercised end-to-end: a timed-out
    aggregate search must instead emit a valid JSON envelope with
    ``result_incomplete: true`` and exit 2 -- the same signal already used for the native
    walk-deadline timeout (#400) and rg's own soft exit-2 partial failure.
    """
    import subprocess as _subprocess

    from tensor_grep.backends.ripgrep_backend import RipgrepBackend as RealRipgrepBackend

    global _FAKE_WALK
    _FAKE_WALK = {".": ["a.log"]}

    real_backend = RealRipgrepBackend()
    monkeypatch.setattr(RealRipgrepBackend, "_get_binary_name", lambda self: "rg")

    def _raise_timeout(*_args, **_kwargs):
        raise _subprocess.TimeoutExpired(cmd=["rg"], timeout=5, output="")

    monkeypatch.setattr("tensor_grep.backends.ripgrep_backend.run_subprocess", _raise_timeout)

    class _RipgrepPipeline:
        def __init__(self, force_cpu=False, config=None):
            self.backend = real_backend
            self.selected_backend_name = "RipgrepBackend"
            self.selected_backend_reason = "rg_json"
            self.selected_gpu_device_ids = []
            self.selected_gpu_chunk_plan_mb = []

        def get_backend(self):
            return self.backend

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _RipgrepPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeScanner)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    result = CliRunner().invoke(app, ["search", "ERROR", ".", "--json"])

    # Click's CliRunner always reports a clean sys.exit(N) as a SystemExit(N)
    # `result.exception` -- that is NOT a crash. Only fail here if something else
    # (RuntimeError, AttributeError, ...) propagated uncaught.
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"uncaught exception: {result.exception!r}")
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is True
    assert "timeout" in payload.get("incomplete_reason", "").lower()


def test_cli_wrapped_rg_regex_parse_error_reports_diagnostic(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(results_by_file={})
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    class _WrappedRgInvalidRegexBackend:
        def search(self, file_path, pattern, config=None):
            raise RuntimeError("rg failed with exit code 2: error parsing regex: missing )")

    class _WrappedRgInvalidRegexPipeline(_FakePipeline):
        def __init__(self, force_cpu=False, config=None):
            super().__init__(force_cpu=force_cpu, config=config)
            self.backend = _WrappedRgInvalidRegexBackend()

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _WrappedRgInvalidRegexPipeline)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "(", ".", "--cpu"])

    assert result.exit_code == 2
    assert "error parsing regex" in result.stderr.lower()
    assert "-P (PCRE2)" in result.stderr
    assert "--fixed-strings (-F)" in result.stderr


def test_cli_should_delegate_ndjson_search_to_native_binary_and_preserve_exit_code(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(
        "tensor_grep.cli.main._can_delegate_to_native_tg_search",
        lambda *args, **kwargs: True,
    )

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        seen["timeout"] = timeout
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ndjson"])

    assert result.exit_code == 2
    assert seen["cmd"] == ["tg.exe", "search", "--ndjson", "--", "ERROR", "."]
    assert isinstance(seen["timeout"], float) and seen["timeout"] > 0


def test_cli_should_emit_ndjson_without_native_binary(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR visible", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--ndjson"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["version"] == 1
    assert rows[0]["file"] == "a.log"
    assert rows[0]["line_number"] == 1
    assert rows[0]["text"] == "ERROR visible"
    assert rows[0]["routing_backend"] == "FakeBackend"
    assert rows[0]["routing_reason"] == "unit_test_fake_pipeline"


def test_cli_should_treat_regexp_as_pattern_when_glob_precedes_path(monkeypatch):
    global _FAKE_WALK, _FAKE_BACKEND, _LAST_PIPELINE_CONFIG
    _FAKE_WALK = {".": ["a.log"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="runCursorWorker()", file="a.log")],
                matched_file_paths=["a.log"],
                match_counts_by_file={"a.log": 1},
                total_files=1,
                total_matches=1,
            )
        }
    )
    _LAST_PIPELINE_CONFIG = None
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["search", "--json", "--glob", "scripts/agents/**", "-e", "runCursorWorker", "."],
    )

    assert result.exit_code == 0
    assert _LAST_PIPELINE_CONFIG is not None
    assert _LAST_PIPELINE_CONFIG.regexp == ["runCursorWorker"]
    assert _LAST_PIPELINE_CONFIG.glob == ["scripts/agents/**"]
    assert "runCursorWorker()" in result.stdout


def test_cli_should_delegate_json_search_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--json"])

    assert result.exit_code == 0
    assert seen["cmd"] == ["tg.exe", "search", "--json", "--", "ERROR", "."]
    assert seen["check"] is False


def test_cli_should_delegate_native_rg_output_flags(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "ERROR",
            ".",
            "--json",
            "--column",
            "--vimgrep",
            "--path-separator",
            "/",
        ],
    )

    assert result.exit_code == 0
    assert seen["cmd"] == [
        "tg.exe",
        "search",
        "--column",
        "--path-separator",
        "/",
        "--vimgrep",
        "--json",
        "--",
        "ERROR",
        ".",
    ]
    assert seen["check"] is False


def test_native_delegation_forwards_resolved_line_number():
    """The native argv builder must forward the resolved line-number decision (-n when shown, -N when
    suppressed). Otherwise an explicit --line-number/--no-line-number is silently dropped: the native
    subprocess re-derives line numbers from its own tty heuristic and ignores tg's resolved choice."""
    from tensor_grep.cli.main import _build_native_tg_search_command

    def _cmd(line_number: bool) -> list[str]:
        return _build_native_tg_search_command(
            Path("tg.exe"),
            pattern="needle",
            paths=["file.txt"],
            config=SearchConfig(force_cpu=True, line_number=line_number, line_number_explicit=True),
            ndjson=False,
        )

    assert "-n" in _cmd(True) and "-N" not in _cmd(True)
    assert "-N" in _cmd(False) and "-n" not in _cmd(False)
    # auto (non-explicit) must NOT forward — the native binary inherits tg's tty heuristic
    auto_cmd = _build_native_tg_search_command(
        Path("tg.exe"),
        pattern="needle",
        paths=["file.txt"],
        config=SearchConfig(force_cpu=True, line_number=False),
        ndjson=False,
    )
    assert "-n" not in auto_cmd and "-N" not in auto_cmd


def test_native_delegation_forwards_case_sensitive():
    """audit #19: the native argv builder forwarded -i (ignore_case) but silently dropped
    -s/--case-sensitive, so `tg search -i -s Foo` ran case-insensitive under native delegation --
    disagreeing with the same flags on the rg-passthrough path."""
    from tensor_grep.cli.main import _build_native_tg_search_command

    cmd = _build_native_tg_search_command(
        Path("tg.exe"),
        pattern="needle",
        paths=["file.txt"],
        config=SearchConfig(force_cpu=True, case_sensitive=True),
        ndjson=False,
    )
    assert "-s" in cmd

    cmd_without = _build_native_tg_search_command(
        Path("tg.exe"),
        pattern="needle",
        paths=["file.txt"],
        config=SearchConfig(force_cpu=True, case_sensitive=False),
        ndjson=False,
    )
    assert "-s" not in cmd_without


def test_uv_tool_managed_python_detection():
    """Audit #2: `tg upgrade` must recognize a uv-tool-managed launcher so it upgrades via the
    uv-tool front door (`uv tool install --force`) rather than `uv pip`/`pip` into the isolated tool
    venv, which cannot upgrade it and strands the launcher at a stale version."""
    from tensor_grep.cli.main import _is_uv_tool_managed_python

    assert _is_uv_tool_managed_python("/home/james/.local/share/uv/tools/tensor-grep/bin/python")
    assert _is_uv_tool_managed_python(
        r"C:\Users\x\AppData\Local\uv\tools\tensor-grep\Scripts\python.exe"
    )
    assert not _is_uv_tool_managed_python("/usr/bin/python3")
    assert not _is_uv_tool_managed_python(r"C:\Python312\python.exe")


def test_search_help_should_describe_json_as_aggregate_json() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "--json" in help_text
    assert "tensor-grep aggregate JSON object, not rg JSON Lines." in normalized_help
    assert "streaming" in help_text
    assert "Print results in JSON Lines format." not in help_text


def test_python_search_accepts_advertised_rg_compatibility_flags(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        seen["paths"] = list(paths)
        seen["pattern"] = pattern
        seen["config"] = config
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--passthrough",
            "--unicode",
            "--pcre2-unicode",
            "--auto-hybrid-regex",
            "--no-auto-hybrid-regex",
            "--no-pcre2-unicode",
            "--no-text",
            "--no-binary",
            "--no-follow",
            "--no-glob-case-insensitive",
            "--no-ignore-file-case-insensitive",
            "--ignore-dot",
            "--ignore-exclude",
            "--ignore-files",
            "--ignore-global",
            "--ignore-messages",
            "--ignore-parent",
            "--ignore-vcs",
            "--ignore",
            "--messages",
            "--require-git",
            "--no-hidden",
            "--no-one-file-system",
            "--no-block-buffered",
            "--no-byte-offset",
            "--no-column",
            "--no-crlf",
            "--no-encoding",
            "--no-fixed-strings",
            "--no-invert-match",
            "--no-mmap",
            "--no-multiline",
            "--no-multiline-dotall",
            "--no-pcre2",
            "--no-pre",
            "--no-search-zip",
            "--no-context-separator",
            "--no-include-zero",
            "--no-line-buffered",
            "--no-max-columns-preview",
            "--no-trim",
            "--no-json",
            "--no-stats",
            "--sort-files",
            "--maxdepth",
            "2",
            "ERROR",
            str(project),
        ],
    )

    assert result.exit_code == 0
    assert seen["paths"] == [str(project)]
    assert seen["pattern"] == "ERROR"
    config = seen["config"]
    assert config.passthru is True
    assert config.unicode is True
    assert config.pcre2_unicode is True
    assert config.auto_hybrid_regex is True
    assert config.no_auto_hybrid_regex is True
    assert config.no_pcre2_unicode is True
    assert config.no_text is True
    assert config.no_binary is True
    assert config.no_follow is True
    assert config.no_glob_case_insensitive is True
    assert config.no_ignore_file_case_insensitive is True
    assert config.ignore_dot is True
    assert config.ignore_exclude is True
    assert config.ignore_files is True
    assert config.ignore_global is True
    assert config.ignore_messages is True
    assert config.ignore_parent is True
    assert config.ignore_vcs is True
    assert config.ignore is True
    assert config.messages is True
    assert config.require_git is True
    assert config.no_hidden is True
    assert config.no_one_file_system is True
    assert config.no_block_buffered is True
    assert config.no_byte_offset is True
    assert config.no_column is True
    assert config.no_crlf is True
    assert config.no_encoding is True
    assert config.no_fixed_strings is True
    assert config.no_invert_match is True
    assert config.no_mmap is True
    assert config.no_multiline is True
    assert config.no_multiline_dotall is True
    assert config.no_pcre2 is True
    assert config.no_pre is True
    assert config.no_search_zip is True
    assert config.no_context_separator is True
    assert config.no_include_zero is True
    assert config.no_line_buffered is True
    assert config.no_max_columns_preview is True
    assert config.no_trim is True
    assert config.no_json is True
    assert config.no_stats is True
    assert config.sort_files is True
    assert config.max_depth == 2


def test_python_search_treats_file_option_as_pattern_file_not_regex(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    windows_pattern_file = r"C:\Users\oimir\patterns.txt"
    seen: dict[str, object] = {}

    def _fake_passthrough(self, paths, pattern, config=None):
        seen["paths"] = list(paths)
        seen["pattern"] = pattern
        seen["config"] = config
        return 0

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        _fake_passthrough,
    )

    result = CliRunner().invoke(
        app,
        [
            "search",
            "--format",
            "rg",
            "--file",
            windows_pattern_file,
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["paths"] == [str(project)]
    assert seen["pattern"] == ""
    config = seen["config"]
    assert config.file_patterns == [windows_pattern_file]


def test_search_file_option_rejects_only_matching(tmp_path):
    # audit #5: `-f/--file` resolves `pattern` to "" (no combined-pattern regex is built), so -o
    # against pattern="" silently returned zero matches (a false "no match"). The #441 combine
    # feature was scoped out; reject the combo instead of silently mis-searching.
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-f", str(patterns_file), "-o", str(project)],
    )

    assert result.exit_code == 2, result.output
    assert "-o/--only-matching" in result.output
    assert "-f/--file" in result.output


def test_search_file_option_rejects_only_matching_json(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-f", str(patterns_file), "-o", "--json", str(project)],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "unsupported_flag"
    assert "-o/--only-matching" in payload["detail"]


def test_search_file_option_rejects_replace(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-f", str(patterns_file), "-r", "OK", str(project)],
    )

    assert result.exit_code == 2, result.output
    assert "-r/--replace" in result.output
    assert "-f/--file" in result.output


def test_search_multiple_regexp_rejects_only_matching(tmp_path):
    # audit #5 (parenthetical): only regexp_patterns[0] is ever used as `pattern`, so -o against
    # multiple -e patterns silently drops every pattern after the first.
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-e", "ERROR", "-e", "WARN", "-o", str(project)],
    )

    assert result.exit_code == 2, result.output
    assert "-o/--only-matching" in result.output
    assert "multiple -e/--regexp" in result.output


def test_search_file_option_rejects_rank(tmp_path):
    # audit #20: --rank/--semantic would rerank against the same empty pattern="" query.
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-f", str(patterns_file), "--rank", str(project)],
    )

    assert result.exit_code == 2, result.output
    assert "--rank/--bm25" in result.output


def test_search_file_option_rejects_semantic(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-f", str(patterns_file), "--semantic", str(project)],
    )

    assert result.exit_code == 2, result.output
    assert "--semantic" in result.output


def test_search_file_option_without_conflicting_flag_still_works(monkeypatch, tmp_path):
    # Guard-rail: the new reject must not fire on the plain, already-supported -f usage.
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.log").write_text("ERROR failed\n", encoding="utf-8")
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("ERROR\n", encoding="utf-8")

    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.search_passthrough",
        lambda self, paths, pattern, config=None: 0,
    )

    result = CliRunner().invoke(
        app,
        ["search", "--format", "rg", "-f", str(patterns_file), str(project)],
    )

    assert result.exit_code == 0, result.output


def test_search_single_regexp_with_unused_file_option_and_only_matching_still_works(
    monkeypatch, tmp_path
):
    # Guard-rail: `elif regexp_patterns:` takes priority over `elif file:`, so a single -e makes
    # -f a dead flag and `pattern` a real single string -- -o must still work here, not be rejected
    # as if -f were the active pattern source.
    global _FAKE_WALK, _FAKE_BACKEND
    _FAKE_WALK = {".": ["a.py"]}
    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.py": SearchResult(
                matches=[MatchLine(line_number=1, text="prefix ERROR suffix", file="a.py")],
                total_files=1,
                total_matches=1,
            )
        }
    )
    _patch_cli_dependencies(monkeypatch)
    patterns_file = tmp_path / "patterns.txt"
    patterns_file.write_text("WARN\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["search", "-e", "ERROR", "-f", str(patterns_file), "-o", "."],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ERROR"


def test_search_version_should_run_from_python_search_entrypoint() -> None:
    result = CliRunner().invoke(app, ["search", "--version"])

    assert result.exit_code == 0
    assert "tensor-grep" in result.stdout


def test_search_help_should_describe_rg_format_as_public_exact_output() -> None:
    result = CliRunner().invoke(app, ["search", "--help"])

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    normalized_help = re.sub(r"\s+", " ", re.sub(r"[│┌┐└┘─]+", " ", help_text))
    assert "--format" in help_text
    assert "Output format: rg, json, table, or csv." in normalized_help
    assert "Use rg for exact ripgrep-style text output." in normalized_help
    assert "Internal formatter" not in help_text


def test_safe_stdout_line_writes_utf8_when_console_encoding_rejects_unicode(monkeypatch):
    class _FailingStdout:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, text: str) -> int:
            raise UnicodeEncodeError("cp1252", text, 0, 1, "simulated")

        def flush(self) -> None:
            return None

    stdout = _FailingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _safe_stdout_line("symbol: \u25cf")

    assert stdout.buffer.getvalue() == "symbol: \u25cf\n".encode()


def test_safe_stdout_line_prefers_utf8_buffer_for_non_utf_text(monkeypatch):
    class _ReplacingStdout:
        encoding = "cp437"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text.encode(self.encoding, errors="replace").decode(self.encoding))
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _ReplacingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _safe_stdout_line("a \u2014 b")

    assert stdout.writes == []
    assert stdout.buffer.getvalue() == "a \u2014 b\n".encode()


def test_write_path_list_prefers_utf8_buffer_for_non_utf_paths(monkeypatch):
    class _ReplacingStdout:
        encoding = "cp437"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text.encode(self.encoding, errors="replace").decode(self.encoding))
            return len(text)

        def flush(self) -> None:
            return None

    stdout = _ReplacingStdout()
    monkeypatch.setattr(sys, "stdout", stdout)

    _write_path_list(["ascii.txt", "unicode/\u25cf.py"], use_nul=False)

    assert stdout.writes == []
    assert stdout.buffer.getvalue() == "ascii.txt\nunicode/\u25cf.py\n".encode()


def test_cli_should_delegate_explicit_gpu_device_ids_to_native_binary(monkeypatch):
    seen: dict[str, object] = {}
    _patch_cli_dependencies(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: Path("tg.exe"))

    def _fake_run(cmd, check=False, timeout=None):
        seen["cmd"] = list(cmd)
        seen["check"] = check
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("tensor_grep.cli.main.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["search", "ERROR", ".", "--gpu-device-ids", "3,7,7"])

    assert result.exit_code == 0
    assert seen["cmd"] == ["tg.exe", "search", "--gpu-device-ids", "3,7", "--", "ERROR", "."]
    assert seen["check"] is False


def test_map_json_emits_repo_inventory_envelope(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "sample.py"
    module_path.write_text(
        "import json\n\nclass Widget:\n    pass\n\ndef add(x, y):\n    return x + y\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_sample.py"
    test_path.write_text("from src.sample import add\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["map", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "repo-map"
    assert payload["sidecar_used"] is False
    assert payload["path"] == str(project.resolve())
    assert payload["scan_limit"]["max_repo_files"] == 512
    assert payload["scan_limit"]["possibly_truncated"] is False
    assert str(module_path.resolve()) in payload["files"]
    assert str(test_path.resolve()) in payload["tests"]
    assert any(
        symbol["name"] == "Widget"
        and symbol["kind"] == "class"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "add"
        and symbol["kind"] == "function"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(module_path.resolve()) and "json" in entry["imports"]
        for entry in payload["imports"]
    )
    assert str(module_path.resolve()) in payload["related_paths"]


def test_map_json_accepts_agent_output_bounds(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    first_path = src_dir / "alpha.py"
    first_path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (src_dir / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["map", "--json", "--max-files", "1", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["files"] == [str(first_path.resolve())]
    assert payload["output_limit"] == {
        "max_files": 1,
        "emitted_files": 1,
        "original_files": 2,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        # ADDED #336: exact-dict assertions exist so a new field must be declared DELIBERATELY.
        # `project-files` is the budget cap, so True is right; an `unreadable-path` cap emits False.
        "budget_remediable": True,
    }


def test_context_json_ranks_related_files_symbols_and_tests(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\n"
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def load_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["context", "--query", "invoice payment", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-pack"
    assert payload["sidecar_used"] is False
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        symbol["name"] == "create_invoice" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "PaymentService" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert payload["related_paths"][0] == str(module_path.resolve())
    assert str(test_path.resolve()) in payload["related_paths"]


def test_context_json_accepts_agent_output_bounds(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def invoice_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["context", "--query", "invoice payment", "--json", "--max-files", "1", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["files"] == [str(module_path.resolve())]
    assert str(other_path.resolve()) not in payload["files"]
    assert payload["tests"] == [str(test_path.resolve())]
    assert payload["output_limit"] == {
        "max_files": 1,
        "emitted_files": 1,
        "original_files": 2,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        # ADDED #336: exact-dict assertions exist so a new field must be declared DELIBERATELY.
        # `project-files` is the budget cap, so True is right; an `unreadable-path` cap emits False.
        "budget_remediable": True,
    }


def test_defs_json_returns_exact_symbol_definitions(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["defs", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-defs"
    assert payload["symbol"] == "create_invoice"
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["files"] == [str(module_path.resolve())]
    assert [symbol["name"] for symbol in payload["symbols"]] == ["create_invoice"]


def test_symbol_commands_accept_path_symbol_positional_alias(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "defs": "symbol-defs",
        "source": "symbol-source",
        "impact": "symbol-impact",
        "refs": "symbol-refs",
        "callers": "symbol-callers",
        "blast-radius": "symbol-blast-radius",
        "blast-radius-render": "symbol-blast-radius-render",
        "blast-radius-plan": "symbol-blast-radius-plan",
    }
    # refs, callers, and blast-radius exit 1 when the symbol has no call sites (L1: exit 1 on
    # zero results; blast-radius joined this contract via audit #12). The symbol
    # `create_invoice` is only defined in the test file — it is never called, so
    # references/callers/blast-radius's callers are empty and the command exits 1.
    # All other commands find non-empty results (defs, source, impact) or do not use the
    # no-match exit convention at all (blast-radius-render/-plan) and still exit 0.
    commands_that_exit_1_on_empty = {"refs", "callers", "blast-radius"}

    for command, routing_reason in commands.items():
        result = runner.invoke(app, [command, str(project), "create_invoice", "--json"])
        expected_exit = 1 if command in commands_that_exit_1_on_empty else 0
        assert result.exit_code == expected_exit, (
            f"command={command!r} expected exit {expected_exit}, got {result.exit_code}:\n"
            + result.output
        )
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["symbol"] == "create_invoice"
        assert _has_expected_file(payload)


def test_symbol_commands_warn_for_legacy_symbol_option(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    expected_file = str(module_path.resolve())

    def _has_expected_file(value):
        if isinstance(value, str):
            return expected_file in value
        if isinstance(value, dict):
            return any(_has_expected_file(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_expected_file(item) for item in value)
        return False

    commands = {
        "defs": "symbol-defs",
        "source": "symbol-source",
        "impact": "symbol-impact",
        "refs": "symbol-refs",
        "callers": "symbol-callers",
        "blast-radius": "symbol-blast-radius",
        "blast-radius-render": "symbol-blast-radius-render",
        "blast-radius-plan": "symbol-blast-radius-plan",
    }
    # refs, callers, and blast-radius exit 1 when the symbol has no call sites (L1: exit 1 on
    # zero results; blast-radius joined this contract via audit #12). The symbol
    # `create_invoice` is only defined in the test file — it is never called, so
    # references/callers/blast-radius's callers are empty and the command exits 1.
    commands_that_exit_1_on_empty = {"refs", "callers", "blast-radius"}

    for command, routing_reason in commands.items():
        result = runner.invoke(
            app,
            [command, "--symbol", "create_invoice", str(project), "--json"],
        )
        expected_exit = 1 if command in commands_that_exit_1_on_empty else 0
        assert result.exit_code == expected_exit, (
            f"command={command!r} expected exit {expected_exit}, got {result.exit_code}:\n"
            + result.output
        )
        assert f"Warning: --symbol is deprecated for tg {command}" in result.stderr
        # 1.28 dogfood: the warning documents BOTH the shorthand (PATH defaults to '.') and the
        # path-first form, and no longer carries the stale 1.13.x/1.14.0 deprecation-cycle text.
        assert f"`tg {command} <SYMBOL>`" in result.stderr
        assert f"`tg {command} <PATH> <SYMBOL>`" in result.stderr
        assert "1.14.0" not in result.stderr
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["symbol"] == "create_invoice"
        assert _has_expected_file(payload)


def test_symbol_command_help_hides_legacy_symbol_option():
    runner = CliRunner()

    for command in (
        "defs",
        "source",
        "impact",
        "refs",
        "callers",
        "blast-radius",
        "blast-radius-render",
        "blast-radius-plan",
    ):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0, result.output
        assert "--symbol" not in _strip_ansi(result.stdout)


def test_symbol_commands_reject_positional_and_flag_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["defs", str(project), "create_invoice", "--symbol", "other", "--json"],
    )

    assert result.exit_code == 1
    assert "Use either positional SYMBOL or --symbol" in result.output


def test_defs_text_lists_definition_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["defs", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "definitions=1" in result.stdout
    assert f"{module_path.resolve()}:4" in result.stdout
    assert "create_invoice" in result.stdout


def test_defs_auto_corrects_reversed_symbol_path_positionals(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    # Reversed `<SYMBOL> <PATH>` order (grep muscle memory / older docs): the
    # first positional is not a path but the second one is, so it should be
    # transparently swapped instead of failing with `Path not found`.
    result = CliRunner().invoke(app, ["defs", "create_invoice", str(project)])

    assert result.exit_code == 0, result.output
    assert "Path not found" not in result.output
    assert "interpreting as `tg defs <PATH> <SYMBOL>`" in result.output
    assert "definitions=1" in result.stdout
    assert f"{module_path.resolve()}:1" in result.stdout


def test_defs_does_not_swap_when_first_positional_is_a_real_path(tmp_path):
    # When the first positional is a real path we must honor the caller's
    # explicit `<PATH> <SYMBOL>` request even if the symbol shares a name with
    # an existing path; the anti-swap guard must not fire.
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["defs", str(project), "create_invoice"])

    assert result.exit_code == 0, result.output
    assert "interpreting as" not in result.output
    assert "definitions=1" in result.stdout


def test_impact_json_returns_ranked_files_and_tests_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\ndef create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["name"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert str(test_path.resolve()) in payload["related_paths"]
    assert payload["preferred_command"] == "blast-radius"
    assert payload["preferred_command"] in payload["preferred_command_reason"]
    assert "planning signal" in payload["preferred_command_reason"]
    assert payload["trust_level"] in {"planning-signal", "heuristic"}


def test_impact_json_no_match_includes_preferred_command_metadata(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "missing", "--json", str(project)])

    # L1: symbol commands exit 1 on zero results; "missing" resolves to no files.
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["no_match"] is True
    # L1: not_found annotated by _emit_symbol_command_result
    assert payload["not_found"] is True
    assert payload["preferred_command"] == "blast-radius"
    assert payload["preferred_command"] in payload["preferred_command_reason"]
    assert "planning signal" in payload["preferred_command_reason"]
    assert payload["trust_level"] in {"planning-signal", "heuristic"}
    # H5: impact now includes a top-level "callers" key (empty list on no-match)
    assert "callers" in payload
    assert payload["callers"] == []


def test_impact_text_guides_direct_symbol_impact_to_blast_radius(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["impact", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "preferred=blast-radius for direct symbol impact" in result.stdout


def test_source_json_returns_exact_symbol_source_blocks(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["source", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-source"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["start_line"] == 1
    assert payload["sources"][0]["end_line"] == 3
    assert "subtotal = total + tax" in payload["sources"][0]["source"]
    assert [symbol["name"] for symbol in payload["symbols"]] == ["create_invoice"]


def test_symbol_source_json_omits_unrelated_symbol_inventory(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "worker.cjs"
    module_path.write_text(
        "\n".join(
            [
                "function safeParseJSON(raw) {",
                "  return JSON.parse(raw);",
                "}",
                "",
                *[f"function unrelatedSymbol{i}() {{ return {i}; }}" for i in range(50)],
                "",
            ]
        ),
        encoding="utf-8",
    )

    defs_payload = repo_map.build_symbol_defs("safeParseJSON", project)
    source_payload = repo_map.build_symbol_source("safeParseJSON", project)

    for payload in (defs_payload, source_payload):
        assert payload.get("no_match") is not True
        assert payload["definitions"][0]["file"] == str(module_path.resolve())
        assert [symbol["name"] for symbol in payload["symbols"]] == ["safeParseJSON"]
        assert "unrelatedSymbol49" not in json.dumps(payload)


def test_symbol_no_match_outputs_are_compact(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (src_dir / "worker.py").write_text(
        "def run_cursor_worker():\n    return True\n", encoding="utf-8"
    )
    (tests_dir / "test_worker.py").write_text(
        "from src.worker import run_cursor_worker\n", encoding="utf-8"
    )

    defs_payload = repo_map.build_symbol_defs("safeParseJSON", project)
    source_payload = repo_map.build_symbol_source("safeParseJSON", project)

    for payload in (defs_payload, source_payload):
        assert payload["no_match"] is True
        assert payload["definitions"] == []
        assert payload["files"] == []
        assert payload["symbols"] == []
        assert payload["imports"] == []
        assert payload["tests"] == []
        assert payload["related_paths"] == []
        assert "No exact definition found" in payload["message"]
    assert source_payload["sources"] == []


def test_refs_json_returns_python_references_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["refs", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-refs"
    assert payload["symbol"] == "create_invoice"
    assert any(ref["file"] == str(other_path.resolve()) for ref in payload["references"])
    assert str(other_path.resolve()) in payload["files"]


def test_refs_text_lists_reference_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["refs", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "references=" in result.stdout
    assert f"{other_path.resolve()}:3" in result.stdout
    assert "result = create_invoice(10, 2)" in result.stdout


def test_refs_json_deduplicates_parser_call_references(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "worker.cjs"
    module_path.write_text(
        "function prepareCursorWorkerInvocation(input) {\n"
        "  return input;\n"
        "}\n"
        "\n"
        "function runCursorWorker() {\n"
        "  return prepareCursorWorkerInvocation({});\n"
        "}\n"
        "\n"
        "module.exports = { prepareCursorWorkerInvocation, runCursorWorker };\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_refs("prepareCursorWorkerInvocation", project)

    keys = [(str(ref["file"]), int(ref["line"]), str(ref["text"])) for ref in payload["references"]]
    assert len(keys) == len(set(keys))
    assert keys == [
        (
            str(module_path.resolve()),
            6,
            "  return prepareCursorWorkerInvocation({});",
        )
    ]


def test_callers_json_returns_python_call_sites_for_symbol(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\nassert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["callers", "--symbol", "create_invoice", "--json", str(project)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-callers"
    assert payload["symbol"] == "create_invoice"
    assert any(caller["file"] == str(other_path.resolve()) for caller in payload["callers"])
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())


def test_callers_text_lists_caller_locations(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["callers", "--symbol", "create_invoice", str(project)])

    assert result.exit_code == 0
    assert "callers=1" in result.stdout
    assert f"{other_path.resolve()}:4" in result.stdout
    assert "return create_invoice(10, 2)" in result.stdout


def test_blast_radius_json_returns_transitive_symbol_radius(tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.service import build_invoice\n\n"
        "def post_invoice(total):\n"
        "    return build_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_api.py"
    test_path.write_text(
        "from src.api import post_invoice\n\n"
        "def test_post_invoice():\n"
        "    assert post_invoice(2) == 3\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["blast-radius", "--symbol", "create_invoice", "--max-depth", "2", "--json", str(project)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-blast-radius"
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 2
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["affected_files"] == payload["files"]
    assert payload["blast_radius_score"] is not None
    # H6 audit: `blast_radius_score` is `round(min(1.0, evidence_score /
    # evidence_denominator), 3)` with a non-negative numerator and a `max(1, ...)`
    # denominator (repo_map.py:19307), so it can never be negative and the upper clamp
    # makes >1.0 unreachable -- pin the exact value this deterministic fixture produces
    # (verified 3x): 0.75.
    assert payload["blast_radius_score"] == 0.75
    assert str(service_path.resolve()) in payload["files"]
    assert str(api_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(level["depth"] == 0 for level in payload["caller_tree"])
    assert any(level["depth"] == 1 for level in payload["caller_tree"])
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_blast_radius_json_no_match_exits_1(tmp_path):
    # audit #12: blast-radius never honored rg's no-match exit convention -- a typo'd/nonexistent
    # symbol previously exited 0 with an empty callers list, reading as "resolved, zero impact" on
    # a refactor-safety command instead of "never found".
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["blast-radius", "--symbol", "totally_nonexistent_symbol", "--json", str(project)]
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["no_match"] is True
    assert payload["not_found"] is True


def test_blast_radius_text_no_match_exits_1(tmp_path):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["blast-radius", "--symbol", "totally_nonexistent_symbol", str(project)]
    )

    assert result.exit_code == 1, result.output


def test_blast_radius_prioritizes_source_dirs_before_bounded_scan_cap(tmp_path):
    project = tmp_path / "project"
    archive_dir = project / "aaa_archive"
    source_dir = project / "scripts" / "agents"
    archive_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    for index in range(5):
        (archive_dir / f"note_{index}.md").write_text(f"# note {index}\n", encoding="utf-8")
    source_file = source_dir / "worker.cjs"
    source_file.write_text(
        "function prepareCursorWorkerInvocation(input) {\n  return input;\n}\n",
        encoding="utf-8",
    )

    payload = repo_map.build_symbol_blast_radius(
        "prepareCursorWorkerInvocation",
        project,
        max_repo_files=1,
    )

    assert payload.get("no_match") is not True
    assert payload["definitions"][0]["file"] == str(source_file.resolve())
    assert payload["scan_limit"] == {
        "max_repo_files": 1,
        "scanned_files": 1,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        # ADDED #336: exact-dict assertions exist so a new field must be declared DELIBERATELY.
        # `project-files` is the budget cap, so True is right; an `unreadable-path` cap emits False.
        "budget_remediable": True,
    }
