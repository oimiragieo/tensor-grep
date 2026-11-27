from __future__ import annotations

import json
from pathlib import Path
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
from tensor_grep.cli.error_handling import (
    _audit_diff_error_payload,
    _audit_history_error_payload,
    _evidence_error_payload,
    _review_bundle_error_payload,
)
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


def audit_verify(
    manifest_path: str = typer.Argument(..., help="Path to the rewrite audit manifest JSON file."),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Optional HMAC signing key path for signed manifests.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous manifest path for validating manifest chaining.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON verification output.",
    ),
) -> None:
    """Verify a rewrite audit manifest digest, chain, and optional signature."""
    from tensor_grep.cli.audit_manifest import (
        verify_audit_manifest,
        verify_audit_manifest_json,
    )

    try:
        if json_output:
            json_text = verify_audit_manifest_json(
                manifest_path,
                signing_key=signing_key,
                previous_manifest=previous_manifest,
            )
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid manifest must exit 1 even in
            # --json mode (audit H1), so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return

        payload = verify_audit_manifest(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        if json_output:
            from tensor_grep.cli.formatters.human_fmt import audit_verify_error_payload

            typer.echo(
                json.dumps(audit_verify_error_payload(exc, manifest_path=manifest_path), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Manifest: {payload['manifest_path']}")
    typer.echo(f"valid={payload['valid']}")
    checks = payload["checks"]
    typer.echo(
        "checks="
        f"digest:{checks['digest_valid']} "
        f"chain:{checks['chain_valid']} "
        f"signature:{checks['signature_valid']}"
    )
    for error in payload["errors"]:
        typer.echo(f"- {error}")
    if not payload["valid"]:
        raise typer.Exit(code=1)


def audit_history(
    path: str = typer.Argument(".", help="Project root to inspect for audit manifests."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON history output.",
    ),
) -> None:
    """List known audit manifests in newest-first chain order."""
    from tensor_grep.cli.audit_manifest import list_audit_history, list_audit_history_payload

    try:
        if json_output:
            typer.echo(json.dumps(list_audit_history_payload(path), indent=2))
            return
        payload = list_audit_history(path)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="not_found"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="invalid_input"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="internal_error"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not payload:
        typer.echo("No audit manifests found.")
        return

    for entry in payload:
        annotations: list[str] = []
        if entry["missing_timestamp"]:
            annotations.append("missing_timestamp")
        if entry["chain_gap"]:
            annotations.append("chain_gap")
        if entry["signature_kind"] is not None:
            annotations.append(f"signature={entry['signature_kind']}")
        created_at = entry["created_at"] or "<missing>"
        suffix = f" [{' '.join(annotations)}]" if annotations else ""
        typer.echo(f"{created_at}  {entry['manifest_sha256']}  {entry['file_path']}{suffix}")


def audit_diff(
    previous_manifest: str = typer.Argument(
        ..., help="Path to the previous audit manifest JSON file."
    ),
    current_manifest: str = typer.Argument(
        ..., help="Path to the current audit manifest JSON file."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON diff output.",
    ),
) -> None:
    """Compute a semantic diff between two audit manifests."""
    from tensor_grep.cli.audit_manifest import diff_audit_manifests, diff_audit_manifests_payload

    try:
        if json_output:
            typer.echo(
                json.dumps(
                    diff_audit_manifests_payload(previous_manifest, current_manifest), indent=2
                )
            )
            return
        payload = diff_audit_manifests(previous_manifest, current_manifest)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(json.dumps(_audit_diff_error_payload(str(exc), code="not_found"), indent=2))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_diff_error_payload(str(exc), code="invalid_json"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _audit_diff_error_payload(str(exc), code="internal_error"),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Audit diff: {previous_manifest} -> {current_manifest}")
    for section_name in ("added", "removed", "changed"):
        typer.echo(f"{section_name.capitalize()}:")
        section = payload[section_name]
        if not section:
            typer.echo("  (none)")
            continue
        for key, value in section.items():
            if section_name == "changed":
                typer.echo(f"  {key}:")
                typer.echo(f"    old: {json.dumps(value['old'], sort_keys=True)}")
                typer.echo(f"    new: {json.dumps(value['new'], sort_keys=True)}")
                continue
            typer.echo(f"  {key}: {json.dumps(value, sort_keys=True)}")


def review_bundle_create(
    manifest_path: str = typer.Option(
        ...,
        "--manifest",
        help="Path to the rewrite audit manifest JSON file.",
    ),
    scan_path: str | None = typer.Option(
        None,
        "--scan",
        help="Optional path to the ruleset scan JSON file.",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID to include in the bundle.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous audit manifest JSON for diff generation.",
    ),
    receipt: list[str] | None = typer.Option(
        None,
        "--receipt",
        help="Path to a signed EvidenceReceipt JSON file (from `tg evidence emit --sign`) to "
        "embed in the bundle. Repeatable. Pair with `tg review-bundle verify --against` in CI to "
        "gate the PR on receipt signature/trust/freshness.",
    ),
    output_path: str | None = typer.Option(
        None,
        "--output",
        "--out",
        help="Optional file path where the review bundle JSON should be written.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the review bundle as structured JSON.",
    ),
) -> None:
    """Create a review bundle for enterprise change review."""
    from tensor_grep.cli.audit_manifest import create_review_bundle, create_review_bundle_json
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        if json_output:
            typer.echo(
                create_review_bundle_json(
                    manifest_path,
                    scan_path=scan_path,
                    checkpoint_id=checkpoint_id,
                    previous_manifest=previous_manifest,
                    receipt_paths=receipt,
                    output_path=output_path,
                    include_change_slice=True,
                )
            )
            return
        payload = create_review_bundle(
            manifest_path,
            scan_path=scan_path,
            checkpoint_id=checkpoint_id,
            previous_manifest=previous_manifest,
            receipt_paths=receipt,
            output_path=output_path,
            include_change_slice=True,
        )
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_receipt",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    included_components = [
        component
        for component in (
            "audit_manifest",
            "scan_results",
            "checkpoint_metadata",
            "diff",
            "evidence_receipts",
        )
        if payload[component] is not None
    ]
    target = output_path or "<not written>"
    typer.echo(
        f"Created review bundle {target} "
        f"(components={','.join(included_components)}, bundle_sha256={payload['bundle_sha256']})"
    )


def review_bundle_verify(
    bundle_path: str = typer.Argument(..., help="Path to the review bundle JSON file."),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="HMAC key path required for an HMAC-signed embedded manifest. "
        "Supply it explicitly; an embedded key_path is never trusted. "
        "This is separate from Ed25519 --trusted-key.",
    ),
    against: str | None = typer.Option(
        None,
        "--against",
        help="Git ref each embedded evidence receipt's revision must match (commit_sha equal, "
        "dirty=false) to be considered fresh; an unresolvable ref fails closed. In GitHub Actions "
        "use the pull_request event's head SHA (github.event.pull_request.head.sha) -- NEVER the "
        "workflow's own GITHUB_SHA, which resolves to the merge commit, not the PR head, and "
        "would read every receipt as stale on every PR.",
    ),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust when "
        "verifying embedded evidence_receipts. Repeatable. Falls back to "
        "TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    require_trusted: bool = typer.Option(
        False,
        "--require-trusted",
        help="Fail closed (valid=false) unless every embedded receipt's key matches a "
        "--trusted-key. Without this flag, an untrusted key is still reported (key_trusted=false) "
        "but does not by itself fail `valid`.",
    ),
    min_receipts: int = typer.Option(
        0,
        "--min-receipts",
        help="Fail closed (valid=false) unless at least N embedded evidence receipts are "
        "present, well-formed, and pass signature/trust/freshness verification. Default 0 = no "
        "enforcement (back-compat: a receipt-less bundle still verifies valid=true). Without "
        "this, a bundle with its receipts stripped and checksums recomputed passes with NO "
        "evidence -- this is the org's real policy lever to require genuine, current evidence.",
    ),
    expect_key: list[str] | None = typer.Option(
        None,
        "--expect-key",
        help="A key_id (from `tg evidence pubkey`, e.g. sha256:<hex>) that must sign at least "
        "one valid embedded receipt. Repeatable; ALL named key_ids must each be represented by a "
        "valid receipt, or verification fails closed (valid=false). Pins WHICH signer(s) are "
        "required, distinct from --trusted-key which pins which signers are cryptographically "
        "trusted at all.",
    ),
    require_change_slice: bool = typer.Option(
        False,
        "--require-change-slice",
        help="Fail closed unless change_slice and slice_digest are present and valid.",
    ),
    expect_slice_digest: str | None = typer.Option(
        None,
        "--expect-slice-digest",
        help="Expected SHA-256 slice_digest the embedded change_slice must match.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured verification JSON.",
    ),
) -> None:
    """Verify review bundle integrity and component checksums."""
    from tensor_grep.cli.audit_manifest import verify_review_bundle, verify_review_bundle_json
    from tensor_grep.cli.evidence_signing import resolve_trusted_public_keys

    try:
        trusted_keys = resolve_trusted_public_keys(trusted_key)
        # Least-surprise guard, mirrors `tg evidence verify`: supplying a specific trusted key
        # strongly implies intent to ENFORCE it, but without --require-trusted an untrusted
        # embedded receipt key still yields valid=true for that receipt (only key_trusted=false).
        # Warn VISIBLY on stderr (never touching --json stdout, never silently changing `valid`).
        if trusted_keys and not require_trusted:
            typer.echo(
                "warning: --trusted-key/TG_EVIDENCE_TRUSTED_KEYS supplied without "
                "--require-trusted; an untrusted embedded receipt key will still report "
                "valid=true for that receipt. Pass --require-trusted to enforce.",
                err=True,
            )
        if json_output:
            json_text = verify_review_bundle_json(
                bundle_path,
                signing_key=signing_key,
                against=against,
                trusted_public_keys=trusted_keys,
                require_trusted=require_trusted,
                min_receipts=min_receipts,
                expect_key_ids=expect_key,
                expected_slice_digest=expect_slice_digest,
                require_change_slice=require_change_slice,
            )
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid bundle must exit 1 even in
            # --json mode (audit H1) so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return
        payload = verify_review_bundle(
            bundle_path,
            signing_key=signing_key,
            against=against,
            trusted_public_keys=trusted_keys,
            require_trusted=require_trusted,
            min_receipts=min_receipts,
            expect_key_ids=expect_key,
            expected_slice_digest=expect_slice_digest,
            require_change_slice=require_change_slice,
        )
    except typer.Exit:
        raise
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    from tensor_grep.cli.review_bundle_render import render_review_bundle_verification

    render_review_bundle_verification(payload)
    if not payload["valid"]:
        raise typer.Exit(code=1)


def evidence_emit(
    path: str = typer.Argument(
        ".", help="Repository path to bind the receipt's revision identity to."
    ),
    query: str | None = typer.Option(None, "--query", help="Symbol/query this receipt is about."),
    manifest_path: str | None = typer.Option(
        None,
        "--manifest",
        help="Path to a prior rewrite-audit-manifest JSON (changes/validation-outcomes/rollback).",
    ),
    capsule_path: str | None = typer.Option(
        None,
        "--capsule",
        help="Path to a prior `tg agent --json` capsule (blast-radius/ambiguity/confidence).",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID for rollback info when --manifest has no checkpoint block.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_AGENT_ID.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Caller-supplied model identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_MODEL.",
    ),
    cost_json: str | None = typer.Option(
        None,
        "--cost-json",
        help="Path to caller-supplied cost JSON, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_COST_JSON.",
    ),
    recompute: bool = typer.Option(
        False,
        "--recompute",
        help="OPT-IN: recompute blast-radius for --query instead of aggregating only. "
        "OFF by default (performance contract: no re-scan unless explicitly requested).",
    ),
    sign: bool = typer.Option(
        False,
        "--sign",
        help="Ed25519-sign the receipt. FAILS CLOSED: a non-zero exit and NO receipt is written "
        "(to --out or stdout) if no signing key resolves -- never emits unsigned when --sign was "
        "requested.",
    ),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Path to the Ed25519 private key used with --sign. Falls back to "
        "TG_EVIDENCE_SIGNING_KEY, then ~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    previous: str | None = typer.Option(
        None,
        "--previous",
        help="Path to a prior receipt to chain to; attaches previous_receipt_sha256 "
        "(independent of --sign).",
    ),
    output_path: str | None = typer.Option(
        None,
        "--out",
        "--output",
        help="Optional file path where the receipt JSON should be written.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the receipt as structured JSON to stdout."
    ),
) -> None:
    """Emit a versioned EvidenceReceipt aggregating tg's existing outputs (no re-scan)."""
    from tensor_grep.cli.evidence_receipt import build_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        receipt = build_evidence_receipt(
            path,
            query=query,
            manifest_path=manifest_path,
            capsule_path=capsule_path,
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            model=model,
            cost_json_path=cost_json,
            recompute=recompute,
            sign=sign,
            signing_key_path=signing_key,
            previous_receipt_path=previous,
        )
    except EvidenceSigningError as exc:
        # Backend Fail-Closed Contract: --sign with no resolvable key (or a broken crypto
        # install) lands here -- NEVER falls through to the write-to-`--out`/stdout step below.
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-emit"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="internal_error", routing_reason="evidence-receipt-emit"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if output_path is not None:
        from tensor_grep.cli.session_store import _write_json_atomic

        try:
            # audit C4 / CWE-59: check for a symlink BEFORE `.resolve()` -- resolving first
            # would follow the symlink to its real target and make `is_symlink()` on the
            # result always False, silently defeating `_write_json_atomic`'s own symlink guard
            # (mirrors evidence_signing.generate_keypair's identical ordering fix).
            # `_write_json_atomic` also makes this write atomic (temp file + fsync +
            # os.replace) instead of the previous bare `write_text`, which could leave a
            # truncated receipt on a crash mid-write.
            expanded_output = Path(output_path).expanduser()
            if expanded_output.is_symlink():
                raise OSError(
                    f"Refusing to write the evidence receipt through a symlink: {expanded_output}"
                )
            resolved_output = expanded_output.resolve()
            _write_json_atomic(resolved_output, receipt)
        except OSError as exc:
            # Backend Fail-Closed Contract: never leak a raw traceback for a refused/failed
            # write -- report the same structured error shape the two except blocks above use.
            if json_output:
                typer.echo(
                    json.dumps(
                        _evidence_error_payload(
                            str(exc), code="write_error", routing_reason="evidence-receipt-emit"
                        ),
                        indent=2,
                    )
                )
            else:
                typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(receipt, indent=2))
        return

    target = output_path or "<stdout only>"
    revision = cast(dict[str, object], receipt.get("revision", {}))
    typer.echo(f"Evidence receipt ({target}):")
    typer.echo(f"  commit_sha={revision.get('commit_sha', '<unavailable>')}")
    typer.echo(f"  dirty={revision.get('dirty', '<unavailable>')}")
    for block_name in ("scope", "blast_radius", "confidence", "validation", "changes", "caller"):
        block = receipt.get(block_name)
        status = block.get("status", "unknown") if isinstance(block, dict) else "unknown"
        typer.echo(f"  {block_name}.status={status}")
    signing_block = receipt.get("signing")
    if isinstance(signing_block, dict):
        typer.echo(f"  signed=True key_id={signing_block.get('key_id')}")
    else:
        typer.echo(f"  signed=False receipt_sha256={receipt.get('receipt_sha256')}")


def evidence_verify(
    receipt_path: str = typer.Argument(..., help="Path to an EvidenceReceipt JSON file to verify."),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust. "
        "Repeatable. Falls back to TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    require_trusted: bool = typer.Option(
        False,
        "--require-trusted",
        help="Fail closed (valid=false) unless the embedded key matches a --trusted-key. "
        "Without this flag, an untrusted key is still reported (key_trusted=false) but does not "
        "by itself fail `valid`.",
    ),
    previous_path: str | None = typer.Option(
        None,
        "--previous",
        help="Path to the prior receipt this one should chain to; checks previous_receipt_sha256.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the verify result as JSON."),
) -> None:
    """Verify an EvidenceReceipt's digest, signature, trust, and (optional) chain link."""
    from tensor_grep.cli.evidence_receipt import verify_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError, resolve_trusted_public_keys

    try:
        trusted_keys = resolve_trusted_public_keys(trusted_key)
        # Least-surprise guard: supplying a specific trusted key strongly implies intent to ENFORCE
        # it, but without --require-trusted an embedded attacker key still yields valid=true (only
        # key_trusted=false). Warn VISIBLY on stderr (never touching --json stdout, never silently
        # changing `valid`) so the caller notices the un-enforced trust. ASCII-only (Windows cp1252).
        if trusted_keys and not require_trusted:
            typer.echo(
                "warning: --trusted-key/TG_EVIDENCE_TRUSTED_KEYS supplied without "
                "--require-trusted; an untrusted key will still report valid=true. Pass "
                "--require-trusted to enforce.",
                err=True,
            )
        payload = verify_evidence_receipt(
            receipt_path,
            trusted_public_keys=trusted_keys,
            require_trusted=require_trusted,
            previous_receipt_path=previous_path,
        )
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-verify"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="internal_error", routing_reason="evidence-receipt-verify"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Evidence receipt: {payload['receipt_path']}")
        typer.echo(f"  valid={payload['valid']}")
        typer.echo(f"  signed={payload['signed']}")
        checks = cast(dict[str, object], payload["checks"])
        typer.echo(
            f"  digest_valid={checks['digest_valid']} signature_valid={checks['signature_valid']} "
            f"signature_status={checks['signature_status']} key_trusted={checks['key_trusted']}"
        )
        if payload.get("key_id"):
            typer.echo(f"  key_id={payload['key_id']}")
        chain = payload.get("chain")
        if isinstance(chain, dict):
            typer.echo(f"  chain_valid={chain['chain_valid']}")
            # Surface WHY the chain failed (e.g. an oversized/missing --previous file, or a digest
            # mismatch) in text mode too, not only in --json -- otherwise a bounded-read refusal or
            # a mismatch reads as a bare `chain_valid=False` with no actionable reason.
            chain_error = chain.get("chain_error")
            if chain_error:
                typer.echo(f"  chain_error: {chain_error}")
        for error in cast(list[object], payload.get("errors") or []):
            typer.echo(f"  error: {error}")

    if not payload["valid"]:
        raise typer.Exit(code=1)


def evidence_keygen(
    out_path: str | None = typer.Option(
        None,
        "--out",
        "--output",
        help="Where to write the private key. Defaults to TG_EVIDENCE_SIGNING_KEY or "
        "~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing key file at the target path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the keygen result as JSON."),
) -> None:
    """Generate a new Ed25519 signing keypair for `tg evidence emit --sign`."""
    from tensor_grep.cli.evidence_receipt import keygen_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        payload = keygen_evidence_receipt(out_path, force=force)
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-keygen"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"Private key: {payload['private_key_path']}")
    typer.echo(f"Public key:  {payload['public_key_path']}")
    typer.echo(f"key_id={payload['key_id']}")
    typer.echo(f"public_key={payload['public_key']}")
    typer.echo("Register the key_id/public_key with your downstream verifier (e.g. gotcontext).")


def evidence_pubkey(
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Path to the private key file. Defaults to TG_EVIDENCE_SIGNING_KEY or "
        "~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the pubkey result as JSON."),
) -> None:
    """Print the public key + key_id for the resolved signing key (for verifier registration)."""
    from tensor_grep.cli.evidence_receipt import pubkey_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        payload = pubkey_evidence_receipt(signing_key)
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-pubkey"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"key_id={payload['key_id']}")
    typer.echo(f"public_key={payload['public_key']}")


def register(app: typer.Typer, review_bundle_app: typer.Typer, evidence_app: typer.Typer) -> None:
    app.command(name="audit-verify")(audit_verify)
    app.command(name="audit-history")(audit_history)
    app.command(name="audit-diff")(audit_diff)
    review_bundle_app.command("create")(review_bundle_create)
    review_bundle_app.command("verify")(review_bundle_verify)
    evidence_app.command("emit")(evidence_emit)
    evidence_app.command("verify")(evidence_verify)
    evidence_app.command("keygen")(evidence_keygen)
    evidence_app.command("pubkey")(evidence_pubkey)
