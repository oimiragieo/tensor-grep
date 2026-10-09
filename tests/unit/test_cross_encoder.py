"""Native scorer orchestration boundaries, independent of optional installed assets."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core import cross_encoder_assets as assets
from tensor_grep.core.cross_encoder import apply_cross_encoder
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import MatchLine, SearchResult


def result(count: int = 3) -> SearchResult:
    return SearchResult(matches=[MatchLine(i + 1, f"text{i}", f"file{i}") for i in range(count)])


def native(monkeypatch: pytest.MonkeyPatch, scores: list[float]) -> list[tuple[object, ...]]:
    from tensor_grep import rust_core

    calls: list[tuple[object, ...]] = []

    def score(*args: object) -> list[float]:
        calls.append(args)
        return scores

    monkeypatch.setattr(rust_core, "cross_encoder_scores", score, raising=False)
    monkeypatch.setattr(assets, "verified_assets", lambda root: (Path("m"), Path("t"), Path("r")))
    return calls


def test_off_preserves_order_and_does_not_probe_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(root: Path) -> None:
        raise AssertionError("off must not load or probe")

    monkeypatch.setattr(assets, "verified_assets", refused)
    original = result()
    before = original.matches.copy()
    assert apply_cross_encoder(original, "query", "off").matches == before
    assert original.rank_fusion is None


def test_only_first_twenty_reorder_with_stable_ties(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = native(monkeypatch, [1.0] * 19 + [2.0])
    original = result(25)
    before = original.matches.copy()
    apply_cross_encoder(original, "query", "cross-encoder")
    assert original.matches == [*before[:8], before[19], *before[8:19], *before[20:]]
    assert len(calls[0][-2]) == 20
    assert set(original.matches) == set(before)
    assert original.rank_fusion["cross_encoder"]["scoring"] == "Rust-ONNX-CPU"


def test_dense_prior_preserves_nonadjacent_order(monkeypatch: pytest.MonkeyPatch) -> None:
    native(monkeypatch, list(range(20)))
    original = result(20)
    original.install_state = "dense_ready"
    apply_cross_encoder(original, "query", "cross-encoder")
    positions = {int(row.text[4:]): index for index, row in enumerate(original.matches)}
    assert all(positions[i] < positions[j] for i in range(20) for j in range(i + 2, 20))
    assert original.rank_fusion["cross_encoder"]["baseline_weight"] == 16.0


@pytest.mark.parametrize("scores", [[1.0], [math.nan, 0.0, 1.0], [math.inf, 0.0, 1.0]])
def test_invalid_scores_fail_execution(
    monkeypatch: pytest.MonkeyPatch, scores: list[float]
) -> None:
    native(monkeypatch, scores)
    with pytest.raises(BackendExecutionError, match="invalid scores"):
        apply_cross_encoder(result(), "query", "auto")


def test_missing_assets_auto_discloses_explicit_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TG_CROSS_ENCODER_DIR", str(tmp_path / "missing"))
    original = result()
    before = original.matches.copy()
    apply_cross_encoder(original, "query", "auto")
    assert original.matches == before
    assert "cross-encoder unavailable" in original.rank_fallback_reason
    assert "install-dense --reranker" in capsys.readouterr().err
    with pytest.raises(ConfigurationError, match="cross-encoder unavailable"):
        apply_cross_encoder(result(), "query", "cross-encoder")


def test_corruption_never_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tensor_grep import rust_core

    monkeypatch.setattr(rust_core, "cross_encoder_scores", lambda *args: [], raising=False)
    (tmp_path / "model.onnx").write_bytes(b"corrupt")
    monkeypatch.setenv("TG_CROSS_ENCODER_DIR", str(tmp_path))
    with pytest.raises(BackendExecutionError, match="checksum mismatch"):
        apply_cross_encoder(result(), "query", "auto")


def test_native_inference_failure_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from tensor_grep import rust_core

    native(monkeypatch, [])

    def fail(*args: object) -> list[float]:
        raise RuntimeError("inference broke")

    monkeypatch.setattr(rust_core, "cross_encoder_scores", fail)
    with pytest.raises(BackendExecutionError, match="inference broke"):
        apply_cross_encoder(result(), "query", "auto")


def test_invalid_mode_refuses() -> None:
    with pytest.raises(ConfigurationError, match="rerank must"):
        apply_cross_encoder(result(), "query", "magic")


def test_install_default_does_not_fetch_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from tensor_grep.cli import main

    monkeypatch.setattr(
        main, "_run_install_dense", lambda: {"ok": True, "message": "ready", "steps": {}}
    )

    def fail() -> Path:
        raise AssertionError("default install must not fetch reranker")

    monkeypatch.setattr(assets, "fetch_cross_encoder_assets", fail)
    assert CliRunner().invoke(main.app, ["install-dense", "--json"]).exit_code == 0


def test_install_reranker_fetch_failure_is_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from tensor_grep.cli import main

    monkeypatch.setattr(
        main, "_run_install_dense", lambda: {"ok": True, "message": "ready", "steps": {}}
    )

    def fail() -> Path:
        raise BackendExecutionError("checksum mismatch")

    monkeypatch.setattr(assets, "fetch_cross_encoder_assets", fail)
    response = CliRunner().invoke(main.app, ["install-dense", "--reranker", "--json"])
    assert response.exit_code == 1
    assert "checksum mismatch" in response.output
