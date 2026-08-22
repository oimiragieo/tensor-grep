"""`tg scan` / `tg test` ruleset loading, suppression, and AST-scan payload construction.

Split out of `cli/main.py` (see `docs/design/2026-08-19-split-floor-escape.md`). This is the
whole `--ruleset` pipeline behind `tg scan`: reading `sgconfig.yml` and inline rule specs,
resolving baselines and suppressions, running the scan, and shaping the JSON payload.

`_self` is `cli/main.py`'s module object, imported from `cli/_main_binding`. Every reference
here to a symbol that still lives in `main.py` goes through it, so a
`monkeypatch.setattr(main, ...)` in the test suite keeps winning after the move -- read that
module's docstring before adding a bare cross-module reference.
"""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tensor_grep.cli._index_lock import atomic_write_bytes_anchored
from tensor_grep.cli._main_binding import _self as _self

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig


def _load_yaml_dict(path: Path) -> dict[str, object]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        try:
            loaded = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            detail = str(exc).splitlines()[0] if str(exc).strip() else "parse error"
            raise ValueError(f"Invalid YAML in {path}: {detail}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML in {path} must be a mapping.")
    return loaded


def _load_sg_project_config(config_path: str | None) -> dict[str, object]:
    from tensor_grep.backends.ast_backend import normalize_ast_language

    resolved = Path(config_path or "sgconfig.yml").resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file {resolved} not found. Use `tg new` to create one.")

    raw = _load_yaml_dict(resolved)
    return {
        "config_path": resolved,
        "root_dir": resolved.parent,
        "rule_dirs": _self._normalize_string_list(raw.get("ruleDirs"), ["rules"]),
        "test_dirs": _self._normalize_string_list(raw.get("testDirs"), ["tests"]),
        "utils_dir": str(raw.get("utilsDir") or "utils"),
        "language": normalize_ast_language(str(raw.get("language") or "python")),
    }


def _load_inline_rule_specs(
    inline_rules_text: str, *, default_language: str | None = None
) -> list[dict[str, str]]:
    import yaml

    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.ast_workflows import _extract_rule_member_patterns

    class _NoAliasSafeLoader(yaml.SafeLoader):
        """SafeLoader that REJECTS YAML aliases. Inline ast-grep rules never legitimately
        need anchors/aliases, and an aliased node graph is a billion-laughs
        memory-exhaustion vector: the downstream ``str()`` coercions on ``id``/``severity``/
        ``message`` (below) deep-walk the SHARED alias graph and expand it ~9^depth. Audit
        #95 Part-2 Opus gate BLOCK proved a 469-byte aliased payload hangs >15s -- the
        ``_MAX_INLINE_RULES_CHARS`` length cap admits depth ~1000 while detonation is at
        depth ~9, so the length cap alone is insufficient; reject at the loader level. This
        shared helper guards BOTH the MCP ``tg_ruleset_scan(inline_rules=...)`` tool and the
        CLI ``--inline-rules`` twin (identical mechanism). Uses the pure-Python SafeLoader
        (not CSafeLoader) so ``compose_node`` is overridable -- inline payloads are small
        (length-capped) so the perf cost is negligible."""

        def compose_node(self, parent, index):  # type: ignore[override,no-untyped-def]
            if self.check_event(yaml.events.AliasEvent):  # type: ignore[no-untyped-call]
                event = self.get_event()  # type: ignore[no-untyped-call]
                raise yaml.composer.ComposerError(
                    None,
                    None,
                    "YAML aliases are not allowed in inline rules",
                    event.start_mark,
                )
            return super().compose_node(parent, index)

    specs: list[dict[str, str]] = []

    try:
        documents = list(yaml.load_all(inline_rules_text, Loader=_NoAliasSafeLoader))
    except (yaml.YAMLError, RecursionError) as exc:
        # RecursionError: a deeply-nested ALIAS-FREE payload (e.g. "["*20000) recurses the YAML
        # parser/composer past the interpreter limit. The _NoAliasSafeLoader cannot reject it (no
        # alias), but the pure-Python SafeLoader raises a *catchable* RecursionError where the C
        # loader would hard-crash the process (native stack overflow). Catch it here so this path
        # also fails closed as a structured invalid_input rather than escaping as a raw traceback
        # -- the tool's fail-closed contract (audit #95 Part-2 re-gate).
        detail = str(exc).splitlines()[0] if str(exc).strip() else "input nesting too deep"
        raise ValueError(f"Invalid inline rules YAML: {detail}") from exc

    for document_index, payload in enumerate(documents, start=1):
        if payload is None:
            continue
        if not isinstance(payload, dict):
            raise ValueError("Inline rules YAML must contain mapping documents.")

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for rule_index, item in enumerate(raw_rules, start=1):
                if not isinstance(item, dict):
                    continue
                # M16 F2: composite (multi-pattern any-of) rules are carried by
                # the SAME member extraction the project-config path uses, so
                # `--rule`/`--inline-rules` no longer silently drop rule.any /
                # pattern-list members. pattern = FIRST member; patterns = ALL.
                member_patterns = _extract_rule_member_patterns(item)
                if not member_patterns:
                    continue
                spec = {
                    "id": str(item.get("id") or f"inline-rule-{document_index}-{rule_index}"),
                    "pattern": member_patterns[0],
                    "language": normalize_ast_language(
                        item.get("language")
                        or payload.get("language")
                        or default_language
                        or "python"
                    ),
                }
                # `engine` is carried VERBATIM: the per-rule router reads
                # `rule.get("engine") == "regex"` to skip AST backend selection, and
                # dropping it here silently routed every inline rule through AST.
                for metadata_key in ("severity", "message", "engine"):
                    if item.get(metadata_key) is not None:
                        spec[metadata_key] = str(item[metadata_key])
                    elif payload.get(metadata_key) is not None:
                        spec[metadata_key] = str(payload[metadata_key])
                if len(member_patterns) > 1:
                    spec["patterns"] = member_patterns  # type: ignore[assignment]
                specs.append(spec)
            continue

        member_patterns = _extract_rule_member_patterns(payload)
        if not member_patterns:
            continue
        spec = {
            "id": str(payload.get("id") or f"inline-rule-{document_index}"),
            "pattern": member_patterns[0],
            "language": normalize_ast_language(
                str(payload.get("language") or default_language or "python")
            ),
        }
        for metadata_key in ("severity", "message", "engine"):
            if payload.get(metadata_key) is not None:
                spec[metadata_key] = str(payload[metadata_key])
        if len(member_patterns) > 1:
            spec["patterns"] = member_patterns  # type: ignore[assignment]
        specs.append(spec)

    return specs


def _filter_ast_rule_specs(
    rules: list[dict[str, str]], filter_regex: str | None
) -> list[dict[str, str]]:
    if filter_regex is None:
        return rules
    try:
        compiled = re.compile(filter_regex)
    except re.error as exc:
        raise ValueError(f"Invalid --filter regex: {exc}") from exc
    return [rule for rule in rules if compiled.search(str(rule.get("id", "")))]


def _build_rulesets_payload() -> dict[str, object]:
    from tensor_grep.cli.rule_packs import list_rule_packs

    return {
        "version": _self._json_output_version(),
        "schema_version": _self._json_output_version(),
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-rulesets",
        "sidecar_used": False,
        "rulesets": list_rule_packs(),
    }


def _ruleset_finding_fingerprint(
    *,
    rule_id: str,
    language: str,
    matched_files: list[str],
) -> str:
    import hashlib

    fingerprint_input = json.dumps(
        {
            "rule_id": rule_id,
            "language": language,
            "files": matched_files,
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(fingerprint_input).hexdigest()


def _truncate_evidence_snippet(text: str, max_chars: int) -> dict[str, object]:
    # Defense-in-depth: coerce to int so a direct (non-MCP) caller passing a fractional float
    # cannot crash the slice below (`normalized[:max_chars]` requires an int index). The MCP
    # surface already rejects non-int max_evidence_snippet_chars at the tool inputSchema + FastMCP
    # pydantic boundary, so this is not a reachable vuln -- it hardens the helper for any future
    # in-process caller. (audit #95 Part-2 round-6 gate: non-blocking hardening note.)
    max_chars = int(max_chars)
    normalized = " ".join(text.split())
    if max_chars <= 0:
        return {"text": "", "truncated": bool(normalized)}
    if len(normalized) <= max_chars:
        return {"text": normalized, "truncated": False}
    return {"text": normalized[:max_chars], "truncated": True}


def _load_ruleset_baseline(path: str) -> dict[str, object]:
    baseline_path = Path(path).expanduser().resolve()
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ruleset baseline must be a JSON object.")
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, list) or not all(
        isinstance(item, str) and item.strip() for item in fingerprints
    ):
        raise ValueError("Ruleset baseline must include a non-empty 'fingerprints' string list.")
    return {
        "path": str(baseline_path),
        "fingerprints": sorted(dict.fromkeys(fingerprints)),
    }


def _load_ruleset_suppressions(path: str) -> dict[str, object]:
    suppressions_path = Path(path).expanduser().resolve()
    payload = json.loads(suppressions_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ruleset suppressions must be a JSON object.")
    entries_payload = payload.get("entries")
    if entries_payload is not None:
        if not isinstance(entries_payload, list):
            raise ValueError("Ruleset suppressions 'entries' must be a list.")
        entries: list[dict[str, object]] = []
        for raw_entry in entries_payload:
            if not isinstance(raw_entry, dict):
                raise ValueError("Ruleset suppressions entries must be JSON objects.")
            fingerprint = raw_entry.get("fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'fingerprint' string."
                )
            justification = raw_entry.get("justification")
            if not isinstance(justification, str) or not justification.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'justification' string."
                )
            created_at = raw_entry.get("created_at")
            if not isinstance(created_at, str) or not created_at.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'created_at' timestamp."
                )
            try:
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    "Ruleset suppressions entries must include ISO-8601 'created_at' timestamps."
                ) from exc
            entry: dict[str, object] = {
                "fingerprint": fingerprint.strip(),
                "justification": justification.strip(),
                "created_at": created_at,
            }
            file_path = raw_entry.get("file")
            if file_path is not None:
                if not isinstance(file_path, str) or not file_path.strip():
                    raise ValueError(
                        "Ruleset suppressions entries must use non-empty strings for optional 'file'."
                    )
                entry["file"] = file_path
            line = raw_entry.get("line")
            if line is not None:
                if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
                    raise ValueError(
                        "Ruleset suppressions entries must use positive integers for optional 'line'."
                    )
                entry["line"] = line
            rule_id = raw_entry.get("rule_id")
            if rule_id is not None:
                if not isinstance(rule_id, str) or not rule_id.strip():
                    raise ValueError(
                        "Ruleset suppressions entries must use non-empty strings for optional 'rule_id'."
                    )
                entry["rule_id"] = rule_id
            entries.append(entry)
        return {
            "path": str(suppressions_path),
            "entries": entries,
            "warnings": [],
        }
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, list) or not all(
        isinstance(item, str) and item.strip() for item in fingerprints
    ):
        raise ValueError(
            "Ruleset suppressions must include a non-empty 'fingerprints' string list."
        )
    return {
        "path": str(suppressions_path),
        "entries": [{"fingerprint": item} for item in sorted(dict.fromkeys(fingerprints))],
        "warnings": [
            "Legacy suppression format using 'fingerprints' is deprecated; use 'entries' instead."
        ],
    }


def _ruleset_suppression_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_ruleset_source_path(file_path: str, root_dir: Path) -> Path:
    candidate = Path(file_path)
    if candidate.is_absolute():
        return candidate
    return (root_dir / candidate).resolve()


def _ruleset_files_match(entry_file: str, occurrence_file: str, root_dir: Path) -> bool:
    if entry_file == occurrence_file:
        return True
    return _resolve_ruleset_source_path(entry_file, root_dir) == _resolve_ruleset_source_path(
        occurrence_file, root_dir
    )


def _inline_suppression_targets(line_text: str, language: str) -> set[str]:
    comment_prefix = (
        "#"
        if language == "python"
        else "//"
        if language
        in {
            "javascript",
            "typescript",
            "rust",
        }
        else None
    )
    if comment_prefix is None:
        return set()
    match = re.search(
        rf"{re.escape(comment_prefix)}\s*tg-ignore\s*:\s*([^\r\n]+)",
        line_text,
    )
    if not match:
        return set()
    return {token.strip() for token in match.group(1).split(",") if token.strip()}


def _occurrence_has_inline_suppression(
    *,
    occurrence_file: str,
    occurrence_line: int,
    rule_id: str,
    language: str,
    root_dir: Path,
    source_cache: dict[str, list[str]],
) -> bool:
    try:
        source_path = _resolve_ruleset_source_path(occurrence_file, root_dir)
        cache_key = str(source_path)
        if cache_key not in source_cache:
            source_cache[cache_key] = source_path.read_text(encoding="utf-8").splitlines()
        source_lines = source_cache[cache_key]
    except OSError:
        return False
    targets: set[str] = set()
    for candidate_line in (occurrence_line - 1, occurrence_line):
        if 1 <= candidate_line <= len(source_lines):
            targets.update(_inline_suppression_targets(source_lines[candidate_line - 1], language))
    return "*" in targets or rule_id in targets


def _suppression_entry_matches(
    *,
    entry: dict[str, object],
    fingerprint: str,
    rule_id: str,
    occurrence_file: str | None,
    occurrence_line: int | None,
    root_dir: Path,
) -> bool:
    if cast(str, entry["fingerprint"]) != fingerprint:
        return False
    entry_rule_id = entry.get("rule_id")
    if entry_rule_id is not None and cast(str, entry_rule_id) != rule_id:
        return False
    entry_file = entry.get("file")
    if entry_file is not None:
        if occurrence_file is None or not _ruleset_files_match(
            cast(str, entry_file), occurrence_file, root_dir
        ):
            return False
    entry_line = entry.get("line")
    if entry_line is not None and occurrence_line != cast(int, entry_line):
        return False
    return True


def _write_json_refuse_symlink(write_path: Path, data: object) -> None:
    """Write JSON through the shared anchored atomic helper.

    Shared hardened path for in-process ruleset writers (`baseline` / `suppressions`) that:

    - preserves overwrite semantics on repeated writes,
    - refuses caller-selected symlinks and live/dangling/reparse destinations,
    - fsyncs temp contents before publish, and
    - keeps path-confinement callers responsible for anchoring their roots.
    """
    payload = json.dumps(data, indent=2).encode()
    try:
        atomic_write_bytes_anchored(write_path, payload, mode=0o600, replace=True)
    except OSError as exc:
        raise ValueError(f"Refusing to write {write_path}: {exc}") from exc


def _apply_ruleset_baseline(
    payload: dict[str, object],
    *,
    baseline_path: str | None = None,
    write_baseline_path: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
) -> None:
    findings = cast(list[dict[str, object]], payload["findings"])
    matched_fingerprints = sorted({
        cast(str, finding["fingerprint"])
        for finding in findings
        if cast(int, finding["matches"]) > 0
    })
    if baseline_path is not None:
        baseline = _load_ruleset_baseline(baseline_path)
        baseline_fingerprints = set(cast(list[str], baseline["fingerprints"]))
        current_fingerprints = set(matched_fingerprints)
        for finding in findings:
            if cast(int, finding["matches"]) <= 0:
                finding["status"] = "clear"
                continue
            finding["status"] = (
                "existing" if cast(str, finding["fingerprint"]) in baseline_fingerprints else "new"
            )
        payload["baseline"] = {
            "path": baseline["path"],
            "new_findings": sum(1 for finding in findings if finding.get("status") == "new"),
            "existing_findings": sum(
                1 for finding in findings if finding.get("status") == "existing"
            ),
            "resolved_findings": len(baseline_fingerprints - current_fingerprints),
            "resolved_fingerprints": sorted(baseline_fingerprints - current_fingerprints),
        }
    else:
        for finding in findings:
            if cast(int, finding["matches"]) <= 0:
                finding["status"] = "clear"
            else:
                finding["status"] = "new"
    if write_baseline_path is not None:
        write_path = Path(write_baseline_path).expanduser()
        baseline_payload = {
            "version": _self._json_output_version(),
            "schema_version": _self._json_output_version(),
            "kind": "ruleset-scan-baseline",
            "ruleset": payload.get("ruleset"),
            "language": payload.get("language"),
            "fingerprints": matched_fingerprints,
        }
        _write_json_refuse_symlink(write_path, baseline_payload)
        payload["baseline_written"] = {
            "path": str(write_path),
            "fingerprints": matched_fingerprints,
            "count": len(matched_fingerprints),
        }
    suppressions_summary: dict[str, object] | None = None
    suppression_entries: list[dict[str, object]] = []
    suppression_warnings: list[str] = []
    if suppressions_path is not None:
        suppressions = _load_ruleset_suppressions(suppressions_path)
        suppressions_summary = {"path": suppressions["path"]}
        suppression_entries = cast(list[dict[str, object]], suppressions["entries"])
        suppression_warnings = cast(list[str], suppressions["warnings"])
        if suppression_warnings:
            suppressions_summary["warnings"] = suppression_warnings
    root_dir = Path(str(payload["path"]))
    source_cache: dict[str, list[str]] = {}
    suppressed_occurrences = 0
    inline_suppressed_occurrences = 0
    for finding in findings:
        raw_occurrences = cast(
            list[dict[str, object]],
            finding.pop("_raw_occurrences", []),
        )
        if cast(int, finding["matches"]) <= 0:
            continue
        base_status = cast(str, finding["status"])
        occurrence_rows: list[dict[str, object]] = []
        finding_suppressed_occurrences = 0
        finding_inline_occurrences = 0
        active_occurrences = 0
        for occurrence in raw_occurrences:
            occurrence_file = cast(str, occurrence["file"])
            occurrence_line = cast(int, occurrence["line"])
            occurrence_status = base_status
            if any(
                _suppression_entry_matches(
                    entry=entry,
                    fingerprint=cast(str, finding["fingerprint"]),
                    rule_id=cast(str, finding["rule_id"]),
                    occurrence_file=occurrence_file,
                    occurrence_line=occurrence_line,
                    root_dir=root_dir,
                )
                for entry in suppression_entries
            ):
                occurrence_status = "suppressed"
                finding_suppressed_occurrences += 1
            elif _occurrence_has_inline_suppression(
                occurrence_file=occurrence_file,
                occurrence_line=occurrence_line,
                rule_id=cast(str, finding["rule_id"]),
                language=cast(str, finding["language"]),
                root_dir=root_dir,
                source_cache=source_cache,
            ):
                occurrence_status = "inline-suppressed"
                finding_inline_occurrences += 1
            else:
                active_occurrences += 1
            occurrence_rows.append({
                "file": occurrence_file,
                "line": occurrence_line,
                "status": occurrence_status,
            })
        if not raw_occurrences and any(
            _suppression_entry_matches(
                entry=entry,
                fingerprint=cast(str, finding["fingerprint"]),
                rule_id=cast(str, finding["rule_id"]),
                occurrence_file=None,
                occurrence_line=None,
                root_dir=root_dir,
            )
            for entry in suppression_entries
        ):
            finding["status"] = "suppressed"
            finding_suppressed_occurrences += 1
        elif occurrence_rows:
            if active_occurrences == 0:
                finding["status"] = (
                    "inline-suppressed"
                    if finding_inline_occurrences > 0
                    else "suppressed"
                    if finding_suppressed_occurrences > 0
                    else base_status
                )
            else:
                finding["status"] = base_status
        if occurrence_rows and (
            suppressions_path is not None
            or finding_suppressed_occurrences > 0
            or finding_inline_occurrences > 0
        ):
            finding["occurrences"] = sorted(
                occurrence_rows,
                key=lambda row: (str(row["file"]), cast(int, row["line"])),
            )
        suppressed_occurrences += finding_suppressed_occurrences
        inline_suppressed_occurrences += finding_inline_occurrences
    if suppressions_summary is not None or inline_suppressed_occurrences > 0:
        if suppressions_summary is None:
            suppressions_summary = {}
        suppressions_summary["suppressed_findings"] = sum(
            1 for finding in findings if finding.get("status") == "suppressed"
        )
        if suppressed_occurrences > 0:
            suppressions_summary["suppressed_occurrences"] = suppressed_occurrences
        if inline_suppressed_occurrences > 0:
            suppressions_summary["inline_suppressed_findings"] = sum(
                1 for finding in findings if finding.get("status") == "inline-suppressed"
            )
            suppressions_summary["inline_suppressed_occurrences"] = inline_suppressed_occurrences
        payload["suppressions"] = suppressions_summary
    if write_suppressions_path is not None:
        if not isinstance(suppression_justification, str) or not suppression_justification.strip():
            raise ValueError("--write-suppressions requires a non-empty --justification value.")
        write_path = Path(write_suppressions_path).expanduser()
        suppressions_payload = {
            "version": _self._json_output_version(),
            "schema_version": _self._json_output_version(),
            "kind": "ruleset-scan-suppressions",
            "ruleset": payload.get("ruleset"),
            "language": payload.get("language"),
            "entries": [
                {
                    "fingerprint": fingerprint,
                    "justification": suppression_justification.strip(),
                    "created_at": _ruleset_suppression_timestamp(),
                }
                for fingerprint in matched_fingerprints
            ],
        }
        _write_json_refuse_symlink(write_path, suppressions_payload)
        payload["suppressions_written"] = {
            "path": str(write_path),
            "fingerprints": matched_fingerprints,
            "count": len(matched_fingerprints),
        }


def _regex_rule_targets_file(rule_language: str, file_path: str) -> bool:
    """Whether a regex-engine ruleset rule should scan ``file_path``.

    AST rules are already scoped to their language by the DirectoryScanner (via
    ``lang=rule["language"]``). The regex engine, by contrast, used to ``finditer``
    over *every* candidate file, so a ``--ruleset secrets-basic --language python``
    scan flagged ``.ts``/``.js``/``.rs`` files as python findings (audit H11). Mirror
    the AST scoping: if the file's language is detectable and differs from the rule's
    language, skip it. Files whose language is undetectable (extensionless, configs,
    data files, or a language tg cannot classify) are left to the rule so we never
    silently drop a finding for a language ``_target_language_for_path`` does not yet
    recognize.
    """
    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.repo_map import _target_language_for_path

    file_language = _target_language_for_path(file_path)
    if file_language is None:
        return True
    return file_language == normalize_ast_language(rule_language, default=file_language)


def _run_ast_scan_payload(
    project_cfg: dict[str, object],
    rules: list[dict[str, str]],
    *,
    routing_reason: str,
    scan_paths: list[str] | None = None,
    candidate_files: list[str] | None = None,
    project_scan_fast_path: bool = False,
    ruleset_name: str | None = None,
    scan_globs: list[str] | None = None,
    scan_types: list[str] | None = None,
    scan_max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
    baseline_path: str | None = None,
    write_baseline_path: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
    include_evidence_snippets: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
) -> dict[str, object]:
    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.ast_workflows import (
        _match_node_identity,
        _rule_member_patterns,
        _select_ast_backend_for_rule,
    )
    from tensor_grep.cli.scan_guardrails import ensure_scan_not_broad
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

    project_language = normalize_ast_language(project_cfg.get("language"))
    normalized_rules: list[dict[str, str]] = []
    for rule in rules:
        normalized_rule = dict(rule)
        normalized_rule["language"] = normalize_ast_language(rule.get("language"))
        normalized_rules.append(normalized_rule)
    rules = normalized_rules

    cfg = SearchConfig(
        ast=True,
        ast_prefer_native=True,
        lang=project_language,
        glob=list(scan_globs or []) or None,
        file_type=list(scan_types or []) or None,
        max_depth=scan_max_depth,
    )
    root_dir = cast(Path, project_cfg["root_dir"])
    include_scan_paths_in_payload = bool(scan_paths)
    resolved_scan_paths = (
        [str(Path(scan_path).expanduser().resolve()) for scan_path in scan_paths]
        if scan_paths
        else [str(root_dir)]
    )
    ensure_scan_not_broad(
        resolved_scan_paths,
        globs=list(scan_globs or []),
        file_types=list(scan_types or []),
        max_depth=scan_max_depth,
        allow_broad_generated_scan=allow_broad_generated_scan,
    )
    scan_has_discovery_filter = bool(scan_globs or scan_types or scan_max_depth is not None)
    scanner: DirectoryScanner | None = None
    resolved_candidate_files = (
        None
        if scan_paths or scan_has_discovery_filter
        else list(candidate_files)
        if candidate_files is not None
        else None
    )
    backend_cache: dict[tuple[str | None, str, bool, bool], ComputeBackend] = {}
    backend_names_used: set[str] = set()

    total_matches = 0
    matched_rules = 0
    findings: list[dict[str, object]] = []

    def _append_finding(
        *,
        rule: dict[str, str],
        rule_matches: int,
        matched_files: set[str],
        match_counts_by_file: dict[str, int],
        snippets_by_file: dict[str, list[dict[str, object]]],
        rule_occurrences: list[dict[str, object]],
    ) -> None:
        nonlocal total_matches, matched_rules

        total_matches += rule_matches
        if rule_matches > 0:
            matched_rules += 1
        sorted_files = sorted(matched_files)
        findings.append({
            "rule_id": rule["id"],
            "language": rule["language"],
            "severity": rule.get("severity"),
            "message": rule.get("message"),
            "fingerprint": _ruleset_finding_fingerprint(
                rule_id=rule["id"],
                language=rule["language"],
                matched_files=sorted_files,
            ),
            "matches": rule_matches,
            "files": sorted_files,
            "evidence": [
                {
                    "file": file_path,
                    "match_count": match_counts_by_file.get(file_path, 0),
                    **(
                        {"snippets": snippets_by_file.get(file_path, [])}
                        if include_evidence_snippets
                        else {}
                    ),
                }
                for file_path in sorted_files
            ],
            "_raw_occurrences": sorted({
                (cast(str, occurrence["file"]), cast(int, occurrence["line"]))
                for occurrence in rule_occurrences
            }),
        })
        if findings[-1]["_raw_occurrences"]:
            findings[-1]["_raw_occurrences"] = [
                {"file": file_path, "line": line_number}
                for file_path, line_number in cast(
                    list[tuple[str, int]], findings[-1]["_raw_occurrences"]
                )
            ]

    def _candidate_files_for_filtered_scan() -> list[str]:
        nonlocal scanner, resolved_candidate_files
        if scanner is None:
            scanner = DirectoryScanner(cfg)
        if resolved_candidate_files is None:
            resolved_candidate_files, _ = _self._collect_candidate_files(
                scanner, resolved_scan_paths
            )
        return resolved_candidate_files

    wrapper_rules: list[tuple[dict[str, str], SearchConfig]] = []
    regex_rules: list[dict[str, str]] = []
    other_resolved: list[tuple[dict[str, str], SearchConfig, ComputeBackend]] = []
    wrapper_backend: object | None = None
    for rule in rules:
        if rule.get("engine") == "regex":
            regex_rules.append(rule)
            continue
        rule_cfg = replace(cfg, lang=rule["language"])
        # M16 F3: rule-aware selection — a composite with non-native members
        # must reach a backend that serves ALL members (or fail closed), never
        # a backend selected from only the first member's shape.
        backend = _select_ast_backend_for_rule(rule_cfg, rule, backend_cache)
        if (
            project_scan_fast_path
            and not scan_has_discovery_filter
            and type(backend).__name__ == "AstGrepWrapperBackend"
            and hasattr(backend, "search_project")
        ):
            wrapper_rules.append((rule, rule_cfg))
            if wrapper_backend is None:
                wrapper_backend = backend
            continue
        other_resolved.append((rule, rule_cfg, backend))

    wrapper_project_results: dict[str, SearchResult] | None = None
    if wrapper_rules and wrapper_backend is not None:
        backend_names_used.add(type(wrapper_backend).__name__)
        try:
            wrapper_project_results = cast(Any, wrapper_backend).search_project(
                str(root_dir), str(project_cfg["config_path"])
            )
        except Exception:
            for rule, rule_cfg in wrapper_rules:
                other_resolved.append((rule, rule_cfg, cast("ComputeBackend", wrapper_backend)))
            wrapper_rules = []

    for rule, _rule_cfg in wrapper_rules:
        result = (
            wrapper_project_results.get(
                rule["id"],
                SearchResult(matches=[], total_files=0, total_matches=0),
            )
            if wrapper_project_results is not None
            else SearchResult(matches=[], total_files=0, total_matches=0)
        )
        matched_files = set(result.matched_file_paths)
        match_counts_by_file = dict(result.match_counts_by_file)
        snippets_by_file: dict[str, list[dict[str, object]]] = {}
        rule_occurrences: list[dict[str, object]] = []
        for match in result.matches:
            if match.file:
                match_counts_by_file[match.file] = match_counts_by_file.get(match.file, 0) + 1
                rule_occurrences.append({"file": match.file, "line": match.line_number})
                if (
                    include_evidence_snippets
                    and len(snippets_by_file.get(match.file, [])) < max_evidence_snippets_per_file
                ):
                    snippets_by_file.setdefault(match.file, []).append(
                        _truncate_evidence_snippet(match.text, max_evidence_snippet_chars)
                    )
        if not matched_files and result.total_files > 0:
            matched_files.update(match.file for match in result.matches if match.file)
        _append_finding(
            rule=rule,
            rule_matches=result.total_matches,
            matched_files=matched_files,
            match_counts_by_file=match_counts_by_file,
            snippets_by_file=snippets_by_file,
            rule_occurrences=rule_occurrences,
        )

    for rule, rule_cfg, backend in other_resolved:
        backend_names_used.add(type(backend).__name__)
        resolved_matched_files: set[str] = set()
        resolved_match_counts_by_file: dict[str, int] = {}
        resolved_snippets_by_file: dict[str, list[dict[str, object]]] = {}
        resolved_rule_occurrences: list[dict[str, object]] = []

        # M16 F1: composite (multi-pattern any-of) rules scan EVERY member and
        # count each matched AST NODE once across members, deduplicating by
        # node SPAN via `_match_node_identity` (file, start_byte, end_byte; the
        # same key the Rust scan core unions) — two distinct nodes on one line
        # each count, matching whole-config ast-grep's per-node `any` count.
        # Single-pattern rules keep the legacy per-node total accounting.
        member_patterns = _rule_member_patterns(rule)
        composite = len(member_patterns) > 1
        resolved_identities: set[tuple[str, int, int]] = set()

        if type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
            backend_scan_paths = (
                _candidate_files_for_filtered_scan()
                if scan_has_discovery_filter
                else resolved_scan_paths
            )
            if backend_scan_paths:
                rule_matches = 0
                for member_pattern in member_patterns:
                    result = backend.search_many(
                        backend_scan_paths, member_pattern, config=rule_cfg
                    )
                    if composite:
                        resolved_identities.update(
                            _match_node_identity(match) for match in result.matches if match.file
                        )
                    else:
                        rule_matches += result.total_matches
                    resolved_matched_files.update(result.matched_file_paths)
                    for file_path, count in result.match_counts_by_file.items():
                        resolved_match_counts_by_file[file_path] = (
                            resolved_match_counts_by_file.get(file_path, 0) + count
                        )
                    for match in result.matches:
                        if match.file:
                            resolved_match_counts_by_file[match.file] = (
                                resolved_match_counts_by_file.get(match.file, 0) + 1
                            )
                            resolved_rule_occurrences.append({
                                "file": match.file,
                                "line": match.line_number,
                            })
                            if (
                                include_evidence_snippets
                                and len(resolved_snippets_by_file.get(match.file, []))
                                < max_evidence_snippets_per_file
                            ):
                                resolved_snippets_by_file.setdefault(match.file, []).append(
                                    _truncate_evidence_snippet(
                                        match.text, max_evidence_snippet_chars
                                    )
                                )
                    if not resolved_matched_files and result.total_files > 0:
                        resolved_matched_files.update(
                            match.file for match in result.matches if match.file
                        )
            else:
                rule_matches = 0
        else:
            if scanner is None:
                scanner = DirectoryScanner(cfg)
            if resolved_candidate_files is None:
                resolved_candidate_files, _ = _self._collect_candidate_files(
                    scanner, resolved_scan_paths
                )
            rule_matches = 0
            for member_pattern in member_patterns:
                for current_file in resolved_candidate_files:
                    result = backend.search(current_file, member_pattern, config=rule_cfg)
                    if composite:
                        resolved_identities.update(
                            _match_node_identity(match, fallback_file=current_file)
                            for match in result.matches
                        )
                    else:
                        rule_matches += result.total_matches
                    if result.total_files > 0 or result.total_matches > 0:
                        resolved_matched_files.add(current_file)
                        resolved_match_counts_by_file[current_file] = (
                            resolved_match_counts_by_file.get(current_file, 0)
                            + result.total_matches
                        )
                        for match in result.matches:
                            resolved_rule_occurrences.append({
                                "file": match.file or current_file,
                                "line": match.line_number,
                            })
                        if include_evidence_snippets:
                            file_snippets = resolved_snippets_by_file.setdefault(current_file, [])
                            for match in result.matches:
                                if len(file_snippets) >= max_evidence_snippets_per_file:
                                    break
                                file_snippets.append(
                                    _truncate_evidence_snippet(
                                        match.text, max_evidence_snippet_chars
                                    )
                                )

        if composite:
            # F1: count the span-union — never the summed multiset — and
            # rebuild the per-file counts from the identities so no member
            # overlap double-counts. Occurrences were appended per member above
            # (file, line) and are deduplicated downstream in `_append_finding`.
            rule_matches = len(resolved_identities)
            resolved_match_counts_by_file = {}
            for file_path, _start_byte, _end_byte in resolved_identities:
                resolved_match_counts_by_file[file_path] = (
                    resolved_match_counts_by_file.get(file_path, 0) + 1
                )

        _append_finding(
            rule=rule,
            rule_matches=rule_matches,
            matched_files=resolved_matched_files,
            match_counts_by_file=resolved_match_counts_by_file,
            snippets_by_file=resolved_snippets_by_file,
            rule_occurrences=resolved_rule_occurrences,
        )

    # Task #299: collect files the regex rules could not read, so the payload below can say the
    # scan did not cover them instead of reporting its findings as the whole answer. Imported
    # lazily here to match this module's existing repo_map pattern (main.py has no module-level
    # repo_map import -- pulling one in would undo the #48 cold-start work).
    from tensor_grep.cli.repo_map import _UnreadablePathFlag as _ScanUnreadableFlag

    scan_unreadable = _ScanUnreadableFlag()
    for rule in regex_rules:
        backend_names_used.add("RegexRulesetBackend")
        if scanner is None:
            scanner = DirectoryScanner(cfg)
        if resolved_candidate_files is None:
            resolved_candidate_files, _ = _self._collect_candidate_files(
                scanner, resolved_scan_paths
            )

        pattern = re.compile(rule["pattern"])
        regex_matched_files: set[str] = set()
        regex_match_counts_by_file: dict[str, int] = {}
        regex_snippets_by_file: dict[str, list[dict[str, object]]] = {}
        regex_rule_occurrences: list[dict[str, object]] = []
        rule_matches = 0
        rule_language = rule["language"]
        for current_file in resolved_candidate_files:
            # H11: scope the regex scan to the rule's language so a python rule does
            # not flag .ts/.js/.rs files, matching how AST rules are scoped.
            if not _regex_rule_targets_file(rule_language, current_file):
                continue
            try:
                lines = (
                    Path(current_file).read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError as exc:
                # Task #299. Skipping here is correct -- an unreadable file cannot be scanned --
                # but doing it SILENTLY made the payload claim a completeness it never had: the
                # rule contributes no findings for this file, and `tg scan --ruleset` reports the
                # result with no marker. A security ruleset then reads as "no violations" for a
                # file nobody opened, and a CI gate keyed on the exit code passes. Record so the
                # payload can say which files were never examined.
                scan_unreadable.record(exc)
                continue
            for line_number, line_text in enumerate(lines, start=1):
                line_matches = list(pattern.finditer(line_text))
                if not line_matches:
                    continue
                match_count = len(line_matches)
                rule_matches += match_count
                regex_matched_files.add(current_file)
                regex_match_counts_by_file[current_file] = (
                    regex_match_counts_by_file.get(current_file, 0) + match_count
                )
                regex_rule_occurrences.append({
                    "file": current_file,
                    "line": line_number,
                })
                if include_evidence_snippets:
                    file_snippets = regex_snippets_by_file.setdefault(current_file, [])
                    for regex_match in line_matches:
                        if len(file_snippets) >= max_evidence_snippets_per_file:
                            break
                        file_snippets.append(
                            _truncate_evidence_snippet(
                                regex_match.group(0), max_evidence_snippet_chars
                            )
                        )

        _append_finding(
            rule=rule,
            rule_matches=rule_matches,
            matched_files=regex_matched_files,
            match_counts_by_file=regex_match_counts_by_file,
            snippets_by_file=regex_snippets_by_file,
            rule_occurrences=regex_rule_occurrences,
        )

    payload = {
        "version": _self._json_output_version(),
        "schema_version": _self._json_output_version(),
        "routing_backend": "AstBackend",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "config_path": str(project_cfg["config_path"]),
        "path": str(root_dir),
        "ruleset": ruleset_name,
        "language": str(project_cfg["language"]),
        "rule_count": len(rules),
        "matched_rules": matched_rules,
        "total_matches": total_matches,
        "backends": sorted(backend_names_used),
        "findings": findings,
    }
    if include_scan_paths_in_payload:
        payload["scan_paths"] = resolved_scan_paths
    if scan_unreadable.hit:
        # Task #299. Same `{count, sample}` shape build_repo_map/codemap/inventory already emit
        # (#276), so a consumer that understands one understands all of them. Emitted ONLY when
        # something was actually skipped: a field that is always present teaches readers to
        # ignore it, and `partial` must mean something when it appears.
        # `count` counts failed READ ATTEMPTS, not distinct files. `_UnreadablePathFlag.record`
        # increments per `OSError`, and `scan` runs two backends (the ast-grep wrapper and the
        # regex leg) that each open the same file -- so ONE unreadable file reports 2. Dogfooded
        # on an ACL-denied fixture holding exactly one blocked file: count=2, sample=[f, f].
        #
        # The event semantics are deliberately NOT changed: other `_UnreadablePathFlag` consumers
        # count `os.scandir` failures, where per-event IS the right number, and task 320 already
        # settled that question for the native `incomplete_paths_count` by DOCUMENTING it rather
        # than deduplicating a hot-path counter. What was wrong is the PROSE, which rendered an
        # event count as "N file(s) ... could not be read" -- a false statement about the world,
        # and one the default text output now prints to stdout rather than burying in --json.
        #
        # The SAMPLE is de-duplicated because it is a list of PLACES, not a tally: two slots spent
        # on one path is a sample that names fewer of them. It is already capped, so this is
        # O(cap) and preserves first-seen order.
        distinct_unreadable = list(dict.fromkeys(scan_unreadable.sample))
        payload["unreadable_paths"] = {
            "count": scan_unreadable.count,
            "sample": distinct_unreadable,
        }
        payload["partial"] = True
        payload["partial_reason"] = "unreadable_path"
        payload["remediation"] = (
            f"{scan_unreadable.count} read attempt(s) in scope failed (e.g. "
            f"{', '.join(distinct_unreadable) or 'an unreadable path'}), so no rule ran "
            "against those files and this result does NOT prove they are clean. Make them "
            "readable, or scope the scan away from them."
        )
    _apply_ruleset_baseline(
        payload,
        baseline_path=baseline_path,
        write_baseline_path=write_baseline_path,
        suppressions_path=suppressions_path,
        write_suppressions_path=write_suppressions_path,
        suppression_justification=suppression_justification,
    )
    return payload


def _describe_ast_backend_mode(backend_name: str) -> str:
    if backend_name == "AstBackend":
        return "native AST matching"
    if backend_name == "AstGrepWrapperBackend":
        return "ast-grep structural matching"
    return backend_name


def _describe_ast_backend_modes(backend_names: set[str]) -> str:
    if not backend_names:
        return "adaptive AST routing"
    if len(backend_names) == 1:
        return _describe_ast_backend_mode(next(iter(backend_names)))
    return "adaptive AST routing"


def _select_ast_backend_for_pattern(
    base_config: "SearchConfig",
    pattern: str,
    backend_cache: dict[tuple[str | None, str, bool, bool], "ComputeBackend"] | None = None,
) -> "ComputeBackend":
    """Thin forwarding shim onto `cli.ast_workflows._select_ast_backend_for_pattern` -- the
    single implementation `tg run` and `tg scan`'s rule loop now share.

    This used to be an independently hand-maintained near-duplicate of the ast_workflows.py
    function, and the two drifted: this copy silently dropped the `requires_ast_grep_wrapper`
    fail-closed guard (ast_selector/ast_strictness/ast_stdin/glob), so a native-shaped pattern
    (e.g. a bare identifier) combined with a wrapper-only knob would fall through to the native
    tree-sitter backend -- which has no concept of those knobs and would silently ignore them --
    instead of refusing per the Backend Fail-Closed Contract. See
    `tests/unit/test_ast_workflows.py`'s Invariant C family for the behavior this pins.

    The import stays function-local (not hoisted to module scope) deliberately: hoisting it
    would eagerly pull in `tensor_grep.backends.ast_backend` -> `tensor_grep.core.config` on
    every `tg` invocation, including `--help`, which is exactly the module-level import cost
    this file's imports already avoid (see the `tensor_grep.io.scan_limits` comment above).
    """
    from tensor_grep.cli.ast_workflows import (
        _select_ast_backend_for_pattern as _select_ast_backend_for_pattern_impl,
    )

    # String forward-reference form: "ComputeBackend" is only bound under TYPE_CHECKING in this
    # module (see the import block above), so the bare-name form `cast(ComputeBackend, ...)`
    # would raise NameError at runtime the moment this line executed -- mypy resolves the string
    # the same way it resolves any other forward reference, with no runtime name lookup.
    return cast(
        "ComputeBackend",
        _select_ast_backend_for_pattern_impl(base_config, pattern, backend_cache),
    )
