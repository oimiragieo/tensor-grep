"""SARIF v2.1.0 rendering for ``tg scan`` findings (task #310).

SARIF is how findings leave this tool and enter something that can act on them -- GitHub code
scanning, Azure DevOps, SonarQube, Defect Dojo. Until this module existed, ``tg scan``'s findings
were trapped in a tg-shaped JSON envelope that every consumer had to be taught to read, which is
the difference between a scanner and a scanner an enterprise can adopt.

Three design decisions carry most of the weight, all of them consequences of rules this repo
learned the hard way:

1.  **Incompleteness is rendered, not dropped.** ``invocations[].executionSuccessful`` is false
    whenever the scan payload says it was partial. A SARIF file that reports "no results" for a
    scan that could not read half the tree is the #276 defect wearing a standard schema -- a NEW
    fail-open surface, and a worse one, because the consumer is a CI gate that merges on green.
    ``docs/CONTRACTS.md`` section 0 pins the completeness contract; this is that contract's SARIF
    projection.

2.  **The severity map is an allow-list, never a pass-through** (#282). SARIF's ``level`` is a
    closed enum of four values; tg's severities are open (built-in packs use ``high``/``medium``,
    the payload defaults to ``warning``, and a user's own sgconfig rule may say anything at all).
    Passing an unrecognised string through would emit invalid SARIF that a consumer rejects
    wholesale -- one bad rule silently losing every finding in the run. Unknown values map to the
    payload's own default and are RECORDED on the result, so the tool's non-comprehension is
    visible rather than laundered into a confident-looking level.

3.  **URIs are POSIX-normalised AND repo-relative.** SARIF requires forward slashes, and GitHub
    code scanning resolves ``artifactLocation.uri`` against the repository root. The scan payload
    reports ABSOLUTE paths even for ``--path .``, so a pass-through renderer emits
    ``C:/Users/.../src/app.py`` -- valid SARIF that attaches to no file in the pull request. Both
    halves of this were found by running the real command, not by reading the spec; the unit tests
    used relative fixtures and were blind to it.

SCOPE, stated so nobody infers more than is here: the completeness signal is only as good as the
payload's, and ``_run_ast_scan_payload``'s disclosure covers the REGEX leg (#299). The AST leg
reaches files through the backends and is not audited. ``executionSuccessful: true`` therefore
means "the regex leg reported no skips", not "every byte was read".
"""

from __future__ import annotations

from typing import Any

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"

_TOOL_NAME = "tensor-grep"
_TOOL_INFORMATION_URI = "https://github.com/oimiragieo/tensor-grep"

# The fingerprint key is versioned because `partialFingerprints` is what a consumer uses to track
# a finding ACROSS runs. Changing how the fingerprint is computed without changing this key would
# silently re-open every previously-triaged finding as new; bumping the key is the honest signal
# that old fingerprints do not compare.
FINGERPRINT_KEY = "tensorGrepRuleFingerprint/v1"

# SARIF 2.1.0 section 3.27.10: the `level` property is exactly one of these.
_VALID_SARIF_LEVELS = frozenset({"none", "note", "warning", "error"})

# Allow-list, deliberately NOT a deny-list (#282: "confirm it is NOT X" fails open on any value the
# author did not foresee). Keys are lowercased before lookup. Covers tg's built-in packs
# (high/medium), the payload's own default (warning), and ast-grep's vocabulary, since a user
# sgconfig rule is authored in ast-grep's terms.
_SEVERITY_TO_SARIF_LEVEL = {
    # tg built-in rule packs
    "high": "error",
    "medium": "warning",
    "low": "note",
    # ast-grep / common linter vocabulary
    "error": "error",
    "critical": "error",
    "warning": "warning",
    "warn": "warning",
    "info": "note",
    "note": "note",
    "hint": "note",
    "off": "none",
    "none": "none",
}

# What an unrecognised severity becomes. Matches `rule.get("severity", "warning")` at
# ast_workflows.py so an absent severity and an unintelligible one land in the same place.
_UNKNOWN_SEVERITY_LEVEL = "warning"


def _normalize_uri(path: str, base_path: str | None = None) -> str:
    """A repo-relative, forward-slashed URI.

    Two corrections, both found by running the real command rather than by reading the schema:

    1.  **Separators.** Windows hands us ``src\\tensor_grep\\cli\\main.py``; SARIF requires
        ``src/tensor_grep/cli/main.py``.

    2.  **Absoluteness.** ``_run_ast_scan_payload`` reports absolute paths even for ``--path .``,
        so a naive render emits ``C:/Users/.../src/app.py``. That is *valid* SARIF and *useless*
        to the main consumer: GitHub code scanning resolves ``artifactLocation.uri`` against the
        repository root, so an absolute URI attaches the finding to nothing and the annotation
        never appears on the pull request. The scan root is therefore stripped when the path is
        genuinely under it.

    A path OUTSIDE ``base_path`` is left absolute on purpose. The alternative -- ``..`` segments
    climbing out of the repo -- is rejected by consumers and would misattribute the finding; an
    honest absolute path is the better failure.
    """
    if base_path:
        try:
            from pathlib import Path

            relative = Path(path).relative_to(Path(base_path))
        except (ValueError, OSError):
            # ValueError: not under the base. OSError: a malformed path on this platform. Either
            # way the original is the honest answer.
            pass
        else:
            path = str(relative)
    return path.replace("\\", "/")


def _sarif_level(severity: Any) -> tuple[str, str | None]:
    """Map a tg severity onto SARIF's closed ``level`` enum.

    Returns ``(level, unmapped_original_or_None)``. The second element is the disclosure channel:
    when it is not None the caller records the original string on the result, so "tg did not
    recognise this severity" survives into the output instead of being flattened into a level the
    tool never actually derived.
    """
    if not isinstance(severity, str):
        # `None` is the ordinary case -- main.py:6412 stores `rule.get("severity")` with no
        # default, so a rule without one arrives here as None. That is absence, not a
        # misunderstanding, so it is not reported as unmapped.
        return _UNKNOWN_SEVERITY_LEVEL, None if severity is None else str(severity)
    key = severity.strip().lower()
    mapped = _SEVERITY_TO_SARIF_LEVEL.get(key)
    if mapped is None:
        return _UNKNOWN_SEVERITY_LEVEL, severity
    return mapped, None


def _finding_locations(
    finding: dict[str, Any], base_path: str | None = None
) -> list[dict[str, Any]]:
    """Physical locations for one finding, preferring evidence line numbers over bare file names.

    A finding carries `evidence` rows (file + optional snippets with line numbers) and a flat
    `files` list. Evidence is strictly better when present because it can point at a line; `files`
    is the fallback for the paths that produced a match count but no retained snippet.
    """
    locations: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()

    for row in finding.get("evidence") or []:
        if not isinstance(row, dict):
            continue
        file_path = row.get("file")
        if not isinstance(file_path, str) or not file_path:
            continue
        snippets = row.get("snippets") or []
        emitted_for_file = False
        for snippet in snippets:
            if not isinstance(snippet, dict):
                continue
            line = snippet.get("line_number")
            if not isinstance(line, int) or line < 1:
                # SARIF regions are 1-based; a 0 or None would make the region invalid, so the
                # location degrades to file-level rather than emitting a malformed region.
                continue
            key = (file_path, line)
            if key in seen:
                continue
            seen.add(key)
            locations.append({
                "physicalLocation": {
                    "artifactLocation": {"uri": _normalize_uri(file_path, base_path)},
                    "region": {"startLine": line},
                }
            })
            emitted_for_file = True
        if not emitted_for_file and (file_path, None) not in seen:
            seen.add((file_path, None))
            locations.append({
                "physicalLocation": {
                    "artifactLocation": {"uri": _normalize_uri(file_path, base_path)}
                }
            })

    for file_path in finding.get("files") or []:
        if not isinstance(file_path, str) or not file_path:
            continue
        if any(k[0] == file_path for k in seen):
            continue
        seen.add((file_path, None))
        locations.append({
            "physicalLocation": {"artifactLocation": {"uri": _normalize_uri(file_path, base_path)}}
        })

    return locations


def _invocation(payload: dict[str, Any]) -> dict[str, Any]:
    """The run's honesty record.

    ``executionSuccessful`` is the single field a CI consumer is most likely to gate on, so it
    carries the completeness verdict rather than "did the process exit 0". A scan that skipped
    unreadable files DID run, and its results ARE valid as far as they go -- but reporting it as a
    successful execution would let a gate treat a partial scan as a clean bill of health.
    """
    unreadable = payload.get("unreadable_paths")
    is_partial = bool(payload.get("partial")) or bool(unreadable)

    invocation: dict[str, Any] = {"executionSuccessful": not is_partial}
    if not is_partial:
        return invocation

    reason = payload.get("partial_reason")
    notifications: list[dict[str, Any]] = []

    if isinstance(unreadable, dict):
        count = unreadable.get("count")
        sample = unreadable.get("sample") or []
        readable_sample = ", ".join(str(s) for s in sample if s)
        text = f"{count} path(s) could not be read and were skipped."
        if readable_sample:
            text += f" For example: {readable_sample}."
        text += (
            " Findings below cover only what was readable; absence of a finding for a skipped"
            " path is not evidence that the path is clean."
        )
        notification: dict[str, Any] = {
            "level": "warning",
            "message": {"text": text},
            "descriptor": {"id": "tensor-grep/unreadable-paths"},
        }
        if isinstance(count, int):
            notification["properties"] = {"unreadablePathCount": count}
        notifications.append(notification)

    if isinstance(reason, str) and reason:
        notifications.append({
            "level": "warning",
            "message": {"text": reason},
            "descriptor": {"id": "tensor-grep/partial-scan"},
        })

    if notifications:
        invocation["toolExecutionNotifications"] = notifications
    return invocation


def scan_payload_to_sarif(
    payload: dict[str, Any],
    *,
    tool_version: str,
    base_path: str | None = None,
    version_unavailable: bool = False,
) -> dict[str, Any]:
    """Render a ``tg scan --json`` payload as a SARIF v2.1.0 log.

    Pure: no I/O, no clock, no environment. That is what lets the tests assert on the whole
    document rather than on a fragment, and what keeps the output byte-stable for a given payload
    (a SARIF file that differs run-to-run defeats the baseline/suppression workflow it exists to
    feed).

    ``version_unavailable`` (A3 MEDIUM, PR #1070): the caller's version lookup can silently
    degrade to a placeholder on a double metadata failure (``importlib.metadata`` AND
    ``pyproject.toml`` both unreadable/unparsable). That degradation must be OBSERVABLE in the
    provenance a security-scan consumer trusts, not just embedded unlabeled in ``tool_version``
    -- so when set, ``run.properties.tensorGrepVersionUnavailable`` is stamped ``True``.
    """
    findings = payload.get("findings") or []

    rules: list[dict[str, Any]] = []
    rule_index_by_id: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule_id = finding.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            # A finding with no rule id cannot be attributed, and SARIF's `ruleId` is how a
            # consumer dedupes and suppresses. Dropping it silently would be a silent loss, so it
            # is attributed to an explicit sentinel instead.
            rule_id = "tensor-grep/unattributed"

        level, unmapped_severity = _sarif_level(finding.get("severity"))
        message = finding.get("message")
        if not isinstance(message, str) or not message:
            message = f"Rule {rule_id} matched."

        if rule_id not in rule_index_by_id:
            rule_index_by_id[rule_id] = len(rules)
            rule: dict[str, Any] = {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": message},
                "defaultConfiguration": {"level": level},
            }
            language = finding.get("language")
            if isinstance(language, str) and language:
                rule["properties"] = {"language": language}
            rules.append(rule)

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "ruleIndex": rule_index_by_id[rule_id],
            "level": level,
            "message": {"text": message},
        }

        locations = _finding_locations(finding, base_path)
        if locations:
            result["locations"] = locations

        fingerprint = finding.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            result["partialFingerprints"] = {FINGERPRINT_KEY: fingerprint}

        if finding.get("status") == "suppressed":
            # SARIF 2.1.0 section 3.27.23: a result carrying a non-empty `suppressions` array is
            # suppressed. `external` is the correct kind -- the suppression came from a file
            # supplied on the command line, not from an in-source annotation.
            result["suppressions"] = [{"kind": "external"}]

        properties: dict[str, Any] = {}
        if unmapped_severity is not None:
            properties["tensorGrepUnmappedSeverity"] = unmapped_severity
        matches = finding.get("matches")
        if isinstance(matches, int):
            properties["tensorGrepMatchCount"] = matches
        if properties:
            result["properties"] = properties

        results.append(result)

    driver: dict[str, Any] = {
        "name": _TOOL_NAME,
        "informationUri": _TOOL_INFORMATION_URI,
        "version": tool_version,
        "semanticVersion": tool_version,
        "rules": rules,
    }

    run: dict[str, Any] = {
        "tool": {"driver": driver},
        "invocations": [_invocation(payload)],
        "results": results,
    }

    run_properties: dict[str, Any] = {}
    ruleset = payload.get("ruleset")
    if isinstance(ruleset, str) and ruleset:
        run_properties["tensorGrepRuleset"] = ruleset
    if version_unavailable:
        run_properties["tensorGrepVersionUnavailable"] = True
    if run_properties:
        run["properties"] = run_properties

    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [run],
    }
