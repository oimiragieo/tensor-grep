"""Language-extractor registry for tensor-grep's multi-language symbol graph (PATH A Stage 0).

This module is the single source of truth for "which languages does the repo-map symbol graph
(defs/refs/callers/blast-radius) support, and which callables implement each stage of
extraction for that language". ``repo_map.py`` registers the CURRENT four languages (python,
javascript, typescript, rust) by wrapping its own existing, UNCHANGED functions -- see the
``lang_registry.register_language(...)`` calls near the language-specific helper functions in
``repo_map.py``.

This module intentionally imports NOTHING from ``repo_map`` (a one-directional dependency:
``repo_map`` -> ``lang_registry``, never the reverse) to avoid an import cycle; the two tiny
helpers it would otherwise need are duplicated below instead of imported.

Stage 0 is a PURE PARITY REFACTOR: the registry replaces scattered
``path.suffix in _JS_TS_SUFFIXES`` / ``_RUST_SUFFIXES`` dispatch in ``repo_map.py`` with
``spec_for_path(path)`` lookups, with ZERO behavior change for the 4 currently-supported
languages. It also underpins the ``resolution_gaps`` honesty floor (see repo_map.py's
``_resolution_gaps_for_universe``): a file in the refs/callers scan universe with no
registered ``LanguageSpec`` becomes a labeled gap instead of a silent, unexplained absence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers (see module docstring: this module imports nothing from repo_map.py
# to avoid a cycle, so the couple of one-line helpers a LanguageSpec's callables might want are
# copied here rather than imported). Keep these BYTE-IDENTICAL to their repo_map.py twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` there) if either ever changes.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Callable shapes. Every LanguageSpec callable field uses a UNIFORM signature across languages
# (even where one language's underlying repo_map.py function does not need every argument --
# e.g. python's import_update_target ignores repo_root) so a generic call site never needs a
# per-language special case just to invoke the callable.
# ---------------------------------------------------------------------------

ReferencesAndCalls = Callable[
    [Path, str, "Path | str | None"], tuple[list[dict[str, Any]], list[dict[str, Any]]]
]
ProviderAliasCalls = Callable[[Path, str, "Path | str | None"], list[dict[str, Any]]]
FileImportsSymbolFromDefinition = Callable[[Path, str, str, str, "Path | str | None"], bool]
ImportUpdateTarget = Callable[[Path, str, str, "Path | str | None"], "dict[str, Any] | None"]
ExtractImportsAndSymbols = Callable[[Path], tuple[list[str], list[dict[str, Any]]]]
PrimeRepoContext = Callable[[Path], dict[str, Any]]
ParserForPath = Callable[[Path], "Any | None"]
ClassifyRefKind = Callable[..., str]


@dataclass(frozen=True)
class LanguageSpec:
    """One entry in the multi-language symbol-graph registry.

    Every callable field is a THIN WRAPPER over an existing repo_map.py function (Stage 0:
    zero new parsing logic -- just a registry-shaped seam other dispatch sites can look up
    instead of re-deriving suffix membership by hand). Fields a language genuinely has no
    behavior for (e.g. python has no separate repo-context priming step, since it needs no
    tsconfig/Cargo.toml-style workspace context) are ``None``; callers must check for that
    before invoking.
    """

    language_id: str
    suffixes: frozenset[str]
    # Third-party grammar package names this language's tree-sitter parser depends on (empty
    # for python, which uses the stdlib ``ast`` module and has no external grammar to miss).
    grammar_modules: tuple[str, ...] = ()
    # Returns the parser object for *path* (already bound to the right grammar variant, e.g.
    # tsx vs plain typescript), or None if the grammar package is not installed. None for
    # python (no gating: `ast.parse` is always attempted directly).
    parser_for_path: ParserForPath | None = None
    provenance_when_parsed: str = "heuristic"
    provenance_when_missing: str = "regex-heuristic"
    # Byte markers checked (case-insensitively) against a file's raw bytes to cheaply decide
    # whether it is even worth running the more expensive import-resolution machinery on it.
    import_markers: tuple[bytes, ...] = ()
    # Tree-sitter node type names (or, for python, ast node class names) this language's
    # definition-symbol walker matches on. Informational/documentation only in Stage 0 -- no
    # dispatch seam reads this field yet; it exists so a Stage 1+ language and any tooling that
    # introspects the registry has a place to look without re-deriving it from source.
    def_node_kinds: tuple[str, ...] = ()
    extract_imports_and_symbols: ExtractImportsAndSymbols | None = None
    references_and_calls: ReferencesAndCalls | None = None
    provider_alias_calls: ProviderAliasCalls | None = None
    file_imports_symbol_from_definition: FileImportsSymbolFromDefinition | None = None
    import_update_target: ImportUpdateTarget | None = None
    # Primes any per-repo-root context this language's import resolution needs (e.g. JS/TS
    # tsconfig paths/baseUrl). None for languages with no such per-repo state (python, and
    # rust's own priming is registered separately since it caches Cargo-workspace layout).
    prime_repo_context: PrimeRepoContext | None = None
    # Classifies an already-matched reference node into the T1 ref_kind taxonomy
    # (call/import/type/field/value). Preserved here for discoverability; the T1 emission
    # sites call the underlying per-language classify function directly and are NOT rewired
    # through the registry in Stage 0 (do not regress the T1 work).
    classify_ref_kind: ClassifyRefKind | None = None


LANGUAGE_REGISTRY: dict[str, LanguageSpec] = {}
_SPEC_BY_SUFFIX: dict[str, LanguageSpec] = {}


def register_language(spec: LanguageSpec) -> LanguageSpec:
    """Register (or replace) a LanguageSpec.

    Idempotent: re-registering the same ``language_id`` (e.g. a module reload during tests)
    simply replaces the prior entry and re-derives every suffix pointer it owns, so a stale
    suffix -> spec mapping never survives a re-registration.
    """
    LANGUAGE_REGISTRY[spec.language_id] = spec
    for suffix in spec.suffixes:
        _SPEC_BY_SUFFIX[suffix.lower()] = spec
    return spec


def spec_for_path(path: str | Path) -> LanguageSpec | None:
    """Return the registered LanguageSpec for *path*'s suffix, or None if unregistered."""
    suffix = Path(path).suffix.lower()
    return _SPEC_BY_SUFFIX.get(suffix)


def graph_suffixes() -> frozenset[str]:
    """Return every suffix with a registered LanguageSpec (the symbol-graph suffix gate)."""
    return frozenset(_SPEC_BY_SUFFIX.keys())


__all__ = [
    "LANGUAGE_REGISTRY",
    "LanguageSpec",
    "graph_suffixes",
    "register_language",
    "spec_for_path",
]
