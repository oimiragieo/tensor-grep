from unittest.mock import patch
from tensor_grep.core.pipeline import Pipeline

class TestPipeline:
    def test_should_select_cudf_when_available(self):
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ == "MagicMock"

    def test_should_fallback_to_cpu_when_no_gpu(self):
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = False
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ == "CPUBackend"
            
    def test_should_force_cpu_when_requested(self):
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=True)
            assert pipeline.backend.__class__.__name__ == "CPUBackend"
