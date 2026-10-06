"""Order-only production filename preferences shared by context and edit planning."""

from pathlib import Path
from typing import Any

from tensor_grep.core.retrieval_lexical import split_terms


def _symbol_span_length(symbol: dict[str, Any]) -> int:
    line = int(symbol.get("line", symbol.get("start_line", 0)) or 0)
    start_line = int(symbol.get("start_line", line) or line)
    end_line = int(symbol.get("end_line", start_line) or start_line)
    return max(1, end_line - start_line + 1)


def _symbol_rank_key(symbol: dict[str, Any]) -> tuple[int, int, int, int, int, str, int, str]:
    if bool(symbol.get("exact_query_match")):
        query_match_rank = 0
    elif bool(symbol.get("bridge_query_match")):
        query_match_rank = 1
    elif bool(symbol.get("covered_query_match")):
        query_match_rank = 2
    else:
        query_match_rank = 3
    return (
        0 if query_match_rank < 2 else (1 if symbol.get("filename_phrase_match") else 2),
        query_match_rank,
        -int(symbol.get("score", 0)),
        0 if str(symbol.get("kind")) == "function" else 1,
        -_symbol_span_length(symbol),
        str(symbol.get("file")),
        int(symbol.get("line", 0)),
        str(symbol.get("name")),
    )


def _filename_phrase_matches(path: str, query: str) -> bool:
    stem_terms = split_terms(Path(path).stem)
    query_terms = split_terms(query)
    count = len(stem_terms)
    return count >= 2 and any(
        query_terms[index : index + count] == stem_terms
        for index in range(len(query_terms) - count + 1)
    )


def _promote_filename_phrase(
    scored_files: list[tuple[int, str]],
    scored_symbols: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    query: str,
    query_language_hints: list[str],
    deweighted_trees: dict[str, dict[str, Any]],
    file_reasons: dict[str, list[str]],
) -> None:
    """Stably move one eligible winner and its symbols without changing any scores.

    Inputs already have the normal score/path order. Symbol intent suppresses this
    preference entirely; classified vendor/generated/test files remain ineligible.
    """
    from tensor_grep.cli import repo_map
    from tensor_grep.cli.orient_capsule import _STRONG0_VENDOR_DIR_NAMES, _TOOL_CONFIG_DIR_NAMES

    if any(
        item.get("exact_query_match") or item.get("bridge_query_match") for item in scored_symbols
    ):
        return
    root = repo_map._repo_map_root_dir(payload)
    for index, (_, path) in enumerate(scored_files):
        candidate = Path(path)
        if not _filename_phrase_matches(path, query):
            continue
        if (
            not candidate.is_relative_to(root)
            or repo_map._is_test_file(candidate)
            or repo_map._path_has_vendor_component(candidate, root)
            or any(
                part.lower() in _STRONG0_VENDOR_DIR_NAMES | _TOOL_CONFIG_DIR_NAMES
                for part in candidate.relative_to(root).parts[:-1]
            )
            or repo_map._target_language_for_path(path) is None
            or any(candidate.is_relative_to(tree_root) for tree_root in deweighted_trees)
            or (
                query_language_hints
                and not repo_map._path_matches_query_language_hints(path, query_language_hints)
            )
        ):
            continue
        scored_files.insert(0, scored_files.pop(index))
        repo_map._append_reason(file_reasons, path, "filename-phrase")
        winners = [item for item in scored_symbols if str(item.get("file")) == path]
        for item in winners:
            item["filename_phrase_match"] = True
        scored_symbols[:] = winners + [
            item for item in scored_symbols if str(item.get("file")) != path
        ]
        return
