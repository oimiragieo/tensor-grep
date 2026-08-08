"""M8 audit: `--ast -v` / `--ast -w` used to SILENTLY drop invert-match / word-regexp on both
AST backends (returning the non-inverted match set as if asked), and ast_backend's persistent
cache key omitted those flags (a cached non-inverted result could serve an inverted query).
Both backends now FAIL CLOSED with a clear error -- never silently return the wrong set.

RED first: each assertion targets a pre-fix behavior (pre-fix the -v search returned matches;
the cache key ignored the flag; pre-fix no error was raised).
"""

from __future__ import annotations

import pytest

from tensor_grep.backends.ast_backend import AstBackend
from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig


def test_ast_backend_refuses_invert_match(monkeypatch) -> None:
    backend = AstBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    with pytest.raises(BackendExecutionError, match="invert-match"):
        backend.search("a.py", "function($A)", SearchConfig(invert_match=True))


def test_ast_backend_refuses_word_regexp(monkeypatch) -> None:
    backend = AstBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    with pytest.raises(BackendExecutionError, match="word-regexp"):
        backend.search("a.py", "function($A)", SearchConfig(word_regexp=True))


def test_ast_wrapper_refuses_invert_match_on_search(monkeypatch) -> None:
    backend = AstGrepWrapperBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    with pytest.raises(BackendExecutionError, match="invert-match"):
        backend.search("a.py", "function($A)", SearchConfig(invert_match=True))


def test_ast_wrapper_refuses_invert_match_on_search_many(monkeypatch) -> None:
    backend = AstGrepWrapperBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    with pytest.raises(BackendExecutionError, match="invert-match"):
        backend.search_many(["a.py"], "function($A)", SearchConfig(invert_match=True))


def test_ast_cache_key_includes_match_flags() -> None:
    """Belt+braces: a persistent cache entry for the non-inverted query must NOT be loadable by
    (or collide with) the same query with -v/-w set -- the key digest must differ."""
    backend = AstBackend()
    base = backend._get_result_cache_path("a.py", "python", "function($A)")
    inverted = backend._get_result_cache_path("a.py", "python", "function($A)", invert_match=True)
    word = backend._get_result_cache_path("a.py", "python", "function($A)", word_regexp=True)
    assert base != inverted
    assert base != word
    assert inverted != word
