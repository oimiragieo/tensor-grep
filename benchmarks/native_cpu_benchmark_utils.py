from __future__ import annotations

import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
KB = 1024
MB = 1024 * KB

DEFAULT_LARGE_FILE_BYTES = 200 * MB
DEFAULT_MANY_FILE_COUNT = 1200
DEFAULT_MANY_FILE_LINES_PER_FILE = 256
NATIVE_CPU_BENCHMARK_PATTERN = "ERROR native cpu benchmark sentinel"


def resolve_native_cpu_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_NATIVE_CPU_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "native_cpu_bench_data"


def ensure_large_file_fixture(
    data_dir: Path,
    *,
    target_bytes: int = DEFAULT_LARGE_FILE_BYTES,
    pattern: str = NATIVE_CPU_BENCHMARK_PATTERN,
) -> dict[str, object]:
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / f"large_file_{target_bytes // MB}mb.log"
    metadata_path = data_dir / f"large_file_{target_bytes // MB}mb.json"

    if file_path.exists() and file_path.stat().st_size >= target_bytes and metadata_path.exists():
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload["path"] = file_path
        payload["cache_hit"] = True
        return payload

    info_template = (
        "2026-03-16T12:00:00Z INFO background task completed shard={shard:02d} "
        "trace_id={trace_id:09d} duration_ms={duration:03d} user_id={user_id:05d} "
        "payload=abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789\n"
    )
    error_template = (
        "2026-03-16T12:00:00Z {pattern} shard={shard:02d} trace_id={trace_id:09d} "
        "service=search worker={worker:02d} duration_ms={duration:03d} "
        "payload=abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789\n"
    )

    total_bytes = 0
    total_lines = 0
    match_count = 0
    batch: list[str] = []
    with file_path.open("w", encoding="utf-8") as handle:
        while total_bytes < target_bytes:
            if total_lines % 257 == 0:
                line = error_template.format(
                    pattern=pattern,
                    shard=total_lines % 16,
                    trace_id=total_lines,
                    worker=total_lines % 32,
                    duration=(total_lines % 97) + 1,
                )
                match_count += 1
            else:
                line = info_template.format(
                    shard=total_lines % 16,
                    trace_id=total_lines,
                    duration=(total_lines % 53) + 1,
                    user_id=total_lines % 10000,
                )
            batch.append(line)
            total_bytes += len(line.encode("utf-8"))
            total_lines += 1
            if len(batch) >= 4096:
                handle.write("".join(batch))
                batch.clear()
        if batch:
            handle.write("".join(batch))

    payload = {
        "path": file_path,
        "actual_bytes": file_path.stat().st_size,
        "target_bytes": target_bytes,
        "line_count": total_lines,
        "match_count": match_count,
        "cache_hit": False,
    }
    metadata_path.write_text(
        json.dumps(
            {
                "actual_bytes": payload["actual_bytes"],
                "target_bytes": target_bytes,
                "line_count": total_lines,
                "match_count": match_count,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def ensure_many_file_fixture(
    data_dir: Path,
    *,
    file_count: int = DEFAULT_MANY_FILE_COUNT,
    lines_per_file: int = DEFAULT_MANY_FILE_LINES_PER_FILE,
    pattern: str = NATIVE_CPU_BENCHMARK_PATTERN,
) -> dict[str, object]:
    data_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = data_dir / f"many_files_{file_count}"
    metadata_path = data_dir / f"many_files_{file_count}.json"

    if fixture_dir.exists() and metadata_path.exists():
        log_file_count = sum(1 for _ in fixture_dir.glob("*.log"))
        if log_file_count >= file_count:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            payload["path"] = fixture_dir
            payload["cache_hit"] = True
            return payload

    fixture_dir.mkdir(parents=True, exist_ok=True)
    for existing in fixture_dir.glob("*.log"):
        existing.unlink()

    info_template = (
        "2026-03-16T12:00:00Z INFO request completed file={file_id:04d} line={line_id:04d} "
        "trace_id={trace_id:09d} payload=abcdefghijklmnopqrstuvwxyz0123456789\n"
    )
    error_template = (
        "2026-03-16T12:00:00Z {pattern} file={file_id:04d} line={line_id:04d} "
        "trace_id={trace_id:09d} payload=abcdefghijklmnopqrstuvwxyz0123456789\n"
    )

    total_bytes = 0
    match_count = 0
    for file_id in range(file_count):
        file_path = fixture_dir / f"shard_{file_id:04d}.log"
        lines: list[str] = []
        for line_id in range(lines_per_file):
            trace_id = (file_id * lines_per_file) + line_id
            if line_id % 41 == 0 or (file_id % 17 == 0 and line_id % 19 == 0):
                line = error_template.format(
                    pattern=pattern,
                    file_id=file_id,
                    line_id=line_id,
                    trace_id=trace_id,
                )
                match_count += 1
            else:
                line = info_template.format(file_id=file_id, line_id=line_id, trace_id=trace_id)
            lines.append(line)
        content = "".join(lines)
        total_bytes += len(content.encode("utf-8"))
        file_path.write_text(content, encoding="utf-8")

    payload = {
        "path": fixture_dir,
        "file_count": file_count,
        "lines_per_file": lines_per_file,
        "actual_bytes": total_bytes,
        "match_count": match_count,
        "cache_hit": False,
    }
    metadata_path.write_text(
        json.dumps(
            {
                "file_count": file_count,
                "lines_per_file": lines_per_file,
                "actual_bytes": total_bytes,
                "match_count": match_count,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload
