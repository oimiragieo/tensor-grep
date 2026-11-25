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
    ensure_tdad_dependency_map,
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


def test_tdad_and_kg_ast_handle_real_build_repo_map_list_imports(tmp_path: Path) -> None:
    from tensor_grep.cli.repo_map import build_repo_map

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "ledger.py").write_text(
        "import json\nimport hashlib\n\ndef post_entry(x):\n    return json.dumps({'x': x})\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_ledger_integration.py").write_text(
        "from src.ledger import post_entry\n\n"
        "def test_post_entry():\n"
        "    assert '1' in post_entry(1)\n",
        encoding="utf-8",
    )

    real_rm = build_repo_map(repo)
    assert isinstance(real_rm["imports"], list)

    build_info = build_tdad_dependency_map(repo, repo_map_payload=real_rm, persist=True)
    assert build_info["persisted"] is True
    assert build_info["edge_count"] > 0

    hits = query_tdad_tests(
        repo,
        ["src/ledger.py"],
        [{"file": "src/ledger.py", "name": "post_entry"}],
        repo_map_payload=real_rm,
    )
    assert any(h["test_file"] == "tests/test_ledger_integration.py" for h in hits)

    risk = score_kg_ast_commit_risk(
        changed_files=["src/ledger.py"],
        changed_symbols=[{"file": "src/ledger.py", "name": "post_entry"}],
        callers=[],
        affected_files=["src/ledger.py"],
        tdad_tests=hits,
        repo_map_payload=real_rm,
        repo_root=repo,
    )
    assert risk["features"]["fan_out"] > 0.0


def test_tdad_cache_invalidates_on_file_change_and_git_head_change(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import os

    from tensor_grep.cli import tdad_kg_ast

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    src_file = repo / "src" / "alpha.py"
    beta_file = repo / "src" / "beta.py"
    test_file = repo / "tests" / "test_suite_one.py"
    src_file.write_text("def fn_a():\n    return 1\n", encoding="utf-8")
    beta_file.write_text("def fn_b():\n    return 2\n", encoding="utf-8")
    test_file.write_text("from src.alpha import fn_a\n", encoding="utf-8")

    rev = ["rev-1"]
    monkeypatch.setattr(tdad_kg_ast, "_git_head_revision", lambda _root: rev[0])

    first = ensure_tdad_dependency_map(repo, persist=True)
    assert first["cache_hit"] is False
    second = ensure_tdad_dependency_map(repo, persist=True)
    assert second["cache_hit"] is True

    # Modify test_file on disk so it now imports beta instead of alpha
    test_file.write_text(
        "from src.beta import fn_b\n\ndef test_b():\n    assert fn_b() == 2\n", encoding="utf-8"
    )
    stat = test_file.stat()
    os.utime(test_file, ns=(stat.st_atime_ns + 1_000_000, stat.st_mtime_ns + 1_000_000))

    beta_hits = query_tdad_tests(repo, ["src/beta.py"], repo_map_payload=None)
    assert any(h["test_file"] == "tests/test_suite_one.py" for h in beta_hits)
    alpha_hits = query_tdad_tests(repo, ["src/alpha.py"], repo_map_payload=None)
    assert not any(h["test_file"] == "tests/test_suite_one.py" for h in alpha_hits)

    # Now bump git HEAD revision without touching files -> must invalidate
    rev[0] = "rev-2"
    third = ensure_tdad_dependency_map(repo, persist=True)
    assert third["cache_hit"] is False


def test_tdad_cache_invalidates_on_same_size_edit_with_restored_mtime(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import os

    from tensor_grep.cli import tdad_kg_ast

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    src_alpha = repo / "src" / "alpha.py"
    src_beta = repo / "src" / "beta.py"
    test_file = repo / "tests" / "test_suite.py"
    src_alpha.write_text("def fn_a():\n    return 1\n", encoding="utf-8")
    src_beta.write_text("def fn_b():\n    return 2\n", encoding="utf-8")
    original_content = "from src.alpha import fn_a\n\ndef test_it():\n    assert fn_a() == 1\n"
    updated_content = "from src.beta  import fn_b\n\ndef test_it():\n    assert fn_b() == 2\n"
    assert len(original_content.encode("utf-8")) == len(updated_content.encode("utf-8"))
    test_file.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(tdad_kg_ast, "_git_head_revision", lambda _root: "rev-1")

    first = ensure_tdad_dependency_map(repo, persist=True)
    assert first["cache_hit"] is False
    second = ensure_tdad_dependency_map(repo, persist=True)
    assert second["cache_hit"] is True

    orig_stat = test_file.stat()
    test_file.write_text(updated_content, encoding="utf-8")
    os.utime(test_file, ns=(orig_stat.st_atime_ns, orig_stat.st_mtime_ns))

    restored_stat = test_file.stat()
    assert restored_stat.st_size == orig_stat.st_size
    assert restored_stat.st_mtime_ns == orig_stat.st_mtime_ns

    third = ensure_tdad_dependency_map(repo, persist=True)
    assert third["cache_hit"] is False

    beta_hits = query_tdad_tests(repo, ["src/beta.py"], repo_map_payload=None)
    assert any(h["test_file"] == "tests/test_suite.py" for h in beta_hits)
    alpha_hits = query_tdad_tests(repo, ["src/alpha.py"], repo_map_payload=None)
    assert not any(h["test_file"] == "tests/test_suite.py" for h in alpha_hits)


def test_prepare_populates_tdad_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "worker.py").write_text("def run_job():\n    return 42\n", encoding="utf-8")
    (repo / "tests" / "test_worker.py").write_text(
        "from src.worker import run_job\n\ndef test_run_job():\n    assert run_job() == 42\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["prepare", "run_job", str(repo), "--json"])
    assert res.exit_code == 0, res.output
    db_path = repo / TDAD_CACHE_REL_PATH
    assert db_path.exists()
    hits = query_tdad_tests(repo, ["src/worker.py"])
    assert any(h["test_file"] == "tests/test_worker.py" for h in hits)


def test_change_slice_does_not_fabricate_edges_for_unattributed_callers() -> None:
    bundle = extract_change_directed_slice(
        changed_files=["src/a.py", "src/b.py"],
        changed_symbols=[{"file": "src/b.py", "name": "beta"}],
        callers=[
            {"file": "src/unattributed.py", "caller": "mystery_caller"},
            {"file": "src/real_caller.py", "caller": "call_beta", "target": "beta"},
            {"file": "src/file_caller.py", "caller": "call_b_file", "target_file": "src/b.py"},
        ],
        affected_files=["src/unattributed.py", "tests/test_extra.py"],
        tdad_tests=[{"test_file": "tests/test_b.py", "matched_sources": ["src/b.py"]}],
    )
    edges = bundle["slice"]["edges"]
    # No fabricated edge to src/a.py for unattributed caller or affected test
    assert not any(
        e["src"] == "sym:src/unattributed.py::mystery_caller" and e["dst"] == "file:src/a.py"
        for e in edges
    )
    assert not any(
        e["src"] == "test:tests/test_extra.py" and e["dst"] == "file:src/a.py" for e in edges
    )
    # Real target matches are preserved
    assert {
        "src": "sym:src/real_caller.py::call_beta",
        "dst": "sym:src/b.py::beta",
        "kind": "calls",
    } in edges
    assert {
        "src": "sym:src/file_caller.py::call_b_file",
        "dst": "file:src/b.py",
        "kind": "calls",
    } in edges
    assert {"src": "test:tests/test_b.py", "dst": "file:src/b.py", "kind": "covers"} in edges


def test_compute_repo_fingerprint_refuses_unconfined_paths_before_lstat(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from tensor_grep.cli import tdad_kg_ast

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    inside = repo / "src" / "inside.py"
    inside.write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 42\n", encoding="utf-8")

    lstat_calls: list[Path] = []
    orig_lstat = Path.lstat

    def spy_lstat(self: Path, *args: Any, **kwargs: Any) -> Any:
        lstat_calls.append(self)
        return orig_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", spy_lstat)

    fp = tdad_kg_ast._compute_repo_fingerprint(
        repo,
        ["src/inside.py", "../outside.py", str(outside), "src/../../outside.py"],
    )
    assert len(fp) == 64
    resolved_outside = outside.resolve()
    assert not any(
        "outside.py" in str(p) or p.resolve() == resolved_outside for p in lstat_calls if p.exists()
    )


def test_corrupt_and_wrong_schema_tdad_sqlite_cache_rebuilds_gracefully(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from tensor_grep.cli import tdad_kg_ast

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (repo / "tests" / "test_billing.py").write_text(
        "from src.billing import charge\n\ndef test_charge():\n    assert charge(1) == 1\n",
        encoding="utf-8",
    )

    db_path = repo / TDAD_CACHE_REL_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"corrupt-not-sqlite-bytes")

    rebuilt = ensure_tdad_dependency_map(repo, persist=True)
    assert rebuilt["cache_hit"] is False
    assert rebuilt["edge_count"] > 0

    # Wrong schema_version -> treated as cache miss and rebuilt
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', '999')"
            )
    finally:
        conn.close()

    rebuilt_schema = ensure_tdad_dependency_map(repo, persist=True)
    assert rebuilt_schema["cache_hit"] is False

    # Missing required table -> query_tdad_tests recovers cleanly
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("DROP TABLE source_test_deps")
    finally:
        conn.close()

    hits = query_tdad_tests(repo, ["src/billing.py"])
    assert any(h["test_file"] == "tests/test_billing.py" for h in hits)

    # Publish cap enforced BEFORE publish_confined
    publish_calls: list[Path] = []
    orig_publish = tdad_kg_ast.publish_confined

    def spy_publish(root: Path, path: Path, data: bytes, **kwargs: Any) -> Any:
        publish_calls.append(path)
        return orig_publish(root, path, data, **kwargs)

    monkeypatch.setattr(tdad_kg_ast, "publish_confined", spy_publish)
    monkeypatch.setattr(tdad_kg_ast, "_TDAD_DB_LIMIT", 4096)
    try:
        try:
            record_tdad_edges(
                repo,
                [("src/billing.py", "charge", "tests/test_billing.py", "import", 1.0)],
            )
        except ValueError as exc:
            assert "byte limit" in str(exc)
        else:
            raise AssertionError("Expected ValueError when serialized DB exceeds _TDAD_DB_LIMIT")
    finally:
        pass
    assert not any(p == db_path for p in publish_calls)


def test_verify_review_bundle_refuses_change_slice_stripping_and_expected_digest_mismatch(
    tmp_path: Path,
) -> None:
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

    bundle = audit_manifest.create_review_bundle(
        manifest_path,
        output_path=bundle_path,
        include_change_slice=True,
    )
    orig_digest = bundle["slice_digest"]
    assert "change_slice" in bundle["checksums"]
    assert "slice_digest" in bundle["checksums"]
    assert (
        audit_manifest.verify_review_bundle(bundle_path, expected_slice_digest=orig_digest)["valid"]
        is True
    )
    assert (
        audit_manifest.verify_review_bundle(bundle_path, expected_slice_digest="f" * 64)["valid"]
        is False
    )
    for bad_digest in ("   ", "abcd", "z" * 64):
        bad_res = audit_manifest.verify_review_bundle(bundle_path, expected_slice_digest=bad_digest)
        assert bad_res["valid"] is False
        assert bad_res["slice_digest_valid"] is False
        assert bad_res["slice_digest_reason"] == "invalid_expected_slice_digest"
    non_str_res = audit_manifest.verify_review_bundle(
        bundle_path,
        expected_slice_digest=123,  # type: ignore[arg-type]
    )
    assert non_str_res["valid"] is False
    assert non_str_res["slice_digest_valid"] is False
    assert non_str_res["slice_digest_reason"] == "invalid_expected_slice_digest"

    # Strip change_slice while keeping checksums -> must fail closed
    stripped_one = json.loads(bundle_path.read_text(encoding="utf-8"))
    stripped_one.pop("change_slice")
    stripped_one["bundle_sha256"] = audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(stripped_one)
    )
    bundle_path.write_text(json.dumps(stripped_one, indent=2), encoding="utf-8")
    assert audit_manifest.verify_review_bundle(bundle_path)["valid"] is False

    # Strip change_slice, slice_digest, AND their checksums, recompute bundle_sha256 ->
    # must fail closed when expected_slice_digest is supplied
    stripped_all = json.loads(bundle_path.read_text(encoding="utf-8"))
    stripped_all.pop("slice_digest", None)
    stripped_all["checksums"].pop("change_slice", None)
    stripped_all["checksums"].pop("slice_digest", None)
    stripped_all["bundle_sha256"] = audit_manifest._sha256_hex(
        audit_manifest._canonical_review_bundle_bytes(stripped_all)
    )
    bundle_path.write_text(json.dumps(stripped_all, indent=2), encoding="utf-8")
    assert (
        audit_manifest.verify_review_bundle(bundle_path, expected_slice_digest=orig_digest)["valid"]
        is False
    )

    # API + CLI: --require-change-slice and --expect-slice-digest fail closed (exit 1)
    # when change_slice + slice_digest + checksums are stripped and bundle_sha256 is recomputed
    api_req = audit_manifest.verify_review_bundle(bundle_path, require_change_slice=True)
    assert api_req["valid"] is False
    assert api_req["slice_digest_valid"] is False
    assert api_req.get("slice_digest_reason") == "missing_change_slice"

    api_req_json = json.loads(
        audit_manifest.verify_review_bundle_json(bundle_path, require_change_slice=True)
    )
    assert api_req_json["valid"] is False
    assert api_req_json["slice_digest_valid"] is False
    assert api_req_json.get("slice_digest_reason") == "missing_change_slice"

    cli_req = runner.invoke(
        app,
        ["review-bundle", "verify", str(bundle_path), "--require-change-slice", "--json"],
    )
    assert cli_req.exit_code == 1
    cli_req_payload = json.loads(cli_req.stdout)
    assert cli_req_payload["valid"] is False
    assert cli_req_payload["slice_digest_valid"] is False
    assert cli_req_payload.get("slice_digest_reason") == "missing_change_slice"

    cli_exp = runner.invoke(
        app,
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--expect-slice-digest",
            orig_digest,
            "--json",
        ],
    )
    assert cli_exp.exit_code == 1
    cli_exp_payload = json.loads(cli_exp.stdout)
    assert cli_exp_payload["valid"] is False
    assert cli_exp_payload["slice_digest_valid"] is False

    # Empty/whitespace expected_slice_digest must fail closed (never normalize "" -> None)
    api_empty = audit_manifest.verify_review_bundle(bundle_path, expected_slice_digest="")
    assert api_empty["valid"] is False
    assert api_empty["slice_digest_valid"] is False
    assert api_empty.get("slice_digest_reason") == "invalid_expected_slice_digest"

    cli_empty = runner.invoke(
        app,
        [
            "review-bundle",
            "verify",
            str(bundle_path),
            "--expect-slice-digest",
            "",
            "--json",
        ],
    )
    assert cli_empty.exit_code == 1
    cli_empty_payload = json.loads(cli_empty.stdout)
    assert cli_empty_payload["valid"] is False
    assert cli_empty_payload["slice_digest_valid"] is False
    assert cli_empty_payload.get("slice_digest_reason") == "invalid_expected_slice_digest"


def test_tdad_sqlite_cache_missing_required_columns_rebuilds_gracefully(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "billing.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (repo / "tests" / "test_billing.py").write_text(
        "from src.billing import charge\n\ndef test_charge():\n    assert charge(1) == 1\n",
        encoding="utf-8",
    )

    db_path = repo / TDAD_CACHE_REL_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    mem = sqlite3.connect(":memory:")
    try:
        with mem:
            mem.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            mem.execute(
                "CREATE TABLE source_test_deps ("
                "source_symbol TEXT NOT NULL, test_file TEXT NOT NULL, "
                "edge_kind TEXT NOT NULL, weight REAL NOT NULL)"
            )
            mem.execute("INSERT INTO metadata(key, value) VALUES ('schema_version', '1')")
        db_path.write_bytes(mem.serialize())
    finally:
        mem.close()

    rebuilt = ensure_tdad_dependency_map(repo, persist=True)
    assert rebuilt["cache_hit"] is False
    assert rebuilt["edge_count"] > 0
    hits = query_tdad_tests(repo, ["src/billing.py"])
    assert any(h["test_file"] == "tests/test_billing.py" for h in hits)
