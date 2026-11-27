from __future__ import annotations

import json
from typing import cast

import typer

from tensor_grep.cli import _index_lock as _index_lock

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
from tensor_grep.cli.error_handling import _emit_ledger_error
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import _json_output_version
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
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

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


def ledger_claim(
    path: str = typer.Argument(
        ".",
        help="Repository subtree this claim is scoped to (e.g. 'core/hooks', or '.' for the "
        "whole repo). Recorded as the claim's `scope` -- claim/list/release all canonicalize "
        "to the SAME repository root regardless of which subtree PATH names, so `tg ledger "
        "list` (broader/ancestor PATH, e.g. the default '.') rolls this claim up. See "
        "`tg ledger --help`.",
    ),
    symbol: list[str] = typer.Option(
        [], "--symbol", help="Symbol name to claim. Repeatable (--symbol A --symbol B)."
    ),
    files: str | None = typer.Option(
        None,
        "--files",
        "--file",
        help="Comma-separated root-relative file paths to claim (e.g. --files a.py,b.py).",
    ),
    intent: str = typer.Option(
        "edit", "--intent", help='Caller-declared intent, e.g. "edit", "review".'
    ),
    ttl: int | None = typer.Option(
        None, "--ttl", help="Claim TTL in seconds. Defaults to TG_LEDGER_CLAIM_TTL_SECONDS or 900."
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). Falls "
        "back to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID. Do not put secrets here.",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Free-text note recorded verbatim. Do not put secrets here.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): record an advisory claim on symbols/files and report live
    overlaps from other agents. Surface and JSON schema may change in a minor release.

    PATH SCOPING: PATH is this claim's `scope`, not an isolated storage location -- claim/
    list/release all canonicalize to the SAME repository root (the nearest `.git` ancestor)
    regardless of which subtree PATH names, so `tg ledger list` (default PATH `.`) always
    rolls up every live claim in the repo, each tagged with its own `scope`.

    ADVISORY ONLY: this never blocks an edit. It always exits 0 on success -- even when
    other agents hold live overlapping claims, which are reported in `overlaps` for the
    caller to act on. It exits 2 only on a fail-closed condition (lock timeout, a `--files`
    entry outside the repo root, or a write failure); nothing is written when that happens.
    """
    from tensor_grep.cli import _index_lock, ledger_store

    file_list = [item.strip() for item in files.split(",") if item.strip()] if files else []

    try:
        result = ledger_store.submit_claim(
            path,
            symbols=list(symbol),
            files=file_list,
            intent=intent,
            note=note,
            ttl_seconds=ttl,
            agent_id=agent_id,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc

    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-claim",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "claim": result["claim"],
        "overlaps": result["overlaps"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    claim = cast(dict[str, object], result["claim"])
    overlaps = cast(list[dict[str, object]], result["overlaps"])
    typer.echo(f"Claim {claim['claim_id']} recorded for agent={claim['agent_id']}")
    typer.echo(f"  scope={claim['scope']} symbols={claim['symbols']} files={claim['files']}")
    typer.echo(f"  expires_at={claim['expires_at']}")
    if overlaps:
        typer.echo(f"  overlaps: {len(overlaps)} live claim(s) from other agents")
        for overlap in overlaps:
            typer.echo(
                f"    {overlap['claim_id']} agent={overlap['agent_id']} "
                f"intent={overlap['intent']} expires_at={overlap['expires_at']}"
            )
    else:
        typer.echo("  overlaps: none")


def ledger_release(
    path: str = typer.Argument(
        ".",
        help="Repository subtree used to resolve the SHARED repo-wide claims index (claim/"
        "list/release all canonicalize to the SAME repository root regardless of which "
        "subtree PATH names). PATH does NOT filter which claim gets released -- "
        "`--claim-id`/`--symbol` do that. See `tg ledger --help`.",
    ),
    claim_id: str | None = typer.Option(
        None, "--claim-id", help="Release the claim with this exact ID."
    ),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Release the caller's own live claim(s) covering this symbol.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier used to scope --symbol release. Falls back "
        "to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): release a claim by exact `--claim-id` (any caller who knows
    the id) or by `--symbol` (scoped to the resolved `--agent-id`'s own claims only). Surface
    and JSON schema may change in a minor release.

    PATH SCOPING: PATH only resolves WHICH repository's shared claims index this call
    operates on (canonicalized to the nearest `.git` ancestor regardless of subtree) -- it
    never filters which claim matches. Always exits 0 on success, including when nothing
    matched -- releasing an already-expired or unknown claim is not an error, and when
    nothing matches, the response names any live claims elsewhere in the repository
    (`unmatched_reason`/`live_claims_elsewhere`) so a wrong `--claim-id`/`--symbol` is
    self-diagnosing instead of a silent no-op. Exits 2 only on a fail-closed condition (lock
    timeout or a write failure)."""
    from tensor_grep.cli import _index_lock, ledger_store

    try:
        result = ledger_store.release_claim(
            path, claim_id=claim_id, symbol=symbol, agent_id=agent_id
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc

    released = cast(list[dict[str, object]], result["released"])
    live_claims_elsewhere = cast(list[dict[str, object]], result["live_claims_elsewhere"])
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-release",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "released": released,
        "released_count": result["released_count"],
        "listed_scope": result["listed_scope"],
        "unmatched_reason": result["unmatched_reason"],
        "live_claims_elsewhere": live_claims_elsewhere,
        "live_claims_elsewhere_count": result["live_claims_elsewhere_count"],
        "live_claims_elsewhere_truncated": result["live_claims_elsewhere_truncated"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not released:
        typer.echo(f"No matching live claim found (listed_scope={result['listed_scope']!r}).")
        if live_claims_elsewhere:
            typer.echo(
                f"  {result['live_claims_elsewhere_count']} live claim(s) exist elsewhere in "
                "this repository:"
            )
            for entry in live_claims_elsewhere:
                typer.echo(
                    f"    {entry['claim_id']} agent={entry['agent_id']} scope={entry['scope']} "
                    f"symbols={entry['symbols']}"
                )
            if result["live_claims_elsewhere_truncated"]:
                typer.echo("    ... (truncated)")
        else:
            typer.echo("  No live claims exist for this repository.")
        return
    for entry in released:
        typer.echo(
            f"Released {entry['claim_id']} (agent={entry['agent_id']}, scope={entry['scope']})"
        )


def ledger_list(
    path: str = typer.Argument(
        ".",
        help="Repository subtree to list claims within. ROLLS UP: listing a broader/ancestor "
        "path (e.g. '.', the default) shows every live claim scoped to it OR to any "
        "descendant subtree -- e.g. `tg ledger claim core/hooks` shows up in `tg ledger "
        "list` (default '.') and in `tg ledger list core`, but not in `tg ledger list docs`. "
        "See `tg ledger --help`.",
    ),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Filter to live claims covering this symbol."
    ),
    agent_id: str | None = typer.Option(
        None, "--agent-id", help="Filter to live claims from this agent."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): list live (non-expired) claims scoped to PATH or to any of its
    descendant subtrees (rollup -- each returned claim carries its own `scope`). Surface and
    JSON schema may change in a minor release. Always exits 0, including an empty result --
    an empty claims list is a normal outcome, not a not-found error."""
    from tensor_grep.cli import ledger_store

    try:
        result = ledger_store.list_claims(path, symbol=symbol, agent_id=agent_id)
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="read_error", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc

    claims = cast(list[dict[str, object]], result["claims"])
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-list",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "claims": claims,
        "count": result["count"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not claims:
        typer.echo(f"No live claims (listed path={path!r}, includes descendant subtrees).")
        return
    typer.echo(f"Live claims within path={path!r} (includes descendant subtrees):")
    for entry in claims:
        typer.echo(
            f"{entry['claim_id']}  agent={entry['agent_id']}  scope={entry.get('scope')}  "
            f"symbols={entry.get('symbols')}  files={entry.get('files')}  "
            f"expires_at={entry.get('expires_at')}"
        )


def ledger_record(
    path: str = typer.Argument(".", help="Repository path the finding is scoped to."),
    receipt: str | None = typer.Option(
        None,
        "--receipt",
        help="Path to the evidence-receipt/blast-radius/context-pack/repo-map artifact JSON "
        "to ingest.",
    ),
    artifact_kind: str = typer.Option(
        "evidence-receipt",
        "--artifact-kind",
        help="One of: blast-radius, evidence-receipt, context-pack, repo-map.",
    ),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Symbol name or query string this artifact answers."
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). Falls "
        "back to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID. Do not put secrets here.",
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help="Finding TTL in seconds. Defaults to TG_LEDGER_FINDING_TTL_SECONDS or 86400 (24h).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 2): ingest an evidence-receipt/blast-radius/context-pack/repo-map
    artifact JSON as a content-addressed finding pointer a sibling agent can `tg ledger find`
    and reuse instead of recomputing. Surface and JSON schema may change in a minor release.

    The artifact is stored once at `findings/blobs/<receipt_sha256>.json`, content-addressed
    by `receipt_sha256` (the same digest `tg evidence` uses) -- recording an identical artifact
    twice dedupes to the same blob. Exits 0 on success. Exits 2 only on a fail-closed condition
    (missing `--receipt`, an invalid `--artifact-kind`, a missing/oversized/non-JSON artifact
    file, a lock timeout, or a write failure); nothing is written when that happens.
    """
    from tensor_grep.cli import _index_lock, ledger_store

    try:
        result = ledger_store.record_finding(
            path,
            receipt_path=receipt,
            artifact_kind=artifact_kind,
            symbol=symbol,
            agent_id=agent_id,
            ttl_seconds=ttl,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc

    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-record",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "finding": result["finding"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    finding = cast(dict[str, object], result["finding"])
    typer.echo(
        f"Finding {finding['finding_id']} recorded (artifact_kind={finding['artifact_kind']})"
    )
    typer.echo(f"  symbol={finding.get('symbol')} receipt_sha256={finding['receipt_sha256']}")
    typer.echo(f"  signed={finding['signed']} expires_at={finding['expires_at']}")


def ledger_find(
    path: str = typer.Argument(".", help="Repository path the findings are scoped to."),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Symbol name or query string to look up."
    ),
    artifact_kind: str | None = typer.Option(
        None,
        "--artifact-kind",
        help="Restrict to one of: blast-radius, evidence-receipt, context-pack, repo-map.",
    ),
    fresh_only: bool = typer.Option(
        False,
        "--fresh-only",
        help="Return only findings whose captured revision matches the current repo state.",
    ),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust when "
        "checking a signed finding's key_trusted. Repeatable. Falls back to "
        "TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 2): look up previously recorded findings for `--symbol` so a
    sibling agent can reuse a prior artifact instead of recomputing it -- `if tg ledger find
    PATH --symbol S --fresh-only; then reuse; else compute; fi`. Surface and JSON schema may
    change in a minor release.

    Exit-code contract (3-state, mirrors the `tg defs`/`callers`/`blast-radius` symbol-command
    contract -- distinct from claim/release/list, which are 0/2 only): `0` = at least one
    returned finding is `fresh` (its captured revision matches the current repo state -- safe
    to reuse); `1` = no returned finding is fresh (recompute) -- covers both "nothing matched"
    and "matches exist but none are fresh"; `2` = a fail-closed condition fired (missing
    `--symbol`, a corrupt/oversized index, or a tampered/unreadable blob -- a corrupted finding
    is refused, never silently served or silently skipped). Every finding actually returned is
    integrity-checked against its recorded `receipt_sha256` before being served, regardless of
    exit code.
    """
    from tensor_grep.cli import ledger_store
    from tensor_grep.cli.evidence_signing import resolve_trusted_public_keys

    resolved_trusted_keys = resolve_trusted_public_keys(trusted_key)

    try:
        result = ledger_store.find_findings(
            path,
            symbol=symbol or "",
            artifact_kind=artifact_kind,
            fresh_only=fresh_only,
            trusted_public_keys=resolved_trusted_keys or None,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="read_error", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc

    findings = cast(list[dict[str, object]], result["findings"])
    any_fresh = bool(result["any_fresh"])
    exit_code = 0 if any_fresh else 1
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-find",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "findings": findings,
        "count": result["count"],
        "any_fresh": any_fresh,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(code=exit_code)

    if not findings:
        typer.echo("No matching findings.")
    else:
        for entry in findings:
            typer.echo(
                f"{entry['finding_id']}  fresh={entry['fresh']}  "
                f"artifact_kind={entry['artifact_kind']}  agent={entry['agent_id']}  "
                f"receipt_sha256={entry['receipt_sha256']}"
            )
    raise typer.Exit(code=exit_code)


def register(ledger_app: typer.Typer) -> None:
    ledger_app.command("claim")(ledger_claim)
    ledger_app.command("release")(ledger_release)
    ledger_app.command("list")(ledger_list)
    ledger_app.command("record")(ledger_record)
    ledger_app.command("find")(ledger_find)
