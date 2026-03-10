from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import torch

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

logger = logging.getLogger(__name__)


class AstBackend(ComputeBackend):
    """
    A Graph Neural Network (GNN) backend that parses source code into an Abstract Syntax Tree (AST)
    using tree-sitter, converts the AST into a geometric graph tensor, and then performs parallel
    subgraph isomorphism matching directly in GPU VRAM using PyTorch Geometric.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._queries: dict[tuple[str, str], Any] = {}
        self._parsed_source_cache: dict[
            tuple[str, str], tuple[tuple[int, int], bytes, list[str], Any]
        ] = {}

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

    def _build_file_signature(self, file_path: str) -> tuple[int, int]:
        stat_result = os.stat(file_path)
        return stat_result.st_mtime_ns, stat_result.st_size

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
        node_type_index: dict[str, set[int]] = {}

        def traverse(node: Any) -> None:
            node_type_index.setdefault(node.type, set()).add(node.start_point[0] + 1)
            for child in node.children:
                traverse(child)

        traverse(root_node)
        return {
            node_type: sorted(line_numbers) for node_type, line_numbers in node_type_index.items()
        }

    def _load_persistent_node_type_index(
        self, file_path: str, lang: str
    ) -> dict[str, list[int]] | None:
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
        return normalized

    def _persist_node_type_index(
        self, file_path: str, lang: str, node_type_index: dict[str, list[int]]
    ) -> None:
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
        """Check if torch-geometric and tree-sitter are installed."""
        try:
            import importlib.util

            if not importlib.util.find_spec("torch_geometric") or not importlib.util.find_spec(
                "tree_sitter"
            ):
                return False

            import torch

            return bool(torch.cuda.is_available())
        except ImportError:
            return False

    def _get_parser(self, lang: str) -> Any:
        import tree_sitter

        if lang in self._parsers:
            return self._parsers[lang]

        parser = tree_sitter.Parser()
        try:
            if lang == "python":
                import tree_sitter_python

                parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_python.language()))
            elif lang == "javascript" or lang == "js":
                import tree_sitter_javascript

                parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language()))
            else:
                raise ValueError(f"Language '{lang}' is not yet supported by the AstBackend.")
        except Exception as e:
            raise RuntimeError(f"Failed to load tree-sitter grammar for {lang}: {e}") from e

        self._parsers[lang] = parser
        return parser

    def _get_query(self, parser: Any, lang: str, pattern: str) -> Any:
        cache_key = (lang, pattern)
        if cache_key in self._queries:
            return self._queries[cache_key]

        query = parser.language.query(f"({pattern}) @match")
        self._queries[cache_key] = query
        return query

    def _get_parsed_source(
        self, parser: Any, file_path: str, lang: str
    ) -> tuple[bytes, list[str], Any]:
        cache_key = (file_path, lang)
        cache_signature = self._build_file_signature(file_path)
        cached = self._parsed_source_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            _, source_bytes, lines, tree = cached
            return source_bytes, lines, tree

        with open(file_path, "rb") as f:
            source_bytes = f.read()

        tree = parser.parse(source_bytes)
        lines = source_bytes.decode("utf-8").split("\n")
        self._parsed_source_cache[cache_key] = (cache_signature, source_bytes, lines, tree)
        return source_bytes, lines, tree

    def _ast_to_graph(
        self, root_node: Any, source_bytes: bytes
    ) -> tuple["torch.Tensor", "torch.Tensor", list[int]]:
        """
        Converts a tree-sitter AST into a PyTorch Geometric Graph (edge_index, node_features).
        Returns:
            edge_index: [2, num_edges] long tensor.
            node_features: [num_nodes, feature_dim] float tensor.
            line_numbers: A mapping from node index back to the source code line number.
        """
        edges = []
        features: list[list[float]] = []
        line_numbers = []

        node_type_map = {}  # In a real model, this would be a loaded embedding dictionary

        def traverse(node: "Any", parent_idx: int = -1) -> None:
            current_idx = len(features)

            # Simple feature representation: Hash the node type string to a pseudo-embedding
            # A true production model uses Word2Vec or CodeBERT embeddings here
            node_type = node.type
            if node_type not in node_type_map:
                node_type_map[node_type] = float(hash(node_type) % 1000) / 1000.0

            features.append([node_type_map[node_type]])
            line_numbers.append(node.start_point[0] + 1)

            if parent_idx != -1:
                edges.append([parent_idx, current_idx])
                edges.append([current_idx, parent_idx])  # Bidirectional for GNNs

            for child in node.children:
                traverse(child, current_idx)

        traverse(root_node)

        import torch

        edge_index = (
            torch.tensor(edges, dtype=torch.long).t().contiguous()
            if edges
            else torch.empty((2, 0), dtype=torch.long)
        )
        x = torch.tensor(features, dtype=torch.float)

        return edge_index, x, line_numbers

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError(
                "AstBackend requires torch-geometric and tree-sitter to be installed."
            )

        lang = "python"
        if config and hasattr(config, "lang") and config.lang:
            lang = config.lang
        elif file_path.endswith(".js") or file_path.endswith(".ts"):
            lang = "javascript"

        persistent_cached_result = self._load_persistent_cached_result(file_path, lang, pattern)
        if persistent_cached_result is not None:
            return persistent_cached_result

        if self._is_simple_node_type_pattern(pattern):
            node_type_index = self._load_persistent_node_type_index(file_path, lang)
            if node_type_index is not None and pattern in node_type_index:
                lines = Path(file_path).read_text(encoding="utf-8").split("\n")
                result = self._build_matches_from_line_numbers(
                    file_path,
                    lines,
                    node_type_index.get(pattern, []),
                    "ast_structural_index_cache",
                )
                if result.total_matches > 0:
                    self._persist_result_cache(file_path, lang, pattern, result)
                    return result

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
                return result

        try:
            query = self._get_query(parser, lang, pattern)
        except Exception as exc:
            raise ValueError(f"Invalid AST query pattern for language '{lang}': {pattern}") from exc

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
        return result
