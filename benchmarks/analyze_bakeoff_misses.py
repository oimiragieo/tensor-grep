from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze false-positive patterns in bakeoff artifacts.")
    parser.add_argument("--input", required=True, help="Path to a bench_bakeoff JSON artifact.")
    parser.add_argument("--output", required=True, help="Path to write the analysis JSON.")
    parser.add_argument(
        "--markdown",
        help="Optional path to also write a markdown summary.",
    )
    return parser.parse_args()


def _bucket_false_positive_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    filename = parts[-1] if parts else normalized

    if "examples/" in normalized:
        return "examples"
    if filename == "__init__.py":
        return "package-entrypoint"
    if filename in {"_compat.py", "_winconsole.py", "compat.py"}:
        return "compat-layer"
    if "shell_completion" in normalized:
        return "shell-completion"
    if "formatting" in normalized:
        return "formatting"
    if "decorator" in normalized:
        return "decorators"
    if "testing" in normalized:
        return "testing"
    if "/types." in normalized or filename.startswith("types."):
        return "types"
    if "parser" in normalized:
        return "parser"
    return "module"


def analyze_bakeoff_misses(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload.get("rows", []))
    bucket_counts: Counter[str] = Counter()
    scenario_summaries: list[dict[str, Any]] = []
    for row in rows:
        false_positive_files = [str(path) for path in row.get("false_positive_files", [])]
        bucket_counter = Counter(_bucket_false_positive_path(path) for path in false_positive_files)
        bucket_counts.update(bucket_counter)
        scenario_summaries.append(
            {
                "name": str(row.get("name", "")),
                "query_or_symbol": str(row.get("query_or_symbol", "")),
                "expected_primary_file": row.get("expected_primary_file"),
                "actual_primary_file": row.get("actual_primary_file"),
                "file_hit_rate": float(row.get("file_hit_rate", 0.0)),
                "file_precision": float(row.get("file_precision", 0.0)),
                "false_positive_count": len(false_positive_files),
                "false_positive_buckets": dict(sorted(bucket_counter.items())),
                "false_positive_files": false_positive_files,
            }
        )

    worst_scenarios = sorted(
        scenario_summaries,
        key=lambda item: (
            -int(item["false_positive_count"]),
            float(item["file_precision"]),
            str(item["name"]),
        ),
    )

    return {
        "artifact": "bakeoff_miss_analysis",
        "generated_at_epoch_s": time.time(),
        "input_artifact": str(payload.get("artifact", "")),
        "scenario_count": len(rows),
        "scenarios_with_false_positives": sum(
            1 for item in scenario_summaries if int(item["false_positive_count"]) > 0
        ),
        "mean_file_hit_rate": float(payload.get("summary", {}).get("mean_file_hit_rate", 0.0)),
        "mean_file_precision": float(payload.get("summary", {}).get("mean_file_precision", 0.0)),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "worst_scenarios": worst_scenarios[:10],
        "scenarios": scenario_summaries,
    }


def render_markdown(payload: dict[str, Any], *, input_path: Path) -> str:
    lines = [
        "# Bakeoff Miss Analysis",
        "",
        f"- Input: `{input_path}`",
        f"- Scenarios: `{payload['scenario_count']}`",
        f"- Scenarios with false positives: `{payload['scenarios_with_false_positives']}`",
        f"- Mean file hit rate: `{payload['mean_file_hit_rate']}`",
        f"- Mean file precision: `{payload['mean_file_precision']}`",
        "",
        "## Bucket Counts",
    ]
    for bucket, count in payload.get("bucket_counts", {}).items():
        lines.append(f"- `{bucket}`: `{count}`")

    lines.extend(["", "## Worst Scenarios"])
    for item in payload.get("worst_scenarios", []):
        lines.append(
            f"- `{item['name']}`: fp=`{item['false_positive_count']}` precision=`{item['file_precision']}` buckets=`{item['false_positive_buckets']}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    analysis = analyze_bakeoff_misses(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        markdown_path = Path(args.markdown).expanduser().resolve()
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(analysis, input_path=input_path), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
