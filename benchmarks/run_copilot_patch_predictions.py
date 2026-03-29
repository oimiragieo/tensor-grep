from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
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

from patch_runner_common import (  # noqa: E402
    derive_patch_from_repo_changes,
    is_probably_patch_text,
    isolated_repo_pair,
    normalize_model_patch_text,
)

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "copilot_patch_predictions.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Copilot headlessly against tensor-grep patch bundles.")
    parser.add_argument("--input", required=True, help="Path to tensor-grep patch driver JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def resolve_copilot_binary() -> str:
    binary = shutil.which("copilot")
    if binary:
        return binary
    raise FileNotFoundError("copilot binary not found on PATH")


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


def _extract_patch_from_copilot_output(stdout: str) -> str:
    lines = stdout.replace("\r", "").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```diff") or stripped.startswith("```patch") or stripped == "```":
            patch_lines: list[str] = []
            for current in lines[index + 1 :]:
                if current.strip() == "```":
                    break
                patch_lines.append(current)
            patch_text = "\n".join(patch_lines).strip()
            if patch_text:
                return patch_text
    anchor = stdout.find("diff --git ")
    if anchor >= 0:
        return stdout[anchor:].strip()
    raise ValueError("Unable to locate diff patch in Copilot output")


_VALID_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+(?: \d+)?$", re.IGNORECASE)


def _normalize_patch_text(patch_text: str) -> str:
    lines: list[str] = []
    for line in patch_text.replace("\r", "").splitlines():
        if line.startswith("index ") and not _VALID_INDEX_RE.match(line.strip()):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(Exception):
        if platform.system().lower().startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def _run_copilot_command(
    repo_root: Path,
    prompt: str,
    *,
    model: str,
    timeout_seconds: int,
) -> str:
    env = dict(os.environ)
    env["COLUMNS"] = "4000"
    env["LINES"] = "200"
    command = [
        resolve_copilot_binary(),
        "-p",
        prompt,
        "--silent",
        "--allow-all-tools",
        "--stream",
        "off",
        "--no-color",
        "--model",
        model,
    ]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(proc)
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=exc.output, stderr=exc.stderr) from None
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, command, output=stdout, stderr=stderr)
    return stdout


def run_copilot_patch_record(
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
    with isolated_repo_pair(repo_root) as (before_root, work_root):
        try:
            with _ephemeral_repo_instructions(work_root):
                stdout = _run_copilot_command(
                    work_root,
                    prompt,
                    model=model,
                    timeout_seconds=timeout_seconds,
                )
            patch_text = normalize_model_patch_text(
                _normalize_patch_text(_extract_patch_from_copilot_output(stdout))
            )
            if not is_probably_patch_text(patch_text):
                patch_text = ""
        except subprocess.TimeoutExpired:
            notes = f"timeout after {timeout_seconds}s"
        except subprocess.CalledProcessError as exc:
            notes = (exc.stderr or exc.output or str(exc)).strip()
        except ValueError as exc:
            notes = str(exc)
        if not patch_text.strip():
            patch_text = derive_patch_from_repo_changes(before_root, work_root)
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    return {
        "instance_id": str(record["instance_id"]),
        "system": "copilot",
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
        run_copilot_patch_record(
            dict(record),
            model=model,
            timeout_seconds=timeout_seconds,
        )
        for record in records
    ]
    return {
        "artifact": "copilot_patch_predictions",
        "suite": "run_copilot_patch_predictions",
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
