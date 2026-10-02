"""Unit tests for P0: Fuse Semantic Dense Retrieval into tg prepare / tg agent.

Validates that when primary target confidence is below 0.60, semantic dense retrieval
and reciprocal rank fusion are invoked to promote semantically relevant alternative targets
over poorly matching lexical targets. Also verifies fail-closed / fail-safe degradation
when the dense extra is not available or raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tensor_grep.cli.agent_capsule_targets import _maybe_fuse_semantic_dense_target


def test_semantic_dense_fusion_bypassed_when_confidence_high() -> None:
    """When primary target confidence is >= 0.60, dense fusion is bypassed."""
    target = {"file": "core/tax.py", "symbol": "calculate_tax", "confidence": 0.85}
    alternatives = [{"file": "core/other.py", "symbol": "other_func", "confidence": 0.70}]

    new_target, new_alternatives = _maybe_fuse_semantic_dense_target(
        "sales surcharge calculation", target, alternatives
    )
    assert new_target == target
    assert new_alternatives == alternatives


def test_semantic_dense_fusion_promotes_semantic_alternative_on_low_confidence() -> None:
    """When primary target confidence is < 0.60 and dense model ranks an alternative first, swap."""
    target = {"file": "core/tax.py", "symbol": "calc_vague", "confidence": 0.35}
    alternatives = [
        {"file": "core/billing.py", "symbol": "compute_surcharge", "confidence": 0.30},
        {"file": "core/dummy.py", "symbol": "dummy_func", "confidence": 0.20},
    ]

    # Mock dense_available returning True and mock dense model/chunks
    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch("tensor_grep.core.retrieval_dense.load_dense_model"),
        patch("tensor_grep.core.retrieval_dense.DenseIndex") as mock_dense_index_cls,
    ):
        mock_dense_index = MagicMock()
        # Suppose query returns compute_surcharge (index 1) as top match
        mock_dense_index.query.return_value = [(1, 0.92), (0, 0.40), (2, 0.10)]
        mock_dense_index_cls.return_value = mock_dense_index

        new_target, new_alternatives = _maybe_fuse_semantic_dense_target(
            "sales surcharge calculation", target, alternatives
        )

        assert new_target["symbol"] == "compute_surcharge"
        assert new_target.get("semantic_fused") is True
        assert new_target.get("confidence", 0) >= 0.70
        assert new_alternatives[0]["symbol"] == "calc_vague"


def test_semantic_dense_fusion_graceful_fallback_when_dense_unavailable() -> None:
    """When dense extra is not available, fail-safe degradation returns original target/alternatives."""
    target = {"file": "core/tax.py", "symbol": "calc_vague", "confidence": 0.40}
    alternatives = [{"file": "core/billing.py", "symbol": "compute_surcharge", "confidence": 0.30}]

    with patch(
        "tensor_grep.core.retrieval_dense.dense_available",
        return_value=(False, "model2vec not installed"),
    ):
        new_target, new_alternatives = _maybe_fuse_semantic_dense_target(
            "sales surcharge calculation", target, alternatives
        )
        assert new_target == target
        assert new_alternatives == alternatives


# --- Disclosure of a dense-leg FAULT (the extra is installed but the leg failed) ---------------
# The capsule deliberately never crashes on an optional dense-leg fault. But a silent fallback is
# indistinguishable from "fusion ran and kept the lexical order" (the normal case, which also sets
# no `semantic_fused`), and `tg find` discloses its BM25 fallback. So a FAULT is disclosed with an
# additive marker; an EXPECTED absence (extra not installed) and a normal non-promotion are not.


def _low_confidence_inputs() -> tuple[dict, list[dict]]:
    target = {"file": "core/tax.py", "symbol": "calc_vague", "confidence": 0.35}
    alternatives = [{"file": "core/billing.py", "symbol": "compute_surcharge", "confidence": 0.30}]
    return target, alternatives


def test_a_dense_model_that_raises_backend_execution_error_is_disclosed() -> None:
    from tensor_grep.backends.base import BackendExecutionError

    target, alternatives = _low_confidence_inputs()
    original_target = dict(target)

    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch(
            "tensor_grep.core.retrieval_dense.load_dense_model",
            side_effect=BackendExecutionError("dense model is corrupt"),
        ),
    ):
        new_target, new_alternatives = _maybe_fuse_semantic_dense_target(
            "sales surcharge calculation", target, alternatives
        )

    # fail-safe is preserved: same ranking, nothing raised ...
    assert new_target["symbol"] == "calc_vague"
    assert new_alternatives == alternatives
    # ... but the fault is now visible, and names the class and the reason
    marker = new_target.get("semantic_fusion_unavailable")
    assert isinstance(marker, str)
    assert marker.startswith("BackendExecutionError")
    assert "corrupt" in marker
    # the caller's own dict is not mutated
    assert target == original_target


def test_a_dense_model_that_cannot_be_loaded_is_disclosed() -> None:
    from tensor_grep.core.retrieval_dense import DenseUnavailableError

    target, alternatives = _low_confidence_inputs()
    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch(
            "tensor_grep.core.retrieval_dense.load_dense_model",
            side_effect=DenseUnavailableError("model files missing"),
        ),
    ):
        new_target, _ = _maybe_fuse_semantic_dense_target("q", target, alternatives)

    assert str(new_target.get("semantic_fusion_unavailable", "")).startswith(
        "DenseUnavailableError"
    )


def test_a_runtime_error_while_querying_the_dense_index_is_disclosed() -> None:
    target, alternatives = _low_confidence_inputs()
    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch("tensor_grep.core.retrieval_dense.load_dense_model"),
        patch("tensor_grep.core.retrieval_dense.DenseIndex") as mock_dense_index_cls,
    ):
        mock_dense_index_cls.return_value.query.side_effect = RuntimeError("onnx session failed")
        new_target, _ = _maybe_fuse_semantic_dense_target("q", target, alternatives)

    assert "onnx session failed" in str(new_target.get("semantic_fusion_unavailable", ""))


def test_a_very_long_fault_message_is_bounded() -> None:
    target, alternatives = _low_confidence_inputs()
    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch(
            "tensor_grep.core.retrieval_dense.load_dense_model",
            side_effect=RuntimeError("x" * 5000),
        ),
    ):
        new_target, _ = _maybe_fuse_semantic_dense_target("q", target, alternatives)

    assert len(str(new_target["semantic_fusion_unavailable"])) <= 260


def test_fusion_that_runs_and_keeps_the_lexical_order_is_not_marked_unavailable() -> None:
    # CONTROL: "fusion ran, lexical order kept" is the normal case and must carry no marker.
    target, alternatives = _low_confidence_inputs()
    with (
        patch("tensor_grep.core.retrieval_dense.dense_available", return_value=(True, "")),
        patch("tensor_grep.core.retrieval_dense.load_dense_model"),
        patch("tensor_grep.core.retrieval_dense.DenseIndex") as mock_dense_index_cls,
    ):
        mock_dense_index_cls.return_value.query.return_value = [(0, 0.95), (1, 0.10)]
        new_target, _ = _maybe_fuse_semantic_dense_target("q", target, alternatives)

    assert "semantic_fusion_unavailable" not in new_target
    assert "semantic_fused" not in new_target


def test_an_extra_that_is_not_installed_is_not_a_fault() -> None:
    # CONTROL: an expected absence stays silent (the target is returned untouched).
    target, alternatives = _low_confidence_inputs()
    with patch(
        "tensor_grep.core.retrieval_dense.dense_available", return_value=(False, "not installed")
    ):
        new_target, _ = _maybe_fuse_semantic_dense_target("q", target, alternatives)

    assert new_target is target
    assert "semantic_fusion_unavailable" not in new_target
