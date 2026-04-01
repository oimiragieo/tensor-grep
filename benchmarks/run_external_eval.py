from __future__ import annotations

import argparse
import json
import platform
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
from analyze_bakeoff_misses import analyze_bakeoff_misses, render_markdown  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_manifest_path() -> Path:
    return ROOT_DIR / "benchmarks" / "external_eval" / "manifest.json"


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_external_eval.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all external evaluation packs from a manifest."
    )
    parser.add_argument("--manifest", default=str(default_manifest_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument(
        "--profile", action="store_true", help="Include per-scenario profiling output."
    )
    parser.add_argument(
        "--provider",
        default="native",
        choices=("native", "lsp", "hybrid"),
        help="Semantic provider mode for symbol-driven scenario packs.",
    )
    parser.add_argument(
        "--write-pack-artifacts",
        action="store_true",
        help="Write per-pack bakeoff and analysis artifacts declared in the manifest.",
    )
    return parser.parse_args()


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    packs = payload.get("packs")
    if not isinstance(packs, list):
        raise ValueError("Manifest must contain a packs list.")
    resolved_packs: list[dict[str, Any]] = []
    for index, entry in enumerate(packs):
        if not isinstance(entry, dict):
            raise ValueError(f"Pack entry {index} must be an object.")
        scenario_pack = entry.get("scenario_pack")
        name = entry.get("name")
        language = entry.get("language")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Pack entry {index} is missing a valid name.")
        if not isinstance(language, str) or not language.strip():
            raise ValueError(f"Pack entry {index} is missing a valid language.")
        if not isinstance(scenario_pack, str) or not scenario_pack.strip():
            raise ValueError(f"Pack entry {index} is missing a valid scenario_pack.")
        current = dict(entry)
        current["scenario_pack"] = str((manifest_path.parent / scenario_pack).resolve())
        for field in ("artifact_output", "analysis_output", "analysis_markdown"):
            value = current.get(field)
            if isinstance(value, str) and value.strip():
                current[field] = str((manifest_path.parent / value).resolve())
        resolved_packs.append(current)
    return {"manifest_path": str(manifest_path), "packs": resolved_packs}


def run_pack(
    entry: dict[str, Any],
    *,
    profile: bool = False,
    provider: str = "native",
) -> dict[str, Any]:
    scenarios = run_bakeoff.load_scenarios(entry["scenario_pack"])
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        evaluated = run_bakeoff.evaluate_scenario(scenario, profile=profile, provider=provider)
        evaluated["pack"] = str(entry["name"])
        evaluated["language"] = str(entry["language"])
        rows.append(evaluated)
    payload = {
        "artifact": "bench_bakeoff",
        "suite": "run_bakeoff",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "repeats": 2,
        "semantic_provider": provider,
        "rows": rows,
        "summary": run_bakeoff.build_summary(rows),
    }
    return {
        "name": str(entry["name"]),
        "language": str(entry["language"]),
        "scenario_pack": str(entry["scenario_pack"]),
        "scenario_count": len(rows),
        "summary": payload["summary"],
        "analysis": analyze_bakeoff_misses(payload),
        "rows": rows,
        "payload": payload,
    }


def _language_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        language = str(row.get("language", "unknown"))
        grouped.setdefault(language, []).append(row)
    return {
        language: run_bakeoff.build_summary(group_rows)
        for language, group_rows in sorted(grouped.items())
    }


def build_external_eval_payload(
    manifest: dict[str, Any],
    *,
    profile: bool = False,
    provider: str = "native",
) -> dict[str, Any]:
    packs: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    aggregate_bucket_counts: dict[str, int] = {}
    for entry in manifest["packs"]:
        pack_result = run_pack(entry, profile=profile, provider=provider)
        packs.append({
            "name": pack_result["name"],
            "language": pack_result["language"],
            "scenario_pack": pack_result["scenario_pack"],
            "scenario_count": pack_result["scenario_count"],
            "summary": pack_result["summary"],
            "analysis": {
                "bucket_counts": pack_result["analysis"].get("bucket_counts", {}),
                "mean_file_precision": pack_result["analysis"].get("mean_file_precision", 0.0),
                "scenarios_with_false_positives": pack_result["analysis"].get(
                    "scenarios_with_false_positives", 0
                ),
            },
            "rows": pack_result["rows"],
        })
        for bucket, count in dict(pack_result["analysis"].get("bucket_counts", {})).items():
            aggregate_bucket_counts[str(bucket)] = aggregate_bucket_counts.get(
                str(bucket), 0
            ) + int(count)
        all_rows.extend(pack_result["rows"])

    return {
        "artifact": "bench_external_eval",
        "suite": "run_external_eval",
        "generated_at_epoch_s": time.time(),
        "manifest_path": manifest["manifest_path"],
        "profile": bool(profile),
        "semantic_provider": provider,
        "pack_count": len(packs),
        "packs": packs,
        "summary": run_bakeoff.build_summary(all_rows),
        "by_language": _language_summary(all_rows),
        "aggregate_bucket_counts": dict(sorted(aggregate_bucket_counts.items())),
    }


def write_pack_artifacts(entry: dict[str, Any], pack_result: dict[str, Any]) -> None:
    artifact_output = entry.get("artifact_output")
    if isinstance(artifact_output, str) and artifact_output:
        write_json(Path(artifact_output), pack_result["payload"])
    analysis_output = entry.get("analysis_output")
    if isinstance(analysis_output, str) and analysis_output:
        write_json(Path(analysis_output), pack_result["analysis"])
    analysis_markdown = entry.get("analysis_markdown")
    if isinstance(analysis_markdown, str) and analysis_markdown:
        markdown = render_markdown(
            dict(pack_result["analysis"]),
            input_path=Path(str(artifact_output or entry["scenario_pack"])),
        )
        markdown_path = Path(analysis_markdown).expanduser().resolve()
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown, encoding="utf-8")


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    pack_results = [
        run_pack(entry, profile=args.profile, provider=args.provider) for entry in manifest["packs"]
    ]
    if args.write_pack_artifacts:
        for entry, pack_result in zip(manifest["packs"], pack_results, strict=True):
            write_pack_artifacts(entry, pack_result)
    payload = {
        "artifact": "bench_external_eval",
        "suite": "run_external_eval",
        "generated_at_epoch_s": time.time(),
        "manifest_path": manifest["manifest_path"],
        "profile": bool(args.profile),
        "semantic_provider": args.provider,
        "pack_count": len(pack_results),
        "packs": [
            {
                "name": result["name"],
                "language": result["language"],
                "scenario_pack": result["scenario_pack"],
                "scenario_count": result["scenario_count"],
                "summary": result["summary"],
                "analysis": {
                    "bucket_counts": result["analysis"].get("bucket_counts", {}),
                    "mean_file_precision": result["analysis"].get("mean_file_precision", 0.0),
                    "scenarios_with_false_positives": result["analysis"].get(
                        "scenarios_with_false_positives", 0
                    ),
                },
                "rows": result["rows"],
            }
            for result in pack_results
        ],
    }
    all_rows = [row for result in pack_results for row in result["rows"]]
    aggregate_bucket_counts: dict[str, int] = {}
    for result in pack_results:
        for bucket, count in dict(result["analysis"].get("bucket_counts", {})).items():
            aggregate_bucket_counts[str(bucket)] = aggregate_bucket_counts.get(
                str(bucket), 0
            ) + int(count)
    payload["summary"] = run_bakeoff.build_summary(all_rows)
    payload["by_language"] = _language_summary(all_rows)
    payload["aggregate_bucket_counts"] = dict(sorted(aggregate_bucket_counts.items()))
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
