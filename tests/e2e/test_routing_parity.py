from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Launchers:
# 1. python -m tensor_grep
# 2. native binary (target/debug/tg.exe or target/release/tg.exe)

def _get_native_binary() -> str:
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    debug_path = Path(f"rust_core/target/debug/{exe_name}")
    release_path = Path(f"rust_core/target/release/{exe_name}")
    if release_path.exists():
        return str(release_path.resolve())
    if debug_path.exists():
        return str(debug_path.resolve())
    pytest.fail("Native binary not found. Please compile it first.")

def run_command(launcher: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    if launcher == "python-m":
        cmd = [sys.executable, "-m", "tensor_grep"] + args
    elif launcher == "native":
        cmd = [_get_native_binary()] + args
    elif launcher == "bootstrap":
        cmd = [sys.executable, str(Path("src/tensor_grep/cli/bootstrap.py").resolve())] + args
    else:
        raise ValueError(f"Unknown launcher {launcher}")
        
    return subprocess.run(cmd, capture_output=True, text=True)

LAUNCHERS = ["python-m", "native", "bootstrap"]

# Format: (args, expected_exit_code, expected_stdout_contains, expected_stderr_contains)
COMMAND_CASES = [
    # search
    (["search", "--help"], 0, "Usage:", ""),
    (["search", "foo", "sample.txt"], 0, "", ""),
    (["search", "foo", "sample.txt", "--json"], 0, '"routing_backend"', ""),
    (["search", "--cpu", "foo", "sample.txt"], 0, "", ""),
    (["search", "--cpu", "foo", "sample.txt", "--json"], 0, '"NativeCpuBackend"', ""),
    (["search", "foo", "sample.txt", "-o"], 0, "", ""),
    (["search", "foo", "sample.txt", "-r", "bar"], 0, "", ""),
    (["search", "foo", "sample.txt", "-F"], 0, "", ""),
    (["search", "foo", "sample.txt", "-w"], 0, "", ""),
    (["search", "foo", "sample.txt", "-m", "1"], 0, "", ""),
    (["search", "foo", "sample.txt", "-c"], 0, "", ""),
    (["search", "foo", "sample.txt", "-C", "1"], 0, "", ""),
    
    # run / scan / test
    (["run", "--help"], 0, "sage:", ""),
    (["scan", "--help"], 0, "sage:", ""),
    (["test", "--help"], 0, "sage:", ""),
    
    # map / doctor / session / checkpoint
    (["map", "--help"], 0, "sage:", ""),
    (["doctor", "--help"], 0, "sage:", ""),
    (["session", "--help"], 0, "sage:", ""),
    (["checkpoint", "--help"], 0, "sage:", ""),
    
    # defs / refs / context
    (["defs", "--help"], 0, "sage:", ""),
    (["refs", "--help"], 0, "sage:", ""),
    (["context", "--help"], 0, "sage:", ""),

    # blast-radius, rulesets, and audit commands
    (["blast-radius", "--help"], 0, "sage:", ""),
    (["rulesets", "--help"], 0, "sage:", ""),
    (["audit-verify", "--help"], 0, "sage:", ""),
    (["audit-history", "--help"], 0, "sage:", ""),
    (["audit-diff", "--help"], 0, "sage:", ""),
    (["review-bundle", "--help"], 0, "sage:", ""),
]

@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("args, expected_code, stdout_contains, stderr_contains", COMMAND_CASES)
def test_routing_parity_matrix(launcher: str, args: list[str], expected_code: int, stdout_contains: str, stderr_contains: str):
    # Setup dummy file for search if needed
    Path("sample.txt").write_text("foo\n", encoding="utf-8")
    
    # For `--help` commands, we might return 0, but if it's missing args, we return non-zero.
    # The matrix should be carefully designed.
    
    result = run_command(launcher, args)
    
    if expected_code is not None:
        assert result.returncode == expected_code, f"Failed on {launcher} {' '.join(args)}: {result.stderr}"
    
    if stdout_contains:
        assert stdout_contains in result.stdout, f"Failed on {launcher} {' '.join(args)}: stdout missing {stdout_contains}"
        
    if stderr_contains:
        assert stderr_contains in result.stderr, f"Failed on {launcher} {' '.join(args)}: stderr missing {stderr_contains}"
