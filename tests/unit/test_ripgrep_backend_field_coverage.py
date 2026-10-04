"""RipgrepBackend._build_cmd must forward (or explicitly gap) every SearchConfig field.

Modelled on tests/unit/test_native_delegation_field_coverage.py.
"""

import ast
import dataclasses
import inspect
import textwrap
from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.ripgrep_backend import RipgrepBackend, _pattern_semantics_flags
from tensor_grep.core.config import SearchConfig

_RG_BACKEND_KNOWN_GAP_FIELDS = frozenset({
    # non-rg engines
    "ast",
    "ast_prefer_native",
    "ast_selector",
    "ast_stdin",
    "ast_stdin_input",
    "ast_strictness",
    "lang",
    "ltl",
    "nlp_threshold",
    "use_jit",
    # routing / telemetry
    "force_cpu",
    "format_type",
    "gpu_device_ids",
    "input_total_bytes",
    "json_mode",
    "query_pattern",
    "rank_bm25",
    "semantic_rank",
    # handled by the CLI layer before the backend
    "generate",
    "type_list",
    "pcre2_version",
    "quiet",
    # irrelevant to rg --json parsing
    "pretty",
    "hostname_bin",
    "hyperlink_format",
    "line_number_explicit",
})


def _forwarded() -> set[str]:
    # `_pattern_semantics_flags` holds the case/fixed/engine/-w/-x reads shared with the
    # binary-file match check; it is part of what `_build_cmd` forwards.
    attrs: set[str] = set()
    for fn in (RipgrepBackend._build_cmd, _pattern_semantics_flags):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        attrs |= {
            n.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "config"
        }
    return attrs


def _cmd(**kw) -> list[str]:
    backend = RipgrepBackend()
    with patch.object(backend, "_get_binary_name", return_value="rg"):
        return backend._build_cmd(
            file_path="a.txt", pattern="foo", config=SearchConfig(**kw), json_mode=True
        )


def test_every_searchconfig_field_forwarded_or_gapped():
    fields = {f.name for f in dataclasses.fields(SearchConfig)}
    uncovered = sorted(fields - _forwarded() - _RG_BACKEND_KNOWN_GAP_FIELDS)
    assert not uncovered, f"classify {uncovered}: forward to rg in _build_cmd or add to KNOWN_GAP"


def test_known_gap_has_no_stale_entries():
    fields = {f.name for f in dataclasses.fields(SearchConfig)}
    assert not (_RG_BACKEND_KNOWN_GAP_FIELDS - fields)
    assert not (_RG_BACKEND_KNOWN_GAP_FIELDS & _forwarded())


@pytest.mark.parametrize(
    "kw, expected",
    [
        ({"smart_case": True}, ["-S"]),
        ({"stop_on_nonmatch": True}, ["--stop-on-nonmatch"]),
        ({"null_data": True}, ["--null-data"]),
        ({"engine": "pcre2"}, ["--engine", "pcre2"]),
        ({"engine": "auto"}, ["--engine", "auto"]),
        ({"dfa_size_limit": "10M"}, ["--dfa-size-limit", "10M"]),
        ({"regex_size_limit": "20M"}, ["--regex-size-limit", "20M"]),
    ],
)
def test_flag_forwarded(kw, expected):
    cmd = _cmd(**kw)
    i = cmd.index(expected[0])
    assert cmd[i : i + len(expected)] == expected


def test_default_engine_and_explicit_case_flags_unchanged():
    assert "--engine" not in _cmd()
    assert "-S" not in _cmd(smart_case=True, ignore_case=True)  # explicit -i wins
    assert "-S" not in _cmd(smart_case=True, case_sensitive=True)  # explicit -s wins


def test_unknown_engine_fails_closed():
    with pytest.raises(BackendExecutionError):
        _cmd(engine="bogus")
