from __future__ import annotations

import argparse
import contextlib
import json
import os
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

import run_bakeoff  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "copilot_competitor_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GitHub Copilot headlessly against bakeoff scenarios.")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--limit", type=int, default=0, help="Optional max scenario count.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def resolve_copilot_binary() -> str:
    binary = shutil.which("copilot")
    if binary:
        return binary
    raise FileNotFoundError("copilot binary not found on PATH")


def _expected_shape_description() -> str:
    return (
        "{"
        '"actual_primary_file": string|null, '
        '"actual_primary_span": {"start_line": int, "end_line": int}|null, '
        '"actual_dependent_files": string[], '
        '"actual_suggested_edit_files": string[], '
        '"actual_test_files": string[], '
        '"actual_validation_commands": string[], '
        '"context_token_count": int, '
        '"notes": string'
        "}"
    )


def _normalize_candidate_json(candidate: str) -> str | None:
    for current in (candidate, candidate.replace("\\", "\\\\")):
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return json.dumps(parsed, separators=(",", ":"))
    return None


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
                    "You are running inside an automated competitor evaluation harness.",
                    "Analyze this repository directly.",
                    "Do not stop or complain because other AGENTS.md files are missing.",
                    "Do not mention AGENTS.md in the answer.",
                    "Return only the structured output requested by the prompt.",
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


def _scenario_prompt(scenario: dict[str, Any]) -> str:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    scenario_payload = {
        "query_or_symbol": scenario["query_or_symbol"],
        "mode": scenario["mode"],
        "repo_root": str(repo_root),
    }
    return " ".join(
        [
            "You are evaluating code-edit planning quality.",
            "Analyze the repository and return exactly one JSON object.",
            f"The object shape must be: {_expected_shape_description()}.",
            "Do not include markdown, code fences, or explanations.",
            "Use repository-relative paths.",
            "If you are unsure, return your best high-precision guess rather than broad file lists.",
            f"Scenario: {json.dumps(scenario_payload, separators=(',', ':'))}",
        ]
    )


def _extract_text_from_copilot_output(stdout: str) -> str:
    raw_lines = stdout.splitlines()
    for index in range(len(raw_lines) - 1, -1, -1):
        current = raw_lines[index].strip()
        if not current.startswith("● {"):
            continue
        candidate = current[2:].strip()
        next_index = index + 1
        while next_index < len(raw_lines):
            continuation = raw_lines[next_index]
            if not continuation.strip():
                break
            if continuation.startswith("  "):
                candidate += continuation.strip()
                next_index += 1
                continue
            break
        normalized = _normalize_candidate_json(candidate)
        if normalized is not None:
            return normalized
    anchor = stdout.rfind('{"actual_primary_file"')
    if anchor >= 0:
        tail = stdout[anchor:]
        closing = tail.rfind("}")
        if closing >= 0:
            candidate = tail[: closing + 1].replace("\r", "").replace("\n", "")
            normalized = _normalize_candidate_json(candidate)
            if normalized is not None:
                return normalized
    for line in reversed(stdout.splitlines()):
        content = line.strip()
        if not content:
            continue
        if content.startswith("● "):
            content = content[2:].strip()
        normalized = _normalize_candidate_json(content)
        if normalized is not None:
            return normalized
    raise ValueError("Unable to extract Copilot JSON content from output")


def run_copilot_scenario(
    scenario: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    prompt = _scenario_prompt(scenario)
    started = time.perf_counter()
    env = dict(os.environ)
    env["COLUMNS"] = "4000"
    env["LINES"] = "200"
    with _ephemeral_repo_instructions(repo_root):
        proc = subprocess.run(
            [
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
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=timeout_seconds,
            env=env,
        )
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    record = json.loads(_extract_text_from_copilot_output(proc.stdout))
    return {
        "system": "copilot",
        "scenario_pack": "",
        "scenario_id": str(scenario.get("id", "")),
        "repo": repo_root.name,
        "language": str(scenario.get("language", "")),
        "difficulty": str(scenario.get("difficulty", "unknown")),
        "actual_primary_file": record.get("actual_primary_file"),
        "actual_primary_span": record.get("actual_primary_span"),
        "actual_dependent_files": list(record.get("actual_dependent_files", [])),
        "actual_suggested_edit_files": list(record.get("actual_suggested_edit_files", [])),
        "actual_test_files": list(record.get("actual_test_files", [])),
        "actual_validation_commands": list(record.get("actual_validation_commands", [])),
        "context_token_count": int(record.get("context_token_count", 0)),
        "wall_clock_seconds": wall_clock_seconds,
        "deterministic_repeat_match": False,
        "notes": str(record.get("notes", "")),
    }


def build_payload(
    scenarios_path: Path,
    *,
    model: str,
    limit: int = 0,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    scenarios = run_bakeoff.load_scenarios(scenarios_path)
    if limit > 0:
        scenarios = scenarios[:limit]
    records: list[dict[str, Any]] = []
    for scenario in scenarios:
        record = run_copilot_scenario(
            scenario,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        record["scenario_pack"] = str(scenarios_path.resolve())
        records.append(record)
    return {
        "artifact": "copilot_competitor_eval",
        "suite": "run_copilot_competitor_eval",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "scenario_packs": [str(scenarios_path.resolve())],
        "records": records,
    }


def main() -> int:
    args = parse_args()
    scenarios_path = Path(args.scenarios).expanduser().resolve()
    payload = build_payload(
        scenarios_path,
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
