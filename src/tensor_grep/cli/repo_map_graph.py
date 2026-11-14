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
        raw_modules = {str(entry.get("module", "")) for entry in candidate_entries}
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
            cached_only_modules = [
                module for module in candidate_modules if module not in raw_modules
            ]
            unknown = (
                candidate_statuses.get(target, {}).get("status") == "uncertain"
                or evidence["status"] == "uncertain"
                or unsupported
                or not raw_entries
                or any(bool(entry.get("dynamic_unresolved")) for entry in raw_entries)
            )
            if unknown or cached_only_modules:
                uncertain[target] = cached_only_modules or candidate_modules
    return confirmed, uncertain


def scored_test_imports(
    owner: ModuleType,
    importer: str,
    targets: list[str],
    repo_root: Path,
    cached_imports: list[str],
    source_tokens: set[str],
    file_distances: dict[str, int],
    candidate_statuses: dict[str, dict[str, Any]],
    *,
    deadline_monotonic: float | None,
    deadline_hit: Any,
) -> tuple[int, list[str], dict[str, list[str]]]:
    confirmed, uncertain = test_import_evidence(
        owner,
        importer,
        targets,
        repo_root,
        cached_imports,
        candidate_statuses=candidate_statuses,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    confirmed_targets = list(confirmed)
    confirmed_modules = list(
        dict.fromkeys(module for values in confirmed.values() for module in values)
    )
    uncertain_modules = list(
        dict.fromkeys(module for values in uncertain.values() for module in values)
    )
    confirmed_imports = {importer: confirmed_modules} if confirmed_modules else {}
    confirmed_distances = {
        target: depth for target, depth in file_distances.items() if target in confirmed
    }
    bonus = owner._test_import_bonus(
        importer, source_tokens, confirmed_imports, confirmed_distances
    )
    if confirmed_targets:
        bonus = max(1, bonus)
    elif not bonus and candidate_statuses.get(importer, {}).get("status") == "uncertain":
        bonus = owner._test_import_bonus(importer, source_tokens, {importer: cached_imports}, {})
    if not bonus and uncertain_modules:
        bonus = owner._test_import_bonus(importer, source_tokens, {importer: uncertain_modules}, {})
        if not bonus:
            bonus = 1
    return bonus, confirmed_targets, confirmed_imports


def reverse_import_distances(
    owner: ModuleType,
    seed_files: list[str],
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
    *,
    repo_root: Path | str | None = None,
    trusted_seed_files: set[str] | None = None,
    uncertain_candidates: dict[str, dict[str, Any]] | None = None,
    candidate_statuses: dict[str, dict[str, Any]] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: Any = None,
    _profiling_collector: Any = None,
) -> dict[str, int]:
    with owner._profiling_phase(_profiling_collector, "graph_bfs"):
        distances: dict[str, int] = {}
        root = (
            Path(repo_root)
            if repo_root is not None
            else owner._infer_project_root(Path(seed_files[0]) if seed_files else Path.cwd())
        )
        root = root.expanduser().resolve()
        reverse = owner._reverse_importers(
            all_files,
            imports_by_file,
            include_directory_index_aliases=True,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
            _profiling_collector=_profiling_collector,
        )
        evidence_cache: dict[str, dict[str, Any]] = {}
        edge_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        uncertain: dict[str, dict[str, Any]] = {}
        statuses: dict[str, dict[str, Any]] = {}
        trusted_seeds = set(seed_files) if trusted_seed_files is None else trusted_seed_files
        trusted_frontier: list[str] = []
        weak_frontier: list[str] = []
        trusted_seen: set[str] = set()
        weak_seen: set[str] = set()
        for seed in seed_files:
            status = "origin" if seed in trusted_seeds else "uncertain"
            if imports_by_file.get(seed):
                if _expired(owner, deadline_monotonic, deadline_hit):
                    break
                evidence = _candidate_evidence(
                    owner,
                    seed,
                    root,
                    imports_by_file,
                    deadline_monotonic=deadline_monotonic,
                    deadline_hit=deadline_hit,
                )
                evidence_cache[seed] = evidence
                if evidence["status"] == "uncertain" or status == "uncertain":
                    uncertain[seed] = {
                        "depth": 0,
                        "reason": evidence["reason"] or "unconfirmed-query-origin",
                        "confidence": 0.2,
                    }
            elif status == "uncertain":
                uncertain[seed] = {
                    "depth": 0,
                    "reason": "unconfirmed-query-origin",
                    "confidence": 0.2,
                }
            statuses[seed] = {"status": status, **uncertain.get(seed, {})}
            if status == "origin":
                trusted_frontier.append(seed)
                trusted_seen.add(seed)
            else:
                weak_frontier.append(seed)
                weak_seen.add(seed)

        for depth in range(1, 4):
            if _expired(owner, deadline_monotonic, deadline_hit):
                break
            possible_trusted: set[str] = set()
            possible_weak: set[str] = set()
            for target in trusted_frontier:
                possible_trusted.update(reverse.get(target, set()))
            for target in weak_frontier:
                possible_weak.update(reverse.get(target, set()))
            possible = possible_trusted | possible_weak
            next_trusted: list[str] = []
            next_weak: list[str] = []
            for candidate in all_files:
                if candidate in trusted_seen or candidate not in possible:
                    continue
                if candidate in weak_seen and candidate not in possible_trusted:
                    continue
                if _expired(owner, deadline_monotonic, deadline_hit):
                    break
                cached_evidence = evidence_cache.get(candidate)
                if cached_evidence is None:
                    evidence = _candidate_evidence(
                        owner,
                        candidate,
                        root,
                        imports_by_file,
                        deadline_monotonic=deadline_monotonic,
                        deadline_hit=deadline_hit,
                    )
                    evidence_cache[candidate] = evidence
                else:
                    evidence = cached_evidence
                if _expired(owner, deadline_monotonic, deadline_hit):
                    break
                if evidence["reason"] == "deadline":
                    break

                full_trusted = False
                full_weak = False
                partial_match = False
                if evidence["status"] != "uncertain" or evidence["raw"]:
                    for target in trusted_frontier + weak_frontier:
                        if candidate not in reverse.get(target, set()):
                            continue
                        pair = (candidate, target)
                        edges = edge_cache.get(pair)
                        if edges is None:
                            edges = owner._confirm_import_edges(
                                Path(candidate),
                                target,
                                root,
                                raw_entries=evidence["raw"],
                                deadline_monotonic=deadline_monotonic,
                                deadline_hit=deadline_hit,
                            )
                            edge_cache[pair] = edges
                        for edge in edges:
                            confidence = float(edge.get("resolution_confidence", 0.0))
                            full = confidence > 0.2 and "partial-resolution" not in str(
                                edge.get("path_provenance", "")
                            )
                            if full and target in trusted_frontier:
                                full_trusted = True
                            elif full:
                                full_weak = True
                            else:
                                partial_match = True
                        if _expired(owner, deadline_monotonic, deadline_hit):
                            break

                if full_trusted:
                    distances[candidate] = depth
                    statuses[candidate] = {"status": "confirmed", "depth": depth}
                    if evidence["status"] == "uncertain":
                        uncertain[candidate] = {
                            "depth": depth,
                            "reason": evidence["reason"] or "unresolved-import",
                            "confidence": 0.2,
                        }
                    else:
                        uncertain.pop(candidate, None)
                    trusted_seen.add(candidate)
                    next_trusted.append(candidate)
                elif full_weak or partial_match or evidence["status"] == "uncertain":
                    reason = (
                        "partial-resolution"
                        if partial_match
                        else evidence["reason"]
                        if evidence["status"] == "uncertain"
                        else "unconfirmed-query-origin"
                    )
                    uncertain.setdefault(
                        candidate,
                        {
                            "depth": depth,
                            "reason": reason or "unresolved-import",
                            "confidence": 0.2,
                        },
                    )
                    statuses[candidate] = {
                        "status": "uncertain",
                        **uncertain[candidate],
                    }
                    if candidate not in weak_seen:
                        weak_seen.add(candidate)
                        next_weak.append(candidate)
                else:
                    statuses.setdefault(candidate, {"status": "nonedge", "depth": depth})
            if _expired(owner, deadline_monotonic, deadline_hit) or not (next_trusted or next_weak):
                break
            trusted_frontier = next_trusted
            weak_frontier = next_weak

        if uncertain_candidates is not None:
            uncertain_candidates.clear()
            uncertain_candidates.update(uncertain)
        if candidate_statuses is not None:
            candidate_statuses.clear()
            candidate_statuses.update(statuses)
        return distances


def uncertainty_gaps(
    owner: ModuleType, paths: list[str], repo_root: Path | None
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        spec = owner.lang_registry.spec_for_path(path)
        language = (
            spec.language_id if spec is not None else path.suffix.lstrip(".").lower() or "unknown"
        )
        try:
            sample = (
                path.resolve().relative_to(repo_root.resolve()).as_posix()
                if repo_root
                else path.as_posix()
            )
        except (OSError, ValueError):
            sample = path.as_posix()
        grouped.setdefault(language, []).append(sample)
    return [
        {
            "language": language,
            "reason": "partial or unresolved import resolution leaves reverse-import candidates uncertain",
            "files_affected": len(samples),
            "files_sample": sorted(set(samples))[:5],
            "remediation": "resolve the listed imports to project files or treat these matches as heuristic",
            "affects_completeness": "never",
        }
        for language, samples in sorted(grouped.items())
    ]


def merge_uncertainty_gaps(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = list(existing)
    for gap in new:
        same = next(
            (
                current
                for current in merged
                if current.get("language") == gap.get("language")
                and current.get("reason") == gap.get("reason")
            ),
            None,
        )
        if same is None:
            merged.append(gap)
            continue
        same["files_affected"] = max(
            int(same.get("files_affected", 0)), int(gap.get("files_affected", 0))
        )
        same["files_sample"] = sorted(
            set(same.get("files_sample", [])) | set(gap.get("files_sample", []))
        )[:5]
    return merged


def add_uncertainty(
    owner: ModuleType, payload: dict[str, Any], paths: list[str], root: Path
) -> None:
    if not paths:
        return
    existing = list(payload.get("resolution_gaps", []))
    new = [
        gap
        for gap in uncertainty_gaps(owner, paths, root)
        if not any(
            current.get("language") == gap.get("language")
            and "no reverse-import resolver" in str(current.get("reason", ""))
            for current in existing
        )
    ]
    payload["resolution_gaps"] = merge_uncertainty_gaps(existing, new)
    payload.setdefault("coverage_summary", {})["graph_completeness"] = "partial"


def uncertain_paths(matches: list[dict[str, Any]], additional: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                str(item.get("path", ""))
                for item in matches
                if "heuristic-import" in item.get("reasons", [])
            ]
            + list(additional)
        )
    )
