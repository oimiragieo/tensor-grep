import gzip

from tensor_grep.io.reader_fallback import FallbackReader


class TestFallbackReader:
    def test_should_read_entire_small_file(self, sample_log_file):
        reader = FallbackReader()
        lines = list(reader.read_lines(str(sample_log_file)))
        assert len(lines) == 5

    def test_should_read_file_in_chunks(self, sample_log_file):
        # By iterating, it effectively reads line by line
        reader = FallbackReader()
        for _i, line in enumerate(reader.read_lines(str(sample_log_file))):
            assert "2026" in line

    def test_should_handle_compressed_gzip(self, tmp_path):
        gz_file = tmp_path / "test.log.gz"
        content = b"ERROR from gzip\n"
        with gzip.open(gz_file, "wb") as f:
            f.write(content)

        reader = FallbackReader()
        lines = list(reader.read_lines(str(gz_file)))
        assert lines[0] == "ERROR from gzip\n"

    def test_should_preserve_line_boundaries_across_chunks(self, tmp_path):
        log = tmp_path / "boundaries.log"
        long_line = "A" * 10000 + "\n"
        log.write_text(long_line * 10)
        reader = FallbackReader()
        lines = list(reader.read_lines(str(log)))
        assert len(lines) == 10
        assert len(lines[0]) == 10001

    def test_should_handle_missing_file(self):
        reader = FallbackReader()
        lines = list(reader.read_lines("missing.log"))
        assert len(lines) == 0
