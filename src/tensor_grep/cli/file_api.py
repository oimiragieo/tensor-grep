"""``tg file-api PATH`` -- every signature in one file, no bodies.

WHY. ``tg defs`` answers "where is symbol X" and needs a symbol; ``tg codemap`` needs a
directory. Neither answers the question an agent actually asks first about an unfamiliar
file: *what is in here?* Without that surface, the only way to learn a file's API is to read
the whole file, which is the single largest avoidable token cost in an agent loop.

WHAT MAKES THIS DIFFERENT FROM A PLAIN OUTLINE. An outline that returns zero symbols for a
file it could not parse is indistinguishable from an outline of a file that genuinely has
none, and the agent cannot tell "this file has no API" from "I could not read it". This
surface therefore carries tg's honesty floor: ``result_incomplete`` plus a named
``incomplete_reason`` whenever the symbol population is not a trustworthy answer, so a low or
zero ``symbol_count`` is UNRESOLVED rather than silently reported as proven-absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# A declaration can legitimately wrap across lines (long parameter lists, generics, multiple
# base classes). Bounded so a pathological or minified file cannot turn one signature into a
# whole-file read -- the thing this command exists to avoid. On hitting the bound we emit what
# we have and SAY so, rather than silently truncating.
_MAX_SIGNATURE_LINES = 8

# Characters that end a declaration and open a body across the parser-backed languages.
_DECLARATION_TERMINATORS = (":", "{", "=>", ";")


def _read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _declaration_is_complete(text: str) -> bool:
    """Has this candidate finished being a DECLARATION (body excluded)?

    Two rules, and the interaction between them is where the first version was wrong. The
    text must end with a terminator, AND its parameter list must have closed.

    The trap: in a brace language the declaration ENDS AT the opening `{` -- that brace is
    the start of the body and never closes on the declaration line. An earlier version
    counted `{}` in the balance check, so `pub struct Foo {` was judged unbalanced and the
    scan ran on until it hit the line bound, flattening whole struct and function BODIES into
    the "signature" and flagging every Rust and TypeScript symbol as truncated. Python hid
    this completely, because `:` closes nothing. So: balance only `()` and `[]`, and measure
    it on the text with the trailing terminator removed.
    """
    stripped = text.rstrip()
    for terminator in _DECLARATION_TERMINATORS:
        if stripped.endswith(terminator):
            head = stripped[: -len(terminator)]
            break
    else:
        return False
    depth = 0
    for ch in head:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
    return depth <= 0


def _signature_for(lines: list[str], start_line: int, end_line: int) -> tuple[str, bool]:
    """Return ``(signature_text, truncated)`` for the symbol starting at ``start_line``.

    Lines are 1-based, matching the symbol records. The signature is lifted as TEXT rather
    than recorded as a line range on purpose: a line number is only correct until unrelated
    code above it shifts, and this repo has a receipt for a citation that was re-stamped twice
    and was wrong again the next day. The declaration text stays true as the file moves.
    """
    first = start_line - 1
    if first < 0 or first >= len(lines):
        return "", False

    collected: list[str] = []
    limit = min(first + _MAX_SIGNATURE_LINES, len(lines), end_line)
    for index in range(first, max(limit, first + 1)):
        raw = lines[index]
        collected.append(raw.rstrip())
        joined = " ".join(part.strip() for part in collected)
        if _declaration_is_complete(joined):
            return "\n".join(collected), False
    # Ran out of budget without seeing the declaration close.
    return "\n".join(collected), True


def build_file_api(
    path: Path,
    symbols: list[dict[str, Any]],
    *,
    language: str | None,
    parser_backed: bool,
) -> dict[str, Any]:
    """Assemble the ``file-api`` payload.

    Extracted as a pure function so the honesty fields are reachable from a test without
    building a repo map -- a completeness claim whose only test drives the whole CLI is a
    claim about the CLI, not about this logic.
    """
    lines = _read_lines(path)

    payload: dict[str, Any] = {
        "path": str(path),
        "language": language,
        "symbols": [],
        "symbol_count": 0,
    }

    if lines is None:
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "file_unreadable"
        payload["remediation"] = (
            f"could not read {path}; a zero symbol_count here is UNRESOLVED, not proven absent"
        )
        return payload

    payload["file_lines"] = len(lines)

    truncated_any = False
    for symbol in symbols:
        start_line = symbol.get("start_line", symbol.get("line"))
        end_line = symbol.get("end_line", start_line)
        if not isinstance(start_line, int):
            continue
        if not isinstance(end_line, int) or end_line < start_line:
            end_line = start_line
        signature, truncated = _signature_for(lines, start_line, end_line)
        truncated_any = truncated_any or truncated
        entry: dict[str, Any] = {
            "name": symbol.get("name"),
            "kind": symbol.get("kind"),
            "line": start_line,
            "end_line": end_line,
            "signature": signature,
        }
        if truncated:
            entry["signature_truncated"] = True
        payload["symbols"].append(entry)

    payload["symbol_count"] = len(payload["symbols"])

    # The honesty floor. A language tg cannot parse for symbols yields zero symbols for a file
    # that may be full of them -- that is UNRESOLVED, and saying so is the whole difference
    # between this and an outline that quietly under-reports.
    if not parser_backed:
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "language_not_parser_backed"
        payload["remediation"] = (
            f"{language or 'this file type'} has no symbol parser in tg, so symbol_count "
            f"({payload['symbol_count']}) is UNRESOLVED, not proven complete. "
            "Run `tg ast-info` for the supported languages."
        )
    elif truncated_any:
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "signature_truncated"
        payload["remediation"] = (
            f"at least one declaration did not close within {_MAX_SIGNATURE_LINES} lines and "
            "was cut; those signatures are partial. Open the file at the named line for the "
            "full declaration."
        )

    return payload


def file_api_command(path: str, *, json_output: bool) -> int:
    """CLI body for ``tg file-api``. Returns the process exit code.

    Lives here rather than in ``main.py`` so the entry point stays a Typer shim -- and
    because ``main.py`` is on a shrink-only size ratchet, which is the repo's mechanism for
    forcing exactly this split.
    """
    import json

    import typer

    from tensor_grep.cli.lang_registry import spec_for_path
    from tensor_grep.cli.repo_map import (
        _symbols_for_file,
        _target_language_for_path,
        build_repo_map,
    )

    target = Path(path).expanduser().resolve()
    if target.is_dir():
        typer.echo(
            f"tg file-api requires a file, got a directory: {target}. "
            "Use `tg codemap` for a directory.",
            err=True,
        )
        return 2

    symbols: list[dict[str, Any]] = []
    if target.exists():
        symbols = _symbols_for_file(build_repo_map(target.parent), str(target))

    payload = build_file_api(
        target,
        symbols,
        language=_target_language_for_path(target),
        parser_backed=spec_for_path(target) is not None,
    )

    typer.echo(json.dumps(payload, indent=2) if json_output else render_file_api_text(payload))
    # Three-state exit contract (docs/CONTRACTS.md): 0 complete, 2 incomplete/untrustworthy.
    return 2 if payload.get("result_incomplete") else 0


def render_file_api_text(payload: dict[str, Any]) -> str:
    """Human-readable form. The incompleteness notice is NEVER dropped in text mode -- the
    surface-agreement rule (CONTRACTS.md P3) means text and JSON must tell the same story.
    """
    lines = [f"{payload['path']}  ({payload.get('language') or 'unknown'})"]
    for symbol in payload["symbols"]:
        marker = " [signature truncated]" if symbol.get("signature_truncated") else ""
        parts = [part.strip() for part in str(symbol.get("signature") or "").splitlines()]
        # A wrapped declaration is FLATTENED to one line rather than having its continuation
        # dropped: showing only the head turns `def f(` into a signature that names none of
        # its parameters, which is worse than useless for the agent this surface serves.
        head = " ".join(part for part in parts if part) or (
            f"{symbol.get('kind')} {symbol.get('name')}"
        )
        lines.append(f"{symbol['line']:>6}: {head}{marker}")
    lines.append(f"symbols={payload['symbol_count']}")
    if payload.get("result_incomplete"):
        lines.append(f"INCOMPLETE RESULT ({payload.get('incomplete_reason')})")
        lines.append(str(payload.get("remediation") or ""))
    return "\n".join(lines)
