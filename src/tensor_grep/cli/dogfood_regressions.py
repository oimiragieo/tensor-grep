"""Disposable artifact checks for grounding, investigation, and content reconciliation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def run_regressions(
    root: Path, *, run: Callable[..., tuple[int, str, str]], record: Callable[..., None]
) -> None:
    fixture = root / "improvement-regressions"
    fixture.mkdir()
    source = fixture / "library.py"
    source.write_text("def calculate_total(amount):\n    return amount * 2\n", encoding="utf-8")
    (fixture / "consumer.py").write_text(
        "from library import calculate_total\n\ndef consume():\n    return calculate_total(3)\n",
        encoding="utf-8",
    )
    (fixture / "test_library.py").write_text(
        "from library import calculate_total\n\ndef test_total():\n    assert calculate_total(3) == 6\n",
        encoding="utf-8",
    )
    (fixture / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0.0.0"\ndependencies=["requests>=2.0"]\n',
        encoding="utf-8",
    )

    def probe(
        name: str,
        args: list[str],
        predicate: Callable[[dict[str, Any]], bool],
        *,
        env: dict[str, str] | None = None,
        want_exit: int = 0,
        refusal: bool = False,
    ) -> dict[str, Any] | None:
        code, out, err = run(args, extra_env=env)
        payload = None
        detail = ""
        ok = code == want_exit
        if not ok:
            detail = f"exit {code} != {want_exit}"
        elif not refusal:
            try:
                payload = json.loads(out)
                ok = predicate(payload)
                if not ok:
                    detail = "output did not establish the required evidence"
            except (ValueError, TypeError, KeyError) as exc:
                ok = False
                detail = str(exc)
        else:
            ok = "cross-encoder unavailable" in out + err
            if not ok:
                detail = "refusal did not identify missing cross-encoder capability"
        record(ok, name, code, detail, out + err)
        return payload

    path = str(fixture)
    defs = ["defs", path, "calculate_total", "--json"]

    def has_target(payload: dict[str, Any]) -> bool:
        return any(row.get("name") == "calculate_total" for row in payload.get("definitions", []))

    probe(
        "AST cache cold declaration",
        defs,
        lambda payload: (
            has_target(payload) and int(payload.get("symbol_cache", {}).get("misses", 0)) > 0
        ),
    )
    probe(
        "AST cache warm declaration",
        defs,
        lambda payload: (
            has_target(payload) and int(payload.get("symbol_cache", {}).get("hits", 0)) > 0
        ),
    )

    find = ["find", "calculate_total", path, "--json"]
    probe(
        "find local dependency grounding",
        [*find, "--grounding", "local"],
        lambda payload: (
            payload.get("dependency_grounding", {}).get("mode") == "local"
            and any(
                row.get("name") == "requests"
                for row in payload.get("dependency_grounding", {}).get("dependencies", [])
            )
        ),
    )
    probe(
        "find grounding off preserves legacy output",
        [*find, "--grounding", "off"],
        lambda payload: "dependency_grounding" not in payload,
    )
    assets = root / "missing-reranker-assets"
    env = {"TG_CROSS_ENCODER_DIR": str(assets)}
    probe(
        "find auto rerank disclosed fallback",
        [*find, "--rerank", "auto"],
        lambda payload: (
            "cross-encoder unavailable" in str(payload.get("rank_fallback_reason", ""))
            and payload.get("rank_fusion", {}).get("cross_encoder", {}).get("scoring") is None
        ),
        env=env,
    )
    probe(
        "find explicit reranker missing-assets refusal",
        [*find, "--rerank", "cross-encoder"],
        lambda _: True,
        env=env,
        want_exit=1,
        refusal=True,
    )
    record(
        not assets.exists(),
        "reranker queries do not download assets",
        0,
        "asset directory created during missing-capability queries" if assets.exists() else "",
    )

    def supported_graph(payload: dict[str, Any]) -> bool:
        graph = payload.get("investigation_hops", {})
        stages = graph.get("stages", [])
        ids = {node["id"] for stage in stages for node in stage.get("nodes", [])}
        return (
            graph.get("status") == "complete"
            and 1 <= len(stages) <= 3
            and stages[0].get("kind") == "declarations"
            and bool(stages[0].get("nodes"))
            and bool(graph.get("edges"))
            and all(
                edge.get("from") in ids
                and edge.get("to") in ids
                and edge.get("evidence", {}).get("provenance")
                not in (None, "unknown", "heuristic", "stale")
                for edge in graph.get("edges", [])
            )
        )

    probe(
        "agent evidence-supported investigation hops",
        ["agent", path, "calculate_total", "--json", "--plan-hops", "--grounding", "off"],
        supported_graph,
    )

    original = source.stat()
    source.write_text("def calculate_other(amount):\n    return amount * 2\n", encoding="utf-8")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    probe(
        "AST cache same-mtime edit reconciliation",
        ["defs", path, "calculate_other", "--json"],
        lambda payload: any(
            row.get("name") == "calculate_other" for row in payload.get("definitions", [])
        ),
    )
    probe(
        "AST cache removes stale declaration",
        defs,
        lambda payload: not payload.get("definitions"),
        want_exit=1,
    )
