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

import run_bakeoff  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "gemini_competitor_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Gemini CLI headlessly against bakeoff scenarios."
    )
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--limit", type=int, default=0, help="Optional max scenario count.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def resolve_gemini_binary() -> str:
    binary = shutil.which("gemini")
    if binary:
        return binary
    raise FileNotFoundError("gemini binary not found on PATH")


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
            'Required keys: "actual_primary_file", "actual_primary_span", "actual_dependent_files", '
            '"actual_suggested_edit_files", "actual_test_files", "actual_validation_commands", '
            '"context_token_count", "notes".',
            'The "actual_primary_span" value must be an object with integer "start_line" and "end_line" fields.',
            "Use repository-relative paths.",
            "Do not include markdown, code fences, or explanations.",
            "If you are unsure, return your best high-precision guess rather than broad file lists.",
            f"Scenario: {json.dumps(scenario_payload, separators=(',', ':'))}",
        ]
    )


def _extract_text_from_gemini_output(stdout: str) -> str:
    anchor = stdout.find("{")
    if anchor < 0:
        raise ValueError("Unable to locate Gemini JSON payload in output")
    payload = json.loads(stdout[anchor:])
    response = payload.get("response")
    if isinstance(response, str):
        stripped = response.strip()
        if stripped.startswith("```json"):
            stripped = stripped[len("```json") :].strip()
        if stripped.startswith("```"):
            stripped = stripped[3:].strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        return stripped
    raise ValueError("Unable to extract Gemini response from JSON output")


def _coerce_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def run_gemini_scenario(
    scenario: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    prompt = _scenario_prompt(scenario)
    started = time.perf_counter()
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
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    record = json.loads(_extract_text_from_gemini_output(proc.stdout))
    return {
        "system": "gemini-cli",
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
        "context_token_count": _coerce_int(record.get("context_token_count", 0)),
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
        record = run_gemini_scenario(
            scenario,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        record["scenario_pack"] = str(scenarios_path.resolve())
        records.append(record)
    return {
        "artifact": "gemini_competitor_eval",
        "suite": "run_gemini_competitor_eval",
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
