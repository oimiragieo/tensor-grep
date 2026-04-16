import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def golden_fixture_dir(tmp_path_factory):
    dir_path = tmp_path_factory.mktemp("golden_fixtures")
    (dir_path / "file1.txt").write_text(
        "hello world\nfoo bar baz\ngoodbye world\n", encoding="utf-8"
    )
    (dir_path / "file2.txt").write_text("nothing here\nhello again friend\nend\n", encoding="utf-8")
    # binary file
    (dir_path / "file3.bin").write_bytes(b"some binary data\0hello\0more data")
    return dir_path


# Format: (name, args, target)
GOLDEN_CASES = [
    ("default_multi_file", ["hello"], ["."]),
    ("default_single_file", ["hello"], ["file1.txt"]),
    ("cpu_multi_file", ["--cpu", "hello"], ["."]),
    ("cpu_single_file", ["--cpu", "hello"], ["file1.txt"]),
    ("only_matching_multi_file", ["-o", "hello"], ["."]),
    ("only_matching_single_file", ["-o", "hello"], ["file1.txt"]),
    ("only_matching_line_number_multi_file", ["-o", "-n", "hello"], ["."]),
    ("only_matching_line_number_single_file", ["-o", "-n", "hello"], ["file1.txt"]),
    ("count_multi_file", ["-c", "hello"], ["."]),
    ("count_single_file", ["-c", "hello"], ["file1.txt"]),
    ("count_matches_multi_file", ["--count-matches", "hello"], ["."]),
    ("count_matches_single_file", ["--count-matches", "hello"], ["file1.txt"]),
    ("replace_multi_file", ["-r", "HI", "hello"], ["."]),
    ("replace_single_file", ["-r", "HI", "hello"], ["file1.txt"]),
    ("line_number_multi_file", ["-n", "hello"], ["."]),
    ("line_number_single_file", ["-n", "hello"], ["file1.txt"]),
    ("binary_multi_file", ["hello"], ["."]),
    ("binary_single_file", ["hello"], ["file3.bin"]),
    ("binary_text_flag", ["-a", "hello"], ["file3.bin"]),  # Treat binary as text
    ("json_multi_file", ["--json", "hello"], ["."]),
    ("ndjson_multi_file", ["--ndjson", "hello"], ["."]),
]

LAUNCHERS = ["python-m", "native"]


def _get_native_binary() -> str:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    debug_path = Path(f"rust_core/target/debug/{exe_name}")
    release_path = Path(f"rust_core/target/release/{exe_name}")
    if release_path.exists():
        return str(release_path.resolve())
    if debug_path.exists():
        return str(debug_path.resolve())
    pytest.fail("Native binary not found. Please compile it first.")


def run_tg(launcher, args, cwd):
    if launcher == "python-m":
        cmd = [sys.executable, "-m", "tensor_grep", "search", *args]
    else:
        cmd = [_get_native_binary(), "search", *args]

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
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
                # Stabilize matches order for json array
                if "matches" in parsed:
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
        lines = [line for line in stdout.splitlines() if line.strip()]
        lines.sort()
        stdout = "\n".join(lines) + "\n" if lines else ""

    return stdout


@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("name, args, target", GOLDEN_CASES, ids=[c[0] for c in GOLDEN_CASES])
def test_output_golden_contract(golden_fixture_dir, snapshot, launcher, name, args, target):
    if launcher == "native" and ("--count-matches" in args or "-a" in args):
        pytest.skip("Native tg.exe does not support this flag currently")
    tg_stdout = run_tg(launcher, args + target, golden_fixture_dir)
    snapshot.assert_match(tg_stdout, f"{launcher}_{name}.txt")
