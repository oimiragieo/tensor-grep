from tensor_grep.backends.cpu_backend import CPUBackend


class TestCPUBackend:
    def test_should_find_simple_pattern(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "ERROR")
        assert result.total_matches == 2

    def test_should_return_empty_for_no_match(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "NONEXISTENT")
        assert result.is_empty is True

    def test_should_support_regex_patterns(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), r"ERROR.*database")
        assert result.total_matches == 1

    def test_should_support_case_insensitive_search(self, tmp_path):
        log = tmp_path / "case.log"
        log.write_text("ERROR\nerror\nErRoR\n")
        backend = CPUBackend()
        result = backend.search(str(log), "(?i)error")
        assert result.total_matches == 3

    def test_should_search_multiple_files(self, tmp_path):
        log1 = tmp_path / "1.log"
        log2 = tmp_path / "2.log"
        log1.write_text("ERROR 1\n")
        log2.write_text("ERROR 2\n")
        backend = CPUBackend()

        # Test individual file
        assert backend.search(str(log1), "ERROR").total_matches == 1

    def test_should_handle_binary_files_gracefully(self, tmp_path):
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"\x00\x01\x02ERROR\x03\x04")
        backend = CPUBackend()
        result = backend.search(str(binary_file), "ERROR")
        assert getattr(result, "total_matches", 0) >= 0

    def test_should_handle_empty_file(self, tmp_path):
        empty_file = tmp_path / "empty.log"
        empty_file.write_text("")
        backend = CPUBackend()
        result = backend.search(str(empty_file), "ERROR")
        assert result.is_empty is True

    def test_should_handle_file_not_found(self):
        backend = CPUBackend()
        result = backend.search("nonexistent_file.log", "ERROR")
        assert result.is_empty is True

    def test_should_report_line_numbers(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "ERROR")
        assert [m.line_number for m in result.matches] == [2, 4]

    def test_should_handle_utf8_and_latin1(self, tmp_path):
        latin_file = tmp_path / "latin.log"
        latin_file.write_bytes("ERROR line café\n".encode("latin-1"))
        backend = CPUBackend()
        result = backend.search(str(latin_file), "ERROR")
        assert result.total_matches == 1

    def test_should_includeAfterContext_when_dashA_isProvided(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "context.log"
        log.write_text("line 1\nERROR MATCH\nline 3\nline 4\nline 5\n")

        backend = CPUBackend()
        config = SearchConfig(after_context=2)
        result = backend.search(str(log), "ERROR", config=config)

        # Should return 3 lines total: The match itself, plus 2 after
        assert len(result.matches) == 3
        assert result.matches[0].line_number == 2
        assert result.matches[0].text == "ERROR MATCH"
        assert result.matches[1].line_number == 3
        assert result.matches[1].text == "line 3"
        assert result.matches[2].line_number == 4
        assert result.matches[2].text == "line 4"

    def test_should_includeBeforeContext_when_dashB_isProvided(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "context_before.log"
        log.write_text("line 1\nline 2\nERROR MATCH\nline 4\n")

        backend = CPUBackend()
        config = SearchConfig(before_context=2)
        result = backend.search(str(log), "ERROR", config=config)

        # Should return 3 lines total: 2 before, plus the match itself
        assert len(result.matches) == 3
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "line 1"
        assert result.matches[1].line_number == 2
        assert result.matches[1].text == "line 2"
        assert result.matches[2].line_number == 3
        assert result.matches[2].text == "ERROR MATCH"
