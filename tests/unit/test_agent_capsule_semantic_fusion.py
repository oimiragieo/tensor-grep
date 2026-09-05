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
