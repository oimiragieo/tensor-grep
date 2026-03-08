from tensor_grep.core.result import MatchLine, SearchResult


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
