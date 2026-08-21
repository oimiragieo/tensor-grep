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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tensor_grep.cli.main import app


class _InjectedMetadataFailure(Exception):
    """Unique marker: `importlib.metadata.version` cannot raise this class naturally."""


class _InjectedPyprojectReadFailure(Exception):
    """Unique marker: `Path.read_text` cannot raise this class naturally."""


def _write_regex_rule_and_target(tmp_path: Path) -> tuple[str, Path]:
    inline_rules = "\n".join([
        "id: w1c-sentinel",
        "engine: regex",
        "pattern: SENTINEL_TOKEN",
        "language: python",
        "severity: high",
        "message: sentinel finding",
    ])
    (tmp_path / "app.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")
    return inline_rules, tmp_path


def test_double_version_lookup_failure_is_disclosed_in_sarif_provenance(
    tmp_path: Path, monkeypatch: Any
) -> None:
    inline_rules, root = _write_regex_rule_and_target(tmp_path)

    def _raise_metadata(*_args: Any, **_kwargs: Any) -> str:
        raise _InjectedMetadataFailure("simulated importlib.metadata failure")

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
    inline_rules, root = _write_regex_rule_and_target(tmp_path)

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
