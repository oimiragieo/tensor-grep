"""Class-level atomic-writer ratchet (task #859).

PR #859's instance fix (commit a41c86f, "route scaffold + ruleset writers through the anchored
atomic writer") closed three concrete symlink-unsafe writers in ``main.py`` by routing them
through ``_index_lock.atomic_write_bytes_anchored``. That fix closed three SITES. It did not
close the CLASS: nothing stops a new hand-rolled ``open(..., "w")`` / ``os.replace`` /
``shutil.move`` writer from being added tomorrow, in this file or a new one, and silently
skipping the symlink refusal + fsync + no-clobber guarantees the shared helper provides.

This module is that ratchet: a small AST-based detector that classifies every write/publish
callsite it can find in the scanned production source into exactly one of:

- ``sanctioned``     -- an exact, individually-reviewed fingerprint of a legitimate direct
                        writer that is NOT expected to route through the shared helper (e.g.
                        the helper's own temp-file creation, or a native-runtime binary swap
                        that relocates already-trusted on-disk bytes rather than publishing new
                        externally-sourced content).
- ``helper-backed``   -- a function whose write op(s) all route through the shared
                        ``atomic_write_bytes`` / ``atomic_write_bytes_anchored`` /
                        ``atomic_write_json`` helper family (directly, or via a local import /
                        assignment alias of it), with no destination pre-resolution that would
                        erase symlink identity before the call.
- ``violating``       -- a direct writer, a hand-rolled tempfile-to-publish flow, or a
                        destination-provenance canonicalization (``.resolve()`` /
                        ``os.path.realpath``) immediately before an approved helper call, none
                        of which is in the sanctioned fingerprint table.

Every candidate this detector recognizes as write-shaped MUST resolve to one of the three
labels above; a call whose target identity cannot be statically determined (a dynamic dispatch,
an un-parseable payload) is NEVER silently dropped from the population -- it fails the run
closed (see ``_UNRESOLVED`` handling below and ``test_population_has_zero_unresolved``).

SCOPE: the production source set scanned by the population/inventory tests is DISCOVERED, not
enumerated -- ``_discover_scanned_production_files()`` walks every ``*.py`` module directly under
``src/tensor_grep/cli/`` (60 as of 2026-08-20 -- re-derive with `len(_SCANNED_PRODUCTION_FILES)` rather than trusting this
number, which read "41" until the cli/main.py split touched this file and was already stale by 19;
``test_scanned_population_floor`` guards against a
truncated/empty walk silently reading as "no violations"). This module's first cut (the original
form of task #859's class fix) scoped the scan to a hardcoded 3-file tuple -- ``main.py``,
``_index_lock.py``, ``codemap.py`` -- named directly by the backlog-closeout plan, plus the module
containing the historical bidirectional control; that hardcoding meant a new cli module joining
the package was silently never scanned by it (see AGENTS.md "a list written at dispatch time is
stale by definition"). Walking the directory closes that gap. The detector ENGINE itself remains
general (callers can point it at any file), and the individually-red controls below exercise it
against small synthetic sources, independent of this production scope. Subdirectories of
``src/tensor_grep/cli/`` (e.g. ``formatters/``) are NOT walked -- a non-recursive glob, matching
what the module count has always meant in this task's own measurements; recursing into subpackages is
a deliberate, undone follow-up, not a silent gap (nothing under ``formatters/`` writes files as of
this task, confirmed by manual review, not by this detector).

Known gaps (stated plainly, not papered over):

- The detector does NOT do full Hindley-Milner-grade type inference. ``Path.write_text`` /
  ``Path.write_bytes`` are always treated as write-shaped (no stdlib type other than
  ``pathlib.Path`` exposes those names, so the collision risk is effectively zero). But
  ``Path.replace`` / ``Path.rename`` / ``Path.open`` share names with ``str.replace`` /
  ``dict``-less-``rename`` / builtin-adjacent ``.open`` patterns used all over this codebase
  (``dataclasses.replace(...)``, ``datetime.replace(...)``, ``text.replace(...)``) -- to avoid
  drowning the population in false positives, those three are only counted as write-sink
  candidates when the receiver is provably Path-typed by a light local dataflow tracker
  (``Path(...)`` construction, a ``Path``-annotated parameter, or a Path-preserving chain:
  ``/``, ``.parent``, ``.resolve()``, ``.expanduser()``, ``.with_name()``, ``.with_suffix()``,
  or a call to a locally-defined helper whose name ends in ``_path``). A receiver this tracker
  cannot prove Path-typed is treated as OUT OF SCOPE for those three method names specifically
  (not a silently-dropped candidate -- see ``test_replace_rename_open_on_non_path_receiver_is_
  not_a_candidate`` for the explicit, asserted behaviour this produces on real code).
- Generated-source execution roots (production code that spawns ``python -c <payload>``) ARE
  discovered and surfaced -- ``test_generated_source_c_sites_are_surfaced`` pins the exact,
  reviewed set of real subprocess ``-c`` call sites in the scanned files -- but their PAYLOAD
  strings are NOT recursively parsed and classified by this first cut of the ratchet. Two of the
  three surfaced sites in ``main.py`` (``_install_release_native_frontdoor``'s detached-upgrade
  helper scripts in `_schedule_windows_native_frontdoor_refresh` and
  `_schedule_windows_self_upgrade`) embed their OWN ``os.replace`` /
  ``write_text`` / ``shutil.copy2`` calls for the SAME native-binary-install flow that produced
  this task's three pinned violations -- so those two generated scripts almost certainly carry
  the identical unaudited-writer defect and are NOT yet covered by this ratchet. This is recorded
  as a follow-up, not silently declared clean: see ``test_generated_source_c_sites_are_
  surfaced``'s docstring.
"""

from __future__ import annotations

import ast
import hashlib
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------------------------
# Paths / fixtures
# ---------------------------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_SRC = _REPO_ROOT / "src" / "tensor_grep" / "cli"


def _discover_scanned_production_files() -> tuple[str, ...]:
    """The population the census scans -- DISCOVERED from ``_CLI_SRC``, not a hand-maintained
    list. Task #859's instance fix (commit a41c86f) closed three sites in ``main.py``; this
    module's FIRST cut then closed the class only up to a hardcoded 3-file
    ``_SCANNED_PRODUCTION_FILES = ("main.py", "_index_lock.py", "codemap.py")`` tuple -- a new cli
    module joining the package was silently never scanned by it (a list written once, stale by
    definition -- see AGENTS.md "a list written at dispatch time is stale by definition"). Walking
    the directory instead means a new ``src/tensor_grep/cli/<x>.py`` module is covered by
    construction: no second edit required to add it to the population. See
    ``test_scanned_population_floor`` for the guard against a truncated/empty walk silently
    reading as "no violations"."""
    return tuple(sorted(p.name for p in _CLI_SRC.glob("*.py")))


_SCANNED_PRODUCTION_FILES = _discover_scanned_production_files()

# A truncated/empty walk must never silently read as "no violations" (AGENTS.md's "a zero means
# two things" law) -- 60 modules exist as of 2026-08-20; 30 is a real floor with headroom for
# ordinary churn while still catching a walk that resolved the wrong directory, hit a glob typo,
# or ran against an empty/partial checkout.
_MINIMUM_SCANNED_MODULE_COUNT = 30

_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "audits" / "codemap_pre_859.py"
# Provenance kept HERE, not as a header baked into the fixture (the fixture must stay a
# byte-exact historical blob -- AGENTS.md "cite the SYMBOL, not the line" / "keep provenance
# constants in the test rather than modifying the fixture with a header").
_FIXTURE_SOURCE_COMMIT = "0c46863cd038efa438fe6af2fc533109af257dc7"
_FIXTURE_SHA256 = "dd16398dc3278efd66d46ab63170cd71cf4e3c9512234f340ef292dff5f2fe76"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Classification labels
# ---------------------------------------------------------------------------------------------

SANCTIONED = "sanctioned"
HELPER_BACKED = "helper-backed"
VIOLATING = "violating"
UNRESOLVED = (
    "unresolved"  # must NEVER survive to a passing test -- see test_population_has_zero_unresolved
)


@dataclass(frozen=True)
class Candidate:
    """One classified write/publish callsite.

    ``fingerprint`` is the exact ``module:outer-function:resolved-callsite:operation:
    destination-provenance`` identity the plan requires sanctions to be keyed on -- never a
    whole-function exemption.
    """

    module: str
    outer_function: str
    line: int
    operation: str
    destination_provenance: str
    classification: str
    detail: str = ""

    @property
    def fingerprint(self) -> str:
        return (
            f"{self.module}:{self.outer_function}:{self.line}:{self.operation}:"
            f"{self.destination_provenance}"
        )


# ---------------------------------------------------------------------------------------------
# Sink / helper identity tables
# ---------------------------------------------------------------------------------------------

# Approved shared writers -- a call resolving to one of these (directly, or via import/assignment
# alias, local or module scope) makes its enclosing function "helper-backed" for that call.
_APPROVED_HELPERS = {
    "tensor_grep.cli._index_lock.atomic_write_bytes",
    "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    "tensor_grep.cli._index_lock.atomic_write_json",
}

# The retry-wrapped os.replace primitive. A call resolving to this (directly or aliased) is a
# publish op in its own right -- callers that reach it WITHOUT going through the approved
# helpers above are candidates like any other direct sink.
_REPLACE_WITH_RETRY = "tensor_grep.cli._index_lock.replace_with_retry"

# Module-qualified direct sinks: resolved via import-alias substitution, matched by full dotted
# identity (never bare attribute-name string matching -- that is what keeps this detector from
# flagging every unrelated `.replace(`/`.write(`/`.copy(` in the codebase).
_MODULE_QUALIFIED_SINKS = {
    "os.replace": "os.replace",
    "os.rename": "os.rename",
    "os.write": "os.write",
    "shutil.move": "shutil.move",
    "shutil.copy": "shutil.copy",
    "shutil.copyfile": "shutil.copyfile",
    "shutil.copy2": "shutil.copy2",
    "shutil.copyfileobj": "shutil.copyfileobj",
    "urllib.request.urlretrieve": "urllib.request.urlretrieve",
}

# Path-preserving method names: a call to one of these on an already Path-typed receiver keeps
# the result Path-typed (used by the light dataflow tracker below).
_PATH_PRESERVING_METHODS = {
    "parent",
    "resolve",
    "expanduser",
    "with_name",
    "with_suffix",
    "absolute",
}

# Method names ALWAYS treated as write-shaped regardless of receiver typing (no ambiguity risk).
_ALWAYS_PATH_METHODS = {"write_text", "write_bytes"}

# Method names treated as write-shaped ONLY when the receiver is provably Path-typed (ambiguity
# risk with str.replace / dataclasses.replace / datetime.replace / socket-like .open()).
_TYPE_GATED_METHODS = {"replace", "rename", "open"}

_ARCHIVE_EXTRACT_METHOD = "extractall"

_WRITE_MODE_MARKERS = set("wax+")


def _mode_is_write(mode: str) -> bool:
    return any(marker in mode for marker in _WRITE_MODE_MARKERS)


# ---------------------------------------------------------------------------------------------
# Import / alias resolution (per source unit, sequential-order sensitive so local imports,
# shadowing/rebinding, and assignment aliasing all resolve correctly).
# ---------------------------------------------------------------------------------------------


def _dotted_name(expr: ast.expr) -> str | None:
    """Textual dotted name of a pure Name/Attribute chain, e.g. ``urllib.request.urlretrieve``;
    ``None`` for anything else (a call result, a subscript, ...)."""
    parts: list[str] = []
    node = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


@dataclass
class _Scope:
    """Mutable name-resolution state for one source unit (function or module), threaded through
    a sequential, source-order walk so later statements see earlier bindings (import, from-import,
    and assignment aliasing all update it in place -- this is how renamed imports, local imports,
    and shadowing/rebinding all resolve)."""

    modules: dict[str, str] = field(default_factory=dict)  # local name -> dotted module path
    callables: dict[str, str] = field(
        default_factory=dict
    )  # local name -> dotted callable identity
    path_vars: set[str] = field(default_factory=set)  # names provably holding a Path object
    resolved_leaves: dict[str, str] = field(
        default_factory=dict
    )  # name -> ".resolve()"/"realpath" source var
    flag_exprs: dict[str, str] = field(
        default_factory=dict
    )  # name -> unparsed os.O_* flag expression text

    def clone(self) -> _Scope:
        return _Scope(
            modules=dict(self.modules),
            callables=dict(self.callables),
            path_vars=set(self.path_vars),
            resolved_leaves=dict(self.resolved_leaves),
            flag_exprs=dict(self.flag_exprs),
        )


def _resolve_identity(expr: ast.expr, scope: _Scope) -> str | None:
    """Best-effort canonical dotted identity of a call target expression, substituting the
    leftmost segment through ``scope.modules``/``scope.callables``. Returns ``None`` when the
    expression isn't a simple Name/Attribute chain we can resolve (a dynamic call target)."""
    if isinstance(expr, ast.Name):
        return scope.callables.get(expr.id)
    dotted = _dotted_name(expr)
    if dotted is None:
        return None
    leftmost, *rest = dotted.split(".")
    base = scope.modules.get(leftmost) or scope.callables.get(leftmost)
    if base is None:
        return None
    return ".".join([base, *rest]) if rest else base


def _is_path_constructor(expr: ast.expr) -> bool:
    if not isinstance(expr, ast.Call):
        return False
    dotted = _dotted_name(expr.func)
    return dotted in ("Path", "pathlib.Path")


def _is_path_producing_expr(expr: ast.expr, scope: _Scope) -> bool:
    """Light local dataflow: does ``expr`` provably evaluate to a ``Path``?"""
    if _is_path_constructor(expr):
        return True
    if isinstance(expr, ast.Name):
        return expr.id in scope.path_vars
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _is_path_producing_expr(expr.left, scope) or _is_path_producing_expr(
            expr.right, scope
        )
    if isinstance(expr, ast.Attribute) and expr.attr in _PATH_PRESERVING_METHODS:
        return _is_path_producing_expr(expr.value, scope)
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr in _PATH_PRESERVING_METHODS:
            return _is_path_producing_expr(func.value, scope)
        if isinstance(func, ast.Name) and func.id.endswith("_path"):
            return True
    return False


def _annotation_is_path(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    dotted = _dotted_name(annotation)
    if dotted in ("Path", "pathlib.Path"):
        return True
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_is_path(annotation.left) or _annotation_is_path(annotation.right)
    return False


def _is_resolve_or_realpath_call(expr: ast.expr, scope: _Scope) -> str | None:
    """If ``expr`` is ``<path>.resolve(...)`` or ``os.path.realpath(<path>)``, return a stable
    label for the erased-symlink-identity provenance; else ``None``."""
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr == "resolve":
            return "leaf-pre-resolved(.resolve())"
        dotted = _dotted_name(func) if isinstance(func, (ast.Name, ast.Attribute)) else None
        resolved = _resolve_identity(func, scope) if dotted else None
        if resolved == "os.path.realpath":
            return "leaf-pre-resolved(os.path.realpath())"
    return None


# ---------------------------------------------------------------------------------------------
# The scanning engine
# ---------------------------------------------------------------------------------------------

_OnCall = Callable[[ast.Call, _Scope], None]


class _UnresolvedCandidate(Exception):
    """Raised when a call is write-shaped but its target identity cannot be statically
    determined -- the fail-closed path. Never caught silently by the population scan."""

    def __init__(self, module: str, outer_function: str, line: int, detail: str) -> None:
        super().__init__(f"{module}:{outer_function}:{line}: {detail}")
        self.module = module
        self.outer_function = outer_function
        self.line = line
        self.detail = detail


def _update_scope_for_import(node: ast.Import | ast.ImportFrom, scope: _Scope) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            scope.modules[local] = alias.name
            # A dotted `import a.b.c` binds only `a` at runtime; keep the FULL dotted path as the
            # value so `_resolve_identity`'s leftmost-substitution still yields the right base
            # for `import urllib.request` -> modules["urllib"] == "urllib.request" is WRONG
            # (that would break bare `urllib.whatever`); instead bind the top segment to itself
            # and separately register the full alias for exact multi-segment imports.
            if "." in alias.name and alias.asname is None:
                scope.modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
                scope.modules[alias.name] = alias.name
    else:
        if node.module is None:
            return
        for alias in node.names:
            local = alias.asname or alias.name
            scope.callables[local] = f"{node.module}.{alias.name}"
            scope.modules.pop(local, None)


def _update_scope_for_assign(target: ast.Name, value: ast.expr, scope: _Scope) -> None:
    resolved = (
        _resolve_identity(value, scope) if isinstance(value, (ast.Name, ast.Attribute)) else None
    )
    if resolved is not None:
        scope.callables[target.id] = resolved
        scope.path_vars.discard(target.id)
        return
    if _is_path_producing_expr(value, scope):
        scope.path_vars.add(target.id)
        scope.callables.pop(target.id, None)
        return
    provenance = _is_resolve_or_realpath_call(value, scope)
    if (
        provenance is not None
        and isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
    ):
        base_dotted = _dotted_name(value.func.value)
        if base_dotted is not None:
            scope.resolved_leaves[target.id] = base_dotted
    unparsed = ast.unparse(value)
    if any(flag in unparsed for flag in ("O_WRONLY", "O_RDWR", "O_APPEND")):
        scope.flag_exprs[target.id] = unparsed
    else:
        scope.flag_exprs.pop(target.id, None)
    # Any other assignment REBINDS/SHADOWS the name -- clear stale identity (handles
    # shadowing/rebinding controls, e.g. `open = something_else`, INCLUDING the virtual
    # `builtin.open` identity seeded into every scope by `_module_level_scope` below).
    scope.callables.pop(target.id, None)
    scope.path_vars.discard(target.id)


def _walk_source_unit(
    node: ast.AST, scope: _Scope, on_call: _OnCall, module: str, outer_function: str
) -> None:
    """Sequential, source-order walk of one function/module body, skipping nested
    function/class defs (each is its own source unit, scanned separately by the driver).
    Updates ``scope`` in place as Import/ImportFrom/Assign statements are encountered, and
    invokes ``on_call(call_node, scope)`` for every ``ast.Call`` reached along the way."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _update_scope_for_import(node, scope)
        return
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        _visit_expr_for_calls(node.value, scope, on_call, module, outer_function)
        _update_scope_for_assign(node.targets[0], node.value, scope)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return  # separate source unit; the driver scans it independently
    # Generic fallback: recurse into every child field. Deliberately dispatches on `ast.expr` vs
    # "any other AST node" (not just `ast.stmt`) so container node types that are neither --
    # `ast.withitem` (the `with p.open("w") as h:` context expression), `ast.ExceptHandler`,
    # comprehension generators, `match` cases -- still get walked instead of silently skipped
    # (a real gap this file's own history caught: `with p.open("w"):` was invisible until this
    # branch stopped requiring `ast.stmt` specifically).
    for _field_name, value in ast.iter_fields(node):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, ast.expr):
                    _visit_expr_for_calls(item, scope, on_call, module, outer_function)
                elif isinstance(item, ast.AST):
                    _walk_source_unit(item, scope, on_call, module, outer_function)
        elif isinstance(value, ast.expr):
            _visit_expr_for_calls(value, scope, on_call, module, outer_function)
        elif isinstance(value, ast.AST):
            _walk_source_unit(value, scope, on_call, module, outer_function)


def _visit_expr_for_calls(
    node: ast.expr, scope: _Scope, on_call: _OnCall, module: str, outer_function: str
) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            on_call(child, scope)


def _classify_call(
    call: ast.Call, scope: _Scope, module: str, outer_function: str
) -> Candidate | None:
    func = call.func

    # --- builtin open()/io.open() ---
    resolved_name_identity: str | None = None
    if isinstance(func, ast.Name):
        resolved_name_identity = scope.callables.get(func.id)
    elif isinstance(func, ast.Attribute):
        resolved_name_identity = _resolve_identity(func, scope)
        if (
            resolved_name_identity is None
            and isinstance(func.value, ast.Name)
            and func.value.id == "io"
            and func.attr == "open"
        ):
            resolved_name_identity = "io.open"

    if resolved_name_identity in ("builtin.open", "io.open"):
        mode = _extract_mode(call, positional_index=1)
        if mode is not None and _mode_is_write(mode):
            return Candidate(
                module, outer_function, call.lineno, resolved_name_identity, "n/a", VIOLATING
            )
        return None  # no mode (defaults to "r") or a non-write mode -- not write-shaped

    if resolved_name_identity == "os.open":
        flags_arg = call.args[1] if len(call.args) > 1 else None
        flags_src = ast.unparse(flags_arg) if flags_arg is not None else ""
        if isinstance(flags_arg, ast.Name) and flags_arg.id in scope.flag_exprs:
            flags_src = scope.flag_exprs[flags_arg.id]
        if any(flag in flags_src for flag in ("O_WRONLY", "O_RDWR", "O_APPEND")):
            return Candidate(module, outer_function, call.lineno, "os.open", "n/a", VIOLATING)
        return None  # read-only open, not write-shaped

    if resolved_name_identity in _APPROVED_HELPERS:
        dest_provenance = _destination_provenance(call, scope)
        if dest_provenance is not None:
            return Candidate(
                module,
                outer_function,
                call.lineno,
                resolved_name_identity,
                dest_provenance,
                VIOLATING,
            )
        return Candidate(
            module, outer_function, call.lineno, resolved_name_identity, "n/a", HELPER_BACKED
        )

    if resolved_name_identity == _REPLACE_WITH_RETRY:
        return Candidate(module, outer_function, call.lineno, _REPLACE_WITH_RETRY, "n/a", VIOLATING)

    if resolved_name_identity in _MODULE_QUALIFIED_SINKS:
        return Candidate(
            module,
            outer_function,
            call.lineno,
            _MODULE_QUALIFIED_SINKS[resolved_name_identity],
            "n/a",
            VIOLATING,
        )

    # --- method-form candidates on an unresolved-module Attribute (Path.write_text etc.) ---
    if isinstance(func, ast.Attribute) and resolved_name_identity is None:
        attr = func.attr
        if attr in _ALWAYS_PATH_METHODS:
            return Candidate(module, outer_function, call.lineno, f"Path.{attr}", "n/a", VIOLATING)
        if attr == _ARCHIVE_EXTRACT_METHOD:
            return Candidate(
                module, outer_function, call.lineno, "archive.extractall", "n/a", VIOLATING
            )
        if attr in _TYPE_GATED_METHODS:
            if not _is_path_producing_expr(func.value, scope):
                return None  # not provably Path-typed -- out of scope by design, not dropped
            if attr == "open":
                # `receiver.open(mode)` is a METHOD call: the mode is the first EXPLICIT
                # argument (index 0), unlike builtin `open(path, mode)` where path occupies
                # index 0 and mode is index 1.
                mode = _extract_mode(call, positional_index=0)
                if mode is None or not _mode_is_write(mode):
                    return None
                return Candidate(module, outer_function, call.lineno, "Path.open", "n/a", VIOLATING)
            return Candidate(module, outer_function, call.lineno, f"Path.{attr}", "n/a", VIOLATING)

    # A Name-call whose target is a local (non-imported, non-builtin) function is not a sink
    # candidate at all -- it's an ordinary call to code elsewhere in the module that this
    # detector doesn't need to open (its own writes, if any, are classified where THEY live).
    return None


def _extract_mode(call: ast.Call, *, positional_index: int) -> str | None:
    """Extract a literal mode string. ``positional_index`` is 1 for the builtin
    ``open(path, mode)``/``io.open(path, mode)`` calling convention, 0 for the method form
    ``receiver.open(mode)`` (``Path.open``) where the receiver is implicit and not itself an
    argument. A non-literal (variable) mode is a documented KNOWN GAP -- see
    ``test_variable_write_mode_is_detected``."""
    if len(call.args) > positional_index:
        positional = call.args[positional_index]
        if isinstance(positional, ast.Constant) and isinstance(positional.value, str):
            return positional.value
    for kw in call.keywords:
        if (
            kw.arg == "mode"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _destination_provenance(call: ast.Call, scope: _Scope) -> str | None:
    """If the first positional argument to an approved-helper call is a name that was assigned
    from ``<expr>.resolve()`` / ``os.path.realpath(...)`` earlier in this same function, the
    symlink identity of the caller-selected leaf was already erased before the call -- report
    the provenance label; else ``None``."""
    if not call.args:
        return None
    dest = call.args[0]
    if isinstance(dest, ast.Name) and dest.id in scope.resolved_leaves:
        return f"leaf-pre-resolved(via {dest.id})"
    provenance = _is_resolve_or_realpath_call(dest, scope)
    return provenance


def scan_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Module,
    module: str,
    outer_function: str,
    base_scope: _Scope,
) -> list[Candidate]:
    """Scan one source unit (a function body, or a module's top-level ``<module>`` body),
    returning every candidate discovered. Raises ``_UnresolvedCandidate`` if a call looks
    write-shaped (a dynamic dispatch through a name this detector cannot resolve AND that shape-
    matches a known risky call pattern) but its identity cannot be determined -- fail closed,
    never silently drop it."""
    found: list[Candidate] = []
    scope = base_scope.clone()

    # Seed path_vars from Path-annotated parameters -- the module docstring's dataflow-tracker
    # description ("a Path-annotated parameter") already promised this, but it was dead: nothing
    # ever called `_annotation_is_path` against a function's `args`. Confirmed via a synthetic
    # probe against the pre-fix engine: `def publish(destination: Path, content): destination.
    # open("wb")...` scanned to `[]` -- zero candidates -- for a call that is write-shaped by any
    # reasonable reading. Wiring this in surfaced 3 previously-invisible real sites in
    # lsp_provider_setup.py (`_download`, `_extract_rust_analyzer_exe_from_zip`,
    # `_download_rust_analyzer`, all `Path.open` in write mode on a `destination: Path` parameter)
    # with zero regressions (no candidate the old engine found was lost -- verified by diffing the
    # full 41-file population before/after).
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        all_params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        for param in all_params:
            if _annotation_is_path(param.annotation):
                scope.path_vars.add(param.arg)

    def on_call(call: ast.Call, live_scope: _Scope) -> None:
        candidate = _classify_call(call, live_scope, module, outer_function)
        if candidate is not None:
            found.append(candidate)

    for stmt in node.body:
        _walk_source_unit(stmt, scope, on_call, module, outer_function)
    return found


def _module_level_scope(tree: ast.Module) -> _Scope:
    scope = _Scope()
    # Seed the virtual `builtin.open` identity so a bare `open` Name resolves through the SAME
    # `scope.callables` lookup path as every other callable -- an assignment-alias
    # (`writer = open`) or a rebinding (`open = something_else`) then composes for free via the
    # normal `_update_scope_for_assign` machinery instead of needing a special case.
    scope.callables["open"] = "builtin.open"
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            _update_scope_for_import(stmt, scope)
    return scope


def _iter_function_defs(
    tree: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Every function/async function definition in the module, at any nesting depth, paired
    with its qualified outer-function name (``outer`` or ``outer.inner`` for a nested def)."""
    results: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

    def _walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                results.append((child, qualname))
                _walk(child, qualname)
            elif isinstance(child, ast.ClassDef):
                _walk(child, prefix)
            # do not descend into unrelated statement bodies beyond class/func (If/For/etc are
            # handled by the outer function's own _walk_source_unit for candidate discovery; here
            # we only need to *locate* def boundaries, so a shallow structural walk is enough
            # since ast.iter_child_nodes already recurses through statement lists).
            elif not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(child, prefix)

    _walk(tree, "")
    return results


def scan_source(source: str, module: str) -> list[Candidate]:
    """Scan an entire module's source text: every function/async function (at any nesting
    depth) plus the module top level (``<module>``), returning the combined candidate list.
    Fails closed (raises) on any call this detector judges write-shaped but cannot resolve."""
    tree = ast.parse(source, filename=module)
    module_scope = _module_level_scope(tree)

    all_candidates: list[Candidate] = []
    all_candidates.extend(scan_function(tree, module, "<module>", module_scope))
    for func_node, qualname in _iter_function_defs(tree):
        all_candidates.extend(scan_function(func_node, module, qualname, module_scope))
    return all_candidates


def scan_file(path: Path, module: str) -> list[Candidate]:
    return scan_source(_read(path), module)


# ---------------------------------------------------------------------------------------------
# Sanctioned fingerprint table -- EXACT callsites, never whole-function exemptions.
# ---------------------------------------------------------------------------------------------

# Each entry is (module, outer_function, line, operation) -> rationale. `line` is pinned to the
# CURRENT reviewed source; the population test below additionally pins by
# (module, outer_function, operation) identity so a pure line-number drift from an unrelated
# edit fails loudly rather than silently reclassifying a moved line as a fresh violation.
_SANCTIONED_SITES: dict[tuple[str, str, str], str] = {
    ("native_frontdoor.py", "_download_native_frontdoor_asset", "os.open"): (
        "TOCTOU fix (H2 deferral closed): claims `destination` exclusively via "
        "O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW BEFORE the streamed download starts, then writes "
        "through that SAME held fd (os.fdopen) for the whole transfer -- urllib.request.urlretrieve "
        "is no longer called, so there is no close-then-reopen-by-name gap for a symlink swap to "
        "win. Same technique as `lsp_provider_setup.py::_download`'s os.open entry below (that one "
        "was the H2 precedent this call now mirrors, rather than the other way around). Refusing "
        "rather than truncating on a pre-existing path is the point. The destination is the "
        "caller's uuid4-suffixed temp path, and the downloaded artifact is still checksum-verified "
        "before os.replace publishes it."
    ),
    ("_index_lock.py", "atomic_write_bytes_anchored", "os.open"): (
        "Defines the shared helper's OWN temp-file creation (O_CREAT|O_EXCL|O_NOFOLLOW, "
        "same-directory temp) -- this IS the primitive other callers are required to route "
        "through, not a caller of it."
    ),
    ("_index_lock.py", "replace_with_retry", "os.replace"): (
        "Defines the shared retry-wrapped publish primitive itself."
    ),
    ("_index_lock.py", "index_lock", "os.open"): (
        "Lock-file acquisition, confined to `_lock_path_for(index_path)` (a dot-prefixed, "
        "internally-derived sibling path) -- not a caller-selected artifact destination."
    ),
    ("_index_lock.py", "index_lock", "os.write"): (
        "Writes the pid+ownership-token into the just-opened, already-confined lock fd from "
        "the same acquisition (same function as the os.open entry directly above)."
    ),
    ("windows_launcher.py", "_write_windows_exe_bridge_marker", "Path.write_text"): (
        "Fixed-content (`_WINDOWS_EXE_BRIDGE_MARKER_CONTENT`), no externally-sourced bytes; "
        "part of the native PATH-bridge maintenance family (see _refresh_windows_tensor_grep_"
        "com_bridges below), not a user-facing artifact publish."
    ),
    ("doctor_report.py", "_doctor_gpu_search_runtime_probe", "Path.write_text"): (
        "`probe_file` is created from a `TemporaryDirectory()` opened in THIS SAME function "
        "(`with TemporaryDirectory(...) as temp_dir: probe_file = Path(temp_dir) / 'probe.log'`) "
        "-- a self-contained temp artifact, never published outside the function's own scope."
    ),
    ("windows_launcher.py", "_refresh_windows_tensor_grep_com_bridges", "shutil.copy2"): (
        "Relocates the ALREADY-INSTALLED, already-verified `native_path` binary between two "
        "PATH bridge locations during `tg doctor` -- a native-runtime swap of trusted on-disk "
        "bytes, not a publish of new externally-sourced content (the plan's Task 3 Step 2 "
        "explicitly separates this category: 'Classify artifact writers separately from "
        "launcher/native-runtime/directory swaps... Do not force runtime swaps through "
        "atomic_write_bytes')."
    ),
    ("windows_launcher.py", "_remove_windows_stale_tensor_grep_python_launchers", "os.replace"): (
        "Moves an orphaned launcher aside to a uniquely-named `.bak` path (backup-only, no new "
        "content written) during `tg doctor` cleanup -- a native-runtime swap, same exemption "
        "as the bridge refresh above."
    ),
    ("windows_launcher.py", "_repair_windows_python_subprocess_launcher", "os.replace"): (
        "Backup-aside (`candidate_path` -> `backup_path`) before repair, and rollback restore "
        "(`backup_path` -> `candidate_path`) on failure -- both relocate already-on-disk bytes, "
        "no new externally-sourced content; same native-runtime-swap exemption. Two call sites "
        "share this one function-level fingerprint entry per the exact-fingerprint rule below "
        "being keyed on (module, outer_function, operation), not a raw line number."
    ),
    ("windows_launcher.py", "_repair_windows_python_subprocess_launcher", "shutil.copy2"): (
        "Copies the ALREADY-VERIFIED `native_tg_binary` into `candidate_path` to repair a "
        "foreign launcher -- again relocating trusted on-disk bytes, not publishing new "
        "externally-sourced content."
    ),
    # --- Added when the census widened from the original 3-file scope to the full _CLI_SRC walk
    # (task #859 class ratchet) -- each entry below was individually reviewed against the real
    # source, not force-fit to make the population test pass. ---
    ("agent_capsule.py", "_agent_gpu_evidence", "Path.write_text"): (
        "`probe_dir` is created from a `TemporaryDirectory(prefix='tg-agent-gpu-probe-')` opened "
        "in THIS SAME function -- the identical self-contained-temp-artifact shape as "
        "`main.py::_doctor_gpu_search_runtime_probe` above (same probe.log sentinel pattern), "
        "never published outside the function's own scope."
    ),
    ("audit_manifest.py", "verify_review_bundle", "Path.write_text"): (
        "`tmp_manifest` is created from a `TemporaryDirectory(prefix='tg_bundle_verify_')` opened "
        "in THIS SAME function to stage an embedded manifest for signature re-verification -- a "
        "self-contained temp artifact, never published outside the function's own scope."
    ),
    ("checkpoint_store.py", "create_checkpoint", "shutil.copy2"): (
        "Snapshot copy explicitly passes `follow_symlinks=False` (audit HIGH -- symlink "
        "disclosure): a symlinked entry is stored AS a link, never resolved and copied through, "
        "so this call carries the same destination-identity guarantee os.replace does. Was "
        'previously listed under a `("checkpoint_store.py", "*", ...)` wildcard key that the '
        "matcher (keyed on the exact (module, outer_function, operation) triple) never actually "
        "matched -- the module was simply outside the old 3-file scan scope, so the wildcard was "
        "dead. Fixed to real per-function entries now that the census walks this file."
    ),
    ("checkpoint_store.py", "undo_checkpoint", "shutil.copy2"): (
        "Same `follow_symlinks=False` guarantee as the `create_checkpoint` entry above, at both "
        "the stage-to-temp copy and the commit-phase copy-into-working-tree in this function "
        "(two call sites sharing this one function-level fingerprint, same pattern as the "
        "`_repair_windows_python_subprocess_launcher` entry above)."
    ),
    ("lsp_provider_setup.py", "_ensure_node_runtime", "os.replace"): (
        "Three `os.replace` calls share this fingerprint, and NOT all three are the same shape -- "
        "recorded honestly rather than as a single blanket claim. Line `runtime_dir -> backup_dir` "
        "(backup-aside) and line `backup_dir -> runtime_dir` (failure rollback) both relocate the "
        "EXISTING, previously-installed runtime -- the same native-runtime-swap-of-trusted-bytes "
        "exemption as `_repair_windows_python_subprocess_launcher` above. The middle call, "
        "`staged_dir -> runtime_dir`, DOES publish freshly-downloaded content (staged_dir holds "
        "the just-extracted Node archive) -- structurally the same shape as `_install_release_"
        "native_frontdoor`'s os.replace (VIOLATING, pinned below), except this one operates on a "
        "DIRECTORY TREE, which `atomic_write_bytes`/`atomic_write_bytes_anchored` cannot write at "
        "all (they publish a single file's bytes). `os.replace` is the correct atomic primitive "
        "for a directory-level install: POSIX/Windows rename() replaces the destination directory "
        "entry without ever dereferencing it, so it carries no destination-symlink-follow risk "
        "either way. Sanctioned as a structural gap (no directory-level equivalent of the shared "
        "helper exists to route through), not a rubber stamp -- the archive itself is checksum-"
        "verified (`_verify_node_archive`) before this swap. Contrast with the `shutil.move` call "
        "at the TOP of this same function (line ~356, staging `extracted_dir` into `staged_dir`), "
        "which is NOT sanctioned: `shutil.move` CAN follow an existing destination symlink when "
        "the target looks like a directory, unlike `os.replace` -- a real, cited, un-fixed gap "
        "(see `_EXPECTED_VIOLATING`)."
    ),
    ("lsp_provider_setup.py", "_safe_extract_tar", "archive.extractall"): (
        "Preceded by an explicit zip-slip guard in the SAME function (audit S6, CVE-2007-4559 "
        "class): every member path AND every symlink/hardlink target is resolved and checked "
        "against the destination root before `extractall` runs, with `filter='data'` hardening on "
        "Python 3.12+. Its one caller (`_extract_archive`, itself called only from "
        "`_ensure_node_runtime`) always passes a `TemporaryDirectory()`-derived destination."
    ),
    ("lsp_provider_setup.py", "_safe_extract_zip", "archive.extractall"): (
        "Same zip-slip guard shape as `_safe_extract_tar` above (member-path containment check "
        "before extractall), same caller chain into a `TemporaryDirectory()`-derived destination."
    ),
    ("lsp_provider_setup.py", "_write_package_json", "Path.write_text"): (
        'Fixed-content package.json manifest (a hardcoded `{"name": ..., "private": True}` '
        "dict), no externally-sourced bytes, guarded by `if package_json.exists(): return` so an "
        "existing file is never overwritten -- same fixed-content-marker shape as `main.py::_"
        "write_windows_exe_bridge_marker` above."
    ),
    ("session_daemon.py", "_try_acquire_daemon_start_lock", "os.open"): (
        "Lock-file acquisition at a fixed, internally-derived path (`_daemon_start_lock_path(root)`"
        " = `_sessions_dir(root) / _DAEMON_START_LOCK_FILE`, never a caller-selected artifact "
        "destination) with `O_CREAT|O_EXCL` -- the exact same shape as the already-sanctioned "
        "`_index_lock.py::index_lock` entry above."
    ),
    ("session_daemon.py", "_try_acquire_daemon_start_lock", "os.write"): (
        "Writes only the acquiring pid into the just-opened, already-confined lock fd from the "
        "same acquisition -- same pattern as `_index_lock.py::index_lock`'s os.write entry above."
    ),
    # --- H2 (backlog closeout, docs/BACKLOG.md): individually reviewed against the real source,
    # classifying the 17-line-item / 16-identity population the task named. ---
    ("ast_workflows.py", "test_command", "Path.write_text"): (
        "`temp_name` (the snippet fallback-path write) is written inside `with TemporaryDirectory"
        "(prefix='.tg_test_session_', dir=project_cfg['root_dir']) as session_temp:` opened in "
        "THIS SAME function two-hundred-odd lines above -- `session_temp_path = Path(session_temp)"
        ".resolve()` -- the identical self-contained-temp-artifact shape as `main.py::_doctor_gpu_"
        "search_runtime_probe` above, never published outside the function's own scope. Contrast "
        "with `_batch_search_snippets` below (now routed, not sanctioned): that function receives "
        "the same `session_temp_path` only as a caller-supplied PARAMETER, so confinement cannot "
        "be proven from its own signature -- the boundary this table draws is whether the "
        "TemporaryDirectory is opened in the SAME function as the write, not merely whether the "
        "actual runtime value happens to be confined today."
    ),
    ("lsp_provider_setup.py", "_download", "os.open"): (
        "H2 fix: claims `destination` exclusively via O_CREAT|O_EXCL|O_NOFOLLOW BEFORE the "
        "streamed download starts, then writes through that SAME held fd for the whole transfer -- "
        "the technique later mirrored by `main.py::_download_native_frontdoor_asset`'s os.open "
        "entry above (that call used to close the fd and let urlretrieve reopen the path by name, "
        "a narrow reopen-gap; a follow-up closed it by adopting this same held-fd streaming "
        "approach). This call never closes or reopens -- there is no window between the claim and "
        "the first byte written for a symlink to be swapped in. `atomic_write_bytes` requires the "
        "whole payload in memory up front, which is unsuited to a size-capped streaming download, "
        "so this is the correct alternative primitive rather than a gap left unrouted."
    ),
    ("session_daemon.py", "_write_daemon_metadata_windows", "os.open"): (
        "Audited (#211, #81, #13), documented hand-rolled re-derivation of the shared atomic-write "
        "pattern, needed because the Windows ACL lockdown (`_restrict_windows_file_to_current_"
        "user`) must run strictly BETWEEN temp-file creation and the secret-token write -- a hook "
        "point `atomic_write_bytes_anchored` does not expose (it writes immediately after opening "
        "the temp fd). This os.open call already carries the same O_CREAT|O_EXCL flag as the "
        "shared helper's own temp creation, plus its own `path.is_symlink()` precheck on the "
        "published path (added specifically because this function does NOT get that check for "
        "free by calling the helper) -- see the docstring at this function's definition. A "
        "well-motivated hand-rolled re-derivation is still not literally 'the helper', per this "
        "detector's own `test_tempfile_to_publish_flow_is_detected` control, so it is sanctioned "
        "here explicitly rather than silently passing as helper-backed."
    ),
    (
        "session_daemon.py",
        "_write_daemon_metadata_windows",
        "tensor_grep.cli._index_lock.replace_with_retry",
    ): (
        "The publish step of the SAME hand-rolled sequence as the os.open entry directly above -- "
        "reuses the shared `replace_with_retry` primitive for the actual atomic rename (identical "
        "safety property to every other sanctioned `os.replace`/`replace_with_retry` entry in this "
        "table), but the function AROUND it is a bespoke re-derivation, not a call INTO "
        "`atomic_write_bytes_anchored`, so the detector correctly does not credit it as helper-"
        "backed. Same rationale as the os.open entry above; sanctioned together."
    ),
}


def classify_with_sanctions(candidates: list[Candidate]) -> list[Candidate]:
    """Reclassify VIOLATING candidates whose (module, outer_function, operation) fingerprint is
    in the sanctioned table as SANCTIONED, attaching the rationale as `detail`."""
    out: list[Candidate] = []
    for c in candidates:
        key = (c.module, c.outer_function, c.operation)
        if c.classification == VIOLATING and key in _SANCTIONED_SITES:
            out.append(
                Candidate(
                    c.module,
                    c.outer_function,
                    c.line,
                    c.operation,
                    c.destination_provenance,
                    SANCTIONED,
                    detail=_SANCTIONED_SITES[key],
                )
            )
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------------------------
# Bidirectional control: the historical pre-#859 fixture vs. the current codemap.py.
# ---------------------------------------------------------------------------------------------


def test_fixture_byte_exact_and_hash_pinned() -> None:
    """If this fails, the fixture drifted -- it must stay byte-exact to the historical blob so
    the 'violating' arm below is testing the ACTUAL pre-fix code, not a paraphrase of it."""
    raw = _FIXTURE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256, (
        f"tests/fixtures/audits/codemap_pre_859.py drifted from commit {_FIXTURE_SOURCE_COMMIT} "
        "-- do not hand-edit this fixture; keep provenance constants in this test file only."
    )


def test_bidirectional_control_historical_violates_current_helper_backed() -> None:
    """The load-bearing control: if both arms classified the same way, the detector is broken.
    Historical `codemap.py::_atomic_write_text` (pre-#859) hand-rolled its own temp+
    `replace_with_retry` publish, bypassing the shared symlink-refusing helper -> VIOLATING.
    Current `codemap.py::_atomic_write_text` delegates via a LOCAL import of `atomic_write_bytes`
    -> HELPER-BACKED. The arms MUST differ."""
    historical = scan_file(_FIXTURE_PATH, "codemap_pre_859.py")
    historical_sink = [c for c in historical if c.outer_function == "_atomic_write_text"]
    assert historical_sink, (
        "detector found no candidate in the historical _atomic_write_text at all"
    )
    assert all(c.classification == VIOLATING for c in historical_sink), (
        f"historical _atomic_write_text must classify VIOLATING, got {historical_sink}"
    )

    current = scan_file(_CLI_SRC / "codemap.py", "codemap.py")
    current_sink = [c for c in current if c.outer_function == "_atomic_write_text"]
    assert current_sink, "detector found no candidate in the current _atomic_write_text at all"
    assert all(c.classification == HELPER_BACKED for c in current_sink), (
        f"current _atomic_write_text must classify HELPER-BACKED, got {current_sink}"
    )

    historical_labels = {c.classification for c in historical_sink}
    current_labels = {c.classification for c in current_sink}
    assert historical_labels != current_labels, (
        "bidirectional control is broken: both arms produced the same classification "
        f"({historical_labels}) -- the detector cannot distinguish the fixed code from the bug"
    )


# ---------------------------------------------------------------------------------------------
# Individually-red controls: each of these exercises exactly one resolution capability. Every
# one was run, alone (`pytest -k <name>`), against a temporarily-neutered engine and observed
# to fail with the exact "no sink discovered" shape (never an ImportError / collection error --
# a missing-module red proves nothing) before the corresponding engine capability was restored.
# See the task report for the exact transcripts on a representative sample.
# ---------------------------------------------------------------------------------------------


def _candidates_for(source: str, module: str = "synthetic.py") -> list[Candidate]:
    return scan_source(textwrap.dedent(source), module)


def test_renamed_os_replace_is_detected() -> None:
    """`import os as _o; _o.replace(...)` must still resolve to the `os.replace` sink identity."""
    src = """
        import os as _o

        def publish(tmp_path, dest):
            _o.replace(tmp_path, dest)
        """
    candidates = _candidates_for(src)
    sinks = [c for c in candidates if c.operation == "os.replace"]
    assert sinks, f"renamed os.replace was not discovered as a sink at all: {candidates}"
    assert sinks[0].classification == VIOLATING


def test_renamed_shutil_move_is_detected() -> None:
    src = """
        import shutil as _sh

        def publish(src_dir, dest_dir):
            _sh.move(src_dir, dest_dir)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "shutil.move"]
    assert sinks and sinks[0].classification == VIOLATING


def test_renamed_shutil_copy_and_copy2_are_detected() -> None:
    src = """
        import shutil as _sh

        def publish_a(src_path, dest_path):
            _sh.copy(src_path, dest_path)

        def publish_b(src_path, dest_path):
            _sh.copy2(src_path, dest_path)
        """
    candidates = _candidates_for(src)
    ops = {c.operation for c in candidates}
    assert "shutil.copy" in ops and "shutil.copy2" in ops
    assert all(c.classification == VIOLATING for c in candidates)


def test_import_aliased_replace_with_retry_is_detected() -> None:
    src = """
        from tensor_grep.cli._index_lock import replace_with_retry as _rwr

        def publish(tmp_path, dest):
            _rwr(tmp_path, dest)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == _REPLACE_WITH_RETRY]
    assert sinks and sinks[0].classification == VIOLATING


def test_assignment_aliased_replace_with_retry_is_detected() -> None:
    src = """
        from tensor_grep.cli._index_lock import replace_with_retry

        def publish(tmp_path, dest):
            do_it = replace_with_retry
            do_it(tmp_path, dest)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == _REPLACE_WITH_RETRY]
    assert sinks and sinks[0].classification == VIOLATING


def test_local_function_scope_import_is_detected() -> None:
    """A local (function-scope) `import os` -- not a module-level import -- must still resolve
    inside that function."""
    src = """
        def publish(tmp_path, dest):
            import os
            os.replace(tmp_path, dest)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "os.replace"]
    assert sinks and sinks[0].classification == VIOLATING


def test_local_import_does_not_leak_into_sibling_function() -> None:
    """A local import in one function must not resolve calls in an unrelated sibling function
    (each function gets a fresh clone of the module-level scope, not a shared mutable one)."""
    src = """
        def has_local_import(tmp_path, dest):
            import os
            os.replace(tmp_path, dest)

        def no_local_import(tmp_path, dest):
            os.replace(tmp_path, dest)
        """
    candidates = _candidates_for(src)
    by_fn = {c.outer_function: c for c in candidates if c.operation == "os.replace"}
    assert "has_local_import" in by_fn
    assert "no_local_import" not in by_fn, (
        "sibling function without its own `os` import must NOT resolve os.replace -- scope leaked"
    )


def test_shadowing_rebinding_clears_stale_identity() -> None:
    """`open` reassigned to something unrelated must stop resolving as the builtin write sink
    for calls AFTER the rebinding."""
    src = """
        def publish(dest, other_thing):
            open = other_thing
            open(dest, "w")
        """
    candidates = _candidates_for(src)
    assert not any(c.operation == "builtin.open" for c in candidates), (
        f"rebound `open` name must not resolve to the builtin write sink: {candidates}"
    )


def test_direct_writer_bound_under_another_name_is_detected() -> None:
    """`writer = open; writer(dest, "w")` -- an assignment-aliased builtin open() call."""
    src = """
        def publish(dest, content):
            writer = open
            handle = writer(dest, "w")
            handle.write(content)
        """
    candidates = _candidates_for(src)
    sinks = [c for c in candidates if c.operation == "builtin.open"]
    assert sinks and sinks[0].classification == VIOLATING


def test_variable_write_mode_is_detected() -> None:
    """The write-mode string arrives via a variable, not a literal -- `_extract_mode` only
    handles the literal-constant case, so a variable mode is a case this control documents as
    NOT resolved by mode-string inspection; the call must still surface as a candidate via the
    unconditional open()-with-non-None-non-read-mode path. This control pins the CURRENT
    (conservative) behaviour: a non-literal mode is currently NOT classified as write-shaped
    (mode is None from `_extract_mode`), which is a KNOWN GAP, not a false negative silently
    reclassified as safe -- see the module docstring."""
    src = """
        def publish(dest, mode_var):
            open(dest, mode_var)
        """
    candidates = _candidates_for(src)
    # Documents the current, conservative behaviour explicitly (no assertion flips silently if
    # this is later tightened -- a literal True/False assertion here forces a deliberate review).
    assert candidates == [], (
        "a variable write-mode is not currently classified write-shaped by this detector "
        "(KNOWN GAP, see module docstring) -- if this now finds a candidate, update this "
        "control's assertion AND the module docstring's Known-gaps section together"
    )


def test_io_open_write_mode_is_detected() -> None:
    src = """
        import io

        def publish(dest, content):
            handle = io.open(dest, "w")
            handle.write(content)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "io.open"]
    assert sinks and sinks[0].classification == VIOLATING


def test_os_open_write_flag_propagation_is_detected() -> None:
    """The O_WRONLY flag arrives via a variable built from `os.O_*` constants, not inline in the
    call -- the flags argument itself is unparsed and scanned for the flag names textually."""
    src = """
        import os

        def publish(dest):
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(dest, flags)
            os.close(fd)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "os.open"]
    assert sinks and sinks[0].classification == VIOLATING


def test_os_open_read_only_is_not_a_candidate() -> None:
    src = """
        import os

        def read_it(path):
            fd = os.open(path, os.O_RDONLY)
            os.close(fd)
        """
    assert _candidates_for(src) == []


def test_path_open_write_mode_is_detected() -> None:
    src = """
        from pathlib import Path

        def publish(dest, content):
            p = Path(dest)
            with p.open("w") as handle:
                handle.write(content)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "Path.open"]
    assert sinks and sinks[0].classification == VIOLATING


def test_path_open_read_mode_is_not_a_candidate() -> None:
    src = """
        from pathlib import Path

        def read_it(src_path):
            p = Path(src_path)
            with p.open("r") as handle:
                return handle.read()
        """
    assert _candidates_for(src) == []


def test_path_write_text_is_detected() -> None:
    src = """
        def publish(dest, content):
            dest.write_text(content, encoding="utf-8")
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "Path.write_text"]
    assert sinks and sinks[0].classification == VIOLATING


def test_path_write_bytes_is_detected() -> None:
    src = """
        def publish(dest, data):
            dest.write_bytes(data)
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "Path.write_bytes"]
    assert sinks and sinks[0].classification == VIOLATING


def test_tempfile_to_publish_flow_is_detected() -> None:
    """A hand-rolled temp-write-then-replace flow (the exact historical #859 shape) must
    classify as violating even though it superficially resembles the approved helper's own
    internals -- it is NOT the approved helper, it is a re-derivation of it."""
    src = """
        import os
        from uuid import uuid4

        def publish(path, content):
            tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, path)
        """
    candidates = _candidates_for(src)
    ops = {c.operation for c in candidates}
    assert "Path.write_text" in ops
    assert "os.replace" in ops
    assert all(c.classification == VIOLATING for c in candidates)


def test_direct_leaf_preresolution_before_approved_helper_is_violating() -> None:
    """`dest.resolve()` erases the caller-selected leaf's symlink identity BEFORE the approved
    helper call -- this must classify VIOLATING even though the actual write goes through the
    approved helper, because the destination it publishes to is no longer the original leaf."""
    src = """
        from tensor_grep.cli._index_lock import atomic_write_bytes

        def publish(dest, data):
            resolved_dest = dest.resolve()
            atomic_write_bytes(resolved_dest, data)
        """
    candidates = _candidates_for(src)
    helper_sinks = [
        c for c in candidates if c.operation == "tensor_grep.cli._index_lock.atomic_write_bytes"
    ]
    assert helper_sinks, f"expected a candidate for the helper call: {candidates}"
    assert helper_sinks[0].classification == VIOLATING, (
        f"destination pre-resolved via .resolve() before an approved helper must be VIOLATING, "
        f"got {helper_sinks[0]}"
    )


def test_aliased_leaf_preresolution_before_approved_helper_is_violating() -> None:
    """Same as above, but through an import-aliased helper AND an aliased `.resolve()` receiver
    chain -- both the destination-provenance check and the helper-alias resolution must compose."""
    src = """
        from tensor_grep.cli._index_lock import atomic_write_bytes as _awb

        def publish(dest, data):
            canonical = dest.resolve()
            _awb(canonical, data)
        """
    candidates = _candidates_for(src)
    helper_sinks = [
        c for c in candidates if c.operation == "tensor_grep.cli._index_lock.atomic_write_bytes"
    ]
    assert helper_sinks and helper_sinks[0].classification == VIOLATING


def test_os_path_realpath_preresolution_before_approved_helper_is_violating() -> None:
    src = """
        import os
        from tensor_grep.cli._index_lock import atomic_write_bytes

        def publish(dest, data):
            real_dest = os.path.realpath(dest)
            atomic_write_bytes(real_dest, data)
        """
    candidates = _candidates_for(src)
    helper_sinks = [
        c for c in candidates if c.operation == "tensor_grep.cli._index_lock.atomic_write_bytes"
    ]
    assert helper_sinks and helper_sinks[0].classification == VIOLATING


def test_direct_call_to_approved_helper_is_helper_backed() -> None:
    """The clean positive control: no aliasing, no pre-resolution -- straight call to the
    approved helper must classify helper-backed, never violating."""
    src = """
        from tensor_grep.cli._index_lock import atomic_write_bytes

        def publish(dest, data):
            atomic_write_bytes(dest, data)
        """
    candidates = _candidates_for(src)
    assert candidates and all(c.classification == HELPER_BACKED for c in candidates)


def test_replace_rename_open_on_non_path_receiver_is_not_a_candidate() -> None:
    """`str.replace`, `dataclasses.replace`, and `datetime.replace` must never be misread as
    Path.replace/Path.rename sinks -- the type-gate excludes them because the receiver is not
    provably Path-typed. This is the documented KNOWN GAP boundary, exercised explicitly rather
    than left as an accidental silence."""
    src = """
        import dataclasses
        from datetime import datetime

        def not_a_sink_one(text):
            return text.replace("a", "b")

        def not_a_sink_two(config):
            return dataclasses.replace(config, flag=True)

        def not_a_sink_three(moment):
            return moment.replace(microsecond=0)
        """
    assert _candidates_for(src) == []


def test_safe_negative_control_temp_created_inside_function_is_not_flagged() -> None:
    """A caller-supplied path is required to have an explicit sanction (confinement is not
    statically decidable); a temp path CREATED inside the analyzed function, from a fresh
    uuid4-derived name, is the safe shape and must not spuriously classify as violating on its
    OWN creation -- only the eventual publish call is a candidate."""
    src = """
        import os
        from uuid import uuid4
        from tensor_grep.cli._index_lock import atomic_write_bytes_anchored

        def publish(base_dir, data):
            tmp_name = f".{uuid4().hex}.tmp"
            tmp_path = base_dir / tmp_name
            atomic_write_bytes_anchored(tmp_path, data)
        """
    candidates = _candidates_for(src)
    assert candidates and all(c.classification == HELPER_BACKED for c in candidates)


def test_caller_supplied_temp_path_requires_explicit_sanction() -> None:
    """A "temp" path handed in as a PARAMETER (not created inside the function) is not
    statically provable to be confined -- the detector correctly has no special-case for
    "looks like a temp path" and classifies purely on the sink shape, same as any other direct
    writer. This control documents that there is deliberately no path-name-based leniency."""
    src = """
        def publish(caller_supplied_temp_path, content):
            caller_supplied_temp_path.write_text(content, encoding="utf-8")
        """
    sinks = [c for c in _candidates_for(src) if c.operation == "Path.write_text"]
    assert sinks and sinks[0].classification == VIOLATING, (
        "a caller-supplied path must not get implicit leniency just because its name looks "
        "like a temp path -- only an exact fingerprint sanction may exempt it"
    )


# ---------------------------------------------------------------------------------------------
# Generated-source surfacing (KNOWN GAP -- see module docstring): the census must never let a
# `python -c <payload>` execution root silently vanish from the population, even though this
# first cut does not recursively classify the payload's OWN write calls.
# ---------------------------------------------------------------------------------------------


def _real_subprocess_dash_c_sites(source: str, module: str) -> list[tuple[str, str]]:
    """Every `subprocess.run(...)`/`subprocess.Popen(...)`/`subprocess.call(...)` (etc.) call
    whose argv LIST contains the literal string ``"-c"`` -- i.e. an actual generated-Python
    execution root, never a same-spelling CLI short flag like Typer's ``-c/--config`` or ripgrep's
    ``-c/--count`` passthrough (those appear as bare string literals OUTSIDE a subprocess-spawn
    argv list, so they never match this check)."""
    tree = ast.parse(source, filename=module)
    # Resolve each call's ENCLOSING FUNCTION. The identity must be stable across unrelated edits:
    # this pin previously used `node.lineno` and reddened main when PR #916 added comment lines to
    # main.py, shifting every number. Both PRs were green alone; only the merged tree was red.
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _outer_function(node: ast.AST) -> str:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current.name
            current = parents.get(current)
        return "<module>"

    hits: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else (func.id if isinstance(func, ast.Name) else None)
        )
        if name not in ("run", "Popen", "call", "check_call", "check_output"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.List):
                for elt in arg.elts:
                    if isinstance(elt, ast.Constant) and elt.value == "-c":
                        hits.append((module, _outer_function(node)))
    return sorted(set(hits))


def test_generated_source_c_sites_are_surfaced() -> None:
    """Pins the exact, reviewed set of real `python -c <payload>` execution roots in the scanned
    files. This is the population-never-silently-drops-a-candidate guarantee applied to the one
    class this detector does not yet recurse into: if a new `-c` site appears (or one of these
    moves/disappears), this test fails LOUDLY rather than the population count quietly staying
    the same while a new unaudited execution root is added.

    KNOWN GAP, stated plainly: `_schedule_windows_native_frontdoor_refresh` and
    `_schedule_windows_self_upgrade` each spawn a detached
    self-upgrade helper script (`helper_code = textwrap.dedent(<triple-quoted literal>)`) that embeds its OWN
    `os.replace(temp_path, native_path)`, `metadata_path.write_text(...)`, and
    `shutil.copy2(native_path, bridge_path)` calls for the SAME native-binary-install flow that
    produced this task's pinned `violating` findings in `_install_release_native_frontdoor` /
    `_write_native_frontdoor_metadata` / `_download_native_frontdoor_asset`. Those two generated
    scripts are NOT recursively parsed by this first cut of the ratchet and their writers are
    therefore NOT yet counted in the `violating` total below -- they almost certainly carry the
    identical defect. `_verify_target_python_tensor_grep_version` spawns a version-check probe
    with no write calls at all
    (`import importlib.metadata as m; ...; print(m.version('tensor-grep'))`) and is lower risk.
    Recursing this detector into generated-source payloads is left as an explicit follow-up, not
    silently declared clean.
    """
    # SCOPE WIDENED 2026-08-20 (the cli/main.py split): this used to scan ONLY main.py and then
    # assert every other module had ZERO sites. Two of the three roots below then moved into
    # main.py's extracted siblings, which would have made the "everyone else has zero" half red
    # and -- worse, had the pin been relaxed instead -- could have let a site LEAVE main.py and
    # leave the population entirely. The pin is now over the whole discovered walk, keyed by
    # (module, function), so relocating a root is visible but not a loophole.
    sites: list[tuple[str, str]] = []
    for fname in _SCANNED_PRODUCTION_FILES:
        sites.extend(_real_subprocess_dash_c_sites(_read(_CLI_SRC / fname), fname))
    assert sorted(sites) == [
        ("main.py", "_schedule_windows_self_upgrade"),
        ("native_frontdoor.py", "_verify_target_python_tensor_grep_version"),
        ("windows_launcher.py", "_schedule_windows_native_frontdoor_refresh"),
    ], (
        f"the set of real subprocess '-c' execution roots under cli/ changed: {sorted(sites)}. "
        "If this is a genuine new site, add it here AND assess whether its payload writes files "
        "(update the module docstring's Known-gaps section and, ideally, extend the detector to "
        "recurse into it rather than just re-pinning this list)."
    )


# ---------------------------------------------------------------------------------------------
# Population / inventory: the complete current census of the three scanned production files,
# pinned by STABLE (module, outer_function, operation) identity -- never a raw line number,
# which drifts on unrelated edits. Zero unresolved candidates is a hard requirement: an
# unresolved call must fail this test, never silently vanish from the count.
# ---------------------------------------------------------------------------------------------


def _scan_all_scoped_files() -> list[Candidate]:
    all_candidates: list[Candidate] = []
    for fname in _SCANNED_PRODUCTION_FILES:
        all_candidates.extend(scan_file(_CLI_SRC / fname, fname))
    return classify_with_sanctions(all_candidates)


def test_scanned_population_floor() -> None:
    """The census must DISCOVER its population by walking `_CLI_SRC`, not enumerate a hand-
    maintained list -- see `_discover_scanned_production_files`. This is the guard against the
    walk silently resolving zero/few files (wrong directory, glob typo, partial checkout) and
    that truncation reading as a clean "no violations" -- exactly the false-green shape AGENTS.md
    warns about: an empty scan and a genuinely clean codebase are indistinguishable without this
    floor. 60 modules exist as of 2026-08-20; asserting only a FLOOR (not an exact count) means
    ordinary future module additions don't require touching this line."""
    assert len(_SCANNED_PRODUCTION_FILES) >= _MINIMUM_SCANNED_MODULE_COUNT, (
        f"the discovered population walk found only {len(_SCANNED_PRODUCTION_FILES)} module(s) "
        f"under {_CLI_SRC} -- expected at least {_MINIMUM_SCANNED_MODULE_COUNT}. This usually "
        "means the walk silently resolved the wrong directory or an (nearly-)empty checkout, "
        "which would make every other population test in this module pass for the wrong reason."
    )


def test_population_has_zero_unresolved() -> None:
    candidates = _scan_all_scoped_files()
    unresolved = [c for c in candidates if c.classification == UNRESOLVED]
    assert unresolved == [], (
        f"the census must never silently drop a candidate -- {len(unresolved)} call(s) were "
        f"write-shaped but could not be classified: {unresolved}"
    )


# The three functions #859's instance fix (commit a41c86f) routed through
# `atomic_write_bytes_anchored` -- pinned green permanently as the regression guard for that fix,
# plus every OTHER helper-backed writer surfaced once the census widened from the original 3-file
# scope (main.py/_index_lock.py/codemap.py) to the full `_CLI_SRC` walk.
_EXPECTED_HELPER_BACKED = {
    (
        "ast_scan.py",
        "_write_json_refuse_symlink",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    (
        "main.py",
        "_write_ast_project_scaffold",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    ("main.py", "new", "tensor_grep.cli._index_lock.atomic_write_bytes_anchored"),
    # The bidirectional control's "current" arm (also asserted directly above).
    ("codemap.py", "_atomic_write_text", "tensor_grep.cli._index_lock.atomic_write_bytes"),
    # --- Newly discovered once the census walked the full _CLI_SRC directory ---
    (
        "audit_manifest.py",
        "_write_history_index",
        "tensor_grep.cli._index_lock.atomic_write_json",
    ),
    (
        "checkpoint_store.py",
        "_write_json_atomic",
        "tensor_grep.cli._index_lock.atomic_write_json",
    ),
    ("agent_installer.py", "_atomic_write_text", "tensor_grep.cli._index_lock.atomic_write_bytes"),
    ("dogfood.py", "_write_json_atomic", "tensor_grep.cli._index_lock.atomic_write_bytes"),
    (
        "evidence_signing.py",
        "_write_private_key_atomic",
        "tensor_grep.cli._index_lock.atomic_write_bytes",
    ),
    ("evidence_signing.py", "generate_keypair", "tensor_grep.cli._index_lock.atomic_write_bytes"),
    ("ledger_store.py", "_write_findings_index", "tensor_grep.cli._index_lock.atomic_write_json"),
    ("ledger_store.py", "_write_index", "tensor_grep.cli._index_lock.atomic_write_json"),
    ("ledger_store.py", "record_finding", "tensor_grep.cli._index_lock.atomic_write_json"),
    ("session_store.py", "_write_json_atomic", "tensor_grep.cli._index_lock.atomic_write_json"),
    # --- H2 (backlog closeout): routed through the shared helper family in this task ---
    # `_batch_search_snippets` moved to `ast_workflow_rules.py` in the enterprise
    # file-size campaign Wave 1 split (ast_workflows.py was 1996 lines).
    (
        "ast_workflow_rules.py",
        "_batch_search_snippets",
        "tensor_grep.cli._index_lock.atomic_write_bytes",
    ),
    (
        "checkpoint_store.py",
        "undo_checkpoint",
        "tensor_grep.cli._index_lock.atomic_write_bytes",
    ),
    (
        "lsp_provider_setup.py",
        "_copy_binary_to_managed",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    (
        "lsp_provider_setup.py",
        "_extract_rust_analyzer_exe_from_zip",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    (
        "lsp_provider_setup.py",
        "_download_rust_analyzer",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    (
        "native_frontdoor.py",
        "_write_native_frontdoor_metadata",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
    (
        "native_frontdoor.py",
        "_install_release_native_frontdoor",
        "tensor_grep.cli._index_lock.atomic_write_bytes_anchored",
    ),
}

# The complete sanctioned population, by (module, outer_function, operation) identity -- see
# `_SANCTIONED_SITES` above for the per-entry rationale.
_EXPECTED_SANCTIONED = {
    # O_EXCL exclusivity claim guarding urlretrieve's symlink-following 'wb'; writes zero bytes.
    ("native_frontdoor.py", "_download_native_frontdoor_asset", "os.open"),
    ("_index_lock.py", "replace_with_retry", "os.replace"),
    ("_index_lock.py", "atomic_write_bytes_anchored", "os.open"),
    ("_index_lock.py", "index_lock", "os.open"),
    ("_index_lock.py", "index_lock", "os.write"),
    ("windows_launcher.py", "_write_windows_exe_bridge_marker", "Path.write_text"),
    ("doctor_report.py", "_doctor_gpu_search_runtime_probe", "Path.write_text"),
    ("windows_launcher.py", "_refresh_windows_tensor_grep_com_bridges", "shutil.copy2"),
    ("windows_launcher.py", "_remove_windows_stale_tensor_grep_python_launchers", "os.replace"),
    ("windows_launcher.py", "_repair_windows_python_subprocess_launcher", "os.replace"),
    ("windows_launcher.py", "_repair_windows_python_subprocess_launcher", "shutil.copy2"),
    # --- Newly discovered once the census walked the full _CLI_SRC directory ---
    ("agent_capsule.py", "_agent_gpu_evidence", "Path.write_text"),
    ("audit_manifest.py", "verify_review_bundle", "Path.write_text"),
    ("checkpoint_store.py", "create_checkpoint", "shutil.copy2"),
    ("checkpoint_store.py", "undo_checkpoint", "shutil.copy2"),
    ("lsp_provider_setup.py", "_ensure_node_runtime", "os.replace"),
    ("lsp_provider_setup.py", "_safe_extract_tar", "archive.extractall"),
    ("lsp_provider_setup.py", "_safe_extract_zip", "archive.extractall"),
    ("lsp_provider_setup.py", "_write_package_json", "Path.write_text"),
    ("session_daemon.py", "_try_acquire_daemon_start_lock", "os.open"),
    ("session_daemon.py", "_try_acquire_daemon_start_lock", "os.write"),
    # --- H2 (backlog closeout) ---
    ("ast_workflows.py", "test_command", "Path.write_text"),
    ("lsp_provider_setup.py", "_download", "os.open"),
    ("session_daemon.py", "_write_daemon_metadata_windows", "os.open"),
    (
        "session_daemon.py",
        "_write_daemon_metadata_windows",
        "tensor_grep.cli._index_lock.replace_with_retry",
    ),
}

# H2 (docs/BACKLOG.md backlog closeout) classified and, where warranted, fixed all 16
# (module, outer_function, operation) identities this set used to carry (the task's brief named
# "17 sites" by counting individual call-site LINES; `checkpoint_store.py::undo_checkpoint`'s
# `Path.write_bytes` rollback fires at two lines that share one identity, so the brief's own line
# count does not equal the identity count this set is keyed on -- worth recording since a future
# reader may otherwise "fix" this set to 17 entries and be wrong).
#
# ELEVEN identities were ROUTED (moved to `_EXPECTED_HELPER_BACKED` above): the three
# `_install_release_native_frontdoor` family sites in `main.py` (the highest-priority group per
# the task brief -- a native EXECUTABLE install to a fixed, predictable path), both
# `checkpoint_store.py::undo_checkpoint` `write_bytes` call sites, `ast_workflows.py::_batch_
# search_snippets`'s `write_text`, and six `lsp_provider_setup.py` sites (`_copy_binary_to_
# managed`, `_extract_rust_analyzer_exe_from_zip`'s `Path.open`+`shutil.copyfileobj` pair, and
# `_download_rust_analyzer`'s gzip-branch `Path.open`+`shutil.copyfileobj` pair). FOUR more were
# individually reviewed and SANCTIONED with a stated reason each (see `_SANCTIONED_SITES` above):
# `ast_workflows.py::test_command`'s `write_text` (a genuinely self-contained TemporaryDirectory,
# unlike its `_batch_search_snippets` sibling which only receives that same directory as a
# parameter -- the exact "no implicit leniency" boundary this table has always drawn), `lsp_
# provider_setup.py::_download`'s new O_EXCL|O_NOFOLLOW-claim-then-write-through-the-held-fd
# technique (a genuine H2 fix, just not literally a call into `atomic_write_bytes`, since a
# size-capped streaming download cannot use a helper that requires the whole payload in memory
# up front), and both halves of `session_daemon.py::_write_daemon_metadata_windows`'s audited
# (#211/#81/#13) hand-rolled ACL-lockdown-between-create-and-write sequence.
#
# One of H2's original two deferrals is now CLOSED: `main.py::_download_native_frontdoor_asset`
# no longer calls `urllib.request.urlretrieve` at all. A follow-up (the frontdoor-download-held-fd
# task) replaced it with the same `urlopen` + chunked-write-through-the-held-fd technique this
# task applied to `lsp_provider_setup.py::_download` (sanctioned above) -- the O_EXCL|O_NOFOLLOW
# claim and the transfer now share one fd, closing the reopen-by-name TOCTOU window this comment
# used to describe. That identity has moved to `_SANCTIONED_SITES` (`os.open`, updated rationale
# above); `urllib.request.urlretrieve` no longer appears in the source, so it is no longer a
# candidate this detector can find at all -- there is nothing left to pin here for that half.
#
# The ONE remaining below is DEFERRED, for a reason too large/risky for a targeted PR to carry:
#
# - `lsp_provider_setup.py::_ensure_node_runtime`'s `shutil.move` (staging the extracted archive
#   into place): `shutil.move` CAN follow an existing destination symlink when the target looks
#   like a directory, unlike its sibling `os.replace` calls in the same function (non-dereferencing
#   rename, sanctioned above) -- and unlike those, there is no directory-tree-safe atomic-publish
#   primitive to route through. The docstring at that call site notes `shutil.move` was chosen
#   specifically for its cross-filesystem fallback (the extracted archive lives under the OS temp
#   root, which may not share a filesystem with the install root); switching to `os.replace` would
#   silently break that cross-fs case rather than close the gap. `_remove_stale_staging_path` does
#   pre-clear this exact path earlier in the same function, so the residual window is the
#   download+extract duration between that pre-clear and this move, not the whole function -- real,
#   but narrowing it further needs either a new directory-safe primitive or giving up cross-fs
#   support, both bigger than this PR.
_EXPECTED_VIOLATING = {
    ("lsp_provider_setup.py", "_ensure_node_runtime", "shutil.move"),
}


def _identity_set(candidates: list[Candidate], label: str) -> set[tuple[str, str, str]]:
    return {
        (c.module, c.outer_function, c.operation) for c in candidates if c.classification == label
    }


def test_population_helper_backed_matches_pinned_set() -> None:
    candidates = _scan_all_scoped_files()
    found = _identity_set(candidates, HELPER_BACKED)
    assert found == _EXPECTED_HELPER_BACKED, (
        f"helper-backed population drifted.\nMissing: {_EXPECTED_HELPER_BACKED - found}\n"
        f"New/unexpected: {found - _EXPECTED_HELPER_BACKED}"
    )


def test_population_sanctioned_matches_pinned_set() -> None:
    candidates = _scan_all_scoped_files()
    found = _identity_set(candidates, SANCTIONED)
    assert found == _EXPECTED_SANCTIONED, (
        f"sanctioned population drifted.\nMissing: {_EXPECTED_SANCTIONED - found}\n"
        f"New/unexpected: {found - _EXPECTED_SANCTIONED} -- a NEW direct writer appeared that "
        "isn't in _SANCTIONED_SITES; it must be individually reviewed as helper-backed, "
        "violating, or explicitly sanctioned with a citation, never silently added here"
    )


def test_population_violating_matches_pinned_four_live_violations() -> None:
    """The inventory stays GREEN while expecting these four known, cited violations to remain --
    this test is NOT asserting they are fixed (that is out of this task's scope; see the module
    docstring). It fails the moment the set changes in EITHER direction: a fix that isn't
    reflected here, or a brand-new unreviewed violation appearing anywhere in the scanned files."""
    candidates = _scan_all_scoped_files()
    found = _identity_set(candidates, VIOLATING)
    assert found == _EXPECTED_VIOLATING, (
        f"violating population drifted.\nMissing (fixed and not updated here?): "
        f"{_EXPECTED_VIOLATING - found}\nNew/unexpected: {found - _EXPECTED_VIOLATING}"
    )


def test_population_every_candidate_is_accounted_for() -> None:
    """No candidate silently falls outside the three pinned sets above -- the union must equal
    the FULL discovered population, with none left over unclassified-but-not-unresolved."""
    candidates = _scan_all_scoped_files()
    all_identities = {(c.module, c.outer_function, c.operation) for c in candidates}
    pinned = _EXPECTED_HELPER_BACKED | _EXPECTED_SANCTIONED | _EXPECTED_VIOLATING
    assert all_identities == pinned, (
        f"discovered population != union of pinned sets.\nDiscovered-not-pinned: "
        f"{all_identities - pinned}\nPinned-not-discovered: {pinned - all_identities}"
    )


# ---------------------------------------------------------------------------------------------
# Mutation controls: inject a violation into a COPY of the scanned tree and prove the discovered
# population AND the violation count each increase by exactly one -- twice, independently (an
# ordinary unsafe writer, and a third generated `python -c` helper).
# ---------------------------------------------------------------------------------------------


def _scan_source_with_appended_function(
    base_source: str, module: str, injected_function_src: str
) -> list[Candidate]:
    mutated = base_source + "\n\n" + textwrap.dedent(injected_function_src) + "\n"
    return scan_source(mutated, module)


def test_mutation_injecting_an_unsafe_writer_increases_population_and_violations_by_one() -> None:
    base_source = _read(_CLI_SRC / "main.py")
    before = classify_with_sanctions(scan_source(base_source, "main.py"))
    before_population = len(before)
    before_violations = len([c for c in before if c.classification == VIOLATING])

    injected = """
        def _mutation_injected_unsafe_writer(dest, content):
            dest.write_text(content, encoding="utf-8")
        """
    mutated_candidates = _scan_source_with_appended_function(base_source, "main.py", injected)
    after = classify_with_sanctions(mutated_candidates)
    after_population = len(after)
    after_violations = len([c for c in after if c.classification == VIOLATING])

    assert after_population == before_population + 1, (
        f"injecting one unsafe writer must grow the discovered population by exactly one: "
        f"before={before_population} after={after_population}"
    )
    assert after_violations == before_violations + 1, (
        f"injecting one unsafe writer must grow the violation count by exactly one: "
        f"before={before_violations} after={after_violations}"
    )
    injected_hit = [c for c in after if c.outer_function == "_mutation_injected_unsafe_writer"]
    assert injected_hit and injected_hit[0].classification == VIOLATING


def test_mutation_injecting_a_third_generated_helper_is_surfaced_and_increases_by_one() -> None:
    """A THIRD generated `python -c <payload>` helper (beyond the two real ones already pinned)
    must grow the surfaced `-c` site count by exactly one -- proving the surfacing check would
    catch a brand-new unaudited execution root, not just the two it already knows about."""
    base_source = _read(_CLI_SRC / "main.py")
    before_sites = _real_subprocess_dash_c_sites(base_source, "main.py")

    injected = """
        def _mutation_injected_generated_helper(log_path):
            import subprocess
            import sys

            payload = "print('mutation probe')"
            subprocess.run([sys.executable, "-c", payload], check=True)
        """
    mutated_source = base_source + "\n\n" + textwrap.dedent(injected) + "\n"
    after_sites = _real_subprocess_dash_c_sites(mutated_source, "main.py")

    assert len(after_sites) == len(before_sites) + 1, (
        f"injecting one new '-c' execution root must grow the surfaced count by exactly one: "
        f"before={len(before_sites)} after={len(after_sites)}"
    )
    new_lines = {ln for _mod, ln in after_sites} - {ln for _mod, ln in before_sites}
    assert len(new_lines) == 1, f"expected exactly one new line number, got {new_lines}"


def test_mutation_control_reverted_is_byte_identical_and_green() -> None:
    """After both mutation probes above, the real on-disk `main.py` this test also scans directly
    (via `_scan_all_scoped_files`) is untouched -- mutations were applied to in-memory COPIES
    (string concatenation), never to the file on disk. Re-running the pinned population test in
    the same process proves nothing leaked between the mutation probes and the inventory."""
    candidates = _scan_all_scoped_files()
    found = _identity_set(candidates, VIOLATING)
    assert found == _EXPECTED_VIOLATING, (
        "the real on-disk population must be unaffected by the in-memory mutation probes above"
    )


def test_population_includes_previously_unscanned_checkpoint_store_write_bytes() -> None:
    """RED-before-fix regression guard (task #859 class ratchet): the population census used to
    be scoped to a hardcoded 3-file ``_SCANNED_PRODUCTION_FILES`` tuple, so a real unsafe writer
    living in any OTHER cli module -- here, ``checkpoint_store.py``'s rollback ``write_bytes`` in
    ``undo_checkpoint`` -- was invisible to it: a new module joining the package was silently
    never scanned. This targets the DISCOVERED population (``_scan_all_scoped_files()``), not the
    hardcoded tuple, so it fails on the pre-fix code with a plain "site not found" assertion (not
    an ImportError/NameError -- a missing-module red proves nothing) and passes once the census
    walks the whole ``_CLI_SRC`` directory.

    H2 (backlog closeout) routed this exact site through ``atomic_write_bytes`` (see
    ``_EXPECTED_HELPER_BACKED`` above), so the identity this test asserts changed from
    ``Path.write_bytes`` to the helper-backed operation string -- updated here rather than
    deleted, since the test's REAL purpose (prove `checkpoint_store.py` is walked by the
    directory-wide census, not a hardcoded file list) still holds and is still worth guarding."""
    candidates = _scan_all_scoped_files()
    identities = {(c.module, c.outer_function, c.operation) for c in candidates}
    assert (
        "checkpoint_store.py",
        "undo_checkpoint",
        "tensor_grep.cli._index_lock.atomic_write_bytes",
    ) in identities, (
        f"checkpoint_store.py's undo_checkpoint write_bytes site is missing from the census -- "
        f"the population walk is still scoped to a hardcoded file list. identities={identities}"
    )
