"""#310 seam test -- the REAL scan payload must drive the SARIF completeness verdict.

`test_sarif_output.py` renders hand-built payloads. That proves the renderer's logic and proves
nothing about whether it is wired to reality: every key in those fixtures was chosen by the same
person who wrote the renderer, so if `_run_ast_scan_payload` spelled the field
`unreadable_files` (or nested it, or set only `partial`), all of those tests would still pass and
`tg scan --sarif` would cheerfully report `executionSuccessful: true` over a tree it could not
read.

That is the cross-module seam failure this repo has hit before: per-module tests green, the
contract between them never exercised. So this file builds the payload with the PRODUCER and
renders it with the CONSUMER, and asserts the verdict flows.

The `PermissionError` injection mirrors `test_scan_unreadable_disclosure.py` deliberately -- a
real chmod is not portable to Windows, which is this repo's primary dogfood platform.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli.sarif import scan_payload_to_sarif

# `engine: "regex"` is the discriminator at main.py:6459 that routes a rule to the regex leg --
# the leg #299's disclosure actually covers. A builtin pack would be AST metavar patterns and
# would never reach it, which is how an earlier version of the #299 test exercised nothing.
_REGEX_RULE: dict[str, Any] = {
    "id": "test-regex-token",
    "engine": "regex",
    "pattern": "SENTINEL_TOKEN",
    "language": "python",
    "severity": "high",
    "message": "sentinel",
}


def _scan(root: Path) -> dict[str, Any]:
    from tensor_grep.cli.main import _run_ast_scan_payload

    return _run_ast_scan_payload(
        {
            "config_path": "builtin:auth-safe",
            "root_dir": root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": "python",
        },
        [dict(_REGEX_RULE)],
        routing_reason="builtin-ruleset-scan",
        ruleset_name="test-regex",
    )


def _read_text_raiser(canary: str, pristine: Any) -> Any:
    def fake(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == canary:
            raise PermissionError(13, "Permission denied", str(self))
        return pristine(self, *args, **kwargs)

    return fake


def test_a_real_unreadable_file_makes_the_sarif_run_unsuccessful(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "clean.py").write_text("SENTINEL_TOKEN = 1\n", encoding="utf-8")
    (tmp_path / "locked.py").write_text("SENTINEL_TOKEN = 2\n", encoding="utf-8")

    # CONTROL FIRST, and it is the whole point of the file: a fully readable scan must render as
    # a successful run. If this ever fails, the renderer is reporting every scan as incomplete and
    # the treatment assertion below would be satisfied by a constant.
    control = scan_payload_to_sarif(_scan(tmp_path), tool_version="test", base_path=str(tmp_path))
    control_invocation = control["runs"][0]["invocations"][0]
    assert control_invocation["executionSuccessful"] is True
    # Premise: the scan must actually have FOUND something, or "no findings and complete" is
    # trivially true and the comparison below is between two empty runs.
    assert control["runs"][0]["results"], "premise: the sentinel rule must match both files"

    pristine = Path.read_text
    monkeypatch.setattr(Path, "read_text", _read_text_raiser("locked.py", pristine))

    treatment = scan_payload_to_sarif(_scan(tmp_path), tool_version="test", base_path=str(tmp_path))
    invocation = treatment["runs"][0]["invocations"][0]

    assert invocation["executionSuccessful"] is False, (
        "the producer disclosed an unreadable path; SARIF must not report a successful run. A "
        "failure here means the producer's field names and the renderer's have drifted apart -- "
        "the seam, not the logic."
    )
    text = " ".join(n["message"]["text"] for n in invocation["toolExecutionNotifications"])
    assert "could not be read" in text
    assert "not evidence that the path is clean" in text
