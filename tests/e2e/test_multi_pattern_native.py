"""E2E coverage for the -e/-f multi-pattern combine on the CPU/native search path.

Bug (audit #69, a re-do of the never-merged PR #441): on the CPU/native (non-rg) search
path -- i.e. whenever `--cpu` (or another flag that routes away from ripgrep) is used --
`tg search -e foo -e bar` silently dropped every pattern but the first
(`regexp_patterns[0]`), and `tg search -f patterns.txt` never read the file at all (the
placeholder empty `pattern` string). The rg-ROUTED path was always correct (rg reads
`config.regexp`/`config.file_patterns` directly and builds its own `-e`/`--file` argv in
`ripgrep_backend.py`), so these tests force `--cpu` to exercise the CPU/native path.

Every test also sets `TG_DISABLE_NATIVE_TG=1`. This is deliberate, not a workaround: it
pins these tests to the in-process Python combine fix in `cli/main.py` regardless of
whether a separately-compiled native `tg` binary happens to be resolvable on the machine
running the suite (it is, in this dev environment -- see
`test_multi_pattern_e_f_do_not_delegate_to_native_binary` in `test_cli_bootstrap.py` for
the DIFFERENT, environment-independent unit test that the outer bootstrap.py fast path
correctly refuses to delegate `-e`/`-f` to that binary at all).

Windows golden-parity discipline: PR #441's actual Windows CI run
(github.com/oimiragieo/tensor-grep/actions/runs/28917558068) shows its own 18 new tests
ALL passing on windows-latest/py3.11 and py3.12 -- the ONLY failure in that run was
`test_output_golden_contract[json_multi_file-python-m]`, a pre-existing, unrelated
snapshot drift (a `submatches` JSON field added by commit fa5fc23 on 2026-07-03, well
before PR #441 existed, without updating that one fixture) reproduced independently on
today's tree BEFORE this change. PR #441 was closed on a misdiagnosis of that failure, not
an actual defect in its multi-pattern combine logic. `test_multi_pattern_golden_parity_*`
below is nonetheless a hard-coded, rg-binary-independent exact-match assertion (never
compares raw stdout byte-order, which is not guaranteed across platforms/filesystems) so a
real future Windows-specific drift in the combine logic itself would still be caught here
without depending on an installed `rg` binary at all.

Dogfoods the REAL shipped entry point via `python -m tensor_grep` (never `CliRunner`,
which bypasses the `bootstrap` front door).
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

pytestmark = pytest.mark.acceptance

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


def _helpers():
    from helpers import rg_parity

    return rg_parity


def _env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_entries = [str(SRC_DIR)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath_entries.extend(
            entry for entry in existing.split(os.pathsep) if entry and entry != str(SRC_DIR)
        )
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Pin these tests to the in-process Python combine fix -- see module docstring.
    env["TG_DISABLE_NATIVE_TG"] = "1"
    return env


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _tg(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, "-m", "tensor_grep", "search", *args], cwd=cwd, env=env)


def _normalize(text: str, root: Path) -> list[str]:
    normalized: list[str] = []
    for line in text.replace("\r\n", "\n").splitlines():
        if not line:
            continue
        current = line.replace(str(root), ".").replace(root.as_posix(), ".").replace("\\", "/")
        if current.startswith("./"):
            current = current[2:]
        normalized.append(current)
    return normalized


def _tg_json_matches(result: subprocess.CompletedProcess[str]) -> list[tuple[str, str]]:
    payload = json.loads(result.stdout)
    return sorted((match["file"], match["text"]) for match in payload["matches"])


def _tg_json_files(result: subprocess.CompletedProcess[str]) -> list[str]:
    payload = json.loads(result.stdout)
    return sorted({match["file"] for match in payload["matches"]})


@pytest.fixture()
def env() -> dict[str, str]:
    return _env()


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    # Pattern files (written by individual tests) go in `tmp_path`, one level ABOVE
    # `root`, so a `-f ../patterns.txt` never becomes a search candidate itself.
    root = tmp_path / "multi-pattern"
    root.mkdir()
    (root / "foo.txt").write_text("foo line\n", encoding="utf-8")
    (root / "bar.txt").write_text("bar line\n", encoding="utf-8")
    (root / "both.txt").write_text("foo and bar together\n", encoding="utf-8")
    (root / "none.txt").write_text("nothing relevant\n", encoding="utf-8")
    return root


# --- (a) -e foo -e bar: both patterns must match, not just the first. -------------------


def test_multi_e_native_matches_all_patterns_not_just_first(
    corpus: Path, env: dict[str, str]
) -> None:
    result = _tg(["--cpu", "--sort", "path", "-e", "foo", "-e", "bar", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = sorted({line.split(":", 1)[0] for line in _normalize(result.stdout, corpus)})
    assert matched_files == ["bar.txt", "both.txt", "foo.txt"]
    assert "none.txt" not in matched_files


def test_multi_e_native_reports_both_match_line_once(corpus: Path, env: dict[str, str]) -> None:
    # both.txt matches "foo" AND "bar" but must be reported as ONE line, never two
    # independent passes (rg parity: OR-combine, not N separate searches).
    result = _tg(["--cpu", "-e", "foo", "-e", "bar", "both.txt"], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_lines = _normalize(result.stdout, corpus)
    assert len(matched_lines) == 1
    assert matched_lines[0] == "foo and bar together"


# --- (b) -f patterns.txt: the file must actually be read, and only its patterns match. --


def test_pattern_file_native_reads_file_and_matches_any_pattern(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "patterns.txt").write_text("foo\nbar\n", encoding="utf-8")
    result = _tg(["--cpu", "--json", "-f", "../patterns.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = _tg_json_files(result)
    # Must NOT flood every file (the pre-fix bug: an unread `-f` collapsed to an empty
    # pattern string that matched every line in every file).
    assert matched_files == ["bar.txt", "both.txt", "foo.txt"]
    assert "none.txt" not in matched_files


def test_pattern_file_missing_exits_2(corpus: Path, env: dict[str, str]) -> None:
    result = _tg(["--cpu", "-f", "does_not_exist.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 2
    assert "does_not_exist.txt" in result.stderr


def test_pattern_file_missing_exits_2_json_envelope(corpus: Path, env: dict[str, str]) -> None:
    result = _tg(["--cpu", "--json", "-f", "does_not_exist.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "pattern_file_error"


def test_pattern_file_blank_line_matches_every_line_rg_parity(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    # Documented rg behavior, pinned deliberately (not a bug to "fix"): a genuinely blank
    # pattern-file line is an EMPTY pattern, which matches every line in every file.
    (tmp_path / "blank.txt").write_text("\n", encoding="utf-8")
    result = _tg(["--cpu", "--json", "-f", "../blank.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = _tg_json_files(result)
    assert matched_files == ["bar.txt", "both.txt", "foo.txt", "none.txt"]


# --- (c) golden-parity: deterministic CPU backend, exact hard-coded match set/counts --
# (no dependency on an installed `rg` binary at all -- Windows-safe per the module
# docstring's PR #441 postmortem).


def test_multi_pattern_golden_parity_deterministic_cpu_backend(
    corpus: Path, env: dict[str, str]
) -> None:
    result = _tg(["--cpu", "--json", "-e", "foo", "-e", "bar", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["total_files"] == 3
    assert payload["total_matches"] == 3
    assert _tg_json_matches(result) == [
        ("bar.txt", "bar line"),
        ("both.txt", "foo and bar together"),
        ("foo.txt", "foo line"),
    ]


def test_multi_pattern_golden_parity_pattern_file_deterministic_cpu_backend(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "patterns.txt").write_text("foo\nbar\n", encoding="utf-8")
    result = _tg(["--cpu", "--json", "-f", "../patterns.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["total_files"] == 3
    assert payload["total_matches"] == 3
    assert _tg_json_matches(result) == [
        ("bar.txt", "bar line"),
        ("both.txt", "foo and bar together"),
        ("foo.txt", "foo line"),
    ]


# --- many-fixed-pattern scale: rg-parity dedup must hold at a REALISTIC pattern count, ---
# not just N=2 (docs/gpu_crossover.md's documented ~2.3x-21x tg-vs-rg gap on 100-pattern ---
# multi-literal search was profiled to this exact `_combine_multi_patterns` call; this pins ---
# the CORRECTNESS side of that same code path at the SAME scale so a future performance fix ---
# cannot silently regress it). ------------------------------------------------------------


def test_many_fixed_patterns_dedupe_overlapping_lines_at_scale(
    env: dict[str, str], tmp_path: Path
) -> None:
    """Regression/characterization test for the many-fixed-string CPU search path
    (docs/gpu_crossover.md: 100 fixed patterns, rg=0.105s vs tg-CPU=2.220s, ~21x).

    The existing tests above pin the OR-combine dedup contract
    (`_combine_multi_patterns`, cli/main.py) at N=2 patterns
    (`test_multi_e_native_reports_both_match_line_once` /
    `test_multi_pattern_golden_parity_pattern_file_deterministic_cpu_backend`); this test
    pins the SAME rg-parity "reported once, never once per matching pattern" contract at
    a REALISTIC pattern count (100, matching the CEO benchmark's own scale) with TWO
    distinct overlap widths (one line hit by exactly 2 patterns, one line hit by exactly
    3), which a naive performance fix could regress if it swapped in the ALREADY-SHIPPED
    but currently-buggy native AhoCorasick multi-pattern fast path
    (`native_search.rs::run_native_fixed_multi_pattern_search` /
    `collect_fixed_multi_pattern_line_matches`) as-is instead of via this Python
    combine-into-one-alternation-regex path -- that native fast path emits ONE match
    record per (line, matching-pattern) pair rather than per line, over-counting
    `total_matches` whenever 2+ patterns hit the same line. Verified directly against the
    published `tg` binary: a 2-line/2-pattern-overlap fixture
    (`tg search -F --cpu --json -e A -e B overlap.txt`) reports `total_matches: 3`
    instead of the rg-correct `2`. This is exactly why `bootstrap.py`'s
    `_can_delegate_to_native_tg_search` and `cli/main.py`'s
    `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS` both deliberately refuse to delegate
    ANY `-e`/`-f` search to the native binary (see the audit #69 comments at both sites)
    -- this test's own combine-in-Python path is the CORRECT side of that trade-off; any
    future fix that reuses the native/AC engine (for speed) MUST fix its per-line dedup
    first, and this test must stay green throughout.
    """
    root = tmp_path / "many-pattern"
    root.mkdir()
    (root / "corpus.txt").write_text(
        "line A has NEEDLE_01 only\n"
        "line B has NEEDLE_02 only\n"
        "line C has NEEDLE_01 and NEEDLE_02 together\n"
        "line D has NEEDLE_03 and NEEDLE_04 and NEEDLE_05 together\n"
        "line E has no needles at all\n",
        encoding="utf-8",
    )

    real_needles = [f"NEEDLE_{i:02d}" for i in range(1, 6)]  # 5 patterns that DO match
    absent_patterns = [f"ABSENT_{i:03d}_XYZQ" for i in range(95)]  # padding to 100 total
    patterns = real_needles + absent_patterns
    assert len(patterns) == 100

    (tmp_path / "patterns.txt").write_text("\n".join(patterns) + "\n", encoding="utf-8")

    result = _tg(
        ["-F", "--cpu", "--json", "-f", "../patterns.txt", "."],
        cwd=root,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    # rg parity: 4 LINES matched (A, B, C, D) -- never one row per (line, pattern) pair.
    # Line C matches 2 of the 100 patterns; line D matches 3. Neither may be double- or
    # triple-counted, and none of the 95 absent patterns may contribute a phantom match.
    assert payload["total_matches"] == 4
    assert sorted(match["text"] for match in payload["matches"]) == [
        "line A has NEEDLE_01 only",
        "line B has NEEDLE_02 only",
        "line C has NEEDLE_01 and NEEDLE_02 together",
        "line D has NEEDLE_03 and NEEDLE_04 and NEEDLE_05 together",
    ]


# --- -F multi-literal: each -e is re.escape'd, never interpreted as regex. --------------


def test_fixed_strings_multi_e_native_literal_only(env: dict[str, str], tmp_path: Path) -> None:
    root = tmp_path / "literal"
    root.mkdir()
    (root / "dot.txt").write_text("a.b literal\n", encoding="utf-8")
    (root / "any.txt").write_text("axb should not match\n", encoding="utf-8")
    (root / "zz.txt").write_text("zz here\n", encoding="utf-8")
    result = _tg(["--cpu", "--sort", "path", "-F", "-e", "a.b", "-e", "zz", "."], cwd=root, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = sorted({line.split(":", 1)[0] for line in _normalize(result.stdout, root)})
    assert matched_files == ["dot.txt", "zz.txt"]
    assert "any.txt" not in matched_files


# --- leading (?i) inline flag stays SCOPED to its own branch, never leaking case- -------
# insensitivity across the whole alternation. -------------------------------------------


def test_multi_e_leading_inline_flag_is_scoped(env: dict[str, str], tmp_path: Path) -> None:
    root = tmp_path / "scoped-flag"
    root.mkdir()
    (root / "upper.txt").write_text("FOO shout\n", encoding="utf-8")
    (root / "lower.txt").write_text("bar quiet\n", encoding="utf-8")
    (root / "upper_bar.txt").write_text("BAR shout\n", encoding="utf-8")
    result = _tg(["--cpu", "--sort", "path", "-e", "(?i)foo", "-e", "bar", "."], cwd=root, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = sorted({line.split(":", 1)[0] for line in _normalize(result.stdout, root)})
    assert matched_files == ["lower.txt", "upper.txt"]
    # BAR must NOT match: (?i) must stay scoped to the foo branch, not leak globally.
    assert "upper_bar.txt" not in matched_files


# --- single -e / single -F must stay BYTE-IDENTICAL to the plain-positional form (no ----
# combine wrapping applied when there is only one total pattern). -----------------------


def test_single_e_byte_identical_to_positional(corpus: Path, env: dict[str, str]) -> None:
    via_flag = _tg(["--cpu", "--sort", "path", "-e", "foo", "."], cwd=corpus, env=env)
    via_positional = _tg(["--cpu", "--sort", "path", "foo", "."], cwd=corpus, env=env)
    assert via_flag.returncode == via_positional.returncode
    assert via_flag.stdout == via_positional.stdout


def test_single_fixed_strings_e_byte_identical_to_positional(
    corpus: Path, env: dict[str, str]
) -> None:
    via_flag = _tg(["--cpu", "--sort", "path", "-F", "-e", "foo", "."], cwd=corpus, env=env)
    via_positional = _tg(["--cpu", "--sort", "path", "-F", "foo", "."], cwd=corpus, env=env)
    assert via_flag.returncode == via_positional.returncode
    assert via_flag.stdout == via_positional.stdout


# --- a single -e alongside an -f file stays a DEAD-flag / single-pattern search (pinned --
# elsewhere too: test_search_single_regexp_with_unused_file_option_and_only_matching_still
# _works in test_cli_modes.py). Deliberately NOT a union -- broadening this boundary would
# regress that existing pinned test (confirmed by direct inspection, not assumption). -----


def test_single_e_with_unused_f_still_dead_flag_on_cpu_path(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "bar_only.txt").write_text("bar\n", encoding="utf-8")
    result = _tg(
        ["--cpu", "--json", "-e", "foo", "-f", "../bar_only.txt", "."],
        cwd=corpus,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    matched_files = _tg_json_files(result)
    # Only "foo" is searched; -f's "bar" pattern is a dead flag here (single -e wins).
    assert matched_files == ["both.txt", "foo.txt"]
    assert "bar.txt" not in matched_files


# --- without --cpu, -e/-f still route through the untouched rg-passthrough path. --------


def test_multi_e_without_cpu_matches_rg_when_available(corpus: Path, env: dict[str, str]) -> None:
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for rg-passthrough parity coverage")
    rg_env = dict(env)
    rg_env["TG_RG_PATH"] = str(rg_binary)
    rg_result = _run(
        [str(rg_binary), "-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=rg_env
    )
    tg_result = _tg(["-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=rg_env)
    assert tg_result.returncode == rg_result.returncode
    assert _normalize(tg_result.stdout, corpus) == _normalize(rg_result.stdout, corpus)


def test_pattern_file_without_cpu_matches_rg_when_available(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for rg-passthrough parity coverage")
    (tmp_path / "patterns2.txt").write_text("foo\nbar\n", encoding="utf-8")
    rg_env = dict(env)
    rg_env["TG_RG_PATH"] = str(rg_binary)
    rg_result = _run(
        [str(rg_binary), "-f", "../patterns2.txt", "--sort", "path", "."], cwd=corpus, env=rg_env
    )
    tg_result = _tg(["-f", "../patterns2.txt", "--sort", "path", "."], cwd=corpus, env=rg_env)
    assert tg_result.returncode == rg_result.returncode
    assert _normalize(tg_result.stdout, corpus) == _normalize(rg_result.stdout, corpus)


# --- -o/-r combined with multi-pattern remain rejected (unchanged pre-existing guard). ---


def test_multi_e_with_only_matching_still_rejected_exit_2(
    corpus: Path, env: dict[str, str]
) -> None:
    result = _tg(["--cpu", "-o", "-e", "foo", "-e", "bar", "both.txt"], cwd=corpus, env=env)
    assert result.returncode == 2
    assert "-o/--only-matching" in result.stderr


def test_pattern_file_with_rank_still_rejected_exit_2(
    corpus: Path, env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "patterns.txt").write_text("foo\nbar\n", encoding="utf-8")
    result = _tg(["--cpu", "--rank", "-f", "../patterns.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 2
    assert "--rank/--bm25" in result.stderr
