"""Diff blast radius and review risk gating (P1 diff-impact).

Computes transitive blast radius for symbols modified in a git diff (working tree,
staged changes, or arbitrary ref/commit). Identifies downstream callers, affected
test files, calculates risk tiers, and supports CI gate failure thresholds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

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

_DIFF_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_GITLINK_INDEX_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+ 160000$")
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


# Environment variables that change `git diff` output or which repository/index git reads. The
# argv pins the config twin of these; this is the environment twin. GIT_DIR/GIT_WORK_TREE/
# GIT_INDEX_FILE are stripped unconditionally (decided: the cwd plus `rev-parse --show-toplevel`
# decide the repo, so an inherited redirect, e.g. from a hook, cannot point us at another one).
_GIT_ENV_STRIP = frozenset({
    "GIT_DIFF_OPTS",
    "GIT_EXTERNAL_DIFF",
    "GIT_PAGER",
    "PAGER",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_DIFF_PATH_COUNTER",
    "GIT_DIFF_PATH_TOTAL",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
})
_GIT_ENV_STRIP_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def _git_env() -> dict[str, str]:
    """A copy of os.environ safe for a read-only `git diff` (stable, non-localized output)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in _GIT_ENV_STRIP and not k.upper().startswith(_GIT_ENV_STRIP_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


class DiffError(RuntimeError):
    """git diff could not be computed; the result must be reported incomplete, never empty."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _validate_ref(ref: str) -> str:
    if not ref or ref.startswith("-") or any(c in ref for c in "\x00\n\r"):
        raise DiffError("invalid_ref", f"invalid git ref: {ref!r}")
    return ref


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
    return Path(raw[2:]) if raw[:2] in ("a/", "b/") else Path(raw)


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

    def __init__(self) -> None:
        super().__init__()
        self.binary_files = set()
        self.deleted_paths = set()
        self.mode_changed_files = set()
        self.submodule_changed_files = set()


_INDEX_LINE_RE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+( [0-7]{6})?$")
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


def _validate_record(record: list[str]) -> tuple[Path, str] | None:
    """Classify ONE `diff --git` record; raise DiffError unless it is a recognised shape.

    Returns the (path, kind) the parser MUST produce for this record, or None for a verified pure
    rename/copy (which records nothing by design). A record is accepted only if it has a
    recoverable identity, so validator and parser cannot disagree about whether it is a change.
    """
    rest = record[0][len("diff --git ") :]
    same_path = _diff_git_line_path(rest)
    hint = str(same_path or rest[:120])
    has_minus = has_plus = binary = body = False
    minus_raw = plus_raw = binary_line = ""
    hunks = 0
    old_mode = new_mode = new_file = deleted_file = False
    sim100 = False
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
            if not _DIFF_HUNK_RE.match(line):
                raise _unparsed(hint, f"malformed hunk header {line[:80]!r}")
            hunks += 1
            body = True
            continue
        if body:
            if line[:1] in (" ", "+", "-", "\\"):
                continue
            raise _unparsed(hint, f"stray line {line[:80]!r} inside a hunk")
        if line.startswith("--- "):
            has_minus = True
            minus_raw = line[4:]
        elif line.startswith("+++ "):
            has_plus = True
            plus_raw = line[4:]
        elif _INDEX_LINE_RE.match(line):
            pass
        elif m := _MODE_LINE_RE.match(line):
            if m.group(1) == "old":
                old_mode = True
            else:
                new_mode = True
        elif m := _FILE_MODE_LINE_RE.match(line):
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

    pair_dest: Path | None = None
    if len(pair) == 2:
        for kind in ("rename", "copy"):
            src, dst = pair.get(f"{kind} from"), pair.get(f"{kind} to")
            if src is not None and dst is not None:
                expected = f"{_quoted_operand(src, 'a/')} {_quoted_operand(dst, 'b/')}"
                if rest == expected:
                    pair_dest = _git_header_path(_quoted_operand(dst, "b/"))
    identity = pair_dest if pair_dest is not None else same_path
    add_del = new_file or deleted_file

    if has_minus and has_plus and hunks >= 1:
        # (a) text change (also covers submodule gitlink hunks); path must be recoverable
        old_p, new_p = _git_header_path(minus_raw), _git_header_path(plus_raw)
        if (new_file and old_p is not None) or (deleted_file and new_p is not None):
            raise _unparsed(hint, "add/delete disagrees with /dev/null in its ---/+++ lines")
        if (old_p is None and not new_file) or (new_p is None and not deleted_file):
            raise _unparsed(hint, "/dev/null in ---/+++ without an add/delete mode line")
        target = new_p if new_p is not None else old_p
        if target is None:
            raise _unparsed(hint, "no recoverable path in ---/+++ lines")
        return target, "deleted" if deleted_file else "text"
    if (hunks == 0 and (has_minus or has_plus)) or (hunks and not (has_minus and has_plus)):
        raise _unparsed(hint, "incomplete ---/+++/@@ structure")
    if pair and pair_dest is None:
        raise _unparsed(hint, "rename/copy metadata is incomplete or does not match the header")
    if binary:  # (b)
        if identity is None:
            raise _unparsed(hint, "binary record has no recoverable path")
        if new_file and not binary_line.startswith("Binary files /dev/null and "):
            raise _unparsed(hint, "added binary disagrees with /dev/null")
        if deleted_file and not binary_line.endswith(" and /dev/null differ"):
            raise _unparsed(hint, "deleted binary disagrees with /dev/null")
        return identity, "deleted" if deleted_file else "binary"
    if add_del and not pair and not (old_mode or new_mode):
        if same_path is None:  # (a) header-only add/delete needs a same-file operand pair
            raise _unparsed(hint, "header-only add/delete has no recoverable path")
        return same_path, "deleted" if deleted_file else "added"
    if pair and not sim100:
        raise _unparsed(hint, "rename/copy record is not 100% similar and has no content")
    if old_mode and new_mode and not add_del:  # (c) mode-only (optionally a verified pure rename)
        if identity is None:
            raise _unparsed(hint, "mode-only record has no recoverable path")
        return identity, "mode"
    if pair_dest is not None and not (old_mode or new_mode or add_del):
        return None  # (e) verified pure rename/copy
    raise _unparsed(hint, "record matches no supported shape")


def _validate_records(diff_text: str) -> list[tuple[Path, str]]:
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
    expectations: list[tuple[Path, str]] = []
    for record in records:
        expected = _validate_record(record)
        if expected is not None:
            expectations.append(expected)
    return expectations


def _parse_checked(diff_text: str) -> DiffHunks:
    """Parse, but never let unexplained git output silently become 'no changes'.

    Cross-check: every record the validator accepted as a change MUST appear in the parsed
    result, so validator and parser can never drift apart without failing closed.
    """
    parsed = parse_git_diff_hunks(diff_text)  # raises on combined (merge) diffs
    for path, kind in _validate_records(diff_text):
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
            continue

        if in_header and _GITLINK_INDEX_RE.match(line):
            is_submodule = True
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
                start = int(match.group("new_start"))
                count_str = match.group("new_count")
                count = int(count_str) if count_str is not None else 1
                if count == 0:
                    # Pure deletion at line `start`, changed point is line start
                    line_start = max(1, start)
                    line_end = line_start
                else:
                    line_start = start
                    line_end = start + count - 1

                ranges = result.setdefault(current_file, [])
                ranges.append((line_start, line_end))

    flush_header_only_entry()

    # Normalize / merge adjacent or overlapping ranges per file
    for file_path, ranges in list(result.items()):
        if not ranges:
            continue
        ranges.sort(key=lambda r: (r[0], r[1]))
        merged: list[tuple[int, int]] = [ranges[0]]
        for r_start, r_end in ranges[1:]:
            last_start, last_end = merged[-1]
            if r_start <= last_end + 1:
                merged[-1] = (last_start, max(last_end, r_end))
            else:
                merged.append((r_start, r_end))
        result[file_path] = merged

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


def map_changed_lines_to_symbols(
    changed_files_with_lines: dict[Path, list[tuple[int, int]]],
    root: Path,
) -> list[dict[str, Any]]:
    """Use LANGUAGE_REGISTRY (or _imports_and_symbols_for_path fallback) to extract symbols for each file,

    checking which symbols span the modified lines.
    """
    changed_symbols: list[dict[str, Any]] = []

    for rel_path, line_ranges in changed_files_with_lines.items():
        full_path = root / rel_path
        if not full_path.is_file():
            continue

        spec = lang_registry.spec_for_path(full_path)
        symbols: list[dict[str, Any]] = []
        if spec is not None and spec.extract_imports_and_symbols is not None:
            try:
                _, symbols = spec.extract_imports_and_symbols(full_path)
            except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
                symbols = []
        else:
            try:
                _, symbols = repo_map._imports_and_symbols_for_path(full_path)
            except (OSError, ValueError, SyntaxError, UnicodeDecodeError):
                symbols = []

        for sym in symbols:
            s_start = int(sym.get("start_line", sym.get("line", 1)))
            s_end = int(sym.get("end_line", s_start))

            # Check overlap between [s_start, s_end] and any [r_start, r_end]
            overlaps = any(
                max(s_start, r_start) <= min(s_end, r_end) for r_start, r_end in line_ranges
            )
            if overlaps:
                sym_copy = dict(sym)
                sym_copy["file"] = str(rel_path).replace("\\", "/")
                changed_symbols.append(sym_copy)

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
    changed_symbols = map_changed_lines_to_symbols(changed_files_with_lines, root)
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
