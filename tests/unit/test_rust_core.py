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
