from __future__ import annotations

import argparse
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
    return ROOT_DIR / "artifacts" / "claude_competitor_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Claude Code headlessly against bakeoff scenarios.")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--limit", type=int, default=0, help="Optional max scenario count.")
    parser.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        choices=("bypassPermissions", "dontAsk", "acceptEdits", "auto", "default"),
    )
    return parser.parse_args()


def resolve_claude_binary() -> str:
    binary = shutil.which("claude")
    if binary:
        return binary
    raise FileNotFoundError("claude binary not found on PATH")


def _expected_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "actual_primary_file": {"type": ["string", "null"]},
            "actual_primary_span": {
                "type": ["object", "null"],
                "properties": {
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["start_line", "end_line"],
                "additionalProperties": False,
            },
            "actual_dependent_files": {"type": "array", "items": {"type": "string"}},
            "actual_suggested_edit_files": {"type": "array", "items": {"type": "string"}},
            "actual_test_files": {"type": "array", "items": {"type": "string"}},
            "actual_validation_commands": {"type": "array", "items": {"type": "string"}},
            "context_token_count": {"type": "integer"},
            "notes": {"type": "string"},
        },
        "required": [
            "actual_primary_file",
            "actual_primary_span",
            "actual_dependent_files",
            "actual_suggested_edit_files",
            "actual_test_files",
            "actual_validation_commands",
            "context_token_count",
            "notes",
        ],
        "additionalProperties": False,
    }


def _scenario_prompt(scenario: dict[str, Any]) -> str:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    expected = {
        "query_or_symbol": scenario["query_or_symbol"],
        "mode": scenario["mode"],
        "repo_root": str(repo_root),
    }
    return (
        "You are evaluating code-edit planning quality.\n"
        "Analyze the repository and return only JSON matching the provided schema.\n"
        "Do not explain your reasoning.\n"
        "Use repository-relative paths.\n"
        "If you are unsure, return your best high-precision guess rather than broad file lists.\n\n"
        f"Scenario:\n{json.dumps(expected, indent=2)}\n"
    )


def _extract_text_from_claude_output(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("result"), str):
        return str(payload["result"])
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
            return "\n".join(part for part in parts if part)
    raise ValueError("Unable to extract Claude text output")


def run_claude_scenario(
    scenario: dict[str, Any],
    *,
    model: str,
    permission_mode: str,
) -> dict[str, Any]:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    prompt = _scenario_prompt(scenario)
    schema = json.dumps(_expected_schema(), separators=(",", ":"))
    started = time.perf_counter()
    proc = subprocess.run(
        [
            resolve_claude_binary(),
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--permission-mode",
            permission_mode,
            "--add-dir",
            str(repo_root),
            "--json-schema",
            schema,
            prompt,
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    outer = json.loads(proc.stdout)
    record = json.loads(_extract_text_from_claude_output(outer))
    return {
        "system": "claude-code",
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
    permission_mode: str,
    limit: int = 0,
) -> dict[str, Any]:
    scenarios = run_bakeoff.load_scenarios(scenarios_path)
    if limit > 0:
        scenarios = scenarios[:limit]
    records: list[dict[str, Any]] = []
    for scenario in scenarios:
        record = run_claude_scenario(
            scenario,
            model=model,
            permission_mode=permission_mode,
        )
        record["scenario_pack"] = str(scenarios_path.resolve())
        records.append(record)
    return {
        "artifact": "claude_competitor_eval",
        "suite": "run_claude_competitor_eval",
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
        permission_mode=args.permission_mode,
        limit=args.limit,
    )
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
