"""A whole-population RATCHET over every broad ``except Exception:`` / bare ``except:`` handler
under ``src/tensor_grep/`` (H6-followup audit, 2026-08-20).

WHY THIS EXISTS AND HOW IT DIFFERS FROM ``test_silent_loss_census_ratchet.py``. That file
ratchets one *subclass* of this defect: an accumulating filesystem-walk loop whose broad
handler drops an entry silently. This file ratchets the *superset*: every broad exception
handler anywhere in the package (excluding the three modules another concurrent audit owns:
``cli/main.py``, ``cli/repo_map.py``, ``cli/mcp_server.py``), whether or not it sits inside an
accumulating loop.

THE AUDIT (full classification table in the PR body). All 137 broad handlers in scope were
read in their enclosing function, not just grepped, and classified into exactly one of:

  SILENT-SWALLOW    catches, does not disclose the reason, does not re-raise, and returns a
                     normal-looking value the caller cannot distinguish from "nothing to report".
  LOGGED-DEGRADE     catches and records the reason -- a log/print, a `debug_trace`, an
                     `errors[key] = str(exc)` dict entry, a `..._unavailable = str(exc)` field,
                     or an embedded `"error": str(exc)` in the returned payload.
  INTENTIONAL-BOUNDARY  a deliberate best-effort boundary where continuing is correct: a
                     hardware/feature probe (`is_available`, `has_gpu`, `supports_*`) whose
                     contract IS "return False/None on any failure", a process-teardown/cleanup
                     path, a version-display fallback terminating in a safe default, or a
                     documented fail-safe contract (e.g. agent_capsule.py's DAR
                     ``FAIL-SAFE (byte-identical contract): every early return here is ([], {})``
                     docstring, or torch_backend.py's device-id fallback that is immediately
                     followed by ``if not resolved_device_ids: raise BackendExecutionError``).

RESULT: zero SILENT-SWALLOW handlers survived manual review. Several sites this file's own
detector could not prove "logged" from AST shape alone (an f-string built from a variable
rather than a literal keyword, or a `_record_debug_trace(...)` call) were confirmed
LOGGED-DEGRADE by reading the source; a naive keyword-substring detector both over- and
under-counts (the earlier draft of this census matched "exception" inside `Name(id='Exception')`
itself and reported near-100% false "logged", which is exactly the kind of self-lying instrument
this repo's memory warns about -- see MEMORY.md "the instrument fails more than the subject").
No behavior changed in this PR; this is the census plus the ratchet so the NEXT broad handler
added to the tree is not free to regress the population size silently.

W1-d (docs/plans/2026-08-20-worldclass-closeout-plan.md) removed ten modules from
``_EXCLUDED_MODULES``: ``cli/repo_map_lang_js.py`` and ``cli/repo_map_lang_rust.py`` (2 broad
handlers between them, both classified INTENTIONAL-BOUNDARY -- see
``docs/audits/2026-08-20-handler-dispositions.json``), plus eight modules with ZERO broad
handlers each (``cli/_main_binding.py``, ``cli/doctor_payload.py``, ``cli/repo_map.py``,
``cli/repo_map_cache.py``, ``cli/repo_map_lang_java.py``, ``cli/repo_map_lang_python.py``,
``cli/repo_map_output_budget.py``, ``cli/repo_map_regex_fallback.py`` -- confirmed by
``scripts/handler_census.py --include-excluded`` printing 0 for each, a labelled zero with the
parse-succeeded control beside it, not an unreachable scan). Ceiling raised 137 -> 139
(137 + the 2 real handlers; the eight zero-handler modules contribute nothing to the delta).

HOW THE CEILING MOVES. Auditing a currently-uncounted handler and hardening it (narrowing the
exception type, re-raising as BackendExecutionError, or attaching a visible reason) removes it
from ``_iter_broad_handlers``'s count -- lower ``TOTAL_BROAD_HANDLERS_CEILING`` in the same PR.
Adding a NEW broad ``except Exception:``/bare ``except:`` anywhere in scope raises the count and
turns this test red; that is intentional friction, not a bug in the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_SRC = REPO_ROOT / "src" / "tensor_grep"

# The three modules a concurrent audit owns; this census must not collide with edits there.
#
# EXTENDED 2026-08-20 by the `cli/main.py` split (PR for
# docs/design/2026-08-19-split-floor-escape.md): the five modules below are `cli/main.py`,
# relocated. Their 23 broad handlers are byte-identical to handlers that were already outside
# this census yesterday because `cli/main.py` is excluded -- moving a file does not audit it, so
# counting them now would raise the ceiling 137 -> 160 on the strength of a `git mv`, which is
# exactly the "raise the pin to make unreviewed handlers pass" this file's own comment forbids.
# They should be classified when `cli/main.py` itself is, and this whole block retired together.
_EXCLUDED_MODULES = frozenset({
    "cli/main.py",
    # W1-a (2026-08-20) RETIRED the four `cli/mcp_*` exclusions -- `cli/mcp_server.py`,
    # `cli/mcp_rewrite_tools.py`, `cli/mcp_audit_tools.py`, `cli/mcp_symbol_tools.py`. All 57
    # of their broad handlers were read in their enclosing functions and dispositioned in
    # `docs/audits/2026-08-20-handler-dispositions.json` (55 INTENTIONAL-BOUNDARY, each with a
    # behavioural fail-closed arm in `tests/unit/test_w1a_mcp_handler_fail_closed.py`;
    # 2 SILENT-SWALLOW, both hardened, RED arms in
    # `tests/unit/test_w1a_mcp_silent_swallow_fixes.py`). This retirement is an AUDIT, not a
    # ceiling bump to absorb a `git mv`.
    # extracted from cli/main.py, unaudited for the same reason it is
    "cli/ast_scan.py",
    "cli/doctor_report.py",
    "cli/native_frontdoor.py",
    "cli/windows_launcher.py",
})

# Pinned 2026-08-20 by the H6-followup silent-failure audit. See the module docstring: every
# handler counted here was read in context and classified LOGGED-DEGRADE or INTENTIONAL-BOUNDARY.
# Lower this number when a handler is hardened; never raise it to make a new unreviewed handler
# pass -- classify the new site first (see AGENTS.md's verification-oracle family, Form 1: "what
# would this check show if the thing were broken?" -- for THIS check, an unreviewed new broad
# handler is exactly the broken thing it exists to catch).
#
# W1-a ceiling arithmetic (plan W1.3 rule 5: base = the CURRENTLY MERGED ceiling on origin/main,
# re-derived at rebase time -- never arithmetic-forwarded from a stale base):
#     base (origin/main, merged by W1-d)                        139
#   + cli/mcp_server.py                                          35
#   + cli/mcp_symbol_tools.py                                    10
#   + cli/mcp_audit_tools.py                                      8
#   + cli/mcp_rewrite_tools.py                                    4
#   ------------------------------------------------------------
#                                                               196
# The two hardened SILENT-SWALLOW sites are still `except Exception`, so hardening them by
# DISCLOSURE (rather than by narrowing the type) does not reduce the count -- the docstring
# above lists three hardening moves and only the first removes a handler from this population.
TOTAL_BROAD_HANDLERS_CEILING = 196


def _body_records_reason(handler: ast.ExceptHandler) -> bool:
    """True if the handler BODY (never the `except Exception as e:` type/name clause itself,
    which would false-positive on the literal string "Exception") discloses why it failed."""

    body_src = "\n".join(ast.dump(stmt) for stmt in handler.body).lower()
    disclosing_substrings = (
        "log",
        "warn",
        "fallback_reason",
        "print(",
        "click.echo",
        "typer.echo",
        "stderr",
        "'error'",
        '"error"',
        "reason=",
        "'reason'",
        '"reason"',
        "str(exc",
        "unavailable",
        "debug_trace",
        "record(",
    )
    return any(needle in body_src for needle in disclosing_substrings)


def _body_reraises(handler: ast.ExceptHandler) -> bool:
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
    return False


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True  # bare `except:`
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _iter_broad_handlers() -> list[tuple[str, int, bool]]:
    """Returns (relative_posix_path, lineno, is_disclosed) for every broad handler in scope."""

    found: list[tuple[str, int, bool]] = []
    for path in sorted(PY_SRC.rglob("*.py")):
        relative = path.relative_to(PY_SRC).as_posix()
        if relative in _EXCLUDED_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if not _is_broad_handler(handler):
                    continue
                disclosed = _body_records_reason(handler) or _body_reraises(handler)
                found.append((relative, handler.lineno, disclosed))
    return found


def test_broad_exception_handler_population_does_not_regress() -> None:
    """Ratchet: the count of `except Exception:`/bare `except:` handlers under
    src/tensor_grep/ (excluding the 3 modules a concurrent audit owns) must never exceed the
    pinned ceiling. Every handler currently in the population was read in context and
    classified LOGGED-DEGRADE or INTENTIONAL-BOUNDARY (see module docstring) -- a NEW broad
    handler has not had that review, so this test forces it to happen before merge."""

    handlers = _iter_broad_handlers()
    total = len(handlers)
    assert total <= TOTAL_BROAD_HANDLERS_CEILING, (
        f"Broad exception handler population grew to {total} "
        f"(ceiling {TOTAL_BROAD_HANDLERS_CEILING}). A new `except Exception:`/bare `except:` "
        "was added under src/tensor_grep/ without classification. Read it in its enclosing "
        "function: if it discloses the failure (log/errors-dict/debug_trace/returned "
        "error field) or is a documented best-effort boundary, lower this test's blocking "
        "requirement by confirming intent in the PR body and raising the pin with a one-line "
        "reason; if it silently returns a normal-looking value on failure, fix it "
        "(narrow the exception type, re-raise as BackendExecutionError where the fail-closed "
        "contract applies, or attach a visible reason) instead of pinning it."
    )


def test_census_detector_has_a_positive_control() -> None:
    """The detector in this file must be able to see BOTH shapes it discriminates, or a bug in
    it (like the "exception" substring self-match this file's docstring describes) would make
    the ratchet above pass vacuously forever. Synthesize a tiny module with one silent handler
    and one disclosed handler and confirm the detector tells them apart."""

    silent_src = "def f():\n    try:\n        1 / 0\n    except Exception:\n        return None\n"
    disclosed_src = (
        "def g():\n"
        "    try:\n"
        "        1 / 0\n"
        "    except Exception as exc:\n"
        "        return {'error': str(exc)}\n"
    )
    bare_reraise_src = (
        "def h():\n    try:\n        1 / 0\n    except:\n        raise RuntimeError('x')\n"
    )

    for src, expect_disclosed in (
        (silent_src, False),
        (disclosed_src, True),
        (bare_reraise_src, True),
    ):
        tree = ast.parse(src)
        handlers = [
            handler
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            for handler in node.handlers
            if _is_broad_handler(handler)
        ]
        assert len(handlers) == 1, f"positive-control fixture did not yield 1 handler: {src!r}"
        disclosed = _body_records_reason(handlers[0]) or _body_reraises(handlers[0])
        assert disclosed is expect_disclosed, (
            f"detector misclassified fixture (expected disclosed={expect_disclosed}): {src!r}"
        )
