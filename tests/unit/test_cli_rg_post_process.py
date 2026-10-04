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
    from tensor_grep.cli.rg_post_process import only_matching_lines, replace_lines

    line = MatchLine(line_number=1, text="foo bar", file="f.txt")
    with pytest.raises(BackendExecutionError):
        replace_lines([line], "foo", SearchConfig(query_pattern="foo", replace_str="x"))
    with pytest.raises(BackendExecutionError):
        only_matching_lines([line], "foo", SearchConfig(only_matching=True))


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
    from tensor_grep.cli import rg_post_process as cli_main

    pattern = r"(foo)\S" if "$1" in template else r"foo\S"
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOOX\n")
    cfg = SearchConfig(query_pattern=pattern, smart_case=True, replace_str=template)
    matches = RipgrepBackend().search(str(f), pattern, cfg).matches
    assert matches and matches[0].submatches

    (replaced,) = cli_main.replace_lines(matches, pattern, cfg)
    assert replaced.text == expected

    only = SearchConfig(query_pattern=pattern, smart_case=True, only_matching=True)
    out = cli_main.only_matching_lines(matches, pattern, only)
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
    from tensor_grep.cli import rg_post_process as cli_main

    f = tmp_path / "a.txt"
    f.write_bytes(content)
    cfg = SearchConfig(
        query_pattern=pattern, smart_case=True, replace_str=template, only_matching=True
    )
    matches = RipgrepBackend().search(str(f), pattern, cfg).matches
    assert matches and matches[0].submatches
    return f, [m.text for m in cli_main.only_matching_lines(matches, pattern, cfg)]


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
    """Search with rg, then run the project's own -o/-r post-processing. Returns (cfg, matches)."""
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import rg_post_process

    cfg = SearchConfig(
        query_pattern=pattern,
        smart_case=smart,
        fixed_strings=fixed,
        replace_str=template,
        only_matching=only,
        **extra,
    )
    matches = RipgrepBackend().search(str(path), pattern, cfg).matches
    if only:
        matches = rg_post_process.only_matching_lines(matches, pattern, cfg)
    elif template is not None:
        matches = rg_post_process.replace_lines(matches, pattern, cfg)
    return cfg, matches


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
    from tensor_grep.cli import rg_post_process as cli_main
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
        got = [m.text.encode() for m in cli_main.only_matching_lines(direct, "ab", cfg)]
    except BackendExecutionError:
        got = "raised"
    assert got == "raised" or got == expected, got
    # and through the pipeline the request is routed to rg: exact
    pipe = Pipeline(force_cpu=True, config=cfg)
    assert type(pipe.backend).__name__ == "RipgrepBackend"
    matches = pipe.backend.search(str(f), "ab", cfg).matches
    assert [m.text.encode() for m in cli_main.only_matching_lines(matches, "ab", cfg)] == expected


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
    # an invalid-UTF-8 context line cannot be reproduced byte-exactly through str
    f = _file(tmp_path, b"\xff\nfoo\n")
    for extra in ([], ["--json"]):
        proc = _py_door(*extra, "-o", "-C", "1", "foo", f)
        assert proc.returncode == 2, (proc.returncode, proc.stderr[-300:])
        assert "Traceback" not in proc.stderr
        assert "rg" in (proc.stderr + proc.stdout)


def test_valid_o_with_context_and_inverted_searches_work_end_to_end(tmp_path):
    f = _file(tmp_path, b"before\nfoo\nafter\n")
    ctx = _py_door("-o", "-C", "1", "foo", f)
    assert ctx.returncode == 0, ctx.stderr[-300:]
    assert ctx.stdout.split() == ["before", "foo", "after"]
    g = _file(tmp_path, b"bar\n")
    inv = _py_door("-v", "-o", "foo", g)
    assert inv.returncode == 0, inv.stderr[-300:]
    assert inv.stdout.split() == ["bar"]


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
