"""EvidenceReceipt v1 -- Phase 1: schema + emitter (aggregator, no signing).

`tg evidence emit` produces a versioned, machine-consumable JSON receipt that aggregates what tg
*already computed* into one stable envelope: repo revision identity, files/caller/blast-radius
evidence, ambiguity/confidence, validation plan + actual outcomes, changed files + rollback, and
caller-supplied agent/model/cost metadata. See the design doc for the full field -> producer map.

Hard contract (Backend Fail-Closed Contract, applied to receipt emission):
  * A missing/unavailable source artifact makes ONLY that block `{"status": "unavailable",
    "reason": "..."}` -- never a silently empty or guessed value, and never a process crash.
  * This module is a pure aggregator: it reads already-persisted JSON (a prior `tg agent --json`
    capsule, a prior rewrite-audit-manifest, checkpoint metadata) plus at most 2 git subprocess
    calls. It never re-runs an expensive scan/repo-map by default; recompute is strictly opt-in
    (`recompute=True` + a `query`), and even then it calls a single already-cited REUSE producer
    (`repo_map.build_symbol_blast_radius`) -- never the session daemon, MCP server, or apply
    policy (out of scope for Phase 1; see AGENTS.md "Backend Fail-Closed Contract").

P2 (signing: `receipt_sha256` / `signature` / `previous_receipt_sha256` / `tg evidence verify`) is
a separate PR -- this module intentionally does not emit those fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tensor_grep.cli.audit_manifest import (
    _envelope,
    _json_output_version,
    _resolve_root,
    _utc_now_iso,
)
from tensor_grep.cli.subprocess_policy import configured_git_timeout_seconds, run_subprocess

RECEIPT_SCHEMA_VERSION = 1

_AGENT_ID_ENV = "TG_EVIDENCE_AGENT_ID"
_MODEL_ENV = "TG_EVIDENCE_MODEL"
_COST_JSON_ENV = "TG_EVIDENCE_COST_JSON"


# ---------------------------------------------------------------------------
# small local helpers (kept local rather than imported cross-module: each is
# a few lines and importing the owning module would add avoidable weight/
# coupling for the common case -- see the docstring above each helper)
# ---------------------------------------------------------------------------


def _read_project_version_fallback() -> str:
    # Mirrors main.py:237-246 _read_project_version_fallback exactly. Not imported from main.py:
    # main.py is the top-level CLI orchestrator (imports typer/ast_workflows/etc at module level)
    # and no lower-level cli/*.py module imports from it (matches the existing layering: e.g.
    # audit_manifest.py has its own _utc_now_iso rather than importing main.py's helpers).
    try:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return "0.0.0"


def _cli_package_version() -> str:
    # Mirrors main.py:249-255 _cli_package_version() exactly.
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _read_project_version_fallback()


def _as_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _display_command(argv: list[str]) -> str:
    # Mirrors checkpoint_store.py:231-234 _display_command exactly.
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _evidence_envelope(*, routing_reason: str) -> dict[str, Any]:
    # audit_manifest._envelope() hardcodes routing_backend="AuditManifest" (audit_manifest.py:37-44)
    # since it backs the audit-manifest/review-bundle family; override for this receipt's own
    # identity while reusing its version/schema_version/sidecar_used plumbing.
    envelope = _envelope(routing_reason=routing_reason)
    envelope["routing_backend"] = "EvidenceReceipt"
    return envelope


def _evidence_error_payload(message: str, *, code: str, routing_reason: str) -> dict[str, Any]:
    # Mirrors main.py:12759-12769 _review_bundle_error_payload's shape for CLI-layer errors.
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "EvidenceReceipt",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


# ---------------------------------------------------------------------------
# GAP 1: commit + dirty-worktree identity (no existing producer -- new helper)
# ---------------------------------------------------------------------------


def _parse_branch_header(header_text: str) -> str | None:
    """Parse a `git status --porcelain=v1 -b` `## ...` header into a branch name."""
    text = header_text.strip()
    if text.startswith("No commits yet on "):
        # unborn branch (a fresh `git init` with no commits yet)
        branch = text[len("No commits yet on ") :].strip()
        return branch or None
    if text == "HEAD (no branch)":
        return None  # detached HEAD
    # "branch...upstream [ahead N, behind M]" -> the local branch is the segment before "..."
    # and before any trailing whitespace/decoration.
    local = text.split("...", 1)[0].split(" ", 1)[0].strip()
    return local or None


def _repo_revision_identity(
    root: Path, exclude_prefixes: Sequence[str] | None = None
) -> dict[str, Any]:
    """`git rev-parse HEAD` + `git status --porcelain=v1 -b` -> commit/branch/dirty identity.

    Exactly 2 git subprocess calls (the CEO's performance mandate): the porcelain `-b` flag folds
    the branch name into the SAME `git status` call that reports dirty entries, so a second
    `rev-parse --abbrev-ref HEAD` call is unnecessary. Fails closed to `status: "unavailable"` on
    any git error (not a repo, git missing, timeout) -- never raises, never fabricates a value.

    `exclude_prefixes` (opt-in, default None): repo-root-relative POSIX path prefixes whose
    porcelain entries never count toward `dirty`/`dirty_tree_sha256` -- e.g. a generator's own
    `--out` directory, so regenerating a persisted, committed artifact doesn't make the repo read
    as dirty against its own prior output (the `tg codemap --check` false-positive this param was
    added for). This module is signing-adjacent (P2 will sign receipts built from this identity),
    so the DEFAULT (None) branch below is left byte-for-byte IDENTICAL to the pre-exclusion
    implementation -- every existing caller that never passes `exclude_prefixes` is provably
    unaffected; see `_repo_revision_identity_excluding` for the separate opt-in branch, which is
    still exactly 1 status call (2 total with the `rev-parse` above).
    """
    timeout_seconds = configured_git_timeout_seconds()

    try:
        commit_result = run_subprocess(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            timeout_seconds=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": f"git rev-parse could not run: {exc}"}
    if commit_result.returncode != 0:
        return {"status": "unavailable", "reason": _last_stderr_line(commit_result.stderr)}
    commit_sha = commit_result.stdout.strip()
    if not commit_sha:
        return {"status": "unavailable", "reason": "git rev-parse HEAD returned no output"}

    if exclude_prefixes:
        return _repo_revision_identity_excluding(
            root,
            commit_sha=commit_sha,
            timeout_seconds=timeout_seconds,
            exclude_prefixes=exclude_prefixes,
        )

    try:
        status_result = run_subprocess(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-b"],
            timeout_seconds=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": f"git status could not run: {exc}"}
    if status_result.returncode != 0:
        return {"status": "unavailable", "reason": _last_stderr_line(status_result.stderr)}

    branch: str | None = None
    dirty_lines: list[str] = []
    for line in status_result.stdout.splitlines():
        if line.startswith("## "):
            branch = _parse_branch_header(line[3:])
        elif line.strip():
            dirty_lines.append(line)

    dirty_tree_sha256 = hashlib.sha256("\n".join(sorted(dirty_lines)).encode("utf-8")).hexdigest()
    return {
        "status": "present",
        "commit_sha": commit_sha,
        "branch": branch,
        "dirty": bool(dirty_lines),
        "dirty_tree_sha256": dirty_tree_sha256,
        "dirty_file_count": len(dirty_lines),
    }


def _parse_porcelain_z(raw: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Parse `git status --porcelain=v1 -b -z` stdout into `(branch, [(XY, path), ...])`.

    `-z` NUL-terminates every record instead of LF and never quotes special characters, so paths
    are read verbatim -- no `" -> "` string-splitting and no core.quotePath unescaping needed. Per
    git-status(1), a rename/copy record's field order is reversed under `-z` ("`from -> to`
    becomes `to from`"), so the FIRST path field is always the destination path; a second,
    NUL-terminated ORIG_PATH field follows only when the status contains R or C, and is consumed
    here without being mistaken for its own record.
    """
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    branch: str | None = None
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if not token:
            continue
        if token.startswith("## "):
            branch = _parse_branch_header(token[3:])
            continue
        if len(token) < 3:
            continue  # malformed/short record -- skip defensively, never crash
        # Fixed-width fields (2-char XY status + 1 separator space + path), NOT a
        # `.partition(" ")`/whole-token `startswith` split: X or Y can itself be a literal space
        # (e.g. " M" = modified-in-worktree-only), which a naive first-space split would misparse.
        status, path = token[:2], token[3:]
        entries.append((status, path))
        if "R" in status or "C" in status:
            i += 1  # skip the NUL-terminated ORIG_PATH field for a rename/copy record
    return branch, entries


def _path_excluded(path: str, exclude_prefixes: Sequence[str]) -> bool:
    """True if `path` should be dropped from the dirty set: `path` is nested under (or equal to)
    an excluded prefix, OR an excluded prefix is nested under (or equal to) `path`. The second
    direction matters because git collapses an entirely-untracked directory to its own path (e.g.
    a brand-new `docs/` shows as a single `?? docs/` entry even though the excluded prefix is the
    deeper `docs/code-map`) -- a one-directional `startswith` would miss that collapsed case.
    Path-segment-boundary-safe: `docs-extra` is never treated as nested under `docs`.
    """
    normalized_path = path.strip("/")
    for prefix in exclude_prefixes:
        normalized_prefix = prefix.strip("/")
        if not normalized_prefix:
            continue
        if normalized_path == normalized_prefix:
            return True
        if normalized_path.startswith(normalized_prefix + "/"):
            return True
        if normalized_prefix.startswith(normalized_path + "/"):
            return True
    return False


def _repo_revision_identity_excluding(
    root: Path,
    *,
    commit_sha: str,
    timeout_seconds: float,
    exclude_prefixes: Sequence[str],
) -> dict[str, Any]:
    """The `exclude_prefixes`-aware sibling of `_repo_revision_identity`'s status half: a SEPARATE
    `-z` status call (never mixed into the default LF-parsed branch above, so that default branch
    stays byte-for-byte unchanged). Still exactly 1 status call (2 total with the already-issued
    `rev-parse`)."""
    try:
        status_result = run_subprocess(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-b", "-z"],
            timeout_seconds=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": f"git status could not run: {exc}"}
    if status_result.returncode != 0:
        return {"status": "unavailable", "reason": _last_stderr_line(status_result.stderr)}

    branch, entries = _parse_porcelain_z(status_result.stdout)
    dirty_lines = [
        f"{status} {path}" for status, path in entries if not _path_excluded(path, exclude_prefixes)
    ]

    dirty_tree_sha256 = hashlib.sha256("\n".join(sorted(dirty_lines)).encode("utf-8")).hexdigest()
    return {
        "status": "present",
        "commit_sha": commit_sha,
        "branch": branch,
        "dirty": bool(dirty_lines),
        "dirty_tree_sha256": dirty_tree_sha256,
        "dirty_file_count": len(dirty_lines),
    }


def _last_stderr_line(stderr: str | None) -> str:
    lines = (stderr or "").strip().splitlines()
    return lines[-1] if lines else "git exited non-zero with no stderr output"


# ---------------------------------------------------------------------------
# Fail-closed file readers (never raise -- return (None, reason) on failure)
# ---------------------------------------------------------------------------


def _read_optional_json_file(
    path: str | Path | None, *, description: str
) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as exc:
        return None, f"{description} path could not be resolved: {exc}"
    if not resolved.exists():
        return None, f"{description} not found: {resolved}"
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"{description} could not be read: {exc}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{description} is not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, f"{description} must be a JSON object."
    return payload, None


def _resolve_caller_str(flag_value: str | None, env_var: str) -> str | None:
    if flag_value is not None:
        stripped = flag_value.strip()
        return stripped or None
    env_value = os.environ.get(env_var)
    if env_value is not None:
        stripped = env_value.strip()
        return stripped or None
    return None


def _resolve_caller_path(flag_value: str | Path | None, env_var: str) -> str | Path | None:
    if flag_value is not None:
        return flag_value
    env_value = os.environ.get(env_var)
    return env_value or None


# ---------------------------------------------------------------------------
# Block builders -- each REUSE producer cited in the design doc, each
# fail-closed (`status: "unavailable"` + `reason` when its source is absent).
# ---------------------------------------------------------------------------


def _capsule_files_selected(capsule: dict[str, Any]) -> list[str]:
    files: list[str] = []
    primary_target = _as_dict(capsule.get("primary_target"))
    if primary_target is not None:
        primary_file = primary_target.get("file")
        if isinstance(primary_file, str) and primary_file:
            files.append(primary_file)
    for alternative in _as_list_of_dicts(capsule.get("alternative_targets")):
        alt_file = alternative.get("file")
        if isinstance(alt_file, str) and alt_file:
            files.append(alt_file)
    for snippet in _as_list_of_dicts(capsule.get("snippets")):
        snippet_file = snippet.get("file")
        if isinstance(snippet_file, str) and snippet_file:
            files.append(snippet_file)
    seen: set[str] = set()
    deduped: list[str] = []
    for file_path in files:
        if file_path not in seen:
            seen.add(file_path)
            deduped.append(file_path)
    return deduped


def _completeness_block(
    capsule: dict[str, Any] | None, manifest: dict[str, Any] | None
) -> dict[str, Any]:
    if capsule is None and manifest is None:
        return {"status": "unavailable", "reason": "no --capsule or --manifest provided"}
    completeness: dict[str, Any] = {"status": "present"}
    if capsule is not None:
        # agent_capsule.py:2276-2294 -- these are ADDITIVE/conditional keys on the real capsule
        # (only stamped when truncation actually occurred), so default them in rather than KeyError.
        completeness["result_incomplete"] = bool(capsule.get("result_incomplete", False))
        completeness["truncated"] = bool(capsule.get("partial", False))
        scan_limit = _as_dict(capsule.get("scan_limit"))
        if scan_limit is not None:
            completeness["scan_limit"] = scan_limit
        deadline_limit = _as_dict(capsule.get("deadline_limit"))
        if deadline_limit is not None:
            completeness["deadline_limit"] = deadline_limit
    if manifest is not None:
        validation = _as_dict(manifest.get("validation"))
        if validation is not None:
            completeness["validation_targets_truncated"] = validation.get(
                "validation_targets_truncated"
            )
    return completeness


def _scope_block(
    *,
    path: str,
    query: str | None,
    capsule: dict[str, Any] | None,
    capsule_error: str | None,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    block: dict[str, Any] = {"path": path, "query": query}
    if capsule is None:
        block["status"] = "unavailable"
        block["reason"] = capsule_error or "no --capsule provided"
        block["files_selected"] = None
        block["files_omitted_count"] = None
    else:
        omissions = _as_dict(capsule.get("omissions")) or {}
        block["status"] = "present"
        block["files_selected"] = _capsule_files_selected(capsule)
        block["files_omitted_count"] = omissions.get("omitted_section_count")
    # A repo-wide total_files/total_matches count is a `tg search` SearchResult concept
    # (core/result.py) -- Phase 1's inputs (--capsule/--manifest) never carry it, so these stay
    # explicitly null (a stable, present-but-unknown field) rather than being fabricated or omitted.
    block["total_files"] = None
    block["total_matches"] = None
    block["completeness"] = _completeness_block(capsule, manifest)
    return block


def _blast_radius_block_from_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    call_site_evidence = _as_dict(capsule.get("call_site_evidence"))
    if call_site_evidence is None:
        return {"status": "unavailable", "reason": "capsule has no call_site_evidence block"}
    status = str(call_site_evidence.get("status") or "unknown")
    related_call_sites = capsule.get("related_call_sites")
    block: dict[str, Any] = {
        "status": status,
        "source": "capsule",
        "symbol": call_site_evidence.get("symbol"),
        "callers": related_call_sites if isinstance(related_call_sites, list) else [],
        "returned_call_sites": call_site_evidence.get("returned_call_sites"),
        "omitted_callers": call_site_evidence.get("omitted_call_sites"),
        "graph_trust_summary": call_site_evidence.get("graph_trust_summary"),
        "resolution_gaps": call_site_evidence.get("resolution_gaps"),
        "provenance": call_site_evidence.get("provenance"),
    }
    reason = call_site_evidence.get("reason")
    if reason is not None:
        block["reason"] = reason
    return block


def _blast_radius_block_recomputed(query: str, path: str) -> dict[str, Any]:
    # Local import: repo_map.py is a large module (tree-sitter parsers etc.); only pay for it on
    # the explicit opt-in --recompute path, matching main.py's own local-import convention for
    # repo_map functions (e.g. main.py:9396 `from tensor_grep.cli.repo_map import ... build_symbol_callers`).
    from tensor_grep.cli import repo_map

    try:
        radius_payload = repo_map.build_symbol_blast_radius(query, path)
    except Exception as exc:  # defensive: an opt-in recompute must never crash the whole receipt
        return {"status": "unavailable", "reason": f"recompute failed: {exc}"}
    if radius_payload.get("no_match"):
        return {
            "status": "unavailable",
            "reason": "recompute found no definition for the given query",
            "symbol": query,
        }
    output_limit = _as_dict(radius_payload.get("output_limit")) or {}
    callers = radius_payload.get("callers")
    return {
        "status": "present",
        "source": "recomputed",
        "symbol": query,
        "callers": callers if isinstance(callers, list) else [],
        "omitted_callers": output_limit.get("omitted_callers"),
        "graph_trust_summary": radius_payload.get("graph_trust_summary"),
        "resolution_gaps": radius_payload.get("resolution_gaps"),
    }


def _blast_radius_block(
    *,
    capsule: dict[str, Any] | None,
    capsule_error: str | None,
    recompute: bool,
    query: str | None,
    path: str,
) -> dict[str, Any]:
    if recompute:
        if not query:
            return {
                "status": "unavailable",
                "reason": "--recompute requested but no --query/query was given",
            }
        return _blast_radius_block_recomputed(query, path)
    if capsule is None:
        return {
            "status": "unavailable",
            "reason": capsule_error or "no --capsule provided and --recompute not requested",
        }
    return _blast_radius_block_from_capsule(capsule)


def _confidence_block(capsule: dict[str, Any] | None, capsule_error: str | None) -> dict[str, Any]:
    if capsule is None:
        return {"status": "unavailable", "reason": capsule_error or "no --capsule provided"}
    confidence = _as_dict(capsule.get("confidence"))
    alternative_targets = capsule.get("alternative_targets")
    return {
        "status": "present",
        "overall": confidence.get("overall") if confidence else None,
        "downgrade_reasons": confidence.get("downgrade_reasons") if confidence else [],
        "ambiguity": capsule.get("ambiguity"),
        "alternative_targets": alternative_targets if isinstance(alternative_targets, list) else [],
        "ask_user_before_editing": capsule.get("ask_user_before_editing"),
    }


def _validation_block(
    *, capsule: dict[str, Any] | None, manifest: dict[str, Any] | None
) -> dict[str, Any]:
    if capsule is None and manifest is None:
        return {
            "status": "unavailable",
            "reason": "no --capsule (planned commands) or --manifest (actual outcomes) provided",
        }
    block: dict[str, Any] = {"status": "present"}

    if capsule is not None:
        validation_commands = capsule.get("validation_commands")
        suggested = capsule.get("suggested_validation_commands")
        validation_plan = capsule.get("validation_plan")
        block["planned_commands"] = (
            validation_commands if isinstance(validation_commands, list) else []
        )
        block["suggested_validation_commands"] = suggested if isinstance(suggested, list) else []
        block["validation_plan"] = validation_plan if isinstance(validation_plan, list) else []
    else:
        block["planned_commands"] = None
        block["suggested_validation_commands"] = None
        block["validation_plan"] = None

    manifest_validation = _as_dict(manifest.get("validation")) if manifest is not None else None
    if manifest_validation is not None:
        block["success"] = manifest_validation.get("success")
        block["commands"] = manifest_validation.get("commands")
        block["targets_truncated"] = manifest_validation.get("validation_targets_truncated")
        block["targets_total"] = manifest_validation.get("validation_targets_total")
    else:
        block["success"] = None
        block["commands"] = None
        block["targets_truncated"] = None
        block["targets_total"] = None
        if manifest is not None:
            block["outcome_reason"] = (
                "manifest present but has no validation block "
                "(tg run --apply was not given --lint-cmd/--test-cmd)"
            )
        else:
            block["outcome_reason"] = "no --manifest provided; only planned commands are known"
    return block


def _rollback_block_from_checkpoint_summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    checkpoint_id = checkpoint.get("checkpoint_id")
    root_value = checkpoint.get("root")
    scope_kind = checkpoint.get("scope")
    original_path = checkpoint.get("original_path")
    if not checkpoint_id or not root_value:
        return {
            "status": "unavailable",
            "reason": "checkpoint block is missing checkpoint_id/root",
        }
    # Mirrors checkpoint_store.py:237-239 _undo_argv exactly. NOT imported: undo_argv/undo_command
    # are only present on the in-memory CheckpointCreateResult returned at *creation* time --
    # neither the persisted checkpoint metadata JSON (checkpoint_store.py:600-611
    # _write_checkpoint_metadata's payload) nor the rewrite-audit-manifest's checkpoint block
    # (rust_core/src/main.rs:6136-6144 CheckpointCreateSummary) persists them. Reconstructing here
    # from the same 4 fields both DO persist (checkpoint_id/root/scope/original_path) is the only
    # way an emitter reading a manifest/checkpoint file after the fact can recover the undo command.
    undo_path = original_path if scope_kind == "file" and original_path else root_value
    argv = ["tg", "checkpoint", "undo", str(checkpoint_id), str(undo_path)]
    return {
        "status": "present",
        "checkpoint_id": checkpoint_id,
        "mode": checkpoint.get("mode"),
        "root": root_value,
        "scope": scope_kind,
        "created_at": checkpoint.get("created_at"),
        "file_count": checkpoint.get("file_count"),
        "undo_argv": argv,
        "undo_command": _display_command(argv),
    }


def _rollback_block_from_checkpoint_id(checkpoint_id: str, root: Path) -> dict[str, Any]:
    from tensor_grep.cli.checkpoint_store import load_checkpoint_metadata

    try:
        metadata = load_checkpoint_metadata(checkpoint_id, str(root))
    except FileNotFoundError as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": f"checkpoint metadata unreadable: {exc}"}
    return _rollback_block_from_checkpoint_summary(metadata)


def _changes_block(
    *,
    manifest: dict[str, Any] | None,
    manifest_error: str | None,
    checkpoint_id: str | None,
    root: Path,
) -> dict[str, Any]:
    if manifest is None:
        if checkpoint_id is None:
            return {"status": "unavailable", "reason": manifest_error or "no --manifest provided"}
        # A standalone --checkpoint-id (no manifest) can still answer rollback even though the
        # changed-files list / validation outcome are unknown without a manifest.
        return {
            "status": "unavailable",
            "reason": "no --manifest provided; changed-files/validation-outcome unknown",
            "rollback": _rollback_block_from_checkpoint_id(checkpoint_id, root),
        }

    files = manifest.get("files")
    applied_edit_ids = manifest.get("applied_edit_ids")
    block: dict[str, Any] = {
        "status": "present",
        "files": files if isinstance(files, list) else [],
        "applied_edit_ids": applied_edit_ids if isinstance(applied_edit_ids, list) else [],
        "plan_total_edits": manifest.get("plan_total_edits"),
    }
    checkpoint = _as_dict(manifest.get("checkpoint"))
    if checkpoint is not None:
        block["rollback"] = _rollback_block_from_checkpoint_summary(checkpoint)
    else:
        block["rollback"] = {
            "status": "unavailable",
            "reason": "manifest has no checkpoint block (tg run --apply was not given --checkpoint)",
        }
    # The actual triggered-rollback OUTCOME (ValidationRollbackSummary: success/files_restored/
    # errors) lives only in `tg run --apply`'s live stdout response
    # (rust_core/src/main.rs:6085-6098 ApplyVerifyJson.rollback) -- it is NOT a field of the
    # persisted rewrite-audit-manifest (rust_core/src/main.rs:6210-6225 RewriteAuditManifest has
    # no rollback field). A manifest read after the fact can never recover it; fail closed rather
    # than guess whether an auto-rollback fired. (Producer-shape gap vs the design doc's assumption
    # that `load_checkpoint_metadata` alone would answer this -- it does not.)
    block["triggered_rollback"] = {
        "status": "unavailable",
        "reason": (
            "not persisted in the audit manifest; only available in the live "
            "`tg run --apply` response at the time it runs"
        ),
    }
    return block


def _caller_block(
    *,
    agent_id: str | None,
    model: str | None,
    cost: dict[str, Any] | None,
    cost_requested: bool,
    cost_error: str | None,
) -> dict[str, Any]:
    caller_metadata_present = bool(agent_id or model or cost)
    block: dict[str, Any] = {
        "status": "caller-supplied",
        "provenance": "caller-supplied",
        "caller_metadata_present": caller_metadata_present,
        "agent_id": agent_id,
        "model": model,
        "cost": cost,
    }
    if cost_requested and cost_error is not None:
        block["cost_source_error"] = cost_error
    return block


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def build_evidence_receipt(
    path: str | Path = ".",
    *,
    query: str | None = None,
    manifest_path: str | Path | None = None,
    capsule_path: str | Path | None = None,
    checkpoint_id: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    cost_json_path: str | Path | None = None,
    recompute: bool = False,
) -> dict[str, Any]:
    """Aggregate an EvidenceReceipt v1 payload (Phase 1: no signature).

    Reads at most two persisted JSON files (`--manifest`, `--capsule`) plus at most 2 git
    subprocess calls (`_repo_revision_identity`). Never re-scans the repo unless `recompute=True`
    AND `query` is given, in which case exactly one already-cited REUSE producer
    (`repo_map.build_symbol_blast_radius`) is invoked for the blast_radius block only.
    """
    root = _resolve_root(Path(path))

    receipt = _evidence_envelope(routing_reason="evidence-receipt-emit")
    receipt["kind"] = "evidence-receipt"
    receipt["receipt_schema_version"] = RECEIPT_SCHEMA_VERSION
    receipt["created_at"] = _utc_now_iso()
    receipt["tool"] = {
        "name": "tensor-grep",
        "version": _cli_package_version(),
        "json_output_version": _json_output_version(),
    }
    receipt["revision"] = _repo_revision_identity(root)

    capsule, capsule_error = _read_optional_json_file(capsule_path, description="Evidence capsule")
    manifest, manifest_error = _read_optional_json_file(manifest_path, description="Audit manifest")

    resolved_cost_path = _resolve_caller_path(cost_json_path, _COST_JSON_ENV)
    cost, cost_error = _read_optional_json_file(resolved_cost_path, description="Cost JSON")

    resolved_agent_id = _resolve_caller_str(agent_id, _AGENT_ID_ENV)
    resolved_model = _resolve_caller_str(model, _MODEL_ENV)

    receipt["scope"] = _scope_block(
        path=str(root),
        query=query,
        capsule=capsule,
        capsule_error=capsule_error,
        manifest=manifest,
    )
    blast_radius = _blast_radius_block(
        capsule=capsule,
        capsule_error=capsule_error,
        recompute=recompute,
        query=query,
        path=str(root),
    )
    receipt["blast_radius"] = blast_radius
    receipt["confidence"] = _confidence_block(capsule, capsule_error)
    receipt["validation"] = _validation_block(capsule=capsule, manifest=manifest)
    receipt["changes"] = _changes_block(
        manifest=manifest,
        manifest_error=manifest_error,
        checkpoint_id=checkpoint_id,
        root=root,
    )
    receipt["caller"] = _caller_block(
        agent_id=resolved_agent_id,
        model=resolved_model,
        cost=cost,
        cost_requested=resolved_cost_path is not None,
        cost_error=cost_error,
    )

    if capsule_path is None:
        capsule_source = "none"
    elif capsule is not None:
        capsule_source = f"file:{capsule_path}"
    else:
        capsule_source = f"unavailable:{capsule_path}"

    receipt["sources"] = {
        "manifest_path": (
            str(Path(manifest_path).expanduser().resolve()) if manifest_path is not None else None
        ),
        "manifest_sha256": manifest.get("manifest_sha256") if manifest is not None else None,
        "capsule_path": (
            str(Path(capsule_path).expanduser().resolve()) if capsule_path is not None else None
        ),
        "capsule_source": capsule_source,
        "session_id": None,  # Phase 1 deliberately does not wire session-daemon reads (see AGENTS.md)
        "recomputed": bool(recompute and blast_radius.get("source") == "recomputed"),
    }
    return receipt


def build_evidence_receipt_json(
    path: str | Path = ".",
    *,
    query: str | None = None,
    manifest_path: str | Path | None = None,
    capsule_path: str | Path | None = None,
    checkpoint_id: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    cost_json_path: str | Path | None = None,
    recompute: bool = False,
) -> str:
    return json.dumps(
        build_evidence_receipt(
            path,
            query=query,
            manifest_path=manifest_path,
            capsule_path=capsule_path,
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            model=model,
            cost_json_path=cost_json_path,
            recompute=recompute,
        ),
        indent=2,
    )
