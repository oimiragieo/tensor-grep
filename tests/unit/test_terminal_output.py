"""Terminal control sequences from matched repository content are inert on TTYs."""

from __future__ import annotations

import io
import os

import pytest

from tensor_grep.cli import ast_workflow_rules, ast_workflows, main
from tensor_grep.cli.terminal_output import sanitize_terminal_controls
from tensor_grep.core.result import MatchLine, SearchResult

ESC = "\x1b"
BEL = "\x07"


def test_removes_terminal_control_sequences_but_keeps_sgr_and_layout() -> None:
    text = f"red {ESC}[31mcolor{ESC}[0m {ESC}[2J{ESC}]0;title{BEL}\tline\n"

    assert sanitize_terminal_controls(text) == f"red {ESC}[31mcolor{ESC}[0m \tline\n"


def test_unterminated_control_sequence_drops_remainder() -> None:
    assert sanitize_terminal_controls(f"visible{ESC}]52;c;payload") == "visible"


@pytest.mark.parametrize("code", range(0x80, 0xA0))
def test_surrogateescaped_c1_control_bytes_are_removed(code: int) -> None:
    assert sanitize_terminal_controls(chr(0xDC00 + code)) == ""


@pytest.mark.parametrize(
    "introducer,terminator",
    [(0x90, 0x9C), (0x98, 0x9C), (0x9D, 0x07), (0x9D, 0x9C), (0x9E, 0x9C), (0x9F, 0x9C)],
)
def test_surrogateescaped_c1_string_controls_drop_their_payload(
    introducer: int, terminator: int
) -> None:
    text = f"left{chr(0xDC00 + introducer)}payload{ESC}[2J{chr(terminator)}right"

    assert sanitize_terminal_controls(text) == "leftright"


def test_surrogateescaped_c1_sgr_is_preserved() -> None:
    text = f"{chr(0xDC9B)}31mred{chr(0xDC9B)}0m"

    assert sanitize_terminal_controls(text) == text


@pytest.mark.parametrize("terminator", ["\x9c", chr(0xDC9C)])
def test_escape_string_controls_accept_c1_string_terminator(terminator: str) -> None:
    text = f"left{ESC}]52;c;payload{ESC}[2J{terminator}right"

    assert sanitize_terminal_controls(text) == "leftright"


class _TtyStdout(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_writer_sanitizes_untrusted_output(monkeypatch) -> None:
    stream = _TtyStdout()
    monkeypatch.setattr(main.sys, "stdout", stream)

    main._safe_stdout_line(f"needle{ESC}]52;c;payload{BEL}")

    assert stream.getvalue() == "needle\n"


def test_interactive_ast_writer_uses_tty_sanitizer(monkeypatch) -> None:
    stream = _TtyStdout()
    monkeypatch.setattr(main.sys, "stdout", stream)

    ast_workflow_rules._safe_stdout_line(f"File: source.py{ESC}[2J")

    assert stream.getvalue() == "File: source.py\n"


def test_ast_writer_preserves_non_utf_redirected_stream_encoding(monkeypatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(main.sys, "stdout", stream)

    ast_workflow_rules._safe_stdout_line("café")
    stream.flush()

    assert stream.buffer.getvalue() == "café\n".encode("cp1252")


def test_ast_writer_keeps_utf8_fallback_for_unencodable_redirected_text(monkeypatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(main.sys, "stdout", stream)

    ast_workflow_rules._safe_stdout_line("snowman ☃")

    assert stream.buffer.getvalue() == "snowman ☃\n".encode()


def test_piped_writer_preserves_exact_output(monkeypatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(main.sys, "stdout", stream)
    text = f"needle{ESC}[2J"

    main._safe_stdout_line(text)

    assert stream.getvalue() == text + "\n"


def test_nul_path_list_sanitizes_tty_output(monkeypatch) -> None:
    stream = _TtyStdout()
    stream.buffer = io.BytesIO()
    monkeypatch.setattr(main.sys, "stdout", stream)

    main._write_path_list([f"needle{ESC}]52;c;payload{BEL}"], use_nul=True)

    assert stream.getvalue() == ""
    assert stream.buffer.getvalue() == b"needle\0"


def test_nul_path_list_preserves_non_tty_bytes(monkeypatch) -> None:
    stream = io.StringIO()
    stream.buffer = io.BytesIO()
    monkeypatch.setattr(main.sys, "stdout", stream)
    path = f"needle{ESC}[2J"

    main._write_path_list([path], use_nul=True)

    assert stream.getvalue() == ""
    assert stream.buffer.getvalue() == path.encode() + b"\0"


def test_nul_path_list_strips_surrogateescaped_c1_from_tty(monkeypatch) -> None:
    stream = _TtyStdout()
    stream.buffer = io.BytesIO()
    monkeypatch.setattr(main.sys, "stdout", stream)
    path = f"before{chr(0xDC9B)}2Jafter"

    main._write_path_list([path], use_nul=True)

    assert stream.buffer.getvalue() == os.fsencode("beforeafter") + b"\0"


def test_interactive_ast_command_sanitizes_displayed_path_and_match(monkeypatch) -> None:
    path = f"sample{ESC}[2J.py"
    text = f"needle{ESC}]52;c;payload{BEL}"

    class AstGrepWrapperBackend:
        def search_many(self, file_paths, pattern, config=None) -> SearchResult:
            _ = file_paths, pattern, config
            return SearchResult(
                matches=[MatchLine(line_number=1, text=text, file=path)],
                matched_file_paths=[path],
                total_files=1,
                total_matches=1,
            )

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda config, pattern: AstGrepWrapperBackend(),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    stream = _TtyStdout()
    monkeypatch.setattr(main.sys, "stdout", stream)

    exit_code = ast_workflows.run_command(
        pattern="needle", path=".", lang="python", rewrite="replace", interactive=True
    )

    assert exit_code == 0
    assert "File: sample.py" in stream.getvalue()
    assert "needle" in stream.getvalue()
    assert ESC not in stream.getvalue()
    assert BEL not in stream.getvalue()
