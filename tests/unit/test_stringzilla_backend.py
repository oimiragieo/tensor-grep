import pytest

from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture
def backend():
    return StringZillaBackend()


def test_stringzilla_availability(backend):
    # Ensure it's installed via pip
    assert backend.is_available() is True


def test_stringzilla_exact_match(backend, tmp_path):
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\nDEBUG trace\nERROR timeout\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 2
    assert len(result.matches) == 2
    assert result.matches[0].line_number == 2
    assert result.matches[1].line_number == 4
    assert result.routing_backend == "StringZillaBackend"
    assert result.routing_reason in {
        "stringzilla_fixed_strings",
        "stringzilla_fixed_strings_index",
        "stringzilla_fixed_strings_index_cache",
    }
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_stringzilla_no_matches(backend, tmp_path):
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nDEBUG trace\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 0
    assert len(result.matches) == 0


def test_stringzilla_reuses_persistent_trigram_index_across_instances(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sz-cache"
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(cache_dir))
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "1")
    StringZillaBackend._clear_shared_caches()

    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\nDEBUG trace\nERROR timeout\n", encoding="utf-8")

    first = StringZillaBackend().search(
        str(log_file), "ERROR", config=SearchConfig(fixed_strings=True)
    )
    assert first.total_matches == 2
    assert first.routing_reason == "stringzilla_fixed_strings_index"

    backend_two = StringZillaBackend()

    def fail_build(*_args, **_kwargs):
        raise AssertionError("should not rebuild trigram index on cache hit")

    backend_two._build_line_trigram_index = fail_build  # type: ignore[method-assign]
    second = backend_two.search(str(log_file), "DEBUG", config=SearchConfig(fixed_strings=True))

    assert second.total_matches == 1
    assert second.matches[0].line_number == 3
    assert second.routing_reason == "stringzilla_fixed_strings_index_cache"


def test_stringzilla_invalidates_persistent_trigram_index_when_file_changes(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sz-cache"
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(cache_dir))
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "1")
    StringZillaBackend._clear_shared_caches()

    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\n", encoding="utf-8")

    first_backend = StringZillaBackend()
    first = first_backend.search(str(log_file), "ERROR", config=SearchConfig(fixed_strings=True))
    assert first.total_matches == 1

    log_file.write_text("INFO ok\nWARN warning\n", encoding="utf-8")

    second_backend = StringZillaBackend()
    build_calls = {"count": 0}
    original_build = second_backend._build_line_trigram_index

    def wrapped_build(lines):
        build_calls["count"] += 1
        return original_build(lines)

    second_backend._build_line_trigram_index = wrapped_build  # type: ignore[method-assign]
    second = second_backend.search(str(log_file), "WARN", config=SearchConfig(fixed_strings=True))

    assert second.total_matches == 1
    assert second.matches[0].line_number == 2
    assert second.routing_reason == "stringzilla_fixed_strings_index"
    assert build_calls["count"] == 1
