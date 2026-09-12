"""`tg file-api` -- every signature in one file, no bodies.

The value of this surface is entirely in what it lets an agent NOT read, so the tests that
matter are the ones proving it never reports a confident empty: a zero `symbol_count` must be
distinguishable from "I could not parse this", or the command is just a quieter way to be
wrong.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.file_api import (
    _MAX_SIGNATURE_LINES,
    build_file_api,
    render_file_api_text,
)

_SYMBOL = {"name": "alpha", "kind": "function", "line": 1, "start_line": 1, "end_line": 3}


def _write(tmp_path: Path, name: str, body: str) -> Path:
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


def test_signature_is_lifted_without_the_body(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "m.py",
        "def alpha(a: int, b: str = 'x') -> bool:\n    secret = 1\n    return True\n",
    )
    payload = build_file_api(target, [_SYMBOL], language="python", parser_backed=True)

    assert payload["symbol_count"] == 1
    signature = payload["symbols"][0]["signature"]
    assert signature == "def alpha(a: int, b: str = 'x') -> bool:"
    # The point of the command: the body is not in the payload at all.
    assert "secret" not in json_blob(payload)
    assert not payload.get("result_incomplete")


def json_blob(payload: dict) -> str:
    import json

    return json.dumps(payload)


def test_a_wrapped_declaration_is_captured_whole(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "m.py",
        "def alpha(\n    a: int,\n    b: str,\n) -> bool:\n    return True\n",
    )
    symbol = {**_SYMBOL, "end_line": 5}
    payload = build_file_api(target, [symbol], language="python", parser_backed=True)

    signature = payload["symbols"][0]["signature"]
    assert "a: int" in signature and "b: str" in signature and "-> bool:" in signature
    assert "return True" not in signature


def test_a_wrapped_declaration_survives_the_text_renderer(tmp_path: Path) -> None:
    """Regression: the renderer originally printed only the FIRST line of a signature, so a
    wrapped `def alpha(` was displayed naming none of its parameters -- strictly worse than
    useless. Text and JSON owe the same story (docs/CONTRACTS.md P3).
    """
    target = _write(
        tmp_path,
        "m.py",
        "def alpha(\n    a: int,\n    b: str,\n) -> bool:\n    return True\n",
    )
    symbol = {**_SYMBOL, "end_line": 5}
    payload = build_file_api(target, [symbol], language="python", parser_backed=True)

    rendered = render_file_api_text(payload)
    assert "a: int" in rendered
    assert "b: str" in rendered


def test_a_language_with_no_parser_reports_unresolved_not_empty(tmp_path: Path) -> None:
    """THE honesty floor. Zero symbols from an unparseable language is UNRESOLVED; reporting
    it as a clean empty is the silent under-report this repo's contract exists to prevent.
    """
    target = _write(tmp_path, "notes.md", "# heading\n\nprose\n")
    payload = build_file_api(target, [], language=None, parser_backed=False)

    assert payload["symbol_count"] == 0
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "language_not_parser_backed"
    assert "UNRESOLVED" in payload["remediation"]
    assert "INCOMPLETE RESULT" in render_file_api_text(payload)


def test_a_parseable_file_with_genuinely_no_symbols_is_complete(tmp_path: Path) -> None:
    """MUTATION CONTROL for the test above. If `result_incomplete` were stamped on every
    zero-symbol result, the honesty check would pass vacuously and the flag would carry no
    information. An empty-but-PARSEABLE file is a complete answer of "nothing here".
    """
    target = _write(tmp_path, "empty.py", "# no symbols, but tg can parse this\n")
    payload = build_file_api(target, [], language="python", parser_backed=True)

    assert payload["symbol_count"] == 0
    assert not payload.get("result_incomplete"), (
        "a parseable file with no symbols is a COMPLETE answer; flagging it would make "
        "result_incomplete mean nothing"
    )


def test_an_unreadable_file_reports_unresolved(tmp_path: Path) -> None:
    missing = tmp_path / "gone.py"
    payload = build_file_api(missing, [], language="python", parser_backed=True)

    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "file_unreadable"


def test_a_runaway_declaration_is_bounded_and_says_so(tmp_path: Path) -> None:
    """A pathological or minified declaration must not turn this command into a whole-file
    read -- the exact cost it exists to avoid. Bounded, and the truncation is DISCLOSED.
    """
    body = (
        "def alpha(\n" + "".join(f"    p{n}: int,\n" for n in range(40)) + ") -> None:\n    pass\n"
    )
    target = _write(tmp_path, "wide.py", body)
    symbol = {**_SYMBOL, "end_line": 45}
    payload = build_file_api(target, [symbol], language="python", parser_backed=True)

    entry = payload["symbols"][0]
    assert entry["signature_truncated"] is True
    assert len(entry["signature"].splitlines()) <= _MAX_SIGNATURE_LINES
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "signature_truncated"


def test_a_brace_language_declaration_stops_at_the_opening_brace(tmp_path: Path) -> None:
    """REGRESSION, found by dogfooding a second language.

    The first version balanced `{}` along with `()`, so in a brace language -- where the
    declaration ENDS AT the opening `{` and that brace never closes on the declaration line --
    nothing ever looked balanced. The scan ran to the line bound, flattened whole BODIES into
    the "signature", and flagged every Rust and TypeScript symbol `signature_truncated`.

    Python hid it completely: `:` closes nothing, so the balance check was vacuous there and
    all seven original tests passed. One language is not a population.
    """
    target = _write(
        tmp_path,
        "lib.rs",
        "pub struct Config {\n    pub name: String,\n    pub retries: usize,\n}\n",
    )
    symbol = {**_SYMBOL, "end_line": 4}
    payload = build_file_api(target, [symbol], language="rust", parser_backed=True)

    entry = payload["symbols"][0]
    assert entry["signature"] == "pub struct Config {"
    assert "retries" not in entry["signature"], "the body must not leak into the signature"
    assert not entry.get("signature_truncated")
    assert not payload.get("result_incomplete")


def test_a_wrapped_brace_declaration_still_captures_its_parameters(tmp_path: Path) -> None:
    """The brace fix must not regress the wrapped case: a parameter list spanning lines still
    has to be collected before the terminator is accepted.
    """
    target = _write(
        tmp_path,
        "lib.rs",
        "fn build(\n    name: &str,\n    retries: usize,\n) -> Config {\n    todo!()\n}\n",
    )
    symbol = {**_SYMBOL, "end_line": 6}
    payload = build_file_api(target, [symbol], language="rust", parser_backed=True)

    signature = payload["symbols"][0]["signature"]
    assert "name: &str" in signature
    assert "retries: usize" in signature
    assert signature.rstrip().endswith("{")
    assert "todo!()" not in signature


def test_a_typescript_declaration_stops_at_its_brace(tmp_path: Path) -> None:
    target = _write(
        tmp_path,
        "svc.ts",
        "export function total(n: number) {\n  return n * 2;\n}\n",
    )
    symbol = {**_SYMBOL, "end_line": 3}
    payload = build_file_api(target, [symbol], language="typescript", parser_backed=True)

    assert payload["symbols"][0]["signature"] == "export function total(n: number) {"
    assert not payload.get("result_incomplete")
