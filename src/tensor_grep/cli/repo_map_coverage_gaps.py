"""Coverage-gap builders and the incompleteness mechanism for the symbol graph (wave-2a Part G1).

A symbol-graph answer is only trustworthy if every file it was supposed to read was actually read.
Four file-level causes used to drop a file from the answer with no trace: it was over the parse cap
(`TENSOR_GREP_MAX_PARSE_BYTES`), it did not parse (Python syntax error), it could not be read, or it
was not valid UTF-8 (undecodable bytes were replaced and an identifier may have been corrupted).

Every gap entry carries `affects_completeness` in {"never", "when_empty", "always"}. One helper,
`apply_coverage_gap_incompleteness`, turns the qualifying entries into `result_incomplete` (exit 2)
plus `incomplete_reason_class == "coverage_gap"`. An EMPTY answer is the only case where silence
reads as "proven absent", so a non-empty answer keeps exit 0 and only discloses the gap.

`repo_map` imports this module, so this module must not import `repo_map` at module level: the
builders take their `repo_map` helpers through a function-local import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli import lang_registry

_SAMPLE_LIMIT = 5
_REPLACEMENT_CHAR = chr(0xFFFD)

PARSE_CAP_REASON = "file(s) over TENSOR_GREP_MAX_PARSE_BYTES were not parsed (skipped, not absent)"
SYNTAX_ERROR_REASON = "python file(s) had syntax errors or could not be read and were not indexed"
UNREADABLE_REASON = "file(s) could not be read and were not indexed"
LOSSY_DECODE_REASON = "file(s) are not valid UTF-8; undecodable bytes were replaced and symbols in them may be missing"

_REMEDIATION = {
    "parse-cap": "raise TENSOR_GREP_MAX_PARSE_BYTES (bytes) or narrow PATH",
    "syntax-error": "fix the syntax error(s) in the listed file(s) or exclude them from PATH",
    "unreadable": "fix the file permissions or exclude the file(s) from PATH",
    "lossy-decode": "re-encode the listed file(s) as UTF-8 or exclude them from PATH",
}
_REASON = {
    "parse-cap": PARSE_CAP_REASON,
    "syntax-error": SYNTAX_ERROR_REASON,
    "unreadable": UNREADABLE_REASON,
    "lossy-decode": LOSSY_DECODE_REASON,
}


def language_coverage_gap_remediation(
    language: str, *, fail_closed: bool = False, import_resolution_only: bool = False
) -> str:
    """F12 fix: the remediation text must match what ACTUALLY happens for this gap.

    An unregistered-language file (``fail_closed=False``, no ``LanguageSpec`` at all) really does
    fall back to plain literal-text/regex matching -- see the generic ``else`` branch in the
    refs/callers scan loops. A registered-but-grammar-missing language with no regex fallback
    (``fail_closed=True``, e.g. Go when ``tree_sitter_go`` is not installed) produces ZERO rows
    for its files instead -- claiming a regex fallback there was simply false.

    ``import_resolution_only`` (audit #81 #4): a registered language whose grammar IS installed
    but whose ``LanguageSpec.import_update_target`` is ``None`` (Go today) -- defs/refs/callers
    all work normally, but the reverse-import-graph edge (``import_graph_consumers``) can never
    be computed for this language, so a zero count there must read as UNKNOWN, not proven-zero.
    """
    if fail_closed:
        return (
            f"tg has a '{language}' extractor registered but its required parser/grammar is not "
            f"installed -- refs/callers on a symbol whose definition or usage lives in a "
            f"{language} file currently produce NO rows for those files ('{language}' has no "
            "plain-text/regex fallback, unlike python/javascript/typescript/rust). Install the "
            f"missing '{language}' tree-sitter grammar package to restore coverage."
        )
    if import_resolution_only:
        return (
            f"tg has a '{language}' extractor registered and its parser/grammar is installed, "
            f"but no reverse-import resolver is wired for '{language}' yet -- `tg callers`/`tg "
            f"blast-radius` cannot discover a {language} file that consumes a symbol purely via "
            "an import statement (`import_graph_consumers` is always empty for this language). "
            "Direct-reference/call matches inside scanned files are unaffected. Treat a zero "
            f"import-graph-consumer count for a {language} definition as UNKNOWN, not "
            "proven-zero, until native reverse-import resolution ships."
        )
    return (
        f"tg has no parser-backed extractor registered for '{language}' files yet -- refs/"
        f"callers on a symbol whose definition or usage lives in a {language} file fall back to "
        "plain literal-text/regex matching (no import-graph resolution, no AST-verified call "
        "sites). Treat matches in these files as lower-confidence until native support ships."
    )


def _sample_name(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return path.as_posix()


def _stat_key(path: Path) -> tuple[str, int | None, int | None]:
    """(path, mtime_ns, size) -- an unstatable file keys as (path, None, None); the classifier
    then reports it as `unreadable`, so nothing is lost here."""
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


_MEMO: tuple[Any, dict[tuple[str, str], dict[str, Any]]] | None = None


def _classify(files: list[Path], root: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Memoised (last universe only): a render loop asks for the same universe many times."""
    global _MEMO
    from tensor_grep.cli import repo_map as _rm

    signature = tuple(
        _stat_key(current) for current in files if lang_registry.spec_for_path(current) is not None
    )
    key = (_rm._max_parse_bytes(), str(root), signature)
    memo = _MEMO
    if memo is not None and memo[0] == key:
        return memo[1]
    result = _classify_uncached(files, root)
    _MEMO = (key, result)
    return result


def _classify_uncached(
    files: list[Path], root: Path | None
) -> dict[tuple[str, str], dict[str, Any]]:
    """One pass: bucket every spec-bearing file into at most the causes that hide it."""
    from tensor_grep.cli import repo_map as _rm

    cap = _rm._max_parse_bytes()
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    def record(kind: str, language: str, path: Path, cause: str | None = None) -> None:
        bucket = buckets.setdefault((kind, language), {"files": [], "causes": set()})
        bucket["files"].append(_sample_name(path, root))
        if cause:
            bucket["causes"].add(cause)

    for current in files:
        spec = lang_registry.spec_for_path(current)
        if spec is None:
            continue
        language = spec.language_id
        try:
            size = current.stat().st_size
        except OSError:
            record("unreadable", language, current)
            continue
        if size > cap:
            record("parse-cap", language, current)
            continue
        try:
            text = _rm._read_source_text_cached(str(current))
        except OSError:
            record("unreadable", language, current)
            continue
        if current.suffix.lower() == ".py":
            try:
                _rm._cached_ast_parse(text)
            except SyntaxError as exc:
                cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else None
                record("syntax-error", language, current, cause)
        if _REPLACEMENT_CHAR in text:
            # A VALID UTF-8 file may legitimately contain a literal U+FFFD, so confirm with a
            # strict decode (only for files that already contain one -- rare).
            try:
                current.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError:
                record("lossy-decode", language, current)
            except OSError:
                record("unreadable", language, current)
    return buckets


def _entries(
    buckets: dict[tuple[str, str], dict[str, Any]], kinds: set[str]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for (kind, language), bucket in buckets.items():
        if kind not in kinds:
            continue
        reason = _REASON[kind]
        if bucket["causes"]:
            reason = f"{reason} [{', '.join(sorted(bucket['causes']))}]"
        names = sorted(bucket["files"])
        entries.append({
            "language": language,
            "reason": reason,
            "files_affected": len(names),
            "files_sample": names[:_SAMPLE_LIMIT],
            "remediation": _REMEDIATION[kind],
            "affects_completeness": "when_empty",
        })
    return sorted(entries, key=lambda item: (-int(item["files_affected"]), str(item["language"])))


def source_coverage_gaps(files: list[Path], root: Path | None = None) -> list[dict[str, Any]]:
    """Every file-level coverage gap (parse cap, syntax error, unreadable, lossy decode)."""
    return _entries(_classify(files, root), set(_REMEDIATION))


def parse_cap_gap(files: list[Path], root: Path | None = None) -> dict[str, Any] | None:
    """The over-`TENSOR_GREP_MAX_PARSE_BYTES` gap for `files` (largest language bucket), or None."""
    found = _entries(_classify(files, root), {"parse-cap"})
    return found[0] if found else None


def python_syntax_error_gap(files: list[Path], root: Path | None = None) -> dict[str, Any] | None:
    """The Python syntax-error / unparseable gap for `files`, or None."""
    found = _entries(_classify(files, root), {"syntax-error"})
    return found[0] if found else None


def mark_incomplete(
    payload: dict[str, Any], *, remediation: str, reason: str, reason_class: str | None = None
) -> None:
    """`_mark_result_incomplete` plus a stored reason (and class); never clobbers an earlier cause."""
    from tensor_grep.cli import repo_map as _rm

    _rm._mark_result_incomplete(payload, remediation=remediation)
    payload.setdefault("incomplete_reason", reason)
    if reason_class is not None and "incomplete_reason_class" not in payload:
        payload["incomplete_reason_class"] = reason_class


# Every key a payload kind counts as "the answer". An answer is EMPTY only when all are empty.
_ANSWER_KEYS = {
    "refs": ("references", "string_refs"),
    "callers": ("callers", "import_graph_consumers"),
    "source": ("sources",),
    "imports": ("imports",),
    "importers": ("importers",),
    "file-api": ("symbols",),
}


def answer_empty(payload: dict[str, Any], kind: str) -> bool:
    """Whether the payload's whole result set for `kind` is empty (never just the headline list)."""
    return not any(payload.get(key) for key in _ANSWER_KEYS[kind])


def blocking_gaps(gaps: list[dict[str, Any]], *, answer_empty: bool) -> list[dict[str, Any]]:
    return [
        gap
        for gap in gaps
        if gap.get("affects_completeness") == "always"
        or (answer_empty and gap.get("affects_completeness") == "when_empty")
    ]


def apply_coverage_gap_incompleteness(
    payload: dict[str, Any], gaps: list[dict[str, Any]], *, answer_empty: bool
) -> None:
    """Flip `result_incomplete` (+ class `coverage_gap`) when a qualifying gap blocks the answer.

    An EARLIER cause (a scan limit, a deadline, an unreadable path) owns reason/class/remediation;
    the gaps stay disclosed in `resolution_gaps` but never get mixed into that cause."""
    blocking = blocking_gaps(gaps, answer_empty=answer_empty)
    if not blocking or payload.get("result_incomplete") or payload.get("partial"):
        return
    why = "; ".join(
        f"{gap.get('files_affected', 0)} {gap['language']} file(s): {gap['reason']}"
        for gap in blocking
    )
    mark_incomplete(
        payload,
        remediation=str(blocking[0]["remediation"]),
        reason=why,
        reason_class="coverage_gap",
    )


def copy_coverage_gap_state(payload: dict[str, Any], source: dict[str, Any]) -> None:
    """Carry the computed gaps and the incompleteness cause fields onto a freshly built envelope.

    Verbatim, and never over an earlier cause already stamped on `payload`."""
    gaps = source.get("resolution_gaps")
    if isinstance(gaps, list) and gaps:
        payload["resolution_gaps"] = [dict(gap) for gap in gaps]
    if not source.get("result_incomplete"):
        return
    payload["result_incomplete"] = True
    for key in ("incomplete_reason", "incomplete_reason_class", "scan_remediation"):
        if source.get(key) and not payload.get(key):
            payload[key] = source[key]


def inherit_coverage_gap(payload: dict[str, Any], source: dict[str, Any]) -> bool:
    """Blast-radius: adopt a callers/defs payload's BLOCKING coverage gap (not a caller-scan ceiling).

    Returns True when `source` is incomplete because of a coverage gap (cause fields copied
    verbatim, `coverage_gap_limit` stamped for the scan-incomplete exit gate, and no
    `caller_scan_truncated`); False for any other cause so the caller keeps its own handling."""
    if (
        not source.get("result_incomplete")
        or source.get("incomplete_reason_class") != "coverage_gap"
    ):
        return False
    from tensor_grep.cli import repo_map as _rm

    remediation = str(source.get("scan_remediation") or "")
    if not payload.get("result_incomplete"):
        _rm._mark_result_incomplete(payload, remediation=remediation)
        payload["incomplete_reason"] = source.get("incomplete_reason", "")
        payload["incomplete_reason_class"] = "coverage_gap"
    sample: list[str] = []
    for gap in payload.get("resolution_gaps") or source.get("resolution_gaps") or []:
        if gap.get("affects_completeness") in {"when_empty", "always"}:
            sample.extend(str(name) for name in gap.get("files_sample", []))
    payload["coverage_gap_limit"] = {
        "possibly_truncated": True,
        "reason_class": "coverage_gap",
        "reason": str(source.get("incomplete_reason", "")),
        "files_sample": sample[:_SAMPLE_LIMIT],
        "remediation": remediation,
    }
    return True


def universe_gaps(files: list[Path], root: Path | None) -> list[dict[str, Any]]:
    """Blocking-capable coverage gaps (grammar missing, parse cap, syntax, unreadable, decode)."""
    from tensor_grep.cli import repo_map as _rm

    return [
        gap
        for gap in _rm._language_coverage_gaps_for_universe(files, root)
        if gap.get("affects_completeness") != "never"
    ]


def target_file_gaps(target: Path) -> list[dict[str, Any]]:
    """Blocking-capable coverage gaps for ONE file."""
    return universe_gaps([target], target.parent)


def attach_importer_coverage(
    payload: dict[str, Any],
    repo_map: dict[str, Any],
    files: list[str],
    root: Path,
    target: Path,
    *,
    answer_empty: bool,
) -> None:
    """`tg importers`: an EMPTY answer is unknown when a candidate importer was skipped or the
    target's language has no reverse-import resolver at all."""
    gaps = [*repo_map.get("resolution_gaps", []), *universe_gaps([Path(f) for f in files], root)]
    payload["resolution_gaps"] = gaps
    apply_coverage_gap_incompleteness(payload, gaps, answer_empty=answer_empty)
    spec = lang_registry.spec_for_path(target)
    if (
        answer_empty
        and spec is not None
        and spec.import_update_target is None
        and not (payload.get("result_incomplete") or payload.get("partial"))
    ):
        mark_incomplete(
            payload,
            remediation=language_coverage_gap_remediation(
                spec.language_id, import_resolution_only=True
            ),
            reason=f"no reverse-import resolver for {spec.language_id}; 0 importers is UNKNOWN",
            reason_class="coverage_gap",
        )


def attach_target_gaps(payload: dict[str, Any], target: Path, *, answer_empty: bool) -> None:
    """Single-file commands (`imports`, `file-api`, the capsule's primary file): disclose the
    target's gaps and block only an EMPTY answer; an earlier cause keeps precedence."""
    gaps = target_file_gaps(target)
    if gaps:
        payload["resolution_gaps"] = gaps
        apply_coverage_gap_incompleteness(payload, gaps, answer_empty=answer_empty)


def apply_answer_gaps(payload: dict[str, Any], kind: str) -> None:
    """Apply the payload's own `resolution_gaps`, judging emptiness from its whole result set."""
    apply_coverage_gap_incompleteness(
        payload, payload.get("resolution_gaps", []), answer_empty=answer_empty(payload, kind)
    )


def attach_found_answer_gaps(payload: dict[str, Any], repo_map: dict[str, Any]) -> None:
    """Disclose (never block on) the blocking-capable gaps on a FOUND defs answer."""
    from tensor_grep.cli import repo_map as _rm

    files, tests = _rm._repo_map_file_and_test_universe(repo_map)
    gaps = universe_gaps([*files, *tests], _rm._repo_map_root_dir(repo_map))
    if gaps:
        payload["resolution_gaps"] = gaps
        apply_coverage_gap_incompleteness(payload, gaps, answer_empty=False)
