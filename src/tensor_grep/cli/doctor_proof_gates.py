"""Promotion and LSP warm-pool proof gates for `tg doctor`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "c",
    "cpp",
    "csharp",
    "php",
)
_DOCTOR_LSP_WARM_POOL_LATENCY_BUDGET_SECONDS = 15.0
_DYNO_RATCHET_REPORT_ENV = "TG_DYNO_RATCHET_REPORT_PATH"


def evaluate_doctor_dyno_promotion_status(search_ready: bool) -> tuple[bool, str]:
    """Return honest (promotion_proof, promotion_reason) for doctor GPU tier reporting."""
    if not search_ready:
        return (
            False,
            "NativeGpuBackend search_runtime_probe is not supported/ready; "
            "1 GB / 5 GB dyno-ratchet parity and speedup gate unverified.",
        )
    report_path_raw = os.environ.get(_DYNO_RATCHET_REPORT_ENV, "").strip()
    if not report_path_raw:
        return (
            False,
            "No verified dyno-ratchet 1 GB / 5 GB parity and wall-clock speedup report found; "
            "GPU search remains experimental.",
        )
    try:
        payload = json.loads(Path(report_path_raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return (False, f"Failed to read dyno-ratchet promotion report ({exc}); unverified.")
    if not isinstance(payload, dict) or payload.get("promotion_proof") is not True:
        reason = (
            str(payload.get("promotion_reason") or "").strip() if isinstance(payload, dict) else ""
        )
        return (
            False,
            reason or "Dyno-ratchet 1 GB / 5 GB parity and wall-clock speedup gate did not pass.",
        )
    return (
        True,
        str(
            payload.get("promotion_reason")
            or "Verified 1 GB / 5 GB parity and wall-clock speedups on NativeGpuBackend."
        ),
    )


def evaluate_lsp_warm_pool_proof(
    providers: list[dict[str, Any]],
    *,
    max_latency_seconds: float | None = None,
) -> dict[str, Any]:
    """Return honest aggregate lsp_proof=True ONLY when all 10 required languages pass."""
    budget_s = (
        max_latency_seconds
        if max_latency_seconds is not None and max_latency_seconds > 0
        else _DOCTOR_LSP_WARM_POOL_LATENCY_BUDGET_SECONDS
    )
    budget_ms = budget_s * 1000.0
    by_lang = {
        str(item.get("language") or "").lower(): item
        for item in providers
        if isinstance(item, dict) and item.get("language")
    }
    verified: list[str] = []
    unverified: list[str] = []
    latency_violations: list[str] = []

    for lang in _DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES:
        entry = by_lang.get(lang)
        if not isinstance(entry, dict) or entry.get("lsp_proof") is not True:
            unverified.append(lang)
            continue
        latency_ms = entry.get("probe_latency_ms")
        if latency_ms is None:
            latency_ms = entry.get("latency_ms")
        if isinstance(latency_ms, (int, float)) and float(latency_ms) > budget_ms:
            latency_violations.append(lang)
            unverified.append(lang)
            continue
        verified.append(lang)

    passed = not unverified and len(verified) == len(_DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES)
    reason: str | None = None
    if not passed:
        if latency_violations:
            reason = (
                f"LSP warm pool latency budget ({budget_s:g}s) exceeded for: "
                f"{', '.join(latency_violations)}."
            )
        else:
            reason = (
                f"LSP warm pool proof requires all {len(_DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES)} "
                f"languages to pass; unverified: {', '.join(unverified)}."
            )
    return {
        "lsp_proof": passed,
        "required_languages": list(_DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES),
        "verified_languages": verified,
        "missing_or_unverified_languages": unverified,
        "latency_violations": latency_violations,
        "latency_budget_seconds": budget_s,
        "not_lsp_proof_reason": reason,
    }
