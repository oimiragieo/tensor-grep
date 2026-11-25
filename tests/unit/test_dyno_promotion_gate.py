from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tensor_grep.cli import doctor_payload, doctor_proof_gates, doctor_report, version_info
from tensor_grep.cli.symbols_cache_auth import load_machine_key

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "benchmarks") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

import run_dyno_ratchet  # noqa: E402

GB = 1024 * 1024 * 1024


def _passing_row(
    size_label: str,
    *,
    rg_s: float = 10.0,
    cpu_s: float = 8.0,
    simd_s: float = 6.0,
    gpu_s: float = 2.0,
    routing_backend: str = "NativeGpuBackend",
    sidecar_used: bool | None = False,
    status: str = "PASS",
    exit_code: int = 0,
    size_bytes: int | None = None,
) -> dict[str, object]:
    resolved_size_bytes = (
        size_bytes if size_bytes is not None else (GB if size_label == "1GB" else 5 * GB)
    )
    return {
        "size_label": size_label,
        "size_bytes": resolved_size_bytes,
        "rg": {"status": "PASS", "exit_code": 0, "median_s": rg_s},
        "tg_cpu": {"status": "PASS", "exit_code": 0, "median_s": cpu_s},
        "tg_simd": {"status": "PASS", "exit_code": 0, "median_s": simd_s},
        "tg_gpu": {
            "status": status,
            "exit_code": exit_code,
            "median_s": gpu_s,
            "routing_backend": routing_backend,
            "sidecar_used": sidecar_used,
        },
    }


def _sign_report(payload: dict[str, Any], checkout: Path) -> dict[str, Any]:
    key = load_machine_key(checkout)
    assert key is not None, "machine-private key must be available in unit test"
    signed = dict(payload)
    signed["hmac_sha256"] = doctor_proof_gates.compute_dyno_report_hmac(signed, key)
    return signed


def _passing_check(size_label: str, *, equal: bool = True) -> dict[str, object]:
    return {
        "size_label": size_label,
        "status": "PASS" if equal else "FAIL",
        "matches_equal": equal,
        "files_equal": equal,
        "rg_matches_equal": equal,
        "rg_files_equal": equal,
        "rg_match_identity_equal": equal,
        "gpu_match_count": 42,
        "cpu_match_count": 42,
        "rg_match_count": 42 if equal else 41,
        "gpu_file_count": 1,
        "cpu_file_count": 1,
        "rg_file_count": 1,
    }


def test_dyno_promotion_gate_passes_only_when_1gb_and_5gb_parity_and_speedups_pass() -> None:
    rows = [_passing_row("1GB"), _passing_row("5GB")]
    checks = [_passing_check("1GB"), _passing_check("5GB")]

    gate = run_dyno_ratchet.evaluate_dyno_promotion_gate(rows, checks)

    assert gate["promotion_proof"] is True
    assert gate["passing_parity_scales"] == ["1GB", "5GB"]
    assert gate["passing_speed_scales"] == ["1GB", "5GB"]
    assert gate["blockers"] == []
    assert "Verified 1GB, 5GB parity" in gate["promotion_reason"]


@pytest.mark.parametrize(
    ("rows", "checks", "expected_blocker_prefix"),
    [
        (
            [_passing_row("1GB")],
            [_passing_check("1GB")],
            "missing_scale_row:5GB",
        ),
        (
            [_passing_row("1GB", sidecar_used=True), _passing_row("5GB")],
            [_passing_check("1GB"), _passing_check("5GB")],
            "native_gpu_not_verified:1GB",
        ),
        (
            [_passing_row("1GB", routing_backend="CpuBackend"), _passing_row("5GB")],
            [_passing_check("1GB"), _passing_check("5GB")],
            "native_gpu_not_verified:1GB",
        ),
        (
            [_passing_row("1GB"), _passing_row("5GB", gpu_s=7.0, simd_s=6.0)],
            [_passing_check("1GB"), _passing_check("5GB")],
            "slower_than_tg_simd:5GB",
        ),
        (
            [_passing_row("1GB"), _passing_row("5GB")],
            [_passing_check("1GB"), _passing_check("5GB", equal=False)],
            "parity_unverified:5GB",
        ),
        (
            [_passing_row("1GB", gpu_s=float("nan")), _passing_row("5GB")],
            [_passing_check("1GB"), _passing_check("5GB")],
            "invalid_gpu_timing:1GB",
        ),
        (
            [
                {
                    **_passing_row("1GB"),
                    "rg": {"status": "PASS", "median_s": float("inf")},
                },
                _passing_row("5GB"),
            ],
            [_passing_check("1GB"), _passing_check("5GB")],
            "missing_baseline:rg:1GB",
        ),
        (
            [
                {
                    **_passing_row("1GB"),
                    "tg_cpu": {"status": "FAIL", "exit_code": 2, "median_s": 10.0},
                },
                _passing_row("5GB"),
            ],
            [_passing_check("1GB"), _passing_check("5GB")],
            "failed_baseline:tg_cpu:1GB",
        ),
        (
            [
                {
                    **_passing_row("1GB"),
                    "rg": {"status": "PASS", "exit_code": 1, "median_s": 10.0},
                },
                _passing_row("5GB"),
            ],
            [_passing_check("1GB"), _passing_check("5GB")],
            "failed_baseline:rg:1GB",
        ),
    ],
)
def test_dyno_promotion_gate_fails_closed_when_thresholds_unmet(
    rows: list[dict[str, object]],
    checks: list[dict[str, object]],
    expected_blocker_prefix: str,
) -> None:
    gate = run_dyno_ratchet.evaluate_dyno_promotion_gate(rows, checks)

    assert gate["promotion_proof"] is False
    assert gate["promotion_reason"]
    assert expected_blocker_prefix in gate["blockers"]


def test_doctor_dyno_promotion_status_never_promotes_without_verified_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TG_DYNO_RATCHET_REPORT_PATH", raising=False)
    promoted, reason = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted is False
    assert "experimental" in reason.lower()

    bad_report = tmp_path / "dyno_fail.json"
    bad_report.write_text(
        json.dumps({"promotion_proof": False, "promotion_reason": "5GB parity failed"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(bad_report))
    promoted_bad, reason_bad = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_bad is False
    assert "5GB parity failed" in reason_bad

    good_payload = _sign_report(
        {
            "promotion_proof": True,
            "promotion_reason": "1GB & 5GB verified",
            "rows": [_passing_row("1GB"), _passing_row("5GB")],
            "correctness_checks": [_passing_check("1GB"), _passing_check("5GB")],
        },
        tmp_path,
    )
    good_report = tmp_path / "dyno_pass.json"
    good_report.write_text(json.dumps(good_payload), encoding="utf-8")
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(good_report))
    promoted_false_probe, _ = doctor_report.evaluate_doctor_dyno_promotion_status(False)
    assert promoted_false_probe is False

    promoted_ok, reason_ok = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_ok is True
    assert "1GB & 5GB verified" in reason_ok

    tampered_payload = dict(good_payload)
    tampered_payload["promotion_reason"] = "tampered"
    tampered_report = tmp_path / "dyno_tampered.json"
    tampered_report.write_text(json.dumps(tampered_payload), encoding="utf-8")
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(tampered_report))
    promoted_tampered, reason_tampered = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_tampered is False
    assert "hmac" in reason_tampered.lower()

    forged_report = tmp_path / "dyno_forged.json"
    forged_report.write_text(
        json.dumps({
            "promotion_proof": True,
            "promotion_reason": "1GB & 5GB verified",
            "rows": [_passing_row("1GB"), _passing_row("5GB")],
            "correctness_checks": [_passing_check("1GB"), _passing_check("5GB")],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(forged_report))
    promoted_forged, reason_forged = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_forged is False
    assert "experimental" in reason_forged.lower() or "verification" in reason_forged.lower()


@pytest.mark.parametrize(
    "case_name",
    [
        "match_count_mismatch",
        "zero_match_count",
        "file_count_mismatch",
        "nonzero_gpu_exit_code",
        "small_size_bytes",
        "under_gib_size_bytes",
        "missing_size_bytes",
        "missing_sidecar_used",
        "gpu_status_fail",
        "missing_baseline_exit_code",
        "duplicate_scale_row",
        "duplicate_scale_check",
        "malformed_observed_backends",
        "huge_int_overflow",
    ],
)
def test_doctor_dyno_promotion_status_rejects_contradictory_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case_name: str
) -> None:
    rows = [_passing_row("1GB"), _passing_row("5GB")]
    checks = [_passing_check("1GB"), _passing_check("5GB")]
    extra: dict[str, Any] = {}

    if case_name == "match_count_mismatch":
        checks[0]["rg_match_count"] = 41
    elif case_name == "zero_match_count":
        checks[0]["gpu_match_count"] = 0
        checks[0]["cpu_match_count"] = 0
        checks[0]["rg_match_count"] = 0
    elif case_name == "file_count_mismatch":
        checks[1]["rg_file_count"] = 2
    elif case_name == "nonzero_gpu_exit_code":
        gpu_entry = rows[0]["tg_gpu"]
        assert isinstance(gpu_entry, dict)
        gpu_entry["exit_code"] = 1
    elif case_name == "small_size_bytes":
        rows[0]["size_bytes"] = 1024
    elif case_name == "under_gib_size_bytes":
        rows[0]["size_bytes"] = 1_000_000_000
    elif case_name == "missing_size_bytes":
        rows[1].pop("size_bytes", None)
    elif case_name == "missing_sidecar_used":
        gpu_entry = rows[0]["tg_gpu"]
        assert isinstance(gpu_entry, dict)
        gpu_entry.pop("sidecar_used", None)
    elif case_name == "gpu_status_fail":
        gpu_entry = rows[1]["tg_gpu"]
        assert isinstance(gpu_entry, dict)
        gpu_entry["status"] = "FAIL"
    elif case_name == "missing_baseline_exit_code":
        rg_entry = rows[0]["rg"]
        assert isinstance(rg_entry, dict)
        rg_entry.pop("exit_code", None)
    elif case_name == "duplicate_scale_row":
        rows = [_passing_row("1GB", gpu_s=99.0), _passing_row("1GB"), _passing_row("5GB")]
    elif case_name == "duplicate_scale_check":
        checks = [_passing_check("1GB", equal=False), _passing_check("1GB"), _passing_check("5GB")]
    elif case_name == "malformed_observed_backends":
        extra["observed_backends"] = [{}]
    elif case_name == "huge_int_overflow":
        gpu_entry = rows[0]["tg_gpu"]
        assert isinstance(gpu_entry, dict)
        gpu_entry["median_s"] = 10**400

    contradictory = tmp_path / f"dyno_{case_name}.json"
    contradictory_payload = _sign_report(
        {
            "promotion_proof": True,
            "promotion_reason": "1GB & 5GB verified",
            "rows": rows,
            "correctness_checks": checks,
            **extra,
        },
        tmp_path,
    )
    contradictory.write_text(json.dumps(contradictory_payload), encoding="utf-8")
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(contradictory))
    promoted, reason = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted is False
    assert "verification" in reason.lower() or "experimental" in reason.lower()


def test_doctor_dyno_promotion_status_rejects_symlink_and_oversized_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    oversized = tmp_path / "dyno_oversized.json"
    oversized.write_bytes(b"{" + b" " * (4 * 1024 * 1024 + 16) + b"}")
    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(oversized))
    promoted_big, reason_big = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_big is False
    assert "limit" in reason_big.lower() or "unverified" in reason_big.lower()

    monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(tmp_path))
    promoted_dir, _ = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_dir is False

    target = tmp_path / "real_report.json"
    target.write_text(
        json.dumps(
            _sign_report(
                {
                    "promotion_proof": True,
                    "promotion_reason": "1GB & 5GB verified",
                    "rows": [_passing_row("1GB"), _passing_row("5GB")],
                    "correctness_checks": [_passing_check("1GB"), _passing_check("5GB")],
                },
                tmp_path,
            )
        ),
        encoding="utf-8",
    )
    link_path = tmp_path / "linked_report.json"
    try:
        link_path.symlink_to(target)
    except OSError:
        import os
        import stat as stat_mod

        real_lstat = os.lstat

        def _fake_lstat(path: object, *args: object, **kwargs: object) -> object:
            res = real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
            if Path(str(path)) == target:
                fake_list = list(res)
                fake_list[0] = (res.st_mode & ~0o170000) | stat_mod.S_IFLNK
                return os.stat_result(fake_list)
            return res

        monkeypatch.setattr(doctor_proof_gates.os, "lstat", _fake_lstat)
        monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(target))
    else:
        monkeypatch.setenv("TG_DYNO_RATCHET_REPORT_PATH", str(link_path))
    promoted_link, reason_link = doctor_report.evaluate_doctor_dyno_promotion_status(True)
    assert promoted_link is False
    assert "symlink" in reason_link.lower() or "regular" in reason_link.lower()


def test_lsp_warm_pool_proof_requires_all_10_languages_within_latency_budget() -> None:
    required = list(doctor_report._DOCTOR_LSP_PROOF_REQUIRED_LANGUAGES)
    assert len(required) == 10

    partial = [
        {"language": lang, "lsp_proof": True, "probe_latency_ms": 120.0} for lang in required[:-1]
    ]
    partial_result = doctor_report.evaluate_lsp_warm_pool_proof(partial)
    assert partial_result["lsp_proof"] is False
    assert required[-1] in partial_result["missing_or_unverified_languages"]

    slow = [
        {
            "language": lang,
            "lsp_proof": True,
            "probe_latency_ms": 20_000.0 if lang == "rust" else 100.0,
        }
        for lang in required
    ]
    slow_result = doctor_report.evaluate_lsp_warm_pool_proof(slow, max_latency_seconds=5.0)
    assert slow_result["lsp_proof"] is False
    assert "rust" in slow_result["latency_violations"]

    all_ok = [{"language": lang, "lsp_proof": True, "probe_latency_ms": 150.0} for lang in required]
    ok_result = doctor_report.evaluate_lsp_warm_pool_proof(all_ok, max_latency_seconds=5.0)
    assert ok_result["lsp_proof"] is True
    assert ok_result["not_lsp_proof_reason"] is None

    missing_latency = [
        {"language": lang, "lsp_proof": True}
        if lang == "go"
        else {
            "language": lang,
            "lsp_proof": True,
            "latency_ms": 120.0,
        }
        for lang in required
    ]
    missing_result = doctor_report.evaluate_lsp_warm_pool_proof(missing_latency)
    assert missing_result["lsp_proof"] is False
    assert "go" in missing_result["missing_or_unverified_languages"]

    nan_latency = [
        {"language": lang, "lsp_proof": True, "latency_ms": float("nan") if lang == "c" else 120.0}
        for lang in required
    ]
    nan_result = doctor_report.evaluate_lsp_warm_pool_proof(nan_latency)
    assert nan_result["lsp_proof"] is False
    assert "c" in nan_result["missing_or_unverified_languages"]


def test_run_dyno_ratchet_rejects_output_parity_mismatch_even_with_exit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_dyno_ratchet.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_time_command(cmd: list[str], *, runs: int, warmup: int) -> dict[str, object]:
        del runs, warmup
        corpus_path = cmd[-1]
        if cmd[0] == "rg":
            stdout = json.dumps({
                "type": "match",
                "data": {
                    "path": {"text": corpus_path},
                    "line_number": 64,
                    "lines": {"text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok\n"},
                },
            })
            return {"status": "PASS", "exit_code": 0, "median_s": 10.0, "stdout": stdout}
        if "--gpu" in cmd:
            stdout = json.dumps({
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
                "matches": [
                    {
                        "file": corpus_path,
                        "line": 999,
                        "text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok",
                    }
                ],
            })
            return {"status": "PASS", "exit_code": 0, "median_s": 1.0, "stdout": stdout}
        stdout = json.dumps({
            "matches": [
                {
                    "file": corpus_path,
                    "line": 64,
                    "text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok",
                }
            ]
        })
        return {"status": "PASS", "exit_code": 0, "median_s": 8.0, "stdout": stdout}

    monkeypatch.setattr(run_dyno_ratchet, "_time_command", _fake_time_command)
    report = run_dyno_ratchet.run_dyno_ratchet(corpus_sizes=(1024,), runs=1, warmup=0)
    assert report["promotion_proof"] is False
    check = report["correctness_checks"][0]
    assert check["status"] == "FAIL"
    assert check["matches_equal"] is False
    assert check["rg_match_identity_equal"] is False


def test_run_dyno_ratchet_rejects_missing_sidecar_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_dyno_ratchet.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fake_time_command(cmd: list[str], *, runs: int, warmup: int) -> dict[str, object]:
        del runs, warmup
        corpus_path = cmd[-1]
        if cmd[0] == "rg":
            stdout = json.dumps({
                "type": "match",
                "data": {
                    "path": {"text": corpus_path},
                    "line_number": 64,
                    "lines": {"text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok\n"},
                },
            })
            return {"status": "PASS", "exit_code": 0, "median_s": 10.0, "stdout": stdout}
        if "--gpu" in cmd:
            stdout = json.dumps({
                "routing_backend": "NativeGpuBackend",
                "matches": [
                    {
                        "file": corpus_path,
                        "line": 64,
                        "text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok",
                    }
                ],
            })
            return {"status": "PASS", "exit_code": 0, "median_s": 1.0, "stdout": stdout}
        stdout = json.dumps({
            "matches": [
                {
                    "file": corpus_path,
                    "line": 64,
                    "text": "2026-11-25 INFO DYNO_RATCHET_TARGET worker=0 status=ok",
                }
            ]
        })
        return {"status": "PASS", "exit_code": 0, "median_s": 8.0, "stdout": stdout}

    monkeypatch.setattr(run_dyno_ratchet, "_time_command", _fake_time_command)
    report = run_dyno_ratchet.run_dyno_ratchet(corpus_sizes=(1024,), runs=1, warmup=0)
    assert report["promotion_proof"] is False
    tg_gpu = report["rows"][0]["tg_gpu"]
    assert tg_gpu["sidecar_used"] is None
    assert tg_gpu["status"] == "UNSUPPORTED"
    assert report["correctness_checks"][0]["status"] == "FAIL"


def test_native_embeddings_ready_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert version_info.native_embeddings_ready() is False

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: SimpleNamespace(native_embeddings_ready=lambda: False),
    )
    assert version_info.native_embeddings_ready() is False

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: SimpleNamespace(native_embeddings_ready=lambda: True),
    )
    assert version_info.native_embeddings_ready() is True
    assert "Native Embeddings: ready" in version_info.version_detail_lines()
    assert callable(doctor_payload._render_doctor_payload)
