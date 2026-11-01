"""Real extension outcome check; ordinary CLI PCRE2 uses a different route."""

from pathlib import Path

import pytest

from tensor_grep.backends import rust_backend
from tensor_grep.backends.base import PassthroughExecutionError
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.config import SearchConfig


@pytest.mark.skipif(not rust_backend.HAVE_RUST, reason="compiled extension unavailable")
@pytest.mark.skipif(resolve_ripgrep_binary() is None, reason="ripgrep unavailable")
def test_real_bridge_error_is_not_an_empty_success(tmp_path: Path):
    source = tmp_path / "data.txt"
    source.write_text("ordinary text\n", encoding="utf-8")
    backend = rust_backend.RustCoreBackend()
    config = SearchConfig(no_ignore_vcs=True)
    assert not backend.search(str(source), "ordinary", config).is_empty
    assert backend.search(str(source), "absent", config).is_empty
    with pytest.raises(PassthroughExecutionError, match="exit status 2"):
        backend.search(str(source), "[", config)
