from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click as _std_click
import typer
from typer.core import TyperGroup

if TYPE_CHECKING:
    import typer._click as click
else:
    try:
        import typer._click as click
    except ImportError:
        import click

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import compact_payloads as _compact_payloads
from tensor_grep.cli import terminal_output as _terminal_output
from tensor_grep.cli._main_binding import _self
from tensor_grep.cli.completeness_output import _output_limit_note, coverage_gap_banner
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.cli.symbol_output import defined_without_results
from tensor_grep.core import result as _JSON_OUTPUT_VERSION_CONTRACT
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.

# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig

# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.

# --- from cli/ast_scan.py ----------------------------------------------------

# --- from cli/doctor_report.py -----------------------------------------------

# --- from cli/doctor_payload.py ----------------------------------------------

# --- from cli/native_frontdoor.py --------------------------------------------

# --- from cli/windows_launcher.py --------------------------------------------

# backlog #1 : kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 3
_DOCTOR_LSP_SCHEMA_VERSION = 2
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig


try:
    from typer._click.exceptions import UsageError as _TyperUsageError

    _USAGE_ERRORS: tuple[type[Any], ...] = (
        click.exceptions.UsageError,
        _std_click.exceptions.UsageError,
        _TyperUsageError,
    )
except ImportError:
    _USAGE_ERRORS = (click.exceptions.UsageError, _std_click.exceptions.UsageError)


class _EvidenceGroup(TyperGroup):
    """Nudge `tg evidence <path>` toward the `emit` subcommand.

    Dogfood trap (v1.61.2): an agent reaches for `tg evidence <PATH> <query>` by
    analogy with `tg defs`/`tg orient` (which take a path directly), but `evidence`
    is a command GROUP whose only action is `emit`. Click's default
    "No such command 'src/...'" is correct (exit 2) but unhelpful -- when the unknown
    subcommand looks like a filesystem path, append the concrete fix so the caller
    does not have to re-read `--help`.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except _USAGE_ERRORS as exc:
            token = args[0] if args else ""
            if (
                token
                and not token.startswith("-")
                and ("/" in token or "\\" in token or Path(token).exists())
            ):
                # Re-raise a NEW UsageError with the hint appended -- `UsageError.message` is a
                # Final attribute (cannot be reassigned in place); a fresh error carrying the same
                # `ctx` renders identically (Usage + "Try --help" + Error:) and keeps exit code 2.
                raise type(exc)(
                    f"{exc.format_message()}\n"
                    "Hint: `tg evidence` is a command group; its receipt action is "
                    f"`emit`. Did you mean `tg evidence emit {token}`? "
                    "(run `tg evidence emit --help`)",
                    ctx=exc.ctx,
                ) from exc
            raise


_VERSION_UNAVAILABLE_SENTINEL = "0.0.0-unavailable"


def _read_project_version_fallback() -> str:
    try:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return _VERSION_UNAVAILABLE_SENTINEL


def _cli_package_version() -> str:
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _self._read_project_version_fallback()


_MAX_NATIVE_ASSET_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _version_detail_lines() -> tuple[str, ...]:
    from tensor_grep.cli.version_info import version_detail_lines

    return version_detail_lines()


def _print_version(*, verbose: bool = False) -> None:
    print(f"tensor-grep {_cli_package_version()}")
    if verbose:
        for line in _version_detail_lines():
            print(line)


def _json_output_version() -> int:
    """Wire-schema version for the ``--json`` envelope (DC-001).

    Reads the shipped literal rather than scraping ``rust_core/src/main.rs``:
    that scrape resolved through ``parents[3]`` -- the repo root in a dev
    checkout, but the directory above ``site-packages`` in a wheel, where
    ``rust_core/`` is absent, so it caught ``OSError`` and silently returned a
    hardcoded 1 forever. See ``core.result.JSON_OUTPUT_VERSION``; cross-pinned
    by ``tests/unit/test_json_output_version_pin.py``."""
    return _JSON_OUTPUT_VERSION_CONTRACT.JSON_OUTPUT_VERSION


def _with_schema_version(payload: dict[str, Any], *, version: int | None = None) -> dict[str, Any]:
    stamped = dict(payload)
    resolved_version = stamped.get(
        "version", version if version is not None else _json_output_version()
    )
    stamped.setdefault("version", resolved_version)
    stamped.setdefault("schema_version", resolved_version)
    return stamped


@dataclasses.dataclass(frozen=True)
class _TailExitCodePolicy:
    """One exit-code RULE that `search_command`'s Python TAIL applies -- i.e. code that only
    runs when a request did NOT take the native-delegation exit
    (``sys.exit(_delegate_to_native_tg_search(...))``, above ``_can_delegate_to_native_tg_search``
    in this module).

    Task 22 investigation: PR #868 added a tail-only exit-code rule
    (``gpu_request_unhonoured``) keyed on a `SearchConfig` field (``gpu_device_ids``) that is
    ELIGIBLE for delegation rather than refused, so a request that triggers the rule can also
    take the OTHER route -- where whether the native binary applies the identical rule is a
    completely separate question this module cannot answer by inspection. This registry makes
    every such rule an explicit, reviewed decision instead of a silent assumption; see
    ``tests/unit/test_search_command_tail_exit_policy_route_parity.py``, which enumerates it and
    fails loudly on an unclassified entry.
    """

    name: str
    """Human-readable identifier for the rule (matches the predicate/variable name in
    `search_command`, e.g. ``"gpu_request_unhonoured"``)."""

    trigger_fields: frozenset[str]
    """`SearchConfig` field name(s) that must be non-default for this rule to ever fire. An
    empty frozenset means the rule can fire on ANY request regardless of flags (e.g. a walk
    error can happen on a plain search too), so route parity is unconditionally required."""

    mirrored_in_native_at: str | None = None
    """A citation into the Rust source (e.g. ``"rust_core/src/main.rs:13064
    walk_was_incomplete()"``) where a human manually verified the native binary applies the SAME
    rule. Exactly one of this and `route_specific_reason` must be set -- never both, never
    neither."""

    route_specific_reason: str | None = None
    """Non-empty iff this rule is knowingly Python-route-only (unverified, or verified NOT to
    hold, on the native binary) -- must name the tracking issue/PR so the gap stays discoverable
    rather than silently assumed. Exactly one of this and `mirrored_in_native_at` must be set."""


def _write_path_list(paths: list[str], *, use_nul: bool) -> None:
    if not paths:
        return
    if use_nul:
        if _terminal_output.stdout_is_tty(sys.stdout):
            paths = [_terminal_output.sanitize_terminal_controls(path) for path in paths]
        payload = b"\x00".join(os.fsencode(path) for path in paths) + b"\x00"
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    _safe_stdout_line("\n".join(paths))


def _path_output_sort_key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _ordered_path_output(paths: list[str], config: SearchConfig) -> list[str]:
    if config.sort_by == "path":
        return sorted(paths, key=_path_output_sort_key)
    if config.sort_by_reverse == "path":
        return sorted(paths, key=_path_output_sort_key, reverse=True)
    return paths


def _looks_like_binary_path(path: str) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


def _path_has_hidden_component(path: str) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in Path(path).parts)


def _safe_stdout_line(text: str) -> None:
    _terminal_output.safe_stdout_line(text)


def _stdout_isatty() -> bool:
    return sys.stdout.isatty()


def _deadline_option(help_text: str) -> Any:
    """The `--deadline` option, declared once.

    This flag was declared **20 separate times** in this file, each an identical 10-line
    `typer.Option(None, "--deadline", min=0.1, help=...)` differing only in its help string --
    204 lines of pure duplication in a module the file-size ratchet forbids from growing. That
    duplication is what made adding the flag to a 21st command (`blast-radius-render`, PR #1102)
    impossible: the ratchet was correctly refusing a file whose bulk was copy-paste, and the
    honest fix was to delete the duplication rather than shave the new declaration until it fit.

    `help_text` is a REQUIRED parameter, not a shared default. Each command's wording is
    user-facing and pinned by contract tests; collapsing 12 distinct explanations into one generic
    sentence would be a behaviour change wearing a refactor's name. The duplication worth removing
    was the option MACHINERY -- the wording is not duplication, it is content.

    Keeping `min=0.1` here makes the floor uniform by construction instead of by 20 people
    remembering it.
    """
    return typer.Option(None, "--deadline", min=0.1, help=help_text)


def _cli_deadline_monotonic(deadline_seconds: float | None) -> float | None:
    """Anchor an absolute ``time.monotonic()`` deadline at CLI command entry (closes the #197/#200
    front-door residual). Mirrors ``repo_map._deadline_monotonic_from_seconds``'s formula, but is
    meant to be called from the TOP of a deadline-bearing command body -- before the lazy builder
    import, path/query resolution, GPU-id parsing, and the warm-daemon gate -- so that front-door
    time is budgeted the same way the underlying repo scan already is. Kept as a tiny, import-free
    helper (uses only the module-level ``time`` already imported at the top of this file) so calling
    it early never forces an eager heavy import.

    The remaining process-startup prefix (interpreter boot + Typer/Click argument dispatch) BEFORE
    Python even reaches this line is a separate, irreducible ~100-200ms budget gap that no CLI
    command body can account for -- documented in the ``--deadline`` help text and
    docs/CONTRACTS.md rather than fixed here."""
    if deadline_seconds is None:
        return None
    return _self.time.monotonic() + deadline_seconds


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _format_symbol_location_row(row: dict[str, Any]) -> str:
    file_name = str(row.get("file", "")).strip()
    if not file_name:
        return ""

    line = _positive_int(row.get("line", row.get("start_line")))
    location = file_name if line is None else f"{file_name}:{line}"
    column = _positive_int(row.get("column", row.get("col", row.get("start_column"))))
    if line is not None and column is not None:
        location = f"{location}:{column}"

    details: list[str] = []
    kind = str(row.get("kind", "")).strip()
    name = str(row.get("name", "")).strip()
    text = " ".join(str(row.get("text", "")).strip().split())
    if kind:
        details.append(kind)
    if name:
        details.append(name)
    if text:
        details.append(f"| {text}")
    if not details:
        return location
    return f"{location} {' '.join(details)}"


def _echo_symbol_location_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        rendered = _format_symbol_location_row(row)
        if rendered:
            typer.echo(rendered)


def _apply_defs_class_filter(payload: dict[str, Any], class_filter: str) -> None:
    """Filter ``payload['definitions']`` in place to those whose enclosing class matches
    ``class_filter`` (case-insensitive exact match), disambiguating common method names
    such as ``search`` (audit L3-cli).

    Each definition carries a ``class`` field (enclosing class name, or ``None`` for
    module-level/free functions) populated by ``build_symbol_defs`` in repo_map.py. The
    filter and the requested value are recorded as additive top-level fields so JSON
    consumers can see that a narrowing was applied; the existing keys are left intact.
    """
    target = class_filter.strip().casefold()
    definitions = payload.get("definitions") or []
    filtered = [
        definition
        for definition in definitions
        if str(definition.get("class") or "").casefold() == target
    ]
    payload["definitions"] = filtered
    payload["class_filter"] = class_filter
    payload["class_filter_matched"] = len(filtered)


def _symbol_payload_has_no_results(payload: dict[str, Any], result_key: str) -> bool:
    """Whether a symbol-command payload found nothing for the requested symbol.

    A payload is empty either when the resolver flagged ``no_match`` or when its
    primary result collection is empty. Used to honor rg's no-match exit convention
    for the symbol commands (audit L1).
    """
    if payload.get("no_match"):
        return True
    return not payload.get(result_key)


def _symbol_not_found_claim(payload: dict[str, Any], result_key: str) -> bool:
    """Whether the payload may affirmatively claim the symbol is ABSENT (task 327).

    ``not_found`` is a positive claim -- "we looked and it is not there" -- and rg's exit-1
    convention is built on it. A scan cut short by ``--deadline`` or a ``--max-repo-files`` cap
    never finished looking, so an empty result from it proves nothing. An external dogfood of
    v1.98.27 caught ``tg refs ... --deadline 0.1 --json`` reporting ``files_scanned: 0`` and
    ``not_found: true`` in the same payload: the confident false zero this command's own emitter
    docstring exists to prevent, surviving in the one field the completeness machinery never
    covered.

    The truncation predicate is ``_scan_incomplete`` plus the upstream ``result_incomplete``
    stamp -- the same two facts used by the exit-2 gates. It gets the OUTPUT-cap boundary right:
    an output cap is a complete analysis capped for display, so it must NOT suppress
    ``not_found``.

    Exit codes: ``_scan_incomplete``-true payloads still exit 2 on a branch evaluated before
    ``not_found`` is consulted. A DEFINED symbol with zero references/callers on a complete scan is
    a complete result, not an absent symbol (B-09): ``not_found`` is False and the command exits 0,
    where it used to exit 1 -- a behaviour change for scripts that read exit 1 as "no callers".
    """
    return (
        _symbol_payload_has_no_results(payload, result_key)
        and not _scan_incomplete(payload)
        and not bool(payload.get("result_incomplete"))
        and not defined_without_results(payload, result_key)
    )


_ZERO_CALLERS_CAVEAT = (
    "0 callers in the static call graph does not mean this symbol is dead code. Dynamic "
    "dispatch (getattr / decorators / string-keyed registries), test files, re-exports, and "
    "cross-repo callers can be invisible to the graph. Cross-check with `tg refs` or grep "
    "before treating it as unused."
)


_TRUNCATION_REMEDY = (
    "A zero or small count here is NOT trustworthy. Remedy: scope to a subdirectory, raise "
    "--max-repo-files / --max-callers / --max-files, or warm the index with "
    "`tg session daemon start`."
)


def _truncation_message(what: str) -> str:
    # ASCII-only (no em-dash): the warning prints to Windows consoles where cp1252 mojibakes it.
    return f"INCOMPLETE RESULT: {what}, so callers/definitions may be missing. {_TRUNCATION_REMEDY}"


_DEADLINE_REMEDY = (
    "A zero or small count here is NOT trustworthy. Remedy: raise --deadline, scope to a "
    "subdirectory, or warm the index with `tg session daemon start`. Raising --max-repo-files "
    "does NOT help here -- the scan ran out of TIME, not budget."
)


def _deadline_truncation_message(what: str) -> str:
    # ASCII-only, same reason as _truncation_message.
    return f"INCOMPLETE RESULT: {what}, so callers/definitions may be missing. {_DEADLINE_REMEDY}"


def _scan_truncation_warning(payload: dict[str, Any]) -> str | None:
    """Human warning when a result's SCAN was truncated before covering the project (P0).

    A truncated result that drops project files can return a confident-looking zero (or small
    count) that renders identically to a real one — the single most dangerous output for a
    refactor-safety tool, since it greenlights deleting live code. The payload already knows;
    this projects it into the default output so an incomplete result can never look complete.
    Handles the three SCAN shapes production emits: the repo-scan cap
    (``scan_limit.possibly_truncated`` — callers/refs/impact) and the caller-scan ceiling
    (``caller_scan_limit.possibly_truncated`` — F1: a COMPLETE repo-map whose own internal
    CALLER_SCAN_FILE_CEILING still bounded how many of its files were walked for callers/refs),
    plus the fail-closed tail below. Returns None when the scan finished.

    OUTPUT caps are deliberately NOT here: an ``output_limit`` stamp (``--max-callers``/
    ``--max-files`` pagination, blast-radius's ``callers_truncated``/``files_truncated`` or the
    repo-map's ``possibly_truncated``) is a COMPLETE analysis whose DISPLAY was paginated, so it
    is disclosed by the sibling `_output_limit_note` as an ``OUTPUT LIMITED`` advisory that keeps
    ``result_incomplete`` False and exit 0. Folding it back in here would flip a paginated display
    to exit 2 — the pagination-as-scan-failure conflation the pagination-caveat slice retires.
    """
    for key in ("scan_limit", "caller_scan_limit"):
        limit = payload.get(key)
        if not (isinstance(limit, dict) and limit.get("possibly_truncated")):
            continue
        if key == "caller_scan_limit":
            ceiling = limit.get("ceiling", "?")
            files_total = limit.get("files_total", "?")
            return _truncation_message(
                f"caller-scan bounded to the first {ceiling} of {files_total} mapped files; "
                "narrow the PATH or raise --max-repo-files for full coverage"
            )
        scanned = limit.get("scanned_files", limit.get("emitted_files", "?"))
        cap = limit.get("max_repo_files", limit.get("max_files", "?"))
        return _truncation_message(
            f"the scan stopped at a {cap}-file cap (scanned {scanned}) and dropped project files"
        )
    # THE DEADLINE SHAPE -- a third cause this function could not see, and the largest ABSENT case
    # in the disclosure class. A `--deadline` cutoff sets `partial` / `deadline_limit`, never a
    # `*_limit.possibly_truncated`, so every branch above missed it and this returned None. Meanwhile
    # `_scan_incomplete` DOES fire on `partial`, so the process exited 2 while stdout said nothing.
    # Measured as a paired arm through `blast_radius`, one variable moving:
    #     ARM A  scan_limit cap        exit 2 + "warning: INCOMPLETE RESULT: ..."
    #     ARM B  partial + deadline    exit 2 + nothing at all
    # Exit 2 with silent stdout is worse than a mispositioned warning: a reader who never sees a
    # line has nothing to be late about.
    #
    # Written LAST so it cannot mask a more specific cause -- a payload carrying both a file cap and
    # a deadline still reports the cap, which names the actionable knob.
    #
    # Predicate deliberately MIRRORS `repo_map._scan_did_not_finish` / `_scan_incomplete` rather than
    # adding a fourth private notion of "truncated". Re-deriving truncation narrowly IS this defect
    # class (task 332 swept 3 of 3 readers for exactly this), so a new definition would guarantee
    # the next drift.
    deadline_limit = payload.get("deadline_limit")
    deadline_hit = isinstance(deadline_limit, dict) and deadline_limit.get("deadline_exceeded")
    if deadline_hit or payload.get("partial"):
        scanned = None
        if isinstance(deadline_limit, dict):
            scanned = deadline_limit.get("files_scanned")
            total = deadline_limit.get("files_total")
            if scanned is not None and total is not None:
                return _deadline_truncation_message(
                    f"the --deadline elapsed after {scanned} of {total} files"
                )
        return _deadline_truncation_message("the scan stopped early at its --deadline")
    # FAIL-CLOSED TAIL -- the class fix, of which the deadline branch above is one instance.
    #
    # Two predicates decide two halves of the same contract: `_scan_incomplete` decides the EXIT
    # CODE, this function decides the MESSAGE. Nothing made them agree, so any cause reaching one
    # and not the other exits 2 in silence. That is not hypothetical -- it was true of TWO fields
    # on `origin/main`, and only one of them was the deadline gap this commit set out to close:
    #
    #     scan_limit cap          exit2=True  discloses=True
    #     caller_scan_limit       exit2=True  discloses=True
    #     partial (deadline)      exit2=True  discloses=False   <- the reported gap
    #     caller_scan_truncated   exit2=True  discloses=False   <- found while fixing it
    #
    # Enumerating causes is what produced the gap in the first place (each branch above was added
    # when its cause arrived, and the next cause arrived without one). So the fix is structural:
    # ask the EXIT GATE. If it considers this scan truncated and nothing above described why, say
    # so generically rather than returning None. A vague warning is recoverable; silence beside
    # exit 2 is the failure this whole surface exists to prevent.
    #
    # Deliberately LAST, so every specific message above still wins and keeps naming its knob.
    # This is the floor, not the answer -- a new cause should still get its own branch.
    if _scan_incomplete(payload):
        return coverage_gap_banner(payload) or _truncation_message("the scan did not finish")
    return None


def _scan_incomplete(payload: dict[str, Any]) -> bool:
    """Whether a payload's SCAN (not output) was truncated.

    The shared exit-2 gate for the daemon/render fast-paths (``map``, ``context-render``,
    ``edit-plan``, ``blast-radius-render``, incl. their warm-daemon routes; Cluster B, 2026-07-06)
    and the ``blast-radius`` command. An OUTPUT cap (``output_limit.*`` -- ``--max-callers``,
    ``--max-files``) is a COMPLETE analysis capped only for display and must stay exit 0, so this
    checks ONLY ``scan_limit`` / ``caller_scan_limit`` ``possibly_truncated``, ``partial`` (a
    ``--deadline`` cutoff), and ``caller_scan_truncated`` (the ``CALLER_SCAN_FILE_CEILING``) --
    NEVER ``result_incomplete``, which ``_annotate_result_completeness`` sets only from an actual
    scan truncation or an upstream-stamped incompleteness (that would silently flip an
    output-cap-only invocation to exit 2 and break the output-cap-stays-0 pins).
    """
    for key in ("scan_limit", "caller_scan_limit", "coverage_gap_limit"):
        limit = payload.get(key)
        if isinstance(limit, dict) and limit.get("possibly_truncated"):
            return True
    return bool(payload.get("partial") or payload.get("caller_scan_truncated"))


def _annotate_result_completeness(
    payload: dict[str, Any], *, result_key: str | None = None
) -> tuple[str | None, bool]:
    """Set additive ``result_incomplete`` + ``caveat`` on a symbol payload.

    Returns ``(caveat_text_or_None, is_truncation)``. Two INDEPENDENT facts are composed here,
    never conflated:

    * SCAN incompleteness (``_scan_truncation_warning`` -- a ``--max-repo-files``/caller-scan cap,
      a ``--deadline`` cutoff) or a preexisting upstream-stamped ``result_incomplete=true`` sets
      ``result_incomplete`` and classifies as truncation (exit 2 downstream).
    * OUTPUT pagination (``_output_limit_note`` -- a ``--max-callers``/``--max-files`` display
      cap) is an ``OUTPUT LIMITED`` advisory on a COMPLETE analysis: it joins the caveat but
      never flips ``result_incomplete`` nor the exit code.

    When both facts are present the caveat carries BOTH (a deadline cannot be masked by an output
    cap, and vice versa). Truncation (P0) supersedes the "zero callers != dead code" caveat (P7),
    which applies only to a resolved ``callers`` result and only while the scan is complete and
    no other disclosure exists. Shared by the symbol-command emitter and the blast-radius command
    (which has its own output), and by ``tg map``'s direct route for its exit gate.
    """
    scan_warning = _scan_truncation_warning(payload)
    output_note = _output_limit_note(payload)
    preexisting_incomplete = bool(payload.get("result_incomplete"))
    # `result_incomplete` describes the ANALYSIS/SCAN, never the display: an output-only cap
    # keeps it False so the exit gate (exit 0) and the JSON field tell one truth.
    payload["result_incomplete"] = preexisting_incomplete or scan_warning is not None
    caveat = scan_warning
    if caveat is None and preexisting_incomplete:
        # FAIL-CLOSED: an upstream-stamped incomplete result with no scan shape of its own still
        # owes the reader a warning -- silence beside exit 2 is the failure class this surface
        # exists to prevent (the same reasoning as _emit_symbol_command_result's fallback below).
        reason = payload.get("incomplete_reason")
        caveat = _truncation_message(
            str(reason) if reason else "the result is incomplete and may be missing entries"
        )
    if caveat is not None and output_note is not None:
        caveat = f"{caveat} {output_note}"
    elif output_note is not None:
        caveat = output_note
    if (
        scan_warning is None
        and not preexisting_incomplete
        and result_key == "callers"
        and not payload.get("no_match")
        and not payload.get("callers")
    ):
        # Zero-callers caution (P7) on a COMPLETE scan -- kept even when an output note fired
        # (both are trailing advisories on a complete analysis), suppressed by a real truncation.
        caveat = f"{caveat} {_ZERO_CALLERS_CAVEAT}" if caveat else _ZERO_CALLERS_CAVEAT
    if caveat is not None:
        payload["caveat"] = caveat
    return caveat, scan_warning is not None or preexisting_incomplete


def _completeness_caveat_lines(
    caveat: str | None, *, is_truncation: bool
) -> tuple[str | None, str | None]:
    """Split a completeness caveat into ``(leading_banner, trailing_note)`` for text output.

    An INCOMPLETENESS warning must be read BEFORE the data it qualifies; an advisory note is
    commentary and reads correctly after. A trailing ``[PARTIAL]``-style marker is the easiest
    thing to emit and the most ignored -- a model consuming the text output treats the prefix
    as the document and a trailing line as a footnote, so a truncated caller-set gets trusted
    as exhaustive anyway (the exact wrong-refactor risk the exit-2 gate exists to prevent).
    The zero-callers caveat (P7) is the opposite shape: the result IS complete, the note only
    warns against over-reading it, so it stays trailing. That asymmetry is the point.

    Shared by all text emitters; JSON carries the same fact in fields, where position is not
    meaningful. Derive the current emitter population from call sites rather than prose.
    """
    if caveat is None:
        return None, None
    if is_truncation:
        return f"warning: {caveat}", None
    return None, f"note: {caveat}"


def _emit_scan_incompleteness_banner(payload: dict[str, Any]) -> bool:
    """Print the leading disclosure for a payload whose SCAN did not finish. Returns whether it did.

    THE INVARIANT: a command that exits ``2`` must have SAID something on stdout. Thirteen of the
    fourteen ``_scan_incomplete`` gates in this module raised ``typer.Exit(2)`` over text output
    that read exactly like a complete result -- ``codemap`` was the only one that disclosed. An
    agent branching on the exit code was fine; every human, and every agent reading the text, was
    told a truncated answer was the whole answer.

    TEXT PATH ONLY. Call this inside the ``else`` of an ``if json_output`` block, never beside the
    ``json.dumps``: a stray line on the JSON route breaks ``json.loads`` on stdout, which is the
    failure this whole surface exists to prevent. JSON carries the same fact as ``result_incomplete``
    / ``caveat`` fields, where position and prose are irrelevant.

    LEADING, by the task #329 rule -- a disclosure must be read BEFORE the data it qualifies. Emits
    nothing for a complete payload, so existing output stays byte-identical.

    The message comes from the shared ``_scan_truncation_warning`` so these commands cannot drift
    into their own vocabulary or their own idea of which knob to suggest.
    """
    if not _scan_incomplete(payload):
        return False
    caveat = _scan_truncation_warning(payload)
    leading, _ = _completeness_caveat_lines(caveat, is_truncation=True)
    if leading is None:
        return False
    typer.echo(leading)
    return True


def _attach_symbol_omissions(
    payload: dict[str, Any],
    *,
    command_name: str,
    path: str,
    symbol: str,
    max_tests: int | None,
    max_tokens: int | None,
    primary_field: str,
) -> None:
    """Stamp an additive, agent-facing ``omissions`` envelope (design #96 item 3).

    Mirrors ``agent_capsule.py``'s ``omissions:{token_budget, omitted_section_count,
    omitted_sections[], follow_up_reads[]}`` shape (``agent_capsule.py:2262-2267``) as a sibling
    key on defs/refs/callers/impact, summarizing what the tests-cap (``output_limit``, set by
    ``repo_map._apply_symbol_field_output_limit``) and the token budget (``token_budget``, set by
    ``repo_map._apply_symbol_token_budget``) trimmed. Unlike the capsule's follow-up reads (which
    point at a DIFFERENT command to read more source), the follow-up pointer here is
    SELF-referential: re-run this SAME command with a bigger ``--max-tests``/``--max-tokens``,
    since there is nothing else to point at. ALWAYS present (even with nothing omitted, in which
    case ``omitted_sections``/``follow_up_reads`` are simply empty) so the shape is stable at v1.

    Purely additive/descriptive: never reads or writes ``result_incomplete``/``partial``/
    ``caller_scan_limit``, so it cannot affect the scan-truncation exit-2 contract.
    """
    omitted_sections: list[dict[str, Any]] = []
    retry_argv: list[str] = ["tg", command_name, path, symbol, "--json"]
    retry_needed = False

    output_limit = payload.get("output_limit")
    if isinstance(output_limit, dict) and output_limit.get("tests_truncated"):
        omitted_sections.append({
            "section": "tests",
            "omitted_count": int(output_limit.get("omitted_tests", 0)),
            "reason": "max-tests cap",
        })
        retry_argv.extend(["--max-tests", str(output_limit.get("total_tests", 0))])
        retry_needed = True

    token_budget = payload.get("token_budget")
    if isinstance(token_budget, dict) and token_budget.get("primary_truncated"):
        omitted_sections.append({
            "section": primary_field,
            "omitted_count": int(token_budget.get("primary_omitted", 0)),
            "reason": "max-tokens budget",
        })
        retry_argv.extend(["--max-tokens", "0"])
        retry_needed = True

    follow_up_reads: list[dict[str, Any]] = []
    if retry_needed:
        follow_up_reads.append({
            "file": None,
            "symbol": symbol,
            "role": "retry-bigger-budget",
            "command": subprocess.list2cmdline(retry_argv),
            "argv": retry_argv,
        })

    payload["omissions"] = {
        "token_budget": max_tokens,
        "max_tests": max_tests,
        "omitted_section_count": len(omitted_sections),
        "omitted_sections": omitted_sections,
        "follow_up_reads": follow_up_reads,
    }


def _emit_symbol_command_result(
    payload: dict[str, Any],
    *,
    result_key: str,
    json_output: bool,
    emit_text: Callable[[dict[str, Any]], None],
    keep_candidate_symbols: bool = False,
    detail: str = "standard",
) -> None:
    """Emit a symbol-command payload and honor the no-match exit convention (L1).

    When the symbol resolved to zero results we annotate the payload with
    ``not_found: true`` (additive JSON field) and exit 1, mirroring how ``rg`` exits 1
    on no match, while still emitting a valid JSON object for ``--json`` consumers.

    Two additive completeness signals are surfaced in BOTH the JSON and the default text
    output so an incomplete answer can never look complete (validated on real repos):

    * ``result_incomplete`` (+ a loud ``caveat``) when the scan was truncated before covering
      the project — the dangerous "confident false zero" (P0).
    * for ``callers``, the "zero callers != dead code" caveat (P7) when a symbol resolved but
      has no callers on a complete scan — dynamic dispatch / tests / re-exports stay invisible.

    The truncation warning supersedes the generic caveat (incompleteness is the real story), and
    leads the text output rather than trailing it (see ``_completeness_caveat_lines``).
    """
    not_found = _symbol_not_found_claim(payload, result_key)
    payload["not_found"] = not_found
    from tensor_grep.cli.symbol_suggestions import suggestions_for_payload

    suggest = not_found or (result_key == "definitions" and not payload.get(result_key))
    suggestions = suggestions_for_payload(payload) if suggest else []
    payload["suggestions"] = suggestions
    if not keep_candidate_symbols:
        payload.pop("candidate_symbols", None)
    caveat, is_truncation = _annotate_result_completeness(payload, result_key=result_key)
    # FAIL-CLOSED COUPLING between the message and the exit code below.
    #
    # The exit gate fires on `partial` / `result_incomplete`. The caveat above is computed by
    # `_annotate_result_completeness` from a DIFFERENT set of signals, so a payload that arrives
    # with `result_incomplete` ALREADY SET by the command itself produced no caveat at all.
    # Measured on `TENSOR_GREP_MAX_PARSE_BYTES=10 tg imports <file>`: exit 2, stdout
    # `imports=0 resolved=0 external=0 unresolved=0`, and ZERO bytes of stderr, while the `--json`
    # arm carried `result_incomplete: true` plus the real reason. A confident false zero with no
    # signal on the text path is precisely the failure this surface exists to prevent, and it was
    # invisible to the never-silent ratchet because that keys on `_scan_incomplete`, which
    # deliberately does not read `result_incomplete`.
    #
    # Deriving the fallback from the SAME predicate as the exit makes the two impossible to
    # disagree by construction, rather than by two lists staying in sync. Exit behaviour is
    # untouched -- this only guarantees an exit is never silent.
    if caveat is None and (payload.get("partial") or payload.get("result_incomplete")):
        reason = payload.get("incomplete_reason")
        caveat = _truncation_message(
            str(reason) if reason else "the result is incomplete and may be missing entries"
        )
        is_truncation = True
    if json_output:
        typer.echo(_compact_payloads.dump_cmd(payload, detail, result_key))
    else:
        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)
        if leading is not None:
            typer.echo(leading)
        emit_text(payload)
        if suggestions:
            typer.echo(f"Did you mean: {', '.join(suggestions)}?")
        if trailing is not None:
            typer.echo(trailing)
    # Exit-code contract (council-verified B, 2026-07-05): a deadline/scan-truncated result is INCOMPLETE
    # and must NOT read as complete (0) nor as a genuine not-found (1). Exit 2 -- REGARDLESS of whether
    # results were found -- mirrors `tg search`'s result_incomplete convention (see the search command) so
    # an agent sees ONE contract across every command, never trusts a truncated caller-set as exhaustive
    # (a wrong blast-radius/refactor decision), and can distinguish "ran out of budget/cap, retry with
    # more" from "genuinely absent". A found-but-truncated result exiting 0 was tried (#399) and overturned
    # by a UNANIMOUS design council: truncation trumps found. The "every big-repo query exits 2" friction
    # is a DEFAULT-CAP miscalibration (512, entangled with the slow TS caller re-parse), to fix separately
    # -- NOT a reason to fork the contract in two. `--deadline` sets `partial`; a --max-repo-files cap sets
    # `result_incomplete`; either -> exit 2.
    if payload.get("partial") or payload.get("result_incomplete"):
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


def _maybe_swap_reversed_positionals(
    *,
    path: str,
    value: str,
    command_name: str,
    value_label: str,
) -> tuple[str, str]:
    """Auto-correct a reversed ``<VALUE> <PATH>`` invocation.

    Agents (and grep muscle memory, and older docs) frequently call these
    commands as ``tg <command> <SYMBOL> <PATH>`` instead of the canonical
    path-first ``tg <command> <PATH> <SYMBOL>``. When that happens the first
    positional is not an existing path but the second one is, which previously
    produced an opaque ``Path not found: <SYMBOL>`` error. Detect that exact
    case and transparently swap, emitting a hint so the caller can learn the
    canonical order. The swap only fires when the first arg is definitively not
    a path AND the second arg definitively is, so a legitimate ``<PATH>
    <VALUE>`` call (where the value happens to share a name with a real path)
    is never disturbed.
    """
    if Path(path).expanduser().exists():
        return path, value
    if not Path(value).expanduser().exists():
        return path, value
    typer.echo(
        f"Warning: '{path}' is not an existing path but '{value}' is; "
        f"interpreting as `tg {command_name} <PATH> <{value_label}>` "
        f"(path={value!r}, {value_label.lower()}={path!r}). "
        f"Pass <PATH> before <{value_label}> to silence this hint.",
        err=True,
    )
    return value, path


def _maybe_swap_reversed_session_path(
    *,
    session_id: str,
    path: str,
    command_name: str,
) -> tuple[str, str]:
    """Auto-correct ``tg session <command> <PATH> <SESSION_ID> ...``.

    Session commands are the one user-facing surface where the stable session
    identifier must lead the path. Agents commonly transpose this after using
    the path-first top-level commands. Only swap when the first positional is
    an existing path and the second positional resolves to an existing session
    under that path, so ordinary session-first calls remain untouched.
    """
    if not Path(session_id).expanduser().exists():
        return session_id, path
    if Path(path).expanduser().exists():
        return session_id, path
    try:
        from tensor_grep.cli.session_store import get_session

        get_session(path, session_id)
    except Exception:
        return session_id, path
    typer.echo(
        f"Warning: '{session_id}' is an existing path and '{path}' is an existing "
        f"session for it; interpreting as `tg session {command_name} <SESSION_ID> "
        f"<PATH> <QUERY>`. Pass <SESSION_ID> before <PATH> to silence this hint.",
        err=True,
    )
    return path, session_id


def _resolve_path_and_symbol(
    *,
    path: str,
    symbol_arg: str | None,
    symbol_option: str | None,
    command_name: str,
) -> tuple[str, str]:
    if symbol_arg is not None and symbol_option is not None:
        raise ValueError("Use either positional SYMBOL or --symbol, not both.")
    if symbol_option is not None:
        typer.echo(
            "Warning: --symbol is deprecated for "
            f"tg {command_name}; pass SYMBOL as a positional instead "
            f"(shorthand `tg {command_name} <SYMBOL>` with PATH defaulting to '.', or "
            f"`tg {command_name} <PATH> <SYMBOL>` to scope a large repo). "
            "The --symbol form remains accepted for backward compatibility.",
            err=True,
        )
        return path, symbol_option
    if symbol_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=symbol_arg,
            command_name=command_name,
            value_label="SYMBOL",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing symbol. Use positional SYMBOL or --symbol SYMBOL.")


def _resolve_path_and_query(
    *,
    path: str,
    query_arg: str | None,
    query_option: str | None,
    command_name: str,
    echo_warning: bool = True,
) -> tuple[str, str]:
    """``echo_warning=False`` lets a --json caller route the deprecation warning into its
    payload (``_query_deprecation_warning``) instead of stderr."""
    if query_arg is not None and query_option is not None:
        raise ValueError("Use either positional QUERY or --query, not both.")
    if query_option is not None:
        if echo_warning:
            typer.echo(_query_deprecation_warning(command_name), err=True)
        return path, query_option
    if query_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=query_arg,
            command_name=command_name,
            value_label="QUERY",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing query. Use positional QUERY or --query QUERY.")


def _query_deprecation_warning(command_name: str) -> str:
    return (
        "Warning: --query is deprecated for "
        f"tg {command_name}; use a positional QUERY form instead. "
        "The --query form remains accepted during the 1.13.x deprecation cycle "
        "and will not be removed before 1.14.0."
    )


def _mermaid_label(text: str) -> str:
    """Neutralize characters that would break a quoted Mermaid node/edge label."""
    return text.replace("\\", "/").replace('"', "'")


_MERMAID_INCOMPLETE_LABEL_LIMIT = 110


def _mermaid_incomplete_label(banner: str) -> str:
    """The disclosure banner reduced to something a DIAGRAM BOX can carry.

    A node label is read in a picture, not a terminal, so it needs the CAUSE and nothing else:
    the ``warning:`` prefix is a log convention that means nothing inside a box, and the remedy
    sentence is long enough to distort the graph it is warning about. Both stay one line up in
    the ``%%`` comment, which is where a source reader looks and where the full text belongs.

    Bounded deliberately. The cause clause is producer-controlled (a caller-scan message names two
    counts and a path-narrowing hint) and an unbounded label would let the worst-truncated graph
    render worst -- the incompleteness signal degrading exactly when it matters most, which is the
    defect inverted. Yes, this truncates a truncation notice; the ellipsis says so, and the
    untruncated text is one line above.
    """
    cause = " ".join(banner.split())
    for prefix in ("warning: ", "note: "):
        if cause.startswith(prefix):
            cause = cause[len(prefix) :]
            break
    cause = cause.split(", so ", 1)[0].split(". ", 1)[0]
    if len(cause) > _MERMAID_INCOMPLETE_LABEL_LIMIT:
        cause = cause[: _MERMAID_INCOMPLETE_LABEL_LIMIT - 3].rstrip() + "..."
    return cause


def _mermaid_relpath(file_path: str, root: str) -> str:
    """A short forward-slashed path for a Mermaid node (relative to root when it stays inside)."""
    forward = file_path.replace("\\", "/")
    try:
        rel = os.path.relpath(file_path, root).replace("\\", "/")
        if rel and not rel.startswith(".."):
            return rel
    except (ValueError, OSError):
        pass
    return os.path.basename(forward) or forward


def _render_blast_radius_mermaid(payload: dict[str, Any]) -> str:
    """Render a blast-radius payload's exact call sites (``callers[]``) as a Mermaid ``graph TD``.

    Only DIRECT callers are drawn (each unique caller file --> the symbol), because they carry
    exact file+line evidence. The depth-layered ``caller_tree`` has no exact file-to-file edges,
    so inventing them would lie to the reader (the agent-native contract). Output is deterministic
    (sorted nodes) so it is diff-friendly for doc generators.
    """
    symbol = str(payload.get("symbol", "symbol"))
    root = str(payload.get("path") or ".")
    callers = cast(list[dict[str, Any]], payload.get("callers") or [])
    grouped: dict[str, list[int]] = {}
    for caller in callers:
        raw = caller.get("file")
        if not raw:
            continue
        entry = grouped.setdefault(_mermaid_relpath(str(raw), root), [])
        line_no = caller.get("line")
        if isinstance(line_no, int):
            entry.append(line_no)
    lines = ["graph TD"]
    # Task #329's law, crossed to this twin: a truncation disclosure must be read BEFORE the data
    # it qualifies. A `%%` note after the nodes is a footnote -- a reader who has already traced
    # the graph formed the answer several lines ago. `graph TD` is the diagram-type DECLARATION,
    # not payload, so it stays line 1 and the banner becomes the first CONTENT line. (An existing
    # contract test already pins `payload["mermaid"].startswith("graph TD")`, so this is also the
    # only option that does not break a shipped promise.) What keeps the line from reading as
    # mermaid's `%%{...}%%` DIRECTIVE form is the space AFTER `%%`, guaranteed by the `warning: `
    # prefix `_completeness_caveat_lines` always emits -- not the indentation, which is cosmetic.
    #
    # The text comes from the shared _scan_truncation_warning/_output_limit_note pair (the same two
    # helpers `_annotate_result_completeness` composes, so the diagram cannot disagree with the
    # JSON/text caveat) rather than a hardcoded literal, which fixes two further defects the old
    # line carried: it said `note:` for a TRUNCATION (inverting the warning-vs-advisory split this
    # command defines one function above), and it advised "raise --max-callers/--max-files" for
    # EVERY cause -- naming the only two knobs that cannot lift a --max-repo-files scan cap or a
    # caller-scan ceiling. Wrong-knob remediation advice is the failure #762 fixed on the MCP surface.
    #
    # TWO disclosure kinds, kept distinct (the pagination-caveat split): a SCAN truncation renders
    # the `warning:` comment + `tg_incomplete` node below; an OUTPUT cap renders a `note: OUTPUT
    # LIMITED` comment + a DISTINCT `tg_output_limited` advisory node -- never the scan-incomplete
    # node, because pagination is not a scan failure. A mixed payload renders both nodes.
    truncation = _scan_truncation_warning(payload)
    if truncation is None and payload.get("result_incomplete"):
        # An incompleteness stamped upstream that carries no scan_limit/output_limit of its own
        # still owes the reader a disclosure; falling through silently would trade a MISPOSITIONED
        # warning for an ABSENT one, which is the worse half of this same class.
        reason = payload.get("incomplete_reason")
        truncation = _truncation_message(
            str(reason) if reason else "the result is incomplete and may be missing entries"
        )
    leading, _ = _completeness_caveat_lines(truncation, is_truncation=truncation is not None)
    if leading is not None:
        # Flattened because a `%%` comment ends at the newline: an embedded one would close the
        # comment and turn the remainder into live graph statements. The deleted literal was a
        # fixed string and could not carry one; this line interpolates payload-derived values
        # (`scan_limit.max_repo_files`, `caller_scan_limit.ceiling`, the `output_limit` counts).
        # Every COLD path types those as int, so no reachable injection was found -- but the
        # warm/daemon payload (`_maybe_symbol_command_via_running_daemon`) is parsed JSON with no
        # field typing, so this is cheap fail-closed hardening rather than a proven vector, and
        # `_mermaid_label` already sanitizes node text on exactly this reasoning.
        lines.append(f"  %% {' '.join(leading.split())}")
        # AND a real NODE, because the comment above is invisible in a RENDERED diagram.
        # Mermaid's own flowchart docs say comments "will be ignored by the parser", so `%%`
        # serves the agent reading the source and nobody looking at the picture -- who is
        # precisely the reader most likely to trust a caller graph at a glance. Verified against
        # the upstream docs rather than assumed; the earlier fix that moved this disclosure to
        # LEAD improved the source-reading case only.
        #
        # Declared with no edge, so `-->` counts and the "no invented edges" guard are untouched;
        # emitted only when incomplete, so a complete graph stays byte-identical. The node carries
        # the CAUSE clause (up to the first sentence break) rather than the full text -- the
        # remedy sentence is long enough to distort a diagram, and it remains one line up in the
        # comment for whoever is reading the source.
        lines.append(f'  tg_incomplete["{_mermaid_label(_mermaid_incomplete_label(leading))}"]')
    output_note = _output_limit_note(payload)
    if output_note is not None:
        # OUTPUT-cap advisory, DISTINCT from the scan-incomplete node above: pagination is not a
        # scan failure, so it must never wear the `warning:`/`tg_incomplete` costume (an exit-0
        # display cap rendering as "INCOMPLETE" would cry wolf on every large caller graph). Same
        # two-line shape -- a greppable `%% note:` comment AND a visible node, because a comment
        # alone never reaches a rendered diagram -- and same no-edge discipline: declared with no
        # `-->`, so the "no invented edges" guard stays untouched. Placed BEFORE the target so a
        # reader meets the advisory before tracing the (paginated) graph it qualifies.
        lines.append(f"  %% note: {' '.join(output_note.split())}")
        lines.append(f'  tg_output_limited["{_mermaid_label(output_note)}"]')
    lines.append(f'  target["{_mermaid_label(symbol)}"]')
    for idx, rel in enumerate(sorted(grouped)):
        node = f"n{idx}"
        lines.append(f'  {node}["{_mermaid_label(rel)}"]')
        call_lines = sorted(grouped[rel])
        if len(call_lines) == 1:
            lines.append(f"  {node} -->|L{call_lines[0]}| target")
        elif call_lines:
            lines.append(f"  {node} -->|{len(call_lines)} calls| target")
        else:
            lines.append(f"  {node} --> target")
    if not grouped:
        # Deliberately still TRAILING, and not swept into the leading banner above: this is the
        # advisory half of the split. The scan COMPLETED and genuinely found nothing, so the line
        # is commentary on a trustworthy result rather than a qualifier on an untrustworthy one.
        lines.append(f"  %% no callers found for {symbol}")
    return "\n".join(lines)


def _daemon_blast_radius_no_match_is_unreliable(payload: dict[str, Any]) -> bool:
    """audit #107 (#94 flip blocker): True iff a warm/daemon blast_radius payload is a no_match on
    a possibly_truncated map -- the one case where the daemon-served
    build_symbol_blast_radius_from_map (repo_map.py, no literal-seed rescue) can disagree with
    what the cold build_symbol_blast_radius (repo_map.py, which DOES retry via
    _literal_symbol_seed_files) would find. The symbol may simply sit outside the daemon
    session's scan window, so a no_match here is unreliable and the caller should fall through to
    cold instead of trusting it.

    Deliberately narrow: only fires on no_match AND possibly_truncated together. A warm no_match
    on a COMPLETE map is a real miss -- falling back to cold there would defeat the daemon
    speedup for every genuine no-match, not just the truncated-and-wrong ones.

    Task #108: delegates to repo_map._blast_radius_no_match_is_possibly_truncated, the ONE shared
    definition of this condition (also used by build_symbol_blast_radius's own literal-seed-rescue
    trigger and the Tier-2 daemon agent-capsule's call-site-evidence collector) so all three arms
    agree on exactly when a no_match is trustworthy instead of drifting independently.
    """
    from tensor_grep.cli.repo_map import _blast_radius_no_match_is_possibly_truncated

    return _blast_radius_no_match_is_possibly_truncated(payload)
