"""Part F tasks that are not the argv grammar (that lives in test_bootstrap_search_guards.py).

F.5 (A-06): a bad ``TG_NATIVE_TG_BINARY`` is a clean ASCII exit 2, never a raw traceback (exit 1 =
"no match" would be a lie), and ``tg --version`` never imports the guards module.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tensor_grep.cli import bootstrap


@pytest.fixture(autouse=True)
def _clear_native_binary_cache():
    # resolve_native_tg_binary is @lru_cache(maxsize=1): a cached result would hide the override.
    from tensor_grep.cli import runtime_paths

    runtime_paths.resolve_native_tg_binary.cache_clear()
    yield
    runtime_paths.resolve_native_tg_binary.cache_clear()


def _arm(monkeypatch, tmp_path, binary, argv=("search", "--json", "foo", ".")):
    calls: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(binary))
    monkeypatch.setenv("TG_RUST_FIRST_SEARCH", "1")
    monkeypatch.delenv("TG_DISABLE_NATIVE_TG", raising=False)
    monkeypatch.delenv("TG_REEXEC_GUARD", raising=False)
    monkeypatch.setattr(sys, "argv", ["tg", *argv])
    monkeypatch.setattr(bootstrap, "_run_native_tg_search", lambda b, a: calls.append(b) or 0)
    monkeypatch.setattr(bootstrap, "_run_native_tg_command", lambda b, a: calls.append(b) or 0)
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: calls.append("FULL"))
    monkeypatch.setattr(bootstrap, "_run_rg_passthrough", lambda *a, **k: calls.append("RG") or 0)
    return calls


def test_bad_native_binary_override_exits_2_with_message(monkeypatch, tmp_path, capsys):
    calls = _arm(monkeypatch, tmp_path, tmp_path / "nope.exe")
    with pytest.raises(SystemExit) as exc:
        bootstrap.main_entry()
    assert exc.value.code == 2 and calls == []
    assert "nope.exe" in capsys.readouterr().err


def test_bad_native_binary_override_message_is_ascii(monkeypatch, tmp_path, capsys):
    calls = _arm(monkeypatch, tmp_path, tmp_path / "né名.exe")
    with pytest.raises(SystemExit) as exc:
        bootstrap.main_entry()
    assert exc.value.code == 2 and calls == []
    assert capsys.readouterr().err.isascii()  # CLI ASCII-only output rule


def test_bad_native_binary_override_on_the_run_branch_exits_2(monkeypatch, tmp_path, capsys):
    calls = _arm(monkeypatch, tmp_path, tmp_path / "nope.exe", argv=("run", "--lang", "py", "x"))
    with pytest.raises(SystemExit) as exc:
        bootstrap.main_entry()
    assert exc.value.code == 2 and calls == []
    assert "nope.exe" in capsys.readouterr().err


def test_valid_native_binary_override_still_delegates(monkeypatch, tmp_path):  # control
    good = tmp_path / "tg.exe"
    good.write_bytes(b"")
    calls = _arm(monkeypatch, tmp_path, good)
    with pytest.raises(SystemExit) as exc:
        bootstrap.main_entry()
    assert exc.value.code == 0 and calls == [str(good.resolve())]


def test_version_fast_path_does_not_import_the_guards_module():
    code = (
        "import sys; sys.argv=['tg','--version']\n"
        "from tensor_grep.cli import bootstrap\n"
        "try:\n"
        "    bootstrap.main_entry()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('GUARDS_LOADED' if 'tensor_grep.cli.bootstrap_search_guards' in sys.modules else 'ABSENT')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        check=False,
    )
    assert "ABSENT" in out.stdout, (out.stdout, out.stderr)


# --- codex audit of #1201: no-pattern modes (`--files`, ...) must not get a sentinel --------------

import random  # noqa: E402
import shutil  # noqa: E402

from tensor_grep.cli import bootstrap_native_argv as _nav  # noqa: E402

_RG = shutil.which("rg")
_needs_rg = pytest.mark.skipif(_RG is None, reason="rg not installed")


def _rg_rc(cwd, argv):
    out = subprocess.run(
        [_RG, "--no-config", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return out.returncode


@pytest.fixture
def _files_dir(tmp_path):
    (tmp_path / "bootstrap.py").write_text("foo\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("foo\n", encoding="utf-8")
    return tmp_path


_NO_PATTERN_REPROS = [
    ["--files", "-g", "bootstrap.py", "-i"],
    ["--files", "-i"],
    ["--files", "-g", "bootstrap.py", "--hidden", "-i"],
    ["--json", "--files", "-i"],
    ["--type-list", "-i"],
    ["--help", "-i"],
    ["-h", "-i"],
    ["--version", "-i"],
    ["-V", "-i"],
    ["--pcre2-version", "-i"],
    ["--generate", "man", "-i"],
]


@pytest.mark.parametrize("argv", _NO_PATTERN_REPROS, ids=lambda a: " ".join(a))
def test_no_pattern_mode_argv_is_left_unchanged(argv):
    assert _nav.bootstrap_native_tg_search_argv(argv) == argv


@_needs_rg
@pytest.mark.parametrize("argv", _NO_PATTERN_REPROS, ids=lambda a: " ".join(a))
def test_no_pattern_mode_builder_agrees_with_rg(_files_dir, argv):
    raw = _rg_rc(_files_dir, argv)
    assert raw == 0, argv  # the oracle: rg accepts the raw argv
    assert _rg_rc(_files_dir, _nav.bootstrap_native_tg_search_argv(argv)) == raw


def test_pattern_mode_still_gets_the_sentinel():  # control: the guard is not a blanket disable
    assert _nav.bootstrap_native_tg_search_argv(["--json", "-kq", "src"]) == [
        "--json",
        "--",
        "-kq",
        "src",
    ]


@_needs_rg
def test_files_mode_differential_against_rg(_files_dir):
    # non-exec flags only: an exec flag deliberately keeps the sentinel decision (tested below)
    pool = [
        "-i",
        "-n",
        "-g",
        "bootstrap.py",
        "--hidden",
        "-S",
        "-u",
        "-kq",
        "--json",
    ]
    rng = random.Random(20261004)
    checked = 0
    for _ in range(150):
        argv = ["--files", *[rng.choice(pool) for _ in range(rng.randint(0, 4))]]
        if rng.random() < 0.5:
            rng.shuffle(argv)
        built = _nav.bootstrap_native_tg_search_argv(argv)
        assert _rg_rc(_files_dir, built) == _rg_rc(_files_dir, argv), (argv, built)
        checked += 1
    assert checked == 150


# --- closure audit of #1201: the no-pattern-mode suppression must never hide an exec flag ---------

import os  # noqa: E402
import re  # noqa: E402


@pytest.fixture
def _marker(tmp_path):
    """A HARMLESS command that records that it ran. Returns (command_path, marker_path)."""
    marker = tmp_path / "ran.txt"
    if os.name == "nt":
        script = tmp_path / "mark.cmd"
        script.write_text(f'@echo ran> "{marker}"\r\n@echo host\r\n', encoding="utf-8")
    else:
        script = tmp_path / "mark.sh"
        script.write_text(f'#!/bin/sh\necho ran > "{marker}"\necho host\n', encoding="utf-8")
        script.chmod(0o755)
    (tmp_path / "a.txt").write_text("foo\n", encoding="utf-8")
    return str(script), marker


def _exec_rows(cmd: str) -> list[list[str]]:
    return [
        [
            "--json",
            "--hostname-bin",
            cmd,
            "--files",
            "--hyperlink-format=file://{host}/{path}",
            "--color=always",
            "a.txt",
        ],
        ["--files", "--hostname-bin", cmd],
        ["--files", "--hostname-bin", cmd, "-i"],
        ["--files", f"--pre={cmd}", "-i"],
        ["--help", f"--pre={cmd}"],
    ]


def test_exec_flag_under_a_no_pattern_mode_keeps_main_sentinel_decision(_marker):
    cmd, _ = _marker
    # main (094dc97) inserted `--` before the first dash-led token here; so must this PR.
    for argv, at in zip(_exec_rows(cmd), [1, 1, 1, 1, 0], strict=True):
        built = _nav.bootstrap_native_tg_search_argv(argv)
        assert built == [*argv[:at], "--", *argv[at:]], argv


def test_version_with_z_and_pattern_source_matches_main():  # main leaves this unchanged
    argv = ["--version", "-z", "-e", "x"]
    assert _nav.bootstrap_native_tg_search_argv(argv) == argv


@_needs_rg
def test_exec_flag_under_a_no_pattern_mode_does_not_execute(_marker):
    cmd, marker = _marker
    cwd = marker.parent
    for argv in _exec_rows(cmd):
        marker.unlink(missing_ok=True)
        _rg_rc(cwd, _nav.bootstrap_native_tg_search_argv(argv))
        assert not marker.exists(), f"rg EXECUTED the marker for {argv}"


@_needs_rg
def test_marker_detects_execution_positive_control(_marker):
    cmd, marker = _marker
    # the raw (unsentineled) repro really makes rg run the command: the marker can see it
    raw = ["--files", "--hostname-bin", cmd, "--hyperlink-format=file://{host}/{path}"]
    _rg_rc(marker.parent, [*raw, "--color=always", "a.txt"])
    assert marker.exists()


@_needs_rg
def test_exec_flag_set_covers_every_rg_flag_that_can_run_a_command():
    help_text = subprocess.run(
        [_RG, "--help"], capture_output=True, text=True, timeout=30, check=False
    ).stdout
    blocks = re.split(r"\n(?=    -)", help_text)
    mentions = re.compile(r"\b(spawn\w*|executable|execut\w+|COMMAND|subprocess)\b")
    # Reviewed false positives: they mention the words but run nothing.
    not_exec = {"no-require-git", "hyperlink-format", "pretty"}
    found: set[str] = set()
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines or not lines[0].startswith("-") or not mentions.search(block):
            continue
        found.update(re.findall(r"--([a-z0-9][a-z0-9-]*)", lines[0]))
    uncovered = found - not_exec - _nav._RG_EXEC_LONG_FLAG_NAMES
    assert not uncovered, (
        f"rg flags that can run a command but are not in the exec set: {uncovered}"
    )
    assert {"pre", "hostname-bin"} <= found  # positive control: the scan really sees exec flags
