import os
from pathlib import Path

import pytest

from tensor_grep.core.config import SearchConfig

_NON_SIMPLE_AST_PATTERN = "(function_definition name: (identifier) @name)"


class _FakeCapturedNode:
    def __init__(self, line_number: int = 0):
        self.start_point = (line_number, 0)


class _FakeCacheQuery:
    def captures(self, _root):
        return [(_FakeCapturedNode(), "match")]


class _FakeCacheLanguage:
    def __init__(self):
        self.query_calls = 0

    def query(self, _pattern):
        self.query_calls += 1
        return _FakeCacheQuery()


class _FakeCacheTree:
    class RootNode:
        type = "module"
        start_point = (0, 0)
        children = ()

    root_node = RootNode()


class _FakeCacheParser:
    def __init__(self):
        self.language = _FakeCacheLanguage()
        self.parse_calls = 0

    def parse(self, _source):
        self.parse_calls += 1
        return _FakeCacheTree()


class TestAstBackend:
    def teardown_method(self):
        from tensor_grep.backends.ast_backend import AstBackend

        AstBackend._clear_shared_caches()

    def test_supported_languages_should_preserve_ast_grep_wrapper_surface(self):
        from tensor_grep.backends.ast_backend import get_supported_languages

        languages = set(get_supported_languages())

        assert {"python", "javascript", "typescript", "tsx", "rust", "go", "java"}.issubset(
            languages
        )

    @pytest.mark.parametrize(
        ("raw_language", "normalized_language"),
        [
            ("Python", "python"),
            ("JavaScript", "javascript"),
            ("TypeScript", "typescript"),
            ("Tsx", "tsx"),
            ("Go", "go"),
            ("CSharp", "csharp"),
            ("C-sharp", "csharp"),
            ("Yaml", "yaml"),
        ],
    )
    def test_normalize_ast_language_should_accept_ast_grep_aliases(
        self, raw_language, normalized_language
    ):
        from tensor_grep.backends.ast_backend import normalize_ast_language

        assert normalize_ast_language(raw_language) == normalized_language

    def test_should_report_unavailable_when_tree_sitter_missing(self, mocker):
        # Arrange
        mocker.patch.dict("sys.modules", {"tree_sitter": None})
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()

        # Act & Assert
        assert backend.is_available() is False

    def test_is_available_is_true_on_tree_sitter_alone_even_without_torch_geometric(self, mocker):
        """Parity/characterization test (delete-dead-lsp-tensor-gnn): AstBackend.search() is
        pure tree-sitter query matching -- the dead GNN/_ast_to_graph path (which was the only
        thing that ever touched torch/torch_geometric/CUDA) has been deleted. is_available()
        must therefore report availability from tree-sitter alone: a fully-functional CPU
        backend must not be gated behind an unrelated, no-longer-relevant GPU dependency.
        Simulates the common non-GPU box: `tree_sitter` importable, `torch_geometric` is not.
        """
        import importlib.util

        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "torch_geometric":
                return None  # simulate: torch_geometric is NOT installed
            if name == "tree_sitter":
                return object()  # simulate: tree_sitter IS installed (any non-None spec)
            return real_find_spec(name, *args, **kwargs)

        mocker.patch("importlib.util.find_spec", side_effect=fake_find_spec)
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()

        # Act & Assert
        assert backend.is_available() is True

    def test_should_parse_python_ast(self, tmp_path, monkeypatch):
        # CPU test (was @pytest.mark.gpu): AstBackend is a pure tree-sitter structural
        # matcher with no torch/GPU dependency, so it must run in ordinary non-GPU CI.
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_python")
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")  # hermetic: no persistent disk cache
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        assert backend.is_available() is True  # tree-sitter present -> available (no CUDA needed)

        # Arrange
        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    print('world')\n", encoding="utf-8")
        config = SearchConfig(ast=True, lang="python")

        # Act: a simple node-type pattern routes through the node-type index path.
        result = backend.search(str(file_path), "function_definition", config)

        # Assert: real native match on CPU.
        assert result.routing_backend == "AstBackend"
        assert result.total_matches == 1

    def test_should_match_sexpr_query_via_query_cursor_on_real_tree_sitter(
        self, tmp_path, monkeypatch
    ):
        """Regression for the tree-sitter >=0.25 `_get_query` migration (found by the Opus gate):
        a non-simple ``(...)`` S-expr pattern routes through ``_get_query`` -> the
        ``tree_sitter.Query(language, source)`` constructor + ``QueryCursor`` (mirroring the
        capture site in ``search()``). tree-sitter 0.26 REMOVED ``Language.query``, so on the
        pre-fix code this raised ``BackendExecutionError: 'tree_sitter.Language' object has no
        attribute 'query'`` -- this test fails on the broken API and passes with the fix. Uses
        REAL tree-sitter (no ``FakeLanguage.query`` mock, which would mask the API mismatch).
        """
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_python")
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")  # hermetic: force the live query path
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        assert backend.is_available() is True

        file_path = tmp_path / "svc.py"
        file_path.write_text(
            "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
        )
        config = SearchConfig(ast=True, lang="python")

        # Act: exercises _get_query on the real tree-sitter >=0.25 API.
        result = backend.search(str(file_path), "(function_definition) @fn", config)

        # Assert: both function definitions matched via the QueryCursor path (not the index).
        assert result.routing_backend == "AstBackend"
        assert result.routing_reason == "ast_structural_match"
        assert result.total_matches == 2

    def test_search_raises_backend_execution_error_when_tree_sitter_missing(self, mocker):
        """Backend Fail-Closed Contract: when tree-sitter is unavailable, search() must raise
        BackendExecutionError (never silent-empty), so run_command reports a real error."""
        mocker.patch.dict("sys.modules", {"tree_sitter": None})
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.backends.base import BackendExecutionError

        backend = AstBackend()
        with pytest.raises(BackendExecutionError):
            backend.search(
                "whatever.py", "function_definition", SearchConfig(ast=True, lang="python")
            )

    def test_should_raise_on_invalid_ast_query(self, tmp_path, mocker):
        """Audit MED: a broad `except Exception` around tree-sitter query compilation
        silently converted an invalid/malformed AST pattern into a look-alike 0-match
        result. It must raise BackendExecutionError so run_command surfaces a real
        'invalid pattern' error instead of a silent 0-match (a silent-fallback sibling)."""
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.backends.base import BackendExecutionError

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeLanguage:
            def query(self, _pattern):
                raise Exception("invalid query")

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    print('world')\n", encoding="utf-8")

        with pytest.raises(BackendExecutionError):
            backend.search(
                str(file_path), "not a valid ast query", SearchConfig(ast=True, lang="python")
            )

    def test_bare_identifier_ast_grep_pattern_fails_closed_with_ast_dependency_message(
        self, monkeypatch
    ):
        """#144 hotfix: release-tag-smoke's ast-run-smoke check runs
        `tg run --pattern calculateTotal tests/fixtures/ast_smoke --lang js --json`, which is
        ast-grep-DSL surface served by AstGrepWrapperBackend. When the ast-grep binary is absent
        (CI, since #542 broadened AstBackend.is_available()), core/pipeline.py routing falls
        through to this native tree-sitter AstBackend instead. "calculateTotal" satisfies
        _is_simple_node_type_pattern (a bare identifier), so the node-type-index lookup runs
        first, finds no such node TYPE (it is a user identifier, not a grammar node type),
        returns 0 matches, and falls through to compiling the pattern as a tree-sitter QUERY --
        which raises because "calculateTotal" is not a valid node type. Pre-fix, that surfaced
        the generic "AST query compilation failed" error, which is not one of
        agent_readiness.py's skip_error_patterns ("Explicit AST search requires AST
        dependencies" / "no AST backend is available"), so CI failed instead of skipping. This
        test drives the native AstBackend DIRECTLY (bypassing AstGrepWrapperBackend entirely)
        against the real ast_smoke fixture and asserts the fail-closed message now names the
        ast-grep dependency, so the smoke check recognizes it as an environment gap rather than
        a real bug (#141 DSL divergence: native tree-sitter-query vs ast-grep-DSL)."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_javascript")
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")  # hermetic: force the live query path
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.backends.base import BackendExecutionError

        backend = AstBackend()
        assert backend.is_available() is True

        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "ast_smoke" / "app.js"
        assert fixture_path.exists()

        with pytest.raises(BackendExecutionError) as exc_info:
            backend.search(str(fixture_path), "calculateTotal", SearchConfig(ast=True, lang="js"))

        assert "Explicit AST search requires AST dependencies" in str(exc_info.value)

    def test_malformed_non_simple_ast_query_still_raises_original_compilation_error(
        self, tmp_path, mocker
    ):
        """Regression guard for #144: the new ast-grep-dependency branch must only fire for a
        *simple* bare-identifier pattern (ast-grep-DSL-shaped). A malformed non-simple `(...)`
        tree-sitter query is a genuinely invalid QUERY, not a plausible ast-grep pattern, and
        must keep raising the ORIGINAL "AST query compilation failed" message -- proving the new
        fail-closed branch was not over-broadened to swallow real invalid-query errors."""
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.backends.base import BackendExecutionError

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeLanguage:
            def query(self, _pattern):
                raise Exception("invalid query")

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    print('world')\n", encoding="utf-8")

        with pytest.raises(BackendExecutionError) as exc_info:
            backend.search(
                str(file_path),
                "(function_definition name",  # malformed s-expr, not a bare identifier
                SearchConfig(ast=True, lang="python"),
            )

        message = str(exc_info.value)
        assert "AST query compilation failed" in message
        assert "Explicit AST search requires AST dependencies" not in message

    def test_should_support_dict_capture_shape(self, tmp_path, mocker):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            pass

        class FakeLanguage:
            def query(self, _pattern):
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        class FakeCursor:
            def __init__(self, _query):
                pass

            def captures(self, _root):
                return {"match": [FakeNode()]}

        class FakeTreeSitterModule:
            QueryCursor = FakeCursor

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())
        mocker.patch.dict("sys.modules", {"tree_sitter": FakeTreeSitterModule})

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n")

        result = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )
        assert result.total_matches == 1
        assert result.routing_backend == "AstBackend"
        assert result.routing_reason == "ast_structural_match"

    def test_should_cap_matches_to_max_count_for_query_capture_path(self, tmp_path, mocker):
        """H6: --ast ... --max-count N must cap the returned matches to N, matching
        the per-file cap semantics of cpu_backend/rust, instead of returning every
        structural match."""
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            def __init__(self, line_number):
                self.start_point = (line_number, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(line), "match") for line in range(4)]

        class FakeLanguage:
            def query(self, _pattern):
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("a\nb\nc\nd\n", encoding="utf-8")

        result = backend.search(
            str(file_path),
            "function_definition",
            SearchConfig(ast=True, lang="python", max_count=2),
        )

        assert result.total_matches == 2
        assert [m.line_number for m in result.matches] == [1, 2]

    def test_max_count_cap_does_not_poison_the_persisted_cache(self, tmp_path, mocker, monkeypatch):
        """The capped result must never be what's persisted -- otherwise a later
        query with a higher/no max_count would silently replay the truncated
        result from a previous capped query."""
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            def __init__(self, line_number):
                self.start_point = (line_number, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(line), "match") for line in range(4)]

        class FakeLanguage:
            def query(self, _pattern):
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("a\nb\nc\nd\n", encoding="utf-8")

        capped = backend.search(
            str(file_path),
            "function_definition",
            SearchConfig(ast=True, lang="python", max_count=1),
        )
        assert capped.total_matches == 1

        # A second, uncapped query for the same file/lang/pattern hits the
        # persistent result cache -- it must return the FULL match set, proving
        # the cache stored the uncapped result rather than the capped one.
        uncapped = backend.search(
            str(file_path),
            "function_definition",
            SearchConfig(ast=True, lang="python"),
        )
        assert uncapped.total_matches == 4

    def test_should_reuse_compiled_query_and_parsed_source_for_repeated_searches(
        self, tmp_path, mocker
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        parser = FakeParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        first = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )
        second = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert second.total_matches == 1
        assert parser.parse_calls == 1
        assert parser.language.query_calls == 1

    def test_should_invalidate_parsed_source_cache_when_file_changes(self, tmp_path, mocker):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        parser = FakeParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend.search(str(file_path), "function_definition", SearchConfig(ast=True, lang="python"))

        # Change both timestamp and file size so cache invalidation is deterministic on Windows.
        file_path.write_text("def hello_world():\n    return 1\n", encoding="utf-8")

        result = backend.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert result.total_matches == 1
        assert parser.parse_calls == 2
        assert parser.language.query_calls == 1

    def test_query_cache_obeys_entry_cap(self, monkeypatch):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES", "2")
        backend = AstBackend()
        parser = _FakeCacheParser()

        backend._get_query(parser, "python", "function_definition")
        backend._get_query(parser, "python", "class_definition")
        backend._get_query(parser, "python", "call")

        assert len(AstBackend._shared_queries) == 2
        assert ("python", "function_definition") not in AstBackend._shared_queries
        assert ("python", "class_definition") in AstBackend._shared_queries
        assert ("python", "call") in AstBackend._shared_queries

    def test_node_type_index_cache_obeys_entry_cap(self, tmp_path, monkeypatch):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES", "2")
        backend = AstBackend()
        files = []
        for index in range(3):
            path = tmp_path / f"module_{index}.py"
            path.write_text(f"def f_{index}():\n    return {index}\n", encoding="utf-8")
            files.append(path)
            backend._persist_node_type_index(str(path), "python", {"function_definition": [1]})

        cache = AstBackend._shared_node_type_index_cache
        assert len(cache) == 2
        assert (str(files[0]), "python") not in cache
        assert (str(files[1]), "python") in cache
        assert (str(files[2]), "python") in cache

    def test_should_reuse_persistent_ast_cache_across_backend_instances(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1
        assert parser_one.language.query_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_invalidate_persistent_ast_cache_when_file_changes(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)
        backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        file_path.write_text("def hello_world():\n    return 1\n", encoding="utf-8")

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)
        result = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert result.total_matches == 1
        assert parser_two.parse_calls == 1
        assert parser_two.language.query_calls == 0

    def test_should_reuse_persistent_node_type_index_for_simple_native_patterns(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))

        class FakeNode:
            def __init__(self, node_type, line_number, children=()):
                self.type = node_type
                self.start_point = (line_number, 0)
                # M16 F2: the node-type index is now span-based; synthetic
                # nodes need deterministic byte spans.
                self.start_byte = line_number * 4
                self.end_byte = self.start_byte + 4
                self.children = children

        class FakeQuery:
            def captures(self, _root):
                return []

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            def __init__(self):
                self.root_node = FakeNode(
                    "module",
                    0,
                    children=(
                        FakeNode("function_definition", 0),
                        FakeNode("class_definition", 2),
                    ),
                )

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\nclass World:\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "class_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert second.matches[0].line_number == 3
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_cap_matches_for_node_type_index_path(self, tmp_path, mocker, monkeypatch):
        """H6: the simple-node-type-index fast path (a different code path from the
        tree-sitter query-capture path) must also honor max_count."""
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))
        AstBackend._clear_shared_caches()

        class FakeNode:
            def __init__(self, node_type, line_number, children=()):
                self.type = node_type
                self.start_point = (line_number, 0)
                # M16 F2: the node-type index is now span-based; synthetic
                # nodes need deterministic byte spans.
                self.start_byte = line_number * 4
                self.end_byte = self.start_byte + 4
                self.children = children

        class FakeQuery:
            def captures(self, _root):
                return []

        class FakeLanguage:
            def query(self, _pattern):
                return FakeQuery()

        class FakeTree:
            def __init__(self):
                self.root_node = FakeNode(
                    "module",
                    0,
                    children=(
                        FakeNode("function_definition", 0),
                        FakeNode("function_definition", 1),
                        FakeNode("function_definition", 2),
                    ),
                )

        class FakeParser:
            language = FakeLanguage()

            def parse(self, _source):
                return FakeTree()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        mocker.patch.object(backend, "_get_parser", return_value=FakeParser())

        file_path = tmp_path / "test.py"
        file_path.write_text("a\nb\nc\n", encoding="utf-8")

        result = backend.search(
            str(file_path),
            "function_definition",
            SearchConfig(ast=True, lang="python", max_count=2),
        )

        assert result.total_matches == 2
        assert [m.line_number for m in result.matches] == [1, 2]

    def test_should_reuse_shared_in_memory_caches_across_backend_instances(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        AstBackend._clear_shared_caches()

        class FakeNode:
            start_point = (0, 0)

        class FakeQuery:
            def captures(self, _root):
                return [(FakeNode(), "match")]

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            class RootNode:
                type = "module"
                start_point = (0, 0)
                children = ()

            root_node = RootNode()

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1
        assert parser_one.language.query_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)

        second = backend_two.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0

    def test_should_apply_calibrated_multiplier_when_estimating_parsed_source_cache_entry_size(
        self,
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        source_bytes = b"def alpha():\n    return 1111111111\n"

        estimated = backend._estimate_parsed_source_cache_entry_size(source_bytes)

        assert estimated == len(source_bytes) * 3

    def test_should_evict_least_recently_used_parsed_source_entries_when_byte_budget_is_exceeded(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "250")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_c = tmp_path / "c.py"
        file_a.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")
        file_b.write_text("def bravo():\n    return 2222222222\n", encoding="utf-8")
        file_c.write_text("def charl():\n    return 3333333333\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_b), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_c), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 3
        assert list(backend._parsed_source_cache) == [
            (str(file_a), "python"),
            (str(file_c), "python"),
        ]
        assert AstBackend._shared_parsed_source_cache_bytes <= 250

        backend.search(str(file_a), _NON_SIMPLE_AST_PATTERN, config)
        assert parser.parse_calls == 3

        backend.search(str(file_b), _NON_SIMPLE_AST_PATTERN, config)
        assert parser.parse_calls == 4

    def test_should_skip_caching_parsed_source_entries_that_exceed_the_byte_budget(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "10")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "oversized.py"
        file_path.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 2
        assert backend._parsed_source_cache == {}
        assert AstBackend._shared_parsed_source_cache_bytes == 0

    def test_should_skip_caching_parsed_source_entries_when_calibrated_estimate_exceeds_budget(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        monkeypatch.setenv("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES", "100")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "midrange.py"
        file_path.write_text("def alpha():\n    return 1111111111\n", encoding="utf-8")

        config = SearchConfig(ast=True, lang="python")
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)
        backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert parser.parse_calls == 2
        assert backend._parsed_source_cache == {}
        assert AstBackend._shared_parsed_source_cache_bytes == 0

    def test_should_invalidate_parsed_source_cache_when_file_identity_changes_without_mtime_or_size_change(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        AstBackend._clear_shared_caches()

        backend = AstBackend()
        mocker.patch.object(backend, "is_available", return_value=True)
        parser = _FakeCacheParser()
        mocker.patch.object(backend, "_get_parser", return_value=parser)

        file_path = tmp_path / "identity.py"
        file_path.write_text("def alpha():\n    pass\n", encoding="utf-8")
        original_stat = file_path.stat()

        config = SearchConfig(ast=True, lang="python")
        first = backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        file_path.unlink()
        file_path.write_text("def bravo():\n    pass\n", encoding="utf-8")
        os.utime(file_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        second = backend.search(str(file_path), _NON_SIMPLE_AST_PATTERN, config)

        assert first.matches[0].text == "def alpha():"
        assert second.matches[0].text == "def bravo():"
        assert parser.parse_calls == 2

    def test_should_reuse_shared_in_memory_node_index_without_disk_reload(
        self, tmp_path, mocker, monkeypatch
    ):
        from tensor_grep.backends.ast_backend import AstBackend

        cache_dir = tmp_path / "ast-cache"
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE_DIR", str(cache_dir))
        AstBackend._clear_shared_caches()

        class FakeNode:
            def __init__(self, node_type, line_number, children=()):
                self.type = node_type
                self.start_point = (line_number, 0)
                # M16 F2: the node-type index is now span-based; synthetic nodes
                # need deterministic byte spans.
                self.start_byte = line_number * 4
                self.end_byte = self.start_byte + 4
                self.children = children

        class FakeQuery:
            def captures(self, _root):
                return []

        class FakeLanguage:
            def __init__(self):
                self.query_calls = 0

            def query(self, _pattern):
                self.query_calls += 1
                return FakeQuery()

        class FakeTree:
            def __init__(self):
                self.root_node = FakeNode(
                    "module",
                    0,
                    children=(
                        FakeNode("function_definition", 0),
                        FakeNode("class_definition", 2),
                    ),
                )

        class FakeParser:
            def __init__(self):
                self.language = FakeLanguage()
                self.parse_calls = 0

            def parse(self, _source):
                self.parse_calls += 1
                return FakeTree()

        file_path = tmp_path / "test.py"
        file_path.write_text("def hello():\n    pass\nclass World:\n    pass\n", encoding="utf-8")

        backend_one = AstBackend()
        mocker.patch.object(backend_one, "is_available", return_value=True)
        parser_one = FakeParser()
        mocker.patch.object(backend_one, "_get_parser", return_value=parser_one)

        first = backend_one.search(
            str(file_path), "function_definition", SearchConfig(ast=True, lang="python")
        )

        assert first.total_matches == 1
        assert parser_one.parse_calls == 1

        backend_two = AstBackend()
        mocker.patch.object(backend_two, "is_available", return_value=True)
        parser_two = FakeParser()
        mocker.patch.object(backend_two, "_get_parser", return_value=parser_two)
        read_text_spy = mocker.spy(Path, "read_text")

        second = backend_two.search(
            str(file_path), "class_definition", SearchConfig(ast=True, lang="python")
        )

        assert second.total_matches == 1
        assert second.matches[0].line_number == 3
        assert parser_two.parse_calls == 0
        assert parser_two.language.query_calls == 0
        assert read_text_spy.call_count == 0

    def test_grammar_node_type_shaped_bare_word_silently_diverges_from_ast_grep_semantics(
        self, tmp_path, monkeypatch
    ):
        """#141 investigation (2026-08-01 backlog audit): the existing fail-closed guard
        (`test_bare_identifier_ast_grep_pattern_fails_closed_with_ast_dependency_message` above)
        only fires when a bare-word ast-grep-DSL pattern (e.g. "calculateTotal") is NOT also a
        real tree-sitter grammar node-type name -- the node-type-index lookup finds zero matches
        for that case, falls through to compiling it as a query, and THAT raises the documented
        ast-grep-dependency error.

        But plenty of realistic search words a user types ARE grammar node-type names in one
        language or another -- "identifier", "string", "call", "comment", "block", "parameters",
        "assignment" among them. For those, the node-type-index lookup SUCCEEDS (real matches
        exist) and `search()` returns a normal, non-empty SearchResult -- it never reaches the
        fail-closed guard at all. ast-grep's CODE-PATTERN semantics for the identical pattern
        text mean something completely different: "match literal source occurrences of the code
        `identifier`" (a handful of hits), not "match every AST node of type `identifier`"
        (dozens of hits: every variable, parameter, class, and function name in the file). Both
        answers are non-empty, well-formed, and equally plausible-looking; nothing in the output
        signals that the DSL was silently reinterpreted. This is the unguarded, silently-wrong
        member of the #141 divergence family -- proven here directly against real tree-sitter
        (no mocks), by asserting the native backend matches a line that does not even CONTAIN the
        substring "identifier" (impossible under any code-pattern reading of that pattern)."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_python")
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")  # hermetic: force the live index path
        from tensor_grep.backends.ast_backend import AstBackend

        backend = AstBackend()
        assert backend.is_available() is True

        file_path = tmp_path / "sample.py"
        file_path.write_text(
            "def compute(identifier):\n"
            "    total = identifier + 1\n"
            "    return total\n"
            "\n"
            "\n"
            "class Foo:\n"
            "    def bar(self):\n"
            "        identifier = 5\n"
            "        return identifier\n",
            encoding="utf-8",
        )

        result = backend.search(str(file_path), "identifier", SearchConfig(ast=True, lang="python"))

        # ast-grep's code-pattern reading of "identifier" can only ever match lines that
        # literally contain the token "identifier" (lines 1, 2, 8, 9 -- 4 matches). The native
        # tree-sitter node-type-index reading matches every `identifier`-typed AST node instead,
        # which also covers "total"/"Foo"/"bar"/"self" -- lines with no "identifier" substring
        # at all (3 "return total", 6 "class Foo:", 7 "def bar(self):").
        literal_token_lines = {1, 2, 8, 9}
        matched_lines = {m.line_number for m in result.matches}
        assert matched_lines != literal_token_lines, (
            "native AstBackend's node-type-index reading of a grammar-node-type-shaped bare "
            "word happened to coincide with ast-grep's code-pattern reading for this fixture; "
            "the whole point of this regression is that they normally do NOT agree"
        )
        lines_with_no_literal_token = {
            line
            for line in matched_lines
            if "identifier" not in file_path.read_text(encoding="utf-8").splitlines()[line - 1]
        }
        assert lines_with_no_literal_token, (
            "expected at least one matched line that does not even contain the substring "
            "'identifier' -- proof this ran a structural node-type query, not an ast-grep "
            "code-pattern match, for the exact same pattern text"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "#141 follow-up, not fixed by this investigation: AstBackend._get_parser raises a "
            "bare RuntimeError (not BackendExecutionError) for any _SUPPORTED_AST_LANGUAGES "
            "entry outside _NATIVE_AST_LANGUAGES (java/csharp/php/c/cpp/...), and search() does "
            "not catch it -- a Backend Fail-Closed Contract violation, distinct from (and "
            "narrower than) the silent node-type-collision divergence proven above."
        ),
    )
    def test_unsupported_but_documented_language_raises_backend_execution_error_not_bare_runtime_error(
        self, tmp_path, monkeypatch
    ):
        """#141 investigation follow-on: `_SUPPORTED_AST_LANGUAGES` advertises languages (java,
        csharp, php, c, cpp, ...) that `_get_parser` cannot actually construct a tree-sitter
        parser for -- only `_NATIVE_AST_LANGUAGES` (python/javascript/typescript/tsx/rust) have a
        real branch there; anything else falls to `_get_parser`'s `else: raise ValueError(...)`,
        wrapped by `except Exception as e: raise RuntimeError(...) from e`. That RuntimeError is
        never caught by `search()` (only the later `_get_query()` call is wrapped in
        try/except-BackendExecutionError), so it escapes as a bare, uncaught RuntimeError --
        violating this file's own Backend Fail-Closed Contract (every real failure must raise
        BackendExecutionError, per backends/base.py and AGENTS.md). This is reachable in
        production via `tg search --ast --lang java <bare-identifier-pattern>` whenever the
        ast-grep wrapper is unavailable: `Pipeline.__init__`'s AST branch (core/pipeline.py) picks
        AstBackend for any native-shaped pattern once the wrapper is absent, with no
        `is_native_ast_language(config.lang)` check gating that choice. Currently xfail: fixing
        it is a #141 follow-up, not this investigation's job."""
        pytest.importorskip("tree_sitter")
        pytest.importorskip("tree_sitter_java")
        monkeypatch.setenv("TENSOR_GREP_AST_CACHE", "0")
        from tensor_grep.backends.ast_backend import AstBackend
        from tensor_grep.backends.base import BackendExecutionError

        backend = AstBackend()
        assert backend.is_available() is True

        file_path = tmp_path / "Sample.java"
        file_path.write_text("class Sample {\n    int someIdentifier = 1;\n}\n", encoding="utf-8")

        with pytest.raises(BackendExecutionError):
            backend.search(
                str(file_path),
                "someIdentifier",
                SearchConfig(ast=True, ast_prefer_native=True, lang="java"),
            )
