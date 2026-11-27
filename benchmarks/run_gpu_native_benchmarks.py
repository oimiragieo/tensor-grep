from __future__ import annotations

import json
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

from gpu_native_bench_gates import (  # noqa: E402,F401
    _build_long_line,
    _get_row_for_size,
    _gpu_proof_status_from_native_summary,
    _many_pattern_proof_gate_from_advanced,
    _native_runtime_gate,
    _native_speed_gate,
    _passing_correctness_size_labels,
    _promotion_blockers,
    _promotion_evidence_contract,
    _required_size_labels,
    _string_list,
    _workload_evidence_status,
    analyze_crossover,
    analyze_throughput_target,
    build_gpu_proof_summary,
    build_many_pattern_proof_gate,
    build_native_scale_gate_summary,
    build_parser,
    build_public_managed_gpu_proof_gate,
    collect_gpu_native_pipeline_samples,
    create_advanced_throughput_corpus,
    create_cuda_graph_corpus,
    create_long_line_corpus,
)

# Re-exported from gpu_native_bench_support.py / gpu_native_bench_gates.py
# (file-size wave 3 split) so existing module-attribute access
# (module.GB, module.build_parser, module.DEFAULT_CORPUS_SIZES, ...) and the
# test suite's monkeypatch.setattr(module, "<name>", ...) sites keep
# resolving through this facade unchanged. Every name imported here was
# verified never to call the native benchmark's monkeypatched I/O-boundary
# functions (_run_command, benchmark_search_command, run_correctness_check,
# run_many_pattern_correctness_check, run_gpu_error_tests,
# probe_native_gpu_runtime_backend, ...), which is why moving their
# definitions out of this file is behavior-neutral.
from gpu_native_bench_support import (  # noqa: E402,F401
    DEFAULT_ADVANCED_GRAPH_BATCH_BYTES,
    DEFAULT_ADVANCED_GRAPH_FILE_COUNT,
    DEFAULT_ADVANCED_GRAPH_PATTERN,
    DEFAULT_ADVANCED_LONG_LINE_PATTERN,
    DEFAULT_ADVANCED_LONG_LINE_TARGET_BYTES,
    DEFAULT_ADVANCED_OOM_BYTES,
    DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
    DEFAULT_ADVANCED_THROUGHPUT_MAX_BATCH_BYTES,
    DEFAULT_ADVANCED_THROUGHPUT_PATTERN_COUNT,
    DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES,
    DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES,
    DEFAULT_COMMAND_TIMEOUT_S,
    DEFAULT_CORPUS_SIZES,
    DEFAULT_GPU_DEVICE_ID,
    DEFAULT_MULTI_GPU_DEVICE_ID,
    DEFAULT_RUNS,
    DEFAULT_TIMEOUT_DESCRIPTION,
    DEFAULT_TIMEOUT_SIMULATION_MS,
    DEFAULT_WARMUP,
    GPU_TIMEOUT_OPTIMIZATIONS,
    MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG,
    MIN_MULTI_GPU_IMPROVEMENT_PCT,
    NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
    NATIVE_SCALE_WORKLOAD_CLASS,
    _as_int,
    _build_command_env,
    _command_display,
    _extract_rg_json_match_signatures,
    _extract_tg_match_signatures,
    _format_size_label,
    _infer_total_files,
    _lookup_nested_float,
    _native_gpu_route_failure,
    _normalized_match_path,
    _normalized_match_text,
    _parse_json_payload,
    _signature_file_count,
    _signature_files,
    _timeout_stderr,
    build_rg_json_command,
    build_rg_multi_pattern_json_command,
    build_rg_multi_pattern_search_command,
    build_rg_search_command,
    build_tg_cpu_search_command,
    build_tg_gpu_cuda_graph_benchmark_command,
    build_tg_gpu_native_stats_command,
    build_tg_gpu_oom_probe_command,
    build_tg_gpu_search_command,
    build_tg_gpu_transfer_benchmark_command,
    build_tg_json_command,
    build_tg_multi_pattern_json_command,
    build_unsupported_native_gpu_error_tests,
    create_error_fixture,
    create_runtime_probe_fixture,
    default_output_path,
    resolve_gpu_native_bench_data_dir,
)
from run_benchmarks import resolve_rg_binary, resolve_tg_binary  # noqa: E402

# Not all of these are referenced by a function defined directly in this
# facade, but they are kept as re-exports because the test suite accesses
# them as module attributes (module.build_gpu_workload_taxonomy(),
# module.parse_corpus_sizes, ...) on whichever module instance it loads via
# spec_from_file_location on this file's path.
from run_gpu_benchmarks import (  # noqa: E402,F401
    DEFAULT_BENCHMARK_PATTERN,
    DEFAULT_CORRECTNESS_PATTERNS,
    DEFAULT_SHARD_COUNT,
    FAIR_RG_MULTI_PATTERN_BASELINE,
    GB,
    GPU_MANY_PATTERN_WORKLOAD_CLASS,
    GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
    MB,
    build_gpu_readiness_next_steps,
    build_gpu_workload_taxonomy,
    extract_gpu_pipeline_breakdown,
    generate_gpu_scale_corpus,
    parse_corpus_sizes,
    summarize_gpu_pipeline_bottlenecks,
)

from tensor_grep.cli.incompleteness import disclosed_incomplete  # noqa: E402
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary  # noqa: E402


def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    capture_output: bool,
    timeout_s: int,
) -> subprocess.CompletedProcess[str] | subprocess.TimeoutExpired:
    try:
        return subprocess.run(
            command,
            cwd=ROOT_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return exc


def benchmark_search_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    corpus_bytes: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
        if isinstance(warmup_result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
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
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        started_at = time.perf_counter()
        result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
        elapsed = round(time.perf_counter() - started_at, 6)
        if isinstance(result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
            no_match_exit_accepted = True
        # Task #276 slice C0. This file ALREADY special-cases exit 2 for classified causes (see
        # the invalid-device probe at :1209) -- the pattern simply never reached the search
        # timing path. Same allow-list rule: disclosed incompleteness is not a benchmark FAIL.
        elif result.returncode == 2 and disclosed_incomplete(result.stdout, result.stderr):
            pass
        elif result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        samples.append(elapsed)
        last_stderr = (result.stderr or "").strip()

    median_s = round(statistics.median(samples), 6)
    throughput = round(corpus_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "throughput_bytes_s": throughput,
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


def probe_native_gpu_runtime_backend(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    command = build_tg_json_command(tg_binary, pattern, corpus_dir, device_id=device_id)
    result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
    command_display = _command_display(command)
    if isinstance(result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": _timeout_stderr(timeout_s),
            "command": command_display,
        }
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": (result.stderr or "").strip(),
            "command": command_display,
        }
    try:
        payload = _parse_json_payload(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": f"failed to parse GPU runtime JSON: {exc}",
            "command": command_display,
        }
    route_failure = _native_gpu_route_failure(payload)
    if route_failure is not None:
        route_failure["command"] = command_display
        if isinstance(payload.get("pipeline"), dict):
            route_failure["pipeline"] = payload["pipeline"]
        return route_failure
    probe: dict[str, object] = {
        "status": "PASS",
        "routing_backend": str(payload.get("routing_backend") or "NativeGpuBackend"),
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": bool(payload.get("sidecar_used", False)),
        "command": command_display,
    }
    if isinstance(payload.get("pipeline"), dict):
        probe["pipeline"] = payload["pipeline"]
    return probe


def _run_json_command(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
    if isinstance(result, subprocess.TimeoutExpired):
        raise RuntimeError(f"command timed out after {timeout_s}s: {_command_display(command)}")
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or f"command failed: {_command_display(command)}"
        )
    return _parse_json_payload(result.stdout or "{}")


def benchmark_json_metric_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    corpus_bytes: int,
    metric_path: tuple[str, ...],
    metric_scale: float = 1.0,
) -> dict[str, object]:
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
        if isinstance(warmup_result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }
        if warmup_result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (warmup_result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

    samples: list[float] = []
    process_samples: list[float] = []
    last_stderr = ""
    last_payload: dict[str, object] = {}
    for _ in range(runs):
        started_at = time.perf_counter()
        result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
        process_elapsed = round(time.perf_counter() - started_at, 6)
        if isinstance(result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }
        if result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

        payload = _parse_json_payload(result.stdout or "{}")
        metric_value = _lookup_nested_float(payload, *metric_path)
        if metric_value is None:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": f"missing metric at {'.'.join(metric_path)}",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

        samples.append(round(metric_value * metric_scale, 6))
        process_samples.append(process_elapsed)
        last_payload = payload
        last_stderr = (result.stderr or "").strip()

    median_s = round(statistics.median(samples), 6)
    process_median_s = round(statistics.median(process_samples), 6) if process_samples else None
    throughput = round(corpus_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "process_median_s": process_median_s,
        "process_samples_s": process_samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "throughput_bytes_s": throughput,
        "payload": last_payload,
    }


def benchmark_command_group(
    commands: list[list[str]],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    workload_bytes: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        for command in commands:
            warmup_result = _run_command(
                command, env=env, capture_output=False, timeout_s=timeout_s
            )
            if isinstance(warmup_result, subprocess.TimeoutExpired):
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": f"command timed out after {timeout_s}s",
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
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
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        started_at = time.perf_counter()
        for command in commands:
            result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
            if isinstance(result, subprocess.TimeoutExpired):
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": samples,
                    "stderr": f"command timed out after {timeout_s}s",
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
            if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
                no_match_exit_accepted = True
            elif result.returncode != 0:
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": samples,
                    "stderr": (result.stderr or "").strip(),
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
            last_stderr = (result.stderr or "").strip()
        elapsed = round(time.perf_counter() - started_at, 6)
        samples.append(elapsed)

    median_s = round(statistics.median(samples), 6)
    throughput = round(workload_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "stderr": last_stderr,
        "command_group": [_command_display(candidate) for candidate in commands],
        "throughput_bytes_s": throughput,
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


def cpu_oracle_search(
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[tuple[str, int, str]]:
    """Independent, obviously-correct CPU oracle for fixed-string multi-pattern search.

    Walks every file under corpus_dir using plain Python string iteration and
    the built-in ``in`` operator (str.find semantics).  This function is the
    ground-truth reference that BOTH the existing brute-force GPU kernel and
    the future PFAC kernel must agree with.  It is intentionally written with
    no dependency on rg, the GPU kernel, or any search library so it can serve
    as an independent third party in correctness comparisons.

    Returns sorted (normalized_path, line_number, normalized_line_text) tuples
    using the same normalization helpers used by _extract_tg_match_signatures
    and _extract_rg_json_match_signatures.  Each line is reported at most once,
    regardless of how many of the supplied patterns it matches — matching the
    semantics of ``rg -F -e p1 -e p2 …``.

    Line numbers are 1-indexed, consistent with rg --json output.
    """
    if not patterns:
        return []
    signatures: list[tuple[str, int, str]] = []
    for file_path in sorted(corpus_dir.rglob("*")):
        if not file_path.is_file():
            continue
        # Skip dot-prefixed files and files inside dot-prefixed directories,
        # matching rg's default behaviour of ignoring hidden paths.
        if any(part.startswith(".") for part in file_path.parts):
            continue
        # Skip binary files: rg skips files whose content contains a NUL byte.
        try:
            probe = file_path.read_bytes()[:8192]
        except OSError:
            continue
        if b"\x00" in probe:
            continue
        # Decode as latin-1 (never raises; each byte maps 1-to-1 to a Unicode code
        # point).  errors="replace" diverges from rg's match text for invalid UTF-8
        # sequences because it rewrites them to U+FFFD, whereas rg surfaces the raw
        # bytes; latin-1 preserves the raw byte values faithfully.
        try:
            text = file_path.read_text(encoding="latin-1")
        except OSError:
            continue
        path_str = _normalized_match_path(str(file_path))
        # Split on \n only, matching rg's line-splitting behaviour.
        # str.splitlines() also splits on \r, \v, \f, \x1c-\x1e, \x85,
        # U+2028, U+2029 — none of which rg treats as line boundaries.
        # Drop the trailing empty element produced by a final \n.
        raw_lines = text.split("\n")
        if raw_lines and raw_lines[-1] == "":
            raw_lines = raw_lines[:-1]
        for line_number, raw_line in enumerate(raw_lines, start=1):
            line_text = _normalized_match_text(raw_line)
            for pattern in patterns:
                if pattern in raw_line:
                    signatures.append((path_str, line_number, line_text))
                    break  # each line reported at most once (rg -F -e … -e … semantics)
    return sorted(signatures)


def run_correctness_check(
    *,
    tg_binary: Path,
    rg_binary: str = "rg",
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    cpu_result = _run_command(
        build_tg_json_command(tg_binary, pattern, corpus_dir, force_cpu=True),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    gpu_result = _run_command(
        build_tg_json_command(tg_binary, pattern, corpus_dir, device_id=device_id),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )

    if isinstance(cpu_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"CPU correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
        }
    if isinstance(gpu_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"GPU correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
        }
    if cpu_result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (cpu_result.stderr or "").strip(),
            "matches_equal": False,
        }
    if gpu_result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
        }

    cpu_payload = _parse_json_payload(cpu_result.stdout or "{}")
    gpu_payload = _parse_json_payload(gpu_result.stdout or "{}")
    route_failure = _native_gpu_route_failure(gpu_payload)
    if route_failure is not None:
        return {
            **route_failure,
            "matches_equal": False,
            "files_equal": False,
        }

    rg_result = _run_command(
        build_rg_json_command(rg_binary, pattern, corpus_dir),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    if isinstance(rg_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"rg correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if rg_result.returncode not in {0, 1}:
        return {
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }

    cpu_total_matches = int(cpu_payload.get("total_matches", 0))
    gpu_total_matches = int(gpu_payload.get("total_matches", 0))
    cpu_total_files = _infer_total_files(cpu_payload)
    gpu_total_files = _infer_total_files(gpu_payload)
    cpu_signatures = _extract_tg_match_signatures(cpu_payload)
    gpu_signatures = _extract_tg_match_signatures(gpu_payload)
    rg_signatures = _extract_rg_json_match_signatures(rg_result.stdout or "")
    cpu_gpu_matches_equal = cpu_signatures == gpu_signatures
    cpu_gpu_files_equal = cpu_total_files == gpu_total_files
    rg_matches_equal = rg_signatures == gpu_signatures
    rg_files_equal = _signature_file_count(rg_signatures) == gpu_total_files
    # Independent CPU oracle: plain Python fixed-string search, no dependency on rg
    # or the GPU kernel.  oracle_status is PASS iff the oracle agrees with rg.
    try:
        oracle_signatures = cpu_oracle_search([pattern], corpus_dir)
        oracle_matches_equal = oracle_signatures == rg_signatures
        oracle_status: str = "PASS" if oracle_matches_equal else "FAIL"
    except Exception:
        oracle_signatures = []
        oracle_matches_equal = False
        oracle_status = "ERROR"
    return {
        "status": (
            "PASS"
            if cpu_total_matches == gpu_total_matches
            and cpu_gpu_matches_equal
            and cpu_gpu_files_equal
            and rg_matches_equal
            and rg_files_equal
            and oracle_status == "PASS"
            else "FAIL"
        ),
        "cpu_total_matches": cpu_total_matches,
        "gpu_total_matches": gpu_total_matches,
        "rg_total_matches": len(rg_signatures),
        "cpu_total_files": cpu_total_files,
        "gpu_total_files": gpu_total_files,
        "rg_total_files": _signature_file_count(rg_signatures),
        "matches_equal": cpu_total_matches == gpu_total_matches and cpu_gpu_matches_equal,
        "files_equal": cpu_gpu_files_equal,
        "rg_matches_equal": rg_matches_equal,
        "rg_files_equal": rg_files_equal,
        "rg_match_identity_equal": rg_matches_equal,
        "oracle_status": oracle_status,
        "oracle_total_matches": len(oracle_signatures),
        "oracle_total_files": _signature_file_count(oracle_signatures),
        "oracle_matches_equal": oracle_matches_equal,
    }


def run_many_pattern_correctness_check(
    *,
    tg_binary: Path,
    rg_binary: str,
    corpus_dir: Path,
    patterns: list[str] | tuple[str, ...],
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    cpu_result = _run_command(
        build_tg_multi_pattern_json_command(
            tg_binary,
            patterns,
            corpus_dir,
            force_cpu=True,
        ),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    gpu_result = _run_command(
        build_tg_multi_pattern_json_command(
            tg_binary,
            patterns,
            corpus_dir,
            device_id=device_id,
        ),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    rg_result = _run_command(
        build_rg_multi_pattern_json_command(rg_binary, patterns, corpus_dir),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    base_payload: dict[str, object] = {
        "workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "patterns": list(patterns),
        "fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
    }
    if isinstance(cpu_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"CPU many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if isinstance(gpu_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"GPU many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if isinstance(rg_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"rg many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if cpu_result.returncode != 0:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (cpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if gpu_result.returncode != 0:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if rg_result.returncode not in {0, 1}:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }

    cpu_payload = _parse_json_payload(cpu_result.stdout or "{}")
    gpu_payload = _parse_json_payload(gpu_result.stdout or "{}")
    route_failure = _native_gpu_route_failure(gpu_payload)
    if route_failure is not None:
        return {
            **base_payload,
            **route_failure,
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    cpu_signatures = _extract_tg_match_signatures(cpu_payload)
    gpu_signatures = _extract_tg_match_signatures(gpu_payload)
    rg_signatures = _extract_rg_json_match_signatures(rg_result.stdout or "")
    cpu_gpu_matches_equal = cpu_signatures == gpu_signatures
    cpu_gpu_files_equal = _signature_files(cpu_signatures) == _signature_files(gpu_signatures)
    rg_matches_equal = rg_signatures == gpu_signatures
    rg_files_equal = _signature_files(rg_signatures) == _signature_files(gpu_signatures)
    # Independent CPU oracle: plain Python fixed-string search, no dependency on rg
    # or the GPU kernel.  oracle_status is PASS iff the oracle agrees with rg.
    try:
        oracle_signatures = cpu_oracle_search(list(patterns), corpus_dir)
        oracle_matches_equal = oracle_signatures == rg_signatures
        oracle_status: str = "PASS" if oracle_matches_equal else "FAIL"
    except Exception:
        oracle_signatures = []
        oracle_matches_equal = False
        oracle_status = "ERROR"
    return {
        **base_payload,
        "status": (
            "PASS"
            if cpu_gpu_matches_equal
            and cpu_gpu_files_equal
            and rg_matches_equal
            and rg_files_equal
            and oracle_status == "PASS"
            else "FAIL"
        ),
        "cpu_total_matches": len(cpu_signatures),
        "gpu_total_matches": len(gpu_signatures),
        "rg_total_matches": len(rg_signatures),
        "cpu_total_files": _signature_file_count(cpu_signatures),
        "gpu_total_files": _signature_file_count(gpu_signatures),
        "rg_total_files": _signature_file_count(rg_signatures),
        "matches_equal": cpu_gpu_matches_equal,
        "files_equal": cpu_gpu_files_equal,
        "rg_matches_equal": rg_matches_equal,
        "rg_files_equal": rg_files_equal,
        "rg_match_identity_equal": rg_matches_equal,
        "oracle_status": oracle_status,
        "oracle_total_matches": len(oracle_signatures),
        "oracle_total_files": _signature_file_count(oracle_signatures),
        "oracle_matches_equal": oracle_matches_equal,
    }


def run_gpu_error_tests(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    device_id: int,
    timeout_s: int,
    timeout_simulation_ms: int,
) -> dict[str, object]:
    base_env = _build_command_env()
    pattern = DEFAULT_BENCHMARK_PATTERN

    invalid_device = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, 99),
        env=base_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    invalid_device_status = "FAIL"
    invalid_device_stderr = _timeout_stderr(timeout_s)
    invalid_device_code = None
    if not isinstance(invalid_device, subprocess.TimeoutExpired):
        invalid_device_stderr = (invalid_device.stderr or "").strip()
        invalid_device_code = invalid_device.returncode
        invalid_device_status = (
            "PASS"
            if invalid_device.returncode == 2
            and "99" in invalid_device_stderr
            and "available CUDA devices" in invalid_device_stderr
            else "FAIL"
        )

    nvrtc_env = _build_command_env({
        "TG_TEST_CUDA_BEHAVIOR": "nvrtc-failure:simulated NVRTC compile error"
    })
    nvrtc_failure = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=nvrtc_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    nvrtc_status = "FAIL"
    nvrtc_stderr = _timeout_stderr(timeout_s)
    nvrtc_code = None
    if not isinstance(nvrtc_failure, subprocess.TimeoutExpired):
        nvrtc_stderr = (nvrtc_failure.stderr or "").strip()
        nvrtc_code = nvrtc_failure.returncode
        nvrtc_status = (
            "PASS"
            if nvrtc_failure.returncode == 2
            and "CUDA kernel compilation failed" in nvrtc_stderr
            and "simulated NVRTC compile error" in nvrtc_stderr
            else "FAIL"
        )

    timeout_env = _build_command_env({
        "TG_TEST_CUDA_BEHAVIOR": f"timeout:{timeout_simulation_ms}ms"
    })
    timeout_result = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=timeout_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    timeout_status = "FAIL"
    timeout_stderr = _timeout_stderr(timeout_s)
    timeout_code = None
    if not isinstance(timeout_result, subprocess.TimeoutExpired):
        timeout_stderr = (timeout_result.stderr or "").strip()
        timeout_code = timeout_result.returncode
        timeout_status = (
            "PASS"
            if timeout_result.returncode == 2 and "timed out" in timeout_stderr.lower()
            else "FAIL"
        )

    malformed_dir = create_error_fixture(corpus_dir / "error_cases")
    malformed_gpu = _run_command(
        build_tg_json_command(tg_binary, pattern, malformed_dir, device_id=device_id),
        env=base_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    malformed_payload: dict[str, object] = {
        "status": "FAIL",
        "simulated": False,
    }
    if not isinstance(malformed_gpu, subprocess.TimeoutExpired):
        if malformed_gpu.returncode == 0:
            gpu_payload = _parse_json_payload(malformed_gpu.stdout or "{}")
            malformed_status = "PASS"
            malformed_payload = {
                "status": malformed_status,
                "exit_code": malformed_gpu.returncode,
                "simulated": False,
                "gpu_total_matches": int(gpu_payload.get("total_matches", 0)),
                "gpu_total_files": int(gpu_payload.get("total_files", 0)),
            }
        else:
            malformed_payload = {
                "status": "FAIL",
                "exit_code": malformed_gpu.returncode,
                "simulated": False,
                "stderr": (malformed_gpu.stderr or "").strip(),
            }
    else:
        malformed_payload = {
            "status": "FAIL",
            "exit_code": None,
            "simulated": False,
            "stderr": _timeout_stderr(timeout_s),
        }

    return {
        "invalid_device": {
            "status": invalid_device_status,
            "exit_code": invalid_device_code,
            "stderr": invalid_device_stderr,
            "simulated": False,
        },
        "nvrtc_failure": {
            "status": nvrtc_status,
            "exit_code": nvrtc_code,
            "stderr": nvrtc_stderr,
            "simulated": True,
        },
        "timeout": {
            "status": timeout_status,
            "exit_code": timeout_code,
            "stderr": timeout_stderr,
            "simulated": True,
            "timeout_ms": timeout_simulation_ms,
        },
        "malformed_inputs": malformed_payload,
    }


def _current_facade() -> object:
    mod = sys.modules.get(__name__)
    if mod is not None:
        return mod

    class _ModuleNamespace:
        def __getattr__(self, name: str) -> object:
            try:
                return globals()[name]
            except KeyError as exc:
                raise AttributeError(name) from exc

    return _ModuleNamespace()


def run_gpu_native_benchmarks(
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    corpus_sizes: tuple[int, ...],
    runs: int,
    warmup: int,
    device_id: int,
    command_timeout_s: int,
    shard_count: int,
    benchmark_pattern: str,
    timeout_simulation_ms: int,
    advanced: bool,
) -> dict[str, object]:
    from gpu_native_bench_helpers import run_gpu_native_benchmarks_impl

    return run_gpu_native_benchmarks_impl(
        _current_facade(),
        tg_binary=tg_binary,
        rg_binary=rg_binary,
        bench_dir=bench_dir,
        corpus_sizes=corpus_sizes,
        runs=runs,
        warmup=warmup,
        device_id=device_id,
        command_timeout_s=command_timeout_s,
        shard_count=shard_count,
        benchmark_pattern=benchmark_pattern,
        timeout_simulation_ms=timeout_simulation_ms,
        advanced=advanced,
    )


def run_advanced_gpu_native_benchmarks(
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    rows: list[dict[str, object]],
    runs: int,
    warmup: int,
    device_id: int,
    command_timeout_s: int,
) -> tuple[dict[str, object], list[str], list[str]]:
    from gpu_native_bench_helpers import run_advanced_gpu_native_benchmarks_impl

    return run_advanced_gpu_native_benchmarks_impl(
        _current_facade(),
        tg_binary=tg_binary,
        rg_binary=rg_binary,
        bench_dir=bench_dir,
        rows=rows,
        runs=runs,
        warmup=warmup,
        device_id=device_id,
        command_timeout_s=command_timeout_s,
    )


def main() -> int:
    args = build_parser().parse_args()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tg_binary = resolve_tg_binary(args.binary)
    rg_binary = resolve_rg_binary()
    bench_dir = resolve_gpu_native_bench_data_dir()
    tg_binary_metadata = inspect_native_tg_binary(tg_binary)

    payload: dict[str, object] = {
        "artifact": "bench_gpu_native_scale",
        "suite": "run_gpu_native_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "tg_binary": str(tg_binary),
        "tg_binary_metadata": tg_binary_metadata,
        "rg_binary": str(rg_binary),
        "runs": args.runs,
        "warmup": args.warmup,
        "gpu_device_id": args.device_id,
        "command_timeout_s": args.command_timeout_s,
    }

    if not tg_binary.exists():
        payload.update({
            "errors": [f"tg binary not found: {tg_binary}"],
            "warnings": [],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "corpus_sizes": [],
            "throughput_target": {
                "met": False,
                "winning_rows": [],
                "best_attempt": None,
                "summary": "Benchmark did not run because the tg binary was missing.",
            },
            "scale_gate_summary": build_native_scale_gate_summary(
                [],
                correctness_checks=[],
            ),
            "advanced": {"enabled": args.advanced},
            "crossover": {
                "exists": False,
                "first_gpu_faster_than_rg": None,
                "summary": "Benchmark did not run because the tg binary was missing.",
                "recommended_optimizations": GPU_TIMEOUT_OPTIMIZATIONS,
            },
        })
        public_gate = build_public_managed_gpu_proof_gate(
            tg_binary_metadata=tg_binary_metadata,
            scale_gate_summary=payload["scale_gate_summary"]
            if isinstance(payload["scale_gate_summary"], dict)
            else {},
            advanced_payload=payload.get("advanced")
            if isinstance(payload.get("advanced"), dict)
            else None,
            requested=args.public_managed_proof,
        )
        scale_gate_summary = payload["scale_gate_summary"]
        proof_status = _gpu_proof_status_from_native_summary(
            scale_gate_summary if isinstance(scale_gate_summary, dict) else {}
        )
        payload.update(proof_status)
        payload["public_managed_gpu_proof_gate"] = public_gate
        payload["public_managed_promotion_ready"] = public_gate["public_managed_promotion_ready"]
        payload["public_gpu_proof"] = public_gate["public_gpu_proof"]
        payload["gpu_proof_summary"] = build_gpu_proof_summary(
            scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
            public_managed_gpu_proof_gate=public_gate,
        )
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 1

    result = run_gpu_native_benchmarks(
        tg_binary=tg_binary,
        rg_binary=str(rg_binary),
        bench_dir=bench_dir,
        corpus_sizes=args.corpus_sizes,
        runs=args.runs,
        warmup=args.warmup,
        device_id=args.device_id,
        command_timeout_s=args.command_timeout_s,
        shard_count=args.shards,
        benchmark_pattern=DEFAULT_BENCHMARK_PATTERN,
        timeout_simulation_ms=args.timeout_simulation_ms,
        advanced=args.advanced,
    )
    payload.update(result)
    scale_gate_summary = payload.get("scale_gate_summary")
    payload.update(
        _gpu_proof_status_from_native_summary(
            scale_gate_summary if isinstance(scale_gate_summary, dict) else {}
        )
    )
    public_gate = build_public_managed_gpu_proof_gate(
        tg_binary_metadata=tg_binary_metadata,
        scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
        advanced_payload=payload.get("advanced")
        if isinstance(payload.get("advanced"), dict)
        else None,
        requested=args.public_managed_proof,
    )
    payload["public_managed_gpu_proof_gate"] = public_gate
    payload["public_managed_promotion_ready"] = public_gate["public_managed_promotion_ready"]
    payload["public_gpu_proof"] = public_gate["public_gpu_proof"]
    payload["gpu_proof_summary"] = build_gpu_proof_summary(
        scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
        public_managed_gpu_proof_gate=public_gate,
    )
    if args.public_managed_proof and public_gate["status"] != "PASS":
        errors = payload.setdefault("errors", [])
        if isinstance(errors, list):
            errors.append(
                "public managed GPU proof gate failed: "
                + ", ".join(str(blocker) for blocker in public_gate["blockers"])
            )
    payload["passed"] = not payload.get("errors")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
