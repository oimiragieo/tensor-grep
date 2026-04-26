from __future__ import annotations

import argparse
import contextlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
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
    return ROOT_DIR / "artifacts" / "codex_competitor_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex headlessly against bakeoff scenarios.")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="gpt-5-codex")
    parser.add_argument("--limit", type=int, default=0, help="Optional max scenario count.")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def resolve_codex_binary() -> str:
    binary = shutil.which("codex")
    if binary:
        return binary
    raise FileNotFoundError("codex binary not found on PATH")


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
        "If no AGENTS.md exists in the target repository, continue normally and do not mention it.\n"
        "If you are unsure, return your best high-precision guess rather than broad file lists.\n\n"
        f"Scenario:\n{json.dumps(expected, indent=2)}\n"
    )


def _fallback_prompt(scenario: dict[str, Any]) -> str:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    scenario_payload = {
        "query_or_symbol": scenario["query_or_symbol"],
        "mode": scenario["mode"],
        "repo_root": str(repo_root),
    }
    return " ".join(
        [
            "Task: produce a code-edit plan for this repository scenario.",
            "Return exactly one JSON object.",
            'Required keys: "actual_primary_file", "actual_primary_span", "actual_dependent_files", '
            '"actual_suggested_edit_files", "actual_test_files", "actual_validation_commands", '
            '"context_token_count", "notes".',
            "Use repository-relative paths.",
            "Do not mention AGENTS.md.",
            "Do not ask for more input.",
            f"Scenario: {json.dumps(scenario_payload, separators=(',', ':'))}",
        ]
    )


def _extract_text_from_codex_output(stdout: str) -> str:
    for line in stdout.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            return str(item["text"])
    raise ValueError("Unable to extract Codex agent_message from JSONL output")


def _run_codex_exec(
    repo_root: Path,
    *,
    model: str,
    timeout_seconds: int,
    prompt: str,
    schema_path: Path | None,
) -> str:
    command = [
        resolve_codex_binary(),
        "exec",
        "--json",
        "--model",
        model,
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        str(repo_root),
    ]
    if schema_path is not None:
        command.extend(["--output-schema", str(schema_path)])
    command.append(prompt)
    proc = subprocess.run(
        command,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=timeout_seconds,
    )
    return _extract_text_from_codex_output(proc.stdout)


def _should_retry_without_schema(record: dict[str, Any]) -> bool:
    if record.get("actual_primary_file"):
        return False
    notes = str(record.get("notes", "")).lower()
    return "agents.md" in notes or "awaiting" in notes or "task" in notes


def _normalize_primary_span(record: dict[str, Any]) -> dict[str, Any]:
    current = dict(record)
    span = current.get("actual_primary_span")
    if isinstance(span, dict):
        return current
    if not isinstance(span, str):
        return current
    text = span.strip()
    if ":" not in text or "-" not in text:
        return current
    try:
        file_part, line_part = text.rsplit(":", 1)
        start_text, end_text = line_part.split("-", 1)
        start_line = int(start_text)
        end_line = int(end_text)
    except ValueError:
        return current
    if not current.get("actual_primary_file"):
        current["actual_primary_file"] = file_part.replace("\\", "/")
    current["actual_primary_span"] = {"start_line": start_line, "end_line": end_line}
    return current


def run_codex_scenario(
    scenario: dict[str, Any],
    *,
    model: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    prompt = _scenario_prompt(scenario)
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", encoding="utf-8", delete=False
    ) as schema_file:
        schema_path = Path(schema_file.name)
        json.dump(_expected_schema(), schema_file)
    try:
        with _ephemeral_repo_instructions(repo_root):
            text = _run_codex_exec(
                repo_root,
                model=model,
                timeout_seconds=timeout_seconds,
                prompt=prompt,
                schema_path=schema_path,
            )
            record = json.loads(text)
            if _should_retry_without_schema(record):
                record = json.loads(
                    _run_codex_exec(
                        repo_root,
                        model=model,
                        timeout_seconds=timeout_seconds,
                        prompt=_fallback_prompt(scenario),
                        schema_path=None,
                    )
                )
            record = _normalize_primary_span(record)
    finally:
        schema_path.unlink(missing_ok=True)
    wall_clock_seconds = round(time.perf_counter() - started, 6)
    return {
        "system": "codex",
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
        record = run_codex_scenario(
            scenario,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        record["scenario_pack"] = str(scenarios_path.resolve())
        records.append(record)
    return {
        "artifact": "codex_competitor_eval",
        "suite": "run_codex_competitor_eval",
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
