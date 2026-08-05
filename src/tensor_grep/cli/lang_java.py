"""Java reference/call extractor for tensor-grep's multi-language symbol graph (Task 10A).

STAGE: this module currently owns ONLY the in-file AST reference/call extractor
(``java_references_and_calls``, wired to ``LanguageSpec.references_and_calls``). Java's
defs/imports extraction (``_java_parser`` / ``_java_imports_and_symbols`` /
``_java_parser_symbol_sources`` / ``_java_imports_with_lines``, plus their shared helpers) stays
INLINE in ``repo_map.py`` -- it was NOT moved here. That is a deliberate scope reduction for Task
10A, not an oversight: moving it safely would require (a) relocating four functions plus their
shared helpers, (b) updating every call site that reaches them (``_imports_and_symbols_for_path``,
``build_symbol_source_from_map``, ``build_file_imports``, the ``LanguageSpec`` registration's
``parser_for_path`` lambda), and (c) re-pointing or shimming the THREE existing
``monkeypatch.setattr(repo_map, "_java_parser", ...)`` call sites in ``tests/unit/test_lang_java.py``
that assume ``_java_parser`` lives on ``repo_map`` -- none of that is required to ship Task 10A's
actual deliverable (in-file references/calls), and doing it in the same PR would mean proving
byte-identical defs/imports/source output under time pressure for no behavioral gain. See the
Task 10A build report for the full justification; a future PR MAY complete the move.

Because the parser factory stays in ``repo_map.py``, this module does NOT duplicate its own
``_java_parser()`` (unlike ``lang_go.py`` / ``lang_php.py`` / ``lang_c.py`` / ``lang_cpp.py``,
which each own their whole extraction pipeline including grammar probing). Duplicating a SECOND
parser factory here would create two independent sources of truth for "is the Java grammar
installed" -- a test that monkeypatches only ``repo_map._java_parser`` would silently desync from
this module's own copy, producing exactly the kind of instrument-disagrees-with-itself bug this
repo's evidence laws warn about. Instead, ``java_references_and_calls`` takes an already-built
``parser`` object (or ``None``) as an explicit keyword argument; the registry adapter in
``repo_map.py`` (``_java_references_and_calls_for_registry``) is the ONE place that calls
``_java_parser()`` to build it, so grammar-presence has a single owner.

Like ``lang_go.py``/``lang_php.py``, this module imports NOTHING from ``repo_map.py``
(``repo_map`` -> ``lang_java``, never the reverse, to avoid an import cycle: ``repo_map.py`` must
import this module to register Java's ``LanguageSpec`` update and to call
``java_references_and_calls`` at the registry dispatch seam). The handful of tiny helpers this
module needs are duplicated here instead of imported, matching the established precedent.

FAIL-CLOSED CONTRACT: Java has NO regex-heuristic fallback (mirrors Go/PHP/C#/C/C++). When
*parser* is ``None`` (grammar not installed, or a caller/test deliberately passes ``None`` to
simulate that), ``java_references_and_calls`` returns ``([], [])`` -- never a crash, never a
partial or fabricated result.

SCOPE: in-file AST extraction (Task 10A) plus cross-file package/source-root confirmation
(Task 11A / F7 wave 1). ``java_file_imports_symbol_from_definition`` answers whether a file can
see a definition via same-package or import under an established source root; when that mapping
cannot be established it returns False (DEMOTE, never guess). ``java_references_and_calls`` still
classifies nodes from local AST shape; the confirmed band now also fires when a receiver's
declared type resolves through package/import into a supplied ``definition_dirs`` set (the
selected definition's package directory).

AST NODE SHAPES (verified against the real ``tree_sitter_java`` grammar, not guessed):

- ``method_invocation``: fields ``object`` (optional -- absent for an unqualified call),
  ``name`` (always an ``identifier``), ``arguments``. A symbol match on ``name`` is a **call**
  (``ref_kind="call"``, emitted into both the references and calls buckets). NOTE: unlike Go's
  ``selector_expression``, Java's grammar does NOT wrap ``obj.method()`` in an intermediate
  ``field_access`` node -- ``object``/``name`` are direct fields of ``method_invocation`` itself.
- ``object_creation_expression``: field ``type`` is either a bare ``type_identifier``
  (``new Helper()``) or a ``generic_type`` whose first child is the base ``type_identifier``
  (``new ArrayList<Foo>()`` -> base type is ``ArrayList``, not ``Foo``). A symbol match on that
  base type identifier is a **constructor reference** (``ref_kind="constructor"``, emitted into
  both buckets). A fully-qualified constructor type (``new java.util.ArrayList()``, a
  ``scoped_type_identifier`` field) is NOT resolved to its trailing segment -- an accepted,
  documented gap, same spirit as the C++ ``class MACRO Name`` limitation.
- ``field_access``: fields ``object``, ``field`` (an ``identifier``). Non-call member access
  (``h.field``, ``this.x``). A symbol match on ``field`` is ``ref_kind="field"``.
- ``type_identifier``: type-position mentions (``extends``/``implements`` clauses, a local
  variable's declared type, generic type arguments). A symbol match is ``ref_kind="type"``,
  EXCEPT the base type identifier of an ``object_creation_expression`` (see above -- that is
  ``"constructor"`` instead, to avoid double-counting the same node under two ref_kinds).
- ``identifier``: reused across value/qualifier/declaration-name roles (unlike ``type_identifier``,
  Java's grammar does not give a distinct node type for a qualifier such as ``Helper`` in
  ``Helper.staticWork()`` -- it is a plain ``identifier``, indistinguishable syntactically from a
  local variable). Every other symbol-matching ``identifier`` not already claimed by one of the
  three special node types above is a plain **value** reference (``ref_kind="value"``), UNLESS it
  is itself the NAME of a declaration (class/interface/enum/record/method/constructor/local
  variable/field/formal parameter/catch parameter/enum constant) -- those are excluded entirely,
  the same "a symbol's own declaration site is not a reference to itself" rule every other
  language in this registry follows.
- ``string_literal`` / ``line_comment`` / ``block_comment``: never walked as identifier/
  type_identifier/method_invocation/object_creation_expression/field_access nodes, so a symbol
  name appearing inside a string literal or a comment is structurally excluded -- this is the
  AST-only distinction a text/regex scan cannot make (and which Java's own
  ``_regex_references_and_calls`` fallback never got the chance to get wrong, since that function
  is suffix-gated to JS/TS/Rust and returns empty for every ``.java`` query today).

RESOLUTION CONFIDENCE / PROVENANCE (fix-first follow-up to Task 10A -- dogfood found the entries
this module emits missing the ``resolution_confidence``/``resolution_provenance`` fields
``lang_go.go_references_and_calls`` carries): EVERY entry this module returns (both buckets) now
carries both fields -- never omitted, mirroring the SHAPE of Go's honesty banding but with Java's
own mechanism and numbers, since Java has no import/type resolver (Task 11A, still not built):

- ``_JAVA_DEMOTED_CONFIDENCE`` (0.6) / ``_JAVA_DEMOTED_PROVENANCE`` (``["java-name-heuristic"]``):
  the DEFAULT band for every entry -- an AST-confirmed node (a real call/field-access/type/value
  site, never a string literal or comment) whose receiver's static type is NOT resolvable from
  evidence in this same file. This is deliberately lower than Go's equifinal
  ``receiver-heuristic`` band (0.7): Go's demoted case still attempted package-alias resolution
  and failed; Java's demoted case attempts no resolution step at all for most ref_kinds (value/
  type/field/constructor), so the honest floor sits lower.
- ``_JAVA_CONFIRMED_CONFIDENCE`` (0.9) / ``_JAVA_CONFIRMED_PROVENANCE``
  (``["java-infile-type-confirmation"]``): a ``method_invocation``/``field_access`` node whose
  receiver (an ``identifier`` or the ``this`` keyword) has a static type DIRECTLY readable from a
  declaration node in this SAME file, AND that exact type ALSO directly declares a member named
  *symbol* in its own body -- two independently-checked AST facts with no cross-file assumption.
- ``_JAVA_CONFIRMED_CONFIDENCE`` (0.9) / ``_JAVA_CROSS_FILE_CONFIRMED_PROVENANCE``
  (``["java-package-type-confirmation"]``): same confidence, different provenance -- the receiver's
  declared type (or a bare type qualifier) resolves through same-package / import evidence into a
  package directory in ``definition_dirs`` (supplied by ``repo_map`` from the selected definition).
  Flat files with no establishable package/source-root mapping stay demoted
  (``test_java_cross_file_call_site_found_via_literal_prefilter_but_unconfirmed``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle. Keep byte-identical to repo_map.py's twins (``_tree_sitter_node_text``) if either
# ever changes.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Node types whose "name" field defines *this* declaration rather than referencing an existing
# one elsewhere -- excluded from the reference/call walk, mirroring every other language's
# ``_is_definition_identifier`` (see ``lang_go._GO_NAME_DEFINING_PARENT_TYPES`` for the sibling).
# ---------------------------------------------------------------------------

# Per-match honesty bands for every Java reference/call entry -- see the module docstring's
# "RESOLUTION CONFIDENCE / PROVENANCE" section for the full derivation of both numbers/strings.
_JAVA_DEMOTED_CONFIDENCE = 0.6
_JAVA_DEMOTED_PROVENANCE = "java-name-heuristic"
_JAVA_CONFIRMED_CONFIDENCE = 0.9
_JAVA_CONFIRMED_PROVENANCE = "java-infile-type-confirmation"
_JAVA_CROSS_FILE_CONFIRMED_PROVENANCE = "java-package-type-confirmation"

# Conventional Maven/Gradle Java source roots (Task 11A). Package-path alignment without these
# markers is also accepted when the on-disk path suffix matches the package declaration.
_JAVA_SOURCE_ROOT_MARKERS: tuple[tuple[str, ...], ...] = (
    ("src", "main", "java"),
    ("src", "test", "java"),
)

_JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)\s*;", re.MULTILINE)
_JAVA_IMPORT_RE = re.compile(
    r"^\s*import\s+(static\s+)?([A-Za-z_][\w.]*(?:\.\*)?)\s*;",
    re.MULTILINE,
)

# Declaration node types (see ``_java_declared_types_for_names``) whose "type"/"name" field pair
# gives a receiver identifier's declared static type, all readable from THIS file alone.
_JAVA_TYPE_DECLARATION_PARENT_TYPES = {"local_variable_declaration", "field_declaration"}

_JAVA_TYPE_BODY_DECLARATION_TYPES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
}

_JAVA_NAME_DEFINING_PARENT_TYPES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "method_declaration",
    "constructor_declaration",
    "variable_declarator",
    "formal_parameter",
    "catch_formal_parameter",
    "enum_constant",
}


def _java_package_declaration(source: str) -> str | None:
    match = _JAVA_PACKAGE_RE.search(source)
    return match.group(1) if match else None


def _java_import_specs(source: str) -> list[tuple[bool, str]]:
    """Return ``(is_static, import_name)`` pairs from *source* (regex; no second parser)."""
    return [(bool(match.group(1)), match.group(2)) for match in _JAVA_IMPORT_RE.finditer(source)]


def _java_source_root_for_path(path: Path, package: str | None) -> Path | None:
    """Return the Java source root for *path*, or None when package/path mapping is unestablishable.

    Fail closed: a ``package`` declaration whose path does not sit under a conventional
    ``src/main/java`` / ``src/test/java`` root (or a package-path-aligned root) yields None --
    never a guessed parent.
    """
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    parts = resolved.parts
    name = resolved.name

    for marker in _JAVA_SOURCE_ROOT_MARKERS:
        marker_len = len(marker)
        for index in range(len(parts) - marker_len):
            if parts[index : index + marker_len] != marker:
                continue
            root = Path(*parts[: index + marker_len])
            if package:
                expected = root.joinpath(*package.split("."), name)
            else:
                expected = root / name
            if expected == resolved:
                return root

    if package:
        pkg_parts = tuple(package.split("."))
        parent_parts = resolved.parent.parts
        if len(parent_parts) >= len(pkg_parts) and parent_parts[-len(pkg_parts) :] == pkg_parts:
            root = resolved.parent
            for _ in pkg_parts:
                root = root.parent
            return root
    return None


def _java_definition_fqn(definition_path: Path, definition_source: str) -> str | None:
    """FQN of the public top-level type in *definition_path*, or None if mapping fails."""
    package = _java_package_declaration(definition_source)
    if package is None:
        return None
    if _java_source_root_for_path(definition_path, package) is None:
        return None
    return f"{package}.{definition_path.stem}"


def _java_type_fqns_visible_in_file(source: str, type_name: str, file_path: Path) -> set[str]:
    """FQNs *type_name* could denote in *source* via type import, wildcard, or same-package."""
    if not type_name or not _is_clean_symbol_name(type_name):
        return set()
    fqns: set[str] = set()
    package = _java_package_declaration(source)
    for is_static, name in _java_import_specs(source):
        if is_static:
            continue
        if name.endswith(".*"):
            fqns.add(f"{name[:-2]}.{type_name}")
        elif name == type_name or name.endswith(f".{type_name}"):
            fqns.add(name)
    if package is not None and _java_source_root_for_path(file_path, package) is not None:
        fqns.add(f"{package}.{type_name}")
    return fqns


def _java_fqn_package_dir_matches(fqn: str, definition_dir: Path) -> bool:
    parts = fqn.split(".")
    if len(parts) < 2:
        return False
    pkg_parts = tuple(parts[:-1])
    dir_parts = definition_dir.parts
    return len(dir_parts) >= len(pkg_parts) and dir_parts[-len(pkg_parts) :] == pkg_parts


def _java_type_resolves_into_definition_dirs(
    type_name: str,
    source: str,
    file_path: Path,
    definition_dirs: frozenset[str],
) -> bool:
    if not definition_dirs:
        return False
    fqns = _java_type_fqns_visible_in_file(source, type_name, file_path)
    if not fqns:
        return False
    for fqn in fqns:
        if fqn.rsplit(".", 1)[-1] != type_name:
            continue
        for directory in definition_dirs:
            try:
                resolved_dir = Path(directory).expanduser().resolve()
            except OSError:
                continue
            if _java_fqn_package_dir_matches(fqn, resolved_dir):
                return True
    return False


def java_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    """True iff *file_path* can see *symbol*'s definition via Java package/import evidence.

    Requires an establishable package/source-root mapping for the definition file. Same-package
    visibility also requires an establishable mapping for the importer. A flat or mismatched tree
    returns False -- demote, never guess. *repo_root* is accepted for LanguageSpec signature
    parity with Go; resolution is path/package local and does not scan the repo.
    """
    del repo_root  # signature parity; unused by design
    del symbol  # Java visibility is type/package scoped; member name is not part of import proof
    try:
        definition = Path(definition_path).expanduser().resolve()
        importer = file_path.expanduser().resolve()
    except OSError:
        return False
    try:
        definition_source = definition.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    definition_fqn = _java_definition_fqn(definition, definition_source)
    if definition_fqn is None:
        return False
    definition_package = definition_fqn.rsplit(".", 1)[0]
    definition_type = definition_fqn.rsplit(".", 1)[1]

    importer_package = _java_package_declaration(source)
    if (
        importer_package is not None
        and importer_package == definition_package
        and _java_source_root_for_path(importer, importer_package) is not None
    ):
        return True

    for is_static, name in _java_import_specs(source):
        if not is_static:
            if name == definition_fqn or name == f"{definition_package}.*":
                return True
            continue
        # static import of the type's members (or a specific member) also proves the file
        # references this definition's type.
        if name == f"{definition_fqn}.*" or name.startswith(f"{definition_fqn}."):
            return True
        if name == definition_fqn:
            return True
    # A non-static import of the simple type under another package must not match by stem alone;
    # only FQN / same-package / wildcard of the definition package qualify.
    _ = definition_type
    return False


def _java_object_creation_type_identifier(node: Any) -> Any | None:
    """The base ``type_identifier`` node for an ``object_creation_expression``'s constructor
    type -- direct if the ``type`` field IS a ``type_identifier`` (``new Helper()``), or the
    first ``type_identifier`` child of a ``generic_type`` field (``new ArrayList<Foo>()`` ->
    ``ArrayList``, never the generic argument ``Foo``). Anything else (a fully-qualified
    ``scoped_type_identifier``, an array type, ...) is an accepted, documented gap -- returns
    ``None`` so that node falls through to no special handling rather than a wrong guess.
    """
    type_field = node.child_by_field_name("type")
    if type_field is None:
        return None
    if type_field.type == "type_identifier":
        return type_field
    if type_field.type == "generic_type":
        for child in type_field.children:
            if child.type == "type_identifier":
                return child
    return None


def _java_resolution_context(
    root: Any, source_bytes: bytes, symbol: str
) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """Single upfront walk building the three facts the in-file receiver-type CONFIRMED band
    (see the module docstring) needs -- all derived from THIS file's AST alone, never another
    file:

    - ``declared_types``: every ``identifier`` NAME this file declares with a readable static
      type (a local variable, a field, a formal parameter, a catch parameter), mapped to the SET
      of ``type_identifier`` texts it was ever declared with in this file (a set, not a single
      value, because the same name can be redeclared with different types across scopes -- this
      walk is file-wide and does not model scoping, so ambiguity is preserved rather than
      guessed away; ``_java_receiver_confirmation`` below only needs ONE member to match).
    - ``method_owner_types``: the name of every class/interface/enum/record declared in this file
      whose OWN body (direct child ``method_declaration``, not inherited, not nested deeper)
      declares a method named *symbol*.
    - ``field_owner_types``: same, for a ``field_declaration``'s ``variable_declarator`` named
      *symbol*.

    Only ``type_identifier``-typed declarations are recorded (a primitive type like ``int`` or an
    array/generic type is never a valid receiver for a member call, so it is deliberately never
    added -- this also means a primitive-typed local variable can never accidentally "confirm" a
    match, which would be a fabricated result).
    """
    declared_types: dict[str, set[str]] = {}
    method_owner_types: set[str] = set()
    field_owner_types: set[str] = set()

    def node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def record_declared_type(name_node: Any | None, type_node: Any | None) -> None:
        if name_node is None or type_node is None or type_node.type != "type_identifier":
            return
        declared_types.setdefault(node_text(name_node), set()).add(node_text(type_node))

    stack = [root]
    while stack:
        node = stack.pop()
        node_type = node.type

        if node_type == "variable_declarator":
            parent = node.parent
            if parent is not None and parent.type in _JAVA_TYPE_DECLARATION_PARENT_TYPES:
                record_declared_type(
                    node.child_by_field_name("name"), parent.child_by_field_name("type")
                )
        elif node_type in {"formal_parameter", "catch_formal_parameter"}:
            record_declared_type(node.child_by_field_name("name"), node.child_by_field_name("type"))
        elif node_type == "method_declaration":
            name_field = node.child_by_field_name("name")
            body_parent = node.parent
            type_decl = body_parent.parent if body_parent is not None else None
            if (
                name_field is not None
                and node_text(name_field) == symbol
                and body_parent is not None
                and body_parent.type == "class_body"
                and type_decl is not None
                and type_decl.type in _JAVA_TYPE_BODY_DECLARATION_TYPES
            ):
                type_name_field = type_decl.child_by_field_name("name")
                if type_name_field is not None:
                    method_owner_types.add(node_text(type_name_field))
        elif node_type == "field_declaration":
            for declarator in node.children:
                if declarator.type != "variable_declarator":
                    continue
                name_field = declarator.child_by_field_name("name")
                body_parent = node.parent
                type_decl = body_parent.parent if body_parent is not None else None
                if (
                    name_field is not None
                    and node_text(name_field) == symbol
                    and body_parent is not None
                    and body_parent.type == "class_body"
                    and type_decl is not None
                    and type_decl.type in _JAVA_TYPE_BODY_DECLARATION_TYPES
                ):
                    type_name_field = type_decl.child_by_field_name("name")
                    if type_name_field is not None:
                        field_owner_types.add(node_text(type_name_field))

        stack.extend(node.children)

    return declared_types, method_owner_types, field_owner_types


def _java_enclosing_type_name(node: Any, source_bytes: bytes) -> str | None:
    """Walk PARENTS (not the file-wide stack above) to find the nearest enclosing type
    declaration's name -- used only for a bare ``this`` receiver, whose "declared type" is by
    definition the type currently being defined, not something read off a variable declaration.
    """
    current = node.parent
    while current is not None:
        if current.type in _JAVA_TYPE_BODY_DECLARATION_TYPES:
            name_field = current.child_by_field_name("name")
            if name_field is not None:
                return _tree_sitter_node_text(source_bytes, name_field)
            return None
        current = current.parent
    return None


def java_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    parser: Any | None = None,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """In-file AST reference/call rows for *symbol* in *path*.

    When *definition_dirs* is supplied (repo_map always supplies it from preferred definitions),
    a receiver whose declared type resolves through package/import into those directories earns the
    cross-file confirmed band. *repo_root* is accepted for registry-adapter signature parity.
    """
    del repo_root  # signature parity with Go adapter; unused by the Java package resolver
    if path.suffix != ".java":
        return [], []
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    # Split strictly on "\n" (tree-sitter's own row semantics), stripping a trailing "\r" so
    # CRLF-terminated files still read cleanly -- see lang_go.go_references_and_calls's identical
    # comment (F26 fix, audit #63) for why `str.splitlines()` is NOT safe here.
    lines = [line.rstrip("\r") for line in source.split("\n")]
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None or parent.type not in _JAVA_NAME_DEFINING_PARENT_TYPES:
            return False
        name_field = parent.child_by_field_name("name")
        return name_field is not None and name_field == node

    declared_types, method_owner_types, field_owner_types = _java_resolution_context(
        tree.root_node, source_bytes, symbol
    )

    def _receiver_confirmation(object_node: Any | None, owner_types: set[str]) -> bool:
        """True only when *object_node* has a static type readable from THIS file that is ALSO
        one of *owner_types* (types this file itself saw declare a member named *symbol*).
        """
        if object_node is None or not owner_types:
            return False
        if object_node.type == "identifier":
            receiver_types = declared_types.get(_node_text(object_node), set())
            return not receiver_types.isdisjoint(owner_types)
        if object_node.type == "this":
            enclosing = _java_enclosing_type_name(object_node, source_bytes)
            return enclosing is not None and enclosing in owner_types
        return False

    def _cross_file_receiver_confirmation(object_node: Any | None) -> bool:
        if object_node is None or not definition_dirs:
            return False
        candidate_types: set[str] = set()
        if object_node.type == "identifier":
            name = _node_text(object_node)
            candidate_types.update(declared_types.get(name, set()))
            # Bare type qualifier (``Foo.getCount()``) -- not a local variable.
            candidate_types.add(name)
        elif object_node.type == "this":
            return False
        for type_name in candidate_types:
            if _java_type_resolves_into_definition_dirs(type_name, source, path, definition_dirs):
                return True
        return False

    def _emit(
        bucket: list[dict[str, Any]],
        node: Any,
        *,
        kind: str,
        ref_kind: str,
        confirmed: bool,
        cross_file: bool = False,
    ) -> None:
        if confirmed:
            confidence = _JAVA_CONFIRMED_CONFIDENCE
            provenance = (
                _JAVA_CROSS_FILE_CONFIRMED_PROVENANCE if cross_file else _JAVA_CONFIRMED_PROVENANCE
            )
        else:
            confidence = _JAVA_DEMOTED_CONFIDENCE
            provenance = _JAVA_DEMOTED_PROVENANCE
        bucket.append({
            "name": symbol,
            "kind": kind,
            "ref_kind": ref_kind,
            "file": str(path),
            "line": node.start_point[0] + 1,
            "text": _line_text(node),
            "resolution_confidence": confidence,
            "resolution_provenance": [provenance],
        })

    # Nodes already claimed by a special-case branch below (method_invocation.name,
    # object_creation_expression's constructor type_identifier, field_access.field) are tracked
    # here so the generic identifier/type_identifier walk never double-emits them. Keyed on
    # (start_byte, end_byte), NOT Python `id()`: the `tree_sitter` bindings mint a FRESH wrapper
    # object on every `.children`/`.child_by_field_name` access to the same underlying node, so
    # `id()` silently never matches across two separate traversals -- verified empirically before
    # shipping this (a real, easy-to-miss bug: it would have made every "claimed" node ALSO
    # double-emit through the generic walk below).
    claimed_node_ids: set[tuple[int, int]] = set()

    def _confirmation_for_member(
        object_node: Any | None, owner_types: set[str]
    ) -> tuple[bool, bool]:
        if _receiver_confirmation(object_node, owner_types):
            return True, False
        if _cross_file_receiver_confirmation(object_node):
            return True, True
        return False, False

    def _walk(root: Any) -> None:
        # Explicit-stack DFS (not recursion) -- matches every other language extractor in this
        # registry (lang_go.py's `_walk`, `_python_references_and_calls`'s manual recursion is
        # the one exception, capped by Python's own recursion limit); avoids a RecursionError on
        # a pathologically deep real-world AST.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type

            if node_type == "method_invocation":
                name_field = node.child_by_field_name("name")
                if name_field is not None and _node_text(name_field) == symbol:
                    claimed_node_ids.add((name_field.start_byte, name_field.end_byte))
                    confirmed, cross_file = _confirmation_for_member(
                        node.child_by_field_name("object"), method_owner_types
                    )
                    _emit(
                        references,
                        name_field,
                        kind="reference",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
                    _emit(
                        calls,
                        name_field,
                        kind="call",
                        ref_kind="call",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )
            elif node_type == "object_creation_expression":
                type_identifier = _java_object_creation_type_identifier(node)
                if type_identifier is not None and _node_text(type_identifier) == symbol:
                    claimed_node_ids.add((type_identifier.start_byte, type_identifier.end_byte))
                    _emit(
                        references,
                        type_identifier,
                        kind="reference",
                        ref_kind="constructor",
                        confirmed=False,
                    )
                    _emit(
                        calls,
                        type_identifier,
                        kind="call",
                        ref_kind="constructor",
                        confirmed=False,
                    )
            elif node_type == "field_access":
                field_field = node.child_by_field_name("field")
                if field_field is not None and _node_text(field_field) == symbol:
                    claimed_node_ids.add((field_field.start_byte, field_field.end_byte))
                    confirmed, cross_file = _confirmation_for_member(
                        node.child_by_field_name("object"), field_owner_types
                    )
                    _emit(
                        references,
                        field_field,
                        kind="reference",
                        ref_kind="field",
                        confirmed=confirmed,
                        cross_file=cross_file,
                    )

            stack.extend(reversed(node.children))

    def _walk_generic_identifiers(root: Any) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if (node.start_byte, node.end_byte) not in claimed_node_ids:
                if (
                    node_type == "identifier"
                    and _node_text(node) == symbol
                    and not _is_definition_identifier(node)
                ):
                    _emit(references, node, kind="reference", ref_kind="value", confirmed=False)
                elif node_type == "type_identifier" and _node_text(node) == symbol:
                    _emit(references, node, kind="reference", ref_kind="type", confirmed=False)
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    _walk_generic_identifiers(tree.root_node)

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


__all__ = [
    "java_file_imports_symbol_from_definition",
    "java_references_and_calls",
]
