from pathlib import Path

import pytest


def test_rust_core_import():
    """Verify that the pyo3 native extension compiles and can be imported."""
    import importlib.util

    if not importlib.util.find_spec("tensor_grep.rust_core"):
        pytest.fail("Failed to import tensor_grep.rust_core")


def test_rust_backend_search(tmp_path: Path):
    """Verify the RustBackend correctly searches a file and returns results."""
    from tensor_grep.backends.rust_backend import RustCoreBackend

    # Create a dummy log file
    log_file = tmp_path / "test.log"
    log_file.write_text("INFO: starting up\nERROR: database connection failed\nWARN: retrying")

    backend = RustCoreBackend()
    result = backend.search(str(log_file), "ERROR")

    assert result.total_matches == 1
    assert "ERROR: database connection failed" in result.matches[0].text
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_regex"


def test_rust_backend_respects_invert_and_skips_count_fast_path(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def count_matches(self, pattern, path, ignore_case, fixed_strings):
            raise AssertionError("count fast-path should be bypassed for invert_match")

        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            assert invert_match is True
            return [(7, "FROM_RUST_WRAPPER")]

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "invert.log"
    log_file.write_text("ERROR\nINFO\n")
    result = backend.search(
        str(log_file),
        "ERROR",
        config=SearchConfig(count=True, invert_match=True),
    )

    assert result.total_matches == 1
    assert result.matches[0].line_number == 7
    assert result.matches[0].text == "FROM_RUST_WRAPPER"
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_regex"


def test_rust_backend_honors_max_count(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            return [(1, "apple"), (2, "apple banana")]

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "max_count.log"
    log_file.write_text("apple\napple banana\n")
    result = backend.search(str(log_file), "apple", config=SearchConfig(max_count=1))

    assert result.total_matches == 1
    assert result.total_files == 1
    assert [(match.line_number, match.text) for match in result.matches] == [(1, "apple")]
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_regex"


def test_rust_backend_skips_file_over_max_filesize(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def execute_ripgrep(self, *args, **kwargs):
            raise RuntimeError("rg unavailable")

        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            raise AssertionError("oversized file should be skipped before Rust search")

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "large.log"
    log_file.write_text("match_me" + ("x" * 1024), encoding="utf-8")
    result = backend.search(str(log_file), "match_me", config=SearchConfig(max_filesize="100B"))

    assert result.total_matches == 0
    assert result.total_files == 0
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_max_filesize_skipped"


def test_rust_backend_limit_passthrough_reports_found_via_exit_code(monkeypatch, tmp_path: Path):
    """The limit/pcre2 passthrough streams matches straight to stdout, so the exact count is
    unknowable; it must still report non-empty via rg's exit code (0 = matched). Otherwise the CLI's
    `is_empty` check treats a real match as empty and exits 1 with the wrong status. Audit #2."""
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    exit_code = {"value": 0}

    class FakeNativeRustBackend:
        def execute_ripgrep(self, *args, **kwargs):
            return exit_code["value"]

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "app.log"
    log_file.write_text("ERROR boom\n", encoding="utf-8")
    config = SearchConfig(no_ignore_vcs=True)

    exit_code["value"] = 0  # ripgrep emitted at least one match
    found = backend.search(str(log_file), "ERROR", config=config)
    assert found.routing_reason == "rust_limit_passthrough"
    assert found.total_matches > 0
    assert not found.is_empty

    exit_code["value"] = 1  # ripgrep matched nothing
    empty = backend.search(str(log_file), "ERROR", config=config)
    assert empty.total_matches == 0
    assert empty.is_empty


def test_rust_limit_passthrough_forwards_previously_dropped_rg_flags(monkeypatch, tmp_path: Path):
    """The PyO3 execute_ripgrep bridge must FORWARD path_separator/vimgrep/sort_files/max_depth/null/
    null_data and the resolved no_line_number, not hardcode them — rg_passthrough.rs already supports
    them. Audit #3 (the bug was the Python<->Rust bridge, not the rg command builder).

    R1a: the bridge call now passes every argument by KEYWORD (matching the exact parameter names of
    `execute_ripgrep` in rust_core/src/lib.rs), so this fake captures **kwargs and asserts by name
    instead of by position — a stale/renamed key here would raise KeyError immediately rather than
    silently reading the wrong positional slot.
    """
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    captured: dict[str, dict] = {}

    class FakeNativeRustBackend:
        def execute_ripgrep(self, *args, **kwargs):
            assert args == (), "execute_ripgrep must be called with keyword args only (R1a)"
            captured["kwargs"] = kwargs
            return 0

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "app.log"
    log_file.write_text("ERROR boom\n", encoding="utf-8")
    config = SearchConfig(
        no_ignore_vcs=True,
        line_number=False,
        path_separator="/",
        vimgrep=True,
        sort_files=True,
        max_depth=5,
        null=True,
        null_data=True,
    )

    backend.search(str(log_file), "ERROR", config=config)
    kwargs = captured["kwargs"]
    # rg_passthrough.rs checks no_line_number before line_number, so a resolved "hide" must arrive as
    # no_line_number=True.
    assert kwargs["no_line_number"] is True
    assert kwargs["path_separator"] == "/"
    assert kwargs["vimgrep"] is True
    assert kwargs["sort_files"] is True
    assert kwargs["max_depth"] == 5
    assert kwargs["null"] is True
    assert kwargs["null_data"] is True
    # globs/file_types must be [] not None: rg's PyO3 Vec<String> rejects None, which silently fell
    # the whole passthrough back to a flag-dropping path (config.glob and config.file_type both
    # default to None). This guard is what makes #2/#3 actually take effect.
    assert kwargs["globs"] == [] and kwargs["file_types"] == []


def test_rust_pcre2_bridge_failure_fails_closed(monkeypatch, tmp_path: Path):
    """Audit #1: if the native ripgrep bridge fails for a PCRE2 search, RustCoreBackend must FAIL
    CLOSED (raise), never silently fall back to the Python-regex engine, which cannot preserve PCRE2
    semantics (that would return wrong matches)."""
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def execute_ripgrep(self, *args, **kwargs):
            raise RuntimeError("bridge boom")

        def search(self, *args, **kwargs):
            return []

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)
    backend = rb.RustCoreBackend()
    log_file = tmp_path / "a.log"
    log_file.write_text("ERROR\n", encoding="utf-8")
    with pytest.raises(BackendExecutionError, match="PCRE2"):
        backend.search(str(log_file), "ERROR", config=SearchConfig(pcre2=True))


def test_rust_limit_bridge_failure_records_fallback_reason(monkeypatch, tmp_path: Path):
    """Audit #1: if the native bridge fails for a limit-flag search, the fallback must record a
    VISIBLE fallback_reason instead of silently downgrading the flag contract to another engine."""
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def execute_ripgrep(self, *args, **kwargs):
            raise RuntimeError("bridge boom")

        def search(self, pattern, path, ignore_case, fixed_strings, invert_match=False):
            return [(1, "ERROR line")]

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)
    backend = rb.RustCoreBackend()
    log_file = tmp_path / "a.log"
    log_file.write_text("ERROR line\n", encoding="utf-8")
    result = backend.search(str(log_file), "ERROR", config=SearchConfig(no_ignore_vcs=True))
    assert result.routing_reason == "rust_regex"
    assert result.fallback_reason is not None
    assert "passthrough failed" in result.fallback_reason


def test_rust_backend_returns_binary_notice_unless_text_or_binary_flag_is_set(
    monkeypatch, tmp_path: Path
):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            return [(1, "ERROR\0hidden")]

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    binary_file = tmp_path / "compiled.pyc"
    binary_file.write_bytes(b"\x80ERROR\x00hidden")

    notice = backend.search(str(binary_file), "ERROR", config=SearchConfig())
    no_match = backend.search(str(binary_file), "MISSING", config=SearchConfig())
    text_result = backend.search(str(binary_file), "ERROR", config=SearchConfig(text=True))
    binary_result = backend.search(str(binary_file), "ERROR", config=SearchConfig(binary=True))

    assert notice.total_matches == 1
    assert notice.total_files == 1
    assert notice.matches[0].text == 'binary file matches (found "/0" byte around offset 6)'
    assert notice.matches[0].meta_variables == {"binary_notice": True}
    assert notice.routing_reason == "rust_binary_notice"
    assert no_match.total_matches == 0
    assert no_match.total_files == 0
    assert no_match.routing_reason == "rust_binary_skipped"
    assert text_result.total_matches == 1
    assert binary_result.total_matches == 1


def test_rust_backend_invalid_regex_in_binary_notice_does_not_become_literal(
    monkeypatch, tmp_path: Path
):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.backends.cpu_backend import InvalidRegexError
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            raise AssertionError("binary notice path should validate before native search")

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    binary_file = tmp_path / "compiled.pyc"
    binary_file.write_bytes(b"\x80(\x00hidden")

    with pytest.raises(InvalidRegexError):
        backend.search(str(binary_file), "(", config=SearchConfig())


def test_rust_backend_count_fast_path_reports_routing_metadata(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    class FakeNativeRustBackend:
        def count_matches(self, pattern, path, ignore_case, fixed_strings):
            return 4

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FakeNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "count.log"
    log_file.write_text("ERROR\nERROR\nERROR\nERROR\n")
    result = backend.search(str(log_file), "ERROR", config=SearchConfig(count=True))

    assert result.total_matches == 4
    assert result.match_counts_by_file == {str(log_file): 4}
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_count"


def test_rust_backend_unavailable_should_report_routing_metadata(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb

    monkeypatch.setattr(rb, "HAVE_RUST", False)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "missing_rust.log"
    log_file.write_text("ERROR\n")
    result = backend.search(str(log_file), "ERROR")

    assert result.total_matches == 0
    assert result.routing_backend == "RustCoreBackend"
    assert result.routing_reason == "rust_unavailable"
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_rust_backend_exception_should_raise_backend_execution_error(monkeypatch, tmp_path: Path):
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.backends.base import BackendExecutionError

    class FailingNativeRustBackend:
        def search(self, pattern, path, ignore_case, fixed_strings, invert_match):
            raise RuntimeError("boom")

    monkeypatch.setattr(rb, "HAVE_RUST", True)
    monkeypatch.setattr(rb, "NativeRustBackend", FailingNativeRustBackend)

    backend = rb.RustCoreBackend()
    log_file = tmp_path / "rust_fail.log"
    log_file.write_text("ERROR\n")

    # audit B2: a native failure must surface as an error the caller can fall back on,
    # never as a silent, empty success-shaped result indistinguishable from no-match.
    with pytest.raises(BackendExecutionError):
        backend.search(str(log_file), "ERROR")
