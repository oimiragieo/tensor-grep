from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_builder import (
    build_agent_capsule_from_map as build_agent_capsule_from_map,
)
from tensor_grep.cli.agent_capsule_call_sites import (
    _collect_capsule_call_site_evidence as _collect_capsule_call_site_evidence,
)
from tensor_grep.cli.agent_capsule_call_sites import (
    _collect_capsule_call_site_evidence_from_map as _collect_capsule_call_site_evidence_from_map,
)
from tensor_grep.cli.agent_capsule_call_sites import (
    _related_call_site_record as _related_call_site_record,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _apply_capsule_token_budget_confidence_uplift as _apply_capsule_token_budget_confidence_uplift,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_confidence_and_ask_without_render as _capsule_confidence_and_ask_without_render,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_context_consistency as _capsule_context_consistency,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_low_confidence_ask_reason as _capsule_low_confidence_ask_reason,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_scan_incomplete as _capsule_scan_incomplete,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _capsule_validation_evidence_ask_reason as _capsule_validation_evidence_ask_reason,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _confidence as _confidence,
)
from tensor_grep.cli.agent_capsule_confidence import (
    _follow_up_reads as _follow_up_reads,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_BASIS as _BEST_EFFORT_PRIMARY_BASIS,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_EVIDENCE as _BEST_EFFORT_PRIMARY_EVIDENCE,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE as _BEST_EFFORT_PRIMARY_MAX_CONFIDENCE,
)
from tensor_grep.cli.agent_capsule_constants import (
    _BEST_EFFORT_PRIMARY_SCAN_CAP as _BEST_EFFORT_PRIMARY_SCAN_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_INLINE_CALLER_ANNOTATION_TOP_LIMIT as _CAPSULE_INLINE_CALLER_ANNOTATION_TOP_LIMIT,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_BOOST_ENV as _CAPSULE_LSP_CONFIDENCE_BOOST_ENV,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_CAP as _CAPSULE_LSP_CONFIDENCE_CAP,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_LSP_CONFIDENCE_LANGUAGES as _CAPSULE_LSP_CONFIDENCE_LANGUAGES,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY as _CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS as _CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT as _CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_ASK_REASON as _CAPSULE_SCAN_TRUNCATED_ASK_REASON,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON as _CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON,
)
from tensor_grep.cli.agent_capsule_constants import (
    DEFAULT_AGENT_CLI_DEADLINE_SECONDS as DEFAULT_AGENT_CLI_DEADLINE_SECONDS,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _agent_gpu_query_terms as _agent_gpu_query_terms,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _alternative_targets as _alternative_targets,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _command_ref as _command_ref,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _normalize_gpu_device_ids as _normalize_gpu_device_ids,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _resolve_match_file as _resolve_match_file,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _summarize_agent_gpu_json_result as _summarize_agent_gpu_json_result,
)
from tensor_grep.cli.agent_capsule_inline_callers import (
    _build_inline_caller_annotation_text as _build_inline_caller_annotation_text,
)
from tensor_grep.cli.agent_capsule_inline_callers import (
    _capsule_inline_caller_annotation_enabled as _capsule_inline_caller_annotation_enabled,
)
from tensor_grep.cli.agent_capsule_inline_callers import (
    _inline_annotation_comment_prefix as _inline_annotation_comment_prefix,
)
from tensor_grep.cli.agent_capsule_inline_callers import (
    _top_caller_symbol_names as _top_caller_symbol_names,
)
from tensor_grep.cli.agent_capsule_outbound import (
    _capsule_outbound_dependencies_enabled as _capsule_outbound_dependencies_enabled,
)
from tensor_grep.cli.agent_capsule_outbound import (
    _outbound_dependency_call_tokens as _outbound_dependency_call_tokens,
)
from tensor_grep.cli.agent_capsule_outbound import (
    _outbound_dependency_import_tails as _outbound_dependency_import_tails,
)
from tensor_grep.cli.agent_capsule_outbound import (
    _outbound_dependency_line_preview as _outbound_dependency_line_preview,
)
from tensor_grep.cli.agent_capsule_outbound import (
    _outbound_dependency_selected_symbol_locations as _outbound_dependency_selected_symbol_locations,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _build_snippets as _build_snippets,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _expanded_line_map as _expanded_line_map,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _raw_context_ref as _raw_context_ref,
)
from tensor_grep.cli.agent_capsule_snippets import (
    _source_refetch_ref as _source_refetch_ref,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_strings as _as_list_of_strings,
)
from tensor_grep.cli.agent_capsule_targets import (
    _best_effort_primary_target_from_map as _best_effort_primary_target_from_map,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_alternative_target_confidences as _cap_alternative_target_confidences,
)
from tensor_grep.cli.agent_capsule_targets import (
    _cap_primary_target_confidence as _cap_primary_target_confidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_lsp_confidence_boost_enabled as _capsule_lsp_confidence_boost_enabled,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_trust_checks as _capsule_trust_checks,
)
from tensor_grep.cli.agent_capsule_targets import (
    _capsule_validation_alignment as _capsule_validation_alignment,
)
from tensor_grep.cli.agent_capsule_targets import (
    _dedupe as _dedupe,
)
from tensor_grep.cli.agent_capsule_targets import (
    _lsp_tie_resolution_evidence as _lsp_tie_resolution_evidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _numeric_confidence as _numeric_confidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _prefer_implementation_over_cli_dispatcher_helper as _prefer_implementation_over_cli_dispatcher_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _prefer_implementation_over_marker_helper as _prefer_implementation_over_marker_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _primary_target as _primary_target,
)
from tensor_grep.cli.agent_capsule_targets import (
    _primary_target_is_unrequested_marker_helper as _primary_target_is_unrequested_marker_helper,
)
from tensor_grep.cli.agent_capsule_targets import (
    _suggested_scope_from_tied_targets as _suggested_scope_from_tied_targets,
)
from tensor_grep.cli.agent_capsule_targets import (
    _target_has_lsp_confidence_proof as _target_has_lsp_confidence_proof,
)
from tensor_grep.cli.agent_capsule_targets import (
    _target_lsp_boost_language as _target_lsp_boost_language,
)
from tensor_grep.cli.agent_capsule_targets import (
    _targeted_validation_evidence as _targeted_validation_evidence,
)
from tensor_grep.cli.agent_capsule_targets import (
    _tied_alternative_targets as _tied_alternative_targets,
)
from tensor_grep.cli.runtime_paths import (
    gpu_probe_timeout_s,
    is_cross_domain_native_binary,
    resolve_native_tg_binary,
    translate_path_for_windows_binary,
)


def _collect_outbound_dependencies(
    query: str,
    path: str,
    target: dict[str, Any],
    payload: dict[str, Any],
    snippets: list[dict[str, Any]],
    related_call_sites: list[dict[str, Any]],
    *,
    max_files: int,
    preview_token_budget: int | None,
    deadline_monotonic: float | None = None,
    deadline_hit: repo_map._DeadlineBreakFlag | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """DAR (arxiv steal #4): the primary target's outbound dependencies, corroboration-gated.

    A candidate call token is kept ONLY if it (i) resolves to a symbol defined in another
    SELECTED file (-> file+line+provenance, `dependency_kind` "call") OR (ii) matches an import
    tail (`dependency_kind` "import", `file` null, provenance "import-heuristic") -- or both
    (`dependency_kind` "call+import"). This is deliberately NOT a bare-regex scan: an unresolved,
    un-imported call token is dropped as noise (the diff-docs/DocPrism false-positive lesson).

    BUDGET ISOLATION (load-bearing): this function NEVER evicts a snippet, caller, or changes any
    omission reason -- the records it returns are metadata outside the snippet token budget, same
    as `related_call_sites`. Only the OPTIONAL `text` preview on each record is budgeted, from the
    caller-supplied `preview_token_budget` (upstream `max_tokens` leftover after snippets) --
    `None` means unlimited (no `max_tokens` cap was requested at all upstream either).

    FAIL-SAFE (byte-identical contract): every early return here is `([], {})` -- the caller MUST
    treat that as "emit NEITHER `outbound_dependencies` nor `outbound_dependency_evidence`", never
    an empty-but-present key. See `build_agent_capsule`.

    dogfood finding 1 / council must-fix #5: ``deadline_monotonic``/``deadline_hit`` follow the
    same ``_DeadlineBreakFlag`` readback contract every other deadline-scoped seam in this PR
    uses. A hit before this function has even started its own FS parse work bails through the
    SAME fail-safe ``([], {})`` shape as every other early return above -- DAR is opt-in
    (default OFF) so this is defensive: once opted in, it must never be the reason a --deadline
    budget is silently blown, even though its own per-primary-file work is normally small.
    """
    if not _capsule_outbound_dependencies_enabled():
        return [], {}
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if deadline_hit is not None:
            deadline_hit.hit = True
        return [], {}
    primary_file = str(target.get("file") or "")
    primary_symbol = str(target.get("symbol") or "")
    if not primary_file or not primary_symbol:
        return [], {}
    primary_snippet = next(
        (snippet for snippet in snippets if str(snippet.get("file") or "") == primary_file),
        None,
    )
    if primary_snippet is None:
        return [], {}
    source = str(primary_snippet.get("source") or "")
    if not source.strip():
        return [], {}
    try:
        start_line = max(1, int(str(primary_snippet.get("start_line") or 1)))
    except (TypeError, ValueError):
        start_line = 1

    try:
        imports, primary_symbols = repo_map._imports_and_symbols_for_path(Path(primary_file))
    except Exception:  # pragma: no cover - defensive; DAR must never break the capsule
        return [], {}

    locally_defined = {
        str(symbol.get("name") or "") for symbol in primary_symbols if symbol.get("name")
    }
    import_tails = _outbound_dependency_import_tails(imports)
    resolved_locations = _outbound_dependency_selected_symbol_locations(
        payload,
        exclude_file=primary_file,
    )
    excluded_pairs = {
        (str(record.get("file") or ""), str(record.get("symbol") or ""))
        for record in related_call_sites
    }

    candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for name, line in _outbound_dependency_call_tokens(source, start_line):
        if not name or name == primary_symbol:
            continue
        if name in _CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS or name in locally_defined:
            continue
        resolution = resolved_locations.get(name)
        import_source = import_tails.get(name)
        if resolution is None and import_source is None:
            continue
        resolved_file = str(resolution["file"]) if resolution else None
        key = (resolved_file or "", name)
        if key in seen_keys or key in excluded_pairs:
            continue
        seen_keys.add(key)
        if resolution is not None and import_source is not None:
            dependency_kind = "call+import"
        elif resolution is not None:
            dependency_kind = "call"
        else:
            dependency_kind = "import"
        candidates.append({
            "file": resolved_file,
            "line": int(resolution["line"]) if resolution else None,
            "symbol": name,
            "kind": str(resolution["kind"]) if resolution else "unknown",
            "relation": "outbound-dependency",
            "dependency_kind": dependency_kind,
            "provenance": str(resolution["provenance"]) if resolution else "import-heuristic",
            "reason": "primary target calls this symbol",
            "_first_use_line": line,
        })

    if not candidates:
        return [], {}

    candidates.sort(
        key=lambda item: (
            _CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY.get(str(item["dependency_kind"]), 3),
            int(item["_first_use_line"]),
        )
    )
    limit = max(1, min(int(max_files) * 2, 8))
    kept = candidates[:limit]
    omitted_count = max(0, len(candidates) - len(kept))

    unlimited_preview = preview_token_budget is None
    remaining_preview_budget: int | None = (
        None if preview_token_budget is None else max(0, int(preview_token_budget))
    )
    records: list[dict[str, Any]] = []
    for candidate in kept:
        record = {key: value for key, value in candidate.items() if key != "_first_use_line"}
        preview = _outbound_dependency_line_preview(
            source,
            start_line,
            int(candidate["_first_use_line"]),
        )[:_CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT]
        if preview:
            if unlimited_preview:
                record["text"] = preview
            else:
                token_cost = repo_map._estimate_tokens(preview)
                if remaining_preview_budget is not None and token_cost <= remaining_preview_budget:
                    record["text"] = preview
                    remaining_preview_budget -= token_cost
        refetch = _source_refetch_ref(
            {"file": candidate["file"], "symbol": candidate["symbol"]},
            query,
            path,
            max_files,
        )
        record["refetch"] = {"command": refetch["command"], "argv": refetch["argv"]}
        records.append(record)

    evidence = {
        "status": "collected",
        "symbol": primary_symbol,
        "returned_dependencies": len(records),
        "omitted_dependencies": omitted_count,
        "max_dependencies": limit,
        "provenance": _dedupe([str(record["provenance"]) for record in records]),
        "preview_token_budget_remaining": (None if unlimited_preview else remaining_preview_budget),
    }
    return records, evidence


def _run_agent_gpu_json_command(
    argv: list[object],
    *,
    timeout_s: float,
    valid_return_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    ref = _command_ref(argv)
    args = [str(arg) for arg in argv]
    try:
        completed = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(float(timeout_s), 0.1),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "reason": f"GPU evidence command timed out after {timeout_s:g}s.",
            "command": ref["command"],
            "argv": ref["argv"],
            "exit_code": None,
            "stderr": str(exc),
        }
    except OSError as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "command": ref["command"],
            "argv": ref["argv"],
            "exit_code": None,
            "stderr": str(exc),
        }

    stdout = completed.stdout or ""
    stderr = (completed.stderr or "").strip()
    result: dict[str, Any] = {
        "status": "ok",
        "command": ref["command"],
        "argv": ref["argv"],
        "exit_code": completed.returncode,
    }
    if stderr:
        result["stderr"] = stderr
    if completed.returncode not in valid_return_codes:
        result["status"] = "failed"
        result["reason"] = f"GPU evidence command exited with code {completed.returncode}."
        return result

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result["status"] = "malformed"
        result["reason"] = f"GPU evidence command did not return JSON: {exc}"
        if stdout.strip():
            result["stdout_preview"] = stdout.strip()[:400]
        return result
    if not isinstance(payload, dict):
        result["status"] = "malformed"
        result["reason"] = "GPU evidence command returned a non-object JSON payload."
        return result
    result["payload"] = payload
    return result


def _agent_gpu_tg_command() -> str:
    native_tg = resolve_native_tg_binary()
    if native_tg is not None:
        return str(native_tg)
    # #704 gate CRUX-4: falling straight through to the bare string "tg" here handed
    # `subprocess.run` an UN-checked PATH lookup -- `resolve_native_tg_binary()` (above)
    # deliberately filters out e.g. a Python console-script shim
    # (`_looks_like_python_scripts_launcher`) and enforces a version match, but a bare "tg"
    # bypasses all of that vetting AND is invisible to the cross-domain classifier below: a
    # relative name has no directory component, so `is_cross_domain_native_binary()`'s
    # sibling-`.exe`/metadata checks (`runtime_paths.py`) resolve against the CWD instead of
    # wherever the shell would actually have found "tg". Pre-resolving via `shutil.which`
    # gives an explicit absolute path that the SAME `is_cross_domain_native_binary(tg_command)`
    # gate the call site already applies (`_agent_gpu_evidence`, below) can classify correctly
    # -- no separate gate call needed here. If nothing resolves at all, preserve the prior
    # behavior exactly: return the bare name rather than raising, so the probe still degrades
    # to its existing honest failure path (`_run_agent_gpu_json_command`'s `OSError` handler
    # turns the resulting spawn failure into a `status: "failed"` result, never a crash).
    which_tg = shutil.which("tg")
    return which_tg if which_tg is not None else "tg"


def _native_gpu_route_rejection(payload: dict[str, Any]) -> str | None:
    backend = str(payload.get("routing_backend") or "")
    sidecar_used = bool(payload.get("sidecar_used"))
    if backend == "NativeGpuBackend" and not sidecar_used:
        return None
    if sidecar_used or "Sidecar" in backend:
        return (
            "sidecar-routed GPU result is unsupported for agent evidence; "
            "use a CUDA-enabled native tg route."
        )
    return (
        "GPU evidence command did not use NativeGpuBackend "
        f"(routing_backend={backend or 'unknown'})."
    )


def _gpu_route_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "routing_backend": str(payload.get("routing_backend") or "unknown"),
        "routing_reason": str(payload.get("routing_reason") or "unknown"),
        "sidecar_used": bool(payload.get("sidecar_used")),
    }


def _agent_gpu_evidence(
    query: str,
    path: str,
    *,
    gpu_device_ids: list[int] | None,
    max_files: int,
    timeout_s: float,
) -> dict[str, Any]:
    requested_device_ids = _normalize_gpu_device_ids(gpu_device_ids)
    if not requested_device_ids:
        return {
            "status": "not_requested",
            "requested_device_ids": [],
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": "No GPU evidence scan requested.",
        }

    try:
        tg_command = _agent_gpu_tg_command()
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(exc),
        }

    # GPU-P0-1 (#171): the agent twin of the doctor's WSL path-domain bug -- tg_command can
    # resolve to a Windows-target binary that cannot open a Linux TemporaryDirectory path. Share
    # the same detection/translation/timeout helpers as the doctor probe (no divergent copy).
    cross_domain = is_cross_domain_native_binary(tg_command)
    effective_timeout_s = gpu_probe_timeout_s(cross_domain=cross_domain, default_s=timeout_s)

    device_arg = ",".join(str(device_id) for device_id in requested_device_ids)
    with tempfile.TemporaryDirectory(prefix="tg-agent-gpu-probe-") as probe_tmp:
        probe_dir = Path(probe_tmp)
        (probe_dir / "probe.log").write_text(
            "tg agent gpu probe sentinel\n",
            encoding="utf-8",
        )
        probe_target = str(probe_dir)
        if cross_domain:
            translated = translate_path_for_windows_binary(probe_dir)
            if translated is None:
                return {
                    "status": "path_domain_mismatch",
                    "requested_device_ids": requested_device_ids,
                    "used_for_evidence": False,
                    "promotion_claim": False,
                    "reason": (
                        "resolved native tg binary targets Windows but this WSL host could not "
                        "translate the probe path via wslpath (path-domain mismatch, not a GPU "
                        "capability gap)"
                    ),
                }
            probe_target = translated
        probe_command: list[object] = [
            tg_command,
            "search",
            "--gpu-device-ids",
            device_arg,
            "--json",
            "-F",
            # End-of-options sentinel, BEFORE both positionals (CWE-88). This is the SECOND argv
            # builder in this function, 86 lines above the one the original fix touched, and it was
            # missed -- found by an independent adversarial review. `probe_target` is
            # caller-derived (and rewritten by the WSL `wslpath` branch just above), so this is the
            # same live shape, not a defensive nicety.
            "--",
            "tg agent gpu probe sentinel",
            probe_target,
        ]
        probe = _run_agent_gpu_json_command(probe_command, timeout_s=effective_timeout_s)

    if probe["status"] != "ok":
        return {
            "status": str(probe["status"]),
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(probe.get("reason") or "GPU route probe failed."),
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
        }

    probe_payload = _as_dict(probe.get("payload"))
    route_rejection = _native_gpu_route_rejection(probe_payload)
    route_fields = _gpu_route_fields(probe_payload)
    if route_rejection is not None:
        return {
            "status": "unsupported",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": route_rejection,
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            **route_fields,
        }

    query_terms = _agent_gpu_query_terms(query)
    if not query_terms:
        return {
            "status": "ready",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": "Native GPU route passed, but the query produced no evidence terms.",
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            **route_fields,
        }

    # GPU-P0 gate-nit B (#172): the probe command above already translates its (self-generated)
    # sentinel path when cross_domain, but this evidence command used to append the RAW user
    # `path` unconditionally -- a Windows-target binary can no more resolve a raw WSL/Linux
    # search root here than it could the probe's temp dir. Mirror the probe's translate-or-fail-
    # closed handling instead of silently handing the native binary an unresolvable path (which
    # would misreport as a generic evidence-command failure rather than the honest
    # path_domain_mismatch status).
    evidence_path = path
    if cross_domain:
        translated_evidence_path = translate_path_for_windows_binary(path)
        if translated_evidence_path is None:
            return {
                "status": "path_domain_mismatch",
                "requested_device_ids": requested_device_ids,
                "used_for_evidence": False,
                "promotion_claim": False,
                "reason": (
                    "resolved native tg binary targets Windows but this WSL host could not "
                    "translate the evidence path via wslpath (path-domain mismatch, not a GPU "
                    "capability gap)"
                ),
            }
        evidence_path = translated_evidence_path

    evidence_command: list[object] = [
        tg_command,
        "search",
        "--gpu-device-ids",
        device_arg,
        "--json",
        "-F",
    ]
    for term in query_terms:
        evidence_command.extend(["-e", term])
    # End-of-options sentinel (CWE-88 / the MCP-276 class, AGENTS.md). `evidence_path` is
    # caller-supplied, and it is appended as a BARE POSITIONAL: the native binary's clap `path`
    # argument (rust_core/src/main.rs:694-695) carries no `allow_hyphen_values`, so a dash-leading
    # path is parsed as a FLAG rather than a path. A list-argv `subprocess` call blocks a SHELL
    # injection; it does nothing at all about flag injection into the callee's own parser.
    #
    # This builder was missed by the #860 sweep, which fixed the sibling
    # `_build_native_tg_search_command` in cli/main.py. Same class, same fix, and the reason the
    # sweep is now tracked by an enumerating test rather than by memory.
    #
    # UNCONDITIONAL, matching #860 and `ripgrep_backend.py`/`mcp_server.py`. A conditional form
    # (emit `--` only when the path starts with `-`) reads as equivalent and leaves the silent
    # case exposed -- the evidence probe would query a scope nobody chose and still report `ok`.
    evidence_command.append("--")
    evidence_command.append(evidence_path)
    evidence = _run_agent_gpu_json_command(
        evidence_command,
        timeout_s=effective_timeout_s,
        valid_return_codes=(0, 1),
    )
    if evidence["status"] != "ok":
        return {
            "status": str(evidence["status"]),
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": str(evidence.get("reason") or "GPU evidence scan failed."),
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            "evidence": _summarize_agent_gpu_json_result(evidence),
            **route_fields,
        }

    evidence_payload = _as_dict(evidence.get("payload"))
    evidence_route_rejection = _native_gpu_route_rejection(evidence_payload)
    evidence_route_fields = _gpu_route_fields(evidence_payload)
    if evidence_route_rejection is not None:
        return {
            "status": "unsupported",
            "requested_device_ids": requested_device_ids,
            "used_for_evidence": False,
            "promotion_claim": False,
            "reason": evidence_route_rejection,
            "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
            "evidence": _summarize_agent_gpu_json_result(evidence),
            **evidence_route_fields,
        }

    search_root = Path(path)
    matched_files: list[str] = []
    evidence_matches: list[dict[str, Any]] = []
    for match in _as_list_of_dicts(evidence_payload.get("matches")):
        matched_file = _resolve_match_file(match.get("file") or match.get("path"), search_root)
        if matched_file is None:
            continue
        if matched_file not in matched_files:
            matched_files.append(matched_file)
        if len(evidence_matches) < max_files:
            evidence_matches.append({
                "file": matched_file,
                "line": match.get("line") or match.get("line_number"),
                "pattern_text": match.get("pattern_text") or match.get("pattern"),
            })

    total_matches = int(evidence_payload.get("total_matches", len(evidence_matches)) or 0)
    status = "used" if matched_files else "ready_no_matches"
    reason = (
        "Native GPU route produced batched query-term evidence."
        if matched_files
        else "Native GPU route ran, but no query-term evidence matched."
    )
    return {
        "status": status,
        "requested_device_ids": requested_device_ids,
        "used_for_evidence": bool(matched_files),
        "promotion_claim": False,
        "reason": reason,
        "query_terms": query_terms,
        "matched_files": matched_files[:max_files],
        "total_matches": total_matches,
        "matches": evidence_matches,
        "probe": _summarize_agent_gpu_json_result(probe, redact_probe_paths=True),
        "evidence": _summarize_agent_gpu_json_result(evidence),
        **evidence_route_fields,
    }


def _apply_inline_caller_annotation(
    snippets: list[dict[str, Any]],
    target: dict[str, Any],
    call_site_evidence: dict[str, Any],
    related_call_sites: list[dict[str, Any]],
    rm: dict[str, Any],
    *,
    max_tokens: int | None,
    used_tokens: int,
) -> None:
    """Prepend a one-line inline structural annotation to the PRIMARY target's rendered snippet --
    the definition an agent is actually about to edit, which is the one snippet this capsule
    already has verified call-site evidence for. Mutates the matching snippet dict in place; a
    no-op (returns without touching anything) whenever the feature is off, the data was never
    collected, the language is unrecognized, or the annotation would blow the caller's own
    `max_tokens` ceiling.

    ORDERING CONTRACT (enforced by the caller in `build_agent_capsule_from_map`, not here): this
    must run AFTER `_collect_outbound_dependencies` (DAR). DAR resolves callee line numbers as
    `start_line + offset` into the primary snippet's OWN rendered source
    (`_outbound_dependency_call_tokens`); prepending a line to that source before DAR runs would
    shift every subsequent line off by one in DAR's own arithmetic. Running last avoids that
    entirely -- DAR always sees the unmodified snippet.

    `line_map` uses the SAME "unknown/synthetic line -> None" convention `_expanded_line_map`
    already emits for a truncated/unmapped rendered line (not a new shape), so a consumer that
    already tolerates `line: None` there tolerates it here too.
    """
    if not snippets or not _capsule_inline_caller_annotation_enabled():
        return
    target_file = str(target.get("file") or "")
    target_symbol = str(target.get("symbol") or "")
    if not target_file or not target_symbol:
        return
    primary_snippet = next(
        (
            snippet
            for snippet in snippets
            if str(snippet.get("file") or "") == target_file
            and str(snippet.get("symbol") or "") == target_symbol
        ),
        None,
    )
    if primary_snippet is None:
        return
    comment_prefix = _inline_annotation_comment_prefix(target_file)
    if comment_prefix is None:
        return
    top_names = _top_caller_symbol_names(
        rm, related_call_sites, limit=_CAPSULE_INLINE_CALLER_ANNOTATION_TOP_LIMIT
    )
    annotation_line = _build_inline_caller_annotation_text(
        comment_prefix, call_site_evidence, top_names
    )
    if annotation_line is None:
        return
    annotation_token_estimate = repo_map._estimate_tokens(annotation_line)
    if max_tokens is not None and used_tokens + annotation_token_estimate > max_tokens:
        # Fail closed on the token budget: never silently exceed a caller-requested --max-tokens
        # ceiling just to squeeze in an annotation. The snippet still renders, unmodified.
        return
    original_source = str(primary_snippet.get("source") or "")
    primary_snippet["source"] = (
        f"{annotation_line}\n{original_source}" if original_source else annotation_line
    )
    raw_line_map = primary_snippet.get("line_map")
    expanded_line_map = raw_line_map if isinstance(raw_line_map, list) else []
    primary_snippet["line_map"] = [
        {"line": None, "text": annotation_line},
        *expanded_line_map,
    ]
    primary_snippet["token_estimate"] = (
        int(primary_snippet.get("token_estimate", 0) or 0) + annotation_token_estimate
    )
    primary_snippet["inline_structural_annotation"] = {
        "applied": True,
        "kind": "callers",
        "callers_returned": int(call_site_evidence.get("returned_call_sites", 0) or 0),
        "callers_truncated": bool(
            int(call_site_evidence.get("omitted_call_sites", 0) or 0) > 0
            or call_site_evidence.get("partial")
        ),
        "top_callers": top_names,
    }


def build_agent_capsule(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int | None = None,
    model: str | None = None,
    include_blast_radius: bool = True,
    semantic_provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    ignore: tuple[str, ...] = (),
    deadline_seconds: float | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Thin cold-path wrapper (task #108): build the repo map (the outer of the two scans this
    capsule used to run independently -- see ``build_agent_capsule_from_map``'s docstring) and
    delegate the RANKING + suggested-scope sub-steps, which are byte-identical cold-vs-warm for
    the same map. Call-site EVIDENCE is the deliberate exception: the cold path passes
    ``_rescue_call_site_evidence=True`` so it collects through the RESCUE-equipped
    ``_collect_capsule_call_site_evidence`` (a second FS-backed ``build_symbol_blast_radius`` scan
    that literal-seed-recovers an out-of-window symbol def on a truncated no_match), recovering
    exactly the callers pre-PR ``main`` did. The warm/daemon caller keeps the single-map win by
    using the rescue-less ``_from_map`` collector and instead stamps the
    ``daemon_evidence_unreliable`` sentinel, which routes the client back to THIS cold path (see
    ``build_agent_capsule_from_map`` + ``main._maybe_agent_via_running_daemon``). Cost: the cold
    path pays its pre-PR second blast-radius scan again -- ACCEPTED, because recall on a
    scan-capped repo must not regress and the daemon path (the whole point) keeps the single map.

    ``deadline_seconds`` (CLI consistency fix, CEO v1.71.3 dogfood): `--deadline` used to be
    undefined on `tg agent` (Click "No such option" exit-2). Converted ONCE (moat P0-6 step-3
    pattern) and shared across the repo-map build AND the capsule's own render/ranking pass in
    ``build_agent_capsule_from_map`` below.

    ``deadline_monotonic`` (closes #197/#200 front-door residual): an optional PRE-ANCHORED
    absolute ``time.monotonic()`` deadline. When supplied, it is used AS-IS instead of being
    recomputed from ``deadline_seconds`` -- the CLI cold path (``main.agent``) anchors it at
    command entry, before the lazy import / path resolution / GPU-id parsing / the daemon gate, so
    that front-door time is budgeted the same way scan time already is. Existing callers that only
    pass ``deadline_seconds`` (the MCP tool, tests, the deprecated ``build_agent_capsule_json``)
    are unaffected: the fallback computation below is byte-identical to the prior behavior.
    """
    from tensor_grep.cli.repo_map import (
        DEFAULT_AGENT_REPO_MAP_LIMIT,
        _deadline_monotonic_from_seconds,
    )

    effective_max_repo_files = (
        max_repo_files if max_repo_files is not None else DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    if deadline_monotonic is None:
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    rm = repo_map.build_repo_map(
        path, max_repo_files=effective_max_repo_files, deadline_monotonic=deadline_monotonic
    )
    return build_agent_capsule_from_map(
        rm,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        max_repo_files=max_repo_files,
        model=model,
        include_blast_radius=include_blast_radius,
        semantic_provider=semantic_provider,
        gpu_device_ids=gpu_device_ids,
        gpu_timeout_s=gpu_timeout_s,
        ignore=ignore,
        deadline_monotonic=deadline_monotonic,
        _rescue_call_site_evidence=True,
    )


def build_agent_capsule_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_tokens: int | None = 1200,
    max_repo_files: int | None = None,
    model: str | None = None,
    include_blast_radius: bool = True,
    semantic_provider: str = "native",
    gpu_device_ids: list[int] | None = None,
    gpu_timeout_s: float = 5.0,
    ignore: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        build_agent_capsule(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
            model=model,
            include_blast_radius=include_blast_radius,
            semantic_provider=semantic_provider,
            gpu_device_ids=gpu_device_ids,
            gpu_timeout_s=gpu_timeout_s,
            ignore=ignore,
        ),
        ensure_ascii=False,
        indent=2,
    )
