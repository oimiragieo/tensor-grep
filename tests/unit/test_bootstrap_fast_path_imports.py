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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")

# Kept as a single string (not a helper module) so the subprocess starts from a totally clean
# interpreter state -- no pytest plugins, no already-imported tensor_grep submodules.
_PROBE_TEMPLATE = """
import sys
sys.argv = {argv!r}
import tensor_grep.cli.bootstrap as bootstrap
try:
    bootstrap.main_entry()
except SystemExit:
    pass
import json
print(json.dumps({{
    "directory_scanner_loaded": "tensor_grep.io.directory_scanner" in sys.modules,
    "core_config_loaded": "tensor_grep.core.config" in sys.modules,
}}))
"""


def _run_fast_path_probe(argv: list[str]) -> dict[str, bool]:
    probe_source = _PROBE_TEMPLATE.format(argv=argv)
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


def test_plain_search_still_imports_directory_scanner_when_actually_needed(
    tmp_path: Path,
) -> None:
    """Non-regression: the deferral must not make the broad-scan guard silently unavailable for
    a real search invocation. `main_entry`'s `guarded_broad_root` check
    (`_search_args_include_unbounded_broad_scan`, which needs `directory_scanner`'s constants)
    runs unconditionally for any search-shaped argv -- BEFORE the native/rg/full-CLI branch
    decision -- so this must hold regardless of what native/rg binaries happen to be resolvable
    in the test environment's PATH. Proves this is genuinely "loaded when needed", not
    "permanently broken"."""
    status = _run_fast_path_probe(["tg", "search", "needle", str(tmp_path)])
    assert status["directory_scanner_loaded"] is True
