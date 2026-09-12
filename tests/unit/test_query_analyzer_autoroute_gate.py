"""C11: the QueryAnalyzer keyword auto-route must be opt-in, never a silent hijack.

`QueryAnalyzer.analyze` substring-matched common code identifiers ("classify",
"detect", ...) in the user's LITERAL search pattern and routed the search to the
NLP backend. Worse, when cybert was unavailable AND `--gpu-device-ids` was set,
a literal pattern like `tg search detect --gpu-device-ids 0 .` hard-failed with a
GPU configuration error purely because the pattern contained the word "detect".

Contract after this fix:
  - default (env unset): every keyword-containing LITERAL pattern stays FAST;
  - `TG_NLP_KEYWORD_AUTOROUTE` (default OFF, boolean convention 1/true/yes/on)
    opts back into the speculative keyword auto-route;
  - a literal pattern must never turn an explicit GPU request into a
    configuration error.
"""

from unittest.mock import patch

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline
from tensor_grep.core.query_analyzer import QueryAnalyzer, QueryType

_NLP_KEYWORD_AUTOROUTE_ENV = "TG_NLP_KEYWORD_AUTOROUTE"
_NLP_KEYWORDS = ["classify", "detect", "extract entities", "anomaly"]


class TestQueryAnalyzerAutorouteGate:
    def test_keyword_autoroute_is_opt_in_default_off(self, monkeypatch):
        """MUTATION CONTROL (C11).

        This is the test that discriminates fixed from broken: on the pre-fix
        tree every keyword below routed to QueryType.NLP, so this test fails on
        the unfixed code and passes only once the keyword scan is gated behind
        the opt-in env var. If anyone reverts the gate, flips the default to
        ON, or loosens the truthy set so an ambient value re-enables the scan,
        this test goes red.
        """
        monkeypatch.delenv(_NLP_KEYWORD_AUTOROUTE_ENV, raising=False)
        qa = QueryAnalyzer()
        for kw in _NLP_KEYWORDS:
            assert qa.analyze(kw).query_type == QueryType.FAST, kw

    def test_literal_identifier_containing_keyword_stays_fast(self, monkeypatch):
        """`classify` and `detect` are common code identifiers: a literal pattern
        that merely CONTAINS one as a substring must stay on the literal path."""
        monkeypatch.delenv(_NLP_KEYWORD_AUTOROUTE_ENV, raising=False)
        qa = QueryAnalyzer()
        for pattern in ("classify_documents", "detect_brute_force", "def detect_all():"):
            assert qa.analyze(pattern).query_type == QueryType.FAST, pattern

    def test_opt_in_env_var_restores_keyword_autoroute(self, monkeypatch):
        """The speculative capability is kept, but only behind explicit opt-in."""
        monkeypatch.setenv(_NLP_KEYWORD_AUTOROUTE_ENV, "1")
        qa = QueryAnalyzer()
        for kw in _NLP_KEYWORDS:
            assert qa.analyze(kw).query_type == QueryType.NLP, kw

    @pytest.mark.parametrize("enabled_value", ["1", "true", "yes", "on"])
    def test_opt_in_env_var_follows_boolean_convention(self, monkeypatch, enabled_value):
        monkeypatch.setenv(_NLP_KEYWORD_AUTOROUTE_ENV, enabled_value)
        assert QueryAnalyzer().analyze("detect").query_type == QueryType.NLP

    @pytest.mark.parametrize("disabled_value", ["0", "false", "no", "off", "garbage", ""])
    def test_opt_in_env_var_non_truthy_values_stay_off(self, monkeypatch, disabled_value):
        monkeypatch.setenv(_NLP_KEYWORD_AUTOROUTE_ENV, disabled_value)
        assert QueryAnalyzer().analyze("detect").query_type == QueryType.FAST

    @patch("tensor_grep.core.pipeline.CuDFBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.backends.cybert_backend.CybertBackend")
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_literal_detect_pattern_with_gpu_ids_never_raises_nlp_config_error(
        self, mock_rust, mock_rg, mock_cybert, mock_mem, mock_cudf, monkeypatch
    ):
        """SHARPEST ARM (C11): pre-fix, `tg search detect --gpu-device-ids 0 .`
        with cybert unavailable hard-failed with ConfigurationError ("NLP
        classification backend (cybert) is unavailable") purely because the
        literal pattern contained the word "detect". A literal pattern must
        never convert an explicit GPU request into a configuration error; the
        explicit --gpu-device-ids intent must reach the explicit GPU route."""
        monkeypatch.delenv(_NLP_KEYWORD_AUTOROUTE_ENV, raising=False)
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_cybert.return_value.is_available.return_value = False  # cybert unavailable
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="detect", gpu_device_ids=[0]),
        )

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.backend == mock_cudf.return_value
