from unittest.mock import patch

from tensor_grep.core.pipeline import Pipeline


class TestPipeline:
    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_select_cudf_when_available(self, mock_mem):
        mock_mem.return_value.get_all_device_chunk_sizes_mb.return_value = [512]
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ == "MagicMock"

    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fallback_to_cpu_when_no_gpu(self, mock_mem):
        mock_mem.return_value.get_all_device_chunk_sizes_mb.return_value = [512]
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock_cudf:
            mock_cudf.return_value.is_available.return_value = False
            pipeline = Pipeline(force_cpu=False)
            # Depending on if torch is installed and if rust is available
            assert pipeline.backend.__class__.__name__ in ("CPUBackend", "RustCoreBackend")

    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fallback_to_cpu_when_no_vram(self, mock_mem):
        mock_mem.return_value.get_all_device_chunk_sizes_mb.return_value = []
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ in ("CPUBackend", "RustCoreBackend")

    def test_should_force_cpu_when_requested(self):
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=True)
            assert pipeline.backend.__class__.__name__ in ("CPUBackend", "RustCoreBackend")
