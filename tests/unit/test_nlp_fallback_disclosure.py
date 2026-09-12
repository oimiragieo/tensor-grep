"""An NLP->standard-backend swap must be disclosed on the result envelope.

`Pipeline` sets a local `selected_backend_reason` on every routing decision, but only
`fallback_reason` is propagated onto the result envelope -- `cli/main.py` and
`cli/mcp_server.py` both read `getattr(pipeline, "fallback_reason", None)`. The
`nlp_backend_unavailable_fallback` arm set the former and not the latter, so a query whose
ENGINE had been swapped came back looking like a clean run of the engine the caller asked
for. The sibling torch->CPU swaps already stamped it; this arm did not.
"""

from __future__ import annotations

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline


@pytest.fixture
def _nlp_autoroute_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # The keyword auto-route is opt-in (default OFF); opt in so this arm is reachable at all.
    monkeypatch.setenv("TG_NLP_KEYWORD_AUTOROUTE", "1")


def test_nlp_backend_unavailable_swap_stamps_a_durable_fallback_reason(
    _nlp_autoroute_on: None,
) -> None:
    pipeline = Pipeline(config=SearchConfig(query_pattern="classify things"))

    if pipeline.selected_backend_reason != "nlp_backend_unavailable_fallback":
        pytest.skip(
            "cybert is installed in this environment, so the unavailable-fallback arm was "
            f"not taken (reason={pipeline.selected_backend_reason!r}); this test targets the "
            "swap path specifically"
        )

    assert pipeline.fallback_reason is not None, (
        "the engine was swapped away from the NLP backend, so the envelope owes a durable "
        "fallback_reason -- only fallback_reason reaches the JSON/MCP envelope, so leaving "
        "it None reports a silent swap as a clean run"
    )
    assert "cybert" in pipeline.fallback_reason


def test_a_plain_literal_query_does_not_claim_a_fallback() -> None:
    """MUTATION CONTROL.

    Stamping `fallback_reason` unconditionally would satisfy the test above while emitting a
    false "we fell back" signal on every ordinary search -- exactly what `result.py`'s own
    comment warns against ("conflating them would emit a false 'we fell back' signal to
    doctor/JSON"). A normal query must carry no fallback claim.
    """
    pipeline = Pipeline(config=SearchConfig(query_pattern="ERROR"))

    assert pipeline.fallback_reason is None, (
        "an ordinary literal search swapped no engine and must not claim a fallback; "
        f"got {pipeline.fallback_reason!r}"
    )
