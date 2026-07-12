import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, ClassVar

from tensor_grep.backends.base import BackendExecutionError, ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

logger = logging.getLogger(__name__)

_DEFAULT_PARSED_SOURCE_CACHE_MAX_BYTES = 64 * 1024 * 1024
_PARSED_SOURCE_CACHE_ENTRY_SIZE_CALIBRATION_MULTIPLIER = 3
_AST_QUERY_CACHE_MAX_ENTRIES_ENV = "TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES"
_AST_NODE_INDEX_CACHE_MAX_ENTRIES_ENV = "TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES"
_DEFAULT_AST_QUERY_CACHE_MAX_ENTRIES = 256
_DEFAULT_AST_NODE_INDEX_CACHE_MAX_ENTRIES = 512

FileSignature = tuple[int, int, int, int, int]
ParsedSourceCacheEntry = tuple[FileSignature, bytes, list[str], Any, int]
NodeTypeIndexCacheEntry = tuple[FileSignature, dict[str, list[int]]]


_AST_LANGUAGE_ALIASES = {
    "bash": "bash",
    "sh": "bash",
    "c": "c",
    "cc": "cpp",
    "c++": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "c-sharp": "csharp",
    "c#": "csharp",
    "c_sharp": "csharp",
    "cs": "csharp",
    "csharp": "csharp",
    "css": "css",
    "elixir": "elixir",
    "ex": "elixir",
    "go": "go",
    "golang": "go",
    "haskell": "haskell",
    "hs": "haskell",
    "hcl": "hcl",
    "html": "html",
    "java": "java",
    "python": "python",
    "py": "python",
    "javascript": "javascript",
    "jsx": "javascript",
    "js": "javascript",
    "json": "json",
    "kotlin": "kotlin",
    "kt": "kotlin",
    "lua": "lua",
    "nix": "nix",
    "php": "php",
    "ruby": "ruby",
    "rb": "ruby",
    "scala": "scala",
    "sol": "solidity",
    "solidity": "solidity",
    "swift": "swift",
    "typescript": "typescript",
    "ts": "typescript",
    "tsx": "tsx",
    "rust": "rust",
    "rs": "rust",
    "yaml": "yaml",
    "yml": "yaml",
}
_SUPPORTED_AST_LANGUAGES = (
    "bash",
    "c",
    "cpp",
    "csharp",
    "css",
    "elixir",
    "go",
    "haskell",
    "hcl",
    "html",
    "java",
    "javascript",
    "json",
    "kotlin",
    "lua",
    "nix",
    "php",
    "python",
    "ruby",
    "rust",
    "scala",
    "solidity",
    "swift",
    "typescript",
    "tsx",
    "yaml",
)
_NATIVE_AST_LANGUAGES = ("python", "javascript", "typescript", "tsx", "rust")


def normalize_ast_language(language: object | None, *, default: str = "python") -> str:
    """Normalize supported ast-grep language names to tg internal identifiers."""
    raw_language = default if language is None or not str(language).strip() else str(language)
    normalized = _AST_LANGUAGE_ALIASES.get(raw_language.strip().lower())
    if normalized is None:
        supported = ", ".join(_SUPPORTED_AST_LANGUAGES)
        raise ValueError(
            f"Unsupported AST language {raw_language}. Supported languages: {supported}."
        )
    return normalized


def is_native_ast_language(language: object | None) -> bool:
    """Return whether tg's in-process tree-sitter backend can parse the language."""
    try:
        normalized = normalize_ast_language(language)
    except ValueError:
        return False
    return normalized in _NATIVE_AST_LANGUAGES


def get_supported_languages() -> list[str]:
    """Return language identifiers supported by tg AST surfaces."""
    return list(_SUPPORTED_AST_LANGUAGES)


class AstBackend(ComputeBackend):
    """
    A native, in-process structural-search backend: parses source code into an Abstract Syntax
    Tree (AST) using tree-sitter and matches tree-sitter queries directly against the parsed
    tree (or a cached node-type index for simple single-node-type patterns). Pure CPU, no
    torch/GPU dependency -- see ``is_available()``.
    """

    _shared_parsers: ClassVar[dict[str, Any]] = {}
    _shared_queries: ClassVar[OrderedDict[tuple[str, str], Any]] = OrderedDict()
    _shared_parsed_source_cache: ClassVar[OrderedDict[tuple[str, str], ParsedSourceCacheEntry]] = (
        OrderedDict()
    )
    _shared_parsed_source_cache_bytes: ClassVar[int] = 0
    _shared_node_type_index_cache: ClassVar[
        OrderedDict[tuple[str, str], NodeTypeIndexCacheEntry]
    ] = OrderedDict()

    def __init__(self) -> None:
        self._parsers = self._shared_parsers
        self._queries = self._shared_queries
        self._parsed_source_cache = self._shared_parsed_source_cache
        self._node_type_index_cache = self._shared_node_type_index_cache

    @classmethod
    def _clear_shared_caches(cls) -> None:
        cls._shared_parsers.clear()
        cls._shared_queries.clear()
        cls._shared_parsed_source_cache.clear()
        cls._shared_parsed_source_cache_bytes = 0
        cls._shared_node_type_index_cache.clear()

    @staticmethod
    def _configured_positive_int(env_var: str, default: int) -> int:
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            return default
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @classmethod
    def _query_cache_max_entries(cls) -> int:
        return cls._configured_positive_int(
            _AST_QUERY_CACHE_MAX_ENTRIES_ENV,
            _DEFAULT_AST_QUERY_CACHE_MAX_ENTRIES,
        )

    @classmethod
    def _node_index_cache_max_entries(cls) -> int:
        return cls._configured_positive_int(
            _AST_NODE_INDEX_CACHE_MAX_ENTRIES_ENV,
            _DEFAULT_AST_NODE_INDEX_CACHE_MAX_ENTRIES,
        )

    @classmethod
    def _remember_node_type_index(
        cls,
        cache_key: tuple[str, str],
        cache_entry: NodeTypeIndexCacheEntry,
    ) -> None:
        cls._shared_node_type_index_cache.pop(cache_key, None)
        cls._shared_node_type_index_cache[cache_key] = cache_entry
        while len(cls._shared_node_type_index_cache) > cls._node_index_cache_max_entries():
            cls._shared_node_type_index_cache.popitem(last=False)

    def _is_persistent_cache_enabled(self) -> bool:
        return os.environ.get("TENSOR_GREP_AST_CACHE", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def _get_persistent_cache_dir(self) -> Path:
        override = os.environ.get("TENSOR_GREP_AST_CACHE_DIR")
        if override:
            return Path(override).expanduser().resolve()
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                return Path(local_appdata) / "tensor-grep" / "ast-cache"
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home) / "tensor-grep" / "ast-cache"
        return Path.home() / ".cache" / "tensor-grep" / "ast-cache"

    def _build_file_signature(self, file_path: str) -> FileSignature:
        stat_result = os.stat(file_path)
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_mtime_ns,
            getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1_000_000_000)),
            stat_result.st_size,
        )

    def _get_parsed_source_cache_limit_bytes(self) -> int:
        raw_limit = os.environ.get("TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES")
        if raw_limit is None:
            return _DEFAULT_PARSED_SOURCE_CACHE_MAX_BYTES

        try:
            return max(int(raw_limit), 0)
        except ValueError:
            logger.debug(
                "Ignoring invalid TENSOR_GREP_AST_PARSED_SOURCE_CACHE_MAX_BYTES=%r",
                raw_limit,
            )
            return _DEFAULT_PARSED_SOURCE_CACHE_MAX_BYTES

    def _estimate_parsed_source_cache_entry_size(self, source_bytes: bytes) -> int:
        # The cache stores more than raw source bytes: we also retain a decoded
        # splitlines() list plus a tree-sitter tree wrapper/native tree.
        # A 3x multiplier keeps the tracked size within the observed ~2-4x real
        # memory footprint while preserving a cheap O(1) estimator on the hot path.
        return len(source_bytes) * _PARSED_SOURCE_CACHE_ENTRY_SIZE_CALIBRATION_MULTIPLIER

    def _discard_cached_parsed_source(self, cache_key: tuple[str, str]) -> None:
        cached = self._parsed_source_cache.pop(cache_key, None)
        if cached is None:
            return

        self.__class__._shared_parsed_source_cache_bytes = max(
            0,
            self.__class__._shared_parsed_source_cache_bytes - cached[4],
        )

    def _get_cached_parsed_source_entry(
        self, file_path: str, lang: str
    ) -> tuple[bytes, list[str], Any] | None:
        cache_key = (file_path, lang)
        cache_signature = self._build_file_signature(file_path)
        cached = self._parsed_source_cache.get(cache_key)
        if cached is None:
            return None
        if cached[0] != cache_signature:
            self._discard_cached_parsed_source(cache_key)
            return None

        self._parsed_source_cache.move_to_end(cache_key)
        return cached[1], cached[2], cached[3]

    def _store_parsed_source_cache_entry(
        self,
        file_path: str,
        lang: str,
        cache_signature: FileSignature,
        source_bytes: bytes,
        lines: list[str],
        tree: Any,
    ) -> None:
        cache_limit_bytes = self._get_parsed_source_cache_limit_bytes()
        cache_key = (file_path, lang)

        self._discard_cached_parsed_source(cache_key)

        entry_size = self._estimate_parsed_source_cache_entry_size(source_bytes)
        if cache_limit_bytes <= 0 or entry_size > cache_limit_bytes:
            return

        self._parsed_source_cache[cache_key] = (
            cache_signature,
            source_bytes,
            lines,
            tree,
            entry_size,
        )
        self._parsed_source_cache.move_to_end(cache_key)
        self.__class__._shared_parsed_source_cache_bytes += entry_size

        while self.__class__._shared_parsed_source_cache_bytes > cache_limit_bytes:
            _, evicted = self._parsed_source_cache.popitem(last=False)
            self.__class__._shared_parsed_source_cache_bytes = max(
                0,
                self.__class__._shared_parsed_source_cache_bytes - evicted[4],
            )

    def _is_simple_node_type_pattern(self, pattern: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern.strip()))

    def _get_node_index_cache_path(self, file_path: str, lang: str) -> Path:
        digest = hashlib.sha256(f"{Path(file_path).resolve()}::{lang}".encode()).hexdigest()
        return self._get_persistent_cache_dir() / lang / "node-index" / f"{digest}.json"

    def _get_result_cache_path(self, file_path: str, lang: str, pattern: str) -> Path:
        digest = hashlib.sha256(
            f"{Path(file_path).resolve()}::{lang}::{pattern}".encode()
        ).hexdigest()
        return self._get_persistent_cache_dir() / lang / f"{digest}.json"

    def _load_persistent_cached_result(
        self, file_path: str, lang: str, pattern: str
    ) -> SearchResult | None:
        if not self._is_persistent_cache_enabled():
            return None

        cache_path = self._get_result_cache_path(file_path, lang, pattern)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        signature = payload.get("file_signature")
        if signature != list(self._build_file_signature(file_path)):
            return None

        matches_payload = payload.get("matches", [])
        if not isinstance(matches_payload, list):
            return None

        matches = [
            MatchLine(
                line_number=int(match["line_number"]),
                text=str(match["text"]),
                file=str(match["file"]),
            )
            for match in matches_payload
        ]

        return SearchResult(
            matches=matches,
            total_files=int(payload.get("total_files", 0)),
            total_matches=int(payload.get("total_matches", len(matches))),
            routing_backend="AstBackend",
            routing_reason="ast_structural_match_cached",
            routing_distributed=False,
            routing_worker_count=1,
        )

    def _persist_result_cache(
        self, file_path: str, lang: str, pattern: str, result: SearchResult
    ) -> None:
        if not self._is_persistent_cache_enabled():
            return

        cache_path = self._get_result_cache_path(file_path, lang, pattern)
        payload = {
            "file_signature": list(self._build_file_signature(file_path)),
            "total_files": result.total_files,
            "total_matches": result.total_matches,
            "matches": [
                {
                    "line_number": match.line_number,
                    "text": match.text,
                    "file": match.file,
                }
                for match in result.matches
            ],
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            logger.debug("Failed to write AST persistent cache for %s", file_path, exc_info=True)

    def _build_node_type_index(self, root_node: Any) -> dict[str, list[int]]:
        # audit B3: convert recursive DFS to explicit stack to avoid RecursionError on
        # deeply-nested ASTs (e.g. long chained expressions or auto-generated code).
        node_type_index: dict[str, set[int]] = {}
        stack = [root_node]
        while stack:
            node = stack.pop()
            node_type_index.setdefault(node.type, set()).add(node.start_point[0] + 1)
            # Push children in reverse order so leftmost child is processed first.
            stack.extend(reversed(node.children))
        return {
            node_type: sorted(line_numbers) for node_type, line_numbers in node_type_index.items()
        }

    def _load_persistent_node_type_index(
        self, file_path: str, lang: str
    ) -> dict[str, list[int]] | None:
        cache_key = (file_path, lang)
        cache_signature = self._build_file_signature(file_path)
        cached = self._node_type_index_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            self._node_type_index_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            self._node_type_index_cache.pop(cache_key, None)

        if not self._is_persistent_cache_enabled():
            return None

        cache_path = self._get_node_index_cache_path(file_path, lang)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("file_signature") != list(self._build_file_signature(file_path)):
            return None

        node_index = payload.get("node_type_index")
        if not isinstance(node_index, dict):
            return None

        normalized: dict[str, list[int]] = {}
        for node_type, line_numbers in node_index.items():
            if not isinstance(node_type, str) or not isinstance(line_numbers, list):
                return None
            normalized[node_type] = [int(line_number) for line_number in line_numbers]
        self._remember_node_type_index(cache_key, (cache_signature, normalized))
        return normalized

    def _persist_node_type_index(
        self, file_path: str, lang: str, node_type_index: dict[str, list[int]]
    ) -> None:
        self._remember_node_type_index(
            (file_path, lang),
            (
                self._build_file_signature(file_path),
                node_type_index,
            ),
        )
        if not self._is_persistent_cache_enabled():
            return

        cache_path = self._get_node_index_cache_path(file_path, lang)
        payload = {
            "file_signature": list(self._build_file_signature(file_path)),
            "node_type_index": node_type_index,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            logger.debug("Failed to write AST node index cache for %s", file_path, exc_info=True)

    @staticmethod
    def _cap_to_max_count(result: SearchResult, config: SearchConfig | None) -> SearchResult:
        """H6: cap `result` to `config.max_count`, matching cpu_backend/rust's
        per-file cap semantics (never return every match). Must only be applied
        to the value returned to the caller, AFTER any persistent-cache write --
        caching a pre-capped result would silently truncate a later query that
        requests a higher (or no) max_count for the same file/lang/pattern.
        """
        max_count = config.max_count if config else None
        if not max_count or len(result.matches) <= max_count:
            return result
        result.matches = result.matches[:max_count]
        result.total_matches = len(result.matches)
        result.total_files = 1 if result.matches else 0
        return result

    def _build_matches_from_line_numbers(
        self, file_path: str, lines: list[str], line_numbers: list[int], routing_reason: str
    ) -> SearchResult:
        matches = [
            MatchLine(line_number=line_num, text=lines[line_num - 1], file=file_path)
            for line_num in line_numbers
            if 0 < line_num <= len(lines)
        ]
        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
            routing_backend="AstBackend",
            routing_reason=routing_reason,
            routing_distributed=False,
            routing_worker_count=1,
        )

    def is_available(self) -> bool:
        """Check if tree-sitter is installed.

        AstBackend.search() is pure tree-sitter query matching -- it never touches torch,
        CUDA, or any graph-learning library (the dead GNN/tensor-graph path, `_ast_to_graph`,
        was audited as unreachable and removed). GPU/torch are therefore not part of this
        backend's availability contract; gating a fully-functional CPU backend behind an
        unrelated GPU dependency was itself the bug.
        """
        try:
            import importlib.util

            return importlib.util.find_spec("tree_sitter") is not None
        except ImportError:
            return False

    def _get_parser(self, lang: str) -> Any:
        import tree_sitter

        lang = normalize_ast_language(lang)
        if lang in self._parsers:
            return self._parsers[lang]

        parser = tree_sitter.Parser()
        try:
            if lang == "python":
                import tree_sitter_python

                parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))
            elif lang == "javascript":
                import tree_sitter_javascript

                parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language()))
            elif lang == "typescript":
                import tree_sitter_typescript

                parser = tree_sitter.Parser(
                    tree_sitter.Language(tree_sitter_typescript.language_typescript())
                )
            elif lang == "tsx":
                import tree_sitter_typescript

                parser = tree_sitter.Parser(
                    tree_sitter.Language(tree_sitter_typescript.language_tsx())
                )
            elif lang == "rust":
                import tree_sitter_rust

                parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
            else:
                raise ValueError(
                    f"Language '{lang}' is supported by the ast-grep wrapper but not by "
                    "the native AstBackend."
                )
        except Exception as e:
            raise RuntimeError(f"Failed to load tree-sitter grammar for {lang}: {e}") from e

        self._parsers[lang] = parser
        return parser

    def _get_query(self, parser: Any, lang: str, pattern: str) -> Any:
        cache_key = (lang, pattern)
        if cache_key in self._queries:
            query = self._queries.pop(cache_key)
            self._queries[cache_key] = query
            return query

        query = parser.language.query(f"({pattern}) @match")
        self._queries.pop(cache_key, None)
        self._queries[cache_key] = query
        while len(self._queries) > self._query_cache_max_entries():
            self._queries.popitem(last=False)
        return query

    def _get_parsed_source(
        self, parser: Any, file_path: str, lang: str
    ) -> tuple[bytes, list[str], Any]:
        cached = self._get_cached_parsed_source_entry(file_path, lang)
        if cached is not None:
            return cached

        cache_signature = self._build_file_signature(file_path)

        with open(file_path, "rb") as f:
            source_bytes = f.read()

        tree = parser.parse(source_bytes)
        lines = source_bytes.decode("utf-8").splitlines()
        self._store_parsed_source_cache_entry(
            file_path,
            lang,
            cache_signature,
            source_bytes,
            lines,
            tree,
        )
        return source_bytes, lines, tree

    def _get_cached_lines(self, file_path: str, lang: str) -> list[str] | None:
        cached = self._get_cached_parsed_source_entry(file_path, lang)
        if cached is not None:
            return cached[1]
        return None

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            # Backend Fail-Closed Contract (base.py): a real failure must raise
            # BackendExecutionError, never fall through to a silent-empty result.
            raise BackendExecutionError("AstBackend requires tree-sitter to be installed.")

        lang = "python"
        if config and hasattr(config, "lang") and config.lang:
            lang = config.lang
        elif file_path.endswith(".js") or file_path.endswith(".ts"):
            lang = "javascript"

        persistent_cached_result = self._load_persistent_cached_result(file_path, lang, pattern)
        if persistent_cached_result is not None:
            return self._cap_to_max_count(persistent_cached_result, config)

        if self._is_simple_node_type_pattern(pattern):
            node_type_index = self._load_persistent_node_type_index(file_path, lang)
            if node_type_index is not None and pattern in node_type_index:
                lines = self._get_cached_lines(file_path, lang)
                if lines is None:
                    lines = Path(file_path).read_text(encoding="utf-8").splitlines()
                result = self._build_matches_from_line_numbers(
                    file_path,
                    lines,
                    node_type_index.get(pattern, []),
                    "ast_structural_index_cache",
                )
                if result.total_matches > 0:
                    self._persist_result_cache(file_path, lang, pattern, result)
                    return self._cap_to_max_count(result, config)

        parser = self._get_parser(lang)
        _source_bytes, lines, tree = self._get_parsed_source(parser, file_path, lang)
        if self._is_simple_node_type_pattern(pattern):
            node_type_index = self._build_node_type_index(tree.root_node)
            self._persist_node_type_index(file_path, lang, node_type_index)
            result = self._build_matches_from_line_numbers(
                file_path,
                lines,
                node_type_index.get(pattern, []),
                "ast_structural_index",
            )
            if result.total_matches > 0:
                self._persist_result_cache(file_path, lang, pattern, result)
                return self._cap_to_max_count(result, config)

        try:
            query = self._get_query(parser, lang, pattern)
        except Exception as exc:
            # Audit MED: a broad `except Exception` here converted a malformed/misspelled AST
            # node-type pattern into a look-alike 0-match result with zero logging — a
            # silent false negative (sibling of the SILENT-FALLBACK class). Raise per base.py's
            # contract so run_command's except-BackendExecutionError handler reports a real
            # "invalid pattern" error instead of "0 matches".
            raise BackendExecutionError(
                f"AST query compilation failed (invalid pattern for language {lang!r}?): {exc}"
            ) from exc

        matches = []
        seen_lines = set()

        # We perform actual structural matching using tree-sitter queries instead of naive hash
        # to fix the ast matching accuracy issue
        if hasattr(query, "captures"):
            captures = query.captures(tree.root_node)
        else:
            import tree_sitter

            cursor = tree_sitter.QueryCursor(query)
            captures = cursor.captures(tree.root_node)

        if isinstance(captures, dict):
            capture_nodes = []
            for nodes in captures.values():
                capture_nodes.extend(nodes)
            iter_nodes = ((node, None) for node in capture_nodes)
        else:
            iter_nodes = captures

        for node, _ in iter_nodes:
            line_num = node.start_point[0] + 1
            if line_num not in seen_lines and line_num <= len(lines):
                seen_lines.add(line_num)
                matches.append(
                    MatchLine(line_number=line_num, text=lines[line_num - 1], file=file_path)
                )

        logger.debug("AST search completed for %s with %d matches", file_path, len(matches))

        matches.sort(key=lambda m: m.line_number)

        result = SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
            routing_backend="AstBackend",
            routing_reason="ast_structural_match",
            routing_distributed=False,
            routing_worker_count=1,
        )
        self._persist_result_cache(file_path, lang, pattern, result)
        return self._cap_to_max_count(result, config)
