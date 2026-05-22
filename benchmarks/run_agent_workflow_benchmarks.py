from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
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

from run_harness_loop_benchmark import (  # noqa: E402
    DEFAULT_PATTERN,
    DEFAULT_REPLACEMENT,
    build_phase_summaries,
    copy_corpus,
    default_binary_path,
    ensure_harness_loop_bench_corpus,
    run_harness_loop_iteration,
    run_json_command,
)

POSITIONING = "agent-native workflow benchmark; not a cold exact-text speed claim"
DEFAULT_AGENT_SCENARIOS: list[dict[str, object]] = [
    {
        "name": "ambiguous_invoice",
        "query": "change invoice tax calculation",
        "expected_min_alternatives": 1,
    },
    {
        "name": "python_invoice",
        "query": "in src/payments.py update create_invoice tax calculation",
        "expected_primary_file_suffix": "src/payments.py",
        "expected_target_file_suffix": "src/payments.py",
        "expected_target_symbol": "create_invoice",
        "expected_ask_required": False,
        "min_validation_commands": 1,
    },
    {
        "name": "ripgrep_binary_resolution",
        "query": "ripgrep binary resolution",
        "expected_primary_file_suffix": "src/tensor_grep/cli/runtime_paths.py",
        "expected_target_file_suffix": "src/tensor_grep/cli/runtime_paths.py",
        "expected_target_symbol": "resolve_ripgrep_binary",
    },
]
WRONG_CONFIDENT_MISS_THRESHOLD = 0.75


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_agent_workflow.json"


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_agent_workflow_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AGENT_WORKFLOW_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_agent_workflow"


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest_for_files(corpus_dir: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(p for p in corpus_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(corpus_dir).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"path": rel, "sha256": digest})
    return entries


def ensure_agent_workflow_corpus(output_dir: Path, *, seed: int) -> dict[str, object]:
    corpus_dir = output_dir / "agent_capsule_corpus"
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    _write_file(
        corpus_dir / "src" / "app.ts",
        """export type InvoiceLine = { net: number; taxable: boolean };

export function createInvoice(lines: InvoiceLine[], taxRate = 0.0825) {
  const subtotal = lines.reduce((sum, line) => sum + line.net, 0);
  const taxableSubtotal = lines
    .filter((line) => line.taxable)
    .reduce((sum, line) => sum + line.net, 0);
  const tax = taxableSubtotal * taxRate;
  return { subtotal, tax, total: subtotal + tax };
}

export function previewInvoice(lines: InvoiceLine[]) {
  return createInvoice(lines, 0.05);
}
""",
    )
    _write_file(
        corpus_dir / "src" / "payments.py",
        """from dataclasses import dataclass


@dataclass
class InvoiceLine:
    amount: float
    taxable: bool = True


def create_invoice(lines: list[InvoiceLine], tax_rate: float = 0.0825) -> dict[str, float]:
    subtotal = sum(line.amount for line in lines)
    taxable_subtotal = sum(line.amount for line in lines if line.taxable)
    tax = taxable_subtotal * tax_rate
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}


def preview_invoice(lines: list[InvoiceLine]) -> dict[str, float]:
    return create_invoice(lines, tax_rate=0.05)
""",
    )
    _write_file(
        corpus_dir / "tests" / "test_payments.py",
        """from src.payments import InvoiceLine, create_invoice


def test_create_invoice_taxable_lines_only():
    invoice = create_invoice([
        InvoiceLine(100.0, taxable=True),
        InvoiceLine(40.0, taxable=False),
    ])

    assert invoice["tax"] == 8.25
    assert invoice["total"] == 148.25
""",
    )
    _write_file(
        corpus_dir / "package.json",
        """{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"latest"}}\n""",
    )
    _write_file(corpus_dir / "pytest.ini", "[pytest]\npythonpath = .\n")
    _write_file(
        corpus_dir / "src" / "tensor_grep" / "cli" / "runtime_paths.py",
        """from pathlib import Path


def resolve_ripgrep_binary(configured_path: str | None = None) -> Path:
    if configured_path:
        return Path(configured_path)
    return Path("rg")


def resolve_native_binary() -> Path:
    return Path("tg")
""",
    )
    _write_file(
        corpus_dir / "src" / "tensor_grep" / "cli" / "ripgrep_fmt.py",
        """def _binary_notice(path: str) -> str:
    return f"Binary file {path} matches"


def format_ripgrep_match(path: str, line: int, text: str) -> str:
    return f"{path}:{line}:{text}"
""",
    )

    manifest_path = output_dir / "agent_capsule_manifest.json"
    manifest = {
        "artifact": "bench_agent_workflow_corpus_manifest",
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


def copy_agent_corpus(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tg_agent_workflow_"))
    shutil.copytree(src, tmp / "corpus")
    return tmp / "corpus"


def build_tg_agent_cmd(
    *,
    tg_binary: Path,
    query: str,
    corpus_dir: Path,
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
) -> list[str]:
    return [
        str(tg_binary),
        "agent",
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
        str(corpus_dir),
    ]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _float_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        return round(float(value), 3)
    return None


def _ask_required(payload: dict[str, object]) -> bool:
    ask_user = _as_dict(payload.get("ask_user_before_editing"))
    if "required" in ask_user:
        return bool(ask_user.get("required"))
    ask_before = _as_dict(payload.get("ask_before_editing"))
    if "ask_required" in ask_before:
        return bool(ask_before.get("ask_required"))
    return bool(payload.get("ask_required"))


def _alignment_status(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        status = value.get("status")
        return status if isinstance(status, str) else None
    return None


def _omission_count(payload: dict[str, object]) -> int:
    counts = _as_dict(payload.get("omission_counts"))
    if counts:
        return sum(int(value) for value in counts.values() if isinstance(value, int))

    omissions = _as_dict(payload.get("omissions"))
    total = 0
    omitted_section_count = omissions.get("omitted_section_count")
    if isinstance(omitted_section_count, int):
        total += omitted_section_count
    total += len(_as_list(omissions.get("follow_up_reads")))
    return total


def _primary_file_matches(primary_file: str, expected_suffix: object) -> bool:
    if not isinstance(expected_suffix, str) or not expected_suffix:
        return True
    normalized_file = primary_file.replace("\\", "/")
    normalized_suffix = expected_suffix.replace("\\", "/")
    return normalized_file.endswith(normalized_suffix)


def _target_file_suffix(scenario: dict[str, object]) -> str:
    for key in ("expected_target_file_suffix", "expected_primary_file_suffix"):
        value = scenario.get(key)
        if isinstance(value, str) and value:
            return value.replace("\\", "/")
    return ""


def _target_symbol(scenario: dict[str, object]) -> str:
    value = scenario.get("expected_target_symbol")
    return value if isinstance(value, str) else ""


def _expected_targets(scenario: dict[str, object]) -> list[dict[str, str]]:
    explicit_targets = scenario.get("expected_targets")
    targets: list[dict[str, str]] = []
    if isinstance(explicit_targets, list):
        for item in explicit_targets:
            if not isinstance(item, dict):
                continue
            file_suffix = item.get("file_suffix") or item.get("file")
            if not isinstance(file_suffix, str) or not file_suffix:
                continue
            symbol = item.get("symbol") or item.get("name") or ""
            targets.append({
                "file_suffix": file_suffix.replace("\\", "/"),
                "symbol": symbol if isinstance(symbol, str) else "",
            })
    else:
        file_suffix = _target_file_suffix(scenario)
        if file_suffix:
            targets.append({
                "file_suffix": file_suffix,
                "symbol": _target_symbol(scenario),
            })
    return targets


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    candidate_file = str(candidate.get("file") or "").replace("\\", "/")
    candidate_symbol = str(candidate.get("symbol") or candidate.get("name") or "")
    return candidate_file, candidate_symbol


def _candidate_matches_target(
    candidate: dict[str, Any],
    *,
    expected_file_suffix: str,
    expected_symbol: str,
) -> bool:
    if not expected_file_suffix:
        return False
    candidate_file = str(candidate.get("file") or "").replace("\\", "/")
    if not candidate_file.endswith(expected_file_suffix):
        return False
    if not expected_symbol:
        return True
    candidate_symbol = str(candidate.get("symbol") or candidate.get("name") or "")
    return candidate_symbol == expected_symbol


def _candidate_matches_any_target(
    candidate: dict[str, Any],
    *,
    expected_targets: list[dict[str, str]],
) -> bool:
    return any(
        _candidate_matches_target(
            candidate,
            expected_file_suffix=target["file_suffix"],
            expected_symbol=target["symbol"],
        )
        for target in expected_targets
    )


def _alternative_target_ranks(
    alternatives: list[object],
    *,
    expected_targets: list[dict[str, str]],
) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for rank, item in enumerate(alternatives, start=2):
        if not isinstance(item, dict):
            continue
        candidate_file, candidate_symbol = _candidate_identity(item)
        ranked.append({
            "rank": rank,
            "file": candidate_file,
            "symbol": candidate_symbol,
            "matches_expected_target": _candidate_matches_any_target(
                item,
                expected_targets=expected_targets,
            ),
        })
    return ranked


def _target_rank(
    primary_target: dict[str, Any],
    alternatives: list[object],
    *,
    expected_targets: list[dict[str, str]],
) -> int | None:
    candidates: list[dict[str, Any]] = [primary_target]
    candidates.extend(item for item in alternatives if isinstance(item, dict))
    for rank, candidate in enumerate(candidates, start=1):
        if _candidate_matches_any_target(
            candidate,
            expected_targets=expected_targets,
        ):
            return rank
    return None


def _source_file_from_item(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("file") or "").replace("\\", "/")
    if isinstance(item, str):
        return item.split("#", maxsplit=1)[0].replace("\\", "/")
    return ""


def _target_covered_by_budget(
    payload: dict[str, object],
    *,
    expected_targets: list[dict[str, str]],
) -> bool:
    if not expected_targets:
        return False
    budget_items = list(_as_list(payload.get("snippets")))
    budget_items.extend(_as_list(_as_dict(payload.get("omissions")).get("follow_up_reads")))
    for item in budget_items:
        item_file = _source_file_from_item(item)
        if any(
            target["file_suffix"] and item_file.endswith(target["file_suffix"])
            for target in expected_targets
        ):
            return True
    return False


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 3)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def extract_capsule_metrics(
    payload: dict[str, object],
    scenario: dict[str, object],
) -> dict[str, object]:
    primary_target = _as_dict(payload.get("primary_target"))
    confidence = _as_dict(payload.get("confidence"))
    consistency = _as_dict(payload.get("context_consistency"))
    alternatives = _as_list(payload.get("alternative_targets"))
    snippets = _as_list(payload.get("snippets"))
    validation_commands = _as_list(payload.get("validation_commands"))
    edit_order = _as_list(payload.get("edit_order"))
    primary_file = str(primary_target.get("file") or "")
    primary_symbol = str(primary_target.get("symbol") or primary_target.get("name") or "")
    validation_filtered_count = consistency.get("validation_filtered_count")
    if not isinstance(validation_filtered_count, int):
        validation_filtered_count = 0
    expected_file_suffix = _target_file_suffix(scenario)
    expected_symbol = _target_symbol(scenario)
    expected_targets = _expected_targets(scenario)
    target_rank = _target_rank(
        primary_target,
        alternatives,
        expected_targets=expected_targets,
    )
    target_selection_evaluated = bool(expected_targets)
    hit_at_1 = target_selection_evaluated and target_rank == 1
    hit_at_3 = target_selection_evaluated and target_rank is not None and target_rank <= 3
    mrr = round(1.0 / target_rank, 3) if target_rank is not None else 0.0
    mrr_at_3 = mrr if target_rank is not None and target_rank <= 3 else 0.0
    coverage_at_budget = _target_covered_by_budget(
        payload,
        expected_targets=expected_targets,
    )

    ask_required = _ask_required(payload)
    passed = bool(primary_file)
    if "expected_ask_required" in scenario:
        passed = passed and ask_required is bool(scenario["expected_ask_required"])
    passed = passed and _primary_file_matches(
        primary_file, scenario.get("expected_primary_file_suffix")
    )
    min_alternatives = scenario.get("expected_min_alternatives")
    if isinstance(min_alternatives, int):
        passed = passed and len(alternatives) >= min_alternatives
    min_validation_commands = scenario.get("min_validation_commands")
    if isinstance(min_validation_commands, int):
        passed = passed and len(validation_commands) >= min_validation_commands
    confidence_overall = _float_or_none(confidence.get("overall"))
    wrong_confident_miss = bool(
        target_selection_evaluated
        and not hit_at_3
        and not ask_required
        and confidence_overall is not None
        and confidence_overall >= WRONG_CONFIDENT_MISS_THRESHOLD
    )
    safe_ambiguity = bool(target_selection_evaluated and not hit_at_1 and ask_required)
    false_primary = bool(target_selection_evaluated and primary_file and not hit_at_1)
    ambiguous_requires_confirmation = bool(false_primary and ask_required)
    primary_confidence = _float_or_none(primary_target.get("confidence"))

    return {
        "scenario": str(scenario["name"]),
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "confidence_overall": confidence_overall,
        "primary_confidence": primary_confidence,
        "ask_required": ask_required,
        "alternative_count": len(alternatives),
        "snippet_count": len(snippets),
        "validation_command_count": len(validation_commands),
        "validation_alignment": _alignment_status(consistency.get("validation_alignment")),
        "validation_filtered_count": validation_filtered_count,
        "edit_order_count": len(edit_order),
        "rollback_present": bool(payload.get("rollback") or payload.get("checkpoint")),
        "omission_count": _omission_count(payload),
        "target_selection_evaluated": target_selection_evaluated,
        "expected_target_file_suffix": expected_file_suffix,
        "expected_target_symbol": expected_symbol,
        "expected_targets": expected_targets,
        "observed_primary_target": {
            "file": primary_file,
            "symbol": primary_symbol,
            "confidence": primary_confidence,
        },
        "alternative_target_ranks": _alternative_target_ranks(
            alternatives,
            expected_targets=expected_targets,
        ),
        "target_rank": target_rank,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "mrr": mrr,
        "mrr_at_3": mrr_at_3,
        "coverage_at_budget": coverage_at_budget,
        "false_primary": false_primary,
        "ambiguous_requires_confirmation": ambiguous_requires_confirmation,
        "wrong_confident_miss": wrong_confident_miss,
        "safe_ambiguity": safe_ambiguity,
        "passed": passed,
    }


def run_agent_capsule_iteration(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    iteration_index: int,
    scenarios: list[dict[str, object]],
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        command = build_tg_agent_cmd(
            tg_binary=tg_binary,
            query=str(scenario["query"]),
            corpus_dir=corpus_dir,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
        )
        elapsed_s, payload = run_json_command(command)
        rows.append({
            "iteration": iteration_index,
            "elapsed_s": elapsed_s,
            **extract_capsule_metrics(payload, scenario),
        })
    return rows


def build_agent_capsule_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    scenario_names = sorted({str(row["scenario"]) for row in rows})
    scenario_medians_s: dict[str, float] = {}
    for scenario in scenario_names:
        values = [float(row["elapsed_s"]) for row in rows if row["scenario"] == scenario]
        scenario_medians_s[scenario] = round(float(statistics.median(values)), 6)

    aligned_validation_cases = sum(
        1
        for row in rows
        if int(row["validation_command_count"]) > 0 and int(row["validation_filtered_count"]) == 0
    )
    contract_summary = {
        "total_cases": len(rows),
        "passed_cases": sum(1 for row in rows if bool(row["passed"])),
        "ask_required_cases": sum(1 for row in rows if bool(row["ask_required"])),
        "alternative_cases": sum(1 for row in rows if int(row["alternative_count"]) > 0),
        "snippet_cases": sum(1 for row in rows if int(row["snippet_count"]) > 0),
        "aligned_validation_cases": aligned_validation_cases,
        "filtered_validation_cases": sum(
            1 for row in rows if int(row["validation_filtered_count"]) > 0
        ),
        "rollback_cases": sum(1 for row in rows if bool(row["rollback_present"])),
        "total_omissions": sum(int(row["omission_count"]) for row in rows),
    }
    target_rows = [row for row in rows if bool(row["target_selection_evaluated"])]
    target_count = len(target_rows)
    hit_at_1_cases = sum(1 for row in target_rows if bool(row["hit_at_1"]))
    hit_at_3_cases = sum(1 for row in target_rows if bool(row["hit_at_3"]))
    coverage_cases = sum(1 for row in target_rows if bool(row["coverage_at_budget"]))
    false_primary_cases = sum(1 for row in target_rows if bool(row["false_primary"]))
    ambiguous_requires_confirmation_cases = sum(
        1 for row in target_rows if bool(row["ambiguous_requires_confirmation"])
    )
    wrong_confident_miss_cases = sum(1 for row in target_rows if bool(row["wrong_confident_miss"]))
    safe_ambiguity_cases = sum(1 for row in target_rows if bool(row["safe_ambiguity"]))
    hit_at_1_rate = _rate(hit_at_1_cases, target_count)
    hit_at_3_rate = _rate(hit_at_3_cases, target_count)
    mrr = _average([
        float(row.get("mrr", row.get("mrr_at_3")))
        for row in target_rows
        if isinstance(row.get("mrr", row.get("mrr_at_3")), int | float)
    ])
    mrr_at_3 = _average([
        float(row["mrr_at_3"])
        for row in target_rows
        if isinstance(row.get("mrr_at_3"), int | float)
    ])
    target_selection_summary = {
        "evaluated_cases": target_count,
        "hit_at_1": hit_at_1_rate,
        "hit_at_1_cases": hit_at_1_cases,
        "hit_at_1_rate": hit_at_1_rate,
        "hit_at_3": hit_at_3_rate,
        "hit_at_3_cases": hit_at_3_cases,
        "hit_at_3_rate": hit_at_3_rate,
        "mrr": mrr,
        "mrr_at_3": mrr_at_3,
        "coverage_at_budget_cases": coverage_cases,
        "coverage_at_budget_rate": _rate(coverage_cases, target_count),
        "false_primary_cases": false_primary_cases,
        "false_primary_rate": _rate(false_primary_cases, target_count),
        "ambiguous_requires_confirmation_cases": ambiguous_requires_confirmation_cases,
        "ambiguous_requires_confirmation_rate": _rate(
            ambiguous_requires_confirmation_cases,
            target_count,
        ),
        "wrong_confident_miss_cases": wrong_confident_miss_cases,
        "wrong_confident_miss_rate": _rate(wrong_confident_miss_cases, target_count),
        "safe_ambiguity_cases": safe_ambiguity_cases,
        "safe_ambiguity_rate": _rate(safe_ambiguity_cases, target_count),
        "wrong_confident_miss_threshold": WRONG_CONFIDENT_MISS_THRESHOLD,
    }
    return {
        "all_passed": all(bool(row["passed"]) for row in rows),
        "scenario_medians_s": scenario_medians_s,
        "contract_summary": contract_summary,
        "target_selection_summary": target_selection_summary,
        "rows": rows,
    }


def run_agent_workflow_benchmark(
    *,
    tg_binary: Path,
    agent_corpus_dir: Path,
    edit_corpus_dir: Path,
    iterations: int,
    scenarios: list[dict[str, object]],
    max_files: int,
    max_sources: int,
    max_tokens: int,
    max_repo_files: int,
    pattern: str,
    replacement: str,
) -> dict[str, object]:
    agent_rows: list[dict[str, object]] = []
    edit_rows: list[dict[str, object]] = []

    for iteration_index in range(1, iterations + 1):
        agent_work_dir = copy_agent_corpus(agent_corpus_dir)
        try:
            agent_rows.extend(
                run_agent_capsule_iteration(
                    tg_binary=tg_binary,
                    corpus_dir=agent_work_dir,
                    iteration_index=iteration_index,
                    scenarios=scenarios,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_repo_files=max_repo_files,
                )
            )
        finally:
            shutil.rmtree(agent_work_dir.parent, ignore_errors=True)

        edit_work_dir = copy_corpus(edit_corpus_dir)
        try:
            edit_rows.append(
                run_harness_loop_iteration(
                    tg_binary=tg_binary,
                    corpus_dir=edit_work_dir,
                    iteration_index=iteration_index,
                    pattern=pattern,
                    replacement=replacement,
                )
            )
        finally:
            shutil.rmtree(edit_work_dir.parent, ignore_errors=True)

    phase_medians_s, phase_totals_s = build_phase_summaries(edit_rows)
    agent_capsule = build_agent_capsule_summary(agent_rows)
    edit_loop = {
        "all_passed": all(bool(row["passed"]) for row in edit_rows),
        "phase_medians_s": phase_medians_s,
        "phase_totals_s": phase_totals_s,
        "rows": edit_rows,
    }
    return {
        "iterations": iterations,
        "agent_capsule": agent_capsule,
        "edit_loop": edit_loop,
        "all_passed": bool(agent_capsule["all_passed"]) and bool(edit_loop["all_passed"]),
    }


def build_base_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "artifact": "bench_agent_workflow",
        "suite": "run_agent_workflow_benchmarks",
        "generated_at_epoch_s": time.time(),
        "positioning": POSITIONING,
        "workflow_surfaces": ["agent_capsule", "edit_loop"],
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "iterations": args.iterations,
        "seed": args.seed,
        "agent_capsule_options": {
            "max_files": args.max_files,
            "max_sources": args.max_sources,
            "max_tokens": args.max_tokens,
            "max_repo_files": args.max_repo_files,
        },
        "edit_loop_options": {
            "file_count": args.files,
            "total_loc": args.loc,
            "pattern": args.pattern,
            "replacement": args.replacement,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark agent capsule routing plus the rewrite edit loop."
    )
    parser.add_argument("--binary", default=str(default_binary_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-files", type=int, default=3)
    parser.add_argument("--max-sources", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--max-repo-files", type=int, default=512)
    parser.add_argument("--files", type=int, default=250, help="Edit-loop synthetic file count.")
    parser.add_argument("--loc", type=int, default=12500, help="Edit-loop synthetic total LOC.")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--replacement", default=DEFAULT_REPLACEMENT)
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    payload = build_base_payload(args)
    tg_binary = resolve_tg_binary(args.binary)

    errors: list[str] = []
    if args.iterations < 1:
        errors.append("iterations must be >= 1")
    if args.files < 1:
        errors.append("files must be >= 1")
    if args.loc < args.files:
        errors.append("loc must be >= files so every generated file contains at least one line")
    if min(args.max_files, args.max_sources, args.max_tokens, args.max_repo_files) < 1:
        errors.append("agent capsule limits must all be >= 1")
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")

    if errors:
        payload.update({
            "passed": False,
            "all_passed": False,
            "error": " ".join(errors),
            "agent_capsule": {"rows": []},
            "edit_loop": {"rows": []},
        })
        write_json(output_path, payload)
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    try:
        bench_dir = resolve_agent_workflow_bench_dir()
        agent_corpus_info = ensure_agent_workflow_corpus(
            bench_dir / "agent_capsule",
            seed=args.seed,
        )
        edit_corpus_info = ensure_harness_loop_bench_corpus(
            bench_dir / "edit_loop",
            file_count=args.files,
            total_loc=args.loc,
            seed=args.seed,
        )
        results = run_agent_workflow_benchmark(
            tg_binary=tg_binary,
            agent_corpus_dir=Path(agent_corpus_info["corpus_dir"]),
            edit_corpus_dir=Path(edit_corpus_info["corpus_dir"]),
            iterations=args.iterations,
            scenarios=DEFAULT_AGENT_SCENARIOS,
            max_files=args.max_files,
            max_sources=args.max_sources,
            max_tokens=args.max_tokens,
            max_repo_files=args.max_repo_files,
            pattern=args.pattern,
            replacement=args.replacement,
        )
    except RuntimeError as exc:
        payload.update({
            "passed": False,
            "all_passed": False,
            "error": str(exc),
            "agent_capsule": {"rows": []},
            "edit_loop": {"rows": []},
        })
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 2

    payload.update({
        "tg_binary": str(tg_binary),
        "agent_corpus_dir": str(agent_corpus_info["corpus_dir"]),
        "agent_manifest_path": str(agent_corpus_info["manifest_path"]),
        "agent_file_count": agent_corpus_info["file_count"],
        "edit_corpus_dir": str(edit_corpus_info["corpus_dir"]),
        "edit_manifest_path": str(edit_corpus_info["manifest_path"]),
        **results,
    })
    payload["passed"] = bool(payload["all_passed"])
    write_json(output_path, payload)

    print(f"iterations:             {payload['iterations']}")
    print(f"all passed:             {payload['all_passed']}")
    print(f"agent capsule medians:  {payload['agent_capsule']['scenario_medians_s']}")
    print(f"edit loop medians:      {payload['edit_loop']['phase_medians_s']}")
    print(f"Results written to {output_path}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
