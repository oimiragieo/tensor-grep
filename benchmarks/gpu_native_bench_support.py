"""Command builders, argv-safe formatting, JSON/rg parsing helpers, and error
fixtures extracted from run_gpu_native_benchmarks.py (file-size wave 3).

These are pure or filesystem-only helpers: none of them call the native
benchmark's I/O boundary (`_run_command`) or any other name the test suite
monkeypatches on the top-level facade module, so moving their definitions
here is behavior-neutral. run_gpu_native_benchmarks.py imports and re-exports
every name below so existing module-attribute access
(module.build_tg_gpu_search_command, module.DEFAULT_CORPUS_SIZES, ...) keeps
working unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from run_gpu_benchmarks import GB, GPU_MANY_PATTERN_WORKLOAD_CLASS, MB  # noqa: E402

DEFAULT_CORPUS_SIZES = (10 * MB, 100 * MB, 500 * MB, 1 * GB, 5 * GB)


DEFAULT_RUNS = 3


DEFAULT_WARMUP = 0


DEFAULT_COMMAND_TIMEOUT_S = 180


DEFAULT_GPU_DEVICE_ID = 0


NATIVE_SCALE_WORKLOAD_CLASS = "single_pattern_cold_grep"


NATIVE_MANY_PATTERN_WORKLOAD_CLASS = GPU_MANY_PATTERN_WORKLOAD_CLASS


DEFAULT_TIMEOUT_SIMULATION_MS = 300


DEFAULT_TIMEOUT_DESCRIPTION = "simulation-backed via TG_TEST_CUDA_BEHAVIOR"


DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES = 1 * GB


DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES = 256 * MB


DEFAULT_ADVANCED_GRAPH_PATTERN = "ERROR cuda graph sentinel"


DEFAULT_ADVANCED_GRAPH_FILE_COUNT = 160


DEFAULT_ADVANCED_GRAPH_BATCH_BYTES = 4 * 1024


DEFAULT_ADVANCED_LONG_LINE_TARGET_BYTES = 128 * MB


DEFAULT_ADVANCED_LONG_LINE_PATTERN = "ERROR long line sentinel"


DEFAULT_ADVANCED_THROUGHPUT_PATTERN_COUNT = 4


DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES = 64 * 1024


DEFAULT_ADVANCED_THROUGHPUT_MAX_BATCH_BYTES = 16 * MB


DEFAULT_ADVANCED_OOM_BYTES = 13 * GB


DEFAULT_MULTI_GPU_DEVICE_ID = 1


MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG = 10.0


MIN_MULTI_GPU_IMPROVEMENT_PCT = 15.0


GPU_TIMEOUT_OPTIMIZATIONS = [
    "cache NVRTC-compiled kernels across CLI invocations",
    "overlap host-to-device transfer with kernel execution via CUDA streams",
    "use pinned host buffers for large corpus transfers",
]


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_run_gpu_native_benchmarks.json"


def resolve_gpu_native_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_GPU_NATIVE_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "gpu_native_bench_data"


def _format_size_label(size_bytes: int) -> str:
    if size_bytes % GB == 0:
        return f"{size_bytes // GB}GB"
    if size_bytes % MB == 0:
        return f"{size_bytes // MB}MB"
    return f"{size_bytes}B"


def _command_display(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(command)


def _build_command_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    if extra:
        env.update(extra)
    return env


def build_rg_search_command(rg_binary: str, pattern: str, corpus_dir: Path) -> list[str]:
    return [rg_binary, "--no-ignore", "-F", pattern, str(corpus_dir)]


def build_rg_json_command(rg_binary: str, pattern: str, corpus_dir: Path) -> list[str]:
    return [rg_binary, "--json", "--no-ignore", "-F", pattern, str(corpus_dir)]


def build_rg_multi_pattern_search_command(
    rg_binary: str,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[str]:
    command = [rg_binary, "--no-ignore", "-F"]
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_rg_multi_pattern_json_command(
    rg_binary: str,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[str]:
    command = [rg_binary, "--json", "--no-ignore", "-F"]
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_tg_cpu_search_command(tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--cpu",
        "--no-ignore",
        "-F",
        pattern,
        str(corpus_dir),
    ]


def build_tg_gpu_search_command(
    tg_binary: Path,
    pattern: str,
    corpus_dir: Path,
    device_id: int,
) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--gpu-device-ids",
        str(device_id),
        "--no-ignore",
        "-F",
        pattern,
        str(corpus_dir),
    ]


def build_tg_json_command(
    tg_binary: Path,
    pattern: str,
    corpus_dir: Path,
    *,
    force_cpu: bool = False,
    device_id: int | None = None,
) -> list[str]:
    command = [str(tg_binary), "search"]
    if force_cpu:
        command.append("--cpu")
    if device_id is not None:
        command.extend(["--gpu-device-ids", str(device_id)])
    command.extend(["--json", "--no-ignore", "-F", pattern, str(corpus_dir)])
    return command


def build_tg_multi_pattern_json_command(
    tg_binary: Path,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
    *,
    force_cpu: bool = False,
    device_id: int | None = None,
) -> list[str]:
    command = [str(tg_binary), "search"]
    if force_cpu:
        command.append("--cpu")
    if device_id is not None:
        command.extend(["--gpu-device-ids", str(device_id)])
    command.extend(["--json", "--no-ignore", "-F"])
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_tg_gpu_native_stats_command(
    tg_binary: Path,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
    device_ids: list[int] | tuple[int, ...],
    *,
    max_batch_bytes: int | None = None,
    summary_only: bool = True,
) -> list[str]:
    command = [str(tg_binary), "__gpu-native-stats"]
    for pattern in patterns:
        command.extend(["--pattern", pattern])
    command.extend(["--path", str(corpus_dir)])
    command.extend(["--gpu-device-ids", ",".join(str(device_id) for device_id in device_ids)])
    command.append("--no-ignore")
    if max_batch_bytes is not None:
        command.extend(["--max-batch-bytes", str(max_batch_bytes)])
    if summary_only:
        command.append("--summary-only")
    return command


def build_tg_gpu_transfer_benchmark_command(
    tg_binary: Path,
    *,
    device_id: int,
    total_bytes: int,
    batch_bytes: int,
    memory_kind: str,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-transfer-bench",
        "--device-id",
        str(device_id),
        "--total-bytes",
        str(total_bytes),
        "--batch-bytes",
        str(batch_bytes),
        "--memory-kind",
        memory_kind,
    ]


def build_tg_gpu_cuda_graph_benchmark_command(
    tg_binary: Path,
    *,
    pattern: str,
    corpus_dir: Path,
    device_id: int,
    max_batch_bytes: int,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-cuda-graphs",
        "--pattern",
        pattern,
        "--path",
        str(corpus_dir),
        "--device-id",
        str(device_id),
        "--no-ignore",
        "--max-batch-bytes",
        str(max_batch_bytes),
    ]


def build_tg_gpu_oom_probe_command(
    tg_binary: Path,
    *,
    device_id: int,
    bytes_to_allocate: int,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-oom-probe",
        "--device-id",
        str(device_id),
        "--bytes",
        str(bytes_to_allocate),
    ]


def _parse_json_payload(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("search output did not produce a JSON object")
    return payload


def _normalized_match_path(value: object) -> str:
    return str(value or "").replace("\\", "/")


def _normalized_match_text(value: object) -> str:
    return str(value or "").rstrip("\r\n")


def _extract_tg_match_signatures(payload: dict[str, object]) -> list[tuple[str, int, str]]:
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    signatures: list[tuple[str, int, str]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        # Native tg search JSON (SearchMatchJson in rust_core/src/main.rs) emits
        # the line number under the key `line`; the rg-passthrough serializer uses
        # `line_number`. Read `line` first, fall back to `line_number` so both the
        # native `--gpu-device-ids` path and the CPU/rg path parse correctly (#131 F2).
        line_number = match.get("line", match.get("line_number"))
        if not isinstance(line_number, int):
            line_number = 0
        signatures.append((
            _normalized_match_path(match.get("file")),
            line_number,
            _normalized_match_text(match.get("text")),
        ))
    return sorted(signatures)


def _extract_rg_json_match_signatures(stdout: str) -> list[tuple[str, int, str]]:
    signatures: list[tuple[str, int, str]] = []
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        if not isinstance(event, dict) or event.get("type") != "match":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        path = data.get("path")
        path_text = path.get("text") if isinstance(path, dict) else ""
        line_number = data.get("line_number")
        if not isinstance(line_number, int):
            line_number = 0
        lines = data.get("lines")
        line_text = lines.get("text") if isinstance(lines, dict) else ""
        signatures.append((
            _normalized_match_path(path_text),
            line_number,
            _normalized_match_text(line_text),
        ))
    return sorted(signatures)


def _signature_file_count(signatures: list[tuple[str, int, str]]) -> int:
    return len({signature[0] for signature in signatures if signature[0]})


def _signature_files(signatures: list[tuple[str, int, str]]) -> set[str]:
    return {signature[0] for signature in signatures if signature[0]}


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timeout_stderr(timeout_s: int) -> str:
    return f"command timed out after {timeout_s}s"


def _native_gpu_route_failure(payload: dict[str, object]) -> dict[str, object] | None:
    routing_backend = str(payload.get("routing_backend") or "unknown")
    routing_reason = payload.get("routing_reason")
    sidecar_used = bool(payload.get("sidecar_used", False))
    if routing_backend == "NativeGpuBackend" and not sidecar_used:
        return None
    return {
        "status": "UNSUPPORTED",
        "routing_backend": routing_backend,
        "routing_reason": routing_reason,
        "sidecar_used": sidecar_used,
        "promotion_evidence": False,
        "not_gpu_proof_reason": (
            "Requested GPU execution did not produce NativeGpuBackend with "
            f"sidecar_used=false (routing_backend={routing_backend}, "
            f"sidecar_used={sidecar_used}); this is CPU/sidecar compatibility "
            "output, not GPU acceleration proof."
        ),
        "error": (
            "GPU route did not use NativeGpuBackend "
            f"(routing_backend={routing_backend}, sidecar_used={sidecar_used}); "
            "sidecar-routed GPU rows are not native CUDA scale proof."
        ),
    }


def _lookup_nested_float(payload: dict[str, object], *path: str) -> float | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (float, int)):
        return float(current)
    return None


def _infer_total_files(payload: dict[str, object]) -> int:
    total_files = payload.get("total_files")
    if isinstance(total_files, int) and total_files > 0:
        return total_files
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return 0
    files = {
        match.get("file")
        for match in matches
        if isinstance(match, dict) and isinstance(match.get("file"), str)
    }
    return len(files)


def create_error_fixture(error_dir: Path) -> Path:
    error_dir.mkdir(parents=True, exist_ok=True)
    (error_dir / "good.log").write_text(
        "INFO boot\nERROR gpu benchmark sentinel\n",
        encoding="utf-8",
    )
    (error_dir / "empty.log").write_text("", encoding="utf-8")
    (error_dir / "binary.bin").write_bytes(b"\x00gpu benchmark sentinel\x00")
    (error_dir / "invalid_utf8.log").write_bytes(b"\xff\xfeERROR gpu benchmark sentinel\n")
    return error_dir


def create_runtime_probe_fixture(probe_dir: Path) -> Path:
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "probe.log").write_text(
        "INFO boot\nERROR gpu benchmark sentinel\n",
        encoding="utf-8",
    )
    return probe_dir


def build_unsupported_native_gpu_error_tests(
    runtime_probe: dict[str, object],
    *,
    timeout_simulation_ms: int,
) -> dict[str, object]:
    diagnostic = str(runtime_probe.get("error") or "native GPU runtime route unsupported")
    base_payload = {
        "status": "UNSUPPORTED",
        "exit_code": None,
        "stderr": diagnostic,
        "routing_backend": runtime_probe.get("routing_backend"),
        "routing_reason": runtime_probe.get("routing_reason"),
        "sidecar_used": runtime_probe.get("sidecar_used"),
    }
    return {
        "invalid_device": {
            **base_payload,
            "simulated": False,
        },
        "nvrtc_failure": {
            **base_payload,
            "simulated": True,
        },
        "timeout": {
            **base_payload,
            "simulated": True,
            "timeout_ms": timeout_simulation_ms,
        },
        "malformed_inputs": {
            **base_payload,
            "simulated": False,
        },
    }
