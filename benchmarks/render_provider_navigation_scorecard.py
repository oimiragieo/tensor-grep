from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a markdown scorecard from provider navigation artifacts."
    )
    parser.add_argument(
        "--inputs", nargs="+", required=True, help="One or more provider navigation JSON artifacts."
    )
    parser.add_argument("--output", required=True, help="Markdown output path.")
    return parser.parse_args()


def _load_provider_rows(paths: list[Path]) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        by_provider = dict(payload.get("by_provider", {}))
        for provider_name, metrics in by_provider.items():
            if not isinstance(metrics, dict):
                continue
            current = dict(metrics)
            current["provider"] = str(provider_name)
            current["_source"] = str(path)
            providers.append(current)
    return providers


def _provider_sort_key(provider_metrics: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        -float(provider_metrics.get("mean_caller_hit_rate", 0.0) or 0.0),
        -float(provider_metrics.get("mean_caller_precision", 0.0) or 0.0),
        -float(provider_metrics.get("mean_test_hit_rate", 0.0) or 0.0),
        str(provider_metrics.get("provider", "")),
    )


def render_markdown(input_path: Path | list[Path]) -> str:
    input_paths = [input_path] if isinstance(input_path, Path) else list(input_path)
    provider_rows = _load_provider_rows(input_paths)
    ordered = sorted(provider_rows, key=_provider_sort_key)
    lines = [
        "# Provider Navigation Scorecard",
        "",
        f"- Artifacts: `{len(input_paths)}`",
        f"- Provider rows: `{len(provider_rows)}`",
        "",
        "## Provider Summary",
    ]
    for current in ordered:
        lines.append(
            "- "
            f"`{current.get('provider', 'unknown')}` "
            f"caller_hit_rate=`{current.get('mean_caller_hit_rate', 0.0)}` "
            f"caller_precision=`{current.get('mean_caller_precision', 0.0)}` "
            f"test_hit_rate=`{current.get('mean_test_hit_rate', 0.0)}` "
            f"scenarios=`{current.get('scenario_count', 0)}`"
        )
    if ordered:
        winner = ordered[0]
        lines.extend([
            "",
            "## Recommended Provider",
            "",
            f"- `{winner.get('provider', 'unknown')}` from `{winner.get('_source', 'unknown')}`",
        ])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    input_paths = [Path(path).expanduser().resolve() for path in args.inputs]
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "suite": "render_provider_navigation_scorecard",
        "generated_at_epoch_s": time.time(),
        "artifact": "provider_navigation_scorecard",
        "inputs": [str(path) for path in input_paths],
    }
    output_path.write_text(
        "<!-- " + json.dumps(metadata, sort_keys=True) + " -->\n" + render_markdown(input_paths),
        encoding="utf-8",
    )
    print(f"Scorecard written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
