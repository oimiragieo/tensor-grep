from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
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

from run_harness_loop_benchmark import default_binary_path, run_json_command  # noqa: E402

POSITIONING = "agent-native end-to-end success harness; not a raw search speed claim"
DEFAULT_PATTERN = "def $F($$$ARGS): return $EXPR"
DEFAULT_REPLACEMENT = "def $F($$$ARGS):\n    result = $EXPR\n    return result"
DEFAULT_SCENARIOS: list[dict[str, object]] = [
    {
        "name": "python_invoice_success",
        "query": "change invoice tax calculation in src/payments.py",
        "expected_primary_file_suffix": "src/payments.py",
        "expected_ask_required": False,
    }
]


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_agent_success_harness.json"


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_agent_success_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AGENT_SUCCESS_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_agent_success"


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_for_files(corpus_dir: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(p for p in corpus_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(corpus_dir).as_posix()
        entries.append({"path": rel, "sha256": _sha256_file(path)})
    return entries


def ensure_agent_success_corpus(output_dir: Path, *, seed: int) -> dict[str, object]:
    corpus_dir = output_dir / "agent_success_corpus"
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    _write_file(
        corpus_dir / "src" / "payments.py",
        """def create_invoice_tax(amount, tax_rate=0.0825): return amount * tax_rate


def create_invoice_total(amount, tax_rate=0.0825): return amount + create_invoice_tax(amount, tax_rate)
""",
    )
    _write_file(
        corpus_dir / "src" / "app.ts",
        """export function createInvoiceTax(amount: number, taxRate = 0.0825) {
  return amount * taxRate;
}
""",
    )
    _write_file(
        corpus_dir / "tests" / "test_payments.py",
        """from src.payments import create_invoice_tax


def test_create_invoice_tax():
    assert create_invoice_tax(100.0) == 8.25
""",
    )
    _write_file(corpus_dir / "pytest.ini", "[pytest]\npythonpath = .\n")

    manifest_path = output_dir / "agent_success_manifest.json"
    manifest = {
        "artifact": "bench_agent_success_corpus_manifest",
        "seed": seed,
        "files": _manifest_for_files(corpus_dir),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "corpus_dir": corpus_dir,
        "manifest_path": manifest_path,
        "file_count": len(manifest["files"]),
        "seed": seed,
    }


def copy_corpus(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tg_agent_success_"))
    shutil.copytree(src, tmp / "corpus")
    return tmp / "corpus"


def build_agent_cmd(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    query: str,
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
) -> list[str]:
    return [
        str(tg_binary),
        "agent",
        str(corpus_dir),
        "--query",
        query,
        "--max-files",
        str(max_files),
        "--max-sources",
        str(max_sources),
        "--max-tokens",
        str(max_tokens),
        "--max-repo-files",
        str(max_repo_files),
        "--json",
    ]


def build_context_render_cmd(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    query: str,
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
) -> list[str]:
    return [
        str(tg_binary),
        "context-render",
        str(corpus_dir),
        "--query",
        query,
        "--max-files",
        str(max_files),
        "--max-sources",
        str(max_sources),
        "--max-tokens",
        str(max_tokens),
        "--max-repo-files",
        str(max_repo_files),
        "--render-profile",
        "full",
        "--json",
    ]


def build_edit_plan_cmd(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    query: str,
    max_files: int,
) -> list[str]:
    return [
        str(tg_binary),
        "edit-plan",
        str(corpus_dir),
        "--query",
        query,
        "--max-files",
        str(max_files),
        "--max-symbols",
        "5",
        "--json",
    ]


def build_checkpoint_create_cmd(*, tg_binary: Path, corpus_dir: Path) -> list[str]:
    return [str(tg_binary), "checkpoint", "create", str(corpus_dir), "--json"]


def build_checkpoint_undo_cmd(
    *, tg_binary: Path, corpus_dir: Path, checkpoint_id: str
) -> list[str]:
    return [str(tg_binary), "checkpoint", "undo", checkpoint_id, str(corpus_dir), "--json"]


def build_rewrite_apply_cmd(
    *,
    tg_binary: Path,
    target_file: Path,
    pattern: str,
    replacement: str,
) -> list[str]:
    return [
        str(tg_binary),
        "run",
        "--lang",
        "python",
        "--rewrite",
        replacement,
        "--apply",
        "--json",
        pattern,
        str(target_file),
    ]


def build_validation_commands(*, corpus_dir: Path, target_file: Path) -> list[list[str]]:
    try:
        target_arg = str(target_file.relative_to(corpus_dir))
    except ValueError:
        target_arg = str(target_file)
    return [
        [sys.executable, "-m", "py_compile", target_arg],
        [sys.executable, "-m", "pytest", "-q", "tests/test_payments.py"],
    ]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _primary_file_from_payload(payload: dict[str, object]) -> str:
    primary_target = _as_dict(payload.get("primary_target"))
    if isinstance(primary_target.get("file"), str):
        return str(primary_target["file"])
    edit_seed = _as_dict(payload.get("edit_plan_seed"))
    if isinstance(edit_seed.get("primary_file"), str):
        return str(edit_seed["primary_file"])
    navigation_pack = _as_dict(payload.get("navigation_pack"))
    navigation_target = _as_dict(navigation_pack.get("primary_target"))
    if isinstance(navigation_target.get("file"), str):
        return str(navigation_target["file"])
    return ""


def _ask_required(payload: dict[str, object]) -> bool:
    ask_user = _as_dict(payload.get("ask_user_before_editing"))
    if "required" in ask_user:
        return bool(ask_user.get("required"))
    ask_before = _as_dict(payload.get("ask_before_editing"))
    if "ask_required" in ask_before:
        return bool(ask_before.get("ask_required"))
    return bool(payload.get("ask_required"))


def _matches_suffix(path: str, suffix: object) -> bool:
    if not isinstance(suffix, str) or not suffix:
        return bool(path)
    return path.replace("\\", "/").endswith(suffix.replace("\\", "/"))


def _validation_command_count(payload: dict[str, object]) -> int:
    commands = _as_list(payload.get("validation_commands"))
    if commands:
        return len(commands)
    edit_seed = _as_dict(payload.get("edit_plan_seed"))
    return len(_as_list(edit_seed.get("validation_plan")))


def _extract_total_edits(payload: dict[str, object]) -> int:
    plan = _as_dict(payload.get("plan"))
    value = plan.get("total_edits", payload.get("total_edits"))
    return int(value) if isinstance(value, int) else 0


def run_validation_commands(
    *, corpus_dir: Path, target_file: Path
) -> tuple[float, list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    total_s = 0.0
    for command in build_validation_commands(corpus_dir=corpus_dir, target_file=target_file):
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=corpus_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        elapsed_s = time.perf_counter() - started
        total_s += elapsed_s
        results.append({
            "command": command,
            "cwd": str(corpus_dir),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "elapsed_s": round(elapsed_s, 6),
            "passed": completed.returncode == 0,
        })
    return total_s, results


def run_agent_success_scenario(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    scenario: dict[str, object],
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
    pattern: str,
    replacement: str,
) -> dict[str, object]:
    query = str(scenario["query"])
    expected_suffix = scenario.get("expected_primary_file_suffix")
    phase_timings: dict[str, float] = {}

    intent_s, intent_payload = run_json_command(
        build_agent_cmd(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            query=query,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
        )
    )
    phase_timings["intent_s"] = intent_s
    intent_primary = _primary_file_from_payload(intent_payload)
    expected_ask = scenario.get("expected_ask_required")
    intent_passed = _matches_suffix(intent_primary, expected_suffix)
    if isinstance(expected_ask, bool):
        intent_passed = intent_passed and _ask_required(intent_payload) is expected_ask

    context_s, context_payload = run_json_command(
        build_context_render_cmd(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            query=query,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
        )
    )
    phase_timings["context_s"] = context_s
    context_primary = _primary_file_from_payload(context_payload)
    context_passed = _matches_suffix(context_primary, expected_suffix)

    edit_seed_s, edit_seed_payload = run_json_command(
        build_edit_plan_cmd(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            query=query,
            max_files=max_files,
        )
    )
    phase_timings["edit_seed_s"] = edit_seed_s
    edit_seed_primary = _primary_file_from_payload(edit_seed_payload)
    edit_seed_passed = _matches_suffix(edit_seed_primary, expected_suffix)

    target_file = Path(intent_primary or edit_seed_primary or context_primary)
    if not target_file.is_absolute():
        target_file = corpus_dir / target_file
    if not target_file.exists():
        raise RuntimeError(f"selected target file does not exist: {target_file}")

    original_digest = _sha256_file(target_file)
    checkpoint_s, checkpoint_payload = run_json_command(
        build_checkpoint_create_cmd(tg_binary=tg_binary, corpus_dir=corpus_dir)
    )
    phase_timings["checkpoint_s"] = checkpoint_s
    checkpoint_id = str(checkpoint_payload.get("checkpoint_id") or "")
    if not checkpoint_id:
        raise RuntimeError("checkpoint create did not return checkpoint_id")

    apply_s, apply_payload = run_json_command(
        build_rewrite_apply_cmd(
            tg_binary=tg_binary,
            target_file=target_file,
            pattern=pattern,
            replacement=replacement,
        )
    )
    phase_timings["apply_s"] = apply_s
    applied_digest = _sha256_file(target_file)
    changed_after_apply = applied_digest != original_digest
    applied_edits = _extract_total_edits(apply_payload)

    verify_s, validation_results = run_validation_commands(
        corpus_dir=corpus_dir,
        target_file=target_file,
    )
    phase_timings["verify_s"] = verify_s
    verify_passed = (
        changed_after_apply
        and applied_edits > 0
        and all(bool(result.get("passed")) for result in validation_results)
    )

    rollback_s, rollback_payload = run_json_command(
        build_checkpoint_undo_cmd(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            checkpoint_id=checkpoint_id,
        )
    )
    phase_timings["rollback_s"] = rollback_s
    restored_digest = _sha256_file(target_file)
    restored = restored_digest == original_digest

    row = {
        "scenario": str(scenario["name"]),
        "query": query,
        "intent": {
            "primary_file": intent_primary,
            "ask_required": _ask_required(intent_payload),
            "alternative_count": len(_as_list(intent_payload.get("alternative_targets"))),
            "validation_command_count": _validation_command_count(intent_payload),
            "passed": intent_passed,
        },
        "context": {
            "primary_file": context_primary,
            "rendered_context_chars": len(str(context_payload.get("rendered_context") or "")),
            "source_count": len(_as_list(context_payload.get("sources"))),
            "passed": context_passed,
        },
        "edit_seed": {
            "primary_file": edit_seed_primary,
            "validation_command_count": _validation_command_count(edit_seed_payload),
            "passed": edit_seed_passed,
        },
        "checkpoint": {
            "checkpoint_id": checkpoint_id,
            "undo_command": checkpoint_payload.get("undo_command"),
            "undo_argv": checkpoint_payload.get("undo_argv"),
        },
        "apply": {
            "target_file": str(target_file),
            "total_edits": applied_edits,
            "changed": changed_after_apply,
            "passed": changed_after_apply and applied_edits > 0,
        },
        "verify": {
            "changed_after_apply": changed_after_apply,
            "validation_results": validation_results,
            "passed": verify_passed,
        },
        "rollback": {
            "checkpoint_id": checkpoint_id,
            "files_restored": rollback_payload.get("files_restored", []),
            "restored": restored,
            "passed": restored,
        },
        "phase_timings_s": phase_timings,
    }
    row["passed"] = all(
        bool(row[section]["passed"])
        for section in ("intent", "context", "edit_seed", "apply", "verify", "rollback")
    )
    return row


def run_agent_success_harness(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    iterations: int,
    scenarios: list[dict[str, object]],
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
    pattern: str,
    replacement: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for iteration in range(1, iterations + 1):
        for scenario in scenarios:
            work_dir = copy_corpus(corpus_dir)
            try:
                row = run_agent_success_scenario(
                    tg_binary=tg_binary,
                    corpus_dir=work_dir,
                    scenario=scenario,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_repo_files=max_repo_files,
                    pattern=pattern,
                    replacement=replacement,
                )
                row["iteration"] = iteration
                rows.append(row)
            finally:
                shutil.rmtree(work_dir.parent, ignore_errors=True)
    return rows


def _phase_medians(rows: list[dict[str, object]]) -> dict[str, float]:
    keys = sorted({key for row in rows for key in _as_dict(row.get("phase_timings_s")).keys()})
    medians: dict[str, float] = {}
    for key in keys:
        values = [
            float(_as_dict(row.get("phase_timings_s"))[key])
            for row in rows
            if key in _as_dict(row.get("phase_timings_s"))
        ]
        medians[key] = round(float(statistics.median(values)), 6)
    return medians


def build_payload(
    *,
    output_path: Path,
    tg_binary: Path,
    corpus_manifest: dict[str, object],
    scenarios: list[dict[str, object]],
    args: argparse.Namespace,
) -> dict[str, object]:
    passed_count = sum(1 for row in scenarios if bool(row.get("passed")))
    return {
        "artifact": "bench_agent_success_harness",
        "suite": "run_agent_success_harness",
        "generated_at_epoch_s": time.time(),
        "positioning": POSITIONING,
        "workflow_surfaces": [
            "intent",
            "context",
            "edit_seed",
            "apply",
            "verify",
            "rollback",
        ],
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
            "tg_binary": str(tg_binary),
        },
        "output": str(output_path),
        "seed": args.seed,
        "iterations": args.iterations,
        "options": {
            "max_files": args.max_files,
            "max_sources": args.max_sources,
            "max_tokens": args.max_tokens,
            "max_repo_files": args.max_repo_files,
            "pattern": args.pattern,
            "replacement": args.replacement,
        },
        "corpus": corpus_manifest,
        "summary": {
            "scenario_count": len(scenarios),
            "passed_count": passed_count,
            "all_passed": passed_count == len(scenarios),
            "phase_medians_s": _phase_medians(scenarios),
        },
        "scenarios": scenarios,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an end-to-end agent success harness from intent to rollback."
    )
    parser.add_argument("--binary", default=str(default_binary_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-files", type=int, default=3)
    parser.add_argument("--max-sources", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--max-repo-files", type=int, default=512)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--replacement", default=DEFAULT_REPLACEMENT)
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    tg_binary = resolve_tg_binary(args.binary)
    output_path = Path(args.output).expanduser().resolve()
    bench_dir = resolve_agent_success_bench_dir()
    bench_dir.mkdir(parents=True, exist_ok=True)
    corpus_manifest = ensure_agent_success_corpus(bench_dir, seed=args.seed)

    rows = run_agent_success_harness(
        tg_binary=tg_binary,
        corpus_dir=Path(corpus_manifest["corpus_dir"]),
        iterations=args.iterations,
        scenarios=DEFAULT_SCENARIOS,
        max_files=args.max_files,
        max_sources=args.max_sources,
        max_tokens=args.max_tokens,
        max_repo_files=args.max_repo_files,
        pattern=args.pattern,
        replacement=args.replacement,
    )
    payload = build_payload(
        output_path=output_path,
        tg_binary=tg_binary,
        corpus_manifest={
            key: str(value) if isinstance(value, Path) else value
            for key, value in corpus_manifest.items()
        },
        scenarios=rows,
        args=args,
    )
    write_json(output_path, payload)
    return 0 if bool(payload["summary"]["all_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
