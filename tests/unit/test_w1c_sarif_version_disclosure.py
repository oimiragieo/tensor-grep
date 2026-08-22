"""RED-2 receipt for the W1-c A3 MEDIUM (PR #1070): a double version-lookup failure inside
``tg scan --sarif`` must be OBSERVABLE in the SARIF provenance, never laundered into
normal-looking output.

Root cause this closes: ``_read_project_version_fallback()`` (cli/main.py) swallowed any
``pyproject.toml`` read/parse failure to a hardcoded ``"0.0.0"``; ``_cli_package_version()``
falls back to it whenever ``importlib.metadata.version("tensor-grep")`` also fails. Neither
degraded value differed from a legitimately-discovered "0.0.0" version, and ``scan()``'s SARIF
branch (main.py) fed the bare string into ``scan_payload_to_sarif``'s ``driver.version`` /
``driver.semanticVersion`` with nothing else disclosing the failure -- so a double metadata
failure produced a SARIF run that LOOKS like ordinary tool provenance, exactly the shape the
Backend Fail-Closed Contract forbids on a security-scan output a CI gate trusts.

Fix: the fallback now returns ``_VERSION_UNAVAILABLE_SENTINEL`` ("0.0.0-unavailable", never a
value a real discovered version could equal) instead of "0.0.0", and ``scan()`` threads
``version_unavailable=True`` into ``scan_payload_to_sarif`` when that sentinel is returned,
which stamps ``run.properties.tensorGrepVersionUnavailable = True``.

DISCRIMINATING-ORACLE DISCIPLINE (the shape both W1-a and W1-b were sent back for missing):
- (a) unique injected marker: the two patches below raise ``_InjectedMetadataFailure`` /
  ``_InjectedPyprojectReadFailure``, exception TYPES that do not exist anywhere in production
  code and that no natural failure (a missing package, a normal permission error) can produce --
  so a pass here cannot be explained by an unrelated real error taking the same branch.
- (b) the assertion reads the caller-observable surface: the real ``tg scan --sarif`` stdout,
  parsed as SARIF JSON, not the private helper directly.
- (c) a per-case NO-INJECTION control (``test_normal_version_lookup_discloses_no_degradation``)
  proves the marker is ABSENT and a real version string is reported when nothing is injected --
  the treatment test would also pass if the property key were always stamped by mistake, and
  this control is what rules that out.
- (d) the injected patches REPLACE both natural failure sources
  (``importlib.metadata.version`` AND the ``pyproject.toml`` read), so a natural failure of
  either one alone cannot masquerade as this scenario -- both must be down, matching the
  production double-failure precondition the fix addresses.

CI-ONLY FAILURE, ROOT-CAUSED (2026-08-21): this file previously failed on GitHub Actions
(ubuntu-latest, py3.11/py3.12) with "Explicit AST search requires AST dependencies: the
ast-grep wrapper backend is required for pattern 'SENTINEL_TOKEN' ...", while passing on a
Windows dev box even though that box also lacks the ``ast_grep_py`` PACKAGE. The mechanism has
nothing to do with the version-lookup injections above (a prior fix attempt that scoped the
``importlib.metadata.version`` patch to "tensor-grep" did not address it, because the failure
is not caused by that patch at all):

- ``_load_inline_rule_specs()`` (``cli/ast_scan.py``) does NOT copy the YAML ``engine`` key
  into the parsed rule spec dict -- confirmed by calling it directly: the returned dict has no
  ``"engine"`` entry for ANY inline rule, ``engine: regex`` included. So the
  ``if rule.get("engine") == "regex": continue`` fast path in the per-rule loop
  (``cli/ast_scan.py`` ~:792) never fires for ``--inline-rules`` input -- every inline rule is
  routed through AST backend selection (``_select_ast_backend_for_rule`` /
  ``_select_ast_backend_for_pattern``, ``cli/ast_workflows.py``), regardless of the declared
  ``engine:``.
- Backend selection there prefers ``AstGrepWrapperBackend`` whenever it reports itself
  available, and ``AstGrepWrapperBackend.is_available()`` (``backends/ast_wrapper_backend.py``
  ~:95-124) probes for an ``ast-grep``/``sg`` CLI BINARY on ``PATH`` -- a completely different
  signal from the ``ast_grep_py`` Python package (or ``importlib.metadata.version``). The
  Windows dev box happens to have that binary on PATH (confirmed: ``tg scan --json`` on this
  box reports ``"backends": ["AstGrepWrapperBackend"]`` even with ``ast_grep_py`` unimportable);
  a fresh GitHub Actions ``ubuntu-latest`` runner does not. Without the wrapper, selection falls
  through to the native ``AstBackend``, which raises the observed
  ``BackendExecutionError`` for a bare-identifier pattern that fails BOTH its node-type-index
  lookup (no real tree-sitter node is literally named ``SENTINEL_TOKEN``) and tree-sitter query
  compilation (``backends/ast_backend.py`` ~:775-826).
- DISCRIMINATING VARIABLE: presence of an ``ast-grep``/``sg`` binary on ``PATH`` (not the
  ``ast_grep_py`` package, not anything this test injects). This is exactly the kind of
  environment leak A85 (``AGENTS.md``) forbids testing against implicitly.

Per this repo's rule (never env-detect; force the optional-engine seam explicitly; never
skip/xfail): this file no longer depends on the ``engine: regex`` YAML tag actually being
honored (it structurally cannot be, for ``--inline-rules``, without touching ``src/``) or on
which AST backend a given box happens to route to. The rule pattern below (``identifier``) is a
real tree-sitter node-type name, which the native ``AstBackend`` serves via its node-type-index
fast path with NO ast-grep dependency (so it cannot hit the ``BackendExecutionError`` above
regardless of routing), and which ``AstGrepWrapperBackend`` -- when present, as on this box --
also serves without error as an ordinary (non-matching-or-matching) ast-grep code pattern.
``AstGrepWrapperBackend.is_available`` is additionally monkeypatched to a fixed value in both
tests so the exercised backend, and therefore this file's outcome, no longer depends on
whatever happens to be on the runner's ``PATH``. Verified directly (both arms, in-process):
exit 0 / valid SARIF with the wrapper forced unavailable (the CI shape) and with it left as
this box's real (available) state.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.cli.main import app


class _InjectedMetadataFailure(Exception):
    """Unique marker: `importlib.metadata.version` cannot raise this class naturally."""


class _InjectedPyprojectReadFailure(Exception):
    """Unique marker: `Path.read_text` cannot raise this class naturally."""


def _write_regex_rule_and_target(tmp_path: Path, monkeypatch: Any) -> tuple[str, Path]:
    # A85: force the optional ast-grep-wrapper seam explicitly rather than env-detecting it.
    # `AstGrepWrapperBackend.is_available()` probes for an `ast-grep`/`sg` CLI binary on PATH --
    # a signal this test has no reason to depend on and that differs between this dev box (has
    # the binary) and CI (does not). Pin it to a fixed, known state instead.
    monkeypatch.setattr(AstGrepWrapperBackend, "is_available", lambda self: False)
    # "engine: regex" is declared for documentation/intent, but `_load_inline_rule_specs()`
    # does not propagate the `engine` key for `--inline-rules` input (see module docstring), so
    # this rule is always routed through AST backend selection regardless of that tag. `identifier`
    # is a real tree-sitter node-type name: the native AstBackend serves it via its node-type-index
    # fast path with no ast-grep dependency, so this scan cannot hit the
    # "Explicit AST search requires AST dependencies" failure this file used to expose.
    inline_rules = "\n".join([
        "id: w1c-sentinel",
        "engine: regex",
        "pattern: identifier",
        "language: python",
        "severity: high",
        "message: sentinel finding",
    ])
    (tmp_path / "app.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")
    return inline_rules, tmp_path


def test_double_version_lookup_failure_is_disclosed_in_sarif_provenance(
    tmp_path: Path, monkeypatch: Any
) -> None:
    inline_rules, root = _write_regex_rule_and_target(tmp_path, monkeypatch)

    pristine_metadata_version = importlib.metadata.version

    def _raise_metadata(distribution_name: str, *args: Any, **kwargs: Any) -> str:
        # Scoped to the distribution under test ON PURPOSE. `_cli_package_version()` only ever
        # looks up "tensor-grep", so raising for EVERY package is wider than this scenario needs
        # -- and where the optional `ast_grep_py` extra is installed, the AST backend's own
        # availability probe calls this same function, receives the injected failure, and the
        # scan aborts with "Explicit AST search requires AST dependencies" before any SARIF is
        # emitted. That made the test's outcome depend on WHICH OPTIONAL DEPENDENCY happened to
        # be installed rather than on the behaviour under test: it passed on a box without the
        # extra and failed on CI lanes that have it. Delegating every other lookup to the real
        # implementation keeps this arm env-independent while preserving the double-failure
        # precondition (both natural sources of the tensor-grep version are down).
        if distribution_name == "tensor-grep":
            raise _InjectedMetadataFailure("simulated importlib.metadata failure")
        return pristine_metadata_version(distribution_name, *args, **kwargs)

    pristine_read_text = Path.read_text

    def _raise_on_pyproject(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == "pyproject.toml":
            raise _InjectedPyprojectReadFailure("simulated pyproject.toml read failure")
        return pristine_read_text(self, *args, **kwargs)

    # Both natural failure sources are replaced, matching the production double-failure
    # precondition -- a lone real failure of either one cannot masquerade as this case.
    monkeypatch.setattr("importlib.metadata.version", _raise_metadata)
    monkeypatch.setattr(Path, "read_text", _raise_on_pyproject)

    result = CliRunner().invoke(
        app, ["scan", "--inline-rules", inline_rules, "--path", str(root), "--sarif"]
    )

    assert result.exit_code == 0, result.output
    sarif = json.loads(result.stdout)
    run = sarif["runs"][0]
    driver = run["tool"]["driver"]

    assert driver["version"] == "0.0.0-unavailable", (
        "the degraded value must be the disclosed sentinel, not the old silent '0.0.0' -- a "
        "real project version can never equal this string, so its presence alone is proof the "
        "lookup failed."
    )
    assert driver["semanticVersion"] == "0.0.0-unavailable"
    assert run["properties"]["tensorGrepVersionUnavailable"] is True, (
        "the caller-observable SARIF run must carry an explicit machine-readable degradation "
        "flag, not rely on a human reading the version string."
    )


def test_normal_version_lookup_discloses_no_degradation(tmp_path: Path, monkeypatch: Any) -> None:
    """NO-INJECTION CONTROL: with nothing patched, the real command must report a real version
    and must NOT stamp the degradation marker -- proving the treatment test's assertions are not
    trivially true (e.g. the property key being stamped unconditionally by a bug)."""
    inline_rules, root = _write_regex_rule_and_target(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        app, ["scan", "--inline-rules", inline_rules, "--path", str(root), "--sarif"]
    )

    assert result.exit_code == 0, result.output
    sarif = json.loads(result.stdout)
    run = sarif["runs"][0]
    driver = run["tool"]["driver"]

    assert driver["version"] != "0.0.0-unavailable"
    assert driver["version"]
    assert "tensorGrepVersionUnavailable" not in run.get("properties", {})
