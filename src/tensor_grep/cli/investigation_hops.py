"""Evidence-only investigation graphs over the capsule's existing repository map."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli.repo_map_test_paths import _is_test_file


def investigation_hops(
    capsule: dict[str, Any],
    rm: dict[str, Any],
    *,
    deadline_monotonic: float | None = None,
    max_tokens: int | None = None,
    max_nodes: int = 12,
    test_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """No reads, walks, mutations, or network calls; reuse bounded collected evidence."""
    target = capsule.get("primary_target", {})
    graph: dict[str, Any] = {
        "status": "complete",
        "stages": [],
        "edges": [],
        "omitted_nodes": 0,
        "evidence_scope": "existing-capsule-and-repository-map",
        "shared_scan_budget": True,
    }
    if capsule.get("ask_user_before_editing", {}).get("required"):
        graph.update(status="ambiguous", reason="target requires clarification")
        return graph
    if not target.get("file") or not target.get("symbol"):
        graph.update(status="unavailable", reason="no supported declaration target")
        return graph
    deadline = deadline_monotonic or float("inf")
    stages: list[dict[str, Any]] = [
        {"stage": 1, "kind": "declarations", "nodes": []},
        {"stage": 2, "kind": "callers", "nodes": []},
        {"stage": 3, "kind": "tests", "nodes": []},
    ]
    chars = 0
    count = 0

    def append(
        stage: int,
        record: dict[str, Any],
        relation: str | None,
        parent: dict[str, Any] | None = None,
    ) -> None:
        nonlocal chars, count
        node = {
            "id": f"{record['file']}#L{record.get('line', 1)}",
            "file": record["file"],
            "line": record.get("line", 1),
            "symbol": record.get("symbol"),
            "provenance": record.get("provenance", "unknown"),
        }
        source = parent or target
        edge = {
            "from": f"{source['file']}#L{source.get('line', 1)}",
            "to": node["id"],
            "relation": relation,
            "evidence": {
                "file": node["file"],
                "line": node["line"],
                "provenance": node["provenance"],
                "ref_kind": record.get("ref_kind"),
                "association": record.get("association"),
            },
        }
        cost = len(json.dumps(node)) + (len(json.dumps(edge)) if relation else 0)
        if count >= max_nodes or (max_tokens is not None and chars + cost > max_tokens * 4):
            graph["omitted_nodes"] += 1
            return
        if node["id"] in {entry["id"] for entry in stages[stage - 1]["nodes"]}:
            return
        chars += cost
        count += 1
        stages[stage - 1]["nodes"].append(node)
        if relation:
            graph["edges"].append(edge)

    append(1, target, None)
    # A connected graph must contain its declaration; don't leave dangling evidence edges.
    if not stages[0]["nodes"]:
        graph.update(status="partial", reason="token_budget")
        return graph
    for record in sorted(
        capsule.get("related_call_sites", []),
        key=lambda row: (str(row.get("file", "")), int(row.get("line", 1))),
    ):
        if time.monotonic() >= deadline:
            graph.update(status="partial", reason="shared_deadline")
            break
        if not record.get("file") or record.get("ref_kind") not in {"call", "import"}:
            continue
        # Heuristics stay visible; unknown evidence cannot establish an edge.
        if record.get("provenance") in {None, "unknown", "heuristic", "stale"}:
            continue
        is_test = _is_test_file(Path(str(record["file"])))
        relation = (
            "imports-declaration" if record.get("ref_kind") == "import" else "calls-declaration"
        )
        append(
            3 if is_test else 2,
            record,
            f"test-{relation}" if is_test else relation,
        )
    # Bind an existing test association to its specific declaration/caller import evidence.
    # Broad associations may concern another ranked file; they cannot establish this edge.
    root = Path(str(rm.get("path", "."))).absolute()
    imports_by_file = {str(entry.get("file")): entry for entry in rm.get("imports", [])}
    for match in sorted(test_matches or [], key=lambda item: str(item.get("path", ""))):
        if time.monotonic() >= deadline:
            graph.update(status="partial", reason="shared_deadline")
            break
        association = match.get("association", {})
        if association.get("edge_kind") not in {"import-graph", "hybrid"} or not match.get("path"):
            continue
        test_imports = imports_by_file.get(str(match["path"]), {})
        if test_imports.get("provenance") != "parser-backed":
            continue
        parents = [*stages[1]["nodes"], target]
        supported_parent = None
        module_evidence = None
        for parent in parents:
            source = Path(str(parent["file"])).absolute()
            if source.suffix != ".py":
                continue
            try:
                relative = source.relative_to(root).with_suffix("")
            except ValueError:
                continue
            module = ".".join(relative.parts)
            for item in test_imports.get("imports", []):
                if isinstance(item, str) and (item == module or item.startswith(f"{module}.")):
                    supported_parent = parent
                    module_evidence = item
                    break
            if supported_parent is not None:
                break
        if supported_parent is None:
            continue
        record = {
            "file": str(match["path"]),
            "line": 1,
            "provenance": "graph-derived",
            "association": {
                **association,
                "import_module": module_evidence,
                "runtime_identity_verified": False,
            },
        }
        # The original association supports an edge to the selected declaration. A direct
        # test caller already creates its stronger line-addressed edge above.
        if not any(node["file"] == record["file"] for node in stages[2]["nodes"]):
            append(3, record, "associated-test", supported_parent)
    graph["stages"] = [stage for stage in stages if stage["nodes"]]
    graph["omitted_nodes"] += int(
        capsule.get("call_site_evidence", {}).get("omitted_call_sites", 0) or 0
    )
    if capsule.get("partial") or capsule.get("result_incomplete") or graph["omitted_nodes"]:
        graph["status"] = "partial"
    graph["unlinked_test_count"] = max(0, len(rm.get("tests", [])) - len(stages[2]["nodes"]))
    return graph


def rerank_capsule_snippets(
    capsule: dict[str, Any], query: str, mode: str, *, deadline_monotonic: float | None = None
) -> None:
    """Cross-encoder ordering is advisory; target identity and line maps are unchanged."""
    from tensor_grep.core.cross_encoder import apply_cross_encoder
    from tensor_grep.core.result import MatchLine, SearchResult

    snippets = capsule.get("snippets", [])
    result = SearchResult(
        matches=[
            MatchLine(
                index + 1, str(row.get("source") or row.get("text") or ""), str(row.get("file", ""))
            )
            for index, row in enumerate(snippets)
        ],
        total_matches=len(snippets),
    )
    result = apply_cross_encoder(result, query, mode, deadline_monotonic=deadline_monotonic)
    if mode != "off":
        capsule["snippets"] = [snippets[match.line_number - 1] for match in result.matches]
        capsule["reranking"] = {
            "scope": "advisory-snippet-order",
            "target_selection_changed": False,
            **(result.rank_fusion or {}),
        }
        if result.rank_fallback_reason:
            capsule["reranking"]["fallback_reason"] = result.rank_fallback_reason
