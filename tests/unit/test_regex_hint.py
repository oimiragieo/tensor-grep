"""Hint argument handling must use the same grammar as search routing."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tensor_grep.cli.regex_hint import emit_literal_pattern_hint


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["code{", "src"], ["code{"]),
        (["--", "code[", "src"], ["code["]),
        (["-iecode[", "src"], ["code["]),
        (["--regexp=code[", "src"], ["code["]),
        (["-F", "--no-fixed-strings", "code[", "src"], ["code["]),
        (["--no-fixed-strings", "-F", "code[", "src"], []),
        (["--replace", "[", "plain", "src"], ["plain"]),
        (["--files", "code["], []),
        (["-f", "patterns[.txt", "src"], []),
        (["-P", "code{", "src"], []),
        (["--engine", "auto", "code{", "src"], []),
        (["--auto-hybrid-regex", "code{", "src"], []),
    ],
)
def test_hint_respects_pattern_sources_and_engine(args, expected, monkeypatch):
    monkeypatch.delenv("RIPGREP_CONFIG_PATH", raising=False)
    parser = Mock(return_value=None)
    monkeypatch.setitem(
        sys.modules, "tensor_grep.rust_core", SimpleNamespace(_literal_pattern_hint=parser)
    )
    emit_literal_pattern_hint(args, 2)
    assert [call.args[0] for call in parser.call_args_list] == expected


@pytest.mark.parametrize("exit_code", [0, 1, 124])
def test_hint_does_not_parse_success_no_match_or_timeout(exit_code, monkeypatch):
    parser = Mock(side_effect=AssertionError("diagnostic parser must not run"))
    monkeypatch.setitem(
        sys.modules, "tensor_grep.rust_core", SimpleNamespace(_literal_pattern_hint=parser)
    )
    emit_literal_pattern_hint(["[", "src"], exit_code)
    parser.assert_not_called()


def test_config_and_no_config_override(monkeypatch, capsys):
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", "engine-config")
    parser = Mock(return_value="literal hint")
    monkeypatch.setitem(
        sys.modules, "tensor_grep.rust_core", SimpleNamespace(_literal_pattern_hint=parser)
    )
    emit_literal_pattern_hint(["[", "src"], 2)
    parser.assert_not_called()
    emit_literal_pattern_hint(["--no-config", "[", "src"], 2)
    assert capsys.readouterr().err == "literal hint\n"


def test_unavailable_extension_keeps_original_failure(monkeypatch, capsys):
    monkeypatch.delenv("RIPGREP_CONFIG_PATH", raising=False)
    monkeypatch.setitem(sys.modules, "tensor_grep.rust_core", None)
    emit_literal_pattern_hint(["[", "src"], 2)
    assert capsys.readouterr().err == ""


def test_hint_is_bounded_and_emitted_once(monkeypatch, capsys):
    monkeypatch.delenv("RIPGREP_CONFIG_PATH", raising=False)
    parser = Mock(return_value=None)
    monkeypatch.setitem(
        sys.modules, "tensor_grep.rust_core", SimpleNamespace(_literal_pattern_hint=parser)
    )
    args = [arg for _ in range(100) for arg in ("-e", "[")]
    emit_literal_pattern_hint(args, 2)
    assert parser.call_count == 64
    parser.reset_mock()
    parser.return_value = "literal hint"
    emit_literal_pattern_hint(args, 2)
    assert parser.call_count == 1
    assert capsys.readouterr().err == "literal hint\n"


def test_unencodable_pattern_does_not_replace_search_error(monkeypatch, capsys):
    monkeypatch.delenv("RIPGREP_CONFIG_PATH", raising=False)
    parser = Mock(side_effect=UnicodeError("bad argument encoding"))
    monkeypatch.setitem(
        sys.modules, "tensor_grep.rust_core", SimpleNamespace(_literal_pattern_hint=parser)
    )
    emit_literal_pattern_hint(["[", "src"], 2)
    assert capsys.readouterr().err == ""
