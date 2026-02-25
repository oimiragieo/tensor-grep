from unittest.mock import patch, MagicMock

class TestCybertBackend:
    @patch("tensor_grep.backends.cybert_backend.AutoTokenizer")
    def test_should_tokenize_log_lines(self, mock_tokenizer):
        mock_instance = MagicMock()
        mock_instance.return_value = {"input_ids": [[1, 2, 3]]}
        mock_tokenizer.from_pretrained.return_value = mock_instance
        
        from tensor_grep.backends.cybert_backend import tokenize
        tokens = tokenize(["test line"])
        assert "input_ids" in tokens
        
    @patch.dict("sys.modules", {"tritonclient": MagicMock(), "tritonclient.http": MagicMock()})
    def test_should_classify_with_model_output(self):
        import tritonclient.http as httpclient
        mock_client = MagicMock()
        httpclient.InferenceServerClient.return_value = mock_client
        
        mock_result = MagicMock()
        import numpy as np
        # 1 log line, 3 classes (e.g., info, warn, err)
        mock_result.as_numpy.return_value = np.array([[0.1, 0.8, 0.1]])
        mock_client.infer.return_value = mock_result
        
        from tensor_grep.backends.cybert_backend import CybertBackend
        backend = CybertBackend()
        results = backend.classify(["test line"])
        
        assert len(results) == 1
        assert results[0]["label"] == "warn"  # index 1 has highest prob 0.8
        assert results[0]["confidence"] == 0.8

    @patch.dict("sys.modules", {"tritonclient": MagicMock(), "tritonclient.http": MagicMock()})
    def test_should_filterConfidence_when_nlpThresholdSet(self):
        import tritonclient.http as httpclient
        mock_client = MagicMock()
        httpclient.InferenceServerClient.return_value = mock_client
        
        mock_result = MagicMock()
        import numpy as np
        mock_result.as_numpy.return_value = np.array([
            [0.1, 0.9, 0.0], # High confidence
            [0.4, 0.4, 0.2]  # Low confidence
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
