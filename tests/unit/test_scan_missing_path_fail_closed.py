"""`tg scan` must fail closed on a scan root that does not exist.

THE DEFECT THIS PINS. `tg scan --ruleset <name> <missing path>` returned exit 0 with
``matched_rules: 0`` and ``total_matches: 0`` -- byte-comparable, on both the exit code and the
payload, to a real scan of a real tree that genuinely found nothing. On a SECURITY-rule surface
that means a CI gate pointed at a mistyped, moved, or wrongly-translated path reports the
repository CLEAN and exits 0. It is the false-zero law in the shipped product: "measured nothing"
and "did not measure" produced the same answer.

`tg search` on the identical input was already correct -- exit 2 with
``{"error": "path_not_found"}`` -- so the fix copies a working sibling rather than inventing a
taxonomy, and this module asserts BOTH commands agree.

Every arm here is paired, because a guard that only ever sees the failing input cannot show it
still permits the succeeding one:

  * missing path  -> non-zero exit AND a machine-readable ``path_not_found`` (the treatment)
  * real path WITH a finding    -> exit 0, ``matched_rules >= 1``  (proves the guard did not
    simply break scanning)
  * real path WITHOUT a finding -> exit 0, ``matched_rules == 0``  (proves a genuine clean scan
    is still reported as clean, i.e. the guard did not turn "no findings" into an error)

That third arm is the one that matters most: without it, a "fix" that made every clean scan fail
would pass the first two and look correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

_VULNERABLE_SOURCE = "import subprocess\n\n\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n"
_CLEAN_SOURCE = "def add(x, y):\n    return x + y\n"
_RULESET = "subprocess-safe"


def _scan(*args: str):
    return CliRunner().invoke(app, ["scan", *args])


def test_scan_missing_path_exits_non_zero_with_path_not_found(tmp_path: Path):
    """TREATMENT. Before the fix this exited 0 with matched_rules: 0 -- a clean bill of health
    from a scanner that never opened a file."""
    missing = tmp_path / "no-such-directory"
    assert not missing.exists(), "fixture precondition: the path must genuinely not exist"

    result = _scan(str(missing), "--ruleset", _RULESET, "--json")

    assert result.exit_code != 0, (
        "a scan root that does not exist must NOT report success; exit 0 here is "
        f"indistinguishable from a clean scan. output={result.output!r}"
    )
    payload = json.loads(result.stdout)
    assert payload.get("error") == "path_not_found", (
        "the failure must be machine-readable, matching `tg search`'s existing taxonomy so an "
        f"agent can branch on it without string-sniffing. payload={payload}"
    )
    assert payload.get("ok") is False


def test_scan_real_path_with_a_finding_still_succeeds(tmp_path: Path):
    """CONTROL A. The guard must not break scanning. Without this arm, a fix that rejected
    everything would pass the treatment test."""
    (tmp_path / "vuln.py").write_text(_VULNERABLE_SOURCE, encoding="utf-8")

    result = _scan(str(tmp_path), "--ruleset", _RULESET, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("matched_rules", 0) >= 1, (
        "this arm cannot discriminate unless the scan actually found the planted finding; "
        f"payload={payload}"
    )


def test_scan_real_path_without_a_finding_is_still_reported_clean(tmp_path: Path):
    """CONTROL B -- the load-bearing one.

    A genuine clean scan must stay exit 0 with zero matches. If the fix turned "found nothing"
    into an error, the two arms above would both pass and the product would be broken for the
    common case.
    """
    (tmp_path / "clean.py").write_text(_CLEAN_SOURCE, encoding="utf-8")

    result = _scan(str(tmp_path), "--ruleset", _RULESET, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("matched_rules", None) == 0, (
        f"a real tree with no findings must still report clean, not error. payload={payload}"
    )


def test_scan_and_search_agree_on_a_missing_path(tmp_path: Path):
    """The two commands must not disagree about whether a path exists.

    `tg search` already fails closed here. The whole defect was that `tg scan` did not, so an
    agent got opposite answers about the same filesystem from the same binary.
    """
    missing = tmp_path / "also-not-here"

    scan_result = _scan(str(missing), "--ruleset", _RULESET, "--json")
    search_result = CliRunner().invoke(app, ["search", "anything", str(missing), "--json"])

    assert search_result.exit_code != 0, (
        "control: `tg search` is the known-correct sibling. If THIS assertion fails, the "
        "reference behaviour changed and this module's premise needs re-deriving."
    )
    assert scan_result.exit_code != 0
    assert json.loads(search_result.stdout).get("error") == "path_not_found"
    assert json.loads(scan_result.stdout).get("error") == "path_not_found"


def test_scan_missing_path_non_json_mode_also_fails_closed(tmp_path: Path):
    """The disclosure must not be JSON-only: a human or a shell gate reading the exit code of a
    plain `tg scan` is exactly the CI case this defect endangers."""
    missing = tmp_path / "nope"

    result = _scan(str(missing), "--ruleset", _RULESET)

    assert result.exit_code != 0, result.output
    assert "does not exist" in result.output.lower(), result.output
