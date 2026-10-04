"""Ratchet: case-insensitivity is decided in ONE place, ``core/case_semantics.py``.

Re-deriving it inline (``config.ignore_case or (config.smart_case and pattern.islower())``) is a
silent-wrong-answer class: the copies drifted from rg's precedence (explicit ``-s`` beats ``-i``
and ``-S``) and miscounted. Every engine must call ``effective_ignore_case`` /
``case_regex_flags`` instead.

The scan is an AST walk over ``src/tensor_grep``: any attribute READ of ``.ignore_case`` or
``.smart_case`` (and any ``getattr(x, "ignore_case"|"smart_case")``) outside the allowlist fails.
Field DEFINITIONS (``ignore_case: bool = False`` in config.py) are ``AnnAssign`` Name targets,
not attribute reads, so they need no entry. ``SearchConfig(ignore_case=...)`` keyword
constructions are ``keyword`` nodes, also not reads.

Allowlist (file, enclosing function) -- each is a pure TRANSLATION of the fields, never a
precedence decision:
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tensor_grep"

_FIELDS = {"ignore_case", "smart_case"}
_ALLOWED = {
    # the resolver itself
    ("core/case_semantics.py", "*"),  # every function in the resolver module
    # rg flag EMISSION: rg itself resolves -i/-s/-S by last-flag-wins (the helper orders them)
    ("backends/ripgrep_backend.py", "_pattern_semantics_flags"),
    # routing predicate "does the native count lack an input for this flag" -- not a case decision
    ("core/pipeline.py", "_count_needs_rg_semantics"),
    # re-emits the user's own -i / -s argv for the native binary (which resolves precedence)
    ("cli/main.py", "_build_native_tg_search_command"),
    # forwards the raw smart_case bool to the rg passthrough as-is
    ("backends/rust_backend.py", "search"),
}


def _enclosing_function_names(tree: ast.AST) -> dict[int, str]:
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner[id(node)] = fn.name  # innermost wins because walk visits outer first
    return owner


def _violations() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owner = _enclosing_function_names(tree)
        for node in ast.walk(tree):
            attr = None
            if isinstance(node, ast.Attribute) and node.attr in _FIELDS:
                if isinstance(node.ctx, ast.Load):
                    attr = node.attr
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _FIELDS
            ):
                attr = str(node.args[1].value)
            if attr is None:
                continue
            fn = owner.get(id(node), "<module>")
            if (rel, fn) not in _ALLOWED and (rel, "*") not in _ALLOWED:
                found.append(f"{rel}:{node.lineno} reads .{attr} in {fn}()")
    return found


def test_no_inline_case_insensitivity_decisions():
    violations = _violations()
    assert not violations, (
        "decide case via tensor_grep.core.case_semantics.effective_ignore_case / "
        "case_regex_flags, not inline:\n" + "\n".join(violations)
    )


def test_allowlist_has_no_stale_entries():
    # an entry must still read a case field, otherwise delete it (keeps the allowlist honest)
    seen: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owner = _enclosing_function_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FIELDS:
                seen.add((rel, owner.get(id(node), "<module>")))
    seen_files = {rel for rel, _ in seen}
    stale = {
        (rel, fn)
        for rel, fn in _ALLOWED
        if (fn == "*" and rel not in seen_files) or (fn != "*" and (rel, fn) not in seen)
    }
    assert not stale, sorted(stale)


def test_scan_detects_the_old_expression():
    # positive control: the scan must fire on the exact expression this ratchet exists to ban
    tree = ast.parse(
        "def f(config, pattern):\n    return config.ignore_case or config.smart_case\n"
    )
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in _FIELDS]
    assert len(hits) == 2
