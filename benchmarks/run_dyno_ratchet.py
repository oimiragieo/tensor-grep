#!/usr/bin/env python3
"""Reproducible dyno-ratchet benchmark & promotion gate for NativeGpuBackend.

Compares `rg`, `tg_cpu`, SIMD/bit-parallel (`tg_simd`), and `NativeGpuBackend`
(`sidecar_used=False`) across corpus scales and evaluates a deterministic
promotion gate (`promotion_proof`).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_BENCHMARKS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _BENCHMARKS_DIR.parent / "src"
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from gpu_native_bench_support import (  # noqa: E402
    _extract_rg_json_match_signatures,
    _extract_tg_match_signatures,
    _signature_files,
)

from tensor_grep.cli.doctor_proof_gates import sign_dyno_promotion_report  # noqa: E402

MB = 1024 * 1024
GB = 1024 * 1024 * 1024
REQUIRED_DYNO_SCALES_BYTES: tuple[int, ...] = (1 * GB, 5 * GB)
REQUIRED_BASELINES: tuple[str, ...] = ("rg", "tg_cpu", "tg_simd")


def format_size_label(size_bytes: int) -> str:
    if size_bytes >= GB and size_bytes % GB == 0:
        return f"{size_bytes // GB}GB"
    if size_bytes >= MB and size_bytes % MB == 0:
        return f"{size_bytes // MB}MB"
    return f"{size_bytes}B"


def _parse_sizes(raw: str) -> tuple[int, ...]:
    sizes: list[int] = []
    for part in raw.split(","):
        token = part.strip().upper()
        if not token:
            continue
        if token.endswith("GB"):
            sizes.append(int(float(token[:-2]) * GB))
        elif token.endswith("MB"):
            sizes.append(int(float(token[:-2]) * MB))
        elif token.endswith("KB"):
            sizes.append(int(float(token[:-2]) * 1024))
        else:
            sizes.append(int(token))
    return tuple(sizes)


def _passing_parity_labels(
    correctness_checks: list[dict[str, Any]],
    required_labels: list[str],
) -> list[str]:
    required_set = set(required_labels)
    passing = {
        str(check.get("size_label"))
        for check in correctness_checks
        if str(check.get("size_label")) in required_set
        and check.get("status") == "PASS"
        and check.get("matches_equal") is True
        and check.get("files_equal") is True
        and check.get("rg_matches_equal") is True
        and check.get("rg_files_equal") is True
        and check.get("rg_match_identity_equal") is True
    }
    return sorted(passing, key=required_labels.index)


def _is_valid_positive_finite_seconds(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0.0
    )


def _baseline_entry_passed(entry: object) -> bool:
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


def evaluate_dyno_promotion_gate(
    rows: list[dict[str, Any]],
    correctness_checks: list[dict[str, Any]],
    *,
    required_corpus_sizes: tuple[int, ...] = REQUIRED_DYNO_SCALES_BYTES,
) -> dict[str, Any]:
    """Deterministically evaluate whether NativeGpuBackend earns promotion_proof.

    `promotion_proof` is True ONLY when:
    1. Every required scale (default: 1GB and 5GB) is present.
    2. `tg_gpu` executed on `NativeGpuBackend` with `sidecar_used=False` and `status == "PASS"`.
    3. Parity checks pass for every required scale (`rg` & `tg_cpu` match/file identity).
    4. `tg_gpu` wall-clock `median_s` is finite, positive, and strictly faster than all
       passing baselines (`rg`, `tg_cpu`, `tg_simd`) at every required scale.
    """
    required_labels = [format_size_label(s) for s in required_corpus_sizes]
    blockers: list[str] = []

    rows_by_label: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = row.get("size_label") if isinstance(row, dict) else None
        if not isinstance(label, str) or not label:
            continue
        if label in rows_by_label:
            blockers.append(f"duplicate_scale_row:{label}")
            continue
        rows_by_label[label] = row

    seen_check_labels: set[str] = set()
    duplicate_check_labels: set[str] = set()
    for check in correctness_checks:
        label = check.get("size_label") if isinstance(check, dict) else None
        if not isinstance(label, str) or not label:
            continue
        if label in seen_check_labels:
            blockers.append(f"duplicate_scale_check:{label}")
            duplicate_check_labels.add(label)
            continue
        seen_check_labels.add(label)

    passing_parity_scales = [
        label
        for label in _passing_parity_labels(correctness_checks, required_labels)
        if label not in duplicate_check_labels
    ]
    passing_parity_set = set(passing_parity_scales)

    sidecar_observed = False
    observed_backends: set[str] = set()
    passing_speed_scales: list[str] = []
    scale_comparisons: list[dict[str, Any]] = []

    for label in required_labels:
        row = rows_by_label.get(label)
        if row is None:
            blockers.append(f"missing_scale_row:{label}")
            continue

        tg_gpu = row.get("tg_gpu")
        if not isinstance(tg_gpu, dict):
            blockers.append(f"missing_tg_gpu:{label}")
            continue

        backend = str(tg_gpu.get("routing_backend") or "")
        if backend:
            observed_backends.add(backend)
        raw_sidecar = tg_gpu.get("sidecar_used")
        row_sidecar = raw_sidecar is True
        sidecar_observed = sidecar_observed or row_sidecar

        if (
            tg_gpu.get("status") != "PASS"
            or backend != "NativeGpuBackend"
            or raw_sidecar is not False
        ):
            blockers.append(f"native_gpu_not_verified:{label}")

        gpu_median = tg_gpu.get("median_s")
        if not _is_valid_positive_finite_seconds(gpu_median):
            blockers.append(f"invalid_gpu_timing:{label}")
            continue

        ratios: dict[str, float] = {}
        beats_all = True
        for baseline in REQUIRED_BASELINES:
            base_entry = row.get(baseline)
            if not _baseline_entry_passed(base_entry):
                blockers.append(f"failed_baseline:{baseline}:{label}")
                beats_all = False
                continue
            assert isinstance(base_entry, dict)
            base_median = base_entry.get("median_s")
            if not _is_valid_positive_finite_seconds(base_median):
                blockers.append(f"missing_baseline:{baseline}:{label}")
                beats_all = False
                continue
            speedup = round(float(base_median) / float(gpu_median), 4)
            ratios[f"speedup_vs_{baseline}"] = speedup
            if float(gpu_median) >= float(base_median):
                beats_all = False
                blockers.append(f"slower_than_{baseline}:{label}")

        if (
            beats_all
            and label in passing_parity_set
            and tg_gpu.get("status") == "PASS"
            and backend == "NativeGpuBackend"
            and raw_sidecar is False
        ):
            passing_speed_scales.append(label)

        scale_comparisons.append({
            "size_label": label,
            "gpu_median_s": float(gpu_median),
            "routing_backend": backend or None,
            "sidecar_used": raw_sidecar,
            **ratios,
        })

    for label in required_labels:
        if label not in passing_parity_set:
            blockers.append(f"parity_unverified:{label}")

    blockers = list(dict.fromkeys(blockers))
    promotion_proof = (
        not blockers
        and passing_parity_scales == required_labels
        and passing_speed_scales == required_labels
        and not sidecar_observed
        and observed_backends == {"NativeGpuBackend"}
    )

    if promotion_proof:
        promotion_reason = (
            f"Verified {', '.join(required_labels)} parity and wall-clock speedups over "
            f"{', '.join(REQUIRED_BASELINES)} on NativeGpuBackend (sidecar_used=False)."
        )
    elif sidecar_observed or (observed_backends and observed_backends != {"NativeGpuBackend"}):
        promotion_reason = (
            "NativeGpuBackend without sidecar was not verified across all required scales; "
            f"blockers={','.join(blockers)}"
        )
    elif passing_parity_scales != required_labels:
        promotion_reason = (
            f"Required scale parity unverified ({passing_parity_scales} != {required_labels}); "
            f"blockers={','.join(blockers)}"
        )
    else:
        promotion_reason = (
            "NativeGpuBackend did not beat all baselines (rg, tg_cpu, tg_simd) at all required "
            f"scales; blockers={','.join(blockers)}"
        )

    return {
        "promotion_proof": promotion_proof,
        "promotion_reason": promotion_reason,
        "required_scales": required_labels,
        "passing_parity_scales": passing_parity_scales,
        "passing_speed_scales": passing_speed_scales,
        "required_baselines": list(REQUIRED_BASELINES),
        "runtime_backend": "NativeGpuBackend",
        "sidecar_observed": sidecar_observed,
        "observed_backends": sorted(observed_backends),
        "blockers": blockers,
        "scale_comparisons": scale_comparisons,
    }


def _generate_corpus_file(path: Path, size_bytes: int, pattern: str) -> None:
    line_hit = f"2026-11-25 INFO {pattern} worker=0 status=ok\n".encode()
    line_miss = b"2026-11-25 INFO normal background log line status=ok\n"
    block = (line_miss * 63) + line_hit
    written = 0
    with path.open("wb") as handle:
        while written < size_bytes:
            chunk = block[: min(len(block), size_bytes - written)]
            handle.write(chunk)
            written += len(chunk)


def _time_command(cmd: list[str], *, runs: int, warmup: int) -> dict[str, Any]:
    for _ in range(warmup):
        subprocess.run(cmd, capture_output=True, text=True, check=False)
    samples: list[float] = []
    returncodes: list[int] = []
    last_stdout = ""
    last_rc = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        elapsed = time.perf_counter() - t0
        samples.append(elapsed)
        returncodes.append(proc.returncode)
        last_stdout = proc.stdout
        last_rc = proc.returncode
    all_zero = bool(returncodes) and all(rc == 0 for rc in returncodes)
    return {
        "status": "PASS" if all_zero else "FAIL",
        "exit_code": last_rc,
        "samples_s": [round(s, 6) for s in samples],
        "median_s": round(statistics.median(samples), 6) if samples else None,
        "stdout": last_stdout,
    }


def _parse_tg_json_payload(stdout: str) -> dict[str, Any] | None:
    if not stdout.strip():
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _evaluate_scale_parity(
    *,
    label: str,
    gpu_res: dict[str, Any],
    cpu_res: dict[str, Any],
    rg_res: dict[str, Any],
    gpu_payload: dict[str, Any] | None,
    routing_backend: Any,
    sidecar_used: Any,
) -> dict[str, Any]:
    cpu_payload = _parse_tg_json_payload(str(cpu_res.get("stdout") or ""))
    rg_stdout = str(rg_res.get("stdout") or "")
    rg_sigs: list[tuple[str, int, str]] | None = None
    if rg_stdout.strip():
        try:
            rg_sigs = _extract_rg_json_match_signatures(rg_stdout)
        except (json.JSONDecodeError, ValueError, TypeError):
            rg_sigs = None

    prereqs_ok = (
        gpu_res.get("status") == "PASS"
        and cpu_res.get("status") == "PASS"
        and rg_res.get("status") == "PASS"
        and routing_backend == "NativeGpuBackend"
        and sidecar_used is False
        and gpu_payload is not None
        and cpu_payload is not None
        and isinstance(gpu_payload.get("matches"), list)
        and isinstance(cpu_payload.get("matches"), list)
        and rg_sigs is not None
    )

    if not prereqs_ok:
        return {
            "size_label": label,
            "status": "FAIL",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }

    assert gpu_payload is not None
    assert cpu_payload is not None
    assert rg_sigs is not None

    gpu_sigs = _extract_tg_match_signatures(gpu_payload)
    cpu_sigs = _extract_tg_match_signatures(cpu_payload)
    gpu_files = _signature_files(gpu_sigs)
    cpu_files = _signature_files(cpu_sigs)
    rg_files = _signature_files(rg_sigs)

    gpu_total_ok = not isinstance(gpu_payload.get("total_matches"), int) or (
        gpu_payload["total_matches"] == len(gpu_sigs)
    )
    cpu_total_ok = not isinstance(cpu_payload.get("total_matches"), int) or (
        cpu_payload["total_matches"] == len(cpu_sigs)
    )

    matches_equal = (
        gpu_total_ok and cpu_total_ok and len(gpu_sigs) == len(cpu_sigs) and gpu_sigs == cpu_sigs
    )
    files_equal = len(gpu_files) == len(cpu_files) and gpu_files == cpu_files
    rg_matches_equal = gpu_total_ok and len(gpu_sigs) == len(rg_sigs)
    rg_files_equal = len(gpu_files) == len(rg_files) and gpu_files == rg_files
    rg_match_identity_equal = gpu_total_ok and gpu_sigs == rg_sigs
    all_equal = (
        matches_equal
        and files_equal
        and rg_matches_equal
        and rg_files_equal
        and rg_match_identity_equal
    )

    return {
        "size_label": label,
        "status": "PASS" if all_equal else "FAIL",
        "matches_equal": matches_equal,
        "files_equal": files_equal,
        "rg_matches_equal": rg_matches_equal,
        "rg_files_equal": rg_files_equal,
        "rg_match_identity_equal": rg_match_identity_equal,
        "gpu_match_count": len(gpu_sigs),
        "cpu_match_count": len(cpu_sigs),
        "rg_match_count": len(rg_sigs),
        "gpu_file_count": len(gpu_files),
        "cpu_file_count": len(cpu_files),
        "rg_file_count": len(rg_files),
    }


def run_dyno_ratchet(
    *,
    corpus_sizes: tuple[int, ...],
    runs: int = 3,
    warmup: int = 1,
    tg_bin: str = "tg",
    rg_bin: str = "rg",
    device_id: int = 0,
    pattern: str = "DYNO_RATCHET_TARGET",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    correctness_checks: list[dict[str, Any]] = []
    rg_available = shutil.which(rg_bin) is not None
    tg_available = shutil.which(tg_bin) is not None

    with tempfile.TemporaryDirectory(prefix="tg_dyno_ratchet_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for size_bytes in corpus_sizes:
            label = format_size_label(size_bytes)
            corpus_file = tmp_path / f"corpus_{label}.log"
            _generate_corpus_file(corpus_file, size_bytes, pattern)

            rg_res = (
                _time_command(
                    [rg_bin, "--json", pattern, str(corpus_file)], runs=runs, warmup=warmup
                )
                if rg_available
                else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
            )
            cpu_res = (
                _time_command(
                    [tg_bin, "search", "--cpu", "--json", pattern, str(corpus_file)],
                    runs=runs,
                    warmup=warmup,
                )
                if tg_available
                else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
            )
            simd_res = (
                _time_command(
                    [tg_bin, "search", "--cpu", "--json", "-F", pattern, str(corpus_file)],
                    runs=runs,
                    warmup=warmup,
                )
                if tg_available
                else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
            )
            gpu_res = (
                _time_command(
                    [
                        tg_bin,
                        "search",
                        "--gpu",
                        "--gpu-device-ids",
                        str(device_id),
                        "--json",
                        pattern,
                        str(corpus_file),
                    ],
                    runs=runs,
                    warmup=warmup,
                )
                if tg_available
                else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
            )

            gpu_payload = _parse_tg_json_payload(str(gpu_res.get("stdout") or ""))
            routing_backend = gpu_payload.get("routing_backend") if gpu_payload else None
            sidecar_used = gpu_payload.get("sidecar_used") if gpu_payload is not None else None
            sidecar_verified = gpu_payload is not None and sidecar_used is False
            rows.append({
                "size_label": label,
                "size_bytes": size_bytes,
                "rg": {
                    "status": rg_res["status"],
                    "exit_code": rg_res.get("exit_code"),
                    "median_s": rg_res.get("median_s"),
                },
                "tg_cpu": {
                    "status": cpu_res["status"],
                    "exit_code": cpu_res.get("exit_code"),
                    "median_s": cpu_res.get("median_s"),
                },
                "tg_simd": {
                    "status": simd_res["status"],
                    "exit_code": simd_res.get("exit_code"),
                    "median_s": simd_res.get("median_s"),
                },
                "tg_gpu": {
                    "status": "PASS"
                    if gpu_res["status"] == "PASS"
                    and routing_backend == "NativeGpuBackend"
                    and sidecar_verified
                    else "UNSUPPORTED",
                    "exit_code": gpu_res.get("exit_code"),
                    "median_s": gpu_res.get("median_s"),
                    "routing_backend": routing_backend,
                    "sidecar_used": sidecar_used,
                },
            })

            correctness_checks.append(
                _evaluate_scale_parity(
                    label=label,
                    gpu_res=gpu_res,
                    cpu_res=cpu_res,
                    rg_res=rg_res,
                    gpu_payload=gpu_payload,
                    routing_backend=routing_backend,
                    sidecar_used=sidecar_used,
                )
            )

    gate = evaluate_dyno_promotion_gate(rows, correctness_checks)
    report: dict[str, Any] = {
        "benchmark": "dyno_ratchet",
        "rows": rows,
        "correctness_checks": correctness_checks,
        **gate,
    }
    return sign_dyno_promotion_report(report)


def run_index_vs_rg(
    *,
    repo_root: Path,
    pattern: str = "TrigramIndex",
    runs: int = 3,
    warmup: int = 1,
    tg_bin: str = "tg",
    rg_bin: str = "rg",
) -> dict[str, Any]:
    rg_available = shutil.which(rg_bin) is not None
    tg_available = shutil.which(tg_bin) is not None
    target = str(repo_root)

    rg_res = (
        _time_command([rg_bin, "--json", pattern, target], runs=runs, warmup=warmup)
        if rg_available
        else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
    )
    tg_res = (
        _time_command(
            [tg_bin, "search", "--index", "--json", pattern, target],
            runs=runs,
            warmup=max(1, warmup),
        )
        if tg_available
        else {"status": "UNAVAILABLE", "median_s": None, "stdout": ""}
    )

    tg_payload = _parse_tg_json_payload(str(tg_res.get("stdout") or ""))
    rg_stdout = str(rg_res.get("stdout") or "")
    raw_rg_sigs = _extract_rg_json_match_signatures(rg_stdout) if rg_stdout.strip() else []
    raw_tg_sigs = _extract_tg_match_signatures(tg_payload) if tg_payload is not None else []
    rg_sigs = sorted((str(Path(f).as_posix()).lstrip("./"), ln, txt) for f, ln, txt in raw_rg_sigs)
    tg_sigs = sorted((str(Path(f).as_posix()).lstrip("./"), ln, txt) for f, ln, txt in raw_tg_sigs)
    rg_files = _signature_files(rg_sigs)
    tg_files = _signature_files(tg_sigs)

    rg_median = rg_res.get("median_s")
    tg_median = tg_res.get("median_s")
    strictly_faster = (
        _is_valid_positive_finite_seconds(rg_median)
        and _is_valid_positive_finite_seconds(tg_median)
        and float(tg_median) < float(rg_median)
    )
    speedup = (
        round(float(rg_median) / float(tg_median), 3)
        if _is_valid_positive_finite_seconds(rg_median)
        and _is_valid_positive_finite_seconds(tg_median)
        else None
    )

    return {
        "benchmark": "dyno_ratchet_index_vs_rg",
        "repo_root": target,
        "pattern": pattern,
        "rg": {
            "status": rg_res["status"],
            "exit_code": rg_res.get("exit_code"),
            "median_s": rg_median,
            "match_count": len(rg_sigs),
            "file_count": len(rg_files),
        },
        "tg_index_warm": {
            "status": tg_res["status"],
            "exit_code": tg_res.get("exit_code"),
            "median_s": tg_median,
            "match_count": len(tg_sigs),
            "file_count": len(tg_files),
        },
        "matches_equal": tg_payload is not None and tg_sigs == rg_sigs,
        "files_equal": tg_payload is not None and tg_files == rg_files,
        "strictly_faster": strictly_faster,
        "speedup": speedup,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run dyno-ratchet GPU promotion gate benchmarks.")
    parser.add_argument(
        "--mode",
        choices=["gpu-promotion", "index-vs-rg"],
        default="gpu-promotion",
        help="Benchmark mode to execute",
    )
    parser.add_argument(
        "--sizes", default="1GB,5GB", help="Comma-separated corpus sizes (e.g. 1GB,5GB)"
    )
    parser.add_argument("--runs", type=int, default=3, help="Timed runs per scale")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs per scale")
    parser.add_argument("--tg-bin", default="tg", help="Path to tg binary")
    parser.add_argument("--rg-bin", default="rg", help="Path to rg binary")
    parser.add_argument("--device-id", type=int, default=0, help="GPU device id")
    parser.add_argument("--repo-root", default=".", help="Repo root for index-vs-rg mode")
    parser.add_argument("--pattern", default="TrigramIndex", help="Pattern for index-vs-rg mode")
    parser.add_argument("--output", default="", help="Optional JSON output file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "index-vs-rg":
        report = run_index_vs_rg(
            repo_root=Path(args.repo_root),
            pattern=args.pattern,
            runs=args.runs,
            warmup=args.warmup,
            tg_bin=args.tg_bin,
            rg_bin=args.rg_bin,
        )
    else:
        sizes = _parse_sizes(args.sizes)
        report = run_dyno_ratchet(
            corpus_sizes=sizes,
            runs=args.runs,
            warmup=args.warmup,
            tg_bin=args.tg_bin,
            rg_bin=args.rg_bin,
            device_id=args.device_id,
        )
    rendered = json.dumps(report, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
