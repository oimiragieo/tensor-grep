"""Diff blast radius and review risk gating (P1 diff-impact).

Computes transitive blast radius for symbols modified in a git diff (working tree,
staged changes, or arbitrary ref/commit). Identifies downstream callers, affected
test files, calculates risk tiers, and supports CI gate failure thresholds.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import diff_impact_git as _dig
from tensor_grep.cli import lang_registry, repo_map
from tensor_grep.cli.repo_map import (
    _deadline_monotonic_from_seconds,
    _scan_did_not_finish,
    build_repo_map,
    build_symbol_blast_radius_from_map,
)
from tensor_grep.cli.subprocess_policy import (
    configured_git_timeout_seconds,
    deadline_capped_timeout_seconds,
    run_subprocess,
)

# Re-exported under the historical private names (tests and callers patch them here).
_ACTIVE_SESSIONS = _dig._ACTIVE_SESSIONS
_OID_RE = _dig.OID_RE
_BlobOverCap = _dig.BlobOverCap
_BlobUnavailable = _dig.BlobUnavailable
_c_quote_path = _dig.c_quote_path
_content_sessions = _dig.content_sessions
_git_env = _dig.git_env
_parse_cat_file_reply = _dig.parse_cat_file_reply
_parse_hash_reply = _dig.parse_hash_reply

# Digit groups are bounded: an unbounded \d+ let a 5000-digit hunk number reach int() and raise
# a bare ValueError that bypassed every DiffError handler (and the CLI exit-2 path).
_DIFF_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d{1,9})(?:,(?P<old_count>\d{1,9}))? "
    r"\+(?P<new_start>\d{1,9})(?:,(?P<new_count>\d{1,9}))? @@"
)
_GITLINK_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+ 160000$")
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


class DiffError(RuntimeError):
    """git diff could not be computed; the result must be reported incomplete, never empty."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _validate_ref(ref: str) -> str:
    if not ref or ref.startswith("-") or any(c in ref for c in "\x00\n\r"):
        raise DiffError("invalid_ref", f"invalid git ref: {ref!r}")
    return ref


def _hunk_new_range(match: re.Match[str]) -> tuple[int, int]:
    """The new-side changed line range of a parsed hunk header (shared by parser and validator)."""
    start = int(match.group("new_start"))
    count_str = match.group("new_count")
    count = int(count_str) if count_str is not None else 1
    if count == 0:
        # Pure deletion at line `start`, changed point is line start
        line_start = max(1, start)
        return line_start, line_start
    return start, start + count - 1


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge adjacent or overlapping inclusive ranges."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = [ordered[0]]
    for r_start, r_end in ordered[1:]:
        last_start, last_end = merged[-1]
        if r_start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, r_end))
        else:
            merged.append((r_start, r_end))
    return merged


_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _check_safe_rel_path(rel: str) -> None:
    """Refuse a diff path that is not a plain repo-relative path (reason: unsafe_path).

    Absolute, drive-qualified (C:), UNC, or any path with a `..` component could make
    `root / rel_path` point outside the repository.
    """
    segments = re.split(r"[\\/]", rel)
    if not rel or rel[0] in "/\\" or _DRIVE_RE.match(rel) or ".." in segments or "\x00" in rel:
        raise DiffError("unsafe_path", f"refusing diff path outside the repository: {rel!r}")


def _git_header_path(raw: str) -> Path | None:
    """Decode a ---/+++ header operand ('/dev/null' -> None, quoted C-string -> text)."""
    if raw == "/dev/null":
        return None
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        body, out, i = raw[1:-1], bytearray(), 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in "01234567":
                    j = i + 1
                    while j < len(body) and j < i + 4 and body[j] in "01234567":
                        j += 1
                    out.append(int(body[i + 1 : j], 8) & 0xFF)
                    i = j
                    continue
                if nxt in _C_ESCAPES:
                    out.append(_C_ESCAPES[nxt])
                    i += 2
                    continue
            out.extend(ch.encode("utf-8", errors="surrogateescape"))
            i += 1
        raw = out.decode("utf-8", errors="surrogateescape")
    elif raw.endswith("\t"):
        raw = raw[:-1]
    rel = raw[2:] if raw[:2] in ("a/", "b/") else raw
    _check_safe_rel_path(rel)
    return Path(rel)


def _diff_git_line_path(rest: str) -> Path | None:
    """Path from a `diff --git A B` operand pair when both sides name the same file.

    Used for deletions that carry no ---/+++ lines (empty or binary files). For a deletion the
    a/ and b/ paths are equal, which makes the otherwise ambiguous space split decidable.
    """
    if rest.startswith('"'):
        i = 1
        while i < len(rest):
            if rest[i] == "\\":
                i += 2
                continue
            if rest[i] == '"':
                break
            i += 1
        first, second = rest[: i + 1], rest[i + 2 :]
    else:
        n = len(rest)
        if n < 5 or n % 2 == 0 or rest[n // 2] != " ":
            return None
        first, second = rest[: n // 2], rest[n // 2 + 1 :]
    a_path, b_path = _git_header_path(first), _git_header_path(second)
    if a_path is None or a_path != b_path:
        return None
    return a_path


class DiffHunks(dict[Path, list[tuple[int, int]]]):
    """Parsed diff ranges per file, plus explicit identity sets for rangeless entries.

    A plain dict subclass so existing callers and equality checks keep working. Several kinds of
    entry have an empty range list, so "no ranges" never means "deleted": identity is explicit.
    `deleted_paths` comes only from `deleted file mode`; `binary_files` from `Binary files ...
    differ` outside a deletion; `mode_changed_files` from `old mode`/`new mode`; `submodule_changed_files` from a 160000
    (gitlink) mode, whose `Subproject commit` hunk is not source and carries no ranges.
    """

    binary_files: set[Path]
    deleted_paths: set[Path]
    mode_changed_files: set[Path]
    submodule_changed_files: set[Path]
    new_oids: dict[Path, str]

    def __init__(self) -> None:
        super().__init__()
        self.binary_files = set()
        self.deleted_paths = set()
        self.mode_changed_files = set()
        self.submodule_changed_files = set()
        self.new_oids = {}


_INDEX_LINE_RE = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)( [0-7]{6})?$")
_MODE_LINE_RE = re.compile(r"^(old|new) mode [0-7]{6}$")
_FILE_MODE_LINE_RE = re.compile(r"^(new|deleted) file mode [0-7]{6}$")
_SIMILARITY_LINE_RE = re.compile(r"^similarity index [0-9]{1,3}%$")
_UNMERGED_PREFIX = "* Unmerged path "


def _unparsed(path_hint: str, detail: str) -> DiffError:
    return DiffError(
        "unparsed_git_output", f"cannot interpret git output for {path_hint}: {detail}"
    )


def _quoted_operand(raw: str, prefix: str) -> str:
    """Rebuild the diff --git operand for a rename/copy path (git quotes each operand alone)."""
    return f'"{prefix}{raw[1:]}' if raw.startswith('"') else f"{prefix}{raw}"


def _close_hunk(hint: str, hunk: list[Any] | None) -> None:
    """Require a hunk body to carry exactly the line counts its header declares."""
    if hunk is None:
        return
    header, exp_old, exp_new, seen_old, seen_new = hunk
    if (seen_old, seen_new) != (exp_old, exp_new):
        raise _unparsed(
            hint,
            f"hunk {header[:60]!r} declares -{exp_old}/+{exp_new} lines but its body has "
            f"-{seen_old}/+{seen_new} (surplus, missing or truncated lines)",
        )


def _validate_record(record: list[str]) -> tuple[Path, str, list[tuple[int, int]]] | None:
    """Classify ONE `diff --git` record; raise DiffError unless it is a recognised shape.

    Returns the (path, kind, ranges) the parser MUST produce for this record, or None for a
    verified pure rename/copy (which records nothing by design). A record is accepted only if it
    has a recoverable identity and a well-formed body, so validator and parser cannot disagree
    about whether it is a change, or about which lines changed.
    """
    rest = record[0][len("diff --git ") :]
    same_path = _diff_git_line_path(rest)
    hint = str(same_path or rest[:120])
    has_minus = has_plus = binary = body = False
    minus_raw = plus_raw = binary_line = ""
    hunks = 0
    old_mode = new_mode = new_file = deleted_file = False
    sim100 = False
    is_sub = False
    hunk: list[Any] | None = None
    ranges: list[tuple[int, int]] = []
    pair: dict[str, str] = {}
    for line in record[1:]:
        if line.startswith(_UNMERGED_PREFIX):
            raise DiffError(
                "unmerged_paths",
                f"unmerged path {line[len(_UNMERGED_PREFIX) :]!r}: resolve or stage the merge "
                "conflicts first, then re-run",
            )
        if line.startswith("@@ "):
            # the parser's own regex: a hunk header it cannot read would silently record nothing
            hm = _DIFF_HUNK_RE.match(line)
            if hm is None:
                raise _unparsed(hint, f"malformed hunk header {line[:80]!r}")
            _close_hunk(hint, hunk)
            hunk = [
                line,
                int(hm.group("old_count")) if hm.group("old_count") is not None else 1,
                int(hm.group("new_count")) if hm.group("new_count") is not None else 1,
                0,
                0,
            ]
            ranges.append(_hunk_new_range(hm))
            hunks += 1
            body = True
            continue
        if body:
            assert hunk is not None
            lead = line[:1]
            if lead == " ":
                hunk[3] += 1
                hunk[4] += 1
            elif lead == "-":
                hunk[3] += 1
            elif lead == "+":
                hunk[4] += 1
            elif lead != "\\":  # "\ No newline at end of file" counts toward neither side
                raise _unparsed(hint, f"stray line {line[:80]!r} inside a hunk")
            continue
        if line.startswith("--- "):
            has_minus = True
            minus_raw = line[4:]
        elif line.startswith("+++ "):
            has_plus = True
            plus_raw = line[4:]
        elif _INDEX_LINE_RE.match(line):
            is_sub = is_sub or line.endswith(" 160000")
        elif m := _MODE_LINE_RE.match(line):
            is_sub = is_sub or line.endswith(" 160000")
            if m.group(1) == "old":
                old_mode = True
            else:
                new_mode = True
        elif m := _FILE_MODE_LINE_RE.match(line):
            is_sub = is_sub or line.endswith(" 160000")
            if m.group(1) == "new":
                new_file = True
            else:
                deleted_file = True
        elif _SIMILARITY_LINE_RE.match(line):
            sim100 = line == "similarity index 100%"
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            kind, direction, operand = line.split(" ", 2)
            if not operand or f"{kind} {direction}" in pair:
                raise _unparsed(hint, f"malformed line {line[:80]!r}")
            pair[f"{kind} {direction}"] = operand
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            binary = True
            binary_line = line
        else:
            raise _unparsed(hint, f"unexpected line {line[:80]!r}")

    _close_hunk(hint, hunk)
    pair_dest: Path | None = None
    pair_src: Path | None = None
    if len(pair) == 2:
        for kind in ("rename", "copy"):
            src, dst = pair.get(f"{kind} from"), pair.get(f"{kind} to")
            if src is not None and dst is not None:
                expected = f"{_quoted_operand(src, 'a/')} {_quoted_operand(dst, 'b/')}"
                if rest == expected:
                    pair_dest = _git_header_path(_quoted_operand(dst, "b/"))
                    pair_src = _git_header_path(_quoted_operand(src, "a/"))
    if pair and pair_dest is None:
        raise _unparsed(hint, "rename/copy metadata is incomplete or does not match the header")
    identity = pair_dest if pair_dest is not None else same_path
    add_del = new_file or deleted_file

    if has_minus and has_plus and hunks >= 1:
        # (a) text change (also covers submodule gitlink hunks); path must be recoverable
        old_p, new_p = _git_header_path(minus_raw), _git_header_path(plus_raw)
        exp_old = pair_src if pair_dest is not None else same_path
        exp_new = pair_dest if pair_dest is not None else same_path
        if (old_p is not None and old_p != exp_old) or (new_p is not None and new_p != exp_new):
            raise _unparsed(hint, "---/+++ paths do not match the diff --git header operands")
        if (new_file and old_p is not None) or (deleted_file and new_p is not None):
            raise _unparsed(hint, "add/delete disagrees with /dev/null in its ---/+++ lines")
        if (old_p is None and not new_file) or (new_p is None and not deleted_file):
            raise _unparsed(hint, "/dev/null in ---/+++ without an add/delete mode line")
        target = new_p if new_p is not None else old_p
        if target is None:
            raise _unparsed(hint, "no recoverable path in ---/+++ lines")
        kept = [] if (deleted_file or is_sub) else _merge_ranges(ranges)
        return target, "deleted" if deleted_file else "text", kept
    if (hunks == 0 and (has_minus or has_plus)) or (hunks and not (has_minus and has_plus)):
        raise _unparsed(hint, "incomplete ---/+++/@@ structure")
    if binary:  # (b)
        if identity is None:
            raise _unparsed(hint, "binary record has no recoverable path")
        if new_file and not binary_line.startswith("Binary files /dev/null and "):
            raise _unparsed(hint, "added binary disagrees with /dev/null")
        if deleted_file and not binary_line.endswith(" and /dev/null differ"):
            raise _unparsed(hint, "deleted binary disagrees with /dev/null")
        return identity, "deleted" if deleted_file else "binary", []
    if add_del and not pair and not (old_mode or new_mode):
        if same_path is None:  # (a) header-only add/delete needs a same-file operand pair
            raise _unparsed(hint, "header-only add/delete has no recoverable path")
        return same_path, "deleted" if deleted_file else "added", []
    if pair and not sim100:
        raise _unparsed(hint, "rename/copy record is not 100% similar and has no content")
    if old_mode and new_mode and not add_del:  # (c) mode-only (optionally a verified pure rename)
        if identity is None:
            raise _unparsed(hint, "mode-only record has no recoverable path")
        return identity, "mode", []
    if pair_dest is not None and not (old_mode or new_mode or add_del):
        return None  # (e) verified pure rename/copy
    raise _unparsed(hint, "record matches no supported shape")


def _validate_records(diff_text: str) -> list[tuple[Path, str, list[tuple[int, int]]]]:
    """Validate EVERY record of git's output; return what the parser must have produced."""
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in diff_text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    records: list[list[str]] = []
    for line in lines:
        if line.startswith("diff --git "):
            records.append([line])
        elif line.startswith(_UNMERGED_PREFIX):
            raise DiffError(
                "unmerged_paths",
                f"unmerged path {line[len(_UNMERGED_PREFIX) :]!r}: resolve or stage the merge "
                "conflicts first, then re-run",
            )
        elif records:
            records[-1].append(line)
        elif line.strip():
            raise _unparsed("<output>", f"line outside any diff record: {line[:80]!r}")
    expectations: list[tuple[Path, str, list[tuple[int, int]]]] = []
    for record in records:
        expected = _validate_record(record)
        if expected is not None:
            expectations.append(expected)
    return expectations


def _parse_checked(diff_text: str) -> DiffHunks:
    """Parse, but never let unexplained git output silently become 'no changes'.

    Cross-check: every record the validator accepted as a change MUST appear in the parsed
    result with exactly the ranges the validator derived from the hunk bodies, and the parser
    may not invent entries, so validator and parser can never drift apart without failing closed.
    """
    parsed = parse_git_diff_hunks(diff_text)  # raises on combined (merge) diffs
    expected_ranges: dict[Path, list[tuple[int, int]]] = {}
    for path, kind, ranges in _validate_records(diff_text):
        missing = path not in parsed
        if kind == "deleted":
            missing = missing or path not in getattr(parsed, "deleted_paths", set())
        elif kind == "binary":
            missing = missing or path not in getattr(parsed, "binary_files", set())
        elif kind == "mode":
            missing = missing or path not in getattr(parsed, "mode_changed_files", set())
        if missing:
            raise _unparsed(
                str(path), f"validated {kind} record was not recorded by the parser (drift)"
            )
        expected_ranges.setdefault(path, []).extend(ranges)
    for path, ranges in expected_ranges.items():
        if parsed.get(path, []) != _merge_ranges(ranges):
            raise _unparsed(
                str(path),
                f"parsed ranges {parsed.get(path)} differ from the hunk bodies "
                f"{_merge_ranges(ranges)} (drift)",
            )
    stray = sorted(str(p) for p in parsed if p not in expected_ranges)
    if stray:
        raise _unparsed(stray[0], "parser recorded a path no validated record accounts for (drift)")
    return parsed


def parse_git_diff_hunks(diff_text: str) -> DiffHunks:
    """Parse git diff hunk headers `@@ -l,s +start,count @@` into mapped 1-indexed line ranges per file.

    Returns a dict mapping relative file Path to a list of (start_line, end_line) inclusive tuples.
    Deleted files map to an empty range list and are recorded in `deleted_paths`. Added or
    modified binary files, empty added files and mode-only changes also map to an empty list
    (binary ones are additionally in `binary_files`, mode changes in `mode_changed_files`).
    A pure rename or copy with no content or mode change is NOT a change and yields no entry.
    """
    result = DiffHunks()
    rename_path: Path | None = None
    is_deleted = False
    is_added = False
    mode_changed = False
    is_submodule = False
    new_oid: str | None = None
    current_file: Path | None = None
    old_path: Path | None = None
    in_header = False
    header_path: Path | None = None

    def flush_header_only_entry() -> None:
        # Header-only entries (no ---/+++/@@ lines) carry their change in the header flags.
        dest = rename_path or header_path
        deleted_path = header_path or old_path  # a text deletion also names itself in `---`
        if is_deleted and deleted_path is not None:
            result.setdefault(deleted_path, [])
            result.deleted_paths.add(deleted_path)
        elif dest is not None and (is_added or mode_changed):
            result.setdefault(dest, [])
        if is_submodule and dest is not None:
            result[dest] = []  # a gitlink hunk is "Subproject commit <sha>", not source lines
            result.submodule_changed_files.add(dest)
        if mode_changed and dest is not None and not is_deleted:
            result.mode_changed_files.add(dest)
        # the `index <old>..<new>` line names the blob of the NEW side of this record
        oid_dest = current_file or dest
        if new_oid and oid_dest is not None and not set(new_oid) <= {"0"}:
            result.new_oids[oid_dest] = new_oid

    # Split on literal LF only: str.splitlines() also breaks on U+2028/U+0085/U+000B/U+000C and
    # friends, which are legal in file names and would truncate the parsed path.
    for raw_line in diff_text.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if line.startswith(("diff --cc ", "diff --combined ", "@@@")):
            # Merge revisions produce a COMBINED diff that this parser cannot read; returning {}
            # would let a merge bypass risk gates, so fail closed instead.
            raise DiffError(
                "unsupported_combined_diff",
                "git produced a combined diff (merge revision), which diff-impact cannot "
                "analyse; diff against one parent instead, e.g. '<merge>^1..<merge>'",
            )
        if line.startswith("diff --git "):
            flush_header_only_entry()
            in_header = True
            old_path = None
            current_file = None
            header_path = _diff_git_line_path(line[len("diff --git ") :])
            rename_path = None
            is_deleted = False
            is_added = False
            mode_changed = False
            is_submodule = False
            new_oid = None
            continue

        if in_header and _GITLINK_INDEX_RE.match(line):
            is_submodule = True
            continue

        if in_header and (index_match := _INDEX_LINE_RE.match(line)):
            new_oid = index_match.group(2)
            continue

        if in_header and line.startswith("new file mode "):
            is_added = True
            is_submodule = is_submodule or line.endswith(" 160000")
            continue

        if in_header and line.startswith(("old mode ", "new mode ")):
            mode_changed = True
            is_submodule = is_submodule or line.endswith(" 160000")
            continue

        if in_header and line.startswith(("rename to ", "copy to ")):
            operand = line.split(" to ", 1)[1]
            # _git_header_path strips an a/ or b/ prefix, so give it one to strip
            rename_path = _git_header_path(
                f'"b/{operand[1:]}' if operand.startswith('"') else f"b/{operand}"
            )
            continue

        if (
            in_header
            and line.startswith("Binary files ")
            and line.endswith(" differ")
            and not is_deleted  # header state, never the text of this line: a legal file name
        ):  # can itself end in " and /dev/null"
            binary_path = rename_path or header_path
            if binary_path is not None:
                result.setdefault(binary_path, [])
                result.binary_files.add(binary_path)
            continue

        if in_header and line.startswith("deleted file mode "):
            # Empty and binary deletions emit no ---/+++ lines; the diff --git header is the only
            # place the path appears. Recorded at flush time from this per-file state.
            is_deleted = True
            is_submodule = is_submodule or line.endswith(" 160000")
            continue

        if in_header and line.startswith("--- "):
            old_path = _git_header_path(line[4:])
            continue

        if in_header and line.startswith("+++ "):
            new_path = _git_header_path(line[4:])
            if new_path is None:
                if old_path is not None:
                    result.setdefault(old_path, [])
                current_file = None
            else:
                current_file = new_path
            continue

        if line.startswith("@@ "):
            # Council round 5: ANY hunk line ends header state; headers never follow a hunk, so
            # removed lines rendered as "--- x" / "+++ y" in a body are never parsed as paths.
            in_header = False
            if current_file is None:
                continue
            match = _DIFF_HUNK_RE.match(line)
            if match:
                result.setdefault(current_file, []).append(_hunk_new_range(match))

    flush_header_only_entry()

    # Normalize / merge adjacent or overlapping ranges per file
    for file_path, ranges in list(result.items()):
        if ranges:
            result[file_path] = _merge_ranges(ranges)

    return result


def _git_toplevel(root: Path, deadline_monotonic: float | None = None) -> Path:
    """Return the git top level containing `root`; raise DiffError (fail closed) when unknown.

    `git diff --no-relative` reports top-level-relative paths, so every join, symbol extraction
    and repo-map scan must be rooted at the top level, not at the caller's working directory.
    """
    timeout = deadline_capped_timeout_seconds(
        configured_git_timeout_seconds(), deadline_monotonic=deadline_monotonic
    )
    if timeout is None:
        raise DiffError("deadline_exceeded")
    cmd = [
        "git",
        "-c",
        "core.quotepath=false",
        "-c",
        "core.fsmonitor=false",
        "rev-parse",
        "--show-toplevel",
    ]
    try:
        proc = run_subprocess(
            cmd,
            cwd=str(root),
            stdout=-1,
            stderr=-1,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=_git_env(),
            timeout_seconds=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiffError("git_diff_timeout") from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise DiffError("git_diff_failed", str(exc)) from exc
    top = (proc.stdout or "").rstrip("\r\n")
    if proc.returncode != 0 or not top:
        raise DiffError("git_diff_failed", (proc.stderr or "").strip()[:500] or "no git top level")
    return Path(top).resolve()


def extract_diff_hunks_from_git(
    ref: str | None = None,
    staged: bool = False,
    root: Path = Path("."),
    deadline_monotonic: float | None = None,
) -> dict[Path, list[tuple[int, int]]]:
    """Call git diff via run_subprocess with deadline capping and parse hunk ranges.

    Returns dict mapping file Path to 1-indexed (start_line, end_line) ranges.
    Raises DiffError (never returns an empty dict) when git cannot produce the diff.
    """
    cmd = [
        # core.fsmonitor=false: a repo-local .git/config (e.g. from an extracted archive) must not
        # get to run an fsmonitor hook during our read (council round 3, defence in depth).
        # No --no-renames (council round 5): it would turn a pure rename into delete-all + add-all,
        # reporting the old path as deleted and the whole new file as changed, where main reports
        # nothing. The opposite is also pinned (--find-renames below): diff.renames=false in a
        # user's config would produce exactly that delete-all + add-all for a pure rename.
        "git",
        "-c",
        "core.quotepath=false",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "-U0",
        "--no-ext-diff",
        "--no-textconv",
        # User git config can change the output SHAPE; pin it (each has a hostile-config test):
        # color.ui/color.diff=always wraps headers in ANSI escapes (parser matched nothing),
        # diff.relative=true rewrites paths against the cwd, and diff.interHunkContext merges
        # neighbouring hunks into ranges that include unchanged lines.
        "--no-color",
        "--no-relative",
        "--inter-hunk-context=0",
        # diff.submodule=log|diff replaces the gitlink hunk with a headerless "Submodule ..." line
        # and diff.ignoreSubmodules=all hides it; both made a changed gitlink read as no_changes.
        "--submodule=short",
        "--ignore-submodules=none",
        "--find-renames",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    ]
    if staged:
        cmd.append("--cached")
    if ref is not None:
        cmd += ["--end-of-options", _validate_ref(ref), "--"]

    base_timeout = configured_git_timeout_seconds()
    timeout = deadline_capped_timeout_seconds(base_timeout, deadline_monotonic=deadline_monotonic)
    if timeout is None:
        raise DiffError("deadline_exceeded")

    try:
        proc = run_subprocess(
            cmd,
            cwd=str(root),
            stdout=-1,
            stderr=-1,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            env=_git_env(),
            timeout_seconds=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiffError("git_diff_timeout") from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise DiffError("git_diff_failed", str(exc)) from exc

    if proc.returncode != 0:
        raise DiffError("git_diff_failed", (proc.stderr or "").strip()[:500])

    return _parse_checked(proc.stdout or "")


def _file_identity(path: Path) -> tuple[int, int, Path] | None:
    """(st_dev, st_ino, resolved path) of what `path` currently points at; None if unreadable."""
    try:
        st = os.stat(path, follow_symlinks=True)
        return st.st_dev, st.st_ino, path.resolve()
    except OSError:
        return None


def _extraction_errors() -> tuple[type[BaseException], ...]:
    """Exceptions an extractor may raise that mean "could not analyse this file"."""
    from tensor_grep.backends.base import BackendExecutionError

    return (
        OSError,
        ValueError,
        SyntaxError,
        UnicodeDecodeError,
        RecursionError,
        BackendExecutionError,
    )


def _read_blob(root: Path, oid: str, max_bytes: int) -> bytes:
    """Read the blob `oid` through the run's single `git cat-file --batch` process."""
    if not _OID_RE.match(oid):  # hex only, so it can never be an option (CWE-88)
        raise _BlobUnavailable(f"not a plausible object id: {oid!r}")
    active = _ACTIVE_SESSIONS.get()
    if active is not None:
        data: bytes = active.cat().exchange(oid, lambda out: _parse_cat_file_reply(out, max_bytes))
        return data
    with _content_sessions(root) as one_shot:  # direct caller outside a mapping run
        data = one_shot.cat().exchange(oid, lambda out: _parse_cat_file_reply(out, max_bytes))
        return data


def _worktree_hash(root: Path, rel_path: Path) -> str | None:
    """The object id git would give the working-tree file (filters/EOL conversion applied)."""
    request = _c_quote_path(str(rel_path))
    try:
        active = _ACTIVE_SESSIONS.get()
        if active is not None:
            result: str | None = active.hasher().exchange(request, _parse_hash_reply)
            return result
        with _content_sessions(root) as one_shot:
            result = one_shot.hasher().exchange(request, _parse_hash_reply)
            return result
    except _BlobUnavailable:
        return None


def _blob_hash_matches(data: bytes, oid_prefix: str) -> bool:
    header = f"blob {len(data)}\0".encode()
    return any(
        algo(header + data, usedforsecurity=False).hexdigest().startswith(oid_prefix)
        for algo in (hashlib.sha1, hashlib.sha256)
    )


def _extract_symbols(extract_path: Path) -> list[dict[str, Any]]:
    """Run the registry's extractor for `extract_path`; raises the narrow extraction errors."""
    spec = lang_registry.spec_for_path(extract_path)
    symbols: list[dict[str, Any]]
    if spec is not None and spec.extract_imports_and_symbols is not None:
        imports, symbols = spec.extract_imports_and_symbols(extract_path)
    else:
        imports, symbols = repo_map._imports_and_symbols_for_path(extract_path)
    if not symbols and not imports:
        # The Python extractor swallows its own read/decode/parse failures and returns
        # ([], []), indistinguishable from a symbol-free file. Re-check ONLY empty Python
        # results (so normal files cost nothing), with the extractor's reader rules (strict
        # UTF-8, then ast.parse). Tree-sitter languages' parse-gap is owned by wave 2a G1
        # (r25 disposition for diff_impact.py:164/:169), deliberately not rebuilt here.
        # Same language decision as the registry (which lowercases suffixes): `app.PY` is
        # handled by the Python extractor, so it gets the same re-check.
        if extract_path.suffix.lower() == ".py" or (
            spec is not None and spec is lang_registry.spec_for_path("x.py")
        ):
            ast.parse(extract_path.read_text(encoding="utf-8"))
    return symbols


def _overlapping_symbols(
    symbols: list[dict[str, Any]], rel_path: Path, line_ranges: list[tuple[int, int]]
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for sym in symbols:
        s_start = int(sym.get("start_line", sym.get("line", 1)))
        s_end = int(sym.get("end_line", s_start))

        # Check overlap between [s_start, s_end] and any [r_start, r_end]
        overlaps = any(max(s_start, r_start) <= min(s_end, r_end) for r_start, r_end in line_ranges)
        if overlaps:
            sym_copy = dict(sym)
            sym_copy["file"] = str(rel_path).replace("\\", "/")
            found.append(sym_copy)
    return found


def _map_blob_path(
    rel_path: Path,
    line_ranges: list[tuple[int, int]],
    root: Path,
    submodule_paths: set[Path],
    binary_paths: set[Path],
    new_oid: str | None,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Analyse the content the diff DESCRIBES (a git blob), never an unrelated working tree."""
    if rel_path in submodule_paths:  # a gitlink names a commit, not a blob: disclosed separately
        return "deleted", None, []
    if rel_path in binary_paths:  # binary content has no source lines to map
        return "analyzed", None, []
    if new_oid is None:
        # no `index` line: nothing was read from the new side (e.g. a pure mode change)
        if not line_ranges:
            return "analyzed", None, []
        return "not_analyzed", "blob_unavailable", []
    try:
        data = _read_blob(root, new_oid, repo_map._max_parse_bytes())
    except _BlobOverCap:
        return "not_analyzed", "over_cap", []
    except _BlobUnavailable:
        return "not_analyzed", "blob_unavailable", []
    if not _blob_hash_matches(data, new_oid):
        return "not_analyzed", "blob_unavailable", []

    # Extractors take a PATH: write the bytes to a temp file OUTSIDE the repo with the same
    # suffix (so the registry picks the same extractor), and always delete it.
    fd, name = tempfile.mkstemp(suffix=rel_path.suffix, prefix="tg-blob-")
    tmp_path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            symbols = _extract_symbols(tmp_path)
        except _extraction_errors() as exc:  # narrow on purpose
            return "not_analyzed", f"extraction_failed: {type(exc).__name__}", []
    finally:
        tmp_path.unlink(missing_ok=True)
    return "analyzed", None, _overlapping_symbols(symbols, rel_path, line_ranges)


def _map_one_path(
    rel_path: Path,
    line_ranges: list[tuple[int, int]],
    root: Path,
    deleted_paths: set[Path],
    submodule_paths: set[Path],
    binary_paths: set[Path] | None = None,
    new_oid: str | None = None,
    content_mode: str = "unverified",
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Map ONE changed path to exactly one outcome: (outcome, reason, symbols).

    Outcomes: "analyzed" (extractor succeeded, identity unchanged); "deleted" (a deliberate,
    separately disclosed handling: a deleted file or a submodule gitlink that is no longer a
    regular file); "not_analyzed" with a reason (path_escapes_root, file_missing,
    extraction_failed: <ExceptionType>, path_changed_during_analysis, blob_unavailable,
    over_cap). There is no branch that returns nothing, so a changed path can never leave the
    mapper unaccounted for.

    `content_mode` says where the new-side bytes come from: "blob" (staged / revision-range
    diffs: the git object named by the record's `index` line), "worktree" (the working-tree file,
    hash-verified against that line), or "unverified" (no git context, e.g. caller-supplied diff
    text: the working-tree file as-is).
    """
    # An explicit deletion is classified FIRST, before any working-tree inspection: what a deleted
    # path leaves behind (e.g. a symlink surviving a `git rm --cached`) says nothing about it. A
    # path that is deleted AND re-added (type change) has content ranges, or is a binary add, and
    # is still analysed, so the exemption never suppresses a surviving source file.
    if rel_path in deleted_paths and not line_ranges and rel_path not in (binary_paths or set()):
        return "deleted", None, []
    if content_mode == "blob":
        return _map_blob_path(
            rel_path, line_ranges, root, submodule_paths, binary_paths or set(), new_oid
        )
    full_path = root / rel_path
    # Reuse repo_map's containment guard (resolves symlinks/`..` on both sides): a diff path,
    # or a symlink in the repo, must never make us open a file outside `root`.
    if not repo_map._path_is_relative_to(full_path, root):
        return "not_analyzed", "path_escapes_root", []
    if not full_path.is_file():
        if rel_path in submodule_paths:  # a gitlink is a directory/absent: disclosed separately
            return "deleted", None, []
        return "not_analyzed", "file_missing", []
    try:
        if full_path.stat().st_size > repo_map._max_parse_bytes():
            return "not_analyzed", "over_cap", []  # the extractors return ([], []) over the cap
    except OSError:
        return "not_analyzed", "file_missing", []

    before = _file_identity(full_path)
    if content_mode == "worktree" and new_oid and not full_path.is_symlink():
        # The diff's new-side oid is the hash of the working-tree file AS DIFFED. If it no longer
        # matches, the file changed after the diff was taken: not the content the diff describes.
        actual = _worktree_hash(root, rel_path)
        if actual is None:
            return "not_analyzed", "blob_unavailable", []
        if not actual.startswith(new_oid):
            return "not_analyzed", "path_changed_during_analysis", []
    try:
        symbols = _extract_symbols(full_path)
    except _extraction_errors() as exc:  # narrow on purpose: anything else is a bug, not a gap
        return "not_analyzed", f"extraction_failed: {type(exc).__name__}", []

    after = _file_identity(full_path)
    if (
        before is None
        or after != before
        or not repo_map._path_is_relative_to(before[2], root)
        or not repo_map._path_is_relative_to(after[2], root)
    ):
        return "not_analyzed", "path_changed_during_analysis", []

    return "analyzed", None, _overlapping_symbols(symbols, rel_path, line_ranges)


def map_changed_lines_to_symbols(
    changed_files_with_lines: dict[Path, list[tuple[int, int]]],
    root: Path,
    not_analyzed: list[dict[str, str]] | None = None,
    content_mode: str = "unverified",
) -> list[dict[str, Any]]:
    """Use LANGUAGE_REGISTRY (or _imports_and_symbols_for_path fallback) to extract symbols for each file,

    checking which symbols span the modified lines.

    Accounting invariant: every changed path ends in EXACTLY ONE outcome (analyzed, deleted, or
    not_analyzed with a reason, appended to `not_analyzed`). After the loop the three sets must
    equal the input paths and be disjoint, otherwise DiffError("internal_accounting_error") is
    raised: the result then fails closed instead of silently shrinking.

    Swap detection (best effort): the file's identity (st_dev, st_ino, resolved path) is captured
    before and after extraction; if it changed, or the resolved path left `root`, that file's
    symbols are discarded (`path_changed_during_analysis`). Limits: a swap-and-restore (ABA) that
    is back in place when extraction finishes is not caught, and the extractor still opens the
    original pathname, so closing the race fully needs extraction through a verified/confined
    handle (tracker R-12).
    """
    deleted_paths: set[Path] = set(getattr(changed_files_with_lines, "deleted_paths", set()))
    submodule_paths: set[Path] = set(
        getattr(changed_files_with_lines, "submodule_changed_files", set())
    )
    binary_paths: set[Path] = set(getattr(changed_files_with_lines, "binary_files", set()))
    new_oids: dict[Path, str] = dict(getattr(changed_files_with_lines, "new_oids", {}))
    changed_symbols: list[dict[str, Any]] = []
    analyzed: set[Path] = set()
    deleted: set[Path] = set()
    failed: dict[Path, str] = {}

    sessions = _content_sessions(root) if content_mode != "unverified" else contextlib.nullcontext()
    with sessions:
        for rel_path, line_ranges in changed_files_with_lines.items():
            outcome, reason, symbols = _map_one_path(
                rel_path,
                line_ranges,
                root,
                deleted_paths,
                submodule_paths,
                binary_paths,
                new_oids.get(rel_path),
                content_mode,
            )
            if outcome == "analyzed":
                analyzed.add(rel_path)
                changed_symbols.extend(symbols)
            elif outcome == "deleted":
                deleted.add(rel_path)
            elif outcome == "not_analyzed" and reason:
                failed[rel_path] = reason
            # any other outcome leaves the path unaccounted for, which the invariant below rejects

    accounted = len(analyzed) + len(deleted) + len(failed)
    expected = set(changed_files_with_lines)
    if (analyzed | deleted | set(failed)) != expected or accounted != len(expected):
        missing = sorted(str(p) for p in expected - (analyzed | deleted | set(failed)))
        raise DiffError(
            "internal_accounting_error",
            f"changed paths not accounted for exactly once: {missing or 'overlapping outcomes'}",
        )
    if not_analyzed is not None:
        not_analyzed.extend(
            {"path": str(p).replace("\\", "/"), "reason": r} for p, r in sorted(failed.items())
        )

    # Sort deterministically
    changed_symbols.sort(
        key=lambda item: (item.get("file", ""), item.get("line", 0), item.get("name", ""))
    )
    return changed_symbols


def _is_test_path(path_str: str) -> bool:
    """Return True if path matches test conventions (tests/** or *_test.* or test_*.*)."""
    p = Path(path_str)
    parts = p.parts
    if any(part in ("tests", "test", "__tests__") for part in parts):
        return True
    name = p.name.lower()
    return name.startswith("test_") or name.endswith((
        "_test.py",
        "_test.go",
        "_test.rs",
        "_test.js",
        "_test.ts",
        ".test.js",
        ".test.ts",
        ".spec.js",
        ".spec.ts",
    ))


def _calculate_risk_tier(
    blast_radius_score: float, affected_files_count: int, callers_count: int
) -> str:
    """Calculate risk tier based on blast radius score, affected file count, and callers count.

    Tiers:
    - critical: score >= 0.7 or affected_files >= 25 or callers >= 50
    - high: score >= 0.4 or affected_files >= 10 or callers >= 20
    - medium: score >= 0.15 or affected_files >= 3 or callers >= 5
    - low: otherwise
    """
    if blast_radius_score >= 0.7 or affected_files_count >= 25 or callers_count >= 50:
        return "critical"
    if blast_radius_score >= 0.4 or affected_files_count >= 10 or callers_count >= 20:
        return "high"
    if blast_radius_score >= 0.15 or affected_files_count >= 3 or callers_count >= 5:
        return "medium"
    return "low"


def _content_mode(ref: str | None, staged: bool, *, from_git: bool) -> str:
    """Where the new side of the diff lives: the index/a revision ("blob") or the working tree."""
    if not from_git:
        return "unverified"  # caller-supplied diff text: no repository to read blobs from
    if staged:
        return "blob"  # `--cached` (with or without a ref): the new side is the index
    if ref is not None and (".." in ref or "^!" in ref or "^-" in ref):
        return "blob"  # `A..B` / `A...B` / `R^!`: the new side is a revision, not the working tree
    return "worktree"  # no ref, or a single rev compared with the working tree


def _empty_payload(
    root: Path,
    ref: str | None,
    staged: bool,
    *,
    partial: bool,
    reason: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "root": str(root).replace("\\", "/"),
        "ref": ref,
        "staged": staged,
        "changed_files": [],
        "changed_symbols": [],
        "callers": [],
        "affected_files": [],
        "affected_tests": [],
        "blast_radius_score": 0.0,
        "risk_tier": "low",
        "partial": partial,
        "downgrade_reasons": [reason] if partial and reason else [],
        "symbol_count": 0,
        "caller_count": 0,
        "file_count": 0,
        "test_count": 0,
        "deleted_files": [],
        "not_analyzed_paths": [],
        "binary_files": [],
        "mode_changed_files": [],
        "submodule_changed_files": [],
        "result_incomplete": partial,
        "incomplete_reason": reason if partial else None,
    }
    if partial:
        payload["error"] = error
    return payload


def build_diff_blast_radius(
    ref: str | None = None,
    staged: bool = False,
    root: Path = Path("."),
    max_depth: int = 3,
    deadline_seconds: float | None = None,
    diff_text: str | None = None,
    max_repo_files: int | None = None,
) -> dict[str, Any]:
    """Compute transitive blast radius and review risk for git diff changes.

    Collects changed symbols, runs blast radius for each symbol from repo_map,
    unions callers and downstream dependents, identifies affected test files,
    and returns Section 0 completeness payload.
    """
    deadline_monotonic = _deadline_monotonic_from_seconds(deadline_seconds)
    root = root.resolve()

    downgrade_reasons: list[str] = []
    partial_reasons: list[str] = []
    partial = False
    changed_files_with_lines: dict[Path, list[tuple[int, int]]]
    if diff_text is not None:
        try:
            changed_files_with_lines = _parse_checked(diff_text)
        except DiffError as exc:
            return _empty_payload(
                root, ref, staged, partial=True, reason=exc.reason, error=str(exc)
            )
    else:
        try:
            if ref is not None:
                _validate_ref(ref)  # before ANY git invocation, including rev-parse
            root = _git_toplevel(root, deadline_monotonic)
            changed_files_with_lines = extract_diff_hunks_from_git(
                ref=ref,
                staged=staged,
                root=root,
                deadline_monotonic=deadline_monotonic,
            )
        except DiffError as exc:
            return _empty_payload(
                root, ref, staged, partial=True, reason=exc.reason, error=str(exc)
            )

    changed_files = sorted([str(p).replace("\\", "/") for p in changed_files_with_lines.keys()])
    # Every changed path ends in exactly one mapper outcome. A path that could not be analysed
    # (escapes the root, vanished, extractor failed, swapped mid-run) is listed and fails closed.
    not_analyzed_paths: list[dict[str, str]] = []
    try:
        changed_symbols = map_changed_lines_to_symbols(
            changed_files_with_lines,
            root,
            not_analyzed_paths,
            _content_mode(ref, staged, from_git=diff_text is None),
        )
    except DiffError as exc:
        return _empty_payload(root, ref, staged, partial=True, reason=exc.reason, error=str(exc))
    for entry in not_analyzed_paths:
        reason_key = entry["reason"].split(":", 1)[0]
        partial = True
        if reason_key not in downgrade_reasons:
            downgrade_reasons.append(reason_key)
            partial_reasons.append(reason_key)
    binary_paths: set[Path] = getattr(changed_files_with_lines, "binary_files", set())
    binary_files = sorted(str(p).replace("\\", "/") for p in binary_paths)
    deleted_paths: set[Path] = getattr(changed_files_with_lines, "deleted_paths", set())
    deleted_files = sorted(str(p).replace("\\", "/") for p in deleted_paths)
    mode_paths: set[Path] = getattr(changed_files_with_lines, "mode_changed_files", set())
    mode_changed_files = sorted(str(p).replace("\\", "/") for p in mode_paths)
    submodule_paths: set[Path] = getattr(changed_files_with_lines, "submodule_changed_files", set())
    submodule_changed_files = sorted(str(p).replace("\\", "/") for p in submodule_paths)
    if submodule_changed_files:
        downgrade_reasons.append("submodule_changes_not_analyzed")
    if binary_files:
        downgrade_reasons.append("binary_files_not_analyzed")
    if deleted_files:
        downgrade_reasons.append("deleted_files_symbols_not_analyzed")

    # If no files or symbols changed
    if not changed_files:
        return _empty_payload(root, ref, staged, partial=False)

    # Build repo map
    repo_m = build_repo_map(
        root,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
    )

    if _scan_did_not_finish(repo_m):
        partial = True
        downgrade_reasons.append("repo_map_scan_incomplete")
        partial_reasons.append("repo_map_scan_incomplete")

    union_callers: list[dict[str, Any]] = []
    seen_callers: set[tuple[str, str, int]] = set()

    union_affected_files: set[str] = set(changed_files)
    union_tests: set[str] = set()
    per_symbol_scores: list[float] = []

    for sym in changed_symbols:
        symbol_name = str(sym.get("name", ""))
        if not symbol_name:
            continue

        # Check deadline before building symbol blast radius
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            partial = True
            if "deadline_exceeded" not in downgrade_reasons:
                downgrade_reasons.append("deadline_exceeded")
                partial_reasons.append("deadline_exceeded")
            break

        sym_radius = build_symbol_blast_radius_from_map(
            repo_m,
            symbol_name,
            max_depth=max_depth,
            deadline_monotonic=deadline_monotonic,
        )

        if sym_radius.get("partial"):
            partial = True
            if "symbol_blast_radius_partial" not in downgrade_reasons:
                downgrade_reasons.append("symbol_blast_radius_partial")
                partial_reasons.append("symbol_blast_radius_partial")

        # Collect callers
        for c in sym_radius.get("callers", []):
            c_file = str(c.get("file", "")).replace("\\", "/")
            c_sym = str(c.get("caller", c.get("name", "")))
            c_line = int(c.get("line", 0))
            key = (c_file, c_sym, c_line)
            if key not in seen_callers:
                seen_callers.add(key)
                caller_dict = dict(c)
                caller_dict["file"] = c_file
                union_callers.append(caller_dict)

        # Collect affected files
        for f in sym_radius.get("affected_files", []):
            union_affected_files.add(str(f).replace("\\", "/"))

        # Collect tests
        for t in sym_radius.get("tests", []):
            union_tests.add(str(t).replace("\\", "/"))

        score = float(sym_radius.get("blast_radius_score", 0.0))
        per_symbol_scores.append(score)

    # Also detect any tests in union_affected_files
    for f in union_affected_files:
        if _is_test_path(f):
            union_tests.add(f)

    # Sort results
    sorted_affected_files = sorted(union_affected_files)
    sorted_tests = sorted(union_tests)
    union_callers.sort(
        key=lambda c: (str(c.get("file", "")), int(c.get("line", 0)), str(c.get("caller", "")))
    )

    # Compute overall blast radius score
    if per_symbol_scores:
        overall_score = round(max(per_symbol_scores), 3)
    else:
        # If files changed without recognized symbols (e.g. config or docs), estimate from files
        overall_score = round(min(1.0, len(changed_files) * 0.05), 3)

    risk_tier = _calculate_risk_tier(overall_score, len(sorted_affected_files), len(union_callers))

    return {
        "root": str(root).replace("\\", "/"),
        "ref": ref,
        "staged": staged,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "callers": union_callers,
        "affected_files": sorted_affected_files,
        "affected_tests": sorted_tests,
        "blast_radius_score": overall_score,
        "risk_tier": risk_tier,
        "partial": partial,
        "downgrade_reasons": downgrade_reasons,
        "symbol_count": len(changed_symbols),
        "caller_count": len(union_callers),
        "file_count": len(sorted_affected_files),
        "test_count": len(sorted_tests),
        "deleted_files": deleted_files,
        "not_analyzed_paths": not_analyzed_paths,
        "binary_files": binary_files,
        "mode_changed_files": mode_changed_files,
        "submodule_changed_files": submodule_changed_files,
        "result_incomplete": partial,
        "incomplete_reason": (partial_reasons[0] if partial and partial_reasons else None),
    }


def diff_impact_command(
    *,
    ref: str | None = None,
    staged: bool = False,
    deadline: float | None = None,
    json_output: bool = False,
    fail_threshold: float | None = None,
    fail_on_risk: str | None = None,
) -> None:
    """CLI implementation for diff-impact command."""
    import typer

    risk_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    if fail_on_risk is not None and fail_on_risk.lower() not in risk_rank:
        typer.echo("Error: --fail-on-risk must be one of low, medium, high, critical", err=True)
        raise typer.Exit(2)

    payload = build_diff_blast_radius(
        ref=ref,
        staged=staged,
        deadline_seconds=deadline,
    )

    breached = False
    if (
        fail_threshold is not None
        and float(payload.get("blast_radius_score", 0.0)) > fail_threshold
    ):
        breached = True
    if fail_on_risk is not None:
        current_rank = risk_rank.get(str(payload.get("risk_tier", "low")).lower(), 1)
        if current_rank >= risk_rank[fail_on_risk.lower()]:
            breached = True

    incomplete = bool(payload.get("partial") or repo_map._scan_did_not_finish(payload))
    if incomplete:
        exit_reason = "incomplete"
    elif breached:
        exit_reason = "gate_breached"
    elif not payload.get("changed_files"):
        exit_reason = "no_changes"
    else:
        exit_reason = "ok"
    payload["gate_breached"] = breached
    payload["exit_reason"] = exit_reason

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        from tensor_grep.cli import main as cli_main

        cli_main._emit_scan_incompleteness_banner(payload)
        if payload.get("result_incomplete"):
            typer.echo(f"Diff impact INCOMPLETE: {payload.get('incomplete_reason')}", err=True)
        typer.echo(
            f"Diff impact: changed_files={payload['file_count']} changed_symbols={payload['symbol_count']} "
            f"callers={payload['caller_count']} affected_files={len(payload['affected_files'])} "
            f"affected_tests={payload['test_count']} score={payload['blast_radius_score']} risk={payload['risk_tier']}"
        )

    if incomplete or breached:
        raise typer.Exit(2)

    if not payload.get("changed_files"):
        raise typer.Exit(1)

    raise typer.Exit(0)


__all__ = [
    "DiffError",
    "build_diff_blast_radius",
    "diff_impact_command",
    "extract_diff_hunks_from_git",
    "map_changed_lines_to_symbols",
    "parse_git_diff_hunks",
]
