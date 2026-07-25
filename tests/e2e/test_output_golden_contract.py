import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.byte_parity import decode_for_display, split_lines_preserve_cr  # noqa: E402


@pytest.fixture(scope="module")
def golden_fixture_dir(tmp_path_factory):
    dir_path = tmp_path_factory.mktemp("golden_fixtures")
    text_dir = dir_path / "text"
    text_dir.mkdir()
    # `newline="\n"` is deliberate on every write below: without it, `Path.write_text()`'s
    # default universal-newlines mode translates `\n` -> `\r\n` on Windows, so this fixture
    # would be CRLF on disk even though every literal here is LF-only. Both real `rg` and
    # tg's rg-routed path correctly preserve a file's own trailing `\r` in a matched line
    # (verified directly against a real `rg.exe`), so an unpinned-newline fixture turned an
    # innocuous test-authoring accident into a spurious CRLF "mismatch" once task #262 made
    # this suite's comparisons byte-honest -- pinning input determinism here is the fix, not
    # a new normalization on the comparison side.
    (text_dir / "file1.txt").write_text(
        "hello world\nfoo bar baz\ngoodbye world\n", encoding="utf-8", newline="\n"
    )
    (text_dir / "file2.txt").write_text(
        "nothing here\nhello again friend\nend\n", encoding="utf-8", newline="\n"
    )
    # binary file
    (dir_path / "file3.bin").write_bytes(b"some binary data\0hello\0more data")
    return dir_path


# Format: (name, args, target)
TEXT_DIR_TARGET = ["text"]
TEXT_FILE1_TARGET = ["text/file1.txt"]

# KNOWN REAL DIVERGENCE (task #262, uncovered by de-blinding this suite -- left intentionally
# RED on Windows, not xfailed/normalized away, per task instructions): `cpu_multi_file` and
# `cpu_single_file` below exercise `tg search --cpu` (the Python/native, non-rg-routed
# backend), which on Windows emits `\r\n` for a matched line whose source file is plain `\n`.
# Independently reproduced in `tests/e2e/test_multi_pattern_native.py` (repro argv there:
# `python -m tensor_grep search --cpu -e foo -e bar both.txt`) -- same root cause, second test
# file. All the OTHER cases here (including `default_*`, which route through `RipgrepBackend`)
# are unaffected: they correctly preserve a source file's own line ending (verified directly
# against real `rg.exe`) rather than injecting a new one. Fixing the `--cpu` backend's stdout
# emission is out of this PR's scope (`src/tensor_grep/` is off-limits here); this snapshot
# assertion is left honest so CI documents the real bug instead of re-hiding it.
GOLDEN_CASES = [
    ("default_multi_file", ["hello"], TEXT_DIR_TARGET),
    ("default_single_file", ["hello"], TEXT_FILE1_TARGET),
    ("cpu_multi_file", ["--cpu", "hello"], TEXT_DIR_TARGET),
    ("cpu_single_file", ["--cpu", "hello"], TEXT_FILE1_TARGET),
    ("only_matching_multi_file", ["-o", "hello"], TEXT_DIR_TARGET),
    ("only_matching_single_file", ["-o", "hello"], TEXT_FILE1_TARGET),
    ("only_matching_line_number_multi_file", ["-o", "-n", "hello"], TEXT_DIR_TARGET),
    ("only_matching_line_number_single_file", ["-o", "-n", "hello"], TEXT_FILE1_TARGET),
    ("count_multi_file", ["-c", "hello"], TEXT_DIR_TARGET),
    ("count_single_file", ["-c", "hello"], TEXT_FILE1_TARGET),
    ("count_matches_multi_file", ["--count-matches", "hello"], TEXT_DIR_TARGET),
    ("count_matches_single_file", ["--count-matches", "hello"], TEXT_FILE1_TARGET),
    ("replace_multi_file", ["-r", "HI", "hello"], TEXT_DIR_TARGET),
    ("replace_single_file", ["-r", "HI", "hello"], TEXT_FILE1_TARGET),
    (
        "replace_capture_groups_single_file",
        ["-r", "$2-$1", "(hello) (world)"],
        TEXT_FILE1_TARGET,
    ),
    ("line_number_multi_file", ["-n", "hello"], TEXT_DIR_TARGET),
    ("line_number_single_file", ["-n", "hello"], TEXT_FILE1_TARGET),
    ("binary_single_file", ["hello"], ["file3.bin"]),
    ("binary_text_flag", ["-a", "hello"], ["file3.bin"]),  # Treat binary as text
    # Pinned to --cpu (same mechanism as cpu_multi_file/cpu_single_file below): --json/--ndjson
    # emit an optional `submatches` field only when the RipgrepBackend supplies rg's byte-offset
    # data (json_fmt.py:_match_payload -- "omit the key entirely ... for non-rg backends"), so an
    # unpinned backend choice here is nondeterministic across environments (rg-on-PATH vs
    # rg-absent) even though the match set, files, counts, and text are identical either way.
    # Pinning keeps the JSON *shape* this case is testing fixed instead of silently depending on
    # whether `rg` happens to be installed on the runner (was flaky on CI Windows legs).
    ("json_multi_file", ["--cpu", "--json", "hello"], TEXT_DIR_TARGET),
    ("ndjson_multi_file", ["--cpu", "--ndjson", "hello"], TEXT_DIR_TARGET),
]

EXACT_OUTPUT_CASES = {
    "replace_capture_groups_single_file": "world-hello\n",
}

# task #121: `--count-matches` reports ripgrep's per-OCCURRENCE count, which no rg-less
# fallback engine can compute (they are line-granular only), so BOTH tg front doors now
# refuse cleanly (structured exit 2) when rg is unresolvable rather than emit a silently
# wrong line-count -- the Python path via `_exit_search_error(count_matches_requires_ripgrep)`
# (cli/main.py) and the native binary via `require_ripgrep_or_exit` (rust_core/src/main.rs).
# `run_tg` hard-asserts exit 0, so these two golden cases MUST be skipped when rg is
# unresolvable (both the `python-m` launcher's TG_DISABLE_NATIVE_TG=1 Python path and the
# `native` launcher hit the refuse). Do NOT re-snapshot: the recorded values happen to be
# correct ONLY because occurrence-count == line-count for this fixture WHEN rg is available;
# with rg absent the output is a structured error, not a count, so there is nothing stable
# to snapshot.
_COUNT_MATCHES_GOLDEN_CASES = {"count_matches_multi_file", "count_matches_single_file"}


def _ripgrep_available() -> bool:
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    return RipgrepBackend().is_available()


LAUNCHERS = ["python-m", "native"]


def _get_native_binary() -> str | None:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    debug_path = Path(f"rust_core/target/debug/{exe_name}")
    release_path = Path(f"rust_core/target/release/{exe_name}")
    if release_path.exists():
        return str(release_path.resolve())
    if debug_path.exists():
        return str(debug_path.resolve())
    return None


def _skip_if_native_binary_missing(launcher: str) -> None:
    if launcher == "native" and _get_native_binary() is None:
        pytest.skip("Native binary not built in this environment")


def _normalize_relative_prefix(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")


def run_tg(launcher, args, cwd):
    env = None
    if launcher == "python-m":
        cmd = [sys.executable, "-m", "tensor_grep", "search", *args]
        env = dict(os.environ)
        env["TG_DISABLE_NATIVE_TG"] = "1"
        env.pop("TG_NATIVE_TG_BINARY", None)
        env.pop("TG_MCP_TG_BINARY", None)
    else:
        native_binary = _get_native_binary()
        assert native_binary is not None, "Native binary not found. Please compile it first."
        cmd = [native_binary, "search", *args]

    # Raw bytes -- no text=True. `text=True` runs the pipe through Python's
    # universal-newlines TextIOWrapper, which translates a real `\r\n` in tg's own stdout to
    # `\n` on Windows before this function (or the golden snapshot it feeds) ever sees it.
    # Decoding strictly afterwards preserves any embedded `\r` verbatim and fails loudly on
    # invalid UTF-8 instead of silently laundering it (task #262).
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True)
    assert result.returncode == 0, (
        f"Command failed: {' '.join(cmd)}\n"
        f"stdout: {decode_for_display(result.stdout)}\n"
        f"stderr: {decode_for_display(result.stderr)}"
    )
    stdout = result.stdout.decode("utf-8")

    # We remove routing/stats output as they are non-contractual metadata
    stdout = "\n".join(
        line
        for line in split_lines_preserve_cr(stdout)
        if not line.startswith("[routing]") and not line.startswith("[stats]")
    )

    # We normalize the randomly generated pytest temp directory to a static string
    # as the absolute execution path is an intentional non-contract field.
    cwd_str = str(cwd)
    stdout = stdout.replace(cwd_str, "<TMP_DIR>")

    cwd_json = json.dumps(cwd_str)[1:-1]
    stdout = stdout.replace(cwd_json, "<TMP_DIR>")

    # We stabilize ordering, because file iteration order in multi-file parallel searches
    # is non-deterministic across OS/environments and is a non-contractual field.
    if "--json" in args or "--ndjson" in args:
        lines = []
        for line in split_lines_preserve_cr(stdout):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                if "version" in parsed:
                    parsed["version"] = "X"
                for non_contract_key in (
                    "routing_backend",
                    "routing_reason",
                    "routing_distributed",
                    "routing_worker_count",
                    "routing_gpu_chunk_plan_mb",
                    "requested_gpu_device_ids",
                    "routing_gpu_device_ids",
                    "sidecar_used",
                ):
                    parsed.pop(non_contract_key, None)
                if "file" in parsed and isinstance(parsed["file"], str):
                    parsed["file"] = _normalize_relative_prefix(parsed["file"])
                if "match_counts_by_file" in parsed and isinstance(
                    parsed["match_counts_by_file"], dict
                ):
                    parsed["match_counts_by_file"] = {
                        _normalize_relative_prefix(str(path)): count
                        for path, count in parsed["match_counts_by_file"].items()
                    }
                if "matched_file_paths" in parsed and isinstance(
                    parsed["matched_file_paths"], list
                ):
                    parsed["matched_file_paths"] = [
                        _normalize_relative_prefix(str(path))
                        for path in parsed["matched_file_paths"]
                    ]
                # Stabilize matches order for json array
                if "matches" in parsed:
                    for match in parsed["matches"]:
                        if "file" in match and isinstance(match["file"], str):
                            match["file"] = _normalize_relative_prefix(match["file"])
                        if "line_number" in match:
                            match["line_number"] = None
                    parsed["matches"].sort(
                        key=lambda m: (
                            m.get("file", ""),
                            m.get("line", m.get("line_number", 0)),
                            m.get("text", ""),
                        )
                    )
                lines.append(json.dumps(parsed, sort_keys=True))
            except json.JSONDecodeError:
                lines.append(line)
        # NDJSON can output objects in random order
        if "--ndjson" in args:
            lines.sort()
        return "\n".join(lines) + "\n"

    if not stdout.strip().isdigit():
        lines = [
            _normalize_relative_prefix(line)
            for line in split_lines_preserve_cr(stdout)
            if line.strip()
        ]
        lines.sort()
        stdout = "\n".join(lines) + "\n" if lines else ""

    return stdout


@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("name, args, target", GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_output_golden_contract(golden_fixture_dir, snapshot, launcher, name, args, target):
    _skip_if_native_binary_missing(launcher)
    if launcher == "native" and "-a" in args:
        pytest.skip("Native tg.exe does not support this flag currently")
    if launcher == "python-m" and "--ndjson" in args:
        pytest.skip("python -m tensor_grep requires native tg support for --ndjson")
    if name in _COUNT_MATCHES_GOLDEN_CASES and not _ripgrep_available():
        pytest.skip(
            "--count-matches needs rg for its per-occurrence count; both front doors refuse "
            "cleanly (exit 2) when rg is unresolvable (#121), so there is no count to snapshot"
        )
    tg_stdout = run_tg(launcher, args + target, golden_fixture_dir)
    if name in EXACT_OUTPUT_CASES:
        assert tg_stdout == EXACT_OUTPUT_CASES[name]
        return
    snapshot.assert_match(tg_stdout, f"{launcher}_{name}.txt")


def test_output_golden_contract_skips_native_when_binary_is_missing(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_get_native_binary", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="Native binary not built"):
        _skip_if_native_binary_missing("native")
