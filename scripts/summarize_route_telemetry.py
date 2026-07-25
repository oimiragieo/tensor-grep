#!/usr/bin/env python3
"""Aggregate `tg` plain-text-native route telemetry into an admission-rate report.

Answers "how often is the plain-text native route actually taken, and when it is not, WHICH clause
refused?" -- a question the repo's benchmark-regression gate cannot answer, because none of the
benchmark scenarios, none of the dogfood calls, and none of the MCP surface (always `--json`) are
eligible for the route.

Usage::

    TG_ROUTE_TELEMETRY=1 TG_ROUTE_TELEMETRY_PATH=/tmp/route.jsonl \
        uv run python benchmarks/run_benchmarks.py     # or any tg workload, unchanged
    python scripts/summarize_route_telemetry.py /tmp/route.jsonl

The workload needs no modification: the counter is emitted by `tg` itself, default-OFF.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Order matters: the first false clause is reported as THE reason a request was refused, so this
# must match the evaluation order in `plain_text_native_cheap_checks_pass` then the expensive tier.
CLAUSES: tuple[tuple[str, bool], ...] = (
    ("only_allowed_flags", True),
    ("structured_output", False),
    ("explicit_format", False),
    ("stdout_is_terminal", False),
    ("rg_config_env_present", False),
    ("path_was_implicit", False),
    ("pattern_is_empty", False),
    ("single_path_is_regular_file", True),
    ("single_path_is_stdin_sentinel", False),
    ("pattern_is_native_renderable", True),
    ("single_path_renders_identically", True),
)


def _first_refusing_clause(record: dict) -> str:
    if record.get("pattern_count") != 1:
        return f"pattern_count={record.get('pattern_count')}"
    if record.get("path_count") != 1:
        return f"path_count={record.get('path_count')}"
    for name, want_true in CLAUSES:
        value = record.get(name)
        if value is None:
            continue
        if bool(value) is not want_true:
            return name
    return "(none - admitted)"


def default_path() -> Path:
    override = os.environ.get("TG_ROUTE_TELEMETRY_PATH")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "tg-route-telemetry.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=None)
    args = parser.parse_args()
    path = args.path or default_path()

    if not path.is_file():
        print(f"no telemetry at {path}", file=sys.stderr)
        print("run a workload with TG_ROUTE_TELEMETRY=1 first", file=sys.stderr)
        return 1

    by_stage: Counter[str] = Counter()
    admitted: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    malformed = 0

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        stage = str(record.get("stage", "?"))
        by_stage[stage] += 1
        if record.get("admitted"):
            admitted[stage] += 1
        else:
            reasons[_first_refusing_clause(record)] += 1

    total = sum(by_stage.values())
    print(f"route telemetry: {path}")
    print(f"  records: {total}" + (f"  (malformed: {malformed})" if malformed else ""))
    if not total:
        return 0

    print("\n  admission rate by stage:")
    for stage, count in sorted(by_stage.items()):
        rate = 100.0 * admitted[stage] / count
        print(f"    {stage:<12} {admitted[stage]:>6} / {count:<6} admitted  ({rate:5.1f}%)")

    # The clap stage is the real admission surface: an admitted request always reaches it.
    clap_total = by_stage.get("clap", 0)
    if clap_total:
        rate = 100.0 * admitted["clap"] / clap_total
        print(
            f"\n  ROUTE TAKEN: {admitted['clap']} of {clap_total} clap-stage searches ({rate:.1f}%)"
        )

    if reasons:
        print("\n  first refusing clause:")
        for name, count in reasons.most_common():
            print(f"    {name:<34} {count:>6}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
