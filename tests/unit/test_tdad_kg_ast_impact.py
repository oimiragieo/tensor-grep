"""Unit tests for Wave 4 Track R (IMP-06): TDAD, KG-AST risk scoring, and minimal change slices."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli import audit_manifest
from tensor_grep.cli.diff_impact import build_diff_blast_radius
from tensor_grep.cli.main import app
from tensor_grep.cli.tdad_kg_ast import (
    TDAD_CACHE_REL_PATH,
    build_tdad_dependency_map,
    extract_change_directed_slice,
    query_tdad_tests,
    record_tdad_edges,
    score_kg_ast_commit_risk,
    verify_change_slice_digest,
)

runner = CliRunner()


def _write_audit_manifest(path: Path, *, project_root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-07-18T12:00:00Z",
        "lang": "python",
        "path": str(project_root),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "src/service.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": None,
    }
    canonical = dict(payload)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def test_tdad_persists_in_tg_cache_graph_v1_and_queries_tests(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (repo / "tests" / "test_billing.py").write_text(
        "from src.billing import charge\n\ndef test_charge():\n    assert charge(1) == 1\n",
        encoding="utf-8",
    )

    repo_map = {
        "files": ["src/billing.py", "tests/test_billing.py"],
        "tests": ["tests/test_billing.py"],
        "symbols": [{"file": "src/billing.py", "name": "charge", "kind": "function"}],
        "imports": {"tests/test_billing.py": ["src.billing", "charge"]},
    }
    build_info = build_tdad_dependency_map(repo, repo_map_payload=repo_map, persist=True)
    db_path = repo / TDAD_CACHE_REL_PATH
    assert build_info["persisted"] is True
    assert db_path.exists()
    assert ".tg_cache/graph_v1" in build_info["cache_path"]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source_file, source_symbol, test_file FROM source_test_deps"
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == "src/billing.py" and r[2] == "tests/test_billing.py" for r in rows)

    # Query from persisted DB without passing repo_map_payload
    hits = query_tdad_tests(
        repo,
        ["src/billing.py"],
        [{"file": "src/billing.py", "name": "charge"}],
        repo_map_payload=None,
    )
    assert len(hits) == 1
    assert hits[0]["test_file"] == "tests/test_billing.py"
    assert hits[0]["score"] > 0.0
    assert "src/billing.py" in hits[0]["matched_sources"]


def test_kg_ast_commit_risk_ordering_and_prioritized_tests() -> None:
    low_risk = score_kg_ast_commit_risk(
        changed_files=["src/leaf.py"],
        changed_symbols=[{"file": "src/leaf.py", "name": "leaf_fn"}],
        callers=[],
        affected_files=["src/leaf.py"],
        tdad_tests=[{"test_file": "tests/test_leaf.py", "score": 0.6}],
        changed_line_counts={"src/leaf.py": 4},
    )
    high_risk = score_kg_ast_commit_risk(
        changed_files=["src/core.py", "src/auth.py"],
        changed_symbols=[
            {"file": "src/core.py", "name": "dispatch"},
            {"file": "src/auth.py", "name": "verify_token"},
        ],
        callers=[
            {"file": f"src/caller_{i}.py", "caller": f"fn_{i}", "line": i + 1} for i in range(10)
        ],
        affected_files=[f"src/caller_{i}.py" for i in range(10)] + ["tests/test_auth.py"],
        tdad_tests=[
            {"test_file": "tests/test_core.py", "score": 1.8},
            {"test_file": "tests/test_auth.py", "score": 1.0},
        ],
        changed_line_counts={"src/core.py": 120, "src/auth.py": 80},
    )

    assert high_risk["risk_score"] > low_risk["risk_score"]
    assert high_risk["risk_level"] in {"high", "critical"}
    assert low_risk["risk_level"] == "low"
    assert high_risk["prioritized_tests"][0] == "tests/test_core.py"
    assert "tests/test_auth.py" in high_risk["prioritized_tests"]
    assert set(high_risk["features"]) == {
        "churn_size",
        "fan_in",
        "fan_out",
        "symbol_centrality",
        "co_change_coupling",
    }


def test_minimal_change_slice_and_sha256_digest_binding() -> None:
    bundle_a = extract_change_directed_slice(
        changed_files=["src/b.py", "src/a.py"],
        changed_symbols=[{"file": "src/a.py", "name": "alpha"}],
        callers=[{"file": "src/c.py", "caller": "gamma", "target": "alpha"}],
        affected_files=["src/c.py", "tests/test_a.py"],
        tdad_tests=[{"test_file": "tests/test_a.py", "matched_sources": ["src/a.py"]}],
    )
    bundle_b = extract_change_directed_slice(
        changed_files=["src/a.py", "src/b.py"],
        changed_symbols=[{"file": "src/a.py", "name": "alpha"}],
        callers=[{"file": "src/c.py", "caller": "gamma", "target": "alpha"}],
        affected_files=["tests/test_a.py", "src/c.py"],
        tdad_tests=[{"test_file": "tests/test_a.py", "matched_sources": ["src/a.py"]}],
    )

    assert bundle_a["slice_digest"] == bundle_b["slice_digest"]
    assert len(bundle_a["slice_digest"]) == 64
    assert verify_change_slice_digest(bundle_a["slice"], bundle_a["slice_digest"]) is True

    tampered = dict(bundle_a["slice"])
    tampered["changed_files"] = ["src/evil.py"]
    assert verify_change_slice_digest(tampered, bundle_a["slice_digest"]) is False


def test_diff_impact_includes_tdad_tests_kg_ast_risk_and_slice(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "sample.py").write_text("def compute(x):\n    return x + 1\n", encoding="utf-8")
    (repo / "tests" / "test_sample.py").write_text(
        "from src.sample import compute\n\ndef test_compute():\n    assert compute(1) == 2\n",
        encoding="utf-8",
    )
    record_tdad_edges(
        repo,
        [("src/sample.py", "compute", "tests/test_sample.py", "symbol_import", 1.0)],
    )

    diff_text = """diff --git a/src/sample.py b/src/sample.py
index 1111111..2222222 100644
--- a/src/sample.py
+++ b/src/sample.py
@@ -1,2 +1,2 @@
 def compute(x):
-    return x + 0
+    return x + 1
"""
    payload = build_diff_blast_radius(root=repo, diff_text=diff_text)

    assert payload["changed_files"] == ["src/sample.py"]
    assert any(t["test_file"] == "tests/test_sample.py" for t in payload["tdad_tests"])
    assert "risk_score" in payload["kg_ast_risk"]
    assert "prioritized_tests" in payload["kg_ast_risk"]
    assert verify_change_slice_digest(payload["change_slice"], payload["slice_digest"]) is True


def test_review_bundle_create_binds_change_slice_and_verifies(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)
    (project / "src" / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (project / "tests" / "test_service.py").write_text(
        "from src.service import run\n\ndef test_run():\n    assert run()\n", encoding="utf-8"
    )
    record_tdad_edges(
        project,
        [("src/service.py", "", "tests/test_service.py", "import", 1.0)],
    )
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"

    res = runner.invoke(
        app,
        [
            "review-bundle",
            "create",
            "--manifest",
            str(manifest_path),
            "--output",
            str(bundle_path),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    bundle = json.loads(res.stdout)
    assert bundle["change_slice"]["changed_files"] == ["src/service.py"]
    assert any(n["file"] == "tests/test_service.py" for n in bundle["change_slice"]["nodes"])
    assert "change_slice" in bundle["checksums"]
    assert verify_change_slice_digest(bundle["change_slice"], bundle["slice_digest"]) is True

    verified = audit_manifest.verify_review_bundle(bundle_path)
    assert verified["valid"] is True

    # Tampering slice_digest must fail verification closed
    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["slice_digest"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    failed = audit_manifest.verify_review_bundle(bundle_path)
    assert failed["valid"] is False
