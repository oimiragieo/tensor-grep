"""Gate: a unit test must not shell out to a binary CI does not install without a skip guard.

RECEIPT. Commit 00695cd added ``tests/unit/test_gitleaks_scan_completeness.py``, which ran
``gitleaks`` unguarded. ``test-python`` (ci.yml) does not install gitleaks, so every lane raised
``FileNotFoundError`` and main was red for four pushes (removed in f5f55ee).

WHAT IT CHECKS. An AST scan of ``tests/unit/*.py`` for ``subprocess.*`` / ``os.system`` calls whose
argv[0] is a string LITERAL naming a binary outside ``CI_PROVIDED_BINARIES``. Each such call needs a
guard in its enclosing function (incl. decorators), enclosing class, or module top level: a
``pytest.skip`` / ``importorskip`` / ``skipif`` / ``mark.skip``, a ``shutil.which`` probe, or a
platform check (``sys.platform`` / ``os.name`` / ``platform.system``).

A guard-less helper is accepted when every call site of it in the same module is guarded (one
level). KNOWN LIMITS (do not read a green as more than this). A non-literal argv[0] (``[exe, ...]``,
``[_bin(), ...]``) is invisible to the scan; the guard test is lexical, so it proves a guard EXISTS
nearby, not that it dominates the call. A helper chain deeper than one level, or a helper with any
unguarded caller, is flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT_DIR = REPO_ROOT / "tests" / "unit"

# Binaries the `test-python` lane of .github/workflows/ci.yml provides on every matrix OS.
# Anything else (gitleaks, curl, rg, ast-grep, sg, node, docker, wsl, ...) needs a skip guard.
CI_PROVIDED_BINARIES = frozenset({
    # Preinstalled on every GitHub-hosted runner image and required by actions/checkout.
    "git",
    # ci.yml test-python "Install Dependencies": `uv pip install -e ".[dev,ast]"` creates the
    # `tg` console script ([project.scripts] tg in pyproject.toml) in the venv.
    "tg",
    # ci.yml test-python: `python -m pip install uv==...` then `uv python install <ver>`.
    "python",
    "python3",
    "uv",
    # ci.yml test-python "Install Rust dependencies (for PyO3 fallback)": `rustup default 1.96.0`
    # then `rustc --version` / `cargo --version`.
    "cargo",
    "rustc",
    "rustup",
})

_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
_GUARD_ATTRS = frozenset({"skip", "importorskip", "skipif", "which"})
_GUARD_NAMES = frozenset({"importorskip", "which"})
_PLATFORM_CHECKS = frozenset({("sys", "platform"), ("os", "name"), ("platform", "system")})


def _literal_binary(call: ast.Call) -> str | None:
    """argv[0] when the call is a subprocess/os.system call with a literal command, else None."""
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    owner, name = func.value.id, func.attr
    is_sub = owner == "subprocess" and name in _SUBPROCESS_FUNCS
    is_system = owner == "os" and name == "system"
    if not (is_sub or is_system) or not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        parts = arg.value.split()
        return Path(parts[0]).name if parts else None
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        first = arg.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return Path(first.value).name
    return None


def _has_guard(nodes: list[ast.AST]) -> bool:
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Attribute):
                if node.attr in _GUARD_ATTRS:
                    return True
                if (
                    isinstance(node.value, ast.Name)
                    and (node.value.id, node.attr) in _PLATFORM_CHECKS
                ):
                    return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _GUARD_NAMES:
                    return True
    return False


def find_unguarded_external_binaries(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, binary)`` for each unguarded call to a non-CI-provided binary."""
    tree = ast.parse(source)
    top_level = [
        n
        for n in tree.body
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    module_guarded = _has_guard(top_level)
    # (lineno, binary, innermost enclosing function name)
    candidates: list[tuple[int, str, str | None]] = []
    # function name -> guarded? for every call site of that name in the module
    call_sites: dict[str, list[bool]] = {}

    def visit(node: ast.AST, scopes: list[ast.AST]) -> None:
        if isinstance(node, ast.Call):
            guarded = module_guarded or _has_guard(scopes)
            callee = node.func
            if isinstance(callee, ast.Name):
                call_sites.setdefault(callee.id, []).append(guarded)
            elif isinstance(callee, ast.Attribute):
                call_sites.setdefault(callee.attr, []).append(guarded)
            binary = _literal_binary(node)
            if binary is not None and binary not in CI_PROVIDED_BINARIES and not guarded:
                funcs = [
                    sc for sc in scopes if isinstance(sc, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                candidates.append((node.lineno, binary, funcs[-1].name if funcs else None))
        child_scopes = scopes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            child_scopes = [*scopes, node]
        for child in ast.iter_child_nodes(node):
            visit(child, child_scopes)

    for top in tree.body:
        visit(top, [])
    # A helper with no guard of its own is accepted when it is called, and EVERY call site is
    # itself guarded (one level; a helper chain or an unguarded caller is still flagged).
    return [
        (lineno, binary)
        for lineno, binary, func in candidates
        if not (func and call_sites.get(func) and all(call_sites[func]))
    ]


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


def test_positive_control_unguarded_binary_is_flagged() -> None:
    assert [b for _, b in find_unguarded_external_binaries(_UNGUARDED)] == ["gitleaks"]
    assert [b for _, b in find_unguarded_external_binaries(_OS_SYSTEM)] == ["rg"]
    # a helper with even one unguarded caller is still flagged
    assert [b for _, b in find_unguarded_external_binaries(_HELPER_UNGUARDED_CALLER)] == ["icacls"]


def test_negative_control_guarded_or_allowlisted_passes() -> None:
    for src in (
        _GUARDED_WHICH,
        _GUARDED_SKIPIF,
        _GUARDED_MODULE,
        _ALLOWED,
        _HELPER_GUARDED_CALLERS,
    ):
        assert find_unguarded_external_binaries(src) == [], src


def test_no_unit_test_shells_out_to_a_binary_ci_lacks_without_a_guard() -> None:
    problems: list[str] = []
    for path in sorted(UNIT_DIR.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for lineno, binary in find_unguarded_external_binaries(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: {binary!r}")
    assert not problems, (
        "unit tests shell out to binaries CI does not install, with no skip guard "
        "(add shutil.which/pytest.skip/skipif, or extend CI_PROVIDED_BINARIES citing ci.yml):\n"
        + "\n".join(problems)
    )
