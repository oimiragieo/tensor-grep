from __future__ import annotations

import argparse
import contextlib
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "gemini_patch_predictions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Gemini headlessly against tensor-grep patch bundles.")
    parser.add_argument("--input", required=True, help="Path to tensor-grep patch driver JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def resolve_gemini_binary() -> str:
    binary = shutil.which("gemini")
    if binary:
        return binary
    raise FileNotFoundError("gemini binary not found on PATH")


def load_driver_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("driver payload missing records list")
    return payload


def _ephemeral_repo_instructions(repo_root: Path) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def _manager() -> Any:
        instructions_path = repo_root / "AGENTS.md"
        if instructions_path.exists():
            yield
            return
        instructions_path.write_text(
            "\n".join(
                [
                    "# Evaluation Instructions",
                    "",
                    "You are running inside an automated patch evaluation harness.",
                    "Analyze this repository directly.",
                    "Return only a unified diff patch that can be applied with git apply.",
                    "Do not include markdown fences or explanations.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            yield
        finally:
            instructions_path.unlink(missing_ok=True)

    return _manager()


def _extract_response_text(stdout: str) -> str:
    anchor = stdout.find("{")
    if anchor < 0:
        raise ValueError("Unable to locate Gemini JSON payload in output")
    payload = json.loads(stdout[anchor:])
    response = payload.get("response")
    if not isinstance(response, str):
        raise ValueError("Unable to extract Gemini response from JSON output")
    stripped = response.strip()
    if stripped.startswith("```diff"):
        stripped = stripped[len("```diff") :].strip()
    elif stripped.startswith("```patch"):
        stripped = stripped[len("```patch") :].strip()
    elif stripped.startswith("```"):
        stripped = stripped[3:].strip()
    if stripped.endswith("```"):
        stripped = stripped[:-3].strip()
    return stripped


def run_gemini_patch_record(
    record: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    repo_root = Path(str(record["repo_fixture"])).resolve()
    prompt = str(record["prompt"])
    started = time.perf_counter()
    notes = ""
    patch_text = ""
    try:
        with _ephemeral_repo_instructions(repo_root):
            proc = subprocess.run(
                [
                    resolve_gemini_binary(),
                    "-p",
                    prompt,
                    "--output-format",
                    "json",
                    "--model",
                    model,
                    "--yolo",
                    "--include-directories",
                    str(repo_root),
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
                timeout=timeout_seconds,
            )
        patch_text = _extract_response_text(proc.stdout)
    except subprocess.TimeoutExpired:
        notes = f"timeout after {timeout_seconds}s"
    except subprocess.CalledProcessError as exc:
        notes = (exc.stderr or exc.stdout or str(exc)).strip()
    except ValueError as exc:
        notes = str(exc)
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    return {
        "instance_id": str(record["instance_id"]),
        "system": "gemini-cli",
        "model_patch": patch_text,
        "actual_test_files": list(record.get("actual_test_files", [])),
        "actual_validation_commands": list(record.get("actual_validation_commands", [])),
        "wall_clock_seconds": wall_clock_seconds,
        "notes": notes,
    }


def build_payload(
    driver_payload: dict[str, Any],
    *,
    model: str,
    limit: int = 0,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    records = list(driver_payload.get("records", []))
    if limit > 0:
        records = records[:limit]
    prediction_records = [
        run_gemini_patch_record(
            dict(record),
            model=model,
            timeout_seconds=timeout_seconds,
        )
        for record in records
    ]
    return {
        "artifact": "gemini_patch_predictions",
        "suite": "run_gemini_patch_predictions",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "records": prediction_records,
    }


def main() -> int:
    args = parse_args()
    driver_payload = load_driver_payload(args.input)
    payload = build_payload(
        driver_payload,
        model=args.model,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
    )
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
