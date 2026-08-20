"""Rust-specific import, module and symbol resolution extracted from `repo_map`.

Everything here is Rust dialect knowledge -- `use`/`mod` binding resolution, crate and
workspace module trees, `impl` block ownership, and the `#[test]` / `#[tokio::test]`
attribute scan -- plus the private helpers only those paths call. Split out of
`repo_map.py` under docs/design/2026-08-19-split-floor-escape.md.

`_rust_parser` and `_rust_classify_ref_kind` deliberately stay in `repo_map`: the test
suite monkeypatches both there, so a moved copy would be the unpatched one.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.repo_map_cache import _mtime_aware_cache as _mtime_aware_cache

# Route A late binding (docs/design/2026-08-19-split-floor-escape.md). `_self` is
# `tensor_grep.cli.repo_map`, NOT this module: the test suite patches names there, and a
# bare call resolved through this file's globals would run the unpatched original while the
# test still passed. A plain import would be circular (repo_map imports this module at its
# top), so the runtime branch resolves through sys.modules on each attribute read. The
# TYPE_CHECKING branch never executes; it is what keeps mypy on the real signatures.
if TYPE_CHECKING:
    from tensor_grep.cli import repo_map as _self
else:

    class _RepoMapProxy:
        """Late-binding view of `tensor_grep.cli.repo_map`."""

        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            module = sys.modules.get("tensor_grep.cli.repo_map")
            if module is None:
                module = importlib.import_module("tensor_grep.cli.repo_map")
            return getattr(module, name)

    _self = _RepoMapProxy()


def _rust_use_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:pub\s+)?use\s+([^;]+);", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(source):
        start_line, end_line = _self._line_span_from_offsets(source, match.start(), match.end())
        for item in _self._flatten_rust_use_items(match.group(1)):
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.endswith("::*"):
                module_glob = normalized[:-3].strip()
                if not _self._is_valid_rust_use_path(module_glob):
                    continue
                bindings.append({
                    "module": module_glob,
                    "wildcard": True,
                    "start_line": start_line,
                    "end_line": end_line,
                })
                continue

            if " as " in normalized:
                imported_path, local_name = (part.strip() for part in normalized.rsplit(" as ", 1))
            else:
                imported_path = normalized
                local_name = normalized.rsplit("::", 1)[-1].strip()

            # Reject false-positive matches (e.g. the word ``use`` inside a doc
            # comment) so downstream path resolution never receives whitespace.
            if not _self._is_valid_rust_use_path(imported_path):
                continue

            if "::" in imported_path:
                module_name, imported_name = imported_path.rsplit("::", 1)
            else:
                module_name = ""
                imported_name = imported_path

            bindings.append({
                "module": module_name.strip(),
                "imported": imported_name.strip(),
                "local": local_name.strip(),
                "path": imported_path.strip(),
                "wildcard": False,
                "start_line": start_line,
                "end_line": end_line,
            })
    return bindings


def _rust_mod_declarations(source: str) -> list[str]:
    pattern = re.compile(
        r"^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$",
        re.MULTILINE,
    )
    return [match.group(1).strip() for match in pattern.finditer(source)]


def _rust_module_base_dir(module_file: Path) -> Path:
    if module_file.name in {"lib.rs", "main.rs", "mod.rs"}:
        return module_file.parent.resolve()
    return (module_file.parent / module_file.stem).resolve()


def _rust_module_file_for_declaration(module_file: Path, module_name: str) -> Path | None:
    base_dir = _rust_module_base_dir(module_file)
    candidates = [
        (base_dir / f"{module_name}.rs").resolve(),
        (base_dir / module_name / "mod.rs").resolve(),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _rust_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _self._normalized_repo_root(repo_root)
    if normalized_root is None:
        return {
            "root": None,
            "workspace": {
                "exists": False,
                "members": {},
            },
            "mod_tree_cache": {},
        }
    cached = _self._get_repo_context_cache_entry(_self._RUST_REPO_CONTEXTS, str(normalized_root))
    if cached is not None:
        return cached
    return _self._prime_rust_repo_context(normalized_root)


def _rust_crate_entry_for_path(path: Path) -> Path | None:
    normalized_path = path.expanduser().resolve()
    candidates = [normalized_path.parent, *normalized_path.parents]
    src_root = next((parent for parent in candidates if parent.name == "src"), None)
    if src_root is None:
        return None
    lib_path = (src_root / "lib.rs").resolve()
    if lib_path.is_file():
        return lib_path
    main_path = (src_root / "main.rs").resolve()
    if main_path.is_file():
        return main_path
    if normalized_path.name in {"lib.rs", "main.rs"} and normalized_path.parent == src_root:
        return normalized_path
    return None


def _rust_module_tree_for_entry(
    entry_path: Path,
    repo_root: Path | str | None = None,
) -> dict[str, str]:
    normalized_entry = entry_path.expanduser().resolve()
    context = _rust_repo_context(repo_root if repo_root is not None else normalized_entry.parent)
    cache_key = str(normalized_entry)
    cached = context["mod_tree_cache"].get(cache_key)
    if cached is not None:
        return dict(cached)
    module_tree = _self._build_rust_module_tree(normalized_entry)
    context["mod_tree_cache"][cache_key] = dict(module_tree)
    return module_tree


def _rust_workspace_entry_for_crate(
    crate_name: str,
    repo_root: Path | str | None = None,
) -> Path | None:
    context = _rust_repo_context(repo_root)
    members = context.get("workspace", {}).get("members", {})
    member_path = members.get(_self._normalize_rust_crate_name(crate_name))
    return Path(str(member_path)).expanduser().resolve() if member_path else None


def _rust_module_tree_lookup(
    entry_path: Path,
    module_parts: list[str],
    repo_root: Path | str | None = None,
) -> Path | None:
    if not module_parts:
        return entry_path.expanduser().resolve()
    module_tree = _rust_module_tree_for_entry(entry_path, repo_root)
    resolved = module_tree.get("::".join(module_parts))
    return Path(resolved).expanduser().resolve() if resolved else None


def _rust_partial_candidate_paths(
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    normalized_root = _self._normalized_repo_root(repo_root)
    if normalized_root is None:
        return []

    normalized_definition = Path(definition_path).expanduser().resolve()
    inferred_candidates: list[dict[str, Any]] = []
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not module_parts or module_parts[0] in {"crate", "self", "super"}:
        return inferred_candidates

    external_entry = (normalized_root / module_parts[0] / "src" / "lib.rs").resolve()
    if not external_entry.is_file():
        fallback_entry = (normalized_root / module_parts[0] / "src" / "main.rs").resolve()
        if fallback_entry.is_file():
            external_entry = fallback_entry
        else:
            return inferred_candidates

    candidate_path = _rust_module_tree_lookup(external_entry, module_parts[1:], normalized_root)
    if candidate_path is None and not module_parts[1:]:
        candidate_path = external_entry
    if candidate_path is not None and candidate_path == normalized_definition:
        provenance = ["partial-resolution"]
        if module_parts[1:]:
            provenance.append("mod-declaration")
        inferred_candidates.append({
            "path": str(candidate_path),
            "provenance": provenance,
            "confidence": 0.2,
        })
    return inferred_candidates


def _rust_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not parts:
        return []

    normalized_root = _self._normalized_repo_root(repo_root)
    normalized_importer = importer_path.expanduser().resolve()
    candidates: list[dict[str, Any]] = []

    def _add_candidate(path: Path | None, provenance: list[str], confidence: float) -> None:
        if path is None:
            return
        resolved_path = str(path.expanduser().resolve())
        if any(str(current["path"]) == resolved_path for current in candidates):
            return
        candidates.append({
            "path": resolved_path,
            "provenance": list(provenance),
            "confidence": float(confidence),
        })

    crate_entry = _rust_crate_entry_for_path(normalized_importer)
    if parts[0] == "crate" and crate_entry is not None:
        _add_candidate(
            _rust_module_tree_lookup(crate_entry, parts[1:], normalized_root),
            ["mod-declaration"] if parts[1:] else [],
            0.95,
        )
    elif normalized_root is not None:
        workspace_entry = _rust_workspace_entry_for_crate(parts[0], normalized_root)
        if workspace_entry is not None:
            _add_candidate(
                _rust_module_tree_lookup(workspace_entry, parts[1:], normalized_root),
                ["workspace-crate", *(["mod-declaration"] if parts[1:] else [])],
                0.92,
            )

    start = normalized_importer.parent
    while start.name == "":
        start = start.parent

    heuristic_parts = list(parts)
    if heuristic_parts[0] == "crate":
        crate_root = next(
            (
                parent
                for parent in [normalized_importer.parent, *normalized_importer.parents]
                if parent.name == "src"
            ),
            None,
        )
        if crate_root is not None:
            start = crate_root.resolve()
        heuristic_parts = heuristic_parts[1:]
    else:
        while heuristic_parts and heuristic_parts[0] == "super":
            start = start.parent
            heuristic_parts = heuristic_parts[1:]
        if heuristic_parts and heuristic_parts[0] == "self":
            heuristic_parts = heuristic_parts[1:]

    if heuristic_parts:
        base = start.joinpath(*heuristic_parts).resolve()
        # Defensive guard: a malformed module name (e.g. a mis-parsed doc
        # comment) can yield a base path whose final component has an empty
        # name, which makes ``with_suffix`` raise ``ValueError``. Skip the
        # ``.rs`` sibling in that case rather than crashing symbol lookup.
        try:
            rust_sibling = base.with_suffix(".rs")
        except ValueError:
            rust_sibling = None
        _add_candidate(rust_sibling, [], 1.0)
        _add_candidate(base / "mod.rs", [], 1.0)

    return candidates


def _rust_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    resolved_definition = str(Path(definition_path).expanduser().resolve())
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 1.0)),
            }

    for candidate in _rust_partial_candidate_paths(module_name, definition_path, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 0.2)),
            }

    if module_parts and module_parts[0] in {"crate", "self", "super"}:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if module_parts and _rust_workspace_entry_for_crate(module_parts[0], repo_root) is not None:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if _self._module_path_matches_definition(module_name, definition_path):
        return {
            "matched": True,
            "provenance": ["partial-resolution"],
            "confidence": 0.2,
        }
    return {"matched": False, "provenance": [], "confidence": 0.0}


def _rust_use_binding_match_details(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    definition_stem = Path(definition_path).with_suffix("").name.lower()
    if not (
        bool(binding.get("wildcard"))
        or imported_name.lower() == symbol.lower()
        or local_name.lower() == symbol.lower()
        or imported_name.lower() == definition_stem
    ):
        return None

    for module_name in [str(binding.get("module", "")), str(binding.get("path", ""))]:
        if not module_name:
            continue
        details = _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )
        if details["matched"]:
            return {
                "provenance": list(details.get("provenance", [])),
                "confidence": float(details.get("confidence", 1.0)),
            }
    return None


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_impl_method_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    path = Path(definition_path)
    if path.suffix not in _self._RUST_SUFFIXES:
        return ()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()

    impl_pattern = re.compile(
        rf"\bimpl(?:\s*<[^{{>]*>)?\s+{re.escape(symbol)}(?:\s*<[^{{>]*>)?\s*\{{",
        re.MULTILINE,
    )
    method_pattern = re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)")
    candidates: list[str] = []
    for match in impl_pattern.finditer(source):
        block = _self._extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        candidates.extend(method_match.group(1) for method_match in method_pattern.finditer(block))
    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_impl_owner_type(definition_path: str, line_number: int) -> str | None:
    path = Path(definition_path)
    if path.suffix not in _self._RUST_SUFFIXES:
        return None
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    impl_pattern = re.compile(
        r"\bimpl(?:\s*<[^{}>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^{}>]*>)?\s*\{",
        re.MULTILINE,
    )
    for match in impl_pattern.finditer(source):
        start_line = source.count("\n", 0, match.start()) + 1
        block = _self._extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        end_index = match.end() - 1 + len(block) + 1
        end_line = source.count("\n", 0, end_index) + 1
        if start_line <= line_number <= end_line:
            return match.group(1)
    return None


@_mtime_aware_cache(maxsize=512)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_symbol_reference_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    candidates = [symbol]
    candidates.extend(_rust_impl_method_candidates(definition_path, symbol))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _rust_file_references_symbol_from_definition(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> bool:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    reference_candidates = _rust_symbol_reference_candidates(definition_path, symbol)
    if not reference_candidates:
        return False

    for candidate in reference_candidates:
        if re.search(rf"\b{re.escape(candidate)}\b", source):
            return True
        if re.search(rf"(?:\.|::){re.escape(candidate)}\s*\(", source):
            return True
    return False


def _rust_resolve_use_binding(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    repo_root: Path | str | None = None,
    _seen: frozenset[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    # Cycle guard. This function follows `use` re-export chains by recursing on
    # the nested binding, and Rust re-exports are a GRAPH, not a tree: a crate
    # root that does `pub use rules::x::{...}` while a submodule re-exports back
    # toward the root closes a loop, and the recursion never terminates.
    #
    # Receipt: `tg refs . <symbol> --json` on a 594-file Rust workspace
    # (claude-code-hydron) died with `RecursionError: maximum recursion depth
    # exceeded`, ~1000 frames of this function, rc=1 and no stdout -- so it took
    # out EVERY refs query on that repo, not just the cyclic symbol.
    #
    # It DOES reproduce on a two-module fixture, but only when the searched
    # symbol differs from the re-exported name: a matching name hits the early
    # return above the recursive call. See
    # test_cyclic_rust_pub_use_reexport_terminates.
    #
    # The key is (resolved importer path, imported name): the same file reached
    # again for the same name is a cycle, while the same file for a DIFFERENT
    # name is legitimate work and must not be pruned.
    key = (str(importer_path).casefold(), str(binding.get("imported", "")).casefold())
    seen = _seen or frozenset()
    if key in seen:
        return None
    seen = seen | {key}
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    wildcard = bool(binding.get("wildcard"))

    module_names = [str(binding.get("module", "")), str(binding.get("path", ""))]
    for module_name in module_names:
        if not module_name:
            continue
        for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
            if not str(candidate.get("path")):
                continue
            candidate_path = Path(str(candidate["path"]))
            if (
                wildcard
                or imported_name.lower() == symbol.lower()
                or local_name.lower() == symbol.lower()
            ):
                return {
                    "symbol": symbol,
                    "definition_file": str(candidate_path),
                    "provenance": list(candidate.get("provenance", [])),
                    "confidence": float(candidate.get("confidence", 1.0)),
                }
            try:
                candidate_source = candidate_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for nested_binding in _rust_use_bindings(candidate_source):
                nested_imported = str(nested_binding.get("imported", ""))
                nested_local = str(nested_binding.get("local", ""))
                if imported_name.lower() not in {nested_imported.lower(), nested_local.lower()}:
                    continue
                nested_resolved = _rust_resolve_use_binding(
                    candidate_path, nested_binding, symbol, repo_root, seen
                )
                if nested_resolved is None:
                    continue
                return {
                    "symbol": symbol,
                    "definition_file": str(nested_resolved.get("definition_file", candidate_path)),
                    "provenance": list(candidate.get("provenance", []))
                    + list(nested_resolved.get("provenance", [])),
                    "confidence": min(
                        float(candidate.get("confidence", 1.0)),
                        float(nested_resolved.get("confidence", 1.0)),
                    ),
                }
    return None


def _rust_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    return bool(
        _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )["matched"]
    )


def _rust_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    bindings = _rust_use_bindings(source)
    if any(
        _rust_use_binding_match_details(
            file_path,
            binding,
            symbol,
            definition_path,
            repo_root,
        )
        is not None
        for binding in bindings
    ):
        return True
    if _self._is_test_file(file_path):
        return _rust_file_references_symbol_from_definition(file_path, symbol, definition_path)
    return False


def _rust_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    for binding in _rust_use_bindings(source):
        if (
            _rust_use_binding_match_details(
                file_path,
                binding,
                symbol,
                definition_path,
                repo_root,
            )
            is None
        ):
            continue
        return {
            "start_line": int(binding.get("start_line", 0)),
            "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
            "module": str(binding.get("module", "")) or str(binding.get("path", "")),
            "provenance": "heuristic",
        }
    return None


def _rust_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _self._RUST_SUFFIXES:
        return []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        kind_map = {
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
        }
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                name = _node_text(name_node)
                if _self._is_clean_symbol_name(name):
                    symbols.append(
                        _self._symbol_record(
                            name=name,
                            kind=kind_map[node.type],
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return symbols


def _rust_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _self._RUST_SUFFIXES:
        return [], []

    try:
        source = _self._read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return [], []

    # PERF increment 1 / Section B mirror (Fable-designed): same alias-aware early exit as
    # _js_ts_references_and_calls above -- bindings only need the source TEXT, so they're
    # resolved before the parse, and a symbol-absent file with no matching `use` binding skips
    # tree-sitter parsing entirely.
    bindings = _rust_use_bindings(source)
    local_name_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        local_name_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            local_name_resolution_by_name.setdefault(symbol, dict(resolved_import))
    local_names = {name for name in local_name_resolution_by_name if name}

    if symbol not in source and not local_names:
        return [], []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    parsed_source, source_bytes, tree = parsed
    # `lines` MUST come from the SAME read as `source_bytes`/`tree` (all three from the single
    # `_parsed_source_and_tree` product), NOT from the earlier `source` text read above: the two are
    # independent (path, mtime, size)-keyed cache lookups, so a file edited between them would leave
    # tree node line-indices (from the parse) indexing into stale `lines` -> wrong reported line
    # content / IndexError. The pre-parse text read keeps using `source` (a cheap heuristic gate).
    lines = parsed_source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        return bool(parent.type in {"function_item", "struct_item", "enum_item", "trait_item"})

    def _walk(node: Any) -> None:
        node_type = node.type
        if node_type == "identifier":
            node_text = _node_text(node)
            alias_resolution = local_name_resolution_by_name.get(node_text)
            if (
                (node_text == symbol or node_text in local_names)
                and not _is_definition_identifier(node)
                and not _self._node_has_ancestor_type(node, {"use_declaration"})
            ):
                try:
                    ref_kind = _self._rust_classify_ref_kind(node)
                except Exception:
                    # F20: a classifier bug must only default THIS row to "value", never drop
                    # every reference in the file.
                    ref_kind = "value"
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": ref_kind,
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(alias_resolution.get("provenance", [])),
                            "resolution_confidence": float(
                                alias_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_resolution
                        else {}
                    ),
                })
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            call_resolution: dict[str, Any] | None = None
            if function_node is not None:
                if function_node.type == "identifier":
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or function_name in local_names
                    call_resolution = local_name_resolution_by_name.get(function_name)
                elif function_node.type == "field_expression":
                    field_node = function_node.child_by_field_name("field")
                    matched = bool(field_node is not None and _node_text(field_node) == symbol)
                elif function_node.type == "scoped_identifier":
                    name_node = function_node.child_by_field_name("name")
                    matched = bool(name_node is not None and _node_text(name_node) == symbol)
            if matched:
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(call_resolution.get("provenance", [])),
                            "resolution_confidence": float(call_resolution.get("confidence", 0.95)),
                        }
                        if call_resolution
                        else {}
                    ),
                })
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(call_resolution.get("provenance", [])),
                            "resolution_confidence": float(call_resolution.get("confidence", 0.95)),
                        }
                        if call_resolution
                        else {}
                    ),
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _rust_provider_alias_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    include_assignment_wrappers: bool = False,
) -> list[dict[str, Any]]:
    if path.suffix not in _self._RUST_SUFFIXES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    bindings = _rust_use_bindings(source)
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        alias_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            alias_resolution_by_name.setdefault(symbol, dict(resolved_import))
    alias_names = {name for name in alias_resolution_by_name if name}

    def _strip_rust_string_and_comment_noise(line: str) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
        escaped = False

        for index, char in enumerate(line):
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if in_single:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_single = False
                cleaned.append(" ")
                continue
            if in_double:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_double = False
                cleaned.append(" ")
                continue
            if char == "/" and next_char == "/":
                break
            if char == "'":
                in_single = True
                cleaned.append(" ")
                continue
            if char == '"':
                in_double = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    if include_assignment_wrappers:
        assignment_pattern = re.compile(
            r"\blet\s+(?:mut\s+)?(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_:]*)\b"
        )
        changed = True
        while changed:
            changed = False
            for line in lines:
                match = assignment_pattern.search(line)
                if match is None:
                    continue
                value_name = match.group("value").split("::")[-1]
                local_name = match.group("local")
                if value_name not in alias_names or local_name in alias_names:
                    continue
                alias_names.add(local_name)
                alias_resolution_by_name[local_name] = dict(
                    alias_resolution_by_name.get(value_name, {})
                )
                changed = True

    calls: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        sanitized_line = _strip_rust_string_and_comment_noise(line)
        for alias_name in sorted(alias_names):
            alias_match = re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line)
            if alias_match is None:
                continue
            # Task 326 (REOPENED): `\bNAME\s*\(` also matches `fn NAME(` / `function NAME(` -- a
            # DECLARATION, not a call site. The first fix for this put the guard in
            # `_regex_references_and_calls`, which sits LATER in the fallback chain and is never
            # reached on a wheel install (no tree-sitter grammar -> the AST arm returns nothing ->
            # THIS arm answers and stops the chain), so the defect shipped in v1.99.1 with green
            # unit tests. Reuses the one shared `_DEFINITION_KEYWORD_BEFORE_SYMBOL` constant
            # rather than a second regex, so the arms cannot drift apart again.
            if _self._DEFINITION_KEYWORD_BEFORE_SYMBOL.search(
                sanitized_line[: alias_match.start()]
            ):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "end_line": line_number,
                "text": line,
                "alias": alias_name,
                "resolution_provenance": list(alias_resolution.get("provenance", [])),
                "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
            })
    calls.sort(
        key=lambda item: (item["file"], item["line"], str(item.get("alias", "")), item["text"])
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for call_entry in calls:
        key = (
            str(call_entry["file"]),
            int(call_entry["line"]),
            int(call_entry.get("end_line", call_entry["line"])),
            str(call_entry.get("alias", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_entry)
    return deduped


def _rust_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _self._RUST_SUFFIXES:
        return []

    parser = _self._rust_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []
    kind_map = {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
    }

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": kind_map[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _rust_references_and_calls_for_registry(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # definition_dirs is part of the uniform registry adapter signature (Go F25); rust
    # ignores it.
    return _rust_references_and_calls(path, symbol, repo_root)


def _rust_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _self._RUST_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _self._max_parse_bytes():
        return []
    try:
        lines = _self._read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        # Same single-line `use ... ;` regex as `_regex_imports_and_symbols` -- a brace-group
        # `use` spanning multiple lines is a pre-existing extraction gap there too, not a new
        # one introduced here (recall gaps must stay honest, not silently "fixed" here only).
        use_match = re.match(r"^\s*use\s+([^;]+);", line)
        if use_match:
            entries.append({"module": use_match.group(1).strip(), "line": line_number})
    return entries


def _rust_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    doc_comment_lines: set[int] = set()
    attribute_lines: set[int] = set()
    in_attribute = False
    attribute_bracket_balance = 0

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("///") or stripped.startswith("//!"):
            doc_comment_lines.add(line_number)

        if not in_attribute and re.match(r"^#\[\s*(derive|cfg|allow)\b", stripped):
            in_attribute = True
            attribute_bracket_balance = 0

        if in_attribute:
            attribute_lines.add(line_number)
            attribute_bracket_balance += line.count("[") - line.count("]")
            if attribute_bracket_balance <= 0:
                in_attribute = False

    return doc_comment_lines, attribute_lines


def _rust_test_attribute_kind(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    after_hash = stripped[1:].lstrip()
    if not after_hash.startswith("[") or not after_hash.endswith("]"):
        return None
    inner = after_hash[1:-1].strip()
    attribute_name = inner.split("(", 1)[0].strip()
    if attribute_name in {"test", "tokio::test"}:
        return attribute_name
    return None


def _rust_test_function_candidates_from_source(
    source: str,
    *,
    tokio_only: bool,
) -> tuple[str, ...]:
    candidates: list[str] = []
    pending_test_attribute = False
    for line in source.splitlines():
        attribute_kind = _rust_test_attribute_kind(line)
        if not pending_test_attribute:
            if attribute_kind == "tokio::test" or (not tokio_only and attribute_kind == "test"):
                pending_test_attribute = True
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if attribute_kind is not None:
            pending_test_attribute = attribute_kind == "tokio::test" or (
                not tokio_only and attribute_kind == "test"
            )
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = _self._RUST_TEST_FN_PATTERN.match(line)
        if match:
            candidates.append(match.group(1))
        pending_test_attribute = False

    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=False)


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _rust_tokio_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=True)


def _rust_file_level_command(test_path: Path, repo_root: Path) -> str | None:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return None
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return None
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    target_parts = parts[tests_index + 1 :]
    if not target_parts:
        return None
    if len(target_parts) == 1:
        target = Path(target_parts[0]).stem
    else:
        target = Path(target_parts[0]).stem
    if not target:
        return None
    return f"cargo test --test {target}"


def _rust_uses_nested_test_target(test_path: Path, repo_root: Path) -> bool:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return False
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return False
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    return len(parts[tests_index + 1 :]) > 1
