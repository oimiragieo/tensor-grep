"""Health/proof fields for external-LSP provider status dicts.

Split out of ``lsp_external_provider`` (size limit). Pure functions over a status dict; the
provider module re-imports both names so existing importers keep working.
"""

from __future__ import annotations

from typing import Any


def _provider_health_status(status: dict[str, Any]) -> str:
    if not status.get("available"):
        return "missing"
    if status.get("last_error"):
        return "unhealthy"
    if status.get("running") and (status.get("initialized") or status.get("capabilities")):
        return "ready"
    if status.get("running"):
        return "running_unverified"
    return "available_unverified"


def _attach_lsp_proof_fields(status: dict[str, Any]) -> dict[str, Any]:
    health_status = str(status.get("health_status", _provider_health_status(status)))
    health_check = str(status.get("health_check", "not_run"))
    status.setdefault("lsp_provider_response", False)
    lsp_proof = (
        bool(status.get("available"))
        and health_status == "ready"
        and status.get("lsp_provider_response") is True
    )
    status["health_status"] = health_status
    status["health_check"] = health_check
    status["lsp_proof"] = lsp_proof
    if lsp_proof:
        status.pop("not_lsp_proof_reason", None)
        stderr_tail = [str(item) for item in status.get("stderr_tail", []) if str(item)]
        provider_warnings = [
            item
            for item in stderr_tail
            if "sre module mismatch" in item.lower()
            or "_sre" in item.lower()
            or "abi mismatch" in item.lower()
        ]
        other_stderr = [item for item in stderr_tail if item not in provider_warnings]
        if provider_warnings:
            status["provider_warnings"] = provider_warnings[-3:]
            status["provider_warning_status"] = "non_current_diagnostic"
            status["provider_warning_remediation"] = (
                "Managed provider proof succeeded, but provider stderr previously reported a "
                "Python runtime or stdlib mismatch. Re-run `tg lsp-setup` after clearing "
                "inherited PYTHONHOME/PYTHONPATH or inspect `tg doctor --with-lsp --json`."
            )
            status["stderr_tail"] = []
            status["stderr_tail_suppressed"] = True
            if other_stderr:
                status["provider_recent_stderr"] = other_stderr[-3:]
        elif stderr_tail:
            status["provider_recent_stderr"] = stderr_tail[-3:]
            status["stderr_tail"] = []
            status["stderr_tail_suppressed"] = True
        return status
    if not status.get("available"):
        reason = "Provider binary is unavailable."
    elif health_status == "available_unverified" and health_check == "not_run":
        reason = "Provider binary is available but health was not verified."
    elif health_status == "unhealthy":
        reason = "Provider semantic health probe failed or timed out."
    elif health_status == "ready" and status.get("lsp_provider_response") is not True:
        reason = "Provider initialized, but semantic health has not been verified in this session."
    else:
        reason = "Provider has not completed a successful initialization probe."
    status["not_lsp_proof_reason"] = reason
    return status
