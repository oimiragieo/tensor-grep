"""One Python parse entry point whose only failure type is ``SyntaxError``.

``ast.parse`` signals "this source cannot be parsed" in more than one way: ``SyntaxError`` for
invalid syntax, but also ``RecursionError`` for a deeply nested expression (a ~20 KB source of
``1+1+1+...`` overflows the parser on CPython 3.12 while sitting far below any byte cap),
``ValueError`` for source containing NUL bytes on older interpreters and ``MemoryError`` for
pathological input. Every caller that handles "unparseable file" with ``except SyntaxError`` would
otherwise crash on the other three, so the conversion happens once, here. The original exception
is kept as ``__cause__`` so a coverage gap can name the real cause.

Imports nothing from ``tensor_grep`` so both ``core`` and ``cli`` can use it.
"""

from __future__ import annotations

import ast


def parse_python(source: str, filename: str = "<unknown>") -> ast.Module:
    """``ast.parse`` with every parse failure surfaced as ``SyntaxError`` (cause preserved)."""
    try:
        return ast.parse(source, filename=filename)
    except (RecursionError, ValueError, MemoryError) as exc:
        raise SyntaxError(f"unparseable Python source ({type(exc).__name__})") from exc
