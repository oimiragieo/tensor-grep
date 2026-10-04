from pathlib import Path

import pytest


def _rg_or_skip():
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if resolve_ripgrep_binary() is None:
        pytest.skip("rg not installed")


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


def test_rust_backend_real_extension_bidirectional_oracle(tmp_path: Path):
    """R1: exercise the REAL pyo3 extension end-to-end through the keyword-argument bridge.

    A bidirectional oracle -- a correct query MATCHES and a wrong query returns EMPTY, and an
    invert_match query returns the complement -- so a dead or mis-forwarded bridge is caught here
    (a ``*args, **kwargs`` mock would swallow a transposed/dropped flag silently). Passing every
    argument by keyword also means a future Rust-side signature change fails loudly with a
    ``TypeError`` instead of silently shifting flag meanings.
    """
    import importlib.util

    if not importlib.util.find_spec("tensor_grep.rust_core"):
        pytest.skip("native tensor_grep.rust_core extension not compiled")

    from tensor_grep.backends.rust_backend import RustCoreBackend
    from tensor_grep.core.config import SearchConfig

    log_file = tmp_path / "bidir.txt"
    log_file.write_text("alpha\nbeta\nalpha\n")
    backend = RustCoreBackend()
    assert backend.is_available()

    # match direction: 'alpha' appears on two lines
    assert backend.search(str(log_file), "alpha", SearchConfig()).total_matches == 2
    # non-match direction (the half a one-sided test misses): a wrong query returns EMPTY
    assert backend.search(str(log_file), "zzz", SearchConfig()).total_matches == 0
    # invert_match kwarg forwards correctly: the complement is the single 'beta' line
    inverted = backend.search(str(log_file), "alpha", SearchConfig(invert_match=True))
    assert inverted.total_matches == 1


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
    _rg_or_skip()  # the regex binary-file check is decided by rg, not Python re
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
    assert notice.matches[0].text == 'binary file matches (found "\\0" byte around offset 6)'
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
    _rg_or_skip()
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


def test_rust_backend_unavailable_should_raise_backend_execution_error(monkeypatch, tmp_path: Path):
    """audit #14: a direct caller that bypasses the ``is_available()`` gate and calls
    ``search()`` while ``self.inner is None`` must not get a silent empty-but-success
    ``SearchResult`` -- that is indistinguishable from a genuine no-match. Per the Backend
    Fail-Closed Contract (base.py), it must raise ``BackendExecutionError`` instead, exactly
    like a genuine native-search failure (see
    ``test_rust_backend_exception_should_raise_backend_execution_error`` below), which a
    caller catching ``BackendExecutionError`` (e.g. main.py's per-file CPU-fallback retry)
    can react to instead of silently trusting a false no-match.
    """
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.backends.base import BackendExecutionError

    monkeypatch.setattr(rb, "HAVE_RUST", False)

    backend = rb.RustCoreBackend()
    assert backend.inner is None
    log_file = tmp_path / "missing_rust.log"
    log_file.write_text("ERROR\n")

    with pytest.raises(BackendExecutionError):
        backend.search(str(log_file), "ERROR")


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


def test_binary_notice_check_does_not_use_python_re():
    # Deviation from the plan's `monkeypatch.setattr(rb.re, "search", boom)`: that stubs the
    # GLOBAL stdlib `re.search` through a module alias (standing rule). Assert on the AST
    # instead: no `re.<anything>` call may remain in the binary-file match check.
    import ast
    import inspect
    import textwrap

    from tensor_grep.backends import rust_backend as rb

    src = textwrap.dedent(inspect.getsource(rb.RustCoreBackend._binary_file_matches_pattern))
    uses_re = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "re"
    ]
    assert not uses_re, "user pattern must not be evaluated by Python re"


def test_binary_notice_check_redos_pattern_goes_through_rg(tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00" + b"a" * 60 + b"\nxyz\n")
    assert rb.RustCoreBackend._binary_file_matches_pattern(str(f), "(a+)+$", SearchConfig()) is True


def test_binary_notice_check_accepts_unicode_class(tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00abc\n")
    assert (
        rb.RustCoreBackend._binary_file_matches_pattern(str(f), r"\p{L}+", SearchConfig()) is True
    )


def test_binary_notice_check_honours_word_and_smart_case(tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00foobar\n")
    check = rb.RustCoreBackend._binary_file_matches_pattern
    assert check(str(f), "foo", SearchConfig(word_regexp=True)) is False
    assert check(str(f), "FOO", SearchConfig(smart_case=True)) is False
    assert check(str(f), "foo", SearchConfig(smart_case=True)) is True
    # fixed-string branch must honour -w / -x too (was raw byte containment)
    assert check(str(f), "foo", SearchConfig(fixed_strings=True, word_regexp=True)) is False
    assert check(str(f), "foo", SearchConfig(fixed_strings=True, line_regexp=True)) is False
    assert check(str(f), "foobar", SearchConfig(fixed_strings=True)) is True
    assert check(str(f), "a.b", SearchConfig(fixed_strings=True)) is False  # literal, not regex


def test_plain_fixed_string_binary_check_works_without_rg(monkeypatch, tmp_path):
    # council round 11: rg-free positive control incl. a match spanning a chunk boundary
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.cli import runtime_paths
    from tensor_grep.core.config import SearchConfig

    monkeypatch.setattr(runtime_paths, "resolve_ripgrep_binary", lambda: None)
    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00" + b"x" * 65530 + b"NEEDLE" + b"\n")  # straddles the 65536 boundary
    check = rb.RustCoreBackend._binary_file_matches_pattern
    assert check(str(f), "NEEDLE", SearchConfig(fixed_strings=True)) is True
    assert check(str(f), "ABSENT", SearchConfig(fixed_strings=True)) is False
    assert rb._file_contains_literal(str(f), b"NEEDLE", chunk_size=4096) is True
    with pytest.raises(rb.BackendExecutionError):  # fail-closed: an unsupported flag needs rg
        check(str(f), "needle", SearchConfig(fixed_strings=True, ignore_case=True))


def test_first_nul_offset_is_chunked(tmp_path):
    from tensor_grep.backends import rust_backend as rb

    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 70000 + b"\x00")
    assert rb._first_nul_offset(str(f), chunk_size=4096) == 70000
    g = tmp_path / "none.txt"
    g.write_bytes(b"abc")
    assert rb._first_nul_offset(str(g)) == -1


def test_first_nul_offset_never_reads_unbounded(tmp_path, monkeypatch):
    # council round 13: offset correctness alone would pass with read_bytes().find(); record reads
    import builtins

    from tensor_grep.backends import rust_backend as rb

    sizes: list[int | None] = []
    real_open = builtins.open

    class _Rec:
        def __init__(self, handle):
            self._h = handle

        def read(self, n=-1):
            sizes.append(n)
            return self._h.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._h.close()

    monkeypatch.setattr(
        rb,
        "open",
        lambda p, mode="r", *a, **k: _Rec(real_open(p, mode, *a, **k)),
        raising=False,
    )
    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 70000 + b"\x00")
    assert rb._first_nul_offset(str(f), chunk_size=4096) == 70000  # crosses many chunk boundaries
    # every read is bounded by chunk_size (a module-global `open` shadow intercepts the call)
    assert sizes
    assert all(isinstance(n, int) and 0 < n <= 4096 for n in sizes), sizes


def test_binary_notice_entry_points_never_read_whole_file(tmp_path, monkeypatch):
    # council round 14: BEHAVIOURAL, through both existing notice consumers.
    from pathlib import Path as _P

    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 200_000 + b"\x00" + b"tail\n")
    monkeypatch.setattr(_P, "read_bytes", lambda self: pytest.fail(f"whole-file read of {self}"))
    assert rb.RustCoreBackend._binary_notice_text(str(f)) is not None  # notice still produced
    assert RipgrepFormatter(SearchConfig())._binary_notice(str(f)) is not None
