from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


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


def run_command(
    launcher: str, args: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if launcher == "python-m":
        cmd = [sys.executable, "-m", "tensor_grep", *args]
    elif launcher == "native":
        native_binary = _get_native_binary()
        assert native_binary is not None, "Native binary not found. Please compile it first."
        cmd = [native_binary, *args]
    elif launcher == "bootstrap":
        cmd = [sys.executable, str(Path("src/tensor_grep/cli/bootstrap.py").resolve()), *args]
    else:
        raise ValueError(f"Unknown launcher {launcher}")

    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


LAUNCHERS = ["python-m", "native", "bootstrap"]


# A fixture to create a realistic search environment
@pytest.fixture(scope="module")
def parity_env(tmp_path_factory):
    env_dir = tmp_path_factory.mktemp("parity_env")
    (env_dir / "target.txt").write_text("apple\nbanana\napple banana\ncherry\n", encoding="utf-8")
    (env_dir / "other.log").write_text("apple juice\nno match here\n", encoding="utf-8")
    (env_dir / "dir").mkdir()
    (env_dir / "dir" / "nested.txt").write_text("banana apple\n", encoding="utf-8")
    (env_dir / "dir" / "second.txt").write_text("apple tart\n", encoding="utf-8")
    return env_dir


# Format: (args, expected_exit_code, check_stdout_fn, check_stderr_fn)
# We use simple string match or lambda for flexibility.
def assert_json(stdout: str):
    assert stdout.strip() != ""
    for line in stdout.strip().splitlines():
        if not line.startswith("[routing]"):  # rust might print this to stderr, but just in case
            json.loads(line)


def assert_ndjson(stdout: str):
    assert stdout.strip() != ""
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    assert len(lines) > 0
    for line in lines:
        try:
            json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Invalid NDJSON line: {line}")


def assert_text_lines(stdout: str):
    assert len(stdout.splitlines()) > 0


def assert_empty(stdout: str):
    assert not stdout.strip()


def no_check(out: str):
    pass


# We check which layer handled it.
# For native rust, search is usually native unless it falls back to python.
# `run` etc. usually fall back to python.
# We don't strictly assert the exact layer unless requested, but we can verify it doesn't crash.

COMMAND_CASES = [
    # --- search ---
    (["search", "--help"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "--json"], 0, assert_json, no_check),
    (["search", "apple", "target.txt", "--ndjson"], 0, assert_ndjson, no_check),
    (["search", "--cpu", "apple", "target.txt"], 0, assert_text_lines, no_check),
    (["search", "--cpu", "apple", "target.txt", "--json"], 0, assert_json, no_check),
    (["search", "apple", "target.txt", "-o"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-r", "orange"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-F"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-w"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-m", "1"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-c"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-C", "1"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "--glob", "*.txt"], 0, assert_text_lines, no_check),
    # missing target
    (
        ["search", "notfoundinanyfile", "target.txt"],
        1,
        no_check,
        no_check,
    ),  # usually exit 1 for no matches
    # --- run / scan / test ---
    (["run", "--help"], 0, assert_text_lines, no_check),
    (["scan", "--help"], 0, assert_text_lines, no_check),
    (["test", "--help"], 0, assert_text_lines, no_check),
    # --- map / doctor / session / checkpoint ---
    (["map", "--help"], 0, assert_text_lines, no_check),
    (["doctor", "--help"], 0, assert_text_lines, no_check),
    (["session", "--help"], 0, assert_text_lines, no_check),
    (["checkpoint", "--help"], 0, assert_text_lines, no_check),
    # --- defs / refs / context ---
    (["defs", "--help"], 0, assert_text_lines, no_check),
    (["refs", "--help"], 0, assert_text_lines, no_check),
    (["context", "--help"], 0, assert_text_lines, no_check),
    # --- blast-radius, rulesets, audit ---
    (["blast-radius", "--help"], 0, assert_text_lines, no_check),
    (["rulesets", "--help"], 0, assert_text_lines, no_check),
    (["audit-verify", "--help"], 0, assert_text_lines, no_check),
    (["audit-history", "--help"], 0, assert_text_lines, no_check),
    (["audit-diff", "--help"], 0, assert_text_lines, no_check),
    (["review-bundle", "--help"], 0, assert_text_lines, no_check),
]


@pytest.mark.parametrize("args, expected_code, check_stdout, check_stderr", COMMAND_CASES)
def test_routing_parity_matrix(
    parity_env, args: list[str], expected_code: int, check_stdout, check_stderr
):
    if "--ndjson" in args and _get_native_binary() is None:
        pytest.skip("python -m tensor_grep requires native tg support for --ndjson")

    # We will use python-m as the baseline
    baseline_result = run_command("python-m", args, cwd=parity_env)

    if expected_code is not None:
        assert baseline_result.returncode == expected_code, (
            f"Failed on python-m {' '.join(args)}\nstdout: {baseline_result.stdout}\nstderr: {baseline_result.stderr}"
        )

    check_stdout(baseline_result.stdout)
    check_stderr(baseline_result.stderr)

    for launcher in ["native", "bootstrap"]:
        _skip_if_native_binary_missing(launcher)
        result = run_command(launcher, args, cwd=parity_env)

        assert result.returncode == baseline_result.returncode, (
            f"Exit code mismatch for {launcher} vs python-m on args: {args}\npython-m: {baseline_result.returncode}\n{launcher}: {result.returncode}"
        )

        # To compare stdout/stderr we ignore execution time logs, routing reason logs, or other non-deterministic stuff.
        def clean_output(output: str) -> str:
            output = output.replace("bootstrap.py", "python -m tensor_grep")
            lines = [
                line
                for line in output.splitlines()
                if not line.startswith("[routing]") and not line.startswith("[stats]")
            ]
            return "\n".join(lines)

        bl_stdout = clean_output(baseline_result.stdout)
        la_stdout = clean_output(result.stdout)

        if "--help" in args:
            # Typer and Clap output differently formatted help text. Even between python-m and bootstrap,
            # Typer's word wrapping changes based on the length of sys.argv[0].
            # We assert that both commands successfully generated help text (Usage string is present).
            assert "Usage:" in la_stdout or "usage:" in la_stdout.lower(), (
                f"Help missing Usage:\n{la_stdout}"
            )
            assert "Usage:" in bl_stdout or "usage:" in bl_stdout.lower(), (
                f"Baseline help missing Usage:\n{bl_stdout}"
            )
        else:
            if "--json" in args or "--ndjson" in args:
                # normalize json before comparing
                def _norm_json(out: str) -> str:
                    try:
                        return json.dumps(json.loads(out), sort_keys=True)
                    except json.JSONDecodeError:
                        return out

                def _norm_ndjson(out: str) -> str:
                    return "\n".join(
                        json.dumps(json.loads(line), sort_keys=True) if line.strip() else line
                        for line in out.splitlines()
                    )

                if "--json" in args:
                    assert _norm_json(la_stdout) == _norm_json(bl_stdout), (
                        f"Stdout JSON mismatch for {launcher} vs python-m on args: {args}"
                    )
                else:
                    assert _norm_ndjson(la_stdout) == _norm_ndjson(bl_stdout), (
                        f"Stdout NDJSON mismatch for {launcher} vs python-m on args: {args}"
                    )
            else:
                assert la_stdout == bl_stdout, (
                    f"Stdout mismatch for {launcher} vs python-m on args: {args}"
                )

        bl_stderr = clean_output(baseline_result.stderr)
        la_stderr = clean_output(result.stderr)

        # Only compare stderr if not help/version since typr/clap help might differ slightly
        if "--help" not in args:
            assert la_stderr == bl_stderr, (
                f"Stderr mismatch for {launcher} vs python-m on args: {args}"
            )


@pytest.mark.parametrize("launcher", ["bootstrap"])
def test_routing_parity_glob(parity_env, launcher: str):
    # Test glob filtering across multiple matched files in a nested directory. This avoids
    # relying on platform-specific `**/*.txt` behavior in raw ripgrep passthrough while
    # still exercising bootstrap-to-python launcher parity for glob handling.
    args = ["search", "apple", ".", "--glob", "dir/*.txt"]
    bl_result = run_command("python-m", args, cwd=parity_env)
    la_result = run_command(launcher, args, cwd=parity_env)

    assert la_result.returncode == bl_result.returncode == 0

    def extract_matched_files(stdout: str) -> set[str]:
        files = set()
        for line in stdout.splitlines():
            if ":" in line and not line.startswith("["):
                # Extract filepath, normalizing prefixes and separators for cross-platform
                # comparison between rg passthrough and Python/native launchers.
                filepath = line.split(":", 1)[0].replace(".\\", "").replace("./", "")
                filepath = filepath.replace("\\", "/")
                files.add(filepath)
        return files

    bl_files = extract_matched_files(bl_result.stdout)
    la_files = extract_matched_files(la_result.stdout)

    assert la_files == bl_files
    assert la_files


def test_routing_parity_glob_skips_native_when_binary_is_missing(monkeypatch, parity_env):
    monkeypatch.setattr(sys.modules[__name__], "_get_native_binary", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="Native binary not built"):
        _skip_if_native_binary_missing("native")
