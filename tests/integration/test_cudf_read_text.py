import pytest

pytestmark = [pytest.mark.gpu, pytest.mark.integration]


class TestCuDFIntegration:
    def test_cudf_read_text_returns_series(self, sample_log_file):
        cudf = pytest.importorskip("cudf")

        series = cudf.read_text(str(sample_log_file), delimiter="\n")
        assert len(series) == 5

    def test_cudf_str_contains_finds_pattern(self, sample_log_file):
        cudf = pytest.importorskip("cudf")

        series = cudf.read_text(str(sample_log_file), delimiter="\n")
        mask = series.str.contains("ERROR")
        assert mask.sum() == 2

    def test_cudf_byte_range_reading(self, sample_log_file):
        import os

        cudf = pytest.importorskip("cudf")

        size = os.path.getsize(str(sample_log_file))
        s1 = cudf.read_text(str(sample_log_file), delimiter="\n", byte_range=(0, size))
        assert len(s1) >= 1
