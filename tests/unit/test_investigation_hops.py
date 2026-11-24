from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.investigation_hops import investigation_hops
from tensor_grep.core.dependency_grounding import dependency_grounding


def test_investigation_hops_multi_stage_from_blast_radius_floor_and_validation_plan() -> None:
    capsule = {
        "primary_target": {
            "file": "src/pkg/billing.py",
            "line": 12,
            "symbol": "compute_invoice",
            "evidence": ["parser-backed", "heuristic"],
        },
        "ask_user_before_editing": {"required": False},
        "related_call_sites": [],
        "blast_radius_floor": {
            "symbol": "compute_invoice",
            "callers_count": 1,
            "top_callers": [
                {
                    "file": "src/pkg/service.py",
                    "line": 44,
                    "symbol": "compute_invoice",
                    "provenance": "python-ast",
                }
            ],
        },
        "validation_plan": [
            {
                "command": "pytest tests/test_billing.py",
                "scope": "file",
                "target": "tests/test_billing.py",
                "detection": "import-graph",
                "confidence": 0.9,
            },
            {
                "command": "pytest",
                "scope": "repo",
                "detection": "heuristic",
                "confidence": 0.5,
            },
        ],
    }

    graph = investigation_hops(capsule, {"tests": ["tests/test_billing.py"]})

    assert graph["status"] == "complete"
    assert [stage["stage"] for stage in graph["stages"]] == [1, 2, 3]
    assert [stage["kind"] for stage in graph["stages"]] == ["declarations", "callers", "tests"]

    stage1_node = graph["stages"][0]["nodes"][0]
    stage2_node = graph["stages"][1]["nodes"][0]
    stage3_node = graph["stages"][2]["nodes"][0]

    assert stage1_node["provenance"] == "parser-backed"
    assert stage2_node["file"] == "src/pkg/service.py"
    assert stage2_node["provenance"] == "python-ast"
    assert stage3_node["file"] == "tests/test_billing.py"
    assert stage3_node["provenance"] == "graph-derived"

    assert len(graph["edges"]) == 2
    assert graph["edges"][0]["from"] == "src/pkg/billing.py#L12"
    assert graph["edges"][0]["to"] == "src/pkg/service.py#L44"
    assert graph["edges"][0]["relation"] == "calls-declaration"
    assert graph["edges"][1]["from"] == "src/pkg/billing.py#L12"
    assert graph["edges"][1]["to"] == "tests/test_billing.py#L1"
    assert graph["edges"][1]["relation"] == "validates-declaration"
    assert all(edge["evidence"]["provenance"] != "unknown" for edge in graph["edges"])


def test_investigation_hops_preserves_graph_derived_and_skips_heuristic_validation() -> None:
    capsule = {
        "primary_target": {
            "file": "src/app.py",
            "line": 5,
            "symbol": "run_job",
            "provenance": "graph-derived",
        },
        "ask_user_before_editing": {"required": False},
        "related_call_sites": [
            {
                "file": "src/worker.py",
                "line": 19,
                "symbol": "run_job",
                "ref_kind": "import",
                "provenance": "graph-derived",
            }
        ],
        "validation_plan": [
            {
                "command": "pytest tests/test_guess.py",
                "scope": "file",
                "target": "tests/test_guess.py",
                "detection": "heuristic",
                "confidence": 0.4,
            }
        ],
    }

    graph = investigation_hops(capsule, {})

    assert [stage["stage"] for stage in graph["stages"]] == [1, 2]
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["relation"] == "imports-declaration"
    assert graph["edges"][0]["evidence"]["provenance"] == "graph-derived"


def test_grounding_walks_up_from_subdirectory_to_manifest_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="sub-test"\ndependencies=["typer>=0.12", "rich"]\n',
        encoding="utf-8",
    )
    subdir = tmp_path / "src" / "tensor_grep" / "cli"
    subdir.mkdir(parents=True)

    evidence = dependency_grounding(subdir, "local")

    assert evidence is not None
    assert evidence["mode"] == "local"
    assert evidence["status"] == "complete"
    names = {row["name"] for row in evidence["dependencies"]}
    assert {"typer", "rich"} <= names


def test_grounding_walks_up_to_go_mod_and_reports_no_manifest_in_scope(tmp_path: Path) -> None:
    go_repo = tmp_path / "go_repo"
    go_sub = go_repo / "internal" / "worker"
    go_sub.mkdir(parents=True)
    (go_repo / "go.mod").write_text(
        "module example.com/worker\n\nrequire (\n\tgolang.org/x/sync v0.7.0\n)\n",
        encoding="utf-8",
    )

    go_evidence = dependency_grounding(go_sub, "local")
    assert go_evidence is not None
    assert go_evidence["status"] == "complete"
    assert [row["name"] for row in go_evidence["dependencies"]] == ["golang.org/x/sync"]

    empty_repo = tmp_path / "empty_repo"
    (empty_repo / ".git").mkdir(parents=True)
    empty_sub = empty_repo / "nested" / "dir"
    empty_sub.mkdir(parents=True)

    empty_evidence = dependency_grounding(empty_sub, "local")
    assert empty_evidence is not None
    assert empty_evidence["status"] == "no_manifest_in_scope"
    assert empty_evidence["dependencies"] == []

    expired_evidence = dependency_grounding(empty_sub, "local", deadline_monotonic=0.0)
    assert expired_evidence is not None
    assert expired_evidence["status"] == "partial"


def test_investigation_hops_skips_unverified_detected_validation_step() -> None:
    capsule = {
        "primary_target": {
            "file": "src/app.py",
            "line": 5,
            "symbol": "run_job",
            "provenance": "parser-backed",
        },
        "ask_user_before_editing": {"required": False},
        "related_call_sites": [],
        "validation_plan": [
            {
                "command": "pytest tests/test_unverified.py",
                "scope": "file",
                "target": "tests/test_unverified.py",
                "detection": "detected",
                "confidence": 0.8,
            }
        ],
    }

    graph = investigation_hops(capsule, {})
    assert [stage["stage"] for stage in graph["stages"]] == [1]
    assert graph["edges"] == []


def test_build_agent_capsule_populates_multi_hop_graph_and_grounding(tmp_path: Path) -> None:
    from tensor_grep.cli.agent_capsule import build_agent_capsule

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="hop-demo"\ndependencies=["pytest>=8.0"]\n',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "core.py").write_text(
        "def compute_total(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (src / "caller.py").write_text(
        "from core import compute_total\n\ndef run() -> int:\n    return compute_total(41)\n",
        encoding="utf-8",
    )

    capsule = build_agent_capsule(
        "compute_total",
        str(src),
        plan_hops=True,
        grounding="local",
    )
    graph = capsule.get("investigation_hops") or {}
    assert len(graph.get("stages", [])) >= 2
    assert len(graph.get("edges", [])) >= 1
    assert capsule["dependency_grounding"]["status"] == "complete"
    assert any(row["name"] == "pytest" for row in capsule["dependency_grounding"]["dependencies"])
