from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main, mcp_server
from tensor_grep.cli.investigation_hops import investigation_hops, rerank_capsule_snippets
from tensor_grep.core.dependency_grounding import (
    MAX_METADATA_BYTES,
    bounded_metadata,
    dependency_grounding,
)
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.registry_grounding import _url, registry_receipts


def test_local_manifest_lock_and_static_api_provenance(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="fixture"\ndependencies=["requests>=2", "private-lib"]\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="requests"\nversion="2.32.1"\n[[package]]\nname="requests"\nversion="2.31.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"dependencies":{"react":"^19"}}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(
        '{"packages":{"node_modules/react":{"version":"19.1.0"}}}', encoding="utf-8"
    )
    (tmp_path / "Cargo.toml").write_text('[dependencies]\nserde="1"\n', encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname="serde"\nversion="1.0.200"\n', encoding="utf-8"
    )
    stub = tmp_path / "node_modules" / "react" / "index.d.ts"
    stub.parent.mkdir(parents=True)
    stub.write_text("export function render(): void;", encoding="utf-8")
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding.subprocess.run",
        lambda *args, **kwargs: pytest.fail("local grounding started a process"),
    )
    evidence = dependency_grounding(tmp_path)
    assert evidence is not None
    rows = {row["name"]: row for row in evidence["dependencies"]}
    assert rows["requests"]["constraints"][0]["value"] == ">=2"
    assert rows["requests"]["resolution_status"] == "ambiguous_multiple_versions"
    assert rows["requests"]["resolved_versions"][0]["provenance"] == "lockfile"
    assert rows["react"]["api_evidence"]["symbols"] == ["render"]
    assert rows["serde"]["api_evidence"]["status"] == "unavailable"
    assert rows["requests"]["import_mapping"]["status"] == "ambiguous"
    assert evidence["api_compatibility_verified"] is False


def test_grounding_off_deadline_budgets_and_unreadable_metadata(tmp_path: Path) -> None:
    assert dependency_grounding(tmp_path, "off") is None
    with pytest.raises(ConfigurationError, match="grounding must"):
        dependency_grounding(tmp_path, "online")
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {f"dep{i}": "*" for i in range(50)}}), encoding="utf-8"
    )
    evidence = dependency_grounding(tmp_path, max_tokens=0)
    assert evidence is not None
    assert evidence["dependencies"] == []
    assert evidence["omitted_dependencies"] == 50
    expired = dependency_grounding(tmp_path, deadline_monotonic=time.monotonic() - 1)
    assert expired is not None and expired["status"] == "partial"
    assert expired["dependencies"] == []
    (tmp_path / "package.json").write_bytes(b"x" * (MAX_METADATA_BYTES + 1))
    overlarge = dependency_grounding(tmp_path)
    assert overlarge is not None and overlarge["status"] == "partial"
    assert overlarge["diagnostics"][0]["status"] == "unreadable_or_invalid"
    # Positive control: the same read primitive accepts a regular bounded metadata file.
    (tmp_path / "package.json").write_bytes(b"{}")
    assert bounded_metadata(tmp_path / "package.json", tmp_path) == b"{}"


def test_grounding_refuses_linked_metadata(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "package.json").write_text('{"dependencies":{"secret":"1"}}', encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "package.json").symlink_to(outside / "package.json")
    except OSError:
        pytest.skip("symlink privilege unavailable")
    evidence = dependency_grounding(root)
    assert evidence is not None
    assert evidence["dependencies"] == []
    assert evidence["diagnostics"][0]["reason"] == "OSError"


def test_registry_is_explicit_allowlisted_isolated_capped_and_cached(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding._cache_root", lambda: tmp_path / "receipts"
    )
    calls = []

    def fetch(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b'{"name":"dep","version":"1"}\n', b"")

    monkeypatch.setattr("tensor_grep.core.registry_grounding.subprocess.run", fetch)
    dependencies = [{"ecosystem": "npm", "name": f"dep{i}"} for i in range(7)]
    result = registry_receipts(dependencies, deadline_monotonic=time.monotonic() + 20)
    assert len(calls) == 4
    assert result["omitted_requests"] == 3
    assert all(call[0][1:3] == ["-I", "-c"] for call in calls)
    assert all(call[1]["timeout"] <= 5 for call in calls)
    assert all(call[0][-1] == str(MAX_METADATA_BYTES) for call in calls)
    assert all("fetched_at" in receipt for receipt in result["receipts"])
    assert all(receipt["api_compatibility_verified"] is False for receipt in result["receipts"])
    cached = registry_receipts(dependencies[:4])
    assert len(calls) == 4
    assert all(receipt["status"] == "cached" for receipt in cached["receipts"])
    for ecosystem, name in [
        ("npm", "../escape"),
        ("pypi", "dep?url=https://other"),
        ("unknown", "dep"),
    ]:
        with pytest.raises(ValueError, match="identifier refused"):
            _url(ecosystem, name)
    assert _url("npm", "@scope/pkg").startswith("https://registry.npmjs.org/%40scope%2Fpkg")


def test_registry_deadline_prevents_children_and_timeout_is_disclosed(monkeypatch) -> None:
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding.subprocess.run",
        lambda *args, **kwargs: pytest.fail("deadline allowed a child"),
    )
    expired = registry_receipts(
        [{"ecosystem": "pypi", "name": "requests"}], deadline_monotonic=time.monotonic() - 1
    )
    assert expired["receipts"] == []
    assert expired["omitted_requests"] == 1
    with pytest.raises(ValueError, match="identifier refused"):
        _url("npm", ".")


def test_cached_receipt_cannot_override_authority_or_compatibility(
    tmp_path: Path, monkeypatch
) -> None:
    from datetime import UTC, datetime

    root = tmp_path / "receipts"
    root.mkdir()
    monkeypatch.setattr("tensor_grep.core.registry_grounding._cache_root", lambda: root)
    url = _url("pypi", "requests")
    key = hashlib.sha256(url.encode()).hexdigest()
    cache = root / f"{key}.json"
    cache.write_text(
        json.dumps({
            "url": url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "metadata": {"name": "requests", "version": "1"},
            "name": "hostile",
            "ecosystem": "hostile",
            "api_compatibility_verified": True,
            "status": "native-api-proof",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding.subprocess.run",
        lambda *args, **kwargs: pytest.fail("valid cache made a child"),
    )
    receipt = registry_receipts([{"ecosystem": "pypi", "name": "requests"}])["receipts"][0]
    assert receipt["name"] == "requests"
    assert receipt["ecosystem"] == "pypi"
    assert receipt["api_compatibility_verified"] is False
    assert receipt["status"] == "cached"
    assert receipt["provenance"] == "caller-writable-registry-cache"


def test_registry_child_timeout_and_response_caps_are_disclosed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding._cache_root", lambda: tmp_path / "receipts"
    )

    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("tensor_grep.core.registry_grounding.subprocess.run", timed_out)
    request = [{"ecosystem": "pypi", "name": "requests"}]
    receipt = registry_receipts(request)["receipts"][0]
    assert receipt["status"] == "unavailable"
    assert receipt["reason"] == "shared_deadline"
    monkeypatch.setattr(
        "tensor_grep.core.registry_grounding.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b"x" * 8193, b""),
    )
    oversized = registry_receipts(request)["receipts"][0]
    assert oversized["status"] == "unavailable"
    assert "response refused" in oversized["reason"]


def _capsule() -> dict:
    return {
        "primary_target": {
            "file": "src/invoice.py",
            "line": 2,
            "symbol": "invoice",
            "provenance": "parser-backed",
        },
        "ask_user_before_editing": {"required": False},
        "related_call_sites": [
            {
                "file": "src/service.py",
                "line": 4,
                "symbol": "invoice",
                "ref_kind": "call",
                "provenance": "parser-backed",
            },
            {
                "file": "tests/test_invoice.py",
                "line": 6,
                "symbol": "invoice",
                "ref_kind": "call",
                "provenance": "parser-backed",
            },
            {
                "file": "tests/test_guess.py",
                "line": 1,
                "symbol": "invoice",
                "ref_kind": "call",
                "provenance": "heuristic",
            },
        ],
        "call_site_evidence": {"omitted_call_sites": 0},
    }


def test_hops_deterministic_bounded_evidence_and_ambiguity() -> None:
    capsule = _capsule()
    original = json.dumps(capsule, sort_keys=True)
    rm = {"tests": ["tests/test_invoice.py", "tests/test_unrelated.py"]}
    graph = investigation_hops(capsule, rm, max_tokens=1000)
    assert [stage["stage"] for stage in graph["stages"]] == [1, 2, 3]
    assert len(graph["edges"]) == 2
    assert all(edge["evidence"]["provenance"] == "parser-backed" for edge in graph["edges"])
    assert graph == investigation_hops(capsule, rm, max_tokens=1000)
    assert json.dumps(capsule, sort_keys=True) == original
    assert investigation_hops(capsule, rm, max_tokens=0)["status"] == "partial"
    expired = investigation_hops(capsule, rm, deadline_monotonic=time.monotonic() - 1)
    assert expired["status"] == "partial" and not expired["edges"]
    capsule["ask_user_before_editing"]["required"] = True
    ambiguous = investigation_hops(capsule, rm)
    assert ambiguous["status"] == "ambiguous" and not ambiguous["edges"]


def test_hops_associations_require_import_evidence() -> None:
    capsule = _capsule()
    capsule["related_call_sites"] = []
    graph = investigation_hops(
        capsule,
        {
            "path": ".",
            "imports": [
                {
                    "file": "tests/test_real.py",
                    "imports": ["src.invoice.invoice"],
                    "provenance": "parser-backed",
                }
            ],
        },
        test_matches=[
            {
                "path": "tests/test_real.py",
                "association": {"edge_kind": "import-graph", "confidence": "moderate"},
            },
            {"path": "tests/test_guess.py", "association": {"edge_kind": "filename"}},
        ],
    )
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["evidence"]["association"]["edge_kind"] == "import-graph"


def test_agent_reranking_preserves_primary_and_line_maps(monkeypatch) -> None:
    capsule = _capsule()
    snippets = [
        {"file": "a.py", "source": "first", "line_map": [1]},
        {"file": "b.py", "source": "second", "line_map": [20]},
    ]
    capsule["snippets"] = snippets

    def reorder(result, query, mode, **kwargs):
        result.matches.reverse()
        result.rank_fusion = {"cross_encoder": {"scoring": "Rust-ONNX-CPU"}}
        return result

    monkeypatch.setattr("tensor_grep.core.cross_encoder.apply_cross_encoder", reorder)
    rerank_capsule_snippets(capsule, "invoice", "cross-encoder")
    assert capsule["snippets"] == snippets[::-1]
    assert capsule["primary_target"]["symbol"] == "invoice"
    assert capsule["reranking"]["target_selection_changed"] is False


def test_cli_options_and_invalid_grounding_refused_before_walk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "tensor_grep.cli.repo_map._iter_repo_files",
        lambda *args, **kwargs: pytest.fail("invalid input reached scan"),
    )
    runner = CliRunner()
    refused = runner.invoke(main.app, ["find", "invoice", str(tmp_path), "--grounding", "online"])
    assert refused.exit_code == 1
    assert "grounding must" in refused.output
    for command, options in [
        ("find", ["--grounding", "--rerank"]),
        ("agent", ["--grounding", "--rerank", "--plan-hops"]),
    ]:
        help_output = runner.invoke(main.app, [command, "--help"])
        assert help_output.exit_code == 0
        assert all(option in help_output.output for option in options)


def test_mcp_options_forward_and_contract_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def build(query, path, **kwargs):
        captured.update(kwargs)
        return {"query": query, "path": path}

    monkeypatch.setattr("tensor_grep.cli.agent_capsule.build_agent_capsule", build)
    payload = json.loads(
        mcp_server.tg_context(
            "capsule", query="invoice", plan_hops=True, grounding="off", rerank="auto"
        )
    )
    assert captured["plan_hops"] is True
    assert captured["grounding"] == "off"
    assert captured["rerank"] == "auto"
    assert payload["mcp_contract_version"] == "1.16.0"
