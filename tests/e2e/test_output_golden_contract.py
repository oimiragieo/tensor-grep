import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def golden_fixture_dir(tmp_path_factory):
    dir_path = tmp_path_factory.mktemp("golden_fixtures")
    text_dir = dir_path / "text"
    text_dir.mkdir()
    (text_dir / "file1.txt").write_text(
        "hello world\nfoo bar baz\ngoodbye world\n", encoding="utf-8"
    )
    (text_dir / "file2.txt").write_text("nothing here\nhello again friend\nend\n", encoding="utf-8")
    # binary file
    (dir_path / "file3.bin").write_bytes(b"some binary data\0hello\0more data")
    return dir_path


# Format: (name, args, target)
TEXT_DIR_TARGET = ["text"]
TEXT_FILE1_TARGET = ["text/file1.txt"]

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
    ("line_number_multi_file", ["-n", "hello"], TEXT_DIR_TARGET),
    ("line_number_single_file", ["-n", "hello"], TEXT_FILE1_TARGET),
    ("binary_single_file", ["hello"], ["file3.bin"]),
    ("binary_text_flag", ["-a", "hello"], ["file3.bin"]),  # Treat binary as text
    ("json_multi_file", ["--json", "hello"], TEXT_DIR_TARGET),
    ("ndjson_multi_file", ["--ndjson", "hello"], TEXT_DIR_TARGET),
]

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

    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"Command failed: {' '.join(cmd)}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    stdout = result.stdout

    # We remove routing/stats output as they are non-contractual metadata
    stdout = "\n".join(
        line
        for line in stdout.splitlines()
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
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                if "version" in parsed:
                    parsed["version"] = "X"
                if "file" in parsed and isinstance(parsed["file"], str):
                    parsed["file"] = _normalize_relative_prefix(parsed["file"])
                # Stabilize matches order for json array
                if "matches" in parsed:
                    for match in parsed["matches"]:
                        if "file" in match and isinstance(match["file"], str):
                            match["file"] = _normalize_relative_prefix(match["file"])
                    parsed["matches"].sort(
                        key=lambda m: (m.get("file", ""), m.get("line", 0), m.get("text", ""))
                    )
                lines.append(json.dumps(parsed, sort_keys=True))
            except json.JSONDecodeError:
                lines.append(line)
        # NDJSON can output objects in random order
        if "--ndjson" in args:
            lines.sort()
        return "\n".join(lines) + "\n"

    if not stdout.strip().isdigit():
        lines = [_normalize_relative_prefix(line) for line in stdout.splitlines() if line.strip()]
        lines.sort()
        stdout = "\n".join(lines) + "\n" if lines else ""

    return stdout


@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("name, args, target", GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_output_golden_contract(golden_fixture_dir, snapshot, launcher, name, args, target):
    _skip_if_native_binary_missing(launcher)
    if launcher == "native" and ("--count-matches" in args or "-a" in args):
        pytest.skip("Native tg.exe does not support this flag currently")
    if launcher == "python-m" and "--ndjson" in args:
        pytest.skip("python -m tensor_grep requires native tg support for --ndjson")
    tg_stdout = run_tg(launcher, args + target, golden_fixture_dir)
    snapshot.assert_match(tg_stdout, f"{launcher}_{name}.txt")


def test_output_golden_contract_skips_native_when_binary_is_missing(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_get_native_binary", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="Native binary not built"):
        _skip_if_native_binary_missing("native")
