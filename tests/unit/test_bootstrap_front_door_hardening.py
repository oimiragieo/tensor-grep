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


# --- adversarial gate of #1201: NO F.1 relaxation may skip the exec policy ------------------------

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_LEGACY_PATH = Path(__file__).parent / "_fixtures" / "legacy_sentinel_builder.py"


def _legacy_builder():
    spec = importlib.util.spec_from_file_location("legacy_sentinel_builder", _LEGACY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.bootstrap_native_tg_search_argv


def _plausible_prefix_rows(cmd: str) -> list[list[str]]:
    hl = "--hyperlink-format=file://{host}/{path}"
    return [
        ["-ii", "foo", f"--pre={cmd}", "f.txt"],
        ["--color", "never", "foo", f"--pre={cmd}", "f.txt"],
        ["-C2", "foo", f"--pre={cmd}", "f.txt"],
        ["--max-count=5", "foo", "--search-zip", "f.txt"],
        ["--json", "-ii", "f.txt", "--files", "--hostname-bin", cmd, hl, "--color=always"],
    ]


def test_plausible_flag_prefix_does_not_skip_the_exec_policy(_marker):
    cmd, _ = _marker
    legacy = _legacy_builder()
    for argv in _plausible_prefix_rows(cmd):
        built = _nav.bootstrap_native_tg_search_argv(argv)
        assert built == legacy(argv), argv  # main's decision, exactly
        assert "--" in built and built != argv, argv  # a sentinel really was inserted


@_needs_rg
def test_plausible_flag_prefix_does_not_execute(_marker):
    cmd, marker = _marker
    (marker.parent / "f.txt").write_text("foo\n", encoding="utf-8")
    for argv in _plausible_prefix_rows(cmd):
        marker.unlink(missing_ok=True)
        _rg_rc(marker.parent, _nav.bootstrap_native_tg_search_argv(argv))
        assert not marker.exists(), f"rg EXECUTED the marker for {argv}"


@_needs_rg
def test_plausible_flag_prefix_marker_positive_control(_marker):
    cmd, marker = _marker
    (marker.parent / "f.txt").write_text("foo\n", encoding="utf-8")
    _rg_rc(marker.parent, ["-ii", "foo", f"--pre={cmd}", "f.txt"])  # the RAW argv runs it
    assert marker.exists()


_DIFF_POOL = [
    "--files", "--type-list", "-h", "--help", "-V", "--version", "--pcre2-version", "--generate",
    "man", "--generate=man", "-z", "-zi", "-iz", "-ez", "-ze", "-izeoo", "--search-zip",
    "--no-search-zip", "--pre", "--pre=x", "--no-pre", "--pre-glob=*", "--pre-glob",
    "--hostname-bin", "--hostname-bin=x", "x", "foo", "src", "-i", "-ii", "-iF", "-e", "-efoo",
    "-f", "-g", "*.py", "--json", "--cpu", "-l", "--", "-", "-m", "5", "-m5", "-ih", "-hi", "-Vi",
    "--hyperlink-format=a{host}", "--regexp=foo", "-t", "py", "--glob", "--type", "-A", "-C2",
    "--color", "never", "--format", "--lang", "--gpu-device-ids", "-T", "-r", "--replace",
    "--iglob", "-e=", "-e=foo", "--glob=*.py", "-w", "-c", "txt",
]  # fmt: skip


# An INDEPENDENT model of rg's argv grammar (not the PR's tokenizer): which exec-capable flags rg
# reads in OPTION position. The oracle for the differential must not be the code under test.
_MODEL_SHORT_VAL = set("efEmjgdtTABCMr")
_MODEL_LONG_VAL = {
    "regexp", "file", "encoding", "max-count", "threads", "glob", "max-depth", "type", "type-not",
    "after-context", "before-context", "context", "max-columns", "replace", "pre", "pre-glob",
    "dfa-size-limit", "engine", "regex-size-limit", "iglob", "ignore-file", "max-filesize",
    "type-add", "type-clear", "color", "colors", "context-separator", "field-context-separator",
    "field-match-separator", "hostname-bin", "hyperlink-format", "path-separator", "sort",
    "sortr", "generate", "format", "lang", "gpu-device-ids",
}  # fmt: skip
_MODEL_EXEC_LONG = {"pre", "pre-glob", "hostname-bin", "search-zip"}


def _model_execy(argv):
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--":
            return False
        if t == "-" or not t.startswith("-"):
            i += 1
            continue
        if t.startswith("--"):
            name = t[2:].split("=", 1)[0]
            if name in _MODEL_EXEC_LONG:
                return True
            i += 2 if name in _MODEL_LONG_VAL and "=" not in t else 1
            continue
        consumed_next = False
        for j, ch in enumerate(t[1:], start=1):
            if ch == "z":
                return True
            if ch in _MODEL_SHORT_VAL:
                consumed_next = j == len(t) - 1
                break
        i += 2 if consumed_next else 1
    return False


def test_no_argv_gains_an_exec_flag_in_option_position_versus_main():
    """Whenever main's output has NO exec flag in option position, this PR's output has none either
    (the sentinel may only be added, never withheld, in front of an exec flag)."""
    legacy = _legacy_builder()
    rng = random.Random(20261004)
    checked = regressions = 0
    for _ in range(4000):
        argv = [rng.choice(_DIFF_POOL) for _ in range(rng.randint(1, 6))]
        mine = _nav.bootstrap_native_tg_search_argv(list(argv))
        theirs = legacy(list(argv))
        checked += 1
        if _model_execy(mine) and not _model_execy(theirs):
            regressions += 1
    assert checked == 4000 and regressions == 0


# --- confirmation gate of #1201: a bare `-` is a positional, never "all options" ------------------

_BARE_DASH_REPROS = [
    ["--cpu", "-t", "txt", "-w", "-"],
    ["--json", "-g", "*.txt", "-i", "-"],
    ["--json", "--glob", "*.txt", "-c", "-"],
    ["-", "-r", "--", "--no-pre"],
    ["--iglob", "*", "--no-pre", "-r", "-z", "-"],
    ["--json", "-i", "-"],
]


@pytest.mark.parametrize("argv", _BARE_DASH_REPROS, ids=lambda a: " ".join(a))
def test_bare_dash_positional_argv_is_left_unchanged(argv):
    assert _nav.bootstrap_native_tg_search_argv(argv) == argv


def _rg_out(cwd, argv):
    out = subprocess.run(
        [_RG, "--no-config", *[t for t in argv if t != "--cpu"]],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # drop the timing-bearing JSON "summary"/"end" lines: they differ run to run
    kept = [ln for ln in out.stdout.splitlines() if "elapsed" not in ln]
    return out.returncode, kept


@_needs_rg
@pytest.mark.parametrize("argv", _BARE_DASH_REPROS, ids=lambda a: " ".join(a))
def test_bare_dash_repro_builds_to_the_same_rg_result(_files_dir, argv):
    (_files_dir / "b.txt").write_text("a - b\n", encoding="utf-8")
    raw = _rg_out(_files_dir, argv)
    built = _rg_out(_files_dir, _nav.bootstrap_native_tg_search_argv(argv))
    assert raw == built, (argv, raw, built)


@_needs_rg
def test_bare_dash_headline_really_differs_when_the_sentinel_is_wrongly_inserted(_files_dir):
    # positive control: the oracle can see the bug (pattern `-w` over stdin vs pattern `-` on cwd)
    (_files_dir / "b.txt").write_text("a - b\n", encoding="utf-8")
    raw = _rg_out(_files_dir, ["-t", "txt", "-w", "-"])
    wrong = _rg_out(_files_dir, ["-t", "txt", "--", "-w", "-"])
    assert raw[0] == 0 and wrong[0] != raw[0]


_RG_VALID_POOL = [
    "-", "-w", "-c", "-i", "-n", "-l", "-t", "txt", "-g", "*.txt", "-e", "foo", "-efoo", "-r", "X",
    "--glob=*.txt", "-e=foo", "-T", "py", "-m", "1", "-A", "1", "--json", "foo", "-F", "-v", "-o",
    "b.txt", "--iglob", "*", "--no-heading", "-S", "--hidden", "--",
]  # fmt: skip


@_needs_rg
def test_argv_main_left_alone_and_the_pr_rewrote_reads_the_same_in_rg(_files_dir):
    """Contract (b): for a VALID raw argv (rg exit 0/1) that main did not rewrite, the PR's rewrite
    must make rg do the same thing (same exit code and output). Exec flags are not in the pool."""
    (_files_dir / "b.txt").write_text("foo - b\n", encoding="utf-8")
    legacy = _legacy_builder()
    rng = random.Random(7)
    compared = 0
    for _ in range(6000):
        argv = [rng.choice(_RG_VALID_POOL) for _ in range(rng.randint(1, 6))]
        built = _nav.bootstrap_native_tg_search_argv(list(argv))
        if built == argv or legacy(list(argv)) != argv:
            continue  # not a case where the PR rewrote what main left alone
        raw = _rg_out(_files_dir, argv)
        if raw[0] not in (0, 1):
            continue  # invalid raw argv: nothing to preserve
        compared += 1
        assert raw == _rg_out(_files_dir, built), (argv, built)
        if compared >= 120:
            break
