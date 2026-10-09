"""Concrete negative controls for independently reproduced implementation defects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli.checkout_environment import diagnose_checkout_environment
from tensor_grep.cli.dogfood_unified import run_unified_dogfood
from tensor_grep.core.dependency_grounding import dependency_grounding


def test_deep_metadata_reports_partial_without_crashing(tmp_path: Path) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_text('{"dependencies":{},"unused":' + "[" * 20000 + "0" + "]" * 20000 + "}")
    report = dependency_grounding(tmp_path)
    assert report is not None
    assert report["status"] == "partial"
    assert report["diagnostics"][0]["reason"] == "RecursionError"
    manifest.write_text('{"dependencies":{"valid":"^1.0.0"}}')
    control = dependency_grounding(tmp_path)
    assert control["status"] == "complete"
    assert control["dependencies"][0]["name"] == "valid"


def test_deep_editable_metadata_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="tensor-grep"\nversion="1.0.0"')
    metadata = tmp_path / ".venv/Lib/site-packages/tensor_grep-1.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: tensor-grep\nVersion: 1.0.0\n")
    direct = metadata / "direct_url.json"
    direct.write_text("[" * 2000 + "0" + "]" * 2000)
    assert diagnose_checkout_environment(tmp_path)["status"] == "ambiguous_environment"
    direct.write_text(json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}))
    assert diagnose_checkout_environment(tmp_path)["status"] == "current"


def test_packaged_runner_cannot_be_shadowed_by_selected_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "tensor_grep/cli"
    fake.mkdir(parents=True)
    (fake.parent / "__init__.py").write_text("")
    (fake / "__init__.py").write_text("")
    fake_report = json.dumps({
        "results": [{"name": "attacker", "status": "passed"}],
        "summary": {"passed": 1},
        "artifact_identity": {"version": "forged"},
    })
    (fake / "dogfood_features.py").write_text(
        "from pathlib import Path\n"
        "Path('shadow-executed').write_text('executed')\n"
        f"print({fake_report!r})\n"
    )
    monkeypatch.setenv("TG_BIN", str(tmp_path / "missing-tg"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    code, report = run_unified_dogfood(features=True, root=tmp_path, timeout_s=30)
    assert code == 1  # the trusted runner reports the deliberately missing executable
    assert "attacker" not in report["selected_checks"]
    assert not (tmp_path / "shadow-executed").exists()


def test_cross_encoder_context_is_aligned_and_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    from tensor_grep import rust_core
    from tensor_grep.core import cross_encoder_assets
    from tensor_grep.core.cross_encoder import apply_cross_encoder
    from tensor_grep.core.result import MatchLine, SearchResult

    monkeypatch.setattr(
        cross_encoder_assets, "verified_assets", lambda root: (Path("m"), Path("t"), Path("r"))
    )
    seen = []

    def score(*args: object) -> list[float]:
        seen.extend(args[4])
        return [0.0, 0.0, 1.0]

    monkeypatch.setattr(rust_core, "cross_encoder_scores", score, raising=False)
    matches = [MatchLine(1, "lineA", "A"), MatchLine(2, "lineB", "B"), MatchLine(3, "lineC", "C")]
    result = SearchResult(
        matches=matches.copy(), rerank_texts=["full context A", "full context B", "full context C"]
    )
    apply_cross_encoder(result, "query", "cross-encoder")
    assert seen == ["full context A", "full context B", "full context C"]
    assert result.matches == [matches[0], matches[2], matches[1]]
    assert result.rerank_texts == ["full context A", "full context C", "full context B"]


def test_capsule_source_refusal_is_not_fabricated_as_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from tensor_grep.cli import repo_map
    from tensor_grep.cli.agent_capsule_builder import build_agent_capsule_from_map

    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "oversized.py").write_text("#" * 300)
    monkeypatch.setenv("TENSOR_GREP_MAX_PARSE_BYTES", "128")
    rm = repo_map.build_repo_map(tmp_path)
    assert rm["symbol_cache_coverage"]["omitted_files"] == 1
    capsule = build_agent_capsule_from_map(
        rm, "alpha", deadline_monotonic=time.monotonic() + 30, grounding="off"
    )
    assert capsule["partial"] is True
    assert capsule["partial_reason"] == "source_coverage"
    assert capsule["symbol_cache_coverage"]["omitted_files"] == 1
    assert capsule["deadline_limit"]["deadline_exceeded"] is False
