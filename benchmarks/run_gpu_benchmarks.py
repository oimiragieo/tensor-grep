from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gpu_bench_support import (  # noqa: E402,F401
    DEFAULT_BENCHMARK_PATTERN,
    DEFAULT_CORPUS_SIZES,
    DEFAULT_CORRECTNESS_PATTERNS,
    DEFAULT_RUNS,
    DEFAULT_SEED,
    DEFAULT_SHARD_COUNT,
    DEFAULT_WARMUP,
    FAIR_RG_MULTI_PATTERN_BASELINE,
    GB,
    GPU_MANY_PATTERN_WORKLOAD_CLASS,
    GPU_PIPELINE_STAGE_FIELDS,
    GPU_RECOMMENDATION_MIN_SPEEDUP_PCT,
    GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
    GPU_SCALE_WORKLOAD_CLASS,
    KB,
    MB,
    PAYLOAD_FILLER,
    RECOMMENDATION_REQUIRED_CORPUS_SIZES,
    _build_command_env,
    _build_corpus_line,
    _clean_selected_gpu_stderr,
    _command_display,
    _format_size_label,
    _gpu_proof_status_from_summary,
    _is_skippable_cybert_exception,
    _not_gpu_proof_reason,
    _numeric_ms,
    _observed_operational_backends,
    _parse_match_output,
    _passing_required_correctness_device_ids,
    _promotion_blockers,
    _promotion_evidence_contract,
    _recreate_dir,
    _required_size_labels,
    _string_list,
    _uses_native_cuda_runtime,
    _workload_evidence_status,
    analyze_gpu_auto_recommendation,
    build_gpu_proof_summary,
    build_gpu_readiness_next_steps,
    build_gpu_workload_taxonomy,
    build_parser,
    build_rg_search_command,
    build_scale_gate_summary,
    build_tg_cpu_search_command,
    build_tg_gpu_native_stats_command,
    build_tg_gpu_search_command,
    default_binary_path,
    default_output_path,
    extract_gpu_pipeline_breakdown,
    merge_gpu_device_inventory,
    parse_corpus_sizes,
    summarize_gpu_pipeline_bottlenecks,
)
from run_benchmarks import resolve_rg_binary  # noqa: E402


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_gpu_bench_data_dir() -> Path:
    """
    Resolve GPU benchmark data location. Defaults to artifacts to avoid mutating
    tracked repository fixtures during repeated local/CI benchmark runs.
    """
    override = os.environ.get("TENSOR_GREP_GPU_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "gpu_bench_data"


def resolve_gpu_sidecar_python(raw: str | None = None) -> Path | None:
    if raw:
        return Path(raw).expanduser().resolve()

    env_value = os.environ.get("TG_SIDECAR_PYTHON")
    if env_value:
        return Path(env_value).expanduser().resolve()

    candidates = []
    if os.name == "nt":
        candidates.extend([
            ROOT_DIR / ".venv_cuda" / "Scripts" / "python.exe",
            ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ])
    else:
        candidates.extend([
            ROOT_DIR / ".venv_cuda" / "bin" / "python",
            ROOT_DIR / ".venv" / "bin" / "python",
        ])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    executable = Path(sys.executable)
    return executable.resolve() if executable.exists() else None


def generate_gpu_scale_corpus(
    output_dir: Path,
    *,
    target_bytes: int,
    shard_count: int,
) -> dict[str, object]:
    _recreate_dir(output_dir)

    file_paths = [output_dir / f"shard_{index:02d}.log" for index in range(shard_count)]
    handles = [file_path.open("w", encoding="utf-8") for file_path in file_paths]
    total_bytes = 0
    total_lines = 0
    pattern_counts = dict.fromkeys(DEFAULT_CORRECTNESS_PATTERNS, 0)

    try:
        while total_bytes < target_bytes:
            buffers = [[] for _ in range(shard_count)]
            for _ in range(2048):
                shard_id = total_lines % shard_count
                line, matched_pattern = _build_corpus_line(total_lines, shard_id)
                encoded = line.encode("utf-8")
                if total_bytes + len(encoded) > target_bytes and total_bytes >= target_bytes:
                    break
                buffers[shard_id].append(line)
                total_bytes += len(encoded)
                total_lines += 1
                if matched_pattern is not None:
                    pattern_counts[matched_pattern] += 1
                if total_bytes >= target_bytes:
                    break
            for handle, lines in zip(handles, buffers, strict=True):
                if lines:
                    handle.write("".join(lines))
            if total_bytes >= target_bytes:
                break
    finally:
        for handle in handles:
            handle.close()

    return {
        "corpus_dir": output_dir,
        "actual_bytes": total_bytes,
        "total_lines": total_lines,
        "file_count": shard_count,
        "pattern_counts": pattern_counts,
    }


def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )


def benchmark_search_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=False)
        if (
            warmup_result.returncode == 1
            and allow_no_match
            and not (warmup_result.stderr or "").strip()
        ):
            no_match_exit_accepted = True
        elif warmup_result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (warmup_result.stderr or "").strip(),
                "command": _command_display(command),
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        start = time.perf_counter()
        result = _run_command(command, env=env, capture_output=False)
        elapsed = time.perf_counter() - start
        if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
            no_match_exit_accepted = True
        elif result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [round(sample, 6) for sample in samples],
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        samples.append(round(elapsed, 6))
        last_stderr = (result.stderr or "").strip()

    return {
        "status": "PASS",
        "median_s": round(statistics.median(samples), 6),
        "samples_s": samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


def run_correctness_check(
    *,
    rg_binary: str,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
) -> dict[str, object]:
    rg_command = build_rg_search_command(rg_binary, pattern, corpus_dir)
    gpu_command = build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id)
    rg_result = _run_command(rg_command, env=env, capture_output=True)
    gpu_result = _run_command(gpu_command, env=env, capture_output=True)

    if rg_result.returncode not in (0, 1):
        return {
            "device_id": device_id,
            "pattern": pattern,
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
        }
    if gpu_result.returncode not in (0, 1):
        return {
            "device_id": device_id,
            "pattern": pattern,
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
        }

    rg_matches, rg_files = _parse_match_output(rg_result.stdout or "")
    gpu_matches, gpu_files = _parse_match_output(gpu_result.stdout or "")
    return {
        "device_id": device_id,
        "pattern": pattern,
        "status": "PASS",
        "rg_matches": rg_matches,
        "gpu_matches": gpu_matches,
        "matches_equal": rg_matches == gpu_matches,
        "files_equal": rg_files == gpu_files,
        "rg_files": rg_files,
        "gpu_files": gpu_files,
    }


def probe_tg_gpu_runtime_backend(
    *,
    tg_binary: Path,
    device_id: int,
    env: dict[str, str],
    bench_dir: Path,
) -> dict[str, object]:
    probe_dir = bench_dir / "_runtime_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "gpu_runtime_probe.log"
    probe_file.write_text("tg gpu runtime probe\n", encoding="utf-8")
    try:
        probe_path = str(probe_file.relative_to(ROOT_DIR))
    except ValueError:
        probe_path = str(probe_file)
    command = [
        str(tg_binary),
        "search",
        "--gpu-device-ids",
        str(device_id),
        "--no-ignore",
        "--json",
        "tg gpu runtime probe",
        probe_path,
    ]
    result = _run_command(command, env=env, capture_output=True)
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (result.stderr or "").strip() or "GPU runtime probe failed.",
            "command": _command_display(command),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "FAIL",
            "error": f"GPU runtime probe returned invalid JSON: {exc}",
            "command": _command_display(command),
        }
    return {
        "status": "PASS",
        "routing_backend": payload.get("routing_backend"),
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": bool(payload.get("sidecar_used", False)),
        "command": _command_display(command),
        **({"pipeline": payload["pipeline"]} if isinstance(payload.get("pipeline"), dict) else {}),
    }


def probe_tg_gpu_native_stats_pipeline(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
) -> dict[str, object]:
    command = build_tg_gpu_native_stats_command(tg_binary, [pattern], corpus_dir, [device_id])
    started_at = time.perf_counter()
    try:
        result = _run_command(command, env=env, capture_output=True)
    except OSError as exc:
        return {
            "status": "FAIL",
            "stderr": str(exc),
            "command": _command_display(command),
            "process_median_s": round(time.perf_counter() - started_at, 6),
        }
    process_median_s = round(time.perf_counter() - started_at, 6)
    command_display = _command_display(command)
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "stderr": (result.stderr or "").strip(),
            "command": command_display,
            "process_median_s": process_median_s,
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "FAIL",
            "stderr": f"native GPU stats returned invalid JSON: {exc}",
            "command": command_display,
            "process_median_s": process_median_s,
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("pipeline"), dict):
        return {
            "status": "FAIL",
            "stderr": "native GPU stats did not include pipeline metrics",
            "command": command_display,
            "process_median_s": process_median_s,
        }
    return {
        "status": "PASS",
        "pipeline": payload["pipeline"],
        "command": command_display,
        "process_median_s": process_median_s,
    }


def probe_gpu_devices(sidecar_python: Path | None) -> dict[str, object]:
    if sidecar_python is None or not sidecar_python.exists():
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": "GPU sidecar Python interpreter was not found.",
        }

    probe_script = """
import json
import warnings

payload = {"available": False, "torch_version": None, "devices": [], "warnings": []}
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    try:
        import torch

        payload["torch_version"] = torch.__version__
        payload["available"] = bool(torch.cuda.is_available())
        for device_id in range(torch.cuda.device_count()):
            entry = {
                "device_id": device_id,
                "name": torch.cuda.get_device_name(device_id),
                "capability": list(torch.cuda.get_device_capability(device_id)),
                "vram_capacity_mb": int(torch.cuda.get_device_properties(device_id).total_memory // (1024 * 1024)),
            }
            try:
                tensor = torch.zeros(1, device=f"cuda:{device_id}")
                entry["operational"] = True
                entry["probe_value"] = float(tensor.cpu()[0])
            except Exception as exc:
                entry["operational"] = False
                entry["error"] = str(exc)
            payload["devices"].append(entry)
    except Exception as exc:
        payload["error"] = str(exc)
    payload["warnings"] = [str(w.message) for w in caught]
print(json.dumps(payload))
"""
    env = _build_command_env(None)
    result = subprocess.run(
        [str(sidecar_python), "-c", probe_script],
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": (result.stderr or "").strip() or "GPU probe failed.",
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": f"GPU probe returned invalid JSON: {exc}",
        }


def probe_native_gpu_devices(*, tg_binary: Path, env: dict[str, str]) -> dict[str, object]:
    command = [str(tg_binary), "devices", "--json"]
    try:
        result = _run_command(command, env=env, capture_output=True)
    except OSError as exc:
        return {
            "available": False,
            "devices": [],
            "warnings": [f"Native GPU inventory failed: {exc}"],
            "command": _command_display(command),
        }
    if result.returncode != 0:
        return {
            "available": False,
            "devices": [],
            "warnings": [
                "Native GPU inventory failed: "
                + ((result.stderr or "").strip() or f"exit {result.returncode}")
            ],
            "command": _command_display(command),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "devices": [],
            "warnings": [f"Native GPU inventory returned invalid JSON: {exc}"],
            "command": _command_display(command),
        }

    raw_devices = payload.get("devices", [])
    if not isinstance(raw_devices, list):
        raw_devices = []
    raw_routable_ids = payload.get("routable_device_ids")
    routable_ids = (
        {
            int(device_id)
            for device_id in raw_routable_ids
            if isinstance(device_id, int) or str(device_id).isdigit()
        }
        if isinstance(raw_routable_ids, list)
        else set()
    )

    devices: list[dict[str, object]] = []
    for raw_device in raw_devices:
        if not isinstance(raw_device, dict):
            continue
        raw_device_id = raw_device.get("device_id")
        if not isinstance(raw_device_id, int) and not str(raw_device_id).isdigit():
            continue
        device_id = int(raw_device_id)
        native_operational = (
            device_id in routable_ids if routable_ids else bool(payload.get("has_gpu"))
        )
        entry: dict[str, object] = {
            "device_id": device_id,
            "name": raw_device.get("name") or f"CUDA device {device_id}",
            "native_operational": native_operational,
            "operational": native_operational,
        }
        for key in ("capability", "vram_capacity_mb"):
            if key in raw_device:
                entry[key] = raw_device[key]
        devices.append(entry)

    return {
        "available": bool(payload.get("has_gpu")) and bool(devices),
        "devices": devices,
        "warnings": [],
        "command": _command_display(command),
    }


def run_gpu_scale_benchmarks(
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    corpus_sizes: tuple[int, ...],
    runs: int,
    warmup: int,
    sidecar_python: Path | None,
    benchmark_pattern: str,
    correctness_patterns: tuple[str, ...],
    shard_count: int,
) -> dict[str, object]:
    probe = probe_gpu_devices(sidecar_python)
    command_env = _build_command_env(sidecar_python)
    torch_devices = (
        list(probe.get("devices", [])) if isinstance(probe.get("devices", []), list) else []
    )
    warnings = (
        list(probe.get("warnings", [])) if isinstance(probe.get("warnings", []), list) else []
    )
    errors: list[str] = []
    if probe.get("error"):
        warnings.append(str(probe["error"]))
    native_probe = probe_native_gpu_devices(tg_binary=tg_binary, env=command_env)
    if isinstance(native_probe.get("warnings", []), list):
        warnings.extend(str(warning) for warning in native_probe.get("warnings", []))
    native_devices = (
        list(native_probe.get("devices", []))
        if isinstance(native_probe.get("devices", []), list)
        else []
    )
    devices = merge_gpu_device_inventory(torch_devices, native_devices)
    if not any(device.get("operational", False) for device in devices):
        recommendation = {
            "should_add_flag": False,
            "reason": "Skipped because no operational GPU devices were detected.",
            "winning_rows": [],
        }
        gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks([])
        scale_gate_summary = build_scale_gate_summary(
            devices=devices,
            correctness_checks=[],
            gpu_auto_recommendation=recommendation,
            correctness_patterns=correctness_patterns,
        )
        return {
            "bench_dir": str(bench_dir),
            "corpus_sizes": [
                {"label": _format_size_label(size_bytes), "bytes": size_bytes}
                for size_bytes in corpus_sizes
            ],
            "devices": devices,
            "rows": [],
            "correctness_checks": [],
            "gpu_auto_recommendation": recommendation,
            "gpu_bottleneck_summary": gpu_bottleneck_summary,
            "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
            "scale_gate_summary": scale_gate_summary,
            **_gpu_proof_status_from_summary(scale_gate_summary),
            "gpu_proof_summary": build_gpu_proof_summary(scale_gate_summary),
            "warnings": warnings,
            "errors": errors,
            "benchmark_pattern": benchmark_pattern,
            "correctness_patterns": list(correctness_patterns),
            "timing_backend": "perf_counter",
            "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
            "torch_version": probe.get("torch_version"),
            "status": "SKIP",
            "skipped": True,
        }

    runtime_probes: dict[int, dict[str, object]] = {}
    runtime_pipeline_samples: list[dict[str, object]] = []
    scale_pipeline_samples: list[dict[str, object]] = []
    for device in devices:
        if not device.get("operational", False):
            continue
        device_id = int(device["device_id"])
        runtime_probe = probe_tg_gpu_runtime_backend(
            tg_binary=tg_binary,
            device_id=device_id,
            env=command_env,
            bench_dir=bench_dir,
        )
        runtime_probes[device_id] = runtime_probe
        device["tg_runtime_backend"] = runtime_probe.get("routing_backend")
        device["tg_runtime_reason"] = runtime_probe.get("routing_reason")
        device["tg_runtime_sidecar_used"] = runtime_probe.get("sidecar_used")
        if isinstance(runtime_probe.get("pipeline"), dict):
            sample = extract_gpu_pipeline_breakdown(
                runtime_probe,
                source="runtime_probe",
                source_label=f"GPU {device_id} runtime probe",
            )
            if sample:
                runtime_pipeline_samples.append(sample)
        if not _uses_native_cuda_runtime(device):
            warnings.append(
                "GPU scale benchmark requires a CUDA-enabled native tg binary; "
                f"device {device_id} routed to "
                f"{runtime_probe.get('routing_backend') or 'unknown'} "
                f"(sidecar_used={bool(runtime_probe.get('sidecar_used'))})."
            )
    rows: list[dict[str, object]] = []
    generated_corpora: dict[int, Path] = {}

    for size_bytes in corpus_sizes:
        size_label = _format_size_label(size_bytes)
        corpus_dir = bench_dir / size_label
        corpus_info = generate_gpu_scale_corpus(
            corpus_dir,
            target_bytes=size_bytes,
            shard_count=shard_count,
        )
        generated_corpora[size_bytes] = corpus_dir
        pattern_counts = corpus_info.get("pattern_counts")
        expected_matches = (
            int(pattern_counts.get(benchmark_pattern, 0)) > 0
            if isinstance(pattern_counts, dict)
            else True
        )
        allow_no_match = not expected_matches

        rg_result = benchmark_search_command(
            build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
            allow_no_match=allow_no_match,
        )
        tg_cpu_result = benchmark_search_command(
            build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
            allow_no_match=allow_no_match,
        )

        gpu_results: list[dict[str, object]] = []
        for device in devices:
            entry = {
                "device_id": device.get("device_id"),
                "name": device.get("name"),
                "vram_capacity_mb": device.get("vram_capacity_mb"),
                "capability": device.get("capability"),
                "tg_runtime_backend": device.get("tg_runtime_backend"),
                "tg_runtime_reason": device.get("tg_runtime_reason"),
                "tg_runtime_sidecar_used": device.get("tg_runtime_sidecar_used"),
            }
            if not device.get("operational", False):
                entry.update({
                    "status": "UNSUPPORTED",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": device.get("error", "device probe failed"),
                    "promotion_evidence": False,
                    "not_gpu_proof_reason": _not_gpu_proof_reason(
                        backend=device.get("tg_runtime_backend"),
                        sidecar_used=device.get("tg_runtime_sidecar_used"),
                    ),
                })
            elif not _uses_native_cuda_runtime(device):
                runtime_probe = runtime_probes.get(int(device["device_id"]), {})
                entry.update({
                    "status": "UNSUPPORTED",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": (
                        "GPU scale benchmark requires a CUDA-enabled native tg binary; "
                        f"runtime probe routed to "
                        f"{runtime_probe.get('routing_backend') or 'unknown'} "
                        f"(sidecar_used={bool(runtime_probe.get('sidecar_used'))})."
                    ),
                    "command": runtime_probe.get("command"),
                    "promotion_evidence": False,
                    "not_gpu_proof_reason": _not_gpu_proof_reason(
                        backend=runtime_probe.get("routing_backend"),
                        sidecar_used=runtime_probe.get("sidecar_used"),
                    ),
                })
            else:
                result = benchmark_search_command(
                    build_tg_gpu_search_command(
                        tg_binary,
                        benchmark_pattern,
                        corpus_dir,
                        int(device["device_id"]),
                    ),
                    env=command_env,
                    runs=runs,
                    warmup=warmup,
                    allow_no_match=allow_no_match,
                )
                result["stderr"] = _clean_selected_gpu_stderr(
                    result.get("stderr"),
                    devices=devices,
                    selected_device_id=int(device["device_id"]),
                    warnings=warnings,
                )
                entry.update(result)
                entry["promotion_evidence"] = True
                native_stats_probe = probe_tg_gpu_native_stats_pipeline(
                    tg_binary=tg_binary,
                    corpus_dir=corpus_dir,
                    pattern=benchmark_pattern,
                    device_id=int(device["device_id"]),
                    env=command_env,
                )
                entry["native_stats_probe"] = native_stats_probe
                if isinstance(native_stats_probe.get("pipeline"), dict):
                    entry["native_stats_pipeline"] = native_stats_probe["pipeline"]
                    sample = extract_gpu_pipeline_breakdown(
                        native_stats_probe,
                        source="scale_native_stats",
                        source_label=f"{size_label} GPU {device.get('device_id')} native stats",
                        size_label=size_label,
                        process_median_s=native_stats_probe.get("process_median_s"),
                    )
                    if sample:
                        scale_pipeline_samples.append(sample)
            gpu_results.append(entry)

        row = {
            "size_label": size_label,
            "size_bytes": size_bytes,
            "actual_bytes": corpus_info["actual_bytes"],
            "file_count": corpus_info["file_count"],
            "total_lines": corpus_info["total_lines"],
            "pattern_counts": corpus_info["pattern_counts"],
            "expected_match": expected_matches,
            "rg": rg_result,
            "tg_cpu": tg_cpu_result,
            "gpu": gpu_results,
        }

        rg_median = rg_result.get("median_s") if isinstance(rg_result, dict) else None
        tg_cpu_median = tg_cpu_result.get("median_s") if isinstance(tg_cpu_result, dict) else None
        for gpu_result in gpu_results:
            gpu_median = gpu_result.get("median_s")
            if (
                isinstance(gpu_median, (int, float))
                and isinstance(rg_median, (int, float))
                and rg_median > 0
            ):
                gpu_result["speedup_vs_rg_pct"] = round(
                    (rg_median - gpu_median) / rg_median * 100.0,
                    2,
                )
            else:
                gpu_result["speedup_vs_rg_pct"] = None
            if (
                isinstance(gpu_median, (int, float))
                and isinstance(tg_cpu_median, (int, float))
                and tg_cpu_median > 0
            ):
                gpu_result["speedup_vs_tg_cpu_pct"] = round(
                    (tg_cpu_median - gpu_median) / tg_cpu_median * 100.0,
                    2,
                )
            else:
                gpu_result["speedup_vs_tg_cpu_pct"] = None

        rows.append(row)

    correctness_corpus_sizes = [size for size in corpus_sizes if size >= 1 * GB]
    if not correctness_corpus_sizes:
        correctness_corpus_sizes = [
            next(
                (size for size in corpus_sizes if size >= 10 * MB),
                corpus_sizes[0],
            )
        ]
    correctness_checks: list[dict[str, object]] = []
    for correctness_corpus_size in correctness_corpus_sizes:
        correctness_corpus_dir = generated_corpora[correctness_corpus_size]
        for device in devices:
            if not device.get("operational", False):
                continue
            if not _uses_native_cuda_runtime(device):
                continue
            for pattern in correctness_patterns:
                check = run_correctness_check(
                    rg_binary=rg_binary,
                    tg_binary=tg_binary,
                    corpus_dir=correctness_corpus_dir,
                    pattern=pattern,
                    device_id=int(device["device_id"]),
                    env=command_env,
                )
                for diagnostic_key in ("stderr", "error"):
                    if diagnostic_key in check:
                        check[diagnostic_key] = _clean_selected_gpu_stderr(
                            check.get(diagnostic_key),
                            devices=devices,
                            selected_device_id=int(device["device_id"]),
                            warnings=warnings,
                        )
                size_label = _format_size_label(correctness_corpus_size)
                check["device_name"] = device.get("name")
                check["corpus_size_label"] = size_label
                if not (check.get("matches_equal") and check.get("files_equal")):
                    errors.append(
                        "Correctness mismatch for GPU "
                        f"{device.get('device_id')} pattern {pattern!r} at {size_label}."
                    )
                correctness_checks.append(check)

    gpu_auto_recommendation = analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=correctness_patterns,
    )
    pipeline_samples = scale_pipeline_samples or runtime_pipeline_samples
    gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks(pipeline_samples)

    scale_gate_summary = build_scale_gate_summary(
        devices=devices,
        correctness_checks=correctness_checks,
        gpu_auto_recommendation=gpu_auto_recommendation,
        correctness_patterns=correctness_patterns,
    )

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes}
            for size_bytes in corpus_sizes
        ],
        "devices": devices,
        "rows": rows,
        "correctness_checks": correctness_checks,
        "gpu_auto_recommendation": gpu_auto_recommendation,
        "gpu_bottleneck_summary": gpu_bottleneck_summary,
        "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
        "scale_gate_summary": scale_gate_summary,
        **_gpu_proof_status_from_summary(scale_gate_summary),
        "gpu_proof_summary": build_gpu_proof_summary(scale_gate_summary),
        "warnings": warnings,
        "errors": errors,
        "benchmark_pattern": benchmark_pattern,
        "correctness_patterns": list(correctness_patterns),
        "timing_backend": "perf_counter",
        "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
        "torch_version": probe.get("torch_version"),
    }


def main() -> int:
    args = build_parser().parse_args()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tg_binary = resolve_tg_binary(args.binary)
    sidecar_python = resolve_gpu_sidecar_python(args.sidecar_python)
    bench_dir = resolve_gpu_bench_data_dir()
    rg_binary = resolve_rg_binary()

    payload: dict[str, object] = {
        "artifact": "bench_gpu_scale",
        "suite": "run_gpu_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "tg_binary": str(tg_binary),
        "rg_binary": str(rg_binary),
        "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
        "runs": args.runs,
        "warmup": args.warmup,
    }

    if not tg_binary.exists():
        recommendation = {
            "should_add_flag": False,
            "reason": "Benchmark did not run because the tg binary was missing.",
            "winning_rows": [],
        }
        gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks([])
        payload.update({
            "errors": [f"tg binary not found: {tg_binary}"],
            "warnings": [],
            "rows": [],
            "correctness_checks": [],
            "corpus_sizes": [],
            "devices": [],
            "gpu_auto_recommendation": recommendation,
            "gpu_bottleneck_summary": gpu_bottleneck_summary,
            "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
            "scale_gate_summary": build_scale_gate_summary(
                devices=[],
                correctness_checks=[],
                gpu_auto_recommendation=recommendation,
            ),
        })
        scale_gate_summary = payload.get("scale_gate_summary")
        if isinstance(scale_gate_summary, dict):
            payload.update(_gpu_proof_status_from_summary(scale_gate_summary))
            payload["gpu_proof_summary"] = build_gpu_proof_summary(scale_gate_summary)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 1

    result = run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary=str(rg_binary),
        bench_dir=bench_dir,
        corpus_sizes=args.corpus_sizes,
        runs=args.runs,
        warmup=args.warmup,
        sidecar_python=sidecar_python,
        benchmark_pattern=DEFAULT_BENCHMARK_PATTERN,
        correctness_patterns=DEFAULT_CORRECTNESS_PATTERNS,
        shard_count=args.shards,
    )
    payload.update(result)
    scale_gate_summary = payload.get("scale_gate_summary")
    if isinstance(scale_gate_summary, dict):
        payload.update(_gpu_proof_status_from_summary(scale_gate_summary))
        payload["gpu_proof_summary"] = build_gpu_proof_summary(scale_gate_summary)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 1 if payload.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
