"""Exercise diagnostics against the native artifact in the native-build CI matrix."""

import json
import subprocess

import pytest

from tests.e2e.test_native_plain_text_parity import _require_binaries


@pytest.mark.parametrize("pattern", ["KNOWN_COMMANDS = {", "items["])
@pytest.mark.parametrize("prefix", [["search"], ["-n"]])
def test_native_literal_delimiter_hint_and_fixed_string_control(tmp_path, pattern, prefix):
    helpers, rg_binary, tg_binary = _require_binaries()
    env = helpers.build_command_env(rg_binary)
    env.pop("RIPGREP_CONFIG_PATH", None)
    fixture = tmp_path / "code.txt"
    fixture.write_bytes((pattern + "\n").encode())
    command = [str(tg_binary), *prefix, "--no-config"]
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
    expected = ("1:" if prefix == ["-n"] else "") + pattern
    assert literal.stdout.splitlines() == [expected.encode()]
    assert literal.stderr == b""


def test_native_cpu_json_preserves_error_envelope(tmp_path):
    helpers, rg_binary, tg_binary = _require_binaries()
    fixture = tmp_path / "code.txt"
    fixture.write_bytes(b"items[\n")
    env = helpers.build_command_env(rg_binary)
    env["TG_DISABLE_RG"] = "1"
    result = subprocess.run(
        [str(tg_binary), "search", "--cpu", "--json", "items[", str(fixture)],
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["error"] == "invalid_regex"
    assert "--fixed-strings (-F)" in payload["detail"]


@pytest.mark.parametrize("pattern", [r"items\[", "[a-z]", "(?x)items # {"])
def test_native_other_patterns_match_same_rg_engine(tmp_path, pattern):
    helpers, rg_binary, tg_binary = _require_binaries()
    fixture = tmp_path / "code.txt"
    fixture.write_bytes(b"items[\n")
    env = helpers.build_command_env(rg_binary)
    args = ["--no-config", pattern, str(fixture)]
    rg = subprocess.run([str(rg_binary), *args], env=env, capture_output=True, timeout=30)
    tg = subprocess.run([str(tg_binary), "search", *args], env=env, capture_output=True, timeout=30)
    assert tg.returncode == rg.returncode
    assert tg.stdout == rg.stdout
    assert tg.stderr == rg.stderr


@pytest.mark.parametrize(
    "flags", [["-P", "--no-pcre2"], ["--auto-hybrid-regex", "--no-auto-hybrid-regex"]]
)
def test_native_reset_to_default_engine_gets_literal_hint(tmp_path, flags):
    helpers, rg_binary, tg_binary = _require_binaries()
    fixture = tmp_path / "code.txt"
    fixture.write_bytes(b"items[\n")
    result = subprocess.run(
        [str(tg_binary), "search", "--no-config", *flags, "items[", str(fixture)],
        env=helpers.build_command_env(rg_binary),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.count(b"--fixed-strings (-F)") == 1
