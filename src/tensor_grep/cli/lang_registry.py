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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SOURCE_SNAPSHOT: ContextVar[tuple[str, bytes, str] | None] = ContextVar(
    "tg_source_snapshot", default=None
)


@contextmanager
def source_snapshot(path: Path, content: bytes, digest: str) -> Iterator[None]:
    """Bind one verified whole-file read to the extractor, without changing its path."""
    token = _SOURCE_SNAPSHOT.set((str(path), content, digest))
    try:
        yield
    finally:
        _SOURCE_SNAPSHOT.reset(token)


def source_snapshot_digest(path: str) -> str | None:
    snapshot = _SOURCE_SNAPSHOT.get()
    return snapshot[2] if snapshot is not None and snapshot[0] == path else None


# ---------------------------------------------------------------------------
# Duplicated tiny helpers (see module docstring: this module imports nothing from repo_map.py
# to avoid a cycle, so the couple of one-line helpers a LanguageSpec's callables might want are
# copied here rather than imported). Keep these BYTE-IDENTICAL to their repo_map.py twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` there) if either ever changes.
# ``_CLEAN_SYMBOL_NAME_RE`` is pinned byte-identical across its 8 copies by
# tests/unit/test_clean_symbol_name_regex_pin.py; ``is_clean_symbol_name`` below is the shared
# predicate every copy's ``_is_clean_symbol_name`` wrapper delegates to.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[^\s\x00-\x23\x25-\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\x7f]+$")


def is_clean_symbol_name(name: str) -> bool:
    """The ONE identifier verdict every ``_is_clean_symbol_name`` wrapper delegates to.

    The regex copies are a structural prefilter only (no whitespace / ASCII punctuation). The
    verdict validates the WHOLE name with ``str.isidentifier`` (Unicode XID_Start/XID_Continue),
    after mapping ``$`` -- the one extra character these languages allow -- to ``_``. So a
    decomposed ``cafe`` + U+0301, ``\u2118x`` and ``x\u2160`` are accepted, while
    ``a\u00b2`` (a No-category character the word-character class admits) is rejected.
    """
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name)) and name.replace("$", "_").isidentifier()


def _is_clean_symbol_name(name: str) -> bool:
    return is_clean_symbol_name(name)


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Callable shapes. Every LanguageSpec callable field uses a UNIFORM signature across languages
# (even where one language's underlying repo_map.py function does not need every argument --
# e.g. python's import_update_target ignores repo_root) so a generic call site never needs a
# per-language special case just to invoke the callable.
# ---------------------------------------------------------------------------

# Minimum shared shape is (path, symbol, repo_root). Registry wrappers additionally
# accept kw-only definition_dirs (Go F25 package-ownership confirmation); non-Go
# wrappers ignore it. The refs/callers dispatch seam
# (repo_map._references_and_calls_for_path) always passes definition_dirs so it
# never needs a language_id branch just to invoke the callable.
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
    # Wired by repo_map._references_and_calls_for_path (the refs/callers dispatch seam).
    # None means foundational-tier / deferred caller-graph: the seam's EXPLICIT fallback is
    # ``_regex_references_and_calls`` (never an implicit empty).
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


def read_source_text(path: Path) -> str:
    """Shared source reader: BOM-stripped; never raises UnicodeDecodeError (only OSError).

    One read with utf-8-sig + errors="replace" (a two-step fallback to plain utf-8 kept the BOM
    as U+FEFF when the file also had an invalid byte)."""
    snapshot = _SOURCE_SNAPSHOT.get()
    if snapshot is not None and snapshot[0] == str(path):
        return (
            snapshot[1]
            .decode("utf-8-sig", errors="replace")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    return Path(path).read_text(encoding="utf-8-sig", errors="replace")


def split_source_lines(text: str) -> list[str]:
    """Split on newline characters only (read_text already normalised newlines); matches ast rows."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


__all__ = [
    "LANGUAGE_REGISTRY",
    "LanguageSpec",
    "graph_suffixes",
    "read_source_text",
    "register_language",
    "spec_for_path",
    "split_source_lines",
]
