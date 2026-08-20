"""Python-specific import, module and symbol resolution extracted from `repo_map`.

Everything here is Python dialect knowledge -- module-path candidate roots, `sys.path` hack
detection, relative-import bases, dynamic `importlib` calls, decorator qualnames, and the
pytest function/parametrize candidate scan -- plus the private helpers only those paths call.
Split out of `repo_map.py` under docs/design/2026-08-19-split-floor-escape.md.

`_python_references_and_calls` deliberately stays in `repo_map`: the test suite monkeypatches
it there, so a moved copy would be the unpatched one.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.repo_map_cache import _mtime_aware_cache as _mtime_aware_cache
from tensor_grep.cli.repo_map_cache import _resolved_path_str as _resolved_path_str

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


def _python_dynamic_import_call_is_relative(node: ast.Call) -> bool:
    """True when a ``__import__(...)`` call is unambiguously or possibly RELATIVE via its
    ``level`` argument (5th positional, or the ``level=`` keyword) -- ``__import__``'s own
    relative-import marker is this integer, separate from ``import_module``'s leading-dot
    module-string convention (the caller checks that with a plain ``.startswith(".")`` on the
    literal module name instead, since ``import_module`` has no ``level`` parameter at all --
    this always returns ``False`` for an ``import_module``/bare-``import_module`` call, harmlessly).

    A non-literal ``level`` value (a variable, an expression) can't be proven to be the safe
    default of ``0``, so it is conservatively treated as relative too -- the same "can't prove
    it's safe" fail-closed posture as every other honesty check in this module (e.g. the
    non-literal-argument case just above, or the #152 sys.path-hack idiom matcher).
    """
    level_arg: ast.expr | None = None
    if len(node.args) >= 5:
        level_arg = node.args[4]
    else:
        for keyword in node.keywords:
            if keyword.arg == "level":
                level_arg = keyword.value
                break
    if level_arg is None:
        return False
    if isinstance(level_arg, ast.Constant) and isinstance(level_arg.value, int):
        return level_arg.value != 0
    return True  # non-literal level -- can't prove it's 0, fail closed (treat as relative)


def _python_dynamic_import_entry_for_call(node: ast.AST) -> dict[str, Any] | None:
    """Given a single AST node, return its dynamic-import entry dict if `node` is one of the 3
    dynamic-import call shapes -- ``__import__(...)``, bare ``import_module(...)``, or
    ``importlib.import_module(...)`` (see `_is_python_dynamic_import_call`) -- else `None`. The
    returned dict is shaped like the static entries `_python_imports_with_lines` emits (`module`,
    `line`, `level`) plus two #93 SUB-1 markers: `dynamic` (always `True` here) and
    `dynamic_unresolved` (`True` when there is no static-string-literal target to resolve at all
    -- the first argument isn't a literal, e.g. a variable or an f-string -- OR when the literal
    names a RELATIVE import this slice deliberately does not attempt to resolve, see below).

    Both `_python_imports_with_lines` (opt10 F4.2) and `_python_imports_and_symbols` (opt10
    lever-1) fold this per-node check into their own single `ast.walk(tree)` pass instead of
    paying for a second whole-tree walk. This used to be the loop body of a separate whole-tree
    helper, `_python_dynamic_import_entries` -- pulled out unchanged (same literal-extraction,
    same relative-literal fail-closed check, same entry shape) so `_python_imports_with_lines`
    could fold it into its existing walk (opt10 F4.2) while `_python_imports_and_symbols` kept
    calling `_python_dynamic_import_entries` wholesale for its own separate walk. Once opt10
    lever-1 migrated that last remaining caller to call this per-node function directly too,
    `_python_dynamic_import_entries` had zero callers left and was removed as dead code -- there
    is no longer a standalone whole-tree dynamic-import walk anywhere in this module.

    Fails CLOSED on the non-literal-argument case: `module` is `""` rather than a guessed name --
    asserting a fabricated edge for an import whose target we can't actually read would be a
    precision regression in a moat feature (see `_resolve_raw_import_entry` /
    `_confirm_import_edges`, which both skip resolution entirely when `dynamic_unresolved` is
    set).

    Also fails CLOSED on a RELATIVE literal -- a leading-dot `import_module(".sibling",
    package=...)` module string, or an `__import__(name, ..., level=N)` call with a nonzero (or
    non-literal, unprovable) `level` (`_python_dynamic_import_call_is_relative` above -- scope
    slice #6, the tractable dynamic-import LITERAL slice). Both forms carry a real literal name,
    kept here (unlike the non-literal case, `module` is NOT blanked to `""` -- nothing is
    fabricated, the literal text is exactly what the source says), but the downstream absolute
    resolver (`_python_module_candidates`) must never see it: its `_python_module_parts` splitter
    drops a leading empty component from `".sibling".split(".")`, so an unguarded relative
    literal would silently be searched for as if it were the ABSOLUTE module "sibling" -- a
    PROVEN false-edge risk (a same-named-but-unrelated top-level file can exist anywhere in the
    search roots) not merely a theoretical one. Resolving the relative form correctly needs a
    second, chained lookup (resolve the `package`/enclosing-package argument to a directory
    FIRST, only then walk it by the relative level) that this slice does not build -- out of
    scope per the "no false edges, missing is fine" contract; a future slice can add it.
    `package` itself is never read here (a non-literal `package` -- the overwhelmingly common
    real-world shape, `package=__name__`/`package=__package__` -- couldn't be resolved statically
    anyway, and even a literal `package` string is left for that future slice), so this is a pure
    detect-and-refuse guard on the `module`/`level` shape alone.
    """
    if not isinstance(node, ast.Call) or not _self._is_python_dynamic_import_call(node):
        return None
    literal_module: str | None = None
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        literal_module = node.args[0].value
    dynamic_unresolved = literal_module is None
    if literal_module is not None and (
        literal_module.startswith(".") or _python_dynamic_import_call_is_relative(node)
    ):
        dynamic_unresolved = True
    return {
        "module": literal_module or "",
        "line": int(node.lineno),
        "level": 0,
        "dynamic": True,
        "dynamic_unresolved": dynamic_unresolved,
    }


def _python_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix != ".py":
        return [], []

    try:
        tree = _self._cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    # opt10/lever-1 speed fix: merge the imports / symbols / dynamic-import scans into a SINGLE
    # `ast.walk(tree)` pass instead of three separate whole-tree walks (one for imports, one for
    # symbols, and a third buried inside `_python_dynamic_import_entries`) -- the same
    # single-walk-plus-helper-reuse pattern #716 already shipped for the sibling
    # `_python_imports_with_lines` (see that function's own comment, and its F4.2 test, in
    # tests/unit/test_file_deps.py). `ast.Import`, `ast.ImportFrom`, `ast.ClassDef`,
    # `ast.FunctionDef`/`ast.AsyncFunctionDef`, and `ast.Call` are mutually-exclusive node
    # subclasses, so each node dispatches to at most one branch below -- identical in effect to
    # filtering three separate walks for three disjoint predicates and concatenating the
    # results. The trailing `sorted(dict.fromkeys(imports))` + `symbols.sort(...)` below make
    # append ORDER irrelevant, so interleaving all three kinds of appends into one walk is
    # byte-identical to the old three-walk output. See
    # test_python_imports_and_symbols_merges_all_three_walks_into_one (walk-count + output-
    # identity proof).
    #
    # Nested-scope recall fix (companion to the same change in `_python_imports_with_lines`):
    # `ast.walk` (not `tree.body`) so a plain `import`/`from ... import` STATEMENT nested inside a
    # function body, an `if`/`try` block, or an `if TYPE_CHECKING:` guard feeds this alias-graph
    # list too. This list becomes `repo_map["imports"]` (`build_repo_map`'s per-file entries),
    # which is the ONLY source `_reverse_importers`'s alias PREFILTER reads (see
    # `build_file_importers_from_map`, `build_symbol_callers_from_map`,
    # `build_symbol_blast_radius_from_map`, `build_context_render`'s agent-capsule scoring) --
    # a candidate file whose ONLY import of a target is scope-nested was previously invisible to
    # the prefilter, so it never even reached the reverse `tg importers` CONFIRM step
    # (`_confirm_import_edges`) regardless of that step's own recall. Verified low-risk: this is a
    # strict superset (`ast.walk` visits everything `tree.body` did, plus more), it only ADDS
    # entries (recall-only, never removes/reorders an existing one), and the full relevant test
    # suite (agent/blast-radius/callers/refs/orient/importers, 500+ tests) is green across this
    # change with zero new failures.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                for alias in node.names:
                    imports.append(f"{node.module}.{alias.name}")
            elif node.level:
                # `from . import x` / `from .. import x` -- no dotted module text, only
                # relative dots plus the imported names (which may themselves be sibling
                # submodules, e.g. `from . import helpers` importing `helpers.py`). Recording
                # the bare alias name keeps this import in the reverse-import alias graph
                # (`_reverse_importers`/`_module_aliases_for_path`) so `tg importers` can even
                # PREFILTER a sibling `from . import X` importer -- omitting it here (unlike
                # `_python_imports_with_lines`, which already records it for the forward
                # `tg imports` primitive) was a genuine recall gap, not an intentional
                # exclusion (#74 review fix). The precise per-candidate CONFIRM step
                # (`_python_module_matches_definition`) still disambiguates which file it
                # actually resolves to -- this only widens the prefilter's candidate set.
                for alias in node.names:
                    imports.append(alias.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                _self._symbol_record(
                    name=node.name,
                    kind="class",
                    file=path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                _self._symbol_record(
                    name=node.name,
                    kind="function",
                    file=path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        elif isinstance(node, ast.Call):
            # #93 SUB-1: fold in dynamic-import call targets (only the STATICALLY resolvable
            # ones -- an unresolved dynamic import has no literal name to add to this
            # alias-graph prefilter list; see `_python_dynamic_import_entry_for_call`) so a file
            # that ONLY reaches a target dynamically is still discoverable as a candidate by the
            # reverse `tg importers` prefilter (`_reverse_importers`), not just by the forward
            # `tg imports` primitive. Reuses `_python_dynamic_import_entry_for_call` -- the
            # ALREADY-TESTED per-node helper `_python_imports_with_lines` folds into its own
            # single walk too (see that function) -- instead of calling the whole-tree
            # `_python_dynamic_import_entries(tree)` this call site used to call, so the
            # dynamic-import check rides the SAME walk as the import/symbol checks above instead
            # of paying for a separate (third) whole-tree `ast.walk`. This was
            # `_python_dynamic_import_entries`'s last remaining caller -- with it migrated to the
            # per-node helper too, that whole-tree function had zero callers left and was removed
            # as dead code (opt10 lever-1).
            #
            # #703 gate NIT-1 fix: a plain `entry["module"]` truthiness check is NOT actually
            # equivalent to "statically resolvable" -- `_python_dynamic_import_entry_for_call`
            # marks a RELATIVE literal (leading-dot `import_module(".sibling", package=...)`) or
            # an explicit-nonzero-`level` `__import__(...)` as `dynamic_unresolved` too, and
            # unlike the non-literal-argument case, those keep their real literal text in
            # `module` (nothing is fabricated/blanked, see that helper's docstring) rather than
            # blanking it to `""`. So an unresolved-but-non-blank literal like `".sibling"` must
            # not slip into `imports`, which becomes `repo_map["imports"]` -- the alias graph
            # `tg blast-radius`'s reverse SCORING prefilter
            # (`_reverse_import_distances`/`_reverse_importers`) reads. A
            # same-named-but-unrelated top-level file (`_import_alias_candidates` + the
            # substring test in `_import_graph_bonus`) could then fuzzy-match that unresolved
            # literal and be pulled into `affected_files`/`dependent_files` -- even though the
            # precise `tg importers` edge (`_resolve_raw_import_entry` / `_confirm_import_edges`)
            # already excludes it correctly, since THAT path has always skipped resolution
            # whenever `dynamic_unresolved` is set. Requiring `not entry["dynamic_unresolved"]`
            # here makes this prefilter honor the exact same "no false edges, missing is fine"
            # contract the precise resolvers already enforce. Pinned by
            # test_blast_radius_excludes_unresolved_dynamic_literal_fuzzy_match
            # (regression-lock) and test_blast_radius_legitimate_dependent_ranking_pin (proves
            # the legitimate reverse-scoring ranking is unaffected) in
            # tests/unit/test_file_deps.py.
            entry = _python_dynamic_import_entry_for_call(node)
            if entry is not None and entry["module"] and not entry["dynamic_unresolved"]:
                imports.append(str(entry["module"]))

    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


# Fix A / Guard 1: this scans+parses the importer file (ast.parse for Python, regex/AST for
# JS/TS/Rust) once PER (file, definition) PAIR, and caller_scan calls it in an any() loop over
# every definition_file for every candidate file -- N definitions means N re-reads/re-parses of
# the same file. @_mtime_aware_cache requires a str first-positional arg (its wrapper keys the
# cache on path_str directly), so the parameter is `path_str: str` here, not `Path` -- callers
# that still pass a Path work at runtime (Path(path_str) below accepts either), but the one hot
# call site (build_symbol_callers_from_map's _should_scan_for_symbol_callers) is updated to pass
# str(current) explicitly so the cache key is a plain, consistently-typed string.
#
# Guard 4: bound the cache's entry count -- this key includes symbol+definition_path+repo_root,
# so it can grow faster than a plain per-file cache; keep it generous but finite.
def _python_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    try:
        tree = _self._cached_ast_parse(source)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                _self._module_path_matches_definition(alias.name, definition_path)
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if not _self._module_path_matches_definition(node.module, definition_path):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return True
            elif node.level:
                # `from . import helpers` / `from .. import helpers` -- no dotted `node.module`
                # text, only relative dots plus the imported name(s). This bare form BINDS THE
                # SUBMODULE ITSELF (like `ast.Import`, not a `from X import symbol` name
                # binding), so match on module path alone -- mirrors #460's fix for the forward
                # `_python_imports_and_symbols`/`_python_imports_with_lines` extractors (the `tg
                # imports`/`tg importers` primitive), applied here to the callers/blast-radius
                # consumer path, which was still silently dropping this shape (audit #81 #3): the
                # old `if not node.module: continue` guard skipped every bare relative import, so
                # a sibling `from . import helpers` consumer was invisible to `tg callers`/`tg
                # blast-radius` even though `tg importers` already found it.
                if any(
                    _self._module_path_matches_definition(alias.name, definition_path)
                    for alias in node.names
                ):
                    return True
    return False


def _python_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = _self._cached_ast_parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _self._module_path_matches_definition(alias.name, definition_path):
                    return {
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "module": alias.name,
                        "provenance": "parser-backed",
                    }
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if not _self._module_path_matches_definition(node.module, definition_path):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return {
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "module": node.module,
                        "provenance": "parser-backed",
                    }
            elif node.level:
                # Mirror the sibling fix in _python_file_imports_symbol_from_definition above
                # (audit #81 #3): `from . import helpers` has no dotted `node.module`, only
                # relative dots + the imported name(s), which bind the SUBMODULE itself.
                for alias in node.names:
                    if _self._module_path_matches_definition(alias.name, definition_path):
                        return {
                            "start_line": int(node.lineno),
                            "end_line": int(getattr(node, "end_lineno", node.lineno)),
                            "module": alias.name,
                            "provenance": "parser-backed",
                        }
    return None


def _python_classify_ref_kind(node: ast.AST, parent: ast.AST | None, *, in_annotation: bool) -> str:
    """Classify an already-matched Python Name/Attribute reference node (T1 additive).

    Only called for nodes the existing matcher already emits a row for (moat P0-T1: classify
    EXISTING rows, never widen the match set -- that would change row counts). Precedence: a
    node that IS the callee of its parent ``ast.Call`` is "call" even inside an annotation
    subtree (unlikely but keeps the check order simple); otherwise annotation subtrees are
    "type"; a bare ``ast.Attribute`` is "field"; anything else is "value".
    """
    if isinstance(parent, ast.Call) and parent.func is node:
        return "call"
    if in_annotation:
        return "type"
    if isinstance(node, ast.Attribute):
        return "field"
    return "value"


def _python_provider_alias_calls(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = _self._cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    alias_names = {symbol}

    def _binding_name(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
        return None

    def _assignment_targets(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for current in target.elts:
                names.extend(_assignment_targets(current))
            return names
        return []

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Import):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Assign):
                binding_name = _binding_name(node.value)
                if binding_name not in alias_names:
                    continue
                for target_name in (
                    _assignment_targets(node.targets[0])
                    if len(node.targets) == 1
                    else [name for target in node.targets for name in _assignment_targets(target)]
                ):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True
            elif isinstance(node, ast.AnnAssign):
                binding_name = _binding_name(node.value) if node.value is not None else None
                if binding_name not in alias_names:
                    continue
                for target_name in _assignment_targets(node.target):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True

    calls: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        alias_name = _binding_name(node.func)
        if alias_name not in alias_names:
            continue
        calls.append({
            "name": symbol,
            "kind": "call",
            "file": str(path),
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
            "alias": alias_name,
        })

    calls.sort(key=lambda item: (item["file"], item["line"], item.get("alias", ""), item["text"]))
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


def _python_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = _self._cached_ast_parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    sources: list[dict[str, Any]] = []

    symbol_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == symbol
    ]
    symbol_nodes.sort(
        key=lambda current: (current.lineno, getattr(current, "end_lineno", current.lineno))
    )
    for node in symbol_nodes:
        end_lineno = getattr(node, "end_lineno", node.lineno)
        block = "\n".join(lines[node.lineno - 1 : end_lineno])
        if block:
            block = f"{block}\n"
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        sources.append({
            "name": symbol,
            "kind": kind,
            "file": str(path),
            "start_line": node.lineno,
            "end_line": end_lineno,
            "source": block,
        })

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _python_references_and_calls_for_registry(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # definition_dirs is part of the uniform registry adapter signature (Go F25); python
    # ignores it. repo_root likewise -- the underlying extractor is path+symbol only.
    return _self._python_references_and_calls(path, symbol)


def _python_provider_alias_calls_for_registry(
    path: Path, symbol: str, repo_root: Path | str | None = None
) -> list[dict[str, Any]]:
    return _python_provider_alias_calls(path, symbol)


def _python_import_update_target_for_registry(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    return _python_import_update_target(file_path, symbol, definition_path)


# #74 moat: `tg imports`/`tg importers` -- the scoped file-dependency primitive. Companion to
# `_imports_and_symbols_for_path` above, which collapses imports to a deduped, line-less
# `list[str]` (fine for the reverse-import alias graph, useless for a command that must report
# *where* each import statement lives). Mirrors that function's per-language extraction sources
# exactly (same AST node types / same regexes as `_python_imports_and_symbols` and
# `_regex_imports_and_symbols`) so raw recall stays identical -- this only adds the line number
# `tg imports` needs and keeps one row per import STATEMENT (not one row per imported symbol),
# which is the right unit for a file-dependency primitive.
def _python_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _self._max_parse_bytes():
        return []
    try:
        tree = _self._cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    dynamic_entries: list[dict[str, Any]] = []
    # Nested-scope recall fix: `ast.walk` (not `tree.body`) so a plain `import`/`from ... import`
    # STATEMENT nested inside a function body, an `if`/`try` block, or an `if TYPE_CHECKING:`
    # guard is collected too -- `tree.body` only ever visited module-top-level statements,
    # silently missing anything scope-nested (a `tg imports`/`tg importers` recall gap;
    # `result_incomplete` stayed False, so the omission was invisible).
    #
    # opt10 F4.2 speed fix: this single walk ALSO picks up `__import__`/`import_module`/
    # `importlib.import_module` CALLS -- the #93 SUB-1 dynamic-import shape -- via
    # `_python_dynamic_import_entry_for_call` (originally the extracted per-node half of a
    # whole-tree helper, `_python_dynamic_import_entries`), instead of a SEPARATE second
    # `ast.walk(tree)` over the same tree the way this used to call that function wholesale.
    # `ast.Import`/`ast.ImportFrom` and `ast.Call` are disjoint node types, so folding both checks
    # into one walk and accumulating into two separate lists (`entries` for static,
    # `dynamic_entries` for dynamic) produces the IDENTICAL two per-kind orderings `ast.walk`
    # would produce run separately -- `ast.walk` is a deterministic traversal of a fixed tree, so
    # filtering it once for two disjoint predicates and concatenating the two result lists
    # (`entries + dynamic_entries`, same order as the old
    # `entries.extend(_python_dynamic_import_entries(tree))`) is exactly equivalent to filtering
    # it twice. See test_python_imports_with_lines_merges_dynamic_walk_into_single_ast_walk_pass
    # (walk-count + order-identity proof). `_python_dynamic_import_entries` itself -- the
    # whole-tree helper this per-node check was extracted from -- kept its own separate `ast.walk`
    # alive at the time (opt10 F4.2) purely for its OTHER remaining caller,
    # `_python_imports_and_symbols`; opt10 lever-1 later migrated that caller to this same
    # per-node helper too, leaving `_python_dynamic_import_entries` with zero callers, so it was
    # removed as dead code.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                entries.append({"module": alias.name, "line": int(node.lineno), "level": 0})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                entries.append({
                    "module": node.module,
                    "line": int(node.lineno),
                    "level": int(node.level or 0),
                })
            elif node.level:
                # `from . import x` / `from .. import x` -- no dotted module text, only
                # relative dots plus the imported names, which may themselves be
                # submodules (e.g. `from . import utils` importing sibling `utils.py`).
                for alias in node.names:
                    entries.append({
                        "module": alias.name,
                        "line": int(node.lineno),
                        "level": int(node.level),
                    })
        elif isinstance(node, ast.Call):
            dynamic_entry = _python_dynamic_import_entry_for_call(node)
            if dynamic_entry is not None:
                dynamic_entries.append(dynamic_entry)
    entries.extend(dynamic_entries)
    return entries


def _python_module_parts(module_name: str) -> list[str]:
    return [part for part in module_name.split(".") if part]


def _python_relative_base_dir(importer_path: Path, level: int) -> Path:
    # PEP 328: level=1 ("from . import x") resolves relative to the importer's OWN package
    # dir (its parent); level=2 ("from .. import x") goes one dir further up, etc.
    current = importer_path.parent
    for _ in range(max(0, level - 1)):
        current = current.parent
    return current


# #152 fix (CEO v1.69.3 dogfood, 2 HIGH): a Python file that path-hacks its own module
# resolution via `sys.path.insert(...)`/`sys.path.append(...)` -- a common same-repo vendoring
# idiom, e.g.:
#
#     import sys, os
#     sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
#     from ultrathink_routing import route   # lib/ultrathink_routing.py
#
# -- used to be invisible to `_python_candidate_roots` below (whose docstring said so outright:
# "no `sys.path` to consult") and, transitively, to both its forward (`tg imports`) and reverse
# (`tg importers`) consumers, which BOTH funnel through it via `_python_module_candidates` --
# fixing that one chokepoint fixes both directions instead of duplicating the logic twice.
#
# Deliberately narrow: only a handful of common, STATICALLY-resolvable directory-argument idioms
# are recognized --
#   * a bare string literal:              sys.path.insert(0, "lib")
#   * os.path.join(DIRNAME_EXPR, "SUB"[, "SUB2", ...])
#   * DIRNAME_EXPR alone (os.path.dirname(__file__) / os.path.dirname(os.path.abspath(__file__)))
#   * Path(__file__).parent / "SUB" (chained; optionally str(...)-wrapped)
#   * os.path.join(HERE, "SUB") where HERE = os.path.dirname(__file__) earlier in the module
# -- anything with a dynamic/computed component (a variable holding an unknown value, an
# f-string, an environment lookup, any non-literal expression) is left alone: the module stays
# `external`/`resolved=None`, honest, the same fail-closed posture as every other resolver in
# this file. A resolved directory is also required to EXIST and stay INSIDE the scanned repo
# root (`_path_is_relative_to`, the same containment guard used elsewhere in this module) -- a
# `..`-escape or an absolute path outside the root is silently ignored, never followed.
def _python_sys_path_dunder_file(node: ast.AST) -> bool:
    """True for the bare `__file__` name expression."""
    return isinstance(node, ast.Name) and node.id == "__file__"


def _python_sys_path_os_path_call_args(node: ast.AST, attr: str) -> list[ast.expr] | None:
    """If `node` is exactly `os.path.<attr>(...)` (the literal dotted chain -- an aliased
    `import os.path as op` or `from os.path import dirname` is left alone), return its call
    arguments; else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == attr
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "os"
    ):
        return None
    return node.args


def _python_sys_path_file_dirname_expr(node: ast.AST) -> bool:
    """True for `os.path.dirname(__file__)` or `os.path.dirname(os.path.abspath(__file__))` --
    both mean "this file's own directory"."""
    args = _python_sys_path_os_path_call_args(node, "dirname")
    if args is None or len(args) != 1:
        return False
    arg = args[0]
    if _python_sys_path_dunder_file(arg):
        return True
    abspath_args = _python_sys_path_os_path_call_args(arg, "abspath")
    return (
        abspath_args is not None
        and len(abspath_args) == 1
        and _python_sys_path_dunder_file(abspath_args[0])
    )


def _python_sys_path_file_parent_expr(node: ast.AST) -> bool:
    """True for `Path(__file__).parent` (bare `Path` or a dotted `pathlib.Path`) -- the pathlib
    equivalent of `_python_sys_path_file_dirname_expr`."""
    if not (isinstance(node, ast.Attribute) and node.attr == "parent"):
        return False
    call = node.value
    if not (isinstance(call, ast.Call) and len(call.args) == 1):
        return False
    if not _python_sys_path_dunder_file(call.args[0]):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "Path"
    return isinstance(func, ast.Attribute) and func.attr == "Path"


def _python_sys_path_file_dir_expr(node: ast.AST) -> bool:
    """True for any expression meaning "this file's own directory" (os.path or pathlib style)."""
    return _python_sys_path_file_dirname_expr(node) or _python_sys_path_file_parent_expr(node)


def _python_sys_path_static_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _python_sys_path_join_suffix(node: ast.AST, here_names: frozenset[str]) -> str | None:
    """`os.path.join(FILE_DIR_EXPR, "SUB"[, "SUB2", ...])` -> `"SUB/SUB2"` (a single "/"-joined
    suffix to append to the file's own directory), or None if `node` isn't this shape or any
    "SUB" component isn't a plain string literal. `here_names` lets a `HERE = os.path.dirname(
    __file__)`-style module-level alias (see `_python_sys_path_here_aliases`) stand in for the
    literal FILE_DIR_EXPR as the join's first argument."""
    args = _python_sys_path_os_path_call_args(node, "join")
    if not args:
        return None
    first, *rest = args
    is_file_dir = _python_sys_path_file_dir_expr(first) or (
        isinstance(first, ast.Name) and first.id in here_names
    )
    if not is_file_dir or not rest:
        return None
    parts: list[str] = []
    for arg in rest:
        literal = _python_sys_path_static_str(arg)
        if literal is None:
            return None
        parts.append(literal)
    return "/".join(parts)


def _python_sys_path_truediv_suffix(node: ast.AST) -> str | None:
    """`Path(__file__).parent / "SUB"` (chained divisions allowed, optionally `str(...)`-wrapped)
    -> `"SUB"`, or None if `node` isn't this shape or any segment isn't a plain string literal."""
    current: ast.AST = node
    if (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Name)
        and current.func.id == "str"
        and len(current.args) == 1
    ):
        current = current.args[0]
    parts: list[str] = []
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        literal = _python_sys_path_static_str(current.right)
        if literal is None:
            return None
        parts.append(literal)
        current = current.left
    if not parts or not _python_sys_path_file_parent_expr(current):
        return None
    parts.reverse()
    return "/".join(parts)


def _python_sys_path_here_aliases(tree: ast.Module) -> frozenset[str]:
    """Module-level `HERE = os.path.dirname(__file__)`-style aliases (the optional bullet #5 of
    the #152 idiom list above) -- lets `os.path.join(HERE, "SUB")` resolve the same as spelling
    the dirname expression out inline. Deliberately broad-recall (`ast.walk`, not just
    `tree.body`), matching this module's established nested-scope extraction posture -- a name
    later reassigned to something else is a rare, low-risk over-recognition: it only ever WIDENS
    which directories get tried, it never resolves to a wrong FILE (the final candidate still
    has to exist on disk, inside the repo root)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and _python_sys_path_file_dir_expr(node.value):
            names.add(target.id)
    return frozenset(names)


def _python_sys_path_insert_or_append_arg(node: ast.Call) -> ast.expr | None:
    """`sys.path.insert(idx, ARG)` / `sys.path.append(ARG)` -> `ARG`, else None. Only the plain,
    unaliased `sys.path` attribute chain is recognized (`import sys` then `sys.path....`) -- an
    aliased `sys` import (`import sys as _sys`) is left alone, the same fail-closed posture as
    every other idiom this fix does not try to statically resolve."""
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "path"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
    ):
        return None
    if func.attr == "insert" and len(node.args) >= 2:
        return node.args[1]
    if func.attr == "append" and len(node.args) >= 1:
        return node.args[0]
    return None


def _python_sys_path_arg_to_dir(
    arg: ast.expr, filedir: Path, here_names: frozenset[str]
) -> Path | None:
    """Resolve one `sys.path.insert`/`.append` directory ARGUMENT expression to an absolute
    `Path` relative to `filedir` (the importing file's own directory) -- or None if `arg` isn't
    one of the recognized static idioms."""
    if _python_sys_path_file_dir_expr(arg):
        return filedir
    join_suffix = _python_sys_path_join_suffix(arg, here_names)
    if join_suffix is not None:
        return filedir / join_suffix
    truediv_suffix = _python_sys_path_truediv_suffix(arg)
    if truediv_suffix is not None:
        return filedir / truediv_suffix
    literal = _python_sys_path_static_str(arg)
    if literal is not None:
        return filedir / literal
    return None


@_mtime_aware_cache(maxsize=1024)  # #152 fix: mtime+size in key; one AST walk per file, shared
def _python_sys_path_hack_dirs(path_str: str) -> tuple[str, ...]:
    """Statically-resolvable absolute directories this Python file adds to `sys.path` via
    `sys.path.insert`/`sys.path.append` (see the idiom list in the block comment above). Returns
    `()` for a file with no such calls, or where every call's directory argument is a
    non-literal/dynamic expression.

    Cached by (path, mtime, size) -- a pure function of the file's own source text -- so a file
    with N raw import entries (`_python_candidate_roots` runs once PER entry) parses and walks
    its own AST for this exactly once, not N times.

    Deliberately returns raw, un-containment-checked strings: existence + "stays inside the
    scanned repo root" is enforced by the caller (`_python_sys_path_hack_roots`), which has the
    `repo_root` this function does not need in its cache key -- the same file's sys.path hacks
    resolve to the same absolute dirs regardless of which root the caller is scanning from.
    """
    try:
        file_size = Path(path_str).stat().st_size
    except OSError:
        file_size = 0
    if file_size > _self._max_parse_bytes():
        return ()
    try:
        source = _self._read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return ()
    if "sys.path" not in source:
        # Fast-reject the overwhelming common case (no sys.path manipulation at all) without
        # paying for a full `ast.walk` -- both recognized calls (`sys.path.insert`/`.append`)
        # always contain this literal substring, so this can never skip a real hit.
        return ()
    try:
        tree = _self._cached_ast_parse(source)
    except SyntaxError:
        return ()

    filedir = Path(path_str).parent
    here_names = _python_sys_path_here_aliases(tree)
    dirs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arg = _python_sys_path_insert_or_append_arg(node)
        if arg is None:
            continue
        resolved_dir = _python_sys_path_arg_to_dir(arg, filedir, here_names)
        if resolved_dir is not None:
            dirs.append(str(resolved_dir))
    return tuple(dict.fromkeys(dirs))


def _python_sys_path_hack_roots(
    importer_path: Path, repo_root: Path | str | None
) -> tuple[Path, ...]:
    """Existing, containment-checked sys.path-hacked directories for `importer_path` (raw
    extraction: `_python_sys_path_hack_dirs`). Shared by `_python_candidate_roots` (folds these
    into the general search-root list, tried FIRST) and `_python_module_candidates` (tags the
    winning candidate's provenance as "sys-path-insert") so the existence/containment check
    itself lives in exactly one place. Returns `()` when `repo_root` is unknown (`None`) -- no
    root means no containment boundary to enforce, so this resolves nothing rather than guess.
    """
    normalized_root = _self._normalized_repo_root(repo_root)
    if normalized_root is None:
        return ()
    validated: list[Path] = []
    for hacked_dir in _python_sys_path_hack_dirs(str(importer_path)):
        candidate_dir = Path(hacked_dir)
        if candidate_dir.is_dir() and _self._path_is_relative_to(candidate_dir, normalized_root):
            validated.append(candidate_dir)
    return tuple(validated)


def _python_candidate_roots(importer_path: Path, repo_root: Path | str | None) -> list[Path]:
    """Plausible absolute-import search roots for a Python file.

    Unlike JS/TS (tsconfig baseUrl/paths) or Rust (Cargo.toml workspace members), tensor-grep
    has no primed "project context" for Python module resolution -- this is the net-new
    resolution seam the #74 design flagged as the highest-risk part of `tg imports`. Tries, in
    order: any directory the file itself adds via a statically-resolvable
    `sys.path.insert`/`.append` call (#152 fix -- see `_python_sys_path_hack_roots`), the repo
    root, a `src/` layout root, the importer's own directory, and each ancestor directory up to
    the repo root (covers same-package absolute imports without a full `sys.path` simulation). A
    bare specifier that is a local workspace package NOT reachable via one of these roots is
    honestly misclassified as external -- see the module docstring risk note; recall gaps here
    are disclosed via ``external``/``unresolved``, never silently hidden.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            roots.append(candidate)

    for hacked_root in _python_sys_path_hack_roots(importer_path, repo_root):
        _add(hacked_root)
    normalized_root = _self._normalized_repo_root(repo_root)
    _add(normalized_root)
    if normalized_root is not None:
        _add(normalized_root / "src")
    current = importer_path.parent
    _add(current)
    if normalized_root is not None:
        try:
            current.relative_to(normalized_root)
            within_root = True
        except ValueError:
            within_root = False
        if within_root:
            while current != normalized_root:
                current = current.parent
                _add(current)
    # Walk up past every `__init__.py`-marked package directory: the first ancestor WITHOUT
    # one is the natural Python "import root" for an absolute dotted import (e.g. `pkg.helpers`
    # written inside `pkg/main.py` resolves relative to pkg's PARENT, not pkg itself). This
    # covers the common case where no project-root marker file exists at all.
    package_top = importer_path.parent
    while (package_top / "__init__.py").exists():
        parent = package_top.parent
        if parent == package_top:
            break
        package_top = parent
    _add(package_top)
    return roots


def _python_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> dict[str, Any]:
    parts = _python_module_parts(module_name)
    if not parts:
        return {"paths": [], "provenance": [], "confidence": 0.0, "path_provenance": {}}

    # opt10 F4.3 speed fast-path: skip the multi-root candidate-path construction below (2
    # `Path` builds per root, each pushed through the `_resolved_path_str` resolve-and-dedupe
    # machinery -- ~10-12 `Path.resolve()` calls for a typical root count, PLUS the caller's own
    # `.is_file()` probe of every returned candidate) for a bare top-level stdlib import
    # (`import os` / `import sys` / `import json`) -- the dominant import shape, 59-100% of
    # imports in sampled real files per the opt10 speed campaign.
    #
    # SHADOW-SAFETY (the whole correctness risk of this fast-path): `parts[0] in
    # sys.stdlib_module_names` alone is NOT sufficient -- a repo can ship a same-named top-level
    # module (e.g. a local `json.py` at its root) that MUST still resolve to that local file, the
    # same way it would via the general path below (see
    # test_build_file_imports_stdlib_shadowed_by_local_module_resolves_to_local_file). So this
    # only returns the fast-path shape after confirming NEITHER shape the general path's
    # level==0 branch would also probe (`<root>/<name>.py`, `<root>/<name>/__init__.py`) exists
    # as a real file at ANY of `_python_candidate_roots`' roots -- the exact same roots (repo
    # root, src/ layout, sys-path-hacked dirs, importer's own dir and ancestors, package-top)
    # the general path already computes, just probed with a cheap `.is_file()`/`.is_dir()` stat
    # instead of building+resolving+deduping the full candidate list. Any doubt (an `OSError`
    # probing a candidate, or `parts[0]` existing locally at all) falls CLOSED to the unchanged
    # general path below, never guesses.
    #
    # Narrowed to `len(parts) == 1` (a bare `import json`, not a dotted `import os.path`):
    # a dotted stdlib access still needs `root/parts[0]` to be an existing local DIRECTORY for
    # any local shadow to be possible at all, so the `is_dir()` probe below already catches that
    # case too and correctly falls through -- but the deeper submodule candidates the general
    # path would build (`root/parts[0]/parts[1]/...`) are not worth fast-pathing separately here,
    # so leave every dotted access on the general path unconditionally.
    #
    # Returns EXACTLY the shape the general (non-relative) branch below always sets for
    # `provenance`/`confidence` -- unconditionally, before any candidate is even probed for
    # existence -- so `_resolve_raw_import_entry` / `_python_module_match_details` read the
    # identical values off this dict as they would off the general path's result for a module
    # that genuinely has zero real candidates (see the opt10 PR body's captured baseline: an
    # empty `paths: []` here is observationally identical to the general path's non-empty-but-
    # entirely-nonexistent candidate list -- both make `resolved`/`matched` come out the same on
    # the calling side, since neither contains a real file).
    if level == 0 and len(parts) == 1 and parts[0] in sys.stdlib_module_names:
        name = parts[0]
        shadowed = False
        for root in _python_candidate_roots(importer_path, repo_root):
            try:
                if (root / f"{name}.py").is_file() or (root / name).is_dir():
                    shadowed = True
                    break
            except OSError:
                shadowed = True  # can't prove no local shadow -- fail closed to the slow path
                break
        if not shadowed:
            return {
                "paths": [],
                "provenance": ["python-path-heuristic"],
                "confidence": 0.7,
                "path_provenance": {},
            }

    candidates: list[Path] = []
    # #152 fix: per-candidate provenance override, keyed by the candidate's OWN resolved path
    # string -- lets a candidate reached ONLY via a sys.path-hacked root report its specific
    # "sys-path-insert" provenance instead of the generic "python-path-heuristic" every other
    # absolute-import candidate gets, without changing `provenance`'s existing list-of-str shape.
    path_provenance: dict[str, str] = {}
    if level > 0:
        base_dir = _python_relative_base_dir(importer_path, level)
        target = base_dir.joinpath(*parts)
        candidates.append(target.with_suffix(".py"))
        candidates.append(target / "__init__.py")
        provenance = ["relative"]
        confidence = 1.0
    else:
        hacked_roots = {
            str(current) for current in _python_sys_path_hack_roots(importer_path, repo_root)
        }
        for root in _python_candidate_roots(importer_path, repo_root):
            module_file = root.joinpath(*parts).with_suffix(".py")
            package_init = root.joinpath(*parts, "__init__.py")
            candidates.append(module_file)
            candidates.append(package_init)
            if str(root) in hacked_roots:
                for hacked_candidate in (module_file, package_init):
                    try:
                        path_provenance[_resolved_path_str(str(hacked_candidate))] = (
                            "sys-path-insert"
                        )
                    except OSError:
                        continue
        provenance = ["python-path-heuristic"]
        confidence = 0.7

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = _resolved_path_str(str(candidate))
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(Path(key))
    return {
        "paths": deduped,
        "provenance": provenance,
        "confidence": confidence,
        "path_provenance": path_provenance,
    }


def _python_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> dict[str, Any]:
    """Resolve-then-compare Python reverse-import confirm.

    Mirrors `_js_ts_module_match_details` / `_rust_module_match_details`: reuses the SAME
    precise resolver the forward `tg imports` uses (`_python_module_candidates`) instead of a
    bare path-SUFFIX match, so two files sharing a basename (`app/config.py` vs
    `tools/config.py`) no longer produce a phantom reverse edge just because an importer's
    `import config` textually ends with "config" (#74 review fix -- see
    `_module_path_matches_definition`, which is exactly that suffix match and is what this
    function replaces for the Python confirm step).

    Deliberately has NO suffix-match fallback (unlike JS/TS's bare-specifier partial-resolution
    or Rust's non-workspace-crate partial-resolution) -- that fallback IS the bug this closes,
    so it must not be reintroduced here.
    """
    candidate_info = _python_module_candidates(importer_path, module_name, repo_root, level=level)
    resolved_definition = _resolved_path_str(definition_path)
    if any(str(candidate) == resolved_definition for candidate in candidate_info["paths"]):
        provenance = list(candidate_info["provenance"])
        tagged_provenance = candidate_info.get("path_provenance", {}).get(resolved_definition)
        if tagged_provenance is not None:
            provenance = [tagged_provenance]
        return {
            "matched": True,
            "provenance": provenance,
            "confidence": float(candidate_info["confidence"] or 1.0),
        }
    return {"matched": False, "provenance": [], "confidence": 0.0}


def _python_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
) -> tuple[bool, list[str]]:
    """Return `(matched, provenance)`.

    Unlike the bool-only `_js_ts_module_matches_definition` / `_rust_module_matches_definition`
    siblings, this also threads through `_python_module_match_details`'s `provenance` (notably
    the "sys-path-insert" tag) -- #155 fix: that tag was computed but provably unreachable
    (this was the only caller, and it discarded everything but the bool) before this change.
    The sole caller, `_confirm_import_edges`, uses it to report the tag honestly on `tg
    importers` reverse edges instead of silently collapsing it into a generic label.
    """
    details = _python_module_match_details(
        importer_path, module_name, definition_path, repo_root, level=level
    )
    return bool(details["matched"]), list(details["provenance"])


def _python_ast_omitted_relative_lines(
    block: str, profile: str = "compact", strip_docstrings: bool = True
) -> tuple[set[int], set[int]]:
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return set(), set()

    docstring_lines: set[int] = set()
    boilerplate_lines: set[int] = set()

    def _walk_and_strip(nodes: list[ast.stmt], parent: ast.AST | None = None) -> None:
        if not nodes:
            return

        # Check if the first node in this body is a docstring
        first = nodes[0]
        first_value = getattr(first, "value", None)
        is_docstring = (
            isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and isinstance(first, ast.Expr)
            and isinstance(first_value, ast.Constant)
            and isinstance(first_value.value, str)
        )

        if is_docstring and (profile == "compact" or strip_docstrings):
            end_lineno = getattr(first, "end_lineno", first.lineno)
            docstring_lines.update(range(first.lineno, end_lineno + 1))

        if profile in {"compact", "llm"}:
            # Strip 'pass' if it's the only node or only node after docstring
            if len(nodes) == 1 or (len(nodes) == 2 and is_docstring):
                last = nodes[-1]
                if isinstance(last, ast.Pass):
                    end_lineno = getattr(last, "end_lineno", last.lineno)
                    boilerplate_lines.update(range(last.lineno, end_lineno + 1))

        # Recurse into all nodes to find nested functions/classes
        for node in nodes:
            node_body = getattr(node, "body", None)
            if node_body and isinstance(node_body, list):
                _walk_and_strip(node_body, parent=node)

    _walk_and_strip(tree.body)
    return docstring_lines, boilerplate_lines


def _python_decorator_qualname(node: ast.AST) -> str | None:
    current = node
    if isinstance(current, ast.Call):
        current = current.func
    if isinstance(current, ast.Name):
        return current.id
    if isinstance(current, ast.Attribute):
        parent = _python_decorator_qualname(current.value)
        return f"{parent}.{current.attr}" if parent else current.attr
    return None


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _python_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = _self._cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            candidates.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test"):
                    candidates.append(member.name)
    return tuple(dict.fromkeys(candidates))


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _python_parametrized_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = _self._cached_ast_parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []

    def visit_body(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                decorator_names = {
                    name
                    for decorator in node.decorator_list
                    if (name := _python_decorator_qualname(decorator))
                }
                if {
                    "pytest.mark.parametrize",
                    "mark.parametrize",
                } & decorator_names:
                    candidates.append(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit_body(list(node.body))

    visit_body(list(tree.body))
    return tuple(dict.fromkeys(candidates))
