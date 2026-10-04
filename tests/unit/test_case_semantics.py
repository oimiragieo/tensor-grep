"""Behavioural pins for the shared case-precedence resolver at its non-rg call sites."""

import re

from tensor_grep.core.case_semantics import case_regex_flags
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine


def _line(text: str = "FOO") -> MatchLine:
    return MatchLine(line_number=1, text=text, file="f.txt")


def test_case_regex_flags_table():
    assert case_regex_flags(None, "foo") == 0
    assert case_regex_flags(SearchConfig(ignore_case=True), "Foo") == re.IGNORECASE
    assert case_regex_flags(SearchConfig(smart_case=True), "foo") == re.IGNORECASE
    assert case_regex_flags(SearchConfig(smart_case=True), "Foo") == 0
    assert case_regex_flags(SearchConfig(smart_case=True, case_sensitive=True), "foo") == 0
    assert case_regex_flags(SearchConfig(ignore_case=True, case_sensitive=True), "foo") == 0


# Removed deliberately (round 6): the `-o` / `--replace` post-processors no longer decide case
# or evaluate the pattern in Python at all -- rg produces that output (see
# tests/unit/test_cli_rg_post_process.py, which compares against rg byte for byte).


def test_torch_and_cudf_backends_call_the_resolver():
    # Both need a GPU stack to RUN a search; assert the case decision is delegated by AST so a
    # re-inlined expression cannot slip back in (the repo-wide ratchet also scans them).
    import ast
    import inspect

    from tensor_grep.backends import cudf_backend, torch_backend

    for module, names in (
        (torch_backend, {"effective_ignore_case"}),
        (cudf_backend, {"case_regex_flags"}),
    ):
        called = {
            n.func.id
            for n in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert names <= called, (module.__name__, names - called)
