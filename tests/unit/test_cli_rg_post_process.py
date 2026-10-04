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


def test_submatch_free_lines_still_use_the_python_regex_path():
    # non-rg engines return no submatches: the old regex path must keep working for them
    from tensor_grep.cli.rg_post_process import only_matching_lines, replace_lines

    cfg = SearchConfig(query_pattern="foo", replace_str="x")
    line = MatchLine(line_number=1, text="foo bar", file="f.txt")
    assert replace_lines([line], "foo", cfg)[0].text == "x bar"
    assert [m.text for m in only_matching_lines([line], "foo", SearchConfig())] == ["foo"]


@pytest.mark.parametrize("template, expected", [("x", "x"), ("[$1]", "[FOO]")])
def test_post_processing_of_rg_matches_never_redecides_smart_case(tmp_path, template, expected):
    # the audit repro at the seam: rg matched FOOX for `foo\S` under -S; extraction and
    # replacement used to re-run the pattern with a strict case resolver and raise
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import main as cli_main

    pattern = r"(foo)\S" if "$1" in template else r"foo\S"
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOOX\n")
    cfg = SearchConfig(query_pattern=pattern, smart_case=True, replace_str=template)
    matches = RipgrepBackend().search(str(f), pattern, cfg).matches
    assert matches and matches[0].submatches

    (replaced,) = cli_main._replace_lines(matches, pattern, cfg)
    assert replaced.text == expected

    only = SearchConfig(query_pattern=pattern, smart_case=True, only_matching=True)
    out = cli_main._only_matching_lines(matches, pattern, only)
    assert [m.text for m in out] == ["FOOX"]
