"""`except BaseException` is a blind spot of the broad-handler census; this closes it.

`tests/unit/test_handler_dispositions.py` (and the ceiling in `test_silent_failure_hardening.py`)
count only a bare `except:` and `except Exception`. A handler for `BaseException` also catches
`KeyboardInterrupt` and `SystemExit`, so one that does NOT re-raise can swallow a user abort or an
interpreter exit -- and the census could not see it. The 12 that exist today were reviewed on
2026-10-02 and fall into two shapes:

* cleanup-then-re-raise (7): the last statement is a bare/explicit `raise`. These are not swallows
  and need no entry.
* deliberate non-re-raise (5): listed in `_REVIEWED_NON_RERAISING` with the reason.

Rather than add 12 ledger records (7 of them are cleanup that re-raises), the gate is: every
`except BaseException` in `src/tensor_grep` must END IN A `raise`, or be on the reviewed list. A new
swallowing one fails this test until someone reads it and writes the reason down.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

PY_SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

# (module relative to src/tensor_grep, enclosing function, index within it) -> why it may not re-raise
_REVIEWED_NON_RERAISING: dict[tuple[str, str, int], str] = {
    ("cli/mcp_server.py", "_log_tool_exception", 0): (
        "strictly non-throwing stderr diagnostics (documented in the docstring): formatting the "
        "traceback of an ALREADY-CAUGHT tool error must never raise into the MCP transport"
    ),
    ("cli/mcp_server.py", "_log_tool_exception", 1): (
        "same helper: str(exc) of a hostile exception may itself raise; falls back to '<unprintable>'"
    ),
    ("cli/mcp_server.py", "_log_tool_exception", 2): (
        "same helper: outermost guard so a failing stderr write/flush cannot escape the logger"
    ),
    ("cli/mcp_server.py", "_safe_exception_class_name", 0): (
        "type introspection hardened against hostile exceptions (spoofed metaclass / __class__): any "
        "failure degrades to the fixed string 'InternalError' and never raises into the caller"
    ),
    ("core/reranker.py", "_run_late_rerank", 0): (
        "worker-thread capture: the exception is appended to rerank_error and RE-RAISED on the "
        "caller thread unless it is the recoverable LateRerankUnavailableError (so a KeyboardInterrupt "
        "or SystemExit still propagates -- see the comment above the thread in the caller)"
    ),
}


def _catches_base_exception(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if isinstance(caught, ast.Name):
        return caught.id == "BaseException"
    if isinstance(caught, ast.Tuple):
        return any(
            isinstance(item, ast.Name) and item.id == "BaseException" for item in caught.elts
        )
    return False


def _enclosing_function(
    tree: ast.AST, handler: ast.ExceptHandler
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= handler.lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def _non_reraising_base_exception_handlers(source: str, module: str) -> list[tuple[str, str, int]]:
    """(module, enclosing function, index) of each `except BaseException` that does NOT end in a
    `raise` -- i.e. can swallow KeyboardInterrupt / SystemExit. Index counts ALL BaseException
    handlers in that function, in source order, so it stays stable when a re-raising one is added."""

    tree = ast.parse(source)
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handlers.extend(h for h in node.handlers if _catches_base_exception(h))
    counter: collections.Counter[str] = collections.Counter()
    flagged: list[tuple[str, str, int]] = []
    for handler in sorted(handlers, key=lambda h: h.lineno):
        enclosing = _enclosing_function(tree, handler)
        symbol = enclosing.name if enclosing is not None else "<module>"
        index = counter[symbol]
        counter[symbol] += 1
        if not (handler.body and isinstance(handler.body[-1], ast.Raise)):
            flagged.append((module, symbol, index))
    return flagged


def _scan_tree() -> set[tuple[str, str, int]]:
    found: set[tuple[str, str, int]] = set()
    for path in sorted(PY_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = path.relative_to(PY_SRC).as_posix()
        found.update(
            _non_reraising_base_exception_handlers(path.read_text(encoding="utf-8"), module)
        )
    return found


def test_every_non_reraising_base_exception_handler_is_reviewed() -> None:
    unreviewed = _scan_tree() - set(_REVIEWED_NON_RERAISING)
    assert not unreviewed, (
        "an `except BaseException` that does not end in `raise` can swallow KeyboardInterrupt / "
        f"SystemExit. Read it, then either re-raise or add it to _REVIEWED_NON_RERAISING with the "
        f"reason: {sorted(unreviewed)}"
    )


def test_the_reviewed_list_has_no_stale_entries() -> None:
    # An entry whose handler was removed (or now re-raises) must be retired, or the list silently
    # rots into a general exemption.
    stale = set(_REVIEWED_NON_RERAISING) - _scan_tree()
    assert not stale, f"reviewed entries with no matching non-re-raising handler: {sorted(stale)}"


def test_every_reviewed_entry_states_a_reason() -> None:
    blank = [key for key, reason in _REVIEWED_NON_RERAISING.items() if len(reason.strip()) < 30]
    assert not blank, f"reviewed entries without a real reason: {blank}"


# --- perturbation arms: the scanner must be able to fail ------------------------------------------


def test_arm_a_swallowing_base_exception_handler_is_flagged() -> None:
    source = "def f():\n    try:\n        g()\n    except BaseException:\n        pass\n"
    assert _non_reraising_base_exception_handlers(source, "m.py") == [("m.py", "f", 0)]


def test_arm_a_tuple_handler_containing_base_exception_is_flagged() -> None:
    source = "def f():\n    try:\n        g()\n    except (OSError, BaseException):\n        pass\n"
    assert _non_reraising_base_exception_handlers(source, "m.py") == [("m.py", "f", 0)]


def test_arm_cleanup_then_reraise_is_not_flagged() -> None:
    # CONTROL: the shape that makes up 7 of the 12 must stay quiet.
    source = "def f():\n    try:\n        g()\n    except BaseException:\n        cleanup()\n        raise\n"
    assert _non_reraising_base_exception_handlers(source, "m.py") == []


def test_arm_plain_exception_handler_is_out_of_scope() -> None:
    # CONTROL: `except Exception` belongs to the other census, not this one.
    source = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    assert _non_reraising_base_exception_handlers(source, "m.py") == []


def test_arm_index_is_stable_when_a_reraising_handler_is_added_first() -> None:
    source = (
        "def f():\n"
        "    try:\n        a()\n    except BaseException:\n        raise\n"
        "    try:\n        b()\n    except BaseException:\n        pass\n"
    )
    # the swallowing handler is the SECOND BaseException handler in f -> index 1
    assert _non_reraising_base_exception_handlers(source, "m.py") == [("m.py", "f", 1)]
