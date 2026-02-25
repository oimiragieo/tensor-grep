import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.integration]

class TestPipelineE2E:
    def test_full_nlp_pipeline_with_triton(self, sample_log_file):
        from cudf_grep.backends.cybert_backend import CybertBackend
        backend = CybertBackend()
        
        # Test basic classification mock
        results = backend.classify(["2026-02-24 ERROR Connection timeout to database"])
        assert len(results) == 1
        assert "label" in results[0]

    def test_batch_inference_throughput(self):
        lines = ["INFO test"] * 100
        from cudf_grep.backends.cybert_backend import CybertBackend
        backend = CybertBackend()
        results = backend.classify(lines)
        assert len(results) == 100
