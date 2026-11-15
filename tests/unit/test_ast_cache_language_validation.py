import hashlib
from collections import OrderedDict
from unittest.mock import Mock

import pytest

from tensor_grep.backends.ast_backend import AstBackend
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


@pytest.fixture
def cached_source(tmp_path, monkeypatch):
    source = tmp_path / "source.js"
    source.write_text("const answer = 42;\n", encoding="utf-8")
    monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "1")
    monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(AstBackend, "_shared_node_type_index_cache", OrderedDict())
    backend = AstBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    monkeypatch.setattr(backend, "_parsed_source_cache", {})
    monkeypatch.setattr(backend, "_get_parser", Mock(side_effect=AssertionError("cache miss")))
    return backend, str(source)


def seed_cache(backend, source, kind):
    if kind == "result":
        result = SearchResult(
            matches=[MatchLine(1, "const answer = 42;", source, 0, 18)],
            total_files=1,
            total_matches=1,
        )
        backend._persist_result_cache(source, "javascript", "identifier", result)
        return backend._get_result_cache_path(source, "javascript", "identifier")
    backend._persist_node_type_index(source, "javascript", {"identifier": [(1, 0, 18)]})
    backend._node_type_index_cache.clear()
    return backend._get_node_index_cache_path(source, "javascript")


@pytest.mark.parametrize("kind", ["result", "node"])
@pytest.mark.parametrize("language", ["../outside", "absolute"])
def test_invalid_language_rejected_before_cache_access(
    cached_source, tmp_path, monkeypatch, kind, language
):
    backend, source = cached_source
    canonical = seed_cache(backend, source, kind)
    language = str(tmp_path / "absolute") if language == "absolute" else language
    key = f"{source}::{language}"
    if kind == "result":
        key += "::identifier::v=False::w=False"
    planted = tmp_path / "cache" / language
    if kind == "node":
        planted /= "node-index"
    planted /= hashlib.sha256(key.encode()).hexdigest() + ".json"
    assert not planted.resolve().is_relative_to((tmp_path / "cache").resolve())
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(canonical.read_bytes())
    loaders = []
    for name in ("_load_persistent_cached_result", "_load_persistent_node_type_index"):
        spy = Mock(wraps=getattr(backend, name))
        monkeypatch.setattr(backend, name, spy)
        loaders.append(spy)
    with pytest.raises(ValueError, match="Unsupported AST language"):
        backend.search(source, "identifier", SearchConfig(lang=language))
    for spy in loaders:
        spy.assert_not_called()


@pytest.mark.parametrize("kind", ["result", "node"])
@pytest.mark.parametrize("language", ["javascript", "js"])
def test_valid_language_reuses_canonical_cache(cached_source, kind, language):
    backend, source = cached_source
    seed_cache(backend, source, kind)
    result = backend.search(source, "identifier", SearchConfig(lang=language))
    assert result.total_matches == 1
    assert result.matches == [MatchLine(1, "const answer = 42;", source, 0, 18)]
    assert result.routing_reason == (
        "ast_structural_match_cached" if kind == "result" else "ast_structural_index_cache"
    )
    backend._get_parser.assert_not_called()


@pytest.mark.parametrize("guard", ["unavailable", "invert_match", "word_regexp"])
def test_existing_guard_precedes_invalid_language(cached_source, monkeypatch, guard):
    backend, source = cached_source
    config = SearchConfig(lang="../outside")
    if guard == "unavailable":
        monkeypatch.setattr(backend, "is_available", lambda: False)
    else:
        setattr(config, guard, True)
    message = "requires tree-sitter" if guard == "unavailable" else "not supported"
    with pytest.raises(BackendExecutionError, match=message):
        backend.search(source, "identifier", config)
