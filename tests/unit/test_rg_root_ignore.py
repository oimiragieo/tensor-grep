"""Task #269: unit tests for the shared Python `root_ignore_file_args` helper.

Mirrors `rust_core/src/rg_passthrough.rs`'s own `root_ignore_file_args` unit tests (task #264 /
PR #744) so the Rust and Python implementations of the same root-only ignore-file discovery
cannot silently diverge. This module is called by both `bootstrap.py::_run_rg_passthrough` and
`backends/ripgrep_backend.py::RipgrepBackend._build_cmd` -- see `tests/unit/test_cli_bootstrap.py`
and `tests/unit/test_ripgrep_backend.py` for the call-site-level coverage; this file pins the
shared helper's own contract in isolation.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.rg_root_ignore import root_ignore_file_args


def _values(operands: list[str]) -> list[str]:
    return [operands[i + 1] for i, tok in enumerate(operands) if tok == "--ignore-file"]


def test_emits_ignore_file_for_root_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands.count("--ignore-file") == 1
    assert _values(operands) == [str(tmp_path / ".gitignore")]


def test_empty_when_no_ignore_files_present(tmp_path: Path) -> None:
    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == []


def test_defaults_root_to_dot_when_roots_empty(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    operands = root_ignore_file_args(
        [],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == ["--ignore-file", ".gitignore"]


def test_defaults_root_to_dot_when_roots_none(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    operands = root_ignore_file_args(
        None,
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == ["--ignore-file", ".gitignore"]


def test_no_ignore_suppresses_all_three(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=True,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == []


def test_no_ignore_files_suppresses_all_three(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=True,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == []


def test_no_ignore_vcs_skips_only_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=True,
        no_ignore_dot=False,
    )

    values = _values(operands)
    assert not any(v.endswith(".gitignore") for v in values), values
    assert any(v.endswith(".ignore") for v in values), values


def test_no_ignore_dot_skips_ignore_and_rgignore_not_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("skipme.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=True,
    )

    values = _values(operands)
    assert any(v.endswith(".gitignore") for v in values), values
    assert not any(v.endswith(".ignore") and not v.endswith(".rgignore") for v in values), values
    assert not any(v.endswith(".rgignore") for v in values), values


def test_covers_every_explicit_root(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")
    (root_b / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(root_a), str(root_b)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands.count("--ignore-file") == 2


def test_no_ignore_files_wins_over_explicit_root_even_with_files_present(tmp_path: Path) -> None:
    """rg's own docs: --no-ignore-files cancels any --ignore-file "regardless of argv order,
    even ones that come after this flag" -- short-circuit before any filesystem probing."""
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=True,
        no_ignore_vcs=True,
        no_ignore_dot=True,
    )

    assert operands == []


# --- Task #269 independent-gate finding: rg's `-u`/`-uu`/`-uuu` alias for `--no-ignore` -------
# Confirmed BLOCKING on the first pass of this task: `-u` is rg's documented alias for
# `--no-ignore` (`-uu` additionally implies `--hidden`, `-uuu` additionally implies `--binary`),
# but neither call site observed it, so the emitted `--ignore-file` (which survives
# `--no-ignore` by design -- see the module docstring) silently resurrected the ignored files
# under `-u`, making `-u` STRICTER than passing no flag at all.


def test_unrestricted_suppresses_injection_even_when_no_ignore_flags_are_false(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
        unrestricted=1,
    )

    assert operands == []


def test_unrestricted_zero_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
        unrestricted=0,
    )

    assert operands == ["--ignore-file", str(tmp_path / ".gitignore")]


def test_unrestricted_default_parameter_does_not_change_existing_callers(tmp_path: Path) -> None:
    """Callers written before `unrestricted` existed (i.e. every pre-existing call in this
    file) must keep working unchanged -- the parameter must default to the no-op value."""
    (tmp_path / ".gitignore").write_text("skipme.txt\n", encoding="utf-8")

    operands = root_ignore_file_args(
        [str(tmp_path)],
        no_ignore=False,
        no_ignore_files=False,
        no_ignore_vcs=False,
        no_ignore_dot=False,
    )

    assert operands == ["--ignore-file", str(tmp_path / ".gitignore")]
