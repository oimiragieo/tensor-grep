"""Task 332: truncation gates must see BOTH truncation causes, not just the file cap.

An external dogfood of the PUBLISHED 1.99.4 wheel found `tg defs/callers/blast-radius` asserting
absence over a `--deadline`-truncated scan for a symbol that demonstrably exists. A mechanical
sweep of `repo_map.py` then showed the class: **3 of 3** readers of `scan_limit.possibly_truncated`
answered "was this truncated?" with "was the FILE CAP hit?", so every one of them read a
deadline-truncated scan as COMPLETE.

Each test below pairs the treatment arm with a CONTROL that must NOT change, because a gate that
answered "truncated" unconditionally would satisfy every positive assertion here while making a
genuine miss unreportable -- which is the failure mode
`_blast_radius_no_match_is_possibly_truncated`'s own docstring warns about (it would defeat the
literal-seed retry and both daemon fallbacks for every real no-match).

Bidirectional receipt, measured before the fix landed (pre-fix `repo_map.py` at d3bcb1b):
    result_incomplete : None                       (now True)
    message           : "No exact definition found for symbol 'absent_symbol_zzz'."   (bare)
    trust helper on a deadline no_match -> False   (now True)
Both controls below were already correct pre-fix and stay correct post-fix -- so these tests
discriminate the fix, they do not merely restate it.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.repo_map import (
    _blast_radius_no_match_is_possibly_truncated,
    _scan_did_not_finish,
    build_repo_map,
    build_symbol_defs_from_map,
)

_ABSENT = "absent_symbol_zzz_7f3a9c"


def _single_file_map(tmp_path: Path) -> dict:
    (tmp_path / "a.py").write_text("def present():\n    return 1\n", encoding="utf-8")
    return build_repo_map(str(tmp_path))


class TestScanDidNotFinishPredicate:
    """The shared predicate mirrors `main._scan_incomplete`'s two-cause contract."""

    def test_file_cap_truncation_counts(self) -> None:
        assert _scan_did_not_finish({"scan_limit": {"possibly_truncated": True}})

    def test_deadline_truncation_counts(self) -> None:
        assert _scan_did_not_finish({"deadline_limit": {"deadline_exceeded": True}})
        assert _scan_did_not_finish({"partial": True})

    def test_complete_scan_is_not_truncated(self) -> None:
        # CONTROL. Without these, a predicate hardcoded to `return True` passes every
        # positive assertion in this file.
        assert not _scan_did_not_finish({})
        assert not _scan_did_not_finish({"scan_limit": {"possibly_truncated": False}})
        assert not _scan_did_not_finish({"deadline_limit": {"deadline_exceeded": False}})

    def test_output_cap_alone_is_not_a_scan_truncation(self) -> None:
        # CONTROL for the boundary `_scan_incomplete` deliberately draws: an OUTPUT cap is a
        # COMPLETE analysis capped for display. Treating it as a scan truncation would flip
        # output-cap-only invocations to exit 2 and break the output-cap-stays-0 pins.
        assert not _scan_did_not_finish({"output_limit": {"primary_truncated": True}})


class TestBlastRadiusNoMatchTrust:
    """The helper gates the literal-seed retry + both daemon fallbacks."""

    def test_deadline_truncated_no_match_is_untrustworthy(self) -> None:
        # THE FIX. Pre-fix this returned False, so a deadline-truncated no_match -- the case
        # that can carry `files_scanned: 0` -- skipped the rescue entirely.
        assert _blast_radius_no_match_is_possibly_truncated({"no_match": True, "partial": True})

    def test_file_cap_truncated_no_match_still_untrustworthy(self) -> None:
        assert _blast_radius_no_match_is_possibly_truncated({
            "no_match": True,
            "scan_limit": {"possibly_truncated": True},
        })

    def test_no_match_on_a_complete_scan_stays_trustworthy(self) -> None:
        # CONTROL, and the one the helper's docstring cares most about: a no_match on a COMPLETE
        # map is a REAL miss. Marking it untrustworthy would fire retries and daemon fallback on
        # every genuine no-match in the repo.
        assert not _blast_radius_no_match_is_possibly_truncated({"no_match": True})

    def test_truncation_without_a_no_match_does_not_fire(self) -> None:
        # CONTROL: the gate is `no_match AND unfinished`, never truncation alone.
        assert not _blast_radius_no_match_is_possibly_truncated({"partial": True})


class TestDefsAbsenceClaimUnderTruncation:
    """`defs`' no_match branch: the prose and the incompleteness mark, per cause."""

    def test_deadline_truncated_defs_discloses_and_names_the_deadline_knob(
        self, tmp_path: Path
    ) -> None:
        repo_map = _single_file_map(tmp_path)
        repo_map["partial"] = True
        repo_map["deadline_limit"] = {
            "deadline_exceeded": True,
            "files_scanned": 0,
            "files_total": 9,
        }

        payload = build_symbol_defs_from_map(repo_map, _ABSENT)

        assert payload.get("result_incomplete") is True
        message = payload.get("message") or ""
        assert "--deadline" in message
        # It must name the knob that can actually fix it. Sending an agent to
        # `--max-repo-files` when the clock was the binding constraint is advice it cannot use.
        assert "--max-repo-files" not in message

    def test_complete_scan_absence_stays_a_clean_miss(self, tmp_path: Path) -> None:
        # CONTROL. A genuinely absent symbol on a COMPLETE scan must still say so plainly, with
        # no truncation caveat and no incompleteness mark -- otherwise every honest miss in the
        # repo starts claiming it might be wrong.
        repo_map = _single_file_map(tmp_path)

        payload = build_symbol_defs_from_map(repo_map, _ABSENT)

        assert payload.get("no_match") is True
        assert not payload.get("result_incomplete")
        message = payload.get("message") or ""
        assert "--deadline" not in message
        assert "--max-repo-files" not in message

    def test_file_cap_arm_keeps_its_original_sentence(self, tmp_path: Path) -> None:
        # CONTROL / refactor regression guard. The count arm was ALREADY honest before task 332.
        # Routing both causes through one shared branch would have had to pick a single knob for
        # both, silently degrading this arm's advice -- so pin it verbatim.
        repo_map = _single_file_map(tmp_path)
        repo_map["scan_limit"] = {"possibly_truncated": True, "scanned_files": 1}

        payload = build_symbol_defs_from_map(repo_map, _ABSENT)

        message = payload.get("message") or ""
        assert "--max-repo-files" in message
        assert "--deadline" not in message
