"""Opt-in native cross-encoder orchestration; queries never install or download assets."""

from __future__ import annotations

import math
import sys
import time
from typing import Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult


def apply_cross_encoder(
    result: SearchResult, query: str, mode: str, *, deadline_monotonic: float | None = None
) -> SearchResult:
    """Reorder only the existing first twenty candidates, retaining stable ties and membership."""
    if mode not in {"off", "auto", "cross-encoder"}:
        raise ConfigurationError("rerank must be off, auto, or cross-encoder")
    if mode == "off":
        return result
    from tensor_grep.core.cross_encoder_assets import (
        CrossEncoderUnavailable,
        default_asset_dir,
        verified_assets,
    )

    metadata: dict[str, Any] = {"requested": mode, "orchestration": "Python", "scoring": None}
    try:
        from tensor_grep import rust_core

        if not hasattr(rust_core, "cross_encoder_scores"):
            raise CrossEncoderUnavailable(
                "native extension has no cross-encoder scorer; rebuild it"
            )
        model, tokenizer, runtime = (
            verified_assets(default_asset_dir(), deadline_monotonic=deadline_monotonic)
            if deadline_monotonic is not None
            else verified_assets(default_asset_dir())
        )
    except (ImportError, CrossEncoderUnavailable) as exc:
        reason = f"cross-encoder unavailable: {exc}; run `tg install-dense --reranker`"
        if mode == "cross-encoder":
            raise ConfigurationError(reason) from exc
        result.rank_fallback_reason = "; ".join(
            part for part in (result.rank_fallback_reason, reason) if part
        )
        metadata["fallback_reason"] = reason
        result.rank_fusion = {**(result.rank_fusion or {}), "cross_encoder": metadata}
        print(f"tg: {reason}", file=sys.stderr)
        return result

    candidates = result.matches[:20]
    baseline_weight = 16.0 if result.install_state == "dense_ready" else 1.0
    texts = (
        result.rerank_texts if result.rerank_texts is not None else [m.text for m in result.matches]
    )
    if len(texts) != len(result.matches):
        raise BackendExecutionError("cross-encoder candidate evidence is misaligned")
    if candidates:
        try:
            budget = (
                min(10.0, deadline_monotonic - time.monotonic()) if deadline_monotonic else 10.0
            )
            if budget <= 0:
                raise BackendExecutionError("cross-encoder shared deadline exceeded")
            scores = list(
                rust_core.cross_encoder_scores(
                    str(model),
                    str(tokenizer),
                    str(runtime),
                    query,
                    texts[:20],
                    budget,
                )
            )
        except Exception as exc:
            raise BackendExecutionError(f"native cross-encoder inference failed: {exc}") from exc
        if len(scores) != len(candidates) or any(not math.isfinite(s) for s in scores):
            raise BackendExecutionError("native cross-encoder produced invalid scores")
        model_order = sorted(range(len(candidates)), key=lambda index: (-scores[index], index))
        model_positions = {index: position for position, index in enumerate(model_order)}
        # Dense retrieval already contains model evidence. Its stronger prior keeps
        # every nonadjacent candidate pair ordered for the twenty-candidate bound;
        # the generic cross-encoder can resolve only adjacent disagreements.
        order = sorted(
            range(len(candidates)),
            key=lambda index: (
                -(baseline_weight / (61 + index) + 1.0 / (61 + model_positions[index])),
                index,
            ),
        )
        result.matches = [candidates[index] for index in order] + result.matches[20:]
        if result.rerank_texts is not None:
            result.rerank_texts = [texts[index] for index in order] + texts[20:]
    metadata.update({
        "scoring": "Rust-ONNX-CPU" if candidates else None,
        "executed": bool(candidates),
        "candidates": len(candidates),
        "max_tokens": 256,
        "ranking_policy": "baseline-cross-encoder-rrf",
        "rrf_k": 60,
        "baseline_weight": baseline_weight,
    })
    result.rank_fusion = {**(result.rank_fusion or {}), "cross_encoder": metadata}
    return result
