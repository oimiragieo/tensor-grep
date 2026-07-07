"""E2E coverage for the -e/-f multi-pattern native-path fix.

Bug: on the NATIVE (non-rg) search path -- i.e. whenever `--cpu` (or another flag that
routes away from ripgrep) is used -- `tg search -e foo -e bar` silently dropped every
pattern but the first, and `tg search -f patterns.txt` never read the file at all (an
empty pattern that matches EVERY line -- a silent flood). The rg-routed path was always
correct (rg reads `config.regexp`/`config.file_patterns` directly and builds its own
`-e`/`--file` argv), so these tests force `--cpu` to exercise the native path, plus a
handful of parity checks confirming the rg-routed path is untouched.

Note: `-f`/`--file` assertions use `--json` rather than the default text format. The
default TEXT formatter (`ripgrep_fmt.py`) has a pre-existing (unrelated to this fix, and
out of scope here -- this fix touches `main.py` only) quirk where it suppresses the
`file:` prefix whenever `config.file_patterns` is set, even across multiple matched
files -- diverging from real rg's own `-f` filename behavior. `--json` always includes an
explicit `file` field per match, so it is unaffected and gives an unambiguous assertion
surface for these tests regardless of that formatter quirk.

Dogfoods the REAL shipped binary via `python -m tensor_grep` (never `CliRunner`, which
bypasses the `bootstrap` front door).

KNOWN GAP (found while writing this suite, deliberately NOT fixed here -- out of the
main.py-only scope of this change): `cli/bootstrap.py`'s OUTER fast-path launcher
(`_can_delegate_to_native_tg_search`, argv-string-based, runs BEFORE Typer parses
anything) delegates `--cpu`/`--json`/`--ndjson`/`--gpu-device-ids` searches straight to a
separately-compiled native `tg` binary when one is resolvable (e.g. a managed install's
`tg.exe`/`tg`), and does NOT exclude `-e`/`--regexp`/`-f`/`--file` from that delegation
-- unlike `cli/main.py`'s OWN inner native-delegation gate, which already refuses ANY
`-e`/`-f` usage via `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`. The native binary's
own multi-pattern implementation has different, independent bugs (verified via direct
invocation): `-f` is silently never read at all (an even more severe flood than the
pre-fix Python bug), and multiple `-e` patterns are not deduplicated when a single line
matches more than one (`both.txt` reported/counted twice for `-e foo -e bar`). On any
machine where that native binary resolves, `tg search --cpu -e foo -e bar`/`-f ...` still
exhibits a version of the original bug through the default front door, because
`bootstrap.py` never reaches this PR's fix in `main.py`. This suite sets
`TG_DISABLE_NATIVE_TG=1` so it verifies the code this PR actually touches; the
bootstrap.py-level gap should be closed as an immediate follow-up (mirror
`main.py`'s exclusion of `-e`/`-f` from native delegation in
`_can_delegate_to_native_tg_search`, `cli/bootstrap.py`).
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

pytestmark = pytest.mark.acceptance


def _helpers():
    from helpers import rg_parity

    return rg_parity


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


def _rg(
    rg_binary: Path, args: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return _run([str(rg_binary), *args], cwd=cwd, env=env)


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


def _tg_json_files(result: subprocess.CompletedProcess[str]) -> list[str]:
    payload = json.loads(result.stdout)
    return sorted(match["file"] for match in payload["matches"])


@pytest.fixture()
def env_and_rg(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    rg_parity = _helpers()
    rg_binary = rg_parity.resolve_pinned_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not available for multi-pattern parity coverage")
    env = rg_parity.build_command_env(rg_binary)
    # This suite verifies the PYTHON native-path fix in `cli/main.py`. A separately
    # compiled native `tg` binary (if one is resolvable on this machine -- e.g.
    # `.venv/Scripts/tg.exe` from a managed install) has its OWN, independent multi-
    # pattern implementation with different bugs (an `-f` pattern file is silently never
    # read at all -- an even more severe flood than the pre-fix Python bug -- and
    # multiple `-e` patterns are not deduplicated when a single line matches more than
    # one). `cli/bootstrap.py`'s OUTER fast-path launcher delegates straight to that
    # native binary for `--cpu`/`--json` searches WITHOUT excluding `-e`/`-f`, bypassing
    # this fix entirely -- see the module docstring "KNOWN GAP" note. Disable that
    # delegation here so this suite exercises (and only exercises) the code this PR
    # actually touches.
    env["TG_DISABLE_NATIVE_TG"] = "1"
    return rg_binary, env


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    # Pattern files (written by individual tests) go in `tmp_path`, one level ABOVE
    # `root`, so a `-f ../patterns.txt` never becomes a search candidate itself and
    # contaminate the expected match set.
    root = tmp_path / "multi-pattern"
    root.mkdir()
    (root / "foo.txt").write_text("foo line\n", encoding="utf-8")
    (root / "bar.txt").write_text("bar line\n", encoding="utf-8")
    (root / "both.txt").write_text("foo and bar together\n", encoding="utf-8")
    (root / "none.txt").write_text("nothing relevant\n", encoding="utf-8")
    return root


# --- (1) -e foo -e bar: both patterns must match, and a line matching both is reported
# once (never two independent passes). ------------------------------------------------


def test_multi_e_native_matches_all_patterns(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    result = _tg(["--cpu", "--sort", "path", "-e", "foo", "-e", "bar", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched = _normalize(result.stdout, corpus)
    matched_files = sorted({line.split(":", 1)[0] for line in matched})
    assert matched_files == ["bar.txt", "both.txt", "foo.txt"]
    # both.txt matches "foo" AND "bar" but must be reported as ONE line, not two.
    both_lines = [line for line in matched if line.startswith("both.txt:")]
    assert len(both_lines) == 1


def test_multi_e_native_matches_rg_passthrough_output(corpus: Path, env_and_rg) -> None:
    rg_binary, env = env_and_rg
    rg_result = _rg(
        rg_binary, ["-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=env
    )
    tg_result = _tg(["--cpu", "-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=env)
    assert tg_result.returncode == rg_result.returncode
    assert _normalize(tg_result.stdout, corpus) == _normalize(rg_result.stdout, corpus)


# --- (2)/(3) -f pattern file: both patterns applied (no flood), and a genuinely blank
# line in the pattern file is an EMPTY pattern that matches every line (rg parity, pinned
# deliberately -- this is documented rg behavior, not a bug to "fix"). -----------------


def test_pattern_file_native_matches_all_patterns_no_flood(
    corpus: Path, env_and_rg, tmp_path: Path
) -> None:
    _rg_binary, env = env_and_rg
    (tmp_path / "patterns.txt").write_text("foo\nbar\n", encoding="utf-8")
    result = _tg(["--cpu", "--json", "-f", "../patterns.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = sorted(set(_tg_json_files(result)))
    # Must NOT flood every file (the pre-fix bug: an unread `-f` collapsed to an empty
    # pattern -> every line matched).
    assert matched_files == ["bar.txt", "both.txt", "foo.txt"]
    assert "none.txt" not in matched_files


def test_pattern_file_blank_line_matches_every_line_rg_parity(
    corpus: Path, env_and_rg, tmp_path: Path
) -> None:
    rg_binary, env = env_and_rg
    (tmp_path / "blank.txt").write_text("\n", encoding="utf-8")
    rg_result = _rg(rg_binary, ["-f", "../blank.txt", "--sort", "path", "."], cwd=corpus, env=env)
    tg_result = _tg(["--cpu", "--json", "-f", "../blank.txt", "."], cwd=corpus, env=env)
    assert tg_result.returncode == 0
    tg_files = sorted(set(_tg_json_files(tg_result)))
    rg_files = sorted({line.split(":", 1)[0] for line in _normalize(rg_result.stdout, corpus)})
    # Every text file matches an empty pattern -- same file set rg itself reports.
    assert tg_files == rg_files
    assert "none.txt" in tg_files


def test_fixed_strings_single_line_pattern_file_still_correct(
    corpus: Path, env_and_rg, tmp_path: Path
) -> None:
    """`-F` + a `-f` file that happens to hold exactly one line must still search that
    line as a LITERAL and produce the correct match set -- even though Pipeline's
    backend-routing decision treats any `-F` + `-f` combo pessimistically (clears
    `fixed_strings` for routing before the file's line count is known, see
    `_resolve_native_search_pattern`'s call site) and so never takes the single-literal
    StringZilla fast path for it. That is a documented PERF-only tradeoff -- this pins
    that it is not also a correctness one."""
    _rg_binary, env = env_and_rg
    (tmp_path / "single.txt").write_text("foo\n", encoding="utf-8")
    result = _tg(["--cpu", "--json", "-F", "-f", "../single.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 0, result.stderr
    matched_files = sorted(set(_tg_json_files(result)))
    assert matched_files == ["both.txt", "foo.txt"]


def test_pattern_file_missing_exits_2(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    result = _tg(["--cpu", "-f", "does_not_exist.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 2
    assert "does_not_exist.txt" in result.stderr


def test_pattern_file_missing_exits_2_json_envelope(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    result = _tg(["--cpu", "--json", "-f", "does_not_exist.txt", "."], cwd=corpus, env=env)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "pattern_file_error"


# --- (5) -F multi-literal: each -e is re.escape'd, never interpreted as regex. --------


def test_fixed_strings_multi_e_native_literal_only(env_and_rg, tmp_path: Path) -> None:
    _rg_binary, env = env_and_rg
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


# --- (7) -e 'a|b' -e c combined with --line-regexp: each -e is its own non-capturing
# group in the alternation, so ^(?:(?:a|b)|(?:c))$ never lets "ac"/"ab" slip through via
# a mis-scoped anchor. -------------------------------------------------------------------


def test_multi_e_per_branch_grouping_line_regexp(env_and_rg, tmp_path: Path) -> None:
    _rg_binary, env = env_and_rg
    root = tmp_path / "grouping"
    root.mkdir()
    for name in ("a", "b", "c", "ac", "ab"):
        (root / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    result = _tg(
        ["--cpu", "--sort", "path", "--line-regexp", "-e", "a|b", "-e", "c", "."],
        cwd=root,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    matched_files = sorted({line.split(":", 1)[0] for line in _normalize(result.stdout, root)})
    assert matched_files == ["a.txt", "b.txt", "c.txt"]
    assert "ac.txt" not in matched_files
    assert "ab.txt" not in matched_files


# --- (8) leading (?i) inline flag is rewritten to a SCOPED flag group so it only applies
# to its own branch, never leaking case-insensitivity across the whole alternation. -----


def test_multi_e_leading_inline_flag_is_scoped(env_and_rg, tmp_path: Path) -> None:
    _rg_binary, env = env_and_rg
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


# --- (9) mixed -e + -f is a union: today the file is silently ignored when -e is also
# present; both must contribute. ---------------------------------------------------------


def test_mixed_e_and_f_is_union(corpus: Path, env_and_rg, tmp_path: Path) -> None:
    _rg_binary, env = env_and_rg
    (tmp_path / "bar_only.txt").write_text("bar\n", encoding="utf-8")
    result = _tg(
        ["--cpu", "--json", "-e", "foo", "-f", "../bar_only.txt", "."],
        cwd=corpus,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    matched_files = sorted(set(_tg_json_files(result)))
    assert matched_files == ["bar.txt", "both.txt", "foo.txt"]
    assert "none.txt" not in matched_files


# --- (10) -o/-r (only-matching / replace) exercise the two remaining native-path call
# sites (_only_matching_lines / _replace_lines) with a combined multi-pattern. A single
# EXPLICIT file target (not a directory) never gets a `file:` prefix, so these compare
# bare line content. --------------------------------------------------------------------


def test_multi_e_only_matching_native(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    result = _tg(
        ["--cpu", "-o", "-e", "foo", "-e", "bar", "both.txt"],
        cwd=corpus,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert _normalize(result.stdout, corpus) == ["foo", "bar"]


def test_multi_e_replace_native(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    result = _tg(
        ["--cpu", "--replace", "HIT", "-e", "foo", "-e", "bar", "both.txt"],
        cwd=corpus,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert _normalize(result.stdout, corpus) == ["HIT and HIT together"]


# --- (11) single -e / single -F must stay BYTE-IDENTICAL to the plain-positional form
# (no combine wrapping applied when there is only one total pattern). ------------------


def test_single_e_byte_identical_to_positional(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    via_flag = _tg(["--cpu", "--sort", "path", "-e", "foo", "."], cwd=corpus, env=env)
    via_positional = _tg(["--cpu", "--sort", "path", "foo", "."], cwd=corpus, env=env)
    assert via_flag.returncode == via_positional.returncode
    assert via_flag.stdout == via_positional.stdout


def test_single_fixed_strings_e_byte_identical_to_positional(corpus: Path, env_and_rg) -> None:
    _rg_binary, env = env_and_rg
    via_flag = _tg(["--cpu", "--sort", "path", "-F", "-e", "foo", "."], cwd=corpus, env=env)
    via_positional = _tg(["--cpu", "--sort", "path", "-F", "foo", "."], cwd=corpus, env=env)
    assert via_flag.returncode == via_positional.returncode
    assert via_flag.stdout == via_positional.stdout


def test_single_pattern_file_line_matches_equivalent_positional_search(
    corpus: Path, env_and_rg, tmp_path: Path
) -> None:
    """A single-line -f file must search exactly like the equivalent positional pattern
    (same matched files/text) -- it hits the len(all_patterns) == 1 no-combine branch, so
    the underlying regex match is byte-identical to `-e foo`/positional `foo`. The
    rendered TEXT differs only because of the pre-existing `file_patterns`-suppresses-
    filename formatter quirk noted in the module docstring (out of scope here), so this
    compares --json match content rather than raw stdout bytes.
    """
    _rg_binary, env = env_and_rg
    (tmp_path / "single.txt").write_text("foo\n", encoding="utf-8")
    via_file = _tg(["--cpu", "--json", "-f", "../single.txt", "."], cwd=corpus, env=env)
    via_positional = _tg(["--cpu", "--json", "foo", "."], cwd=corpus, env=env)
    assert via_file.returncode == via_positional.returncode == 0
    file_payload = json.loads(via_file.stdout)
    positional_payload = json.loads(via_positional.stdout)
    to_pairs = lambda payload: sorted(  # noqa: E731
        (match["file"], match["text"]) for match in payload["matches"]
    )
    assert to_pairs(file_payload) == to_pairs(positional_payload)


# --- (12) without --cpu, -e/-f still route through rg (unchanged), for both -e-only and
# -f-only shapes. -------------------------------------------------------------------------


def test_multi_e_without_cpu_matches_rg(corpus: Path, env_and_rg) -> None:
    rg_binary, env = env_and_rg
    rg_result = _rg(
        rg_binary, ["-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=env
    )
    tg_result = _tg(["-e", "foo", "-e", "bar", "--sort", "path", "."], cwd=corpus, env=env)
    assert tg_result.returncode == rg_result.returncode
    assert _normalize(tg_result.stdout, corpus) == _normalize(rg_result.stdout, corpus)


def test_pattern_file_without_cpu_matches_rg(corpus: Path, env_and_rg, tmp_path: Path) -> None:
    rg_binary, env = env_and_rg
    (tmp_path / "patterns2.txt").write_text("foo\nbar\n", encoding="utf-8")
    rg_result = _rg(
        rg_binary, ["-f", "../patterns2.txt", "--sort", "path", "."], cwd=corpus, env=env
    )
    tg_result = _tg(["-f", "../patterns2.txt", "--sort", "path", "."], cwd=corpus, env=env)
    assert tg_result.returncode == rg_result.returncode
    assert _normalize(tg_result.stdout, corpus) == _normalize(rg_result.stdout, corpus)
