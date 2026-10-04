"""Gate: a test must not shell out to a binary CI does not install without a skip guard.

RECEIPT. Commit 00695cd added ``tests/unit/test_gitleaks_scan_completeness.py``, which ran
``gitleaks`` unguarded. ``test-python`` (ci.yml) does not install gitleaks, so every lane raised
``FileNotFoundError`` and main was red for four pushes (removed in f5f55ee).

WHAT IT CHECKS. An AST scan of every ``tests/**/*.py`` (CI runs ``pytest tests``; ``tests/fixtures``
is skipped) for ``subprocess.*`` / ``os.system`` calls whose argv[0] is a string LITERAL naming a
binary outside ``CI_PROVIDED_BINARIES``. ``subprocess``/``os`` import aliases, ``from subprocess
import run`` and ``args=`` keywords are resolved; ``.exe`` suffixes are stripped.

GUARDS ARE BOUND TO THE BINARY. A guard counts only if it is in the call's enclosing function
(incl. decorators), enclosing class (decorators and non-method statements only), or module top
level, and is one of: ``shutil.which("<same literal>")``; a ``skipif`` whose condition contains that
``which`` call or the binary name as a string; an unconditional ``pytest.skip(...)`` /
``mark.skip`` / truthy-constant ``skipif`` (``skipif(False)`` is NOT a guard); or, ONLY for OS-native binaries
(``_OS_NATIVE_BINARIES``), a platform check (``sys.platform`` / ``os.name`` /
``platform.system``). A platform check or an unrelated ``which`` does not exempt ``gitleaks``. A ``pytest.skip(...)`` inside
an ``if`` counts only when that ``if`` test binds the binary (or, for OS-native binaries, is a
platform check), or inside ``except FileNotFoundError/OSError``.

A guard-less helper is accepted only when it is never referenced except as a direct call target and
EVERY call site in the module is guarded (one level).

KNOWN LIMITS (do not read a green as more than this). A non-literal argv[0] (``[exe, ...]``,
``[_bin(), ...]``, ``shutil.which(var)``) is invisible to / does not bind in the scan; the guard
check is lexical, so it proves a guard EXISTS nearby, not that it dominates the call; a helper chain
deeper than one level is flagged. A ``shutil.which("<binary>")`` call ANYWHERE in scope suffices on
its own, regardless of branch or polarity (``if which("x"): ...`` guards just as well as
``if which("x") is None: return``) -- the scan does not check what the code does with the answer.
Not recognised as shell-outs at all: ``asyncio.create_subprocess_*``,
``os.exec*``, ``os.popen``, ``subprocess.getoutput`` / ``getstatusoutput``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"

# Binaries a CI test lane provides. Trimmed to what the test tree actually shells out to; anything
# else (gitleaks, curl, rg, ast-grep, sg, node, docker, wsl, cargo, ...) needs a guard bound to it.
# NOTE: `native-build-smoke` (ci.yml) runs a unit test with plain pip+pytest, WITHOUT uv or tg --
# the allowlist describes `test-python`, the lane that runs the whole tree.
CI_PROVIDED_BINARIES = frozenset({
    # Preinstalled on every GitHub-hosted runner image and required by actions/checkout.
    "git",
    # ci.yml test-python "Install Dependencies": `uv pip install -e ".[dev,ast]"` creates the
    # `tg` console script ([project.scripts] tg in pyproject.toml) in the venv.
    "tg",
    # ci.yml test-python: `python -m pip install uv==...` then `uv python install <ver>`.
    "python",
    "uv",
})

# OS-inbox binaries: a platform check is a sufficient guard for these (and only these).
_OS_NATIVE_BINARIES = frozenset({"cmd", "icacls", "powershell", "reg"})

_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
_PLATFORM_CHECKS = frozenset({("sys", "platform"), ("os", "name"), ("platform", "system")})
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _norm(name: str) -> str:
    base = re.split(r"[\\/]", name.strip())[-1].lower()
    return base[:-4] if base.endswith(".exe") else base


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _Aliases:
    """Names bound to the subprocess / os modules and directly imported run/system functions."""

    def __init__(self, tree: ast.AST) -> None:
        self.sub_mods = {"subprocess"}
        self.os_mods = {"os"}
        self.sub_funcs: set[str] = set()
        self.system_funcs: set[str] = set()
        self.pytest_mods = {"pytest"}
        self.pytest_skip_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "subprocess":
                        self.sub_mods.add(a.asname or a.name)
                    elif a.name == "os":
                        self.os_mods.add(a.asname or a.name)
                    elif a.name == "pytest":
                        self.pytest_mods.add(a.asname or a.name)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if node.module == "subprocess" and a.name in _SUBPROCESS_FUNCS:
                        self.sub_funcs.add(a.asname or a.name)
                    elif node.module == "os" and a.name == "system":
                        self.system_funcs.add(a.asname or a.name)
                    elif node.module == "pytest" and a.name == "skip":
                        self.pytest_skip_names.add(a.asname or a.name)
        # a locally DEFINED `def skip` is not pytest's
        for node in ast.walk(tree):
            if isinstance(node, _FUNC_TYPES):
                self.pytest_skip_names.discard(node.name)


def _literal_binary(call: ast.Call, al: _Aliases) -> str | None:
    """Normalized argv[0] when ``call`` is a subprocess/os.system call with a literal command."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner, name = func.value.id, func.attr
        hit = (owner in al.sub_mods and name in _SUBPROCESS_FUNCS) or (
            owner in al.os_mods and name == "system"
        )
    elif isinstance(func, ast.Name):
        hit = func.id in al.sub_funcs or func.id in al.system_funcs
    else:
        hit = False
    if not hit:
        return None
    arg: ast.AST | None = call.args[0] if call.args else None
    if arg is None:
        for kw in call.keywords:
            if kw.arg in {"args", "command"}:
                arg = kw.value
    text = _str_const(arg)
    if text is not None:
        parts = text.split()
        return _norm(parts[0]) if parts else None
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        first = _str_const(arg.elts[0])
        if first is not None:
            return _norm(first)
    return None


def _is_which(call: ast.Call) -> bool:
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "which") or (
        isinstance(f, ast.Name) and f.id == "which"
    )


def _which_binds(call: ast.Call, binary: str) -> bool:
    lit = _str_const(call.args[0]) if call.args else None
    return lit is not None and _norm(lit) == binary


def _cond_binds(cond: ast.AST | None, binary: str) -> bool:
    """Does a skipif condition mention ``binary`` (a bound which() call or the name as a string)?"""
    if cond is None:
        return False
    if isinstance(cond, ast.Constant) and not isinstance(cond.value, str):
        return bool(cond.value)  # only a TRUTHY constant skips; skipif(False/0/None) never does
    for n in ast.walk(cond):
        if isinstance(n, ast.Call) and _is_which(n) and _which_binds(n, binary):
            return True
        lit = _str_const(n)
        if lit is not None and _norm(lit) == binary:
            return True
    return False


def _is_platform_test(test: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and (n.value.id, n.attr) in _PLATFORM_CHECKS
        for n in ast.walk(test)
    )


def _skip_is_effective(
    call: ast.Call,
    root_ids: set[int],
    parents: dict[ast.AST, ast.AST],
    binary: str,
) -> bool:
    """A skip(...) call guards ``binary`` only if it is unconditional or its condition binds."""
    cur: ast.AST | None = call
    nearest_cond: ast.AST | None = None
    while cur is not None:
        cur = parents.get(cur)
        if cur is None:
            break
        if id(cur) not in root_ids and isinstance(cur, (*_FUNC_TYPES, ast.Lambda)):
            return False  # skip lives in a nested def/lambda that may never run
        if isinstance(cur, ast.ExceptHandler) and nearest_cond is None:
            names = {getattr(n, "id", None) for n in ast.walk(cur.type)} if cur.type else set()
            if names & {"FileNotFoundError", "OSError"}:
                return True
        if nearest_cond is None and isinstance(cur, (ast.If, ast.IfExp, ast.While)):
            nearest_cond = cur.test
        if id(cur) in root_ids:
            break
    if nearest_cond is None:
        return True
    if _cond_binds(nearest_cond, binary):
        return True
    return binary in _OS_NATIVE_BINARIES and _is_platform_test(nearest_cond)


def _has_guard(
    nodes: Sequence[ast.AST],
    binary: str,
    al: _Aliases,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    root_ids = {id(n) for n in nodes}
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Attribute):
                if (
                    binary in _OS_NATIVE_BINARIES
                    and isinstance(node.value, ast.Name)
                    and (node.value.id, node.attr) in _PLATFORM_CHECKS
                ):
                    return True
                # bare `@pytest.mark.skip` decorator / mark expression (no call)
                if (
                    node.attr == "skip"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "mark"
                    and not isinstance(parents.get(node), ast.Call)
                ):
                    return True
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if _is_which(node) and _which_binds(node, binary):
                return True
            is_skip = (
                isinstance(f, ast.Attribute)
                and f.attr == "skip"
                and (
                    (isinstance(f.value, ast.Name) and f.value.id in al.pytest_mods)
                    or (isinstance(f.value, ast.Attribute) and f.value.attr == "mark")
                )
            ) or (isinstance(f, ast.Name) and f.id in al.pytest_skip_names)
            if is_skip and _skip_is_effective(node, root_ids, parents, binary):
                return True
            if isinstance(f, ast.Attribute) and f.attr == "skipif":
                cond = node.args[0] if node.args else None
                for kw in node.keywords:
                    if kw.arg == "condition":
                        cond = kw.value
                if _cond_binds(cond, binary):
                    return True
    return False


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes of a scope that can guard calls inside it (a class excludes its sibling methods)."""
    if isinstance(scope, ast.ClassDef):
        return [
            *scope.decorator_list,
            *(n for n in scope.body if not isinstance(n, (*_FUNC_TYPES, ast.ClassDef))),
        ]
    return [scope]


def find_unguarded_external_binaries(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, binary)`` for each unguarded call to a non-CI-provided binary."""
    tree = ast.parse(source)
    aliases = _Aliases(tree)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    top_level = [n for n in tree.body if not isinstance(n, (*_FUNC_TYPES, ast.ClassDef))]
    candidates: list[tuple[int, str, str | None, list[ast.AST]]] = []
    call_sites: dict[str, list[list[ast.AST]]] = {}
    escaped: set[str] = set()  # names referenced other than as a direct call target

    def guarded(scopes: list[ast.AST], binary: str) -> bool:
        nodes = [*top_level, *(n for sc in scopes for n in _scope_nodes(sc))]
        return _has_guard(nodes, binary, aliases, parents)

    def visit(node: ast.AST, scopes: list[ast.AST]) -> None:
        skip_child: ast.AST | None = None
        if isinstance(node, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name):
                call_sites.setdefault(callee.id, []).append(scopes)
                skip_child = callee
            elif isinstance(callee, ast.Attribute):
                call_sites.setdefault(callee.attr, []).append(scopes)
                skip_child = callee
            binary = _literal_binary(node, aliases)
            if binary is not None and binary not in CI_PROVIDED_BINARIES:
                funcs = [sc for sc in scopes if isinstance(sc, _FUNC_TYPES)]
                candidates.append((node.lineno, binary, funcs[-1].name if funcs else None, scopes))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            escaped.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            escaped.add(node.attr)
        child_scopes = scopes
        if isinstance(node, (*_FUNC_TYPES, ast.ClassDef)):
            child_scopes = [*scopes, node]
        for child in ast.iter_child_nodes(node):
            if child is skip_child:
                # a direct call target is not an escape; still descend into `a.b.c()` receivers
                if isinstance(child, ast.Attribute):
                    visit(child.value, scopes)
                continue
            visit(child, child_scopes)

    for top in tree.body:
        visit(top, [])

    offenders: list[tuple[int, str]] = []
    for lineno, binary, func, scopes in candidates:
        if guarded(scopes, binary):
            continue
        sites = call_sites.get(func, []) if func else []
        if func and func not in escaped and sites and all(guarded(s, binary) for s in sites):
            continue  # guard-less helper, every caller guarded, never referenced otherwise
        offenders.append((lineno, binary))
    return offenders


# --- controls -------------------------------------------------------------------------------

_UNGUARDED = """
import subprocess

def test_scan():
    subprocess.run(["gitleaks", "git", "."], check=True)
"""

_GUARDED_WHICH = """
import shutil, subprocess

def test_scan():
    if shutil.which("gitleaks") is None:
        return
    subprocess.run(["gitleaks", "git", "."], check=True)
"""

_GUARDED_SKIPIF = """
import shutil, subprocess
import pytest

@pytest.mark.skipif(shutil.which("curl") is None, reason="no curl")
def test_fetch():
    subprocess.run(["curl", "-s", "x"], check=True)
"""

_GUARDED_MODULE = """
import subprocess
import pytest

pytestmark = pytest.mark.skipif(True, reason="x")

def test_fetch():
    subprocess.check_output(["docker", "ps"])
"""

_ALLOWED = """
import subprocess

def test_git():
    subprocess.run(["git", "init"], check=True)
"""

_HELPER_GUARDED_CALLERS = """
import subprocess
import pytest

def _run(*args):
    return subprocess.run(["icacls", *args])

@pytest.mark.skipif(True, reason="x")
def test_a():
    _run("a")
"""

_HELPER_UNGUARDED_CALLER = """
import subprocess
import pytest

def _run(*args):
    return subprocess.run(["icacls", *args])

@pytest.mark.skipif(True, reason="x")
def test_a():
    _run("a")

def test_b():
    _run("b")
"""

_OS_SYSTEM = """
import os

def test_it():
    os.system("rg foo")
"""

_PLATFORM_ONLY_WRONG_BINARY = """
import subprocess, sys
import pytest

@pytest.mark.skipif(sys.platform == "win32", reason="x")
def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_WHICH_OTHER_BINARY = """
import shutil, subprocess

def test_a():
    if shutil.which("rg") is None:
        return
    subprocess.run(["gitleaks", "version"])
"""

_MODULE_WHICH_UNRELATED = """
import shutil, subprocess

GIT = shutil.which("git")

def test_a():
    subprocess.run(["gitleaks", "version"])
"""

# Reduced copy of `git show 00695cd:tests/unit/test_gitleaks_scan_completeness.py`.
_RECEIPT_00695CD = """
import subprocess
import tempfile
from pathlib import Path

def test_gitleaks_scan_includes_merge_commits():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        result = subprocess.run(
            ["gitleaks", "detect", "--source", "git", "--json"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result is not None
"""

_ALIAS_MODULE = """
import subprocess as sp

def test_a():
    sp.run(["gitleaks", "version"])
"""

_ALIAS_FROM = """
from subprocess import run as r

def test_a():
    r(["gitleaks", "version"])
"""

_ARGS_KEYWORD = """
import subprocess

def test_a():
    subprocess.run(args=["gitleaks", "version"])
"""

_EXE_SUFFIX = """
import subprocess

def test_a():
    subprocess.run(["gitleaks.exe", "version"])
"""

_GIT_EXE_ALLOWED = """
import subprocess

def test_a():
    subprocess.run(["git.exe", "init"])
"""

_HELPER_ESCAPES = """
import subprocess
import pytest

def _run(*args):
    return subprocess.run(["icacls", *args])

@pytest.mark.skipif(True, reason="x")
def test_a():
    _run("a")
    list(map(_run, ["b"]))
"""

_SIBLING_GUARD = """
import shutil, subprocess

class TestX:
    def test_guarded(self):
        if shutil.which("gitleaks") is None:
            return

    def test_unguarded(self):
        subprocess.run(["gitleaks", "version"])
"""

_PLATFORM_OS_NATIVE_OK = """
import subprocess, sys
import pytest

@pytest.mark.skipif(sys.platform != "win32", reason="icacls is Windows-only")
def test_a():
    subprocess.run(["icacls", "x"])
"""

_SKIPIF_WHICH_OK = """
import shutil, subprocess
import pytest

@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="no gitleaks")
def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_UNCONDITIONAL_SKIP_OK = """
import subprocess
import pytest

def test_a():
    pytest.skip("not available in CI")
    subprocess.run(["gitleaks", "version"])
"""


_SKIPIF_FALSE_DECORATOR = """
import subprocess
import pytest

@pytest.mark.skipif(False, reason="x")
def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_SKIPIF_FALSE_KW = """
import subprocess
import pytest

@pytest.mark.skipif(condition=False, reason="x")
def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_SKIPIF_FALSE_MODULE = """
import subprocess
import pytest

pytestmark = pytest.mark.skipif(False, reason="x")

def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_INLINE_PLATFORM_SKIP = """
import subprocess, sys
import pytest

def test_a():
    if sys.platform == "win32":
        pytest.skip("x")
    subprocess.run(["gitleaks", "version"])
"""

_INLINE_ENV_SKIP = """
import os, subprocess
import pytest

def test_a():
    if not os.environ.get("CI"):
        pytest.skip("x")
    subprocess.run(["gitleaks", "version"])
"""

_MODULE_PLATFORM_SKIP = """
import subprocess, sys
import pytest

if sys.platform == "win32":
    pytest.skip("x", allow_module_level=True)

def test_a():
    subprocess.run(["gitleaks", "version"])
"""

_INLINE_WHICH_SKIP_OK = """
import shutil, subprocess
import pytest

def test_a():
    if shutil.which("gitleaks") is None:
        pytest.skip("no gitleaks")
    subprocess.run(["gitleaks", "version"])
"""

_INLINE_PLATFORM_SKIP_NATIVE_OK = """
import subprocess, sys
import pytest

def test_a():
    if sys.platform != "win32":
        pytest.skip("windows only")
    subprocess.run(["icacls", "x"])
"""

_EXCEPT_FNF_SKIP_OK = """
import subprocess
import pytest

def test_a():
    try:
        subprocess.run(["gitleaks", "version"])
    except FileNotFoundError:
        pytest.skip("no gitleaks")
"""

_LOCAL_SKIP_DEF = """
import subprocess

def skip(*a):
    return None

def test_a():
    skip("x")
    subprocess.run(["gitleaks", "version"])
"""

_UNRELATED_ATTR_SKIP = """
import subprocess

def test_a():
    logger.skip("x")
    subprocess.run(["gitleaks", "version"])
"""


_EXCEPT_THEN_NONBINDING_IF = """
import os, subprocess
import pytest

def test_a():
    try:
        subprocess.run(["git", "init"])
    except OSError:
        if os.environ.get("X"):
            pytest.skip("x")
    subprocess.run(["gitleaks", "version"])
"""

_EXCEPT_NEARER_NONBINDING_IF_AROUND_CALL = """
import os, subprocess
import pytest

def test_a():
    try:
        pass
    except OSError:
        if os.environ.get("X"):
            pytest.skip("x")
        subprocess.run(["gitleaks", "version"])
"""

_NESTED_DEF_SKIP = """
import subprocess
import pytest

def test_a():
    def _never():
        pytest.skip("x")
    subprocess.run(["gitleaks", "version"])
"""

_LAMBDA_SKIP = """
import subprocess
import pytest

def test_a():
    f = lambda: pytest.skip("x")
    subprocess.run(["gitleaks", "version"])
"""


def _bins(src: str) -> list[str]:
    return [b for _, b in find_unguarded_external_binaries(src)]


def test_positive_control_unguarded_binary_is_flagged() -> None:
    assert _bins(_UNGUARDED) == ["gitleaks"]
    assert _bins(_OS_SYSTEM) == ["rg"]
    # a helper with even one unguarded caller is still flagged
    assert _bins(_HELPER_UNGUARDED_CALLER) == ["icacls"]


def test_negative_control_guarded_or_allowlisted_passes() -> None:
    for src in (
        _GUARDED_WHICH,
        _GUARDED_SKIPIF,
        _GUARDED_MODULE,
        _ALLOWED,
        _HELPER_GUARDED_CALLERS,
    ):
        assert _bins(src) == [], src


def test_guard_must_bind_to_the_binary() -> None:
    assert _bins(_PLATFORM_ONLY_WRONG_BINARY) == ["gitleaks"]
    assert _bins(_WHICH_OTHER_BINARY) == ["gitleaks"]
    assert _bins(_MODULE_WHICH_UNRELATED) == ["gitleaks"]
    assert _bins(_SIBLING_GUARD) == ["gitleaks"]
    for ok in (_PLATFORM_OS_NATIVE_OK, _SKIPIF_WHICH_OK, _UNCONDITIONAL_SKIP_OK):
        assert _bins(ok) == [], ok


def test_falsy_skipif_and_conditional_skips_are_not_guards() -> None:
    for src in (
        _SKIPIF_FALSE_DECORATOR,
        _SKIPIF_FALSE_KW,
        _SKIPIF_FALSE_MODULE,
        _INLINE_PLATFORM_SKIP,
        _INLINE_ENV_SKIP,
        _MODULE_PLATFORM_SKIP,
        _LOCAL_SKIP_DEF,
        _UNRELATED_ATTR_SKIP,
        _EXCEPT_THEN_NONBINDING_IF,
        _EXCEPT_NEARER_NONBINDING_IF_AROUND_CALL,
        _NESTED_DEF_SKIP,
        _LAMBDA_SKIP,
    ):
        assert _bins(src) == ["gitleaks"], src
    for ok in (_INLINE_WHICH_SKIP_OK, _INLINE_PLATFORM_SKIP_NATIVE_OK, _EXCEPT_FNF_SKIP_OK):
        assert _bins(ok) == [], ok


def test_receipt_00695cd_shape_is_flagged() -> None:
    assert _bins(_RECEIPT_00695CD) == ["gitleaks"]


def test_import_aliases_args_keyword_and_exe_suffix_are_resolved() -> None:
    for src in (_ALIAS_MODULE, _ALIAS_FROM, _ARGS_KEYWORD, _EXE_SUFFIX):
        assert _bins(src) == ["gitleaks"], src
    assert _bins(_GIT_EXE_ALLOWED) == []


def test_helper_referenced_other_than_by_direct_call_is_unguarded() -> None:
    assert _bins(_HELPER_ESCAPES) == ["icacls"]


def _scanned_files() -> list[Path]:
    fixtures = TESTS_DIR / "fixtures"
    return [
        p
        for p in sorted(TESTS_DIR.rglob("*.py"))
        if fixtures not in p.parents and p.resolve() != Path(__file__).resolve()
    ]


def test_no_test_shells_out_to_a_binary_ci_lacks_without_a_guard() -> None:
    files = _scanned_files()
    assert len(files) >= 400, f"vacuity floor: only {len(files)} test files scanned"
    problems: list[str] = []
    for path in files:
        for lineno, binary in find_unguarded_external_binaries(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {binary!r}")
    assert not problems, (
        "tests shell out to binaries CI does not install, with no guard bound to that binary "
        "(add shutil.which('<bin>')/pytest.skip/skipif, or extend CI_PROVIDED_BINARIES citing "
        "ci.yml):\n" + "\n".join(problems)
    )
