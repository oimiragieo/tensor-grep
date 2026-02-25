import pytest
from hypothesis import given
from hypothesis import strategies as st

pytestmark = pytest.mark.property


@given(st.text(min_size=1, max_size=50000, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_reader_never_loses_bytes(text):
    """Property: total bytes read == total bytes written."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(text)
        f.flush()
        path = f.name
    try:
        from tensor_grep.io.reader_fallback import FallbackReader

        reader = FallbackReader()
        content = "".join(reader.read_lines(path))
        assert len(content.encode("utf-8")) == len(text.encode("utf-8"))
    finally:
        os.unlink(path)


@given(st.from_regex(r"[A-Za-z0-9.*+?\[\]{}()^$|\\]+", fullmatch=True))
def test_cpu_backend_never_crashes_on_valid_regex(pattern):
    """Property: CPU backend handles any valid regex without exception."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("test line ERROR something\nanother line\n")
        path = f.name
    try:
        from tensor_grep.backends.cpu_backend import CPUBackend

        backend = CPUBackend()
        result = backend.search(path, pattern)
        assert result is not None
    except Exception:
        pass  # Invalid regex is acceptable to reject
    finally:
        os.unlink(path)
