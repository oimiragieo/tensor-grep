"""Reverse import graph confirmation and uncertainty tracking."""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType
from typing import Any


def source_tokens(source_files: list[str]) -> set[str]:
    tokens: set[str] = set()
    for current in source_files:
        stem = Path(current).stem.lower()
        tokens.add(stem)
        tokens.update(part for part in re.split(r"[^A-Za-z0-9_]+", stem) if part)
    return tokens


def test_import_bonus(
    owner: ModuleType,
    test_path: str,
    source_tokens: set[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        if any(token and token in lowered for token in source_tokens):
            bonus += 3
        for file_path, depth in file_distances.items():
            aliases = owner._module_aliases_for_path(file_path)
            if any(alias and alias in lowered for alias in aliases):
                bonus += max(1, 4 - depth)
                break
    return bonus


def test_graph_score(
    owner: ModuleType,
    test_path: str,
    source_files: list[str],
    imports_by_file: dict[str, list[str]],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
) -> float:
    aliases_by_file = {current: owner._module_aliases_for_path(current) for current in source_files}
    score = 0.0
    matched_files: set[str] = set()
    max_file_score = max(file_scores.values(), default=0)
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        for current, aliases in aliases_by_file.items():
            if current in matched_files:
                continue
            if any(alias and alias in lowered for alias in aliases):
                matched_files.add(current)
                score += graph_scores.get(current, 0.0)
                if max_file_score > 0:
                    score += file_scores.get(current, 0) / max_file_score
    return score


def _expired(owner: ModuleType, deadline: float | None, hit: Any) -> bool:
    if deadline is None or owner.time.monotonic() < deadline:
        return False
    if hit is not None:
        hit.hit = True
    return True


def confirm_edges(
    owner: ModuleType,
    candidate_importer: Path,
    target_file: str,
    repo_root: Path | str | None,
    *,
    raw_entries: list[dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: Any = None,
) -> list[dict[str, Any]]:
    spec = owner.lang_registry.spec_for_path(candidate_importer)
    language_id = spec.language_id if spec is not None else None
    if language_id not in ("javascript", "typescript", "rust", "python"):
        return []
    if raw_entries is None:
        if _expired(owner, deadline_monotonic, deadline_hit):
            return []
        raw_entries = owner._imports_with_lines_for_path(candidate_importer)

    edges: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    for raw_entry in raw_entries:
        if _expired(owner, deadline_monotonic, deadline_hit):
            break
        module = str(raw_entry.get("module", ""))
        line = int(raw_entry.get("line", 0) or 0)
        dynamic = bool(raw_entry.get("dynamic", False))
        dynamic_unresolved = bool(raw_entry.get("dynamic_unresolved", False))
        if dynamic_unresolved:
            continue
        if language_id in ("javascript", "typescript"):
            details = owner._js_ts_module_match_details(
                candidate_importer, module, target_file, repo_root
            )
            matched = bool(details.get("matched"))
            path_provenance = list(details.get("provenance", []))
            confidence = float(details.get("confidence", 0.0))
        elif language_id == "rust":
            details = owner._rust_module_match_details(
                candidate_importer, module, target_file, repo_root
            )
            matched = bool(details.get("matched"))
            path_provenance = list(details.get("provenance", []))
            confidence = float(details.get("confidence", 0.0))
        else:
            details = owner._python_module_match_details(
                candidate_importer,
                module,
                target_file,
                repo_root,
                level=int(raw_entry.get("level", 0) or 0),
            )
            matched = bool(details.get("matched"))
            path_provenance = list(details.get("provenance", []))
            confidence = float(details.get("confidence", 0.0))
        if not matched or line in seen_lines:
            continue
        seen_lines.add(line)
        provenance = "parser-backed" if language_id == "python" else "heuristic"
        edge: dict[str, Any] = {
            "file": str(candidate_importer),
            "line": line,
            "text": owner._source_line_text(candidate_importer, line),
            "kind": "import-consumer",
            "edge_kind": "reverse-import",
            "module": module,
            "provenance": provenance,
            "resolution_confidence": confidence,
        }
        if path_provenance == ["sys-path-insert"]:
            edge["path_provenance"] = path_provenance[0]
        elif "partial-resolution" in path_provenance:
            edge["path_provenance"] = path_provenance
        if dynamic:
            edge["dynamic"] = dynamic
            edge["dynamic_unresolved"] = dynamic_unresolved
        edges.append(edge)
    return edges


def _candidate_evidence(
    owner: ModuleType,
    candidate: str,
    repo_root: Path,
    imports_by_file: dict[str, list[str]],
    *,
    deadline_monotonic: float | None,
    deadline_hit: Any,
) -> dict[str, Any]:
    path = Path(candidate)
    spec = owner.lang_registry.spec_for_path(path)
    language_id = spec.language_id if spec is not None else None
    if language_id not in ("javascript", "typescript", "rust", "python"):
        return {"status": "uncertain", "reason": "unsupported-language", "raw": []}
    if _expired(owner, deadline_monotonic, deadline_hit):
        return {"status": "uncertain", "reason": "deadline", "raw": []}
    try:
        if path.stat().st_size > owner._max_parse_bytes():
            return {"status": "uncertain", "reason": "over-cap", "raw": []}
        if _expired(owner, deadline_monotonic, deadline_hit):
            return {"status": "uncertain", "reason": "deadline", "raw": []}
        source = owner.lang_registry.read_source_text(path)
        if _expired(owner, deadline_monotonic, deadline_hit):
            return {"status": "uncertain", "reason": "deadline", "raw": []}
        if language_id == "python":
            owner._cached_ast_parse(source)
        else:
            parser = spec.parser_for_path(path) if spec.parser_for_path is not None else None
            if parser is None:
                return {"status": "uncertain", "reason": "parser-unavailable", "raw": []}
            tree = parser.parse(source.encode("utf-8"))
            if tree.root_node.has_error:
                return {"status": "uncertain", "reason": "malformed-source", "raw": []}
        if _expired(owner, deadline_monotonic, deadline_hit):
            return {"status": "uncertain", "reason": "deadline", "raw": []}
        raw_entries = owner._imports_with_lines_for_path(path)
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return {"status": "uncertain", "reason": "unreadable-or-malformed", "raw": []}
    except Exception:
        return {"status": "uncertain", "reason": "parser-failure", "raw": []}

    if imports_by_file.get(candidate) and not raw_entries:
        return {"status": "uncertain", "reason": "partial-parse", "raw": raw_entries}
    uncertain_reason = ""
    for raw_entry in raw_entries:
        if _expired(owner, deadline_monotonic, deadline_hit):
            return {"status": "uncertain", "reason": "deadline", "raw": raw_entries}
        if bool(raw_entry.get("dynamic_unresolved", False)):
            uncertain_reason = "dynamic-unresolved"
            continue
        resolved = owner._resolve_raw_import_entry(path, raw_entry, repo_root, language_id)
        if not resolved.get("resolved") and not resolved.get("external"):
            uncertain_reason = "unresolved-import"
    return {
        "status": "uncertain" if uncertain_reason else "complete",
        "reason": uncertain_reason,
        "raw": raw_entries,
    }


def test_import_evidence(
    owner: ModuleType,
    importer: str,
    targets: list[str],
    repo_root: Path,
    cached_imports: list[str],
    *,
    candidate_statuses: dict[str, dict[str, Any]],
    deadline_monotonic: float | None,
    deadline_hit: Any,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    evidence = _candidate_evidence(
        owner,
        importer,
        repo_root,
        {importer: cached_imports},
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    raw_entries = evidence["raw"]
    if evidence["reason"] == "deadline":
        return {}, {}
    language = owner.lang_registry.spec_for_path(importer)
    unsupported = language is None or language.language_id not in (
        "python",
        "javascript",
        "typescript",
        "rust",
    )
    confirmed: dict[str, list[str]] = {}
    uncertain: dict[str, list[str]] = {}
    modules = list(
        dict.fromkeys(
            [str(item) for item in cached_imports]
            + [str(entry.get("module", "")) for entry in raw_entries]
        )
    )
    for target in targets:
        if _expired(owner, deadline_monotonic, deadline_hit):
            break
        aliases = owner._module_aliases_for_path(target)
        candidate_modules = [
            module
            for module in modules
            if aliases.intersection(owner._import_alias_candidates(module))
        ]
        if not candidate_modules:
            continue
        candidate_entries = [
            entry for entry in raw_entries if str(entry.get("module", "")) in candidate_modules
        ]
        if candidate_entries:
            edges = owner._confirm_import_edges(
                Path(importer),
                target,
                repo_root,
                raw_entries=candidate_entries,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
            for edge in edges:
                module = str(edge.get("module", ""))
                confidence = float(edge.get("resolution_confidence", 0.0))
                partial = "partial-resolution" in str(edge.get("path_provenance", ""))
                trusted_target = candidate_statuses.get(target, {}).get("status") in {
                    "origin",
                    "confirmed",
                }
                destination = (
                    confirmed if confidence > 0.2 and not partial and trusted_target else uncertain
                )
                destination.setdefault(target, [])
                if module not in destination[target]:
                    destination[target].append(module)
        if target not in confirmed and target not in uncertain:
            unknown = (
                candidate_statuses.get(target, {}).get("status") == "uncertain"
                or evidence["status"] == "uncertain"
                or unsupported
                or not raw_entries
                or any(bool(entry.get("dynamic_unresolved")) for entry in raw_entries)
            )
            if unknown:
                uncertain[target] = candidate_modules
    return confirmed, uncertain
