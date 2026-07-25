"""Task #264: assert the real SEARCH OUTCOME (which files get matched), not just constructed
argv.

The Rust-side coverage added for #264 (`rust_core/src/rg_passthrough.rs`'s unit tests plus
`rust_core/tests/test_public_native_cli_parity.rs`'s CLI tests) all assert the `--ignore-file`
argv `tg` constructs for real `rg`, never spawning a real search over real files. That pins the
MECHANISM but not the OUTCOME the bug report was actually about: "which files does `tg search`
find". This file closes that gap by running the real compiled `tg` binary directly against a
real `rg` binary over an actual non-git fixture directory, and asserting on the actual matched
file set.

Skipped whenever a real `rg` binary cannot be resolved (mirrors `tests/e2e/test_rg_parity_edges.py`
and the `rg_path` fixture in `tests/conftest.py`) -- notably, the standard Linux/macOS
`test-python` CI runners do not install ripgrep, so this suite skips there; it runs on Windows
CI (bundled `benchmarks/rg.zip` fallback in `resolve_pinned_rg_binary`), in the
`benchmark-regression` job (`.github/workflows/ci.yml` installs ripgrep via apt), and locally on
any machine with `rg` on PATH.

ALSO skipped whenever no compiled native `tg` binary is discoverable (`resolve_native_tg_binary`):
the #264 fix lives ONLY in `rust_core/src/rg_passthrough.rs`, reached through that binary's own
`execute_ripgrep_search`.

WHY THIS FILE INVOKES THE NATIVE BINARY DIRECTLY INSTEAD OF `python -m tensor_grep search`
(the first version of this test used the latter, following `test_rg_parity_edges.py`'s
convention, and shipped a REAL false negative on Windows CI as a result -- see the #264 PR
discussion): `python -m tensor_grep search` goes through `tensor_grep.cli.bootstrap.main_entry`,
whose OWN native-delegation gate (`_can_delegate_to_native_tg_search`, `bootstrap.py:456-528`)
only delegates a search to the native binary when the argv contains one of a fixed
`supported_trigger` set -- `{--cpu, --force-cpu, --json, --ndjson, --gpu-device-ids}`
(`bootstrap.py:460-464`) -- or `TG_RUST_FIRST_SEARCH=1` is set (`_prefer_rust_first_search`,
`bootstrap.py:292-294`, default off). A BARE plain-text search has none of those, so bootstrap.py
ALWAYS falls through to its own separate, naive `_run_rg_passthrough` (`bootstrap.py:1088`,
reached via `bootstrap.py:1256-1260`), which forwards argv to real `rg` verbatim with zero
`--ignore-file` injection -- REGARDLESS of whether a native binary is discoverable. That is a
real, separate, still-open bug (tracked as a follow-up, out of scope for this PR), and it is
exactly what the Windows `test-python` run caught: the plain-text assertion failed with both
files present, while every Rust-level argv test passed, because `python -m tensor_grep search
needle .` never reached this PR's fix at all. Calling the compiled `tg` binary's `search`
subcommand directly (as a real user running the installed `tg` command does -- the exact shape
of the original #264 bug report) removes that extra, irrelevantly-gated Python routing layer and
tests what this PR actually changes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    from helpers import rg_parity

    return rg_parity


def _write_non_git_root_gitignore_corpus(root: Path) -> None:
    root.mkdir(parents=True)
    # Deliberately NOT `git init`: task #264 is specifically about a NON-git directory, where
    # real rg's own `.gitignore` auto-discovery is a no-op by default (`require_git=true`). A
    # `.git` marker anywhere in an ancestor of `root` would mask the exact bug this file pins.
    (root / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    (root / "skipme.txt").write_text("needle\n", encoding="utf-8")


def _run_native_tg_search(
    native_binary: Path,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    # No `--no-heading`: it is not a registered flag on the native `search` subcommand (only
    # accepted by a separate Python-side early-parse forwarding list) -- and it is unnecessary,
    # since piped/non-tty stdout (which `capture_output=True` always is) already defaults to the
    # same one-path-per-match-line shape `_matched_files_from_plain_text` below expects, verified
    # live against the bundled Windows rg binary.
    return subprocess.run(
        [str(native_binary), "search", *args],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _matched_files_from_plain_text(stdout: str) -> set[str]:
    """Extract the distinct file identifiers from `path:line:text` output, normalized so
    absolute-vs-relative and `/`-vs-`\\` differences don't cause a false mismatch.
    """
    files: set[str] = set()
    for line in stdout.replace("\r\n", "\n").splitlines():
        if not line:
            continue
        path = line.split(":", 1)[0]
        files.add(_normalize_path_str(path))
    return files


def _normalize_path_str(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


@pytest.fixture()
def non_git_root_gitignore_corpus(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "task-264-root-ignore"
    _write_non_git_root_gitignore_corpus(root)
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for #264 root-ignore-file outcome coverage")
    native_binary = rg_parity.resolve_native_tg_binary()
    if native_binary is None:
        pytest.skip(
            "no compiled native `tg` binary discoverable -- this test calls it directly to "
            "exercise this PR's fix; see the module docstring for why `python -m tensor_grep "
            "search` cannot be used as a substitute"
        )
    return root, native_binary, rg_parity.build_command_env(rg_binary)


def test_plain_text_and_json_search_agree_on_root_gitignore_outside_git_repo(
    non_git_root_gitignore_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    """The core #264 claim: an output-format flag must never change WHICH FILES are searched.
    In a non-git directory with a root `.gitignore` excluding `skipme.txt`, both plain-text
    `tg search` (rg-passthrough, this fix) and `tg search --json` (native engine, already
    correct pre-fix per #127) must return the SAME one-file set.
    """
    root, native_binary, env = non_git_root_gitignore_corpus

    plain = _run_native_tg_search(native_binary, ["needle", "."], cwd=root, env=env)
    assert plain.returncode == 0, f"stdout={plain.stdout!r}\nstderr={plain.stderr}"
    plain_files = _matched_files_from_plain_text(plain.stdout)
    assert plain_files == {"a.txt"}, (
        "plain-text `tg search` must exclude the gitignored file in a non-git directory "
        f"(task #264): got {plain_files}\nstdout={plain.stdout!r}\nstderr={plain.stderr}"
    )

    as_json = _run_native_tg_search(native_binary, ["--json", "needle", "."], cwd=root, env=env)
    assert as_json.returncode == 0, f"stdout={as_json.stdout!r}\nstderr={as_json.stderr}"
    payload = json.loads(as_json.stdout)
    json_files = {_normalize_path_str(str(p)) for p in payload["matched_file_paths"]}
    json_basenames = {Path(p).name for p in json_files}
    assert json_basenames == {"a.txt"}, (
        "`tg search --json` must also exclude the gitignored file (the pre-existing, correct "
        f"side of the #264 divergence): got {json_files}\nstdout={as_json.stdout!r}"
    )

    # THE #264 ASSERTION: both surfaces must agree on the file COUNT, regardless of output format.
    assert len(plain_files) == len(json_files) == 1


def test_no_ignore_flag_restores_the_gitignored_file(
    non_git_root_gitignore_corpus: tuple[Path, Path, dict[str, str]],
) -> None:
    """Regression control for the fix's escape hatch: `--no-ignore` must still search BOTH
    files, proving `root_ignore_file_args`'s own `no_ignore` gate (`rg_passthrough.rs:658`) is
    load-bearing end-to-end, not just at the unit-test level. Without it, an explicit
    `--ignore-file` would silently resurrect the rule `--no-ignore` asked to disable (verified
    live against rg 15.1.0 in the #264 PR; this test pins the outcome through the real CLI).
    """
    root, native_binary, env = non_git_root_gitignore_corpus

    result = _run_native_tg_search(native_binary, ["--no-ignore", "needle", "."], cwd=root, env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr}"
    matched_files = _matched_files_from_plain_text(result.stdout)
    assert matched_files == {"a.txt", "skipme.txt"}, (
        f"--no-ignore must restore the gitignored file: got {matched_files}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr}"
    )
