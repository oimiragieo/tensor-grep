"""Census + raw-argv gates for plan 2026-10-03-bughunt-wave2a Part F.

Split out of test_bootstrap_search_guards.py to stay under the 2000-line test limit.
Pure AST over the three front-door modules: no rg, no git, no subprocess. Collects on main (it reads
the source files; the guards module is simply absent there, so its parametrized case fails).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap

_CLI_DIR = Path(bootstrap.__file__).parent


def _cli_source(module_file: str) -> str:
    return (_CLI_DIR / module_file).read_text(encoding="utf-8")


_MODULE_FILES = ["bootstrap.py", "bootstrap_native_argv.py", "bootstrap_search_guards.py"]


def _argv_taking_functions(tree: ast.Module) -> set[str]:
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and any(a.arg == "search_args" for a in n.args.args)
    }


def raw_argv_violations(source: str) -> list[str]:
    """AST gate for the r36 rule. (1) a function with a ``search_args`` parameter never assigns it;
    (2) a name bound from ``option_tokens(...)`` is never passed to a function that takes argv."""
    tree = ast.parse(source)
    argv_fns = _argv_taking_functions(tree) | {
        "flag_present",
        "option_tokens",
        "positionals",
        "end_of_options_index",
        "has_end_of_options",
        "regex_patterns",
        "_flag_present",
        "_regex_patterns_from_search_args",
    }
    bad: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        params = {a.arg for a in fn.args.args}
        opts_names: set[str] = set()
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == "search_args" and "search_args" in params:
                    bad.append(f"{fn.name}: reassigns search_args")
            if isinstance(node, ast.Assign) and any(
                isinstance(c, ast.Call) and getattr(c.func, "id", "") == "option_tokens"
                for c in ast.walk(node.value)
            ):
                opts_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                callee = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if callee in argv_fns and any(
                    isinstance(a, ast.Name) and a.id in opts_names for a in node.args
                ):
                    bad.append(f"{fn.name}: passes an option_tokens() result to {callee}")
    return bad


@pytest.mark.parametrize("module_file", _MODULE_FILES)
def test_guards_take_raw_argv(module_file):
    assert raw_argv_violations(_cli_source(module_file)) == []


def test_raw_argv_gate_can_fail():  # mutation controls on temporary source
    reassigns = "def f(search_args):\n    search_args = list(search_args)\n    return search_args\n"
    refeeds = (
        "def f(search_args):\n    opts = list(option_tokens(search_args))\n"
        "    return flag_present(opts, {'-e'})\n"
    )
    clean = (
        "def f(search_args):\n    opts = list(option_tokens(search_args))\n"
        "    return [o for o in opts] and flag_present(search_args, {'-e'})\n"
    )
    assert raw_argv_violations(reassigns) == ["f: reassigns search_args"]
    assert raw_argv_violations(refeeds) == ["f: passes an option_tokens() result to flag_present"]
    assert raw_argv_violations(clean) == []


# --- census (plan matcher, WIDENED in round 40 to the gaps found by executing it) ---
#
# Classes, by ENCLOSING FUNCTION NAME (never line number):
#   T    top-level dispatch on argv[0]/argv[1] or a cheap pre-gate before the guards run
#   V    value-consumption parsing: the tokenizer itself and the pattern-slot heuristic
#   PARSED  consumes the tokenizer's output (spellings/values), never raw argv
#   R    "route to the full CLI" tests: wrongly routing a pattern/value there only costs speed
#        (the documented safe direction); short flags in them are deliberately exact-token
#   X    other commands' argv (`tg run`, `tg scan`), not search arguments
#   PTH  scanners over an already-extracted PATH list
# (P) presence checks and (S) sentinel checks have NO class: they must be flag_present /
# has_end_of_options calls, so any Compare/startswith shaped like one outside these names fails.
_T_SITES = {
    "_print_version",
    "_top_level_command_refusal",
    "_is_public_help_invocation",
    "_normalize_search_invocation",
    "main_entry",
}
# Round 39: `_requires_full_cli` is NOT class T. The plan filed it under "top-level dispatch on
# argv[0]/argv[1]", but it is called with SEARCH args (main_entry passes passthrough_search_args),
# so `-e -h` and `-- -h` reached it. Its help/completion test is now a (P) site routed through
# flag_present; what stays is the tg-only-flag over-route (R) and the bundled-scan walk (V).
_R_SITES = {"_requires_full_cli"}
_X_SITES = {"_scan_requires_full_cli", "_run_requires_ast_workflow"}
_PTH_SITES = {
    "_search_args_include_guarded_broad_root",
    "_search_paths_include_generated_root",
    "_search_paths_include_oversized_implicit_root",
    "_search_paths_include_workspace_root",
    "_names_stdin_path",
}
_PARSED_SITES = {
    "regex_patterns",
    "engine_selects_pcre2",
    "explicit_rg_format",
    "strip_noop_rg_format",
}
_V_SITES = {
    "_short_value_pos",
    "_consumes_next_arg",
    "_parse",
    "_flags",
    "_cancelled_by",
    "_first_dash_led_pattern_index_after_tg_flags",
    "_first_dash_led_positional_index",
    "_is_plausible_rg_flag_token",
    "_exec_capable_flag_present",
    "end_of_options_index",
    "flag_present",
}
_ALLOWED_SITES = _T_SITES | _R_SITES | _X_SITES | _PTH_SITES | _PARSED_SITES | _V_SITES


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dashy(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("-")
    )


def _dashy_collection(node: ast.AST) -> bool:
    """Any (possibly nested, or wrapped in ``frozenset({...})``) collection holding a ``-``-led
    string."""
    return any(_dashy(n) for n in ast.walk(node) if isinstance(n, ast.Constant))


def _flag_collection_names(tree: ast.Module) -> set[str]:
    """Names bound anywhere (module or local) to a flag collection: ``unsupported_flags``,
    ``_TG_ONLY_SEARCH_FLAGS``, ``_SCAN_FULL_CLI_FLAGS``, ``_JSON_INCOMPATIBLE_RENDER_FLAGS``,
    ``_TG_ONLY_SEARCH_FLAG_PREFIXES``..."""
    names: set[str] = set()
    for node in ast.walk(tree):
        value = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        if value is None or not isinstance(value, (ast.Set, ast.List, ast.Tuple, ast.Call)):
            continue
        if isinstance(value, ast.Call) and _name_of(value.func) not in {
            "frozenset",
            "set",
            "tuple",
            "list",
        }:
            continue
        if _dashy_collection(value):
            names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names


def census_sites(source: str) -> list[tuple[str, str]]:
    """Flags, by enclosing function name:
    * ``X in Y`` / ``X not in Y`` where Y is named ``_SEARCH_*``, OR a name BOUND to a flag
      collection, OR a set/list/tuple LITERAL holding a ``-``-led string, OR X is a ``-``-led
      string literal;
    * ``==`` / ``!=`` against a ``-``-led string literal (``arg == "--format"``);
    * ``.startswith(...)`` / ``.index(...)`` / ``.count(...)`` given a ``-``-led literal, a tuple
      of them, an f-string, or a flag-collection name."""
    tree = ast.parse(source)
    bound = _flag_collection_names(tree)
    hits: list[tuple[str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _add(self, kind: str) -> None:
            hits.append((self.stack[-1] if self.stack else "<module>", kind))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Compare(self, node: ast.Compare) -> None:
            operands = [node.left, *node.comparators]
            for op, x, y in zip(node.ops, operands[:-1], operands[1:], strict=True):
                if isinstance(op, (ast.In, ast.NotIn)):
                    if (_name_of(y) or "").startswith("_SEARCH_") or _name_of(y) in bound:
                        self._add("named")
                    elif _dashy(x):
                        self._add("x-literal")
                    elif isinstance(y, (ast.Set, ast.List, ast.Tuple)) and any(
                        _dashy(e) for e in y.elts
                    ):
                        self._add("collection")
                elif isinstance(op, (ast.Eq, ast.NotEq)) and (_dashy(x) or _dashy(y)):
                    self._add("eq-literal")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "startswith",
                "index",
                "count",
            }:
                for arg in node.args:
                    if (
                        _dashy(arg)
                        or _name_of(arg) in bound
                        or isinstance(arg, ast.JoinedStr)
                        or (isinstance(arg, ast.Tuple) and any(_dashy(e) for e in arg.elts))
                    ):
                        self._add(node.func.attr)
                        break
            self.generic_visit(node)

    Visitor().visit(tree)
    return hits


def census_violations(source: str) -> list[tuple[str, str]]:
    return [h for h in census_sites(source) if h[0] not in _ALLOWED_SITES]


def test_census_finds_no_p_or_s_shaped_site_outside_the_named_functions():
    for module_file in _MODULE_FILES:
        assert census_violations(_cli_source(module_file)) == [], module_file


def test_census_pins_the_classification_by_function_name_not_line_number():
    names = {fn for fn, _ in census_sites(_cli_source("bootstrap.py"))}
    assert names <= _ALLOWED_SITES
    assert "_requires_full_cli" in names and "_requires_full_cli" not in _T_SITES  # round 39 (b)
    # every class is non-empty in the live tree, so a class cannot silently rot into a wildcard
    live = names | {
        fn for module_file in _MODULE_FILES[1:] for fn, _ in census_sites(_cli_source(module_file))
    }
    for cls in (_T_SITES, _R_SITES, _X_SITES, _PTH_SITES, _PARSED_SITES, _V_SITES):
        assert cls & live, cls


def test_every_allow_listed_function_still_exists():
    live = set()
    for module_file in _MODULE_FILES:
        tree = ast.parse(_cli_source(module_file))
        live |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert _ALLOWED_SITES <= live


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ('def f(a):\n    return any(arg in {"-F"} for arg in a)\n', True),
        ('def _print_version(a):\n    return any(arg in {"-v"} for arg in a)\n', False),
        ('def f(a):\n    return "--json" in a\n', True),
        ('def f(a):\n    return "--" in a\n', True),
        ('def _consumes_next_arg(arg):\n    return arg in {"-e"}\n', False),
        ("def f(a):\n    return any(x in _SEARCH_LITERAL_FLAGS for x in a)\n", True),
        ("def f(a):\n    return len(a) > 2\n", False),
        # round 39(b): a raw help-scan in a function that is not allow-listed IS flagged.
        ('def _scan_help(a):\n    return any(x in {"--help", "-h"} for x in a)\n', True),
        # round 40 widening: each gap the plan matcher missed
        ('def f(a):\n    return a == "--json"\n', True),
        ('def f(a):\n    return any(x != "--format" for x in a)\n', True),
        ('def f(a):\n    local = {"-x", "-y"}\n    return any(t in local for t in a)\n', True),
        ('_BAD = ("--a", "--b")\ndef f(a):\n    return any(t in _BAD for t in a)\n', True),
        ('def f(a):\n    return a[0].startswith("--rank")\n', True),
        ('_P = ("--x=",)\ndef f(a):\n    return a.startswith(_P)\n', True),
        ('def f(a, flag):\n    return a.startswith(f"{flag}=")\n', True),
        ('def f(a):\n    return a.index("--")\n', True),
        ('def _requires_full_cli(a):\n    return any(x in {"--help", "-h"} for x in a)\n', False),
        ('def _scan_requires_full_cli(a):\n    return a == "--json"\n', False),
        ('def f(a):\n    return a == "plain"\n', False),
    ],
)
def test_census_matcher_controls(source, flagged):
    assert bool(census_violations(source)) is flagged


def test_requires_full_cli_help_scan_is_flagged_if_it_regresses_to_a_raw_membership_test():
    src = 'def _scan_help(a):\n    return any(x in {"--help", "-h"} for x in a)\n'
    assert census_violations(src) == [("_scan_help", "collection")]


# --- r40: the guarded-broad-root guard reads the tokenizer's PATH list, not raw argv ---------------


def test_broad_root_ignores_a_flag_value_that_looks_like_a_root():
    # `-g .claude` is a glob VALUE, `foo` the pattern, `src` the only path.
    assert (
        bootstrap._search_args_include_guarded_broad_root(["-g", ".claude", "foo", "src"]) is False
    )


def test_broad_root_still_refuses_a_genuine_guarded_path():  # positive control
    assert bootstrap._search_args_include_guarded_broad_root(["foo", ".claude"]) is True
    assert (
        bootstrap._search_args_include_guarded_broad_root(["-g", "*.py", "foo", ".claude"]) is True
    )


def test_broad_root_pattern_with_default_scope_is_not_a_root():
    assert bootstrap._search_args_include_guarded_broad_root([".claude"]) is False
    assert bootstrap._search_args_include_guarded_broad_root(["-e", ".claude"]) is False
