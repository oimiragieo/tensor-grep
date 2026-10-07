"""The Python bootstrap must add useful hints without altering regex semantics."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("pattern", ["KNOWN_COMMANDS = {", "items["])
def test_python_rg_route_hints_literal_delimiters(tmp_path, rg_path, pattern):
    fixture = tmp_path / "code.txt"
    fixture.write_text(pattern + "\n", encoding="utf-8")
    env = {**os.environ, "TG_DISABLE_NATIVE_TG": "1", "TG_RG_PATH": str(rg_path)}
    env.pop("RIPGREP_CONFIG_PATH", None)
    command = [sys.executable, "-m", "tensor_grep", "search", "--no-config"]
    invalid = subprocess.run(
        [*command, pattern, str(fixture)], env=env, capture_output=True, timeout=30
    )
    assert invalid.returncode == 2
    assert invalid.stdout == b""
    assert b"regex parse error" in invalid.stderr
    assert b"--fixed-strings (-F)" in invalid.stderr
    literal = subprocess.run(
        [*command, "-F", pattern, str(fixture)], env=env, capture_output=True, timeout=30
    )
    assert literal.returncode == 0
    assert literal.stdout.splitlines() == [pattern.encode()]
    assert literal.stderr == b""


@pytest.mark.parametrize(
    ("args", "hint_expected"),
    [
        (["-e", "items["], True),
        (["-ieitems["], True),
        (["--", "items["], True),
        (["-F", "--no-fixed-strings", "items["], True),
        (["[a-z]"], False),
        ([r"items\["], False),
        (["(?x)items # {"], False),
        (["-P", "items{"], False),
        (["-P", "--no-pcre2", "items["], True),
    ],
)
def test_python_hint_preserves_rg_results(tmp_path, rg_path, args, hint_expected):
    fixture = tmp_path / "code.txt"
    fixture.write_text("items[\nitems{\n", encoding="utf-8")
    env = {**os.environ, "TG_DISABLE_NATIVE_TG": "1", "TG_RG_PATH": str(rg_path)}
    env.pop("RIPGREP_CONFIG_PATH", None)
    rg = subprocess.run(
        [str(rg_path), "--no-config", *args, str(fixture)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    tg = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "search", "--no-config", *args, str(fixture)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert tg.returncode == rg.returncode
    assert tg.stdout == rg.stdout
    if hint_expected:
        assert b"--fixed-strings (-F)" in tg.stderr
        assert tg.stderr.startswith(rg.stderr)
    else:
        assert tg.stderr == rg.stderr


def test_missing_path_for_valid_regex_does_not_get_literal_hint(tmp_path, rg_path):
    env = {**os.environ, "TG_DISABLE_NATIVE_TG": "1", "TG_RG_PATH": str(rg_path)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep",
            "search",
            "--no-config",
            "[ab]",
            str(tmp_path / "absent"),
        ],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert b"--fixed-strings (-F)" not in result.stderr
