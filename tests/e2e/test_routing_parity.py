from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.rg_contract import PUBLIC_SEARCH_HELP_FLAGS

PUBLIC_TOP_LEVEL_COMMANDS = {
    "search",
    "calibrate",
    "upgrade",
    "update",
    "audit-verify",
    "mcp",
    "classify",
    "run",
    "scan",
    "test",
    "ast-info",
    "new",
    "defs",
    "refs",
    "source",
    "impact",
    "callers",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
    "edit-plan",
    "context-render",
    "rulesets",
    "audit-history",
    "audit-diff",
    "review-bundle",
    "devices",
    "context",
    "lsp",
    "map",
    "session",
    "doctor",
    "checkpoint",
}


def _get_native_binary() -> str | None:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    debug_path = Path(f"rust_core/target/debug/{exe_name}")
    release_path = Path(f"rust_core/target/release/{exe_name}")
    if release_path.exists():
        return str(release_path.resolve())
    if debug_path.exists():
        return str(debug_path.resolve())
    return None


def _resolve_cargo_exe() -> Path | None:
    cargo_name = "cargo.exe" if sys.platform == "win32" else "cargo"
    cargo_which = shutil.which("cargo")
    if cargo_which:
        resolved = Path(cargo_which)
        if resolved.name.lower() == cargo_name:
            return resolved
    fallback = Path.home() / ".cargo" / "bin" / cargo_name
    if fallback.exists():
        return fallback
    return None


def _run_native_front_door(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    native_binary = _get_native_binary()
    if native_binary is not None:
        return subprocess.run(
            [native_binary, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    cargo_exe = _resolve_cargo_exe()
    if cargo_exe is None:
        pytest.skip("native front door not available in this environment")

    manifest_path = Path(__file__).resolve().parents[2] / "rust_core" / "Cargo.toml"
    return subprocess.run(
        [
            str(cargo_exe),
            "run",
            "--quiet",
            "--manifest-path",
            str(manifest_path),
            "--bin",
            "tg",
            "--",
            *args,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


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


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _extract_visible_help_commands(stdout: str) -> set[str]:
    commands: set[str] = set()
    mode: str | None = None
    box_header_prefixes = ("┌", "╭", "┏", "+")
    box_footer_prefixes = ("└", "╰", "┗", "+")
    box_verticals = "│┃║╎┆|"
    for raw_line in stdout.splitlines():
        line = _strip_ansi(raw_line)
        stripped = line.strip()
        if stripped == "Commands:":
            mode = "plain"
            continue
        if "Commands" in stripped and stripped.startswith(box_header_prefixes):
            mode = "box"
            continue
        if mode is None:
            continue
        if mode == "plain":
            if stripped in {"Options:", "Environment overrides:"}:
                break
            cleaned = line.lstrip()
        else:
            if stripped.startswith(box_footer_prefixes):
                break
            if not stripped.startswith(tuple(box_verticals)):
                continue
            cleaned = stripped.strip(box_verticals).strip()
        if not cleaned:
            continue
        match = re.match(r"^([a-z][a-z0-9-]*)\s{2,}", cleaned)
        if match:
            commands.add(match.group(1))
            alias_match = re.search(r"\[aliases?: ([^\]]+)\]", cleaned)
            if alias_match:
                for alias in alias_match.group(1).split(","):
                    normalized = alias.strip()
                    if re.match(r"^[a-z][a-z0-9-]*$", normalized):
                        commands.add(normalized)
    return commands


def test_extract_visible_help_commands_handles_unicode_box_help() -> None:
    stdout = """
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ search               Search files for a regex pattern.                      │
│ doctor               Print diagnostics.                                     │
│ upgrade              Upgrade tensor-grep to the latest version.             │
╰──────────────────────────────────────────────────────────────────────────────╯
"""

    assert _extract_visible_help_commands(stdout) == {"search", "doctor", "upgrade"}


# We check which layer handled it.
# For native rust, search is usually native unless it falls back to python.
# `run` etc. usually fall back to python.
# We don't strictly assert the exact layer unless requested, but we can verify it doesn't crash.

COMMAND_CASES = [
    # --- search ---
    (["search", "--help"], 0, assert_text_lines, no_check),
    (["apple", "target.txt"], 0, assert_text_lines, no_check),
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


def test_routing_parity_glob_skips_native_when_binary_is_missing(monkeypatch, parity_env):
    monkeypatch.setattr(sys.modules[__name__], "_get_native_binary", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="Native binary not built"):
        _skip_if_native_binary_missing("native")


def test_search_help_exposes_required_public_flags(parity_env):
    python_help = run_command("python-m", ["search", "--help"], cwd=parity_env)
    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0

    python_stdout = _strip_ansi(python_help.stdout)
    native_stdout = _strip_ansi(native_help.stdout)

    for flag in PUBLIC_SEARCH_HELP_FLAGS:
        assert flag in python_stdout, f"Missing {flag} in python-m search --help"
        assert flag in native_stdout, f"Missing {flag} in native search --help"


def test_top_level_help_visible_commands_match_public_contract(parity_env):
    python_help = run_command("python-m", ["--help"], cwd=parity_env)
    native_help = _run_native_front_door(["--help"], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0
    python_commands = _extract_visible_help_commands(python_help.stdout)
    native_commands = _extract_visible_help_commands(native_help.stdout)

    assert python_commands == PUBLIC_TOP_LEVEL_COMMANDS
    assert native_commands == PUBLIC_TOP_LEVEL_COMMANDS


def test_empty_invocation_visible_commands_match_public_contract(parity_env):
    python_help = run_command("python-m", [], cwd=parity_env)
    native_help = _run_native_front_door([], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0
    python_commands = _extract_visible_help_commands(python_help.stdout)
    native_commands = _extract_visible_help_commands(native_help.stdout)
    assert python_commands == PUBLIC_TOP_LEVEL_COMMANDS
    assert native_commands == PUBLIC_TOP_LEVEL_COMMANDS


def test_public_help_falls_back_to_native_when_python_passthrough_is_broken(parity_env):
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = dict(**os.environ, TG_SIDECAR_PYTHON=broken_python)

    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    assert "Usage:" in native_help.stdout
    assert "search" in native_help.stdout
    assert native_help.stderr.strip() == ""


def test_public_help_falls_back_to_native_when_python_passthrough_times_out(parity_env):
    if sys.platform == "win32":
        wrapper = parity_env / "wedged-python.cmd"
        wrapper.write_text("@echo off\r\nping -n 6 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        wrapper = parity_env / "wedged-python.sh"
        wrapper.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    env = dict(**os.environ, TG_SIDECAR_PYTHON=str(wrapper))

    started = __import__("time").perf_counter()
    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env, env=env)
    elapsed = __import__("time").perf_counter() - started

    assert elapsed < 4.0, f"public help timeout fallback took too long: {elapsed:.2f}s"
    assert native_help.returncode == 0
    assert "Usage:" in native_help.stdout
    assert "search" in native_help.stdout
    assert native_help.stderr.strip() == ""


def test_unknown_first_token_without_explicit_path_behaves_like_bare_search(parity_env):
    search_cwd = parity_env / "bare_token_cwd"
    search_cwd.mkdir()
    (search_cwd / "sample.txt").write_text("help\nother\n", encoding="utf-8")

    default_result = _run_native_front_door(["help"], cwd=search_cwd)
    early_rg_result = _run_native_front_door(
        ["help"],
        cwd=search_cwd,
        env={**os.environ, "TG_RUST_EARLY_POSITIONAL_RG": "1"},
    )

    for native_result in (default_result, early_rg_result):
        assert native_result.returncode == 0
        assert "Usage:" not in native_result.stdout
        assert "help" in native_result.stdout
