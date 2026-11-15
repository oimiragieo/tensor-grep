from pathlib import Path

import pytest

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.citations import result_citations
from tensor_grep.core.config import SearchConfig


def test_cpu_regex_match_is_citable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "sample.py"
    source.write_text("foo_here\n", encoding="utf-8")
    backend = CPUBackend()
    monkeypatch.setattr(backend, "_rust_match_set", lambda *_args, **_kwargs: [(1, "foo_here")])

    result = backend.search(str(source), r"foo_here|bar_baz")
    envelope = result_citations(
        result, tmp_path, query_pattern=r"foo_here|bar_baz", fixed_strings=False
    )

    assert envelope["citations"] == [{"path": "sample.py", "start_line": 1, "end_line": 1}]
    assert envelope["omitted"] == 0


def test_cpu_context_is_not_cited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "sample.py"
    source.write_text("before\nfoo_here\nafter\n", encoding="utf-8")
    backend = CPUBackend()
    monkeypatch.setattr(backend, "_rust_match_set", lambda *_args, **_kwargs: [(2, "foo_here")])

    result = backend.search(str(source), r"foo_here|bar_baz", config=SearchConfig(after_context=1))
    envelope = result_citations(
        result, tmp_path, query_pattern=r"foo_here|bar_baz", fixed_strings=False
    )

    assert envelope["citations"] == [{"path": "sample.py", "start_line": 2, "end_line": 2}]
    assert envelope["omitted"] == 0
