import pytest

from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture
def backend():
    return StringZillaBackend()


def test_stringzilla_should_round_trip_compact_line_indexes():
    encoded = StringZillaBackend._compress_line_indexes([1, 2, 3, 7, 9, 10])
    assert encoded == [[1, 3], [7, 7], [9, 10]]
    assert StringZillaBackend._decompress_line_indexes(encoded) == [1, 2, 3, 7, 9, 10]


def test_stringzilla_should_intersect_sorted_line_indexes():
    postings = [[1, 2, 4, 7], [2, 4, 7, 9], [0, 2, 7, 10]]
    assert StringZillaBackend._intersect_sorted_line_indexes(postings) == [2, 7]


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


def test_stringzilla_plain_path_preserves_crlf_source_line(backend, tmp_path, monkeypatch):
    """task #262 BLOCKING finding: `_load_searchable_text`'s non-binary-as-text branch used
    to open the file via `open(file_path, encoding="utf-8")` (Python default universal-
    newlines TEXT mode, translating a real `\\r\\n` to `\\n` ON READ) and then split with
    `sz.Str.splitlines()` -- silently stripping a CRLF file's own `\\r` even when the file's
    plain-text (non `-F`) output otherwise agreed with `rg`. Real `rg` preserves that `\\r`
    (verified directly against `rg.exe`); this must too, on both the plain path (this test)
    and the `-F` trigram-index path (the sibling test below).
    """
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    crlf_log = tmp_path / "crlf.log"
    crlf_log.write_bytes(b"alpha needle one\r\nbeta line two\r\n")

    result = backend.search(str(crlf_log), "needle", config=SearchConfig(fixed_strings=False))
    assert result.total_matches == 1
    assert result.matches[0].text == "alpha needle one\r"

    lf_log = tmp_path / "lf.log"
    lf_log.write_text("alpha needle one\nbeta line two\n", encoding="utf-8", newline="\n")
    lf_result = backend.search(str(lf_log), "needle", config=SearchConfig(fixed_strings=False))
    assert lf_result.matches[0].text == "alpha needle one"


def test_stringzilla_fixed_strings_index_path_preserves_crlf_source_line(tmp_path, monkeypatch):
    """The `-F` / trigram-index sibling of the test above -- `_search_with_index` had the
    identical `content.splitlines()` defect independently of `_load_searchable_text`'s own
    read-mode bug, so both must be exercised."""
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    StringZillaBackend._clear_shared_caches()

    crlf_log = tmp_path / "crlf.log"
    crlf_log.write_bytes(b"alpha needle one\r\nbeta line two\r\n")

    result = StringZillaBackend().search(
        str(crlf_log), "needle", config=SearchConfig(fixed_strings=True)
    )
    assert result.total_matches == 1
    assert result.matches[0].text == "alpha needle one\r"


def test_stringzilla_does_not_treat_a_bare_cr_as_a_line_break(backend, tmp_path, monkeypatch):
    """Ground truth verified directly against `rg.exe`: a file with ONLY bare `\\r` line
    endings (no `\\n` at all) is ONE line to `rg` (it splits on `\\n` alone), matched_lines=1.
    Before task #262, StringZilla's own `Str.splitlines()` (and Python's `str.splitlines()`
    in the trigram-index path) treated the bare `\\r` as a line break too, silently
    reporting a phantom second match neither `rg` nor the fixed CPU/Rust paths ever did.
    """
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    cr_only_log = tmp_path / "cronly.log"
    cr_only_log.write_bytes(b"line one\rline two\r")

    result = backend.search(str(cr_only_log), "line", config=SearchConfig(fixed_strings=False))
    assert result.total_matches == 1
    assert result.matches[0].line_number == 1
    assert result.matches[0].text == "line one\rline two\r"


def test_stringzilla_no_matches(backend, tmp_path):
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nDEBUG trace\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 0
    assert len(result.matches) == 0


def test_stringzilla_skips_invalid_utf8_by_default(backend, tmp_path):
    binary_file = tmp_path / "compiled.pyc"
    binary_file.write_bytes(b"\x80\x81\x82needle\xff\xfe")

    result = backend.search(str(binary_file), "needle", config=SearchConfig(fixed_strings=True))

    assert result.total_matches == 0
    assert result.matches == []
    assert result.routing_backend == "StringZillaBackend"


def test_stringzilla_can_search_binary_like_content_when_text_mode_enabled(backend, tmp_path):
    binary_file = tmp_path / "compiled.pyc"
    binary_file.write_bytes(b"\x80\x81needle\x00tail\xff")

    result = backend.search(
        str(binary_file),
        "needle",
        config=SearchConfig(fixed_strings=True, text=True),
    )

    assert result.total_matches == 1
    assert len(result.matches) == 1
    assert result.matches[0].line_number == 1
    assert result.matches[0].file == str(binary_file)


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


def test_stringzilla_rejects_stale_pre_fix_persistent_index(tmp_path, monkeypatch):
    """task #262 BLOCKING finding #2: the persisted index payload used to carry only
    `file_signature = (mtime_ns, size)`, with no format version. `(mtime, size)` proves the
    file's BYTES are unchanged; it cannot prove the *interpretation* of those bytes is still
    current -- a cache written by pre-fix code (CRLF `\\r` already stripped from `lines`)
    would silently defeat this fix and be served forever (same file, unchanged mtime/size),
    reading as WORSE than pre-fix (which was byte-identical to `rg` by cancellation with the
    stdout bug). Simulates exactly that: a hand-written on-disk cache with no
    `format_version` key, same file_signature as the real file, but pre-fix (CRLF-stripped)
    `lines`. Must be rejected and transparently recomputed + rewritten, not served.
    """
    import json

    from tensor_grep.backends.stringzilla_backend import _STRING_INDEX_CACHE_FORMAT_VERSION

    cache_dir = tmp_path / "sz-cache"
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(cache_dir))
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "1")
    StringZillaBackend._clear_shared_caches()

    log_file = tmp_path / "sys.log"
    log_file.write_bytes(b"alpha needle one\r\nbeta line two\r\n")

    backend = StringZillaBackend()
    cache_path = backend._get_index_cache_path(str(log_file), False, False)
    # The version lives in the cache PATH itself, not just the payload -- see the identical
    # assertion + comment on CPUBackend's sibling test
    # (test_rejects_stale_pre_fix_persistent_literal_index) for why the payload-only check
    # below cannot catch a filename regression that silently drops the `-v{VERSION}` suffix.
    assert cache_path.name.endswith(f"-v{_STRING_INDEX_CACHE_FORMAT_VERSION}.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stale_payload = {
        # No "format_version" key at all -- the exact pre-#262 on-disk shape.
        "file_signature": list(backend._build_file_signature(str(log_file))),
        "lines": ["alpha needle one", "beta line two"],  # pre-fix: \r already stripped
        "trigram_index": {"alp": [0], "lph": [0], "pha": [0]},
    }
    cache_path.write_text(json.dumps(stale_payload), encoding="utf-8")

    result = backend.search(
        str(log_file), "alpha needle one", config=SearchConfig(fixed_strings=True)
    )
    assert result.routing_reason == "stringzilla_fixed_strings_index"  # fresh, not "_cache"
    assert result.matches[0].text == "alpha needle one\r"

    rewritten = json.loads(cache_path.read_text(encoding="utf-8"))
    assert rewritten["format_version"] == _STRING_INDEX_CACHE_FORMAT_VERSION
    assert rewritten["lines"] == ["alpha needle one\r", "beta line two\r"]


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


def test_stringzilla_cache_hit_search_uses_sorted_posting_intersection(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sz-cache"
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(cache_dir))
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "1")
    StringZillaBackend._clear_shared_caches()

    log_file = tmp_path / "sys.log"
    log_file.write_text(
        "INFO ok\nERROR alpha timeout\nDEBUG trace\nERROR alpha critical timeout\n",
        encoding="utf-8",
    )

    first = StringZillaBackend().search(
        str(log_file),
        "alpha timeout",
        config=SearchConfig(fixed_strings=True),
    )
    assert first.total_matches == 1

    backend_two = StringZillaBackend()
    calls = {"count": 0}
    original = backend_two._intersect_sorted_line_indexes

    def wrapped(postings):
        calls["count"] += 1
        return original(postings)

    backend_two._intersect_sorted_line_indexes = wrapped  # type: ignore[method-assign]
    second = backend_two.search(
        str(log_file),
        "critical timeout",
        config=SearchConfig(fixed_strings=True),
    )

    assert second.total_matches == 1
    assert second.matches[0].line_number == 4
    assert second.routing_reason == "stringzilla_fixed_strings_index_cache"
    assert calls["count"] == 1


def test_stringzilla_honors_invert_match_indexed_path(backend, tmp_path):
    """H5: -F --invert-match must return the COMPLEMENT (lines NOT containing the
    pattern), not the matching lines. Pattern length >=3 hits the trigram-index
    fast path by default; invert_match must fall through to a full scan there since
    the index can only answer "which lines DO contain every trigram", not the
    inverse."""
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\nDEBUG trace\nERROR timeout\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, invert_match=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 2
    assert [m.line_number for m in result.matches] == [1, 3]
    assert all("ERROR" not in m.text for m in result.matches)


def test_stringzilla_honors_invert_match_matches_cpu_backend(tmp_path):
    """H5 parity: stringzilla's inverted result must match CPUBackend's (the
    already-correct reference implementation) for the same query."""
    from tensor_grep.backends.cpu_backend import CPUBackend

    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\nDEBUG trace\nERROR timeout\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, invert_match=True)
    sz_result = StringZillaBackend().search(str(log_file), "ERROR", config=config)
    cpu_result = CPUBackend().search(str(log_file), "ERROR", config)

    assert sz_result.total_matches == cpu_result.total_matches
    assert [m.line_number for m in sz_result.matches] == [m.line_number for m in cpu_result.matches]


def test_stringzilla_honors_invert_match_non_indexed_path(backend, tmp_path, monkeypatch):
    """H5 also applies on the short-pattern (<3 chars) path that bypasses the
    trigram index entirely."""
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    log_file = tmp_path / "sys.log"
    log_file.write_text("hi\nno\nhi\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, invert_match=True)
    result = backend.search(str(log_file), "hi", config=config)

    assert result.total_matches == 1
    assert result.matches[0].line_number == 2


def test_stringzilla_honors_max_count_indexed_path(backend, tmp_path):
    """H6: -F --max-count 2 must cap to exactly 2 matches, matching rg/cpu_backend's
    per-file cap semantics, not return every match."""
    log_file = tmp_path / "sys.log"
    log_file.write_text("ERROR one\nERROR two\nERROR three\nERROR four\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, max_count=2)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 2
    assert [m.line_number for m in result.matches] == [1, 2]


def test_stringzilla_max_count_matches_cpu_backend(tmp_path):
    from tensor_grep.backends.cpu_backend import CPUBackend

    log_file = tmp_path / "sys.log"
    log_file.write_text("ERROR one\nERROR two\nERROR three\nERROR four\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, max_count=3)
    sz_result = StringZillaBackend().search(str(log_file), "ERROR", config=config)
    cpu_result = CPUBackend().search(str(log_file), "ERROR", config)

    assert sz_result.total_matches == cpu_result.total_matches == 3
    assert [m.line_number for m in sz_result.matches] == [m.line_number for m in cpu_result.matches]


def test_stringzilla_honors_max_count_non_indexed_path(backend, tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    log_file = tmp_path / "sys.log"
    log_file.write_text("hi\nhi\nhi\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True, max_count=2)
    result = backend.search(str(log_file), "hi", config=config)

    assert result.total_matches == 2
    assert [m.line_number for m in result.matches] == [1, 2]


def test_stringzilla_native_fault_raises_backend_execution_error(tmp_path, monkeypatch):
    """audit #10: search() previously had no try/except, so a fault raised anywhere inside
    it (originally demonstrated via a native ``sz.Str()`` construction failure) escaped raw
    instead of surfacing as BackendExecutionError per the Backend Fail-Closed Contract
    (base.py). A caller's `except BackendExecutionError` handler (main.py's per-file
    CPU-fallback retry, cli/main.py:6756-6761) must catch it instead of the search crashing
    outright.

    task #262: `search()` no longer constructs an `sz.Str()` at all (the correctness fix
    replaced native-but-buggy line-splitting with `split_source_lines`, a plain-Python
    helper shared with CPUBackend), so this now injects the fault at that call site instead
    -- the *mechanism* under test is the try/except wrapping around the whole method body,
    not specifically a StringZilla-native failure.
    """
    from tensor_grep.backends import stringzilla_backend as sz_backend_module
    from tensor_grep.backends.base import BackendExecutionError

    log_file = tmp_path / "sys.log"
    log_file.write_text("ERROR failure\n", encoding="utf-8")

    def _raise_fault(*_args, **_kwargs):
        raise RuntimeError("native stringzilla panic: kaboom")

    monkeypatch.setattr(sz_backend_module, "split_source_lines", _raise_fault)

    backend = StringZillaBackend()
    # fixed_strings=False bypasses the pure-Python trigram-index fast path
    # (_search_with_index) so the patched split_source_lines call is actually exercised.
    with pytest.raises(BackendExecutionError) as exc_info:
        backend.search(str(log_file), "ERROR", config=SearchConfig(fixed_strings=False))
    assert isinstance(exc_info.value, RuntimeError)  # broader `except RuntimeError` still works
    assert "kaboom" in str(exc_info.value)


def test_stringzilla_in_memory_index_cache_obeys_entry_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES", "2")
    StringZillaBackend._clear_shared_caches()
    backend = StringZillaBackend()
    files = []
    for index in range(3):
        path = tmp_path / f"file_{index}.log"
        path.write_text(f"needle {index}\n", encoding="utf-8")
        files.append(path)
        backend._persist_index(
            str(path),
            False,
            False,
            [f"needle {index}"],
            {"nee": [0]},
        )

    cache = StringZillaBackend._shared_index_cache
    assert len(cache) == 2
    assert (str(files[0]), False, False) not in cache
    assert (str(files[1]), False, False) in cache
    assert (str(files[2]), False, False) in cache
