import subprocess
import sys
import time
import types
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.core.config import SearchConfig


class TestCPUBackend:
    def teardown_method(self):
        CPUBackend._clear_shared_caches()

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

    def test_should_skip_binary_files_unless_text_or_binary_flag_is_set(self, tmp_path):
        binary_file = tmp_path / "test.pyc"
        binary_file.write_bytes(b"\x00\x01ERROR\x02\n")
        backend = CPUBackend()

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Simulate the native engine being genuinely ABSENT (audit #111 Opus-gate hardening: the
        # Python fallback loop is now reachable for a non-fixed-strings pattern ONLY via the
        # ImportError branch -- a present-but-failing Rust fails closed instead). This test is
        # about binary-file handling, not ReDoS, so routing it through the Rust-absent fall-open
        # preserves its intent while staying on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            skipped = backend.search(str(binary_file), "ERROR", config=SearchConfig())
            text_result = backend.search(str(binary_file), "ERROR", config=SearchConfig(text=True))

        assert skipped.total_matches == 0
        assert skipped.routing_reason == "cpu_binary_skipped"
        assert text_result.total_matches == 1

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
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_missing_file"
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1

    def test_should_report_line_numbers(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "ERROR")
        assert [m.line_number for m in result.matches] == [2, 4]

    def test_should_handle_utf8_and_latin1(self, tmp_path):
        latin_file = tmp_path / "latin.log"
        latin_file.write_bytes(b"ERROR line caf\xe9\n")

        backend = CPUBackend()
        result = backend.search(str(latin_file), "ERROR")
        assert result.total_matches == 1

    def test_should_includeAfterContext_when_dashA_isProvided(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "context.log"
        # newline="\n" pins this fixture to LF regardless of platform default: without it,
        # Path.write_text() silently writes CRLF on Windows, and task #262's
        # strip_line_terminator fix now correctly preserves a CRLF line's own trailing \r
        # instead of over-stripping it, which would otherwise make every .text assertion
        # below fail on an incidental platform-default newline, not a real bug.
        log.write_text("line 1\nERROR MATCH\nline 3\nline 4\nline 5\n", newline="\n")

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
        log.write_text("line 1\nline 2\nERROR MATCH\nline 4\n", newline="\n")

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

    def test_should_not_fallback_to_python_when_rust_returns_empty(self, tmp_path):
        log = tmp_path / "fallback.log"
        log.write_text("ERROR present\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FakeRustBackend:
            def search(self, **kwargs):
                return []

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "ERROR")

        assert result.total_matches == 0
        assert result.matches == []

    def test_should_use_rust_path_for_invert_match_when_supported(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "invert.log"
        log.write_text("ERROR\nINFO\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FakeRustBackend:
            def search(self, **kwargs):
                assert kwargs["invert_match"] is True
                return [(2, "FROM_RUST")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "ERROR", config=SearchConfig(invert_match=True))

        assert result.total_matches == 1
        assert result.matches[0].line_number == 2
        assert result.matches[0].text == "FROM_RUST"
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex"

    def test_simple_fixed_inverted_internal_typeerror_fails_closed(self, tmp_path):
        """Plan Task 5 Step 3: the inline Rust adapter on the primary ("simple", -F/-v, no
        -C/-A/-B/-w/-x) search path used to catch ANY `TypeError` from
        `rust_backend.search(..., invert_match=...)` and retry WITHOUT `invert_match`, justified
        as "older rust_core builds without the kwarg".

        BE PRECISE ABOUT REACHABILITY (an earlier draft of this docstring was wrong): on a
        CURRENT build the retry cannot silently succeed, because `invert_match` is a REQUIRED
        positional in `rust_core/src/lib.rs` -- the retry raises `TypeError` again. The silent
        wrong answer was reachable only under VERSION SKEW, against an OLD extension lacking the
        parameter, where the retry was CORRECT for `invert_match=False` and WRONG for `-v`,
        returning the MATCHING set instead of the NON-matching one as a successful result.

        The fake backend below models exactly that skew: it raises TypeError whenever
        `invert_match` is supplied and returns a result on any other call, so the pre-fix retry
        silently "succeeds" with the wrong answer. It is a stand-in for the old build, NOT for an
        unrelated internal fault on a current one.

        Pre-fix: two calls, the second missing `invert_match` (the retry).
        Post-fix: exactly one call (carrying `invert_match`), and `BackendExecutionError` raised.
        """
        log = tmp_path / "invert_typeerror.log"
        log.write_text("apple\nbanana\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")
        calls = []

        class FakeRustBackend:
            def search(self, **kwargs):
                calls.append(kwargs)
                if "invert_match" in kwargs:
                    raise TypeError("simulated internal TypeError unrelated to invert_match")
                # The retry "succeeds" -- but this is the MATCHING set, not the inverted set.
                return [(1, "apple")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        config = SearchConfig(fixed_strings=True, invert_match=True)
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                backend.search(str(log), "apple", config=config)

        assert len(calls) == 1, (
            "expected exactly one native call carrying invert_match, no dropped-semantics retry; "
            f"got {len(calls)} calls: {calls}"
        )
        assert calls[0].get("invert_match") is True

    def test_word_regexp_inverted_internal_typeerror_fails_closed(self, tmp_path):
        """Same defect, sibling site: `_rust_match_set` (used by the -w/-x/-C/-A/-B path via
        `_search_word_line_context_via_rust`) had its own `except TypeError:` retry that dropped
        `invert_match`. `_search_word_line_context_via_rust` already fails closed on ANY
        exception from `_rust_match_set` -- so removing the retry there (letting TypeError
        propagate) is sufficient to fix this site; no new BackendExecutionError call needed at
        this site specifically, it inherits the existing wrapper.
        """
        log = tmp_path / "word_invert_typeerror.log"
        log.write_text("cat\nconcatenate\nscatter cat here\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")
        calls = []

        class FakeRustBackend:
            def search(self, **kwargs):
                calls.append(kwargs)
                if "invert_match" in kwargs:
                    raise TypeError("simulated internal TypeError unrelated to invert_match")
                # The retry "succeeds" -- but this is the MATCHING set, not the inverted set.
                return [(1, "cat")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        config = SearchConfig(word_regexp=True, invert_match=True)
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                backend.search(str(log), "cat", config=config)

        assert len(calls) == 1, (
            "expected exactly one native call carrying invert_match, no dropped-semantics retry; "
            f"got {len(calls)} calls: {calls}"
        )
        assert calls[0].get("invert_match") is True

    def test_cpu_backend_has_one_native_adapter_and_zero_typeerror_retries(self):
        """Population check for the fix above, derived by walking the AST of
        `cpu_backend.py` -- NOT by substring-counting (after a fix, a grep hit is usually the
        fix's own comment/docstring, not a real occurrence). Pinned by AST shape, never by line
        number.

        "Native adapter" = a `<expr>.search(...)` call whose keyword arguments include
        `invert_match` (the actual call into the Rust engine).

        "TypeError compatibility retry" = an `except TypeError:` handler whose body itself
        contains a `.search(...)` call -- i.e. one that CALLS THE NATIVE ENGINE AGAIN, dropping
        semantics along the way. This deliberately does NOT count a `TypeError` handler that
        instead fails closed (raises `BackendExecutionError` with no further native call) --
        that is the fix, not the defect: the defect is retrying, not catching.

        Pre-fix baseline (verified against origin/main): (2, 2) -- the inline adapter on the
        primary path and `_rust_match_set` each make their own native call and each retry (call
        `.search(...)` a second time) on `TypeError`. Post-fix: (1, 0) -- both sites are
        consolidated onto the single native call inside `_rust_match_set`, which never retries;
        a `TypeError` there propagates to a caller-side handler that raises
        `BackendExecutionError` without ever calling `.search(...)` again.
        """
        import ast

        import tensor_grep.backends.cpu_backend as cpu_backend_module

        source_path = Path(cpu_backend_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        native_adapter_calls = 0
        typeerror_retries = 0

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "search"
                and any(keyword.arg == "invert_match" for keyword in node.keywords)
            ):
                native_adapter_calls += 1
            if (
                isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name)
                and node.type.id == "TypeError"
            ):
                handler_calls_search_again = any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "search"
                    for inner in ast.walk(node)
                )
                if handler_calls_search_again:
                    typeerror_retries += 1

        assert (native_adapter_calls, typeerror_retries) == (1, 0), (
            "expected exactly one native adapter carrying invert_match and zero TypeError "
            f"compatibility retries; got native_adapter_calls={native_adapter_calls}, "
            f"typeerror_retries={typeerror_retries}"
        )

    def test_rejects_stale_pre_fix_persistent_literal_index(self, tmp_path, monkeypatch):
        """task #262 BLOCKING finding #2: the persisted literal-prefilter index payload used
        to carry only `file_signature = (mtime_ns, size)`, with no format version.
        `(mtime, size)` proves the file's BYTES are unchanged; it cannot prove the
        *interpretation* of those bytes is still current -- a cache written by pre-fix code
        (CRLF `\\r` already stripped from `lines`) would silently defeat this fix and be
        served forever (same file, unchanged mtime/size), reading as WORSE than pre-fix
        (which was byte-identical to `rg` by cancellation with the stdout bug). Simulates
        exactly that: a hand-written on-disk cache with no `format_version` key, same
        file_signature as the real file, but pre-fix (CRLF-stripped) `lines`. Must be
        rejected and transparently recomputed + rewritten, not served.

        The literal-prefilter path is only reached when `search()` cannot use the
        Rust-delegated fast path (`cpu_rust_regex`) -- on a machine/CI leg with the compiled
        `rust_core` extension present (every CI matrix leg; a normal shipped-wheel install),
        a plain query like this one is answered entirely by `rust_backend.search(...)` and
        never touches `_load_literal_index`/`_store_literal_index` at all, so asserting
        `routing_reason == "cpu_python_regex_prefilter"` without forcing Rust absent would
        pass only on a dev sandbox that happens to lack the built extension (as this was
        first written and verified) and fail everywhere else -- exactly the kind of
        environment-gated, not behavior-gated, oracle this task exists to eliminate. Forces
        the "Rust genuinely absent" branch deterministically via the same
        `patch.dict("sys.modules", {"tensor_grep.rust_core": None})` convention already used
        by `test_should_fail_closed_when_context_search_cannot_use_rust` elsewhere in this
        file, so the cache-versioning logic is exercised regardless of what's installed.
        """
        import json

        cache_dir = tmp_path / "cpu-cache"
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX_DIR", str(cache_dir))
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "1")
        backend = CPUBackend()
        CPUBackend._clear_shared_caches()

        from tensor_grep.backends.cpu_backend import _CPU_LITERAL_INDEX_CACHE_FORMAT_VERSION

        log_file = tmp_path / "sys.log"
        log_file.write_bytes(b"alpha needle one\r\nbeta line two\r\n")

        cache_path = backend._get_prefilter_cache_path(str(log_file), False)
        # The version lives in the cache PATH itself, not just the payload -- an older tg
        # sharing this cache dir with a newer one has no idea the payload's own
        # "format_version" field exists, so it would read (and re-corrupt) a newer payload
        # verbatim; a filename collision is impossible only because the version is baked
        # into the name. Assert the shape directly so a future refactor/merge that silently
        # drops the `-v{VERSION}` suffix (reopening exactly that corruption) is caught here
        # -- the payload-only assertion below cannot catch a filename regression, since it
        # reads whatever `cache_path` computes to, not a fixed expected name.
        assert cache_path.name.endswith(f"-v{_CPU_LITERAL_INDEX_CACHE_FORMAT_VERSION}.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        stale_payload = {
            # No "format_version" key at all -- the exact pre-#262 on-disk shape.
            "file_signature": list(backend._build_file_signature(str(log_file))),
            "lines": ["alpha needle one", "beta line two"],  # pre-fix: \r already stripped
            "trigram_index": {"alp": [0], "lph": [0], "pha": [0]},
        }
        cache_path.write_text(json.dumps(stale_payload), encoding="utf-8")

        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            result = backend.search(str(log_file), "alpha needle one", config=SearchConfig())
        assert result.routing_reason == "cpu_python_regex_prefilter"  # fresh, not "_cache"
        assert result.matches[0].text == "alpha needle one\r"

        rewritten = json.loads(cache_path.read_text(encoding="utf-8"))
        assert rewritten["format_version"] == _CPU_LITERAL_INDEX_CACHE_FORMAT_VERSION
        assert rewritten["lines"] == ["alpha needle one\r", "beta line two\r"]

    def test_literal_index_cache_obeys_entry_cap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "0")
        monkeypatch.setenv("TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES", "2")
        backend = CPUBackend()
        files = []
        for index in range(3):
            path = tmp_path / f"file_{index}.log"
            path.write_text(f"needle {index}\n", encoding="utf-8")
            files.append(path)
            backend._store_literal_index(
                str(path),
                False,
                [f"needle {index}"],
                {"nee": [0]},
            )

        cache = CPUBackend._shared_literal_index_cache
        assert len(cache) == 2
        assert (str(files[0]), False) not in cache
        assert (str(files[1]), False) in cache
        assert (str(files[2]), False) in cache

    def test_literal_prefilter_path_preserves_crlf_source_line(self, tmp_path, monkeypatch):
        """task #262 -- the LARGEST behavior change in this fix, previously uncovered by any
        test: the pure-Python literal-prefilter fallback used to read the file via
        `Path.read_text(encoding="utf-8", errors="replace").splitlines()`, which performs
        universal-newlines translation ON READ (eating a CRLF file's own `\\r`) AND treats a
        bare `\\r` as a line break on top of that. Both are now fixed by reading raw bytes and
        splitting on a bare `\\n` only (`split_source_lines`). A plain 3+ char literal with no
        other flags routes through this exact path (`_extract_required_literal`) -- but ONLY
        when `search()` cannot reach the Rust-delegated `cpu_rust_regex` fast path first (see
        `test_rejects_stale_pre_fix_persistent_literal_index` above for the full reasoning);
        force that deterministically via the file's established
        `patch.dict("sys.modules", {"tensor_grep.rust_core": None})` convention rather than
        relying on the extension being ambiently absent.
        """
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "0")  # force a fresh read every call
        backend = CPUBackend()
        CPUBackend._clear_shared_caches()

        crlf_log = tmp_path / "crlf.log"
        crlf_log.write_bytes(b"alpha needle one\r\nbeta line two\r\n")
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            result = backend.search(str(crlf_log), "needle", config=SearchConfig())
        assert result.routing_reason.startswith("cpu_python_regex_prefilter")
        assert result.total_matches == 1
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "alpha needle one\r"

        lf_log = tmp_path / "lf.log"
        lf_log.write_text("alpha needle one\nbeta line two\n", encoding="utf-8", newline="\n")
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            lf_result = backend.search(str(lf_log), "needle", config=SearchConfig())
        assert lf_result.matches[0].text == "alpha needle one"

    def test_literal_prefilter_path_does_not_treat_a_bare_cr_as_a_line_break(
        self, tmp_path, monkeypatch
    ):
        """Ground truth verified directly against `rg.exe`: a file containing ONLY bare `\\r`
        line endings (no `\\n` at all) is ONE line to `rg` (it splits on `\\n` alone, never a
        bare `\\r`), matched_lines=1. Before task #262, `Path.read_text().splitlines()` here
        treated the bare `\\r` as a line break too (Python's universal-newlines convention),
        silently reporting a phantom SECOND match/line that neither `rg` nor the fixed
        Rust-delegated path ever produced -- a line-number AND match-count divergence with no
        prior regression coverage. Forces the pure-Python path deterministically -- see the
        sibling test above; without this, CI (which builds `rust_core` on every matrix leg)
        answers via `cpu_rust_regex` and never reaches the code this test exists to cover.
        """
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "0")
        backend = CPUBackend()
        CPUBackend._clear_shared_caches()

        cr_only_log = tmp_path / "cronly.log"
        cr_only_log.write_bytes(b"line one\rline two\r")
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            result = backend.search(str(cr_only_log), "line", config=SearchConfig())
        assert result.routing_reason.startswith("cpu_python_regex_prefilter")
        assert result.total_matches == 1
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "line one\rline two\r"

    def test_should_strip_only_the_trailing_newline_from_rust_backend_matches(self, tmp_path):
        """task #262: the OLD `.rstrip("\n\r")` here stripped ANY trailing run of `\r`/`\n`,
        which silently ate a genuine trailing `\r` from a CRLF source line too (real `rg` and
        Rust's own line-splitter both preserve that `\r` -- verified directly against
        `rg.exe`). `strip_line_terminator` must remove ONLY the single trailing `\n`.

        This is the bidirectional pair: an LF-only match text loses just its `\n` (this test),
        while `test_should_preserve_a_genuine_trailing_cr_from_a_crlf_rust_backend_match`
        below proves a real `\r` immediately before it survives.
        """
        log = tmp_path / "rust_newlines.log"
        log.write_text("apple\nbanana\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FakeRustBackend:
            def search(self, **kwargs):
                return [(1, "apple\n")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "apple")

        assert result.total_matches == 1
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "apple"
        assert result.routing_backend == "CPUBackend"

    def test_should_preserve_a_genuine_trailing_cr_from_a_crlf_rust_backend_match(self, tmp_path):
        """The other half of the task #262 bidirectional fix -- see the sibling test above.
        Rust's own line-splitter keeps a CRLF line's trailing `\r` (only the `\n` record
        separator is removed by the split itself); the fake backend below mirrors that real
        shape. This must NOT be corrupted into "apple" (over-stripped) OR "apple\r\r"
        (doubled).
        """
        log = tmp_path / "rust_newlines_crlf.log"
        log.write_text("apple\nbanana\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FakeRustBackend:
            def search(self, **kwargs):
                return [(1, "apple\r\n")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "apple")

        assert result.total_matches == 1
        assert result.matches[0].line_number == 1
        assert result.matches[0].text == "apple\r"
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex"

    def test_should_honor_max_count_on_rust_backend_fast_path(self, tmp_path):
        log = tmp_path / "rust_max_count.log"
        log.write_text("apple\napple banana\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FakeRustBackend:
            def search(self, **kwargs):
                return [(1, "apple"), (2, "apple banana")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "apple", config=SearchConfig(max_count=1))

        assert result.total_matches == 1
        assert result.total_files == 1
        assert [(match.line_number, match.text) for match in result.matches] == [(1, "apple")]
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex"

    def test_should_route_context_searches_through_the_rust_match_set(self, tmp_path):
        # Audit #6 (ReDoS gate bypass) fix: -C/-A/-B now route the MATCH-SET through the
        # linear-time Rust engine (context windows are assembled in pure Python around it)
        # instead of unconditionally falling to Python's unbounded backtracking `re`.
        log = tmp_path / "rust_context.log"
        # newline="\n": see the comment on test_should_includeAfterContext_when_dashA_isProvided
        # above -- this fixture feeds _assemble_context_matches, which reads the REAL file.
        log.write_text("before\napple\nafter\n", encoding="utf-8", newline="\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")
        calls = []

        class FakeRustBackend:
            def search(self, **kwargs):
                calls.append(kwargs["pattern"])
                assert kwargs["pattern"] == "apple"
                return [(2, "apple")]

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "apple", config=SearchConfig(context=1))

        assert calls == ["apple"]  # Rust WAS invoked -- no Python-re fallback
        assert [(match.line_number, match.text) for match in result.matches] == [
            (1, "before"),
            (2, "apple"),
            (3, "after"),
        ]
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex_context"

    def test_should_fail_closed_when_context_search_cannot_use_rust(self, tmp_path):
        # THE RESIDUAL (audit #16): Rust genuinely absent must fail closed for -C, not fall
        # open to the unbounded Python backtracking engine.
        log = tmp_path / "rust_context_absent.log"
        log.write_text("before\napple\nafter\n", encoding="utf-8")

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            with pytest.raises(BackendExecutionError):
                backend.search(str(log), "apple", config=SearchConfig(context=1))

    def test_should_fail_closed_when_context_search_hits_generic_rust_failure(self, tmp_path):
        log = tmp_path / "rust_context_fail.log"
        log.write_text("before\napple\nafter\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FailingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("native panic")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                backend.search(str(log), "apple", config=SearchConfig(context=1))

    def test_should_match_word_regexp_via_rust_match_set(self, tmp_path):
        log = tmp_path / "word.log"
        log.write_text("cat\nconcatenate\nscatter cat here\n", encoding="utf-8")

        backend = CPUBackend()
        result = backend.search(str(log), "cat", config=SearchConfig(word_regexp=True))

        assert [m.line_number for m in result.matches] == [1, 3]
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex"

    def test_should_match_line_regexp_via_rust_match_set(self, tmp_path):
        log = tmp_path / "line.log"
        log.write_text("cat\ncat dog\nCAT\n", encoding="utf-8")

        backend = CPUBackend()
        result = backend.search(str(log), "cat", config=SearchConfig(line_regexp=True))

        assert [m.line_number for m in result.matches] == [1]
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_rust_regex"

    def test_should_combine_word_regexp_with_context_via_rust(self, tmp_path):
        log = tmp_path / "word_context.log"
        # newline="\n": same reasoning as the sibling context-fixture comments above.
        log.write_text("before\ncat\nconcatenate\nafter\n", encoding="utf-8", newline="\n")

        backend = CPUBackend()
        result = backend.search(
            str(log), "cat", config=SearchConfig(word_regexp=True, after_context=1)
        )

        assert [(m.line_number, m.text) for m in result.matches] == [
            (2, "cat"),
            (3, "concatenate"),
        ]
        assert result.routing_reason == "cpu_rust_regex_context"

    def test_should_fail_closed_for_word_regexp_when_rust_unavailable(self, tmp_path):
        log = tmp_path / "word_absent.log"
        log.write_text("cat\nconcatenate\n", encoding="utf-8")

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            with pytest.raises(BackendExecutionError):
                backend.search(str(log), "cat", config=SearchConfig(word_regexp=True))

    def test_should_match_ltl_eventually_sequence_when_ordered(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "ltl.log"
        log.write_text("INFO boot\nAUTH_FAIL user=a\nINFO retry\nDB_TIMEOUT after auth\n")

        backend = CPUBackend()
        config = SearchConfig(ltl=True)
        result = backend.search(str(log), r"AUTH_FAIL -> eventually DB_TIMEOUT", config=config)

        assert result.total_matches == 1
        assert [m.line_number for m in result.matches] == [2, 4]
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_ltl_python"

    def test_should_emit_python_fallback_routing_metadata_when_rust_fails(self, tmp_path):
        log = tmp_path / "python_fallback.log"
        log.write_text("ERROR one\nINFO two\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "ERROR", config=SearchConfig())

        assert result.total_matches == 1
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason in {
            "cpu_python_regex",
            "cpu_python_regex_prefilter",
            "cpu_python_regex_prefilter_cache",
        }

    def test_should_report_total_files_for_count_mode_without_materialized_matches(self, tmp_path):
        log = tmp_path / "count_mode.log"
        log.write_text("ERROR one\nERROR two\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), "ERROR", config=SearchConfig(count=True))

        assert result.total_matches == 2
        assert result.total_files == 1
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason in {
            "cpu_python_regex",
            "cpu_python_regex_prefilter",
            "cpu_python_regex_prefilter_cache",
        }

    def test_should_not_match_ltl_when_order_is_wrong(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "ltl_wrong_order.log"
        log.write_text("DB_TIMEOUT first\nAUTH_FAIL second\n")

        backend = CPUBackend()
        config = SearchConfig(ltl=True)
        result = backend.search(str(log), r"AUTH_FAIL -> eventually DB_TIMEOUT", config=config)

        assert result.total_matches == 0
        assert result.matches == []
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_ltl_python"

    def test_should_error_for_unsupported_ltl_syntax(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "ltl_invalid.log"
        log.write_text("A\nB\n")

        backend = CPUBackend()
        config = SearchConfig(ltl=True)

        try:
            backend.search(str(log), "A UNTIL B", config=config)
            raise AssertionError("Expected ValueError for invalid LTL expression")
        except ValueError as exc:
            assert "Unsupported LTL query" in str(exc)

    def test_should_route_ltl_sub_expressions_through_rust_match_sets(self, tmp_path):
        # Audit #6 fix: --ltl now resolves both sub-expressions via the linear-time Rust
        # engine's match-set instead of Python's backtracking `re.search()` per line.
        log = tmp_path / "ltl_rust.log"
        log.write_text("INFO boot\nAUTH_FAIL user=a\nINFO retry\nDB_TIMEOUT after auth\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")
        seen_patterns = []

        class FakeRustBackend:
            def search(self, **kwargs):
                seen_patterns.append(kwargs["pattern"])
                if kwargs["pattern"] == "AUTH_FAIL":
                    return [(2, "AUTH_FAIL user=a")]
                if kwargs["pattern"] == "DB_TIMEOUT":
                    return [(4, "DB_TIMEOUT after auth")]
                return []

        rust_mod.RustBackend = FakeRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(
                str(log), "AUTH_FAIL -> eventually DB_TIMEOUT", config=SearchConfig(ltl=True)
            )

        assert seen_patterns == ["AUTH_FAIL", "DB_TIMEOUT"]  # Rust WAS invoked, twice
        assert result.total_matches == 1
        assert [m.line_number for m in result.matches] == [2, 4]

    def test_should_fail_closed_when_ltl_search_cannot_use_rust(self, tmp_path):
        # THE RESIDUAL (audit #16): Rust genuinely absent must fail closed for --ltl, not fall
        # open to the unbounded Python backtracking engine.
        log = tmp_path / "ltl_absent.log"
        log.write_text("AUTH_FAIL user=a\nDB_TIMEOUT after auth\n")

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            with pytest.raises(BackendExecutionError):
                backend.search(
                    str(log), "AUTH_FAIL -> eventually DB_TIMEOUT", config=SearchConfig(ltl=True)
                )

    def test_should_fail_closed_when_ltl_search_hits_generic_rust_failure(self, tmp_path):
        log = tmp_path / "ltl_fail.log"
        log.write_text("AUTH_FAIL user=a\nDB_TIMEOUT after auth\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FailingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("native panic")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                backend.search(
                    str(log), "AUTH_FAIL -> eventually DB_TIMEOUT", config=SearchConfig(ltl=True)
                )

    def test_should_suppress_non_fatal_regex_futurewarnings_in_python_fallback(self, tmp_path):
        from tensor_grep.core.config import SearchConfig

        log = tmp_path / "warning_regex.log"
        log.write_text("literal [text]\n")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with (
            patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}),
            warnings.catch_warnings(record=True) as captured,
        ):
            warnings.simplefilter("always")
            result = backend.search(str(log), "[[]", config=SearchConfig())

        assert result.total_matches == 1
        assert not any(isinstance(warning.message, FutureWarning) for warning in captured)

    def test_should_use_literal_prefilter_for_safe_python_regex_fallback(self, tmp_path):
        log = tmp_path / "prefilter.log"
        log.write_text("INFO ok\nERROR x timeout\nWARN no\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), r"ERROR.*timeout", config=SearchConfig())

        assert result.total_matches == 1
        assert result.matches[0].line_number == 2
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "cpu_python_regex_prefilter"

    def test_should_reuse_literal_prefilter_index_across_backend_instances(self, tmp_path):
        log = tmp_path / "prefilter_cache.log"
        log.write_text("INFO ok\nERROR x timeout\nWARN no\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            first = CPUBackend().search(str(log), r"ERROR.*timeout", config=SearchConfig())
            assert first.total_matches == 1

            backend_two = CPUBackend()

            def fail_build(*_args, **_kwargs):
                raise AssertionError("should not rebuild literal prefilter index on cache hit")

            backend_two._build_line_trigram_index = fail_build  # type: ignore[method-assign]
            second = backend_two.search(str(log), r"ERROR.*timeout", config=SearchConfig())

        assert second.total_matches == 1
        assert second.routing_reason == "cpu_python_regex_prefilter_cache"

    def test_should_not_use_literal_prefilter_for_unsafe_regex_constructs(self, tmp_path):
        log = tmp_path / "unsafe_prefilter.log"
        log.write_text("foo\nbar\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), r"foo|bar", config=SearchConfig())

        assert result.total_matches == 2
        assert result.routing_reason == "cpu_python_regex"

    def test_should_reuse_persistent_literal_prefilter_cache_across_instances(
        self, tmp_path, monkeypatch
    ):
        cache_dir = tmp_path / "cpu-prefilter-cache"
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX_DIR", str(cache_dir))
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "1")
        CPUBackend._clear_shared_caches()

        log = tmp_path / "persistent_prefilter.log"
        log.write_text("INFO ok\nERROR x timeout\nWARN no\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            first = CPUBackend().search(str(log), r"ERROR.*timeout", config=SearchConfig())
            assert first.total_matches == 1
            assert first.routing_reason == "cpu_python_regex_prefilter"

            CPUBackend._clear_shared_caches()
            backend_two = CPUBackend()

            def fail_build(*_args, **_kwargs):
                raise AssertionError("should not rebuild literal prefilter index from disk cache")

            backend_two._build_line_trigram_index = fail_build  # type: ignore[method-assign]
            second = backend_two.search(str(log), r"ERROR.*timeout", config=SearchConfig())

        assert second.total_matches == 1
        assert second.routing_reason == "cpu_python_regex_prefilter_cache"

    def test_should_invalidate_persistent_literal_prefilter_cache_when_file_changes(
        self, tmp_path, monkeypatch
    ):
        cache_dir = tmp_path / "cpu-prefilter-cache"
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX_DIR", str(cache_dir))
        monkeypatch.setenv("TENSOR_GREP_CPU_REGEX_INDEX", "1")
        CPUBackend._clear_shared_caches()

        log = tmp_path / "persistent_prefilter_invalidation.log"
        log.write_text("INFO ok\nERROR x timeout\n", encoding="utf-8")

        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend

        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            first = CPUBackend().search(str(log), r"ERROR.*timeout", config=SearchConfig())
            assert first.total_matches == 1

            log.write_text("INFO ok\nWARN timeout\n", encoding="utf-8")
            CPUBackend._clear_shared_caches()

            backend_two = CPUBackend()
            build_calls = {"count": 0}
            original_build = backend_two._build_line_trigram_index

            def wrapped_build(lines):
                build_calls["count"] += 1
                return original_build(lines)

            backend_two._build_line_trigram_index = wrapped_build  # type: ignore[method-assign]
            second = backend_two.search(str(log), r"WARN.*timeout", config=SearchConfig())

        assert second.total_matches == 1
        assert second.routing_reason == "cpu_python_regex_prefilter"
        assert build_calls["count"] == 1

    # --- Round-4: literal-prefilter must not fold the optional (*-quantified) atom ---

    def test_extract_required_literal_excludes_optional_star_atom(self):
        # "colou*r" matches "color" (zero u's); the required substring is "colo", not "colou".
        assert CPUBackend._extract_required_literal("colou*r") == "colo"

    def test_star_prefilter_does_not_silently_drop_zero_repetition_match(self, tmp_path):
        # End-to-end: "color" legitimately matches r"colou*r"; the prefilter must not exclude it.
        log = tmp_path / "star.log"
        log.write_text("the color is red\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend
        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), r"colou*r", config=SearchConfig())

        assert result.total_matches == 1
        assert result.matches[0].line_number == 1

    def test_star_prefilter_pops_only_the_optional_atom_not_the_run(self, tmp_path):
        # "flagok" (zero x's) matches r"flagx*ok"; surviving literal is the truncated "flag"
        # (not the buggy "flagx", and not emptied out entirely).
        log = tmp_path / "run.log"
        log.write_text("flagok\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend
        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), r"flagx*ok", config=SearchConfig())

        assert result.total_matches == 1

    def test_star_prefilter_still_filters_decoys_and_guards_leading_star(self, tmp_path):
        # The surviving literal ("worke") must still exclude a decoy line (prefilter not degraded
        # into "scan everything"); and a leading-'*' pattern must not raise IndexError.
        log = tmp_path / "decoy.log"
        log.write_text("workers\nunrelated line\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        # Audit #111 (Opus-gate hardening): the Python prefilter loop is reachable for a
        # non-fixed-strings pattern ONLY when the native engine is genuinely ABSENT (a
        # present-but-failing Rust now fails closed). Simulate absence via ImportError so this
        # prefilter test stays on a currently-reachable path.
        class FailingRustBackend:
            def search(self, **_kwargs):
                raise ImportError("simulated rust_core absent")

        rust_mod.RustBackend = FailingRustBackend
        backend = CPUBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = backend.search(str(log), r"worker*s", config=SearchConfig())
            assert result.total_matches == 1
            assert result.matches[0].line_number == 1
            # empty-`current` guard: leading '*' must not IndexError.
            guarded = backend.search(str(log), r".*abc", config=SearchConfig())
        assert guarded.total_matches == 0

    # --- Round-4: fail closed (no silent ReDoS-prone Python-re swap) on Rust syntax rejection ---

    def test_should_fail_closed_when_rust_rejects_backreference_syntax(self, tmp_path):
        import pytest

        from tensor_grep.backends.cpu_backend import InvalidRegexError

        f = tmp_path / "x.txt"
        f.write_text("a" * 40 + "!\n", encoding="utf-8")  # catastrophic-backtracking payload
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class RejectingRustBackend:
            def search(self, **_kwargs):
                # The Rust `regex` crate rejects look-around/backreferences at COMPILE time.
                raise RuntimeError("regex parse error: look-around is not supported")

        rust_mod.RustBackend = RejectingRustBackend
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(InvalidRegexError):
                CPUBackend().search(str(f), r"(?=(a+)+)$", config=SearchConfig())

    def test_should_fail_closed_on_nonsyntax_rust_runtime_failure_for_regex(self, tmp_path):
        # Audit #111 Opus-gate hardening (must-fix #2): a present-but-failing Rust (non-syntax
        # runtime fault, NOT a syntax rejection, NOT --pcre2) must NOT fall open to unbounded
        # Python `re` for an arbitrary pattern -- a hazard pattern would then backtrack unbounded.
        # Fail CLOSED, matching the -w/-x/-C/--ltl/--pcre2 siblings. (Before this fix it returned
        # a match here; the availability of that fall-open was the ReDoS hole.)
        f = tmp_path / "x.txt"
        f.write_text("ERROR here\nno match\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FailingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("native panic, unrelated to pattern syntax")

        rust_mod.RustBackend = FailingRustBackend
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                CPUBackend().search(str(f), "ERROR", config=SearchConfig())

    def test_should_still_fall_open_on_nonsyntax_rust_failure_for_fixed_strings(self, tmp_path):
        # The legitimate robustness case is preserved for the ONE provably-safe shape:
        # fixed_strings is re.escape'd -> a literal automaton -> cannot catastrophically
        # backtrack, so a transient Rust runtime fault may safely fall open to Python here.
        f = tmp_path / "x.txt"
        f.write_text("ERROR here\nno match\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FailingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("native panic, unrelated to pattern syntax")

        rust_mod.RustBackend = FailingRustBackend
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            result = CPUBackend().search(str(f), "ERROR", config=SearchConfig(fixed_strings=True))
        assert result.total_matches == 1
        assert result.routing_reason.startswith("cpu_python_regex")

    def test_should_fail_closed_when_pcre2_backreference_cannot_run_through_rust(self, tmp_path):
        # Audit #16: --pcre2 is a "Python-re-is-unavoidable" residual. CPUBackend has no real
        # PCRE2 engine -- only Python `re` as a backtracking approximation -- so a pattern Rust
        # cannot compile must now fail closed (BackendExecutionError) instead of silently
        # running through the ReDoS-hazardous Python fallback. (Real PCRE2 semantics are
        # available through ripgrep itself, which this refusal message points users at.)
        f = tmp_path / "x.txt"
        f.write_text("aa bb\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class RejectingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("regex parse error: backreferences are not supported")

        rust_mod.RustBackend = RejectingRustBackend
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                CPUBackend().search(str(f), r"(a)\1", config=SearchConfig(pcre2=True))

    def test_should_fail_closed_when_pcre2_hits_generic_rust_failure(self, tmp_path):
        # The --pcre2 residual is fail-closed regardless of WHY Rust could not service the
        # request -- not just a syntax rejection, per audit #16 (the old "Rust accepted syntax
        # so it's safe" premise does not hold for a generic runtime failure either).
        f = tmp_path / "x.txt"
        f.write_text("aa bb\n", encoding="utf-8")
        rust_mod = types.ModuleType("tensor_grep.rust_core")

        class FailingRustBackend:
            def search(self, **_kwargs):
                raise RuntimeError("native panic, unrelated to pattern syntax")

        rust_mod.RustBackend = FailingRustBackend
        with patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}):
            with pytest.raises(BackendExecutionError):
                CPUBackend().search(str(f), "aa", config=SearchConfig(pcre2=True))


def test_max_count_zero_returns_no_matches_on_pure_python_path(tmp_path):
    """`--max-count 0` means ZERO matches (ripgrep's contract). The pure-Python loop checks the cap
    AFTER appending and `config.max_count and ...` treats 0 as falsy, so before the guard `-m 0` on
    the context-forced pure-Python path emitted every match. after_context forces that path."""
    log = tmp_path / "app.log"
    log.write_text(
        "ERROR one\nplain a\nERROR two\nplain b\nERROR three\nplain c\n", encoding="utf-8"
    )
    backend = CPUBackend()

    zero = backend.search(str(log), "ERROR", config=SearchConfig(max_count=0, after_context=1))
    assert zero.total_matches == 0
    assert zero.matches == []
    assert zero.routing_reason == "cpu_max_count_zero"

    # Regression: a positive cap still returns exactly that many pattern matches (the guard only
    # short-circuits max_count == 0; max_count > 0 keeps flowing through the normal loop).
    capped = backend.search(str(log), "ERROR", config=SearchConfig(max_count=2, after_context=1))
    assert capped.total_matches == 2


def test_max_count_zero_returns_no_matches_on_ltl_path(tmp_path):
    """The LTL/sequence path (reached via search() -> _search_ltl) shares the same search()-entry
    guard, so `-m 0` on an LTL query is also zero, not one-sequence."""
    log = tmp_path / "seq.log"
    log.write_text("alpha here\nbeta here\nalpha again\nbeta again\n", encoding="utf-8")
    backend = CPUBackend()

    zero = backend.search(str(log), "alpha ~> beta", config=SearchConfig(ltl=True, max_count=0))
    assert zero.total_matches == 0
    assert zero.routing_reason == "cpu_max_count_zero"


# --- Audit #6 + #16: ReDoS-gate bypass regression -----------------------------------------
#
# `(a+)+$` is a classic catastrophic-backtracking payload for a BACKTRACKING regex engine
# (nested quantifiers): under Python's `re`, searching it against a long run of "a"s followed
# by a non-matching character can take exponential time. It is, however, perfectly valid Rust
# `regex` crate syntax that Rust's automata engine runs in guaranteed O(n) -- so these cases
# must EITHER complete quickly via the linear-time Rust engine (the common case, Rust present)
# OR raise `BackendExecutionError` (the fail-closed residual) -- and must NEVER hang. Each test
# wall-clock-bounds the call; a hang manifests as a test timeout, not a silent pass.
_HAZARD_PATTERN = r"(a+)+$"
_HAZARD_BOUND_SECONDS = 2.0


def _run_hazard_pattern_bounded(backend, log_path, config):
    start = time.perf_counter()
    try:
        backend.search(str(log_path), _HAZARD_PATTERN, config=config)
    except BackendExecutionError:
        pass  # fail-closed residual is an acceptable, bounded outcome
    elapsed = time.perf_counter() - start
    assert elapsed < _HAZARD_BOUND_SECONDS, (
        f"hazard pattern took {elapsed:.2f}s (must be < {_HAZARD_BOUND_SECONDS}s, never hang)"
    )


def test_ltl_hazard_pattern_is_bounded_not_hung(tmp_path):
    log = tmp_path / "ltl_hazard.log"
    log.write_text("a" * 40 + "!\nDONE\n", encoding="utf-8")
    backend = CPUBackend()
    config = SearchConfig(ltl=True)
    start = time.perf_counter()
    try:
        backend.search(str(log), f"{_HAZARD_PATTERN} -> eventually DONE", config=config)
    except BackendExecutionError:
        pass
    elapsed = time.perf_counter() - start
    assert elapsed < _HAZARD_BOUND_SECONDS


def test_word_regexp_hazard_pattern_is_bounded_not_hung(tmp_path):
    log = tmp_path / "word_hazard.log"
    log.write_text("a" * 40 + "!\n", encoding="utf-8")
    _run_hazard_pattern_bounded(CPUBackend(), log, SearchConfig(word_regexp=True))


def test_line_regexp_hazard_pattern_is_bounded_not_hung(tmp_path):
    log = tmp_path / "line_hazard.log"
    log.write_text("a" * 40 + "!\n", encoding="utf-8")
    _run_hazard_pattern_bounded(CPUBackend(), log, SearchConfig(line_regexp=True))


def test_context_hazard_pattern_is_bounded_not_hung(tmp_path):
    log = tmp_path / "context_hazard.log"
    log.write_text("a" * 40 + "!\n", encoding="utf-8")
    _run_hazard_pattern_bounded(CPUBackend(), log, SearchConfig(context=2))


def test_pcre2_hazard_pattern_alone_is_bounded_not_hung(tmp_path):
    log = tmp_path / "pcre2_hazard.log"
    log.write_text("a" * 40 + "!\n", encoding="utf-8")
    _run_hazard_pattern_bounded(CPUBackend(), log, SearchConfig(pcre2=True))


def test_context_and_word_regexp_combined_hazard_pattern_is_bounded_not_hung(tmp_path):
    log = tmp_path / "combo_hazard.log"
    log.write_text("a" * 40 + "!\n", encoding="utf-8")
    _run_hazard_pattern_bounded(
        CPUBackend(), log, SearchConfig(word_regexp=True, context=2, pcre2=True)
    )


# --- Audit #111 + Opus-gate hardening: UTF-8-fallback / native-failure ReDoS gate --------------
#
# `cpu_backend.py`'s "simple pattern" path attempts the linear-time Rust engine first. Two of its
# residual paths used to fall through to raw, unbounded Python `re.search()`: (1) the
# _RustUtf8DecodeMismatch branch (Rust returned empty on a non-UTF-8 file), on the premise "Rust
# already ran the pattern in O(n), so it's ReDoS-safe"; and (2) the generic `except Exception`
# branch (a non-syntax Rust runtime fault), "for robustness". Both premises are refuted -- the
# same way `--pcre2` was: a pattern Rust runs in guaranteed linear time can still catastrophically
# backtrack under Python's backtracking engine.
#
# Catastrophic backtracking has TWO independent sources, so NO static pattern check is a sound
# gate (the Opus security gate proved this by breaking an earlier "no `*+?{` quantifier char"
# heuristic):
#   * nested quantifiers -- `(a+)+$`
#   * variable-length ALTERNATION -- `(a|aa)(a|aa)...(a|aa)b` (i.e. `"(a|aa)"*k + "b"`) backtracks
#     2^k with NO quantifier metacharacter at all.
# The fix admits ONLY `fixed_strings` (re.escape'd -> a literal automaton -> provably linear) to
# the Python fallback; every other pattern fails CLOSED (BackendExecutionError), matching the
# -w/-x/-C/--ltl/--pcre2 siblings. The two bomb tests below assert both sources fail closed.
#
# A REAL SUBPROCESS bounds these tests (anti-hang-test-protocol), not a same-process thread
# watchdog. Python's `_sre` backtracking engine holds the GIL for the ENTIRE duration of a single
# `re.search()` call and never yields it mid-match, so a sibling thread stuck in catastrophic
# backtracking can ALSO prevent the main thread's own `Thread.join(timeout=...)` wait from waking
# up on schedule (empirically confirmed: an earlier in-process `Thread.join(timeout=2.0)` version
# of the nested-quantifier test itself hung well past a 30s outer shell timeout against unfixed
# code -- the "thread-based timeout mechanism" the anti-hang-test-protocol generally recommends is
# NOT sufficient for this GIL-monopolizing hang class). A real OS process can always be
# force-terminated externally regardless of its GIL state: `subprocess.run(..., timeout=...)`
# kills the child via `Popen.kill()` (`TerminateProcess` on Windows, `SIGKILL` on POSIX).
_REDOS_SUBPROCESS_BOUND_SECONDS = 15.0
_REDOS_SUBPROCESS_SNIPPET = (
    "import sys\n"
    "from tensor_grep.backends.base import BackendExecutionError\n"
    "from tensor_grep.backends.cpu_backend import CPUBackend\n"
    "from tensor_grep.core.config import SearchConfig\n"
    "try:\n"
    "    CPUBackend().search(sys.argv[1], sys.argv[2], config=SearchConfig(text=True))\n"
    "except BackendExecutionError:\n"
    "    print('FAIL_CLOSED')\n"
    "    sys.exit(0)\n"
    "print('NO_ERROR_RAISED')\n"
)
# Trailing bytes that make a file invalid UTF-8 (bare continuation bytes, no NUL -> not treated as
# binary), so `Path.read_text('utf-8')` raises and the _RustUtf8DecodeMismatch branch is reached.
_NON_UTF8_TAIL = b"!" + b"\x80\x81\xfe"


def _assert_search_fails_closed_bounded(tmp_path, name, file_bytes, pattern):
    """Run `CPUBackend().search(pattern)` on a non-UTF-8 file in a CHILD PROCESS bounded by an
    OS-level timeout; assert it fails closed (`BackendExecutionError`) rather than hanging.

    The child inherits PYTHONPATH/VIRTUAL_ENV from the current interpreter, so it resolves the
    same `tensor_grep` (and the same compiled `rust_core`) this test file imports.
    """
    f = tmp_path / name
    f.write_bytes(file_bytes)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _REDOS_SUBPROCESS_SNIPPET, str(f), pattern],
            capture_output=True,
            text=True,
            timeout=_REDOS_SUBPROCESS_BOUND_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"pattern {pattern!r} on a non-UTF-8 file did not return within "
            f"{_REDOS_SUBPROCESS_BOUND_SECONDS}s -- the audit #111 ReDoS gate failed to fail "
            "closed and instead hung"
        )
    assert "FAIL_CLOSED" in proc.stdout, (
        f"expected a fail-closed BackendExecutionError for {pattern!r}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_should_fail_closed_for_nested_quantifier_bomb_on_non_utf8_file(tmp_path):
    """RED->GREEN regression for audit #111: the classic nested-quantifier catastrophic-
    backtracking payload `(a+)+$` on a non-UTF-8 file must fail closed, bounded, never hung.
    """
    _assert_search_fails_closed_bounded(
        tmp_path, "nested_q_bomb.bin", b"a" * 45 + _NON_UTF8_TAIL, _HAZARD_PATTERN
    )


def test_should_fail_closed_for_alternation_bomb_on_non_utf8_file(tmp_path):
    """Opus-gate counterexample (must-fix #3): variable-length ALTERNATION
    `(a|aa)...(a|aa)b` backtracks 2^k under Python `re` with NO quantifier metacharacter --
    the exact bomb that broke the earlier "no `*+?{` char" static allow-list. Must fail closed
    exactly like `(a+)+$`. k=26 backtracks ~25s unfixed (measured k=24 -> 6.19s), well past the
    15s bound, so a regressed gate manifests as a subprocess timeout, not a silent pass.
    """
    alternation_bomb = "(a|aa)" * 26 + "b"
    _assert_search_fails_closed_bounded(
        tmp_path, "alternation_bomb.bin", b"a" * 52 + _NON_UTF8_TAIL, alternation_bomb
    )


def test_fixed_strings_nonascii_literal_matches_via_python_fallback_but_regex_fails_closed(
    tmp_path,
):
    """Semantics preservation + the structural contrast the audit #111 gate enforces.

    The ONE legitimate case the UTF-8 fallback exists for: a non-ASCII LITERAL that Rust (which
    searches raw bytes with a UTF-8-encoded pattern) cannot match against a latin-1-encoded file,
    but Python CAN after decoding. This genuinely exercises the fallback (Rust returns empty ->
    _RustUtf8DecodeMismatch), and with `fixed_strings` (re.escape'd -> provably linear) the gate
    admits it and the latin-1 match succeeds.

    The SAME literal WITHOUT `fixed_strings` fails closed -- the gate is structural (admits only
    fixed_strings), it does NOT try to statically classify a pattern as hazardous-or-not (that is
    unsound). This documents the accepted security-over-availability trade: a legit non-ASCII
    regex on a non-UTF-8 file is refused, not silently run through the backtracking engine. Both
    calls are bounded literals -> safe to run in-process (no hang risk for a benign pattern).
    """
    f = tmp_path / "latin1_cafe.bin"
    # "cafe" + e-acute (U+00E9). Built with chr(0xE9) so this source file stays strictly ASCII.
    # Encoded latin-1 the accented char is the single byte 0xE9, which alone is invalid UTF-8 ->
    # forces the _RustUtf8DecodeMismatch fallback (Rust searches the UTF-8-encoded pattern bytes
    # 0xC3 0xA9 and misses the file's single 0xE9 byte).
    literal = "caf" + chr(0xE9)
    f.write_bytes(("ERROR " + literal + " here").encode("latin-1") + b"\n")

    matched = CPUBackend().search(str(f), literal, config=SearchConfig(fixed_strings=True))
    assert matched.total_matches == 1
    assert matched.routing_reason.startswith("cpu_python_regex")

    with pytest.raises(BackendExecutionError):
        CPUBackend().search(str(f), literal, config=SearchConfig())


def test_python_fallback_regex_engine_failure_is_not_clean_no_match(tmp_path):
    """Sol FIX-FIRST (HANDLER-CENSUS-W2-a): regex_str.search failures must not look like no-match.

    Pre-harden, utf-8/latin-1 match-test arms wrapped decode+search in one ``except Exception:
    pass``, so a terminal regex engine error became ``matched=False`` and a clean empty
    ``SearchResult``. Both topology arms (source_lines prefilter + streaming open) must raise.
    """

    class _BoomPattern:
        def search(self, _text):
            raise RuntimeError("injected regex engine failure")

    def _boom_compile(pattern, flags, config):
        boom = _BoomPattern()
        return boom, boom

    rust_mod = types.ModuleType("tensor_grep.rust_core")

    class AbsentRustBackend:
        def search(self, **_kwargs):
            raise ImportError("simulated rust_core absent")

    rust_mod.RustBackend = AbsentRustBackend

    target = tmp_path / "probe.txt"
    target.write_text("ERROR one\nERROR two\n", encoding="utf-8")

    with (
        patch.dict("sys.modules", {"tensor_grep.rust_core": rust_mod}),
        patch.object(CPUBackend, "_compile_regexes", staticmethod(_boom_compile)),
    ):
        backend = CPUBackend()
        # Streaming arm: fixed_strings disables the source_lines prefilter.
        with pytest.raises(RuntimeError, match="CPU backend search failed"):
            backend.search(str(target), "ERROR", config=SearchConfig(fixed_strings=True))
        # source_lines / prefilter arm: literal extractable pattern, not fixed_strings.
        with pytest.raises(RuntimeError, match="CPU backend search failed"):
            backend.search(str(target), "ERROR", config=SearchConfig())
