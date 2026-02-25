from unittest.mock import patch, MagicMock

class TestCybertBackend:
    @patch("cudf_grep.backends.cybert_backend.AutoTokenizer")
    def test_should_tokenize_log_lines(self, mock_tokenizer):
        mock_instance = MagicMock()
        mock_instance.return_value = {"input_ids": [[1, 2, 3]]}
        mock_tokenizer.from_pretrained.return_value = mock_instance
        
        from cudf_grep.backends.cybert_backend import tokenize
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
        
        from cudf_grep.backends.cybert_backend import CybertBackend
        backend = CybertBackend()
        results = backend.classify(["test line"])
        
        assert len(results) == 1
        assert results[0]["label"] == "warn"  # index 1 has highest prob 0.8
        assert results[0]["confidence"] == 0.8
