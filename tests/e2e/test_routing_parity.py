from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tensor_grep.cli.rg_contract import PUBLIC_SEARCH_HELP_FLAGS
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary

PUBLIC_TOP_LEVEL_COMMANDS = {
    "agent",
    "search",
    "calibrate",
    "upgrade",
    "update",
    "repair-launcher",
    "audit",
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
    "imports",
    "importers",
    "find",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
    "edit-plan",
    "context-render",
    "route-test",
    "prepare",
    "rulesets",
    "audit-history",
    "audit-diff",
    "review-bundle",
    "evidence",
    "ledger",
    "devices",
    "context",
    "lsp",
    "lsp-setup",
    "map",
    "orient",
    "codemap",
    "inventory",
    "docs-coverage",
    "session",
    "doctor",
    "checkpoint",
    "dogfood",
    "install-dense",
}

# Commands that are clap `visible_alias`es of another command (e.g. `update` -> `upgrade`).
# clap renders aliases in a terminal-width/platform-dependent way, so a parity check against
# clap's own rendered help must treat these as OPTIONAL in the parsed set (see
# test_empty_invocation_fallback_help_matches_public_contract).
PUBLIC_TOP_LEVEL_ALIASES = {
    "update",
}


def _get_native_binary() -> str | None:
    try:
        resolve_native_tg_binary.cache_clear()
        native_binary = resolve_native_tg_binary()
    except FileNotFoundError:
        return None
    return str(native_binary) if native_binary is not None else None


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


def test_search_force_cpu_alias_matches_cpu_flag(parity_env) -> None:
    force_result = run_command(
        "python-m", ["search", "--force-cpu", "apple", "target.txt"], cwd=parity_env
    )
    cpu_result = run_command("python-m", ["search", "--cpu", "apple", "target.txt"], cwd=parity_env)

    assert force_result.returncode == cpu_result.returncode == 0, (
        f"--force-cpu stdout: {force_result.stdout}\nstderr: {force_result.stderr}\n"
        f"--cpu stdout: {cpu_result.stdout}\nstderr: {cpu_result.stderr}"
    )
    assert force_result.stdout == cpu_result.stdout
    assert force_result.stderr == cpu_result.stderr


def test_native_top_level_pcre2_version_matches_public_contract(parity_env) -> None:
    _skip_if_native_binary_missing("native")

    result = _run_native_front_door(["--pcre2-version"], cwd=parity_env)

    assert result.returncode == 0, result.stderr
    assert "PCRE2" in result.stdout
    assert result.stderr.strip() == ""


def test_native_pcre2_without_ripgrep_fails_closed(parity_env) -> None:
    """Audit #81 finding #9: `--pcre2` must fail closed (exit 2) when rg is unavailable rather
    than silently swapping to the native regex engine, which does not support PCRE2 syntax."""
    _skip_if_native_binary_missing("native")

    env = dict(**os.environ, TG_DISABLE_RG="1")
    result = _run_native_front_door(
        ["search", "--pcre2", "apple", "target.txt"], cwd=parity_env, env=env
    )

    assert result.returncode == 2, (
        f"expected exit 2 (backend unavailable), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "--pcre2" in result.stderr
    assert result.stdout.strip() == ""


def test_native_search_required_passthrough_flag_without_ripgrep_exits_2_not_1(
    parity_env,
) -> None:
    """Audit #81 finding #7: a passthrough-required flag (e.g. --max-depth) with rg unavailable
    must exit 2 (backend unavailable), not the masked exit 1 ("no match") that bubbling the
    `execute_ripgrep_search` error through `?` to main()'s default Result termination used to
    produce."""
    _skip_if_native_binary_missing("native")

    env = dict(**os.environ, TG_DISABLE_RG="1")
    result = _run_native_front_door(
        ["search", "--max-depth", "2", "apple", "target.txt"], cwd=parity_env, env=env
    )

    assert result.returncode == 2, (
        "expected exit 2 (backend unavailable), not 1 (no-match-shaped); got "
        f"{result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_native_scan_ruleset_json_uses_python_full_contract_without_sgconfig(
    parity_env,
) -> None:
    _skip_if_native_binary_missing("native")
    (parity_env / "safe.py").write_text("def f():\n    return 'ok'\n", encoding="utf-8")

    result = _run_native_front_door(
        ["scan", "--ruleset", "secrets-basic", "--json", "--path", "."],
        cwd=parity_env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ruleset"] == "secrets-basic"
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["path"] == str(parity_env.resolve())


# We check which layer handled it.
# For native rust, search is usually native unless it falls back to Python.
# The public Python/bootstrap entrypoints delegate `run` to the managed native front door
# when one is available, so its advertised edit flags stay route-consistent.
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
    (["imports", "--help"], 0, assert_text_lines, no_check),
    (["importers", "--help"], 0, assert_text_lines, no_check),
    # --- blast-radius, rulesets, audit ---
    (["blast-radius", "--help"], 0, assert_text_lines, no_check),
    (["rulesets", "--help"], 0, assert_text_lines, no_check),
    (["audit-verify", "--help"], 0, assert_text_lines, no_check),
    (["audit-history", "--help"], 0, assert_text_lines, no_check),
    (["audit-diff", "--help"], 0, assert_text_lines, no_check),
    (["review-bundle", "--help"], 0, assert_text_lines, no_check),
    (["evidence", "--help"], 0, assert_text_lines, no_check),
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


def test_run_help_exposes_native_diff_contract_on_public_routes(parity_env):
    _skip_if_native_binary_missing("native")

    for launcher in LAUNCHERS:
        result = run_command(launcher, ["run", "--help"], cwd=parity_env)

        assert result.returncode == 0, (
            f"{launcher} run --help failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "--diff" in _strip_ansi(result.stdout), (
            f"{launcher} run --help is missing native --diff contract"
        )


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


def test_empty_invocation_fallback_help_matches_public_contract(parity_env):
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = dict(**os.environ, TG_SIDECAR_PYTHON=broken_python)

    native_help = _run_native_front_door([], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    # Unlike the passthrough parity tests above (which parse Python/Typer's stable help), this
    # fallback path is rendered by clap's own formatter (`CommandCli::command().print_help()` in
    # `print_native_top_level_help`). clap lays out the `update` visible-alias in a terminal-
    # width/platform-dependent way, so it is not always parsed back out of the rendered text --
    # the SAME byte-identical native binary passed this on one v1.76.10 release run and failed it
    # on the next (PR #616). Assert the load-bearing invariant instead of exact set equality:
    # every REAL command is present and no UNEXPECTED command leaks; only known aliases may be
    # absent. This still catches genuine native<->Python command drift.
    native_commands = _extract_visible_help_commands(native_help.stdout)
    assert native_commands <= PUBLIC_TOP_LEVEL_COMMANDS, (
        "native fallback help exposed commands outside the public contract: "
        f"{sorted(native_commands - PUBLIC_TOP_LEVEL_COMMANDS)}"
    )
    missing_real = (PUBLIC_TOP_LEVEL_COMMANDS - native_commands) - PUBLIC_TOP_LEVEL_ALIASES
    assert not missing_real, (
        f"native fallback help is missing real (non-alias) commands: {sorted(missing_real)}"
    )
    assert native_help.stderr.strip() == ""


def test_public_help_falls_back_to_native_when_python_passthrough_times_out(parity_env):
    if sys.platform == "win32":
        wrapper = parity_env / "wedged-python.cmd"
        wrapper.write_text("@echo off\r\nping -n 9 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        wrapper = parity_env / "wedged-python.sh"
        wrapper.write_text("#!/bin/sh\nsleep 8\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    env = dict(**os.environ, TG_SIDECAR_PYTHON=str(wrapper))
    if _get_native_binary() is None:
        _run_native_front_door(["--version"], cwd=parity_env)

    started = __import__("time").perf_counter()
    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env, env=env)
    elapsed = __import__("time").perf_counter() - started

    assert elapsed < 6.0, f"public help timeout fallback took too long: {elapsed:.2f}s"
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


def test_native_help_fallback_still_surfaces_moat_commands_when_python_passthrough_unavailable(
    parity_env,
):
    """Audit #97 item 1: test_empty_invocation_fallback_help_matches_public_contract already proves
    the clap fallback's auto-generated Commands: list has always contained every public command
    name (moat and maintenance alike) -- that part of the audit's "missing moat commands" framing
    does not hold against current code. What the fallback lacked was a curated, agent-oriented
    pointer to the flagship/moat commands positioned where an agent would actually see it, mirroring
    the Typer help's "AI workflows" section. This asserts that pointer exists and appears before the
    undifferentiated ~40-command wall, not buried after it."""
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = dict(**os.environ, TG_SIDECAR_PYTHON=broken_python)

    native_help = _run_native_front_door(["--help"], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    stdout = _strip_ansi(native_help.stdout)
    assert native_help.stderr.strip() == ""
    assert "AI agent moat commands" in stdout

    for moat_command in (
        "orient",
        "defs",
        "refs",
        "callers",
        "impact",
        "blast-radius",
        "map",
        "agent",
        "search",
        "mcp",
    ):
        assert moat_command in stdout, (
            f"Missing moat command {moat_command!r} in native fallback help"
        )

    moat_header_pos = stdout.index("AI agent moat commands")
    commands_list_pos = stdout.index("Commands:")
    assert moat_header_pos < commands_list_pos, (
        "the curated moat-commands pointer must appear before the auto-generated Commands: list "
        "so an agent that stops reading early still sees it"
    )


def test_help_probe_timeout_env_override_is_honored(parity_env):
    """Audit #97 item 1: TG_HELP_PROBE_TIMEOUT_MS must override the (now-3000ms) default, mirroring
    how TG_SIDECAR_TIMEOUT_MS overrides the general sidecar timeout. A short override must make the
    fallback trigger fast even against a wedged Python that never responds to --help, proving the
    env var is actually read rather than silently ignored (which would fall through to the raised
    3000ms default and take ~3s instead)."""
    if sys.platform == "win32":
        wrapper = parity_env / "wedged-python-help-probe-override.cmd"
        wrapper.write_text("@echo off\r\nping -n 9 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        wrapper = parity_env / "wedged-python-help-probe-override.sh"
        wrapper.write_text("#!/bin/sh\nsleep 8\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    env_override = dict(
        **os.environ, TG_SIDECAR_PYTHON=str(wrapper), TG_HELP_PROBE_TIMEOUT_MS="250"
    )
    env_default = dict(**os.environ, TG_SIDECAR_PYTHON=str(wrapper))
    env_default.pop("TG_HELP_PROBE_TIMEOUT_MS", None)
    if _get_native_binary() is None:
        _run_native_front_door(["--version"], cwd=parity_env)

    # Measure the 250ms override AND the 3000ms default back-to-back against the SAME wedged Python
    # under the SAME machine load, then compare the DELTA rather than an absolute wall-clock bound.
    # An absolute `elapsed < 3.0` is flaky under parallel-spawn contention: process-spawn overhead can
    # push a correctly-honored 250ms probe past 3s (false failure). The delta cancels shared
    # contention -- if the override is honored it MUST fall back markedly faster than the 3000ms
    # default, whatever the absolute machine load. (Wedged Python hangs ~8-9s, so the default probe
    # waits its full 3000ms before falling back; the override waits only ~250ms.)
    started = time.perf_counter()
    native_help = _run_native_front_door(["--help"], cwd=parity_env, env=env_override)
    override_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    default_help = _run_native_front_door(["--help"], cwd=parity_env, env=env_default)
    default_elapsed = time.perf_counter() - started

    assert override_elapsed < default_elapsed - 1.0, (
        f"TG_HELP_PROBE_TIMEOUT_MS=250 override was not honored -- override fallback took "
        f"{override_elapsed:.2f}s vs the 3000ms-default {default_elapsed:.2f}s (delta "
        f"{default_elapsed - override_elapsed:.2f}s <= 1.0s margin), consistent with the 250ms "
        "override being ignored and the raised 3000ms default being used for both."
    )
    # Both paths must still fall back cleanly to the native help.
    for result in (native_help, default_help):
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert "AI agent moat commands" in _strip_ansi(result.stdout)
        assert result.stderr.strip() == ""
