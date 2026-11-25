"""Promotion and LSP warm-pool proof gates for `tg doctor`."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import stat
from pathlib import Path
from typing import Any

from tensor_grep.cli.symbols_cache_auth import load_machine_key
from tensor_grep.io.confined import read_confined

_DYNO_REQUIRED_SCALES: tuple[str, ...] = ("1GB", "5GB")
_DYNO_MIN_SIZE_BYTES: dict[str, int] = {
    "1GB": 1024**3,
    "5GB": 5 * (1024**3),
}
_DYNO_REQUIRED_BASELINES: tuple[str, ...] = ("rg", "tg_cpu", "tg_simd")
_DYNO_REPORT_MAX_BYTES = 4 * 1024 * 1024
_DYNO_HMAC_FIELD = "hmac_sha256"
_DYNO_HMAC_DOMAIN = b"tg-dyno-ratchet-v1\0"

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


def compute_dyno_report_hmac(payload: dict[str, Any], key: bytes) -> str:
    """Compute machine-private HMAC-SHA256 over canonical report JSON excluding `hmac_sha256`."""
    body = {k: v for k, v in payload.items() if k != _DYNO_HMAC_FIELD}
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hmac.new(key, _DYNO_HMAC_DOMAIN + canonical, hashlib.sha256).hexdigest()


def sign_dyno_promotion_report(
    payload: dict[str, Any],
    *,
    checkout: Path | None = None,
) -> dict[str, Any]:
    """Attach `hmac_sha256` to `payload` using the machine-private signing key when available."""
    key = load_machine_key((checkout or Path.cwd()).resolve(strict=False))
    if key is None:
        return payload
    try:
        payload[_DYNO_HMAC_FIELD] = compute_dyno_report_hmac(payload, key)
    except (ValueError, TypeError, OverflowError, RecursionError):
        payload.pop(_DYNO_HMAC_FIELD, None)
    return payload


def _read_dyno_report_bytes(report_path: Path) -> bytes:
    st = os.lstat(report_path)
    if (
        stat.S_ISLNK(st.st_mode)
        or not stat.S_ISREG(st.st_mode)
        or getattr(st, "st_file_attributes", 0) & 0x400
    ):
        raise OSError("dyno-ratchet report path must be a regular non-symlink file")
    if st.st_nlink > 1:
        raise OSError("dyno-ratchet report path must not be a hardlink")
    data = read_confined(report_path.parent, report_path, _DYNO_REPORT_MAX_BYTES)
    post_st = os.lstat(report_path)
    if (
        stat.S_ISLNK(post_st.st_mode)
        or not stat.S_ISREG(post_st.st_mode)
        or getattr(post_st, "st_file_attributes", 0) & 0x400
        or post_st.st_nlink > 1
        or (post_st.st_dev, post_st.st_ino) != (st.st_dev, st.st_ino)
    ):
        raise OSError("dyno-ratchet report path changed or is a hardlink")
    return data


def _parse_positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def _baseline_passed(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get("status") != "PASS":
        return False
    rc = entry.get("exit_code")
    if isinstance(rc, bool) or not isinstance(rc, int) or rc != 0:
        return False
    if "returncode" in entry and entry["returncode"] is not None:
        returncode = entry["returncode"]
        if isinstance(returncode, bool) or not isinstance(returncode, int) or returncode != 0:
            return False
    return True


def _is_positive_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _verify_dyno_promotion_report(payload: dict[str, Any]) -> str | None:
    """Independently verify 1 GB & 5 GB parity, speedups, and NativeGpuBackend evidence."""
    try:
        return _verify_dyno_promotion_report_inner(payload)
    except (OverflowError, TypeError):
        return "invalid_report_structure"


def _verify_dyno_promotion_report_inner(payload: dict[str, Any]) -> str | None:
    if payload.get("sidecar_observed") is True:
        return "sidecar_observed=True"
    observed_backends = payload.get("observed_backends")
    if observed_backends is not None and (
        not isinstance(observed_backends, list)
        or not all(isinstance(item, str) for item in observed_backends)
        or set(observed_backends) != {"NativeGpuBackend"}
    ):
        return "observed_backends_mismatch"

    rows = payload.get("rows")
    checks = payload.get("correctness_checks")
    if not isinstance(rows, list) or not isinstance(checks, list):
        return "missing_rows_or_correctness_checks"

    rows_by_label: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return "invalid_row_entry"
        label = row.get("size_label")
        if not isinstance(label, str) or not label:
            return "invalid_row_size_label"
        if label in rows_by_label:
            return f"duplicate_scale_row:{label}"
        rows_by_label[label] = row

    checks_by_label: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict):
            return "invalid_correctness_check_entry"
        label = check.get("size_label")
        if not isinstance(label, str) or not label:
            return "invalid_check_size_label"
        if label in checks_by_label:
            return f"duplicate_scale_check:{label}"
        checks_by_label[label] = check

    for label in _DYNO_REQUIRED_SCALES:
        row = rows_by_label.get(label)
        if row is None:
            return f"missing_scale_row:{label}"
        size_bytes = row.get("size_bytes")
        min_bytes = _DYNO_MIN_SIZE_BYTES[label]
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < min_bytes
        ):
            return f"invalid_scale_size_bytes:{label}"
        tg_gpu = row.get("tg_gpu")
        if not isinstance(tg_gpu, dict):
            return f"missing_tg_gpu:{label}"
        gpu_exit_code = tg_gpu.get("exit_code")
        if (
            tg_gpu.get("status") != "PASS"
            or isinstance(gpu_exit_code, bool)
            or not isinstance(gpu_exit_code, int)
            or gpu_exit_code != 0
            or tg_gpu.get("routing_backend") != "NativeGpuBackend"
            or tg_gpu.get("sidecar_used") is not False
        ):
            return f"native_gpu_not_verified:{label}"
        gpu_median = _parse_positive_finite(tg_gpu.get("median_s"))
        if gpu_median is None:
            return f"invalid_gpu_timing:{label}"

        for baseline in _DYNO_REQUIRED_BASELINES:
            base_entry = row.get(baseline)
            if not _baseline_passed(base_entry):
                return f"failed_baseline:{baseline}:{label}"
            assert isinstance(base_entry, dict)
            base_median = _parse_positive_finite(base_entry.get("median_s"))
            if base_median is None:
                return f"invalid_baseline_timing:{baseline}:{label}"
            if gpu_median >= base_median:
                return f"slower_than_{baseline}:{label}"

        check = checks_by_label.get(label)
        if check is None or (
            check.get("status") != "PASS"
            or check.get("matches_equal") is not True
            or check.get("files_equal") is not True
            or check.get("rg_matches_equal") is not True
            or check.get("rg_files_equal") is not True
            or check.get("rg_match_identity_equal") is not True
        ):
            return f"parity_unverified:{label}"

        gpu_matches = check.get("gpu_match_count")
        cpu_matches = check.get("cpu_match_count")
        rg_matches = check.get("rg_match_count")
        if (
            not _is_positive_int(gpu_matches)
            or not _is_positive_int(cpu_matches)
            or not _is_positive_int(rg_matches)
            or not (gpu_matches == cpu_matches == rg_matches)
        ):
            return f"parity_count_mismatch:{label}"

        file_count_keys = ("gpu_file_count", "cpu_file_count", "rg_file_count")
        if any(key in check for key in file_count_keys):
            gpu_files = check.get("gpu_file_count")
            cpu_files = check.get("cpu_file_count")
            rg_files = check.get("rg_file_count")
            if (
                not _is_positive_int(gpu_files)
                or not _is_positive_int(cpu_files)
                or not _is_positive_int(rg_files)
                or not (gpu_files == cpu_files == rg_files)
            ):
                return f"parity_file_count_mismatch:{label}"
    return None


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
    report_path = Path(report_path_raw)
    try:
        raw_bytes = _read_dyno_report_bytes(report_path)
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        return (False, f"Failed to read dyno-ratchet promotion report ({exc}); unverified.")
    if not isinstance(payload, dict) or payload.get("promotion_proof") is not True:
        reason = (
            str(payload.get("promotion_reason") or "").strip() if isinstance(payload, dict) else ""
        )
        return (
            False,
            reason or "Dyno-ratchet 1 GB / 5 GB parity and wall-clock speedup gate did not pass.",
        )
    key = load_machine_key(report_path.resolve(strict=False).parent)
    if key is None:
        return (
            False,
            "Machine-private signing key unavailable; dyno-ratchet report cannot be verified; "
            "GPU search remains experimental.",
        )
    signature = payload.get(_DYNO_HMAC_FIELD)
    if not isinstance(signature, str) or not signature:
        return (
            False,
            "Dyno-ratchet report missing HMAC-SHA256 signature; GPU search remains experimental.",
        )
    try:
        expected_signature = compute_dyno_report_hmac(payload, key)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return (
            False,
            "Dyno-ratchet report failed HMAC canonicalization; GPU search remains experimental.",
        )
    if not hmac.compare_digest(expected_signature, signature):
        return (
            False,
            "Dyno-ratchet report failed HMAC-SHA256 signature verification; "
            "GPU search remains experimental.",
        )
    evidence_blocker = _verify_dyno_promotion_report(payload)
    if evidence_blocker is not None:
        return (
            False,
            f"Dyno-ratchet report failed independent evidence verification ({evidence_blocker}); "
            "GPU search remains experimental.",
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
        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(float(latency_ms))
            or float(latency_ms) < 0.0
        ):
            unverified.append(lang)
            continue
        if float(latency_ms) > budget_ms:
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
