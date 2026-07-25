"""Task #269: assert the real SEARCH OUTCOME (which files get matched) through the two
Python-only real-`rg` forwarding paths, forcing the no-native-binary channel deterministically.

`tests/e2e/test_search_root_ignore_file_outcome.py` (task #264 / PR #744) pins the identical
outcome contract for the COMPILED native `tg` binary's rg-passthrough
(`rust_core/src/rg_passthrough.rs`), and deliberately SKIPS whenever no native `tg` binary is
discoverable -- because while writing that test, the author found `python -m tensor_grep search`
falls back to TWO separate, Python-only rg-passthrough implementations
(`tensor_grep.cli.bootstrap._run_rg_passthrough` and
`tensor_grep.backends.ripgrep_backend.RipgrepBackend._build_cmd`, the latter reached via
`cli/main.py`'s full-CLI search command for both plain-text and `--json`) that shared the
identical pre-#264-fix defect and were explicitly out of scope for that PR.

This file closes that gap for the Python channel: unlike the compiled-native test, the
no-native-binary condition can be FORCED deterministically here (`TG_DISABLE_NATIVE_TG=1`)
rather than merely hoped for, so this suite never needs to skip on that account -- it always
exercises `bootstrap.py:1088` (plain-text) and `ripgrep_backend.py`'s `_build_cmd` (via
`cli/main.py`'s full CLI, `--json`) for real, over a real non-git fixture directory and a real
`rg` binary.

WHY PLAIN-TEXT AND `--json` DIVERGE AT ALL (the actual mechanism behind the whole #264/#269 bug
family, independently confirmed here and by the sibling #744 fix on PR c597b85): bootstrap's
own native-delegation gate, `_can_delegate_to_native_tg_search` (`bootstrap.py:456-464`), only
forwards a search to the compiled native binary when argv contains one of a fixed
`supported_trigger` set -- `{--cpu, --force-cpu, --json, --ndjson, --gpu-device-ids}` -- or
`TG_RUST_FIRST_SEARCH=1` is set (`_prefer_rust_first_search`, `bootstrap.py:292-294`, default
off). A bare plain-text `tg search PATTERN .` has none of those triggers, so bootstrap ALWAYS
falls through to its own `_run_rg_passthrough` REGARDLESS of whether a native binary is
discoverable (see `tests/unit/test_cli_bootstrap.py::
test_main_entry_bare_plain_text_search_bypasses_native_delegation_even_when_native_binary_present`
for the routing-only proof, which pins this even with a native binary resolvable -- not merely
absent). `--json` IS a supported trigger, so it can reach the native engine (which already
honors root ignore files correctly per #127) whenever a native binary exists; the output-format
flag doesn't change ignore-handling directly, it changes WHICH ENGINE runs. That is the real
generator of "an output-format flag changes the file set" -- flagged here for visibility, not
fixed (a delegation gate keyed on output-format flags is a broader design question, out of
scope for this task).

This file forces the no-native-binary cell specifically (the pip/uvx pure-Python channel this
task's bug lives in) via `TG_DISABLE_NATIVE_TG=1`, so it is not sensitive to whether a native
binary happens to be discoverable on the machine running it.

Skipped only when a real `rg` binary cannot be resolved (mirrors
`tests/e2e/test_search_root_ignore_file_outcome.py` and the `rg_path` fixture in
`tests/conftest.py`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _helpers():
    from helpers import rg_parity

    return rg_parity


def _write_non_git_root_gitignore_corpus(root: Path) -> None:
    root.mkdir(parents=True)
    # Deliberately NOT `git init`: task #264/#269 is specifically about a NON-git directory,
    # where real rg's own `.gitignore` auto-discovery is a no-op by default
    # (`require_git=true`). A `.git` marker anywhere in an ancestor of `root` would mask the
    # exact bug this file pins.
    (root / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    (root / "skipme.txt").write_text("needle\n", encoding="utf-8")


def _force_no_native_binary_env(rg_binary: Path) -> dict[str, str]:
    """Build a subprocess env that forces the Python-only channel deterministically:
    `TG_DISABLE_NATIVE_TG=1` short-circuits `resolve_native_tg_binary()` to `None`
    (`runtime_paths.py:282-283`) regardless of what happens to be on this machine, so this
    suite never needs to skip (or worse, silently exercise the wrong implementation) based on
    native-binary discoverability."""
    env = os.environ.copy()
    pythonpath_entries = [str(SRC_DIR)]
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        pythonpath_entries.extend(
            entry
            for entry in existing_pythonpath.split(os.pathsep)
            if entry and entry != str(SRC_DIR)
        )
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["TG_RG_PATH"] = str(rg_binary)
    env["TG_DISABLE_NATIVE_TG"] = "1"
    env.pop("TG_NATIVE_TG_BINARY", None)
    env.pop("TG_MCP_TG_BINARY", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _run_tg_search(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", "search", *args],
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
def non_git_root_gitignore_corpus(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "task-269-root-ignore"
    _write_non_git_root_gitignore_corpus(root)
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for #269 root-ignore-file outcome coverage")
    return root, _force_no_native_binary_env(rg_binary)


def test_plain_text_search_excludes_gitignored_file_via_python_bootstrap_passthrough(
    non_git_root_gitignore_corpus: tuple[Path, dict[str, str]],
) -> None:
    """Exercises `bootstrap.py::_run_rg_passthrough` (bootstrap.py:1088) directly: a bare
    `tg search` with no complicating flags stays on bootstrap's own fast path and never reaches
    `cli/main.py`'s full CLI."""
    root, env = non_git_root_gitignore_corpus

    result = _run_tg_search(["--no-heading", "needle", "."], cwd=root, env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr}"
    matched_files = _matched_files_from_plain_text(result.stdout)
    assert matched_files == {"a.txt"}, (
        "plain-text `tg search` (bootstrap._run_rg_passthrough) must exclude the gitignored "
        f"file in a non-git directory (task #269): got {matched_files}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr}"
    )


def test_json_search_excludes_gitignored_file_via_python_ripgrep_backend(
    non_git_root_gitignore_corpus: tuple[Path, dict[str, str]],
) -> None:
    """Exercises `RipgrepBackend._build_cmd` (via `cli/main.py`'s full CLI): a bare `--json`
    is one of bootstrap's `_TG_ONLY_SEARCH_FLAGS`, so it routes to the full CLI, where
    `RipgrepBackend.search()` (json_mode=True) is the engine actually invoked whenever no
    native binary is discoverable."""
    root, env = non_git_root_gitignore_corpus

    result = _run_tg_search(["--json", "needle", "."], cwd=root, env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "RipgrepBackend", payload
    json_files = {_normalize_path_str(str(p)) for p in payload["matched_file_paths"]}
    json_basenames = {Path(p).name for p in json_files}
    assert json_basenames == {"a.txt"}, (
        "`tg search --json` (RipgrepBackend._build_cmd) must exclude the gitignored file: "
        f"got {json_files}\nstdout={result.stdout!r}"
    )


def test_plain_text_and_json_agree_on_the_same_one_file_set(
    non_git_root_gitignore_corpus: tuple[Path, dict[str, str]],
) -> None:
    """The core #264/#269 claim ported to the Python-only channel: an output-format flag must
    never change WHICH FILES are searched. A test that only checks parity (not the absolute
    correct set) would have passed BEFORE this fix too, since both Python implementations
    shared the identical defect -- so this is deliberately a THIRD assertion alongside the two
    single-surface tests above, never a substitute for them."""
    root, env = non_git_root_gitignore_corpus

    plain = _run_tg_search(["--no-heading", "needle", "."], cwd=root, env=env)
    as_json = _run_tg_search(["--json", "needle", "."], cwd=root, env=env)

    assert plain.returncode == 0 and as_json.returncode == 0
    plain_files = _matched_files_from_plain_text(plain.stdout)
    payload = json.loads(as_json.stdout)
    json_files = {Path(_normalize_path_str(str(p))).name for p in payload["matched_file_paths"]}

    assert plain_files == json_files == {"a.txt"}


@pytest.mark.parametrize(
    "flag",
    ["--no-ignore", "--no-ignore-vcs", "--no-ignore-files"],
)
def test_ignore_disabling_flag_restores_the_gitignored_file_via_python_bootstrap_passthrough(
    non_git_root_gitignore_corpus: tuple[Path, dict[str, str]],
    flag: str,
) -> None:
    """Regression control for the fix's escape hatches, ported from PR #744's outcome test:
    each of `--no-ignore` / `--no-ignore-vcs` / `--no-ignore-files` must still search BOTH
    files (only `.gitignore` exists in this fixture, so `--no-ignore-vcs` and
    `--no-ignore-files` behave identically to `--no-ignore` here). Proves the shared
    `root_ignore_file_args` gating is load-bearing end-to-end through the real CLI, not just at
    the unit-test level -- without it, an explicit `--ignore-file` would silently resurrect the
    rule the user asked to disable (verified live against rg 15.1.0 in the #264 PR; reused, not
    re-derived, per this task's brief)."""
    root, env = non_git_root_gitignore_corpus

    result = _run_tg_search(["--no-heading", flag, "needle", "."], cwd=root, env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr}"
    matched_files = _matched_files_from_plain_text(result.stdout)
    assert matched_files == {"a.txt", "skipme.txt"}, (
        f"{flag} must restore the gitignored file: got {matched_files}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr}"
    )


def test_git_repo_cell_does_not_regress_via_python_bootstrap_passthrough(tmp_path: Path) -> None:
    """Inside a real git repo, real rg's own auto-discovery already honors the root
    `.gitignore` (require_git defaults true and is satisfied) -- this fix must not add a
    SECOND, redundant `--ignore-file` that changes behavior there. `--ignore-file` ranks below
    rg's auto-discovered rules, so redundant-not-harmful is the expected shape (reused from the
    #264 PR's live verification), but this test pins the OUTCOME regardless of mechanism."""
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for #269 root-ignore-file outcome coverage")

    root = tmp_path / "task-269-git-repo"
    _write_non_git_root_gitignore_corpus(root)
    init = subprocess.run(
        ["git", "init", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip(f"git init unavailable for the control cell: {init.stderr}")
    env = _force_no_native_binary_env(rg_binary)

    result = _run_tg_search(["--no-heading", "needle", "."], cwd=root, env=env)

    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr}"
    matched_files = _matched_files_from_plain_text(result.stdout)
    assert matched_files == {"a.txt"}, (
        f"git-repo cell must stay correct (no regression): got {matched_files}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr}"
    )
