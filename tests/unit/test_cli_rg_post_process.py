"""`-o` / `--replace` must use rg's authoritative submatch offsets when rg ran the search.

Smart case on `foo\\S` is decided by rg alone, so the pipeline routes it to rg; the Python
post-processing must then slice at rg's offsets instead of re-deciding case (it used to call the
strict resolver and crash with BackendExecutionError for that very pattern).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine

pytestmark = pytest.mark.skipif(resolve_ripgrep_binary() is None, reason="rg not installed")

_SRC = str(Path(__file__).resolve().parents[2] / "src")


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


def test_lines_without_rg_data_are_refused_not_rebuilt_in_python():
    # design: Python never rebuilds rg's -o/-r output; a line with no rg data is refused
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli.rg_post_process import post_process_matches

    line = MatchLine(line_number=1, text="foo bar", file="f.txt")
    with pytest.raises(BackendExecutionError):
        post_process_matches(
            [line], "foo", SearchConfig(query_pattern="foo", replace_str="x"), False
        )
    with pytest.raises(BackendExecutionError):
        post_process_matches([line], "foo", SearchConfig(only_matching=True), True)
    rg_line = MatchLine(line_number=1, text="foo", file="f.txt", rg_kind="match")
    assert post_process_matches([rg_line], "foo", SearchConfig(only_matching=True), True) == [
        rg_line
    ]
    # no -o / -r requested: nothing to guard
    assert post_process_matches([line], "foo", SearchConfig(), False) == [line]


def test_post_process_module_no_longer_evaluates_the_user_pattern():
    import ast
    import inspect

    from tensor_grep.cli import rg_post_process

    tree = ast.parse(inspect.getsource(rg_post_process))
    imported = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    }
    assert "re" not in imported
    assert not any(
        isinstance(n, ast.Attribute) and n.attr in {"compile", "search", "findall", "match"}
        for n in ast.walk(tree)
    )


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


def test_plain_output_parser_handles_hostile_paths_separators_and_inverted_lines():
    from tensor_grep.backends.rg_plain_output import parse_rg_plain_output

    raw = (
        b"dir/a:1:2:b.txt\x001-ctx 5:6:7\n"  # context text that LOOKS like line:col
        b"dir/a:1:2:b.txt\x002:4:ab:7:8\n"  # match text containing more ':' digits
        b"dir/a:1:2:b.txt\x009:1:\n"  # empty text (a zero-width match)
    )
    got = parse_rg_plain_output(raw)
    assert [(m.file, m.line_number, m.rg_kind, m.text) for m in got] == [
        ("dir/a:1:2:b.txt", 1, "context", "ctx 5:6:7"),
        ("dir/a:1:2:b.txt", 2, "match", "ab:7:8"),
        ("dir/a:1:2:b.txt", 9, "match", ""),
    ]
    # a match record with no `<column>:` field is malformed for a non-inverted parse
    from tensor_grep.backends.base import BackendExecutionError

    with pytest.raises(BackendExecutionError):
        parse_rg_plain_output(b"p\x009:\n")
    inv = parse_rg_plain_output(b"p\x001:bar\np\x002-ctx\n", inverted=True)
    assert [(m.line_number, m.rg_kind, m.text, m.submatches) for m in inv] == [
        (1, "inverted", "bar", None),
        (2, "context", "ctx", None),
    ]
    nul = parse_rg_plain_output(b"p\x001:5:foo\nbar\x00", null_data=True)
    assert [(m.text, m.submatches[0]["start"]) for m in nul] == [("foo\nbar", 4)]
    with pytest.raises(BackendExecutionError):
        parse_rg_plain_output(b"p\x001:1:\xff\n")  # not valid UTF-8 -> refused


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
