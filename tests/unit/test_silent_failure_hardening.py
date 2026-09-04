"""A whole-population RATCHET over every broad ``except Exception:`` / bare ``except:`` handler
under ``src/tensor_grep/`` (H6-followup audit, 2026-08-20).

WHY THIS EXISTS AND HOW IT DIFFERS FROM ``test_silent_loss_census_ratchet.py``. That file
ratchets one *subclass* of this defect: an accumulating filesystem-walk loop whose broad
handler drops an entry silently. This file ratchets the *superset*: every broad exception
handler anywhere in the package. ``_EXCLUDED_MODULES`` below started as a live carve-out for
modules a concurrent audit owned; the W1 campaign (docs/plans/2026-08-20-worldclass-closeout-
plan.md) retired every entry in four serialized slices and the set is now EMPTY -- kept as an
append point rather than deleted, per that plan's note in the block below.

THE AUDIT (full classification table in ``docs/audits/2026-08-20-handler-dispositions.json``,
not restated here). Every broad handler in scope was read in its enclosing function, not just
grepped, and classified into exactly one of:

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

# W1 excluded-handler audit (docs/plans/2026-08-20-worldclass-closeout-plan.md, W1). All
# modules this census once excluded have now been read and dispositioned in
# `docs/audits/2026-08-20-handler-dispositions.json`; this set is retained EMPTY (rather than
# deleted) so a future split/relocation cannot silently reopen an audited module without a
# reviewer noticing the pattern. Adding a module back here requires the same disposition-ledger
# treatment the four waves below used, not a bare re-exclusion.
_EXCLUDED_MODULES = frozenset({
    # W1-a (2026-08-20) RETIRED the four `cli/mcp_*` exclusions -- `cli/mcp_server.py`,
    # `cli/mcp_rewrite_tools.py`, `cli/mcp_audit_tools.py`, `cli/mcp_symbol_tools.py`. All 57
    # of their broad handlers were read in their enclosing functions and dispositioned in
    # `docs/audits/2026-08-20-handler-dispositions.json` (55 INTENTIONAL-BOUNDARY, each with a
    # behavioural fail-closed arm in `tests/unit/test_w1a_mcp_handler_fail_closed.py`;
    # 2 SILENT-SWALLOW, both hardened, RED arms in
    # `tests/unit/test_w1a_mcp_silent_swallow_fixes.py`). This retirement is an AUDIT, not a
    # ceiling bump to absorb a `git mv`.
    # W1-b (2026-08-20) RETIRED the four remaining split-floor exclusions --
    # `cli/doctor_report.py`, `cli/native_frontdoor.py`, `cli/windows_launcher.py`,
    # `cli/ast_scan.py`. All 24 of their broad handlers (23 originally audited + 1 new
    # `except Exception` added by this PR's own A3 round-1 MEDIUM hardening of
    # `_install_release_native_frontdoor`'s checksum-fetch call, itself dispositioned
    # LOGGED-DEGRADE) were read in their enclosing functions and dispositioned in
    # `docs/audits/2026-08-20-handler-dispositions.json` (14 INTENTIONAL-BOUNDARY, 9
    # LOGGED-DEGRADE, 1 SILENT-SWALLOW hardened with a RED-2 receipt in
    # `tests/unit/test_w1b_cli_handler_fail_closed.py`). This retirement is an AUDIT, not a
    # ceiling bump to absorb a `git mv`.
    # W1-c (2026-08-20) RETIRED the final exclusion -- `cli/main.py`. All 46 of its broad
    # handlers were read in their enclosing functions and dispositioned in
    # `docs/audits/2026-08-20-handler-dispositions.json` (12 not-provably-disclosing at
    # audit time, all 12 classified INTENTIONAL-BOUNDARY -- 5 are the daemon-fast-path
    # `_maybe_*_via_running_daemon` helpers whose fail-open `None` return always falls
    # through to an independently-correct cold path; the remaining 7 are best-effort
    # display/advisory/heuristic-confirmation fallbacks). Zero handlers needed to CHANGE
    # category, but the version-lookup pair (`_read_project_version_fallback` /
    # `_cli_package_version`) needed DISCLOSURE hardening: A3 round-1 (codex REVISE,
    # 2026-08-20) found their placeholder fallback fed `tg scan --sarif`'s tool provenance
    # indistinguishably from a real version, so a double metadata failure produced
    # normal-looking SARIF output with zero disclosed degradation. Fixed by a
    # `_VERSION_UNAVAILABLE_SENTINEL` plus a `run.properties.tensorGrepVersionUnavailable`
    # SARIF flag, RED-2'd against the real `tg scan --sarif` surface in
    # `tests/unit/test_w1c_sarif_version_disclosure.py`. This retirement is an AUDIT, not
    # a ceiling bump to absorb a `git mv` -- `_EXCLUDED_MODULES` is now empty and every
    # broad handler under `src/tensor_grep` is in-census.
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
#
# W1-b ceiling arithmetic (this PR; base is W1-a's own committed ceiling, this branch's parent
# commit -- re-derived via `python scripts/handler_census.py --include-excluded --by-slice`
# immediately before this commit, not arithmetic-forwarded):
#     base (this branch's parent, W1-a)                          196
#   + cli/doctor_report.py + native_frontdoor.py + windows_launcher.py + ast_scan.py  23
#   ------------------------------------------------------------
#                                                               219
# The one hardened SILENT-SWALLOW site (_doctor_ast_cache_status) is still `except Exception`,
# hardened by disclosure + fail-safe default rather than type-narrowing, so it does not reduce
# this count either.
#
# A3 round-1 correction (2026-08-20, codex REVISE on #1068): fixing the HIGH finding on
# `_restart_session_daemon_after_upgrade` reused the existing `except Exception as exc:` sites
# (no new handler), but fixing the MEDIUM finding on `_install_release_native_frontdoor` added
# ONE new broad handler -- wrapping the checksum-fetch call so an injected/future-raising
# `_fetch_native_frontdoor_checksums` still produces a disclosed refusal instead of an unwrapped
# exception. Re-derived via `python scripts/handler_census.py --include-excluded --by-slice`
# immediately before this commit: W1-b handlers=24 (was 23), dispositioned 14/9/1 in
# `docs/audits/2026-08-20-handler-dispositions.json` (INTENTIONAL-BOUNDARY/LOGGED-DEGRADE/
# SILENT-SWALLOW). Ceiling raised by exactly that one handler:
#     219 + 1 (native_frontdoor.py checksum-fetch except, LOGGED-DEGRADE)              220
#
# 2026-08-21: +1 for `ast_scan._ruleset_backend_available` (INTENTIONAL-BOUNDARY, record in
# `docs/audits/2026-08-20-handler-dispositions.json`). It answers 'can the advertised built-in
# rulesets actually RUN on this install?' by importing AstGrepWrapperBackend and calling
# is_available(); both the import and the probe fail on a stock install, which is the ORDINARY
# case it exists to detect. It fails CLOSED -- any error reports unavailable, so `tg rulesets`
# warns rather than staying silent -- and it cannot swallow an incomplete RESULT because it
# produces no result: it returns a boolean that only ever ADDS disclosure, never suppresses a
# finding. Raised because the handler is classified, not to make an unreviewed one pass.
#     266 + 1 (ast_scan.py ruleset-backend availability probe, INTENTIONAL-BOUNDARY)   267
# - 2026-09-03 (HANDLER-CENSUS-W2-b): 267 -> 266 (-1: cybert_backend.py deobfuscate_payload narrowed to ValueError, binascii.Error)
# - 2026-09-03 (HANDLER-CENSUS-W2-b): 267 -> 266 (-1: cybert_backend.py deobfuscate_payload narrowed to ValueError, binascii.Error)
# - 2026-09-04 (SEC-007 MCP error sanitization): 266 -> 338 (+72: +39 mcp_server.py, +10 mcp_symbol_tools.py,
#   +19 mcp_audit_tools.py, +4 mcp_rewrite_tools.py). All 72 additions are INTENTIONAL-BOUNDARY handlers
#   providing fail-closed outer error containment across all 58 registered MCP tools and engine helpers,
#   ensuring internal exceptions and path confinement errors are logged server-side to stderr and never leaked on wire.
#   (Command builders _build_rewrite_command and _build_index_search_command require native_binary with no PATH fallback).
TOTAL_BROAD_HANDLERS_CEILING = 338


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
    src/tensor_grep/ (excluding whatever `_EXCLUDED_MODULES` above currently names -- empty
    as of W1's close, see that constant, not a number restated here) must never exceed the
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
