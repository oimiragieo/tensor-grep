from tensor_grep.core.result import MatchLine, SearchResult, strip_line_terminator


class TestStripLineTerminator:
    """task #262: every backend's own trailing-terminator strip used to be
    `.rstrip("\n\r")` / `.rstrip("\r\n")`, which eats ANY trailing run of `\r`/`\n` in any
    order -- silently dropping a genuine trailing `\r` from a CRLF-terminated source line
    (Rust's line-splitter and rg's own `--json` "lines" field both include that `\r`; real
    `rg`'s plain-text output preserves it too). `strip_line_terminator` must remove ONLY the
    single trailing `\n` every engine here is known to append.
    """

    def test_strips_a_bare_trailing_newline(self):
        assert strip_line_terminator("hello world\n") == "hello world"

    def test_preserves_a_genuine_trailing_cr_from_a_crlf_line(self):
        # This is the whole point of the fix: a real \r immediately before the \n must
        # survive, not be eaten alongside it.
        assert strip_line_terminator("hello world\r\n") == "hello world\r"

    def test_does_not_touch_text_with_no_trailing_newline(self):
        assert strip_line_terminator("hello world") == "hello world"

    def test_does_not_touch_a_bare_trailing_cr_with_no_following_newline(self):
        # Nothing here manufactures a strip that was never asked for -- a trailing \r with
        # no \n after it is untouched (there is no known engine output shape that produces
        # this, but the function must not guess).
        assert strip_line_terminator("hello world\r") == "hello world\r"

    def test_only_strips_one_trailing_newline_not_a_run(self):
        # A blank final line (two trailing \n) must lose only the very last terminator --
        # the same "at most one" semantics real rg/Rust line-splitting implies.
        assert strip_line_terminator("hello\n\n") == "hello\n"

    def test_empty_string_is_a_noop(self):
        assert strip_line_terminator("") == ""


class TestSearchResult:
    def test_should_create_result_with_matches(self):
        match = MatchLine(line_number=2, text="ERROR Connection timeout", file="test.log")
        result = SearchResult(matches=[match], total_files=1, total_matches=1)
        assert result.total_matches == 1
        assert result.matches[0].line_number == 2

    def test_should_report_empty_when_no_matches(self):
        result = SearchResult(matches=[], total_files=1, total_matches=0)
        assert result.is_empty is True

    def test_should_store_routing_metadata_fields(self):
        result = SearchResult(
            matches=[],
            matched_file_paths=["a.log", "b.log"],
            match_counts_by_file={"a.log": 2, "b.log": 1},
            total_files=0,
            total_matches=0,
            routing_backend="CuDFBackend",
            routing_reason="gpu_explicit_ids_cudf",
            routing_gpu_device_ids=[3, 7],
            routing_gpu_chunk_plan_mb=[(3, 256), (7, 512)],
            routing_distributed=True,
            routing_worker_count=2,
        )
        assert result.routing_backend == "CuDFBackend"
        assert result.routing_reason == "gpu_explicit_ids_cudf"
        assert result.matched_file_paths == ["a.log", "b.log"]
        assert result.match_counts_by_file == {"a.log": 2, "b.log": 1}
        assert result.routing_gpu_device_ids == [3, 7]
        assert result.routing_gpu_chunk_plan_mb == [(3, 256), (7, 512)]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2
