from unittest.mock import MagicMock, patch

import numpy as np
import pytest


class TestCybertBackend:
    @patch.dict(
        "sys.modules",
        {
            "numpy": MagicMock(),
            "transformers": MagicMock(),
            "tritonclient": MagicMock(),
            "tritonclient.http": MagicMock(),
        },
    )
    def test_should_report_unavailable_when_triton_model_is_not_ready(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        mock_client.is_server_live.return_value = True
        mock_client.is_server_ready.return_value = True
        mock_client.is_model_ready.return_value = False
        httpclient.InferenceServerClient.return_value = mock_client

        from tensor_grep.backends.cybert_backend import CybertBackend

        assert CybertBackend().is_available() is False

    @patch.dict(
        "sys.modules",
        {
            "numpy": MagicMock(),
            "transformers": MagicMock(),
            "tritonclient": MagicMock(),
            "tritonclient.http": MagicMock(),
        },
    )
    def test_should_report_available_when_triton_model_is_ready(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        mock_client.is_server_live.return_value = True
        mock_client.is_server_ready.return_value = True
        mock_client.is_model_ready.return_value = True
        httpclient.InferenceServerClient.return_value = mock_client

        from tensor_grep.backends.cybert_backend import CybertBackend

        assert CybertBackend().is_available() is True

        client_kwargs = httpclient.InferenceServerClient.call_args.kwargs
        assert client_kwargs["url"] == "localhost:8000"
        assert client_kwargs.get("network_timeout") == 5.0
        assert client_kwargs.get("connection_timeout") == 5.0

    @patch.dict(
        "sys.modules",
        {
            "numpy": MagicMock(),
            "transformers": MagicMock(),
            "tritonclient": MagicMock(),
            "tritonclient.http": MagicMock(),
        },
    )
    def test_should_use_configured_triton_timeout_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("TENSOR_GREP_TRITON_TIMEOUT_SECONDS", "12.5")

        import tritonclient.http as httpclient

        from tensor_grep.backends.cybert_backend import _create_triton_http_client

        httpclient.InferenceServerClient.return_value = MagicMock()
        _create_triton_http_client("localhost:8000")

        client_kwargs = httpclient.InferenceServerClient.call_args.kwargs
        assert client_kwargs["url"] == "localhost:8000"
        assert client_kwargs.get("network_timeout") == 12.5
        assert client_kwargs.get("connection_timeout") == 12.5

    @patch("tensor_grep.backends.cybert_backend._has_cybert_runtime_dependencies", return_value=False)
    def test_should_report_unavailable_when_runtime_dependencies_are_missing(self, _mock_has_deps):
        from tensor_grep.backends.cybert_backend import CybertBackend

        assert CybertBackend().is_available() is False

    @patch.dict(
        "sys.modules",
        {
            "numpy": MagicMock(),
            "transformers": MagicMock(),
            "tritonclient": MagicMock(),
            "tritonclient.http": MagicMock(),
        },
    )
    def test_should_report_unavailable_when_triton_client_creation_fails(self):
        import tritonclient.http as httpclient

        httpclient.InferenceServerClient.side_effect = RuntimeError("connection refused")

        from tensor_grep.backends.cybert_backend import CybertBackend

        assert CybertBackend().is_available() is False

    @patch.dict(
        "sys.modules",
        {
            "numpy": MagicMock(),
            "transformers": MagicMock(),
            "tritonclient": MagicMock(),
            "tritonclient.http": MagicMock(),
        },
    )
    def test_should_report_unavailable_when_triton_server_is_not_live(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        mock_client.is_server_live.return_value = False
        httpclient.InferenceServerClient.return_value = mock_client

        from tensor_grep.backends.cybert_backend import CybertBackend

        assert CybertBackend().is_available() is False

    def test_should_inherit_compute_backend_protocol(self):
        from tensor_grep.backends.base import ComputeBackend
        from tensor_grep.backends.cybert_backend import CybertBackend

        assert ComputeBackend in CybertBackend.__mro__

    @patch.dict("sys.modules", {"transformers": MagicMock()})
    def test_should_tokenize_log_lines(self):
        import transformers

        mock_tokenizer = MagicMock()
        mock_instance = MagicMock()
        mock_instance.return_value = {"input_ids": [[1, 2, 3]]}
        mock_tokenizer.from_pretrained.return_value = mock_instance
        transformers.AutoTokenizer = mock_tokenizer

        from tensor_grep.backends.cybert_backend import tokenize

        tokens = tokenize(["test line"])
        assert "input_ids" in tokens

    @patch.dict("sys.modules", {"tritonclient": MagicMock(), "tritonclient.http": MagicMock()})
    def test_should_classify_with_model_output(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        httpclient.InferenceServerClient.return_value = mock_client

        mock_result = MagicMock()

        # 1 log line, 3 classes (e.g., info, warn, err)
        mock_result.as_numpy.return_value = np.array([[0.1, 0.8, 0.1]])
        mock_client.infer.return_value = mock_result

        from tensor_grep.backends.cybert_backend import CybertBackend

        backend = CybertBackend()
        results = backend.classify(["test line"])

        assert len(results) == 1
        assert results[0]["label"] == "warn"  # index 1 has highest prob 0.8
        assert results[0]["confidence"] == 0.8

        client_kwargs = httpclient.InferenceServerClient.call_args.kwargs
        assert client_kwargs["url"] == "localhost:8000"
        assert client_kwargs.get("network_timeout") == 5.0
        assert client_kwargs.get("connection_timeout") == 5.0

    @patch.dict("sys.modules", {"tritonclient": MagicMock(), "tritonclient.http": MagicMock()})
    def test_should_filterConfidence_when_nlpThresholdSet(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        httpclient.InferenceServerClient.return_value = mock_client

        mock_result = MagicMock()

        mock_result.as_numpy.return_value = np.array([
            [0.1, 0.9, 0.0],  # High confidence
            [0.4, 0.4, 0.2],  # Low confidence
        ])
        mock_client.infer.return_value = mock_result

        from tensor_grep.backends.cybert_backend import CybertBackend
        from tensor_grep.core.config import SearchConfig

        backend = CybertBackend()

        # Test default (no threshold filters)
        results = backend.classify(["good line", "vague line"])
        assert len(results) == 2

        # Test with high confidence threshold
        # We'll map something like `max_count` or a new flag to represent threshold
        # But wait, there is no confidence threshold flag yet in SearchConfig
        # I will just pass an imaginary attribute `nlp_threshold` which I will add to config
        config = SearchConfig()
        config.nlp_threshold = 0.5

        results_filtered = backend.classify(["good line", "vague line"], config=config)
        assert len(results_filtered) == 1
        assert results_filtered[0]["confidence"] >= 0.5

    @patch.dict("sys.modules", {"tritonclient": MagicMock(), "tritonclient.http": MagicMock()})
    def test_should_raise_when_inference_fails(self):
        import tritonclient.http as httpclient

        mock_client = MagicMock()
        mock_client.infer.side_effect = RuntimeError("server unavailable")
        httpclient.InferenceServerClient.return_value = mock_client

        from tensor_grep.backends.cybert_backend import CybertBackend

        backend = CybertBackend()
        with pytest.raises(RuntimeError, match="CyBERT inference failed"):
            backend.classify(["test line"])

    def test_should_use_heuristic_fallback_when_triton_missing(self):
        from tensor_grep.backends.cybert_backend import CybertBackend

        backend = CybertBackend()
        lines = [
            "2026-03-01 [INFO] startup completed",
            "2026-03-01 [WARNING] memory usage is high",
            "2026-03-01 [ERROR] database connection timeout",
            "fatal exception: cannot allocate memory",
        ]

        # Call heuristic classifier directly to keep this test deterministic
        # even in environments where tritonclient is installed but no Triton server is running.
        results = backend._heuristic_classify(lines)
        labels = [r["label"] for r in results]

        assert labels.count("error") >= 2
        assert "warn" in labels

    def test_should_return_search_result_with_nlp_routing_metadata(self, tmp_path):
        from tensor_grep.backends.cybert_backend import CybertBackend
        from tensor_grep.core.config import SearchConfig

        log_path = tmp_path / "nlp.log"
        log_path.write_text("warning: latency is high\ninfo: startup ok\n", encoding="utf-8")

        backend = CybertBackend()
        with patch.object(
            backend,
            "classify",
            return_value=[
                {"label": "warn", "confidence": 0.85},
                {"label": "info", "confidence": 0.20},
            ],
        ):
            result = backend.search(
                str(log_path),
                pattern="classify warnings",
                config=SearchConfig(nlp_threshold=0.5),
            )

        assert result.total_matches == 1
        assert result.total_files == 1
        assert result.matched_file_paths == [str(log_path)]
        assert result.match_counts_by_file == {str(log_path): 1}
        assert result.routing_backend == "CybertBackend"
        assert result.routing_reason == "nlp_cybert"
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "[warn 0.850] warning: latency is high"
