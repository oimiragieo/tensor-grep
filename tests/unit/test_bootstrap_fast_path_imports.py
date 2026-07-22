"""perf/#48 regression: bootstrap.py's trivial fast paths (currently just ``tg --version`` /
``-V``, the only branch of ``main_entry`` that returns before ever calling ``_run_full_cli``,
native dispatch, or the search broad-scan guards) must not pay the import cost of
``tensor_grep.io.directory_scanner`` -- which itself transitively pulls in
``tensor_grep.core.config`` (and, via its ``from dataclasses import dataclass``, the stdlib
``dataclasses``/``inspect``/``ast``/``dis``/``tokenize`` chain). Those modules are only needed by
the plain-text-search unbounded-broad-scan guard (``_search_args_include_unbounded_broad_scan``
and the three helpers it calls: ``_path_has_project_marker``,
``_search_paths_include_workspace_root``, ``_search_paths_include_vendored_root``), which never
runs for ``--version``.

Measured on this machine (``python -X importtime -c "import tensor_grep.cli.bootstrap"``,
warm-cache): deferring this import saves ~25ms of the ~78ms total bootstrap import cost.

Subprocess-based, deliberately NOT an in-process ``sys.modules`` check: pytest's own process may
already have ``tensor_grep.io.directory_scanner`` (and ``tensor_grep.core.config``) loaded via
unrelated test collection in the same session (e.g. any test module that imports
``tensor_grep.cli.main``, which uses ``DirectoryScanner`` directly) -- that would make an
in-process assertion pass trivially regardless of whether bootstrap.py's OWN import graph still
pulls it in. A fresh subprocess guarantees a clean ``sys.modules`` baseline so this test actually
exercises what a real cold ``tg --version`` invocation pays for.

Note: ``tensor_grep.cli.runtime_paths`` (``resolve_native_tg_binary`` / ``resolve_ripgrep_binary``
/ ``env_flag_enabled``) is deliberately NOT deferred and is NOT asserted absent here -- ~90
existing tests in ``test_cli_bootstrap.py`` do
``monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", ...)``, which requires that name to
stay a directly-bound module attribute of ``bootstrap`` (pytest's ``monkeypatch.setattr`` raises
``AttributeError`` by default when the target attribute does not already exist, and a
function-local import would create a local binding that silently shadows any monkeypatched
module attribute instead of being intercepted by it). Deferring it would require rewriting that
entire mocking contract -- out of scope for this fast-path-import PR.

perf (+10% campaign #6, F2.4/F2.5/F2.6 follow-up): three more import-deferral moves, each pinned
below.

- F2.4: the 5 broad-scan-guard constants (``UNBOUNDED_VENDORED_ROOT_DIR_NAMES``,
  ``BROAD_WORKSPACE_PROJECT_MARKERS``, ``BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD``,
  ``BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD``, ``IMPLICIT_SEARCH_WALK_FILE_CEILING``) moved
  from ``tensor_grep.io.directory_scanner`` to the new zero-dependency ``tensor_grep.io.
  scan_limits`` module. The 3 guard helpers above (and ``cli/main.py``'s module-level broad-scan
  literals, and ``cli/scan_guardrails.py``) now import from ``scan_limits`` instead, so a real
  search invocation that reaches the guard no longer drags in ``SearchConfig``/``dataclasses``/
  ``inspect`` merely to read 5 plain constants -- even when the search is ultimately delegated to
  the native binary or ``rg`` (both of which do their own walk in a separate process and never
  touch Python's ``DirectoryScanner`` at all). ``directory_scanner`` (and hence ``core.config``)
  is still imported exactly when a real Python-side walk is genuinely needed -- e.g.
  ``_search_paths_include_oversized_implicit_root``, which constructs a real ``DirectoryScanner``,
  is deliberately UNCHANGED.
- F2.5: ``cli/runtime_paths.py``'s module-level ``import json`` / ``import shutil`` moved to
  function-local at their only call sites (``_read_native_frontdoor_metadata``,
  ``translate_path_for_windows_binary``, ``resolve_ripgrep_binary``). This module is imported
  EAGERLY by ``cli/bootstrap.py``'s own module level (for ``env_flag_enabled``,
  ``resolve_native_tg_binary``, ``resolve_ripgrep_binary``), so its top-level imports were paid on
  every invocation including ``--version`` -- which calls none of the three functions above.
- F2.6: ``cli/__init__.py``'s ``from typing import Any`` removed (the sole use, ``__getattr__``'s
  return annotation, is now the builtin ``object`` instead -- needs no import at all).
  ``tensor_grep.cli`` is the parent package of every ``tensor_grep.cli.*`` submodule, so this
  file's own import cost is paid before ANY submodule-specific code runs, on every invocation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")

# Kept as a single string (not a helper module) so the subprocess starts from a totally clean
# interpreter state -- no pytest plugins, no already-imported tensor_grep submodules. ``{setup}``
# is an optional extra statement block (e.g. a monkeypatch) injected AFTER importing bootstrap
# but BEFORE calling ``main_entry`` -- used by the oversized-single-root test below to force the
# "no native binary, no rg" branch deterministically instead of depending on the ambient dev
# machine's PATH (which may or may not resolve a real ``tg``/``rg``).
#
# The ``sys.modules`` snapshot (``_status``) is captured BEFORE this probe's OWN diagnostic
# ``import json`` runs -- serializing the result via ``json.dumps`` would otherwise contaminate
# the very ``json_loaded`` measurement it is trying to report (a probe-design bug caught by
# ``test_version_fast_path_does_not_import_json_or_shutil`` initially failing against a correct
# F2.5 fix: the PROBE, not the product, was the false positive).
_PROBE_TEMPLATE = """
import sys
sys.argv = {argv!r}
import tensor_grep.cli.bootstrap as bootstrap
{setup}
try:
    bootstrap.main_entry()
except SystemExit:
    pass
_status = {{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "core_config_loaded": "tensor_grep.core.config" in sys.modules,
    "scan_limits_loaded": "tensor_grep.io.scan_limits" in sys.modules,
    "json_loaded": "json" in sys.modules,
    "shutil_loaded": "shutil" in sys.modules,
}}
import json as _json
print(_json.dumps(_status))
"""


def _run_fast_path_probe(argv: list[str], *, setup: str = "") -> dict[str, bool]:
    probe_source = _PROBE_TEMPLATE.format(argv=argv, setup=setup)
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (argv={argv!r}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # main_entry() may print e.g. the version banner to stdout ahead of our JSON marker line.
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def test_version_fast_path_does_not_import_directory_scanner_or_core_config() -> None:
    status = _run_fast_path_probe(["tg", "--version"])
    assert status["directory_scanner_loaded"] is False, (
        "tg --version must not import tensor_grep.io.directory_scanner -- it is fast-path-unused "
        "(only the search broad-scan guard needs it) and costs ~25ms including its transitive "
        "tensor_grep.core.config/dataclasses/inspect pull"
    )
    assert status["core_config_loaded"] is False, (
        "tg --version must not import tensor_grep.core.config (pulled in transitively by "
        "directory_scanner's `from tensor_grep.core.config import SearchConfig`)"
    )


def test_short_version_flag_fast_path_does_not_import_directory_scanner() -> None:
    status = _run_fast_path_probe(["tg", "-V"])
    assert status["directory_scanner_loaded"] is False
    assert status["core_config_loaded"] is False


def test_version_fast_path_does_not_import_json_via_runtime_paths() -> None:
    """F2.5 (partial, end-to-end): ``cli/runtime_paths.py`` is imported eagerly by
    ``cli/bootstrap.py`` (for ``env_flag_enabled``/``resolve_native_tg_binary``/
    ``resolve_ripgrep_binary``), but ``--version`` calls none of the three -- so the stdlib
    ``json`` module ``_read_native_frontdoor_metadata`` needs must not load just because
    runtime_paths.py itself was imported.

    Deliberately does NOT also assert ``shutil_loaded is False`` here: verified via
    ``-X importtime`` that ``_print_version``'s OWN ``from importlib.metadata import version;
    version("tensor-grep")`` call pulls in ``shutil`` (plus ``csv``/``email``/``bz2``/``lzma``)
    through CPython's OWN package-metadata resolution machinery -- entirely unrelated to
    runtime_paths.py and outside this PR's scope. See
    ``test_runtime_paths_bare_import_does_not_pull_json_or_shutil`` below for the precise,
    uncontaminated claim: runtime_paths.py ITSELF no longer needs either module merely to import.
    """
    status = _run_fast_path_probe(["tg", "--version"])
    assert status["json_loaded"] is False, (
        "tg --version must not import the stdlib json module -- runtime_paths.py's only json "
        "user (_read_native_frontdoor_metadata) is unreachable from --version"
    )


def test_runtime_paths_bare_import_does_not_pull_json_or_shutil() -> None:
    """F2.5 (the precise claim): importing ``tensor_grep.cli.runtime_paths`` in total isolation
    -- calling none of its functions -- must not load the stdlib ``json``/``shutil`` modules.
    Both are now function-local at their only call sites (``_read_native_frontdoor_metadata``,
    ``translate_path_for_windows_binary``, ``resolve_ripgrep_binary``), so a bare import should
    cost neither."""
    probe_source = f"""
import sys
sys.path.insert(0, {_REPO_SRC!r})
import tensor_grep.cli.runtime_paths
_status = {{
    "json_loaded": "json" in sys.modules,
    "shutil_loaded": "shutil" in sys.modules,
}}
import json as _json
print(_json.dumps(_status))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["json_loaded"] is False, (
        "a bare `import tensor_grep.cli.runtime_paths` must not import the stdlib json module"
    )
    assert status["shutil_loaded"] is False, (
        "a bare `import tensor_grep.cli.runtime_paths` must not import the stdlib shutil module"
    )


def test_plain_search_still_imports_directory_scanner_when_actually_needed(
    tmp_path: Path,
) -> None:
    """Non-regression: the deferral must not make the broad-scan guard silently unavailable for
    a real search invocation, and ``directory_scanner`` (hence ``SearchConfig``) must still load
    exactly when a real Python-side walk is genuinely required.

    F2.4 update: before campaign #6, `directory_scanner` loaded UNCONDITIONALLY for any
    search-shaped argv (the guard helpers imported it just for 5 constants). After F2.4 it loads
    only when Python's own `DirectoryScanner` actually runs -- which does NOT happen when the
    search gets delegated to the native binary or `rg` (both walk in a separate process). This
    test therefore forces that "no native, no rg" branch explicitly (rather than relying on
    whether the CI/dev machine happens to have a real `tg`/`rg` on PATH) so the assertion is
    deterministic: `resolve_native_tg_binary`/`resolve_ripgrep_binary` are monkeypatched to `None`
    via the injected `setup` block, mirroring the exact pattern `test_cli_bootstrap.py` uses
    in-process (`monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", ...)`) -- just applied
    via source injection since this probe must run in a fresh subprocess.
    """
    status = _run_fast_path_probe(
        ["tg", "search", "needle", str(tmp_path)],
        setup=(
            "bootstrap.resolve_native_tg_binary = lambda: None\n"
            "bootstrap.resolve_ripgrep_binary = lambda: None"
        ),
    )
    assert status["directory_scanner_loaded"] is True


def test_search_broad_scan_guard_helpers_use_scan_limits_not_core_config(
    tmp_path: Path,
) -> None:
    """F2.4 (the core fix): the 3 broad-scan guard helpers (`_path_has_project_marker`,
    `_search_paths_include_workspace_root`, `_search_paths_include_vendored_root`) only need 5
    plain frozenset/int constants -- calling them directly must load the new zero-dependency
    `tensor_grep.io.scan_limits` module WITHOUT ever loading `tensor_grep.io.directory_scanner`
    or `tensor_grep.core.config` (and therefore never `SearchConfig`/`dataclasses`/`inspect`).
    This is the precise claim the ranked-queue item's "~36ms on explicit-path search" prediction
    rests on: the guard-only path, in isolation, must be genuinely `SearchConfig`-free."""
    probe_source = f"""
import sys
sys.path.insert(0, {_REPO_SRC!r})
import tensor_grep.cli.bootstrap as bootstrap
from pathlib import Path
root = Path({str(tmp_path)!r})
bootstrap._path_has_project_marker(root)
bootstrap._search_paths_include_workspace_root([str(root)])
bootstrap._search_paths_include_vendored_root([str(root)])
import json
print(json.dumps({{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "core_config_loaded": "tensor_grep.core.config" in sys.modules,
    "scan_limits_loaded": "tensor_grep.io.scan_limits" in sys.modules,
}}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["scan_limits_loaded"] is True, (
        "the 3 guard helpers must load tensor_grep.io.scan_limits to read their constants"
    )
    assert status["directory_scanner_loaded"] is False, (
        "the 3 guard helpers must NOT load tensor_grep.io.directory_scanner -- they only need "
        "plain constants, never SearchConfig or the DirectoryScanner class"
    )
    assert status["core_config_loaded"] is False, (
        "the 3 guard helpers must NOT load tensor_grep.core.config (SearchConfig) -- that module "
        "is only needed by a real DirectoryScanner walk, not by a constants-only guard check"
    )


def test_bare_cli_main_import_does_not_pull_directory_scanner() -> None:
    """F2.4 (cli/main.py side): importing `tensor_grep.cli.main` in isolation -- simulating what
    happens for `tg --help`/`scan`/`test`/`ast-info`, which import it but run no command body --
    must not pull in `tensor_grep.io.directory_scanner` merely to read the 5 broad-scan
    constants at module level (main.py:46-52 pre-fix read them FROM directory_scanner).
    `DirectoryScanner` is still imported lazily, function-local, at each command that actually
    walks a tree -- a bare import must not reach it.

    Deliberately does NOT also assert `core_config_loaded is False`: verified via a traced
    `builtins.__import__` that `tensor_grep.core.config` (SearchConfig) still loads for a bare
    `cli.main` import through a WHOLLY UNRELATED, pre-existing path --
    `cli/formatters/json_fmt.py:5`'s own module-level `from tensor_grep.core.config import
    SearchConfig`, reached via `cli/formatters/__init__.py` (main.py's own eager `from
    tensor_grep.cli.formatters.base import OutputFormatter` pulls in the whole `formatters`
    package). That is out of this PR's scope (F2.4 is specifically about the 5 broad-scan
    constants' source module, not every SearchConfig import in the CLI). The precise, TRUE claim
    this PR makes is `directory_scanner_loaded is False` -- confirmed below."""
    probe_source = f"""
import sys
sys.path.insert(0, {_REPO_SRC!r})
import tensor_grep.cli.main
import json
print(json.dumps({{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "scan_limits_loaded": "tensor_grep.io.scan_limits" in sys.modules,
}}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["scan_limits_loaded"] is True
    assert status["directory_scanner_loaded"] is False, (
        "a bare `import tensor_grep.cli.main` must not import directory_scanner -- "
        "main.py:46-52 must read the 5 broad-scan constants from tensor_grep.io.scan_limits"
    )


def test_bare_scan_guardrails_import_does_not_pull_directory_scanner_or_core_config() -> None:
    """F2.4 (cli/scan_guardrails.py side): `tg scan`/`test`/`ast-info`/`run` reach this module
    (via the lazily-imported `cli/ast_workflows.py`) for its 3 broad-scan constants only --
    importing it in isolation must not pull in `directory_scanner`/`core.config`."""
    probe_source = f"""
import sys
sys.path.insert(0, {_REPO_SRC!r})
import tensor_grep.cli.scan_guardrails
import json
print(json.dumps({{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "core_config_loaded": "tensor_grep.core.config" in sys.modules,
    "scan_limits_loaded": "tensor_grep.io.scan_limits" in sys.modules,
}}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["scan_limits_loaded"] is True
    assert status["directory_scanner_loaded"] is False
    assert status["core_config_loaded"] is False


def test_scan_limits_import_alone_is_dependency_free() -> None:
    """F2.4: `tensor_grep.io.scan_limits` is documented as zero-dependency -- importing it in
    total isolation must never pull in `tensor_grep.core.config` or `tensor_grep.io.
    directory_scanner` (the whole point of splitting the constants into their own module)."""
    probe_source = f"""
import sys
sys.path.insert(0, {_REPO_SRC!r})
import tensor_grep.io.scan_limits
import json
print(json.dumps({{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "core_config_loaded": "tensor_grep.core.config" in sys.modules,
}}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [sys.executable, "-c", probe_source],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["directory_scanner_loaded"] is False
    assert status["core_config_loaded"] is False


def test_cli_package_getattr_hook_still_resolves_main_entry() -> None:
    """F2.6 non-regression: `cli/__init__.py`'s `__getattr__` return annotation changed from
    `Any` to the builtin `object` (removing the module-level `from typing import Any`) -- this
    must be a purely static-typing change with zero effect on the PEP 562 dynamic-attribute
    mechanism itself. `tensor_grep.cli.main_entry` must still resolve to the exact same callable
    as `tensor_grep.cli.bootstrap.main_entry`."""
    import tensor_grep.cli
    import tensor_grep.cli.bootstrap as bootstrap

    assert tensor_grep.cli.main_entry is bootstrap.main_entry


def test_cli_package_init_has_no_eager_typing_import() -> None:
    """F2.6 source-level pin: `cli/__init__.py` is the parent-package init for every
    `tensor_grep.cli.*` submodule, so ANY top-level `from typing import ...` there is paid on
    every single `tg` invocation before any submodule-specific code runs. A source-text check
    (rather than a `sys.modules` probe) is used here because `typing` may legitimately already be
    loaded via an unrelated import by the time any probe subprocess reaches this file (e.g.
    `click`/`typer` import it once the full CLI is reached) -- the actual, durable regression
    surface is this file's OWN source no longer NEEDING the import, not whether `typing` happens
    to be in `sys.modules` for some other reason."""
    import ast

    init_source = (Path(_REPO_SRC) / "tensor_grep" / "cli" / "__init__.py").read_text(
        encoding="utf-8"
    )
    # AST-parse the TOP-LEVEL statements (module-load-time cost) so the pin catches BOTH import
    # forms. A bare `"import typing" not in init_source` substring check MISSES the exact
    # `from typing import Any` this move removed -- that text contains "typing import", not
    # "import typing" -- making the pin weaker than its own contract. Assert the real invariant:
    # no top-level `import typing[.*]` and no top-level `from typing[.*] import ...`. (Imports
    # nested under a function or an `if TYPE_CHECKING:` guard are not module-load cost and are not
    # children of the Module node, so they are correctly ignored.)
    eager_typing_imports: list[str] = []
    for node in ast.iter_child_nodes(ast.parse(init_source)):
        if isinstance(node, ast.Import):
            eager_typing_imports += [
                f"import {alias.name}"
                for alias in node.names
                if alias.name == "typing" or alias.name.startswith("typing.")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "typing" or module.startswith("typing."):
                eager_typing_imports.append(f"from {module} import ...")
    assert not eager_typing_imports, (
        "cli/__init__.py must not import `typing` at module level (neither `import typing` nor "
        "`from typing import ...`) -- it is the parent-package init paid on every tg invocation; "
        f"__getattr__'s return annotation should be the builtin `object`. Found: {eager_typing_imports}"
    )


@pytest.mark.parametrize("argv", [["tg", "--version"], ["tg", "-V"]])
def test_version_fast_path_exit_code_and_banner_unaffected(argv: list[str]) -> None:
    """Non-regression across all three moves at once: `--version`/`-V` must still print the
    banner and exit 0 -- the import-deferral changes must be behavior-invisible on the one path
    that exercises all of F2.4 (guard helpers never even called here), F2.5 (runtime_paths.py's
    other functions never called here), and F2.6 (the package __getattr__ hook) simultaneously."""
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_SRC
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.argv = {argv!r}; "
            "import tensor_grep.cli.bootstrap as bootstrap\n"
            "try:\n    bootstrap.main_entry()\nexcept SystemExit as e:\n    "
            "sys.exit(e.code if e.code is not None else 0)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip().startswith("tensor-grep ")
