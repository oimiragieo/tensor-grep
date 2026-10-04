"""`-o` / `--replace` must use rg's authoritative submatch offsets when rg ran the search.

Smart case on `foo\\S` is decided by rg alone, so the pipeline routes it to rg; the Python
post-processing must then slice at rg's offsets instead of re-deciding case (it used to call the
strict resolver and crash with BackendExecutionError for that very pattern).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine

pytestmark = pytest.mark.skipif(resolve_ripgrep_binary() is None, reason="rg not installed")

_SRC = os.environ.get("TG_SRC_UNDER_TEST") or str(Path(__file__).resolve().parents[2] / "src")


class _Result:
    def __init__(self, proc: subprocess.CompletedProcess[str]):
        self.exit_code = proc.returncode
        self.output = proc.stdout
        self.stderr = proc.stderr


def _file(tmp_path, content: bytes = b"FOOX\n"):
    f = tmp_path / "a.txt"
    f.write_bytes(content)
    return str(f)


def _search(*args: str):
    # real subprocess through the Python door: rg-passthrough output bypasses CliRunner capture
    env = {**os.environ, "PYTHONPATH": _SRC, "TG_FORCE_PYTHON": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "tensor_grep", "search", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    return _Result(proc)


def test_only_matching_smart_case_regex_with_escape(tmp_path):
    result = _search("-S", "-o", r"foo\S", _file(tmp_path))
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.strip() == "FOOX"


def test_replace_smart_case_regex_with_escape(tmp_path):
    result = _search("-S", "--replace", "x", r"foo\S", _file(tmp_path))
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.strip() == "x"


def test_replace_with_capture_group_uses_rg_span(tmp_path):
    result = _search("-S", "--replace", "[$1]", r"(foo)\S", _file(tmp_path))
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.strip() == "[FOO]"


def test_only_matching_multiple_occurrences_use_every_span(tmp_path):
    result = _search("-o", "ab", _file(tmp_path, b"ab xx ab\n"))
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.split() == ["ab", "ab"]


def test_only_matching_byte_offsets_with_multibyte_prefix(tmp_path):
    result = _search("-o", "foo", _file(tmp_path, "éé foo\n".encode()))
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.strip() == "foo"


def test_ascii_literal_controls_keep_existing_behaviour(tmp_path):
    f = _file(tmp_path, b"foo bar\n")
    assert _search("-o", "foo", f).output.strip() == "foo"
    assert _search("--replace", "x", "foo", f).output.strip() == "x bar"


@pytest.mark.parametrize("template, expected", [("x", "x"), ("[$1]", "[FOO]")])
def test_post_processing_of_rg_matches_never_redecides_smart_case(tmp_path, template, expected):
    # the audit repro at the seam: rg matched FOOX for `foo\S` under -S; extraction and
    # replacement used to re-run the pattern with a strict case resolver and raise
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    pattern = r"(foo)\S" if "$1" in template else r"foo\S"
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOOX\n")
    cfg = SearchConfig(query_pattern=pattern, smart_case=True, replace_str=template)
    (replaced,) = RipgrepBackend().search(str(f), pattern, cfg).matches
    assert replaced.text == expected

    only = SearchConfig(query_pattern=pattern, smart_case=True, only_matching=True)
    out = RipgrepBackend().search(str(f), pattern, only).matches
    assert [m.text for m in out] == ["FOOX"]


def _rg_o_replace(path, pattern, template, *flags):
    import subprocess as sp

    proc = sp.run(
        [
            str(resolve_ripgrep_binary()),
            "--no-config",
            *flags,
            "-o",
            "-r",
            template,
            "-e",
            pattern,
            "--",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.splitlines()


def _seam(tmp_path, content: bytes, pattern: str, template: str):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    f = tmp_path / "a.txt"
    f.write_bytes(content)
    cfg = SearchConfig(
        query_pattern=pattern, smart_case=True, replace_str=template, only_matching=True
    )
    matches = RipgrepBackend().search(str(f), pattern, cfg).matches
    assert matches and matches[0].submatches
    return f, [m.text for m in matches]


def test_only_matching_with_replace_emits_each_replaced_match_alone(tmp_path):
    f, lines = _seam(tmp_path, b"FOOX\n", r"foo\S", "[$0]")
    assert lines == ["[FOOX]"]
    assert lines == _rg_o_replace(f, r"foo\S", "[$0]", "-S")


def test_only_matching_with_replace_two_matches_on_one_line_give_two_lines(tmp_path):
    f, lines = _seam(tmp_path, b"ab xx ab\n", "ab", "<$0>")
    assert lines == ["<ab>", "<ab>"]
    assert lines == _rg_o_replace(f, "ab", "<$0>", "-S")


def test_only_matching_with_replace_capture_group(tmp_path):
    f, lines = _seam(tmp_path, b"FOOX foox\n", r"(foo)(\S)", "$2-$1")
    assert lines == ["X-FOO", "x-foo"]
    assert lines == _rg_o_replace(f, r"(foo)(\S)", "$2-$1", "-S")


def test_only_matching_with_replace_literal_template_and_end_to_end(tmp_path):
    f = _file(tmp_path, b"FOOX\n")
    result = _search("-S", "-o", "-r", "[$0]", r"foo\S", f)
    assert result.exit_code == 0, (result.output, result.stderr)
    assert result.output.splitlines() == _rg_o_replace(f, r"foo\S", "[$0]", "-S") == ["[FOOX]"]


# ---------------------------------------------------------------------------
# Round-6 design: rg itself produces -o / -r output; Python never rebuilds it.
# Every comparison is against rg's own bytes, or the call must raise BackendExecutionError.
# ---------------------------------------------------------------------------


def _rg_lines(path, pattern, *flags) -> list[bytes]:
    proc = subprocess.run(
        [
            str(resolve_ripgrep_binary()),
            "--no-config",
            "--no-line-number",
            *flags,
            "-e",
            pattern,
            "--",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    return proc.stdout.splitlines()


def _tg_matches(path, pattern, *, template=None, only=False, smart=False, fixed=False, **extra):
    """Search through RipgrepBackend (rg renders -o/-r itself). Returns (cfg, matches)."""
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    cfg = SearchConfig(
        query_pattern=pattern,
        smart_case=smart,
        fixed_strings=fixed,
        replace_str=template,
        only_matching=only,
        **extra,
    )
    return cfg, RipgrepBackend().search(str(path), pattern, cfg).matches


def _tg_lines(path, pattern, **kw):
    from tensor_grep.backends.base import BackendExecutionError

    try:
        _cfg, matches = _tg_matches(path, pattern, **kw)
        return [m.text.encode("utf-8") for m in matches]
    except BackendExecutionError:
        return "raised"


def _same_as_rg_or_raised(path, pattern, rg_flags, **kw):
    got = _tg_lines(path, pattern, **kw)
    assert got == "raised" or got == _rg_lines(path, pattern, *rg_flags), (got, rg_flags)
    return got


def test_alternation_captures_are_rgs_not_a_python_recovery(tmp_path):
    # audit 1: `(foo)|(\w+)` -S `[$1][$2]` on FOO -> rg gives [FOO][]; recovery gave [][FOO]
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOO\n")
    pattern = r"(foo)|(\w+)"
    got = _same_as_rg_or_raised(
        f, pattern, ["-S", "-r", "[$1][$2]"], template="[$1][$2]", smart=True
    )
    assert got == [b"[FOO][]"]
    got_o = _same_as_rg_or_raised(
        f, pattern, ["-S", "-o", "-r", "[$1][$2]"], template="[$1][$2]", smart=True, only=True
    )
    assert got_o == [b"[FOO][]"]


def test_redos_pattern_is_bounded_because_python_never_evaluates_it(tmp_path):
    # audit 2: `(a+)+b|a+$` on 32 a's took >3s in Python re while rg is instant
    import time

    f = tmp_path / "a.txt"
    f.write_bytes(b"a" * 32 + b"\n")
    started = time.monotonic()
    got = _same_as_rg_or_raised(f, r"(a+)+b|a+$", ["-r", "[$0]"], template="[$0]")
    got_o = _same_as_rg_or_raised(
        f, r"(a+)+b|a+$", ["-o", "-r", "[$0]"], template="[$0]", only=True
    )
    assert time.monotonic() - started < 3.0
    assert got == got_o == [b"[" + b"a" * 32 + b"]"]


def test_cpu_fallback_repeated_matches_never_yield_a_single_occurrence(monkeypatch, tmp_path):
    # audit 3: the CPU Python loop recorded only the first re.search hit, so `ab ab` -o gave one
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.backends.cpu_backend import CPUBackend
    from tensor_grep.cli.rg_post_process import post_process_matches
    from tensor_grep.core.pipeline import Pipeline

    f = tmp_path / "a.txt"
    f.write_bytes(b"ab ab\n")
    expected = _rg_lines(f, "ab", "-F", "-o")
    assert expected == [b"ab", b"ab"]

    def native_fault(*args, **kwargs):
        raise RuntimeError("fault")  # force the Python-re loop

    monkeypatch.setattr(CPUBackend, "_rust_match_set", native_fault)
    cfg = SearchConfig(query_pattern="ab", fixed_strings=True, only_matching=True)
    # direct CPU result: either complete (== rg) or refused, never one occurrence
    try:
        direct = CPUBackend().search(str(f), "ab", cfg).matches
        got = [m.text.encode() for m in post_process_matches(direct, "ab", cfg, True)]
    except BackendExecutionError:
        got = "raised"
    assert got == "raised" or got == expected, got
    # and through the pipeline the request is routed to rg: exact
    pipe = Pipeline(force_cpu=True, config=cfg)
    assert type(pipe.backend).__name__ == "RipgrepBackend"
    matches = pipe.backend.search(str(f), "ab", cfg).matches
    assert [m.text.encode() for m in matches] == expected


def test_invalid_utf8_line_offsets_are_applied_to_original_bytes(tmp_path):
    # audit 4: rg offsets [1,4) were applied to the re-encoded U+FFFD text of b"\xfffoo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"\xfffoo\n")
    got = _same_as_rg_or_raised(f, "foo", ["-o"], only=True)
    assert got == [b"foo"]
    # -r on a line that is not valid UTF-8 cannot be rendered byte-exactly through str: it must
    # equal rg's bytes or be refused, never garbage
    _same_as_rg_or_raised(f, "foo", ["-r", "X"], template="X")


@pytest.mark.parametrize(
    "name, content, pattern, rg_flags, kw",
    [
        (
            "o+context",
            b"before\nfoo\nafter\n",
            "foo",
            ["-o", "-C", "1"],
            {"only": True, "context": 1},
        ),
        ("o+invert", b"bar\n", "foo", ["-v", "-o"], {"only": True, "invert_match": True}),
        (
            "r+context",
            b"before\nfoo\nafter\n",
            "foo",
            ["-r", "X", "-C", "1"],
            {"template": "X", "context": 1},
        ),
        ("r+invert", b"bar\n", "foo", ["-v", "-r", "X"], {"template": "X", "invert_match": True}),
        # audit 4: rg preserves the raw \xff context byte; a str cannot, so equal-or-refuse
        (
            "r+context+invalid-utf8",
            b"\xff\nfoo\n",
            "foo",
            ["-r", "X", "-C", "1"],
            {"template": "X", "context": 1},
        ),
        (
            "o+context+invalid-utf8",
            b"\xff\nfoo\n",
            "foo",
            ["-o", "-C", "1"],
            {"only": True, "context": 1},
        ),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 30 else "",
)
def test_context_and_inverted_records_pass_through_as_rg_printed_them(
    tmp_path, name, content, pattern, rg_flags, kw
):
    f = tmp_path / "a.txt"
    f.write_bytes(content)
    got = _same_as_rg_or_raised(f, pattern, rg_flags, **kw)
    if "invalid-utf8" in name:
        assert got == "raised"  # never U+FFFD garbage
    else:
        assert got != "raised"  # valid UTF-8 context/inverted records must not be refused


def _rendered(path, pattern, **kw):
    from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
    from tensor_grep.core.result import SearchResult

    cfg, matches = _tg_matches(path, pattern, column=True, line_number=True, **kw)
    result = SearchResult(matches=matches, total_files=1, total_matches=len(matches))
    return RipgrepFormatter(cfg).format(result).split("\n")


@pytest.mark.parametrize(
    "rg_flags, kw",
    [
        (["-o"], {"only": True}),
        (["-r", "X"], {"template": "X"}),
        (["-o", "-r", "X"], {"only": True, "template": "X"}),
        (["-o", "-r", "XYZ"], {"only": True, "template": "XYZ"}),  # longer than the match
        (["-o", "-r", ""], {"only": True, "template": ""}),  # shorter than the match
    ],
    ids=["o", "r", "o+r", "o+r-longer", "o+r-empty"],
)
def test_columns_match_rg_and_never_claim_rg_was_unavailable(tmp_path, capsys, rg_flags, kw):
    f = tmp_path / "a.txt"
    f.write_bytes(b"xx ab yy ab\n")
    expected = [line.decode() for line in _rg_lines(f, "ab", "-n", "--column", *rg_flags)]
    assert _rendered(f, "ab", **kw) == expected
    assert "column approximated" not in capsys.readouterr().err


def _py_door(*args: str):
    code = f"import sys; sys.path.insert(0, {_SRC!r}); from tensor_grep.cli.main import app; app()"
    return subprocess.run(
        [sys.executable, "-c", code, "search", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_bogus_engine_exits_2_with_a_structured_error_not_a_traceback(tmp_path):
    f = _file(tmp_path, b"foo\n")
    plain = _py_door("--engine", "bogus", "foo", f)
    as_json = _py_door("--json", "--engine", "bogus", "foo", f)
    for proc in (plain, as_json):
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-300:])
        assert "Traceback" not in proc.stderr
    assert "bogus" in plain.stderr
    import json

    assert "error" in json.loads(as_json.stdout)


def test_unrepresentable_rg_output_exits_2_with_a_structured_error(tmp_path):
    # an invalid-UTF-8 context line cannot be reproduced byte-exactly through str, so the
    # STRUCTURED (--json) route refuses it with exit 2 ...
    f = _file(tmp_path, b"\xff\nfoo\n")
    proc = _py_door("--json", "-o", "-C", "1", "foo", f)
    assert proc.returncode == 2, (proc.returncode, proc.stderr[-300:])
    assert "Traceback" not in proc.stderr
    assert "rg" in (proc.stderr + proc.stdout)
    # ... while plain text is rg's own raw bytes, \xff included
    plain = _py_door_bytes("-o", "-C", "1", "foo", f)
    assert plain.stdout == _rg_run("foo", f, "-o", "-C", "1").stdout
    assert plain.returncode == 0 and b"\xff" in plain.stdout


def test_valid_o_with_context_and_inverted_searches_work_end_to_end(tmp_path):
    f = _file(tmp_path, b"before\nfoo\nafter\n")
    ctx = _py_door("-o", "-C", "1", "foo", f)
    assert ctx.returncode == 0, ctx.stderr[-300:]
    assert ctx.stdout.split() == ["before", "foo", "after"]
    g = _file(tmp_path, b"bar\n")
    inv = _py_door("-v", "-o", "foo", g)
    assert inv.returncode == 0, inv.stderr[-300:]
    assert inv.stdout.split() == ["bar"]


def _py_door_bytes(*args: str):
    code = f"import sys; sys.path.insert(0, {_SRC!r}); from tensor_grep.cli.main import app; app()"
    return subprocess.run(
        [sys.executable, "-c", code, "search", *args],
        capture_output=True,
        timeout=120,
        check=False,
    )


def _rg_run(pattern: str, path, *flags: str):
    return subprocess.run(
        [str(resolve_ripgrep_binary()), "--no-config", *flags, "-e", pattern, "--", str(path)],
        capture_output=True,
        check=False,
    )


# Round 8: plain-text -o / -r is rg's OWN stdout, written through unchanged (the same
# passthrough route every other plain rg search uses); Python renders only structured output.
_TEXT_CASES = [
    ("empty-match", b"foo\n", "^", ["-o"]),
    ("multiline", b"foo\nbar\n", r"foo\nbar", ["-U", "-o", "-n", "--column"]),
    ("context", b"before\nfoo\nafter\n", "foo", ["-o", "-C", "1", "-n", "--column"]),
    ("context+r", b"before\nfoo\nafter\n", "foo", ["-o", "-r", "X", "-C", "1", "-n"]),
    ("invert", b"bar\n", "foo", ["-v", "-o"]),
    ("col-o", b"xx ab yy ab\n", "ab", ["-n", "--column", "-o"]),
    ("col-r", b"xx ab yy ab\n", "ab", ["-n", "--column", "-r", "X"]),
    ("col-o-r", b"xx ab yy ab\n", "ab", ["-n", "--column", "-o", "-r", "X"]),
    ("col-o-r-long", b"xx ab yy ab\n", "ab", ["-n", "--column", "-o", "-r", "XYZ"]),
]


@pytest.mark.parametrize(
    "name, content, pattern, flags", _TEXT_CASES, ids=[c[0] for c in _TEXT_CASES]
)
def test_plain_text_o_and_r_are_rgs_own_bytes_and_exit_code(
    tmp_path, name, content, pattern, flags
):
    f = tmp_path / "a.txt"
    f.write_bytes(content)
    expected = _rg_run(pattern, f, *flags)
    got = _py_door_bytes(*flags, pattern, str(f))
    assert got.stdout == expected.stdout, (name, got.stdout, expected.stdout, got.stderr)
    assert got.returncode == expected.returncode, (name, got.returncode, got.stderr[-200:])
    assert b"column approximated" not in got.stderr


def _json_matches(*args: str):
    import json

    proc = _py_door_bytes("--json", *args)
    return proc, json.loads(proc.stdout or b"{}")


def test_json_empty_match_is_kept_and_success_is_not_derived_from_text(tmp_path):
    # audit 1: `-o '^'` printed nothing and exited 1; rg prints an empty line and exits 0
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    proc, doc = _json_matches("-o", "^", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert doc["total_matches"] == 1
    (entry,) = doc["matches"]
    assert (entry["line_number"], entry["text"], entry["column"]) == (1, "", 1)


def test_json_multiline_only_matching_gets_one_numbered_entry_per_line(tmp_path):
    # audit 2: `-U -o -n 'foo\nbar'` -> rg prints 1:1:foo then 2:1:bar
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\n")
    proc, doc = _json_matches("-U", "-o", r"foo\nbar", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    got = [(m["line_number"], m["text"], m["column"]) for m in doc["matches"]]
    assert got == [(1, "foo", 1), (2, "bar", 1)]
    assert doc["total_matches"] == 2


def _rg_entries(path, pattern, *flags, sep: bytes = b"\n"):
    """rg's own `-n --column -o` output as (line, column, text) triples."""
    out = _rg_run(pattern, path, *flags, "-n", "--column", "-o").stdout
    entries = []
    chunks = out.split(sep)
    if chunks and chunks[-1] == b"":
        chunks.pop()
    for chunk in chunks:
        line, col, text = chunk.split(b":", 2)
        entries.append((int(line), int(col), text.decode()))
    return entries


def _tg_entries(path, pattern, *flags):
    proc, doc = _json_matches(*flags, "-o", pattern, str(path))
    assert proc.returncode == 0, proc.stderr[-300:]
    return [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]], doc


_FORGE = 'fake: binary file matches (found "\\0" byte around offset 7)'


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="LF/':'/'\"' are not legal in Windows names"
)
def test_a_filename_containing_lf_cannot_forge_records_posix(tmp_path):
    # round 14: a name with LF + a notice lookalike used to forge a match for `fake` plus one for
    # `real`. Paths are JSON data now, so the name is just a name.
    name = _FORGE + "\nreal"
    f = tmp_path / name
    f.write_bytes(b"foo\n")
    proc, doc = _json_matches("-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["file"], m["line_number"], m["text"]) for m in doc["matches"]] == [
        (str(f), 1, "foo")
    ]
    assert doc["total_files"] == 1


def test_a_hostile_looking_filename_is_just_a_name_windows_legal(tmp_path):
    # the Windows-legal equivalent: digits, dashes and lookalike words in a name
    f = tmp_path / "1-fake 2-binary file matches (found 0 byte around offset 7) real.txt"
    f.write_bytes(b"foo\n")
    proc, doc = _json_matches("-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["file"], m["line_number"], m["text"]) for m in doc["matches"]] == [
        (str(f), 1, "foo")
    ]


def test_replacement_that_removes_newlines_shifts_columns_like_rg(tmp_path):
    # round 14: `ab\nab ab` -a -U -o -r X -> rg: (1,1,X),(1,3,X); the old rule gave (2,4,X)
    f = tmp_path / "a.txt"
    f.write_bytes(b"ab\nab ab\n")
    pattern = r"ab\nab|ab"
    proc, doc = _json_matches("-a", "-U", "-o", "-r", "X", pattern, str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    got = [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]]
    assert got == [(1, 1, "X"), (1, 3, "X")]
    assert got == _rg_entries(f, pattern, "-a", "-U", "-r", "X")


def test_multiline_o_is_split_per_line_like_rg_even_with_text_mode(tmp_path):
    # round 14: `-a -U -o 'foo\nbar'` is two numbered entries; `-a -U -o '\n'` prints nothing, exit 0
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\n")
    proc, doc = _json_matches("-a", "-U", "-o", r"foo\nbar", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]] == [
        (1, 1, "foo"),
        (2, 1, "bar"),
    ]
    proc2, doc2 = _json_matches("-a", "-U", "-o", r"\n", str(f))
    assert proc2.returncode == 0 and doc2["matches"] == [] and doc2["total_matches"] == 0
    assert _rg_run(r"\n", f, "-a", "-U", "-o").returncode == 0


@pytest.mark.parametrize(
    "extra", [["-o"], ["-r", "X"], ["-o", "-r", "X"], ["-C", "1"], ["-C", "1", "-o"]]
)
@pytest.mark.parametrize("negated", [False, True])
def test_no_crlf_wins_over_crlf_like_rg(tmp_path, extra, negated):
    # round 15: the renderer used the raw --crlf and gave "foo\r" where rg (last wins) gives "foo".
    # SearchConfig keeps separate booleans and the backend emits --crlf then --no-crlf, so with
    # both set rg receives (and the renderer must use) the negation. Compared with rg directly.
    f = tmp_path / "a.txt"
    f.write_bytes(b"x\nfoo\ny\n")
    rg_extra = ["--crlf", "--no-crlf"] if negated else ["--crlf"]
    kw = {"only_matching": "-o" in extra, "crlf": True, "no_crlf": negated}
    if "-r" in extra:
        kw["replace_str"] = "X"
    if "-C" in extra:
        kw["context"] = 1
    cfg = SearchConfig(query_pattern="foo", **kw)
    expected = _reference_entries(
        _rg_run("foo", f, "-n", "--column", "--no-context-separator", *rg_extra, *extra).stdout,
        null_data=False,
        inverted=False,
    )
    got = _printer_entries(f, "foo", cfg)
    assert got == expected
    if negated:
        assert all(not e[3].endswith("\r") for e in got)


def test_json_trailing_newline_in_a_match_is_not_a_phantom_entry(tmp_path):
    # audit (round 9): `-U -o 'foo\n'` on foo/bar -> rg reports ONE match, 1:1:foo
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\n")
    got, doc = _tg_entries(f, r"foo\n", "-U")
    assert got == _rg_entries(f, r"foo\n", "-U") == [(1, 1, "foo")]
    assert doc["total_matches"] == 1


def test_json_multiline_match_starting_with_the_delimiter_uses_rgs_coordinates(tmp_path):
    # round 11: `\nbar` on foo/bar -> rg prints `2:4:bar` (NOT a phantom (1,4,"") plus (2,1,"bar"))
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\n")
    got, doc = _tg_entries(f, r"\nbar", "-U")
    assert got == _rg_entries(f, r"\nbar", "-U") == [(2, 4, "bar")]
    assert doc["total_matches"] == 1


def test_json_match_consisting_only_of_the_delimiter_is_zero_entries_and_success(tmp_path):
    # round 11: `-U -o '\n'` -> rg prints NOTHING and exits 0 (tg used to invent two empty matches)
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\n")
    assert _rg_run(r"\n", f, "-U", "-o").returncode == 0
    proc, doc = _json_matches("-U", "-o", r"\n", str(f))
    assert doc["matches"] == [] and doc["total_matches"] == 0
    assert proc.returncode == 0, proc.stderr[-300:]  # rg's success, faithfully


def test_json_multiline_replacement_uses_replaced_line_coordinates(tmp_path):
    # round 11: `ab\nab|ab` -U -o -r X -> rg: 1:1:X then 1:3:X (not 1:1 then 2:4)
    f = tmp_path / "a.txt"
    f.write_bytes(b"ab\nab ab\n")
    pattern = r"ab\nab|ab"
    got, _doc = _tg_entries(f, pattern, "-U", "-r", "X")
    assert got == _rg_entries(f, pattern, "-U", "-r", "X") == [(1, 1, "X"), (1, 3, "X")]


def test_json_non_ascii_text_columns_are_rgs_byte_columns(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("xéé é\n".encode())
    got, _doc = _tg_entries(f, "é", "-n")
    assert got == _rg_entries(f, "é") == [(1, 2, "é"), (1, 4, "é"), (1, 7, "é")]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="':' is not valid in Windows filenames")
def test_json_filename_with_colon_and_digits_is_parsed_via_nul(tmp_path):
    f = tmp_path / "12:3:x.txt"
    f.write_bytes(b"foo\n")
    proc, doc = _json_matches("-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["file"], m["line_number"], m["text"]) for m in doc["matches"]] == [
        (str(f), 1, "foo")
    ]


# ---------------------------------------------------------------------------------------------
# Round 12: user flags that change rg's OUTPUT FORMAT must not break the internal record stream.
# Differential: every conflicting user flag must give exactly the entries of the plain request.
# ---------------------------------------------------------------------------------------------
_FMT_CONTENT = b"a\nfoo bar foo\nb\n\nc\nfoo\nd\n"


def _rendered_entries(path, pattern, **extra):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    cfg = SearchConfig(query_pattern=pattern, only_matching=True, context=1, **extra)
    matches = RipgrepBackend().search(str(path), pattern, cfg).matches
    return [(m.line_number, m.rg_kind, m.text) for m in matches]


@pytest.mark.parametrize(
    "extra",
    [
        {"field_match_separator": "@"},
        {"field_context_separator": "@"},
        {"field_match_separator": "9", "field_context_separator": "9"},
        {"context_separator": "XX"},
        {"stats": True},
        {"heading": True},
        {"color": "always"},
        {"pretty": True},
        {"line_number": False},
        {"no_filename": True},
        {"hyperlink_format": "default"},
        {"path_separator": "/"},
    ],
    ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()),
)
def test_pinned_output_format_flags_override_conflicting_user_values(tmp_path, extra):
    f = tmp_path / "a.txt"
    f.write_bytes(_FMT_CONTENT)
    baseline = _rendered_entries(f, "foo")
    assert [kind for _, kind, _ in baseline].count("match") == 3
    assert _rendered_entries(f, "foo", **extra) == baseline


# Round 13: flags that change rg's TEXT rendering are ROUTED (the flags are no-ops for the
# structured entries, exactly as in JSON mode), never refused. Golden values below were captured
# from origin/main's own CLI (line number + text; main's columns were all 1, rg's are correct).
_MAIN_ENTRIES = [(1, "class"), (2, "class"), (3, "class"), (3, "class")]
_MAIN_FIXTURE = b"class Foo:\n    def class_x(self): pass\n  # class again class\n"


def _entries_of(proc, fmt):
    import json

    if fmt == "--json":
        return [(m["line_number"], m["text"]) for m in json.loads(proc.stdout)["matches"]]
    rows = [json.loads(x) for x in proc.stdout.decode().splitlines() if x.strip()]
    return [(r["line_number"], r["text"]) for r in rows]


@pytest.mark.parametrize("fmt", ["--json", "--ndjson"])
@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["-a"],
        ["--vimgrep"],
        ["--path-separator", "/"],
        ["--path-separator", "\n"],
        ["-b"],
        ["--passthru"],
        ["-M", "1000"],
        ["--trim"],
    ],
    ids=lambda e: " ".join(e).replace("\n", "NL") or "plain",
)
def test_text_rendering_flags_are_routed_not_refused_and_match_main(tmp_path, fmt, extra):
    import json

    f = tmp_path / "cls.py"
    f.write_bytes(_MAIN_FIXTURE)
    proc = _py_door_bytes(fmt, *extra, "-o", "class", str(f))
    if fmt == "--json" and extra in (["-b"], ["--passthru"], ["-M", "1000"], ["--trim"]):
        # main's CLI guard rejects these for plain --json (unchanged, not ours to change)
        assert proc.returncode == 2
        assert json.loads(proc.stdout)["error"] == "unsupported_flag"
        return
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-300:], proc.stdout[:200])
    assert _entries_of(proc, fmt) == _MAIN_ENTRIES


def test_forged_binary_notice_in_a_replacement_cannot_create_a_match_for_another_file(tmp_path):
    # round 13: a replacement containing LF + `fake: binary file matches (...)` used to be parsed as
    # a notice for a file named `fake`. Structured JSON data cannot be forged that way.
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    forged = 'X\nfake: binary file matches (found "\\0" byte around offset 7)'
    proc, doc = _json_matches("-r", forged, "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    (entry,) = doc["matches"]
    assert entry["file"].endswith("a.txt") and "fake" not in entry["file"]
    assert entry["text"] == forged
    assert doc["total_files"] == 1


def test_a_newline_path_separator_cannot_break_the_framing(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar foo\n")
    proc, doc = _json_matches("--path-separator", "\n", "-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]] == [
        (1, 1, "foo"),
        (2, 5, "foo"),
    ]
    assert all("\n" not in m["file"] for m in doc["matches"])


_JSON_ROUTE_CONTENT = b"foo\nbar\x00baz foo\n"


@pytest.mark.parametrize(
    "flags, expected",
    [
        # -a: binary file searched as text; rg's JSON carries both matches (text may hold NUL)
        (["-a", "-o"], [(1, 1, "foo"), (2, 9, "foo")]),
        (["-a", "-o", "-r", "X"], [(1, 1, "X"), (2, 9, "X")]),
        (["-a", "-r", "X"], [(1, 1, "X"), (2, 9, "bar\x00baz X")]),
    ],
    ids=["a-o", "a-o-r", "a-r"],
)
def test_text_mode_route_follows_rgs_structured_data(tmp_path, flags, expected):
    f = tmp_path / "a.txt"
    f.write_bytes(_JSON_ROUTE_CONTENT)
    proc, doc = _json_matches(*flags, "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]] == expected


_ENTRY_RE = re.compile(rb"(\d+):(\d+):(.*)\Z", re.DOTALL)
_INVERTED_RE = re.compile(rb"(\d+):(.*)\Z", re.DOTALL)
_CONTEXT_RE = re.compile(rb"(\d+)-(?:(\d+)-)?(.*)\Z", re.DOTALL)


def _reference_entries(stdout: bytes, *, null_data: bool, inverted: bool):
    """Parse rg's own plain `-n --column` output into (kind, line, column, text) tuples.

    Why this reference parse is UNAMBIGUOUS here: the fuzz uses benign file names (the path is not
    printed at all: a single FILE operand with `-I`), file content from the alphabet `ab c` plus
    line terminators, and replacements that never start with a digit. So a printed line that starts
    with `<digits>:<digits>:` / `<digits>:` / `<digits>-` is a record header, and every other line
    is a continuation of the previous record's text (a replacement or `-U` text containing LF).
    Under `--null-data` records end in NUL and may contain LF freely.
    """
    entries: list[list] = []
    chunks = stdout.split(b"\0" if null_data else b"\n")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    for chunk in chunks:
        if (m := _CONTEXT_RE.match(chunk)) is not None and not _ENTRY_RE.match(chunk):
            col = int(m.group(2)) if m.group(2) else None  # -v: a matching line as context
            entries.append(["context", int(m.group(1)), col, m.group(3)])
        elif (m := _ENTRY_RE.match(chunk)) is not None:  # benign text never starts with a digit
            entries.append(["match", int(m.group(1)), int(m.group(2)), m.group(3)])
        elif (m := _INVERTED_RE.match(chunk)) is not None:
            # `N:text`: an inverted (-v) line, or a match rg prints without offsets (EOF quirk)
            entries.append(["inverted" if inverted else "match", int(m.group(1)), None, m.group(2)])
        elif entries and not null_data:
            entries[-1][3] += b"\n" + chunk  # continuation line of the previous record
        else:
            raise AssertionError(f"unparseable reference line {chunk!r}")
    return [(k, ln, col, text.decode()) for k, ln, col, text in entries]


_FUZZ_PATTERNS = [
    "a", "ab", "b+", "a|b", "(a)(b)?", "^", "$", r"\bc", "c ?", "(a)|(b)", "(?P<x>a)b?", "zz",
    r"\n", r"a\n", r"\nb", r"a\nb", r"b\n", r"\n\n", r"(a)\n?", r"a\n?b", r"^a", r"a$", r"\nb\n",
    r"(a|b)\n(a|b)", r"b?\n",
]  # fmt: skip
_FUZZ_TEMPLATES = [
    "X", "", "\n", "X\nY", "é", "$1", "${x}", "$$", "$0$0", "LONGLONGLONG", "[$0]", "$1$2",
    "\n\n", "Y\n", "\nY",
]  # fmt: skip


def _fuzz_file(rng, null_data: bool) -> bytes:
    sep = b"\0" if null_data else b"\n"
    lines = []
    for _ in range(rng.randint(1, 6)):
        text = "".join(rng.choice("ab c") for _ in range(rng.randint(0, 7))).encode()
        if rng.random() < 0.15:
            text += b"\r"
        if null_data and rng.random() < 0.3:
            text += b"\n" + "".join(rng.choice("ab c") for _ in range(rng.randint(0, 4))).encode()
        lines.append(text)
    body = sep.join(lines)
    return body + (sep if rng.random() < 0.85 else b"")


def _printer_entries(path, pattern, cfg):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    rows = RipgrepBackend().search(str(path), pattern, cfg).matches
    return [
        (
            m.rg_kind,
            m.line_number,
            (m.submatches[0]["start"] + 1) if m.submatches else None,
            m.text,
        )
        for m in rows
    ]


# 6 chunks x 400 seeded cases = 2400 differential cases (chunked so each test stays ~1-2 minutes)
FUZZ_CHUNKS = 6
FUZZ_CASES = int(os.environ.get("TG_PRINTER_FUZZ_CASES", "400"))


@pytest.mark.parametrize("chunk", range(FUZZ_CHUNKS))
def test_json_printer_matches_rgs_plain_printer_on_a_seeded_differential_fuzz(tmp_path, chunk):
    """The rg-compatible printer (from `--json` data) must equal rg's own plain `-o`/`-r` output.

    Axes: patterns incl. `\\n`, `^`, `$`, empty matches and multi-line classes; replacements that are
    empty, contain/remove LF, are non-ASCII, or use `$1`/`${x}`/`$$`; `-o`, `-r`, `-o -r`; `-U`, `-a`,
    `-C1`, `-v`, `--crlf`, `--null-data`; files with CRLF lines, no final terminator, empty lines.
    A request rg itself rejects (e.g. `\\n` without `-U`) is skipped.
    """
    import random

    rng = random.Random(20261014 + chunk)
    f = tmp_path / "fuzz.txt"
    mismatches: list[str] = []
    seen = {
        "lines_mode": 0,
        "lf_replacement": 0,
        "null_data": 0,
        "crlf_file": 0,
        "context": 0,
        "inverted": 0,
        "empty_output_exit0": 0,
        "no_final_terminator": 0,
        "text_flag": 0,
        "crlf_flag": 0,
        "negated_crlf": 0,
        "negated_multiline": 0,
        "skipped": 0,
        "compared": 0,
    }
    for case in range(FUZZ_CASES):
        null_data = rng.random() < 0.12
        content = _fuzz_file(rng, null_data)
        f.write_bytes(content)
        pattern = rng.choice(_FUZZ_PATTERNS)
        replace_str = rng.choice([None, *_FUZZ_TEMPLATES]) if rng.random() < 0.75 else None
        only = True if replace_str is None else rng.random() < 0.6
        flags = {
            "multiline": rng.random() < 0.5 or "\\n" in pattern,
            "text": rng.random() < 0.15,
            "context": 1 if rng.random() < 0.15 else None,
            "invert_match": rng.random() < 0.08,
            "crlf": rng.random() < 0.1,
            "null_data": null_data,
        }
        if flags["crlf"] and null_data:
            # UNREPRODUCIBLE from rg's JSON data (reported, not guessed): with --crlf AND --null-data
            # rg's plain printer works on per-record blocks (line/column relative to the record)
            # while its JSON merges several records into one block, so the plain layout cannot be
            # derived from the JSON fields. The two flags are fuzzed separately, never together.
            flags["crlf"] = False
        # a flag together with its negation: SearchConfig keeps booleans and the backend emits the
        # positive flag first and the negation after it, so rg (last wins) sees the negation win
        flags["no_crlf"] = rng.random() < 0.1
        flags["no_multiline"] = rng.random() < 0.06
        rg_flags = ["--no-config", "-n", "--column", "-I", "--no-context-separator"]
        if only:
            rg_flags.append("-o")
        if replace_str is not None:
            rg_flags += ["-r", replace_str]
        if flags["multiline"]:
            rg_flags.append("-U")
        if flags["no_multiline"]:
            rg_flags.append("--no-multiline")
        if flags["text"]:
            rg_flags.append("-a")
        if flags["context"]:
            rg_flags += ["-C", "1"]
        if flags["invert_match"]:
            rg_flags.append("-v")
        if flags["crlf"]:
            rg_flags.append("--crlf")
        if flags["no_crlf"]:
            rg_flags.append("--no-crlf")
        if null_data:
            rg_flags.append("--null-data")
        ref = subprocess.run(
            [str(resolve_ripgrep_binary()), *rg_flags, "-e", pattern, str(f)],
            capture_output=True,
            check=False,
        )
        if ref.returncode > 1:
            seen["skipped"] += 1
            continue
        cfg = SearchConfig(
            query_pattern=pattern,
            only_matching=only,
            replace_str=replace_str,
            **{k: v for k, v in flags.items() if v is not None},
        )
        try:
            expected = _reference_entries(
                ref.stdout, null_data=null_data, inverted=flags["invert_match"]
            )
        except AssertionError:
            seen["skipped"] += 1  # a reference line this benign grammar cannot classify
            continue
        got = _printer_entries(f, pattern, cfg)
        seen["compared"] += 1
        seen["lines_mode"] += int(flags["multiline"] and "\\n" in pattern)
        seen["lf_replacement"] += int(replace_str is not None and "\n" in replace_str)
        seen["null_data"] += int(null_data)
        seen["crlf_file"] += int(b"\r" in content)
        seen["context"] += int(bool(flags["context"]))
        seen["inverted"] += int(flags["invert_match"])
        seen["empty_output_exit0"] += int(ref.returncode == 0 and not ref.stdout)
        seen["no_final_terminator"] += int(not content.endswith((b"\n", b"\0")))
        seen["text_flag"] += int(flags["text"])
        seen["crlf_flag"] += int(flags["crlf"] and not flags["no_crlf"])
        seen["negated_crlf"] += int(flags["crlf"] and flags["no_crlf"])
        seen["negated_multiline"] += int(flags["multiline"] and flags["no_multiline"])
        if got != expected:
            mismatches.append(
                f"case {case}: pattern={pattern!r} replace={replace_str!r} only={only} "
                f"flags={flags} content={content!r}\n  rg:      {expected}\n  printer: {got}"
            )
    print(f"FUZZ chunk {chunk} seed {20261014 + chunk}: {seen}")  # visible with -s
    assert seen["compared"] >= 0.7 * FUZZ_CASES, seen
    for axis in ("lines_mode", "lf_replacement", "null_data", "crlf_file", "context", "inverted",
                 "empty_output_exit0", "no_final_terminator", "text_flag", "crlf_flag"):  # fmt: skip
        assert seen[axis] >= min(20, FUZZ_CASES // 40), (
            axis,
            seen,
        )  # positive control: every axis was exercised
    assert not mismatches, f"{len(mismatches)} mismatches of {seen['compared']}:\n" + "\n".join(
        mismatches[:6]
    )


@pytest.mark.parametrize(
    "flags, expected",
    [
        (["-r", "X\nY"], [(1, 3, "a X\nY b X\nY")]),
        (["-o", "-r", "X\nY"], [(1, 3, "X\nY"), (1, 9, "X\nY")]),
    ],
    ids=["r-lf", "o-r-lf"],
)
def test_lf_replacement_route_follows_rgs_structured_data(tmp_path, flags, expected):
    f = tmp_path / "a.txt"
    f.write_bytes(b"a foo b foo\nz\n")
    proc, doc = _json_matches(*flags, "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]] == expected
    # rg's own plain text agrees on the replaced line
    if flags == ["-r", "X\nY"]:
        assert _rg_run("foo", f, *flags).stdout == b"a X\nY b X\nY\n"


def test_stats_flag_with_structured_o_exits_zero_and_tg_owns_the_statistics(tmp_path):
    # round 12 repro: rg's --stats summary went to stdout and was parsed as a record (exit 2).
    # Decision: rg's own summary is suppressed (--no-stats pinned); tg's `--stats` is tg's own.
    f = tmp_path / "a.txt"
    f.write_bytes(b"  foo bar baz qux foo\nz\n")
    proc, doc = _json_matches("--stats", "-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [m["text"] for m in doc["matches"]] == ["foo", "foo"]


def test_field_match_separator_repro_exits_zero(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"  foo bar baz qux foo\nz\n")
    proc, doc = _json_matches("--field-match-separator", "@", "-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"]) for m in doc["matches"]] == [(1, 3), (1, 19)]


@pytest.mark.parametrize(
    "template, expected_text",
    [
        ("X\nY", "  X\nY bar baz qux X\nY"),
        ("X\n\nY", "  X\n\nY bar baz qux X\n\nY"),
    ],
)
def test_multiline_replacement_is_continuation_lines_of_one_record(
    tmp_path, template, expected_text
):
    # round 12: `-r 'X\nY'` makes rg print `<path>NUL1:3:  X` / `Y bar ...` -- the second line
    # has no NUL, so it continues the previous record
    f = tmp_path / "a.txt"
    f.write_bytes(b"  foo bar baz qux foo\nz\n")
    _cfg, matches = _tg_matches(f, "foo", template=template)
    assert [(m.line_number, m.text) for m in matches] == [(1, expected_text)]
    rg_plain = _rg_run("foo", f, "-r", template).stdout
    assert rg_plain == expected_text.encode() + b"\n"  # rg's own plain text agrees


def test_multiline_replacement_next_to_a_context_line(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"before\nfoo\nafter\n")
    cfg_extra = {"context": 1}
    _cfg, matches = _tg_matches(f, "foo", template="X\n\nY", **cfg_extra)
    assert [(m.line_number, m.rg_kind, m.text) for m in matches] == [
        (1, "context", "before"),
        (2, "match", "X\n\nY"),
        (3, "context", "after"),
    ]


def test_null_data_replacement_keeps_lf_content_and_nul_terminated_records(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"a\nfoo\x00b\x00foo\n\x00")
    _cfg, matches = _tg_matches(f, "foo", template="X", null_data=True)
    assert [(m.line_number, m.text) for m in matches] == [(1, "a\nX"), (3, "X\n")]


def test_binary_file_is_reported_from_rgs_json_data(tmp_path):
    # rg's --json reports a file with an embedded NUL as ordinary match records (the `end` message
    # carries `binary_offset`), so there is no notice line to parse -- or to forge
    f = tmp_path / "bin.txt"
    f.write_bytes(b"foo\nbar\x00baz foo\nfoo\n")
    proc, doc = _json_matches("-o", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [(m["line_number"], m["column"], m["text"]) for m in doc["matches"]] == [
        (1, 1, "foo"),
        (2, 9, "foo"),
        (3, 1, "foo"),
    ]


@pytest.mark.parametrize(
    "null_data, content",
    [
        (False, b"x\nfoo\ny\nz\nw\nv\nfoo\n"),  # two context groups: rg would print `--` between
        (True, b"x\x00foo\x00y\x00z\x00w\x00v\x00foo\x00"),
    ],
    ids=["lf", "null-data"],
)
def test_a_file_named_double_dash_parses_as_a_path_never_as_a_separator(
    monkeypatch, tmp_path, null_data, content
):
    # round 11 follow-up: the request passes --no-context-separator, so rg never prints `--` and
    # the parser has no separator case: a path that is literally `--` is just a path
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend

    (tmp_path / "--").write_bytes(content)
    monkeypatch.chdir(tmp_path)
    flags = ["--null-data"] if null_data else []
    term = b"\x00" if null_data else b"\n"
    raw = subprocess.run(
        [
            str(resolve_ripgrep_binary()),
            "--no-config",
            "-n",
            "--column",
            "--with-filename",
            "--null",
            "--no-heading",
            "--no-context-separator",
            "-C1",
            "-o",
            *flags,
            "-e",
            "foo",
            "--",
            "--",
        ],
        capture_output=True,
        check=False,
    ).stdout
    if null_data:  # NUL both ends the path and terminates the record: pairs
        parts = raw.split(b"\x00")
        parts.pop()
        expected = [(p.decode(), b.decode()) for p, b in zip(parts[0::2], parts[1::2], strict=True)]
    else:
        expected = [
            (path.decode(), body.decode())
            for path, _, body in (chunk.partition(b"\x00") for chunk in raw.split(term) if chunk)
        ]
    assert expected and all(path == "--" for path, _ in expected)

    cfg = SearchConfig(query_pattern="foo", only_matching=True, context=1, null_data=null_data)
    got = RipgrepBackend().search("--", "foo", cfg).matches
    assert [m.file for m in got] == ["--"] * len(expected)
    assert [m.rg_kind for m in got] == ["context", "match", "context", "context", "match"]
    assert [m.line_number for m in got] == [1, 2, 3, 6, 7]
    assert [m.text for m in got] == ["x", "foo", "y", "v", "foo"]
    # the match columns are rg's own (0-based start 0 here); context entries carry none
    assert [(m.submatches[0]["start"] if m.submatches else None) for m in got] == [
        None,
        0,
        None,
        None,
        0,
    ]


def test_json_null_data_uses_nul_as_the_record_delimiter(tmp_path):
    # audit (round 9): under --null-data a record ends at NUL; LF is content, not a boundary
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\nbar\x00baz\x00")
    got, doc = _tg_entries(f, "bar|baz", "--null-data")
    assert (
        got
        == _rg_entries(f, "bar|baz", "--null-data", sep=b"\x00")
        == [
            (1, 5, "bar"),
            (2, 1, "baz"),
        ]
    )
    assert doc["total_matches"] == 2
    # a match that spans an embedded LF stays ONE entry
    spanning, _ = _tg_entries(f, r"foo\nbar", "--null-data")
    assert spanning == _rg_entries(f, r"foo\nbar", "--null-data", sep=b"\x00")
    assert spanning == [(1, 1, "foo\nbar")]


def _rg_record_texts(path, pattern, flags, term: bytes):
    out = _rg_run(pattern, path, *flags).stdout
    chunks = out.split(term)
    if chunks and chunks[-1] == b"":
        chunks.pop()
    return [c.decode() for c in chunks]


_NUL = b"\x00"
_RECORD_CASES = [
    # (id, content, pattern, flags) -- each run with --null-data (NUL) and as an LF control
    ("replace-terminated", b"foo\x00", "foo", ["-r", "X"]),
    ("replace-unterminated-lf", b"foo\n", "foo", ["-r", "X"]),
    ("inverted-terminated", b"bar\x00baz\x00", "foo", ["-v", "-o"]),
    ("inverted-unterminated-lf", b"bar\n", "foo", ["-v", "-o"]),
    ("context-terminated", b"before\x00foo\x00after\x00", "foo", ["-r", "X", "-C", "1"]),
    ("context-unterminated-lf", b"a\x00foo\n", "foo", ["-r", "X", "-C", "1"]),
]
_LF_CONTROLS = [
    ("replace", b"foo\n", "foo", ["-r", "X"]),
    ("inverted", b"bar\nbaz\n", "foo", ["-v", "-o"]),
    ("context", b"before\nfoo\nafter\n", "foo", ["-r", "X", "-C", "1"]),
]


@pytest.mark.parametrize(
    "name, content, pattern, flags", _RECORD_CASES, ids=[c[0] for c in _RECORD_CASES]
)
def test_json_null_data_record_terminator_is_stripped_but_lf_content_is_kept(
    tmp_path, name, content, pattern, flags
):
    # round 10: the configured delimiter (NUL) was ignored for replacement/context/inverted
    # records: LF content was deleted and the NUL terminator leaked into the text
    f = tmp_path / "a.txt"
    f.write_bytes(content)
    expected = _rg_record_texts(f, pattern, ["--null-data", *flags], _NUL)
    proc, doc = _json_matches("--null-data", *flags, pattern, str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [m["text"] for m in doc["matches"]] == expected, (name, expected)


@pytest.mark.parametrize(
    "name, content, pattern, flags", _LF_CONTROLS, ids=[c[0] for c in _LF_CONTROLS]
)
def test_json_lf_records_control(tmp_path, name, content, pattern, flags):
    f = tmp_path / "a.txt"
    f.write_bytes(content)
    expected = _rg_record_texts(f, pattern, flags, b"\n")
    proc, doc = _json_matches(*flags, pattern, str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    assert [m["text"] for m in doc["matches"]] == expected


def test_json_context_records_are_marked_context_and_carry_no_column(tmp_path):
    # audit 3: context records must not get match prefixes / columns
    f = tmp_path / "a.txt"
    f.write_bytes(b"before\nfoo\nafter\n")
    proc, doc = _json_matches("-o", "-C", "1", "foo", str(f))
    assert proc.returncode == 0, proc.stderr[-300:]
    shape = [(m["line_number"], m["text"], m.get("kind"), "column" in m) for m in doc["matches"]]
    assert shape == [
        (1, "before", "context", False),
        (2, "foo", None, True),
        (3, "after", "context", False),
    ]
    assert doc["total_matches"] == 1  # context lines are not matches
    assert b"column approximated" not in proc.stderr


def test_engine_diagnostic_is_ascii_on_both_streams(tmp_path):
    # audit 4: `--engine é` echoed the non-ASCII value into stderr
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    engine = "é"
    for extra in ([], ["--json"]):
        proc = _py_door_bytes(*extra, "--engine", engine, "foo", str(f))
        assert proc.returncode == 2, proc.stderr[-300:]
        assert proc.stdout.isascii(), proc.stdout
        assert proc.stderr.isascii(), proc.stderr
        assert b"Traceback" not in proc.stderr


_TEMPLATES = [
    "$digits-${letters}-$1-$2-$$-$0-${1}a-$1a",
    "[$0]",
    "$2$1",
    "$$",
    "$" + chr(0xE9) + "bar-$" + chr(0x661),
]


@pytest.mark.parametrize("template", _TEMPLATES)
def test_replacement_templates_are_expanded_by_rg_not_python(tmp_path, template):
    # the `$N` / `${name}` / `$$` semantics belong to rg; compare with rg byte for byte
    f = tmp_path / "a.txt"
    f.write_bytes(b"abc123\n")
    pattern = "(?P<letters>[a-z]+)(?P<digits>[0-9]+)"
    got = _same_as_rg_or_raised(f, pattern, ["-r", template], template=template)
    assert got != "raised"


def test_only_matching_and_replace_are_unsupported_on_non_rg_engines():
    from tensor_grep.backends.cpu_backend import CPUBackend
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend, _pattern_semantics_flags
    from tensor_grep.backends.rust_backend import RustCoreBackend
    from tensor_grep.core.pipeline import _enforce_semantics_support

    rg = RipgrepBackend()
    for kw in ({"only_matching": True}, {"replace_str": "x"}):
        cfg = SearchConfig(query_pattern="foo", **kw)
        for engine in (RustCoreBackend(), CPUBackend()):
            backend, reason = _enforce_semantics_support(
                engine, "x", cfg, _pattern_semantics_flags(cfg), rg, True
            )
            assert backend is rg and reason == "semantics_require_rg", (kw, type(engine))


def test_without_rg_only_matching_and_replace_keep_main_route():
    # CI regression vs main: with rg absent, `-o`/`-r` were refused. Main served them from the
    # selected engine, so the engine is kept (never an error) when rg is unavailable.
    from tensor_grep.backends.cpu_backend import CPUBackend
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend, _pattern_semantics_flags
    from tensor_grep.backends.rust_backend import RustCoreBackend
    from tensor_grep.core.pipeline import _enforce_semantics_support

    for kw in (
        {"only_matching": True},
        {"replace_str": "x"},
        {"only_matching": True, "replace_str": "x"},
    ):
        cfg = SearchConfig(query_pattern="foo", **kw)
        for engine in (RustCoreBackend(), CPUBackend()):
            backend, _ = _enforce_semantics_support(
                engine, "x", cfg, _pattern_semantics_flags(cfg), RipgrepBackend(), False
            )
            assert backend is engine, (kw, type(engine))


def test_engine_lines_get_mains_o_and_r_post_processing_when_rg_did_not_render_them():
    from tensor_grep.cli.rg_post_process import post_process_matches

    line = MatchLine(line_number=1, text="say hello hello", file="f")
    cfg = SearchConfig(query_pattern="hello", only_matching=True)
    assert [m.text for m in post_process_matches([line], "hello", cfg, True)] == ["hello", "hello"]
    cfg = SearchConfig(query_pattern="(h)ello", replace_str="$1!")
    assert [m.text for m in post_process_matches([line], "(h)ello", cfg, False)] == ["say h! h!"]
    rendered = MatchLine(line_number=1, text="x", file="f", rg_kind="match")
    assert post_process_matches([rendered], "hello", cfg, False) == [rendered]
