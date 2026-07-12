"""Go language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 1).

First language expansion beyond the original four (python/javascript/typescript/rust). Plugs
into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` + the
``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- Go gets
its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``, with zero special-casing beyond
the couple of dispatch sites documented in the module docstring there.

Like ``lang_registry.py``, this module imports NOTHING from ``repo_map.py`` (``repo_map`` ->
``lang_go``, never the reverse -- ``repo_map.py`` needs to import this module to register Go's
``LanguageSpec`` and to call its ``references_and_calls``/``file_imports_symbol_from_definition``
directly at a couple of per-language dispatch sites that mirror how it calls the Rust
equivalents; a reverse import would cycle). The handful of tiny helpers this module needs from
``repo_map.py`` are duplicated here instead of imported, matching ``lang_registry.py``'s own
precedent.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended to Go): Go has NO regex-heuristic
fallback. When the ``tree_sitter_go`` grammar package is not installed, every extractor in this
module returns empty ([]/([], [])) rather than degrading to a regex/text heuristic (unlike
JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter grammar is
missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT ``"regex-heuristic"``)
is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py treat a grammar-absent Go
file as a genuine ``resolution_gaps`` entry instead of silently reporting zero matches as if the
symbol just did not exist.

Go's import/package model is much simpler than Rust's (no ``mod`` tree to walk): a Go
"package" IS a directory, and cross-package access is resolved by mapping an imported path
(e.g. ``"example.com/mod/bar"``) through the enclosing module's ``go.mod`` ``module`` line (plus
any ``go.work`` ``use`` entries for a multi-module workspace) to an absolute directory, then
checking whether the definition file lives in that directory. Exported-ness
(``symbol[0].isupper()``) gates cross-package visibility exactly as the Go compiler does;
same-package access needs no import at all (and reaches even unexported symbols).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle (repo_map.py imports THIS module). Keep byte-identical to repo_map.py's twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` / ``_node_has_ancestor_type`` /
# ``_symbol_record``) if any of them ever change there.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _node_has_ancestor_type(node: Any, ancestor_types: set[str]) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type in ancestor_types:
            return True
        current = getattr(current, "parent", None)
    return False


def _symbol_record(
    *,
    name: str,
    kind: str,
    file: Path,
    start_line: int,
    end_line: int | None = None,
) -> dict[str, Any]:
    normalized_end_line = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": kind,
        "file": str(file),
        "line": start_line,
        "start_line": start_line,
        "end_line": normalized_end_line,
    }


# ---------------------------------------------------------------------------
# Parser factory (clone of repo_map.py's ``_rust_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _go_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_go
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_go.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

_GO_TYPE_SPEC_KIND_BY_TYPE_FIELD = {
    "struct_type": "struct",
    "interface_type": "interface",
}
# node types a Go value/type can be nested under on its way to a name-bearing declaration --
# used only for documentation/introspection parity with the other LanguageSpec entries; the
# actual walk below matches concrete node kinds directly rather than deriving them from this set.
_GO_DEF_NODE_KINDS = (
    "function_declaration",
    "method_declaration",
    "type_spec",
    "const_spec",
    "var_spec",
)


def _go_receiver_type_name(method_node: Any, source_bytes: bytes) -> str | None:
    """Return the (possibly pointer, possibly generic) receiver type name for a
    ``method_declaration`` node.

    F8 fix: a GENERIC receiver (``func (r *MyType[T]) M()`` / ``func (r MyType[T]) N()``) parses
    the receiver's type as a ``generic_type`` node whose own text is ``"MyType[T]"`` -- that never
    matches the plain ``"MyType"`` name a ``type_spec`` declares, so method<->type association
    silently broke for every generic Go receiver. Descending to ``generic_type``'s ``type`` field
    (the base ``type_identifier``) restores the plain base name.
    """
    receiver = method_node.child_by_field_name("receiver")
    if receiver is None:
        return None
    param_decl = next(
        (child for child in receiver.children if child.type == "parameter_declaration"),
        None,
    )
    if param_decl is None:
        return None
    type_node = param_decl.child_by_field_name("type")
    if type_node is None:
        return None
    if type_node.type == "pointer_type":
        inner = next(
            (child for child in type_node.children if child.type != "*"),
            None,
        )
        if inner is not None:
            type_node = inner
    if type_node.type == "generic_type":
        base_type_node = type_node.child_by_field_name("type")
        if base_type_node is not None:
            type_node = base_type_node
    return _tree_sitter_node_text(source_bytes, type_node)


def _go_import_spec_path_text(path_field: Any, source_bytes: bytes) -> str | None:
    """Return an ``import_spec``'s string-literal import path (quotes stripped), or ``None``.

    F11 fix: the primary route reads the ``interpreted_string_literal_content`` child that recent
    ``tree_sitter_go`` grammar versions expose. An older/differently-built grammar package can
    omit that child (there is no dedicated content node at all, or it is named differently) --
    when that happens this used to silently return no path for EVERY import in the file, which
    then silently degrades all cross-package import resolution with no error and no
    ``resolution_gaps`` entry (the parser loaded fine, so nothing marks a gap). Falling back to
    quote-stripping the string literal's own raw text keeps import extraction working even
    against a grammar shape this module wasn't written against.
    """
    if path_field is None:
        return None
    content_node = next(
        (
            child
            for child in path_field.children
            if child.type == "interpreted_string_literal_content"
        ),
        None,
    )
    if content_node is not None:
        return _tree_sitter_node_text(source_bytes, content_node)
    raw_text = _tree_sitter_node_text(source_bytes, path_field).strip()
    if len(raw_text) >= 2 and raw_text[0] in {'"', "`"} and raw_text[-1] == raw_text[0]:
        return raw_text[1:-1]
    return None


def go_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract import paths + top-level definitions from a Go source file, one AST pass.

    Defs covered: ``function_declaration`` (kind "function"), ``method_declaration`` (kind
    "method", with an additive ``receiver_type`` field), ``type_spec`` (kind "struct"/
    "interface"/"type" depending on the underlying type), and top-level ``const_spec``/
    ``var_spec`` (kind "const"/"var" -- local var/const statements INSIDE a function body are
    deliberately excluded via the ``"block"`` ancestor check, matching "top-level" in the
    design). Imports come from every ``import_spec``'s string-literal path, regardless of
    whether it carries an alias/dot/blank qualifier (the qualifier only matters for reference
    resolution, not for the flat import-path list this function returns).
    """
    if path.suffix != ".go":
        return [], []

    parser = _go_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # F26 fix (audit #63): explicit-stack DFS instead of recursion, so a pathologically
        # deep AST can never raise RecursionError (matches the ast_backend.py B3 precedent:
        # `AstBackend._build_node_type_index`). Children are pushed in reverse so the leftmost
        # child is popped (and thus visited) first, preserving the original pre-order traversal.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "import_spec":
                path_field = node.child_by_field_name("path")
                import_path_text = _go_import_spec_path_text(path_field, source_bytes)
                if import_path_text is not None:
                    imports.append(import_path_text)
            elif node_type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        record = _symbol_record(
                            name=name,
                            kind="method",
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                        receiver_type = _go_receiver_type_name(node, source_bytes)
                        if receiver_type:
                            record["receiver_type"] = receiver_type
                        symbols.append(record)
            elif node_type == "type_spec":
                name_node = node.child_by_field_name("name")
                type_node = node.child_by_field_name("type")
                if name_node is not None:
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        kind = _GO_TYPE_SPEC_KIND_BY_TYPE_FIELD.get(
                            type_node.type if type_node is not None else "", "type"
                        )
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind=kind,
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type in {"const_spec", "var_spec"} and not _node_has_ancestor_type(
                node, {"block"}
            ):
                kind = "const" if node_type == "const_spec" else "var"
                for name_node in node.children_by_field_name("name"):
                    if name_node.type != "identifier":
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind=kind,
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def go_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every top-level def matching *symbol* (mirrors the Rust/JS-TS
    ``_parser_symbol_sources`` shape for the ``tg source`` command)."""
    if path.suffix != ".go":
        return []

    parser = _go_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _walk(root: Any) -> None:
        # F26 fix (audit #63): explicit-stack DFS instead of recursion -- see the identical
        # comment on go_imports_and_symbols's `_walk` above for the rationale/precedent.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            name_node: Any | None = None
            kind: str | None = None
            if node_type == "function_declaration":
                name_node = node.child_by_field_name("name")
                kind = "function"
            elif node_type == "method_declaration":
                name_node = node.child_by_field_name("name")
                kind = "method"
            elif node_type == "type_spec":
                name_node = node.child_by_field_name("name")
                type_node = node.child_by_field_name("type")
                kind = _GO_TYPE_SPEC_KIND_BY_TYPE_FIELD.get(
                    type_node.type if type_node is not None else "", "type"
                )
            if name_node is not None and kind is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": kind,
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


# ---------------------------------------------------------------------------
# Per-repo-root context: go.mod ``module`` line + go.work ``use`` entries -> {import path
# prefix: absolute dir}. Cached with the same move-to-end-on-hit / evict-oldest-beyond-max
# shape as repo_map.py's ``_remember_repo_context`` (duplicated locally rather than imported,
# same no-cycle rationale as the tiny helpers above).
# ---------------------------------------------------------------------------

_GO_REPO_CONTEXT_CACHE_MAX_ROOTS = 32
_GO_REPO_CONTEXTS: OrderedDict[str, dict[str, Any]] = OrderedDict()

_GO_MODULE_LINE_RE = re.compile(r"^\s*module\s+(\S+)\s*$", re.MULTILINE)
_GO_WORK_USE_LINE_RE = re.compile(r"^\s*use\s+(\S+)\s*$", re.MULTILINE)
_GO_WORK_USE_BLOCK_RE = re.compile(r"use\s*\(([^)]*)\)", re.DOTALL)


def _remember_go_repo_context(key: str, context: dict[str, Any]) -> dict[str, Any]:
    _GO_REPO_CONTEXTS.pop(key, None)
    _GO_REPO_CONTEXTS[key] = context
    while len(_GO_REPO_CONTEXTS) > _GO_REPO_CONTEXT_CACHE_MAX_ROOTS:
        _GO_REPO_CONTEXTS.popitem(last=False)
    return context


def clear_go_repo_context_cache() -> None:
    """Exposed so repo_map.py's daemon-refresh sweep (``_clear_all_source_caches``) can flush
    this module's per-repo-root cache too, matching the existing JS/TS + Rust context clears."""
    _GO_REPO_CONTEXTS.clear()


def _parse_go_mod_module(go_mod_path: Path) -> str | None:
    try:
        text = go_mod_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _GO_MODULE_LINE_RE.search(text)
    return match.group(1).strip() if match else None


def _go_work_use_dirs(go_work_path: Path) -> list[str]:
    try:
        text = go_work_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    dirs: list[str] = []
    for line_match in _GO_WORK_USE_LINE_RE.finditer(text):
        candidate = line_match.group(1).strip()
        # F23: the single-line `use ...$` regex also matches the `use (` block-open header,
        # capturing "(" (or a compact `use (./m)`). Never treat the block opener as a directory.
        if candidate and not candidate.startswith("("):
            dirs.append(candidate)
    for block_match in _GO_WORK_USE_BLOCK_RE.finditer(text):
        for raw_line in block_match.group(1).splitlines():
            # F23: strip a trailing `// comment` (e.g. `./m // legacy`), not just full-line
            # comments -- otherwise the comment text becomes part of the resolved directory path.
            candidate = raw_line.split("//", 1)[0].strip()
            if candidate:
                dirs.append(candidate)
    return dirs


def _prime_go_repo_context(root: Path) -> dict[str, Any]:
    """Prime the ``{module-path-prefix: absolute-dir}`` map for *root* from its own ``go.mod``
    plus any ``go.work`` workspace ``use`` entries (each pointing at ANOTHER directory with its
    own ``go.mod``, e.g. a multi-module monorepo)."""
    normalized_root = root.expanduser().resolve()
    key = str(normalized_root)
    module_dirs: dict[str, str] = {}

    go_mod = normalized_root / "go.mod"
    if go_mod.is_file():
        module_path = _parse_go_mod_module(go_mod)
        if module_path:
            module_dirs[module_path] = str(normalized_root)

    go_work = normalized_root / "go.work"
    if go_work.is_file():
        for use_dir in _go_work_use_dirs(go_work):
            candidate_dir = (normalized_root / use_dir).resolve()
            candidate_mod = candidate_dir / "go.mod"
            if candidate_mod.is_file():
                candidate_module_path = _parse_go_mod_module(candidate_mod)
                if candidate_module_path:
                    module_dirs[candidate_module_path] = str(candidate_dir)

    context = {"root": key, "module_dirs": module_dirs}
    return _remember_go_repo_context(key, context)


def prime_go_repo_context(root: Path) -> dict[str, Any]:
    """Public entry point for repo_map.py's registry-driven ``_prime_all_language_repo_contexts``
    (the ``LanguageSpec.prime_repo_context`` callable field)."""
    return _prime_go_repo_context(root)


def _go_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    if repo_root is None:
        return {"root": None, "module_dirs": {}}
    try:
        normalized_root = Path(repo_root).expanduser().resolve()
    except OSError:
        return {"root": None, "module_dirs": {}}
    key = str(normalized_root)
    cached = _GO_REPO_CONTEXTS.get(key)
    if cached is not None:
        _GO_REPO_CONTEXTS.move_to_end(key)
        return cached
    return _prime_go_repo_context(normalized_root)


def _go_import_path_to_dir(import_path: str, context: dict[str, Any]) -> Path | None:
    """Resolve an import path (e.g. ``"example.com/mod/bar"``) to an absolute directory via the
    longest matching ``module_dirs`` prefix (longest-prefix-wins handles a workspace where one
    module's path is itself a prefix of another's).

    F24 fix: stop at an intervening ``go.mod`` boundary. A nested module (its own ``go.mod`` that
    is NOT listed as a ``go.work`` ``use`` entry -- e.g. simply forgotten) is a SEPARATE module
    even when the enclosing module's path prefix happens to textually match its subdirectory --
    walking past that boundary used to silently resolve a cross-package reference into the wrong
    module's directory. Any ``go.mod`` strictly under the matched prefix's own root (which is
    expected and ignored -- that root's ``go.mod`` is exactly what got this prefix primed) now
    aborts resolution (returns ``None``) instead of fabricating a cross-module match.
    """
    module_dirs = context.get("module_dirs", {})
    if not isinstance(module_dirs, dict):
        return None
    best_prefix: str | None = None
    for prefix in module_dirs:
        if import_path == prefix or import_path.startswith(f"{prefix}/"):
            if best_prefix is None or len(prefix) > len(best_prefix):
                best_prefix = prefix
    if best_prefix is None:
        return None
    base_dir = Path(str(module_dirs[best_prefix])).resolve()
    remainder = import_path[len(best_prefix) :].lstrip("/")
    if not remainder:
        return base_dir
    current = base_dir
    for part in remainder.split("/"):
        current = current / part
        if (current / "go.mod").is_file():
            return None
    return current


def _go_import_bindings(source_bytes: bytes, tree: Any) -> list[dict[str, Any]]:
    """Every ``import_spec`` in *tree*: ``{"path", "alias", "dot", "blank"}``."""
    bindings: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # F26 fix (audit #63): explicit-stack DFS instead of recursion -- see the identical
        # comment on go_imports_and_symbols's `_walk` (above, this module) for the rationale/
        # precedent. Order preservation matters MORE here than in the other three walkers: unlike
        # them, `bindings` is never re-sorted by its caller (`_go_alias_to_import_path` folds it
        # into a dict in traversal order) -- pushing children in reverse and popping from the end
        # keeps this an exact pre-order DFS, byte-identical visiting order to the old recursion.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "import_spec":
                path_field = node.child_by_field_name("path")
                name_field = node.child_by_field_name("name")
                import_path_text = _go_import_spec_path_text(path_field, source_bytes)
                if import_path_text is not None:
                    alias: str | None = None
                    dot = False
                    blank = False
                    if name_field is not None:
                        if name_field.type == "package_identifier":
                            alias = _tree_sitter_node_text(source_bytes, name_field)
                        elif name_field.type == "dot":
                            dot = True
                        elif name_field.type == "blank_identifier":
                            blank = True
                    bindings.append({
                        "path": import_path_text,
                        "alias": alias,
                        "dot": dot,
                        "blank": blank,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return bindings


def _go_alias_to_import_path(bindings: list[dict[str, Any]]) -> dict[str, str]:
    alias_to_path: dict[str, str] = {}
    for binding in bindings:
        if binding.get("blank") or binding.get("dot"):
            continue
        import_path = str(binding["path"])
        local_name = str(binding.get("alias") or import_path.rsplit("/", 1)[-1])
        alias_to_path[local_name] = import_path
    return alias_to_path


# ---------------------------------------------------------------------------
# Import-based caller-scan pre-filter (registered as ``file_imports_symbol_from_definition``).
# ---------------------------------------------------------------------------


def go_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    """True iff *file_path* can see *symbol*'s definition: same package (any visibility) OR a
    resolved import of the definition's package AND *symbol* is exported (``symbol[0].isupper()``
    -- Go's own visibility rule, not a tensor-grep heuristic)."""
    try:
        definition_dir = Path(definition_path).expanduser().resolve().parent
        importer_dir = file_path.expanduser().resolve().parent
    except OSError:
        return False
    if importer_dir == definition_dir:
        return True
    if not symbol or not symbol[:1].isupper():
        return False

    parser = _go_parser()
    if parser is None:
        return False
    try:
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except (UnicodeDecodeError, ValueError):
        return False

    context = _go_repo_context(repo_root)
    for binding in _go_import_bindings(source_bytes, tree):
        if binding.get("blank"):
            continue
        target_dir = _go_import_path_to_dir(str(binding["path"]), context)
        if target_dir is not None and target_dir == definition_dir:
            return True
    return False


# ---------------------------------------------------------------------------
# References + calls: identifier / type_identifier / field_identifier (selector) walk.
# ---------------------------------------------------------------------------

# Node types whose "name" field (or, for const/var specs, "name"-field-tagged children) defines
# *this* declaration rather than referencing an existing one elsewhere -- excluded from the
# reference/call walk below exactly like every other language's ``_is_definition_identifier``.
_GO_NAME_DEFINING_PARENT_TYPES = {
    "function_declaration",
    "method_declaration",
    "type_spec",
    "const_spec",
    "var_spec",
    "field_declaration",
    "method_elem",
    "parameter_declaration",
}


def _go_package_defines_function(target_dir: str, name: str) -> bool:
    """F10 fallback confirmation: True iff *target_dir* (a resolved package directory) contains a
    top-level ``function``/``method`` declaration named *name*.

    Used only when ``go_references_and_calls`` is not given ``definition_dirs`` (the stronger,
    definition-aware check used by repo_map.py's refs/callers scan -- see F25 in that function's
    docstring). Deliberately un-cached: this module never caches anything that reads file content
    without an mtime guard (see the module-level caches above, which are keyed on repo ROOT
    priming, not file content), and a Go package directory is small enough that a plain per-call
    scan is cheap.
    """
    directory = Path(target_dir)
    if not directory.is_dir():
        return False
    try:
        entries = sorted(directory.glob("*.go"))
    except OSError:
        return False
    for entry in entries:
        _, symbols = go_imports_and_symbols(entry)
        for record in symbols:
            if record.get("name") == name and record.get("kind") in {"function", "method"}:
                return True
    return False


def go_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reference/call rows for *symbol* in *path*.

    Go's grammar gives ``ref_kind`` classification almost for free at the node-type level
    (unlike JS/Python/Rust, where a single ``identifier`` node type is reused across value/type/
    field roles and ref_kind must be inferred from ancestor context): a *value*-position mention
    is node type ``identifier``, a *type*-position mention is ``type_identifier``, and a
    *field*/method-selector mention is ``field_identifier``.

    Package-qualified access (``pkg.Symbol``) resolves through this file's import bindings +
    the primed repo context. Two shapes:

    - **Non-call selector** (e.g. ``config.DefaultTimeout``): F9 fix -- classified ``ref_kind``
      "value" (a package-qualified const/var read), not "field", whenever the operand resolves as
      a recognized import alias; an unresolved operand (e.g. a genuine struct field access) still
      classifies "field" as before.
    - **Call** (e.g. ``pkg.Helper(...)`` / ``w.Write(...)``): a resolved package alias only earns
      ``resolution_provenance=["go-import-resolution"]`` at ``resolution_confidence=0.95`` if the
      resolved package is CONFIRMED to actually own *symbol* -- F25 fix: when *definition_dirs* is
      supplied (repo_map.py always supplies it, built from the symbol's own known definitions),
      confirmation means the resolved directory is one of those definition dirs, which also fixes
      a same-named-export collision (two unrelated packages each exporting a symbol with the same
      name used to both get 0.95 turn just because the alias itself resolved); when
      *definition_dirs* is omitted (e.g. a standalone caller), F10 fallback confirmation checks
      whether the resolved package directory itself defines a top-level function/method named
      *symbol* -- this is what prevents a LOCAL VARIABLE that happens to share its name with an
      unrelated import alias (``w := SomeStruct{}`` shadowing `import w "pkg/w"`) from fabricating
      high confidence for ``w.Method()`` when ``pkg/w`` does not actually define ``Method``.
      Either way, an unconfirmed resolution is never dropped -- it demotes to the same
      ``resolution_confidence<=0.7`` / ``resolution_provenance=["receiver-heuristic"]`` band as a
      selector call whose left-hand operand is not a recognized package alias at all (e.g.
      ``w.Write(x)`` where ``w`` is an arbitrary receiver variable of unknown static type) --
      equifinal in both cases (ANY type with a same-named method could match textually), per the
      Stage 1 no-fabricated-precision trap.
    """
    if path.suffix != ".go":
        return [], []

    parser = _go_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    # F26 fix (audit #63): tree-sitter's row counting (`node.start_point[0]`) advances only on
    # "\n", but `str.splitlines()` ALSO splits on "\r", "\v"/"\x0b", "\f"/"\x0c", "\x1c"-"\x1e",
    # "\x85", U+2028 and U+2029 -- a single stray form-feed (or any of those other separators)
    # anywhere in the source injects an EXTRA entry into a splitlines()-based array, shifting
    # every row-indexed `text` lookup below it out of alignment with tree-sitter's own rows.
    # Split strictly on "\n" (matching tree-sitter's row semantics) and strip a trailing "\r"
    # per line so CRLF-terminated files still read cleanly.
    lines = [line.rstrip("\r") for line in source.split("\n")]
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    context = _go_repo_context(repo_root)
    alias_to_path = _go_alias_to_import_path(_go_import_bindings(source_bytes, tree))

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None or parent.type not in _GO_NAME_DEFINING_PARENT_TYPES:
            return False
        name_field = parent.child_by_field_name("name")
        if name_field is not None and name_field == node:
            return True
        return any(candidate == node for candidate in parent.children_by_field_name("name"))

    def _resolution_for_package(pkg_name: str | None) -> dict[str, Any] | None:
        if not pkg_name:
            return None
        import_path = alias_to_path.get(pkg_name)
        if import_path is None:
            return None
        target_dir = _go_import_path_to_dir(import_path, context)
        if target_dir is None:
            return None
        return {"provenance": ["go-import-resolution"], "confidence": 0.95, "dir": target_dir}

    def _confident_call_resolution(package_resolution: dict[str, Any] | None) -> dict[str, Any]:
        """F10/F25 fix: only trust ``package_resolution`` (0.95) for a CALL when the resolved
        package is confirmed to actually own *symbol* -- see the ``go_references_and_calls``
        docstring for the two confirmation routes. Anything unconfirmed demotes to the same
        equifinal receiver-heuristic band a non-package selector call already gets, never drops.
        """
        if package_resolution is not None:
            target_dir = str(package_resolution.get("dir", ""))
            if definition_dirs is not None:
                if target_dir in definition_dirs:
                    return package_resolution
            elif _go_package_defines_function(target_dir, symbol):
                return package_resolution
        return {"provenance": ["receiver-heuristic"], "confidence": 0.7}

    def _emit(
        bucket: list[dict[str, Any]],
        node: Any,
        *,
        kind: str,
        ref_kind: str,
        resolution: dict[str, Any] | None,
    ) -> None:
        entry: dict[str, Any] = {
            "name": symbol,
            "kind": kind,
            "ref_kind": ref_kind,
            "file": str(path),
            "line": node.start_point[0] + 1,
            "text": _line_text(node),
        }
        if resolution is not None:
            entry["resolution_provenance"] = list(resolution.get("provenance", []))
            entry["resolution_confidence"] = float(resolution.get("confidence", 0.95))
        bucket.append(entry)

    def _walk(root: Any) -> None:
        # F26 fix (audit #63): explicit-stack DFS instead of recursion -- see the
        # identical comment on go_imports_and_symbols's `_walk` (above, this module) for
        # the rationale/precedent. This is the walker invoked BARE at repo_map.py:14613
        # (build_symbol_refs) and :15339 (build_symbol_callers) -- the highest-priority
        # crash site the audit finding names.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            node_text = (
                _node_text(node)
                if node_type in {"identifier", "type_identifier", "field_identifier"}
                else ""
            )
            if (
                node_type == "identifier"
                and node_text == symbol
                and not _is_definition_identifier(node)
            ):
                parent = node.parent
                if (
                    parent is not None
                    and parent.type == "call_expression"
                    and parent.child_by_field_name("function") == node
                ):
                    _emit(references, node, kind="reference", ref_kind="call", resolution=None)
                    _emit(calls, node, kind="call", ref_kind="call", resolution=None)
                else:
                    _emit(references, node, kind="reference", ref_kind="value", resolution=None)
            elif (
                node_type == "type_identifier"
                and node_text == symbol
                and not _is_definition_identifier(node)
            ):
                _emit(references, node, kind="reference", ref_kind="type", resolution=None)
            elif (
                node_type == "field_identifier"
                and node_text == symbol
                and not _is_definition_identifier(node)
            ):
                parent = node.parent
                if parent is not None and parent.type == "selector_expression":
                    field_node = parent.child_by_field_name("field")
                    if field_node is not None and field_node == node:
                        operand_node = parent.child_by_field_name("operand")
                        operand_name = (
                            _node_text(operand_node)
                            if operand_node is not None and operand_node.type == "identifier"
                            else None
                        )
                        package_resolution = _resolution_for_package(operand_name)
                        grandparent = getattr(parent, "parent", None)
                        is_call = (
                            grandparent is not None
                            and grandparent.type == "call_expression"
                            and grandparent.child_by_field_name("function") == parent
                        )
                        if is_call:
                            resolution = _confident_call_resolution(package_resolution)
                            _emit(
                                references,
                                node,
                                kind="reference",
                                ref_kind="call",
                                resolution=resolution,
                            )
                            _emit(calls, node, kind="call", ref_kind="call", resolution=resolution)
                        else:
                            # F9 fix: a package-qualified non-call selector
                            # (`config.DefaultTimeout`) is a VALUE read (const/var), not a struct
                            # FIELD access -- only a genuinely unresolved operand (a real struct
                            # field access) stays "field".
                            _emit(
                                references,
                                node,
                                kind="reference",
                                ref_kind="value" if package_resolution is not None else "field",
                                resolution=package_resolution,
                            )
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


__all__ = [
    "clear_go_repo_context_cache",
    "go_file_imports_symbol_from_definition",
    "go_imports_and_symbols",
    "go_parser_symbol_sources",
    "go_references_and_calls",
    "prime_go_repo_context",
]
