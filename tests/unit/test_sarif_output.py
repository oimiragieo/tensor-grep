"""Task #310 -- SARIF v2.1.0 rendering of ``tg scan`` findings.

Every assertion here is paired. The reason is specific to what this module does: SARIF is a
schema, and schema code fails in the direction of "emitted something plausible" rather than
"crashed". A test that only checks the incomplete case passes against a renderer that hardcodes
``executionSuccessful: false``; a test that only checks a known severity passes against one that
hardcodes ``warning``. So each behaviour is asserted together with the input that must NOT produce
it -- the rule from the verification-oracle family in AGENTS.md: *what would this check show if the
thing were broken? "the same" means it is not verification.*
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.sarif import (
    _VALID_SARIF_LEVELS,
    FINGERPRINT_KEY,
    SARIF_VERSION,
    scan_payload_to_sarif,
)

_VERSION = "1.98.25"


def _payload(**overrides: Any) -> dict[str, Any]:
    """A minimal COMPLETE scan payload, shaped exactly like ast_workflows.py:1457."""
    payload: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstGrepWrapperBackend",
        "routing_reason": "builtin-ruleset-scan",
        "sidecar_used": False,
        "total_matches": 1,
        "matched_rules": 1,
        "rule_count": 1,
        "backends": ["AstGrepWrapperBackend"],
        "findings": [
            {
                "rule_id": "py-eval-call",
                "language": "python",
                "severity": "high",
                # Inert fixture text. This is the SNIPPET a security rule REPORTS having found,
                # i.e. the string a scanner shows you; nothing here evaluates anything.
                "message": "eval() on untrusted input",
                "matches": 2,
                "files": ["src/app/handler.py"],
                "fingerprint": "a" * 64,
                "evidence": [
                    {
                        "file": "src/app/handler.py",
                        "match_count": 2,
                        "snippets": [{"line_number": 12, "text": "eval(x)"}],
                    }
                ],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _render(**overrides: Any) -> dict[str, Any]:
    return scan_payload_to_sarif(_payload(**overrides), tool_version=_VERSION)


# --------------------------------------------------------------------------------------------
# Envelope shape
# --------------------------------------------------------------------------------------------


def test_document_is_a_sarif_2_1_0_log() -> None:
    doc = _render()
    assert doc["version"] == SARIF_VERSION == "2.1.0"
    assert doc["$schema"].endswith("sarif-2.1.0.json")
    assert len(doc["runs"]) == 1
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "tensor-grep"
    assert driver["version"] == _VERSION


def test_every_emitted_level_is_in_sarifs_closed_enum() -> None:
    # The premise check that makes the severity tests mean something. SARIF's `level` is a closed
    # four-value enum; a consumer rejects the WHOLE file on an invalid one, so a single unmapped
    # severity passed through would lose every finding in the run, not just its own.
    severities = ["high", "medium", "low", "error", "warning", "info", "off", "totally-made-up"]
    doc = scan_payload_to_sarif(
        _payload(
            findings=[
                {
                    "rule_id": f"rule-{i}",
                    "severity": sev,
                    "message": "m",
                    "files": ["a.py"],
                    "evidence": [],
                }
                for i, sev in enumerate(severities)
            ]
        ),
        tool_version=_VERSION,
    )
    results = doc["runs"][0]["results"]
    assert len(results) == len(severities)
    for result in results:
        assert result["level"] in _VALID_SARIF_LEVELS


# --------------------------------------------------------------------------------------------
# Completeness -- the field a CI gate reads
# --------------------------------------------------------------------------------------------


def test_an_incomplete_scan_reports_execution_unsuccessful() -> None:
    doc = _render(
        partial=True,
        partial_reason="scan skipped 3 unreadable path(s)",
        unreadable_paths={"count": 3, "sample": ["vendor/locked", "secrets/"]},
    )
    invocation = doc["runs"][0]["invocations"][0]
    assert invocation["executionSuccessful"] is False

    notifications = invocation["toolExecutionNotifications"]
    text = " ".join(n["message"]["text"] for n in notifications)
    assert "3 path(s) could not be read" in text
    assert "vendor/locked" in text
    # The interpretive sentence matters as much as the count: a consumer that sees zero findings
    # for a skipped path must not read that as "clean".
    assert "not evidence that the path is clean" in text


def test_a_complete_scan_reports_execution_successful() -> None:
    # THE CONTROL. Without it, a renderer hardcoding `executionSuccessful: false` -- or one that
    # treats any payload as partial -- passes the test above. This is also the arm that would fail
    # if someone "simplified" the partial check into something always-truthy.
    invocation = _render()["runs"][0]["invocations"][0]
    assert invocation["executionSuccessful"] is True
    assert "toolExecutionNotifications" not in invocation


def test_unreadable_paths_alone_is_enough_to_flip_the_verdict() -> None:
    # `partial` and `unreadable_paths` are set together by the current producer, but a consumer of
    # THIS function must not depend on that: either one alone means the scan was incomplete. If
    # this ever regresses to requiring both, a producer that sets only one silently reports a
    # clean run.
    invocation = _render(unreadable_paths={"count": 1, "sample": ["x"]})["runs"][0]["invocations"][
        0
    ]
    assert invocation["executionSuccessful"] is False


# --------------------------------------------------------------------------------------------
# Severity -- allow-list, with the non-comprehension disclosed
# --------------------------------------------------------------------------------------------


def test_known_severities_map_to_their_sarif_levels() -> None:
    for severity, expected in (
        ("high", "error"),
        ("medium", "warning"),
        ("low", "note"),
        ("error", "error"),
        ("info", "note"),
        ("off", "none"),
        ("HIGH", "error"),  # case-insensitive: rule authors are not consistent
        (" high ", "error"),  # and neither is YAML whitespace
    ):
        doc = _render(
            findings=[{"rule_id": "r", "severity": severity, "message": "m", "files": ["a.py"]}]
        )
        result = doc["runs"][0]["results"][0]
        assert result["level"] == expected, f"{severity!r} should map to {expected!r}"
        # The disclosure must be ABSENT for a value the tool understood, or it is decoration that
        # a reader learns to ignore.
        assert "tensorGrepUnmappedSeverity" not in result.get("properties", {})


def test_an_unrecognised_severity_is_disclosed_not_laundered() -> None:
    # CONTROL for the test above. The renderer must still emit valid SARIF (so the run survives),
    # but must not present a level it did not actually derive as though it had.
    doc = _render(
        findings=[{"rule_id": "r", "severity": "catastrophic", "message": "m", "files": ["a.py"]}]
    )
    result = doc["runs"][0]["results"][0]
    assert result["level"] in _VALID_SARIF_LEVELS
    assert result["properties"]["tensorGrepUnmappedSeverity"] == "catastrophic"


def test_an_absent_severity_is_not_reported_as_unmapped() -> None:
    # Absence is not misunderstanding. main.py:6412 stores `rule.get("severity")` with no default,
    # so None arrives here routinely; flagging it would make the disclosure fire on the common
    # case and drown the real signal from the test above.
    doc = _render(findings=[{"rule_id": "r", "severity": None, "message": "m", "files": ["a.py"]}])
    result = doc["runs"][0]["results"][0]
    assert result["level"] in _VALID_SARIF_LEVELS
    assert "tensorGrepUnmappedSeverity" not in result.get("properties", {})


# --------------------------------------------------------------------------------------------
# Locations -- the Windows trap
# --------------------------------------------------------------------------------------------


def test_windows_paths_are_normalised_to_forward_slashes() -> None:
    doc = _render(
        findings=[
            {
                "rule_id": "r",
                "severity": "high",
                "message": "m",
                "files": [],
                "evidence": [
                    {
                        "file": "src\\tensor_grep\\cli\\main.py",
                        "match_count": 1,
                        "snippets": [{"line_number": 7, "text": "x"}],
                    }
                ],
            }
        ]
    )
    location = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "src/tensor_grep/cli/main.py"
    assert "\\" not in location["artifactLocation"]["uri"]
    assert location["region"]["startLine"] == 7


def test_a_posix_path_survives_normalisation_unchanged() -> None:
    # CONTROL: proves the normaliser rewrites separators rather than mangling paths generally.
    doc = _render(
        findings=[
            {
                "rule_id": "r",
                "severity": "high",
                "message": "m",
                "files": ["src/app/handler.py"],
                "evidence": [],
            }
        ]
    )
    uri = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ]
    assert uri == "src/app/handler.py"


def test_a_file_without_a_usable_line_degrades_to_a_file_level_location() -> None:
    # A region with startLine 0 is invalid SARIF. Degrading to file-level keeps the finding
    # reportable instead of emitting a malformed region or dropping the location entirely.
    doc = _render(
        findings=[
            {
                "rule_id": "r",
                "severity": "high",
                "message": "m",
                "files": [],
                "evidence": [{"file": "a.py", "match_count": 1, "snippets": [{"line_number": 0}]}],
            }
        ]
    )
    physical = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "a.py"
    assert "region" not in physical


# --------------------------------------------------------------------------------------------
# Cross-run identity + suppression
# --------------------------------------------------------------------------------------------


def test_the_fingerprint_becomes_a_partial_fingerprint() -> None:
    # This is what lets a consumer recognise the SAME finding across runs -- the SARIF-native
    # equivalent of the baseline/suppression fingerprint file tg already writes.
    result = _render()["runs"][0]["results"][0]
    assert result["partialFingerprints"][FINGERPRINT_KEY] == "a" * 64


def test_a_finding_without_a_fingerprint_carries_no_fingerprint_key() -> None:
    # CONTROL: an empty/absent fingerprint must not become an empty string that a consumer would
    # treat as a real identity and collapse unrelated findings onto.
    doc = _render(
        findings=[{"rule_id": "r", "severity": "high", "message": "m", "files": ["a.py"]}]
    )
    assert "partialFingerprints" not in doc["runs"][0]["results"][0]


def test_a_suppressed_finding_is_marked_suppressed() -> None:
    doc = _render(
        findings=[
            {
                "rule_id": "r",
                "severity": "high",
                "message": "m",
                "files": ["a.py"],
                "status": "suppressed",
            }
        ]
    )
    assert doc["runs"][0]["results"][0]["suppressions"] == [{"kind": "external"}]


def test_an_unsuppressed_finding_has_no_suppressions_array() -> None:
    # CONTROL, and the load-bearing half: SARIF says a result with a NON-EMPTY `suppressions`
    # array is suppressed. Emitting `[]` -- or the key unconditionally -- risks a consumer
    # treating every finding as triaged-away, which is a silent, total loss of the scan.
    assert "suppressions" not in _render()["runs"][0]["results"][0]


# --------------------------------------------------------------------------------------------
# Rules table
# --------------------------------------------------------------------------------------------


def test_repeated_rule_ids_share_one_rule_entry_and_index() -> None:
    doc = _render(
        findings=[
            {"rule_id": "dup", "severity": "high", "message": "m", "files": ["a.py"]},
            {"rule_id": "dup", "severity": "high", "message": "m", "files": ["b.py"]},
            {"rule_id": "other", "severity": "low", "message": "n", "files": ["c.py"]},
        ]
    )
    run = doc["runs"][0]
    assert [r["id"] for r in run["tool"]["driver"]["rules"]] == ["dup", "other"]
    # ruleIndex must actually resolve -- a stale index silently attributes a finding to the wrong
    # rule in every consumer that renders the rules table.
    for result in run["results"]:
        assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == result["ruleId"]


def test_a_finding_with_no_rule_id_is_attributed_rather_than_dropped() -> None:
    # Silent loss is the defect class this whole campaign exists to remove (#292). An
    # unattributable finding is still a finding.
    doc = _render(findings=[{"severity": "high", "message": "m", "files": ["a.py"]}])
    assert doc["runs"][0]["results"][0]["ruleId"] == "tensor-grep/unattributed"


def test_rendering_is_deterministic_for_a_given_payload() -> None:
    # A SARIF file that differs run-to-run defeats the baseline workflow it exists to feed, and
    # would make the #311 determinism gate unable to cover this surface.
    payload = _payload()
    assert scan_payload_to_sarif(payload, tool_version=_VERSION) == scan_payload_to_sarif(
        payload, tool_version=_VERSION
    )


# --------------------------------------------------------------------------------------------
# Repo-relative URIs -- the defect the unit tests were blind to until the real command ran
# --------------------------------------------------------------------------------------------


def test_an_absolute_path_under_the_scan_root_becomes_repo_relative() -> None:
    # Found by dogfooding, not by reading the SARIF spec: the scan payload reports ABSOLUTE paths
    # even for `--path .`, and GitHub code scanning resolves artifactLocation.uri against the
    # repository root. An absolute URI is valid SARIF that attaches to no file in the PR, so the
    # annotation silently never appears -- the feature looks like it works and delivers nothing.
    doc = scan_payload_to_sarif(
        _payload(
            findings=[
                {
                    "rule_id": "r",
                    "severity": "high",
                    "message": "m",
                    "files": ["/repo/src/app.py"],
                    "evidence": [],
                }
            ]
        ),
        tool_version=_VERSION,
        base_path="/repo",
    )
    uri = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ]
    assert uri == "src/app.py"


def test_a_path_outside_the_scan_root_stays_absolute() -> None:
    # CONTROL, and a deliberate design choice rather than a gap: relativising this would emit
    # `../elsewhere/x.py`, which consumers reject and which would misattribute the finding to a
    # file inside the repo. An honest absolute path is the better failure.
    doc = scan_payload_to_sarif(
        _payload(
            findings=[
                {
                    "rule_id": "r",
                    "severity": "high",
                    "message": "m",
                    "files": ["/elsewhere/x.py"],
                    "evidence": [],
                }
            ]
        ),
        tool_version=_VERSION,
        base_path="/repo",
    )
    uri = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ]
    assert uri == "/elsewhere/x.py"
    assert ".." not in uri


def test_without_a_base_path_the_path_is_left_alone() -> None:
    # CONTROL for the whole feature: relativisation must be opt-in, so a caller that has no
    # meaningful root (or a payload already carrying relative paths) is never mangled.
    doc = scan_payload_to_sarif(
        _payload(
            findings=[
                {
                    "rule_id": "r",
                    "severity": "high",
                    "message": "m",
                    "files": ["/repo/src/app.py"],
                    "evidence": [],
                }
            ]
        ),
        tool_version=_VERSION,
    )
    uri = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ]
    assert uri == "/repo/src/app.py"
