"""EXECUTED reference prototype for plan 2026-10-03-bughunt-wave2a Part F (F.1, F.2, F.3) plus the
round-39 findings. Every case the plan's tables and RED lists name is here, and every claim about
what rg does is checked against the installed ``rg`` (skipped when absent), not asserted from prose.

Layout: rg fixture/helpers; F.1 (sentinel); F.2 (scans stop at ``--``, explicit-path, quiet note);
F.3 (cluster-aware presence, regex pre-validation, census, raw-argv rule); round 39 (a)(b); parser
differential against rg.
"""

from __future__ import annotations

import ast
import io
import random
import re
import shutil
import string
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap
from tensor_grep.cli import bootstrap_native_argv as nav


class _LazyGuards:
    """``tensor_grep.cli.bootstrap_search_guards`` imported on first USE, not at collection.

    The module does not exist on main. A module-scope import would turn the whole file into one
    collection error there; this way behavioural tests (which go through bootstrap.py /
    bootstrap_native_argv.py entry points that exist on main) collect and fail on ASSERTIONS, and
    only tests of the new helpers fail with ModuleNotFoundError. Dunder lookups stay
    AttributeError so pytest's id generation never triggers the import."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        import importlib

        return getattr(importlib.import_module("tensor_grep.cli.bootstrap_search_guards"), name)


g = _LazyGuards()
_CLI_DIR = Path(bootstrap.__file__).parent


def _cli_source(module_file: str) -> str:
    return (_CLI_DIR / module_file).read_text(encoding="utf-8")


_MODULE_FILES = ["bootstrap.py", "bootstrap_native_argv.py", "bootstrap_search_guards.py"]

RG = shutil.which("rg")
needs_rg = pytest.mark.skipif(RG is None, reason="rg not installed")
_SCOPE_NOTE_MARKER = "no PATH was given"

# --------------------------------------------------------------------------------------------
# rg fixture + helpers
# --------------------------------------------------------------------------------------------

_FILES = {
    "src/a.txt": "foo\n-kq\n--\n-h\n-uuu\n=foo\n(\nzzz\n",
    "src/b.py": "foo\nneedle\n",
    "src/deep/x/y.txt": "foo\n",
    "(/c.txt": "foo\n",
    "pats.txt": "foo\n",
    "a.txt": "foo\n(\n",
    ".hid.txt": "foo\n",
    ".ignore": "ign/\n",
    "ign/ignored.txt": "foo\n",
}


@pytest.fixture(scope="module")
def rgdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("rgfix")
    for rel, text in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def rg_run(rgdir: Path, argv: list[str], stdin: str = "") -> tuple[int, list[str], str]:
    """(exit code, sorted stdout lines with / separators, first stderr line)."""
    assert RG is not None
    proc = subprocess.run(
        [RG, *argv], cwd=rgdir, capture_output=True, text=True, input=stdin, timeout=60, check=False
    )
    lines = sorted(proc.stdout.replace("\\", "/").splitlines())
    err = (proc.stderr.strip().splitlines() or [""])[0]
    return proc.returncode, lines, err


def _flat(lines: list[str]) -> str:
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# F.1  no `--` before rg flag clusters
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "-C2", "FOO", "a.txt"],
        ["--cpu", "-m1", "foo", "a.txt"],
        ["--json", "-in", "foo", "a"],
        ["--json", "-g*.py", "FOO", "."],
        ["--json", "--no-heading", "FOO", "a"],
        ["--json", "--max-count=1", "foo", "a"],
        ["--json", "-efoo", "a"],
    ],
)
def test_rg_flags_after_tg_flag_get_no_sentinel(argv):
    assert "--" not in nav.bootstrap_native_tg_search_argv(argv)


@pytest.mark.parametrize("argv", [["--json", "-ig*.py", "FOO", "."], ["--json", "-iefoo", "a"]])
def test_combined_flags_with_attached_values_get_no_sentinel(argv):
    assert "--" not in nav.bootstrap_native_tg_search_argv(argv)


def test_dash_led_non_flag_pattern_after_tg_flag_still_gets_sentinel():  # positive control
    assert nav.bootstrap_native_tg_search_argv(["--cpu", "-kq", "src"]) == [
        "--cpu",
        "--",
        "-kq",
        "src",
    ]
    assert nav.bootstrap_native_tg_search_argv(["--json", "-i", "-r"]) == [
        "--json",
        "--",
        "-i",
        "-r",
    ]


@pytest.mark.parametrize(
    "token",
    [
        "--pre=calc.exe",
        "--pre",
        "--pre-glob=*",
        "--hostname-bin=calc.exe",
        "--hostname-bin",
        "--search-zip",
    ],
)
def test_exec_capable_flag_in_pattern_slot_keeps_sentinel(token):
    # CWE-88 guard. Bare 2-char `-z` is NOT listed: the existing `len > 2` gate never sentinels a
    # 2-char token (pre-existing, unchanged).
    out = nav.bootstrap_native_tg_search_argv(["--json", token, "src"])
    assert out[: out.index(token)][-1] == "--"


def test_pretty_is_a_flag_not_caught_by_the_exec_guard():
    assert "--" not in nav.bootstrap_native_tg_search_argv(["--json", "--pretty", "foo", "src"])


def test_unknown_long_option_in_pattern_slot_keeps_sentinel():
    out = nav.bootstrap_native_tg_search_argv(["--json", "--not-a-real-rg-option", "src"])
    assert out[: out.index("--not-a-real-rg-option")][-1] == "--"


def test_declared_long_flag_table_matches_the_search_command():
    import typer.main

    from tensor_grep.cli.main import app

    search = next(c for c in typer.main.get_command(app).commands.values() if c.name == "search")
    declared = {
        o[2:] for p in search.params for o in p.opts + p.secondary_opts if o.startswith("--")
    }
    assert nav._TG_DECLARED_LONG_FLAG_NAMES == frozenset(declared)
    assert not (nav._RG_ONLY_LONG_FLAG_NAMES & nav._TG_DECLARED_LONG_FLAG_NAMES)


@needs_rg
def test_rg_only_long_flag_table_is_real_rg_flags():
    help_text = subprocess.run(
        [RG, "--help"], capture_output=True, text=True, timeout=30, check=False
    ).stdout
    rg_long = set(re.findall(r"--([a-z][a-z0-9-]+)", help_text))
    assert nav._RG_ONLY_LONG_FLAG_NAMES <= rg_long
    assert "no-heading" in nav._RG_ONLY_LONG_FLAG_NAMES  # positive control: the motivating case
    # Completeness the other way: every rg long flag is either declared, rg-only or exec-capable.
    # A newer rg that adds a flag fails HERE, which is the prompt to regenerate the table.
    known = nav._TG_DECLARED_LONG_FLAG_NAMES | nav._RG_ONLY_LONG_FLAG_NAMES
    assert rg_long - known - nav._RG_EXEC_LONG_FLAG_NAMES == set()


def test_rg_only_long_flag_after_tg_flag_gets_no_sentinel():
    assert "--" not in nav.bootstrap_native_tg_search_argv(["--json", "--no-heading", "FOO", "a"])


@needs_rg
def test_short_flag_tables_match_real_rg_letter_by_letter(rgdir):
    """The plan says 'implementer verifies against rg --help'. Executed: probe EVERY letter.
    `rg -X` alone distinguishes the classes: 'missing value' (value-taking), 'requires at least one
    pattern' (no-value), exit 0 (help/version) and 'unrecognized flag'."""
    no_value, value, info, numeric = set(), set(), set(), set()
    for ch in string.ascii_letters + string.digits + ".":
        rc, _, err = rg_run(rgdir, [f"-{ch}"])
        if "missing value" in err:
            value.add(ch)
            _, _, err2 = rg_run(rgdir, [f"-{ch}", "zzzqqq"])
            if "not a valid number" in err2:
                numeric.add(ch)
        elif "requires at least one pattern" in err:
            no_value.add(ch)
        elif rc == 0:
            info.add(ch)
    assert value == {f[1] for f in bootstrap._SEARCH_ATTACHED_VALUE_SHORT_FLAGS}
    assert numeric == set(nav._RG_NUMERIC_VALUE_SHORT)
    # `z` is no-value in rg but DELIBERATELY absent from the pattern-slot table (exec-capable).
    assert no_value | info == set(nav._RG_NO_VALUE_SHORT) | {"z"}
    assert info == {"h", "V"}


@needs_rg
@pytest.mark.parametrize(
    "token", ["-C2", "-m1", "-in", "-ig*.py", "-iefoo", "-m=1", "-d=1", "-g=*.py", "-e=foo"]
)
def test_plausible_flag_tokens_are_flags_in_real_rg(rgdir, token):
    """Every token the table calls 'plausible' is read by rg as flag(s): rg does NOT take it as a
    pattern, so exit != the pattern-search outcome. Compared to the same token behind `--`."""
    assert nav._is_plausible_rg_flag_token(token)
    flagged = rg_run(rgdir, [token, "foo", "src"])
    as_pattern = rg_run(rgdir, ["--", token, "foo", "src"])
    assert flagged != as_pattern


@needs_rg
@pytest.mark.parametrize("token", ["-kq", "-pk", "-m1x", "-mx", "-A-1"])
def test_non_flag_tokens_are_not_plausible_and_rg_agrees(rgdir, token):
    assert not nav._is_plausible_rg_flag_token(token)
    rc, _, _ = rg_run(rgdir, [token, "foo", "src"])
    assert rc == 2  # rg itself rejects it as a flag cluster (unrecognized flag / bad number)


def test_equals_attached_numeric_value_is_plausible():
    # council gap, found by probing: rg drops one leading `=` on attached short values, so
    # `-m=1` is a flag. The plan's `rest.isdigit()` made it a PATTERN and inserted `--`.
    assert "--" not in nav.bootstrap_native_tg_search_argv(["--json", "-m=1", "foo", "a"])


@needs_rg
@pytest.mark.parametrize(
    "argv",
    [
        ["-l", "-C2", "foo", "src"],
        ["-l", "-in", "foo", "src"],
        ["-l", "-g*.py", "foo", "src"],
        ["-l", "-efoo", "src"],
        ["-l", "--no-heading", "foo", "src"],
        ["-l", "--max-count=1", "foo", "src"],
    ],
)
def test_f1_builder_output_runs_identically_to_the_users_argv_in_rg(rgdir, argv):
    assert rg_run(rgdir, nav.bootstrap_native_tg_search_argv(argv)) == rg_run(rgdir, argv)


@needs_rg
def test_f1_sentinel_makes_a_dash_led_token_a_pattern_in_rg(rgdir):
    boot = nav.bootstrap_native_tg_search_argv(["-l", "-kq", "src"])
    assert boot == ["-l", "--", "-kq", "src"]
    rc, lines, _ = rg_run(rgdir, boot)
    assert rc == 0 and any(line.endswith("a.txt") for line in lines)  # `-kq` is a line in a.txt


def test_tg_only_value_flag_owns_its_value_in_the_pattern_slot():
    # found while running (a): `-g -kq` is a glob; the old loop skipped only `-g` and sentinelled
    # the VALUE, producing `-g -- -kq src` (glob `--`).
    assert nav.bootstrap_native_tg_search_argv(["--json", "-g", "-kq", "src"]) == [
        "--json",
        "-g",
        "-kq",
        "src",
    ]
    assert nav.bootstrap_native_tg_search_argv(["--json", "--glob", "-kq", "FOO", "src"]) == [
        "--json",
        "--glob",
        "-kq",
        "FOO",
        "src",
    ]


# --------------------------------------------------------------------------------------------
# F.2  scans stop at `--`; explicit-path predicate; quiet note
# --------------------------------------------------------------------------------------------


def test_scan_bound_ignores_tokens_after_sentinel():
    f = bootstrap._search_args_include_generated_scan_bound
    assert f(["--", "-dfoo"], paths_defaulted=True) is False
    assert f(["-d1", "--", "x"], paths_defaulted=True) is True  # control
    assert f(["-dfoo"], paths_defaulted=True) is True  # control


@pytest.mark.parametrize(
    ("args", "expect_unrestricted"),
    [
        (["-e", "--", "-uv", "src"], True),
        (["-g", "--", "--no-ignore", "x"], False),
        (["--replace", "--", "-uuu", "s"], True),
    ],
)
def test_double_dash_as_a_flag_value_does_not_end_options(args, expect_unrestricted):
    assert bootstrap._search_args_request_unrestricted(args) is expect_unrestricted
    assert bootstrap._search_args_request_unrestricted_generated_scan(args) is True


def test_flag_value_is_never_read_as_a_scan_bound():
    f = bootstrap._search_args_include_generated_scan_bound
    assert f(["-e", "-d1", "-uuu"], paths_defaulted=True) is False
    assert bootstrap._search_args_request_unrestricted(["-e", "-d1", "-uuu"]) is True
    assert f(["-d1", "-uuu", "foo"], paths_defaulted=True) is True  # genuinely bounded control


def test_generated_root_refusal_sees_unrestricted_after_a_value_flag():
    gen = bootstrap._search_args_request_unrestricted_generated_scan
    assert gen(["-e", "--", "-uuu", "src"]) is True
    assert gen(["-g", "*.py", "-uuu", "src"]) is True
    assert bootstrap._search_args_include_generated_scan_bound(["-d1", "foo"], paths_defaulted=True)


def test_unrestricted_ignores_tokens_after_sentinel():
    assert bootstrap._search_args_request_unrestricted(["--", "-uv", "src"]) is False
    assert bootstrap._search_args_request_unrestricted(["-uv", "src"]) is True  # control
    gen = bootstrap._search_args_request_unrestricted_generated_scan
    assert gen(["--", "--no-ignore", "x"]) is False
    assert gen(["--no-ignore", "x"]) is True  # control


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-f", "pats.txt", "a.txt"], True),
        (["--regexp=A", "src"], True),
        (["-e", "A", "-e", "B"], False),
        (["-e", "A", "src"], True),
        (["PAT"], False),
        (["PAT", "."], True),
    ],
)
def test_explicit_path_with_pattern_source_flags(args, expected):
    assert bootstrap._search_args_include_explicit_path(args) is expected


def _drive(monkeypatch, tmp_path, argv, *, native=False, rc=1, capsys=None):
    """Run main_entry on ``tg search <argv>`` with every executor stubbed. Returns
    (route, args_seen, stderr). route is rg | native | full."""
    seen: dict[str, object] = {"route": "none"}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["tg", "search", *argv])
    monkeypatch.delenv("TG_REEXEC_GUARD", raising=False)
    monkeypatch.delenv("TG_FORCE_CPU", raising=False)
    monkeypatch.setattr(
        bootstrap, "resolve_native_tg_binary", lambda: "native-bin" if native else None
    )
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: "rg")

    def rg_stub(binary, args):
        seen.update(route="rg", args=list(args))
        return rc

    def native_stub(binary, args):
        seen.update(route="native", args=list(args))
        return rc

    def full_stub():
        seen.update(route="full")

    monkeypatch.setattr(bootstrap, "_run_rg_passthrough", rg_stub)
    monkeypatch.setattr(bootstrap, "_run_native_tg_search", native_stub)
    monkeypatch.setattr(bootstrap, "_run_full_cli", full_stub)
    try:
        bootstrap.main_entry()
    except SystemExit as exc:
        seen["code"] = exc.code
    err = capsys.readouterr().err if capsys is not None else ""
    return seen["route"], seen.get("args"), err


def _drive_rg_route(monkeypatch, tmp_path, capsys, argv) -> str:
    route, _, err = _drive(monkeypatch, tmp_path, argv, capsys=capsys)
    assert route == "rg", route
    return err


def test_defaulted_scope_note_written_without_quiet(monkeypatch, tmp_path, capsys):  # control
    assert _SCOPE_NOTE_MARKER in _drive_rg_route(monkeypatch, tmp_path, capsys, ["foo"])


def test_explicit_stdin_path_gets_no_defaulted_scope_note(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert _SCOPE_NOTE_MARKER not in _drive_rg_route(monkeypatch, tmp_path, capsys, ["foo", "-"])


def test_dash_as_pattern_value_is_not_a_stdin_path():
    assert g._names_stdin_path(["-e", "-"]) is False
    assert g._names_stdin_path(["foo", "-"]) is True
    assert g._names_stdin_path(["-"]) is False  # `-` is the PATTERN here
    assert g._names_stdin_path(["--", "-"]) is False  # pattern after the sentinel
    assert g._names_stdin_path(["--", "foo", "-"]) is True
    assert g._names_stdin_path(["-e", "foo", "-"]) is True  # -e supplied the pattern; `-` is a path


@pytest.mark.parametrize("argv", [["-"], ["--", "-"]])
def test_dash_pattern_still_gets_the_defaulted_scope_note(monkeypatch, tmp_path, capsys, argv):
    assert _SCOPE_NOTE_MARKER in _drive_rg_route(monkeypatch, tmp_path, capsys, argv)


def test_no_messages_does_not_suppress_the_scope_note(monkeypatch, tmp_path, capsys):
    assert _SCOPE_NOTE_MARKER in _drive_rg_route(
        monkeypatch, tmp_path, capsys, ["--no-messages", "foo"]
    )


@pytest.mark.parametrize("flag", ["-q", "--quiet", "-iq"])
def test_defaulted_scope_note_suppressed_by_quiet(monkeypatch, tmp_path, capsys, flag):
    assert _SCOPE_NOTE_MARKER not in _drive_rg_route(monkeypatch, tmp_path, capsys, [flag, "foo"])


def test_native_route_also_honours_quiet_and_stdin(monkeypatch, tmp_path, capsys):
    # the SECOND note site in main_entry (native delegation) uses the same predicate
    route, _, err = _drive(
        monkeypatch, tmp_path, ["--json", "-q", "foo"], native=True, capsys=capsys
    )
    assert route == "native" and _SCOPE_NOTE_MARKER not in err
    route, _, err = _drive(monkeypatch, tmp_path, ["--json", "foo"], native=True, capsys=capsys)
    assert route == "native" and _SCOPE_NOTE_MARKER in err
    route, _, err = _drive(
        monkeypatch, tmp_path, ["--json", "foo", "-"], native=True, capsys=capsys
    )
    assert route == "native" and _SCOPE_NOTE_MARKER not in err


def test_quiet_helper_ignores_flag_values_and_sentinel():
    assert g.request_quiet(["-e", "-q"]) is False
    assert g.request_quiet(["-ie", "-q"]) is False  # cluster ending in a value flag
    assert g.request_quiet(["-qe", "PAT"]) is True
    assert g.request_quiet(["foo", "--", "-q"]) is False
    assert g.request_quiet(["-g", "*.py", "-q", "foo"]) is True


@pytest.mark.parametrize(
    ("args", "expect_unrestricted"),
    [(["-ie", "--", "-uuu", "src"], True), (["-ig", "--", "--no-ignore", "x"], False)],
)
def test_cluster_ending_in_value_flag_does_not_end_options(args, expect_unrestricted):
    assert bootstrap._search_args_request_unrestricted(args) is expect_unrestricted
    assert bootstrap._search_args_request_unrestricted_generated_scan(args) is True


def test_attached_value_cluster_does_not_consume_next():  # control: `-iefoo` carries its value
    assert bootstrap._search_args_request_unrestricted(["-iefoo", "--", "-uuu", "src"]) is False


@needs_rg
@pytest.mark.parametrize(
    ("argv", "quiet"),
    [
        (["-q", "foo", "a.txt"], True),
        (["--quiet", "foo", "a.txt"], True),
        (["-iq", "foo", "a.txt"], True),
        (["-qe", "foo", "a.txt"], True),
        (["-ie", "-q", "a.txt"], False),  # `-q` is the PATTERN
        (["foo", "--", "-q"], False),
        (["--no-messages", "foo", "a.txt"], False),
        (["-g", "*.txt", "-q", "foo", "a.txt"], True),
    ],
)
def test_quiet_matches_real_rg(rgdir, argv, quiet):
    """Oracle: with a matching pattern, rg prints nothing and exits 0 iff quiet was in effect."""
    rc, lines, _ = rg_run(rgdir, argv)
    silent_success = rc == 0 and lines == []
    assert g.request_quiet(argv) is quiet
    if quiet:
        assert silent_success
    else:
        assert not silent_success


@needs_rg
def test_rg_has_no_no_quiet_and_no_messages_is_not_quiet(rgdir):
    # why _QUIET_NAMES has no negation entry and no `--no-messages`
    assert "unrecognized flag --no-quiet" in rg_run(rgdir, ["--no-quiet", "foo", "a.txt"])[2]
    rc, lines, _ = rg_run(rgdir, ["--no-messages", "foo", "a.txt"])
    assert rc == 0 and lines  # still prints matches


# --------------------------------------------------------------------------------------------
# F.3  regex pre-validation
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        r"\p{Lu}\w+",
        r"\x{61}bc",
        r"(?-i)xyz",
        r"(?<n>a)\k<n>",
        r"\pL",
        r"[[:digit:]]+",
        r"[[:alpha:](]",
    ],
)
def test_rg_valid_regex_is_not_rejected(pattern):
    assert bootstrap._search_args_include_obviously_invalid_regex([pattern, "."]) is False


@pytest.mark.parametrize("pattern", ["(a", "a)", "*a", "a|*", "a{3,1}"])
def test_regex_both_engines_reject_still_rejected(pattern):  # positive control
    assert bootstrap._search_args_include_obviously_invalid_regex([pattern, "."]) is True


@needs_rg
@pytest.mark.parametrize("pattern", ["(a", "a)", "*a", "a|*", "a{3,1}"])
def test_rg_really_rejects_the_pre_rejected_patterns(rgdir, pattern):
    # Executed claim: the pre-reject list is a SUBSET of what rg rejects (never a rg-valid regex).
    rc, _, err = rg_run(rgdir, ["-e", pattern, "a.txt"])
    assert rc == 2 and "regex parse error" in err


@needs_rg
@pytest.mark.parametrize(
    "pattern",
    [
        r"\p{Lu}\w+",
        r"\x{61}bc",
        r"(?-i)xyz",
        r"(?<n>a)",
        r"\pL",
        r"[[:digit:]]+",
        r"[[:alpha:](]",
        "^*",
    ],
)
def test_rg_accepts_the_patterns_python_rejects_or_warns_on(rgdir, pattern):
    rc, _, err = rg_run(rgdir, ["-e", pattern, "a.txt"])
    assert rc in (0, 1), err


@needs_rg
def test_plan_list_item_named_group_backref_is_not_valid_in_rgs_default_engine(rgdir):
    # PLAN ERROR found by executing: `(?<n>a)\k<n>` is listed as an "rg-valid regex", but rg's
    # default (Rust) engine has no backreferences, so it is valid only under PCRE2. Not rejecting
    # it is still right (Python also rejects the `(?<n>` syntax, so the 'both engines reject' rule
    # never fires), but the plan's justification is wrong for the default engine.
    pattern = r"(?<n>a)\k<n>"
    assert rg_run(rgdir, ["-e", pattern, "a.txt"])[0] == 2
    assert rg_run(rgdir, ["-P", "-e", pattern, "a.txt"])[0] in (0, 1)


def test_posix_class_emits_no_python_warning():
    re.purge()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bootstrap._search_args_include_obviously_invalid_regex(["[[:digit:]]+", "."])
    assert [w for w in caught if issubclass(w.category, FutureWarning)] == []


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="'Possible nested set' probe verified on 3.12+"
)
def test_warning_probe_can_see_the_warning():  # positive control for the test above
    re.purge()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        re.compile("[[:digit:]]+")
    assert any(issubclass(w.category, FutureWarning) for w in caught)


def test_caret_star_is_not_rejected_but_leading_star_is():
    assert g.pattern_invalid_in_both_engines("^*") is False
    assert g.pattern_invalid_in_both_engines("*a") is True


# ---- PCRE2 / engine selection (plan F.3 paragraph on r6/r7) ----


@pytest.mark.parametrize(
    "args",
    [
        ["--engine=pcre2", "(*UTF)x", "."],
        ["--engine", "pcre2", "(*UTF)x", "."],
        ["-P", "(*UTF)x", "."],
        ["-iP", "(*UTF)x", "."],
        ["--auto-hybrid-regex", "(*UTF)x", "."],
        ["--engine=auto", "(*UTF)x", "."],
        ["--pcre2", "(*UTF)x", "."],
    ],
)
def test_pcre2_selecting_args_skip_pre_validation(args):
    assert bootstrap._search_args_include_obviously_invalid_regex(args) is False


@pytest.mark.parametrize(
    "args",
    [
        ["(*UTF)x", "."],
        ["-e", "-P", "-e", "(*UTF)x", "."],
        ["(*UTF)x", "--", "-P"],
        ["-P", "--no-pcre2", "(*UTF)x", "."],  # last wins: back to the default engine
        ["-P", "--engine=default", "(*UTF)x", "."],
        ["--auto-hybrid-regex", "--no-auto-hybrid-regex", "(*UTF)x", "."],
    ],
)
def test_non_pcre2_args_are_still_pre_validated(args):
    assert bootstrap._search_args_include_obviously_invalid_regex(args) is True


@needs_rg
@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["-P"],
        ["-iP"],
        ["--pcre2"],
        ["--engine=pcre2"],
        ["--engine", "pcre2"],
        ["--engine=auto"],
        ["--auto-hybrid-regex"],
        ["-P", "--no-pcre2"],
        ["--no-pcre2", "-P"],
        ["-P", "--engine=default"],
        ["--engine=default", "-P"],
        ["--auto-hybrid-regex", "--no-auto-hybrid-regex"],
        ["--no-auto-hybrid-regex", "--auto-hybrid-regex"],
        ["-e", "-P"],
    ],
)
def test_engine_state_matches_real_rg(rgdir, flags):
    """Oracle: the look-behind `(?<=a)b` is valid ONLY under PCRE2 (`auto` falls back to it)."""
    argv = [*flags, "(?<=a)b", "a.txt"] if flags[:1] != ["-e"] else ["-e", "-P", "a.txt"]
    if flags[:1] == ["-e"]:
        # `-e -P` makes `-P` the PATTERN: valid in the default engine, so not PCRE2
        assert g.engine_selects_pcre2(argv) is False
        return
    rc, _, _ = rg_run(rgdir, argv)
    assert g.engine_selects_pcre2(argv) is (rc != 2)


# ---- pattern extraction (r34 table) ----


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["-efoo", "("], ["foo"]),
        (["-iefoo", "("], ["foo"]),
        (["-ffile.txt", "("], []),
        (["-f", "file.txt", "("], []),
        (["-e", "foo", "("], ["foo"]),  # control
        (["--regexp=foo", "("], ["foo"]),  # control
        (["foo", "("], ["foo"]),  # control
        (["--file=x", "("], []),
        (["--file", "x", "("], []),
        (["-e", "a", "-e", "b", "("], ["a", "b"]),
        (["--regexp", "a", "-eb", "--regexp=c"], ["a", "b", "c"]),
        (["--", "-(x", "src"], ["-(x"]),
        (["-e", "foo", "--", "-(x"], ["foo"]),  # after the sentinel a pattern source was given
        (["-e", "--", "x"], ["--"]),  # `--` is -e's VALUE; `x` is a path
        (["-ie", "--", "x"], ["--"]),
        (["-e=foo", "("], ["foo"]),  # rg drops one leading `=` on an attached short value
        (["-ie=foo", "("], ["foo"]),
        (["-e==foo", "("], ["=foo"]),
        (["--regexp==foo", "("], ["=foo"]),  # long form keeps everything after the first `=`
        (
            ["-g", "-e", "foo", "("],
            ["foo"],
        ),  # `-e` is -g's VALUE; first positional `foo` is the pattern
    ],
)
def test_regex_patterns_table(argv, expected):
    assert bootstrap._regex_patterns_from_search_args(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["-efoo", "("],
        ["-iefoo", "("],
        ["-ffile.txt", "("],
        ["-f", "file.txt", "("],
        ["-e", "foo", "("],
        ["--regexp=foo", "("],
        ["foo", "("],
        ["-e=foo", "("],
    ],
)
def test_regex_looking_path_is_not_pre_rejected(argv):
    assert bootstrap._search_args_include_obviously_invalid_regex(argv) is False


@pytest.mark.parametrize(
    "argv", [["-e", "(", "src"], ["-e(", "src"], ["-e=(", "src"], ["--regexp=(", "."]]
)
def test_genuinely_invalid_inline_pattern_is_pre_rejected(argv):
    assert bootstrap._search_args_include_obviously_invalid_regex(argv) is True


def test_ordinary_path_control():
    assert bootstrap._search_args_include_obviously_invalid_regex(["foo", "src"]) is False


@needs_rg
@pytest.mark.parametrize(
    "argv",
    [
        ["-efoo", "("],
        ["-iefoo", "("],
        ["-e=foo", "("],
        ["-ffile.txt", "("],
        ["-f", "pats.txt", "("],
        ["-e", "foo", "("],
        ["--regexp=foo", "("],
        ["-e", "--", "src"],
    ],
)
def test_pattern_extraction_agrees_with_what_rg_searches(rgdir, argv):
    """rg is the oracle: running rg on the argv equals running it with the extracted patterns as
    explicit `-e` flags and every remaining positional as a path (canonical form), proving the
    extraction (including `-e=foo` and `-e --`) reads the argv the way rg does."""
    canon = _canonical(argv)
    assert rg_run(rgdir, argv) == rg_run(rgdir, canon)


def test_dispatch_spy_value_flag_with_regexlike_path(monkeypatch, tmp_path, capsys):
    # `tg search -e foo "("` in a directory that contains a directory named `(` must reach rg.
    (tmp_path / "(").mkdir()
    route, args, _ = _drive(monkeypatch, tmp_path, ["-e", "foo", "("], capsys=capsys)
    assert route == "rg" and args == ["-e", "foo", "("]


@pytest.mark.parametrize(
    "argv",
    [["-iF", "(", "."], ["-Fi", "(", "."], ["-F", "(", "."], ["--fixed-strings", "(", "."]],
)
def test_dispatch_spy_cluster_fixed_strings_reaches_its_backend(
    monkeypatch, tmp_path, capsys, argv
):
    route, args, _ = _drive(monkeypatch, tmp_path, argv, capsys=capsys)
    assert route == "rg" and args == argv  # not diverted to the full CLI as an 'invalid regex'


def test_dispatch_spy_invalid_regex_without_f_goes_to_the_full_cli(monkeypatch, tmp_path, capsys):
    route, _, _ = _drive(monkeypatch, tmp_path, ["(", "."], capsys=capsys)
    assert route == "full"  # control: the pre-reject still diverts a real invalid regex


# ---- r35: every boolean flag check is cluster-aware ----


def test_cluster_fixed_strings_not_pre_rejected():
    for argv in (["-iF", "(", "."], ["-Fi", "(", "."]):
        assert bootstrap._search_args_include_obviously_invalid_regex(argv) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["(", "."]) is True  # control


def test_cluster_pcre2_skips_validation():
    assert bootstrap._search_args_include_obviously_invalid_regex(["-iP", "(*UTF)x", "."]) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["-i", "(*UTF)x", "."]) is True


def test_hidden_and_scan_bound_found_inside_one_cluster():
    assert bootstrap._search_args_include_generated_scan_bound(
        ["-.d1", "foo"], paths_defaulted=True
    )
    assert g.flag_present(["-.d1", "foo"], bootstrap._SEARCH_HIDDEN_FLAGS)


def test_cluster_glob_counts_as_scan_bound_only_with_an_explicit_path():
    f = bootstrap._search_args_include_generated_scan_bound
    assert f(["-ig", "*.py", "foo", "src"], paths_defaulted=False) is True
    assert f(["-ig", "*.py", "foo"], paths_defaulted=True) is False  # path-conditional bound


def test_value_letter_in_cluster_is_a_value_not_a_flag():
    # `-eF`: F is the PATTERN. A naive expand-every-letter predicate would enable literal mode.
    assert g.fixed_strings_requested(["-eF", "."]) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["-e(", "-eF", "."]) is True


def test_fixed_strings_flag_after_sentinel_does_not_count():
    assert g.fixed_strings_requested(["(", "--", "-F"]) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["(", "--", "-F"]) is True


def test_fixed_strings_last_wins_against_its_negation():
    assert (
        bootstrap._search_args_include_obviously_invalid_regex([
            "-F",
            "--no-fixed-strings",
            "(",
            ".",
        ])
        is True
    )
    assert (
        bootstrap._search_args_include_obviously_invalid_regex([
            "--no-fixed-strings",
            "-F",
            "(",
            ".",
        ])
        is False
    )


@needs_rg
@pytest.mark.parametrize(
    "flags",
    [
        ["-F"],
        ["-iF"],
        ["-Fi"],
        ["--fixed-strings"],
        ["-F", "--no-fixed-strings"],
        ["--no-fixed-strings", "-F"],
        ["-eF"],
        [],
    ],
)
def test_fixed_strings_matches_real_rg(rgdir, flags):
    """Oracle: pattern `(` is an error in a regex and a literal under fixed-strings."""
    argv = [*flags, "(", "a.txt"] if flags[:1] != ["-eF"] else ["-eF", "(", "a.txt"]
    if flags[:1] == ["-eF"]:
        assert g.fixed_strings_requested(argv) is False
        return
    rc, _, _ = rg_run(rgdir, argv)
    assert g.fixed_strings_requested(argv) is (rc != 2)


@needs_rg
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["-."], True),
        (["--hidden"], True),
        (["-i."], True),
        (["--hidden", "--no-hidden"], False),
        (["--no-hidden", "--hidden"], True),
        (["-e", "-."], False),
        ([], False),
    ],
)
def test_hidden_matches_real_rg(rgdir, argv, expected):
    """Oracle: rg lists the dot-file only with hidden enabled. The pattern is added by position
    so `-e -.` leaves `-.` as the pattern (no hidden)."""
    full = [*argv, "foo", "."] if argv[:1] != ["-e"] else ["-e", "-.", "."]
    _, lines, _ = rg_run(rgdir, full)
    sees_hidden = ".hid.txt" in _flat(lines)
    assert g.flag_present(full, bootstrap._SEARCH_HIDDEN_FLAGS) is expected
    if argv[:1] != ["-e"]:
        assert sees_hidden is expected


@needs_rg
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["-u"], True),
        (["-uuu"], True),
        (["--unrestricted"], True),
        (["-iu"], True),
        (["--no-ignore"], True),
        ([], False),
        (["-tu"], False),  # `-t u`: type u
    ],
)
def test_unrestricted_and_no_ignore_match_real_rg(rgdir, argv, expected):
    """Oracle: ign/ignored.txt is hidden by `.ignore` and shown by -u/--no-ignore. `-tu` is a
    type filter named `u` (an error in rg), not unrestricted."""
    rc, lines, err = rg_run(rgdir, [*argv, "foo", "."])
    full = [*argv, "foo", "."]
    gen = bootstrap._search_args_request_unrestricted_generated_scan(full)
    assert gen is expected
    if argv != ["-tu"]:
        assert ("ign/ignored.txt" in _flat(lines)) is expected
    else:
        assert rc == 2 and "unrecognized file type" in err


@needs_rg
@pytest.mark.parametrize(
    ("argv", "bounded"),
    [
        (["-d1"], True),
        (["-id1"], True),
        (["--max-depth=1"], True),
        (["--max-depth", "1"], True),
        (["--maxdepth", "1"], True),
        (["-d", "1"], True),
        (["-e", "foo", "-d", "1"], True),
        ([], False),
    ],
)
def test_depth_bound_matches_real_rg(rgdir, argv, bounded):
    """Oracle: src/deep/x/y.txt is 3 levels below `src`; -d1 hides it."""
    full = ["-l", *argv, "foo", "src"] if argv[:1] != ["-e"] else ["-l", *argv, "src"]
    _, lines, _ = rg_run(rgdir, full)
    reaches_deep = "deep/x/y.txt" in _flat(lines)
    assert (
        bootstrap._search_args_include_generated_scan_bound(full, paths_defaulted=False) is bounded
    )
    assert reaches_deep is (not bounded)


# ---- r36: raw-argv rule behaviours ----


def test_raw_argv_rule_scan_bound_survives_a_preceding_value_flag():
    f = bootstrap._search_args_include_generated_scan_bound
    assert f(["-g", "*.py", "-d", "2", "foo"], paths_defaulted=True) is True
    assert f(["-e", "needle", "-id1", "-uuu"], paths_defaulted=True) is True
    assert f(["-e", "-id1", "-uuu"], paths_defaulted=True) is False  # `-id1` IS -e's value


def test_raw_argv_rule_generated_scan_sees_hidden_after_value_flag():
    gen = bootstrap._search_args_request_unrestricted_generated_scan
    assert gen(["--files", "-g", "x", "-.", "p"]) is True
    assert gen(["-g", "x", "--no-ignore", "p"]) is True
    assert gen(["--files", "-g", "x", "--hidden"]) is True
    assert gen(["-g", "x", "-.", "p"]) is False  # `-.` alone without --files is not unrestricted


def test_raw_argv_rule_pins_from_r19_r20_stay_green():
    f = bootstrap._search_args_include_generated_scan_bound
    assert f(["-d1", "-uuu", "foo"], paths_defaulted=True) is True
    assert f(["-e", "-d1", "-uuu"], paths_defaulted=True) is False
    assert bootstrap._search_args_request_unrestricted(["-e", "-d1", "-uuu"]) is True


# ---- r37 / r38: presence (P), sentinel (S), value (V) classification ----


def test_explicit_json_requested_is_position_aware():
    assert bootstrap._explicit_json_requested(["--", "--json"]) is False
    assert bootstrap._explicit_json_requested(["-e", "--json"]) is False
    assert bootstrap._explicit_json_requested(["--json", "x"]) is True


def test_allow_broad_generated_scan_as_pattern_or_value_does_not_disable_the_refusal(monkeypatch):
    monkeypatch.setattr(bootstrap, "_search_paths_include_workspace_root", lambda paths: True)
    f = bootstrap._search_args_include_unbounded_broad_scan
    assert f(["--json", "--", "--allow-broad-generated-scan"]) is True  # pattern after `--`
    assert f(["-e", "--allow-broad-generated-scan"]) is True  # -e's value
    assert f(["--allow-broad-generated-scan", "foo"]) is False  # the genuine opt-out
    assert f(["-d1", "foo"]) is False  # a genuine depth bound stays effective
    assert f(["foo"]) is True  # control: the patched root IS refused


def test_cpu_used_as_a_pattern_does_not_force_cpu_and_force_cpu_inserts_before_real_sentinel(
    monkeypatch,
):
    monkeypatch.setenv(
        "TG_FORCE_CPU", "1"
    )  # explicit, so the early return cannot make this vacuous
    eff = bootstrap._effective_native_tg_search_args
    assert eff(["x"]) == ["x", "--cpu"]  # absent sentinel
    assert eff(["--", "x"]) == ["--cpu", "--", "x"]  # genuine sentinel
    assert eff(["-e", "--", "x"]) == ["-e", "--", "x", "--cpu"]  # value-consumed `--` is not it
    assert eff(["-ie", "--", "x"]) == ["-ie", "--", "x", "--cpu"]
    assert eff(["-e", "--cpu", "x"]) == [
        "-e",
        "--cpu",
        "x",
        "--cpu",
    ]  # `--cpu` pattern forces nothing
    assert eff(["--cpu", "x"]) == ["--cpu", "x"]  # control: a genuine --cpu is left alone


def test_can_delegate_trigger_must_be_in_option_position():
    d = bootstrap._can_delegate_to_native_tg_search
    assert d(["--cpu", "pat"]) is True  # control: genuine trigger
    assert d(["-g", "--cpu", "pat"]) is False  # `--cpu` is -g's value: no trigger
    assert d(["--", "--cpu", "pat"]) is False
    assert d(["--cpu", "--", "-e"]) is True  # `-e` after the sentinel is a pattern, not -e
    assert d(["--cpu", "-e", "foo"]) is False  # control: genuine -e is unsupported
    assert d(["--json", "-efoo", "src"]) is False
    assert d(["--json", "-ie", "foo", "src"]) is False  # cluster: was delegated on main
    assert d(["--json", "--format=rg", "x"]) is False
    assert d(["--gpu-device-ids=0", "x"]) is True
    assert d(["--gpu-device-ids", "0", "x"]) is True


def test_pattern_source_pre_pass_is_value_and_sentinel_aware():
    s = bootstrap._search_args_contains_pattern_source_flag
    assert s(["-e", "--"]) is True
    assert s(["--", "-e"]) is False
    assert s(["-g", "-e", "x"]) is False
    assert s(["-ir", "-e", "x"]) is False
    assert s(["-ie", "x"]) is True
    assert s(["-e=x"]) is True


def test_json_incompatible_render_flag_scan_is_cluster_value_and_sentinel_aware():
    b = bootstrap._json_aggregate_blocks_passthrough
    assert b(["--json", "-b"]) is True
    assert b(["--json", "-nb"]) is True  # cluster
    assert b(["--json", "-e", "-b"]) is False  # `-b` is the pattern
    assert b(["--json", "--", "-b"]) is False
    assert b(["--json", "-e", "--", "-b"]) is True  # `--` was -e's value, `-b` is a real flag
    assert b(["-e", "--json", "-b"]) is False  # `--json` is a pattern here
    assert b(["--json", "foo"]) is False


# --- (S) sentinel in the native builder ---


def test_sentinel_still_inserted_when_no_real_sentinel_exists():
    assert nav.bootstrap_native_tg_search_argv(["--json", "-kq", "src"]) == [
        "--json",
        "--",
        "-kq",
        "src",
    ]


def test_genuine_sentinel_keeps_exactly_one():
    out = nav.bootstrap_native_tg_search_argv(["--json", "--", "pat", "src"])
    assert out == ["--json", "--", "pat", "src"] and out.count("--") == 1


def test_has_end_of_options_is_a_boolean_never_none():
    # r38: `end_of_options_index` returns len(args) when absent, so an `is not None` test is
    # ALWAYS true and would have skipped every insertion.
    assert g.end_of_options_index(["a", "b"]) == 2
    assert g.has_end_of_options(["a", "b"]) is False
    assert g.has_end_of_options(["a", "--", "b"]) is True
    assert g.has_end_of_options(["-e", "--"]) is False
    assert g.has_end_of_options(["-ie", "--", "x"]) is False
    assert g.has_end_of_options(["-e", "--", "--"]) is True


# --------------------------------------------------------------------------------------------
# Round 39
# --------------------------------------------------------------------------------------------


@needs_rg
def test_r39a_e_dashdash_is_not_rewritten_and_rg_reads_kq_as_an_option(rgdir):
    raw = ["-e", "--", "-kq", "src"]
    assert nav.bootstrap_native_tg_search_argv(raw) == raw  # unchanged
    rc, _, err = rg_run(rgdir, raw)
    assert rc == 2 and "unrecognized flag -k" in err  # `-kq` IS an option cluster here
    # the plan's "gets a sentinel before -kq" would have produced this instead:
    planned = ["-e", "--", "--", "-kq", "src"]
    rc2, _, err2 = rg_run(rgdir, planned)
    assert "unrecognized flag" not in err2  # -kq silently became a PATH
    assert (rc2, err2) != (rc, err)


@needs_rg
def test_r39a_with_a_real_sentinel_kq_is_a_path(rgdir):
    raw = ["-e", "foo", "--", "-kq", "src"]
    assert nav.bootstrap_native_tg_search_argv(raw) == raw
    rc, _, err = rg_run(rgdir, raw)
    assert rc == 2 and "-kq" in err and "unrecognized" not in err  # a missing PATH, not an option


@pytest.mark.parametrize(
    "argv",
    [
        ["-e", "--"],
        ["-e", "--", "-kq", "src"],
        ["--json", "-e", "--", "-kq", "src"],
        ["--json", "-e", "-foo", "src"],
        ["--json", "-e", "--"],
        ["-ie", "--", "src"],
        ["--json", "-ie", "--", "-kq"],
        ["--json", "-f", "pats.txt", "-kq"],
        ["--json", "--regexp", "--", "-kq"],
        ["--json", "--regexp=--", "-kq"],
        ["--json", "-efoo", "-kq"],
    ],
)
def test_r39a_pattern_source_means_no_dash_led_pattern_slot(argv):
    assert nav.bootstrap_native_tg_search_argv(argv) == argv


def test_r39a_bare_lone_dash_led_token_still_gets_the_sentinel():
    # control: removing the slot must be conditioned on a pattern SOURCE, not on dash-led tokens
    assert nav.bootstrap_native_tg_search_argv(["--json", "-kq"]) == ["--json", "--", "-kq"]
    assert nav.bootstrap_native_tg_search_argv(["-kq"]) == ["--", "-kq"]


def test_r39a_is_a_regression_vs_main_for_nothing():
    # on main `"--" in search_args` already returned `-e -- -kq src` unchanged; `-e --` alone was
    # rewritten to `-- -e --` ONLY once the sentinel test became value-aware. Pin the end state.
    assert nav._sentinel_insertion_index(["-e", "--"]) is None
    assert nav._sentinel_insertion_index(["-e", "--", "-kq", "src"]) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["-h"],
        ["--help"],
        ["foo", "-h"],
        ["-e", "foo", "--help"],
        ["-ih"],
        ["--show-completion"],
        ["--install-completion", "x"],
    ],
)
def test_r39b_genuine_help_and_completion_select_the_full_cli(argv):
    assert bootstrap._requires_full_cli(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["-e", "-h"],
        ["--", "-h"],
        ["-e", "--help"],
        ["-ie", "-h", "src"],
        ["-e", "--show-completion", "src"],
        ["--", "--install-completion"],
        ["foo", "--", "-h"],
        ["-f", "-h"],
        ["-A", "-h", "foo"],
    ],
)
def test_r39b_help_as_a_pattern_or_value_does_not_select_the_full_cli(argv):
    assert bootstrap._requires_full_cli(argv) is False


@needs_rg
def test_r39b_rg_agrees_help_in_value_or_pattern_position_is_a_search(rgdir):
    rc, lines, _ = rg_run(rgdir, ["-e", "-h", "src/a.txt"])
    assert rc == 0 and lines == ["-h"]
    rc, lines, _ = rg_run(rgdir, ["--", "-h", "src/a.txt"])
    assert rc == 0 and lines == ["-h"]
    rc, lines, _ = rg_run(rgdir, ["-ih", "foo"])
    assert rc == 0 and "ripgrep 15" in _flat(lines)  # `-ih` IS help (cluster)
    rc, lines, _ = rg_run(rgdir, ["-e", "foo", "--help"])
    assert rc == 0 and "ripgrep 15" in _flat(lines)


def test_r39b_tg_only_flag_detection_is_unchanged_and_ignore_json_variant():
    assert bootstrap._requires_full_cli(["--rank", "foo"]) is True
    assert bootstrap._requires_full_cli(["--json", "foo"]) is True
    assert bootstrap._requires_full_cli_ignoring_rg_json(["--json", "foo"]) is False
    assert bootstrap._requires_full_cli_ignoring_rg_json(["--json", "--cpu", "foo"]) is True
    # the old implementation FILTERED `--json` tokens out first, so a `--json` that was `-e`'s
    # value vanished and `-h` then looked like `-e`'s value: help was missed.
    assert bootstrap._requires_full_cli_ignoring_rg_json(["-e", "--json", "-h"]) is True
    assert bootstrap._requires_full_cli([]) is True


def test_r39b_help_routes_to_the_full_cli_but_pattern_h_does_not(monkeypatch, tmp_path, capsys):
    route, _, _ = _drive(monkeypatch, tmp_path, ["-e", "-h", "src"], capsys=capsys)
    assert route == "rg"
    route, _, _ = _drive(monkeypatch, tmp_path, ["--", "-h"], capsys=capsys)
    assert route == "rg"
    route, _, _ = _drive(monkeypatch, tmp_path, ["-h"], capsys=capsys)
    assert route == "full"
    route, _, _ = _drive(monkeypatch, tmp_path, ["foo", "--help"], capsys=capsys)
    assert route == "full"


# --------------------------------------------------------------------------------------------
# Parser differential against real rg
# --------------------------------------------------------------------------------------------


def _canonical(argv: list[str]) -> list[str]:
    """Rebuild argv from the guards' parse alone: every flag separated (``-e foo``), then ``--``
    and the positionals. If the parse is right, rg behaves identically on this form."""
    out: list[str] = []
    for spelling, value in g._flags(argv):
        out.append(spelling)
        if value is not None:
            out.append(value)
    return [*out, "--", *g.positionals(argv)]


_POOL = [
    "-e",
    "-f",
    "-g",
    "-r",
    "-i",
    "-q",
    "-l",
    "-F",
    "-w",
    "-s",
    "-ie",
    "-ig",
    "-iefoo",
    "-e=foo",
    "--regexp",
    "--regexp=needle",
    "--file",
    "pats.txt",
    "--",
    "-",
    "foo",
    "needle",
    "-uuu",
    "-kq",
    "-d1",
    "-m1",
    "-A1",
    "-g=*.py",
    "*.py",
    "(",
    "a.txt",
    "--no-ignore",
    "--ignore",
    "-m+1",
    "-C=+2",
    "-d+1",
    "-m-1",
    "-j+1",
    "-A++1",
    "--files",
    "--format",
    "--hidden",
    "-.",
    "-n",
    "--no-fixed-strings",
    "-P",
    "--no-pcre2",
    "--engine=default",
    "-tpy",
    "-t",
    "py",
    "-T",
    "py",
    "--glob",
    "--max-count=1",
    "-C",
    "1",
    "-ir",
    "X",
]
# -h/-V/--help print unbounded text; --files/-z/--pre spawn or list; --stats/--debug are timed.
_AVOID = {"-h", "-V", "--help", "--version", "--stats", "--debug", "--trace", "--pre", "-z"}


@needs_rg
def test_parser_differential_against_real_rg(rgdir):
    rng = random.Random(20261004)
    checked = disagreements = 0
    mismatches: list[tuple[list[str], tuple, tuple]] = []
    for _ in range(120):
        tokens = [rng.choice(_POOL) for _ in range(rng.randint(1, 5))]
        assert not set(tokens) & _AVOID
        argv = [*tokens, "src"]
        raw, canon = rg_run(rgdir, argv), rg_run(rgdir, _canonical(argv))
        checked += 1
        if raw != canon:
            disagreements += 1
            mismatches.append((argv, raw, canon))
    assert checked == 120
    assert disagreements == 0, mismatches[:3]


@needs_rg
def test_parser_differential_can_fail(rgdir):
    """Positive control for the differential: a deliberately WRONG canonicaliser (treating every
    `--` as the sentinel, as the pre-round-2 `'--' in args` did) diverges from rg on `-e --`."""
    argv = ["-e", "--", "-kq", "src"]
    wrong = [*argv[:2], "--", *argv[2:]]  # naive: sentinel before the tail
    assert rg_run(rgdir, argv) != rg_run(rgdir, wrong)
    assert rg_run(rgdir, argv) == rg_run(rgdir, _canonical(argv))


def _legacy_path_walk():
    """The pre-prototype path walker, FROZEN from main 094dc97 in tests/unit/_fixtures (a copy, so
    the differential never reads git and keeps meaning something after this change merges)."""
    import importlib.util

    path = Path(__file__).parent / "_fixtures" / "legacy_search_path_walk.py"
    spec = importlib.util.spec_from_file_location("legacy_search_path_walk", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._search_path_args_raw


def test_path_walk_agrees_with_the_pre_prototype_walker_except_dash_and_files():
    """Residual 1 closed: `_search_path_args_raw` now runs on the guards' grammar. Differential
    over 20000 seeded argvs against the walker it replaced (frozen copy): identical
    everywhere EXCEPT the two documented fixes, a bare `-` before `--` and `--files`."""
    legacy = _legacy_path_walk()
    rng = random.Random(3)
    pool = [
        *_POOL,
        "-e",
        "--regexp",
        "-f",
        "-g",
        "--",
        "-ie",
        "-ig",
        "-",
        "-qe",
        "-iefoo",
        "-ge",
        "-e=",
        "-f=pats.txt",
        "--file=pats.txt",
        "-e==x",
        "--glob=*.py",
        "--max-count",
        "-uu",
        "-.d1",
        "--files",
    ]
    differing = 0
    for _ in range(20000):
        argv = [rng.choice(pool) for _ in range(rng.randint(1, 6))]
        new, old = bootstrap._search_path_args_raw(argv), legacy(argv)
        if new != old:
            differing += 1
            before_sentinel = argv[: g.end_of_options_index(argv)]
            explained = "--files" in argv or "-" in before_sentinel
            assert explained, (argv, new, old)
    assert differing > 0  # positive control: the differential can see the fixes it documents


@pytest.mark.parametrize(
    ("argv", "paths", "defaulted"),
    [
        (["-", "pats.txt"], ["pats.txt"], False),  # `-` is the PATTERN (was lost on main)
        (["-", "--", "a.txt"], ["a.txt"], False),
        (["foo", "-"], ["-"], False),  # stdin is an explicit path
        (["-"], [], True),  # a lone `-` is the pattern; scope defaulted
        (["-e", "-"], [], True),
        (["--files", "foo", "src"], ["foo", "src"], False),  # --files: no pattern, all paths
        (["--files"], [], True),
        (["--files", "-e", "foo", "src"], ["src"], False),
    ],
)
def test_path_walk_follows_rgs_grammar_for_dash_and_files(argv, paths, defaulted):
    assert bootstrap._search_path_args_raw(argv) == paths
    assert bootstrap._search_args_paths_defaulted(argv) is defaulted


@needs_rg
@pytest.mark.parametrize(
    "argv",
    [
        ["--files", "foo", "src"],
        ["--files", "src"],
        ["--files", "-g", "*.py", "src"],
        ["--files", "-e", "foo", "src"],
        ["--files", "--regexp=foo", "src"],
        ["--files", "--", "src"],
        ["--files", "-f", "pats.txt", "src"],
    ],
)
def test_files_mode_takes_no_pattern_in_real_rg(rgdir, argv):
    """Residual 5: rg --files treats EVERY positional as a path (`--files foo src` errors on the
    missing path `foo`, it does not search for pattern `foo`)."""
    rc, lines, err = rg_run(rgdir, argv)
    pattern, paths = g.split_pattern_and_paths(argv)
    assert pattern == []
    assert g.regex_patterns(argv) == []
    if "foo" in paths:
        assert rc == 2 and "foo:" in err  # rg stat'ed `foo` as a path
    else:
        assert rc == 0 and any(line.endswith(("a.txt", "b.py")) for line in lines)


def test_files_mode_pattern_is_not_prevalidated():
    # `tg search --files "(" src` has no pattern; the old extraction flagged `(` as invalid
    assert bootstrap._search_args_include_obviously_invalid_regex(["--files", "(", "src"]) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["(", "src"]) is True


def test_bare_dash_scope_note_and_stdin_still_correct_after_the_walker_change(
    monkeypatch, tmp_path, capsys
):
    for argv, note in ((["foo", "-"], False), (["-"], True), (["-", "src"], False)):
        assert (_SCOPE_NOTE_MARKER in _drive_rg_route(monkeypatch, tmp_path, capsys, argv)) is note


@needs_rg
def test_top_level_pattern_then_dash_e_dash_h_is_a_search_in_rg(rgdir):
    """Residual 2: `rg PATTERN -e -h`: `-h` is -e's value (the pattern), `PATTERN` is a path."""
    rc, lines, err = rg_run(rgdir, ["foo", "-e", "-h", "src/a.txt"])
    assert rc == 2 and "foo:" in err and _flat(lines) == "src/a.txt:-h"
    rc, lines, _ = rg_run(rgdir, ["src/a.txt", "-h"])
    assert "ripgrep 15" in _flat(lines)  # control: a genuine -h is help


@pytest.mark.parametrize(
    ("argv", "refused"),
    [
        (["foo", "-e", "-h"], False),  # was refused as "unknown command foo"
        (["foo", "--", "-h"], False),
        (["foo", "-ie", "--help"], False),
        (["foo", "-h"], True),  # control: genuine help for an unknown command
        (["foo", "--help"], True),
        (["foo", "-e", "x", "--help"], True),
        (["foo", "-ih"], True),
        (["foo", "-e", "x"], False),
    ],
)
def test_a90_top_level_refusal_is_value_aware(argv, refused):
    assert (bootstrap._top_level_command_refusal(argv) is not None) is refused


def test_a90_reserved_command_with_only_a_value_is_not_refused_as_flagged():
    from tensor_grep.cli.commands import RESERVED_TOP_LEVEL_COMMANDS

    reserved = sorted(RESERVED_TOP_LEVEL_COMMANDS)[0]
    assert bootstrap._top_level_command_refusal([reserved, "-x"]) is not None  # a real flag
    assert bootstrap._top_level_command_refusal([reserved, "--", "-x"]) is None  # sentinel
    assert bootstrap._top_level_command_refusal([reserved]) is None


@pytest.mark.parametrize(
    ("argv", "explicit"),
    [
        (["--format", "rg", "x"], True),
        (["--format=rg", "x"], True),
        (["-e", "--format", "rg"], False),  # `--format` is -e's value
        (["--", "--format=rg"], False),
        (["--format", "json"], False),
        (["-ie", "--format=rg"], False),
        (["x"], False),
    ],
)
def test_explicit_rg_format_is_option_position_only(argv, explicit):
    assert bootstrap._explicit_rg_format_requested(argv) is explicit


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--format", "rg", "x"], ["x"]),
        (["--format=rg", "x"], ["x"]),
        (["--format", "json", "x"], None),
        (["--format=json", "x"], None),
        (["--format"], None),
        (["-e", "--format", "rg"], ["-e", "--format", "rg"]),  # a PATTERN, kept
        (["--", "--format", "json"], ["--", "--format", "json"]),
        (["-ie", "--format=json", "x"], ["-ie", "--format=json", "x"]),
        (["x", "--format", "rg", "-i"], ["x", "-i"]),
    ],
)
def test_strip_noop_rg_format_is_option_position_only(argv, expected):
    assert bootstrap._strip_noop_rg_format(argv) == expected


@needs_rg
def test_rg_itself_rejects_format_so_stripping_only_matters_for_option_position(rgdir):
    # `--format` is a tg flag; rg errors on it as a flag but accepts it as a pattern or value.
    assert "unrecognized flag --format" in rg_run(rgdir, ["--format", "rg", "foo", "a.txt"])[2]
    assert rg_run(rgdir, ["-e", "--format", "a.txt"])[0] in (0, 1)
    assert rg_run(rgdir, ["--", "--format", "a.txt"])[0] in (0, 1)


# ---- residual 4: --ignore negates --no-ignore (last wins, per name) ----


@needs_rg
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (["--no-ignore"], True),
        (["--no-ignore", "--ignore"], False),
        (["--ignore", "--no-ignore"], True),
        (["--no-ignore", "--ignore", "--no-ignore"], True),
        (["--no-ignore-dot"], True),
        (["--no-ignore-dot", "--ignore-dot"], False),
        (["--ignore-dot", "--no-ignore-dot"], True),
    ],
)
def test_ignore_negation_matches_real_rg(rgdir, flags, expected):
    """Oracle: `.ignore` hides ign/ignored.txt; --no-ignore or --no-ignore-dot reveals it, their
    positive forms hide it again. Per-name last-wins, like rg."""
    full = [*flags, "foo", "."]
    _, lines, _ = rg_run(rgdir, full)
    assert ("ign/ignored.txt" in _flat(lines)) is expected
    assert g.flag_present(full, bootstrap._SEARCH_NO_IGNORE_FLAGS) is expected
    assert bootstrap._search_args_request_unrestricted_generated_scan(full) is expected


def test_negation_rule_is_generic_and_per_name():
    assert (
        g.flag_present(["--no-ignore-vcs", "--ignore-vcs"], bootstrap._SEARCH_NO_IGNORE_FLAGS)
        is False
    )
    assert (
        g.flag_present(["--no-ignore-vcs", "--ignore"], bootstrap._SEARCH_NO_IGNORE_FLAGS) is True
    )
    assert g.flag_present(["-e", "--ignore", "--no-ignore"], {"--no-ignore"}) is True
    assert (
        g.flag_present(["--no-ignore", "--", "--ignore"], {"--no-ignore"}) is True
    )  # after `--`: a pattern
    assert g.flag_present(["--no-ignore", "-e", "--ignore"], {"--no-ignore"}) is True  # a VALUE


# ---- residual 6: --file / --ignore-file in the pattern slot ----


@needs_rg
def test_pattern_slot_file_flag_is_an_option_in_rg_and_in_tg(rgdir):
    """STATED AND PINNED: rg reads `--file=PATH` in option position as a pattern-file OPTION; tg
    does the same, and a token the USER put behind `--` (or in `-e`) stays a literal pattern.
    The builder therefore adds no file read rg would not already perform for the same argv.
    NOTE this differs from main, which sentinel-ed any dash-led token in that slot: it was
    `--json --file=PATH src` -> pattern `--file=PATH`; it is now rg's reading, an option.
    Also `--file` is in `unsupported_flags`, so the normal delegation gate never reaches the
    builder with it (only TG_RUST_FIRST_SEARCH does)."""
    argv = ["-l", "--file=nosuch-file", "src"]
    assert nav.bootstrap_native_tg_search_argv(argv) == argv  # no sentinel: an option, like rg
    rc, _, err = rg_run(rgdir, argv)
    assert rc == 2 and "nosuch-file" in err  # rg tried to READ the file: it is an option
    quoted = nav.bootstrap_native_tg_search_argv(["-l", "--", "--file=nosuch-file", "src"])
    rc, _, err = rg_run(rgdir, quoted)
    assert "nosuch-file:" not in err and rc in (0, 1)  # behind `--` it is only a pattern
    rc, _, err = rg_run(rgdir, ["-e", "--file=nosuch-file", "src"])
    assert "nosuch-file:" not in err and rc in (0, 1)  # as -e's value it is only a pattern
    assert bootstrap._can_delegate_to_native_tg_search(["--json", "--file=x", "src"]) is False


def test_file_flags_are_plausible_flags_but_exec_flags_are_not():
    for tok in ("--file=/etc/passwd", "--file", "--ignore-file=x", "--ignore-file"):
        assert nav._is_plausible_rg_flag_token(tok)
    for tok in ("--pre=x", "--pre-glob=x", "--hostname-bin=x", "--search-zip"):
        assert not nav._is_plausible_rg_flag_token(tok)


def test_legacy_first_dash_led_positional_index_is_dead_code():
    """FINDING (not in the plan): its trailing `return index` is unreachable (every dash token
    `continue`s while options are open and parse_options never turns False), so the third branch of
    the sentinel builder never fires. Pinned so a revival is noticed."""
    rng = random.Random(5)
    for _ in range(5000):
        argv = [rng.choice(_POOL) for _ in range(rng.randint(1, 6))]
        assert nav._first_dash_led_positional_index(argv) is None


# --------------------------------------------------------------------------------------------
# Mutation controls: the round-38/39 defects, reintroduced on purpose, must be caught
# --------------------------------------------------------------------------------------------


def test_mutation_is_not_none_sentinel_test_skips_every_insertion(monkeypatch):
    """r38: `end_of_options_index(...) is not None` is ALWAYS true (it returns len, never None),
    so the CWE-88 builder would return before inserting `--`."""
    monkeypatch.setattr(nav, "has_end_of_options", lambda a: g.end_of_options_index(a) is not None)
    assert nav.bootstrap_native_tg_search_argv(["--json", "-kq", "src"]) == ["--json", "-kq", "src"]
    monkeypatch.undo()
    assert nav.bootstrap_native_tg_search_argv(["--json", "-kq", "src"]) == [
        "--json",
        "--",
        "-kq",
        "src",
    ]


def test_mutation_without_the_pattern_source_guard_rewrites_e_dashdash(monkeypatch):
    """r39(a): a value-aware sentinel test alone sends `-e --` to the all-dash-led branch."""
    monkeypatch.setattr(nav, "flag_present", lambda *a, **k: False)
    assert nav.bootstrap_native_tg_search_argv(["-e", "--"]) == ["--", "-e", "--"]  # the bug
    assert nav.bootstrap_native_tg_search_argv(["--json", "-e", "-foo"]) == [
        "--json",
        "--",
        "-e",
        "-foo",
    ]
    monkeypatch.undo()
    assert nav.bootstrap_native_tg_search_argv(["-e", "--"]) == ["-e", "--"]


def test_mutation_plain_membership_help_scan_misroutes_pattern_h():
    """r39(b): the pre-fix `any(arg in {"--help", "-h"})` selects the full CLI for `-e -h`."""
    naive = lambda args: any(a in {"--help", "-h"} for a in args)  # noqa: E731
    assert naive(["-e", "-h"]) is True and naive(["--", "-h"]) is True
    assert bootstrap._requires_full_cli(["-e", "-h"]) is False


def test_mutation_filtering_twice_hides_the_flag_after_a_value():
    """r19/r36: feeding an `option_tokens` result back into a value-aware scan consumes one more
    value. `["-g", "*.py", "-d", "2", "foo"]` becomes `["-g", "-d"]` and `-d` turns into -g's value."""
    argv = ["-g", "*.py", "-d", "2", "foo"]
    once = list(g.option_tokens(argv))
    assert once == ["-g", "-d"]
    assert g.flag_present(once, {"-d"}) is False  # the defect the raw-argv rule forbids
    assert g.flag_present(argv, {"-d"}) is True


def test_flag_present_with_negation_names_only_cancels_its_own_family():
    assert g.flag_present(["-F", "--no-pcre2"], {"-F", "--fixed-strings"}) is True
    assert g.flag_present(["-P", "--no-fixed-strings"], {"-P", "--pcre2"}) is True
    assert g.flag_present(["--hidden", "--no-hidden", "-."], {"-.", "--hidden"}) is True
    assert g.flag_present(["-.", "--no-hidden"], {"-.", "--hidden"}) is False


def test_e_cpu_pattern_is_not_the_cpu_flag():
    assert bootstrap._flag_present(["-e", "--cpu", "pat"], {"--cpu"}) is False
    assert bootstrap._flag_present(["--cpu", "-e", "pat"], {"--cpu"}) is True


@needs_rg
def test_no_ignore_is_composite_in_rg_so_the_per_name_model_is_conservative(rgdir):
    """FINDING: in rg `--no-ignore` implies the dot/vcs/global/exclude/parent family, and a later
    `--ignore-dot` re-enables ONLY the dot rules. So `--no-ignore --ignore-dot` hides `.ignore`d
    files again (rg), while vcs rules stay off. The guards' per-name model reports the no-ignore
    family as still in effect: it can over-report a walk bound, never under-report one."""
    argv = ["--no-ignore", "--ignore-dot", "foo", "."]
    assert "ign/ignored.txt" not in _flat(rg_run(rgdir, argv)[1])  # dot rules back on
    assert g.flag_present(argv, bootstrap._SEARCH_NO_IGNORE_FLAGS) is True  # vcs rules still off


# --------------------------------------------------------------------------------------------
# Council round 40: the exec-only sentinel policy must survive a pattern source
# --------------------------------------------------------------------------------------------


def _builder_without_the_pattern_source_early_return(argv):
    """The sentinel builder with the round-39 early return switched off (flag_present -> False):
    the behaviour main's logic plus F.1 would give. The reference for 'a pattern source never
    changes the outcome for argv carrying an exec-capable flag'."""
    patch = pytest.MonkeyPatch()
    patch.setattr(nav, "flag_present", lambda *a, **k: False)
    try:
        return nav.bootstrap_native_tg_search_argv(argv)
    finally:
        patch.undo()


@pytest.mark.parametrize(
    ("argv", "exec_token"),
    [
        (["--json", "-zebra", "src"], "-zebra"),  # `-z -e bra`: search-zip in a cluster with -e
        (["--json", "-izebra", "src"], "-izebra"),
        (["--pre=sh", "-efoo"], "--pre=sh"),  # exec flag + a pattern source
        (["--json", "--pre=sh", "-efoo"], "--pre=sh"),
        (["--json", "--pre", "sh", "-efoo"], "--pre"),
    ],
)
def test_r40_exec_capable_flag_keeps_the_sentinel_even_with_a_pattern_source(argv, exec_token):
    out = nav.bootstrap_native_tg_search_argv(argv)
    assert out.count("--") == 1 and out[out.index("--") + 1] == exec_token
    assert out == _builder_without_the_pattern_source_early_return(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "--pre-glob=*", "-e", "foo"],
        ["--json", "--hostname-bin=x", "-efoo", "src"],
        ["--json", "--search-zip", "-efoo", "src"],
        ["-zebra", "-e", "foo"],
    ],
)
def test_r40_pre_existing_gap_exec_flag_followed_by_another_dash_token_has_no_sentinel(argv):
    """NOT a regression (main behaves the same: its second branch needs a non-dash next token),
    and NOT widened here: the user typed both flags, so a `--` would turn their options into a
    pattern and paths. Pinned, with the early return shown to be irrelevant."""
    out = nav.bootstrap_native_tg_search_argv(argv)
    assert "--" not in out
    assert out == _builder_without_the_pattern_source_early_return(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["-e", "--"],
        ["--json", "-e", "--"],
        ["--json", "-e", "-foo"],
        ["-e", "--", "-kq", "src"],
        ["--json", "-e", "--", "-kq", "src"],
        ["-e", "--", "-kq"],  # all dash-led, but -e supplies the pattern
        ["--json", "-ez", "src"],  # `-e z`: z is a VALUE here, not search-zip
        ["--json", "-iefoo", "src"],
    ],
)
def test_r40_pattern_source_without_an_exec_flag_stays_unchanged(argv):
    assert nav.bootstrap_native_tg_search_argv(argv) == argv


@needs_rg
def test_r40_rg_reads_zebra_as_search_zip_plus_pattern_and_the_sentinel_quotes_it(rgdir):
    """Evidence: rg parses `-zeoo` as `-z -e oo` (it MATCHES `foo`), while behind `--` it is the
    literal pattern `-zeoo` (no match). The sentinel therefore changes meaning exactly where the
    exec-capable `-z` is involved, which is the point of keeping it."""
    rc, lines, _ = rg_run(rgdir, ["-l", "-zeoo", "a.txt"])
    assert rc == 0 and lines == ["a.txt"]
    boot = nav.bootstrap_native_tg_search_argv(["-l", "-zeoo", "a.txt"])
    assert boot == ["-l", "--", "-zeoo", "a.txt"]
    rc, lines, _ = rg_run(rgdir, boot)
    assert rc == 1 and lines == []


@needs_rg
def test_r40_pattern_source_cluster_without_z_is_a_real_pattern_source_in_rg(rgdir):
    # control: `-ez` is `-e z` (value z), no search-zip, so no sentinel is wanted
    assert nav.bootstrap_native_tg_search_argv(["-l", "-ez", "a.txt"]) == ["-l", "-ez", "a.txt"]
    rc, _, err = rg_run(rgdir, ["-l", "-ez", "a.txt"])
    assert rc == 1 and err == ""


def test_r40_every_early_return_in_the_sentinel_builder_is_accounted_for():
    """SWEEP of the class. The builder returns without a `--` in exactly these cases, and each is
    either user-quoted, a value, or exec-free:
    1. a REAL `--` already present (the user quoted everything after it);
    2. no remainder after the tg-only flags;
    3. a pattern source AND no exec-capable flag in the remainder (this fix);
    4. no dash-led token in the pattern slot / plausible rg flag cluster (F.1);
    5. `_first_dash_led_positional_index`, provably None (rg has no dash-led positional).
    Exec-capable flags in the pattern slot reach a sentinel in every route: asserted below over a
    product of exec tokens x followers x leading tg flags (2-char `-z` alone is the documented
    pre-existing exception: the `len > 2` gate)."""
    execs = [
        "-zebra",
        "-izeoo",
        "--pre=sh",
        "--pre",
        "--pre-glob=*",
        "--hostname-bin=x",
        "--search-zip",
    ]
    followers = [["-efoo"], ["-e", "foo"], ["src"], ["-f", "pats.txt"], ["--regexp=foo"], []]
    leads = [[], ["--json"], ["--cpu", "-l"], ["-g", "*.py"]]
    sentinelled = 0
    for lead in leads:
        for ex in execs:
            for follow in followers:
                argv = [*lead, ex, *follow]
                out = nav.bootstrap_native_tg_search_argv(argv)
                # the invariant: the pattern-source early return never changes the outcome
                assert out == _builder_without_the_pattern_source_early_return(argv), (argv, out)
                sentinelled += "--" in out
    assert sentinelled > 40  # positive control: the product really exercises the sentinel


def test_r40_mutation_plain_pattern_source_early_return_drops_the_exec_sentinel(monkeypatch):
    """The proposed-by-r39 shape (`return None` whenever a pattern source exists) loses the `--`
    main inserted for `-zebra src` and `--pre=sh -efoo`: the RED the fix closes."""
    monkeypatch.setattr(nav, "_exec_capable_flag_present", lambda args: False)
    assert nav.bootstrap_native_tg_search_argv(["--json", "-zebra", "src"]) == [
        "--json",
        "-zebra",
        "src",
    ]
    assert nav.bootstrap_native_tg_search_argv(["--pre=sh", "-efoo"]) == ["--pre=sh", "-efoo"]
    monkeypatch.undo()
    assert nav.bootstrap_native_tg_search_argv(["--pre=sh", "-efoo"]) == [
        "--",
        "--pre=sh",
        "-efoo",
    ]


def test_suite_never_reads_git_or_origin_main():
    """Council r40 P1: nothing in this file may depend on a moving ref."""
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = ["origin" + "/main", "[" + '"git"', "'" + "git'"]
    assert [word for word in forbidden if word in source] == []


# --------------------------------------------------------------------------------------------
# Council round 41: rg accepts ONE leading `+` on numeric short-option values
# --------------------------------------------------------------------------------------------

_NUMERIC_SHORT = "ABCMdjm"
# (value, rg accepts). Verified one by one against rg 15.1.0 for EVERY numeric short flag.
_NUMERIC_VALUES = [
    ("1", True),
    ("+1", True),
    ("=1", True),
    ("=+1", True),
    ("01", True),
    ("+01", True),
    ("0", True),
    ("+0", True),
    ("-1", False),
    ("=-1", False),
    ("-0", False),
    ("++1", False),
    ("+", False),
    ("=+", False),
    ("=", False),
    ("+x", False),
    ("1x", False),
    ("x", False),
    (" 1", False),
    ("1 ", False),
    ("+ 1", False),
    ("1_0", False),
    ("1e3", False),
    (chr(0x661), False),  # Arabic-Indic digit one: str.isdigit() is True, rg rejects
    ("+" + chr(0x661), False),
    (chr(0xFF0B) + "1", False),  # full-width plus
    (chr(0x2212) + "1", False),  # minus sign U+2212
    ("+1+", False),
    ("99999999999999999999", False),  # > u64
    ("+99999999999999999999", False),
    ("18446744073709551615", True),  # u64::MAX
    # council r42: huge values. int() on > 4300 digits raises ValueError on Python 3.11+; every
    # verdict below was read off rg 15.1.0 (-m and -A), not assumed.
    ("1" * 4301, False),
    ("+" + "1" * 4301, False),
    ("=" + "1" * 4301, False),
    ("1" * 5000, False),
    ("0" * 5000 + "1", True),  # rg accepts any number of leading zeros
    ("+" + "0" * 5000 + "1", True),
    ("=+" + "0" * 5000 + "1", True),
    ("0" * 5000, True),  # all zeros is the number 0 (-m 0: valid, finds nothing)
    ("+" + "0" * 5000, True),
    ("0" * 4300 + "1", True),
    ("0" * 4299 + "18446744073709551615", True),  # zero-padded u64::MAX
    ("+" + "0" * 5000 + "18446744073709551615", True),
    ("=+" + "0" * 20 + "18446744073709551615", True),
    ("18446744073709551616", False),  # u64::MAX + 1
    ("+18446744073709551616", False),
    ("0" * 5000 + "18446744073709551616", False),  # zero-padded overflow
]


def _numeric_id(val):
    if not isinstance(val, str):
        return None
    return val if len(val) <= 24 else f"{len(val)}chars-{val[:4]}..{val[-4:]}"


@pytest.mark.parametrize("letter", list(_NUMERIC_SHORT))
@pytest.mark.parametrize(("value", "accepted"), _NUMERIC_VALUES, ids=_numeric_id)
def test_r41_numeric_short_value_plausibility_per_rg_verdict(letter, value, accepted):
    token = f"-{letter}{value}"
    assert nav._is_plausible_rg_flag_token(token) is accepted
    out = nav.bootstrap_native_tg_search_argv(["--json", token, "foo", "src"])
    assert ("--" in out) is (not accepted)  # accepted -> flag, no sentinel; rejected -> pattern


@needs_rg
@pytest.mark.parametrize("letter", list(_NUMERIC_SHORT))
def test_r41_rg_oracle_agrees_with_every_numeric_verdict(rgdir, letter):
    """rg itself is the oracle for each (flag, value): a rejected value is rg's own 'not a valid
    number' parse error, an accepted one is not."""
    for value, accepted in _NUMERIC_VALUES:
        _, _, err = rg_run(rgdir, ["--no-config", f"-{letter}{value}", "foo", "a.txt"])
        rejected_by_rg = "not a valid number" in err or "error parsing flag" in err
        assert rejected_by_rg is (not accepted), (letter, value, err)


@needs_rg
def test_r41_the_reported_case_returns_a_line_in_real_rg_and_is_not_sentinelled(rgdir):
    for token in ("-m+1", "-m=+1", "-im+1"):
        argv = ["-n", token, "foo", "src/a.txt"]
        rc, lines, err = rg_run(rgdir, ["--no-config", *argv])
        assert rc == 0 and err == "" and len(lines) == 1, (token, lines, err)
        assert nav.bootstrap_native_tg_search_argv(["--json", *argv[1:]]) == ["--json", *argv[1:]]


@needs_rg
@pytest.mark.parametrize(
    ("flag", "value", "accepted"),
    [
        (flag, value, accepted)
        for flag in (
            "--max-count",
            "--max-depth",
            "--after-context",
            "--before-context",
            "--context",
            "--threads",
            "--max-columns",
        )
        for value, accepted in (
            ("1", True),
            ("+1", True),
            ("-1", False),
            ("++1", False),
            ("+", False),
        )
    ],
)
def test_r41_long_numeric_forms_agree_with_the_short_grammar(rgdir, flag, value, accepted):
    """Long forms use the same number grammar (`--max-count=+1` is accepted, `=-1` is not). The
    builder treats any KNOWN long name as a flag token whatever its value (rg then reports the bad
    number itself), so only the short-cluster path needs the number rule; pin both facts."""
    for argv in ([f"{flag}={value}"], [flag, value]):
        _, _, err = rg_run(rgdir, ["--no-config", *argv, "foo", "a.txt"])
        assert ("not a valid number" in err) is (not accepted), (argv, err)
    assert nav._is_plausible_rg_flag_token(f"{flag}={value}") is True


def test_r41_every_numeric_short_flag_in_the_table_is_the_set_the_rule_covers():
    # sweep of the class: the only numeric parse in the tokenizer/builder is
    # `_is_plausible_rg_flag_token` -> `_is_rg_unsigned_number`; no other `isdigit`/`int(` exists.
    assert set(_NUMERIC_SHORT) == set(nav._RG_NUMERIC_VALUE_SHORT)
    for source_file in _MODULE_FILES:
        text = _cli_source(source_file)
        assert text.count(".isdigit()") == (1 if source_file == "bootstrap_native_argv.py" else 0)


@pytest.mark.parametrize(("value", "accepted"), _NUMERIC_VALUES[-19:], ids=_numeric_id)
def test_r42_builder_never_raises_on_huge_numeric_values(value, accepted):
    """The r41 `int(digits)` crashed past 4300 digits (ValueError). Long valid values are left
    unchanged, long invalid ones get the sentinel, none raise."""
    for lead in ("-m", "-A", "-im", "-C"):
        argv = ["--json", f"{lead}{value}", "foo", "src"]
        out = nav.bootstrap_native_tg_search_argv(argv)  # must not raise
        assert (out == argv) is accepted
        assert (out[1:2] == ["--"]) is (not accepted)


def test_r42_reported_reproductions_do_not_raise():
    for argv in (
        ["--json", "-m" + "1" * 4301, "foo", "src"],
        ["--json", "-m" + "0" * 5000 + "1", "foo", "src"],
        ["--json", "-m" + "1" * 5000, "foo", "src"],
    ):
        nav.bootstrap_native_tg_search_argv(argv)
        nav._first_dash_led_pattern_index_after_tg_flags(argv)
    assert nav._is_rg_unsigned_number("0" * 5000 + "1") is True
    assert nav._is_rg_unsigned_number("1" * 5000) is False
    assert nav._is_rg_unsigned_number("") is False


def test_r42_no_int_conversion_of_user_argv_in_the_front_door_modules():
    """Sweep: `int(` over argv-derived text is forbidden in the three modules (the only `int(` in
    bootstrap.py convert return codes and a bool)."""
    for module_file in ("bootstrap_native_argv.py", "bootstrap_search_guards.py"):
        tree = ast.parse(_cli_source(module_file))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "int"]
        assert calls == [], module_file
    tree = ast.parse(_cli_source("bootstrap.py"))
    allowed = {
        "int(result.returncode)",
        "int(rc)",
        "int(_search_args_request_unrestricted(search_args))",
    }
    found = {
        ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and _call_name(n) == "int"
    }
    assert found <= allowed, found


def _call_name(node):
    return getattr(node.func, "id", None)


_HOSTILE_PATTERNS = {
    "nest100k": "(" * 100000,
    "nest5k-closed": "(" * 5000 + ")" * 5000,
    "noncapturing-3k": "(?:" * 3000 + "a" + ")" * 3000,
    "repeat-overflow": "a{99999999999999999999}",
}


@pytest.mark.parametrize("name", sorted(_HOSTILE_PATTERNS))
def test_r42_hostile_pattern_cannot_crash_the_pre_validator(name):
    """Python's re raises RecursionError / OverflowError (not re.error) on these; main only caught
    re.error, so the pre-validator crashed. Now: not pre-rejected, the real engine reports."""
    pattern = _HOSTILE_PATTERNS[name]
    assert g.pattern_invalid_in_both_engines(pattern) is False
    assert bootstrap._search_args_include_obviously_invalid_regex(["-e", pattern, "."]) is False
    assert bootstrap._regex_patterns_from_search_args(["--", pattern]) == [pattern]


@needs_rg
@pytest.mark.parametrize("name", sorted(_HOSTILE_PATTERNS))
def test_r42_rg_reports_its_own_error_for_the_hostile_patterns(rgdir, name):
    # passed via -f: a 100000-char pattern does not fit a Windows command line
    (rgdir / "hostile.pat").write_text(_HOSTILE_PATTERNS[name], encoding="utf-8")
    rc, _, err = rg_run(rgdir, ["--no-config", "-f", "hostile.pat", "a.txt"])
    assert rc == 2, (name, rc, err)  # rg rejects it itself (nest limit / size limit)


def test_r42_main_entry_survives_hostile_argv(monkeypatch, tmp_path, capsys):
    """main_entry has no try/except around the argv helpers, so any raise there is a traceback and
    exit 1. Drive the hostile cases end to end through it."""
    for argv in (
        ["-e", "(" * 100000, "."],
        ["-e", "a{99999999999999999999}", "."],
        ["--json", "-m" + "1" * 4301, "foo", "."],
        ["--json", "-m" + "0" * 5000 + "1", "foo", "."],
    ):
        route, _, _ = _drive(monkeypatch, tmp_path, argv, native=True, capsys=capsys)
        assert route in {"rg", "native", "full"}, argv


def test_r42_positive_control_python_really_refuses_int_past_4300_digits():
    with pytest.raises(ValueError):
        int("1" * 4301)
    with pytest.raises(ValueError):
        int("0" * 5000 + "1")
